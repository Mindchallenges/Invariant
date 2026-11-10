Backlog
=======

The work items, written by ``invariant item`` as ``idea`` and ``bug`` needs.
An item holds the problem, the evidence and the status; a decision it needs
is a decision need, linked by id.

.. bug:: The pre-tool-use hook fails OPEN on an internal error
   :id: BUG_001
   :status: filed
   :severity: high
   :slug: hook-fails-open-disarms-every-gate
   :residue: DECISION 2026-10-20 (user-approved): Consumers get a non-editable / version-pinned install\; the editable install stays only in this repository's own checkout. A mid-edit sentinel was not chosen. The remainder is a deployment/doc change, not an engine change.
   :affects: COMP_HOSTS_CLAUDE_CODE, COMP_CLI_MAIN

   An unhandled exception inside the engine is reported as advisory context
   and the tool call proceeds. Observed three times in one session, each a
   different exception, while the library's working tree was mid-edit.

   The severity comes from the deployment, not the policy: consumers run an
   EDITABLE install, so any half-written edit in the library's tree disarms
   privacy.clean, session.no_edit_in_main_checkout, test.\* and the push barrier
   for every concurrent session in the consuming repository.

   Fail-closed was considered and rejected: a broken library would then deny every
   tool call INCLUDING the ones that would repair it, which is a self-locking
   failure with no recovery that does not involve hand-editing hook scripts.

   The half that WAS a defect is fixed (2a3af78): ``doctor`` now invokes every hook
   with an inert payload and reads a machine-readable degraded marker, because a
   crashed engine answers with exit zero and valid JSON by design.

   What remains is the deployment shape. Options, none free:

   - ship a non-editable install for consumers, editable only in this checkout
   - version-pin the consumer's copy so a mid-edit tree is never live
   - a "library is mid-edit" sentinel the hook can see

   REPRODUCTION: available from the reporting session on request.

   **Partly fixed, and the remainder is not a code change.** ``doctor`` now invokes every hook and reads a degraded marker, so an outage is visible instead of silent; the adapter self-check reports tooling reachable only via a fallback. What remains is the deployment: consumers run an editable install of a tree under active development. No gate can fix that, and fail-closed would make a broken library deny the calls that would repair it. Left open deliberately, as an environment decision for the consuming project.

.. bug:: Blocking gates that are structurally inapplicable to a docs-only change
   :id: BUG_002
   :status: fixed
   :severity: high
   :slug: docs-only-diff-blocks-on-inapplicable-gates
   :affects: COMP_CORE_RULES, COMP_MACHINE_DOCS

   A change consisting of two .rst files and zero source blocked on three
   gates that could not be satisfied even in principle:

   test.new_test_fails - there is no implementation, so no test could fail first
   coverage.meets_floor - no code to measure
   docs.diagrams_current - the change adds no component, seam or dependency, so a
   diagram would assert a structural change that did not
   happen

   ``pipeline ack`` refuses all three because they block, which is correct by design
   (otherwise block and warn are the same level spelled differently). So the only
   route was ``advance --force``, which records a reason but teaches --force as
   routine — corrosive for a tool whose whole value is that gates mean something.

   Two candidate fixes, and the choice matters:

   - a ``docs`` methodology alongside ``tdd``, chosen per pipeline. Explicit, and the
     machinery already exists (methodology.pipeline).
   - key gate applicability to the diff's file types. Automatic, but it means a
     rule deciding for itself whether it applies, which is the inference this
     library generally refuses to make.

   Leaning toward the first: a caller declaring "this is documentation work" is a
   statement, and a rule guessing it is an inference.

   **Fixed: a ``docs`` methodology is shipped, and docs.diagrams_current abstains for a pipeline that has DECLARED itself documentation. Applicability is declared, never inferred from file extensions.**

   .. invariant-history:
      - from: fixed
        to: fixed
        reason: 'backfill: status was set by hand before transitions were recorded; fixed in ab49796'
        forced: true

.. bug:: INVARIANT_SESSION_ID does not reach the pre-tool-use hook
   :id: BUG_003
   :status: fixed
   :severity: medium
   :slug: session-id-env-does-not-reach-the-hook
   :affects: COMP_HOSTS_CLAUDE_CODE, COMP_DOMAINS_SESSION

   Every ambiguity message tells the caller to prefix a command with
   ``INVARIANT_SESSION_ID=<id>``. That works for a direct CLI call, because the CLI
   reads it from its own environment. It does NOT work for a hook-mediated tool
   call: the prefix applies to the invoked process, while the hook is a separate
   process the host spawns, so the variable never reaches the code doing the
   gating.

   Concretely: a registered-but-not-isolated session running
   ``INVARIANT_SESSION_ID=<id> git push`` was still refused as having no session.

   This is narrower than it was — attribution now resolves by host session id and
   by the edit's target path — but a session whose work is not code (a device soak
   owning no worktree) still has no way to name itself to a hook.

   Either the advice should stop being printed for hook-mediated operations, or
   the hook needs another channel for it. Printing advice the tool cannot honour
   is the dead-end shape fixed for --id in df464a8.

   **Fixed the honest half: the ambiguity message no longer implies an exported variable reaches a hook. It cannot — a hook runs as its own process. The message now names ``--id`` first, says where the variable does work, and says a hook identifies a session by the host session bound to it or by the path an edit targets. The 1:1 host binding, which lands automatically at ``session start``, is the mechanism a worktree-less session now uses.**

   .. invariant-history:
      - from: fixed
        to: fixed
        reason: 'backfill: status was set by hand before transitions were recorded; fixed in 959dbe0'
        forced: true

.. bug:: next_id only mints numeric ids, so a slug convention is unreachable
   :id: BUG_004
   :status: fixed
   :severity: medium
   :slug: item-ids-cannot-follow-a-project-convention
   :affects: COMP_CORE_ITEMS

   ``next_id()`` matches ^BUG\_(\\d+)$ and mints BUG_001. A project whose backlog
   uses descriptive cross-linked slugs — BUG_PHOTOS_IMPORT_SINGLE_FLIGHT_STALLS_QUEUE
   — gets an id that is inconsistent with every neighbour and, where entries are
   referenced as :need: targets in architecture docs, unlinkable.

   The reporting project's curator had to hand-author the entry instead, which
   means the backend was bypassed for the operation it exists to perform.

   Wants an id strategy that is configurable per project: numeric, slug-from-title,
   or a supplied id.

   **Fixed: items.id_strategy accepts 'slug', deriving a descriptive id from the title with collision suffixing. 'numeric' remains the default.**

   .. invariant-history:
      - from: fixed
        to: fixed
        reason: 'backfill: status was set by hand before transitions were recorded; fixed in 19ea5a4'
        forced: true

.. bug:: The shipped bug machine's states do not match a project's own vocabulary
   :id: BUG_005
   :status: fixed
   :severity: medium
   :slug: bug-lifecycle-vocabulary-is-not-configurable
   :affects: COMP_MACHINE_BUG

   The shipped ``bug`` machine starts at ``filed``. The reporting project's legend
   defines open / investigating / fixed / wontfix / cannot-reproduce, and every
   existing query and report keys off those. An entry filed at ``filed`` is invisible
   to all of them.

   Machines are already pluggable via methodology.extra_machines, so the mechanism
   exists; what is missing is that item machines are not routed through it the same
   way, and that nothing warns when a project's backend vocabulary and the item
   machine disagree.

   **Fixed the half that was missing. The mechanism already existed — ``load_item_machine`` routes through ``methodology.extra_machines``, so a project can ship its own ``bug.yaml`` — but nothing said so and nothing noticed a mismatch. ``doctor`` now reports item statuses that are not states of their configured machine, and names extra_machines as the fix.**

   .. invariant-history:
      - from: fixed
        to: fixed
        reason: 'backfill: status was set by hand before transitions were recorded; fixed in 959dbe0'
        forced: true

.. bug:: SphinxNeedsBackend.save() appends to the end of the file
   :id: BUG_006
   :status: fixed
   :severity: medium
   :slug: sphinx-needs-backend-appends-outside-document-structure
   :affects: COMP_ITEMS_SPHINX_NEEDS

   The backend has no notion of a structured document, so a saved item lands
   after everything rather than inside the batch or tier section it belongs to,
   and any summary-count table in the document goes stale.

   In the reporting project both had to be corrected by hand, which means the
   backend produced a file that a human then had to repair — worse than not
   writing it, because the intermediate state was committed-shaped.

   **Fixed: a new entry is inserted after the LAST directive of its own type, which keeps it inside whichever section that type already lives in. With no entry of that type to anchor to it appends, and does not pretend to parse the document's structure.**

   .. invariant-history:
      - from: fixed
        to: fixed
        reason: 'backfill: status was set by hand before transitions were recorded; fixed in 19ea5a4'
        forced: true

.. bug:: The option parser silently mis-parses multi-line fields
   :id: BUG_007
   :status: fixed
   :severity: high
   :slug: sphinx-needs-option-parser-drops-multi-line-fields
   :affects: COMP_ITEMS_SPHINX_NEEDS

   Continuation lines of a multi-line option, and the option that follows,
   leak into ``body`` instead of the field.

   This affects EXISTING entries, not only newly written ones: two were confirmed
   in the reporting project's backlog. It is silent, so anything reading those
   fields programmatically is wrong today and reports nothing amiss.

   Ranked above the other backend items because it is a READ defect on data the
   tool did not write — the blast radius is every consumer of the field, not just
   the next save.

   **Fixed: the option block now ends at the first blank line, as reStructuredText does, and a wrapped value continues the option above it rather than falling into the body.**

   .. invariant-history:
      - from: fixed
        to: fixed
        reason: 'backfill: status was set by hand before transitions were recorded; fixed in ab49796'
        forced: true

.. bug:: Consider exempting work-item paths from session isolation by default
   :id: BUG_008
   :status: fixed
   :severity: low
   :slug: work-item-paths-are-not-exempt-from-worktree-isolation
   :affects: COMP_CORE_RULES, COMP_DOMAINS_SESSION

   A backlog file is not exempt, so ``session.no_edit_in_main_checkout`` blocks a
   curator editing it in the shared checkout. That is the rule working as written.

   The question is whether it should be written that way. Backlog entries are data
   every session touches, like configuration and memory, and routing them through a
   branch produces conflicts with no review value — which is the stated rationale
   for the existing exemptions. The work-item backend is this library's own
   feature, so the default is this library's call.

   Argument against: a backlog edit can accompany a code change and belongs with
   it. Argument for: in practice it does not, and the reporting project's own
   convention had treated it as data until the manifest said otherwise.

   Deliberately filed as a question rather than a defect.

   **Decided in favour: the work-item store is exempt from worktree isolation by default, for the reason already stated for config and memory — it is data every session touches, and routing it through a branch produces conflicts with no review value. Not added to the default exempt_paths list, because its path is configurable and the library knows where it put it. ``items.exempt: false`` restores the old behaviour for a project that wants backlog edits reviewed with the code.**

   .. invariant-history:
      - from: fixed
        to: fixed
        reason: 'backfill: status was set by hand before transitions were recorded; fixed in 959dbe0'
        forced: true

.. bug:: Teardown removes a worktree the host session is still standing in
   :id: BUG_009
   :status: fixed
   :severity: high
   :slug: teardown-removes-a-worktree-a-host-still-occupies
   :affects: COMP_DOMAINS_SESSION

   **Symptom:** ``invariant session teardown`` removes the session's worktree and
   branch without checking whether a live agent-host session still has that
   directory as its working root. When one does, that host session is wedged:
   every subsequent shell call from it is refused by the HOST's own worktree
   isolation, and the refusal orders the caller back into a directory that no
   longer exists. The repair commands are themselves shell calls, so they sit
   behind the same refusal. The deadlock is total and self-sealing — there is no
   in-band recovery.

   **Expected:** Teardown notices that a live host session is bound into the
   worktree it is about to remove, and refuses or warns naming the exact step that
   clears the binding first. Removing a directory another system holds a live
   reference to should not be a silent success.

   **Repro:** Observed 2026-08-11. A session reached ``torn_down``; its worktree was
   removed and its branch deleted. A live host session remained bound to the
   removed path. ``git worktree list`` showed three live trees where four were
   expected, and the removed path returned ENOENT. That host session could run
   nothing at all until the host-level binding was dropped by a non-shell route.

   **Suspected cause:** ``SessionDomain.teardown`` in ``src/invariant/domains/session.py``
   has exactly two preconditions — a clean tree for ``git worktree remove``, and a
   safe ``git branch -d``. Neither asks whether anything outside git is standing in
   the directory. The host binding this library already models is not consulted
   here at all.

   **Attribution — the refusal text is the HOST's, not invariant's.** Recorded
   because the obvious first reading is that ``session.no_edit_in_main_checkout`` or
   ``session.isolated_before_edit`` misfired, and neither did. Verified two ways:
   grepping the installed package for the refusal wording returns zero matches
   (the install is editable and resolves to ``src/invariant/``, so installed bytes
   are source bytes), and the pre-tool-use shim is a pure
   ``exec invariant hook pre-tool-use`` with no logic of its own. Invariant's own
   isolation refusals are worded differently and never name a worktree and order
   the caller into it. So this is NOT a defect in the isolation rule. It is a
   missing precondition on teardown.

   **Affected:** ``domains/session`` — teardown. See ``docs/architecture/overview.md``,
   "Component responsibilities" → ``domains/``, which gives this domain identity,
   isolation, the push barrier and teardown; and
   ``docs/architecture/state_model.md``, "Domain: session" for the machine and
   "Host binding" for the 1:1 host-to-session binding that exists but is not read
   by teardown. CLI surface: ``docs/architecture/cli.md``, "Session".

   **Severity note — high, but not data loss.** The failure mode is a completely
   wedged session with no in-band recovery. It does not endanger work: teardown
   already refuses on a dirty worktree and deletes the branch with ``-d``, so
   unmerged commits are still protected by the existing checks.

   **Fix sketch / next step:** Teardown should detect a live host binding into the
   worktree it is about to remove and refuse or warn with the exact command that
   clears the binding. Whether that binding is discoverable at all is the open
   design question, and it is constrained by the dependency rule in
   ``docs/architecture/overview.md``: ``hosts/`` reach the engine only through the
   CLI, and ``core/`` knows nothing about hosts, so teardown cannot interrogate a
   host adapter directly. Candidate shapes to weigh, none free: read the session
   record's own host binding and treat one not yet marked spent by ``SessionEnd`` as
   live; or make the check advisory with an explicit override for the case where
   the operator knows the host has already moved. Start the session by proving the
   wedge in a test — a teardown against a session whose binding is still live —
   rather than at the fix.

   **Related:** BUG_010 is the other half of the same seam: invariant models the
   host binding as a scalar with no notion of which directory or project the host
   is standing in. Here that costs a precondition; there it costs the evidence.

   **Repro proof:** ``tests/test_domains.py::TestTeardownAndALiveHostBinding::test_teardown_does_not_consult_a_live_host_binding``,
   committed 8814f3b (2026-08-11) — asserts that teardown removes the worktree and
   deletes the branch of a session whose host binding is live (``host_session_id``
   set, ``host_ended`` False) without ever consulting that binding. Strict xfail, so
   the suite stays green while the defect is live and fails the moment a fix lands.
   Confirmed red for the right reason: the failure is the assertion, not the
   fixture, and it reports ``destroyed=True, consulted=[]``.

   **What the proof does and does not cover.** It pins the missing precondition,
   not the wedge. The wedge itself is environment state — a live host session whose
   every subsequent shell call is refused — and is not expressible in this test
   target; it stays an environment-level observation, already recorded above under
   "Repro". Three notes for the fixing session, each measured rather than assumed:

   - The assertion is deliberately design-neutral: ``not (destroyed and not consulted)``. Verified satisfiable by patching both candidate shapes in from
     outside the module — a refusal leaves the directory standing and passes, a
     warning removes it but consults first and also passes. It therefore does not
     foreclose the open refuse-vs-warn decision, which is still the user's to make.
   - Reads are counted only while the directory still exists, and that ordering
     discriminator is load-bearing. Teardown *does* read ``host_session_id`` after
     the removal — twice for the transition's telemetry watermark in ``_advance``,
     once when the record is serialised — so a plain "was it read at all" spy
     reports a consultation that never happened.
   - The red does not depend on the binding being live, because nothing is
     consulted at all; that absence is the defect. So the fix needs a companion
     negative case this repro deliberately does not supply: a session whose binding
     is spent (``host_ended`` True) must still tear down cleanly, or the fix becomes
     a blanket refusal.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: null
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Teardown now reads the host binding before removal and refuses (not warns) when the binding is live AND the caller is standing inside the worktree being removed; --force overrides deliberately. Verified against 8/8 historical torn_down sessions in this repo''s state root: a liveness-only trigger would have refused 6 of them (the resting state of every healthy ending), so the occupancy half is load-bearing, not incidental. Residue recorded for the still-open cross-host case (BUG_010''s wide half).'
        forced: false

.. bug:: Evidence records only into the project the host session is rooted at
   :id: BUG_010
   :status: fixed
   :severity: high
   :slug: evidence-records-into-the-hosts-own-project-only
   :affects: COMP_HOSTS_CLAUDE_CODE, COMP_CLI_MAIN

   **Symptom:** Docs-reads and agent dispatches are recorded into the project the
   invoking host session's working directory resolves to, not the project the work
   happened in. A host session rooted at project X that does work in project Y
   records Y's evidence into X's state root, or drops it. Y's gates never see it,
   so a gate in Y cannot be satisfied by doing the thing it asks for.

   **Expected:** Evidence is attributed to the session and the project the tool
   call actually acted on. A Read of a file inside a session's worktree feeds that
   session's ``docs.read_architecture`` gate regardless of which directory the host
   happens to be standing in.

   **Repro:** Reproduced 2026-08-11. From a host session rooted at a different
   project, with an isolated invariant session in ``designing`` owing
   ``docs.read_architecture``, an architecture doc under the invariant worktree's
   ``docs/architecture/`` was read. ``invariant pipeline gates``, run in the invariant
   repo, still reported "no architecture doc has been read in this pipeline".
   Corroborating evidence: this repo's state root
   (``.git/invariant/state/registry.json``) had not been written since the previous
   day, while the other project's had been written in the same working period.

   **Why this is not cosmetic.** ``agents.design_agent_ran`` and
   ``agents.review_agent_ran`` are ``block`` in this repository by deliberate
   decision. Combined with this defect, any invariant pipeline driven from a host
   session rooted elsewhere is unsatisfiable: the gate cannot be passed by doing
   the right thing, only by forcing. That converts a correct gate into a trap, and
   a gate whose only exit is ``--force`` trains everyone to reach for ``--force`` —
   the same corrosion BUG_002 was filed against. The manifest comment beside those
   two rules already predicted this and stated the resolution in advance: the
   defect is in the recording and gets fixed there; lowering the gate is not
   available.

   **Suspected cause:** two layers, one direction.

   1. Project root. ``cmd_hook`` in ``src/invariant/cli/main.py`` builds its ``Context``
      from the invoking process's working directory, so the manifest — and
      therefore the state root — are the host's, not the work's. The path that
      stays silent when no manifest resolves at the cwd is by design, and is the
      same path that silently drops the evidence.
   2. Attribution. ``ClaudeCodeHost._post_tool_use`` resolves the owner with
      ``ctx.current_session(strict=False)`` before recording, i.e. working directory
      and host identifier only.

   **Sub-observation, a recording asymmetry a fix should probably close at the
   same time.** Edit tools are attributed by their destination path:
   ``Context.session_for_payload`` reads ``file_path`` out of the payload, which is
   absolute and independent of the caller's working directory — step 3 of the
   documented order in ``docs/architecture/state_model.md``, "Attribution". Read and
   the agent-dispatch tools are not: they take ``current_session()``, which is steps
   2 and 4 only. A Read whose ``file_path`` names a file inside a session's worktree
   is therefore NOT attributed to that session, even though the payload carries
   everything needed to do so — ``_post_tool_use`` pulls that same ``file_path`` out a
   few lines further down, purely to record it. Path-based attribution already
   exists in this codebase; it is simply not used on these two paths.

   **Affected:** ``hosts/claude_code`` (``_post_tool_use``) and the CLI's context
   construction. See ``docs/architecture/overview.md``, "Component responsibilities"
   → ``hosts/`` and the "Enforcing a transition" flow, for the dependency rule that
   constrains any fix; and ``docs/architecture/state_model.md`` — "Attribution" for
   the documented resolution order, "Host binding" for the 1:1 binding this leans
   on, "Agent-role gates" for the two rules that become unsatisfiable, and
   "Storage layout" for why the state root is per-repository in the first place.

   **Fix sketch / next step:** The narrow half — attribute Read and the
   agent-dispatch tools by the payload's ``file_path``, exactly as edits already are
   — is testable in isolation and closes the sub-observation. The wide half — a
   hook payload whose target lies in a repository other than the host's working
   directory — needs a decision about whether a hook may resolve a second manifest
   and write to a second state root at all; note that ``hosts/`` reach the engine
   only through the CLI, so that decision belongs in the CLI's context
   construction, not in a host adapter learning about foreign roots. Both halves
   want a red test first. Blocks current work: implementing ADR-002 in this
   repository cannot record its own design-agent run.

   **Repro proof (narrow half), 2026-08-11:**
   ``tests/test_host_claude_code.py::TestReadIsAttributedByItsPath::test_BUG_010_a_read_inside_a_worktree_satisfies_that_sessions_docs_gate``,
   committed ``88bee0f``. Two sessions live in one project, the hook firing from
   the shared checkout, a PostToolUse ``Read`` naming an absolute path inside one
   session's worktree: nothing is recorded, and ``docs.read_architecture`` for that
   session still reports "no architecture doc has been read in this pipeline" —
   verbatim the message the hand repro saw. Carried as a strict ``xfail``, so the
   suite stays green while the defect is live and fails the moment it stops
   occurring. The fixing session deletes the marker; the assertion must then pass
   unchanged. Verified satisfiable by the documented fix: passing the payload's
   ``file_path`` through as ``target`` turns it green, and that change alone (since
   reverted) breaks nothing else in the suite.

   **Narrow half fixed, 2026-08-11.** ``_post_tool_use`` resolves the owner through
   ``Context.session_for_payload`` — the same call pre-tool-use enforcement already
   makes on the same payload — instead of ``current_session``, so recording and
   enforcement now agree on the owner by construction rather than by coincidence.
   The xfail marker is gone and the assertion passed unchanged. Three tests were
   added around it to pin what the fix must *not* move: the host identifier still
   outranks the path (a read inside a peer's worktree stays the reader's), a path
   in no worktree still falls through to the working directory rather than being
   dropped, and an agent dispatch — which carries no path — resolves exactly as
   before.

   Measured end to end on this repository, same payload and same state root, the
   hook fired from the shared checkout with a host session that owns nothing:
   the installed pre-fix code recorded nothing; the fixed code recorded the read
   into the session whose worktree contains the file. Note this is a *stronger*
   failure than the "recorded by luck" case below — a host session owning no
   session here gets no origin candidate either, so the evidence was dropped
   outright.

   Two facts the repro established that narrow the fix:

   - **One session is not enough to see it.** With a single session registered at
     the project root, the working directory still resolves to it by *origin* —
     step 5 — so the read is recorded by luck. The defect needs either several
     live sessions or a working directory no session's worktree contains.
   - **Junk is recorded, not merely dropped.** In the cross-root case the read of
     a foreign repository's doc lands in the *host's* pipeline as a ``docs_read``
     entry naming a path in another project. That gate stays red there, because
     the path is outside the host project's docs root — so it pollutes without
     falsely greening.

   **Wide half — reproduced as behaviour, but it is a missing mechanism, not a
   failing contract, 2026-08-11.** Measured with a host bound in project X and
   the work in project Y, each with its own state root:

   - An ``Agent``/``Task`` payload carries exactly ``subagent_type``, ``description`` and
     ``prompt`` — confirmed against real host transcripts, not from the tool docs.
     No path. The only project-identifying value in the whole payload is the
     **host** session id, and the envelope's other fields (``cwd``,
     ``transcript_path``) name the host's project too. There is no signal in the
     payload that names project Y, so no test can assert Y's evidence was
     recorded without first specifying a mechanism that does not exist.
   - The lookup that would use the host id happens in the wrong registry anyway:
     the state root is per-repository (see "Storage layout"), and there is no
     machine-global host-session index for a hook to consult.
   - **Consequence, and this is worse than the filed symptom:** the dispatch is
     recorded into X's pipeline, where ``agents.design_agent_ran`` — a *blocking*
     gate here — then reports "feature-architect ran in this pipeline". Y's stays
     unsatisfiable. Cross-root, this defect does not only drop evidence; it
     greens a blocking gate in a project where that work never happened.
   - **Fixing the narrow half does not unblock this.** Measured: resolving with
     the payload's target against X's registry returns X's own session by *host
     identifier*, because step 2 outranks step 3 — and Y's sessions are not in
     that registry to be found. Whoever fixes the narrow half should not expect
     the blocked work to come unblocked with it.
   - **An unblock that needs no fix at all:** drive the pipeline from a host
     session rooted at this repository. Measured green — with the working
     directory inside the session's own worktree, both the docs read and the
     agent dispatch record into the right pipeline. That is the route to landing
     ADR-002 without ``--force``, and it costs nothing but a new host session.

   **Related:** BUG_003 (fixed) established host-and-path attribution for edits;
   this is the same seam on the tool paths that fix did not reach. Not a
   regression of it — Read and agent dispatch were never covered. BUG_009 is the
   other half of the same mechanism: invariant models the host binding as a scalar
   with no notion of which directory or project the host is standing in.

   **Note 2026-10-24:** the wide-half residue (a hook payload whose target lies in another repository) is tracked in :need:`BUG_040 <BUG_040>`, which carries the user-approved 2026-10-20 decision and the pending ADR.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: null
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Narrow half fixed: _post_tool_use now resolves the owner through Context.session_for_payload instead of current_session, so recording and pre-tool-use enforcement agree on the owner by construction. Wide half (cross-repository dispatch) is unfixed and tracked as residue.'
        forced: false

.. bug:: The Python adapter cannot parse a branch-coverage table
   :id: BUG_011
   :status: fixed
   :severity: high
   :priority: high
   :area: adapters/python_
   :batch: inert-instruments
   :slug: branch-coverage-table-never-parses
   :affects: COMP_ADAPTERS_PYTHON

   **Symptom:** ``invariant pipeline coverage`` exits 2 with "the coverage run
   produced no per-file measurements", naming the command and its exit status 0.
   The underlying suite run succeeds and coverage.py prints a complete per-file
   table, but nothing is recorded, so ``coverage.meets_floor`` reports "no coverage
   was recorded" indefinitely.

   **Expected:** The per-file percentages coverage.py just printed are recorded as
   pipeline evidence, and ``coverage.meets_floor`` evaluates the configured floor
   against them.

   **Repro:** Observed 2026-08-11 on master, pipeline ``tdd`` in ``reviewing``.

   1. A repository whose ``pyproject.toml`` sets ``[tool.coverage.run] branch = true``
      and whose ecosystem resolves to the ``python`` adapter.
   2. ``pytest`` on PATH.
   3. Run ``invariant pipeline coverage``.
   4. Exit 2 and the empty-measurement refusal, while running
      ``pytest --cov --cov-report=term-missing`` by hand exits 0 and prints a full
      per-file table.

   **Cause (confirmed by source reading, not suspected):** ``parse_coverage()`` in
   ``src/invariant/adapters/python_.py`` matches on

   ::

      ^(?P<file>[\w./+-]+\.py)\s+\d+\s+\d+\s+(?P<pct>\d+)%

   which encodes coverage.py's NON-branch table shape — ``Name Stmts Miss Cover Missing``, two numeric columns before the percentage. With ``branch = true``
   coverage.py emits the BRANCH table instead — ``Name Stmts Miss Branch BrPart Cover Missing``, four numeric columns before the percentage. No line matches,
   ``measured`` returns ``{}``, and ``PipelineDomain`` (``src/invariant/domains/pipeline.py``,
   around the coverage-recording path) then refuses to record an empty result.
   **The refusal is correct and is not the defect** — recording ``{}`` would let the
   gate pass on a measurement that never happened. The parser is what is broken.

   **Scope — this is what sets the severity.** ``branch = true`` has been in
   ``pyproject.toml`` since this repository's first commit (e4f4ded), predating the
   adapter. ``coverage.meets_floor`` has therefore never been satisfiable here, for
   any change, by any author, across the entire history, and the project's own
   configured floor (``methodology.gates.coverage_min: 85``) has never once been
   enforced by the engine. This is not a gate that degrades under some condition;
   it has never fired.

   **Why it survived:** the failure is silent by construction. The rule is ``warn``
   in this manifest, so the pipeline prints an advisory notice and proceeds, and
   the notice's natural reading is "nobody ran coverage yet" rather than "coverage
   cannot be recorded in this repository". A project setting the same rule to
   ``block`` would be wedged with no visible cause.

   **Affected:** ``adapters/`` — the ``python`` adapter's ``parse_coverage``, one of the
   six methods of the adapter protocol documented in ``docs/architecture/manifest.md``
   ("Adapters"). The consumer is ``invariant pipeline coverage``, documented in
   ``docs/architecture/cli.md`` as the command that parses with the adapter and
   records the per-file numbers that ``coverage.meets_floor`` reads.

   **Fix sketch / next step:** The fix is NOT a better column count — it is to stop
   counting columns. Anchor on the ``.py`` extension and take the first percentage
   that follows it. A parser that counts columns is coupled to a table layout it
   does not own, so tolerating "two or four numeric columns" only postpones the
   next break to the next time coverage.py changes its table. Stated that way the
   defect is not "the regex has the wrong number of ``\s+\d+`` groups" but "the
   parser is coupled to a layout owned by another tool", which is also why it
   survived from the first commit.

   The in-repo proof that this works is ``adapters/swift_xcode.py`` — see the checked
   result below. Land it behind a regression test that feeds ``parse_coverage`` a
   real BRANCH-shaped table specifically; the existing tests evidently exercise
   only the non-branch shape. Do not relax the empty-result refusal in
   ``domains/pipeline.py``; it is the one part of this path behaving correctly.

   **Checked: ``swift_xcode.py`` is NOT affected.** Its ``parse_coverage()`` matches on

   ::

      ^\s*(?P<file>[\w./+-]+\.swift)\s+(?P<pct>\d+(?:\.\d+)?)%

   which anchors on the ``.swift`` extension and takes the first percentage after it.
   It never counts columns at all, so a table gaining or losing numeric columns
   cannot break it — precisely the failure mode that kills the Python one.
   Corroborated empirically as well as structurally: that parser has been returning
   real per-file numbers in another project this week, so it is demonstrably not
   silently-empty the way ``python_.py`` has always been. Recorded as a closed lead so
   nobody re-investigates it.

   **Open question: ``adapters/node.py`` is UNVERIFIED.** Nobody has looked at it. The
   Swift result says nothing about it either way — different adapter, different
   tool's output. The general shape of defect (a parser keyed to one output layout,
   returning empty on a legitimate variant, and read as a tooling problem rather
   than a broken instrument) was hit independently in another repository and another
   ecosystem, so the question is worth answering; it is not a finding yet.

   **Sibling, same mechanism: BUG_012** — ``docs.updated`` and
   ``docs.diagrams_current`` go inert on a committed tree. There the changed-file
   query asks a git surface that a commit empties; here the coverage parser
   matches nothing. Different files, but the same shape — a rule whose input set
   is empty by construction, returning a non-verdict that reads as a pass — and
   the same diagnosis method: trace each rule's input back to its producer and ask
   whether it can be non-empty in the state the gate actually runs in.
   Fix-together candidates; batched as ``inert-instruments``.

   **Related in kind, not in cause:** BUG_009 and BUG_010. The common theme across
   all four is instruments that cannot fail where they are meant to fire. Distinct
   from BUG_002, which concerned gates being structurally inapplicable to a change
   rather than a gate being unable to take a measurement at all.

   **Repro proof (2026-08-11):** ``tests/test_adapters.py::TestPythonAdapter::test_coverage_parsing_branch_table``,
   committed dda6ce9 — feeds ``parse_coverage()`` a realistic branch-shaped table
   (``Name Stmts Miss Branch BrPart Cover Missing``) and asserts the per-file
   percentage comes back; currently returns ``{}``. Wrapped in a strict ``xfail`` so
   the suite stays green until the fix lands, at which point the marker must be
   deleted and the assertion passes unchanged. Also confirmed live in this
   repository: ``uv run invariant pipeline coverage`` on master exits with
   "the coverage run produced no per-file measurements. command: pytest --cov
   --cov-report=term-missing exit: 0", while ``parse_coverage()`` invoked directly
   against this run's own real branch-shaped table output returns ``{}``.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: null
        forced: false
      - from: reproduced
        to: fixed
        reason: PythonAdapter.parse_coverage anchors on the .py extension and reads the row's numeric cells rather than counting them, so branch-coverage tables (four columns) parse the same as non-branch tables (two); regression tests cover branch/non-branch/fractional/multiline/false-positive cases.
        forced: false

.. bug:: docs.updated and docs.diagrams_current go inert on a committed tree
   :id: BUG_012
   :status: fixed
   :severity: high
   :priority: high
   :area: core/rules
   :batch: inert-instruments
   :slug: docs-gates-inert-on-committed-tree
   :affects: COMP_CORE_RULES

   **Symptom:** At the end of a pipeline phase, when the work under review is
   already committed, both ``docs.updated`` and ``docs.diagrams_current`` return N/A
   rather than a pass or a failure:

   ::

      [skip] docs.updated: nothing changed
      [skip] docs.diagrams_current: no doc changed

   ``docs.updated`` is ``block`` in the shipped ``standard`` profile. In the gate
   listing an N/A is visually and semantically indistinguishable from a pass, so a
   reader cannot tell "the docs were updated" from "the gate could not look"
   without reading the implementation.

   **Expected:** At an end-of-phase gate the rules consider the work actually done
   in the phase — which, by construction, is committed by then — and either pass
   or fail against it.

   **Repro (observed 2026-08-11, pipeline ``tdd``, session state ``reviewing``, work
   committed):**

   1. Isolate a session, make a change that touches both source and architecture
      docs, and commit it.
   2. Advance the pipeline to a state whose exit gates include ``docs.updated``.
   3. Run ``invariant pipeline gates``.
   4. Observe ``[skip] docs.updated: nothing changed``, despite the commit
      demonstrably containing architecture-doc changes — verifiable with
      ``git show --stat <sha> -- docs/architecture``.

   In the run where this was observed the commit contained four architecture-doc
   files totalling +203/-6, and the gate saw none of them.

   **Cause — confirmed by source reading, not suspected:**

   - ``src/invariant/core/rules.py:819`` — ``_docs_updated`` does
     ``changed = git.changed_files(ctx.cwd)``, then
     ``if not changed: return Verdict.na("nothing changed")``.
   - ``src/invariant/core/rules.py:868`` — ``_docs_diagrams_current`` does
     ``docs_changed = [p for p in git.changed_files(ctx.cwd) ...]``, then
     ``if not docs_changed: return Verdict.na("no doc changed")``.
   - ``src/invariant/core/git.py:109-118`` — ``changed_files`` is
     ``git diff --name-only`` plus ``git diff --cached --name-only``. Working tree and
     index ONLY. A commit empties both.

   So on a committed tree the input set is empty and both rules bail before
   evaluating anything.

   **This is not a missing insight — it is an unpropagated one.** That is what
   gives this entry its weight. The hazard is already written down twice inside
   the codebase, by the same author, in the two files involved:

   1. ``src/invariant/core/git.py:121+``, the docstring of ``branch_files`` itself:
      "``changed_files`` answers for the working tree and the index, which is the
      right question while work is in progress and the wrong one afterwards: a
      commit empties both. Any gate that runs at the END of a phase — when the
      work is, by construction, already committed — therefore sees nothing changed
      unless it also asks this."
   2. ``src/invariant/core/rules.py:1005-1011``, the comment inside
      ``coverage.meets_floor``: "...The working tree and index answer for work in
      progress and go empty on commit... The branch's own commits answer for
      exactly the state this gate runs in — after the commit — and are what makes
      the set non-empty when it matters most."

   And ``branch_files`` has exactly ONE real caller repo-wide:
   ``src/invariant/core/rules.py:1014``, inside ``coverage.meets_floor``. The only
   other mention, at ``src/invariant/adapters/base.py:207``, is a doc-comment citing
   its ``None`` semantics, not a call. So of the three rules that ask "what
   changed", two get it wrong and the third carries the comment explaining why
   they are wrong. The fix is not a discovery; it is propagation.

   **Fix direction — THREE sources, not two.** ``coverage.meets_floor`` at
   ``src/invariant/core/rules.py:1004-1017`` is the working reference
   implementation, in the same file. It unions three sets:
   ``git.changed_files(cwd)`` + ``git.branch_files(cwd, base)`` + the session's
   DECLARED file set.

   **The trap a two-source fix falls into, rested on the code rather than on a
   sighting.** The declared set is not belt-and-braces. ``branch_files`` returns
   ``None`` when ``base`` is empty, and that is the FIRST thing it does, before it
   attempts a ref at all. It is also the *documented* case: the docstring names
   "no base" first among the three reasons it returns ``None``, immediately before
   the passage about ``None`` meaning "do not relax". When it fires, the declared
   set is the only thing still carrying an answer.

   What makes that path real is not a field observation but an inconsistency
   inside this file — the same field, read from the same record, twice, with two
   different fallbacks:

   ::

      src/invariant/core/rules.py:586    base = str(ctx.session.get("base") or "master")
      src/invariant/core/rules.py:1013   base = str((ctx.session or {}).get("base") or "")
      src/invariant/core/git.py:137-138  if not base: return None   <- first statement in branch_files

   ``session.work_would_be_stranded`` (:586, registered at
   ``src/invariant/core/rules.py:538``) falls back to ``master`` and gets a real
   comparison out of ``git.commits_ahead``. ``coverage.meets_floor`` (:1013,
   ``_coverage_meets_floor`` at ``src/invariant/core/rules.py:983``) falls back to the
   empty string, and ``branch_files`` short-circuits to ``None`` on its very first
   statement. **A session whose ``base`` is absent makes ``commits_ahead`` compare
   against ``master`` and makes ``branch_files`` compare against nothing — same field,
   same record, two answers.**

   **Evidence grades, stated explicitly because this entry is about instruments
   that cannot distinguish cases and must not commit the same sin:**

   - UNOBSERVED. Nobody has seen a session record with an absent ``base``. A count
     across another repository's whole state root found 29 session records with
     ``base`` set and 0 absent, null or empty; in this repository ``session start --base`` carries a CLI default of ``master``
     (``src/invariant/cli/main.py:1393``).
   - STRUCTURALLY REACHABLE. The empty-string fallback at
     ``src/invariant/core/rules.py:1013`` reaches ``src/invariant/core/git.py:137``'s
     ``None`` unconditionally for any session record whose ``base`` is absent, null or
     empty. The branch exists whether or not anyone has stood in it.
   - INCONSISTENTLY HANDLED. ``rules.py:586`` against ``rules.py:1013`` — same field,
     different defaults, and only one of the two survives the missing value.

   So the three-source direction rests on three things, none of them anecdotal:
   the ``None`` branch is reachable by construction, the codebase already contains a
   second reader of the same field that does NOT go silent on the same input, and
   the declared file set is what carries the answer when ``branch_files`` cannot. A
   two-source fix would still LOOK correct to anyone testing it in THIS
   repository, whose sessions always have a base. Construct the no-base case
   deliberately.

   **A refuted line of evidence — recorded, not deleted.** The first version of
   this entry (commit 09c43ab) supported the three-source direction with a field
   sighting: "``pipeline.base: null``, observed in another repository". It was wrong
   twice over, and both defects are worth carrying:

   - Provenance. It was a peer's assertion, repeated and then "confirmed" by a
     check chosen because it would confirm it — inference reported as observation,
     with no record ever read.
   - Wrong field, disqualifying on its own. It named ``pipeline.base``, but the rule
     at ``src/invariant/core/rules.py:1013`` reads the SESSION record's ``base``. The
     sighting could not have supported the argument even had it been genuine.

   The later count over that repository's whole state root: 24 pipeline records,
   ``base`` key absent from all 24; 29 session records, ``base`` set in all 29. So
   "``pipeline.base`` is null" is literally true — no pipeline record carries the
   key at all — and that is almost certainly what was seen and repeated. It is
   simply not the field this rule consults, and the unset-base path is not
   observed as reachable there either. **Those 24 pipeline records still exist.**
   The next person reasoning about the ``None`` branch will find them and derive the
   same wrong anecdote; this paragraph exists to foreclose that, which is why it
   is recorded rather than quietly corrected.

   **Preserve the None-means-do-not-relax semantics.**
   ``src/invariant/core/rules.py:1031`` treats ``committed is None and not declared``
   as ``Verdict.fail("the touched set could not be established")``, never as a pass.
   A fix that treats an unanswerable query as "nothing to check" reintroduces this
   exact defect in a new place. An unanswered question is not a negative answer.

   **Severity note — the ``warn``-level instance may be the more interesting one.**
   ``docs.diagrams_current`` is ``warn`` in this manifest, which reads like the lesser
   half, and it is not. It guards the mermaid dependency graph in
   ``docs/architecture/overview.md`` — the document whose own opening line calls it
   "the authoritative map of the components and their" relationships, and on which
   this project's docs-first rule rests. A ``warn`` that cannot fire is not a weaker
   gate than a ``block`` that cannot fire; it is the same nothing, reported more
   quietly.

   **Not a defect — do not "fix" ``privacy.clean`` while in here.** ``privacy.clean``
   also returns N/A at that edge, and that is CORRECT. It is event-bound
   (``events=("pre-tool-use",)``, ``src/invariant/core/rules.py:1152``), so it scans
   content as it is written and legitimately has nothing in scope at a transition.
   Its implementation distinguishes the two cases explicitly at
   ``src/invariant/core/rules.py:1163-1166``: "'nothing was in scope' and
   'everything in scope was clean' are different facts, and reporting the first as
   the second is how a gate comes to look green while inert." That is the correct
   handling of this exact hazard, by the same author, in the same file. Cite it as
   the model, not as a defect.

   **Affected:** ``core/rules`` — see ``docs/architecture/overview.md``, "Component
   responsibilities" → ``core/``, which gives ``rules`` the registry of "an id, a
   bound host event, an evaluation function, and a level" and explicitly withholds
   policy from it ("levels come from the manifest"). That boundary matters to the
   fix: the repair is to the evaluation function's input set, not to any level.
   The two rules' place in the pipeline is documented in
   ``docs/architecture/state_model.md``, "Domain: pipeline" — ``docs.updated`` is one
   of the two gates the engine injects for docs-first, leaving the review state,
   and a methodology cannot opt out of it. ``docs/architecture/state_model.md``
   line 43 shows ``docs.updated`` as an exit gate in the declared FSM format.
   ``core/git`` is not itself a documented component; its ``branch_files`` docstring
   is the authority.

   **Fix sketch / next step:** Widen the input set of both rules to the same three
   sources ``coverage.meets_floor`` already unions, and keep the ``None`` handling.
   Two details that decide whether the fix is real:

   - Prove it with a red test on a COMMITTED tree — a clean working tree, the doc
     change present only in the branch's commits. A test that dirties the working
     tree passes against the current broken code and proves nothing.
   - Add a second case with no base configured — constructed, since no such
     session record has ever been observed — asserting the declared file set still
     carries the answer and that an unanswerable set fails rather than skips.
     While in there, decide deliberately whether ``rules.py:1013`` should adopt the
     ``master`` fallback that ``rules.py:586`` already uses; that is the narrower
     repair of the inconsistency, and it is not the same fix as widening the
     input set.

   Consider also whether an N/A from a ``block``-level gate should render distinctly
   from a pass in the gate listing. That is arguably a separate entry, but it is
   the reason this defect was invisible for as long as it was, and a fixer should
   decide deliberately rather than by omission.

   **Overlap warning for whoever picks this up:** ``tests/test_rules.py`` — the
   natural home for the regression tests above — is currently dirty in the main
   checkout under another live session, along with ``src/invariant/adapters/base.py``,
   ``src/invariant/adapters/swift_xcode.py`` and ``tests/test_adapters.py``. Coordinate
   or rebase before adding tests there.

   **Amended 2026-08-11:** the unset-base argument was rebuilt on source-level
   evidence (``rules.py:586`` vs ``rules.py:1013`` vs ``git.py:137``) after the field
   sighting it originally rested on was refuted; the refutation is kept above
   deliberately. Severity, priority, batch and the three-source direction are
   unchanged. The amendment also corrects the reader named at ``rules.py:586``: it
   is ``session.work_would_be_stranded``, not ``session.ff_merge_target`` — that rule
   (``src/invariant/core/rules.py:447``) parses the merge command and never reads
   ``session.base`` at all.

   **Related:** BUG_011 is the sibling entry, same family: an instrument that
   cannot fire. There the coverage parser matches nothing and the measurement is
   never recorded; here the changed-file query asks a surface that is empty by the
   time the gate runs. Different files, same mechanism — a rule whose input set is
   empty by construction, returning a non-verdict that reads as a pass — and the
   same diagnosis method: trace each rule's input back to its producer and ask
   whether it can be non-empty in the state the gate actually runs in. Fix-together
   candidates. BUG_009 and BUG_010 are related in kind rather than in mechanism;
   the common theme across all four is instruments that cannot fail where they are
   meant to fire. Distinct from BUG_002, which concerned gates being structurally
   inapplicable to a change — there the gate correctly declined a question that did
   not apply; here the gate cannot take a measurement it is required to take.

   **Repro proof (2026-08-11):** ``tests/test_rules.py::TestDocsGatesOnACommittedTree``,
   committed ebcdf26 — asserts the reported symptom on a genuinely committed tree.
   The class holds four tests. Three are strictly ``xfail``\ ed, so the suite stays
   green while the defect is live and fails the moment the fix lands, at which
   point the markers are deleted and the assertions pass unchanged:

   - ``test_docs_updated_reads_a_source_only_commit`` — a source-only commit on the
     branch, clean working tree, session ``base: master``. ``docs.updated`` must fail
     with "source changed but no architecture doc did". It currently returns
     ``Verdict(ok=True, message='nothing changed', skipped=True)`` at ``Level.BLOCK``
     — verbatim the ``[skip] docs.updated: nothing changed`` reported above.
   - ``test_diagrams_current_reads_a_committed_doc_change`` — a committed doc change
     with the diagram removed. ``docs.diagrams_current`` must report the missing
     diagram; it currently returns skipped ``no doc changed``.
   - ``test_docs_updated_reads_the_declared_set_when_no_base_is_configured`` — the
     constructed unset-base case this entry asks for, ``base: ""`` with
     ``files: ["app.py"]``. A two-source repair leaves this one red.

   The fourth, ``test_the_fixture_leaves_a_genuinely_committed_tree``, is
   deliberately NOT ``xfail``\ ed. A strict ``xfail`` counts a failure in setup as the
   expected failure, so a repro that quietly dirtied the tree would report red for
   the wrong reason; this asserts the precondition separately —
   ``git.changed_files(wt) == []`` and ``git.branch_files(wt, "master") == [Path("app.py")]``.

   Two checks were run against the repro before the verdict, and neither is
   committed. Dirtying the same fixture instead of committing it produces the
   asserted failure message from the current code, so the harness reaches the
   surface and the committed-vs-dirty state is the only variable — which is also
   why every pre-existing docs-rule test in that file passes today. And
   monkeypatching ``git.changed_files`` to union the branch's commits (and, for the
   unset-base case, the declared set) makes all three assertions pass with the rule
   source untouched, so they are satisfiable by the three-source direction above
   rather than by a rewrite of the rules' messages.

   The **overlap warning** above is now stale: ``tests/test_rules.py``,
   ``src/invariant/adapters/base.py``, ``src/invariant/adapters/swift_xcode.py`` and
   ``tests/test_adapters.py`` were committed and merged before this repro was
   written, and the repro added a new class rather than touching any existing test.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: repro test committed ebcdf26 — three strict xfails on a committed tree
        forced: false
      - from: reproduced
        to: fixed
        reason: docs.updated and docs.diagrams_current now read _changed_set (working tree + index, branch commits, declared file set) instead of the working tree alone, so an end-of-phase exit gate judges committed work instead of reporting nothing changed; an unanswerable changed set now fails rather than silently skipping.
        forced: false

.. bug:: The telemetry report states a false cause for a missing breakdown
   :id: BUG_013
   :status: fixed
   :severity: medium
   :priority: high
   :area: domains/stats
   :batch: adr-002-followups
   :slug: telemetry-false-cause-for-missing-breakdown
   :affects: COMP_DOMAINS_STATS

   **Symptom:** For a session whose pipeline has simply not transitioned yet,
   ``invariant stats show`` and the rendered report print:

   ::

      no breakdown available — recorded before per-state attribution

   The session does not predate per-state attribution. It was recorded after it
   and carries a perfectly good ``usage_at_start`` baseline; it has merely not left
   its first state, so there is no consecutive-watermark pair to subtract yet. The
   report asserts a specific historical cause, with full confidence, that is
   false.

   A second surface, same root: a session holding one completed pipeline plus one
   freshly-started current pipeline sets ``states_partial`` and renders "some
   pipelines in this session predate per-state attribution" — also false.

   **Expected:** The report distinguishes "no attribution data was ever recorded
   for this pipeline" (genuinely pre-instrumentation) from "this pipeline has not
   transitioned yet, so there is nothing to attribute". Both may legitimately
   render as no-breakdown, but they must not both claim the pre-instrumentation
   cause. "Not yet" is honest; "recorded before per-state attribution" is a
   fabricated history.

   **Repro (observed 2026-08-11 on master, at commit 919f7b2):**

   1. Start a pipeline in a session and do not transition it — leave it in its
      first state, which is where every session sits for real time.
   2. Run ``invariant stats show <session>``.
   3. Observe the no-breakdown line naming pre-instrumentation as the cause,
      against a session recorded entirely after the instrumentation landed.

   For the second surface: complete one pipeline in a session, start a second,
   and render before the second transitions. ``states_partial`` is set and the
   "some pipelines … predate per-state attribution" sentence is printed.

   **Cause — confirmed by source reading, not suspected.** Two independent passes
   reached it separately and it checks out on inspection:

   - ``src/invariant/core/stats.py:536-539`` — ``breakdown()`` returns ``None`` for an
     EMPTY history (``if not history: return None``) on the line above the genuine
     pre-instrumentation test (``if any(entry.get("elapsed") is None …)``). Empty
     history is the state of every freshly-started pipeline. One ``None``, two
     entirely different facts behind it.
   - ``src/invariant/domains/stats.py:288-331`` — ``_breakdown`` treats ANY pipeline
     that ``breakdown()`` declines to attribute as unattributed (``if spends is None: continue``), without asking why it declined, and folds the count into
     both the ``None`` return and the ``attributed < len(raw)`` partial flag.
   - ``src/invariant/domains/stats.py:611-614`` renders that case through
     ``NO_BREAKDOWN`` at ``src/invariant/core/stats.py:66-69``, a module constant
     that hardcodes the pre-instrumentation wording, and
     ``src/invariant/domains/stats.py:630-632`` renders the partial-flag sentence
     the same way.

   **Nothing here needs to be inferred that cannot be.** The two cases are
   already distinguishable in the data the function is holding: an empty history
   versus a history whose entries lack ``elapsed``/``usage``. The code simply does not
   make the distinction — the information is discarded at the ``None`` return and
   cannot be recovered downstream.

   **Blast radius — this is what lifts it above an edge case.** It fires on EVERY
   currently-active session sitting in its first pipeline state, not only on
   legacy ones. Every session spends real time in its first state, so this is the
   ordinary path rather than a corner, and the false sentence is what an in-flight
   session will normally show. The wording was written for a shrinking historical
   population and is in practice served to the live one.

   **Why it matters more than its size.** ADR-002 D3 spends several paragraphs
   establishing that the report must never let a reader draw a wrong conclusion
   from a missing number: a pre-instrumentation session's totals "must say so
   explicitly rather than rendering a zero or a blank", because "a blank invites
   the reader to conclude the work was free". D6 applies the same rule to cost
   (every figure labelled a floor rather than implying a total) and D8 to pricing
   (a session containing an unpriced model reports no total at all, because "a
   total that silently omits a model's spend is worse than no total, because it
   looks like a total"). The through-line of all three is that the report never
   volunteers more certainty than it has.

   This defect is the same harm one step worse. Instead of a blank that invites a
   wrong conclusion, the report volunteers a wrong explanation. A confidently
   stated false cause is harder to detect than an absence, because nothing prompts
   the reader to question it — the reader has been given a reason and will stop
   looking for one. It is D3's own failure mode, arrived at by the mechanism D3
   installed to prevent it.

   **Affected:** ``domains/stats`` — the report renderer and ``_breakdown``, the
   domain documented in ``docs/architecture/overview.md`` ("``domains/``", the ``stats``
   bullet) as owning the rendered report, and in
   ``docs/architecture/state_model.md`` ("Domain: stats") as a store with invariants
   rather than an FSM. The attribution arithmetic itself lives in ``core/stats``,
   listed in the same overview module table as "the arithmetic". The user-facing
   contract is written down in ``docs/architecture/cli.md`` under ``stats show``:
   "A session recorded before per-state attribution existed says 'no breakdown
   available' explicitly." The docs scope the sentence to the pre-instrumentation
   case exactly as intended — the defect is in the code's reading of that
   contract, not in the contract. No doc change is implied by this entry.

   **Fix sketch / next step:** Small and local; both independent passes described
   it that way, and it is a distinction the available data already supports rather
   than a redesign.

   1. Separate the two ``None`` returns in ``breakdown()`` at
      ``src/invariant/core/stats.py:536-539`` — empty history ("nothing to
      attribute yet") versus history present but lacking attribution fields
      ("never instrumented"). A sentinel or a two-valued return; the callers are
      few.
   2. Propagate the distinction through ``_breakdown``
      (``src/invariant/domains/stats.py:288-331``) so the not-yet case does not
      count toward ``states_partial``. A session whose only unattributed pipeline is
      an untransitioned current one is not partial in D3's sense — nothing was
      lost.
   3. Render a distinct, accurate sentence for the not-yet case, alongside
      ``NO_BREAKDOWN`` rather than replacing it. Keep the existing wording for the
      genuine pre-instrumentation case; it is correct where it applies and is
      quoted verbatim in ``cli.md``.

   Land it behind a regression test that renders a session with a started,
   never-transitioned pipeline and asserts the output does NOT claim
   pre-instrumentation — the existing tests evidently cover the empty-history path
   only through its ``None``, not through the sentence it produces.

   **Regressed in:** commit 919f7b2 (ADR-002 session telemetry), landed on master
   2026-08-11. This is a defect in that commit's own new behaviour, not a
   pre-existing one it exposed. Filed as a follow-up rather than a re-open of the
   commit, on the concurring recommendation of an independent design-verification
   pass and an independent compliance review; both landed on medium severity
   independently.

   **Sibling, same review, different mechanism:** the telemetry source-directory
   prefix-collision entry filed from the same pass over 919f7b2, together with its
   smaller ADR-002 follow-ups. Siblings by origin — one review of one commit — not
   by cause.

   **Deliberately NOT batched with BUG_011 / BUG_012 (``inert-instruments``).** That
   batch is about gates whose input set is empty by construction, returning a
   non-verdict that reads as a pass. This is a renderer asserting an unverified
   cause over data it holds and does not consult. The diagnosis methods differ:
   ``inert-instruments`` is traced by asking whether a rule's input can be non-empty
   in the state it runs in, this one by asking whether a rendered claim is
   entailed by the data behind it. Fixing one teaches nothing about the other, so
   batching them would buy adjacency at the cost of a false shared root cause.
   Filed unbatched. If a batch is warranted it is an ADR-002-followups one, minted
   when the cluster entry justifies a second member.

   **Batched 2026-08-11 — ``adr-002-followups`` minted, on explicit user signal.**
   The condition above is met and then some: the remaining review findings landed
   as THREE entries, not one — BUG_014 (a forced rebind crosses two host sessions'
   usage counters), BUG_015 (telemetry counts a sibling project whose directory
   name extends the origin's) and BUG_016 (five small follow-ups). This entry
   joins them as the fourth member. The batch groups by origin — one commit
   (919f7b2), one review, one telemetry subsystem, and a shared diagnosis method:
   check each rendered telemetry claim against the data actually behind it. It
   does NOT assert a shared root cause; the four causes are distinct, and the
   paragraph above still stands on why this one is not ``inert-instruments``.

   **Repro proof:** ``tests/test_stats_domain.py::TestTheNoBreakdownLineDoesNotFabricateAHistory``,
   committed 43ee286 (2026-08-14) — two strict-xfail assertions on the rendered
   sentence, plus a non-xfail precondition test recording that the fixture is
   post-instrumentation (empty history, ``usage_at_start`` present).

   - ``test_a_never_transitioned_pipeline_is_not_called_pre_instrumentation`` —
     a session whose only pipeline was started with a watermark baseline and never
     transitioned renders "no breakdown available — recorded before per-state
     attribution".
   - ``test_a_freshly_started_second_pipeline_is_not_called_pre_instrumentation`` —
     one fully attributed completed pipeline plus one freshly started current one
     renders "some pipelines in this session predate per-state attribution".

   Both assert on ``StatsDomain.render_session``, the exact call ``invariant stats show <session>`` makes, and deliberately NOT on the internal ``states is None``
   that produces the line: ``None`` is a legitimate answer for both cases, and this
   entry's expected behaviour allows both to render as no-breakdown. The defect is
   the cause attached to it.

   Discrimination checked from outside with a throwaway probe that was not
   committed: in the same harness a fully attributed pipeline renders without
   either phrase, so the assertion is satisfiable rather than unconditional; and a
   genuinely pre-instrumentation history (an entry with no ``elapsed``) still
   renders the sentence, so the repro demands the two cases be told apart rather
   than the wording be removed.

   **Noticed while reproducing, not part of this entry.** A session with no
   pipeline at all takes the same ``None`` return (``_breakdown`` returns early on an
   empty ``raw``) and renders the same pre-instrumentation sentence — a third fact
   behind one ``None``, pinned today by
   ``test_a_session_with_no_pipeline_at_all_reports_no_breakdown``, which asserts
   the flag and not the sentence. Whoever fixes this should decide what that case
   should say; it is not covered by the committed repro.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: null
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Fix landed: breakdown() distinguishes empty-history-not-yet from never-instrumented; NO_BREAKDOWN_YET/NO_BREAKDOWN_NO_PIPELINE render the honest cause. Repro''s strict-xfail markers deleted, assertions unchanged, both pass.'
        forced: false

.. bug:: A forced rebind crosses two host sessions' usage counters
   :id: BUG_014
   :status: fixed
   :severity: medium
   :priority: medium
   :area: domains/session
   :batch: adr-002-followups
   :slug: forced-rebind-crosses-usage-counters
   :affects: COMP_DOMAINS_SESSION

   **Symptom:** After a forced rebind moves a session from host session A to host
   session B, the per-state token figures in ``invariant stats show`` and in the
   rendered report are produced by subtracting a watermark counted against A's
   transcript from one counted against B's. Where B's cumulative total happens to
   be the higher of the two, the subtraction returns a plausible positive number
   and it is rendered as a measured per-state spend, indistinguishable in the
   output from a correct one.

   **Expected:** A rebind either keeps the attribution arithmetic commensurable —
   by re-baselining ``usage_at_start`` and marking the pre-rebind watermarks as
   belonging to a different counter — or it declares the affected states
   unattributable, the way the existing backwards-watermark guard already does.
   What must not happen is a number derived from two unrelated counters wearing
   the same units as a measured one.

   **Repro (source-confirmed 2026-08-11 on master, at commit 919f7b2; not yet
   demonstrated by a failing test):**

   1. Register a session bound to host session A and let it transition at least
      once, so ``usage_at_start`` and one or more ``history`` ``usage`` watermarks are
      recorded against A's transcript.
   2. Rebind it to a host session B whose transcript already carries a HIGHER
      cumulative token total than A's — ``invariant session adopt <session> B --force`` (``src/invariant/cli/main.py:684-686``).
   3. Transition the session again, so a post-rebind watermark is recorded
      against B.
   4. Run ``invariant stats show <session>``. The state spanning the rebind reports
      a positive token figure. It is B's cumulative count minus A's, which
      measures nothing.

   Reverse the totals — B lower than A — and the defect is caught rather than
   rendered, which is the point below.

   **Cause — confirmed by source reading, not suspected.**

   - ``src/invariant/domains/session.py:356-390`` — ``adopt_host_session(..., force=True)`` reassigns ``session.host_session_id`` at :387 and touches neither
     ``usage_at_start`` nor the existing ``history`` entries. The docstring at
     :359-365 reasons carefully about what a rebind hands over (pipeline,
     evidence, file set) and does not consider the telemetry baseline at all.
   - ``src/invariant/core/stats.py:551`` — per-state spend is ``current - previous``
     over consecutive watermarks. Across the rebind boundary those two readings
     are cumulative counts against two different transcripts. The subtraction is
     well-formed and meaningless.
   - ``src/invariant/core/stats.py:551-556`` — the existing guard catches this
     ONLY when the delta is negative, and its own comment states the principle it
     is defending: clamping "would hand the reader a small plausible number
     instead of a visible anomaly."

   **The asymmetry is the sharp part.** The guard is a monotonicity check, and
   crossing two counters violates monotonicity in one direction only. When B's
   total is lower the anomaly is visible and correctly suppressed; when B's total
   is higher the same crossing produces a well-behaved positive delta and is
   rendered as a measurement. The failure that survives the guard is precisely
   the one that looks correct, which is the harder of the two to ever notice.

   **Why it matters beyond its size.** ADR-002 D8 keeps measured tokens and
   modelled cost structurally apart on the stated grounds that a number arrived at
   by modelling must never wear the same units as a measured one — "a total that
   silently omits a model's spend is worse than no total, because it looks like a
   total." This defect produces exactly the thing D8 exists to prevent: a wrong
   number wearing the units of a right one, on the measured side of the split
   where the ADR's whole argument is that the figures are exact by construction.
   The feature contradicts its own governing principle in this edge case.

   **Why it is nonetheless medium and not higher.** Rebinding is a deliberate,
   rare, force-gated operator action. It cannot happen by accident: the unforced
   path refuses it outright (``src/invariant/domains/session.py:366-377``) with an
   error explaining the handover. Nothing is lost, nothing crashes, and the
   blast radius is the states of one session either side of one operator action.

   **A milder relative, same root, recorded here rather than separately.** A
   session registered while UNBOUND gets ``usage_at_start = None`` permanently
   (``src/invariant/domains/session.py:499``; ``_watermark`` short-circuits on a
   falsy id at :125), and a later adoption cannot backfill it because the reading
   is no longer observable — the count it needed was the one true at registration.
   This renders honestly as no-spend rather than wrongly, so it is NOT a defect
   and no code change is claimed for it.

   It does, though, expose an unstated assumption in the doc. ADR-002 D3 says
   ``usage_at_start`` is "read once, at ``start`` / registration", which silently
   assumes the binding exists AT registration — while ``adopt_host_session`` exists
   precisely to make binding optional at that moment. Both this case and the
   rebind above are instances of one fact the ADR never writes down:

      the baseline is commensurable with the deltas only while the binding is
      stable.

   The code cannot detect a violation of that condition and the document as
   written implies it cannot arise.

   **Affected:**

   - ``domains/session`` — owner of the binding and of ``usage_at_start``, listed in
     ``docs/architecture/overview.md`` under ``domains/`` and in
     ``docs/architecture/state_model.md`` as the session machine. This is where the
     rebind happens and where the fix belongs.
   - ``core/stats`` — "the arithmetic" in the overview module table; the crossing
     is consumed at ``:551`` and the guard that half-catches it lives at ``:551-556``.
   - ``docs/adr/ADR-002-session-telemetry.md`` — D3 and the Consequences section.

   **Repro proof:** ``tests/test_stats_domain.py::TestAForcedRebindDoesNotCrossTwoHostSessionsUsageCounters::test_the_state_spanning_the_rebind_is_not_rendered_as_a_measurement``,
   committed 3cf9743 (2026-08-14) — asserts the rendered per-state row, not
   ``usage_at_start``. A session started under host A, advanced once, force-rebound
   via ``adopt_host_session(force=True)``, then advanced again under a host B whose
   cumulative total is the higher, renders:

   ::

        per state:
          designing            0s       200 tokens
          red                  0s     49.1k tokens

   The ``red`` row is B's cumulative total minus A's watermark (50_300 - 1_200),
   formatted identically to the correctly-attributed row above it and carrying no
   note. Wrapped in a strict ``xfail``, so the suite stays green while the defect is
   live and fails the moment it stops occurring.

   Three supporting tests in the same class stay green and are not xfailed:
   the rebind precondition; a state wholly inside one binding still attributing
   correctly at 200 tokens (so the red row fails for the crossing and not for the
   harness); and the lower-direction control, where the existing
   backwards-watermark guard declines to attribute and the same row renders
   ``unknown tokens``. That control is also the falsifiability check — it proves
   this harness reaches the surface and can render the outcome a fix must produce.

   **Fix sketch / next step:**

   1. [STRIKEOUT:First prove it.] Done — see the repro proof above. Delete the strict
      ``xfail`` marker with the fix; the assertion must then pass unchanged. The
      lower-direction control must stay green throughout: the existing guard is
      correct and the fix may not weaken or reroute it.
   2. Decide the semantics at ``domains/session.py:387``. Two defensible shapes:
      re-baseline (set ``usage_at_start`` from the new binding and mark the states
      before the rebind as belonging to a closed counter), or mark the boundary
      (record the rebind in ``history`` so ``core/stats`` can decline the one
      crossing delta the way it declines a negative one, reusing the existing
      ``spend.note`` channel). The second is smaller and matches the guard's
      established behaviour of naming an unattributable state rather than
      inventing a value for it.
   3. Whichever is chosen, the crossing must never render as a bare number.
   4. **This entry implies a doc change, unlike its siblings.** Add a line to
      ADR-002's Consequences recording that the baseline is commensurable with
      the deltas only while the binding is stable, and amend D3's "read once at
      start / registration" to say what happens when binding is deferred or
      changed. The Consequences section already carries comparable standing
      caveats (the pre/post-instrumentation asymmetry, the unrecorded subagent
      spend), so this is the section's existing job, not a new one.

   **Regressed in:** commit 919f7b2 (ADR-002 session telemetry), landed on master
   2026-08-11. A defect in that commit's own new behaviour, not a pre-existing one
   it exposed. Filed as a follow-up rather than a re-open of the commit, on the
   concurring recommendation of an independent design-verification pass and an
   independent compliance review that read the cited lines; neither considered it
   a blocker.

   **Siblings, same review, same commit:** BUG_013 (false "no breakdown" cause),
   the source-directory prefix-collision entry, and the five-finding tail cluster.
   All four are batched as ``adr-002-followups``. BUG_011 and BUG_012
   (``inert-instruments``) came out of the same review session but a different
   mechanism entirely — related by origin only, not kin.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: null
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Fix landed: each watermark reading is recorded with the host session it was counted against (usage_host / usage_at_start_host); a rebind boundary is declined and named (SPEND_NOTE_REBOUND) rather than subtracted across two counters. Repro''s strict-xfail marker deleted, assertion unchanged, passes.'
        forced: false

.. bug:: Telemetry counts a sibling project whose directory name extends the origin's
   :id: BUG_015
   :status: fixed
   :severity: medium
   :priority: medium
   :area: hosts/claude_code_telemetry
   :batch: adr-002-followups
   :slug: telemetry-sibling-prefix-collision
   :affects: COMP_HOSTS_CLAUDE_CODE_TELEMETRY

   **Symptom:** A project's telemetry silently absorbs the transcripts of an
   unrelated sibling project whose directory name extends the project's own. With
   an origin at ``<root>/app``, a sibling checkout at ``<root>/app-v2`` is counted as
   part of ``<root>/app``, and ``invariant stats show`` plus the rendered report
   overstate the origin project's tokens and modelled cost by the sibling's whole
   spend. Nothing in the output indicates that foreign sessions were included.

   **Expected:** Transcript narrowing includes the origin project and its
   worktrees — which is a real, tested requirement — and excludes any other
   project, whether or not that project's directory name begins with the origin's.

   **Repro (source-confirmed 2026-08-11 on master, at commit 919f7b2; not yet
   demonstrated by a failing test):**

   1. Two sibling checkouts under one parent, where one directory name is a
      prefix of the other: ``<root>/app`` and ``<root>/app-v2``.
   2. Host transcripts exist for both, so the host has minted a project directory
      per path — slugs ``-<root>-app`` and ``-<root>-app-v2``.
   3. From ``<root>/app``, run ``invariant stats show`` or refresh the telemetry
      cache.
   4. The sibling's sessions are included in the origin's totals, because
      ``"-<root>-app-v2".startswith("-<root>-app")`` is true.

   **Cause — confirmed by source reading, not suspected.**
   ``src/invariant/hosts/claude_code_telemetry.py``, ``ClaudeCodeTranscripts.paths()``
   at ``:224-243``. The origin path is slugified into a prefix at ``:228`` and every
   project directory is filtered with a bare ``project.name.startswith(prefix)`` at
   ``:237``. There is no boundary check — nothing requires the character following
   the matched prefix to be the separator used in the slug.

   **The permissiveness is deliberate and the requirement behind it is real.** The
   class docstring at ``:209`` states it outright: matching "by *prefix* rather than
   by exact name is what keeps a project's own" worktree sessions attributed to
   the project, and
   ``tests/test_host_telemetry.py:264-274``
   (``test_a_worktree_of_the_project_is_attributed_to_the_project``) locks that
   behaviour in. A worktree lives at a path UNDER the origin, so its slug extends
   the origin's slug after a separator. The bug is not that the match is a prefix
   match; it is that the match does not require the extension to begin at a path
   boundary. A sibling directory extends the slug too, just not at a boundary.

   **Why it survived review.** The negative test that should have caught it,
   ``tests/test_host_telemetry.py:276-282`` (``test_another_project_is_excluded``),
   only exercises wholly-different directory names — "repo" versus "elsewhere" —
   which no prefix rule of any strictness would confuse. The prefix-extending
   sibling shape is untested, so the distinction between "extends after a
   separator" and "extends mid-segment" was never put under load.

   **Impact and its boundaries.** Silently overstated tokens and modelled cost for
   the origin project, with no visible signal. It is explicitly NOT a privacy
   leak: no cross-project content is read out or rendered, only aggregated counts
   are affected. It needs a specific directory-naming coincidence to trigger —
   though ``app`` / ``app-v2``, ``svc`` / ``svc-old``, ``<name>`` / ``<name>-fork`` are
   ordinary ways for a person to lay out sibling checkouts, so the coincidence is
   not exotic. Medium severity on a wrong rendered measurement; nothing is lost,
   nothing crashes.

   **Affected:** ``hosts/claude_code_telemetry``, documented in
   ``docs/architecture/overview.md`` under ``hosts/`` as "a second, deliberately
   separate module" and in the same document's module table as the component that
   may "Read a transcript; know any host's on-disk format" — which is exactly the
   knowledge this defect misapplies. The consumer that renders the inflated
   figures is ``domains/stats``, but nothing there is wrong; it is faithfully
   totalling the sample set it was handed. The fix belongs entirely inside
   ``paths()``, below the ``SampleSource`` protocol boundary that ADR-002 D2
   established, so no other layer changes and no doc changes.

   **Fix sketch / next step:**

   1. First prove it. Add the missing negative case to
      ``tests/test_host_telemetry.py`` alongside ``test_another_project_is_excluded``:
      an origin and a SIBLING whose directory name extends the origin's without a
      separator, asserting the sibling's sessions are excluded. It must sit next
      to the worktree test, because the two together are what pin the rule — a
      fix that satisfies one and breaks the other is the obvious wrong turn here.
   2. At ``:237``, require either an exact match on the slug, or a match followed
      by the separator the slug uses (the slugification at ``:228`` replaces the
      path separator, so the boundary character is a known constant, not a guess).
   3. Keep the worktree test green. It is the requirement, not an accident.

   **Regressed in:** commit 919f7b2 (ADR-002 session telemetry), landed on master
   2026-08-11. A defect in that commit's own new behaviour. Found by the second of
   two independent passes over the commit — an independent compliance review that
   read the cited lines confirmed it; the first pass did not surface it. Neither
   pass considered it a blocker.

   **Siblings, same review, same commit:** BUG_013 (false "no breakdown" cause),
   the forced-rebind commensurability entry, and the five-finding tail cluster.
   All four are batched as ``adr-002-followups``. BUG_011 and BUG_012
   (``inert-instruments``) came out of the same review session but a different
   mechanism entirely — related by origin only, not kin.

   **Repro proof:** ``tests/test_host_telemetry.py::TestDiscovery:: test_BUG_015_a_sibling_whose_name_extends_the_origins_is_excluded_but_the_worktree_stays_in``,
   committed d5f55af (2026-08-14) — asserts the set of transcript stems
   ``ClaudeCodeTranscripts(origin=...).paths()`` selects for an origin, a real
   worktree of it, and a sibling whose directory name merely extends the
   origin's without a path separator. Fails today: the selected set includes
   the sibling's ``hs-sibling`` alongside ``hs-origin`` and ``hs-worktree``
   (``AssertionError: ... Extra items in the left set: 'hs-sibling'``), which is
   exactly the reported symptom — the sibling's transcript is swept in. The
   worktree side is pinned in the same assertion, so a fix that turns the
   match into a bare equality test (breaking worktree attribution) would also
   fail this test. Committed under a strict ``xfail`` so the suite stays green
   until the fix at ``:237`` lands; delete the marker then.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: null
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Fix landed: ClaudeCodeTranscripts narrows by evidence (filesystem, git worktrees(), and the hidden-directory naming convention) rather than by a bare prefix match, so a sibling whose name extends the origin''s is excluded while worktrees stay attributed. Repro''s strict-xfail marker deleted, assertion unchanged, passes.'
        forced: false

.. bug:: Five small ADR-002 telemetry follow-ups from the 919f7b2 review
   :id: BUG_016
   :status: fixed
   :severity: low
   :priority: low
   :area: domains/stats
   :batch: adr-002-followups
   :slug: adr-002-telemetry-small-followups
   :residue: reproduced covers F1 and F2 only, each with a committed strict-xfail proof. F3 (latent hazard, one caller that does pass the fact), F4 (a decision to record) and F5 (a doc sentence) carry no test proof and cannot: re-confirmed accurate as filed 2026-08-14 by source reading, with line-number drift noted in the entry. Do not read this entry's status as covering them.; F1, F2, F4 and F5 are fixed in this change\; F3 is NOT. F3's repair — one route to the host session id — cannot be made without either silencing telemetry inside the committed repro fixtures for BUG_013, BUG_014 and F1 (their controls would fail) or rewriting test_the_host_session_id_comes_from_the_session_record, whose subject is the route F3 removes. Deferred deliberately to its own sitting, after these fixtures stop being the live acceptance criteria for three other entries. Do not read this entry as done until F3 lands.; F3 is now filed as its own entry, BUG_023, because it carries no test proof and cannot: keeping it only as residue would either block this entry from closing or lose it at close. This entry's F3 text stands as the diagnosis\; BUG_023 carries the fix and the reason it is blocked on other entries' live fixtures. Close BUG_016 on F1/F2 without waiting for F3.
   :affects: COMP_DOMAINS_STATS

   **Fixed 2026-10-20** (commit a7ebbdc). F1, F2, F4 and F5 landed in a7ebbdc;
   verified on main 2026-10-20 — the strict-xfail markers on the F1 and F2 repro
   classes are removed and both classes pass. F3 is carried by BUG_023.

   **Symptom:** Five small, independently confirmed defects left over from the
   ADR-002 telemetry review, filed as one entry because each is a few lines and
   none justifies its own repro session. They are grouped by size and by the
   sitting that closes them, not by a shared root cause — the two substantive
   findings from the same review are filed separately as BUG_014 and BUG_015.

   **Expected:** Each sub-finding states its own expectation below.

   **Repro:** Each sub-finding is confirmed by source reading at the cited lines
   by two independent passes, on master at commit 919f7b2, 2026-08-11. Runtime
   repro steps are given only where the path is known reachable; F1's
   reachability is explicitly unsettled and recorded as such.

   **F1 (low) — a frozen transcript renders a hard zero where every other
   unobservable path renders nothing.**

   ``src/invariant/core/stats.py:304-317``. If a session's recorded
   ``host_session_id`` still names a transcript that is findable but has stopped
   growing, two consecutive watermarks are equal, their difference is a ``Usage()``
   with every field zero rather than ``None``, and
   ``src/invariant/domains/stats.py:617-623`` renders "0 tokens" — indistinguishable
   from a state that genuinely cost nothing.

   *Expected:* a state whose spend could not be observed renders as unobserved,
   the way every other such path does. ADR-002 D3 is explicit that the failure
   mode is "the same ``None``, rendered as 'no breakdown' rather than a zero",
   because "a blank invites the reader to conclude the work was free" — a zero
   invites it more strongly than a blank does.

   *Reachability is CANNOT-VERIFY, and this entry does not assert it.* The code
   path is confirmed; whether a live session can hold a findable-but-frozen
   transcript depends on the host's transcript-rotation behaviour on resume,
   which neither pass could settle from inside this repository. A fixer should
   settle that question FIRST: if the shape is unreachable the correct outcome is
   a recorded note saying so, not a speculative fix. Graded low on that basis.

   **F2 (low) — the privacy rationale for not printing host identifiers is
   applied on one branch and not on the branch thirty lines above it.**

   ``src/invariant/domains/stats.py:373-380`` interpolates a raw ``host_session_id``
   into the unreadable-record message that ``invariant stats check`` prints.
   ``:412-419``, in the same function, deliberately DECLINES to list identifiers for
   un-ingested sessions, with the comment: "Naming them would mean printing host
   session identifiers, and terminal output gets pasted into commits". Both
   branches print to the same terminal.

   *Expected:* one rule, applied to both branches. The rationale at :412-419 is
   right; the inconsistency is in its application, and the repair is the same
   single command whichever record is unreadable, so the identifier is no more
   load-bearing on the first branch than on the second.

   *Explicitly NOT a D7 breach.* D7 scopes to the committed report, which is
   clean and has a dedicated privacy-scanner test at ``tests/test_cli_stats.py:113``.
   This is terminal output only — hygiene consistency, not a rule violation.

   **F3 (low) — the baseline and the deltas reach one fact by two routes, one of
   which fails silently.**

   ``src/invariant/domains/pipeline.py:170`` takes ``host_session_id`` as an explicit
   argument, passed by its one caller at ``src/invariant/cli/main.py:893``.
   ``src/invariant/domains/pipeline.py:347`` digs the same fact out of an optional
   ``session: dict | None``. There is exactly one caller today and it does pass it
   (``src/invariant/cli/main.py:972``), so nothing is broken now.

   *Expected:* one route to one fact. Any future caller that omits ``session=``
   records ``usage: None`` for every transition with no error, no warning and no
   test failure — the telemetry simply stops, and the report renders the absence
   honestly, which is exactly why nobody would notice. A latent hazard rather
   than a present defect; graded low for that reason.

   **F4 (low) — ``Session.usage_at_start`` and session-transition telemetry are
   written and never read; the decision was never made explicitly.**

   ``src/invariant/domains/session.py:73`` and ``:697`` populate them.
   ``src/invariant/domains/stats.py:288-331`` reads ``self.pipelines`` only, and the
   one nearby read at ``:309-310`` is of the PIPELINE's field, not the session's.
   Confirmed by grep in both passes: no reader anywhere.

   *Expected:* an explicit decision, recorded. This matches ADR-002 D3 as written,
   so it is not drift, and it is NOT proposed for removal — the session-side
   reading may well be the right thing to have taken. But it is the mirror image
   of the defect ADR-002's own Notes section records against its rejected prior-art
   sketch ("a field for per-state timing that nothing could ever populate"), and
   the same standard should apply to a field nothing ever reads. Either give it a
   consumer or record in the ADR that the session-side fields are forward-looking.
   Cheapest of the five, and a decision rather than a repair.

   **F5 (nit) — a doc sentence miscounts the arrows it is describing.**

   ``docs/architecture/overview.md:95`` says "The two dotted-and-labelled arrows
   around ``stats``". Only one is dotted: ``CLI -. injects .-> CCTEL`` at ``:63`` is
   dotted and labelled, while ``CCTEL -- implements SampleSource --> STATS`` at
   ``:78`` is a solid labelled arrow. Both are labelled; one is dotted.

   *Expected:* the sentence describes the diagram it points at. Cosmetic, but it
   sits in the paragraph whose entire job is making the ADR-002 D2 layer inversion
   legible to a reader following the arrows, so a reader who checks the diagram
   against the prose finds the prose wrong at the exact moment they are trusting
   it. A one-word doc fix ("two labelled arrows", or naming them individually).

   **Affected:**

   - ``core/stats`` (F1) and ``domains/stats`` (F1, F2) — "the arithmetic" and the
     rendered report respectively, per the module table and the ``domains/`` section
     of ``docs/architecture/overview.md``.
   - ``domains/pipeline`` (F3) and ``domains/session`` (F4) — the two machines that
     record watermarks under ADR-002 D3.
   - ``docs/architecture/overview.md`` (F5) and ``docs/adr/ADR-002-session-telemetry.md``
     (F4, if the decision goes the "forward-looking" way).

   **Fix sketch / next step:** One sitting, in this order — F5 and F2 are
   mechanical; F3 is a signature change with one call site; F4 is a decision to
   record before any code moves; F1 is the only one needing investigation, and it
   needs its reachability settled before a line is changed. Do not let F1's
   open question hold up the other four: if F1 turns out unreachable, close this
   entry with a note recording that finding rather than a fix.

   **Regressed in:** commit 919f7b2 (ADR-002 session telemetry), landed on master
   2026-08-11 — except F5, which is a documentation sentence introduced by the
   same commit. All five were produced by an independent design-verification pass
   and independently re-confirmed at the cited lines by a compliance review.
   Neither pass considered any of them a blocker; both recommended them as
   follow-ups.

   **Known and accepted, recorded here so a fixer does not re-litigate them.**
   Three findings from the same review were confirmed as accurately disclosed and
   deliberately accepted, and are named in 919f7b2's own commit message. They are
   NOT defects and are not in scope for this entry:

   - ``paths.reports`` defaults to ``docs/reports`` and a declared report store is
     unconditionally isolation-exempt with no ``reports.exempt`` opt-out
     (``src/invariant/core/manifest.py:78``, ``src/invariant/core/rules.py:1394-1396``).
   - ``TranscriptWatermarks.watermark()`` re-hashes and re-parses the whole
     transcript on every transition of both machines, non-incrementally
     (``src/invariant/core/stats.py:307-314``). Disclosed, tested around, fail-safe.
   - ``cli/main.py`` at 83% against an 85 floor is inherited: zero of its 111
     uncovered lines are new, and its coverage rose from this change.

   **Siblings, same review, same commit:** BUG_013 (false "no breakdown" cause),
   BUG_014 (forced-rebind commensurability) and BUG_015 (telemetry
   source-directory prefix collision). All four are batched as
   ``adr-002-followups``. BUG_011 and BUG_012 (``inert-instruments``) came out of the
   same review session but a different mechanism entirely — related by origin
   only, not kin.

   **Repro pass 2026-08-14 — F1's reachability SETTLED; F1 and F2 proven actual;
   F3, F4 and F5 re-confirmed accurate as filed with nothing to reproduce.**

   ``reproduced`` covers F1 and F2 ONLY. Three of the five carry no test proof and
   cannot: F3 is a latent hazard with no present defect, F4 is a decision to
   record, F5 is a doc sentence. See the residue on this entry.

   **F1 — REACHABLE, and by a plainer mechanism than the entry hypothesised.**
   The entry asked that reachability be settled before a line is changed, and
   graded it CANNOT-VERIFY on the host's transcript-rotation behaviour. Rotation
   is not required. Two watermark reads return equal values whenever no billed
   assistant record is appended between them, and that is the ordinary case, not
   a corner:

   - Two ``invariant`` processes chained in one shell command
     (``invariant pipeline advance a && invariant pipeline advance b``) read the
     same transcript bytes. Measured directly against this machine's own live
     transcripts: two separate processes in one shell call returned identical
     cumulative totals.
   - The live transcript of the host session driving this project returned an
     identical cumulative total across repeated reads spanning several minutes of
     agent work, while the file itself was being appended to — because a
     subagent's own loop is not written to the parent transcript, which is
     ADR-002's own recorded finding ("zero records carry a sidechain marker while
     several hundred record a subagent launch"). Every transition an agent
     records during that window observes a static watermark.

   So the zero-delta is reachable wherever the watermark is readable at all, with
   no frozen or rotated transcript anywhere in the story. Graded on that basis the
   sub-finding is worth more than "low".

   **F1 repro proof:** ``tests/test_stats_domain.py::TestAFrozenTranscriptDoesNotRenderAsZeroSpend::test_a_state_whose_watermark_did_not_move_is_not_rendered_as_zero``,
   committed 870dc64 (2026-08-14) — asserts the rendered per-state spend field,
   not the internal ``Usage``. A pipeline whose every watermark read returns the
   same reading renders:

   ::

        per state:
          designing            0s         0 tokens
          red                  0s         0 tokens

   Wrapped in a strict ``xfail``. Three controls in the same class stay green and
   are NOT xfailed: the precondition (the source answers on all three reads and
   every state carries a usage reading, so this is not the already-correct ``None``
   path); a growing transcript still rendering a measured ``150``; and the
   no-watermark row rendering ``unknown``, which is the form the fix must produce
   and may not weaken.

   *A trap worth recording, caught by that second control:* asserting
   ``"0 tokens" not in text`` is NOT the assertion it looks like — ``150 tokens``
   contains it as a substring. The committed test asserts on the extracted spend
   field of one named row. The pre-existing
   ``test_a_state_with_no_watermark_renders_unknown_and_not_zero`` uses the loose
   form and passes only because its rows render ``unknown``.

   **F2 — real, assertable, and proven.** ``invariant stats check`` prints
   ``report.problems()`` verbatim to the terminal (``cli/main.py``, ``cmd_stats_check``),
   and the unreadable-record message leads with the raw identifier. Both branches
   in one paste:

   ::

        <host-session-uuid>: the record could not be read; 'invariant stats refresh --force' rebuilds it
        1 host session(s) have not been ingested; 'invariant stats refresh' picks them up

   **F2 repro proof:** ``tests/test_cli_stats.py::TestStatsCheckDoesNotPrintAHostSessionIdentifier::test_the_unreadable_record_message_does_not_name_the_host_session``,
   committed 870dc64 (2026-08-14) — asserts the synthetic host identifier does not
   appear in the command's stdout. Strict ``xfail``. Two controls stay green: the
   unreadable branch is reached and prints its advice (so the assertion cannot
   pass by printing nothing), and the un-ingested branch reports the same class of
   problem and names nobody — the form the fix must produce.

   **F3 — accurate as filed, still a latent hazard, nothing to reproduce.**
   ``domains/pipeline.py:170`` and ``:347`` are unchanged. There is still exactly one
   caller of each, and it does pass the fact: ``cmd_pipeline_start`` passes
   ``host_session_id=session.host_session_id`` and ``cmd_pipeline_advance`` passes
   ``session=session.to_dict()``, which carries ``host_session_id`` (``Session.to_dict``
   is ``asdict``). No red is possible without inventing a caller that does not
   exist, and manufacturing one would prove nothing about this codebase. The cited
   CLI line numbers have drifted (``:893`` is now ``:905``, ``:972`` is now ``:982``); the
   ``domains/pipeline.py`` line numbers are still exact. Needs: a signature change
   with one call site.

   **F4 — accurate as filed, nothing to reproduce.** ``Session.usage_at_start`` is
   declared at ``domains/session.py:73`` and written at ``:503``; session-transition
   ``elapsed`` and ``usage`` are written at ``:785-791``. The only read of any
   ``usage_at_start`` anywhere in ``src/`` is ``domains/stats.py:309-310``, and its loop
   variable is a PIPELINE, not the session — confirmed again by grep across ``src/``.
   No reader exists. The entry's ``:697`` has drifted to ``:503``. Needs: a decision
   recorded in the ADR, before any code moves.

   **F5 — accurate as filed, verified against the current file.** The diagram
   contains exactly ONE dotted arrow in total (``CLI -. injects .-> CCTEL``, still at
   ``:63``), and ``CCTEL -- implements SampleSource --> STATS`` is still solid at
   ``:78``. The sentence still says "The two dotted-and-labelled arrows" — but it has
   moved from ``:95`` to ``:107``, displaced by the ``core/ → adapters/`` paragraph that
   commit 35f8c8a added above it. Needs: a one-word doc fix.

   **A SIXTH finding, distinct from F1 — the backwards-watermark guard's note
   never reaches the reader.** Not a restatement of F1: F1 is a wrong VALUE
   (``0`` where ``unknown`` belongs) produced in ``core/stats.breakdown()``; this is a
   wrong CAUSE attached to a correct value, produced one layer up in
   ``domains/stats._breakdown()``.

   ``core/stats.py:556`` sets ``spend.note = "usage watermark went backwards; spend not attributable"`` and leaves ``usage`` at ``None``. The merge loop at
   ``domains/stats.py:316-327`` then builds a FRESH ``StateSpend`` per state, never
   copies the incoming note, and sets
   ``target.note = "some transitions carried no watermark"`` for any spend whose
   ``usage is None``. Since the guard's spend always has ``usage is None``, its note is
   unconditionally replaced. Verified by executing both layers over a single
   pipeline with one backwards step: ``core`` produces the guard's note, the merge
   loop replaces it with the milder one. It fires on a single pipeline — no
   multi-pipeline session is needed.

   The reader is told the watermark was missing when the code in fact detected two
   readings that cannot describe one monotonic run. Same family as BUG_013 (a
   rendered claim not entailed by the data behind it), different site and
   different data, and NOT covered by BUG_013's cause section, which cites this
   line range only for its ``if spends is None: continue``.

   *This bears directly on BUG_014.* That entry's fix option 2 proposes recording
   the rebind boundary so ``core/stats`` can decline the crossing delta "reusing the
   existing ``spend.note`` channel". That channel is stomped before it reaches the
   report, so the note-based fix would be invisible unless this is fixed with it.
   Left unfiled here deliberately — it belongs to whoever curates BUG_014's fix,
   not to this entry's five.

   **Also observed while reproducing, not part of this entry and not filed.** On
   this machine, ZERO of 19 recorded pipeline transitions carry a watermark, and
   only one of seven pipelines carries a baseline — although the telemetry is
   fully wired, ten of thirteen sessions are host-bound, and the transcript for
   that host session is findable. Cause: ``ClaudeCodeTranscripts`` narrows by the
   origin path's mangled prefix, and inside a session worktree the origin IS the
   worktree, whose mangled name is longer than the parent project's — so the
   parent's transcripts fail the ``startswith`` test and nothing is visible.
   Measured: 2 transcripts visible from the main checkout, 0 from a session
   worktree. Since the methodology requires all work to happen in an isolated
   worktree, per-state attribution is silently absent for essentially every
   transition recorded the way the library tells you to work. This is the
   under-inclusion direction and is NOT BUG_015, which is the over-inclusion
   direction (a sibling project whose name extends the origin's); BUG_015's
   "Expected" explicitly wants worktrees included. Worth its own entry from
   ``backlog-curator``; it is out of scope here and is recorded so the observation
   is not lost.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: null
        forced: false
      - from: reproduced
        to: fixed
        reason: fixed in a7ebbdc (F1, F2, F4, F5); F3 split out to BUG_023
        forced: false

.. bug:: Per-state attribution is absent for every transition from a worktree
   :id: BUG_017
   :status: fixed
   :severity: high
   :priority: high
   :area: hosts/claude_code_telemetry
   :batch: adr-002-followups
   :slug: worktree-origin-hides-the-parent-projects-transcripts
   :affects: COMP_HOSTS_CLAUDE_CODE_TELEMETRY

   **Symptom:** No pipeline transition recorded from inside a session worktree
   carries a spend watermark, so ``invariant stats report`` renders "no breakdown"
   or ``unknown`` for every state of those pipelines. Measured on one machine, not
   inferred: ZERO of 19 real recorded transitions carry a watermark and only one
   of seven pipelines carries a baseline, although ADR-002 telemetry is fully
   wired, ten of thirteen sessions are host-bound, and the transcript for the
   host session driving the work is findable.

   **Expected:** A session working the way the methodology MANDATES — inside an
   isolated worktree — attributes spend per state exactly as a session in the
   main checkout does. Per-state attribution is a shipped ADR-002 feature; under
   the only workflow the library permits, it has effectively never functioned.

   **Repro:** From the main checkout, count the transcripts the reader narrows to
   (``ClaudeCodeTranscripts(origin=<repo root>).paths()``). Then repeat with the
   origin set to an isolated worktree of the same repository. Measured: 2
   transcripts visible from the main checkout, 0 from the worktree. Then record a
   transition from a worktree session and read its stored record — ``usage`` is
   absent for every state.

   **Suspected cause:** ``ClaudeCodeTranscripts.roots()``
   (``src/invariant/hosts/claude_code_telemetry.py``) asks git for the repository's
   linked worktrees ONLY when the origin is the main checkout. From inside a
   worktree the origin IS the worktree, whose mangled project-directory name is
   LONGER than the parent project's, so the parent's transcript directory fails
   the ``startswith`` narrowing in ``_under_origin`` and nothing above the worktree
   is visible. The source already names this as an open question and declines to
   settle it by accident — the ``roots()`` docstring says the narrowing is
   "deliberately asked only from the main checkout" and that what a worktree's
   own report should contain "is a separate open question about the other
   direction of this narrowing". This entry is that question, now with a
   measurement attached.

   **DIRECTION IS THE KEY FACT — this is not BUG_015.** BUG_015 is the
   OVER-inclusion direction: a sibling checkout whose directory name extends the
   origin's was being counted. This is UNDER-inclusion: the parent project's own
   transcripts are not counted from a worktree. BUG_015's **Expected** explicitly
   WANTS worktrees included, so its fix in a7ebbdc did not fix this — and an
   equality-based fix to BUG_015 (matching the mangled name exactly) would have
   DEEPENED it. A fixer must not reach for one.

   **A real asymmetry to preserve in the fix, measured after a7ebbdc.** A
   torn-down worktree placed by the DEFAULT sibling convention (``<root>-wt-<id>``,
   beside the repository) loses its attribution permanently once removed: the
   filesystem no longer holds the path, git no longer lists the worktree, and the
   hidden-directory naming heuristic does not match a single-dash name. A
   torn-down worktree under the hidden worktrees directory stays attributed via
   that heuristic. This is evidence-honest by design and was accepted knowingly;
   record it here so a fixer does not read it as a second defect, and so that any
   fix is measured against both placements.

   **Affected:** ``hosts/claude_code_telemetry`` — per ``docs/architecture/overview.md``
   ("hosts/"), the module that reads back the host's per-project JSONL transcripts
   and narrows them to one project. The consumers that go blind are ``core/stats``
   (the arithmetic) and ``domains/stats`` (the rendered report).

   **Fix sketch / next step:** This may warrant revisiting ADR-002 rather than
   only a code change, and that decision should be taken FIRST. The open question
   is what a worktree session's report is *for*: its own spend, or the whole
   project's. Only once that is answered does the code shape follow — resolving
   the origin up to the repository's main checkout before narrowing, versus
   keeping per-worktree narrowing and joining upward at report time. Whatever
   lands must keep BUG_015 red (a sibling checkout stays excluded) and should be
   proven by a test that narrows from a worktree origin, not from the main
   checkout, since narrowing from the main checkout is the only case the existing
   tests cover.

   **Siblings:** BUG_015 (fixed, a7ebbdc) — same function, opposite direction.
   BUG_016 (reproduced) — this defect was observed and recorded during its repro
   pass and deliberately left unfiled there. BUG_013 (fixed) — the report's
   honesty about missing data is why nobody noticed the data was missing.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: tests/test_host_telemetry.py::TestDiscovery::test_BUG_017_a_worktree_origin_sees_the_whole_projects_transcripts — measured 0 of the parent project's transcripts visible from a worktree origin
        forced: false
      - from: reproduced
        to: fixed
        reason: 'roots() resolves the origin up to the repository''s main checkout before narrowing; a worktree session''s report is the project''s. Measured on this repo: 0 -> 6 transcripts visible from the worktree. BUG_015 stays red from both directions.'
        forced: false

.. bug:: The node adapter inherits a no-op parse_coverage
   :id: BUG_018
   :status: filed
   :severity: high
   :priority: medium
   :area: adapters/node
   :batch: inert-instruments
   :slug: node-adapter-has-no-coverage-parser
   :affects: COMP_ADAPTERS_NODE

   **Symptom:** ``invariant pipeline coverage`` in ANY Node project runs the whole
   test suite and then refuses with "produced no per-file measurements" — the
   identical refusal BUG_011 opened with, and the identical invisibility: the
   gate cannot pass, and nothing says why.

   **Expected:** An adapter that claims an ecosystem parses that ecosystem's
   default coverage output. ``node``'s ``commands.coverage`` default is
   ``npm test -- --coverage``, so the adapter names the reporter it cannot read.

   **Repro:** ``'parse_coverage' in NodeAdapter.__dict__`` is ``False``, and
   ``NodeAdapter().parse_coverage(<an istanbul text table>)`` returns ``{}``.
   Verified by execution against ``src/invariant/adapters/node.py`` at a7ebbdc. In
   a real Node project, ``invariant pipeline coverage`` then reports no measurements
   for a suite that measured them.

   **Suspected cause:** ``src/invariant/adapters/node.py`` declares no
   ``parse_coverage`` at all and inherits ``Adapter.parse_coverage``, which returns
   ``{}`` unconditionally.

   **What makes this a defect rather than a gap.** ``generic`` returning ``{}`` is
   DECLARED — ``docs/architecture/overview.md`` says ``generic`` "executes literal
   commands from the manifest and parses nothing", so a project selecting it knows
   what it bought. ``node`` advertises ecosystem knowledge, ships a coverage command,
   and silently inherits the no-op. The contract the adapter layer states in the
   overview ("how to read a coverage number") is unmet without a word to the
   caller.

   **Sibling: BUG_011 (fixed, 15d309a) — same symptom, DIFFERENT cause.** BUG_011
   was a parser coupled to one table layout; this is no parser at all. The fix
   does not transfer and the entry is not a duplicate.

   **Affected:** ``adapters/node`` — per ``docs/architecture/overview.md``
   ("adapters/"), the layer that supplies "how to read a coverage number".
   ``coverage.meets_floor`` in ``core/rules`` is the consumer that goes inert.

   **Fix sketch / next step:** Needs a decision BEFORE code: which istanbul
   reporter to key on — the text table (``--coverage`` default, human-formatted and
   layout-coupled, which is exactly what made BUG_011 brittle), ``json-summary``
   (stable, machine-shaped, needs a reporter flag in the default command), or
   ``lcov`` (universal, but a different parse entirely). Then a repro against a real
   Node project. Deliberately NOT fixed inline during the 2026-08-14 sweep:
   writing an istanbul parser with no Node project to test it against would be
   inference reported as observation. Consider also whether ``carries_executable_code``
   is owed (the adapter declares none either, so it answers ``None`` — correct, but
   unexamined).

.. bug:: A Python session declaring its own test files gets an unsatisfiable gate
   :id: BUG_019
   :status: fixed
   :severity: medium
   :priority: high
   :area: adapters/python_
   :batch: unsatisfiable-gates
   :slug: touched-test-files-can-never-carry-a-coverage-row
   :residue: COMPLEMENTARY MECHANISM (AUD-40 salvage). A parallel session fixed this independently with a new Adapter.is_test_file fact (python/node/swift), consulted by _explain_absent_rows on the repo-relative path. It answers the conventional half of the question for a project whose coverage configuration measured_scope cannot read (.coveragerc, setup.cfg, non-Python ecosystems), so both mechanisms now coexist: configured scope first, naming convention second. That session also confirmed the entry's fix sketch was right to reject carries_executable_code — a test file is full of executable lines, so answering False there would assert the direction adapters/base.py forbids.
   :affects: COMP_ADAPTERS_PYTHON

   **Symptom:** A Python session whose change set includes its own test files gets
   a ``coverage.meets_floor`` verdict it cannot satisfy by any amount of work.
   Observed on this repository today:

   ::

        2 changed source file(s) have no coverage row — tests/test_adapters.py, tests/test_rules.py

   Nothing the session can do produces those rows, so the gate stays unmet for
   the life of the change. It fires on essentially every session in this
   repository.

   **Expected:** A gate reports something the actor it applies to can act on.
   Either the files it demands rows for are measurable, or the gate does not
   demand rows for files the configured reporter is defined never to measure.

   **Repro:** In this repository, edit any file under ``tests/`` alongside a source
   file and run ``invariant pipeline coverage`` (or advance to ``reviewing``, whose
   exit gates include ``coverage.meets_floor``). The touched test files are reported
   as changed source files with no coverage row.

   **Suspected cause:** Three correct behaviours meeting badly.
   ``PythonAdapter.source_files`` counts every ``.py`` as source, so test files enter
   the touched set. ``[tool.coverage.run] source = ["src/invariant"]`` in this
   project's config means test files are never measured, so no row can exist for
   them. And ``PythonAdapter`` declares no ``carries_executable_code`` override
   (verified: ``'carries_executable_code' in PythonAdapter.__dict__`` is ``False``),
   so it inherits the base ``None`` — "cannot tell", which per
   ``docs/architecture/overview.md`` correctly relaxes nothing. The doctrine is
   behaving EXACTLY as the overview describes it; the consequence is a demand
   that cannot be met.

   **Visible only because BUG_011's fix (15d309a) made Python coverage record for
   the first time.** Not a regression of that fix — the fix revealed a
   pre-existing disagreement between the touched set and the measurable set that
   no one could see while the parser produced nothing.

   **Affected:** ``adapters/python_`` (``source_files``, the absent
   ``carries_executable_code``) and ``core/rules`` (``coverage.meets_floor``, which owns
   the policy). Per ``docs/architecture/overview.md``, ``core/ → adapters/`` is the
   documented seam for exactly this question — "the rule keeps the policy; the
   adapter supplies the fact" — and this is a case where the adapter has no fact
   to supply and the rule has no way to know the reporter's scope.

   **Fix sketch / next step:** Needs a DECISION, not a quiet patch, and the
   decision is between two defensible answers:

   - **Measure the tests.** Widen the reporter's configured source so test files
     carry rows. Honest, but changes what the coverage number means and what the
     floor is measured against.
   - **Narrow the touched set to what the reporter can cover.** Have the rule (or
     the adapter) read the reporter's configured source and drop files outside it
     from the demand. Keeps the number's meaning; needs the rule to learn a fact
     it does not have today, which is an ``adapters/`` question by the overview's
     own seam.

   A third answer — a ``carries_executable_code`` override on ``PythonAdapter`` — is
   tempting and probably wrong: a test file DOES carry executable code, so
   answering ``False`` would be a lie told to relax a gate, which is the failure
   mode the base ``None`` exists to prevent.

   **Do not resolve this by silencing the gate.** The shape it produces — a
   blocking-class gate that no work can satisfy, trained around rather than met —
   is what BUG_002 (fixed) was filed against.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: tests/test_rules.py::TestTheReportersScopeIsNotDemandedOfFilesOutsideIt::test_a_touched_test_file_outside_the_reporters_scope_does_not_block — a touched test file blocked with a demand no work could satisfy
        forced: false
      - from: reproduced
        to: fixed
        reason: New Adapter.measured_scope supplies the reporter's configured directories; coverage.meets_floor stops demanding rows for files outside them. carries_executable_code deliberately NOT overridden. Every 'cannot tell' still blocks.
        forced: false

.. bug:: A block-level N/A is invisible on pipeline advance
   :id: BUG_020
   :status: fixed
   :severity: medium
   :priority: high
   :area: cli/main
   :batch: inert-instruments
   :slug: skipped-blocking-gates-are-silent-without-verbose
   :affects: COMP_CLI_MAIN

   **Symptom:** On ``invariant pipeline advance``, a blocking gate that went
   inapplicable is completely silent. The reader is not told the gate did not
   apply, is not told it was a ``block``-level gate, and cannot distinguish that
   from the gate having passed. A ``warn``-level gate going inert reads identically.

   **Expected:** Two distinctions reach the reader without ``--verbose``:

   - a gate that did not apply is visibly different from a gate that passed, on
     the ordinary ``pipeline advance`` output; and
   - a ``block``-level gate going inapplicable is visibly different from a ``warn``-level
     one, because the first means a gate the project chose to enforce did not run.

   **Repro:** Advance a pipeline in a state whose exit gates include a blocking
   rule that reports ``skipped`` (e.g. a docs-only or committed-tree shape). Nothing
   about that gate appears on stdout or stderr. Re-run with ``--verbose`` and the
   ``[skip]`` line appears.

   **Suspected cause — the renderer is NOT the site; two residual gaps in
   ``cli/`` and ``domains/pipeline`` are.** BUG_012's entry asked its fixer to decide
   this; the fixer checked rather than accepting the framing and found the lower
   layers already correct:

   - ``Evaluated.render`` (``src/invariant/core/rules.py``, near line 112) ALREADY
     marks ``[skip]`` distinctly from ``[ok]``.
   - ``summarise`` (``src/invariant/domains/pipeline.py``, near line 690) ALREADY
     counts "not applicable" separately from passed and failed.

   The gaps are above them:

   - ``report_verdicts`` (``src/invariant/cli/main.py:48``) drops skipped verdicts
     ENTIRELY unless ``--verbose``:
     ``if verdict.verdict.skipped and not getattr(args, "verbose", False): continue``.
     So the distinction the renderer draws never reaches stdout on the default path.
   - Nothing anywhere distinguishes a ``block``-level N/A from a ``warn``-level one.
     ``Verdict.skipped`` is a boolean and the level is dropped from the summary
     counts, so "a gate you chose to enforce did not run" and "an advisory gate
     did not run" are the same line and the same number.

   **This is why BUG_012 survived undetected as long as it did.** The docs gates
   went inert on a committed tree and the tool's own output was, by construction,
   incapable of saying so. Filing it separately because the fix is a reporting
   change in ``cli/`` and ``domains/pipeline``, not a rules change — a different
   sitting from the one that fixed BUG_012's rules.

   **Affected:** ``cli/`` (``report_verdicts``) and ``domains/pipeline`` (``summarise``).
   Per ``docs/architecture/overview.md``, ``core/rules`` keeps the reasoning and
   level is "a property of the verdict's *delivery*, not of the rule's logic" —
   which is precisely why the delivery layer is the one that must carry the
   level through, and today does not.

   **Fix sketch / next step:** Start at ``src/invariant/cli/main.py:48``. Decide
   what the default (non-verbose) ``pipeline advance`` output owes the reader for a
   skipped gate — likely: always print a skipped ``block``-level gate, keep skipped
   ``warn``-level gates behind ``--verbose``. Then decide whether ``summarise``'s
   "N not applicable" should be split by level. A red test asserting that a
   skipped blocking gate appears on the default ``pipeline advance`` output is the
   target. Sibling: BUG_012 (fixed, 15d309a).

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: tests/test_cli.py::TestSkippedGateVisibility::test_a_skipped_blocking_gate_is_reported_without_verbose — a skipped block-level gate produced no output at all
        forced: false
      - from: reproduced
        to: fixed
        reason: report_verdicts always prints a skipped block-level gate, render marks it [skip:BLOCK], summarise reports 'N not applicable (M blocking)'. cmd_check's drifted copy of the same filter folded into one predicate.
        forced: false

.. bug:: agents.design_agent_ran cannot be cleared by the agent it names
   :id: BUG_021
   :status: fixed
   :severity: high
   :priority: high
   :area: cli/main
   :batch: unsatisfiable-gates
   :slug: design-agent-cannot-clear-its-own-gate
   :residue: CAUSE CORRECTED. The entry's second mechanism does not exist. A probe repo showed a Task PostToolUse firing from the MAIN CHECKOUT records into the WORKTREE session's pipeline (agents_run == ['feature-architect']) with no path in the payload — Manifest.state_root is the git common dir, shared by every worktree, and the host session id resolves the session directly. BUG_010's residue is NOT a precondition and the instruction to settle it first is withdrawn. The entry's 'second inconsistency' is also a misreading: tdd.yaml's designing state declares roles: [design] and the gate is injected onto it by load_pipeline_machine, so there was one enforcement site, not two.; UNEXPLAINED. feature-architect absent from pipeline 67ca8b19's agents_run ENTIRELY, rather than merely late, could not be reproduced — the probe records the dispatch reliably under the same shape. Recorded as residue rather than folded into a cause it does not support.
   :affects: COMP_CLI_MAIN

   **Symptom:** ``agents.design_agent_ran`` is ``block`` in this project's manifest,
   and the design agent named by ``agents.roles.design`` cannot clear it. A
   ``feature-architect`` session cannot advance its own pipeline out of ``designing``
   without ``--force``.

   **Expected:** A gate that requires an agent to have run is satisfiable by that
   agent running. Either the dispatch evidence reaches the pipeline the work
   belongs to, or the gate does not apply to the agent it names.

   **Repro:** Run ``feature-architect`` on a task in a worktree session, then
   ``invariant pipeline advance red``. The gate refuses. Read
   ``.git/invariant/state/pipelines/<id>.json`` directly: ``agents_run`` contains
   ``["compliance-reviewer"]`` and nothing else.

   **Suspected cause — and note this CORRECTS the first explanation offered.** A
   design agent reported a timing race: PostToolUse for a ``Task`` fires only when
   the subagent RETURNS, so the agent's own dispatch is recorded after it has
   already handed back its report and can never precede its own advance. That
   ordering is real, but it is not what happened here. A compliance reviewer read
   the pipeline record directly and found ``feature-architect`` appeared nowhere —
   not in that pipeline, not in any pipeline, not anywhere under the state root.
   The dispatch evidence never arrived at all.

   Contributing mechanism: the parent dispatches subagents from the MAIN
   CHECKOUT, so the ``Task`` PostToolUse fires with the host cwd there while the
   pipeline belongs to a worktree session. A ``Task`` payload carries NO path — that
   is BUG_010's recorded residue — so nothing can attribute the dispatch to the
   right project or the right pipeline. Both mechanisms have to be settled: fixing
   the ordering alone would still record nothing.

   **A second, separate inconsistency worth capturing.** The gate is not wired as
   an ``exit_gate`` anywhere in ``src/invariant/data/machines/tdd.yaml`` — the
   ``designing`` state declares ``roles: [design]`` and no ``exit_gates`` at all
   (verified at a7ebbdc). So the rule blocks ``invariant check`` without blocking
   pipeline advancement. Whatever the fix, the gate should be enforced in one
   place with one meaning, not in two places with two.

   **This is the ``--force``-training shape BUG_002 was filed against.** A blocking
   gate that the correct actor cannot satisfy does not enforce the discipline; it
   teaches the operator that ``--force`` is the normal way past a gate, which
   disarms every other gate in the same breath.

   **Affected:** ``hosts/claude_code`` (the ``Task`` PostToolUse payload and what it
   can carry), ``cli/`` context construction (which project and pipeline a dispatch
   is attributed to), ``core/rules`` (``agents.design_agent_ran``), and
   ``src/invariant/data/machines/tdd.yaml`` (the exit-gate wiring). Per
   ``docs/architecture/overview.md``, deciding which project a hook payload writes
   to is CLI context construction, not a host adapter's business.

   **Fix sketch / next step:** BUG_010's residue is the precondition — it already
   records that a cross-root dispatch is attributed to the HOST's pipeline and
   that a ``Task`` payload carries no path, and calls the wide half ADR-worthy.
   Settle that first; this entry cannot be fixed around it. Then decide the
   ordering question separately: whether a design agent's own dispatch can ever
   satisfy a gate it must pass before returning, or whether the gate belongs at a
   different point in the pipeline. A red test should assert that a dispatch made
   from the main checkout against a worktree session's pipeline lands in that
   pipeline's ``agents_run``.

   **Siblings:** BUG_002 (fixed) — the unsatisfiable-blocking-gate shape.
   BUG_010 (fixed, residue owed) — the attribution mechanism this depends on.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'Probe repo reproduced the block: a design agent advancing designing->red from inside its worktree gets [BLOCK] agents.design_agent_ran. Red test: test_the_design_gate_does_not_sit_on_the_design_state'
        forced: false
      - from: reproduced
        to: fixed
        reason: 'ADR-003 D1/D2: AGENT_ROLE_GATES re-keyed so both agent gates hang off the review role''s exit. Verified end-to-end against a probe repo: designing->red no longer blocks, reviewing->committed still does.'
        forced: false

.. bug:: overview.md states a dependency rule the code no longer keeps
   :id: BUG_022
   :status: fixed
   :severity: low
   :priority: medium
   :area: docs/architecture
   :slug: hosts-hook-schema-claim-is-already-false
   :affects: COMP_CORE_RULES, COMP_HOSTS_CLAUDE_CODE

   **Symptom:** ``docs/architecture/overview.md``, under "Component responsibilities
   → ``hosts/``", states: "Nothing above ``hosts/`` is aware of hook schemas." That
   sentence is already false, and a shipped fix now depends on it being false.

   **Expected:** The authoritative dependency map states a rule the code keeps, or
   states the rule it actually keeps. A dependency rule readers are told to
   enforce must not be one the codebase has already, deliberately, departed from.

   **Repro:** Read the sentence, then read the two sites above ``hosts/`` that know
   hook payload keys: ``Context.session_for_payload`` reads ``tool_input.file_path``,
   and ``core/rules.RuleContext`` reads ``tool_name``, ``tool_input`` and ``target_path``.
   Both are above ``hosts/``. Confirmed at a7ebbdc.

   **Suspected cause:** The sentence was accurate when written and was not
   revisited when the seam was widened. BUG_010's narrow fix (faa4de0) now DEPENDS
   on that seam — the payload's target path is what lets a hook attribute evidence
   to the right project — so the claim is materially misleading rather than merely
   stale. A reader enforcing it as written would read faa4de0 as a layering
   violation and could "correct" a shipped fix by reverting it.

   **This is a docs/ADR decision, not a code defect.** ``feature-architect``
   declined to rewrite the sentence on the ground that weakening a stated
   dependency rule is an architectural decision, not a Boy-Scout edit. That
   judgement is recorded here as correct: the repair is to DECIDE what the rule
   now is, and the decision is the deliverable.

   **Proposed wording, recorded as a proposal needing accept/reject — not as the
   answer:**

      ``hosts/`` own hook *transport* — payload parsing, response shape, settings
      wiring. A normalised payload dict travels upward and ``cli/`` and ``core/rules``
      read documented keys from it (``tool_name``, ``tool_input.file_path``); no layer
      above ``hosts/`` knows the hook *protocol*.

   If accepted, the documented keys become a contract with a name, and a host
   adapter for a different agent host has an explicit normalisation obligation
   rather than an implied one. If rejected, the alternative is to restore the
   original rule by moving the payload reads back below the seam — which means
   re-opening faa4de0's fix, and that cost belongs in the decision.

   **Affected:** ``docs/architecture/overview.md`` (the "hosts/" responsibilities
   section and the dependency rule under the layer diagram); possibly a new or
   amended ADR, since the layer contract is ADR-001 material. ``cli/`` and
   ``core/rules`` are the layers the decision governs.

   **Fix sketch / next step:** Accept, reject, or amend the proposed wording, then
   edit ``overview.md`` to match — and if accepted, name the normalised payload keys
   in one place so both the doc and the host adapters point at the same list. No
   code change is implied by the accept path. Sibling: BUG_010 (fixed, faa4de0) —
   the fix whose seam this sentence contradicts.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: tests/test_rules.py::TestTheNormalisedPayloadContract::test_the_documented_rule_is_the_one_the_overview_states — overview.md stated a dependency rule two sites above hosts/ already broke
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Proposed wording accepted: hosts/ own hook transport, documented keys travel upward, no layer above knows the protocol. Keys named once as core.rules.NORMALISED_PAYLOAD_KEYS, referenced by the doc and the host module, pinned by a test.'
        forced: false

.. bug:: The host session id reaches the pipeline by two routes, one silent
   :id: BUG_023
   :status: filed
   :severity: low
   :priority: low
   :area: domains/pipeline
   :batch: adr-002-followups
   :slug: two-routes-to-the-host-session-id
   :affects: COMP_DOMAINS_PIPELINE

   **Symptom:** The pipeline baseline and the pipeline deltas reach the same fact
   — the host session id — by two different routes, and one of them fails
   silently. A caller that omits ``session=`` on the delta route records
   ``usage: None`` for every transition of that pipeline, with no error, no warning
   and no test failure. The telemetry simply stops, and the report renders the
   absence honestly, which is exactly why nobody would notice.

   **Expected:** One route to one fact. A caller cannot silently switch telemetry
   off by omitting an optional argument.

   **Repro:** None possible, and none should be manufactured. There is exactly one
   caller of each route today and both do pass the fact: ``cmd_pipeline_start``
   passes ``host_session_id=session.host_session_id``, and ``cmd_pipeline_advance``
   passes ``session=session.to_dict()``, which carries ``host_session_id`` because
   ``Session.to_dict`` is ``asdict``. Nothing is broken now. Inventing a caller that
   does not exist would prove nothing about this codebase. Re-confirmed by source
   reading at a7ebbdc.

   **Suspected cause:** ``src/invariant/domains/pipeline.py`` takes
   ``host_session_id`` as an explicit argument on the baseline path (near line 185,
   formerly cited as :170) and digs the same fact out of an optional
   ``session: dict | None`` on the delta path (near line 364, formerly :347). The
   optional parameter is the hazard: absent it, ``_reading`` is handed ``None`` and
   returns no watermark, which is indistinguishable from a transcript that could
   not be read.

   **Affected:** ``domains/pipeline`` — per ``docs/architecture/overview.md``, one of
   the two machines that record watermarks under ADR-002 D3.

   **Fix sketch / next step:** A signature change with one call site — but it is
   BLOCKED today, and the block is the reason this is its own entry rather than a
   five-minute repair. Every way of collapsing the two routes to one breaks a live
   acceptance fixture belonging to another entry:

   - Making ``advance`` take the host session id explicitly stops the BUG_013,
     BUG_014 and BUG_016-F1 repro fixtures recording telemetry at all — their
     green controls (``200 tokens``, ``frozen.reads == 3``, the measured ``150``) would
     fail.
   - It also requires rewriting
     ``test_the_host_session_id_comes_from_the_session_record``, whose entire
     subject is the route being removed.
   - Making ``start`` take the session dict instead breaks BUG_013's precondition
     assertion ``usage_at_start == Usage(input=100)``.

   So this needs its own sitting, AFTER those fixtures stop being four entries'
   live acceptance criteria and become ordinary regression tests. A fixer who
   takes it before then will be rewriting other entries' proofs, which is how
   proofs quietly stop proving anything.

   **Filed separately from BUG_016 deliberately.** This is BUG_016's sub-finding
   F3. BUG_016 is ``reproduced`` with F3 recorded as residue precisely because F3
   carries no test proof and cannot; keeping it only as residue means it either
   blocks BUG_016 from ever closing or is lost when BUG_016 closes. It is here so
   that neither happens. BUG_016's other four sub-findings are fixed and are not
   in scope for this entry. Graded low, as F3 was: a latent hazard, not a present
   defect.

.. bug:: The pre-instrumentation cause is still printed without evidence
   :id: BUG_024
   :status: fixed
   :severity: low
   :priority: low
   :area: domains/stats
   :batch: adr-002-followups
   :slug: pre-instrumentation-sentence-for-an-unjoined-record
   :affects: COMP_DOMAINS_STATS

   **Symptom:** The telemetry report still prints the pre-instrumentation
   explanation for a missing breakdown in one case where it has no positive
   evidence for it: a stats record with NO invariant session joined to it. After
   a7ebbdc it is the ONLY remaining place where that sentence is printed without
   evidence. The same holds for a record written before ``states_reason`` existed.

   **Expected:** Every rendered cause is entailed by the data behind it — the
   standard BUG_013 established. Where nothing is known about when a host session
   ran, the report should say that nothing is known, not name a cause it cannot
   support.

   **Repro:** Render a report over a stats record with no joined invariant session
   (and, separately, over a record predating ``states_reason``). The
   pre-instrumentation sentence appears in both, with nothing in the record
   capable of distinguishing "ran before instrumentation" from "ran after, and we
   simply never joined it".

   **Suspected cause:** Deliberate, and defensible as far as it goes. When
   a7ebbdc fixed BUG_013 the fixer retained the original wording for this one
   case on the ground that nothing is known about when that host session ran, so
   the pre-instrumentation sentence is the only candidate wording that does not
   assert MORE than the data supports. The residue is that it still asserts
   SOMETHING the data does not support, and it is now the lone exception to a rule
   the rest of the renderer keeps. The fixer flagged it as curator scope rather
   than fix scope for that change.

   **Affected:** ``domains/stats`` — per ``docs/architecture/overview.md``, the
   rendered report and the join from a host session to an invariant session.

   **Fix sketch / next step:** This is a wording-and-evidence decision, not a
   mechanism repair. Either find a fact that discriminates the two cases (a record
   timestamp against the instrumentation's own landing, if one is recoverable
   without printing a host identifier — see ADR-002 D7), or replace the sentence
   for the unjoined case with one that claims only what is known: no invariant
   session is joined to this record, so nothing can be said about its states.
   Prefer the second unless the first is cheap; a report that says less is the
   whole point of BUG_013's fix. A red test should assert that the
   pre-instrumentation sentence does NOT appear for an unjoined record.

   **Sibling:** BUG_013 (fixed, a7ebbdc) — same file, same standard, the sliver
   its fix deliberately left behind. Graded low because the report is already
   honest in every other case and the remaining claim is soft.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: tests/test_stats_domain.py::TestTheUnjoinedRecordDoesNotFabricateAHistory::test_an_unjoined_record_is_not_called_pre_instrumentation — the pre-instrumentation cause was printed for a record with no session joined
        forced: false
      - from: reproduced
        to: fixed
        reason: New DECLINED_UNJOINED code and sentence; the unknown-reason fallback no longer claims a history. The pre-instrumentation sentence is now reachable only from its own evidence-backed code.
        forced: false

.. bug:: A Swift session touching a stored-property-only type gets an unsatisfiable gate
   :id: BUG_025
   :status: fixed
   :severity: medium
   :priority: high
   :area: adapters/swift_xcode
   :batch: unsatisfiable-gates
   :slug: stored-property-only-types-can-never-carry-a-coverage-row
   :affects: COMP_ADAPTERS_SWIFT_XCODE

   **Symptom:** A Swift session whose change set includes a type declaring only
   stored properties gets a ``coverage.meets_floor`` verdict it cannot satisfy by
   any amount of work. Observed on a consuming project, on a change whose only
   edit to both named files was a **comment**:

   ::

        2 changed source file(s) have no coverage row
        Heap/Domain/Entities/WatchedOrigin.swift,
        Heap/Protocols/SmartFeatures/WatchedOriginRefreshServiceProtocol.swift

   The first is a ``struct`` of six ``let``/``var`` declarations wrapping a bare nested
   ``enum``; the second is a bare ``protocol`` alongside a ``struct`` of five ``let``
   declarations. Neither can earn a row in any lane — confirmed by running the
   gate's own whole-scheme lane (~14.5k tests), not a narrow one.

   **Expected:** ``_explain_absent_rows`` sets aside files that positively cannot
   earn a row, and its own comment names the intended cases — *"a bare protocol,
   an enum of cases, a file this change DELETED"*. A stored-property-only type
   belongs to the same family and was not being recognised.

   **Repro:** Ask the Swift adapter directly:

   .. code:: python

      from invariant.adapters.swift_xcode import _swift_carries_executable_code as f
      f("struct Outcome: Equatable { let id: Int\n let name: String }")   # True, wrongly

   **Cause:** ``_INERT_BLOCK_HEADER`` matched ``protocol|enum`` only. A ``struct`` /
   ``class`` / ``actor`` block therefore reached the residue check, where its own
   content — the member declarations — reads as identifiers outside
   ``_ACCESSOR_ONLY``, and the file answers "carries code". The answer is wrong for
   the same reason it is right for a protocol: **Swift permits no statement in a
   type body.** The two shapes were never different; only one was listed.

   **Not the same defect as BUG_019, and the distinction is load-bearing.** There
   the file (a test file) genuinely *does* carry executable code and the honest
   fix was ``measured_scope`` — narrowing what the reporter is pointed at.
   Overriding ``carries_executable_code`` there would have been "a lie told to relax
   a gate". Here the file carries none, so the fact itself was wrong, and fixing
   the fact is the whole repair.

   **Fix:** ``_INERT_BLOCK_HEADER`` extended to ``protocol|enum|struct|class|actor``.

   Skipping a type's HEADER is not skipping its body, and three properties keep
   the dangerous direction closed:

   - every ``func``, ``init``, ``deinit``, ``subscript``, computed property and property
     observer inside is its own block, yielded by ``_brace_blocks`` with its own
     header, and still judged on it;
   - a stored property with an initialiser expression (``var limit: Int = 200``) is
     caught file-wide by ``_INITIALISED_BINDING`` *before* any block is classified;
   - ``extension`` is deliberately NOT added — its residue can be a member list that
     only a body could explain.

   ``class`` and ``actor`` ship with ``struct`` rather than after it: the reasoning is
   identical, and a stored-property-only model class is the same shape wearing a
   different keyword.

   **Mutation-scored, not asserted.** With the regex reverted the three new inert
   cases fail and the three body-reach controls (``init`` body, ``deinit`` body,
   ``actor`` method body) stay GREEN in both states — the controls exist to catch a
   *wrong* version of this fix, one that skipped whole type bodies including their
   nested blocks.

   **Affected:** ``adapters/swift_xcode`` (``_INERT_BLOCK_HEADER``,
   ``_swift_carries_executable_code``). ``core/rules`` unchanged: the rule already
   owned the policy and already asked the adapter the right question — the adapter
   was answering it wrongly.

   **Cost of not fixing it, recorded because it is the point.** The consuming
   project's rule is that a wedge is a recording defect to fix, never a reason to
   force or lower a gate. Left standing, this one's only exits were a waiver or
   reverting two comment corrections — and those comments cited a table and a type
   that the very change under review deletes. A gate no work can satisfy makes the
   stale citation the cheaper option, which is the failure mode BUG_002 was filed
   against.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: tests/test_adapters.py::TestCarriesExecutableCode::test_a_stored_property_only_struct_carries_no_executable_code — a value type of declarations answered True, so its absent coverage row kept blocking
        forced: false
      - from: reproduced
        to: fixed
        reason: _INERT_BLOCK_HEADER extended to struct|class|actor. Skipping a type's HEADER is not skipping its body — func/init/deinit/subscript/accessor blocks are still judged on their own headers, and an initialised binding is still caught file-wide. Mutation-scored, controls green in both states.
        forced: false

.. bug:: The coverage floor has no way to excuse a test target or a comment-only diff
   :id: BUG_026
   :status: fixed
   :severity: medium
   :priority: high
   :area: core/rules, adapters/base, adapters/swift_xcode
   :batch: unsatisfiable-gates
   :slug: coverage-floor-cannot-excuse-a-test-target-or-a-cosmetic-diff
   :affects: COMP_CORE_RULES, COMP_ADAPTERS_BASE, COMP_ADAPTERS_SWIFT_XCODE

   **Symptom.** A consuming project finished a phase — built, reviewed,
   mutation-scored, docs rebuilt — and ``coverage.meets_floor`` blocked on eight
   files, of which seven were blocked for reasons no correct work could clear:

   ::

        8 file(s) below the 90% floor
        HeapPreviewRoot.swift 70.93%, ImportDiagnosticsScreen.swift 1.63%,
        WatchLiveSection.swift 30.82%, AppDependenciesWiringTests.swift 89.55%,
        BugPendingInboxUnreachableReproTests.swift 88.35%,
        UIContractSurfaceLintTests.swift 63.25%  (+2 more)

   Two distinct defects wearing one verdict.

   **Defect 1 — a test target has no expressible exemption.** Five of the eight
   are the project's own test files, every one of them lowered *by deleting
   covered lines*. The uncovered remainder in this family is, measured rather
   than assumed: ``XCTAssert`` failure-message expressions (an ``@autoclosure``, so
   code that runs only when the assertion FAILS) and protocol-conformance stubs a
   capturing fake must declare but no test calls. **The better the diagnostics, the
   further below the floor the suite sinks, and the cheapest way to clear the gate
   is to delete explanation.**

   The consuming project enumerated every manifest lever and followed each to
   where it fails: ``coverage_min`` and the rule LEVEL are global and would weaken
   the floor for source files too; ``coverage_scope`` not-``touched`` is strictly
   worse; narrowing ``commands.coverage_report`` converts a below-floor block into
   a **no-row** block, because the no-row guard correctly refuses a touched
   source file with no row; and the declared file set only ever ADDS. Its
   conclusion — "the remedy is an ENGINE change, not a project change" — was
   correct, and this entry is that change.

   **Defect 2 — a comment-only diff is held to a floor it cannot move.** Two of
   the eight were edited by this change in exactly one way: deleting a citation of
   a symbol the same change deletes.

   .. code:: diff

      -//  new items NEVER auto-download — they queue into pending_imports
      +//  new items NEVER auto-download — they queue for review

   Coverage is a property of executable lines. That diff moved no number and can
   move none, so the floor is demanding work that cannot affect the thing being
   demanded. It is not a rare shape either: **a change that deletes a symbol must
   rewrite every comment that cites it**, so this arrives on precisely the changes
   that deserve the least friction — and the cheapest way out is to leave the
   stale citation, which is worse than the debt being enforced.

   **Fix.** Two answers, because they are two different claims.

   - **Policy** — ``methodology.gates.coverage_exclude``, a list of glob patterns
     under the same gates block. Empty by default. A project states out loud that
     test targets are not held to the production floor, and the statement lives in
     a reviewable line of a diff instead of in each author's memory.
   - **Fact** — ``Adapter.executable_text(source)``, the third fact this gate needs
     and cannot derive, alongside ``carries_executable_code`` and ``measured_scope``.
     Comparing the base and working revisions through it answers "did this change
     alter any line a coverage run could measure". ``None`` is "cannot tell" and
     relaxes nothing, per the same asymmetry the other two carry.

   Three properties both share, each one a lesson this tracker already carries:

   1. **Excluded from the FLOOR, never from the MEASUREMENT.** The row is still
      required and the number still reported; only the below-floor verdict skips.
   2. **Both run AFTER the no-row check.** Without that ordering, ``coverage_exclude``
      plus a narrowed report would hide a path-spelling mismatch — the exact
      failure the no-row guard exists for. Pinned by
      ``test_an_excluded_path_with_no_row_still_blocks``.
   3. **The verdict names every path it set aside, with its number and what
      matched it.** An exclusion nobody can see is indistinguishable from a gate
      that did not run.

   **The sharpest trap, and it is in the Swift reader.** ``executable_text`` must
   NOT reuse ``_strip_comments_and_strings``, which blanks a string literal's
   CONTENTS. That is right for "is there a statement here" and fatal for "did the
   code change": ``"a"`` and ``"b"`` reduce to the same text, so a real edit would
   read as cosmetic — the one mistake this answer must never make. A dedicated
   reader copies literals verbatim and returns ``None`` on any input it cannot read
   to the end. **Proven, not asserted:** swapping the wrong reader in reds
   ``test_a_changed_string_literal_still_blocks`` and nothing else.

   **Mutation-scored.** With both helpers disabled, the four behaviour tests fail
   and all six controls stay GREEN — unexcluded-path-still-blocks,
   no-row-still-blocks, no-exclude-configured, changed-statement,
   changed-literal, no-base-revision. Controls that hold in both states are what
   tells this fix from a silencing.

   **Rejected — make the floor smarter about ``@autoclosure`` thunks.** Tempting:
   the gap is a known shape rather than arbitrary uncovered code, and it would
   keep test files under a real floor. It puts Swift-expression-level knowledge
   into a generic coverage rule, it is unfixably approximate (``xccov`` reports
   "implicit closure" regions, not "this one is an assertion message"), and it
   would still leave the conformance-stub half of the gap. A per-path exclusion is
   honest about being a policy decision instead of pretending to be a measurement.

   **Related:** :need:`BUG_019 <BUG_019>` is the same family from the Python side and
   its distinction is load-bearing — there the file genuinely DOES carry
   executable code, so ``measured_scope`` was the honest fix and a
   ``carries_executable_code`` override would have been "a lie told to relax a
   gate". :need:`BUG_025 <BUG_025>` is the third: a fact that was simply wrong.
   All three are the same failure mode — a blocking gate no work can satisfy —
   reached by three different routes.

   **What this does NOT excuse, deliberately.** The eighth file in the reported
   case is a view at 70.93% against a floor of 90, pre-existing at 70.97% and
   nudged down by two real wiring lines. It is a genuine coverage debt on a real
   code change, no rule here touches it, and it was landed under an explicit
   waiver naming the measurement and the date. That is the correct outcome: the
   gate should still be asking that question.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: tests/test_rules.py::TestPathsTheManifestHoldsOutOfTheFloor and ::TestAChangeThatAlteredNoCodeIsNotHeldToTheFloor — five test files and two comment-only edits blocked with a demand no correct work could satisfy
        forced: false
      - from: reproduced
        to: fixed
        reason: methodology.gates.coverage_exclude (policy, glob, manifest-visible) plus Adapter.executable_text (fact, comment-only diffs). Both exclude from the FLOOR and never from the MEASUREMENT, both run AFTER the no-row guard, both name what they set aside. Mutation-scored; six controls green in both states.
        forced: false

.. bug:: privacy.clean never screens a write made through the shell
   :id: BUG_027
   :status: fixed
   :severity: critical
   :priority: high
   :area: core/rules
   :batch: inert-instruments
   :slug: privacy-gate-never-screens-a-shell-mediated-write
   :affects: COMP_CORE_RULES

   **Symptom.** ``privacy.clean`` is a ``pre-tool-use`` rule and ``_pre_tool_use`` runs
   it on **every** tool call, ``Bash`` included — the hook is not tool-filtered. It
   still screens nothing that a shell command writes. In one long session in a
   consuming project the majority of that project's backlog edits were made
   through ``python3`` heredocs and ``sed``, and not one of them was scanned. Each of
   those calls got the same line:

   ::

      [skip] privacy.clean: nothing in privacy scope was written here

   The sentence is false on that route. It is also indistinguishable from a pass.

   **Expected.** A gate that did not look says that it did not look. A write that
   lands in a path the project put in ``privacy.scope`` is screened whatever tool
   carried it, or the verdict names the route it could not follow.

   **Cause — confirmed by source reading, not suspected.** The hook is fine; the
   gap is inside the rule.

   - ``_privacy_clean`` extracts content only under ``if ctx.is_edit``.
   - ``RuleContext.is_edit`` is ``tool_name in EDIT_TOOLS``, and ``EDIT_TOOLS`` is
     ``{"Edit", "Write", "MultiEdit", "NotebookEdit"}``. A ``Bash`` payload is not in
     it.
   - ``RuleContext.target_path`` reads ``file_path`` or ``notebook_path``. A ``Bash``
     payload carries neither — it carries ``command`` — so there is no target to
     test against the scope even if the content were reachable.
   - ``_edit_content`` reads ``content``, ``new_string``, ``new_source`` and a
     ``MultiEdit`` list. A shell command's text is under none of those keys.
   - With nothing appended to ``scanned``, the rule falls through to
     ``Verdict.na("nothing in privacy scope was written here")``.

   **The rule already reads a Bash payload — for the other half of its job.**
   When ``commit_messages`` is in ``privacy.scope``, ``_privacy_clean`` calls
   ``_extract_commit_message(str(ctx.tool_input.get("command", "")))``, and that
   branch is careful: a ``-F`` file it cannot read fails CLOSED, because "an
   unanswered question is not a pass" (AUD-09). So the route exists, is used, and
   was built with exactly the right instinct. **A shell call that commits a file
   is scanned; a shell call that writes one is not.**

   **The same blind spot was patched once already, in the same shape.**
   ``_edit_content``'s own docstring records AUD-06: only ``content`` and
   ``new_string`` were read, so personal data that blocked a ``Write`` passed through
   a ``MultiEdit`` untouched. That fix widened coverage from two tool names to
   four. It never crossed from **tool identity** to **content landing**, which is
   the axis the defect is actually on. Widening a list of tool names cannot close
   this, because the next route will not be a tool name either.

   **Why this is critical when the other open defects here are not.** Every other
   engine defect in this tracker fails CLOSED: it refuses work, prints a refusal,
   and the session argues with it. This one fails OPEN — deterministically, on
   every shell-mediated write, on a rule the consuming project designates hard
   ("no personal data in anything written down"), while printing a sentence
   asserting the opposite. And it is the one failure here that cannot be undone
   after the fact: a skipped gate is recovered by running it, and nothing
   un-writes an identifier out of a published history.

   **No leak shipped, and that is not mitigation.** Three separate reviewers each
   noticed and ran ``invariant privacy scan`` by hand. Hand-verification is
   precisely the habit the gate exists to make unnecessary — the gate's value is
   that nobody has to remember, and a control that only works when its subject
   independently remembers to do the check is not a control.

   **Fix.** Two directions, not exclusive.

   - **Screen where content LANDS, not where an edit tool announces itself.** A
     post-tool-use scan of what the call actually wrote, or a pre-commit scan
     over the diff, catches every route — including ones nobody has enumerated:
     ``tee``, an in-place editor, a generator that emits a doc. This is the answer
     that stops the axis from producing a third instance.
   - **Make the skip honest.** "not screened: this turn made no Edit or Write" is
     a very different sentence from "nothing in privacy scope was written here",
     and only the first is true. Worth doing even alongside the first fix, for
     BUG_020's reason: an N/A a reader cannot tell from a pass is worse than a
     refusal.

   A third, narrower answer — extend the existing ``Bash`` branch to scan the whole
   ``command`` string when the working directory is in scope — is cheap and would
   have caught the observed case, because a heredoc carries its payload in the
   command text. It is worth taking as an interim ONLY: it scans the
   *instruction*, not the *content that landed*, so it misses ``sed -i``, a ``cp``, a
   generator, and anything reading from a file.

   **Rejected — treat ``is_edit`` as the definition of "a write" and document the
   limit.** It leaves the rule's own message false, which is the part that made
   this survive a long session unnoticed. It also institutionalises three further
   blind spots (below) rather than deciding about them.

   **Same predicate, three more rules — source-confirmed here, NOT observed in
   the wild.** ``session.isolated_before_edit``, ``session.no_edit_in_main_checkout``
   and ``pipeline.started_before_edit`` all open with
   ``if not ctx.is_edit: return Verdict.na()`` and all three resolve their subject
   through ``RuleContext.target_path``, which a ``Bash`` payload cannot populate. On
   that reading a shell-mediated write is invisible to the isolation guarantee as
   well as to the privacy one. It is recorded here rather than filed separately
   because it is the same predicate and the same seam, and whoever fixes this
   should decide deliberately whether the answer covers all four rules or only
   this one. It is a reading, not an observation: the agent host has directory
   protections of its own, and no one has yet run the experiment that separates
   them.

   **Related:** :need:`BUG_001 <BUG_001>` is the same polarity — the pre-tool-use
   hook failing open disarms every gate — and its lesson applies here: the
   severity comes from the deployment, because a control nobody can see fail is
   one nobody re-checks. :need:`BUG_020 <BUG_020>` is the reading problem, a
   block-level N/A that is invisible without ``--verbose``.
   :need:`BUG_012 <BUG_012>` is the same shape again: a gate returning N/A on a route
   it was written for. :need:`BUG_032 <BUG_032>` may be downstream of this one — a
   curator that wrote through a shell would leave no ``target_path`` for the
   exemption to consult.

   **Provenance.** Found by a consuming project during one long session, by
   reading this repository's source after noticing that no privacy verdict ever
   appeared for a heredoc write. Reported 2026-08-17. **Not reproduced in this
   repository's own suite:** there is no test here that drives ``_privacy_clean``
   with a ``Bash`` payload carrying personal data, and writing one — asserting a
   FAIL, not an N/A — is the first step.

   **Repro proof:** ``tests/test_rules.py::TestPrivacyReachesAShellMediatedWrite::test_BUG_027_a_leak_written_by_a_shell_heredoc_is_blocked``,
   committed 233224f (2026-08-20) — drives ``privacy.clean`` with a ``Bash`` payload
   whose ``command`` is a ``cat`` heredoc writing an email address into
   ``docs/architecture/new.md``, a path inside the default ``privacy.scope``. It
   asserts the verdict is a blocking FAIL. Observed today at HEAD:

   ::

      Verdict(ok=True, message='nothing in privacy scope was written here',
              detail='', fixes=(), skipped=True)   level=Level.BLOCK

   The sibling ``Write``-payload test asserting the same content at the same path
   blocks correctly, so the only variable between the green control and the red
   repro is which tool carried the write — which is the axis the entry names.

   The test is wrapped in a **strict** ``xfail``, so the suite stays green while the
   defect is live and fails the moment anyone fixes it. **Handoff note:** the
   ``reproducing`` state's exit gate ``test.new_test_fails`` reads a RECORDED FAILING
   run, and the wrapper makes the suite green, so it cannot be satisfied while the
   marker is in place. The fixing session's first two steps are therefore: delete
   the ``xfail`` marker (the assertion must stay byte-identical), then
   ``invariant pipeline test --phase reproducing`` to record the genuine red — and
   only then write the fix.

   **Confirmed at HEAD while reproducing, and deliberately NOT covered by this
   test:** the same ``if not ctx.is_edit`` predicate opens
   ``session.isolated_before_edit`` (rules.py:315), ``session.no_edit_in_main_checkout``
   (rules.py:371) and ``pipeline.started_before_edit`` (rules.py:793); the first two
   return a BARE ``na()`` with no reason at all. The entry called that "a reading,
   not an observation" — it is now verified source fact. One test, one symptom:
   this repro does not assert it, and widening the fix to cover those three is a
   scoping decision for the fixer, not something this proof licenses.

   .. rubric:: Fix

   **Screens what LANDED, at the commit boundary.** ``privacy.clean`` now scans the
   **staged content** of a ``git commit``, path by path, against the same
   ``privacy.scope`` rules an edit's target is tested by. That covers every write
   route at once — heredoc, ``sed -i``, ``tee``, ``cp``, a generator emitting a
   document, and the ones nobody has enumerated — because nothing in a staged
   blob records which tool produced it. Scanning the Bash ``command`` string was
   considered and rejected as the primary fix: it screens the *instruction*, not
   the content that landed, and misses every route that writes from a file.

   Properties worth knowing, each with a test:

   - The **whole staged blob**, not the diff's added lines, because an exemption
     marker reaches a block (BUG_045) and a marker on an unchanged line above an
     addition would otherwise be invisible.
   - A staged set or blob that **cannot be read fails closed**, per AUD-09.
   - A **deleted** path is excluded before any blob is read, so removing a
     document is not a fail-closed; **undecodable bytes** count as looked at with
     nothing to find, the answer ``scan_file`` already gives.
   - ``_is_git_commit`` is **token-aware and shared** by both halves of the rule, so
     ``git add -A && git commit -m …`` is a commit, ``git config commit.gpgsign`` is
     not, and the message half and the content half can never disagree about
     whether a given call is one.

   **The symptom was worse at the commit than the entry recorded.** The verdict
   there was not a skip but a clean PASS — ``scanned commit message: no matches`` —
   over a commit carrying an address into a scoped path. The gate vouched, having
   read only the message.

   **Skip honesty (the entry's second direction), done.** Each route now states
   the limit that applies to it, and the three sibling rules got a stated reason
   in place of their bare ``na()``. Their **enforcement is deliberately unchanged**:
   teaching them to refuse shell commands could wedge a live session and is its
   own scoped decision, recorded in ``manifest.md`` rather than left silent.

   **Acceptance test rewritten to the boundary.** The original repro
   (``test_BUG_027_a_leak_written_by_a_shell_heredoc_is_blocked``) pinned a block at
   WRITE time, which the commit-boundary fix correctly does not deliver, so it was
   replaced — not weakened — by
   ``TestPrivacyScreensWhatLandedAtTheCommitBoundary``, which asserts the same
   symptom where the fix acts. Its honest-skip half survives as
   ``test_a_bash_call_that_is_not_a_commit_says_it_was_not_screened``, driving the
   identical heredoc payload. The new acceptance test was confirmed red for the
   right reason before any production code changed.

   **Found while fixing this:** :need:`BUG_047 <BUG_047>` — ``privacy.clean`` is named
   as an exit gate by five state machines and screens nothing in any of them,
   because a transition carries no tool payload. Filed rather than folded in.

   **BUG_032 is not resolved by this.** That defect is in the isolation guard's
   own handling of work-item paths, and this change deliberately does not teach
   the isolation rules about shell-mediated writes.

   **Status.** Fixed. The code fix landed in 7c77a1e ("BUG_027 fix —
   privacy.clean screens what landed, at the commit boundary"); this immediately
   following commit records the ``reproduced → fixed`` transition against that
   hash, since a commit cannot cite its own.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: null
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Screens what landed, at the commit boundary: privacy.clean now scans the whole staged blob of every in-scope path on a git commit, fails closed on an unreadable index or blob, and the skip sentences for it and three sibling rules now state the limit that actually applies. Landed 7c77a1e.'
        forced: false

.. bug:: A dispatched agent is recorded against the wrong pipeline, or nowhere
   :id: BUG_028
   :status: fixed
   :severity: high
   :priority: high
   :area: hosts/claude_code, domains/pipeline, domains/session, cli/main
   :batch: inert-instruments
   :slug: agent-dispatch-recorded-against-the-wrong-pipeline
   :residue: Fix part (3), untouched: reorder SessionDomain.teardown so the transition is attempted before the worktree and branch are removed, and make build_context/cmd_context distinguish 'nothing to say' from 'cannot tell who you are'.; Phantom ownership: SessionDomain.resolve returns an 'origin' guess as an ordinary Resolution and build_context renders it identically to a bound owner. Evidence writes now refuse it\; DISPLAY still does not distinguish it. Live in this repository right now — session a3ee3426 displays as owned while its host_session_id reads None.; Second foreign-write mechanism, unfixed: a pathless DISPATCH from a host that owns a session is filed against that session regardless of where the agent works. Confirmed source of the backlog-curator/general-purpose rows on d5c8605b and c143efd8. The actor route now refuses this shape\; the dispatch route still accepts it.
   :affects: COMP_HOSTS_CLAUDE_CODE, COMP_DOMAINS_PIPELINE, COMP_DOMAINS_SESSION, COMP_CLI_MAIN

   **Symptom.** A subagent dispatch is recorded as evidence on a pipeline that did
   not dispatch it, or on no pipeline at all — and either way nothing says so.
   Cross-tabulating five sessions of one consuming project by two axes, whether
   the session was bound to a host and whether it had a pipeline:

   ::

      bound   + pipeline     -> correct: each session's own agents recorded (2 sessions)
      unbound + pipeline     -> agents_run key NEVER WRITTEN, or a sink for a
                                FOREIGN session's agent
      bound   + no pipeline  -> the dispatch lands somewhere else entirely

   ``agents.design_agent_ran`` and ``agents.review_agent_ran`` are ``block`` in this
   repository's own manifest. So this misses in both directions: a session whose
   agent really ran cannot clear the gate, and a session whose agent never ran
   can have it cleared by somebody else's.

   **Expected.** A dispatch is recorded against the session that made it, or the
   attempt to record it fails loudly. Evidence that silently lands elsewhere is
   worse than evidence not collected, because the gate reading it is confident.

   **This supersedes a weaker diagnosis.** The first framing was "the hook cannot
   disambiguate between concurrently live sessions and silently drops the
   payload". That is not the mechanism, and the cross-tabulation is what refutes
   it. The two unbound cases failed **differently** — one never had the key
   written at all, the other received a foreign session's ``backlog-curator`` while
   its own two agents were absent. One deterministic wrong destination would not
   produce both.

   **The decisive cell.** A session that was correctly **bound** but had **no
   pipeline** — a backlog filing needs none — had its dispatch recorded against a
   *different* session's pipeline. So **the pipeline is the write destination and
   the binding is only the lookup.** Fixing the lookup does not help when there is
   nothing at the end of it.

   **Cause — the drop is source-confirmed; the destination is not.**
   ``_post_tool_use`` resolves an owner with ``ctx.session_for_payload(payload)`` and
   then calls ``ctx.pipelines.record_agent_run(session.id, agent_type)``. An agent
   dispatch carries no path, so it cannot use the payload-attribution route that
   BUG_010 added for reads and edits; it falls all the way through
   ``SessionDomain.resolve``. And ``record_agent_run`` opens with:

   .. code:: python

      pipeline = self.current(session_id)
      if pipeline is None:
          return

   Its docstring calls this deliberate — "unrecorded outside a pipeline, like a
   docs read: dispatching an agent before starting one is allowed, it just cannot
   be evidence for a gate that does not exist yet". That reasoning is sound for
   ``record_docs_read`` and wrong here, because an agent gate is ``block``: the
   session that dispatched the agent has no way to learn that its evidence was
   dropped, and discovers it at the gate, at the end of the phase.

   **Repro — and it needs no concurrency at all.** Record this verbatim; it is
   far cheaper than reproducing a race.

   1. Register a session. Do NOT start a pipeline.
   2. Dispatch any agent.
   3. Diff every ``pipelines/*.json`` under the state root
      (``<git common dir>/invariant/state`` by default; ``invariant config show``
      resolves ``paths.state``).

   If any ``agents_run`` grew, the write did not fail closed and the destination is
   somebody else's. If none did, the write was dropped silently — which is the
   other half of the defect and is provable in the same run.

   **A distinction that matters for the repro.** One session's ``agents_run`` key
   was **absent**, not an empty list. A test asserting ``== []`` passes against a
   dropped write and fails against a never-written key, or the reverse, depending
   on which state it happens to meet — they are different states with different
   causes. Assert on key presence and on contents separately.

   Methodology note worth keeping: an analysis script written as
   ``d.get('agents_run') or []`` erases exactly this distinction. It did, and the
   analysis had to be redone.

   **Counter-evidence, so this is not overstated.** ``backlog-curator`` appears in
   ``agents_run`` for seven other pipelines, where it is almost certainly
   legitimate — sessions where a curator really did run, listed alongside
   ``feature-architect`` and ``compliance-reviewer``. Its presence is not inherently
   anomalous. It is anomalous *for the one pipeline where the session's own two
   agents are missing*. Without that context a reviewer will find those seven and
   dismiss the finding.

   **Open question — where does a homeless dispatch go?** The foreign agent landed
   on the one session that was unbound *and* had a pipeline. If the fallback
   prefers an unowned pipeline, that explains the destination. But it is ONE
   observation and these are not excluded:

   - "most recently touched" or "first enumerated";
   - the ``origin`` step of ``SessionDomain.resolve`` — its fifth and last resort
     returns ``Resolution(candidates[0], "origin")`` when exactly one *attributable*
     session is registered at or above the working directory, and ``attributable``
     excludes sessions bound to a different host, which would leave precisely the
     unbound one standing. This is a reading of the code, offered as an
     alternative and not as the answer; note it explains the foreign-agent cell
     but not the never-written cell, where the same path returns
     ``Resolution(None, "ambiguous")`` or ``"none"``.

   Whichever it is, the fix is the same shape: a write that cannot find its
   pipeline must say so, not choose.

   .. rubric:: Three further faces, all source-confirmed

   The first shares the root cause. The second and third share the family — an
   instrument that cannot express its own failure — and may well be split into
   their own entries by whoever picks this up.

   **``invariant context`` prints nothing and exits 0 when the host owns no
   session.** ``cmd_context`` calls ``build_context(ctx)`` with the default
   ``greeting=False``; when a host identifier is present and owns nothing, that
   returns ``""``. ``emit`` prints nothing for empty text and unconditionally returns
   ``EXIT_OK``. So "context has nothing to say" and "context could not resolve you"
   are the same output and the same exit status — on the command whose entire job
   is to answer "where am I". **Independently observed by two sessions that had
   not compared notes**, which is worth recording: the same silence read as the
   same false reassurance twice.

   **Phantom ownership: the hook DISPLAYS an owner the engine never BOUND.** One
   session's hook reported its host as owning an invariant session across three
   different ids in one conversation, always naming whichever was live, while the
   persisted ``host_session_id`` read ``None`` throughout. ``SessionDomain.resolve``
   returns its last-resort guess as an ordinary ``Resolution`` carrying
   ``how="origin"``, and ``build_context`` prints
   ``invariant — session <id> (<name>) is <state>`` without ever branching on
   ``resolution.how``. So a guess renders identically to an explicit
   ``INVARIANT_SESSION_ID`` match. **Record the precise distinction: displaying a
   wrong owner is confirmed, binding a wrong owner is not** — the record said
   ``None`` the whole time.

   **Teardown cannot record its own transition.** ``session.yaml`` declares
   ``torn_down`` reachable only from ``merged`` and ``pushed``; ``working`` lists
   ``[working, reviewed, abandoned]``. ``SessionDomain.teardown`` removes the worktree
   and deletes the branch and *then* calls ``_advance(session, "torn_down")``. When
   that raises — which it must, from ``working`` — the following ``_deregister`` never
   runs, leaving a session record claiming ``working`` against a worktree that no
   longer exists and still holding its host binding. Two things follow, and only
   the second is a defect: refusing to tear down an unmerged session is correct;
   doing the irreversible half first and the recordable half second is not.
   ``session reap`` is the existing escape and works.

   **Fix.** Three separable changes, in the order they pay off:

   1. **A dispatch that cannot be recorded must fail loudly.** Either buffer it on
      the SESSION record (where it can be replayed into a pipeline started later,
      which is the legitimate case ``record_agent_run``'s docstring is protecting),
      or return a verdict the hook surfaces. Silence is the one option to remove.
   2. **Never write evidence to a pipeline the resolution did not prove.** Carry
      ``Resolution.how`` to the write site and refuse to record on a guess, the
      same way ``Verdict.na`` is refused as a pass elsewhere in this tracker.
   3. **Reorder ``teardown``** so the transition is attempted before anything
      irreversible happens, and make ``build_context`` and ``cmd_context`` distinguish
      "nothing to say" from "cannot tell who you are".

   .. rubric:: Resolution — the open question is answered, and it changed the fix

   **An agent-type identifier DOES reach a hook.** The entry's fix sketch did not
   know this and the consuming project asked for it to be investigated rather
   than assumed. It is confirmed from the host binary, not inferred: Claude Code
   builds every hook payload through one helper that emits
   ``{session_id, transcript_path, cwd, prompt_id, permission_mode, agent_id, agent_type, effort}``, where ``agent_type`` is the dispatched agent's own type
   (``feature-architect``, ``compliance-reviewer``, …) for any call made *inside* a
   subagent, and absent in an ordinary main-loop turn. The subagent's tool-use
   context is constructed with ``agentId``/``agentType`` set from the agent
   definition, and it carries the PARENT's ``session_id`` — which is also why a
   subagent's ``invariant session start`` binds the session it creates to the
   parent host. Corroborated in the host's own transcript store, where each
   subagent run is written to ``<project>/<parent-session>/subagents/agent-*.jsonl``
   with a sibling ``.meta.json`` naming its ``agentType``.

   **Fix parts (1) and (2) were necessary and NOT sufficient**, which is the
   consuming project's reading and it is correct. They make the record honest —
   (2) stops the foreign write, (1) stops the silence — but the reported wedge is
   not a misresolution at all. A ``feature-architect`` was dispatched, and *it* ran
   ``session start``, ``session isolate`` and ``pipeline start`` before authoring
   anything: at the moment the dispatch was observed there was no session to
   attribute it to and no pipeline to be evidence for. An honest recorder records
   nothing there, and the gate still refuses. The positive control confirms the
   other direction independently — create session and pipeline first, *then*
   dispatch, and the write lands first try with three other sessions live.

   **So the load-bearing change is a third one: record the ACTOR, not only the
   dispatch.** The dispatch and the run are different events at different times,
   and the second one is attributable when the first is not. Landed:

   1. ``hosts/claude_code._record_acting_agent`` — on every post-tool-use event
      carrying ``agent_type``, the acting agent is recorded against the resolved
      session's current pipeline. The architect that starts a pipeline and then
      writes into it records itself in the pipeline it made.
   2. ``Resolution.proven`` (``domains/session.PROVEN_ROUTES``) — evidence is written
      only on a route that IDENTIFIED the session. ``origin``, the last-resort
      "only session registered at or above here", is excluded. This is the cell
      the cross-tabulation above found: an UNBOUND session with a pipeline, left
      standing as the sole attributable candidate, acting as a sink. Display and
      enforcement still use ``origin``; recording does not.
   3. ``_actor_is_placed_here`` — an actor row requires the call to be PLACED in the
      session, which is a higher bar than ``proven``. See the second mechanism
      below; without this the actor route would have opened a third contamination
      channel, and a worse one, because an actor row asserts the stronger thing.
   4. ``record_agent_run`` returns ``recorded`` / ``already`` / ``no-pipeline`` instead of
      dropping silently, and the hook surfaces a dispatch it could not file — in
      the turn it happened, not at the gate at the end of the phase. The
      permission the old docstring protected is kept exactly: dispatching before
      starting a pipeline is still allowed and still raises nothing.
   5. ``agents_run_how`` records WHY each agent is in ``agents_run`` — ``dispatch``
      (somebody asked, and it resolved here) or ``actor`` (this agent demonstrably
      worked here). An actor reading is never downgraded by a later dispatch.
      ``agents_run`` keeps its shape, so no stored record and no gate changes.
   6. ``NORMALISED_PAYLOAD_KEYS`` gains ``agent_type``, which is a deliberate widening
      of the layer contract, recorded as such.

   .. rubric:: A SECOND foreign-write mechanism, found while checking the first

   The cross-tabulation above attributes the foreign write to the ``origin`` guess,
   and an early reading of the fix claimed part (2) closed it. **That was
   overstated, and the correction matters more than the claim did.** Two
   contaminated pipelines in the consuming project were re-examined against its
   state directory and its host transcript store, and both victims are **bound**,
   not unbound:

   - ``d5c8605b`` — bound; ``agents_run`` accumulated ``backlog-curator`` and
     ``general-purpose`` alongside one legitimate ``compliance-reviewer``, while
     recording none of its own design work.
   - ``c143efd8`` — bound; carries a ``backlog-curator`` belonging to a different
     task.

   The host's own record names the source. The parent session that owns
   ``d5c8605b`` dispatched, in one afternoon: two ``backlog-curator``\ s, three
   ``general-purpose`` agents, a ``feature-architect``, a ``bug-reproducer`` and a
   ``compliance-reviewer``. So the foreign rows did not arrive by a guess at all.
   They arrived by the **host route**, which identified the session correctly —
   and then filed against it every agent the host dispatched, including the ones
   working in the shared checkout on unrelated tasks.

   ``origin_candidates`` excludes sessions bound to a different host, so a stranger
   could not have reached these pipelines by ``origin`` even in principle. Two
   mechanisms, not one:

   +----------------------+------------+----------------------------------+
   | Victim               | Route      | Cause                            |
   +======================+============+==================================+
   | unbound + pipeline   | ``origin`` | sole attributable candidate left |
   |                      |            | standing                         |
   +----------------------+------------+----------------------------------+
   | **bound + pipeline** | ``host``   | the binding is a scalar; one     |
   |                      |            | host, many tracks                |
   +----------------------+------------+----------------------------------+

   Only the first is closed by refusing guesses. The second is the 1:1 binding
   meeting a parent that drives several tracks at once, and the dispatch event
   genuinely cannot tell where the agent will work — which is precisely why the
   ACTOR route is the better instrument, and precisely why it must not inherit
   the same flaw. ``_actor_is_placed_here`` is that guard: the host route is asked
   a second question, for the location it deliberately ignores.

   **Still open:** the dispatch route continues to bank a pathless dispatch on the
   host's binding. That is honest as far as it goes — at dispatch time there is no
   better evidence — but it is how these rows got written, and the actor route
   does not remove them. Recorded as residue rather than silently carried.

   **The mitigation is not what it looks like.** In the consuming project one
   session stayed unbound while a sibling was bound, and the natural reading was
   that driving a session through ``INVARIANT_SESSION_ID`` keeps it out of the host
   route's blast radius. It does not: ``session start`` runs through Bash and
   ``_bind_started_session`` fires on it regardless of any environment variable. That
   session stayed unbound because the host **already owned another one** and
   ``adopt_host_session`` refuses a second — the 1:1 rule working, its ``UsageError``
   swallowed by the caller as designed. Being unbound is a side effect of the rule,
   not a technique, and it cuts both ways: an unbound session cannot receive its
   own pathless dispatches either. What actually works is naming the target
   worktree in the brief (the ``prompt`` route), and — once the actor route is live —
   simply having the agent work in the right worktree.

   **Buffer-and-replay was considered and not taken.** It is the shape fix part
   (1) suggests and the consuming project's second message favours it, but for
   the reported case it has nowhere honest to buffer: at dispatch time no session
   existed, so the buffer would have to hang off the HOST record and replay into
   "the first pipeline this host creates" — a guess by construction, and the same
   guess-shaped write that part (2) exists to forbid. The actor route records a
   proven fact instead of deferring an unproven one. It is not ruled out for some
   future case; it is unnecessary for this one.

   **Reproduced in this repository's suite**, which the provenance note below
   said was not yet done. ``TestBUG028TheAgentThatCreatedThePipelineIsInIt`` walks
   the reported order — dispatch with nothing live, subagent registers, isolates,
   starts a pipeline, then works — and asserts ``agents.design_agent_ran`` goes
   from failing to clean. Six of its eight cases are red without the fix; the two
   that are green either way are deliberate controls (an ordinary turn carries no
   actor; the positive control still lands). Key PRESENCE and contents are
   asserted separately throughout, per the methodology note above.

   **Still open from this entry, untouched here:** the third fix part — reorder
   ``SessionDomain.teardown`` so the transition is attempted before anything
   irreversible happens, and make ``build_context`` / ``cmd_context`` distinguish
   "nothing to say" from "cannot tell who you are". The phantom-ownership face is
   also unfixed, and visible in this repository's own state right now: a live
   session displays as owned while its ``host_session_id`` reads ``None``. These are
   the "same family, different mechanism" faces and are better split out than
   carried by this entry.

   **A record already written cannot be repaired by this.** The fix records
   actors from the moment it lands; it does not reconstruct the past. A pipeline
   already wedged by this defect needs a separate, justified data repair, not a
   gate retune — and the host's ``subagents/agent-*.meta.json`` is primary evidence
   for what actually ran, so such a repair can be grounded rather than asserted.

   **Related:** :need:`BUG_010 <BUG_010>` added payload attribution for reads and
   edits and explicitly noted that "agent dispatches carry no path, so they
   resolve exactly as before" — this is what "as before" costs.
   :need:`BUG_017 <BUG_017>` and :need:`BUG_023 <BUG_023>` are the neighbouring
   attribution defects in the ``adr-002-followups`` batch, and BUG_023's "two routes
   to the host session id, one silent" is the lookup half of this.
   :need:`BUG_009 <BUG_009>` is teardown's other ordering hazard.
   :need:`BUG_021 <BUG_021>` is the same gate seen from the other side: an agent
   that cannot clear the gate it exists to clear.

   **Provenance.** Found by a consuming project during one long session, by
   cross-tabulating five of its own sessions against the state directory — not by
   this repository's suite. Reported 2026-08-17. **Nothing here is reproduced in
   this repository's tests yet**; the no-concurrency repro above is the cheapest
   first step and it tests the drop, not the destination.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'Reproduced in this repository''s suite: TestBUG028TheAgentThatCreatedThePipelineIsInIt walks the reported order (dispatch with nothing live, subagent registers/isolates/starts a pipeline, then works) and agents.design_agent_ran refuses the pipeline the design agent created. Six of eight cases red without the fix.'
        forced: false
      - from: reproduced
        to: fixed
        reason: Actor recording (agent_type from the hook payload) plus a proven-resolution rule for evidence writes, plus a recorder that reports instead of dropping. The headline symptom and the foreign-write cell are closed; the teardown-ordering and cmd_context faces are recorded as residue.
        forced: false

.. bug:: test.new_test_fails accepts a run whose failure count could not be parsed
   :id: BUG_029
   :status: fixed
   :severity: high
   :priority: high
   :area: core/rules, domains/pipeline
   :batch: inert-instruments
   :slug: proof-of-red-accepts-an-unparseable-failure-count
   :affects: COMP_CORE_RULES, COMP_DOMAINS_PIPELINE

   **Symptom.** ``test.new_test_fails`` is the TDD pipeline's sole proof-of-red. It
   accepted a run recorded as:

   ::

      executed: 1, exit_code: 65, failed: null
      note: "exit 65 says the run failed, but no failure count could be parsed from
             its output — the count is unknown, not zero."

   and reported ``[ok] a red run is on record``. The adapter said in as many words
   that it could not tell what failed; the gate keyed off the non-zero exit and
   called it proof.

   **Expected.** The gate's name is a claim about a TEST. "A test failed on the
   symptom under investigation" and "the command exited non-zero" are different
   facts, and only the first licenses a fix session.

   **Cause — confirmed by source reading.** ``_test_new_test_fails`` filters the
   recorded runs to ``phase == "red"`` and then asks one question:

   .. code:: python

      if any(not r.get("passed", True) for r in red):
          return Verdict.clean("a red run is on record")

   ``passed`` is derived from the exit status. ``failed``, ``executed`` and ``hazards``
   are all in the same record and none is consulted. So the rule's real predicate
   is **"something failed"** — satisfiable by a compile error, a signing failure,
   a provisioning failure, a missing simulator, or a host crash. In the observed
   case the likely truth was a **compile error**, which would have licensed a fix
   session against a red that proved nothing about the defect.

   **The distinction already exists in this file, one rule below.**
   ``test.all_pass`` carries three guards that ``test.new_test_fails`` has none of:

   - ``failed is None`` is rendered as "an unknown number of", with the comment
     *"Rendering that as '0 failing' would read as green prose attached to a red
     verdict."*
   - ``executed == 0`` fails outright: *"A filter that matches nothing exits zero.
     'The suite is green' and 'the suite did not run' are the same exit status,
     and only the executed count separates them."*
   - a hazard on a passing run fails it, because a pass with a live hazard is not
     a trustworthy pass.

   Read those next to each other and the asymmetry is stark: **the same file
   refuses a green that executed nothing, and accepts a red that executed
   nothing.** A compile error executes zero tests.

   **The fact exists one layer up and never reaches the rule.**
   ``TestResult.trustworthy`` is deliberately narrower than ``passed`` for exactly
   this reason, and ``_record_test_run`` does not persist it — the record keeps
   ``passed``, ``exit_code``, ``executed``, ``failed``, ``failed_tests``, ``source``,
   ``device``, ``skipped``, ``hazards``, ``note``. The adapter computes the judgement the
   rule needs and the recording boundary drops it. Whatever else is done here,
   that gap is worth closing: a rule cannot consult a fact that was never written
   down.

   **The consuming project's own docs already carry the rule this gate was meant
   to mechanise:** *"count by the ``Executed N tests`` trailer, never the exit
   code."* The engine is doing the thing that project wrote down not to do.

   **Fix.** Require a parsed, non-zero failure count attributable to the selector
   under test. Concretely:

   - ``failed is None`` is **inconclusive**, never red — the same asymmetry the
     adapter layer already states, where "cannot tell" relaxes nothing.
   - ``executed == 0`` is inconclusive too, and for the identical reason
     ``test.all_pass`` already gives.
   - Say which run was rejected and why. An inconclusive verdict that does not
     name the run sends the session looking for a second red rather than at the
     compile error it actually has.

   **Rejected — accept a non-zero exit when the note is empty.** Tempting because
   it costs nothing and the observed run had a note. It makes the gate's
   correctness depend on how chatty an adapter is, which is the wrong axis: a
   silent adapter is not a more trustworthy one, and a future adapter that parses
   nothing and says nothing would sail through.

   **Rejected — hard-fail an unparseable count.** It would convert a bad red into
   a wedge, and this tracker already carries three entries for gates no correct
   work can satisfy. Inconclusive is the honest verdict: the session can produce a
   real red, and until it does the gate has not passed.

   **Fixed: the gate's predicate is now a parsed, non-zero failure count, and a
   run that cannot supply one is INCONCLUSIVE rather than proof.** ``executed == 0``,
   ``failed is None`` and ``failed == 0`` each reject the run, and the verdict names
   the rejected run's resolved command, its reason and its exit code — so a
   session meets "go read the build output" instead of "go find a second red".
   ``executed is None`` still passes, because that means the adapter does not count
   tests rather than that none ran, which is the distinction
   ``TestResult.trustworthy`` already documents.

   Two decisions worth recording against the entry's own text.

   **Hazards are deliberately not a rejection, and the asymmetry with
   ``test.all_pass`` is the point.** A hazard casts doubt on a GREEN because the
   doubt is "tests may not have run"; against a counted, non-zero failure there is
   positive evidence that a test ran and failed. Rejecting on one would have made
   the gate unsatisfiable under ``xcodebuild``'s exit-65 hazard — in the ecosystem
   it was written for — which is the failure mode this tracker files most often.

   **``TestResult.trustworthy`` was NOT persisted, contrary to the entry's
   suggestion.** The reasoning does not survive contact with the definition:
   ``trustworthy`` is ``passed and not hazards and total != 0``, so it is ``False`` for
   every failing run and tells the red rule nothing. What the rule needs —
   ``executed``, ``failed``, ``hazards`` — was already in the record all along. The
   recording boundary was not the gap; the predicate was. Adding a field no rule
   can read would have been a change that looks like a fix.

   **Related:** :need:`BUG_030 <BUG_030>` is the same rule seen from the other side —
   this one accepts evidence it should not, that one refuses evidence it should
   accept, and a single session should fix both while it has the rule open.
   :need:`BUG_031 <BUG_031>` is the same epistemic family in the CLI: a run reporting
   success for something other than what was asked. :need:`BUG_018 <BUG_018>` and
   :need:`BUG_011 <BUG_011>` are the batch's parsing cousins — a reader that cannot
   read is invisible until someone checks whether the instrument can fail at all.

   **Provenance.** Found by a consuming project during one long session, from the
   gate's own verdict sitting beside the adapter note that contradicted it — not
   by this repository's suite. Reported 2026-08-17. **Not reproduced here yet:**
   the repro is a fabricated ``test_runs`` record with ``passed=False, failed=None, executed=0`` and an assertion that the verdict is NOT clean.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: tests/test_rules.py::TestProofOfRedIsAProofAboutATest — a red run recorded as exit 65 / executed 0 / failed None is accepted by _test_new_test_fails and reported as 'a red run is on record'; four assertions red before the fix
        forced: false
      - from: reproduced
        to: fixed
        reason: 'core/rules._test_new_test_fails now requires a parsed non-zero failure count; executed==0, failed is None and failed==0 each reject the run as inconclusive and the verdict names the rejected run''s command, reason and exit code. executed is None still passes (adapter cannot count != nothing ran). Hazards deliberately not a rejection: it would make the gate unsatisfiable under xcodebuild''s exit-65 hazard. TestResult.trustworthy NOT persisted — it is False for every failing run, so it cannot inform this rule; the predicate was the gap, not the recording boundary.'
        forced: false

.. bug:: test.new_test_fails is unsatisfiable from the bugfix methodology
   :id: BUG_030
   :status: fixed
   :severity: medium
   :priority: high
   :area: core/rules, data/machines
   :batch: unsatisfiable-gates
   :slug: proof-of-red-is-unreachable-from-the-bugfix-machine
   :affects: COMP_CORE_RULES

   **Symptom.** A session running the **bugfix** methodology holds genuinely
   failing pre-fix runs and is still refused by its own exit gate:

   ::

      [block] test.new_test_fails: no failing run was recorded before the
              implementation

   The runs are there, they failed, and they preceded every production edit. The
   rule cannot see them because of the name of the state they were recorded in.

   **Expected.** A gate a machine declares as its exit condition is satisfiable
   from that machine. If a methodology names its red state something else, the
   rule reads the machine rather than a string constant.

   **Cause — confirmed by source reading.** ``_test_new_test_fails`` selects
   evidence with ``r.get("phase") == "red"``. The ``bugfix`` machine has no such
   state: it runs ``triaging -> reproducing -> fixing -> verifying -> committed``,
   and ``reproducing`` — the state that declares ``test.new_test_fails`` as its
   ``exit_gates`` — is the red state under another name. ``advance red`` is not a
   legal transition from anywhere on it. ``tdd`` does have a ``red`` state, which is
   why the constant went unnoticed: the rule was written against one machine and
   is declared by two.

   **The gate's advice was corrected while this was being filed, and the defect
   survived the correction.** As of ``ab8f7f8`` the ``fixes`` tuple reads
   ``invariant pipeline test --phase red  (records the failing run)``. It previously
   named ``invariant pipeline advance red``, which on this machine is a transition
   that does not exist — and the correction was made for an unrelated reason
   (``advance`` evaluates gates, it does not run anything). So the misdirection is
   gone and the gate now points at the one command that satisfies it.

   What remains is worse-shaped than it first looks. That advice tells an operator
   standing in ``reproducing`` to record their run under the phase name ``red`` — a
   label their machine cannot produce on its own — purely to satisfy a string
   comparison inside the rule. The evidence then says the run happened in a state
   the pipeline was never in. **The workaround is now documented; the disagreement
   between the rule and the machine is not, and the documentation of the
   workaround makes it less likely anyone notices.**

   **The workaround is legitimate, and that is exactly why it is dangerous.**
   ``invariant pipeline test --phase red`` satisfies the gate honestly in every way
   that matters: the run really executes, really fails, and really precedes any
   production edit. Nothing is being forced and no gate is being lowered. It is
   only the phase LABEL that is fiction — which is the kind of small, harmless-
   looking untruth that a later reader of the evidence has no way to detect, and
   which quietly teaches every bugfix session to write down a state it was not in.

   **Fix.** Let the machine name its own red-equivalent rather than making every
   machine reach a state name it cannot have. In rough order of preference:

   - **The machine declares it** — a ``red_phase: reproducing`` key in
     ``bugfix.yaml``, defaulting to ``red``. The rule stops carrying a state name, and
     a new methodology answers the question by existing.
   - **The rule infers it** — accept evidence recorded in whichever state declares
     ``test.new_test_fails`` as an exit gate. No new key, but it couples the rule to
     the gate wiring and gives a confusing answer if two states ever declare it.
   - **Accept any phase preceding the first production edit.** The most faithful
     to what the gate actually means, and the most work: nothing currently records
     when production code was first touched.

   Whichever is chosen, revisit the ``fixes`` tuple in the same commit. Once the
   machine's own state name is accepted, the advice should name it — telling a
   bugfix session to label its run ``red`` stops being a workaround and becomes a
   leftover.

   **Rejected — rename ``reproducing`` to ``red`` in ``bugfix.yaml``.** It makes the
   constant true and costs the machine its vocabulary: ``reproducing`` is named for
   what it proves, and the machine's own description is explicit that the state's
   success condition is a trustworthy verdict rather than a landed fix. Renaming a
   state to satisfy a string comparison is the shape this tracker files against,
   not the shape it ships.

   **Fixed: a machine declares its own red state.** ``red_phase`` is an optional
   top-level key on any state machine, validated against the machine's states, and
   ``bugfix.yaml`` sets ``red_phase: reproducing``. ``test.new_test_fails`` resolves the
   phase from the machine recorded on the pipeline — the same
   ``load_pipeline_machine`` route ``pipeline.complete`` already uses — instead of
   carrying the state name as a constant, and falls back to ``red`` for a machine
   that says nothing. A machine that cannot be loaded FAILS the gate rather than
   defaulting quietly: an unanswerable question is not a pass (AUD-08).

   The ``fixes`` tuple now names the machine's own phase, so the advice a bugfix
   session reads is ``invariant pipeline test --phase reproducing`` — the label the
   pipeline was actually standing in. The workaround the entry describes, and the
   untruth it wrote into durable evidence, are both gone rather than documented.

   The first of the entry's three options was taken. The second (infer the phase
   from which state declares the gate) was left: it gives a confusing answer if two
   states ever declare it. The third (accept any phase preceding the first
   production edit) remains the most faithful reading of what the gate means and
   still needs a fact nobody records — when production code was first touched.

   ``tests/test_machines.py`` now pins the general property rather than this one
   machine: any shipped machine declaring ``test.new_test_fails`` must have the
   state it would record a failing run in.

   **Related:** the ``unsatisfiable-gates`` trio it joins —
   :need:`BUG_019 <BUG_019>`, :need:`BUG_025 <BUG_025>`, :need:`BUG_026 <BUG_026>` — is the
   same failure mode reached by three different routes through the coverage
   floor; this is the fourth route and the first outside coverage.
   :need:`BUG_021 <BUG_021>` is the closest sibling in shape: a gate the actor it
   applies to cannot clear. :need:`BUG_029 <BUG_029>` is the same rule's opposite
   polarity — it accepts evidence it should refuse — and a single session should
   fix both while it has the rule open.

   **Provenance.** Found by a consuming project during one long session, running a
   bugfix pipeline against this engine — not by this repository's suite. Reported
   2026-08-17, and re-checked against ``ab8f7f8`` on the same day, which changed the
   gate's advice and not its predicate. **Not reproduced here yet:** the repro is a
   ``bugfix`` pipeline in ``reproducing`` with a failing run recorded under that phase,
   and an assertion that the exit gate passes.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'tests/test_rules.py::TestProofOfRedReadsTheMachinesOwnRedState plus a live repro in this repository''s own session: a bugfix pipeline standing in ''reproducing'' with a genuine 25-failure run recorded under that phase was refused with ''no failing run was recorded before the implementation'''
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fsm.Machine gains an optional, validated red_phase; bugfix.yaml declares red_phase: reproducing; the rule resolves the phase from the pipeline''s recorded machine via load_pipeline_machine and fails (never defaults) when the machine cannot be loaded. The fixes tuple now names the machine''s own phase. Option 1 of the entry''s three taken. Demonstrated live: this session''s own bugfix pipeline cleared the gate from ''reproducing''.'
        forced: false

.. bug:: pipeline test --selector silently keeps only the last of repeated flags
   :id: BUG_031
   :status: fixed
   :severity: medium
   :priority: high
   :area: cli/main
   :batch: inert-instruments
   :slug: repeated-selector-flags-keep-only-the-last
   :affects: COMP_CLI_MAIN

   **Symptom.** ``invariant pipeline test --selector A --selector B`` does not run
   both. ``B`` wins, ``A`` never executes, and the run reports a healthy green **for a
   scope nobody asked for**. Nothing is printed about the discarded flag. Found
   when a reviewer, trying to run two test classes in one pass, had to fall back
   to one class per invocation.

   **Expected.** Either both selectors run, or the command refuses. A flag that is
   accepted and then discarded is the worst of the three, because the caller
   believes a scope was covered that was not.

   **Cause — confirmed by source reading.** The parser declares

   .. code:: python

      q.add_argument("--selector")

   with no ``action="append"`` and no ``nargs``. argparse's documented behaviour for a
   repeated single-value option is that the last occurrence overwrites the
   earlier ones, silently. Downstream, ``run_tests`` selects
   ``command_key = "test_one" if selector else "test"`` and substitutes the single
   surviving value into ``commands.test_one`` — so the command that runs is
   well-formed and honest about itself. Only the request was lost.

   **The idiom is already in the same parser, a few lines below.**
   ``item new --field`` is declared ``action="append"`` for exactly this reason. This
   is not a missing capability in the toolkit; it is one option declared without
   it.

   **What kept this from being worse.** ``_record_test_run`` stores the RESOLVED
   command — "placeholders substituted", by deliberate design, so a discovered
   value that picked the wrong thing is visible in the evidence. That is the only
   reason the loss is recoverable at all: a reader of ``test_runs`` can see which
   selector actually ran. It is also how this was caught. The evidence layer did
   its job; the CLI did not.

   **Why this matters beyond one confused invocation.** ``test.all_pass`` reads the
   LAST run. A gate cleared by a run covering half the requested scope is a green
   for work nobody measured — the same defect as a filter that matches nothing
   exiting zero, which this codebase already guards against by name in
   ``test.all_pass`` and in ``TestResult.trustworthy``. The guard exists for the
   selector that matches nothing; there is none for the selector that was never
   passed on.

   **Fix.** Take the selectors as a list and make the adapter say what it can do
   with them.

   - ``action="append"`` on the flag, so ``--selector`` accumulates.
   - A ``{selector}`` placeholder that renders a list needs an adapter-side
     contract: pytest joins with spaces, ``xcodebuild`` needs a repeated
     ``-only-testing:`` per selector. That is an ``adapters/`` question — the rule
     keeps the policy, the adapter supplies the fact — so the honest interim is
     for a multi-selector request to run once per selector and record each run.
   - Until that lands, **refuse a repeated flag** rather than discarding one. A
     refusal costs the caller one retry; a discard costs them the belief that the
     scope was covered.

   **Rejected — document "one selector per invocation".** A flag that silently
   discards input is not fixed by a note, and the note would live in the one place
   the caller is not looking when they type the second flag.

   **Fixed: ``--selector`` is ``action="append"``, and several selectors either run
   together or are refused.** How several are spelled in one command is ecosystem
   knowledge, so it is an adapter question — ``Adapter.join_selectors``, answering
   ``None`` for "cannot tell" in the same shape as ``carries_executable_code`` and
   ``measured_scope``. ``python`` joins with spaces, because pytest takes node ids
   positionally. ``swift_xcode`` deliberately does NOT override it: xcodebuild needs
   a repeated ``-only-testing:`` FLAG that lives in ``commands.test_one``, a template
   a project may have rewritten, and producing ``A -only-testing:B`` would be right
   against the shipped default and silently wrong against any other — the same
   defect one layer along.

   Where the adapter cannot join them the command refuses, names both selectors,
   and records nothing. The entry's interim — one run per selector — was rejected
   on a second look: ``test.all_pass`` reads the LAST run, so two legs of which the
   first fails and the second passes would have been read as a green suite. That
   is this bug's own family, reintroduced by its own fix.

   **Related:** :need:`BUG_029 <BUG_029>` is the same epistemic family one layer in —
   an instrument reporting success for something other than what was requested.
   :need:`BUG_020 <BUG_020>` is the same again in the reporting layer. The batch's
   common test is the one to apply here: ask not whether the instrument passes,
   but whether it can express the failure of the thing it was asked to do.

   **Provenance.** Found by a consuming project during one long session, when two
   ``-only-testing``-style selectors produced one class's results. Reported
   2026-08-17. **Not reproduced here yet:** the repro is a parser-level assertion
   that two ``--selector`` flags survive to ``run_tests``, which will fail on the
   current declaration.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: tests/test_cli.py::TestRepeatedSelectorsSurviveTheParser — build_parser().parse_args(['pipeline','test','--selector','A','--selector','B']).selector was 'B', the first flag gone with no diagnostic
        forced: false
      - from: reproduced
        to: fixed
        reason: '--selector is action=append; run_tests takes selectors and asks the new Adapter.join_selectors; python joins with spaces, swift_xcode deliberately declines (the repeated -only-testing flag lives in a template a project may have rewritten), and where the adapter cannot join the command refuses naming both selectors and records nothing. The entry''s one-run-per-selector interim was rejected: test.all_pass reads the LAST run, so a failing leg followed by a passing one would read as green.'
        forced: false

.. bug:: items.exempt does not survive session isolation
   :id: BUG_032
   :status: filed
   :severity: medium
   :priority: high
   :area: core/rules, domains/session
   :batch: unsatisfiable-gates
   :slug: work-item-exemption-lost-inside-an-isolated-session
   :residue: Live leg: isolate a session in a consuming project, dispatch backlog-curator from it to file one trivial bug, and capture the first refusal verbatim (redacted) with its tool name\; '[BLOCK] <rule>' text means this engine, anything else means the host's own permission layer.; DECISION 2026-10-21 (user-approved): The user will run the live leg themselves from a host session rooted in the consuming project (Heap) and report the verbatim refusal back. The entry stays filed until that capture arrives.
   :affects: COMP_CORE_RULES, COMP_DOMAINS_SESSION

   **Symptom.** The exemption that lets a work-item file be written outside the
   worktree flow is reported lost inside an isolated session: a ``backlog-curator``
   dispatched from one could write nothing. **And it costs the whole run**, because
   the refusal arrives only after the agent has read the backlog, interviewed,
   scored and drafted — every phase of its work is spent before the first
   keystroke that could fail.

   Two further refusals were reported from the same guard in the same session:
   pure git **reads** were refused, and so was any command it could not statically
   parse.

   **Expected.** ``items.exempt`` is on by default and :need:`BUG_008 <BUG_008>` decided
   it deliberately: the work-item store is data every session touches, like
   configuration and memory, and routing it through a branch produces conflicts
   with no review value. An exemption that stops applying inside an isolated
   session is not in force in the one place it was written for — a curator is
   almost always dispatched from a session that is already isolated for other
   work.

   **Cause — NOT confirmed, and the entry should not pretend otherwise.** A
   reading of the obvious suspects did not find it, which is worth recording so
   the next session does not repeat the reading:

   - ``_is_exempt`` does add the work-item path when ``items.exempt`` is true, as two
     glob patterns (``<items.path>`` and ``<items.path>/**``), and BUG_008's decision
     is visibly implemented there.
   - All three isolation rules — ``session.isolated_before_edit``,
     ``session.no_edit_in_main_checkout``, ``pipeline.started_before_edit`` — consult
     ``_is_exempt`` FIRST and return ``Verdict.na`` on a match.
   - ``_project_relative_forms`` is explicitly designed for the multi-worktree case:
     a file inside a linked worktree is spelled against that worktree, everything
     else is tried against the manifest root, the git toplevel and the main
     checkout in turn. Its docstring names the exact failure this bug describes —
     "the exemption silently stops applying exactly when several worktrees
     exist" — as something it exists to prevent.

   So the mechanism is somewhere else. Three candidates, in the order they should
   be tested:

   1. **The write may not have been an edit at all.** If the curator wrote through
      a shell — a heredoc, ``sed -i``, a redirect — then ``RuleContext.target_path``
      is ``None``, ``_is_exempt`` is never consulted, and the refusal came from
      somewhere else entirely. :need:`BUG_027 <BUG_027>` is why that trail is cold:
      the same predicate makes a shell-mediated write invisible to these rules.
      Establish first WHICH tool the refused write used.
   2. **The refusal may not be this engine's.** No rule in this repository parses
      a Bash command except ``_extract_commit_message`` (git commit only) and the
      push-barrier's ``git push`` regex, and neither refuses a git read or an
      unparseable command. Either the reported refusals came from the agent host's
      own directory protections, or from a path this reading missed. The exact
      refusal text, captured verbatim, settles it in one line and nothing else
      will.
   3. **Only then**, the exemption itself: whether ``items.path`` resolves as
      expected when the manifest is loaded from inside a worktree, and whether a
      backlog file in the MAIN checkout edited while worktrees exist takes the
      branch its docstring intends.

   **Fix.** Whatever the mechanism, one property is owed independently of it:
   **the refusal must arrive before the agent does its work, not after.** A
   dispatched agent that cannot write is a whole run spent, and the cost is set
   entirely by when it is discovered — which makes a cheap pre-flight ("can this
   session write the work-item store?") worth more than the eventual root-cause
   fix. ``invariant doctor`` or ``invariant context`` is the natural place: an agent
   that must write the backlog can ask before it starts.

   The consequence worth recording either way: consuming projects have learned to
   dispatch filing agents from a NON-isolated session. That is exactly the kind of
   unwritten rule an enforcement engine exists to remove, and it is currently
   being passed between sessions as folklore.

   **Related:** :need:`BUG_008 <BUG_008>` is the decision this defect nullifies —
   read it first, because it also records the argument AGAINST the exemption, and
   a fix that reverses the decision is a legitimate outcome as long as it is
   chosen rather than defaulted into. :need:`BUG_021 <BUG_021>` is the same shape from
   the other side: this library's own agent blocked by this library's own gate.
   :need:`BUG_027 <BUG_027>` is candidate 1 above.

   **Provenance.** Reported by a consuming project after a curator run was lost to
   it. Reported 2026-08-17. **Not reproduced here, and not source-confirmed
   either** — unlike the other six filed alongside it, this entry names a
   symptom and rules out three mechanisms rather than naming one. Treat the first
   diagnostic step as "capture the verbatim refusal", not "read ``_is_exempt``
   again".

   **Repro debt (since 2026-10-21):** not expressible in-target. The engine's
   own ``pre-tool-use`` hook was driven through the real CLI (``invariant hook pre-tool-use``, a subprocess per call) over a 2600-call matrix: worktrees inside
   and beside the repository; markdown and sphinx-needs backends; ``items.path``
   spelled ``docs/backlog``, ``docs/backlog/``, ``./docs/backlog``, and two ``.rst``
   files; sessions isolated, registered, and absent; cwd and ``CLAUDE_PROJECT_DIR``
   each at the main checkout, the worktree, or unset; acting agent the main
   thread or ``backlog-curator``. Calls: Write/Edit/MultiEdit of the store in the
   main checkout and in the worktree, shell heredoc and ``sed -i`` into the store,
   ``invariant item new``, git reads (``status``, ``log``, ``-C <main> log``, ``diff``,
   ``show``), an unbalanced quote, a for-loop, command substitution, and ``git commit`` with an inline and a heredoc message. Findings:

   - With every canonical ``items.path`` spelling, an isolated session's write to the
     work-item store in the main checkout is ALLOWED, from every cwd /
     project-dir combination, for both acting agents. The harness can see the
     surface: the same write with ``items.exempt: false`` is DENIED by
     ``session.no_edit_in_main_checkout``.
   - No Bash call in the matrix — git reads, unparseable commands, shell writes —
     was refused by this engine, matching candidate 2 above: the reported Bash
     refusals did not come from these rules.
   - One in-target refusal exists and is NOT this symptom: ``items.path: ./docs/backlog`` (leading ``./``) makes the exemption match nothing, so store
     writes in the main checkout are denied — but from non-isolated sessions too,
     which contradicts the report that a non-isolated dispatch works. Worth its
     own entry (the exempt patterns are not normalised before ``fnmatch``). Filed
     2026-10-21 as :need:`BUG_071 <BUG_071>`.

   Live-environment leg: in a consuming project with a canonical ``items.path``,
   start a session and isolate it, then from that session dispatch
   ``backlog-curator`` with a brief to file one trivial bug. When the first
   refusal arrives, copy the refusal text verbatim (redacted) together with the
   tool name and the absolute-path-free target. Then decide: text beginning
   ``[BLOCK] <rule id>`` is this engine (re-file with that rule); anything else is
   the agent host's own permission layer (working-directory scope of a subagent
   rooted in a worktree, or its static-parse check on Bash), and the fix is the
   pre-flight named under **Fix**, not ``_is_exempt``.

.. bug:: The shipped agents' gate receipt hashes an empty diff under zsh
   :id: BUG_033
   :status: fixed
   :severity: high
   :priority: high
   :area: data/agents
   :batch: inert-instruments
   :slug: gate-receipt-hashes-an-empty-diff-under-zsh
   :affects: COMP_DATA_AGENTS

   **Symptom.** The gate-receipt step shipped in this library's agent data runs

   .. code:: bash

      SESSION_FILES="<space-separated touched source/test/doc files>"
      git diff --no-color HEAD -- $SESSION_FILES | shasum -a 256 | cut -d' ' -f1

   **zsh does not word-split an unquoted parameter.** A multi-file set arrives at
   git as ONE bogus pathspec, the diff comes back empty, and ``diff_sha`` becomes
   ``e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`` — the
   SHA-256 of the empty string — regardless of what changed. **Four separate
   agents hit this in one session**, each catching it only by recognising that
   hash; one of them hit it inside a verification script it had written
   specifically to catch its own mistakes.

   **Reproduced, in a throwaway repository, two modified tracked files, zsh:**

   ::

      unquoted   -> 0 diff lines, sha e3b0c442…b855, exit 0
      ${=…}      -> 14 diff lines, exit 0

   ``git diff`` exits **0** on a pathspec that matches nothing and prints no
   diagnostic — unlike ``git add``, which errors. So the failure emits no signal at
   any layer: no output, no stderr, no non-zero status. Only the constant gives
   it away, and only to a reader who recognises the constant.

   **Expected.** A receipt over a non-empty file set reflects the diff of that
   file set. A receipt that cannot be computed is not written.

   **Why this is the dangerous one.** ``compliance-reviewer``'s Phase 5a fast path
   compares its own ``WANT_DIFF`` against the receipt's ``GOT_DIFF`` and, on a match
   with ``head``, runs an **incremental smoke build** and **reuses the architect's
   coverage numbers** instead of re-running its gates. Both sides compute the hash
   with the same broken expression, so both get the empty-string constant and
   ``WANT_DIFF == GOT_DIFF`` **always**. ``head`` matches too in the intended workflow,
   because the architect leaves the tree uncommitted for the reviewer to commit.

   The receipt's entire purpose is stated in the spec — detect "whether anything
   changed after you (e.g. the main agent edited a file post-handoff)" — and that
   is precisely the case it cannot see. The short-circuit takes the fast path on a
   tree that has moved, deterministically, every time.

   **Precise scope, because the two files differ.** The literal unquoted expansion
   ships in ``src/invariant/data/agents/feature-architect.md`` only.
   ``src/invariant/data/agents/compliance-reviewer.md`` writes the same command with
   a ``<SESSION FILE SET>`` placeholder, so its text is not itself broken — it
   invites the operator to substitute a variable, which is how the reviewer side
   reached the same empty hash. Both need editing; only one is a defect in the
   shipped text, and a fix that changes only the architect leaves the reviewer
   free to reintroduce it.

   **This repository runs the defect against itself.** The installed copies under
   ``.claude/agents/`` are regenerated from the shipped sources with a content hash
   that ``doctor`` checks, and ``.claude/agents/feature-architect.md`` carries the
   same unquoted expansion — regenerated most recently by ``ab8f7f8``. So the fix
   lands in ``src/invariant/data/agents/`` and is regenerated; hand-patching the
   installed copy makes ``doctor`` report drift on the repair.

   **Fix.**

   - ``${=SESSION_FILES}`` under zsh, or portably a shell **array** built in the
     spec and expanded ``"${SESSION_FILES[@]}"``. Prefer the array: it is correct in
     both shells and it does not depend on knowing which one the host runs.
   - Give ``compliance-reviewer`` the same concrete construction instead of a
     placeholder, so the two sides cannot drift.
   - **A sanity check belongs in the spec, not only in the syntax.** A receipt
     over a non-empty file set with a zero-line diff is suspicious on its face.
     The architect should refuse to write a receipt whose diff is empty when it
     knows it changed files; the reviewer should ignore a receipt whose
     ``diff_sha`` is the empty-string hash. Naming that constant in the spec costs
     one line and converts a silent match into a caught one — which matters
     because the next way to produce an empty diff will not be a quoting bug.

   **Why high and not critical, having been proposed as critical.** The argument
   for critical is strong: it ships in the library, it is deterministic, it fires
   on every run, and it turns the review agent's own change-detection into a
   rubber stamp. Two things hold it at high. First, the consequence is a *missed*
   gate, and a missed gate is recoverable by running it — nothing is destroyed and
   nothing escapes, whereas :need:`BUG_027 <BUG_027>`, the other critical proposed in
   this batch, writes something that cannot be unwritten. Second, the fast path
   still runs an incremental smoke build and re-runs the touched-file test
   classes; what is skipped is the full build and the full coverage pass, not
   every check. That is a serious hole in the receipt's stated purpose and not the
   disappearance of review. Priority is high regardless: the fix is small, the
   failure is silent, and it affects every consuming project rather than this one.

   **Rejected — pin the receipt block to bash with a shebang.** The agent spec
   does not choose the operator's shell; the host runs these commands in whatever
   shell it uses, and a snippet correct only under one shell has the same defect
   one layer up. Write it shell-agnostically instead.

   **Rejected — drop the fast path.** It would remove the defect and the feature
   together. The receipt saves a cold rebuild between two agents sharing a gate
   directory, which is real; the flaw is in how it computes its signature, not in
   having one.

   **Fixed: both agent specs build the pathspec as a shell ARRAY and expand it
   quoted, and both name the empty-string hash as a refusal condition.**
   ``feature-architect`` computes ``DIFF_SHA`` once, refuses to write a receipt whose
   diff is empty over a non-empty file set, and says so; ``compliance-reviewer``
   carries the same concrete construction instead of the ``<SESSION FILE SET>``
   placeholder that let it reach the same empty hash, and discards a receipt where
   either side's signature is the empty-string constant. An array is correct in
   bash and zsh alike, so the fix does not depend on knowing the host's shell.

   Re-verified from scratch in a throwaway repository, two modified tracked files,
   zsh: unquoted → 0 diff lines and ``e3b0c442…b855``; array → 14 diff lines and a
   real digest; the same array under bash → 14 lines.

   ``tests/test_assets.py`` now reads the shipped markdown, extracts only its fenced
   shell blocks — the prose names the broken form deliberately, and a check that
   could not tell an example from an instruction would forbid documenting the
   defect — and asserts the construction and the named constant on BOTH agents.
   That is the answer to the entry's closing thought: the shipped instruction now
   has a test that runs against it.

   The installed copies under ``.claude/agents/`` were regenerated from the shipped
   sources rather than hand-patched, so ``doctor`` reports them current.

   **Related:** :need:`BUG_029 <BUG_029>` and :need:`BUG_031 <BUG_031>` are the same
   family in the engine — an instrument reporting a result for something other
   than what it was asked about. :need:`BUG_020 <BUG_020>` is the reading half of it.
   The batch's standing test applies exactly: an instrument that cannot express
   its own failure has not been shown to work, however green it reads.

   **Provenance.** Found by a consuming project during one long session, by four
   agents independently recognising the empty-string hash — not by this
   repository's suite. The zsh behaviour above was re-verified from scratch while
   filing, in a throwaway repository, on 2026-08-17. **Not reproduced in this
   repository's tests:** the agent data is markdown and nothing here executes it,
   which is itself worth a thought — a shipped instruction that no test runs is an
   instrument nobody self-tested.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'tests/test_assets.py::TestTheGateReceiptCannotHashAnEmptyDiff over the shipped markdown, plus a from-scratch zsh re-verification in a throwaway repo: unquoted expansion gave 0 diff lines and e3b0c442...b855, the array form 14 lines and a real digest'
        forced: false
      - from: reproduced
        to: fixed
        reason: both shipped agent specs build the pathspec as a shell array expanded quoted, and both name e3b0c442...b855 as a refusal condition — the architect will not write a receipt hashing empty over a non-empty file set, the reviewer discards one. The reviewer's <SESSION FILE SET> placeholder is replaced by the same concrete construction so the two cannot drift. Installed copies regenerated from source; doctor reports 'library agents current'.
        forced: false

.. bug:: A Swift extension of literal-bound static-let constants gets an unsatisfiable coverage gate
   :id: BUG_034
   :status: fixed
   :severity: medium
   :priority: high
   :area: adapters/swift_xcode
   :batch: unsatisfiable-gates
   :slug: swift-extension-of-literal-static-lets-gets-an-unsatisfiable-gate
   :affects: COMP_ADAPTERS_SWIFT_XCODE

   **Symptom.** ``coverage.meets_floor`` (block level) refused a finished, low-risk
   merge in a consuming project with "1 changed source file(s) have no coverage
   row". The file was 180 lines of seven bare ``protocol : AnyObject`` declarations
   plus an ``extension Notification.Name`` of three ``static let`` constants and an
   ``extension Notification`` of one. No function body anywhere — no test anyone
   could write would ever produce a row for it, which this project's own doctrine
   treats as an engine defect rather than a reason to force or lower anything.

   **Confirmed genuinely absent, three independent ways** — not merely "present
   at 0%":

   - the engine's own recorded pipeline evidence;
   - a direct ``xcrun xccov view --report --json`` extraction, absent from every
     one of nine targets' file lists, with sibling files in the same directory
     present and correctly numbered;
   - a raw ``grep -c`` over the full 23 MB JSON report, returning 0.

   **Expected.** ``_explain_absent_rows()`` in ``core/rules.py`` already sets a file
   aside on positive evidence that it can never earn a row, and deliberately
   keeps every "cannot tell" case blocking. A file of only declarations belongs
   to that family, and did not read as one.

   **Cause.** One layer below ``_explain_absent_rows()``, in
   ``adapters/swift_xcode.py``: ``_swift_carries_executable_code()``'s first
   substantive check was ``if _INITIALISED_BINDING.search(text): return True``, and
   ``static let x = Notification.Name("…")`` matches it, returning True file-wide
   before any block is classified. Reaching block classification would not have
   helped either — ``extension`` is deliberately absent from
   ``_INERT_BLOCK_HEADER``.

   Compounding cause worth recording: ``_strip_comments_and_strings`` deleted
   string literals entirely, quotes included, contradicting its own docstring —
   so after stripping, ``Notification.Name("abc")`` and ``Registry()`` read as
   identical text, and the zero-argument exclusion the fix needed was
   unimplementable until that was corrected too.

   **Repro:**

   .. code:: python

      _swift_carries_executable_code(
          'extension Notification.Name { static let x = Notification.Name("abc") }'
      )

   returned ``True`` before the fix, ``False`` after. A bare ``protocol`` already
   answered ``False`` correctly both before and after.

   **Fix, landed in commit ``9cfd372``.** A ``static`` binding whose right-hand side
   is a bare literal, or a capitalised dotted constructor call with at least one
   literal argument, is now treated as a declaration rather than code. The
   ``static`` condition is load-bearing, verified by removing it: an *instance*
   stored property's initialiser is emitted into every ``init`` the type has, so it
   is a real coverage line, and dropping the condition breaks the pre-existing
   ``test_a_stored_property_initialiser_carries_code``.

   **Evidence of tightness, worth recording as the model for this kind of
   change.** Across the consuming project's 982 Swift files exactly four answers
   changed, all True→False, all four independently confirmed absent from the real
   coverage report; and a false-inert check in the dangerous direction found 0 of
   the 48 files the new reader calls inert have a coverage row, versus 0 of 44
   for the old reader — the relaxation added no lie. Full suite 1224 passed / 0
   failed; mutation score with the adapter reverted, exactly 4 fail, and the same
   controls stay green in both states.

   **Filed after the fact.** The fix landed without a backlog entry. This records
   it because the family it belongs to — a coverage gate a consuming project
   cannot satisfy by any amount of work — is this repository's most important
   one, and its own doctrine says a wedge like this is an engine defect to fix,
   never a reason to force or lower a gate.

   **Related:** :need:`BUG_025 <BUG_025>` is the sibling shape in the same adapter —
   a stored-property-only type rather than a static-literal binding — and shares
   the ``_INITIALISED_BINDING``/``_INERT_BLOCK_HEADER`` mechanism family. Read it
   first; the "not the same defect as BUG_019" distinction it draws applies here
   too, for the same reason.

   **Provenance.** Fix authored and merged without a matching backlog entry;
   filed here as the record, from the recorded fix evidence.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'confirmed absent three independent ways: recorded pipeline evidence, direct xccov JSON extraction absent from all nine targets'' file lists, and grep -c over the full report returning 0; source-read isolated the false True to _INITIALISED_BINDING firing before block classification'
        forced: false
      - from: reproduced
        to: fixed
        reason: 'static-let-bound-to-a-bare-literal-or-literal-argument-constructor now reads as a declaration in _swift_carries_executable_code (adapters/swift_xcode.py); landed in commit 9cfd372. Mutation-scored: exactly 4 of 982 files flip True to False, all four confirmed absent from the real coverage report; full suite 1224/0'
        forced: false

.. bug:: invariant pipeline test records a false red when INVARIANT_SESSION_ID collides with its own suite
   :id: BUG_035
   :status: fixed
   :severity: high
   :priority: high
   :area: domains/session, domains/pipeline
   :batch: inert-instruments
   :slug: session-id-env-var-poisons-this-repos-own-suite
   :affects: COMP_DOMAINS_SESSION, COMP_DOMAINS_PIPELINE

   **Symptom.** In THIS repository, ``INVARIANT_SESSION_ID=<id> invariant pipeline test`` records a false red. The environment variable is inherited by the
   spawned ``pytest`` process — ``commands.test`` is ``uv run pytest``, run through
   ``subprocess.run(..., shell=True)`` with no ``env=`` override — and this
   repository's own suite exercises session creation, so every test that starts a
   session collides with the named one and dies on ``UsageError: session '<id>' already exists``. Measured: **25 failures**, all in ``tests/test_domains.py``,
   against an identical bare ``uv run pytest`` that is fully green (1224 passed).
   Reproduced independently by two different agents in the same session.

   **Expected.** The documented way to name a session for recording purposes
   should not change what the suite it records measures. A false red should not
   be reachable by following the documented workflow.

   **Mechanism, confirmed from source, not merely from the symptom.**
   ``SessionDomain.start()`` resolves the id to register as
   ``requested = session_id or os.environ.get("INVARIANT_SESSION_ID")``
   (``domains/session.py``), and raises ``session '<id>' already exists`` when that
   id is already registered. Most of ``tests/test_domains.py`` calls ``.start(...)``
   without an explicit ``session_id``, relying on the derived/uuid fallback — so
   when ``INVARIANT_SESSION_ID`` is set in the environment the pytest process
   inherits, every one of those calls resolves to the *real, currently-running*
   session id instead, and collides with the session already registered under
   it. ``domains/pipeline.py``'s ``subprocess.run`` calls (``~line 503``, ``~line 655``)
   pass no ``env=``, so the inheritance is unconditional — nothing in the test
   command strips the variable before handing the environment to the child
   process.

   **Why this is the one repository where it fires.** No other project's own
   suite exercises ``invariant session start``, so nowhere else does inheriting
   ``INVARIANT_SESSION_ID`` collide with anything. The documented way to name a
   run for recording purposes is exactly what poisons the recorder, and it does
   so only in the one repository that tests sessions — which is this one.

   **Already wrote poisoned evidence once.** Two ``"green"``-phase ``test_runs``
   records in durable pipeline evidence show 1222 executed / 25 failed from this
   exact collision, later superseded by clean ``refactor``/``coverage``-phase entries
   at 1224/0, which the gates correctly read as the trustworthy result. Nothing
   was ultimately blocked this time, but the false counts sat in the durable
   record until superseded, and an **inflated failure count on a phase that later
   reads green is the harder of the two mistakes to notice** — a false green
   hides behind nothing, a false red hides behind a plausible-looking regression
   that a reader has to actively disprove.

   **Not prescribing the fix.** Plausible directions exist — the CLI could avoid
   exporting the variable into a spawned test process, or the test fixtures could
   neutralise it before ``SessionDomain.start()`` sees it — but choosing between
   them changes behaviour outside this entry's scope. Recorded as a finding, not
   a repair.

   **Fixed on both routes.** ``domains/pipeline`` strips ``INVARIANT_SESSION_ID`` from
   the environment it spawns the suite and the coverage run with — the variable
   names the session a CALLER records against, and the test command is a child
   program, not a caller. The stripping is declared as ``CHILD_ENV_STRIPPED`` rather
   than inlined at three ``subprocess.run`` sites, so a fourth spawn cannot
   reintroduce it silently. The parent's own environment is untouched, which a
   test pins.

   The other route is a bare ``uv run pytest`` typed in a shell that has the
   variable exported, which no engine change can reach. ``tests/conftest.py`` gains
   an autouse fixture that deletes it, alongside the one already there for
   ``CLAUDE_PROJECT_DIR`` — the same ambient-environment hazard, found the same way.

   Measured after the fix: the full suite is green both with
   ``INVARIANT_SESSION_ID`` set to a live session id and without it. The entry
   declined to prescribe between these two directions; both were taken, because
   they close different routes and neither closes the other.

   **Related:** same family as :need:`BUG_029 <BUG_029>` and
   :need:`BUG_031 <BUG_031>` — an instrument reporting a result for something other
   than what it was asked about, here the proof-of-red itself rather than its
   reader. :need:`BUG_020 <BUG_020>` is the reading half of the same family.

   **Provenance.** Reported from a consuming project's session, reproduced twice
   independently within it against this repository's own suite. Not yet fixed
   here.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: tests/test_domains.py::TestTheSuiteDoesNotInheritTheRecordersIdentity — a suite whose test command asserts the variable is unset failed, proving the spawned process inherits INVARIANT_SESSION_ID
        forced: false
      - from: reproduced
        to: fixed
        reason: 'both routes closed: domains/pipeline strips INVARIANT_SESSION_ID from the environment of every command it spawns (declared as CHILD_ENV_STRIPPED, applied at all three subprocess sites), and tests/conftest.py deletes it in an autouse fixture for the bare ''uv run pytest'' route. Measured: 1255 passed with the variable exported to a live session id, 1255 passed without it.'
        forced: false

.. bug:: coverage.meets_floor blocks forever on a committed binary file, and advises a remedy that does not exist
   :id: BUG_036
   :status: fixed
   :severity: high
   :priority: high
   :area: core/rules
   :batch: unsatisfiable-gates
   :slug: a-committed-binary-file-is-unanswerable-by-every-set-aside-branch
   :affects: COMP_CORE_RULES

   **Symptom.** A consuming project's session committed a golden PNG as part of a
   snapshot-test change. ``coverage.meets_floor`` blocked with

   ::

      1 changed source file(s) have no coverage row
      HeapTests/.../__Snapshots__/StorageScreenSnapshotTests/test_exemptBlock....1.png
              try: check these files are in the coverage target
              try: or drop them from the session file set if they are not code

   and nothing the session could do would clear it. There is no test to write
   for an image, and the advertised remedy does not exist.

   **Expected.** A gate blocks work that could be different. A binary asset
   cannot be covered by any test in any ecosystem, so demanding a coverage row
   for one is a gate nobody can clear — the shape :need:`BUG_002 <BUG_002>` was filed
   against, and the one people learn to route around.

   **Mechanism, traced end to end.**

   1. The PNG is a changed file that is not docs (``_UNMEASURED_SUFFIXES`` lists
      only text-document suffixes), so it enters ``source_changed``.
   2. No coverage reporter emits a row for an image, so it lands in ``unmatched``.
   3. ``_explain_absent_rows`` sets a file aside only on positive evidence, and
      every branch answered "cannot tell": the file was not deleted; the
      reporter's configured scope is usually unreadable for an asset directory;
      and **both adapter hooks are keyed on a language suffix**, so
      ``Adapter.is_test_file`` and ``Adapter.carries_executable_code`` return ``None``
      for anything that is not source — ``swift_xcode`` returns ``None`` for any
      path whose suffix is not ``.swift``. A binary asset was unanswerable by every
      branch.
   4. The verdict's own remedy was unreachable: ``invariant session files`` exposes
      ``add`` only. That absence is deliberate — a remove verb would be a lever for
      dropping a real source file out of the floor.
   5. And it would not have worked anyway. ``_changed_set`` is a **union** of
      ``git.changed_files``, ``git.branch_files(base)`` and the declared set, so a
      file committed on the session's branch is re-supplied by git however the
      declared set is edited.

   **Resolution — a fact, not a relaxation.** A binary file positively carries no
   executable lines. That is a property of the bytes, true in every ecosystem, so
   it belongs in ``core/rules`` rather than in any one adapter, and it is exactly
   the kind of positive evidence the other branches already demand.
   ``_explain_absent_rows`` gained a final branch, asked after every adapter
   opinion, that names the file in the set-aside list:

   ::

      ... (a binary file; no executable lines to measure)

   ``_is_binary_file`` reads a bounded 8 KiB prefix and answers ``True`` only on a
   successful read whose content cannot be text — a NUL byte, or a prefix that
   will not decode as UTF-8. Every other outcome returns ``False`` and leaves the
   demand standing, because the asymmetry the function documents applies here
   unchanged: calling a live file inert passes unmeasured code silently, while
   calling an inert file live costs a visible block that sends someone to look.

   The decode is **incremental**. A bounded read can cut a multi-byte character
   in half, and a decoder that reported that truncation as an error would call a
   live UTF-8 source file binary — the one direction that must stay shut. A test
   constructs a Swift file whose final character begins at the last byte read and
   asserts it still blocks; it fails against a non-incremental decode.

   Ordering is unchanged: the no-row check still runs before
   ``_excluded_by_manifest_globs``, so suppressing a path from the reporter still
   cannot buy a pass (:need:`BUG_026 <BUG_026>`'s guard is untouched).

   **The advice was corrected rather than made true.** The removed line is now:

   ::

      try: or check the reporter and git spell the path the same way
      try: or teach the ecosystem adapter to answer for this file type

   Both are available and both work. Adding ``session files remove`` to make the
   old line true was rejected: it would be a documented bypass of the floor.

   **Related.** :need:`BUG_019 <BUG_019>` is the same family from the Python side —
   a file the reporter is defined never to emit a row for. :need:`BUG_025 <BUG_025>`
   and :need:`BUG_026 <BUG_026>` are the other two unsatisfiable-gate members. This
   one differs from all three in that no adapter was even *positioned* to answer:
   the question was about the bytes, and every hook asked about the language.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: tests/test_rules.py::TestABinaryAssetHasNoExecutableLinesToMeasure — a changed binary asset with no coverage row blocked the gate, and the verdict's own fixes named an action `invariant session files` does not expose
        forced: false
      - from: reproduced
        to: fixed
        reason: '_explain_absent_rows gained a binary-file branch keyed on the file''s own bytes (NUL, or a bounded prefix that will not decode as UTF-8), setting the file aside with a named reason; the misleading fix line was replaced. Controls pin both directions: a text file with no row still blocks, a file that cannot be read still blocks, and a text file whose last character straddles the sniff boundary still blocks. Measured: 1270 passed.'
        forced: false

.. bug:: A backward pipeline edge is gated by the origin state's exit gates
   :id: BUG_037
   :status: fixed
   :severity: medium
   :priority: medium
   :area: core/fsm, domains/pipeline
   :batch: unsatisfiable-gates
   :slug: a-backward-pipeline-edge-is-gated-by-the-origin-states-exit-gates
   :residue: The session machine's 'reviewed' state still carries privacy.clean as an EXIT gate, so 'reviewed -> working' — going back to fix something a review found — is gated by it. Same shape as this entry and deliberately not fixed here: session 8dab3bbd holds an unlanded BUG_047 fix whose whole subject is making privacy.clean screen at that review boundary, and moving the gate underneath it would collide. Only the abandon role was added to session.yaml, which is additive. Revisit once BUG_047/048 land.
   :affects: COMP_CORE_FSM, COMP_DOMAINS_PIPELINE

   **Symptom.** In ``tdd``'s ``reviewing`` state, ``invariant context`` advertises
   ``may advance to: committed, red, refactor, abandoned``. Two of those four are
   backward edges, and both are refused — by ``coverage.meets_floor`` and
   ``agents.review_agent_ran``, which are ``reviewing``'s exit gates and exist to
   permit **shipping**.

   So a review that discovers more TDD work is owed forces a choice between
   satisfying ship gates on a change the reviewer has just judged unfinished, and
   doing the work in the wrong state. Both routes end at ``--force``, which is the
   outcome this library treats as the defect.

   **Cause.** Gates are attached to a state and evaluated on *leaving* it,
   irrespective of where the edge goes. ``reviewing``'s exit gates are the right
   question for ``reviewing → committed`` and the wrong one for ``reviewing → red``:
   going back to write a failing test is not landing anything, so nothing they
   assert is owed yet.

   **Suggested fix,** from the session that found it: gate an edge by its
   DESTINATION rather than by its origin's exit set — or, equivalently, let a
   state declare per-edge gates, so ``→ committed`` keeps the ship gates and
   ``→ red`` / ``→ refactor`` carry none. Reviewing's exit gates would then be spelled
   as the entry gates of ``committed``, which is a change to the shipped
   methodologies as well as to the evaluator.

   Worth checking during the fix, since the same shape recurs: ``refactor → red``
   is gated by ``test.all_pass`` (defensible — a refactor should leave the suite
   green before more red is written), and the session machine's backward edges
   have the same origin-exit structure.

   **Not fixed here.** Found by a consuming project while an unrelated adapter
   defect was being fixed (:need:`BUG_039 <BUG_039>`); recorded rather than folded
   in, because it changes the FSM's gate model and the shipped methodology files,
   which is its own change with its own review.

   **Related.** :need:`BUG_002 <BUG_002>` is the same family from the other side —
   gates structurally inapplicable to the change in front of them — and was
   settled by declaring a different methodology. That escape does not reach this
   one: the pipeline is genuinely a ``tdd`` pipeline, and the state it needs to
   return to is one ``tdd`` itself declares legal.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'Reproduced across every shipped methodology in tests/test_machines.py::TestBUG037ABackwardEdgeIsNotGatedByTheShipGates — 11 red. tdd''s reviewing -> red and -> refactor were refused by coverage.meets_floor, docs.updated, docs.diagrams_current, agents.design_agent_ran, agents.review_agent_ran and privacy.clean; the same shape in bugfix, spec-first, chore and docs; and every -> abandoned edge carried the full ship set too. Two further instances the entry did not name, found by the reproduction rather than assumed: bugfix''s reproducing -> not_reproducible was gated by test.new_test_fails, which asks for a failing test as the price of recording that no failing test could be written; and every design state''s -> abandoned edge was gated by the injected docs.read_architecture.'
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Gates are written on the state they are a precondition OF. Every shipped pipeline machine restated that way: the ship gates moved from the review state''s exit set to the landing state''s entry set, and the injected gates moved with them — DOCS_FIRST_GATES and AGENT_ROLE_GATES now key on a new ''landing'' role as entry gates. ADR-003 D2''s conclusion is unchanged and this is not a reopening of it: the landing edge is evaluated at the same moment with the same obligation, and what stopped is their firing on the retreats. A pipeline machine declaring a review state and no landing state is REFUSED at load, because a role nothing carries injects nothing and that omission would otherwise be silent; the design-role case is deliberately not given the same treatment, since chore omits it on purpose. A second role, ''abandon'', makes an edge into it take only that state''s own entry gates — the exit gates of the state being left all exist to protect an outcome, and abandonment is the edge with none, so holding it to them made finishing the precondition of stopping. Honoured by both PipelineDomain.gates_for and SessionDomain.pending_gates. Checked for the opposite error and one instance was caught by the suite rather than by review: chore''s ''doing'' never had an exit gate, so putting test.all_pass on its reviewing entry would have demanded a test run to START reviewing a dependency bump. Reverted, and the never-gated edges are now pinned. Reporting changed with the model: gates_by_destination and evaluate_gates_by_destination report per edge, so ''invariant pipeline gates'' and the context projection say what each destination owes instead of one flat list that hid which two of four successors were refused. Docs: state_model.md''s machine example and its gate-model paragraph, the docs-first injection prose, and an amendment to ADR-003.'
        forced: false

.. bug:: A swift-testing run scrapes as zero tests executed
   :id: BUG_038
   :status: filed
   :severity: high
   :priority: medium
   :area: adapters/swift_xcode
   :batch: unsatisfiable-gates
   :slug: a-swift-testing-run-scrapes-as-zero-tests-executed
   :residue: COUNTING HALF DIAGNOSED from real artefacts, 2026-08-23 — a captured swift-testing console log and the .xcresult bundle from the SAME xcodebuild invocation in a consuming project. Not reproduced in this repository's suite and no test written, so the entry stays 'filed'. (1) Console line confirmed verbatim: '(x) Test run with 1071 tests in 88 suites failed after 37.239 seconds with 13 issues.' Its 1071 equals the count of @Test symbols greppable from that project's source, so the console counts test FUNCTIONS, exactly as the entry predicts. Only the FAILED shape is attested\; a passing run's line is unseen. (2) The bundle does not fix this at the level the adapter reads: 'xcresulttool get test-results summary' reports totalTestCount 15588, counting a parameterised test as ONE. (3) But the bundle DOES enumerate the arms one level deeper — 'get test-results tests' returns 42 Arguments nodes under 6 parameterised Test Case nodes, each arm carrying its own result and durationInSeconds. True executed arms 15624 against 15588 reported: an under-count of 36.; THE ENTRY'S FIX DIRECTION MUST BE RESTATED BEFORE ANYONE IMPLEMENTS IT. It says the bundle is the only countable source and 'this adapter already prefers it — the fix belongs there'. True of the bundle, FALSE of the level result_bundle_counts reads: 'get test-results summary' flattens arms by construction, so teaching that level this shape would be confidently wrong by exactly the amount the entry warns a second console pattern would be. The arms are reachable only from the 'tests' sub-command, which was a 9.4 MB JSON tree for the captured run. That makes the fix a choice between two PRODUCTS, not two implementations: parse the node tree on every coverage run to recover a true arm count, or keep the summary number and state explicitly that parameterised arms are collapsed. The ratio in the observed corpus — 36 arms out of 15588, from 6 parameterised tests — argues for the second, but that ratio is a property of THAT corpus and the under-count is unbounded in a project that leans on parameterisation. Decide it on the general case, with the observed ratio as one data point rather than as the answer.; THE SILENT-FALLBACK HALF IS UNTOUCHED AND IS THE MORE ACTIONABLE ONE. The reported symptom '0 test(s) executed, counts scraped from console output' means result_bundle_counts returned None and the console parser ran, and nothing reports WHY — the entry's own second requirement. Cause now ATTESTED rather than hypothesised, in two independent places. In this repository: reading the captured bundle required setting DEVELOPER_DIR by hand, because xcrun resolves through xcode-select and a machine pointing at CommandLineTools cannot find xcresulttool at all. In the consuming project, from inside its own xcodebuild process in the same run: 'IDETestOperationsObserverDebug: Failure collecting diagnostics from simulator: Process exited with error 72: xcrun: error: unable to find utility simctl, not a developer tool or in PATH'. That is simctl rather than xcresulttool, so it is not the failure itself, but it is the same resolver failing the same way in the same process on the same machine. That project's manifest and memory both carry the misconfiguration twice over. Note the adapter already retries against _newest_developer_dir() when DEVELOPER_DIR is unset — so a run can still fall back for other reasons, and saying which is the point. This half is separable from the counting decision and worth shipping on its own: it converts a silently wrong count into a reported one.; The captured run was RED (13 issues, 53 failures), so it cannot demonstrate the entry's HEADLINE symptom — a green run scraping as executed==0 and clearing test.all_pass. It speaks only to the count question. The entry asks the fixer to measure which symptom is present post-BUG_029 rather than repeat its claim\; on this evidence the counting defect is real and the green-passes-vacuously reading remains unmeasured. Both the reporter and this session state that limit explicitly rather than letting the capture stand for more than it shows.; DECISION 2026-10-20 (user-approved): Fix the silent-fallback half first (say WHY the bundle read failed — e.g. xcresulttool unreachable via xcode-select/DEVELOPER_DIR). For the counting half, report the swift-testing console count as a count of test FUNCTIONS, labelled as such\; do not parse the full xcresult tests node tree.
   :affects: COMP_ADAPTERS_SWIFT_XCODE

   **Symptom.** ``invariant pipeline test`` against a selector whose tests are
   written with **swift-testing** rather than XCTest reports:

   ::

      0 test(s) executed, counts scraped from console output

   for a run that really did execute and pass.

   **Cause.** ``_parse_counts`` in ``adapters/swift_xcode.py`` matches XCTest's
   summary line, ``Executed N tests, with M failures``. swift-testing prints a
   different one — ``Test run with 2 tests in 1 suite passed after N seconds`` —
   which no pattern here matches, so the count comes out zero.

   **Consequences,** and the second needs re-measuring rather than repeating:

   - ``test.all_pass`` is unsatisfiable for a swift-testing-only selector, since a
     green with ``executed == 0`` is exactly the shape the ``-only-testing:`` folder
     hazard exists to distrust.
   - The finder also reports that ``test.new_test_fails`` accepts an uncountable
     red. That was true of the rule before :need:`BUG_029 <BUG_029>`, whose fix
     rejects ``executed == 0``. Under that fix the more likely present-day symptom
     is the opposite — a genuine red that cannot be *recorded* as one — so
     measure which it is before writing the test.

   **A wrinkle that rules out the obvious fix.** Teaching the scraper
   swift-testing's line would still under-count: its number is of test
   FUNCTIONS, not of the arms a parameterised test expands to. The ``.xcresult``
   bundle is the only countable source, and this adapter already prefers it —
   ``result_bundle_counts`` reads ``xcresulttool``, with the console parser as the
   fallback. The fix belongs there: make the bundle reader cover this shape (and
   say why it did not here — a run that produced no bundle, or a bundle whose
   read failed), rather than adding a second console pattern that would be
   confidently wrong by a different amount.

   **Not fixed here.** Found by a consuming project while an unrelated adapter
   defect was being fixed (:need:`BUG_039 <BUG_039>`). Recorded rather than folded
   in: proving it needs a real swift-testing run to capture the console text and
   a bundle from the same run, which this repository's own Python suite cannot
   produce.

   **Related.** :need:`BUG_029 <BUG_029>` hardened the rule that reads these counts;
   this is the layer that produces them. The pairing is the point — a rule that
   now refuses an uncountable run is only as useful as the adapter's ability to
   count a run that really happened.

.. bug:: A touched Xcode resource file gets an unsatisfiable coverage gate
   :id: BUG_039
   :status: fixed
   :severity: medium
   :priority: high
   :area: adapters/swift_xcode
   :batch: unsatisfiable-gates
   :slug: a-touched-xcode-resource-file-gets-an-unsatisfiable-coverage-gate
   :affects: COMP_ADAPTERS_SWIFT_XCODE

   **Symptom.** ``coverage.meets_floor`` (block level in the consuming project)
   refused a finished change whose load-bearing edit was ``Info.plist``, with test
   files and docs beside it and zero production Swift:

   ::

      [BLOCK] coverage.meets_floor: 1 changed source file(s) have no coverage row
              <App>/Info.plist. Either they are outside the measured target, or the
              path spellings still disagree.

   ``xccov`` measures compiled Swift/ObjC/C only, so a property list can never earn
   a coverage row and no amount of testing clears the gate. ``pipeline ack`` refuses
   a blocking rule, leaving only ``--force``, which that project's own doctrine
   excludes. The gate's second suggestion — drop the file from the session's
   declared file set — asks the author to hide the change in order to describe
   it, and would hand ``session.own_file_set_only`` a false picture besides.

   **Not a defect in the rule.** ``_explain_absent_rows()`` in ``core/rules.py``
   splits "no row could exist" from "no row was produced", sets a file aside only
   on positive evidence, names everything it sets aside, and treats every "cannot
   tell" as "do not relax". That asymmetry is correct and was preserved: calling
   a live file inert passes unmeasured code silently, while calling an inert file
   live costs a visible block.

   **Cause,** one rung down, in ``adapters/swift_xcode.py``:

   .. code:: python

      def carries_executable_code(self, path: Path) -> bool | None:
          if path.suffix != ".swift":
              return None

   Every non-Swift path answered "cannot tell", so the rule correctly refused to
   relax on it. But an Xcode adapter genuinely *can* answer this positively for
   its own ecosystem's resource kinds — that is the knowledge the question exists
   to encode. The adapter knew a fact about the LANGUAGE and none about which of
   its ecosystem's file kinds a compiler ever sees.

   **Fix.** An allowlist, checked after the compiled kinds and never a denylist:

   - resource suffixes — ``.plist``, ``.entitlements``, ``.strings``, ``.stringsdict``,
     ``.xcstrings``, ``.storyboard``, ``.xib``, ``.xcassets``, ``.xcconfig``;
   - resource *directory bundles* — a path with an ``.xcassets``, ``.lproj``,
     ``.docc``, ``.xcodeproj``, ``.xcworkspace`` or ``.xcdatamodeld``/``.xcdatamodel``
     ancestor, which is the half that matters in practice: a touched path more
     often names a payload inside a catalog (``…/icon.png``, or an
     ``.xcdatamodel/contents`` with no suffix at all) than the catalog itself;
   - everything else keeps answering ``None``.

   Three properties keep it narrow. ``.m``, ``.mm``, ``.c``, ``.cc``, ``.cpp``, ``.cxx``,
   ``.h``, ``.hpp``, ``.hh`` — which ``xccov`` measures as readily as Swift — are listed
   explicitly and tested FIRST, so neither a future allowlist entry nor the
   directory rule can reach one; ``.swift`` keeps its existing content-parsing path
   for the same reason. Documentation and configuration suffixes are deliberately
   excluded, because ``core/rules._UNMEASURED_SUFFIXES`` already holds ``.md``,
   ``.rst``, ``.json``, ``.yaml`` and ``.toml`` out of the changed-source set for every
   ecosystem, and the same judgement in two layers is the duplication that
   drifts. ``.bundle`` and ``.framework`` are excluded too: a vendored bundle may
   hold anything, and "vendored" is exactly the guess this table must not make.

   **Repro,** recorded red in this pipeline before the fix existed, at both
   layers — the adapter's answer, and the gate's verdict, which reproduced the
   consuming project's message verbatim:

   ::

      AssertionError: 1 changed source file(s) have no coverage row
      detail='<App>/Info.plist. Either they are ou…'

   **Evidence of tightness,** the model BUG_034 set. Over the consuming
   project's 3789 tracked files, exactly 73 answers flip ``None`` → ``False`` and
   **none** flips ``True`` → ``False`` (the "carries code" count is identical, 1753,
   before and after). Of the 122 files the new answer calls inert, **0** appear
   among the 100661 coverage rows that project's own runs recorded — the
   relaxation added no lie. Full suite 1287 passed / 0 failed. In the mutation
   matrix, the 15 new "is False" assertions fail with the adapter reverted and
   the 13 "must not be called inert" controls stay green in both states.

   **Related.** :need:`BUG_025 <BUG_025>` and :need:`BUG_034 <BUG_034>` are the same
   family — a coverage gate a consuming project cannot satisfy by any amount of
   work — and the same adapter. They are the *contents* half of the question;
   this is the *kind* half. :need:`BUG_019 <BUG_019>`'s distinction applies here too
   and in the same direction: a test file carries executable code and is set
   aside by a different question, whereas a property list genuinely carries none,
   so answering here is a fact rather than a lie told to relax a gate.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'red recorded in this pipeline at both layers before the fix: 15 adapter assertions and the gate verdict reproducing the consuming project''s message verbatim'
        forced: false
      - from: reproduced
        to: fixed
        reason: 'resource-kind allowlist plus resource-bundle-directory rule in SwiftXcodeAdapter.carries_executable_code; compiled kinds tested first and never relaxed. Mutation-scored over 3789 real files: 73 flip None to False, 0 flip True to False, 0 of the 122 inert files hold a coverage row among 100661 recorded; full suite 1287/0'
        forced: false

.. bug:: An agent dispatch from a host session rooted in another repository is recorded nowhere
   :id: BUG_040
   :status: fixed
   :severity: medium
   :priority: high
   :area: cli/main, hosts/claude_code, domains/session
   :batch: unsatisfiable-gates
   :affects: COMP_CLI_MAIN, COMP_HOSTS_CLAUDE_CODE, COMP_DOMAINS_SESSION

   **Symptom.** A session that had dispatched both a ``feature-architect`` and a
   ``compliance-reviewer`` could not leave ``reviewing``. ``invariant pipeline gates``
   reported:

   ::

      [BLOCK] agents.design_agent_ran: no design agent ran in this pipeline
      [BLOCK] agents.review_agent_ran: no review agent ran in this pipeline

   ``invariant pipeline status --json`` for that session carried ``docs_read``,
   ``test_runs``, ``coverage`` and ``coverage_run`` under ``evidence`` — and **no
   ``agents_run`` key at all**. Both rules are ``block`` in this repository's manifest,
   so ``reviewing``'s exit (``core/machines.py:59-60``) was unsatisfiable by any work
   the track could do. The distinguishing condition: the host session driving the
   work was rooted in a **different repository** than the pipeline.

   **Expected.** An agent dispatched for a track's work is recorded against that
   track's pipeline, whatever repository the host session driving it is rooted in.
   Failing that, the attempt fails loudly — an evidence write that finds no
   destination must say so, not return silently.

   **Repro.**

   1. Have two invariant projects, A and B, both with a manifest at their root.
   2. From a host session whose project root is A, dispatch an agent for a live
      isolated session in B, naming B's worktree by absolute path in the prompt.
   3. Inspect ``agents_run`` in B's pipeline evidence — the key is never written.

   The control, which is what makes this a diagnosis rather than a guess: the same
   prompts, aimed at the same worktree, dispatched from a host session rooted in B
   bind correctly and turn both gates green with no force, no ack, and no
   hand-edited state. Same inputs, different host root, opposite outcome.

   **Cause — verified call chain, refs current on master ``6e83fd4``.**

   1. ``cli/main.py:1372-1375`` — ``cmd_hook`` sets ``manifest_start`` from
      ``CLAUDE_PROJECT_DIR``, the **host's** project dir, guarded by
      ``Manifest.find_root(project_dir)``.
   2. ``cli/main.py:118-119`` — ``Manifest.load(manifest_start or self.cwd)``, then
      ``JsonStore(self.manifest.state_root)``. A host rooted in A therefore gives
      ``ctx.manifest`` and ``ctx.store`` belonging to **A**.
   3. ``cli/main.py:172`` — ``ctx.sessions`` is a ``SessionDomain`` over A's store.
   4. ``cli/main.py:246`` — a dispatch carries no ``file_path``, so
      ``_worktree_named_in_prompt(prompt)`` runs and correctly extracts B's absolute
      worktree path.
   5. ``cli/main.py:266`` — ``self.sessions.by_worktree_containing(named)`` iterates
      ``self.list()`` (``domains/session.py:294``), which enumerates **A's** sessions
      only. B's worktree is not in A's registry, so it returns ``None``.
   6. ``sessions.resolve(...)`` then consults A as well, and also finds nothing.
   7. ``hosts/claude_code.py:803`` — ``session_for_payload`` returns ``None``, the hook
      returns ``{}``, and ``record_agent_run`` (``domains/pipeline.py:466``) is never
      reached.

   **Why this is distinct from the fix that looks like it.** Commit ``f1396f9``
   ("Attribute an agent dispatch by the worktree its prompt names") fixed one host
   session driving two tracks in the **same** repository: there the registry held
   both sessions and the *precedence* was wrong, so the dispatch banked on
   whichever session the host was bound to. This is the mirror image. The
   precedence is now right and the **registry is wrong**. f1396f9's
   worktree-in-prompt route is real, and structurally **unreachable across
   repositories** — it makes the correct signal available and then looks it up in
   the wrong store. The prompt's absolute path already identifies B unambiguously;
   nothing uses it to re-root the manifest and store before the lookup.

   **Pre-empting the obvious objection.** ``docs_read`` *did* record for the wedged
   session, with worktree-absolute paths, which looks like counter-evidence that
   the store was ever wrong. It is not: those come from ``Read`` calls, which carry
   ``file_path`` and so take BUG_010's payload-attribution route, and they were
   recorded while the host was rooted in this repository. The two facts are about
   different host roots and different attribution routes.

   **A nuance for whoever fixes it.** The ``Manifest.find_root(project_dir)`` guard
   at ``cli/main.py:1374`` means this failure needs A to itself be an invariant
   project. If A has no manifest, ``manifest_start`` stays ``None`` and resolution
   falls back to ``Path.cwd()`` (``cli/main.py:118``) — which for a hook is not
   guaranteed to be anywhere in particular. That path is differently wrong, not
   safe, and should not be mistaken for the boundary of the defect.

   **Affected.** The hook's context construction in ``cli/main.py`` —
   ``Context.__init__``'s manifest/store rooting and ``Context.session_for_payload``'s
   lookup — with ``SessionDomain.by_worktree_containing`` as the registry it
   queries
   and ``hosts/claude_code._post_tool_use`` as the caller that goes silent. See the
   ``hosts/`` layering rule in ``docs/architecture/overview.md``, which
   already records that payload-derived attribution above ``hosts/`` is deliberate,
   and the "Host bridge" section of ``docs/architecture/cli.md`` for the
   ``CLAUDE_PROJECT_DIR`` resolution rule this defect sits on.

   **Fix sketch / next step — record only, do not implement from this entry.**
   From the session that found it: when ``_worktree_named_in_prompt`` yields a path
   lying outside the current manifest's root, re-root the manifest and store from
   that path before resolving the session, rather than looking it up in the host's
   registry. Keep f1396f9's conservatism — only for a path matching a registered
   live worktree, and only when every match lands on one session. A red test should
   pin the cross-root case specifically: a dispatch payload naming B's worktree,
   evaluated with ``CLAUDE_PROJECT_DIR`` set to A, must reach B's pipeline. Note this
   is the exact shape BUG_010's residue reserved as "ADR-worthy" — whether a hook
   may resolve a second manifest and write to a second state root — so the fix
   carries that decision with it.

   **A wedge the manifest already anticipated.** This repository's own manifest
   comments the ``agents.*_ran`` block settings with the warning that "a pipeline in
   this repository is driven from a host session whose project root is a different
   repository, so an agent dispatched for this work may not be recorded against
   this pipeline at all. If that wedges, the defect is in the recording and gets
   fixed there. It is not a reason to lower the gate." That anticipated wedge has
   now happened. Recording it here is what keeps the second half of that sentence
   honest.

   **Related.** :need:`BUG_010 <BUG_010>` is the parent: its narrow half (payload
   attribution by ``file_path``) shipped, and its residue names this wide half
   verbatim — "a hook payload whose target lies in another repository" — while
   predicting the cross-root dispatch would land in the *host's* pipeline.
   After f1396f9 it lands in none, which is the better failure of the two and
   still a failure. :need:`BUG_028 <BUG_028>` is the sibling in the
   ``inert-instruments`` batch: same silent-drop site, different mechanism — its
   cases are within one store (unbound sessions, and a dispatch with no
   pipeline at the end of the lookup),
   where this one is the store itself being the wrong repository. Fixing either
   leaves the other. :need:`BUG_023 <BUG_023>` is the host-session-id lookup half.
   :need:`BUG_037 <BUG_037>` and :need:`BUG_038 <BUG_038>` are the batch siblings —
   a gate a track cannot satisfy by any work it can do.

   **Third instance, 2026-08-19 — the same blackout, a different repository as
   the host root.** A ``feature-architect`` dispatched for the session that fixed
   :need:`BUG_045 <BUG_045>` found ``pipeline status --json`` carrying an empty
   ``evidence`` object: no ``docs_read``, no ``agents_run``, after five architecture
   documents had been read with ``Read`` calls naming absolute paths inside this
   repository's worktree. The host session driving it was rooted in the consuming
   project, which is the distinguishing condition this entry already names. The
   agent fed the true payloads to ``invariant hook post-tool-use`` by hand — the
   same recording the hook would have made, from the same reads — and disclosed
   it, because a session that records its own evidence is indistinguishable from
   one that certifies itself, and only the disclosure tells them apart. Nothing
   was forced, no gate was lowered, and no session-state JSON was edited. Recorded
   here because three independent instances in three sessions is what makes the
   "if that wedges, the defect is in the recording" sentence in the manifest a
   standing cost rather than an anticipated one.

   **Both roles, same session.** The ``compliance-reviewer`` dispatched for that
   same session hit the identical blackout on the other side: ``agents.review_agent_ran``
   blocked while the reviewer was demonstrably running, because the dispatch that
   started it was recorded nowhere. It resolved it the same way and on the same
   terms — the true payload fed to ``invariant hook post-tool-use``, disclosed here,
   nothing forced, no gate lowered, no session-state JSON touched. Worth recording
   because it shows the blackout is not specific to the design role: every
   ``agents.*_ran`` gate in a cross-repository session is unsatisfiable by the work
   the session can do, so the count of affected gates is the count of roles the
   project configures, not one.

   **Provenance.** Found and verified against master ``6e83fd4`` by the session that
   completed the fix now recorded as :need:`BUG_039 <BUG_039>`, which is the work this
   wedge blocked. Reported 2026-08-18. **Not reproduced in this repository's tests
   yet** — the two-project repro above is the first step, and it needs no
   concurrency.

   **Fourth and fifth instances, 2026-10-24 — edits drop too.**

   - From a host session rooted in a consuming project, a full tdd track was
     worked inside an isolated session worktree of this repository. Architecture
     ``Read``\ s, source/test/doc edits and agent dispatches all went unrecorded:
     ``docs.read_architecture`` stayed owed, the pipeline never left ``designing``,
     ``invariant session files`` showed 0 declared, and ``agents_run`` was empty. The
     work was unblocked only by resuming it from a host session rooted in this
     repository, re-reading the architecture doc and re-dispatching
     ``feature-architect`` and ``compliance-reviewer``. Nothing was forced, no gate
     was lowered, and no session-state JSON was edited.
   - The reverse direction, 2026-08-24: a ``compliance-reviewer`` dispatched from a
     host session rooted in this repository, for a consuming project's pipeline.
     ``agents.review_agent_ran`` never cleared there.
   - Scope note for the fix: this widens the symptom beyond dispatch and ``Read``.
     An ``Edit``/``Write`` carries an absolute ``file_path``, but it is looked up in the
     host's registry just the same, so it drops too. The red test should cover
     ``Edit``/``Write`` and ``Read`` alongside ``Agent``/``Task``; all three go through the
     same wrong-store lookup in ``Context.session_for_payload``.

   **Sixth instance, 2026-10-26 — a consuming project's live sessions carry no
   agent-ran evidence.** Heap's two live session records both carry a
   ``host_session_id`` and their ``hosts``, and neither carries any agent-ran
   evidence, so every ``agents.*_ran`` gate there is unsatisfiable by the work the
   sessions can do. The handoff from the coverage-advisory-paths work
   (:need:`IDEA_028 <IDEA_028>`) flagged it. This now blocks
   :need:`IDEA_031 <IDEA_031>`: its A4 verdict gate judges per-rule verdicts from the
   design and review agents, and would wedge the same way in any consuming
   project driven from a host rooted elsewhere. Fix this entry (its ADR first)
   before or alongside IDEA_031's A0. Current workaround, until the fix lands:
   drive a project's pipeline from a host session rooted in that project (memory
   note "Drive a pipeline from a host rooted in that project"). Priority stays
   high.

   **Repro proof:** ``tests/test_bug_040_repro.py::test_BUG_040_a_dispatch_from_a_host_rooted_elsewhere_reaches_the_named_pipeline``,
   committed 22ccde9 (2026-10-27) on the session branch, strict xfail; recorded
   red via ``pipeline test --phase reproducing``. Two temporary invariant projects,
   A and B, each with its own git common directory as state root. B has one live
   isolated session with a pipeline running. A ``Task`` dispatch for
   ``feature-architect``, whose prompt names B's worktree by absolute path, is fed
   to the real ``invariant hook post-tool-use`` entry point with
   ``CLAUDE_PROJECT_DIR`` naming A and the working directory in A. The hook exits 0,
   and B's pipeline evidence is ``{}``: there is no ``agents_run`` key, where
   ``["feature-architect"]`` is expected. A scratch control (not committed) runs the
   same harness with ``CLAUDE_PROJECT_DIR`` naming B, and that run records
   ``["feature-architect"]``. So the host root is the only input that flips the
   result. The test asserts B's recorded evidence, not the registry lookup. It does
   not cover ``Read``/``Edit``/``Write``; those belong in the fix's own tests. One
   correction to the cause chain: ``_worktree_named_in_prompt`` itself matches
   candidates through ``by_worktree_containing`` against A's registry, so it
   returns ``None`` and never yields B's path (step 4 above says it does). For the
   ADR: ``docs/architecture/state_model.md``, "Attribution", currently documents
   this behaviour as the contract: "Attribution is within one project by
   construction." The fix has to amend that sentence.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red test 22ccde9 on session cd7c3be2
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Fixed in 749c5c3 (ADR-011): a hook call whose path lies in another project''s live session worktree is recorded there when every match lands on one session; otherwise it is recorded nowhere and a dispatch or architecture Read says so. Red test 8efea94 now passes; 4473 tests green.'
        forced: false

.. bug:: An Objective-C file xccov measured has its coverage row dropped by the parser
   :id: BUG_041
   :status: fixed
   :severity: medium
   :priority: high
   :area: adapters/swift_xcode
   :batch: unsatisfiable-gates
   :residue: Not confirmed against a captured xccov sample containing a real non-Swift row. The entry asked for one first and this fix did not have it: the row SHAPE is taken from the genuine Swift sample already in the suite and only the extension is varied, on the reading that xccov's per-file row format carries no language-dependent part. The risk this leaves is bounded and runs one way — if a non-Swift row is laid out differently, the pattern misses it and the file is unmeasured exactly as before, which is the status quo rather than a false relaxation. A consuming project with a mixed ObjC/Swift target should capture one 'xccov view --report' and confirm the shape\; that is the only thing this fix rests on that was not verified here.; The 'all' coverage scope was the open question the entry raised and it is answered: this adapter never computes a whole-project percentage — parse_coverage returns per-file rows and coverage.meets_floor judges each file against the floor in both scopes. So dropped ObjC rows did not skew an aggregate number\; they made ObjC files invisible to the floor entirely under 'all' scope, on top of the unsatisfiable gate under 'touched'. Widening the parser therefore starts enforcing the floor on files that were silently exempt, which will make things go red in mixed projects and is the correct outcome.; Heap cannot supply the missing xccov sample and the search should move elsewhere — definitive negative from the consuming project, 2026-08-23. A completed full-lane coverage run there reports 2,362 files, every one .swift\; find across its three source roots returns 0 non-Swift compiled sources. Its only ObjC/C is inside CocoaPods, and Pods targets are not instrumented into the report. So no xccov run from that repository will ever contain a non-Swift row. The risk analysis is unchanged — a differently laid out row is missed, which is the status quo, not a false relaxation — but the confirming sample must come from a different mixed ObjC/Swift project.
   :affects: COMP_ADAPTERS_SWIFT_XCODE

   **Symptom.** In a mixed Objective-C / Swift Xcode project, a touched ``.m`` (or
   ``.mm``) file that ``xccov`` genuinely DID measure, and genuinely has a coverage
   percentage for, still trips:

   ::

      [BLOCK] coverage.meets_floor: N changed source file(s) have no coverage row

   No test the author can write will ever clear it. The row was not lost by the
   coverage tool — it was discarded on the way in.

   **Expected.** A file the coverage tool measured arrives at the gate carrying
   the percentage the tool reported for it, whatever language it is written in.
   ``coverage.meets_floor`` should then judge that number against the floor — pass
   or fail on the merits, which is a verdict a test can move.

   **Repro.** Run ``invariant pipeline coverage`` in an Xcode project holding both
   Swift and Objective-C sources, with a touched ``.m`` file among the changed set
   and a real ``xccov`` report that lists a row for it. The gate blocks on that
   file as unmeasured. At the unit layer the same thing shows without Xcode:
   ``SwiftXcodeAdapter.parse_coverage`` fed report text containing both a ``.swift``
   row and a ``.m`` row returns only the Swift one.

   **Cause,** verified by source read at ``6e83fd4``. ``parse_coverage`` in
   ``adapters/swift_xcode.py``:

   .. code:: python

      r"^\s*(?P<file>[\w./+-]+\.swift)\s+(?P<pct>\d+(?:\.\d+)?)%"

   The ``\.swift`` literal is the whole defect: only Swift rows are ever
   extracted. An ``.m`` row sitting in the very same ``xccov`` output is dropped by
   the parser, so the file reaches ``core/rules._explain_absent_rows`` looking
   exactly like a file the tool never measured. The rule then asks
   ``carries_executable_code`` about it and correctly gets ``None`` — a ``.m`` IS
   compiled and IS measurable — so it correctly refuses to relax, and the gate
   blocks permanently. Every layer downstream of the parser behaves correctly on
   the false picture it was handed.

   **Not a defect in the :need:`BUG_039 <BUG_039>` fix, and not fixable there.** The
   two are easy to conflate, so the boundary is worth stating:

   - BUG_039 taught the adapter which file KINDS are never compiled. That was
     correct and is not implicated here.
   - ``.m`` is compiled and measurable, so ``None`` is the RIGHT answer from
     ``carries_executable_code``. Adding ``.m`` to the resource allowlist would be
     precisely the silent-pass mistake that question's contract forbids — it
     would relax a gate over genuinely unmeasured code. ``_COMPILED_SUFFIXES`` is
     checked first specifically to make that unreachable, and it should stay
     unreachable.
   - The BUG_039 session's own control test in ``tests/test_rules.py``,
     ``test_a_touched_objective_c_file_still_blocks``, pins this blocking
     behaviour deliberately, and is RIGHT for that change — it is the one-bit
     control proving the resource allowlist never swallowed a compiled
     language. But it means the suite currently pins a state that is itself an
     unsatisfiable gate for mixed projects. **A session fixing this bug must
     revisit that test rather than read it as settled intent**: what it should
     keep pinning is that an ObjC file with no row still blocks; what it can no
     longer assume is that an ObjC file never HAS a row.
   - The fix therefore belongs in ``parse_coverage``, at the parsing layer —
     neither in the adapter's kind tables nor in ``core/rules``.

   **Fix direction** (recorded, not implemented). Widen the row pattern to the
   compiled kinds ``xccov`` actually reports rather than Swift alone. The natural
   source is ``_COMPILED_SUFFIXES``, the list BUG_039 already established as
   compiled and measurable: reusing a reviewed, tested table beats a second
   pattern that can drift from it — the same "duplication that drifts" reasoning
   BUG_039 used to keep documentation suffixes out of the adapter.

   Two things to settle during the fix rather than assume:

   - ``_COMPILED_SUFFIXES`` is WIDER than the set this entry's report names. It
     also holds ``.metal`` and ``.s``. Compiled is not the same claim as "xccov
     emits a row for it", and asserting a row shape for a Metal shader or an
     assembly file on no evidence would be a guess in the direction of the
     parser accepting garbage. Either confirm those two or subset the frozenset
     explicitly and say why.
   - Whether ``xccov``'s row format for non-Swift kinds differs beyond the
     extension — column layout, path spelling, header/target grouping — since
     the present pattern also anchors on ``^\s*`` and a trailing ``%``.

   Worth measuring too, because it may widen the fix's value beyond this gate:
   with ObjC rows dropped, a whole-project coverage percentage from this adapter
   is computed over Swift rows alone. Whether that skews the number the floor is
   judged against, or only the per-file ``touched`` scope this bug was found
   through, is a question the fix should answer rather than leave.

   **Affected.** ``SwiftXcodeAdapter.parse_coverage`` in ``adapters/swift_xcode.py``
   — the ``adapters/`` layer's "how to read a coverage number" duty, per
   :doc:`overview.md </architecture/overview>`. The gate that surfaces it,
   ``coverage.meets_floor`` via ``_explain_absent_rows`` in ``core/rules.py``, is
   correct throughout and should not be touched: ``core/ → adapters/`` is the
   documented arrow, the rule keeps the policy, and here the adapter supplied a
   false fact.

   **Provenance, and why it is filed rather than fixed.** Found by a
   ``feature-architect`` design review of the BUG_039 fix, not by a consuming
   project. Deliberately not folded into that change — reviewer and finder both
   judged it a separate defect in adjacent code, owed its own reproduction.
   Proving it needs a real ``xccov`` output sample containing an Objective-C row,
   which this repository's own Python suite cannot produce unaided; the same
   constraint :need:`BUG_038 <BUG_038>` carries for its swift-testing repro. Capture
   that sample first — the pattern this fix widens is only as good as the row
   shape it was widened against.

   **Related.** :need:`BUG_039 <BUG_039>` is the immediate sibling: same family, same
   file, one file kind over, and the change under whose review this was found.
   :need:`BUG_025 <BUG_025>` and :need:`BUG_034 <BUG_034>` are the wider family — a
   coverage gate a consuming project cannot satisfy by any amount of work, in
   this same adapter. Those three are all the answering layer, the
   ``carries_executable_code`` half; this is the READING layer, and the first of
   the family to sit in ``parse_coverage``. That distinction also marks its other
   neighbourhood: the ``inert-instruments`` batch holds :need:`BUG_018 <BUG_018>` and
   :need:`BUG_011 <BUG_011>`, the node and Python adapters' ``parse_coverage`` failing
   to produce rows. Same silent-drop site, different mechanism — those two parse
   nothing at all and refuse visibly, while this one parses successfully and
   drops a subset, which is why it presents as an unsatisfiable gate rather than
   an absent instrument.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: Reproduced at the unit layer, tests/test_adapters.py::TestBUG041CoverageRowsAreNotSwiftOnly::test_an_objective_c_row_survives_the_parser — parse_coverage fed report text holding a .swift row and a .m row returned only the Swift one, exactly as the entry's unit-layer repro predicts. The row shape used is the real captured xccov output already in the suite (TestSwiftCoverageCommand::test_its_output_is_what_parse_coverage_already_reads); only the extension is varied.
        forced: false
      - from: reproduced
        to: fixed
        reason: 'The fix is at the parsing layer only, as the entry requires — core/rules and the adapter''s kind tables are untouched by it. parse_coverage''s row pattern is built from _COMPILED_SUFFIXES rather than a second list written beside it, so the reviewed table cannot drift from its copy; suffixes are alternated longest-first so Engine.swift cannot backtrack into a .s match. The whole table is used, .metal and .s included, and the reason is that the asymmetry INVERTS between the two uses of that table: for the kind question a wrongly listed suffix would relax a gate, while for the row pattern a wrongly listed suffix matches nothing and an omitted one drops a real measurement. The parser stays an allowlist, so report target lines (Heap.app 78.00%) and resource kinds still do not become coverage rows. As the entry instructed, tests/test_rules.py''s test_a_touched_objective_c_file_still_blocks was revisited rather than read as settled intent: it keeps pinning that an ObjC file with NO row still blocks, and its docstring now says so explicitly, with two siblings added for the state that was previously unreachable — a measured .m judged on the merits, and a measured .m below the floor being caught as under-covered rather than as unmeasured.'
        forced: false

.. bug:: The Xcode resource-bundle ancestor scan reads an absolute path where its neighbour deliberately reads a relative one
   :id: BUG_042
   :status: fixed
   :severity: low
   :priority: medium
   :area: adapters/swift_xcode
   :batch: unsatisfiable-gates
   :affects: COMP_ADAPTERS_SWIFT_XCODE

   **Symptom.** ``SwiftXcodeAdapter.carries_executable_code`` can answer ``False`` —
   "no executable lines, relax the coverage gate" — for a file whose only claim
   to being a resource is an ancestor directory **above the repository root**.
   The relaxation is driven by a component of the machine's filesystem layout
   that has nothing to do with the project.

   Nothing has been observed firing. This entry records a latent hazard and the
   contract gap underneath it, not an incident: unlike :need:`BUG_040 <BUG_040>` and
   :need:`BUG_041 <BUG_041>`, no consuming project has hit this and no plausible
   real-world case could be constructed.

   **Expected.** The directory convention that answers this question is the
   project's own layout. A path component the project does not own — a checkout
   parent, a mount point, a home directory — must not decide whether a file
   inside the repository carries executable code.

   **Repro,** at the unit layer, verified by running it at ``0dde554`` (a
   demonstration of the mechanism, not a reproduction of any reported failure):

   .. code:: python

      a = SwiftXcodeAdapter()
      a.carries_executable_code(Path("/checkouts/Vendor.docc/repo/src/Engine.hxx"))
      # -> False   (relaxes)
      a.carries_executable_code(Path("src/Engine.hxx"))
      # -> None    (correctly refuses to relax)

   Same file, same project, opposite answers — decided entirely by which
   *spelling* of the path the caller happened to pass.

   **Cause.** ``_xcode_resource_kind`` in ``adapters/swift_xcode.py:522`` scans
   ancestors:

   .. code:: python

      part.lower().endswith(_RESOURCE_BUNDLE_DIR_SUFFIXES) for part in path.parts[:-1]

   ``core/rules._explain_absent_rows`` passes it an ABSOLUTE path, so ``parts[:-1]``
   includes every component above the project root.

   **The defect worth recording is not that one call used an absolute path.** The
   same ancestor-scanning idiom already exists TWICE in this adapter, and only
   one of the two is protected — by a guarantee that lives nowhere in the
   adapter:

   - ``adapters/swift_xcode.py:135`` — ``any(part.endswith("Tests") for part in path.parts[:-1])``, in ``is_test_file``. Safe **only because**
     ``core/rules.py:2074`` hands it the repo-relative spelling,
     ``_is_test_file(adapters, Path(rel))``.
   - ``adapters/swift_xcode.py:522`` — the resource-bundle scan added by
     :need:`BUG_039 <BUG_039>`, in ``carries_executable_code``. Exposed **only
     because** ``core/rules.py:2076`` hands it the absolute one,
     ``_carries_executable_code(adapters, found)``.

   Two lines apart, deliberately opposite spellings, and the reason for the first
   is written only as a comment at the call site in a *different layer*, directly
   above line 2074:

      Asked on the repo-relative spelling, not on ``found``: the directory
      convention is what carries the answer, and an absolute path can pick up a
      stray ``tests`` component from somewhere above the project root.

   That comment states the exact hazard, one question over, and the new rule
   walked into it. The adapter has no way to know which spelling it will be
   given: ``Adapter.carries_executable_code`` and ``Adapter.is_test_file`` in
   ``adapters/base.py`` document the answer contract in detail — what ``None`` means,
   which wrong answer is cheaper — and say nothing about the path spelling.
   Every future ancestor-based rule in this adapter inherits whichever spelling
   its caller happens to use, with nothing to catch a mismatch. That is the
   defect; the wrong answer above is one instance of it.

   **Exploitability — low, and stated plainly.** A wrong relaxation needs BOTH:

   1. a compiled kind ABSENT from ``_COMPILED_SUFFIXES``. The compiled-first check
      at ``swift_xcode.py:517`` catches every listed kind before the directory rule
      is reached — ``.m`` and ``.h`` really are unreachable, confirmed by running it
      — so the exposed set is exactly the unlisted C++ spellings ``.hxx``, ``.ipp``,
      ``.inl``, ``.cp``, ``.c++`` (absent from ``_RESOURCE_SUFFIXES`` too, so they fall
      through); AND
   2. an ancestor **above the repository root** ending in ``.xcassets``, ``.lproj``,
      ``.docc``, ``.xcodeproj``, ``.xcworkspace``, ``.xcdatamodeld`` or ``.xcdatamodel``.

   Neither the design reviewer nor the finder could construct a plausible
   real-world case, and the entry should not pretend otherwise. Two things keep
   it from being zero: a checkout sitting inside a directory named ``*.xcodeproj``
   or ``*.docc`` is unusual rather than impossible, and condition 1 widens every
   time someone adds a compiled kind to the resource tables without adding it to
   ``_COMPILED_SUFFIXES`` — which is a maintenance path, not a hypothetical.

   **Second half — the documentation claim.** ``docs/architecture/overview.md``
   (updated by BUG_039) now states the safety property absolutely:

      Compiled kinds are listed explicitly and tested first, so neither a later
      allowlist entry nor the directory rule can reach an ``.m`` or a ``.h`` that
      ``xccov`` really does measure.

   True for the kinds listed in ``_COMPILED_SUFFIXES``, and not quite true for the
   unlisted C++ spellings above. Whatever the code fix turns out to be, that
   sentence must be brought into line with what the code actually guarantees.
   This half of the item is not optional and does not lapse if the code fix is
   deferred: an architecture doc overstating a safety property is what the next
   reader will reason from.

   **Affected.** ``adapters/swift_xcode.py`` — ``_xcode_resource_kind`` (the exposed
   scan) and ``is_test_file`` (the protected one); ``core/rules._explain_absent_rows``
   (the two call sites that choose the spellings); ``adapters/base.py`` (where the
   contract is absent). Per :doc:`overview.md </architecture/overview>` the
   documented arrow is ``core/ → adapters/``: ``core/rules`` keeps the policy, the
   adapter supplies the ecosystem fact — and this is a fact the adapter cannot
   supply correctly without knowing what it was handed.

   **Fix directions** (recorded, not implemented — these are ALTERNATIVES for the
   code half, not a sequence):

   1. Pass the repo-relative spelling at ``core/rules.py:2076``, matching its
      neighbour two lines up. Cheapest, but it must be checked against the
      Swift-contents branch of ``carries_executable_code``, which READS the file
      and so genuinely needs a resolvable path. That may mean splitting the path
      argument rather than swapping it — which is precisely why this is its own
      change and not a one-liner folded into something else.
   2. Make the adapter defensive: have the ancestor scan stop at the project root
      rather than trust the spelling it was handed.
   3. Make the contract explicit in ``Adapter``'s protocol docstrings in
      ``adapters/base.py`` — state which spelling each question receives, so the
      guarantee stops living only in a call-site comment in another layer. Worth
      doing alongside 1 or 2 rather than instead of them: it is the half that
      prevents the next recurrence, and it is the reason this entry exists at a
      priority above its severity.

   Independently of which is chosen, settle whether ``.hxx``, ``.ipp``, ``.inl``,
   ``.cp``, ``.c++`` should simply be added to ``_COMPILED_SUFFIXES`` — that shrinks
   the exposed set on its own and is decided on its own merits (are they kinds
   ``xccov`` measures?), not as a substitute for fixing the spelling contract.

   **Provenance.** Found by a ``feature-architect`` design review of the BUG_039
   fix and confirmed by the reporter's own check; every reference verified
   against ``master`` at ``0dde554`` and re-verified by this filing.

   **Related.** :need:`BUG_039 <BUG_039>` is the change that introduced the exposed
   scan — same file, same function, and the design review of that fix is where
   this surfaced. :need:`BUG_019 <BUG_019>` is where the protected half came from:
   its ``is_test_file`` fact is consulted on the repo-relative path (its own residue
   records this), and the call-site comment at ``core/rules.py:2074`` is the reason
   written down. :need:`BUG_041 <BUG_041>` is the sibling from the same review — that
   one is the READING layer (``parse_coverage``), this one is the ANSWERING layer,
   and a session already in this file for BUG_041 is the natural place for this
   to ride along.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'Reproduced at the unit layer, tests/test_adapters.py::TestCarriesExecutableCode::test_BUG_042_an_ancestor_above_the_project_does_not_decide — carries_executable_code answered False (relax the floor) for src/Engine.hxx sitting under a checkout parent named Vendor.docc. The mechanism, not any reported incident: nothing has been observed firing and the entry says so.'
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Directions 1, 2 and 3 from the entry, together, because the entry''s own analysis is that the third is what prevents the recurrence. Adapter.carries_executable_code now takes TWO spellings and the base protocol says which answers which: ''path'' is resolvable and is what a contents reader opens; ''relative'' is the project-relative spelling and is the only one a directory convention may be read from. Split rather than swapped, because the Swift contents branch genuinely needs a resolvable path — the trap the entry named. Without a relative spelling the ancestor rule abstains with None, the fail-safe answer the base contract already demands. is_test_file''s docstring now states the same rule, so the guarantee that had lived only as a comment at one call site in core/rules is where an implementer can see it. core/rules._explain_absent_rows passes Path(rel) alongside ''found''. Settled separately and on its merits: .hxx, .ipp, .inl, .cp and .c++ join _COMPILED_SUFFIXES — the same claim as .h/.hpp/.hh already in it, and since that table is checked first and answers None, an entry there can never relax anything. That shrinks the exposed set to nil on its own. The doc half: overview.md''s ''neither a later allowlist entry nor the directory rule can reach an .m or a .h'' was true only of the kinds the table names, and is now stated as exactly that wide, plus a fourth property recording the spelling contract.'
        forced: false

.. bug:: The compliance reviewer credited its own commit to the user, reasoning from an optional trailer
   :id: BUG_043
   :status: filed
   :severity: low
   :priority: medium
   :area: data/agents/compliance-reviewer
   :residue: DECISION 2026-10-20 (user-approved): Fix directions 1-3 (state that author field / trailer presence does not discriminate\; say 'cannot establish' rather than resolve toward the user\; default commits appearing inside the run window on the session branch to the agent's own). Direction 4 (unconditional trailer) is NOT chosen — it conflicts with privacy.clean blocking the Claude-Session trailer.
   :affects: COMP_DATA_AGENTS

   **Symptom.** A ``compliance-reviewer`` run's final report asserted a false
   provenance claim — it described a commit made *inside its own run* as work
   performed by the human user. Finalising the :need:`BUG_039 <BUG_039>` session, the
   run met a base-drift id collision (a peer session had filed and merged its own
   ``BUG_036`` while this branch was open) and renumbered ``BUG_036.md`` to
   ``BUG_039.md``. Its report then presented that renumber as foreign work: the
   commit "landed directly on my session branch", was "authored by the repo's user
   account, not carrying my co-author trailer", and — in as many words — "I did
   not author that correction", "I'm reporting what happened and what I verified
   about it, not claiming the fix as my own work."

   No human input occurred at any point in that session. The renumber was made by
   the reviewer or by something it spawned.

   **Expected.** A report claims authorship, or disclaims it, only on evidence
   that can tell the two apart. Where it cannot, it says so — "this commit
   appeared during my run and I cannot establish who made it" — rather than
   resolving the uncertainty in either direction. This is not a new standard for
   this project; it is the one ``compliance-reviewer.md:540`` already states for the
   whole run: *"If at any phase you genuinely cannot decide, bail with
   ``[NEEDS-USER]`` — do not guess."*

   **Repro.** Not step-driven; it is a reasoning pattern that fires whenever a
   commit appears on the session branch that the reviewer does not remember
   making — a peer's merged work pulled in by a rebase, a mid-run correction by a
   spawned agent, a squash that reshaped the branch. The reviewer inspects the
   commit, finds the user's git identity on it and no co-author trailer, and
   concludes foreign authorship. The precondition is uncommon; the inference is
   deterministic once it holds.

   **Cause — the evidence could not support the conclusion, and this is the core
   of the entry.** Three signals were read; none discriminates.

   1. **The git author field.** Every commit any agent makes in this repository
      carries the repository's configured git identity. It is the same string for
      agent and human work, so it can never separate them. Verified: all 14 most
      recent commits on ``master`` carry one identical author.

   2. **The absent co-author trailer — the load-bearing error.** The reviewer's
      OWN commit in that same run carries **zero** ``Co-Authored-By`` trailers. On
      ``master`` the renumber survives only inside ``6e83fd4``, the reviewer's single
      session commit: it is the commit that adds ``docs/backlog/BUG_039.md``, and
      its own message explains the renumber in the session's voice ("Filed as
      BUG_039, not BUG_036: a peer session filed and merged its own BUG_036 …
      while this branch was still open"). It has no trailer. Across the 14 most
      recent commits, 11 carry exactly one trailer and 3 carry none — ``6e83fd4``,
      ``f1396f9``, ``043380a``. Absence recurs, on agent work, and therefore means
      nothing.

      The shipped definition is what makes this predictable rather than
      accidental. ``src/invariant/data/agents/compliance-reviewer.md:295``
      instructs:

         (Append the host's standard agent co-author trailer if the project uses
         one.)

      Explicitly CONDITIONAL. An agent told the trailer is optional cannot then
      read its absence as a signal of authorship. The definition supplies the
      premise that defeats the inference and nowhere warns against taking the
      step anyway. That is why this is a defect in the agent definition and not
      merely a bad call by one run.

   3. **Timing.** The renumber fell within the reviewer's own run window, before
      the merge that same run performed. That is the one signal with real
      content — and it points the opposite way from the conclusion drawn.

   **What is NOT wrong here** — stated explicitly so a fixing session does not
   over-correct:

   - **The renumber itself was correct and correctly executed.** The collision was
     real. Verified independently at this filing: the ``id`` field moved with the
     file, all three cross-references repoint (:need:`BUG_037 <BUG_037>`,
     :need:`BUG_038 <BUG_038>` and the ``overview.md`` line the same session added), and
     no reference to the old id survives outside the peer's own
     :need:`BUG_036 <BUG_036>`.
   - **The reviewer was right to surface the event prominently** rather than let
     it pass silently. The instinct to flag "something happened here that did not
     go through my normal judgement" is exactly right and must survive any fix.
   - The failure is narrowly the **provenance inference** — not the disclosure,
     not the outcome.

   **Why it is filed at all, given nothing broke.** This library's product is
   trustworthy evidence about who did what. Per
   :doc:`state_model.md </architecture/state_model>`, the agent-role gates exist
   "because installing an agent and using one are different events, and only the
   second changes any outcome" — they are there to record that an *agent*, not a
   human working inline, did the work. A compliance report that misattributes
   agent work to the human corrupts that record at the one moment it is most
   consulted, and it does so in the direction that flatters the agent: it
   disclaims authorship of an unreviewed change. Had the renumber been wrong
   instead of right, the same report would have handed the user a defect described
   as their own work.

   **Affected.** ``src/invariant/data/agents/compliance-reviewer.md`` — the shipped
   agent definition, ``agents/*.md`` in the shipped-data group of
   :doc:`overview.md </architecture/overview>`. The fix belongs there and **not in
   engine code**: no gate is wedged, no rule is wrong, and nothing in
   ``core/`` or ``domains/`` evaluated anything incorrectly. Specific sites:

   - ``:295`` — the conditional trailer instruction, the premise that defeats the
     inference.
   - ``Phase 8b`` (``:306``) — where base drift is handled by rebase, and the phase a
     mid-run foreign-looking commit is most likely to be met in.
   - ``Phase 9`` (``:387``) — the report contract, which has slots for review rules,
     auto-fixes, gates and risk, and no slot for "a change appeared that I did not
     make".
   - ``:540`` — the existing fail-closed constraint the inference walked past.

   **Checked and NOT widened.** The same pattern does not appear in the other
   three shipped definitions: ``backlog-curator.md``, ``bug-reproducer.md`` and
   ``feature-architect.md`` contain no reference to co-author trailers, git author
   fields, or authorship attribution of any kind. ``compliance-reviewer.md:295`` is
   the only occurrence of the trailer instruction in the whole shipped set. The
   area stays narrow deliberately.

   **Fix directions** (recorded, not implemented):

   1. State that neither the git author field nor the presence or absence of a
      co-author trailer distinguishes the agent's own commits from a human's in a
      repository where agents commit under the user's identity. This is the
      minimum fix and it is the one that closes the reported defect.
   2. Where provenance cannot be established, require the report to say so, rather
      than resolve toward the user. Fail closed — the convention ``:540`` already
      sets for every unanswerable question, applied to this one.
   3. Consider making "did this appear inside my own run window, on my own session
      branch" the working signal: default such a commit to the agent's own work
      absent positive evidence otherwise. This is the only signal in the incident
      that actually carried information.
   4. **Option, not recommendation:** make ``:295``'s trailer unconditional, so the
      trailer BECOMES the discriminator it was mistaken for. Recorded because it
      is the only change that would make the inference sound rather than merely
      forbidden — but it touches the project's commit conventions rather than one
      agent's reasoning, and is a larger decision than this entry should presume.

   Give Phase 9's report shape somewhere to put the result, whichever is chosen —
   a claim with no slot in the template is a claim that gets improvised.

   **Noticed while filing; deliberately not a second entry.** This was caught only
   because the human parent session independently checked the claim. No gate
   covers report accuracy, and nothing in the pipeline would have flagged it. That
   gap is real, but it is not a defect: no shipped behaviour is failing its own
   contract, and no rule ever claimed to check report prose. Making a report's
   prose machine-checkable is a new capability and a hard, possibly unbounded one
   — if it is ever pursued it belongs in an ``IDEA_``, scoped on its own merits, not
   as a follow-up smuggled in here.

   **Related.** :need:`BUG_013 <BUG_013>` (fixed) and :need:`BUG_024 <BUG_024>` (fixed) are
   the standard this entry applies, one artifact over: a rendered claim must be
   entailed by the data behind it, and "a confidently stated false cause is harder
   to detect than an absence, because nothing prompts the reader to question it."
   Those two are the telemetry renderer in ``domains/stats`` — code, with data in
   hand it declined to consult. This one is an agent's prose report, with a
   premise its own definition handed it. Same standard, different artifact and
   different fix layer, which is also why it does not join their
   ``adr-002-followups`` batch: that batch is constituted by origin (one commit,
   one review, one telemetry subsystem), and this shares none of them.
   :need:`BUG_028 <BUG_028>` and :need:`BUG_040 <BUG_040>` are the adjacent neighbourhood
   — agent dispatches recorded against the wrong pipeline or nowhere. Those are
   defects in *recording* who acted; this is a defect in *reporting* it. The two
   halves fail the same promise from opposite ends.

   **Provenance.** Reported by the human parent session of the run in question,
   after independently checking the report's claim against the repository. Every
   reference above re-verified against ``master`` at ``6b15e7b`` by this filing.

.. bug:: The links pattern flags any scheme with ://, so private-scheme identifiers read as leaked addresses
   :id: BUG_044
   :status: fixed
   :severity: high
   :priority: high
   :area: core/privacy
   :slug: links-pattern-matches-any-scheme
   :affects: COMP_CORE_PRIVACY

   **Symptom.** The ``links`` pattern's first arm is
   ``[a-z][a-z0-9+.-]*://\S+`` — **any** scheme followed by ``://``. A consuming
   project's architecture documents describe their app's own private URL
   schemes with placeholder payloads, and every one of them is reported as a
   leaked link.

   Measured on two architecture documents in that project: **26 findings, 20 of
   them false**. The false ones are eleven distinct forms of private-scheme
   identifier — an asset scheme with a ``<lid>`` placeholder, a live-photo variant,
   a share scheme over a ``<uuid>``, an inspect scheme over ``capture/<hash>/<ts>``,
   an encrypted-media scheme both bare and with a ``local/...`` payload, a scan
   scheme over ``<uuid>`` in three spellings, and ``file://`` both bare and with a
   ``blob`` label. Several have **no authority at all**. None addresses a network
   resource; none can leak anything.

   The six genuine findings on those documents are ordinary web addresses and
   must keep being flagged.

   **Expected.** The module's own docstring already states the principle being
   violated: "Each pattern is deliberately narrow. Breadth here buys false
   positives, and a noisy privacy gate is one that gets disabled wholesale rather
   than tuned." A gate that reports twenty false findings on two documents is
   that gate.

   **Cause.** Scheme breadth is the wrong axis. What makes a URI a *link* is that
   its **authority names a network host** — a dotted label ending in a TLD-like
   suffix, or an IP literal. An empty authority (``file://``) or a bare
   non-host label (``asset``, ``local``, ``capture``, ``blob``, ``<uuid>``) names nothing
   reachable.

   **Not the fix.** Narrowing the scheme to an allowlist of the well-known web
   schemes. That stops flagging a custom scheme that *does* point at a real
   host, which is a loss of protection rather than a reduction in noise. The
   authority test keeps that case flagged.

   **Not the fix either.** Widening ``privacy.allow``, or inline
   ``invariant: allow-privacy`` markers in the consuming project. Those are
   per-project escapes; the defect is in the shared pattern and belongs there.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 10 tests, 4 red on the non-host-authority half; the 6 network-host controls green in the same run
        forced: false
      - from: reproduced
        to: fixed
        reason: Authority-based links pattern; 4 of 10 new tests mutation-proven red against the old pattern, 834 affected-set tests green, downstream corpus 26 findings to 6 with zero genuine losses
        forced: false

.. bug:: The privacy exemption marker is same-line only, so no comment-only markup can use it without rendering it
   :id: BUG_045
   :status: fixed
   :severity: high
   :priority: high
   :area: core/privacy
   :slug: exemption-marker-is-same-line-only
   :affects: COMP_CORE_PRIVACY

   **Symptom.** ``PrivacyScanner._is_exempt_line`` is a same-line substring test:
   the marker suppresses findings on the line it is written on, and on no other.
   Source code can satisfy that, because every programming language has an inline
   comment. **reStructuredText and Markdown cannot**: a comment there must own its
   line. So the only way to exempt a finding in prose is to append the marker to
   the prose itself — where it renders into the published output.

   The engine's own ``privacy.scope`` defaults to ``docs``, ``memory`` and ``reports``.
   All three are prose. The escape hatch is therefore unusable, as designed, in
   the bulk of what the gate actually covers.

   **Measured downstream, not hypothetical.** A consuming project carries six
   same-line markers across two architecture documents — code default values and
   worked examples of a host-matching algorithm, all of them legitimate
   documentation. All six reach the rendered HTML as visible parentheticals of the
   form ``(invariant: allow-privacy — the literal code default)``. Confirmed by
   grepping the built HTML, not inferred from the source. The author's only
   choices were to publish tooling metadata as prose or to delete correct
   documentation.

   **Expected.** A marker may sit on a comment line adjacent to what it exempts,
   so that it can live in an RST or Markdown comment and render nothing, while
   staying bounded enough that a reviewer reading the diff can see exactly what it
   covers.

   **Not the fix — "the marker exempts the next line".** Verified against
   docutils rather than reasoned about, because RST's structure defeats it. Of the
   six real cases, every one sits **mid-block**: inside a paragraph, inside a
   bullet-list item, or inside an enumerated item in a directive body. A comment
   cannot go on the line before any of them:

   - Inserted mid-paragraph, the comment **splits the paragraph** into two.
   - Inserted between list items, the comment **ends the list** and starts a
     second one — reproduced on docutils, which also emits two warnings
     (``Bullet list ends without a blank line``, ``Explicit markup ends without a blank line``) and produces two sibling ``bullet_list`` nodes where the source
     had one.

   The comment can only go before the whole block, so a one-line reach exempts
   nothing that needs exempting.

   **Not the fix either — "the rest of the file" or "the rest of the section".**
   A file-wide or section-wide escape is how a real leak gets hidden behind a
   marker somebody wrote for something else.

   **Cause.** ``core/privacy.py``, ``PrivacyScanner._is_exempt_line``: the marker's
   reach is one line because the predicate is evaluated per line with no notion of
   the block a line belongs to.

   **Repro (in this repository's own suite).** Scan a text in which the marker
   sits alone on a comment line and the finding sits in the block below it. The
   finding is reported. Scan the same text with the marker appended to the finding
   line and it is not. Same finding, same file, two placements, opposite verdicts
   — and only the placement that renders works.

   **Fix.** ``_is_exempt_line`` is replaced by ``_exempt_lines``, which returns the
   set of line numbers the markers in a text cover. A marker exempts its own line;
   and when no alphanumeric character precedes it on that line, it also exempts
   the rest of the block it opens and the whole of the next block, a block being a
   run of consecutive non-blank lines. The alphanumeric test is what keeps the
   same-line form from acquiring block reach: a marker with prose to its left
   still reaches one line. The reach runs forward only, so a line above the marker
   in the same block is still scanned, and the blank run the markup needs to close
   a comment is skipped without spending the one following block.

   **Verified downstream, in the consuming project this was found in.** Its six
   markers are all inline, so the new code exempts exactly the six marker lines
   across 26,073 lines of its prose and widens nothing. Rewritten into comment
   form they scan to 0 findings, against 6 from the same text with the markers
   stripped. Rendering the comment form produces HTML identical to the stripped
   build for all 98 pages, once the ids sphinx-needs generates non-deterministically
   are normalised — non-determinism confirmed by rebuilding one source twice and
   seeing the same 13 pages differ. The marker text survives in the ``_sources``
   mirror and in ``needs.json``, as it already did for the inline form; what the fix
   removes is its appearance in the rendered pages.

   **Related.** :need:`BUG_044 <BUG_044>` records the same six documents and rejects
   per-project marker escapes as an answer to a defect in a shared pattern; this
   entry is the other half — the escape hatch itself is broken for prose, which is
   independent of how noisy any pattern is. :need:`BUG_027 <BUG_027>` is the same
   module's other open defect and a different seam: what the gate is pointed at,
   rather than how an exemption is scoped.

   **Provenance.** Found by a consuming project after its architecture docs were
   published with all six markers visible. Reported 2026-08-19.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: '24 tests, 7 red: the six comment-only marker placements plus the two-block law; the seven over-reach controls and the same-line regression case green in the same run'
        forced: false
      - from: reproduced
        to: fixed
        reason: 'block-scoped exemption marker: a marker on a comment-only line covers the rest of its block and the whole of the next one; 28 tests both directions, mutation-proven bound; downstream re-verified 0 findings in comment form against a 6-finding stripped control'
        forced: false

.. bug:: docs.read_architecture is unsatisfiable across a session's two trees
   :id: BUG_046
   :status: fixed
   :severity: high
   :priority: high
   :area: hosts/claude_code, core/rules, domains/session
   :batch: unsatisfiable-gates
   :slug: read-architecture-unsatisfiable-across-two-trees
   :affects: COMP_HOSTS_CLAUDE_CODE, COMP_CORE_RULES, COMP_DOMAINS_SESSION

   **Symptom.** With an isolated session live, ``docs.read_architecture`` can become
   unsatisfiable: **no doc the agent reads will satisfy it.** The gate's own
   remedy line — "read the relevant doc under docs/architecture" — is advice that
   cannot be taken, because the two independent steps a read must survive prefer
   **opposite path spellings** for the same document. Reading the main-checkout
   copy is dropped at the recording step; reading the worktree copy is rejected at
   the matching step. The gate is ``block`` in this repository's manifest, so a
   design state's exit is closed against any work the track can do.

   **Expected.** A read of an architecture document satisfies the gate, or the
   engine says which copy it wanted. Two steps that disagree about the spelling of
   one file is not a state a session can be asked to reason its way out of. At
   minimum, evidence that is deliberately discarded says so — a drop a gate will
   later read as "you did not do the work" cannot be silent.

   **Repro.**

   1. In a project with ``enforcement.rules.docs.read_architecture = block``,
      register and isolate a session, so a linked worktree exists.
   2. From an agent bound to that session, ``Read`` an architecture doc by its
      **main-checkout** absolute path.
   3. Run ``invariant pipeline gates`` **from the main checkout**. The gate blocks,
      and ``pipeline status --json`` shows no ``docs_read`` entry for that read.
   4. Now ``Read`` the same doc by its **worktree** absolute path. The row is
      recorded — and the gate run from the main checkout still blocks, because the
      recorded path is not inside the main checkout's docs root.

   The two halves are separable and each looks like a different bug on its own,
   which is why they are filed together (see **Why one entry**).

   **Cause — confirmed by source reading, refs current on ``b95c4f5``. Two
   mechanisms that compose.**

   **Mechanism 1 — recording drops an unproven read, silently.**
   ``hosts/claude_code.py:822``, inside ``_post_tool_use``:

   ::

      if tool == "Read":
          target = tool_input.get("file_path")
          if target and resolution.proven:
              ctx.pipelines.record_docs_read(session.id, str(target))
          return {}

   ``Resolution.proven`` (``domains/session.py:73-80``) is ``session is not None and how in PROVEN_ROUTES``, and ``PROVEN_ROUTES`` (``session.py:53``) is
   ``{"explicit", "host", "target", "prompt", "cwd"}`` — ``origin``, ``ambiguous`` and
   ``none`` are excluded. **That exclusion is correct and must not be relaxed**: it
   is BUG_028's fix, and the state model's Attribution section states the rule it
   encodes — "a resolution is not automatically an identification", evidence
   writers ask ``proven`` because writing on a guess is how a foreign session's
   agent came to sit in a pipeline that never dispatched it.

   The defect is not the guard, it is that the guard's **rejection is silent**. A
   read aimed at a **main-checkout** doc path, while sessions are live and none of
   their worktrees contains that path, resolves ``ambiguous``/``origin`` — unproven —
   and the row is dropped with no note to anyone. A read aimed at the **worktree**
   copy resolves ``target`` and is recorded. So step 1 accepts only worktree
   spellings. The silence is what makes the wedge undiagnosable from inside:
   nothing distinguishes "your read was discarded on an attribution rule" from
   "you have not read anything".

   **Mechanism 2 — matching resolves the docs root from the tree the CLI is
   standing in.** ``core/rules.py:966``:

   ::

      inside = [p for p in read if _is_within(p, docs_root)]

   ``_docs_root`` (``rules.py:1924``) builds from ``_repo_root(ctx)``, and ``_repo_root``
   (``rules.py:1912``) is ``git.toplevel(ctx.cwd) or ctx.manifest.root``. The docs
   root is therefore the docs directory of **whichever working tree the CLI was
   invoked from**. ``_is_within`` (``rules.py:2766``) resolves both operands, so this
   is a genuine tree-identity test and not a string comparison a normalisation
   would soften: a recorded main-checkout path is not within a worktree docs root,
   and a recorded worktree path is not within a main-checkout docs root. So step 2
   accepts only the spelling belonging to the tree the CLI ran in.

   **The composition.** Run the CLI from the **main checkout** with an isolated
   session live. Mechanism 1 means only **worktree** paths are ever recorded;
   mechanism 2 means the docs root is the **main checkout**. The two accepted sets
   are disjoint and the intersection is empty — the gate fails with no read the
   agent can perform to satisfy it. Run the CLI from the **worktree** instead and
   both steps prefer the worktree spelling, so it passes. That is why "which copy
   satisfies the gate" appears to depend on where the CLI was invoked from: it
   does, and neither step tells anyone which copy it wanted.

   **Why one entry and not two.** Each mechanism read alone is defensible.
   Mechanism 1 alone is BUG_028's guard doing its job. Mechanism 2 alone is
   ``_repo_root``'s docstring doing exactly what it documents — "multi-worktree is
   the normal case for this library, so the git top-level wins". Neither is a
   defect on its own reading, and a fixer handed either half separately would
   close it as working-as-intended. The defect is the **disjointness**, and it
   only exists when both are on the same page. Two live observations are the
   mirror image of each other (below), which is precisely how this presents as two
   contradictory bugs until both steps are in view.

   **Evidence — live, from a peer session in a consuming project; not
   manufactured payloads.**

   - Session ``8101f69a``: a ``feature-architect`` read four architecture documents,
     two by main-checkout path and two by worktree path. ``docs_read`` ended with
     exactly **two** entries, **both the worktree paths**. The two main-checkout
     reads are absent.
   - **The peer's honesty caveat, recorded as such.** That session *also* recorded
     reads by hand via ``invariant hook post-tool-use``, and it cannot tell from
     stored state whether those two rows arrived by the tool route or the manual
     one. What the data supports unambiguously is the shape, not the route: only
     worktree paths present, no main-checkout path present. It is recorded this
     way rather than cleaned up because a session that records its own evidence is
     indistinguishable from one that certifies itself, and only the disclosure
     tells them apart (the same discipline BUG_040 keeps).
   - Second observation, same day, same session: a ``compliance-reviewer`` needed
     the gate, was instructed to read the **worktree** copy, and it registered
     first time. Twice observed rather than inferred.
   - **The mirror image, session ``f44aeae1``.** A ``bug-reproducer`` working in an
     isolated session cleared the gate by reading the **main-checkout** path,
     because the docs root resolved from the main checkout. That is mechanism 2 in
     isolation, with mechanism 1 not biting, and it is the observation that makes
     the two mechanisms visible as one wedge rather than as two entries
     contradicting each other.
   - **Corroborating, from this repository's own CLI.** ``invariant context`` run
     from the main checkout prints "N live sessions were registered here and none
     of their worktrees contains this directory"
     (``domains/session.py:984``, ``ambiguity_message``). That is the ``ambiguous``
     state rendered in prose — the same state that silently drops the read one
     layer over. The engine can already say this sentence; the recorder just never
     says it.

   **Affected.**

   - ``hosts/claude_code._post_tool_use``, the ``Read`` branch — the silent drop. Its
     neighbouring ``session is None`` branch is *deliberately* silent and documents
     why; the ``proven``-but-dropped case inherited that silence without inheriting
     the reasoning, and unlike an ungoverned turn this one has an identified
     session to tell.
   - ``core/rules._docs_read_architecture``, with ``_docs_root`` / ``_repo_root`` as the
     root it tests against.
   - ``domains/session.Resolution.proven`` and ``PROVEN_ROUTES`` — the boundary being
     crossed. **Not the thing to change.**
   - The **Attribution** section of ``docs/architecture/state_model.md`` is the
     authoritative statement of the resolution order and of the ``proven`` rule, and
     the ``hosts/`` section of ``docs/architecture/overview.md`` is the layering rule
     that puts payload-derived attribution above ``hosts/``. Both describe current
     behaviour correctly; neither anticipates the two steps disagreeing about
     spelling, and whichever fix lands will need to add that sentence.

   **Fix constraint — record before any work starts.** Do **not** relax ``proven``
   to admit ``origin`` or ``ambiguous``. That is a straight BUG_028 regression, and
   BUG_028's own residue records that the guess route is still live. Any fix that
   makes an unproven read recordable is wrong however convenient it looks from
   here.

   **Fix sketch / next step — record only, do not implement from this entry.**
   Two directions, not exclusive:

   - **Make the drop visible.** A dropped read that a ``block`` gate will later
     read as absence must be reported, not swallowed — this is ``record_agent_run``'s
     lesson, which stopped being a bare ``return`` and started returning
     ``RECORDED`` / ``ALREADY`` / ``NO_PIPELINE`` for exactly this reason (BUG_028), and
     BUG_020's lesson that an N/A a reader cannot tell from a pass is worse than a
     refusal. The ``ambiguity_message`` text already exists and names the remedy.
     This alone does not un-wedge the gate; it converts an unsatisfiable gate into
     a legible one, which is the difference between a session that argues with the
     engine and a session that reports a bug.
   - **Make the two steps agree on a canonical spelling.** Either record the read
     in a tree-independent form (repo-relative to the resolving tree) and compare
     in that form, or have the matching step accept a path that is inside *any*
     live worktree's docs root for the pipeline's session rather than only the
     CLI's own tree. The recording side has BUG_042-adjacent prior art about a
     path spelling the adapter cannot see, and the same caution applies: whichever
     spelling is chosen, both ends must be changed together or the disjointness
     just moves.

   A red test should pin the composition, not either half: with a live isolated
   session, a recorded worktree doc path plus a ``RuleContext`` whose ``cwd`` is the
   main checkout must not leave ``_docs_read_architecture`` failing with no
   satisfiable read — and a ``Read`` payload naming a main-checkout doc, resolving
   ``ambiguous``, must produce a report rather than silence.

   **Batch.** Filed to ``unsatisfiable-gates``, not to ``inert-instruments`` as the
   reporter suggested. ``inert-instruments`` is constituted by rules whose input set
   is empty by construction, returning a non-verdict that reads as a pass — an
   instrument that cannot **fail**. This one fails, permanently, and cannot
   **pass**; the polarity is opposite. BUG_041 settled the same question the same
   way: a mechanism that "drops a subset ... presents as an unsatisfiable gate
   rather than an absent instrument". The mechanism sibling is BUG_040 — same
   file, same ``_post_tool_use`` silent-drop site, same shape of evidence for a
   ``block`` gate never arriving, and the same diagnosis method: trace the recording
   route to its producer, then trace the reading rule to the root it tests
   against, and ask whether the two can ever agree.

   **Related.** :need:`BUG_028 <BUG_028>` is the origin of the ``proven`` guard this
   entry must not relax, and is the same silent-drop site one branch over.
   :need:`BUG_040 <BUG_040>` is the closest sibling: cross-root dispatch recorded
   nowhere, wedging ``agents.*_ran``; this is the same failure on the ``docs_read``
   key and across two **trees** rather than two **repositories** — note BUG_040
   explicitly cited ``docs_read`` recording correctly with worktree-absolute paths
   as counter-evidence about its own store, which stands, and is now known to be
   the *only* spelling that records. :need:`BUG_010 <BUG_010>` is the parent of the
   payload-attribution route both depend on, and its residue reserves the
   cross-root half. :need:`BUG_021 <BUG_021>` matters as a caution: its CAUSE
   CORRECTED note establishes by probe that ``Manifest.state_root`` is the git
   common dir shared by every worktree, so the **store** is not the variable here
   — which rules out the first explanation a reader will reach for and leaves the
   path spelling as the actual axis. :need:`BUG_023 <BUG_023>` is the same genus in
   the pipeline domain: one fact reaching a destination by two routes, one of them
   silent. :need:`BUG_003 <BUG_003>` is the earlier instance of an identifying signal
   not reaching the hook. :need:`BUG_037 <BUG_037>`, :need:`BUG_038 <BUG_038>`,
   :need:`BUG_041 <BUG_041>` and :need:`BUG_042 <BUG_042>` are the batch siblings.

   **Provenance.** Found by source reading in this repository plus live
   observation from a peer session in a consuming project, across two sessions on
   the same day. Reported 2026-08-20 against ``b95c4f5``. **Not reproduced in this
   repository's suite** — the two-tree repro above is the first step, and it needs
   no concurrency, only one registered session with a live worktree and a
   ``RuleContext`` rooted in the main checkout.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'Reproduced at both layers in this session''s worktree. Mechanism 2, tests/test_rules.py::TestDocsRules::test_BUG_046_a_worktree_doc_read_satisfies_the_gate_from_the_main_checkout — a recorded worktree doc path with a RuleContext rooted in the main checkout failed the gate, and its mirror failed too. Mechanism 1, tests/test_host_claude_code.py::TestReadIsAttributedByItsPath::test_BUG_046_a_dropped_docs_read_is_reported_rather_than_swallowed — an ambiguous Read of the shared-checkout doc returned {} and recorded nothing. The composition is pinned by test_BUG_046_the_gate_is_satisfiable_from_the_checkout_the_cli_stands_in, which needed no concurrency: two live sessions and a hook firing from the shared checkout.'
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Both mechanisms, changed together as the entry requires. Matching: core.rules.docs_roots reduces the docs root to the docs directory of EVERY working tree of this repository, so the verdict no longer depends on which of a session''s two trees the CLI was invoked from; _docs_read_architecture matches against that set. The widening stops at this repository — a foreign checkout''s docs/architecture is still refused (control test). Recording: proven is untouched, so the BUG_028 guard stands and an unproven read is still discarded; what changed is that the discard now SPEAKS. _record_docs_read reports a dropped read of an architecture doc naming the gate, saying the read was discarded rather than absent, and giving the worktree spelling as the remedy. It stays silent for an ordinary file and for an ungoverned turn, so the note is signal rather than noise. The reading side''s question is asked through core.rules.is_architecture_doc, the same helper the gate matches with, so the two ends cannot drift apart again. Verified on the real repository with two live worktrees: all four (cwd, read-spelling) cells now pass. Docs: state_model.md Attribution gains ''What may not be recorded may still have to be said'', the docs-first gate pair gains the spelling bullet, and overview.md''s hosts/ section records the shared-helper obligation.'
        forced: false

.. bug:: privacy.clean screens nothing as a review-boundary exit gate
   :id: BUG_047
   :status: fixed
   :severity: high
   :priority: medium
   :area: core/rules
   :batch: inert-instruments
   :slug: privacy-clean-is-inert-as-a-pipeline-gate
   :affects: COMP_CORE_RULES

   **Symptom.** ``privacy.clean`` is named as an exit gate in five places —
   ``session.yaml`` (``reviewed``), ``tdd.yaml``, ``bugfix.yaml``, ``spec-first.yaml`` and
   ``docs.yaml`` — and screens nothing in any of them. It is the gate the review
   boundary advertises as its privacy check, and it has never read a byte there.

   **Cause — source-confirmed, not suspected.** The rule is written against a
   **tool payload**. A transition evaluates it with a ``RuleContext`` whose
   ``payload`` is empty, so ``tool_name`` is ``""``, ``is_edit`` is ``False``, no command
   is present to be a commit, and the rule falls through to its N/A. Every one of
   its three scanning routes — the edit payload, the commit message, the staged
   content — needs a payload that a transition does not have.

   **Expected.** At a review boundary the subject is obvious and available: the
   work this session is about to put into history. The machinery now exists —
   BUG_027's fix added ``_staged_content_in_scope``, which resolves staged paths
   against ``privacy.scope`` and reads each blob, and ``git.branch_files`` /
   ``unpushed_files`` already answer the wider "what has this branch written"
   question that a review, unlike a commit, is really asking.

   **Not the same defect as BUG_027, and worth stating why.** BUG_027 was a gate
   that ran on the right event and looked at the wrong thing. This is a gate that
   runs where it has no subject at all. Its fix made the symptom *visible* rather
   than fixing it: the N/A now says "not screened: this turn made no Edit, Write
   or commit", and a block-level skip renders as ``[skip:BLOCK]`` and is counted
   separately (BUG_020), so a reader advancing a pipeline can now SEE that the
   privacy gate abstained. Before, it read as a pass.

   **Scope decision this needs, which is why it is filed rather than folded in.**
   Deciding what a review-boundary scan covers is a real choice — the staged set,
   the branch's whole diff against its base, or every file the session declared —
   and each answer changes which transitions start refusing in consuming projects
   that are mid-review today. That belongs in its own change with its own
   reproduction, not as a rider on a defect about shell writes.

   **Related:** :need:`BUG_027 <BUG_027>` is the sibling defect on the same rule and
   the source of the machinery a fix would reuse. :need:`BUG_012 <BUG_012>` is the
   same shape — a gate returning N/A on a route it was written for.
   :need:`BUG_020 <BUG_020>` is why the abstention is now visible at all.

   **Provenance.** Found while fixing BUG_027, by reading the machine definitions
   that name this rule after changing it. Not observed in the wild: no consuming
   project has reported a leak reaching a review, which is unsurprising, because
   a gate that never fires produces no evidence either way.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'Reproduced in tests/test_rules.py::TestPrivacyScreensTheReviewBoundary::test_BUG_047_a_leak_committed_on_the_branch_blocks_the_review — a transition-shaped RuleContext (empty payload) over a branch carrying personal data into docs/architecture/leak.md returned the N/A ''not screened: this turn made no Edit, Write or commit''.'
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Screens the review boundary: with an empty payload (the shape a transition hands the rule) privacy.clean now reads _review_boundary_content_in_scope — the _changed_set union of the branch''s commits against its base, the index, the working tree and the session''s declared file set — filtered by privacy.scope, and fails closed on an unestablished changed set via _unestablished_changed_set, the same refusal docs.updated and coverage.meets_floor already give there. Scope decision (staged set vs branch-vs-base vs declared set) taken by the user: branch-vs-base plus uncommitted. Deleted and undecodable paths are handled as at the commit boundary; untracked files are out of the subject by construction and the limit is documented. Six machines name this gate, not the five the entry lists — chore.yaml was missed.'
        forced: false

.. bug:: privacy.clean screens a git -C commit against the wrong index
   :id: BUG_048
   :status: fixed
   :severity: medium
   :priority: high
   :area: core/rules
   :slug: privacy-clean-screens-the-wrong-repositorys-index
   :affects: COMP_CORE_RULES

   **Symptom.** ``privacy.clean`` now screens the staged content of a ``git commit``
   — but always the staged content of **``ctx.cwd``'s index**, never the index of
   the repository the commit is actually being made in. ``_is_git_commit`` is
   token-aware and correctly reads ``git -C <other> commit -m …`` as a commit;
   nothing downstream of that decision consults ``-C``. So the gate screens one
   tree and vouches for a commit made in another: it reports having scanned a
   staged set that has nothing to do with the commit being made, and the content
   that lands in the named repository goes unscreened while the verdict asserts a
   scan.

   **Expected.** The content screened is the content THIS commit will put into
   history. Where the gate cannot resolve which index that is, it fails closed and
   says which option it could not follow — the do-not-relax convention every other
   unanswerable question in this rule already follows (AUD-09), and the one that
   matters most here, because nothing un-writes an identifier out of a published
   history.

   **Repro.**

   1. A project with the default ``privacy.scope`` — ``memory`` and ``commit_messages``
      are both in it (``core/manifest.py`` defaults).
   2. From a ``Bash`` tool call whose working directory is a *different* tree, stage
      a file inside that other tree's privacy scope carrying personal data, then
      commit it: ``git -C <other> add <path>`` followed by
      ``git -C <other> commit -m "…"``.
   3. The gate passes. Either it reports ``scanned <paths>: no matches`` over
      ``ctx.cwd``'s staged set — files with no relation to the commit — or, when
      ``ctx.cwd`` has nothing staged, ``scanned commit message: no matches``, a clean
      PASS over a commit whose staged blobs were never read at all.

   **Cause — source-confirmed by reading, not suspected. Refs current on this
   session's branch.**

   - ``_is_git_commit(command)`` takes only the command string and answers only
     "is this a commit". It is shared by both halves of the rule, which is
     correct and is not the defect.
   - ``_staged_content_in_scope(ctx, scope)`` then asks git three questions, each
     rooted at ``ctx.cwd``: ``git.staged_files(ctx.cwd, exclude_deleted=True)`` for
     the staged set, ``git.toplevel(ctx.cwd)`` for the root those relative paths are
     resolved against before the scope test, and ``git.staged_blob(ctx.cwd, rel)``
     for the bytes.
   - ``-C`` changes git's working directory for the invoked command only. Nothing
     in the rule's path resolution consults it, so both the scope test and the
     staged read resolve against the wrong toplevel.
   - The **message half has the same blindness**, one size smaller:
     ``_extract_commit_message(command, cwd=ctx.cwd)`` hands ``ctx.cwd`` to
     ``_read_commit_message_file``, which resolves a relative ``-F`` name against it.
     Under ``git -C <other> commit -F msg.txt`` that reads the wrong file — absent
     is a fail-closed and therefore safe, but *present* means scanning an
     unrelated message and passing.
   - ``--git-dir`` and ``--work-tree`` are the same class of option and are equally
     unconsulted.

   **A residual limit of BUG_027's fix, NOT a regression of it — this matters for
   how the entry is read.** Before that fix, no staged content was scanned on any
   route: not in ``ctx.cwd``, not in a ``-C`` target, not anywhere. Measured as new
   exposure this is nil; the fix strictly widened what gets screened and this is
   the edge it did not reach. What the fix DID introduce is the verdict — a
   sentence asserting a scan — and that sentence is the defect, on exactly the
   axis :need:`BUG_027 <BUG_027>` was filed on. Nobody should read this as the fix
   having broken something.

   **The trigger is not exotic — this library ships an agent that uses it.**
   ``compliance-reviewer``'s memory-sync step stages with
   ``git -C <MAIN> add -- <memory/config paths>`` and commits that staged set, and
   the reviewer runs **from its session worktree**. A main checkout and a linked
   worktree are one repository with **separate indexes**, so
   ``git.staged_files(ctx.cwd)`` reads the worktree's index — typically empty by
   then, the session having already committed — while the commit lands the main
   checkout's staged set. ``backlog-curator``'s own "in the shared main checkout,
   ``git add`` only the backend's item file(s)" is the same shape. The paths in
   question are ``paths.memory`` and ``paths.memory_index``, which the DEFAULT scope
   covers in every consuming project.

   **Its compensating control, and the limit of leaning on one.** That same step
   mandates ``invariant memory check`` — which does run a privacy scan — before the
   commit. It is a real control for the memory half, it covers none of the host
   config paths the step also stages, and BUG_027's entry already wrote the
   argument against resting on it: *"a control that only works when its subject
   independently remembers to do the check is not a control."*

   **Severity is medium, not the low the report proposed, and the reason belongs
   in the entry.** The report's case for low was that the rule "fails toward
   scanning something rather than nothing", so it is not a silent no-op. That
   holds for a genuinely separate repository. It does **not** hold on the most
   reachable instance above: the worktree-to-main-checkout commit stages into an
   index the rule never reads while the rule's own index is empty, so it fails
   toward scanning *nothing* and still returns a clean PASS off the message half.
   The mitigation the low grade rested on is absent from the case most likely to
   occur. It stays short of high because the blast radius is bounded to
   directory-redirected invocations, the message half still does its own job, and
   git being unable to answer still fails closed.

   **Affected.** ``core/rules`` — ``_privacy_clean`` and its helper
   ``_staged_content_in_scope``, plus ``_extract_commit_message`` /
   ``_read_commit_message_file`` for the smaller ``-F`` instance. ``core/git`` supplies
   the answers and is **not** at fault: per the component table in
   ``docs/architecture/overview.md`` it answers read-only questions about the
   repository at a ``cwd`` it is handed and never decides what an answer means.
   Choosing WHICH cwd to hand it is the rule's job, which is where this sits.

   ``docs/architecture/manifest.md`` § *Where the gate screens, and why it is not
   only the edit tools* states the property this breaks: *"A commit typed in a
   subdirectory names the same blobs, because staged paths are spelled against
   the repository toplevel."* That is true of a subdirectory and false of ``-C``,
   and the document does not distinguish the two. Correcting it belongs with the
   fix, not with this entry.

   **Fix sketch / next step.**

   - **Reproduce first.** Drive ``_privacy_clean`` with a ``Bash`` payload
     ``git -C <other> commit -m …`` where the personal data is staged in ``<other>``
     and ``ctx.cwd``'s index is clean, and assert a blocking FAIL. Pair it with a
     control that stages the same content in ``ctx.cwd`` and commits a clean
     ``<other>`` — the two together pin the mis-target itself rather than the
     absence of a scan.
   - **Two directions, not exclusive.** Parse ``-C`` (and ``--git-dir`` /
     ``--work-tree``) out of the command tokens and resolve the index against it —
     ``_shell_tokens`` already exists and ``_is_git_commit`` already walks the tokens,
     so the seam is open. Or detect the redirection and **fail closed**, which is
     the cheaper honest floor: a verdict naming the option it will not follow
     beats a clean scan of the wrong tree.
   - **Two traps for whoever takes it.** ``-C`` is repeatable and its paths are
     cumulative and relative to each other, so a parser that takes the first and
     stops will be wrong. And whether a tree OUTSIDE the manifest's project is in
     scope at all is a decision this change has to make deliberately — for that
     case the honest answer may be "refuse", not "scan".

   **Batch.** Unbatched, deliberately, and the reasoning is the same one BUG_046
   used. ``inert-instruments`` is constituted by rules whose input set is empty by
   construction — an instrument that cannot **fail**; this one fires, reads a real
   staged set, and returns a real verdict. ``unsatisfiable-gates`` is the opposite
   polarity — a gate that cannot **pass**; this one passes, which is the whole
   problem. The mechanism it actually shares is *a rule resolving its subject
   against ``ctx.cwd``'s tree rather than the tree the action names*, and its only
   neighbour on that axis is :need:`BUG_046 <BUG_046>`, already batched elsewhere and
   not to be moved without a call from the user. A batch of one is unbatched, so
   this rides along; a **second** instance of the wrong-tree-resolution mechanism
   is the point at which minting that batch would be worth it.

   **Related.** :need:`BUG_027 <BUG_027>` is the fix this is a residual limit of, and
   the source of every helper named above. :need:`BUG_047 <BUG_047>` is the sibling
   found in the same review: the same rule, a different route it cannot cover,
   filed rather than folded in for the same reason. Together the three map the
   rule's coverage — the edit payload (works), the commit boundary (works, except
   here), and the transition (nothing at all). :need:`BUG_046 <BUG_046>` earns its
   link on mechanism rather than on subsystem: a rule resolving evidence against
   the wrong one of a session's two trees, presenting as a verdict that does not
   match what was examined. :need:`BUG_001 <BUG_001>` is the polarity precedent
   BUG_027 already cited — a control nobody can see fail is one nobody re-checks,
   and a PASS naming only the commit message is precisely that.

   **Provenance.** Found and disclosed by the implementing agent while fixing
   BUG_027, which chose not to file it; the user decided it should be filed.
   Reported 2026-08-20. Confirmed here by source reading in this session's
   worktree, together with the shipped agent instructions that make the route
   reachable. **Not observed in the wild and not reproduced:** no test drives
   ``_privacy_clean`` with a directory-redirected commit, and writing one —
   asserting a FAIL, not a PASS — is the first step.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: Reproduced in tests/test_rules.py::TestPrivacyScreensTheIndexTheCommitNames::test_BUG_048_a_leak_staged_in_the_named_tree_is_screened — 'git -C <wt> commit' with the leak staged in <wt> and ctx.cwd's index clean returned a clean PASS over the commit message alone. Paired with a control that stages the leak in ctx.cwd and commits <wt>, which pins the mis-target rather than the absence of a scan.
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Screens the index the commit names: _git_command_cwd resolves the repository a ''git commit'' runs in before either half of the rule runs, and both halves use it — the staged read (_staged_content_in_scope now takes the repo) and the relative -F message file. Read per INVOCATION, so the chained ''git add -A && git -C <other> commit'' spelling resolves the commit''s own directory and a --git-dir on a neighbouring subcommand does not fail the commit. -C is cumulative. --git-dir/--work-tree fail closed naming the option they will not follow. A commit into a repository outside the project abstains visibly rather than refusing — the message is still scanned and the verdict states the content was not — because privacy.scope is project-relative and _is_outside_project''s position is that blocking unrelated checkouts is what gets a rule turned off.'
        forced: false

.. bug:: A blocking hook verdict cannot be overridden in band, and never asks the operator
   :id: BUG_049
   :status: fixed
   :severity: high
   :priority: high
   :area: hosts/claude_code
   :slug: hook-block-has-no-in-band-override
   :affects: COMP_HOSTS_CLAUDE_CODE

   **Symptom.** Every gate reachable through the CLI can be passed deliberately:
   ``session transition --force --reason``, ``pipeline advance --force --reason``,
   ``item transition --force --reason``, each recording an override onto the record.
   The gates that actually stop work do not go through the CLI. Seven rules
   evaluate at ``pre-tool-use`` and five at ``stop``, and a blocking verdict there is
   final: ``_pre_tool_use`` returns ``permissionDecision: "deny"``, ``_stop`` returns
   ``decision: "block"``, and there is no argument, marker, flag or prompt that lets
   the operator say "yes, do it anyway" for this one call. The only relief is to
   edit the manifest — a permanent change to project policy, made mid-task, to get
   past a single call — or to set ``INVARIANT_DISABLE``, which the running host
   cannot pick up because the hook subprocess inherits the environment the host
   was launched with.

   **Why this is a compliance defect and not a missing feature.** The library's own
   doctrine states it: *"Every gate needs an escape hatch for the case it gets
   wrong"* (``docs/architecture/manifest.md``, on the exemption marker). The escape
   hatch exists for privacy (a marker in the text), for isolation
   (``session.exempt_paths``) and for every transition gate (``--force``). It does not
   exist for the delivery surface as a class, and that is the surface where the
   operator meets the tool. A deny with no in-band appeal makes the engine, not
   the operator, the final authority on the operator's own repository.

   **The advice at the one place overriding IS discussed points somewhere that
   does not apply.** ``PipelineDomain.acknowledge`` refuses a block-level rule with:

   ::

      <rule> blocks in this project and cannot be acknowledged.
        Satisfy it, or record a deliberate override on the transition itself:
          invariant pipeline advance <state> --force --reason '<why>'

   For ``docs.updated`` that is correct advice. For ``privacy.clean``,
   ``session.isolated_before_edit``, ``session.no_edit_in_main_checkout``,
   ``session.push_barrier``, ``session.ff_merge_target``, ``session.own_file_set_only``
   or ``pipeline.started_before_edit`` there is no transition to force — the block
   happened on a tool call. The message sends the caller to a command that cannot
   help, which is worse than saying "there is no override for this".

   **Acknowledgement does not reach the hook surface at all.** ``acknowledged``
   evidence is consulted only in ``PipelineDomain.evaluate_gates``
   (``domains/pipeline.py``, ``_apply_acknowledgements``). Hooks call
   ``evaluate_event`` -> ``evaluate`` in ``core/rules``, which never reads it. So even a
   *warn*-level hook rule cannot be acknowledged into silence for the run.

   **Repro.**

   1. A project on the ``standard`` profile with a registered session that has not
      isolated yet.
   2. Edit any non-exempt file. ``session.isolated_before_edit`` blocks the call.
   3. There is no way, from inside that turn, to perform the edit deliberately and
      have the decision recorded. ``invariant pipeline acknowledge session.isolated_before_edit --reason '...'`` refuses (block-level). Nothing
      else accepts an override for a rule that was never asked at a transition.

   **Expected.** A blocking hook verdict is an obstacle the operator can pass
   deliberately, exactly once, with the decision recorded the way a forced
   transition is recorded. Two directions, not exclusive:

   - **Ask instead of deny.** The host's ``PreToolUse`` contract carries an ``ask``
     decision alongside ``allow`` and ``deny``; ``ask`` routes the call to the human
     with the reason attached. A rule level of ``ask`` — a fourth ``Level``, delivered
     as ``permissionDecision: "ask"`` with the verdict's render as its reason — puts
     the human back in the loop without weakening what the rule proves. This is
     purely a delivery change, which is the axis ``Level`` already exists on: *"Level
     is a property of how a verdict is delivered, not of the rule's logic."* Note
     that a human approval arrives back through the host and is not currently
     observable to this library, so recording it needs its own answer.
   - **A one-shot override token.** ``invariant override <rule.id> --reason '<why>' [--calls 1]``, stored on the session record and consumed by the next
     evaluation of that rule at that surface, appended to ``overrides`` exactly like
     a forced transition. This works for ``stop`` rules too, which ``ask`` does not
     reach.

   **Affected.** ``hosts/claude_code.py`` — ``_pre_tool_use``, ``_stop``.
   ``core/rules.py`` — ``evaluate``, ``evaluate_event``, ``Level``.
   ``core/manifest.py`` — ``Level``, ``rule_level``. ``domains/pipeline.py`` —
   ``acknowledge`` (its refusal message), ``_apply_acknowledgements``.
   ``docs/architecture/manifest.md`` and ``docs/architecture/cli.md`` state the
   override surface and would both move.

   **Fix sketch / next step.** Reproduce first, at the host boundary: drive
   ``ClaudeCodeHost.handle("pre-tool-use", ...)`` with a payload that trips a
   block-level rule and assert that no manifest-independent input to that call can
   turn the deny into a pass. That test is the acceptance criterion, and it is
   also the thing that would have caught this: every existing test of the override
   machinery drives a domain method, and none drives the hook.

   **Related.** :need:`BUG_046 <BUG_046>` — a gate no session can satisfy is the same
   family seen from the other side: this one can be satisfied and cannot be
   declined. ``docs/adr/ADR-001`` D12 makes docs-first core policy while README
   promises *"Any of these can be dropped to warn or off in the manifest"* — that
   promise is about permanence, and this entry is about the temporary case it does
   not cover.

   **Provenance.** Found by a review of the whole implementation against the
   project's founding position: the engine makes an agent deterministic, and must
   never take authority away from the operator, who stays free to override a rule
   temporarily or permanently. Reported 2026-08-20. Confirmed by source reading;
   not yet reproduced by a test.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: null
        forced: false
      - from: reproduced
        to: fixed
        reason: null
        forced: false

.. bug:: An enforcement override naming an unknown rule is accepted and silently discarded
   :id: BUG_050
   :status: fixed
   :severity: medium
   :priority: high
   :area: core/manifest
   :slug: unknown-rule-override-silently-discarded
   :affects: COMP_CORE_MANIFEST

   **Symptom.** An enforcement override that names a rule id the registry does not
   know is accepted in silence and does nothing. The rule the operator meant to
   downgrade keeps blocking, ``invariant doctor`` is green, ``invariant rules list``
   shows the rule at its profile level with no hint that anything nearby was
   rejected, and the only surface that would reveal it —
   ``config show --provenance`` — shows the *typo* carrying a provenance, which
   reads like the override took.

   **Expected.** An override is an exercise of the operator's authority. It either
   takes effect or it says out loud that it did not. This is the library's own
   central claim applied to its own configuration: *"Non-compliance is silent"* is
   the failure it exists to remove, and a discarded override is exactly that
   failure pointed at the operator instead of at the agent.

   **Repro.** Confirmed by running it, not by reading:

   .. code:: python

      m = Manifest.from_raw(
          {"project": {"name": "x", "ecosystems": ["python"]},
           "enforcement": {"profile": "standard",
                           "rules": {"privacy_clean": "off",
                                     "docs.read_arcitecture": "off",
                                     "not.a.rule": "block"}}},
          root=..., path=..., env={})
      # loads with no error
      m.rule_level("privacy.clean")           # -> Level.BLOCK
      m.rule_level("docs.read_architecture")  # -> Level.BLOCK

   All three bogus keys survive into ``data["enforcement"]["rules"]`` beside the real
   ones. The environment layer is the same:
   ``INVARIANT_DISABLE="privacy.klean,docs.read_architecture"`` disables the second
   and silently ignores the first.

   **Cause — source-confirmed.**

   - ``Manifest.validate()`` iterates ``enforcement.rules`` and calls ``Level.parse`` on
     each **value**. The **key** is never checked against anything. Validation
     covers the manifest version, ``privacy.scope``'s type, the items backend and the
     methodology's existence — the methodology check was added for precisely this
     class of fault (AUD-08: *"a mistyped methodology used to surface only when a
     machine load happened to run"*), and the rule ids were not given the same
     treatment.
   - ``Manifest.rule_level()`` returns ``Level.WARN`` for any id absent from the
     resolved map, so an unknown id is inert rather than loud.
   - ``_apply_env`` splits ``INVARIANT_DISABLE`` on commas and writes each token
     straight into the map with no membership test.
   - ``core/rules.evaluate`` DOES have the loud behaviour for this exact case — an
     id with no registered rule produces a failing verdict, *"A gate naming a rule
     that does not exist would otherwise pass silently forever."* That reasoning is
     already written down in this codebase; it is applied to a rule someone asks
     to evaluate and not to a rule someone asks to downgrade.
   - ``validate()`` cannot import the registry naively — ``core/rules`` imports
     ``core/manifest``, so the check has to be a deferred import or a callable the
     caller supplies. That is the whole reason it is not already there, and it is
     a solvable one.

   **Severity.** Medium, and it is the *combination* that earns it: the operator's
   override is the one control this library gives back, and a silent no-op there
   costs more than a silent no-op anywhere else. It stays short of high because
   nothing is corrupted and the fix is small.

   **Affected.** ``core/manifest.py`` — ``validate``, ``_apply_env``, and the docstring
   for the resolution layers. ``cli/main.py`` — ``cmd_doctor`` (which should carry the
   same check, so an existing project hears about it without editing anything) and
   ``cmd_rules_list``. ``docs/architecture/manifest.md`` § the layer list.

   **Fix sketch / next step.**

   - Reproduce first, with the snippet above as a test asserting a ``ManifestError``
     naming the unknown id and, ideally, the nearest real one — a typo three
     characters off should be answered with the id the operator meant.
   - Refuse at load, do not warn: a manifest is read on every command, so a warning
     here is printed hundreds of times and is read none of them.
   - An extension point deserves care: a project may name a rule from a machine or
     adapter this build does not carry. If any such path exists the check must be
     "unknown to the registry AND unknown to every loaded extension", not "unknown
     to the registry".
   - ``INVARIANT_DISABLE`` is an escape hatch reached in a hurry, and refusing to
     load the whole manifest because one token was mistyped would wedge a caller
     who is already blocked. There it should be loud on stderr and otherwise
     proceed.

   **Related.** :need:`BUG_049 <BUG_049>` — the same axis, the operator's authority,
   seen at the delivery surface rather than at the configuration one. AUD-08 in
   ``docs/audits/mission-audit-2026-08.md`` is the precedent for validating a
   configuration key at load rather than at first use.

   **Provenance.** Found by a review of the implementation against the project's
   founding position: the operator stays the authority and may override any rule
   temporarily or permanently. Reported 2026-08-20. Reproduced against the current
   source; no committed test covers it.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: null
        forced: false
      - from: reproduced
        to: fixed
        reason: null
        forced: false

.. bug:: assets install destroys the local edits it has already identified as local edits
   :id: BUG_051
   :status: fixed
   :severity: medium
   :priority: medium
   :area: hosts/claude_code
   :slug: asset-install-destroys-local-edits
   :affects: COMP_HOSTS_CLAUDE_CODE

   **Symptom.** ``invariant assets install`` and ``invariant agents install``
   overwrite every library-shipped asset in the project unconditionally — including
   the ones the tool has already recognised as **locally edited**. There is no
   ``--force`` to make the destruction deliberate, no per-asset skip, no backup, and
   ``--dry-run`` reports "would reinstall library assets" without naming a single
   file it would flatten. The operator's customisation of an agent living in their
   own repository is discarded with no prompt and no trace.

   **And the tool routes people into it.** ``ClaudeCodeHost._stale_for`` already
   distinguishes the two cases and labels them differently — ``"(edited locally)"``
   when the marker digest matches what the library shipped (so the divergence is
   the operator's own edit) versus ``"(installed from an older version)"``. That
   distinction is computed, rendered into a **failing** ``doctor`` check, and then
   ignored by the only command ``doctor``'s output points at. The knowledge needed to
   protect the edit exists; the code that would act on it does not.

   **Expected.** A file in the operator's repository is the operator's. The
   library may say "this differs from what I ship" — that is useful and already
   works. It may not silently revert it. An install that would destroy a local edit
   either skips it, or refuses and names it, or requires the operator to say so:

   ::

      invariant assets install
        skipped feature-architect (edited locally) — pass --overwrite-local to replace

   **Repro.**

   1. ``invariant agents install`` in any initialised project.
   2. Edit ``.claude/agents/feature-architect.md`` — add a project-specific
      instruction, leaving the managed frontmatter block intact.
   3. ``invariant doctor`` reports the asset check as FAILED, naming
      ``feature-architect (edited locally)``.
   4. ``invariant agents install``, the repair that failure suggests. The edit is
      gone. Output reads ``installed N asset(s)`` with no mention that one of them
      had been changed.

   **Cause — source-confirmed.** ``ClaudeCodeHost.install_assets`` loops the shipped
   sources and calls ``target.write_text(assets.render_managed(body, digest))`` for
   each, with no read of the existing target and no consultation of ``_stale_for``.
   ``install_agent`` is the same shape. ``cmd_assets_install`` and ``cmd_agents_install``
   expose only ``--kind`` and ``--dry-run``. The dry run returns before any per-file
   work, so it cannot name what it would touch.

   **A design tension the fix has to settle, not dodge.** These assets are managed
   files with a digest marker, and the marker is what makes staleness detectable at
   all. "Managed" reasonably means the library owns the bytes. But the project
   already offers ``agents.local`` for project-owned agents, and the operator who
   edits a shipped one is telling the tool something — usually "your version is
   nearly right". The honest resolution is that the library owns the *shipped*
   copy and the operator owns the *installed* one, so an install that diverges from
   what is on disk is a merge question, not a write. At minimum: never destroy
   without being asked, and print the path so the edit can be recovered from git.

   **Severity.** Medium. Recoverable where the file is committed — this repository
   commits ``.claude/agents/`` — and unrecoverable where it is not, which is the
   default in a project that gitignores its host directory.

   **Affected.** ``hosts/claude_code.py`` — ``install_assets``, ``install_agent``,
   ``_stale_for``, ``agent_checks``, ``asset_checks``. ``cli/main.py`` —
   ``cmd_assets_install``, ``cmd_agents_install``, and their parsers.
   ``docs/architecture/cli.md`` § the asset commands.

   **Fix sketch / next step.** Reproduce first: install, mutate the body, install
   again, assert the mutation survives. Then teach ``install_assets`` to classify
   each target before writing (the classifier exists — lift it out of ``_stale_for``
   so both callers share one answer), skip anything locally edited, return what was
   skipped as well as what was written, and add ``--overwrite-local`` for the
   operator who does want the shipped version back. Make ``--dry-run`` enumerate.

   **Related.** :need:`BUG_049 <BUG_049>` and :need:`BUG_050 <BUG_050>` — the same
   principle at two other surfaces: the operator decides, and the tool does not
   quietly decide for them. ``cmd_assets_install``'s own docstring already carries
   the right instinct for the neighbouring case — it exists *"separate from init
   deliberately"* so a repair cannot cost the operator their enforcement
   configuration — and it is that same instinct this entry asks to extend from the
   manifest to the assets.

   **Provenance.** Found by a review of the implementation against the project's
   founding position: the operator holds authority over their own project.
   Reported 2026-08-20. Confirmed by source reading; not reproduced by a test.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: null
        forced: false
      - from: reproduced
        to: fixed
        reason: null
        forced: false

.. bug:: A pipeline-scoped stand-down granted with no pipeline running expires the instant one starts, not when it finishes
   :id: BUG_052
   :status: fixed
   :area: core/rules
   :severity: medium
   :priority: high
   :affects: COMP_CORE_RULES

   **The want.** ``invariant standdown --reason '<why>'`` (the default ``--until pipeline``) granted while NO pipeline is running should hold until the next
   pipeline that starts under it finishes — the CLI's own confirmation message
   says exactly that: "lifts when a pipeline starts and finishes".

   **What actually happened.** ``core.rules.standdown_in_force`` records
   ``pipeline_started: None`` when there is nothing to pin the grant to at grant
   time, and read that ``None`` as "ends the moment ANY pipeline exists" rather
   than "waits for one, then rides it to its finish". The ordinary next step
   after standing down for unrelated work is starting the pipeline that work
   needed — so the grant evaporated at exactly that step, before any work had
   happened under it, with nothing at that moment saying the hold had lapsed. The
   next blocked call read as an ordinary deny with no mention that a stand-down
   the operator believed was still active had just ended.

   **Repro.** ``invariant standdown --reason why`` with no pipeline running, then
   ``invariant pipeline start``: a ``pre-tool-use`` call that the stand-down should
   still be covering was denied immediately after start, not after the pipeline
   finished.

   **Fix.** ``standdown_in_force`` now treats "no pipeline at grant time" as
   WAITING rather than scoped-and-satisfied: once a pipeline starts, it becomes
   the pipeline the hold rides, on the same finish/terminal-state test already
   applied to a hold granted while one was running. Regression test:
   ``tests/test_authority.py::TestAStandDownHoldsEveryGateReversibly::test_granted_with_no_pipeline_running_holds_through_the_first_one``.

   **Found by.** Post-hoc review of the authority-audit fix batch
   (a01077b..72e7d23), specifically asked to scrutinise whether
   ``standdown_in_force`` actually expires. It does — this was the opposite defect,
   expiring too early and silently, which is a smaller hole than "never expires"
   but is still enforcement drifting out from under the operator's own stated
   intent.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: failing test test_granted_with_no_pipeline_running_holds_through_the_first_one proves the premature-expiry defect, against the pre-fix standdown_in_force
        forced: false
      - from: reproduced
        to: fixed
        reason: standdown_in_force treats no-pipeline-at-grant as waiting for the first pipeline rather than as already-scoped-and-satisfied; regression test now passes
        forced: false

.. bug:: The ip_addresses pattern matches a hierarchical document row number
   :id: BUG_053
   :status: fixed
   :severity: high
   :priority: high
   :area: core/privacy
   :slug: ip-pattern-matches-a-document-row-number
   :residue: OPEN, and deliberately not fixed here: a four-segment row number written BARE — in a table cell, a reST field name, a section title — is indistinguishable from an address by any local rule and is still flagged. This entry's own distinct-values list is the demonstration: it needs a permanent exemption marker, and the marker's comment says so. Whether the reporter's 55 matches are ALL marker-prefixed was requested and is not established here, so the fix's coverage of that corpus is unmeasured on this side. If any are bare, the answer is a project-declared disqualifier (privacy.allow takes literals today, not shapes) and that is its own entry and its own config decision.; The change could not be screened by its own fix. privacy.clean at the review boundary — itself live only since BUG_047 merged an hour earlier — flagged 9 matches in this change, every one of them a false positive that this change makes stop matching. The engine screening the commit is the editable install against the main checkout, so the new pattern was not live in it. Recorded as an override with that reason rather than papered over with exemption markers: markers added here would be inert the moment the fix merged, and an inert marker is exactly the erosion IDEA_002 is about. Verified before overriding, by running the NEW scanner over the changed files: manifest.md clean, BUG_053.md clean apart from the bare-values block that carries a permanent marker.; RESIDUE DISCHARGED for the reporting corpus, 2026-08-24, measured by the reporter with the SHIPPED scanner rather than a copy of the regex — imported PATTERNS['ip_addresses'] and _ROW_NUMBER_REFERENCE and ran both over the whole tree. Bare quads DID exist: 20 hits, all in one file. Eleven were the queue table's own id column, where the header is '#' and the cell was bare — so the id that every prose citation writes as marked was unmarked in the one place it is defined — and nine were prose references written without the marker. The reporter fixed all 20 on their side by writing the marker, which is that project's own convention applied where it was missing rather than a workaround: no config change, no privacy.allow entry, no exemption markers. After: 61 raw matches before the row-reference guard, 0 findings after it, across all of docs/. The raw count moved 55 -> 61 between the two measurements because a consolidation folded a second document into the plan, which is the concentration the entry predicted and the reason the fix order (pattern first, scope second) was right. NOTE what this does and does not settle: the bare-quad residue above is UNCHANGED as a property of the pattern — it was discharged by the corpus changing, not by the pattern learning to read a bare row id. A project that cannot mark its row ids still needs the project-declared shape this entry defers.
   :affects: COMP_CORE_PRIVACY

   **Symptom.** ``PATTERNS["ip_addresses"]`` matches a hierarchical document row
   number. A release plan that numbers its rows four levels deep writes ``#10.14.6.1``
   and ``#10.14.4.6``; four integers, dot-separated, every one at or below 255 is a
   valid dotted quad, so the privacy gate fires on the plan's own numbering.

   **Measured, and not by this project.** Reported by the maintainer of a consuming
   project (Heap) on 2026-08-24, against that exact regex over its whole tracked
   corpus:

   .. invariant: allow-privacy — the values below are written BARE, which is
      precisely the case the fix deliberately does not cover: nothing local
      tells a bare quad from an address. So this marker is permanent, and it is
      the entry's own demonstration of its residue rather than a workaround.

   | ``docs/release_dashboard.rst   45 matches``
   | ``docs/backlog.rst              9 matches``
   | ``docs/release_plan.rst         1 match``
   | ``distinct values: 10.14.6.1, 10.14.6.2, 10.14.4.4, 10.14.4.6..10.14.4.9, 10.14.6.3``
   | ``real IP addresses among them: zero``

   **Expected.** ``ip_addresses`` means "an IP literal", not "four numbers with dots
   between them". Where local context settles the question — a preceding ``#`` or
   ``§``, a run longer than four segments — the pattern does not fire.

   **A second defect, found here rather than reported.** The pattern is
   ``\b{_IPV4}\b``, and ``\b`` is satisfied by the ``.`` that follows a longer run. So a
   four-segment PREFIX of a five-or-more-segment dotted number matches:
   a five-segment number yields its first four segments as a finding, and
   ``#10.14.6.1.2`` yields the marked row number the marker was supposed to settle.
   Verified by execution on master at bffc504. This half is settled structurally and is not a
   judgement call; the reporter's four-deep rows are exactly four segments, so it
   does not cover them.

   **Not a regex correctness bug, which is why the fix is a narrowing and not a
   repair.** ``#10.14.4.4`` IS a syntactically valid dotted quad, and the pattern is
   doing what it says. What is wrong is the breadth, against this file's own
   standing instruction at ``core/privacy.py``: *"Each pattern is deliberately
   narrow. Breadth here buys false positives, and a noisy privacy gate is one that
   gets disabled wholesale rather than tuned."* The reported consequence is exactly
   that sentence coming true.

   **The cost is already being paid in coverage, not in noise.** The reporting
   project suppressed these with ``.. invariant: allow-privacy`` markers, and because
   an exemption reaches to the end of its block (BUG_045), 58 lines across two
   files are now exempt from a BLOCKING gate in order to suppress 4 false matches.
   Every marker is a region where a real violation passes silently, and the count
   rises while the pattern stays wrong. That measurement is the strongest evidence
   yet for IDEA_002 (a scan reports nothing about what a marker suppressed) and is
   recorded there too.

   **Fix-order dependency, stated by the reporter.** That project's
   ``privacy.scope`` resolves ``docs`` through ``paths.docs``, so its plan documents sit
   outside the block-level gate today — the same shape this repository fixed for
   itself with the literal ``docs/`` scope entry. The moment it widens that scope,
   which it must, the 55 false positives become blocking. This fix has to land
   first.

   **Affected:** ``core/privacy`` — the pattern table. ``_IPV4`` itself is SHARED with
   the ``links`` pattern, which asks whether a URI's authority is an IP literal;
   there the scheme already proves it is an address. So the narrowing belongs on
   the ``ip_addresses`` entry, NOT in ``_IPV4``, or ``links`` changes meaning silently
   (the comment above ``_IPV4`` says why the two share one spelling).

   **Fix sketch.** Two guards, both in idioms this file already uses:

   1. ``(?<![\d.])`` / ``(?![\d.])`` instead of ``\b...\b``, the exact boundary
      ``clock_times`` already uses to keep a version string from reading as a time.
      Settles the longer-run half.
   2. A reference-marker disqualifier read from the text immediately LEFT of the
      match — ``#``, ``§``, or a ``no.``/``item``/``row``/``section``/``step`` word. Same shape
      as ``_TIME_FALSE_ALARM``, but anchored to the match rather than to the whole
      line, which is strictly narrower than the precedent.

   **Test direction, and the reporter's warning about it.** A regression fixture
   needs BOTH directions in one table: ``#10.14.6.1`` as a must-NOT-match and a
   genuine address as a must-match. A test asserting only the must-match direction
   passes today and proves nothing.

   **Open, pending the reporter's corpus.** Whether every one of the 55 matches
   carries a ``#`` is not established here. If any are written bare — in a table
   cell, a reST field name, a section title — no local rule can tell them from an
   address, and the answer is a project-declared disqualifier rather than a
   narrower pattern. That would be its own entry and its own config decision.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'Reproduced in tests/test_privacy.py::TestIpAddressesAreAddressesAndNotRowNumbers — 12 tests, 5 red on master bffc504, every red in the must-NOT-match direction and every must-match test green, which is the asymmetry the reporter warned about. The reds cover both halves: a marked four-level row number in seven contexts, a dotted run longer than four segments, both guards on one string, and the reported release-plan line verbatim.'
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Narrowed on the ip_addresses entry only, in two guards. (1) Length: (?<![\w.]){_IPV4}(?!\.?\d) replaces \b...\b — it keeps the word boundary that stops a version string reading as an address and adds the dot that \b could not see, so a four-segment run adjacent to a longer one is the tail of a number rather than an address. Spelled around the DIGIT rather than the dot, because a trailing dot with nothing after it is how prose ends a sentence and the other spelling would have cost every address written at the end of one. (2) A reference marker written hard against the digits — # or the section or pilcrow sign — marks a row or a section; adjacency is the whole of the rule, so a spaced marker (a heading, a shell comment) leaves the finding standing. Anchored to the text immediately left of the match rather than searched over the whole line, which is strictly tighter than the _TIME_FALSE_ALARM precedent it copies. _IPV4 itself is UNCHANGED because links shares it and a scheme already proves its authority is an address.'
        forced: false

.. bug:: The coverage floor is one global number, so a file whose ceiling is below it blocks forever
   :id: BUG_054
   :status: not_reproducible
   :severity: medium
   :priority: high
   :area: core/rules, core/manifest
   :batch: unsatisfiable-gates
   :slug: coverage-floor-is-global-so-a-low-ceiling-file-blocks-forever
   :residue: DECISION 2026-10-21 (user-approved): closed not_reproducible\; the remaining design question is IDEA_011\; the repro test lands as a regression guard (tests/test_bug_054_repro.py).
   :affects: COMP_CORE_RULES, COMP_CORE_MANIFEST

   **Symptom.** ``coverage.meets_floor`` blocks indefinitely on a source file whose
   uncovered remainder is **structurally unreachable by the ecosystem's test
   runner**, and no key the manifest offers can express that fact. The floor is a
   single global integer, resolved once at the top of the rule
   (``methodology.gates.coverage_min``, ``core/rules.py::_coverage_meets_floor``), and
   every relaxation built since is about which files are *asked* the question —
   none of them can change the number the question is asked with.

   Measured in the consuming project at the gate's own whole-scheme lane, ~29k
   cases, every suite executed and passed — so these are real numbers and not the
   silently-dropped suites of :need:`BUG_038 <BUG_038>`:

   ::

      File                               before      after   floor   ratchet
      RecoveryPhraseCopy.swift  [NEW]         —    100.00%      90   —        PASS
      ExportRecoveryPhraseSheet.swift    14.83%     14.83%      90   none     FAIL
      ImportKeySheet.swift                7.18%      7.23%      90   none     FAIL
      LibraryKeyScreen.swift              4.34%      4.49%      90   none     FAIL
      ImportLibraryWizard.swift           0.00%      0.00%      90   none     FAIL

   The change under the gate corrected ten false user-facing strings. Copy only,
   no logic: every line it authored is 100% covered, it moved three of the four
   blocked files *up*, and it made two of them smaller. It still cannot land, and
   no amount of correct work by its author changes that — the shortfall was never
   this change's, and is not closable by the change that trips over it.

   **Expected.** A project can state, reviewably and per path, the floor a file is
   actually held to — the way it can already state that a path is not code
   (``non_source``), that a path is measured but unjudged (``coverage_exclude``), and
   that a specific shortfall is accounted for (``coverage_baseline``). What it
   cannot say today is the one thing true here: *this file's ceiling is below the
   project floor, for a reason that is a property of the ecosystem and not of any
   change*.

   **Why the remainder is unreachable, measured rather than assumed.** The
   uncovered lines in all four files are SwiftUI view-body lines and ``Button``
   action closures. No suite in the scheme renders these bodies; and rendering one
   would not help, because rendering a view does not execute a ``Button`` action
   closure. The logic behind them is not untested — the sibling view model of the
   worst-blocked file sits at 96.04%. The gap is the boundary between a view and
   its model, and it moves only by relocating code across that boundary.

   **The ceiling is already measured, in this project's own record.** This is the
   strongest evidence in the entry, and it was written down before this defect was
   filed. ``scripts/coverage_floor_ratchet.txt`` carries an entry for
   ``TagDetailScreen.swift`` at 86.88, recorded 2026-08-20 after a session moved the
   file 66.30 -> 86.88 by relocating **every liftable action** into its view model
   and extracting two modifier types. Of the 47 lines still uncovered, 20 are
   ``Button`` action closures and 9 are player/fullscreen content closures a
   compliance rule forbids snapshotting. The entry's own comment states the
   conclusion:

      STRIKE THIS when the project rules on whether Button action closures are
      expected to be covered at all. That answer, not more tests, is what unblocks
      the file — and it is owed to every view file, not just this one.

   So a *complete* extraction refactor — the maximal correct response to the gate
   — tops out near 89 against a floor of 90. The gate is not asking for tests that
   have not been written. It is asking for a number that cannot be reached.

   **Every lever the manifest offers, followed to where it fails.**

   1. **``coverage_min``** — one global integer. Lowering it to admit one file
      weakens the floor for every file in the project, including the ones the
      floor is right about. It is the only knob that can change the number, and it
      cannot change it for one path.
   2. **``coverage_exclude``** — excludes from the FLOOR and keeps the MEASUREMENT,
      which is exactly the right shape, and it is the near miss that makes this
      entry worth filing rather than shrugging at. It fails on the consuming
      project's own stated criterion, which its manifest repeats once per existing
      entry: the path must be *"named exactly and never widened to its
      directory"*, and it is exempt *"only while every member stays pure wiring
      whose logic is tested in the component it forwards to"*, to be REVOKED *"if
      the file ever gains a branch, a computation or a stored decision"*. A
      ``Heap/Views/**`` glob violates the first outright. Naming the four files
      exactly violates the second: a view with a ``footerHint(for:)`` ``switch`` has
      precisely a branch and a computation. The two entries that criterion has
      admitted so far are a 54-line and a 504-line composition root of pure
      delegation — this is a different kind of file, and the difference is the
      point of the criterion.
   3. **``coverage_baseline``** (the ratchet) — per-file recorded shortfalls, and
      the closest thing to a fit. Its own header forbids exactly this use: *"Do
      not seed this file in bulk"*, and every entry needs *"a named work item and
      a measurement with a date"*. Both existing entries (70.99, 86.88) forgive a
      post-improvement REMAINDER. Entries at ``0.00`` and ``4.49`` would forgive whole
      files — a permanent exemption written in the syntax of a measurement — and
      four at once triples the file's population in a single commit. A ratchet
      that admits ``0.00`` has stopped ratcheting.
   4. **``non_source``** — drops the MEASUREMENT rather than the floor. A view file
      *is* compiled and *does* earn a row, so declaring it non-source is a false
      statement; worse, it is the one relaxation under which a suite that
      collapsed to zero would stop being visible. It is the right key for assets
      and the wrong key for code with a low ceiling.
   5. **``coverage_scope``** not-``touched`` — strictly worse, for the reason
      :need:`BUG_026 <BUG_026>` already recorded.

   **Therefore the remedy is an engine change, not a project change.** That is the
   same conclusion BUG_026 reached from a different starting point, and this is
   the next instance of it: three of the five levers above exist *because* of
   earlier entries in this batch, and the family keeps producing files the newest
   key still cannot describe.

   **Suggested shape — a starting point, explicitly NOT a settled design.** A
   per-path floor: let ``coverage_min`` be resolved per file rather than once
   globally, so a project can say "this path is held to 85" instead of choosing
   between 90 everywhere and an exemption. It preserves the property every
   relaxation in this batch has kept — the row is still required, the number still
   measured and reported, and the verdict names what it resolved differently and
   with which pattern.

   Two objections the fix session must rule on before writing any of it:

   - **It is a fourth knob in an area that already has three easily-confused
     ones.** ``core/manifest.py`` says so in its own words about the existing pair
     — *"a DIFFERENT question from ``coverage_exclude``, and the pair is easy to
     confuse"* — and a fourth key whose distinction from ``coverage_baseline`` is
     "a policy ceiling, not an accounted debt" is a distinction that will be got
     wrong in review. A per-path floor and a per-path baseline are separated by
     intent alone, and intent is not checkable.
   - **Whether the floor's design should be revisited instead.** Four keys
     answering four adjacent questions about one comparison may be the symptom
     rather than the shortfall. This entry poses that question; it does not
     answer it.

   **The batch itself is evidence.** With this entry ``unsatisfiable-gates`` stands
   at 16 members, 12 of them already ``fixed`` — twelve shipped patches to one gate
   family, most of them to this single rule. A sixteenth is worth reading as data
   about the design and not only as one more defect: each fix has correctly
   identified a class of file the floor should not judge, and the classes have not
   stopped arriving.

   **What this must NOT become, deliberately.** Any answer here must not become a
   route for genuine coverage debt. The distinguishing fact is a *ceiling*: the
   maximum a correct, complete effort can reach, measured and dated, below the
   floor. A file that is merely under-tested has no ceiling — it has a gap — and
   the floor should keep asking it the question. If the fix cannot make that
   distinction reviewable in a diff, it is a silencing wearing a policy's clothes,
   and the ratchet's existing discipline (a named work item, a dated measurement,
   strike-when-reached) is the standard to meet.

   **Related, and checked before filing.** :need:`BUG_026 <BUG_026>` is the nearest
   neighbour and the direct parent — it created both ``coverage_exclude`` and
   ``Adapter.executable_text`` — but it is a distinct mechanism: there the file
   either carried no measurable change at all (a comment-only diff) or belonged to
   a target the project holds to no floor. Here the file is production code, the
   change is real and measured, and the remainder is reachable-in-principle but
   not by this ecosystem's runner. :need:`BUG_036 <BUG_036>` (committed binary),
   :need:`BUG_039 <BUG_039>` (touched Xcode resource) and :need:`BUG_025 <BUG_025>`
   (stored-property-only type) are all the *no row at all* half of the family;
   this file has a row, and the row is honest. Not a duplicate of any of them.

   **Fix sketch / next step.** Start at
   ``core/rules.py::_coverage_meets_floor``, where ``floor`` is read once at line 1
   of the function and compared at ``below = {f: pct for f, pct in coverage.items() if float(pct) < floor}``; the per-path resolution would sit
   between those two, after the no-row guard and beside ``_excluded_detail`` /
   ``_ratcheted_against_baseline``, whose ordering discipline it must inherit
   exactly. The manifest key belongs with its three siblings in
   ``core/manifest.py``, whose comment block is where the distinction between all
   four has to be made legible or abandoned. Ordering is the whole of the safety
   here, as the ratchet's comment already records: asked LAST, and only of what
   the floor already rejected.

   **Repro attempt 2026-10-21:** does not reproduce on the main branch (a4cef88),
   and never did at the engine level — the mechanism predates the filing.
   Harness: ``coverage.meets_floor`` evaluated directly on a touched set of three
   measured files (global floor 90, ``coverage_scope: touched``): one file at 14.83%
   beside two neighbours at 95%. With no per-path statement it blocks ("1 file(s)
   below the 90% floor"). With the one carrier the current manifest offers for a
   per-path number — a ``coverage_baseline`` entry holding that path to 14 — the
   same file at 14.83% PASSES, the same file at 10% BLOCKS ("fell below a recorded
   coverage baseline"), and neighbours at 85% still BLOCK on the 90% floor. So a
   project can already state, in a reviewable diff, "this path is held to N while
   the floor binds its neighbours"; the per-path floor is
   ``coverage_baseline`` (c29a2af, 2026-08-19, five days before this entry), which
   the entry itself names as lever 3. Falsifiability: the same file raised to 92%
   passes with no configuration, and each inverted assertion (held file blocks;
   file at 10% passes; neighbours at 85% pass) went red — the harness reaches the
   verdict. By contrast ``coverage_exclude`` passes the file even at 0%, which is
   the floor-lost case. What survives is not a reproducible defect but the
   entry's own policy/design question: the only per-path carrier is worded and
   documented as a "pre-existing shortfall, ratcheted", the consuming project's
   baseline-file header forbids seeding it this way, and the verdict cannot tell
   a measured ceiling from an ordinary gap. That is a design proposal (a
   distinct, reviewable "ceiling" claim), not a symptom a test can be red on
   without asserting a key that does not exist. An earlier session's incomplete
   repro (d8313ac, unmerged) asserted the verdict sentence differs between two
   baseline entries fed identical input — red by construction, discarded. Test:
   tests/test_bug_054_repro.py::test_BUG_054_a_low_ceiling_file_can_be_held_to_its_own_floor
   (green, not committed to main).

   **Closed not_reproducible 2026-10-21** (user-approved): the per-path number is already carried by ``coverage_baseline``; the ceiling-vs-shortfall design question moves to IDEA_011.

   .. invariant-history:
      - from: filed
        to: not_reproducible
        reason: per-path floor already expressible via coverage_baseline (c29a2af, predates the 2026-08-24 filing); green proven meaningful by inverted assertions (repro attempt 2026-10-21, 2a035ee); remaining ceiling-vs-shortfall question filed as IDEA_011
        forced: false

.. bug:: The home_paths pattern matches a URL path segment
   :id: BUG_055
   :status: filed
   :severity: low
   :priority: low
   :area: core/privacy
   :slug: home-paths-matches-a-url-path-segment
   :affects: COMP_CORE_PRIVACY

   **Symptom.** ``PATTERNS["home_paths"]`` matches a URL path segment. A document
   that describes a route beginning ``/home/...`` — a web route, an API path, the
   path half of a URL — is read as a home directory carrying a username.

   **Expected.** ``home_paths`` means a home directory that embeds a username. A
   path segment inside a URL names a route on a server and identifies nobody.

   **Repro.** Verified by execution on master at f1d9066. The block below is
   the one place this entry writes the literal segment; everywhere else it is
   spelled with a placeholder, so one marker covers the whole entry and the
   gate stays live over the prose:

   .. invariant: allow-privacy

   | ``"see /home/search for the route"                -> home_paths /home/search``
   | ``"GET /home/search?q=x"                          -> home_paths /home/search``
   | ``"https://api.example.invalid/home/search"       -> home_paths /home/search``
   |     AND links (correctly)

   The third line is the useful one and is the reason this is filed rather than
   accepted: the same text produces a CORRECT ``links`` finding and a false
   ``home_paths`` one, from the same characters, which means the discriminator is
   present in the text and simply is not read.

   **Measured downstream, not here.** Reported 2026-08-24 by the maintainer of the
   consuming project that reported :need:`BUG_053 <BUG_053>`, from a scan of its whole
   corpus with the shipped scanner: one instance, ``adr-028`` line 1897, carrying the
   URL path ``/home/<segment>``. One instance is why this is filed at low priority
   rather than fixed on sight. The class is not rare, though — any project that
   documents a ``/home/...`` route hits it, and a documentation corpus is exactly
   where routes get written down.

   **Cause.** ``r"(?:/Users|/home)/(?!$)[A-Za-z0-9][A-Za-z0-9._-]*"`` tests the two
   prefixes and the shape of the segment after them, and nothing about what
   precedes the prefix. A URL authority to the left of it is invisible to the
   pattern.

   **Same class as BUG_053, which is the reason to file it together.** Four
   numbers with dots between them are not an address; ``/home/<segment>`` is not a home
   directory. Both are a pattern that names a CONCEPT and implements a SHAPE, and
   in both the context that settles it sits immediately to the left of the match.
   The fix that landed for BUG_053 is the precedent for what this one looks like,
   including where it goes — a guard on the entry, read from the text left of the
   match, narrow enough to state in a sentence.

   **Affected:** ``core/privacy`` — the pattern table.

   **Fix sketch, and the part that needs deciding.** Two candidate discriminators,
   and they are not equally safe:

   1. **A URL authority to the left.** ``https://host/home/<segment>`` — the scheme and
      authority prove the ``/home/`` is a route. Structural, safe, and the shipped
      ``links`` pattern already recognises the authority, so the two would agree
      about the same characters instead of disagreeing.
   2. **A bare route with no scheme** — ``GET /home/<segment>?q=x``. Harder, and where
      the real decision is. A query string or an HTTP verb to the left is
      suggestive and not conclusive, and a home path in prose is far more
      identifying than a false negative here is costly. Erring noisy is the
      standing call for this class (``links`` makes it about reverse-DNS
      identifiers, BUG_053 makes it about bare row numbers), so a fix that covers
      only case 1 and says so is defensible and is probably the right scope.

   **Test direction.** Both directions in one table, as
   ``TestIpAddressesAreAddressesAndNotRowNumbers`` and
   ``TestLinksNameANetworkHost`` are built: a URL-embedded ``/home/<segment>`` as a
   must-NOT-match, and a real home path in prose — including one inside a
   sentence that also carries a URL — as a must-match. The must-match direction
   passes today, so a one-directional fixture proves nothing.

.. bug:: A markdown link nested inside parentheses makes the index check report a present file as missing
   :id: BUG_056
   :status: filed
   :severity: medium
   :priority: high
   :area: domains/memorystore
   :batch: unsatisfiable-gates
   :slug: nested-link-makes-the-index-check-report-a-present-file-missing
   :affects: COMP_DOMAINS_MEMORYSTORE

   **Symptom.** ``MemoryStore._index_problems`` collects the set of files the memory
   index references with ``re.findall(r"\(([^)]+\.md)\)", text)``. The character
   class ``[^)]+`` contains neither ``]`` nor ``(``, so it crosses the ``](`` boundary of a
   link nested inside a parenthetical. The capture then begins at the OUTER ``(`` and
   is not a filename. Two findings follow from the one match, and they point in
   opposite directions:

   - ``index references missing file [remediation](project_clean_arch_remediation.md``
     — for a file that is on disk.
   - ``project_clean_arch_remediation.md is not in the index`` — because the real
     target was never recorded as referenced, the same file is ALSO reported
     unindexed while its row sits in the index.

   **Expected.** The referenced set is the set of link targets. A file that is on
   disk and named by a link in the index produces neither finding.

   **Repro.** Verified by execution on master at dd5b755, against the pattern
   lifted out of the method source with ``inspect.getsource`` rather than retyped,
   and then end to end through ``MemoryStore.check()``. Verbatim output:

   ::

      pattern lifted from the method source: r"\(([^)]+\.md)\)"

      A. captures on the nested line
         old: ['feedback_clean_architecture.md', '[remediation](project_clean_arch_remediation.md']
         new: ['feedback_clean_architecture.md', 'project_clean_arch_remediation.md']

      B. end to end, hand-curated index, both files present on disk
         on disk: ['MEMORY.md', 'feedback_clean_architecture.md', 'project_clean_arch_remediation.md']
         check().index:
           - project_clean_arch_remediation.md is not in the index
           - index references missing file [remediation](project_clean_arch_remediation.md

      C. end to end, index written by regenerate_index itself, nothing hand-edited
         check().index right after regenerate_index(): ['index references missing file see [beta](beta.md']

   The index line driving A and B is a real row from a consuming project's index,
   one entry among several hundred:

   ::

      - [clean_arch](feedback_clean_architecture.md) - clean arch + DI; views never touch concrete services ([remediation](project_clean_arch_remediation.md)).

   **C is the part that makes this a gate defect rather than a lint nit.** The
   index in C was not hand-curated. It was written by ``regenerate_index()`` in the
   line above the check, from two ordinary memory files, and ``check().index`` is
   non-empty on the artifact the library itself had just produced. ``regenerate_index``
   renders ``- [{name}]({file}) - {description}`` and the description is free text
   from the memory's own frontmatter; a description that parenthesises a link
   (``clean arch (see [beta](beta.md))``) is enough. So the remedy the failure
   advertises does not clear the failure.

   ``memory.index_current`` is ``warn`` in the standard and loose profiles and
   **``block`` in strict**. In a strict project this is a stop-event gate that
   ``invariant memory index`` cannot clear — the same shape as BUG_021, where a gate
   could not be cleared by the agent it names, which is why this carries the
   ``unsatisfiable-gates`` batch rather than being filed as a cosmetic false positive.

   **Not seen here, and that is why.** This repository sets ``memory: managed: false``, so all four memory rules return N/A against its own tree and the library
   has never run this check on itself. Reported 2026-08-24 by the maintainer of the
   consuming project that reported BUG_053 and BUG_055; two false positives were
   observed in that store, which hand-curates its index (``memory.index_generated: false``) and therefore writes exactly the nested shape the generator only reaches
   via a description.

   **Cause.** ``src/invariant/domains/memorystore.py``, ``_index_problems``, the
   ``referenced = set(re.findall(...))`` line. The pattern was written for the flat
   row ``regenerate_index`` emits and is applied to arbitrary index prose. ``[^)]+`` is
   the whole of it: it stops at a close paren and at nothing else, so the first
   ``(`` of a parenthetical and the ``)`` of the link inside it are read as one pair.

   **Affected:** ``domains/memorystore`` — the index check only. The generator, the
   schema check, the dead-link check (``LINK_RE``, a different and correct pattern)
   and the duplicate check are untouched.

   **Severity and priority.** Severity medium: nothing is destroyed and nothing is
   mis-recorded on disk; the harm is an instrument reporting a hygiene problem that
   does not exist, in both directions at once. Priority high: it is not
   intermittent and not clearable, it blocks under strict, and — the reason it
   should not wait — the failure text names ``invariant memory index`` as its fix,
   which is unconditional regeneration (``cli/main.py::cmd_memory_index`` calls
   ``regenerate_index()`` without consulting ``memory.index_generated``). An operator
   chasing a false "not in the index" line into that remedy overwrites a
   hand-curated index with a flat generated one, which is a real loss on a file
   that is deliberately not generated. That second-order hazard is adjacent and is
   NOT in this entry's fix scope; if it is worth a guard it is its own item.

   **Fix sketch.** Forbid the capture from containing the two characters that let
   it cross a link boundary: ``r"\(([^)(\]]+\.md)\)"``. Tested against the
   non-regressing shapes as well as the defect, same run as above:

   ::

      D. candidate fix on ordinary shapes
         plain link                               old=['plain_target.md']                            new=['plain_target.md']
         two plain links                          old=['one.md', 'two.md']                           new=['one.md', 'two.md']
         bare parenthesised name                  old=['bare_file.md']                               new=['bare_file.md']
         subdirectory target                      old=['sub/dir_file.md']                            new=['sub/dir_file.md']
         generated row with a parenthesised link  old=['alpha.md', 'see [beta](beta.md']             new=['alpha.md', 'beta.md']
         link then parenthetical link             old=['one.md', 'see [b](two.md']                   new=['one.md', 'two.md']

   Every shape the old pattern got right, the new one still gets right, including
   the two the generator emits; the two it got wrong both become correct. A regex
   is the minimum fix and not necessarily the right one — a real inline-link parse
   would also settle the adjacent class where a bare ``(some/path.md)`` in prose,
   naming no memory at all, is read as a reference and reported missing. That class
   is not exercised above and is not claimed fixed by this pattern.

   **Test direction, and whether existing coverage can carry it.** It cannot. The
   existing suite reaches ``_index_problems`` — ``tests/test_memorystore.py``,
   ``TestIndexGeneration``, six tests, two of them asserting exactly the two findings
   this defect fakes (``test_a_new_file_makes_the_index_stale`` for "is not in the
   index", ``test_index_pointing_at_a_deleted_file_is_reported`` for "missing file").
   But EVERY fixture in that class builds its index by calling
   ``store.regenerate_index()``, and the ``memo()`` helper defaults every description
   to the string ``a fact``. The corpus is closed: no fixture in the file writes an
   index the generator did not write, and no fixture supplies a description
   containing a link or a parenthesis. So the path is covered and the defect is
   invisible to it — the suite is green today and stays green under the fix. A new
   red test is required, not an assertion added to an existing one.

   Two cases, both new, and the second is the one that matters because it needs no
   hand-authored fixture at all:

   1. A hand-written index whose row nests a link inside a parenthetical, both
      files present: ``check().index == []``. Fails today with the two findings in B.
   2. ``memo(..., description="clean arch (see [beta](beta.md))")`` then
      ``regenerate_index()`` then ``check().index == []``. Fails today with C. This one
      belongs next to ``test_regenerating_makes_the_check_clean``, whose claim it
      directly contradicts.

   A must-match control belongs in the same table — a genuinely missing target
   inside a nested link must still be reported — because both existing arms pass
   today and a one-directional fixture would prove only that the pattern still
   matches something.

.. bug:: The review boundary reads content from the tree the merge is about to replace, so a session that removes a violation can never merge
   :id: BUG_057
   :status: fixed
   :severity: high
   :priority: high
   :area: core/rules
   :batch: unsatisfiable-gates
   :slug: review-boundary-reads-the-tree-the-merge-replaces
   :affects: COMP_CORE_RULES

   **Symptom.** ``privacy.clean`` at the review boundary reads file CONTENT off the
   filesystem at ``_repo_root(ctx)``, which is ``git.toplevel(ctx.cwd)``. In the
   session machine that gate is the **exit** gate of ``reviewed``, and a session
   transition evaluates the source state's exit gates plus the target's entry
   gates (``domains/session.py::gates_for``). ``reviewed`` is left for ``merged`` by one
   edge, so the gate's single firing on that machine is ``invariant session merge``
   — which ``session.ff_merge_target`` requires be run from the MAIN checkout,
   because merging inside the branch's own worktree merges it into itself and
   exits zero. The gate therefore reads the working tree the merge is about to
   REPLACE, never the branch tip the merge publishes.

   Two consequences follow from the one wrong read, and they point in opposite
   directions:

   - **It refuses what it should pass.** A session whose purpose is REMOVING
     pre-existing personal data from a tracked file is refused, on the exact lines
     its commit corrects. The remediation the gate exists to encourage is the one
     class of work it cannot let through.
   - **It passes what it should refuse.** A document the branch ADDS is not in the
     main checkout at all, and the reader's ``FileNotFoundError`` arm treats absence
     as "removed by this change, so nothing of it lands in the merged tree". At
     the merge that premise is false: the file is absent because it has not been
     published YET. It is not read, it is not counted as scanned, and the gate
     returns N/A with the sentence *"no path this session would put into master is
     inside privacy scope"* — of a path it had just decided WAS in scope.

   **Expected.** The subject at a boundary is what that boundary publishes. At
   ``session review``, inside the session's own worktree, that is the working tree
   including uncommitted work — correct today, and it must stay correct. At
   ``session merge``, run from a checkout that is not on the session's branch, it is
   the branch tip.

   **Repro.** Self-contained, executed against a pristine export of master at
   ``a367e3d`` rather than a working tree, driving the real rule through
   ``_REGISTRY["privacy.clean"].fn`` with the empty payload a transition hands it.
   One tracked doc, one ``links`` match on master, one branch that removes it. The
   fixture text is never printed: a report that quotes what it found is a second
   copy of it. Verbatim output:

   ::

      FIXTURE  one tracked doc; master holds a links match, session/x removes it
         master blob            : 1 finding(s)
         session/x blob         : 0 finding(s)
         what the merge publishes = the session/x blob

      A. inside the session worktree  (invariant session review)
         _repo_root                     : wt
         paths read                     : ['docs/guide.md']
         findings in what it read       : 0
         privacy.clean                  : PASS - scanned docs/guide.md: no matches

      B. from the main checkout       (invariant session merge)
         _repo_root                     : proj
         paths read                     : ['docs/guide.md']
         findings in what it read       : 1
         privacy.clean                  : FAIL - 1 personal-data match(es) in content being written down
         detail                         : docs/guide.md:3:5  [links]  ht████████████

      C. from the main checkout, no declared file set
         _repo_root                     : proj
         paths read                     : []
         findings in what it read       : 0
         privacy.clean                  : NA - no path this session would put into master is inside privacy scope

         the branch ADDS docs/added.md carrying 1 finding(s)
      D. inside the session worktree, branch ADDS a leak
         _repo_root                     : wt
         paths read                     : ['docs/added.md', 'docs/guide.md']
         findings in what it read       : 1
         privacy.clean                  : FAIL - 1 personal-data match(es) in content being written down
         detail                         : docs/added.md:3:5  [links]  ht████████████

      E. from the main checkout, branch ADDS a leak
         _repo_root                     : proj
         paths read                     : []
         findings in what it read       : 0
         privacy.clean                  : NA - no path this session would put into master is inside privacy scope

   A and B are the same branch, the same session record and the same rule,
   differing only in ``ctx.cwd``. D and E are the must-fail direction: the leak is
   screened from inside the worktree and invisible from the main checkout.

   **C is a second finding, and it is load-bearing for the fix.** ``_changed_set``
   reads ``ctx.cwd`` too, so at the merge BOTH layers are wrong — the set as well as
   the content. ``git.branch_files`` runs ``diff --name-only base...HEAD``, and in the
   main checkout HEAD *is* the base, so the branch-commits leg returns an empty
   list. Empty is not ``None``, so ``answerable`` stays true and the emptiness is
   silent. The only surviving source of paths there is the session's DECLARED file
   set. A project whose sessions do not declare files gets N/A at every merge,
   which is BUG_047 reappearing on the boundary BUG_047 created. A fix that
   corrects only the content read leaves that half standing.

   **Observed in a consuming project, which is where it was found.** A docs-only
   session whose whole purpose was scrubbing pre-existing findings ahead of
   widening its privacy scope. ``invariant privacy scan`` over all 97 ``.rst`` files
   under ``docs/`` in the session worktree: **0 findings**. ``invariant session review``: clean. ``invariant session merge`` from the main checkout: refused with
   ``[BLOCK] privacy.clean: 3 personal-data match(es)``, naming the three lines that
   commit fixes. Evaluating the rule directly against that session's own record,
   read-only, on the pristine ``a367e3d`` engine, reproduces both verdicts out of
   the one unchanged state:

   ::

      session worktree (review)    -> PASS: scanned docs/architecture/adr/adr-028-inspect-driven-universal-capture.rst, docs/architecture/intake_sources.rst, docs/architecture/v2_browser.rst: no matches
      main checkout (merge)        -> FAIL: 3 personal-data match(es) in content being written down
                                      docs/architecture/adr/adr-028-inspect-driven-universal-capture.rst:1897:30  [home_paths]  /h██████████; docs/architecture/intake_sources.rst:6389:6  [links]  ht████████████; docs/architecture/v2_browser.rst:2179:3  [links]  ht████████████

   Scanning the two versions of those three files with ``PrivacyScanner`` and that
   project's seven configured patterns: main-checkout working tree, 1 finding
   each; the branch's blob of the same path, 0 findings each. The session declared
   seven files; the three in the report are exactly the three that fall inside its
   ``paths.docs``, so the scoping is right and only the tree is wrong.

   **Cause.** ``core/rules.py::_review_boundary_content_in_scope``, the loop body:

   ::

          root = _repo_root(ctx)
          for path in changed.paths:
              absolute = path if Path(path).is_absolute() else Path(root) / path
              if not _in_privacy_scope(ctx, absolute, scope):
                  continue
              found.append((_rel(absolute, Path(root)), absolute.read_text("utf-8")))

   The assumption is stated in the function's own docstring, in one sentence:

      Content is read from the working tree, which is what the merge publishes.

   True inside the session worktree, false at the merge, and nothing in the
   function distinguishes the two. The distinction is not a new insight here:
   ``_session_worktree(ctx) or ctx.cwd`` already exists and is already used by two
   other rules — the coverage gate's exemption reader at line 2630 and the
   deleted-file leg at line 2451 — for exactly this reason, that a gate can be
   evaluated from a checkout which is not the work's. This function reaches for
   ``_repo_root`` instead. It is the shape its own docstring names about BUG_012:
   one insight implemented in one rule and not propagated to the rules beside it.

   **Affected:** ``core/rules``, the no-payload route of ``privacy.clean`` only. The
   commit boundary is untouched and correct — ``_staged_content_in_scope`` reads
   ``git.staged_blob``, a blob and not a file, in the repository the command names
   (BUG_048). The pipeline machines that also list ``privacy.clean`` run inside the
   session worktree, where the working-tree read is right. The session machine is
   the one that does not.

   **Severity and priority.** Severity **high**. The false-positive half wedges a
   whole class of session with no clean route past it: ``privacy.clean`` is the sole
   member of ``HELD_OUT_OF_STANDDOWN``, so a blanket stand-down does not cover it,
   and the only exit is ``invariant override privacy.clean --reason``, which records
   an exception against a session that is REMOVING personal data — a record that
   says the opposite of what happened. The false-negative half is the graver one
   on the merits: this is, in the codebase's own words, "the one gate whose
   subject cannot be recovered afterwards", and at the merge edge it does not read
   new documents at all. Not **critical**, and the reason is precise rather than
   charitable: the commit boundary still screens every added file at the commit
   that created it, so nothing reaches history unscreened by *any* boundary — at
   the merge the gate is redundant here rather than the last line. Priority
   **high**: it is deterministic and not intermittent, it blocks under every
   profile (``loose`` keeps ``privacy.clean`` blocking), and the work it blocks is
   privacy remediation, which is the work most costly to defer.

   **Batch.** ``unsatisfiable-gates`` fits, and on the reported symptom it is the
   only fit: a correct and complete change cannot clear the gate, and no manifest
   key changes that — the same shape as BUG_021, where a gate could not be cleared
   by the agent named as its remedy. The honest caveat is that the second half
   belongs to ``inert-instruments``: a boundary reporting N/A over content it never
   read is BUG_047's defect at the boundary BUG_047 created. One entry carries one
   batch; this paragraph is where the other half is recorded, and a fix that
   closes only the batch this is filed under has closed half of it.

   **Fix sketch, checked against the surrounding code.** When the checkout is not
   on the session's branch, read the branch's committed blob —
   ``git.file_at(ctx.cwd, branch, rel)`` — instead of the filesystem. Both helpers
   exist at ``a367e3d``: ``git.current_branch`` at ``core/git.py`` line 69 and
   ``git.file_at`` at line 260, the latter already used by ``core/rules.py`` at line
   2639. ``file_at`` returns ``None`` for every unreadable reason at once, which for a
   path in the changed set means the branch does not carry it — deleted, or never
   there — and nothing of it lands in the merged tree, so ``None`` joins the
   existing missing-file arm rather than failing closed.

   **What a naive version of that breaks, which is the important part.** The
   working-tree read is DELIBERATE, and three existing tests depend on it:
   ``test_a_leak_not_yet_committed_blocks_the_review_too``,
   ``test_a_modification_to_a_tracked_file_is_in_the_subject`` and
   ``test_an_untracked_file_is_not_in_the_subject`` all pin that the index and the
   working tree are in the subject at the review boundary. Swapping every read for
   the branch tip would stop screening uncommitted work at ``session review``, where
   the gate DOES run in the right tree and where uncommitted work is exactly what
   it is for. So the branch-tip read has to be conditional on the checkout not
   being the session's, and that condition — not the read — is what a reviewer
   must be able to see in the diff. ``_session_worktree(ctx)`` is the other
   candidate and is a worse one here: it reads a working tree that may hold
   uncommitted changes the merge will NOT publish, and it returns ``None`` once the
   worktree is torn down. The blob is the more exact answer to "what does this
   merge publish"; the worktree path is the more consistent with the two rules
   that already solved the same problem. That choice is the fix session's, and
   this entry does not make it.

   The ``_changed_set`` half needs its own decision, stated here rather than assumed
   away: reading the branch's file list from the main checkout requires either the
   same branch-aware ref or the session's worktree, and until that is decided, a
   merge in a project that declares no files is screened over an empty set.

   **Test direction, and whether existing coverage carries it.** It does not.
   ``_review_boundary_content_in_scope`` is well covered — ``tests/test_rules.py``,
   ``TestPrivacyScreensTheReviewBoundary``, 12 tests, every one of them reaching the
   helper through the real rule. But the corpus is closed on the one axis that
   matters: **11 of the 12 pass ``cwd=wt``**, the session's own worktree, and the
   12th (``test_a_tool_call_is_still_judged_as_a_tool_call``) passes ``cwd=main`` with
   a payload, so it never enters the no-payload route at all. No fixture anywhere
   in the file evaluates the review boundary from a checkout that is not on the
   session's branch. The suite is green today and stays green under the fix; the
   defect is invisible to it. A new red test is required, and none of the 12 can
   be turned into it by adding an assertion.

   The ``repo`` fixture already supplies what is needed — a main checkout plus a
   linked worktree on ``session/x`` — so this costs no new harness. Three cases, and
   the second and third are what keep the fix honest:

   1. The violation is committed on master BEFORE the session, the branch takes it
      and fixes it, the rule evaluated with ``cwd=main`` and the session naming the
      file. Expect a pass. Fails today, on the text the branch fixed.
   2. The must-fail control: the branch ADDS a document carrying a violation,
      evaluated with ``cwd=main``. Expect a refusal naming the path. Fails today by
      returning N/A — so this arm is red before the fix too, and a one-directional
      fixture would prove only that the reader still reads something.
   3. The regression guard: an uncommitted leak in the session worktree, evaluated
      with ``cwd=wt``. Expect a refusal. Green today, and it is the arm a naive
      branch-tip swap turns red.

   **Related, and checked before filing.** :need:`BUG_047 <BUG_047>` is the direct
   parent: it created this function, and this is its assumption failing on the one
   boundary of the six that runs somewhere else. Not a duplicate — there the route
   did not exist, here it exists and reads the wrong subject.
   :need:`BUG_048 <BUG_048>` is the same class of mistake on the sibling half of the
   same rule, "a clean scan of the wrong tree", and its fix is the precedent for
   resolving the subject before reading it. :need:`BUG_012 <BUG_012>` is the
   propagation failure this repeats. :need:`BUG_027 <BUG_027>` is the nearest severity
   comparison, and the difference is that there the write was screened by no
   boundary at all; here the commit boundary still screens it.

   .. rubric:: Fixed 2026-08-24

   Both halves, in one change.

   **The content read.** ``_review_boundary_content_in_scope`` now reads from the
   tree the merge would publish: the working tree while the checkout is on the
   session's branch, the branch tip otherwise, via ``git.file_at``. The
   discriminator is ``git.current_branch(ctx.cwd) != branch``, so the review
   boundary inside the worktree is byte-for-byte unchanged and uncommitted work
   is still screened there.

   **The path set.** ``git.branch_files`` gained an explicit ``tip``, and
   ``_changed_set`` passes the session's branch when the checkout is not standing
   on it. Without this the content fix alone would still screen nothing at a
   merge, because ``base...HEAD`` from the main checkout is empty and reads as an
   answer rather than as an unanswerable question. That was the fail-OPEN half
   recorded above, and it silently affected every gate sharing the union, not
   only this one.

   **Tests.** ``TestPrivacyScreensWhatTheMergeWouldPublish``, four cases: a branch
   that FIXES a leak can merge (the wedge); a branch that ADDS one is still
   refused (the must-fail control); the branch is the subject even when the
   session declares no files (the fail-open half, which returned a vacuous pass
   whose own message said it had screened nothing); and an uncommitted leak
   inside the worktree is still screened (the BUG_047 regression guard that a
   naive unconditional swap turns red).

   Full suite green afterwards, 1560 passing, no failures.

   .. invariant-history:
      - from: fixed
        to: fixed
        reason: 'backfill: status was set by hand before transitions were recorded; fixed in 6898740'
        forced: true

.. bug:: tests/conftest.py does not strip INVARIANT_HOST_SESSION_ID, so a bare pytest run in a shell that exports it can collide (BUG_035, one variable over)
   :id: BUG_058
   :status: filed
   :area: tests/conftest.py, domains/session
   :severity: medium
   :priority: medium
   :batch: inert-instruments
   :affects: COMP_DOMAINS_SESSION

   **Symptom.** ``tests/conftest.py`` has an autouse fixture,
   ``_no_ambient_session_id``, that deletes ``INVARIANT_SESSION_ID`` from the test
   environment because a bare ``uv run pytest`` typed in a shell that exports it
   collides ``SessionDomain.start()`` with the real, live session (BUG_035,
   measured: 25 false failures). ``SessionDomain.resume()``, added in the
   session-outlives-host increment, reads a second environment variable with the
   same shape and the same fallback pattern:

   ::

      host_session_id = host_session_id or os.environ.get("INVARIANT_HOST_SESSION_ID")

   ``SessionDomain.start()`` already reads the same variable (line 721 as of this
   filing) and was not covered by BUG_035's fix either — only
   ``INVARIANT_SESSION_ID`` was. ``conftest.py`` clears the session-id variable but
   not the host-session-id one, so a suite run in a shell that has
   ``INVARIANT_HOST_SESSION_ID`` exported (this project's own agent host sets it)
   can have ``start()`` or ``resume()`` silently resolve to the ambient host id
   instead of the test's own, in any test that does not pass
   ``host_session_id=`` explicitly.

   **Where it was noticed.** The session-outlives-host increment's own resume
   tests hit exactly this seam: ``test_resume_without_a_host_leaves_the_spent_binding_alone``
   needs the variable UNSET to exercise the "no host to bind to" branch, and was
   written with a local ``monkeypatch.delenv("INVARIANT_HOST_SESSION_ID", raising=False)``
   rather than widening the autouse fixture, deliberately, to keep that increment's
   diff scoped to session-outlives-host rather than to this suite-wide hazard.

   **Expected.** The same treatment BUG_035 gave ``INVARIANT_SESSION_ID``: an
   autouse fixture in ``tests/conftest.py`` that deletes
   ``INVARIANT_HOST_SESSION_ID`` for every test, so the suite's default posture does
   not depend on what the invoking shell happens to have exported, and individual
   tests that need the variable set it explicitly via ``monkeypatch.setenv``.

   **Not yet reproduced against this repository's own suite** — BUG_035 was
   filed only after a red run demonstrated the collision; this entry is filed on
   inspection (the identical fallback pattern, the identical missing autouse
   coverage) rather than on a demonstrated failure here, and reproduction is
   owed before a fix is credited.

   **Related.** :need:`BUG_035 <BUG_035>` is the direct precedent: same variable
   shape, same fallback pattern, same fix shape, one environment variable over.

.. bug:: invariant doctor ends the caller's host binding, because its shim check fires a real session-end
   :id: BUG_059
   :status: fixed
   :area: hosts/claude_code, cli/main
   :severity: medium
   :priority: high
   :affects: COMP_HOSTS_CLAUDE_CODE, COMP_CLI_MAIN

   **Symptom.** Running ``invariant doctor`` marks the caller's own host binding
   SPENT. The session is not ended, abandoned or torn down — but ``host_ended``
   flips to ``true``, so the session stops being attributable to the host that is
   still working in it, and every subsequent hook resolves to no session at all.

   **Cause.** ``doctor``'s "hook shims actually run" check executes all six shims as
   subprocesses. One of them is ``session-end``, and the shim does exactly what the
   host would do: ``invariant hook session-end``, which calls
   ``SessionDomain.end_host_session`` against the LIVE state root. The check means
   "can these scripts execute and return a verdict"; what it does is deliver a
   real lifecycle event.

   **Repro** (attested on master ``7028c8e``, and again on the session branch, with
   the same result on both):

   1. ``invariant init`` in a scratch repo, commit, ``invariant session start x``
      with ``INVARIANT_HOST_SESSION_ID`` exported.
   2. Read the session document: ``host_ended`` is ``false``.
   3. ``invariant doctor``.
   4. Read it again: ``host_ended`` is ``true``.

   **Why it is worth more than it looks.** The session-outlives-its-host model
   gives ``host_ended`` a second job: a spent binding is what
   ``SessionDomain.resumable`` admits, so ``doctor`` does not merely mislabel a
   session — it moves the caller's live session into the set that is offered for
   somebody else to pick up. ``doctor``'s own sessions row then reports the caller's
   session as "parked here", which is the diagnosis command inventing the
   condition it is reporting.

   **Found by** eyeballing the ``doctor`` rows added in the CLI increment of the
   session-outlives-host branch; the rows are the messenger, not the cause.

   **A second hazard the same repro exposed** is filed separately as **BUG_061**
   and is not part of this item. Where the shim on PATH is an OLDER build of the
   library than the process being diagnosed — the normal state of this repository
   while a feature branch is unmerged, since the editable install points at the
   main checkout — the hook subprocess writes the session record back through ITS
   dataclass, and every field that build does not have is silently deleted. It is
   recorded here because this repro is where it was seen; it is filed there
   because it is a defect of the record round-trip rather than of ``doctor``, and
   two defects under one id is how one of them gets half-fixed and forgotten.

   **Repro proof:** tests/test_bug_059_repro.py::test_BUG_059_doctor_does_not_spend_the_callers_host_binding,
   committed 462da8f on session branch session/f5ec91e4 (2026-10-21) — asserts
   that the caller's session document reads ``host_ended`` false both before and
   after ``invariant doctor``; today it reads False -> True. Driven end-to-end
   (init, commit, ``session start`` with a synthetic ``INVARIANT_HOST_SESSION_ID``,
   ``doctor``) against a tmp project and a pinned tmp state root, with the shims
   exec'ing this checkout's source. Doctor's own "sessions parked here" row
   reports the caller's session in the same run. Wrapped in strict xfail.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: strict-xfail repro red in 462da8f (session branch)
        forced: false
      - from: reproduced
        to: fixed
        reason: fixed in 4b8254e
        forced: false

.. bug:: Two domain refusals print the exact sequence the host binds a session on
   :id: BUG_060
   :status: filed
   :area: domains/session
   :severity: low
   :priority: medium
   :affects: COMP_DOMAINS_SESSION

   **Symptom.** Two refusals in ``domains/session.py`` print a ``session <id> (``
   sequence, which is the exact pattern ``hosts/claude_code._bind_claimed_session_id``
   searches the whole tool response for in order to bind a host session:

   ::

      SessionDomain.resume:              "this host session already owns session
                                          {other.id} ({other.name}, {other.state})."
      SessionDomain.adopt_host_session:  "this host session already owns session
                                          {other.id} ({other.name})."

   Confirmed by feeding the first string to ``_bind_claimed_session_id``, which
   returns ``ab12cd34``.

   **Why it has not bitten.** ``other`` is by construction the session
   ``by_host_session(host_session_id)`` returned — bound to THIS host, and live. So
   the resulting ``adopt_host_session(other.id, this_host)`` is a no-op rebind to
   the owner it already has. The near-miss is benign *today*, for a reason that
   lives two modules away from either line.

   **Why it is still worth a fix.** ``cmd_session_resume``'s docstring states the
   contract as a property of the whole command — "every refusal below names
   sessions in the ``<name>  [<state>]`` shape ... which has no such sequence in
   it" — and the claim is true only of the refusals raised in ``cli/main.py``. The
   domain's own refusals travel the same path to the same parser, and nothing
   tests them for it. The safety currently rests on an accident of which session
   the message happens to name; the stated design rests on the message shape.

   **Expected.** Both refusals name the session in the ``<name>  [<state>]`` shape
   the ambiguity messages already use, and a test asserts ``_STARTED_ID`` finds
   nothing in either — the same test ``tests/test_cli.py::TestSessionResume``
   already applies to the CLI-side refusals.

   **Found by** the session-outlives-host host increment, while widening the
   claim pattern to admit ``session resume``. Not fixed there: the lines are in
   ``domains/session.py``, which that increment does not touch, and the message
   shape is increment 1's to choose.

.. bug:: An older build silently deletes every record field it does not know, because from_dict drops unknown keys and to_dict never writes them back
   :id: BUG_061
   :status: fixed
   :area: core/store,domains/session,domains/pipeline
   :severity: high
   :priority: high
   :affects: COMP_CORE_STORE, COMP_DOMAINS_SESSION, COMP_DOMAINS_PIPELINE

   **Symptom.** A build that predates a field on a persisted record DELETES that
   field when it writes the record back. ``Session.from_dict`` filters unknown keys
   and ``to_dict`` emits only declared ones, so a load-modify-save through an older
   dataclass is a silent, total loss of everything that dataclass has not heard
   of. Nothing warns, nothing records it, and the command that did it succeeded.

   The exposure is live in this repository as it stands. The editable install on
   ``PATH`` points at the MAIN checkout, so every hook subprocess and every bare
   ``invariant`` call runs ``master``'s dataclass while ``plan``, ``plan_touched``,
   ``resumed_count`` and ``hosts`` exist only on the session-outlives-host branch. A
   session's whole deliverable plan can be deleted by a command that reported
   success.

   **Reproduced** during increment 3 of that branch and recorded there: measured,
   a ``plan``, ``plan_touched``, ``resumed_count`` and ``hosts`` all vanished from a
   resumed session, and ``host_ended`` reverted. The observation was written into
   the BODY of BUG_059, whose titled defect is a different one — ``invariant doctor`` spending the caller's host binding — and this item exists so that two
   defects do not share one id and one of them get half-fixed and forgotten.

   **Cause, stated as the asymmetry it is.** Increment 1 designed the FORWARD
   direction and tested it: a record written before a field existed loads under
   new code with that field at its default, so a new field costs no migration
   (``tests/test_domains.py::test_a_record_written_before_the_field_existed_still_loads``).
   The REVERSE direction was never designed. Forward compatibility is what
   ``from_dict``'s filter buys; backward compatibility is what it spends, and
   nothing said so.

   **Scope: which records share it.**

   - ``domains/session.Session`` — the live exposure, four fields deep.
   - ``domains/pipeline.Pipeline`` — the identical idiom on the identical store.
     No field is currently at risk only because this branch adds none; the next
     pipeline field lands straight into it.
   - ``core/items.Item`` — NOT exposed through the backend this project uses. The
     markdown backend parses every non-reserved frontmatter key into ``fields`` and
     writes it back, and the sphinx-needs backend documents the same promise for
     directive options. ``JsonBackend`` does drop unknown top-level keys, but its
     documents are written by this library alone and ``fields`` is already the
     declared bucket for anything else.
   - ``domains/stats.StatRecord`` and the ``core/stats`` value types drop unknown
     keys too, and are deliberately left alone: the storage layout calls
     ``stats/<host-id>.json`` derived and rebuildable, so a key lost there is
     recoverable by re-deriving it. A session document has no such second source.

   **Expected.** ``from_dict`` preserves the keys it does not recognise and
   ``to_dict`` writes them back at the top level, so a write-back by any build is
   additive rather than destructive.

   **The alternative, and why it is not this.** The other shape is a version
   stamp on the record and a refusal on downgrade. It fails the case that
   actually occurs: the older writer is a hook subprocess, which cannot usefully
   refuse — a hook that errors turns every tool call in the repository into a
   failure, and one that proceeds has still lost the data. Preservation needs no
   cooperation from the caller and no decision at the point of the write.

   **What the fix cannot do.** It does not rescue a record from a build that is
   already installed, because that build has neither the fields nor the contract.
   It stops the NEXT asymmetry, not this one; this one closes when the branch
   merges. Any fix has this property, including the version stamp.

   **Repro attempt 2026-10-20:** passes on the main branch; fixed by d71fa35
   (2026-09-07, "A record survives a build that does not know its fields"). Harness:
   a session and a pipeline started through ``SessionDomain``/``PipelineDomain``, an
   unknown top-level key written onto both on-disk documents, then the real
   load-modify-save paths (``session add_files``, ``pipeline record_docs_read``);
   asserts the key is still on disk after the save. Green on main. Falsifiable:
   the inverted assertion fails on main, and the same test run against the tree at
   d71fa35^ fails on both halves with "session record lost ... on write-back" and
   "pipeline record lost ... on write-back". The contract is now documented in
   ``docs/architecture/state_model.md`` ("The round trip, in both directions") and
   covered by ``tests/test_store.py::TestPreservesUnknownKeys`` and
   ``tests/test_domains.py::TestARecordSurvivesAnOlderBuild``.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'red against d71fa35^: tests/test_bug_061_repro.py fails with ''session record lost ... on write-back'' (and the pipeline half the same way); repro attempt 2026-10-20'
        forced: false
      - from: reproduced
        to: fixed
        reason: fixed in d71fa35 (PreservesUnknownKeys on Session and Pipeline); same test passes on main; regression tests tests/test_store.py::TestPreservesUnknownKeys, tests/test_domains.py::TestARecordSurvivesAnOlderBuild
        forced: false

.. bug:: A session stays isolated through every commit, so review refuses it
   :id: BUG_062
   :status: fixed
   :severity: medium
   :priority: medium
   :area: domains/session
   :batch: inert-instruments
   :slug: session-never-leaves-isolated
   :residue: DECISION 2026-10-20 (user-approved): Implement the trigger — the first edit in an isolated session's worktree advances it isolated -> working (a hook path that does not advance a session that is not isolated). Do not correct the diagram instead.
   :affects: COMP_DOMAINS_SESSION

   **Symptom.** A session's state never leaves ``isolated``, no matter how much work
   happens in it. The session that observed this ran eight development increments
   on its branch, produced seven commits, declared 37 files and drove eight
   pipelines to ``committed`` — and ``invariant session list`` still showed it as
   ``isolated``. ``invariant session review`` then refuses:

   ::

      session <id> is 'isolated'; review is not legal from there.
      Legal from 'isolated': working, abandoned

   The operator has to run ``invariant session transition working`` by hand before
   the session can be reviewed or merged.

   **Expected.** ``docs/architecture/state_model.md``'s session diagram draws the
   edge as ``isolated --> working: first edit``, and
   ``data/machines/session.yaml`` describes ``working`` as "Edits and local commits".
   A session that has made edits and local commits should be in ``working`` without
   an operator naming the edge, or the diagram should not claim a trigger that
   does not exist.

   **Repro.** Isolate a session, edit and commit inside its worktree, then
   ``invariant session review`` — it refuses from ``isolated``. Confirmed at eb79f4a.
   Grep confirms the write site is absent: ``_advance`` is called at exactly four
   sites in ``domains/session.py`` — ``isolate`` (to ``isolated``), the generic
   ``transition`` command path, ``push`` and ``teardown``. No rule, no CLI command and
   no hook advances a session to ``working``; only an explicit
   ``invariant session transition working`` does.

   **Suspected cause.** The edge was specified and never implemented. ``working`` is
   reachable in the machine (``isolated.next: [working, abandoned]``) but nothing
   fires the documented trigger, so the state is only ever reached manually.

   **Why it matters past the inconvenience.**

   1. Every session that has ever merged either had ``transition working`` run by
      hand or was forced past the edge. The documented "first edit" trigger has
      never existed.
   2. The session record misreports what happened. ``isolated`` means "a worktree
      and branch exist and belong to this session" — a reader of
      ``invariant session list`` sees that for a session with seven commits in it.
      The state field is an instrument that records nothing about progress.
   3. It is the D16 / BUG_022 shape: an authoritative diagram a reader could
      enforce against the code. Someone reading the state model would reasonably
      believe the edge is automatic and write code that depends on it.

   **Open design question — record, do not resolve here.** Two defensible fixes,
   and the entry deliberately presupposes neither:

   - **Implement the trigger.** The edit hook advances the session on the first
     successful edit — ``session.isolated_before_edit`` already fires there and is
     the natural place to notice a first edit. Makes the state honest, but puts a
     state *write* on a hook path that today only reads and records.
   - **Correct the diagram.** Make the transition explicitly manual, and say so in
     the state model, so ``working`` means "the operator declared work started"
     rather than "an edit happened".

   Whichever is chosen, the deliverable includes the diagram and the code agreeing.

   **Affected.** ``domains/session.py`` (``SessionDomain.isolate`` / ``_advance``; the
   ``isolated`` state is written there and never advanced),
   ``docs/architecture/state_model.md`` (the session diagram, "Domain: session"),
   ``data/machines/session.yaml`` (the ``isolated``/``working`` state descriptions), and
   possibly ``hosts/claude_code.py`` if the trigger lands at the edit hook.

   **Fix sketch / next step.** Decide the design question first — it selects the
   failing test. If the trigger is implemented, the red test is a session that
   edits a file in its worktree and is in ``working`` afterwards, with the hook path
   asserted not to advance a session that is not ``isolated``. If the diagram is
   corrected instead, the red test is a doc-vs-machine consistency assertion of
   the same shape BUG_022's fix used. Sibling: BUG_022 (fixed) — same class of
   defect, an authoritative doc stating behaviour the code does not implement.

   **Found by** the session-outlives-host session, at the point of trying to
   review itself. Not fixed there: the fix needs a design decision the session did
   not own.

   **Repro proof:** tests/test_bug_062_repro.py::test_BUG_062_first_edit_in_isolated_worktree_leaves_session_working, committed e98857b (2026-10-21, session branch) — an isolated session whose worktree receives an Edit through the host's pre-tool-use/post-tool-use hook pair is still ``isolated`` afterwards, not ``working``. Wrapped in strict xfail; recorded red via ``pipeline test --phase reproducing``.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: strict-xfail repro red at e98857b (session branch)
        forced: false
      - from: reproduced
        to: fixed
        reason: fixed in e8104e9
        forced: false

.. bug:: session merge reads privacy.allow from the manifest the merge replaces, so an allow entry cannot land with the content it allows
   :id: BUG_063
   :status: filed
   :severity: medium
   :priority: medium
   :area: cli/main,core/rules
   :batch: unsatisfiable-gates
   :slug: merge-screens-against-the-pre-merge-manifest
   :affects: COMP_CLI_MAIN, COMP_CORE_RULES

   **Symptom.** A commit that adds a ``privacy.allow`` entry to the manifest
   TOGETHER with content that relies on it passes every gate inside the session
   worktree and is refused at ``invariant session merge``. Observed 2026-10-03 on
   the session named ``project-agnostic-manual``, commit 552e1fc: the commit added
   one ``privacy.allow`` literal — this repository's own hosting address — to
   ``.claude/invariant.yaml``, and a new ``docs/guide/installation.md`` carrying that
   address on four lines. In the worktree, ``invariant pipeline gates --state committed`` reported ``privacy.clean: ok``, ``invariant privacy scan`` over every
   touched document was clean, the pipeline reached ``committed`` and the session
   reached ``reviewed``. From the main checkout, ``invariant session merge`` refused
   with ``[BLOCK] privacy.clean: 4 personal-data match(es)``, naming exactly those
   four lines. The branch landed with ``--force``.

   **Expected.** The merge gate's privacy scan evaluates the incoming content
   against the manifest AS IT WILL BE after the merge — the manifest at the tip
   of the session branch, which is what ``session review`` already scanned against
   in the worktree. Failing that, the gate at least reports that the manifest is
   itself part of the change and names that as the reason its verdict differs
   from the review's. A gate whose verdict depends on which checkout evaluates it
   is one the operator cannot predict, and the two checkouts are not a choice:
   ``session.ff_merge_target`` REQUIRES the merge be run from the main checkout.

   **Repro (deterministic, any adopted repository).** Start a session and
   isolate. In the worktree, add a ``privacy.allow`` entry for a placeholder address
   under a reserved test domain, and a document under ``paths.docs`` that carries a
   matching URL. Drive the pipeline to ``committed`` and the session to ``reviewed``
   inside the worktree — both pass, because both read the worktree's manifest.
   Then ``invariant session merge`` from the main checkout: refused by
   ``privacy.clean`` on the lines the new allow entry covers.

   **Suspected cause.** Diagnosed by the compliance reviewer at the merge, not yet
   pinned by a failing test. ``Context.__init__`` does
   ``Manifest.load(manifest_start or self.cwd)``, so a command run from the main
   checkout resolves its manifest from master's HEAD — the OLD manifest, without
   the allow entry — and ``privacy.clean`` scans the incoming branch's content
   against it. BUG_057 moved the CONTENT read at this boundary from the tree the
   merge replaces to the branch tip; the MANIFEST read still comes from the tree
   the merge replaces. It is the same wrong-checkout split, one input over, and
   the same shape its own entry names about BUG_012: one insight implemented in
   one read and not propagated to the read beside it. The commit boundary is not
   affected — it runs inside the worktree, where ``self.cwd`` resolves the branch's
   manifest.

   **Why medium rather than high.** The engine's documented route past the gate
   exists and works: ``--force`` on the merge, recorded on the session as an
   override. What the defect costs is that it fires on EVERY self-referential
   privacy change — any commit whose allow entry and allowed content travel
   together, which is the natural way to write one — and so trains ``--force`` as a
   routine step at the one boundary ADR-001 (D17, D21) says it must not become
   one. ``privacy.allow`` is an operator-authority value (manifest.md "Resolution
   order", layer 5), and this makes a layer-5 decision unexpressible without an
   override.

   **Related observation, recorded here rather than as a second item.** ``invariant session merge --force`` accepts no ``--reason`` flag, unlike ``pipeline advance --force --reason`` and ``session transition --force --reason``, so the override
   this defect forces carries no stated reason. ADR-001 D25's constraint is that
   every route past a gate is RECORDED; a recorded override with no reason is
   recorded but not explained, and the one this bug produces is exactly the kind
   whose reason ("the manifest in this change is the one that allows this
   content") a later reader needs. A fix here may close that gap or leave it; it
   is noted so the gap is not rediscovered.

   **Precedent.** BUG_053's residue records the same shape — "the change could
   not be screened by its own fix… recorded as an override with that reason" —
   for a pattern change rather than an allow entry. The commonality is that the
   engine screening the boundary is not the engine, or here the manifest, the
   change ships. BUG_040 shares the ``Manifest.load(manifest_start or self.cwd)``
   call site for a different defect (a hook rooted in another project); a fix
   that changes how that line resolves should be read against both.

   **Affected.** ``cli/main.py`` (``Context.__init__``, the manifest resolution) and
   ``core/rules`` (``privacy.clean``'s no-payload review-boundary route — the reader
   BUG_057 corrected, which takes its patterns and allow list from
   ``ctx.manifest``). The session machine's ``reviewed -> merged`` edge
   (``docs/architecture/state_model.md`` "Domain: session"; ``session.ff_merge_target``
   is what pins the evaluating checkout). The scope the gate applies is
   ``docs/architecture/manifest.md`` "Privacy scope"; the layer the allow entry
   sits in is "Resolution order". The command is ``docs/architecture/cli.md``
   ``session merge``. ``core/manifest`` is not at fault — it loads what it is handed;
   choosing WHICH checkout to hand it is the caller's job (the same division
   BUG_048 drew for ``core/git``).

   **Fix sketch / next step.** Reproduce first, in the shape BUG_057's repro used:
   drive ``privacy.clean`` through the registry with the empty payload a transition
   hands it, from a main-checkout ``ctx`` whose session branch adds an allow entry
   and matching content. Red is the merge-side FAIL where the worktree-side
   verdict is PASS; the control is that content NOT covered by the branch's allow
   entry still FAILS from both sides, so the fix does not become "trust whatever
   manifest the branch brings". Then decide the design: (a) at the review
   boundary, when the checkout is not on the session's branch, load the manifest
   from the branch tip (``git.file_at``, as BUG_057's content read does) and scan
   against THAT — scoped to ``privacy.*``, since a branch must not be able to swap
   the gate set it is evaluated under (the AUD-02/03 argument ``pipeline start``
   already makes); or (b) keep the main-checkout manifest and have the gate
   detect that the manifest is in the changed set, naming it in the verdict. (a)
   makes review and merge agree; (b) only makes the disagreement legible. Whether
   a ``--reason`` for ``session merge --force`` rides along is for whoever fixes it.

.. bug:: The chore lane cannot land a LICENSE-only change without --force: it is gated on a design agent and on docs.updated
   :id: BUG_064
   :status: fixed
   :severity: medium
   :priority: medium
   :area: core/machines,core/rules
   :batch: unsatisfiable-gates
   :slug: chore-lane-gated-on-design-and-docs
   :affects: COMP_CORE_MACHINES, COMP_CORE_RULES

   **Half (b) fixed 2026-10-17** (commits 0411e2a, 99f48ee, merged from session
   39231a47). Status was left ``filed`` then because half (a) was open; it is now
   ``reproduced`` (2026-10-21) on the strength of half (a)'s repro alone — see the
   **Repro proof** block at the end.

   - **(b) ``docs.updated`` reading non-architecture docs and repository metadata
     as source: FIXED on main.** ``docs.updated`` now holds out three classes of
     path from "source changed": prose (``.md`` / ``.rst`` / ``.txt``); extensionless
     repository metadata by whole name (``LICENSE``, ``LICENCE``, ``COPYING``,
     ``NOTICE``, ``AUTHORS``, ``CONTRIBUTORS``, ``CODEOWNERS``, plus ``LICENSE-*``,
     ``LICENCE-*``, ``COPYING-*``); and ``methodology.gates.non_source`` globs. Its
     verdict names each ``non_source`` match. It deliberately does NOT reuse the
     coverage gate's full unmeasured set, because configuration and markup
     (``.yaml`` / ``.json`` / ``.toml`` / ``.html`` / ``.css``) can change architecture, so
     a machine YAML changed alone still blocks. The coverage gate now also treats
     the metadata names as non-source.
   - **Evidence for (b).** On 2026-10-12, Heap session 23fd0870, a
     docs-methodology change touching only ``docs/backlog.rst`` and
     ``docs/release_plan.rst``, was blocked by ``docs.updated``.
   - **(a) ``agents.design_agent_ran`` on the chore lane (``core/machines.py``,
     ``AGENT_ROLE_GATES``): still OPEN and unreproduced.** No red test yet. The
     fix sketch for (a) below still applies.

   **Symptom.** A ``chore`` pipeline whose only change was a new ``LICENSE`` file at
   the repository root could not reach ``committed`` without a forced override.
   Observed 2026-10-07 in this repository, the session that landed 4af6e15. The
   compliance reviewer had committed the change, ``invariant pipeline advance reviewing`` passed, and then ``invariant pipeline advance committed`` was blocked
   by two gates, neither of which describes this change:

   ::

      [BLOCK] agents.design_agent_ran: no design agent ran in this pipeline
              dispatch one of: feature-architect
      [BLOCK] docs.updated: source changed but no architecture doc did

   **Expected.** A ``chore``-lane pipeline for a ``LICENSE``-only change reaches
   ``committed`` with no override. Both halves are what the architecture docs
   already say:

   1. ``docs/architecture/state_model.md`` (the docs-first gates section, "Injection
      is by role, not by state name") says a methodology that declares no design
      state, which is ``chore``, "is asserting there is no design content". The
      ``chore`` machine itself says "Mechanical work with no design content". A gate
      asking whether a design agent ran has no question to ask there.
   2. A license file is not source. A change that touches only non-source paths
      should not read as "source changed" to ``docs.updated``.

   **Repro.** In this repository (its manifest raises ``agents.design_agent_ran``
   to ``block``):
   ``invariant session start <name>``, then ``invariant pipeline start --methodology chore --task "..."``, then ``invariant session isolate --execute``. Add a
   ``LICENSE`` file in the worktree. Have the review agent run and commit it.
   ``invariant pipeline advance reviewing`` passes, and ``invariant pipeline advance committed`` blocks on both gates above. With the shipped profiles, where
   ``agents.design_agent_ran`` is ``warn``, only ``docs.updated`` blocks. The design
   gate still fires; it just prints a warning instead of blocking.

   **Suspected cause.** These are two separate mechanisms that showed up on one
   edge. They were read from the code at 4af6e15 but are not yet pinned by a
   failing test.

   - **Design gate.** ``core/machines.py``, ``AGENT_ROLE_GATES`` (around line 83) hangs
     ``agents.design_agent_ran`` on the ``landing`` role's ENTRY set, and
     ``load_pipeline_machine`` injects it into every pipeline machine. ``chore``
     declares a ``landing`` state (``committed: roles: [landing]``), so it gets the
     gate even though it has no ``design`` state. BUG_021 / ADR-003 D2 moved the gate
     from the design state to landing. Before that move it applied only where a
     design state existed. After it, it applies everywhere. ``_require_a_landing_state``'s
     own docstring states the intent, "chore declares no design state because it
     is asserting there is no design content", but that intent was only carried
     through for ``docs.read_architecture`` (``DOCS_FIRST_GATES["design"]``), which
     is still keyed to the design role. ``_agent_role_gate`` in ``core/rules.py`` only
     checks whether ``agents.roles.design`` is configured. It never checks whether
     the pipeline's machine has a design state.
   - **docs.updated.** In ``core/rules.py``, ``_docs_updated`` treats every changed
     path outside ``paths.docs`` as source (``source_changed = [p for p in changed if not _is_within(root / p, docs_root)]``). It does not consult the non-source
     classification that ``coverage.meets_floor`` uses: ``_UNMEASURED_SUFFIXES``,
     ``methodology.gates.non_source``. Even that classification would not catch this
     case, because ``LICENSE`` has no suffix. So a fix needs a rule for
     extensionless repository metadata (``LICENSE``, ``NOTICE``, ``CODEOWNERS``, ...),
     or has to send it through ``methodology.gates.non_source``. It also has to
     decide whether ``docs.updated`` should share the coverage gate's
     classification at all. BUG_012's fix moved both gates onto ``_changed_set`` and
     explicitly unified the "what changed" question. The "is it source" question
     was left with two answers.

   **Why medium.** The documented override route works: ``--force --reason``,
   recorded on the pipeline. The cost is that this lane exists so mechanical work
   does not need a bypass (``chore.yaml``: "easier than disabling the pipeline
   entirely"). If its first non-code change needs ``--force`` to land, ``--force``
   becomes a routine step for that lane, and ``state_model.md`` warns that people
   then reach for ``none`` and lose every gate.

   **Related observation, already filed.** After the forced advance, ``invariant session review`` refused because the session was still ``isolated``, and
   ``invariant session transition working`` had to be run by hand. That is BUG_062.
   It is a different mechanism (no trigger for the ``isolated -> working`` edge),
   so it is not repeated here. This run is one more instance of it.

   **Workaround used (operator-approved).** ``invariant pipeline advance committed --force --reason "..."`` (1 override recorded), then ``invariant session transition working``, ``session review``, ``session merge`` and teardown. LICENSE
   landed as 4af6e15.

   **Affected.** ``core/machines.py`` (``AGENT_ROLE_GATES``, ``load_pipeline_machine``,
   the ``_require_a_landing_state`` docstring), ``core/rules.py``
   (``agents.design_agent_ran`` via ``_agent_role_gate``; ``docs.updated``;
   ``_UNMEASURED_SUFFIXES`` / ``_looks_like_docs`` /
   ``_excluded_as_declared_non_source`` as the classification it lacks),
   ``data/machines/chore.yaml``. Documented in ``docs/architecture/state_model.md``
   (docs-first gates, "Agent-role gates") and ``docs/architecture/manifest.md``
   (``methodology.gates.non_source``).

   **Fix sketch / next step.** Reproduce first with two red tests, one per
   mechanism, so they can be fixed and proven separately:
   (a) ``load_pipeline_machine(manifest, "chore")`` has ``agents.design_agent_ran`` on
   ``committed``'s entry gates, while the expected result is that it is absent. The
   control is that ``tdd`` still has it on its landing state. The likely fix is to
   inject the design-agent gate only into machines that declare a ``design``-role
   state, so it matches ``docs.read_architecture``. Making the rule return N/A when
   the pipeline's machine has no design state also works. Either way this is an
   ADR-003 D2 boundary and should be stated there.
   (b) ``docs.updated`` over a changed set of only ``LICENSE`` FAILS, while the
   expected result is N/A or clean. The control is that a changed ``.py`` file with
   no doc change still FAILS. The design question is whether
   ``docs.updated`` should share one source/non-source classifier with
   ``coverage.meets_floor`` (and ``methodology.gates.non_source``), and how
   extensionless repository metadata gets classified.

   **Repro proof (half a):** tests/test_bug_064_repro.py::test_BUG_064_a_chore_landing_is_not_gated_on_a_design_agent, committed c529e3d (2026-10-21, session branch) — a ``chore`` pipeline in ``reviewing``, with a design role configured, ``agents.design_agent_ran`` at ``block`` and the review agent on record, is refused ``reviewing -> committed`` by ``agents.design_agent_ran``: "no design agent ran in this pipeline". Asserts the landing edge's verdicts (what ``pipeline advance committed`` evaluates), not ``AGENT_ROLE_GATES`` itself. Wrapped in strict xfail; recorded red via ``pipeline test --phase reproducing``. Control observed by hand: ``tdd``'s ``committed`` also carries the gate (the fix must keep it there), and recording a design agent turns the chore verdict non-blocking, so the assertion can go green. Half (b) is not covered by this proof; it was fixed in 0411e2a, 99f48ee.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'half (a) only: strict-xfail repro red at c529e3d (session branch); half (b) already fixed in 0411e2a, 99f48ee'
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Fixed in ca6102f (half a: design-agent gate injected only into machines declaring a design state); half (b) fixed earlier in 0411e2a and 99f48ee'
        forced: false

.. bug:: item transition rewrites a multi-line RST entry: a wrapped option is reflowed, an inline role becomes a field, the trailing blank line is lost
   :id: BUG_065
   :status: fixed
   :severity: high
   :priority: high
   :area: core/items
   :slug: transition-serializer-corrupts-multi-line-rst-entries
   :affects: COMP_CORE_ITEMS

   **Symptom.** In a consuming project whose backlog is a reStructuredText
   document on the sphinx-needs backend, ``invariant item transition`` rewrote a
   bug entry in three wrong ways. Observed twice on 2026-10-17, both on
   ``filed -> reproduced`` transitions:

   - (a) a multi-line ``:area:`` option value is reflowed onto one line;
   - (b) a ``:doc:`` role that opens a continuation line of ``:area:`` is split out
     into a bogus separate ``:doc:`` option line;
   - (c) the blank line between the end of the entry and the next section
     heading is deleted, which breaks the document as reStructuredText.

   **Expected.** A transition changes the status (and appends history) and
   nothing else. Every other option, its line structure, and the blank-line
   separation from whatever follows the directive survive byte-for-byte.

   **Repro.** A sphinx-needs backlog with an entry whose ``:area:`` value wraps,
   the continuation line beginning with an inline role such as ``:doc:``, and a
   section heading directly after the entry separated by one blank line. Run
   ``invariant item show`` on it first: the parser already reports a separate
   ``doc`` field and a truncated ``area`` before any write. Then run
   ``invariant item transition <id> reproduced`` and diff the file.

   **Suspected cause.** A READ defect first, a write defect second. The option
   parser treats any continuation line that starts with ``:word:`` as a new option,
   so an inline role at the start of a wrapped value is mis-read as an option
   name. The serializer then writes back what the parser produced: a single-line
   area, a new ``doc`` option, and an entry block re-emitted without the trailing
   blank line that separated it from the next heading. This is the residue of
   :need:`BUG_007 <BUG_007>`, whose fix taught the parser that a wrapped value
   continues the option above it; that fix evidently does not cover a
   continuation line that itself looks like an option. The trailing-blank-line
   loss is a separate serializer property (re-emitting a directive without
   preserving its terminator) and may share a cause with the insertion work in
   :need:`BUG_006 <BUG_006>`.

   **Impact.** Every transition on a multi-line entry silently corrupts the
   backlog, and closing a bug will repeat it. The workaround in use is a hand
   repair after every transition — the same committed-shaped intermediate state
   BUG_006 was filed against. Silent, so a consumer that does not diff after
   each transition does not know.

   **Affected.** ``core/items`` — the work-item model and its pluggable store
   backends (see overview.md, the ``items`` row), specifically the sphinx-needs
   option parser and the save path ``invariant item transition`` goes through.

   **Fix sketch / next step.** Red tests first, two of them, against the
   sphinx-needs backend on a fixture shaped like the repro: (1) a parse test —
   ``show`` on an entry whose wrapped ``:area:`` continuation begins with ``:doc:``
   must return one ``area`` field holding both lines and no ``doc`` field; (2) a
   round-trip test — ``transition`` on that entry followed by a diff must change
   only the status and history lines and must keep the blank line before the
   next heading. A continuation line is distinguishable from a new option by its
   indentation relative to the option block, not by its leading characters; the
   serializer should preserve the original option text for any field it did not
   change rather than re-flowing it.

   **Provenance.** Reported 2026-10-17 by a consuming project from two live
   transitions. Not reproduced in this repository yet.

   **Repro proof:** tests/test_bug_065_repro.py::test_BUG_065_show_reads_the_whole_wrapped_area_and_no_doc_field and ::test_BUG_065_transition_changes_only_the_status_line, committed 50fd322 (2026-10-21) on the session branch — on a sphinx-needs fixture whose entry wraps ``:area:`` over three lines, the last opening on a ``:doc:`` role, with a neighbouring entry before it and a section heading one blank line after it. The read test asserts ``show`` returns one ``area`` holding all three lines and no ``doc`` field; today it returns ``area='core/items, the store backends, see'`` and :literal:`doc='\`architecture/overview\` for the owner'`. The write test asserts a real ``WorkItemDomain.transition(filed -> reproduced)`` removes no original line but the status line; today the diff also removes the three ``:area:`` lines (replaced by a one-line ``:area:`` and a ``:doc: ...`` option line) and the blank line before the next heading. All three reported symptoms (a), (b), (c) fire. Both wrapped in strict xfail; recorded red via ``pipeline test --phase reproducing``. The fix deletes the markers and keeps the assertions.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: strict-xfail repro 50fd322 fails as reported
        forced: false
      - from: reproduced
        to: fixed
        reason: fixed in 983e0ab
        forced: false

.. bug:: A repro-only session cannot land its proof: no terminal short of fixing, and a strict expected failure records as a pass
   :id: BUG_066
   :status: fixed
   :severity: medium
   :priority: high
   :area: data/machines,core/rules
   :batch: unsatisfiable-gates
   :slug: repro-only-session-cannot-land-its-proof
   :residue: DECISION 2026-10-20 (user-approved): Do BOTH — add a landable terminal after reproducing on the bugfix machine (gated on a recorded red run, accepted by pipeline.complete and the review boundary), AND teach the red-phase recorder that a strict expected failure that fails is a red run.
   :affects: COMP_CORE_RULES

   **Symptom.** A session whose only job is to prove a bug real — the
   ``bug-reproducer`` role, which must commit its red test wrapped in a strict
   expected-failure marker and must never fix — has no lawful way to land that
   proof on the base branch:

   - Under the ``tdd`` machine the session cannot reach ``reviewed``:
     ``pipeline.complete`` refuses because the pipeline is still in ``red``, and the
     only way out of ``red`` is forward into ``green``, which is fixing.
   - Under the ``bugfix`` machine no terminal is reachable from ``reproducing``
     except through ``fixing -> verifying -> committed``, or by declaring the bug
     ``not_reproducible`` or ``abandoned`` — both of which are false for a proof that
     fired.
   - Recording the expected-failure-wrapped proof with
     ``invariant pipeline test --phase red`` (or the machine's own red phase) counts
     as a PASS, because a strict expected failure that fails exits clean. So
     ``test.new_test_fails`` reads "the run recorded in the red phase passed". The
     gate is only satisfiable once the fixer removes the wrapper — which
     feature-architect.md documents as its first edit, so on the fixer's side that
     half works as designed.

   **Expected.** A repro-only session can end lawfully with its committed red
   test merged to the base branch, the bug at ``reproduced``, and the fix left to a
   later session — which is exactly the split the work-item lifecycle and the
   bug-reproducer role describe.

   **Repro.** In any consuming project: start a ``bugfix`` (or ``tdd``) pipeline for
   a filed bug, commit a strict expected-failure test that fails on the symptom,
   record it with ``invariant pipeline test --phase <red phase>``, then try to reach
   a terminal state and ``invariant session merge`` without editing production
   code. Every route is refused or requires a false status.

   **Impact.** A proof cannot reach the base branch until its fix does. In
   practice proofs are stranded on unmerged session branches — seen at least
   twice in the reporting project — so the next session either cannot find the
   red test or re-derives it, and the work-item backlog says ``reproduced`` about a
   test that is not on the base branch.

   **Suspected cause.** Two gaps, both confirmed only from the reported
   behaviour: (1) neither shipped machine models "proved, not fixed" as a
   landable outcome — the bug-reproducer role was written against the work-item
   lifecycle, which has a ``reproduced`` state, while the pipeline machines have no
   counterpart; (2) the red-phase recorder reads only the run exit status, so it
   cannot tell a strict expected failure (the test failed, as intended) from a
   passing test.

   **Affected.** ``data/machines`` (``tdd.yaml``, ``bugfix.yaml``; see state_model.md,
   the methodology table) and ``core/rules`` (``test.new_test_fails``,
   ``pipeline.complete``), plus the red-phase recording path behind
   ``invariant pipeline test``.

   **Fix sketch / next step.** Options, not ranked:

   - **A landable terminal after ``reproducing``** — for example ``reproduced`` on
     the ``bugfix`` machine, gated on a recorded red run, accepted by
     ``pipeline.complete`` and therefore by the review boundary.
   - **Teach the red-phase recorder about strict expected failures** — when the
     selector names the repro test and the run reports it as an expected failure
     under a strict marker, record the run as red. This keeps the wrapper honest
     and makes ``test.new_test_fails`` satisfiable before the fix.

   Probably both: the first lets the session end, the second lets the evidence
   say what actually happened. Red test first: a ``bugfix`` pipeline in
   ``reproducing`` with a strict expected-failure run recorded must have some
   lawful edge to a terminal the merge accepts.

   **Related:** :need:`BUG_030 <BUG_030>` made ``test.new_test_fails`` satisfiable from
   the bugfix machine at all (the phase-name half); this is the polarity half —
   the run is in the right phase but reads as a pass.
   :need:`BUG_029 <BUG_029>` is the same rule refusing an unparseable failure count.
   :need:`BUG_021 <BUG_021>` is the closest sibling in shape: a gate the actor it
   applies to cannot clear.

   **Provenance.** Reported 2026-10-17 by a consuming project after repeated
   repro-only sessions. Not reproduced in this repository yet.

   **Repro proof:** ``tests/test_bug_066_repro.py``, committed d988e84 (2026-10-20)
   on session branch ``session/a8e177ea`` — NOT yet on the base branch, because the
   merge is exactly what this bug blocks (see below). Two tests, each wrapped in
   ``pytest.mark.xfail(strict=True)``; the fixer deletes both markers first.

   - ``test_BUG_066_a_reproduced_bugfix_pipeline_has_a_lawful_terminal`` — walks
     every state reachable from ``reproducing`` without entering ``fixing``, drops the
     terminals that would be false for a proof that fired (``not_reproducible``,
     ``abandoned``), and asks ``pipeline.complete`` (the entry gate of the session's
     ``reviewed`` and ``merged``) about each remainder with a red run on record.
     Today: ``reaches no terminal pipeline.complete accepts without fixing; terminals reachable without fixing: ['abandoned', 'not_reproducible']``.
   - ``test_BUG_066_a_strict_expected_failure_that_fails_is_a_red_run`` — builds a
     real python project whose only test is a failing strict xfail, records it
     through ``PipelineDomain.run_tests(phase="reproducing", selectors=[...])``, and
     evaluates ``test.new_test_fails``. Today: ``the run recorded in the reproducing phase passed`` (the recorded run: ``passed: True, exit_code: 0, executed: None, failed: None`` — pytest prints ``1 xfailed``, which the adapter counts as
     neither passed nor failed).

   Live corroboration in the reproducing session itself: recording the wrapped
   proof with ``invariant pipeline test --phase reproducing --selector tests/test_bug_066_repro.py`` recorded ``passed``, ``pipeline gates`` then read
   ``to fixing: test.new_test_fails: the run recorded in the reproducing phase passed``, and the only other legal destinations were ``not_reproducible`` and
   ``abandoned``.

   Note for the fixer: half (2) needs more than flipping ``passed``.
   ``_why_not_proof`` also requires a parsed non-zero ``failed`` count, and the python
   adapter does not count ``xfailed`` today, so a recorder that only flipped the
   polarity would trade "passed" for "no failure count could be parsed".

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: two strict-xfail repros red in d988e84 on session/a8e177ea
        forced: false
      - from: reproduced
        to: fixed
        reason: fixed in c89bc0a
        forced: false

.. bug:: privacy.clean blames -m for an unreadable -F file that the same command writes
   :id: BUG_067
   :status: fixed
   :severity: low
   :priority: medium
   :area: core/rules
   :affects: COMP_CORE_RULES

   **Symptom.** When ``privacy.clean`` refuses a ``git commit -F <file>`` because the
   file could not be read at scan time, its remedy names the wrong cause if the
   same Bash command is the one that creates the file. Observed 2026-10-20 in this
   repository on:

   ::

      f=<abs>/msg.txt && printf '...' > "$f" && git commit -q -F <abs>/msg.txt docs/backlog/IDEA_008.md

   The PreToolUse refusal read:

   ::

      [BLOCK] privacy.clean: the commit message could not be scanned
      -F names '<abs>/msg.txt', which could not be read at scan time. The message would land in history unscanned.
      try: write the message inline with -m '<message>'

   The hook runs before the command, so a file the command itself writes (a ``>`` or
   ``>>`` redirect, ``tee``, a ``cat > f <<EOF`` heredoc) can never exist at scan time.
   The refusal does not say so; it points only at ``-m``, and because a denied
   PreToolUse cancels the whole call, the file is still missing on a retry and the
   next refusal looks like a different fault. It has cost real turns more than
   once; the project memory "Commit messages need a file, not stdin" exists
   because of it.

   **Expected.** The same refusal, still a BLOCK — failing closed is correct and
   must stay. When the command writes the ``-F`` target, the reason and the ``try:``
   line name that cause: the file is written by this same command, which the hook
   runs before; write it in a separate call first, then commit — with ``-m`` as the
   alternative. Every other unreadable ``-F`` case (a missing file, ``-F -`` from
   stdin) keeps its current wording.

   **Repro.** In a project whose ``privacy.scope`` includes ``commit_messages``, feed
   the pre-tool-use hook a Bash command of the form
   ``printf 'msg' > <abs>/m.txt && git commit -F <abs>/m.txt`` where ``<abs>/m.txt``
   does not exist. The verdict is FAIL with detail "could not be read at scan
   time" and the single fix "write the message inline with ``-m '<message>'``".

   **Suspected cause.** ``_read_commit_message_file`` in ``src/invariant/core/rules.py``
   reports any ``OSError`` with one generic sentence, and the ``commit_messages``
   branch of the privacy rule attaches one fixed remedy
   (``fixes=("write the message inline with -m '<message>'",)``). Neither looks at
   the rest of the command for a write to the ``-F`` path.

   **Affected.** ``privacy.clean``, ``commit_messages`` scope — documented under
   "Privacy scope" in ``docs/architecture/manifest.md`` (AUD-09 fail-closed
   convention).

   **Fix sketch / next step.** Red test first: a hook-level case feeding the
   command above and asserting the detail and ``try:`` line name the same-command
   write and the separate-call fix, plus guard cases that a plain missing file and
   ``-F -`` keep today's wording. Then, with the command's shell tokens already in
   hand, detect a redirect (``>``, ``>>``, ``>|``), a ``tee [-a]`` argument, or a
   heredoc-fed ``cat >`` whose target resolves (same cwd / ``-C`` resolution as the
   ``-F`` read) to the ``-F`` path, and return a cause-specific detail and fix pair.
   Adjacent but out of scope: an unexpanded ``$VAR`` in the ``-F`` path is the other
   trap the memory records; a follow-up could name it the same way. Sibling on
   the refusal-wording surface, different mechanism: BUG_060. No schema involved.

   **Repro proof:** ``tests/test_cli.py::TestBUG_067_DashFWrittenByTheSameCommand::test_BUG_067_refusal_names_the_same_command_write_and_a_separate_call``,
   committed a10585b (2026-10-20) on the session branch — feeds ``invariant hook pre-tool-use`` a Bash payload ``printf 'msg' > <abs>/m.txt && git commit -F <abs>/m.txt`` (file absent, default ``privacy.scope``) and asserts the
   ``privacy.clean`` block says "same command" and that a ``try:`` line says
   "separate". Today the block reads "-F names ``'<abs>/m.txt'``, which could not be
   read at scan time" with the single ``try: write the message inline with -m '<message>'``, so the first assertion fails. Wrapped in a strict xfail; the
   fixing session deletes the marker and keeps the assertions. The red run is
   recorded in the bugfix pipeline (``--runxfail``, 1 executed, 1 failed). Guards
   in the same class, not xfail and green today: the same-command case is still
   a ``privacy.clean`` BLOCK with the file absent; a plain missing file keeps
   "could not be read at scan time" and the ``-m`` fix; ``-F -`` keeps "reads the
   message from stdin, which a hook cannot see" and the ``-m`` fix.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red test a10585b on the session branch
        forced: false
      - from: reproduced
        to: fixed
        reason: fixed in 7f86e58
        forced: false

.. bug:: session handoff launches its successor with the parent Claude session's environment: broken colors, transcripts off
   :id: BUG_068
   :status: fixed
   :severity: medium
   :priority: high
   :area: hosts/claude_code_launch
   :affects: COMP_HOSTS_CLAUDE_CODE_LAUNCH

   **Symptom.** A session started by ``invariant session handoff`` runs with the
   environment of the Claude Code session that launched it. Observed 2026-10-20
   with ``ps eww`` on the two sessions one handoff launched (one per project): both
   carry ``CLAUDECODE=1``, ``CLAUDE_CODE_CHILD_SESSION=1``, the parent session's
   ``CLAUDE_CODE_SESSION_ID`` (one ancestor's id, the same in both),
   ``CLAUDE_CODE_BRIDGE_SESSION_ID``, ``CLAUDE_CODE_SSE_PORT``,
   ``CLAUDE_CODE_MESSAGING_TOKEN`` / ``CLAUDE_CODE_MESSAGING_SOCKET``,
   ``CLAUDE_CODE_EXECPATH``, ``CLAUDE_CODE_SESSION_ATTENDED`` and
   ``COLORTERM=truecolor``, alongside ``TERM_PROGRAM=Apple_Terminal``. Two visible
   effects:

   - **Broken colors.** The launched Terminal window draws highlight blocks behind
     every line of text and full-width colored bars.
   - **Transcripts off.** The launched session's status line reads "Transcript
     saving is off — inherited CLAUDE_CODE_CHILD_SESSION marker — restart with
     CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1". With no transcript,
     ``claude_code_telemetry`` has nothing to read, so ``invariant stats`` silently
     knows nothing about any handed-off session.

   **Expected.** A handed-off session starts as a fresh, top-level host session:
   none of the launcher's host markers, its own session id, transcripts saved,
   and the color depth the terminal it runs in actually renders — while keeping
   the user's own environment (PATH from the login shell, their exports).

   **Repro.** From inside a Claude Code session on macOS, with Terminal.app not
   running, run ``invariant session handoff`` with a launch. In the new window, run
   ``env | grep -E 'CLAUDE|COLORTERM'`` (or ``ps eww`` on the launched ``claude``) and
   observe the markers above; the successor's status line shows transcript saving
   off and its rendering shows the color artefacts.

   **Suspected cause.** ``TerminalLauncher`` runs ``osascript`` as a child of the
   launching session, so ``osascript`` carries that session's environment. When
   Terminal.app is not already running, the AppleScript ``tell application "Terminal"`` starts it from that process, and Terminal.app takes the
   environment with it; every window it opens afterwards inherits it, including
   windows the user opens by hand. In the observed case Terminal.app had parent
   pid 1 and had been running since an earlier handoff, so a polluted Terminal.app
   stays polluted until it is quit. Then:

   - the inherited ``CLAUDE_CODE_CHILD_SESSION=1`` makes the successor treat itself
     as a child session and turn transcript saving off;
   - the inherited ``COLORTERM=truecolor`` makes it emit 24-bit color, which
     Terminal.app on macOS 15 (Darwin 24) does not render — Terminal.app does not
     set ``COLORTERM`` itself, so the value can only have come from the launcher.

   **Affected.** ``hosts/claude_code_launch`` — ``LaunchPlan`` and
   ``TerminalLauncher`` (:need:`ADR-005 <DEC_ADR_005>`
   D3; the ``hosts/`` section of the
   :doc:`architecture overview </architecture/overview>`). Shipped in 2d8204a,
   extended in 33b1bd1 (IDEA_008).

   **Fix sketch / next step.** For the architect to decide; two directions, not
   exclusive:

   - (a) The typed shell line removes the inherited host markers before ``claude``
     — for example ``env -u CLAUDECODE -u CLAUDE_CODE_… -u COLORTERM claude …`` —
     without discarding the rest of the user's environment. This fixes the
     successor even when Terminal.app is already polluted.
   - (b) Stop handing the session's environment to Terminal.app: start it through
     LaunchServices (``open -a Terminal``, which does not pass the caller's
     environment) before the AppleScript runs, and/or run ``osascript`` itself with
     the markers removed. This keeps a fresh Terminal.app clean, but cannot clean
     one that is already running.

   A repro test can assert on the ``LaunchPlan`` (the shell line strips the markers)
   and on the environment ``TerminalLauncher`` passes to its subprocess, with no
   window opened, through the existing ``_handoff_launcher`` seam. Which variables
   count as "host markers" should be a named, owned list in the host module, not
   scattered literals.

   **Repro proof:** ``tests/test_handoff.py::TestBUG068SuccessorEnvironment::test_BUG_068_successor_claude_starts_without_the_launchers_host_markers``,
   committed 1d3ea31 (2026-10-20) on the session branch — exports ``CLAUDECODE=1``,
   ``CLAUDE_CODE_CHILD_SESSION=1``, ``CLAUDE_CODE_SESSION_ID=x``,
   ``COLORTERM=truecolor`` and ``FOO=bar``, runs ``TerminalLauncher`` with ``osascript``
   recorded (no window), then runs ``plan.shell_line`` with a real ``/bin/sh`` in the
   environment ``osascript`` was handed (its ``env=``, or the caller's own when none)
   and a fake ``claude`` dumps what it received. Asserts none of the four markers
   reach ``claude``, while ``FOO`` and ``INVARIANT_HANDOFF`` do. Today the assertion
   reads "successor claude inherited host markers" with all four present at the
   values exported. Either fix direction turns it green: the shell line removing
   the markers, or ``osascript`` handed a clean environment. Wrapped in a strict
   xfail; the fixing session deletes the marker and keeps the assertions. The red
   run is recorded in the bugfix pipeline (``--runxfail``, 1 executed, 1 failed).
   Guard in the same class, not xfail and green today: ``FOO=bar`` and the claim
   token reach the fake ``claude``, so a fix that wipes the whole environment
   cannot pass.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red test 1d3ea31 on the session branch
        forced: false
      - from: reproduced
        to: fixed
        reason: fixed in 7ab69a4
        forced: false

.. bug:: Handoff successor still inherits CLAUDE_EFFORT and GIT_EDITOR from the launching Claude session
   :id: BUG_069
   :status: fixed
   :severity: medium
   :priority: high
   :area: hosts/claude_code_launch
   :affects: COMP_HOSTS_CLAUDE_CODE_LAUNCH

   **Symptom.** A session started by ``invariant session handoff`` still inherits
   two variables the launching Claude Code session set: ``CLAUDE_EFFORT=medium`` and
   ``GIT_EDITOR=true``. Inside a Claude Code session the Bash environment carries
   both, and neither is set by the user (not in a shell rc file, not in settings);
   Claude Code sets them. The handoff removes ``HOST_MARKERS`` from the typed launch
   line (``env -u …``) and from the environment handed to ``osascript``, but neither
   name is on that list, so the new Terminal window and the successor ``claude``
   both receive them. Effects:

   - ``GIT_EDITOR=true`` makes a ``git commit`` without ``-m``, or a ``git rebase -i``, run
     in that Terminal window complete silently without ever opening an editor.
   - ``CLAUDE_EFFORT=medium`` may pin the successor's effort level instead of the
     user's default.

   **Expected.** The successor starts with the user's normal environment. Both
   names are host markers and are removed exactly like the BUG_068 set, while the
   rest of the user's environment is kept.

   **Repro.** In a Claude Code session run
   ``invariant session handoff --dry-run --note "next: x"`` and inspect the typed
   line: its ``env -u`` list names neither ``CLAUDE_EFFORT`` nor ``GIT_EDITOR``. In
   isolation, ``without_host_markers({"CLAUDE_EFFORT": "medium", "GIT_EDITOR": "true"})`` returns both unchanged.

   **Suspected cause.** ``HOST_MARKERS`` in ``hosts/claude_code_launch`` is
   incomplete: the BUG_068 fix (7ab69a4) listed the ``CLAUDECODE`` /
   ``CLAUDE_CODE_*`` / ``COLORTERM`` markers observed then, and did not include these
   two Claude Code exports.

   **Affected.** ``hosts/claude_code_launch`` — ``HOST_MARKERS`` and
   ``without_host_markers``, consumed by the launch plan's shell line and by
   ``TerminalLauncher``
   (:need:`ADR-005 <DEC_ADR_005>` D3 note; the
   ``hosts/`` section of the :doc:`architecture overview </architecture/overview>`;
   the ``session handoff`` entry in the :doc:`CLI reference </architecture/cli>`).
   Follow-up to BUG_068 (fixed in 7ab69a4); not a regression of it — the same
   mechanism, two names its list missed.

   **Fix sketch / next step.** Repro test first, beside the BUG_068 class in
   ``tests/test_handoff.py``: export ``CLAUDE_EFFORT=medium`` and ``GIT_EDITOR=true``
   and assert neither reaches the fake ``claude`` nor survives
   ``without_host_markers``. Then add both names to ``HOST_MARKERS``, and update the
   ADR-005 D3 note and the CLI reference's handoff section if they enumerate the
   list. Whether ``GIT_EDITOR`` should instead be restored from the user's own
   setting is moot: unsetting it lets git fall back to the user's configured
   editor.

   **Repro proof:** ``tests/test_handoff.py::TestBUG069SuccessorEnvironment::test_BUG_069_successor_starts_without_claude_effort_and_git_editor``,
   committed c137962 (2026-10-20) on the session branch — with ``CLAUDE_EFFORT=medium``,
   ``GIT_EDITOR=true`` and ``FOO=bar``, asserts that ``without_host_markers`` drops both
   names, that ``plan_launch``'s typed line unsets both with ``env -u``, and that the
   successor ``claude`` (the BUG_068 harness: ``TerminalLauncher`` with ``osascript``
   recorded, then ``plan.shell_line`` through a real ``/bin/sh`` and a fake ``claude``)
   starts without them while ``FOO`` and ``INVARIANT_HANDOFF`` still arrive. Today it
   fails at the first assertion: "without_host_markers kept launcher exports:
   {'FOO': 'bar', 'CLAUDE_EFFORT': 'medium', 'GIT_EDITOR': 'true'}"; the typed line
   names neither and a real shell passes both through. Strict xfail; with both
   names patched into ``HOST_MARKERS`` it reports XPASS(strict), so the fix deletes
   the marker and the assertions pass unchanged.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red test c137962 on the session branch
        forced: false
      - from: reproduced
        to: fixed
        reason: fixed in 46c56b5
        forced: false

.. bug:: item.history_matches_status warning states a count that doesn't match the items it lists
   :id: BUG_070
   :status: filed
   :severity: low
   :priority: low
   :area: core/rules
   :affects: COMP_CORE_RULES

   **Symptom.** The ``item.history_matches_status`` Stop-hook warning gives one
   count in its headline and lists fewer items in its detail, with nothing to say
   the list was cut. Observed 2026-10-20 in this repository, before the history
   backfill on the hand-set ``fixed`` bugs (commit 5933ea2): after every turn the
   warning reported 8 items holding a status their history does not explain, but
   the detail named only 6. BUG_008 and BUG_057 were left out, with no "and N
   more" marker, so the reader cannot tell the list is incomplete and has no way
   to learn which items are missing short of running the walk by hand.

   **Expected.** The headline count and the listing agree. Either every item is
   listed, or the listing ends with how many were left out (for example
   "… and 2 more"), so a truncated list reads as truncated.

   **Repro.** The warning no longer fires here: the backfill cleared the
   condition. Reproducing it needs a fixture item store with more than six items
   whose status their history does not explain (for example seven or more bugs at
   ``fixed`` with ``history: []``). Run the rule on the ``stop`` event: the headline
   says "7 item(s) hold a status their history does not explain" and the detail
   names six.

   **Suspected cause.** ``_history_matches_status`` in ``src/invariant/core/rules.py``
   builds the detail with ``"; ".join(problems[:6])`` and the headline with
   ``len(problems)``, and never appends the remainder. (``_history_problems`` returns
   at most one problem per item, so the count is a true item count; only the
   listing is cut.)

   **Affected.** The ``item.history_matches_status`` rule, documented under "The
   store the gates cannot sit in front of" in
   :doc:`state_model.md </architecture/state_model>`.

   **Fix sketch / next step.** Red test first: a fixture store with seven
   unexplained items, asserting the detail either names all seven or ends with a
   "and 1 more" remainder. The same silent ``[:N]`` cut appears at several other
   ``Verdict.fail`` detail sites in ``rules.py`` (the ``privacy.clean`` findings list,
   the memory-store check, the residue list, the coverage below-floor and
   regressed lists, the stray-paths list); a fix may want one shared helper that
   appends the remainder, applied across them. Message-wording sibling:
   :need:`BUG_067 <BUG_067>`.

   **Batch.** Unbatched — no open batch shares the mechanism (a truncated listing
   in a verdict message). ``inert-instruments`` and ``unsatisfiable-gates`` are about
   checks that drop input or cannot be satisfied, not about message wording.

.. bug:: An items.path spelled with a leading ./ silently loses the work-item exemption from session isolation
   :id: BUG_071
   :status: fixed
   :severity: medium
   :priority: medium
   :area: core/rules
   :slug: dot-slash-items-path-loses-exemption
   :affects: COMP_CORE_RULES

   **Symptom.** With ``items.path: ./docs/backlog`` in the manifest and
   ``items.exempt`` at its default (true), writes to the work-item store in the main
   checkout are DENIED by ``session.no_edit_in_main_checkout`` or
   ``session.isolated_before_edit``. This happens for isolated, registered and
   sessionless setups alike. The same layout spelled ``docs/backlog`` or
   ``docs/backlog/`` is allowed. Nothing says the exemption was lost: the
   declaration looks right and the refusal names an isolation rule, not the path.
   The refusal also arrives only when the first write is attempted, so a dispatched
   filing agent has already done all its work before the refusal (the cost
   :need:`BUG_032 <BUG_032>` describes).

   **Expected.** Equivalent spellings of the same path exempt identically. The
   manifest contract (:doc:`manifest.md </architecture/manifest>`, "Declared stores
   are writable stores") says declaring ``items.path`` is what exempts it from
   session isolation. How the declaration is spelled should not decide whether the
   exemption applies.

   **Repro.** Found by the 2026-10-21 bug-reproducer run on BUG_032 (entry updated
   in 280469a). It ran a 2600-call in-target matrix, a real ``invariant hook pre-tool-use`` subprocess per call, against temporary git projects with the
   markdown and sphinx_needs backends and Write/Edit/MultiEdit of the store. All 72
   denials in that matrix came from this one spelling. Minimal: a temporary git
   project whose manifest sets ``items.path: ./docs/backlog``; drive ``invariant hook pre-tool-use`` with an Edit of ``docs/backlog/<any item>.md`` in the main
   checkout; the verdict is a block from an isolation rule. Change the value to
   ``docs/backlog`` and the same call is allowed.

   **Suspected cause.** ``_is_exempt`` in ``src/invariant/core/rules.py`` builds the
   glob patterns ``<items.path>`` and ``<items.path>/**`` from the raw manifest string
   and fnmatches the target's project-relative forms against them without
   normalising either side. A project-relative form never starts with ``./``, so a
   pattern that does can never match.

   **Affected.** The isolation-exemption decision in core/rules (``_is_exempt``,
   consulted first by ``session.isolated_before_edit``,
   ``session.no_edit_in_main_checkout`` and ``pipeline.started_before_edit``). The
   contract it breaks is in :doc:`manifest.md </architecture/manifest>`. The
   exemption itself was decided in :need:`BUG_008 <BUG_008>`.

   **Fix sketch / next step.** Red test first: a fixture manifest with ``items.path: ./docs/backlog`` and an Edit of a store file in the main checkout, asserting the
   isolation rules return n/a, as they do for ``docs/backlog``. Then normalise the
   declared path once, where the manifest is loaded or where the exempt patterns
   are built (strip ``./``, collapse redundant separators, drop a trailing ``/``), and
   do not patch the ``./`` case alone. Check before fixing whether the other exempt
   sources share the same un-normalised handling: ``session.exempt_paths``,
   ``paths.memory``, ``paths.reports``, and the coverage / non-source globs. If they
   do, one shared normaliser covers them all. Whether to normalise or to reject a
   non-canonical spelling at manifest validation is a design choice for
   feature-architect. Silent non-matching is the one outcome that must go.

   **Related.** :need:`BUG_032 <BUG_032>`: this entry came out of investigating it, but
   it is NOT BUG_032's cause. The ``./`` spelling denies non-isolated sessions too,
   which contradicts BUG_032's report that a non-isolated dispatch works.
   :need:`BUG_008 <BUG_008>` is the exemption decision this defect quietly turns off.

   **Batch.** Unbatched. No open batch shares the mechanism (a declared path used
   as a match pattern without normalisation). BUG_032 sits in
   ``unsatisfiable-gates``, but that batch shares the symptom family with this
   entry, not the root cause.

   **Repro proof:** tests/test_bug_071_repro.py::test_BUG_071_dot_slash_items_path_store_write_is_not_denied, committed 82c006f (2026-10-21) — asserts that a Write to a store file in the main checkout, driven through the real ``pre-tool-use`` hook in a sessionless temporary git project whose manifest says ``items.path: ./docs/backlog``, is not refused. Today it is denied by ``session.isolated_before_edit`` and ``session.no_edit_in_main_checkout``. Wrapped in a strict xfail; recorded red via ``pipeline test --phase reproducing``. The same harness with ``docs/backlog`` passes (scratch control, not committed). The fix deletes the marker and keeps the assertion.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: strict-xfail repro 82c006f fails as reported
        forced: false
      - from: reproduced
        to: fixed
        reason: fixed in a3748c4
        forced: false

.. bug:: A declared paths.memory is documented as exempt from session isolation, but the exemption never adds it
   :id: BUG_072
   :status: filed
   :severity: low
   :priority: low
   :area: core/rules, docs/architecture
   :residue: DECISION 2026-10-21 (user-approved): Fix by correcting the docs, not by widening the exemption. Narrow docs/architecture/manifest.md ('Declared stores are writable stores') and docs/architecture/cli.md (~line 43) to what the code does: items.path and paths.reports are exempt\; a declared paths.memory is not (the default .claude/memory is exempt only via .claude/*).
   :affects: COMP_CORE_RULES

   **Symptom.** Two architecture docs state that declaring ``paths.memory`` exempts
   the memory store from session isolation, as declaring ``items.path`` or
   ``paths.reports`` does: docs/architecture/manifest.md ("Declared stores are
   writable stores") and docs/architecture/cli.md (the write-scope invariant, "the
   stores the manifest declares ... exempt from session isolation by the same
   mechanism that already exempts the backlog"). ``_is_exempt`` in
   src/invariant/core/rules.py adds ``items.path`` (unless ``items.exempt: false``)
   and ``paths.reports``, and never adds ``paths.memory``. The default ``.claude/memory``
   is exempt only by accident, because it matches the ``.claude/*`` pattern. A
   memory store declared anywhere else is not exempt, so the isolation rules
   (``session.no_edit_in_main_checkout``, ``session.isolated_before_edit``) refuse
   memory writes from an isolated session and in the main checkout.

   **Expected.** The code and the docs agree. Either the exemption covers a
   declared ``paths.memory``, or the docs stop claiming that it does. Covering it
   widens an exemption, so it is a deliberate decision (compare
   :need:`BUG_008 <BUG_008>`'s reasoning for ``items.exempt``, which includes an
   opt-out).

   **Repro.** In a project whose manifest sets ``paths.memory`` outside ``.claude/``
   (for example ``docs/memory``), write a memory file in the main checkout through
   the ``pre-tool-use`` hook. The write is refused by an isolation rule. The same
   write with the default ``.claude/memory`` is allowed.

   **Suspected cause.** ``_is_exempt`` was extended store by store (``items.path``,
   then ``paths.reports`` under ADR-002 D4), and ``paths.memory`` was never added. The
   default location hid the gap.

   **Affected.** ``_is_exempt`` in core/rules (the session-isolation rules);
   docs/architecture/manifest.md and docs/architecture/cli.md (the claims).

   **Fix sketch / next step.** Prove it first. A hook-driven test in a temporary
   project with ``paths.memory: docs/memory`` that asserts a store write is not
   refused should fail today. Then decide: (a) add ``paths.memory`` to ``_is_exempt``
   through the same declared-path normaliser BUG_071's fix introduces
   (``project_relative`` in core/manifest), possibly with an opt-out like
   ``items.exempt``; or (b) narrow the two docs to the stores that are actually
   exempt. Option (a) widens an exemption and needs its own decision.

   **History.** Found 2026-10-21 by a feature-architect audit during the
   :need:`BUG_071 <BUG_071>` fix. It was left out of that fix on purpose: closing it
   widens an exemption. BUG_071's fix records the gap in manifest.md. This is the
   same class of defect as :need:`BUG_022 <BUG_022>`: an authoritative doc stating
   behaviour the code does not implement.

   **Batch.** Unbatched. It has the same owner as BUG_071 (``_is_exempt``) but a
   different mechanism: a missing store, not an unnormalised spelling. BUG_071 is
   also being fixed now, so a batch of the two would hold one open member.

.. bug:: The sphinx_needs backend never persists item history, so item.history_matches_status fires falsely
   :id: BUG_073
   :status: fixed
   :severity: medium
   :priority: medium
   :area: core/items, core/rules
   :residue: DECISION 2026-10-21 (user-approved): Option B — persist history as an RST comment block inside the directive body (e.g. '.. invariant-history::' with indented YAML): renders nothing, needs no conf.py change, appended byte-stably\; the body parser must exclude it. Not A (conf.py registration + escaping), not C (sidecar), not Interim-only.
   :affects: COMP_CORE_ITEMS, COMP_CORE_RULES

   **Symptom.** ``SphinxNeedsBackend`` in src/invariant/core/items.py never writes
   ``Item.history``, and its parser never reads one. ``WorkItemDomain.transition``
   appends a history row and calls ``save``, and the backend drops the row. This has
   three effects on every ``sphinx_needs`` project:

   - ``item.history_matches_status`` (a Stop event, a warning under ``standard``)
     reports "status X with no recorded history (the machine starts at 'filed')"
     for every item that has moved past its initial status. The finding is false
     and it fires on every turn.
   - The forced/reason audit trail of ``--force`` transitions is lost entirely.
   - ``item.bug_reproduced_before_fix`` passes only by accident, because it also
     accepts the current status.

   **Expected.** History round-trips on every backend that claims the work-item
   contract. Otherwise the backend declares that it does not persist history, and
   the rule stands aside instead of reporting a false cause.

   **Repro.** In a project with ``items.backend: sphinx_needs``, file a bug and
   transition it (for example ``filed`` to ``reproduced``). Read the item back: its
   history is empty. End the turn: ``item.history_matches_status`` warns about the
   item. The same steps on the ``markdown`` backend keep the history row and do not
   warn.

   **Suspected cause.** The sphinx_needs serialiser and parser have no
   representation for history. The directive format has no slot for it, and none
   was added when history joined the work-item record.

   **Affected.** ``SphinxNeedsBackend`` in core/items (the persistence gap);
   ``item.history_matches_status`` and ``item.bug_reproduced_before_fix`` in
   core/rules (the consumers); docs/architecture/manifest.md (the backend table,
   and the sphinx_needs round-trip limitation BUG_065's fix documents).

   **Fix sketch / next step.** Prove it first: a backend round-trip test that
   transitions a sphinx_needs item and asserts its history survives a reload
   should fail today. The fix needs a decision. Two constraints apply. Sphinx-needs
   rejects unknown directive options unless the consumer registers them in
   ``needs_extra_options`` in conf.py. History reasons are free text, so they need
   escaping. The options, not ranked:

   - **(A)** A ``:history:`` directive option with one transition per continuation
     line. Needs conf.py registration and an escaping rule.
   - **(B)** An RST comment block inside the directive body (for example
     ``.. invariant-history::`` with indented YAML). It renders nothing, needs no
     conf.py change, and appends byte-stably. The body parser must exclude it.
   - **(C)** A sidecar file next to the .rst.
   - **(Interim)** A backend capability flag (``persists_history = False``) so
     ``item.history_matches_status`` returns N/A on this backend. Small, but it is
     a policy decision: it accepts the lost audit trail.

   Also undocumented: the existing ``:residue:`` option already requires a
   ``needs_extra_options`` registration in the consumer's conf.py, and no doc says
   so. Any option-based fix (A) adds a second such cost.

   **History.** Found 2026-10-21 by the feature-architect during the
   :need:`BUG_065 <BUG_065>` fix, and confirmed by code reading. BUG_065's fix
   documents the limitation in manifest.md ("The sphinx_needs round trip").
   Related: :need:`BUG_070 <BUG_070>` (the same rule, a different defect: its
   count); :need:`BUG_006 <BUG_006>` and :need:`BUG_007 <BUG_007>` (the same backend).

   **Batch.** Unbatched. BUG_065 shares the backend but its mechanism is the
   rewrite of existing lines, not a missing field. BUG_070 shares the rule but its
   mechanism is the message count.

   **Repro proof:** tests/test_bug_073_repro.py::test_BUG_073_sphinx_needs_history_reads_back_after_a_ruled_closure,
   committed 83fe022 (2026-11-09) on the session 27bb15be branch, strict xfail — asserts
   that a sphinx_needs bug closed by a ruling (filed -> reproduced -> fixed) reads back
   both history rows with ruling and evidence; today it reads back status fixed and
   history []. The same assertion passes on the json backend.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'red recorded: tests/test_bug_073_repro.py, commit 83fe022 on session 27bb15be'
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fixed by commit 46b27eb on session 27bb15be (ADR-018 increment 1): history persisted in an .. invariant-history: comment block'
        forced: false

.. bug:: item new cannot file on a sectioned sphinx_needs backlog: --field duplicates id and status, no section placement, closed store unchecked
   :id: BUG_074
   :status: fixed
   :severity: medium
   :priority: high
   :area: core/items, cli
   :affects: COMP_CORE_ITEMS, COMP_CLI_MAIN

   **Symptom.** On a ``sphinx_needs`` backlog laid out in named sections with a
   separate closed store, ``invariant item new`` cannot file an entry correctly.
   There are three defects:

   - (a) ``--field id=X`` APPENDS a second ``:id:`` option instead of replacing the
     minted one. A scratch-repo probe produced one directive carrying both
     ``:id: BUG_PROBE_ONE`` and ``:id: BUG_EXPLICIT``. ``--field status=...`` behaves
     the same way.
   - (b) The new entry always lands after the last directive of its type (the
     :need:`BUG_006 <BUG_006>` fix). It cannot be placed in a named section, so a
     backlog grouped into batch sections puts every new bug in the wrong one.
   - (c) The id uniqueness check reads only ``items.path``. A backend whose
     retired entries live in a closed store can mint an id that already exists
     there.

   **Expected.** A ``--field`` that names a field the backend already writes
   replaces it and never produces a duplicate. An explicit id is checked for
   uniqueness and refused on a collision. The caller can name the placement, as
   ``--section "<title>"`` or after a named item id. With no placement given, the
   behaviour falls back to what it is today. Uniqueness covers every store the
   backend reads, the closed store included.

   **Repro.** In a scratch repo with ``items.backend: sphinx_needs``, run
   ``invariant item new bug --title "probe" --field id=BUG_EXPLICIT`` and read the
   file: two ``:id:`` lines in one directive. Do the same with ``--field status=reproduced``: two ``:status:`` lines. For (b), use a backlog with two
   section headings, each holding bugs: the new bug lands under the last one
   whatever its batch. For (c), put an entry in the closed store whose id the
   next mint would produce, then file: the minted id collides.

   **Not a defect.** Slug ids already exist: ``items.id_strategy: slug`` (since
   19ea5a4, see :doc:`manifest.md </architecture/manifest>`). The reporting
   project had not set it. It is part of why its curator passed ``--field id=``,
   which is how (a) surfaced. The fix should not add a second slug mechanism.

   **Suspected cause.** (a) ``item new`` merges ``--field`` pairs into the option
   list by appending, without checking which keys the backend already emits
   (``id``, ``status``). (b) BUG_006's fix chose "after the last directive of the
   same type" and deliberately does not parse document structure, so it has no
   notion of a section to aim at. (c) The id allocator scans ``items.path`` only.
   The backend has no declared closed store, so there is nothing else for it to
   scan.

   **Impact.** It blocks the reporting project's backlog curator. That curator
   has reverted and hand-edited its backlog twice after ``item new``, which leaves
   the same committed-shaped intermediate state BUG_006 was filed against.

   **Affected.** ``core/items`` (the work-item model and its store backends, see
   :doc:`overview.md </architecture/overview>`, the ``items`` row): the
   sphinx_needs serialiser, its insertion point, and the id allocator. The ``cli``
   ``item new`` surface (see :doc:`cli.md </architecture/cli>`, "Work items") for
   the new placement option.

   **Fix sketch / next step.** Prove it first with three red tests against the
   sphinx_needs backend: (1) ``item new --field id=X`` yields exactly one ``:id:``,
   equal to X, and the same for ``status``; (2) an explicit id equal to an existing
   open id is refused; (3) with a closed store declared, an id present only there
   is never minted. (b) is new surface, not a regression. It needs
   ``--section``/``--after``, and an error when the named section does not exist,
   never a silent fallback. (c) needs the backend to declare its closed store.
   That is the same declaration :need:`IDEA_019 <IDEA_019>` needs for retirement, so
   agree the key once.

   **History.** Reported 2026-10-20 by two sessions of a consuming project
   (sphinx_needs backend, sectioned backlog, separate closed store). Verified
   2026-10-21 against main at 61f86b5. Related: BUG_006 (the placement rule (b)
   outgrows), :need:`BUG_065 <BUG_065>` and :need:`BUG_073 <BUG_073>` (the same backend,
   other write-path defects).

   **Batch.** Unbatched. BUG_065 and BUG_073 share the backend, but their
   mechanisms are rewriting existing lines and a missing history field. This one
   is the ``new`` path: field merging, placement and the id scan. No open batch
   (``unsatisfiable-gates``, ``inert-instruments``, ``adr-002-followups``) shares it.

   **Repro proof (2026-10-22):** ``tests/test_bug_074_repro.py``, committed
   1f129c0. Four strict xfails, recorded red in the ``reproducing`` phase:

   - ``TestBUG_074_a_FieldNamingAWrittenKey::test_BUG_074_a_explicit_id_becomes_the_entry_id``
     asserts that ``item new --field id=BUG_EXPLICIT`` writes one ``:id:`` equal to
     ``BUG_EXPLICIT``. Today it exits 0 and writes ``:id: BUG_PROBE_ONE``.
   - ``...::test_BUG_074_a_explicit_id_colliding_with_an_open_id_is_refused``
     asserts that ``--field id=<an open id>`` is refused and leaves the file as it
     was. Today it exits 0.
   - ``...::test_BUG_074_a_explicit_status_is_not_silently_dropped`` asserts that
     ``--field status=reproduced`` is either refused or honoured. Today it exits 0
     and writes ``:status: filed``. Which of the two the fix chooses is the fixer's
     call: the ``filed -> reproduced`` invariant argues for refusing.
   - ``TestBUG_074_b_SectionPlacement::test_BUG_074_b_new_bug_lands_in_the_named_section``
     asserts that ``--section "Batch one"`` lands the bug under that heading. Today
     the option is rejected as unrecognised (exit 2) and nothing is filed. The
     test uses the surface named under Expected, so it is an acceptance
     criterion for new surface, not a regression test.

   The symptom (a) was filed with, two ``:id:`` / ``:status:`` lines in one
   directive, no longer fires. 983e0ab (BUG_065, 2026-10-21) holds ``id``,
   ``status`` and ``residue`` out of the written fields. That commit landed after
   the 61f86b5 probe. So (a) is now a silent drop: the value is never written
   twice, and it is never honoured. (c) has no test. The engine declares no
   closed store (``items.closed_path`` is only a proposal, IDEA_019), so the scan
   has no second store to miss. (c) needs design, not reproduction.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'red: tests/test_bug_074_repro.py, 1f129c0'
        forced: false
      - from: reproduced
        to: fixed
        reason: fixed in 07ee5c7
        forced: false

.. bug:: session handoff with a long note never starts the successor, reports success, then SIGTERMs the parent
   :id: BUG_075
   :status: fixed
   :severity: high
   :priority: high
   :area: hosts/claude_code_launch, cli/main
   :affects: COMP_HOSTS_CLAUDE_CODE_LAUNCH, COMP_CLI_MAIN

   **Symptom.** When the note is long, ``invariant session handoff`` silently fails
   to launch the successor, reports success anyway, and then SIGTERMs the
   launching session. The result is no session at all. In a consuming project on
   2026-10-20, a handoff's new Terminal tab received exactly 1024 bytes of the
   typed command line. No ``claude`` process started, and the tab showed only a
   bare shell prompt. The command still printed "opened a new Terminal window"
   and exited 0. Without ``--keep-open`` it sent SIGTERM to the launching session
   about 20 seconds later.

   **Expected.** A handoff with a note of any length launches a live successor.
   If it cannot, it says so, exits non-zero, and leaves the parent session
   running. Success is reported, and the parent is ended, only after a successor
   has been seen alive.

   **Repro.** On macOS, run ``invariant session handoff --note "<a note over ~540 bytes>"`` (no ``--keep-open``). The new tab shows a shell with a
   truncated, unterminated command. No ``claude`` starts, the command reports
   success, and the parent receives SIGTERM.

   **Suspected cause.** Confirmed by measurement. ``plan_launch`` in
   ``hosts/claude_code_launch.py`` builds ONE shell line: ``cd`` + ``env -u ...`` +
   ``claude --name N --remote-control N '<note>'``. Terminal's ``do script`` TYPES
   that line into the tty, and macOS canonical input (MAX_CANON) is 1024 bytes.
   With a 10-byte note the line is already 487 bytes, so any note over about 540
   bytes is cut mid-quote and the shell never runs ``claude``. The success path in
   ``cli/main.py`` handoff takes "Terminal accepted the script" as "successor
   launched". It never checks for a successor process before reporting and
   signalling.

   **Affected.** ``claude_code_launch`` in ``hosts/`` (see
   :doc:`overview.md </architecture/overview>`) and the ``session handoff`` command
   (see :doc:`cli.md </architecture/cli>`, "Session", and
   :doc:`state_model.md </architecture/state_model>`, "Handing off: an event, not
   an edge").

   **Required fix (maintainer ruling, 2026-10-20).**

   1. Write the note to a temp file and pass its location, not its text, so the
      typed line stays short whatever the note's length. Clean up or expire the
      file once it has been read. Keep it out of anything committed: privacy
      rules apply to its content and its path.
   2. Before reporting success or sending any SIGTERM, verify a live successor
      process whose argv carries ``--name <slug>`` and ``--remote-control <slug>``,
      still alive after a short grace period. If it is absent, fail non-zero and
      never SIGTERM the parent.
   3. Remote Control is mandatory in the plan. ``plan_launch`` refuses to build a
      plan without it, with a pinning test.
   4. A regression test with a note of 2000+ bytes.

   **Fix sketch / next step.** Red first. A ``plan_launch`` test asserts the typed
   line stays under MAX_CANON for a 2000-byte note: it fails today, at
   about 2500 bytes. A handoff test with a launcher stub that starts no successor
   asserts a non-zero exit and no SIGTERM: it fails today with exit 0 and a
   signal. :need:`BUG_069 <BUG_069>` touched the same file and is fixed, so there is
   no sequencing conflict.

   **History.** Reported 2026-10-20 by a consuming project from a live handoff.
   Line length measured, and the 1024-byte cut matched, on 2026-10-21 against
   main at 61f86b5. Related: :need:`IDEA_009 <IDEA_009>` (the SIGTERM-on-launch
   behaviour this makes destructive), :need:`BUG_068 <BUG_068>` and BUG_069 (the
   same launcher, environment defects).

   **Batch.** Unbatched. BUG_068 and BUG_069 share the launcher but are fixed,
   and their mechanism was inherited environment, not line length or an
   unverified launch. No open batch shares the mechanism.

   **Repro proof:** ``tests/test_handoff.py::TestBUG075LongNoteHandoff``, committed
   2ff310a (2026-10-22) on the session branch — three strict xfails, one per
   mechanism, with a 1962-character, 2005-byte note that a real handoff accepts
   (a non-xfail guard pins that). ``test_BUG_075_typed_line_fits_the_tty_for_a_2000_byte_note``
   asserts ``plan_launch``'s typed line plus its newline fits MAX_CANON (1024 bytes);
   today "typed line is 2543 bytes; the tty keeps 1024".
   ``test_BUG_075_the_line_a_tty_delivers_still_starts_claude`` runs the first 1024
   bytes of the line through a real ``/bin/sh`` with a fake ``claude``; today the shell
   stops at "unexpected EOF while looking for matching ``''" and "no claude started from the line the tty delivered" (with a short note the same harness does start it).``\ test_BUG_075_no_live_successor_means_no_success_and_no_sigterm\ ``runs the command with a launcher stub that starts nothing and``\ CLAUDE_PID\ ``set; today it exits 0 and schedules "SIGTERM ... with no successor: [(4242, 20)]". The fix deletes the three markers and the assertions pass unchanged. The third also needs the fix's successor check to find no successor under the stub launcher. Recorded red through``\ invariant pipeline test --phase reproducing\`.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'red: tests/test_handoff.py::TestBUG075LongNoteHandoff, 3 strict xfails, committed 2ff310a'
        forced: false
      - from: reproduced
        to: fixed
        reason: fixed in af99365
        forced: false

.. bug:: pipeline advance committed succeeds while the session's declared files are uncommitted
   :id: BUG_076
   :status: filed
   :severity: medium
   :priority: medium
   :area: core/machines, core/rules
   :batch: inert-instruments
   :affects: COMP_CORE_MACHINES, COMP_CORE_RULES

   **Symptom.** ``invariant pipeline advance committed`` succeeds while the
   session's work is still uncommitted in the worktree. Observed 2026-10-22 in
   session 9a819a5a (the BUG_074 fix). The compliance reviewer's ``git add`` failed
   on a zsh word-splitting mistake, so nothing was staged and nothing was
   committed. Its chained ``invariant pipeline advance committed`` ran anyway, and
   every entry gate passed ("6 passed"). The pipeline was recorded in ``committed``
   while the declared files were still dirty. The real commit, 07ee5c7, was made
   afterwards, so no harm came of it this time.

   **Expected.** A pipeline cannot enter its landing state ``committed`` while any
   of the session's declared files is dirty, i.e. modified or staged in the
   worktree but not in a commit. The advance is refused with a message naming
   the dirty files and the fix (commit them, then advance). Without this, the
   state named ``committed`` asserts something nothing checked.

   **Repro.** In an isolated session with a pipeline at ``reviewing`` and a
   declared file modified but not committed, run ``invariant pipeline advance committed``. It succeeds, the pipeline records ``committed``, and ``git status``
   still shows the file as modified.

   **Suspected cause.** No rule asserts a clean tree on the landing state. I
   checked whether an existing rule was meant to cover this and is just not
   attached. None is:

   - The ``committed`` entry gates (state_model.md, the tdd machine) are
     ``test.all_pass``, ``coverage.meets_floor`` and ``privacy.clean``. The ``landing``
     role adds ``docs.updated`` and the ``agents.*`` gates. None of them asks whether
     the work is in a commit. Several of them deliberately read uncommitted work
     through ``core/rules._changed_set``, so they pass on a dirty tree by design.
   - ``session.own_file_set_only`` checks that *staged* files are declared. It says
     nothing about declared files that are left unstaged.
   - The only clean-tree checks are teardown's precondition in the session domain
     and ``session rebase``'s refusal in cli/main (through ``git.changed_files``).
     Neither is a rule, and neither runs on the pipeline's landing edge.

   So this is a missing gate, not a detached one. Every methodology (``tdd``,
   ``spec-first``, ``bugfix``, ``chore``, ``docs``) ends in ``committed``, so all of them
   are affected.

   **Affected:** ``core/machines`` (the landing role and the methodology YAMLs
   under data/machines) and ``core/rules`` (the gate registry), per
   :doc:`state_model.md </architecture/state_model>`. The ``git.changed_files``
   helper in ``core/git`` already answers the question.

   **Fix sketch / next step:** bug-reproducer first: a red test that advances a
   pipeline to ``committed`` with a declared file dirty and asserts the advance is
   refused. Then add a gate (e.g. ``session.work_committed`` or
   ``pipeline.work_committed``) on the ``landing`` role, so that every methodology
   gets it centrally, as ``docs.updated`` does. It must fail when git cannot answer
   (the AUD-07 convention) and ignore dirty paths outside the declared set, which
   belong to parallel sessions. One thing to decide: whether the gate is
   satisfied by the declared files being clean, or by the declared files being
   clean *and* the branch carrying at least one commit since the pipeline
   started.

.. bug:: tests/conftest.py does not strip INVARIANT_HANDOFF, so the suite run inside a handed-off host reports 15 false binding failures
   :id: BUG_077
   :status: filed
   :area: tests/conftest.py, domains/pipeline
   :severity: medium
   :priority: medium
   :affects: COMP_DOMAINS_PIPELINE

   **Symptom.** Run inside a Claude Code session that ``session handoff`` launched,
   the full suite reports 15 false failures, all in host-binding tests:
   ``tests/test_host_claude_code.py::TestAutomaticBinding`` (9),
   ``TestTheClaimPatternIsNarrow::test_the_two_claiming_verbs_do_bind`` (2) and
   ``tests/test_cli.py::TestResumeBindsTheHostThatRanIt`` (4). The fake domains
   these tests use raise ``AttributeError: 'FakeSessions' object has no attribute 'claim_handoff'``, and the real-domain tests claim a note on a session that
   belongs to no test. With ``INVARIANT_HANDOFF`` removed from the environment
   (``env -u INVARIANT_HANDOFF uv run pytest ...``), all 27 tests pass.

   **Cause (suspected).** A host launched by ``session handoff`` inherits
   ``INVARIANT_HANDOFF``, and that inheritance is by design (IDEA_008). The hook's
   binding path reads the variable from ``os.environ`` on every claimed
   ``session start`` / ``resume``. The autouse fixtures in ``tests/conftest.py`` strip
   ``CLAUDE_PROJECT_DIR``, ``INVARIANT_SESSION_ID`` and ``CLAUDE_PID``, but not this
   variable. This is the BUG_035 / BUG_058 shape one more variable over.
   ``invariant pipeline test`` does not strip it from the suite's environment
   either, so it records the false red as durable evidence.

   **Found by.** IDEA_012 increment 1 (session 373aae25). Its recorded green run
   failed on exactly these 15 tests and nothing else.

   **Fix direction.** Add an autouse fixture in ``tests/conftest.py`` that deletes
   ``INVARIANT_HANDOFF``. Consider stripping it in ``domains/pipeline._child_env``
   too, beside ``INVARIANT_SESSION_ID``. Possibly fold in with BUG_058.

   **Seen again 2026-11-02.** Session e727b841 (2026-11-01/02, privacy engine
   fixes, landed cb3815b) hit the same 15 failures in the same three classes. They
   were already failing at HEAD b01a4ea, before that change, and do not come from it.
   State of the main checkout on 2026-11-02:

   - ``tests/conftest.py`` still does not delete ``INVARIANT_HANDOFF``. Its autouse
     fixtures strip only ``CLAUDE_PROJECT_DIR`` (line 29), ``INVARIANT_SESSION_ID``
     (line 48) and ``CLAUDE_PID`` (line 95), so the main fix above has not landed.
   - ``domains/pipeline._child_env`` already lists ``INVARIANT_HANDOFF`` among the
     variables it strips (``src/invariant/domains/pipeline.py`` lines 91-100). So
     ``invariant pipeline test`` should no longer record the false red, and only a
     bare ``pytest`` run is exposed. Not verified by a run.

.. bug:: The oracle reads a host's last activity from the transcript's mtime, which moves while the host does nothing
   :id: BUG_078
   :status: fixed
   :severity: medium
   :priority: high
   :area: oracle/scan, hosts/claude_code_telemetry
   :affects: COMP_ORACLE_SCAN, COMP_HOSTS_CLAUDE_CODE_TELEMETRY

   **Symptom.** The oracle reads a host as recently active when it has done
   nothing for days. On 2026-10-22, ``invariant oracle list`` on a Mac with two
   registered projects showed 15 orphan host rows, and every one of them had a
   last activity under 1 h. For the same hosts, the last timestamped user or
   assistant entry in the transcript was up to 235 h old. So a dead or idle
   orphan never ages out of the 24 h window, and the ``stuck`` signal (last
   activity older than 20 minutes) is judged against a clock that keeps being
   reset.

   **Expected.** Last activity reflects real conversational activity: the
   timestamp of the newest user or assistant entry in the host's transcript. A
   host whose last such entry is 235 h old reads as idle for 235 h, drops out of
   the orphan list 24 h after its last sign of life (unless it is known to be
   alive), and a running turn with no new entry for 20 minutes raises ``stuck``.

   **Repro.** Leave a Claude Code host open and idle for more than a day (or
   find one whose session was torn down days ago). Run ``invariant oracle list --json`` and compare the row's last activity with the ``timestamp`` of the last
   ``user`` or ``assistant`` line in its transcript JSONL. The row reports minutes;
   the transcript says days.

   **Suspected cause.** ``oracle/scan`` takes last activity as the latest of the
   transcripts' modification times (oracle.md, "Last activity"). Claude Code
   keeps touching the transcript file without any user or assistant turn, so
   the mtime moves while the conversation does not. The value is right by the
   documented formula and wrong as a measure of activity.

   **Affected:** ``oracle/scan`` (the last-activity read, and through it the
   orphan window and ``oracle/signals``' ``stuck``), per
   :doc:`oracle.md </architecture/oracle>`, "Shipped: the scan, the signals and
   the recommendations". The transcript reader is ``hosts/claude_code_telemetry``
   (:doc:`overview.md </architecture/overview>`), which already parses the JSONL.
   Transcript facts reach the scan through ``HostProbe`` (ADR-007 D11), so the new
   read belongs behind the probe, not in ``oracle/``.

   **Fix sketch / next step:** bug-reproducer first: a red test with a
   transcript whose mtime is fresh and whose last user/assistant entry is old,
   asserting the row's last activity is the entry's timestamp and that the
   orphan drops out after 24 h. Then have the probe return the last timestamped
   user/assistant entry (read from the file's tail, so the cost stays small),
   and keep the mtime only as a fallback when no entry carries a timestamp. The
   attention records' mtimes, the session ``heartbeat`` and ``plan_touched`` stay as
   they are. Update oracle.md's "Last activity" paragraph with the fix.

   **Related:** IDEA_024 (host liveness from Claude Code's per-process records)
   and IDEA_025 (orphan management), which needs a truthful idle time to tell a
   leftover host from a recently active one. Found during IDEA_012's real-machine
   use.

   **Repro proof:** tests/test_bug_078_repro.py::test_BUG_078_last_activity_is_the_last_turn_not_the_transcript_mtime, committed 8617181 (2026-10-25) — asserts that an orphan host whose newest user/assistant transcript entry is 240 h old, and whose transcript file was last modified 5 minutes ago, is reported by ``scan_project`` with a last activity at that entry. It goes through the real ``ClaudeCodeProbe`` pointed at a synthetic Claude Code home, with a live pid so the row is listed at any age. Today the row reads idle for 0.08 h: its ``last_activity`` is the file's mtime. The transcript's trailing lines are untimestamped ``last-prompt`` and ``cost-state`` records, the kind real transcripts gain after the last turn. Wrapped in a strict xfail; recorded red via ``pipeline test --phase reproducing``. The fix deletes the marker and keeps the assertion. ``tests/test_oracle_scan.py::TestClaudeCodeProbe::test_transcript_activity_is_each_transcripts_mtime`` pins the old formula and changes with the fix.

   **Note for the fix (shared with IDEA_029):** a tail reader in ``hosts/claude_code_telemetry`` fits both items: read the transcript backwards from its end in fixed blocks and stop at the first JSONL line whose ``type`` is ``user`` or ``assistant`` and that carries a ``timestamp``. Return that entry, with role, timestamp as an epoch, and message content. BUG_078 needs only the timestamp; IDEA_029 needs the content. Skip untimestamped bookkeeping lines (``last-prompt``, ``cost-state``, ``bridge-session``) and timestamped non-turn lines (``system``, ``attachment``). Return nothing when no turn is found, so the mtime fallback applies. Two choices for feature-architect: whether a ``user`` line that only carries a ``tool_result`` counts as activity (it matters for ``stuck`` during a running turn), and whether ``isSidechain`` lines count.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red test 8617181
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fixed in ce374b9: last activity read from the newest user or assistant turn'
        forced: false

.. bug:: The oracle web page refuses its own pairing form: Referrer-Policy no-referrer makes browsers send Origin: null
   :id: BUG_079
   :status: fixed
   :severity: high
   :priority: high
   :area: oracle/web
   :affects: COMP_ORACLE_WEB

   **Symptom.** The oracle web page refuses its own pairing form. On
   2026-10-22, from Safari on a phone over Tailscale: open
   ``http://<tailnet address>:7341/pair``, enter a valid fresh code, submit. The
   phone shows "not paired / That form did not come from this page." (HTTP
   403). No real browser can pair, so the whole web dashboard is unusable. The
   confirm-first actions were first suspected of failing the same way; they do
   not (see the repro proof): ``/api/act`` was never affected, only pairing.

   **Expected.** A pairing form submitted from the page itself pairs the
   device, and an action confirmed on the page runs. A genuinely cross-site
   ``POST`` is still refused.

   **Repro.** From the Mac, after ``invariant oracle web pair``:
   ``curl -H "Origin: null" -d code=<code> http://<tailnet address>:7341/pair``
   returns 403. The same ``POST`` without an ``Origin`` header pairs fine. A
   curl-driven end-to-end pairing works, which is why the suite missed it: no
   test sends the ``Origin`` a browser derives from the page's own headers.

   **Suspected cause (verified).** ``src/invariant/oracle/web.py`` sends
   ``Referrer-Policy: no-referrer`` on every response (oracle.md, "Who gets an
   answer": every response is ``no-store``, ``nosniff`` and ``no-referrer``). Under
   the Fetch spec's "serialize a request origin" step, a request that is not
   CORS-tainted, whose method is not ``GET`` or ``HEAD``, is sent with
   ``Origin: null`` when the referrer policy is ``no-referrer``. That covers the
   page's same-origin form ``POST`` (a navigation), which is the pairing form. A
   ``fetch()`` ``POST`` in the default "cors" mode keeps its real ``Origin``, so
   ``/api/act`` was never affected. ``_same_origin`` (pairing) and ``_origin_required``
   (``/api/act``) both compare ``Origin`` with the ``Host`` header, so ``null`` is
   refused.

   **Affected:** the oracle web listener's origin checks and response headers,
   per :doc:`oracle.md </architecture/oracle>`, "Shipped: the web page (IDEA_023
   increment 1)": "Who gets an answer", "Actions from the page" and "Pairing";
   and :need:`ADR-008 <DEC_ADR_008>`
   D10/D11 (the origin checks) and D5 (which names the ``no-referrer`` header).
   Shipped in IDEA_023: commits 19be448, 33bdf8b and ccec29d.

   **Fix sketch / next step:** bug-reproducer first: a red test that pins the
   header pair, the ``Referrer-Policy`` the page sends and the ``Origin`` a browser
   would derive from it for a same-origin ``POST``, against both ``POST /pair`` and
   ``POST /api/act``. Likely fix, for the architect to confirm:
   ``Referrer-Policy: same-origin``. Browsers then send the real origin on
   same-origin requests and ``null`` on cross-origin ones, and referrers never
   leave the origin, so a ``?code=`` query string still never leaks. A
   cross-site ``POST`` must stay a 403. Update oracle.md's header line and
   ADR-008 D5's.

   **Related:** IDEA_023 (the web dashboard this defect makes unusable).

   **Repro proof:** ``tests/test_oracle_web_actions.py::test_BUG_079_the_pages_own_post_is_accepted_with_the_origin_a_browser_derives``,
   committed 34134a2 (2026-10-22) on the session branch — one test, two cases.
   It reads the ``Referrer-Policy`` the page actually serves and derives the
   ``Origin`` per the Fetch standard's "append a request ``Origin`` header". The
   ``[pair]`` case is a strict xfail: the pairing form's submission is a
   navigation (mode "navigate"), so under ``no-referrer`` it carries
   ``Origin: null``, and ``POST /pair`` with a valid fresh code answers 403 "That
   form did not come from this page." instead of the 303 with the cookie. The
   fix deletes that marker and the assertion passes unchanged; with
   ``Referrer-Policy: same-origin`` it already XPASSes. The ``[act]`` case is not
   marked and passes today. The standard applies the referrer-policy switch only
   when the request's mode is not "cors", and the page's ``fetch("/api/act", …)``
   sets no ``mode``, so it is "cors" and keeps the page's real origin under any
   policy. With a nonce from the confirm, that ``POST`` answers 202. Inverting the
   case (``mode: "same-origin"``, so ``Origin: null``) gives the 403, so the harness
   does see the action route. The "actions would fail the same way" half of this
   entry therefore does not hold for a standard-conforming browser. Whether
   mobile Safari conforms is unchecked: confirm one action from the paired page
   on the phone once pairing works. Also for the fix:
   ``TestOverHttp::test_a_foreign_origin_is_refused`` pins ``"null"`` as refused on
   ``/api/act``, which stays right under ``same-origin``.

   **Fix (landed).** ``Referrer-Policy`` is now ``same-origin``
   (``src/invariant/oracle/web.py``, ``_BASE_HEADERS``). The page's own form ``POST``
   carries its real ``Origin`` and pairs. A cross-site ``POST``, including
   ``Origin: null``, is still refused on ``/pair`` and on ``/api/act``, and the
   referrer, with any ``?code=`` query, never leaves the origin. oracle.md and
   ADR-008 D5 are updated. Correction to the original report: only pairing was
   affected; ``/api/act`` was never broken.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'red: tests/test_oracle_web_actions.py::test_BUG_079_the_pages_own_post_is_accepted_with_the_origin_a_browser_derives[pair], strict xfail, committed 802a3d9'
        forced: false
      - from: reproduced
        to: fixed
        reason: Referrer-Policy same-origin; pairing form accepted, cross-site POST incl. Origin null still refused
        forced: false

.. bug:: Oracle tests hardcode a wall-clock wait time, so the waiting-row-is-red tests fail on every run since 2026-10-22
   :id: BUG_080
   :status: fixed
   :severity: high
   :priority: high
   :area: oracle tests
   :affects: COMP_ORACLE_SIGNALS

   **Symptom.** Two oracle tests that assert a waiting session renders red fail
   on every run, on main, for everyone:
   ``tests/test_oracle_web.py::TestView::test_a_waiting_session_is_red_and_counted``
   and ``tests/test_oracle_tui.py::TestLines::test_the_waiting_row_is_red``. The
   row comes out amber. Main was green at merge ccec29d; the tests went red on
   2026-10-22, about an hour after that merge, with no commit in between. Because
   ``test.all_pass`` gates every pipeline, the red suite blocks every session from
   reaching verifying; it is currently holding the BUG_079 fix (session
   f5fa04f5).

   **Expected.** The tests are deterministic: a waiting session in the fixture
   reads as a fresh wait (high severity, red row) whatever the wall clock says.

   **Repro.** Run either test with the clock later than six hours after the
   fixture's ``waiting.since`` literal (any run from that point on). Verified by
   the feature-architect with a pinned clock: both tests pass one minute before
   the six-hour threshold and fail one minute after it.

   **Cause (verified).** The ``machine_world`` fixture in
   ``tests/test_oracle_scan.py`` (around line 170) hardcodes
   ``waiting.since`` as a fixed ISO-8601 literal on 2026-10-22 (an absolute
   instant, not one relative to the test clock). The tests
   evaluate it against the real clock: ``_document`` calls
   ``assess(..., time.time())``, and the module sets ``NOW = time.time()`` at import.
   ``oracle/signals``' classify drops a wait older than ``stale_wait_hours`` (6.0)
   from high to medium severity, so once the real clock passed the literal plus
   six hours the row turned amber instead of red. The production behaviour is
   correct; the fixture is a time bomb.

   **Affected:** the oracle test fixtures (``tests/test_oracle_scan.py``
   ``machine_world``) and the tests that consume them in
   ``tests/test_oracle_web.py`` and ``tests/test_oracle_tui.py``. The rule they trip
   is the stale-wait downgrade in ``oracle/signals``, per
   :doc:`oracle.md </architecture/oracle>`, "Shipped: the scan, the signals and
   the recommendations".

   **Fix sketch / next step:** the two failing tests are already the red proof.
   Make ``since`` relative to the test's clock (for example ``NOW - 60`` formatted as
   ISO-8601), or pass one pinned clock into ``assess`` and into the fixture so both
   sides read the same instant. Also check ``tests/test_oracle_watcher.py`` and
   ``tests/test_oracle_attention.py``, which pin the same literal and will turn
   the same way wherever an age is computed against the real clock. A pinned
   clock regression test (just under and just over ``stale_wait_hours``) keeps the
   fix honest.

   **Related:** IDEA_012 (Session Oracle v1, which shipped the signals), BUG_079
   (its fix is blocked by this red suite).

   **Repro proof:** ``tests/test_bug_080_repro.py::test_BUG_080_a_waiting_session_reads_red_whatever_the_clock_says``,
   committed 8c4e9fe (2026-10-22) — builds ``machine_world`` with its own clock
   (``test_oracle_scan.NOW``) pinned to 2036-01-01, reads the dashboard
   (``Watcher.tick`` then ``dashboard.lines``) at that same instant, and asserts the
   ``a-live`` row's ``waiting`` signal is red. It is amber today
   (``waiting on a permission: Allow Bash? (4834920 min)``). Wrapped in a strict
   xfail: delete the marker with the fix and the assertion must pass unchanged.
   The two plain-red tests named in the Symptom still fail on main with the same
   amber-for-red message. Checked: with the clock pinned one hour short of the
   fixture's ``since`` plus six hours, the same test passes, and one hour past it,
   it fails, so the stale-wait downgrade is the only thing that flips it. A trial
   fix (``since`` = ``NOW - 60`` in the fixture, not committed) turned it green at
   2036. The test reads the fixture's ``NOW``, so the fix has to derive ``since``
   from that same clock; a fix that leaves ``assess`` on the real clock would still
   pass on the real clock but not this test.

   **Repro attempt 2026-10-22 (sibling files):** ``tests/test_oracle_watcher.py``
   (``SINCE``) ages the wait against the real clock, but nothing there turns on
   it: a stale wait drops from high to medium, both play the alert sound, and
   the only message check is ``"waiting on a permission" in message``. Green
   before and after the threshold. ``tests/test_oracle_attention.py`` (``NOW``)
   only records the literal through an injected clock and computes no age.
   Neither is a time bomb today. Moving ``time.time`` itself years forward for the
   whole oracle suite also turns 12 other tests red, but that comes from the
   probe: real file mtimes do not move with the shifted clock (snapshot
   freshness, the 24-hour orphan window, the stuck timer). It is not this
   defect.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red proof committed 8c4e9fe
        forced: false
      - from: reproduced
        to: fixed
        reason: fixture waits made relative to the test clock; full suite green
        forced: false

.. bug:: Intermittent flake: the TUI refresh-timer test raises textual WorkerCancelled under full-suite load
   :id: BUG_081
   :status: filed
   :severity: medium
   :priority: medium
   :area: oracle tests (TUI)
   :affects: COMP_ORACLE_TUI

   **Symptom.** Under full-suite load,
   ``tests/test_oracle_tui.py::TestItAlwaysRefreshes::test_the_timer_runs_from_mount_even_when_the_first_load_fails``
   intermittently errors with textual's ``WorkerCancelled``. It failed in one of
   two full-suite runs on 2026-10-22. It passes when run alone, and it passes
   when its file runs alone.

   **Expected.** The test passes deterministically, in isolation and under full
   suite load alike.

   **Repro.** Run the full suite repeatedly; roughly one run in two (small
   sample) the test errors with ``WorkerCancelled``. Isolated runs do not
   reproduce it.

   **Suspected cause.** The drive/settle helper in ``tests/test_oracle_tui.py``
   (lines 129-138) waits in ``workers.wait_for_complete`` while a refresh worker
   running on a 0.05 s interval is cancelled and replaced. When the cancellation
   lands during the wait, the helper surfaces ``WorkerCancelled`` instead of
   treating a cancelled refresh as settled. Load widens the window. Unconfirmed.

   **Affected:** the TUI dashboard test helpers in ``tests/test_oracle_tui.py``;
   the behaviour under test is the dashboard's auto-refresh timer, per
   :doc:`oracle.md </architecture/oracle>`, "Shipped: the dashboard".

   **Fix sketch / next step:** bug-reproducer first: force the race (a worker
   cancelled while the helper waits, for example by stretching the refresh or
   cancelling explicitly) to get a deterministic red. Then make the settle
   helper tolerate a cancelled refresh worker (catch ``WorkerCancelled``, or wait
   only on workers that are not superseded), or drive the refresh with a
   controllable timer rather than a real 0.05 s interval.

   **Related:** IDEA_023 (the auto-refresh change that added this test).

.. bug:: A subagent's architecture-doc reads are credited to its parent host's bound session, not to the session whose worktree it reads in
   :id: BUG_082
   :status: filed
   :severity: medium
   :priority: medium
   :area: hosts/claude_code hooks and session attribution
   :batch: unsatisfiable-gates
   :affects: COMP_HOSTS_CLAUDE_CODE

   **Symptom:** A subagent that starts its own session has its architecture-doc
   reads, and its first edit, credited to the session its parent host is bound
   to, not to the session whose worktree it is working in. Observed 2026-10-22:
   the parent host was bound to the session running the BUG_079 bugfix pipeline.
   A ``bug-reproducer`` subagent started a second session and read
   ``docs/architecture/oracle.md`` inside that second session's worktree. The
   ``docs_read`` evidence landed on the parent's session, still naming the path in
   the second session's worktree. The second session's ``triaging -> reproducing``
   was then blocked by ``docs.read_architecture``, and the agent had to run
   ``pipeline advance --force`` with a reason; the force is in that pipeline's
   history. The same agent also had to move its session from ``isolated`` to
   ``working`` by hand with ``invariant session transition``, because the hook
   credited its first edit to the parent's session.

   **Expected:** A read or an edit whose path lies inside a session's worktree is
   credited to that session, whichever host made it. A subagent working in its
   own session's worktree satisfies that session's gates and walks that
   session's first-edit edge without a force or a manual transition.

   **Repro:** One project, two live sessions. Bind the host to session A. From a
   subagent of that host (the payload carries ``agent_type``), start session B,
   then fire a PostToolUse ``Read`` naming an architecture doc by its absolute path
   inside B's worktree, and a first edit inside B's worktree. Check
   ``invariant pipeline gates`` on B, and B's session state. Observed: B still
   reports no architecture doc read, B stays ``isolated``, and A's pipeline holds a
   ``docs_read`` entry naming B's worktree path.

   **Suspected cause:** Not yet verified in code. The documented attribution order
   in ``docs/architecture/state_model.md``, "Attribution", puts the host identifier
   (step 2) above the path the call names (step 3), and a subagent shares its
   parent's host identifier. The same section documents one exception: for an
   ACTOR row, when the host names an actor and the location contradicts the
   binding, the location wins (introduced with BUG_028). The docs-read recording
   and the first-edit hook do not apply that exception, so the binding wins and
   the work lands on the parent. Correction to the reporting agent's diagnosis:
   the edit was misattributed too, so it is not only the docs-read path. The
   missing exception is on every recording path except the actor row. BUG_010's
   narrow-half fix pinned "the host identifier still outranks the path (a read
   inside a peer's worktree stays the reader's)" with a test. That is right for a
   main-loop turn reaching into a peer's worktree, and wrong for a subagent that
   created the peer session itself.

   **Affected:** ``hosts/claude_code`` (``_post_tool_use``, and the first-edit hook
   that walks ``isolated -> working``), plus ``Context.session_for_payload`` /
   ``Context.resolution_for_payload`` where the order is applied. See
   ``docs/architecture/state_model.md``, "Attribution" (the order and the actor-row
   inversion), "Host binding", and "The one edge a hook walks: the first edit";
   and ``docs/architecture/overview.md``, "Component responsibilities", on the
   ``agent_type`` key in the normalised payload contract.

   **Fix sketch / next step:** Run ``bug-reproducer`` first. The red test is BUG_010's
   two-session setup with an ``agent_type`` added to the payload: a Read inside B's
   worktree should satisfy B's ``docs.read_architecture``, and a first edit there
   should walk B to ``working``. Then apply the actor-row rule (location wins over
   binding only when the host names an actor) to the docs-read and first-edit
   recording paths. A main-loop turn without ``agent_type`` must stay attributed to
   the reacher, so BUG_010's pinned test stays green as written. Whether that
   inversion may move the host binding, or only the recorded row, is a design
   question for ``feature-architect``. Workaround in use: run the follow-up work
   inside the parent's bound session, or rebind the parent host with
   ``session start`` first.

   **Related:** BUG_010 (fixed), the narrow half that made Read resolve by path at
   all. The repro session ``repro-bug010-read-attribution`` belongs to it. Not a
   duplicate: BUG_010 was a read with no path resolution. This is path resolution
   that loses to a shared host identifier. BUG_028 (fixed) added the actor-row
   inversion this entry asks to extend. BUG_040 (filed, same batch) is the
   cross-repository version of a parent host's binding absorbing work done
   elsewhere. Memory note: "drive a pipeline from a host rooted in that project".

.. bug:: Over HTTPS every connection to the oracle web page crashes: the CLI passes the certificate holder into WebServer's request_timeout
   :id: BUG_083
   :status: fixed
   :severity: critical
   :priority: high
   :area: oracle/web, cli
   :affects: COMP_ORACLE_WEB, COMP_CLI_MAIN

   **Symptom:** Once a certificate is kept, the oracle web page answers nothing
   at all. Observed live on 2026-10-22, right after IDEA_026 merged (commit
   888af9b): a TLS connection to the page is reset at the client hello, and a
   plain-HTTP request to the bare tailnet address gets nothing either, where it
   should get the ``307`` to the https name. The page agent's ``web.log`` shows,
   per connection:
   ``TypeError: 'invariant.oracle.tls.Served' object cannot be interpreted as an integer or float``.
   Whenever HTTPS works, the always-on page is completely dead.

   **Expected:** With a kept certificate the page serves HTTPS at the
   certificate's name and answers a plain-HTTP request with ``307`` (oracle.md,
   "Over HTTPS (IDEA_026)"). The CLI hands the listener its certificate holder
   as the certificate holder.

   **Repro:** Enable HTTPS Certificates on the tailnet, let ``oracle web --wait``
   (the ``dev.invariant.oracle.web`` LaunchAgent) fetch and keep a certificate,
   then connect with any browser or ``curl`` over https or http. Every connection
   fails; ``web.log`` carries the TypeError above. In the suite it never fires.

   **Suspected cause (verified in code):** ``cmd_oracle_web`` in
   ``src/invariant/cli/main.py`` (around line 3316) calls
   ``_oracle_web_server()(app, bound, port, served)``. ``WebServer``'s signature is
   ``(app, addresses, port, request_timeout=..., max_connections=..., served=None)``,
   so the ``Served`` holder lands in ``request_timeout``. ``_Server.finish_request``
   then calls ``request.settimeout(<Served>)`` and raises on every connection.
   The suite missed it on both sides of the seam: the ``RecordingServer`` that
   replaces ``_oracle_web_server`` accepts ``served`` positionally, and the
   real-TLS loopback tests build ``WebServer`` with ``served=`` as a keyword, so
   nothing ever drives the CLI's call against the real constructor.

   **Affected:** the CLI's web server seam (``_oracle_web_server``, ADR-008 D7)
   and the ``WebServer`` listener's TLS path, per
   :doc:`oracle.md </architecture/oracle>`, "Over HTTPS (IDEA_026)" and
   :need:`ADR-008 <DEC_ADR_008>` D19-D24.
   Regression introduced by IDEA_026 (commit 888af9b). Related: BUG_084 (the
   launchd environment bug whose workaround exposed this one).

   **Fix sketch / next step:** bug-reproducer first: a red test that drives
   ``cmd_oracle_web``'s real server factory with a kept fixture certificate (or
   pins the seam's call against ``WebServer``'s actual signature, e.g. a
   ``RecordingServer`` built with ``inspect.signature(WebServer)`` binding) and
   shows a connection fails. Likely fix: pass ``served=`` by keyword. Also make
   the ``RecordingServer`` seam reject arguments ``WebServer`` would misbind, so the
   seam cannot drift from the real constructor again.

   **Live state 2026-10-22:** service was restored by hand by removing the kept
   certificate and restarting the page agent, so the page currently serves
   plain HTTP with the red warning.

   **Repro proof:** tests/test_bug_083_repro.py::test_BUG_083_a_kept_certificate_serves_https_and_redirects_http, committed 186b04c (2026-10-22) on session/841185a1 — runs ``oracle web`` with the suite's certificate recorder handing it fixture certificate a, and lets the command build its listener through the real ``WebServer`` constructor with the command's own arguments (only the bind moved to loopback port 0). It asserts that a TLS client asking for ``/`` at the certificate's name gets 401 and a plain-HTTP request gets ``307`` to ``https://<name>:<port>/``. Today both get ``ConnectionResetError: [Errno 54] Connection reset by peer``, and the server logs, per connection, ``TypeError: 'invariant.oracle.tls.Served' object cannot be interpreted as an integer or float``. That is raised from ``socketserver.StreamRequestHandler.setup``'s ``settimeout``, reached through ``_Handler.setup``; because ``served`` is misbound, ``_Server.served`` is ``None`` and the TLS branch of ``_Server.finish_request`` never runs. Wrapped in a strict xfail. Control (scratch, not committed): the same test with the factory passing the holder as ``served=`` passes, so the harness reaches the surface. The fix deletes the marker and keeps the assertion.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: strict-xfail repro 186b04c
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fix 587c4f8: served= by keyword, recorder holds WebServer''s signature; repro xfail removed'
        forced: false

.. bug:: Under launchd the Tailscale app's CLI starts its GUI instead of answering, so the page agent never fetches a certificate
   :id: BUG_084
   :status: closed
   :severity: high
   :priority: high
   :area: oracle/tls, oracle/launchd
   :affects: COMP_ORACLE_TLS, COMP_ORACLE_LAUNCHD

   **Verified 2026-10-23, device leg done; closed.** After 587c4f8 merged, the
   real dev.invariant.oracle.web LaunchAgent was restarted (launchctl kickstart).
   With no TERM from launchd it fetched and kept the Let's Encrypt certificate:
   web.json showed scheme https, an expiry of 2027-01-20, error none; oracle.pem
   mode 0600. openssl verified the certificate (CN the Mac's tailnet name, issuer
   Let's Encrypt, return code 0). Plain http by IP redirected 307, https by IP
   gave 421, no tracebacks in the log, and a phone then used the page. The
   "device leg owed" residue is discharged.

   **Symptom:** Under launchd the page agent never gets a certificate. Its
   ``web.json`` reports the certificate error
   ``tailscale status --json did not print JSON: Expecting value: line 1 column 1 (char 0)``,
   and the page stays on plain HTTP with the red warning, even though HTTPS
   Certificates is enabled on the tailnet. Observed live on 2026-10-22, right
   after IDEA_026 merged (commit 888af9b).

   **Expected:** With HTTPS Certificates enabled, the LaunchAgent fetches and
   keeps the certificate and serves HTTPS (oracle.md, "Over HTTPS (IDEA_026)").
   Concretely:

   - The oracle runs the Tailscale CLI with ``TERM`` set, either in
     ``run_program``'s environment for that binary or in the web plist's
     ``EnvironmentVariables``.
   - A CLI answer that is not JSON is reported by the CLI's own first line, not
     by a JSON parse error.
   - ADR-008 D19 and the guide (:doc:`The oracle on your phone </guide/oracle-web>`)
     note the requirement.

   **Repro:** With the Mac App Store or standalone Tailscale app,
   ``env -i HOME=$HOME PATH=/usr/bin:/bin /Applications/Tailscale.app/Contents/MacOS/Tailscale status --json``
   prints "The Tailscale GUI failed to start: ... Tailscale.CLIError", exits 0,
   and prints nothing useful on stdout. The same command with ``TERM=xterm`` or
   ``TERM=dumb`` added prints the JSON.

   **Suspected cause (verified live):** the app bundle's binary decides whether
   it is the CLI or the GUI from its environment. With no ``TERM`` it tries to
   start the GUI, fails, and exits 0. The launchd agent's environment has no
   ``TERM``, so every ``status --json`` and ``cert`` call made by ``oracle/tls.issue``
   through ``web.tailscale_binaries`` gets the GUI's failure instead of an
   answer, and the exit status 0 hides it until the JSON parse.

   **Affected:** ``oracle/tls`` (``issue``, the certificate fetch, ADR-008 D19) and
   ``oracle/launchd`` (the ``dev.invariant.oracle.web`` plist's environment), per
   :doc:`oracle.md </architecture/oracle>`, "Over HTTPS (IDEA_026)" and
   "Shipped: the launchd agent", and
   :need:`ADR-008 <DEC_ADR_008>` D19
   ("One lookup for the CLI", which falls back to the app bundle's path).
   Regression of IDEA_026's design assumption (commit 888af9b). Related:
   BUG_083, which the workaround below exposed.

   **Workaround tried 2026-10-22:** adding ``TERM=dumb`` to the plist's
   ``EnvironmentVariables`` got the certificate fetched and kept. That exposed
   BUG_083 (every connection then crashed); the certificate was removed again.

   **Fix sketch / next step:** bug-reproducer first: a red test that runs the
   certificate fetch through the program seam with an environment lacking
   ``TERM`` and asserts the CLI is invoked with ``TERM`` set, plus one that feeds a
   non-JSON answer and asserts the reported error carries the CLI's first line.
   The device leg (a real launchd agent against the app's CLI) stays owed.
   Decide with the architect whether ``TERM`` belongs in ``run_program``'s
   environment for the Tailscale binary (covers address discovery too) or in
   the plist; then update ADR-008 D19 and the guide.

   **Repro proof:** tests/test_bug_084_repro.py, committed b61fb5b (2026-10-22) on session/841185a1, two strict-xfail tests. No ``tailscale`` runs: a stand-in for ``subprocess.run`` plays the app bundle's binary as recorded above (no ``TERM``: the GUI failure on stderr, exit 0; with ``TERM``: the CLI's answers, the fixture certificate).

   - ``test_BUG_084_the_web_agent_fetches_a_certificate_with_no_TERM_from_launchd`` puts the web plist's ``EnvironmentVariables`` in the environment, without ``TERM``. It drives the real certificate seam (``_real_oracle_web_certificates``, through the real ``run_program``) and asserts that it returns the certificate. Today it raises ``CertError: tailscale status --json did not print JSON: Expecting value: line 1 column 1 (char 0)``, the live ``web.json`` error verbatim. The test passes whether ``TERM`` is added in ``run_program``'s environment or in the plist. Control (scratch, not committed): the same test with ``TERM=dumb`` set passes.
   - ``test_BUG_084_an_answer_that_is_not_json_is_reported_by_its_first_line`` asserts that ``issue``'s error carries "The Tailscale GUI failed to start". Today the error is the JSON parse message.
     The real-launchd leg stays owed (residue above).

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: strict-xfail repros b61fb5b
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fix 587c4f8: run_program sets TERM=dumb when missing; non-JSON reported by first line; device leg still owed (residue)'
        forced: false
      - from: fixed
        to: verified
        reason: 'device leg done 2026-10-23: real web LaunchAgent restarted after 587c4f8, fetched and kept the Let''s Encrypt certificate with no TERM from launchd; page served https; residue discharged'
        forced: false
      - from: verified
        to: closed
        reason: verified live on device 2026-10-23; no residue owed
        forced: false

.. bug:: A touched Podfile or Podfile.lock gets an unsatisfiable coverage gate under swift_xcode
   :id: BUG_085
   :status: fixed
   :severity: high
   :priority: high
   :area: adapters/swift_xcode
   :batch: unsatisfiable-gates
   :affects: COMP_ADAPTERS_SWIFT_XCODE

   **Symptom.** In a consuming iOS project's session, ``coverage.meets_floor``
   blocked a finished change because the change touched ``Podfile`` and
   ``Podfile.lock``. The verdict lists both as changed source files with no
   coverage row and no explanation. No test can clear it: ``pipeline ack`` refuses
   a blocking rule, so ``--force`` is the only way past.

   **Expected.** For the ``swift_xcode`` ecosystem, a touched ``Podfile`` or
   ``Podfile.lock`` is set aside as "no executable lines to measure", just as a
   touched ``Info.plist`` has been since :need:`BUG_039 <BUG_039>`. These are CocoaPods
   files, a Ruby DSL and its lockfile. No Xcode target compiles them, so ``xccov``
   can never emit a row for either.

   **Repro.** Run a session in a project using the ``swift_xcode`` adapter, change
   ``Podfile`` (or ``Podfile.lock``) along with any other edit, and run the coverage
   gate. Both files come back as unexplained and the gate blocks. Adapter-level:
   ``SwiftXcodeAdapter.carries_executable_code(Path("Podfile"))`` returns ``None``
   where it should return ``False``. The same holds for ``Podfile.lock``.

   **Suspected cause.** The cause sits in ``adapters/swift_xcode.py``, in
   ``carries_executable_code`` and ``_xcode_resource_kind``. The BUG_039 allowlist is
   keyed on suffix (``_COMPILED_SUFFIXES``, ``_RESOURCE_SUFFIXES``) and on a
   resource-bundle ancestor directory. ``Podfile`` has no suffix at all, and
   ``Podfile.lock``'s suffix ``.lock`` is in neither table. None of the three rules
   fires, so the adapter answers "cannot tell". ``_explain_absent_rows`` in
   ``core/rules.py`` then refuses to relax, which is correct. The rule is not at
   fault. The adapter is missing its ecosystem's dependency-manager manifests.

   **Affected.** The ``swift_xcode`` adapter: see the ``adapters/`` section of
   :doc:`the architecture overview </architecture/overview>`. The gate in
   ``core/rules.py`` (``coverage.meets_floor``, ``_explain_absent_rows``) only
   consumes the adapter's answer.

   **Fix sketch / next step.** Start with ``bug-reproducer``. Write a failing
   adapter assertion that ``carries_executable_code`` answers ``False`` for
   ``Podfile`` and ``Podfile.lock``, both at the root and nested in a subdirectory.
   Add a gate-level test that reproduces the "no coverage row" block. The likely
   fix is an exact-filename allowlist next to the suffix tables, checked after
   the compiled kinds the same way BUG_039's tables are. It must stay an
   allowlist, never a denylist. Questions for the design step:

   - Should sibling dependency manifests ride along, such as ``Gemfile`` /
     ``Gemfile.lock``, ``Cartfile`` / ``Cartfile.resolved``, and SwiftPM's
     ``Package.resolved``? ``Package.swift`` must keep its ``.swift`` content path.
   - The ``Pods/`` directory must NOT be called inert. It holds vendored ObjC/C
     that ``xccov`` can measure (:need:`BUG_041 <BUG_041>`), so the fix covers the
     manifest files and nothing under them.

   Use BUG_039's tightness check again: rerun it over a consuming project's
   tracked files and show that no answer flips ``True`` to ``False``.

   **Related.** :need:`BUG_039 <BUG_039>` (fixed) introduced the allowlist this
   entry extends. This is a gap in that fix's coverage, not a regression of it.
   :need:`BUG_036 <BUG_036>` (fixed) is the same family: a changed non-code file
   that every set-aside branch answered "cannot tell" for. The user ruled this
   an engine defect, so the fix belongs in the adapter, not in a project-side
   override.

   **Repro proof:** ``tests/test_adapters.py::TestCarriesExecutableCode::test_BUG_085_a_cocoapods_manifest_carries_no_executable_code``
   (parametrized over ``Podfile``, ``Podfile.lock``, ``ios/Podfile``, ``ios/Podfile.lock``)
   and ``tests/test_rules.py::TestFilesThatCannotHaveACoverageRow::test_BUG_085_a_touched_podfile_and_lockfile_are_set_aside_and_named``,
   committed e472359 (2026-10-24) on the session branch. The adapter test asserts
   ``carries_executable_code(path, relative)`` is ``False`` for each; today every case
   fails with "assert None is False". The rule test touches ``Podfile`` and
   ``Podfile.lock`` beside a measured Swift file and asserts the gate passes and
   names both as "no executable lines to measure", in the style of the BUG_039
   ``Info.plist`` test; today it fails with "2 changed source file(s) have no
   coverage row" and detail "Podfile, Podfile.lock". The ``Info.plist`` control in
   the same harness passes. Strict xfail; with ``_xcode_resource_kind`` patched to
   answer ``False`` for the two names, all five report XPASS(strict), so the fix
   deletes both markers and the assertions pass unchanged. Red run recorded with
   ``pipeline test --phase reproducing`` (5 strict expected failures read as red).

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: strict-xfail repro committed e472359 on session/7107852a
        forced: false
      - from: reproduced
        to: fixed
        reason: fixed in cbfafcf
        forced: false

.. bug:: A live Claude host reads host_gone: the pid-reuse guard compares the record's UTC procStart with ps's local-time start
   :id: BUG_086
   :status: fixed
   :severity: high
   :priority: high
   :area: hosts/claude_code_launch, oracle/scan
   :affects: COMP_HOSTS_CLAUDE_CODE_LAUNCH, COMP_ORACLE_SCAN

   **Symptom.** On 2026-10-25, right after IDEA_024 (32b104a) and IDEA_027
   increment 6 (1702185) merged, ``invariant oracle list --json`` showed live
   Claude Code hosts as ``alive: false`` with the ``host_gone`` signal and
   ``pid_source: both``, while their processes were running. Three such hosts
   (pids 8601, 94916 and 40767; ``ps`` names 8601 as ``/usr/local/bin/claude`` on
   a terminal) and the host driving the oracle's own session (pid 57127) all
   read ``host_gone``. Consequences: increment 6's Close is never offered, since
   it needs ``alive`` true; and the panel tells the operator a live window closed
   ("Its Claude window closed"), which invites a ``session resume --force`` onto
   a session that is still running.

   **Expected.** A running host whose per-process record and process table
   describe the same process reads ``alive: true``, its pid verified. The start
   comparison treats the record's ``procStart`` as UTC (or compares instants with
   both zones handled), so the same instant spelled in two zones is equal.

   **Repro.** On a Mac whose local zone is not UTC (here UTC+2), compare
   ``procStart`` in ``~/.claude/sessions/<pid>.json`` with ``ps -o lstart= -p <pid>``
   for any running Claude Code host. Every pair observed is the same instant
   exactly the UTC offset apart, to the second: pid 8601 (both on
   2026-10-25), pid 94916 (both on 2026-10-23) and pid 57127 (both on
   2026-10-21) each read two hours earlier in the record than in ``ps``.
   ``invariant oracle list --json`` then reports each of those hosts
   ``host_gone``.

   **Suspected cause.** Claude Code writes ``procStart`` in UTC, while
   ``ps -o lstart=`` prints local time. The reuse guard in
   ``ScanCache.process_alive`` compares the two start tokens
   (``process_start_token``) as plain strings or naive times, reads every pair as
   different, treats the pid as reused, and marks the host not alive. The
   close-host re-check in ``oracle/closehost`` ("``ps`` describes it ... started
   at ``--start``") compares the same way, so it would refuse a Close too. The
   tests missed it because their fixtures wrote ``procStart`` and the canned
   ``ps`` output in the same zone.

   **Affected.** ``hosts/claude_code_launch`` (``ClaudeCodeProbe.process_facts``,
   ``process_start_token``); ``oracle/scan`` (``ScanCache.process_alive``, the
   recorded-pid start check described in
   :doc:`oracle.md, oracle/scan </architecture/oracle>`); ``oracle/closehost``
   (the start check in its refusal table, same doc). ADR-009 D15 (Close host)
   is blocked by it. Introduced by IDEA_024 (32b104a); blocks IDEA_027
   increment 6 (1702185) in practice.

   **Fix sketch / next step.** Red test first, in a non-UTC local zone (set
   ``TZ`` to a fixed non-zero offset in the test), with a record ``procStart`` in
   UTC and canned ``ps lstart`` output in local time for the same instant: the
   host must read alive and the close re-check must pass; today both refuse.
   Fix in ``process_facts`` / ``process_start_token``, one of: run ``ps`` with
   ``TZ=UTC`` in its environment so both sides are UTC, or parse both sides to
   an epoch, the record as UTC and ``ps`` as local time. Keep the existing rule
   that a start either side cannot read is not compared. Check that the
   close-host path uses the same normalised token, not a second comparison.

   **Repro proof:** ``tests/test_bug_086_repro.py::test_BUG_086_a_live_host_reads_alive_when_procStart_is_UTC_and_ps_is_local``
   and ``tests/test_bug_086_repro.py::test_BUG_086_close_host_start_recheck_accepts_the_same_process``,
   committed d2430ea (2026-10-25) on the session branch, both strict xfail. With
   the local zone fixed at UTC+2 (``Etc/GMT-2``), a synthetic record whose
   ``procStart`` is one instant in UTC, and canned ``ps`` output printing the same
   instant in local time, both read through the real ``ClaudeCodeProbe``: the scan
   test asserts the host row reads ``alive`` true; today it reads ``alive=False``,
   its two start tokens exactly the two-hour offset apart. The close-host test
   asserts the start re-check passes and one SIGTERM goes to a fake; today it
   refuses with "process 8601 started at another time than the Claude window
   confirmed, so it is another process". The same tests pass with ``ps`` printing
   the record's zone, which is why the earlier fixtures missed it. Checked
   read-only against live records on 2026-10-25: ``TZ=UTC ps -o lstart=`` matches
   each record's ``procStart`` exactly.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: strict-xfail repro committed d2430ea on session/903da837
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fixed in b876e41: ps runs with TZ=UTC so its start matches Claude Code''s UTC procStart'
        forced: false

.. bug:: A long-idle host reads orphan_host active because a stale running turn outranks Claude Code's own idle status
   :id: BUG_087
   :status: fixed
   :severity: high
   :priority: high
   :area: oracle/signals, oracle/scan
   :affects: COMP_ORACLE_SIGNALS, COMP_ORACLE_SCAN

   **Symptom.** On 2026-10-25, with main at e06d5ff (after IDEA_027 increment 6,
   1702185, and the Attach-uniqueness follow-up), six live Claude Code hosts in
   the Heap project that had been idle for 14 to 44 hours were classed
   ``orphan_host`` with cause ``active``, so Close was never offered for any of them.
   They were pids 8601, 50673, 27516, 94916, 69065 and 40767; every one was
   alive, with ``pid_source: both``, ``rooted_here`` true and no conflict. Each row
   also carried a signal that contradicts "active": five showed ``your_turn``
   ("idle at the prompt"), and one showed ``stuck`` ("a running turn, silent for
   880 min").

   **Expected.** A host idle for longer than ``Thresholds.orphan_idle_minutes``
   (60) whose Claude Code per-process record says it is not busy is classed
   ``leftover`` or ``unmanaged``, not ``active``, so Close is offered under ADR-009
   D15's guards. A host whose record says ``busy``, or that was heard from within
   60 minutes, stays ``active``. A host waiting on a permission prompt or a
   question is never closeable without the user seeing it: while the record's
   status is ``waiting``, the host is not closeable (proposed; the fix must state
   how ``waiting`` is treated).

   **Repro.** On a machine with a project whose hosts' Stop hooks record no
   evidence (see below), leave a Claude Code host idle at the prompt for more
   than an hour after a turn, with its session ended or never bound. Its
   attention record still says ``turn: running``. ``invariant oracle list --json``
   classes the host ``active``, while ``<claude home>/sessions/<pid>.json`` for the
   same pid reads ``status: idle`` (one of the six read ``waiting``; a host really
   mid-turn reads ``busy``) and the BUG_078 last-turn time is hours old.

   **Suspected cause.** ``signals.orphan_class`` returns ``ORPHAN_ACTIVE`` whenever
   the host's attention record has ``turn == "running"``, whatever its age. All six
   attention records said ``turn: running`` and were stale: the Stop hook never
   wrote ``turn-ended`` for these Heap hosts. The likely reason is the
   cross-project hook attribution gap (hooks fired by a host rooted in another
   project record no evidence; compare BUG_040). The fresher fact is already on
   disk: Claude Code's per-process record, read since IDEA_024, carries a
   ``status`` field the scan does not use.

   **Affected.** ``oracle/signals`` (``orphan_class``, and the ``stuck`` /
   ``your_turn`` signals that disagree with it), ``oracle/scan`` (the per-process
   record join, which reads the pid, ``procStart`` and ``cwd`` but not ``status``) —
   see docs/architecture/oracle.md, "oracle/scan" and "oracle/signals", and the
   class table in docs/architecture/oracle-panel.md. Downstream: ``oracle/recommend``
   (the close-host rows for ``leftover`` / ``unmanaged``) and the panel's leftovers
   screen. The gate cache's "Never while a turn runs" rule reads the same
   attention ``turn``, so a stale ``running`` turn on a live host also keeps that
   session from ever being evaluated; check it in the same fix.

   **Fix sketch / next step.** Red test first: an orphan host, alive, last turn
   hours old, attention ``turn: running``, per-process record ``status: idle``;
   assert the class is ``leftover`` (session ended) or ``unmanaged`` (never bound),
   not ``active``. Two directions, not yet chosen: (a) when the record's ``status``
   is present it outranks the attention ``turn`` (``busy`` → active, ``idle`` →
   idle, ``waiting`` → not closeable); or (b) a ``running`` turn with no activity
   for longer than the idle edge stops counting as mid-turn. Either way: keep
   ``active`` for ``busy`` or activity within 60 minutes, decide ``waiting``
   explicitly, and keep the signals consistent with the class (a host the
   record calls idle should not read ``stuck``). Regressed against recent work:
   IDEA_027 increment 6 (1702185) introduced the classes; e06d5ff is the head
   it was observed on. Related: IDEA_024 (record source), BUG_078 (last-turn
   time), BUG_086 (three of these hosts, 8601, 94916 and 40767, read ``host_gone`` before b876e41).

   **Repro proof:** ``tests/test_bug_087_repro.py::test_BUG_087_a_long_idle_host_with_a_stale_running_turn_is_offered_close``,
   committed 0d62b20 (2026-10-25) on the session branch, strict xfail. An alive
   orphan host (a synthetic record read through the real ``ClaudeCodeProbe``,
   ``pid_source: both``, ``rooted_here`` true, no conflict, not claimed elsewhere),
   bound to a torn-down session, last turn and attention record 15 hours old,
   attention ``turn: running``, per-process record ``status: idle``. The
   ``oracle list --json`` document (``assess``) gives it ``orphan_host`` cause
   ``active`` and the step "start or resume a session from that host's own
   terminal" with no argv, where ``leftover`` and ``invariant oracle close-host``
   are expected. A control run with only the attention ``turn`` changed to
   ``ended`` passes the same assertion, so the stale running turn is the one
   input that flips it. Asserts the class and the offered step, not how
   ``orphan_class`` reads ``turn``.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red test 0d62b20 on session 8b269575
        forced: false
      - from: reproduced
        to: fixed
        reason: Fixed in 3e39af0
        forced: false

.. bug:: A scoped coverage run that executed no tests is refused as a likely build or launch failure, not as a selector that matched nothing
   :id: BUG_088
   :status: fixed
   :severity: low
   :priority: low
   :area: domains/pipeline
   :affects: COMP_DOMAINS_PIPELINE

   **Symptom.** ``invariant pipeline coverage --selector <sel>`` (the scoped run,
   ``commands.coverage_one``) whose run executed no tests is refused by
   ``measure_coverage`` with "the coverage run executed no tests, so its
   measurement describes nothing" and the hint "Usually a build or launch
   failure rather than a coverage problem — read the build output first." For a
   scoped run that hint points the reader away from the likelier cause: a
   selector that matched nothing. Under ``swift_xcode`` that is a selector the
   ``-only-testing:`` filter does not resolve (the shape the
   ``xcode.only_testing_takes_target_class`` hazard names), which builds, launches
   and exits clean having run nothing.

   **Expected.** The refusal itself is right and stays. Its hint depends on the
   run's scope: a scoped run that executed zero tests names the selector first
   ("the selector may have matched nothing — check it"), and a build or launch
   failure second; a full run (no selector, including the sweep's suite run in
   ``domains/sweep.py``) keeps the build-or-launch hint. This matches the wording
   ``test.all_pass`` already uses for a clean zero-executed run, whose fix is
   "check the test selector".

   **Repro.** In a project with ``commands.coverage_one`` configured, run
   ``invariant pipeline coverage --selector <a selector that names no test>``.
   The command refuses, nothing is recorded, and the message blames a build or
   launch failure although the build output is clean. Observed in a consuming
   Swift project while wiring IDEA_032's scoped evidence; the exact selector
   spelling it used is unconfirmed (the report described a comma-joined set in
   one ``-only-testing:`` flag, which ``--selector`` never produces on its own, so
   the trigger is left unrecorded here).

   **Suspected cause.** ``measure_coverage`` in ``domains/pipeline.py`` is the one
   seam for scoped and full coverage runs (ADR-010 D4), and its ``total == 0``
   refusal carries a single hint written for the full-run case. It is not told
   whether the command it runs is ``coverage`` or ``coverage_one``, so it cannot
   pick the hint.

   **Affected.** ``domains/pipeline`` (``measure_coverage``, called by ``pipeline coverage`` with ``coverage_one`` when selectors are given) — see
   docs/architecture/overview.md, "Component responsibilities" / ``domains/``, and
   the ``pipeline coverage`` entry in docs/architecture/cli.md. The sweep's full
   suite run in ``domains/sweep.py`` carries the same hint, correctly, and should
   stay as is.

   **Fix sketch / next step.** Red test first: a scoped coverage run whose
   adapter reads ``total == 0`` with exit 0; assert the refusal names the
   selector(s) and says a selector that matched nothing is the likely cause.
   Then pass the scope (or the selectors) into ``measure_coverage`` and choose the
   hint from it. Message-only; no rule, schema or record change. Low severity:
   nothing wrong is recorded, the run is refused either way, and the cost is a
   misdirected diagnosis.

   **Related.** :need:`BUG_038 <BUG_038>` — a swift-testing run that really ran can
   scrape as zero executed, so under ``swift_xcode`` a zero is ambiguous between
   "matched nothing" and "counted nothing"; the scoped hint should not claim
   more than the count supports. :need:`BUG_029 <BUG_029>` hardened the rule side
   against ``executed == 0``; this is the same refusal's wording one layer out.
   :need:`IDEA_032 <IDEA_032>` made scoped coverage the normal per-change run, which
   is why this hint now fires on the common path.

   **Repro proof:** ``tests/test_domains.py::TestBUG_088_ScopedZeroExecutedRefusal::test_BUG_088_scopedZeroExecutedRefusalNamesTheSelector``,
   committed d1e0200 (2026-10-27) on the session branch, strict xfail; recorded
   red via ``pipeline test`` in ``reproducing``. A swift-ecosystem project whose
   ``commands.coverage_one`` is a script printing a clean xcodebuild run that
   executed 0 tests and exits 0; ``run_coverage`` with the selector
   ``AppTests/NoSuchClass``. The run is refused with "executed no tests" (correct),
   and the refusal's only hint is "Usually a build or launch failure rather than
   a coverage problem — read the build output first." The assertion asks that
   the refusal say a selector may have "matched nothing", without requiring
   certainty (BUG_038). The adapter's own outcome note for this run already reads
   "check the -only-testing: selector"; ``measure_coverage`` does not surface it.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red test d1e0200 on session 71c7ee75
        forced: false
      - from: reproduced
        to: fixed
        reason: fix commit d4ee3e9 on session 71c7ee75
        forced: false

.. bug:: An oracle started before an upgrade calls every project unreadable
   :id: BUG_089
   :status: fixed
   :severity: high
   :priority: high
   :area: oracle/scan,oracle/watcher,oracle/launchd,oracle/web
   :affects: COMP_ORACLE_SCAN, COMP_ORACLE_WATCHER, COMP_ORACLE_LAUNCHD, COMP_ORACLE_WEB

   **Symptom.** After a change to invariant's shipped data files lands on the
   main checkout, the long-running oracle processes (the watcher LaunchAgent
   running ``oracle watch``, and the web page LaunchAgent running ``oracle web``)
   report every registered project as unreadable. Observed 2026-10-27: the
   Session Oracle phone page showed one PROBLEMS card per registered project
   ("A record invariant cannot read" / "Cannot read this project") and nothing
   else. The panel blames the project; the project is fine. The current build
   read both projects cleanly, and restarting both agents (``launchctl kickstart -k`` on each) cleared it — the next snapshot carried zero error rows.

   **Expected.** A long-running oracle whose installed code or data has changed
   underneath it does not present that as "this project cannot be read". It
   either notices the skew and restarts itself (re-exec, or exit and let the
   LaunchAgent's ``KeepAlive`` restart it), or it says so distinctly — "the
   oracle is older than the installed invariant; restart it" — as a single
   oracle-level problem, not one project error per project. ``invariant doctor``
   and ``invariant oracle status`` report the skew too.

   **Repro.** Start the oracle agents, then merge a change that adds a rule id
   to a shipped profile. Observed with IDEA_032 (e417275..ac73b18):
   ``src/invariant/data/profiles/{strict,standard,loose}.yaml`` now name
   ``sweep.no_full_run`` under ``enforcement.rules``. The install is editable
   against the main checkout, so the running processes keep the OLD in-memory
   rule registry while reading the NEW profile YAML from disk. Manifest
   validation (``src/invariant/core/manifest.py``, the unknown-rule check) then
   refuses every manifest with "enforcement.rules names 'sweep.no_full_run',
   which is not a rule this build carries", ``scan_project`` in
   ``src/invariant/oracle/scan.py`` turns that into one project ``error`` row, and
   ``src/invariant/oracle/panel.py`` renders it as the "Cannot read this project"
   card.

   **Suspected cause.** Version skew between a long-lived process and the
   editable install, with no detection. Two layers:

   - The oracle processes never check whether the code/data they were started
     from is still what is installed. Any change to shipped data files — a new
     rule id in a profile, a new machine, a new agent — breaks a running oracle
     the same way, not just this one.
   - ``scan_project``'s catch-all maps every load failure to the project-level
     ``error`` row, so a skew failure (identical across every project, and
     caused by the oracle itself) is indistinguishable from a genuinely broken
     manifest.

   **Affected.** ``oracle/scan`` (``scan_project``, the ``error`` row) and
   ``oracle/watcher`` — see docs/architecture/oracle.md, "Shipped: the scan, the
   signals and the recommendations" and "Shipped: the watcher and the
   notifier"; ``oracle/launchd`` and ``invariant oracle install``, ``uninstall`` and
   ``status`` (same doc, "Shipped: the launchd agent"); the web page (same doc,
   "Shipped: the web page (IDEA_023 increment 1)"); the panel's ``error``, scan
   wording in docs/architecture/oracle-panel.md, "Wording" / "States (chips and
   sentences)"; the doctor's oracle rows in docs/architecture/cli.md. The
   manifest's unknown-rule refusal is correct and stays.

   **Fix sketch / next step.** Red test first, at the scan seam: run
   ``scan_project`` with a rule registry that lacks a rule id (simulating the
   older in-memory build) against a project whose profile names that id;
   today it returns the single project ``error`` row the panel renders as
   "Cannot read this project". Assert instead that the skew is detected and
   surfaced distinctly (an oracle-level "restart the oracle" problem, not a
   per-project unreadable row), and/or that the long-running loop detects the
   change and restarts. Candidate detection signals: the package version, a
   build stamp, or the mtimes of the installed ``src/invariant`` tree (code and
   ``data/``) captured at start and compared each tick. Candidate responses:
   re-exec, or a clean exit that ``KeepAlive`` restarts; plus a distinct panel
   message and a doctor / ``oracle status`` row for the skew. No schema change
   expected.

   **Related.** :need:`BUG_061 <BUG_061>` — the same editable-install skew
   (an older in-memory build against newer on-disk state), on records rather
   than rules. :need:`IDEA_027 <IDEA_027>` (the mobile control panel this card
   appears on), :need:`IDEA_030 <IDEA_030>` (start-session, which also runs from
   the long-lived web process), :need:`IDEA_032 <IDEA_032>` (the change that
   exposed it). The dashboards are expected to refresh themselves and survive
   failed reads; this is a failed read they cannot recover from without a
   restart.

   **Repro proof:** ``tests/test_oracle_scan.py::TestVersionSkew::test_BUG_089_a_stale_rule_registry_is_not_every_project_unreadable``,
   committed bcde5cb on session/4a8cfa13 (2026-10-27). Two healthy projects scan
   clean; with ``sweep.no_full_run`` removed from the rule registry while the
   shipped default profile names it, ``scan_all`` returns one project-level error
   row per project (the "Cannot read this project" card), each carrying the
   manifest's unknown-rule refusal. The test asserts no such row; strict xfail,
   delete the marker with the fix. It pins the scan seam only: a restart-only fix
   leaves it red.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'red at the scan seam: tests/test_oracle_scan.py::TestVersionSkew::test_BUG_089_a_stale_rule_registry_is_not_every_project_unreadable (bcde5cb, session/4a8cfa13)'
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fixed by 2ca87c4 (session/4a8cfa13): installed-tree stamp, skew card, watcher and web re-exec'
        forced: false

.. bug:: On a project with device_ids enabled, sweep run can never commit its record: full 40-hex shas trip the scan
   :id: BUG_090
   :status: fixed
   :severity: high
   :priority: high
   :area: sweep,privacy
   :affects: COMP_DOMAINS_SWEEP, COMP_CORE_PRIVACY

   **Repro proof:** ``tests/test_sweep_domain.py::test_BUG_090_a_sweep_commits_on_a_project_with_device_ids_enabled``,
   committed 1c9c5ef on session/1626be86 (2026-10-27). In a fixture project whose
   ``privacy.patterns`` is the default set plus ``device_ids``, the baseline sweep
   must commit its record, then a later sweep must commit the record and the
   ``test:`` bug it files. Today the baseline is refused: ``committed`` is False,
   "the privacy scan found personal data, so nothing was committed", one
   ``[device_ids]`` finding in the record, on the line of its ``head`` field. Strict
   xfail; delete the marker with the fix. A read-only probe of the second leg
   (baseline committed without ``device_ids``, then the filing sweep with it)
   confirmed the filed item trips too: the record's ``head`` and ``previous``, the
   item's ``sweep_range`` frontmatter and its body's commit range line, so a fix
   that shortens only the record leaves this test red. Side note for the fix:
   item findings are described with the item's absolute path (``scan_file`` on
   ``self.root / rel``), unlike the record's project-relative one.

   **Symptom.** On a project whose ``privacy.patterns`` enables ``device_ids``,
   ``invariant sweep run`` never commits its record. Observed 2026-10-27 on a
   downstream iOS project's first baseline sweep after IDEA_032 shipped
   (ac73b18): the coverage lane completed (17547 tests executed, 80 failed,
   hazard ``xcode.exit65_drops_suites``), then the sweep refused itself with
   "NOT committed: the privacy scan found personal data —
   ``docs/sweeps/<record>.json:<line>:<col> [device_ids]``". The record is left
   untracked in ``docs/sweeps/``, nothing is committed, ``sweep status`` still
   reports no sweep, and every scheduled run hits the same wall — so
   ``sweep.fresh`` warns forever. The only ways out today are disabling
   ``device_ids`` (which the project will not do) or forcing; neither is a fix.

   **Expected.** A sweep record and the bug entries a sweep files are
   privacy-clean under every pattern the engine ships, including the opt-in
   ``device_ids``, so a project that enables it can run and commit sweeps.
   ADR-010 D9 promises the record is "privacy-clean by construction, then
   checked"; the check should never be the thing that fails on the engine's
   own output.

   **Repro.** In a project whose manifest sets ``privacy.patterns`` to include
   ``device_ids``, on a clean main checkout with a sweep cadence configured, run
   ``invariant sweep run``. The record's ``head`` field is a full 40-hex commit
   sha; the scan refuses it and the commit never happens.

   **Suspected cause.** Verified read-only, 2026-10-27:

   - ``src/invariant/core/privacy.py``: the ``device_ids`` pattern's third arm is
     ``[0-9A-Fa-f]{40}`` bounded by ``\b``. The comment above ``DEFAULT_PATTERNS``
     says this arm "matches every full git commit hash", which is why
     ``device_ids`` is opt-in rather than default. It is working as designed.
   - ``src/invariant/domains/sweep.py``, ``_sweep``: ``head`` comes from
     ``git.resolve_commit(root, "HEAD")`` (full hash) and is written to the
     record's ``head``; ``previous`` copies the prior record's ``head``, also full.
     The range ``rng = "<previous.head>..<head>"`` (two full hashes) goes into
     ``_Context.range``.
   - Same file, ``_file``: every bug the sweep files carries
     ``fields={"sweep_key": ..., "sweep_range": context.range}`` and ``_describe``
     puts the same range in the body ("Commit range since the last sweep").
     ``_record_and_commit`` scans those item files too, so a sweep that files
     anything would be refused a second time even once the record is fixed.
   - The commit message and the record file name (``record_name``,
     ``<date>-<head[:12]>.json``) already use 12-char short shas, and so do the
     rule messages in ``core/rules.py`` and ``sweep status``.
   - ADR-010 D9 specifies ``head`` and ``previous`` as full hashes, while
     promising the record holds only "hashes, a bare date, project-relative
     paths" — the two promises collide under ``device_ids``.

   **Affected.** The sweep record writer and its filing step:
   ``domains/sweep`` (``_sweep``, ``_file``, ``_describe``, ``_record_and_commit``) and
   ``core/sweep`` (``SweepRecord``, ``record_name``, ``latest``, ``diff``) — see
   docs/architecture/cli.md, "Sweep", and ADR-010 D9 (the committed,
   privacy-scanned record store). ``core/privacy`` is correct and stays.

   **Fix sketch / next step.** Red test first: a sweep run in a fixture
   project with ``device_ids`` enabled refuses to commit today; assert it
   commits. Fix direction: write every commit sha the sweep persists the way
   the record file name already does — 12 hex digits. Audit every hash the
   sweep writes: record fields ``head`` and ``previous``, the ``sweep_range``
   field and body text of filed bug entries, and any culprit or per-finding
   commit fields. Check the readers stay stable: nothing found in the
   read-only pass resolves a record's hash back through git (records are read
   via ``git ls-tree`` / ``git show`` on a branch ref, not by sha); ``sweep show <head prefix>``
   matches by ``startswith`` with a 7-char minimum, so 12-char
   heads still resolve; ``previous`` must compare equal across old full-hash
   records and new short ones, so normalise or compare by prefix where a
   record is matched by head, and keep the diff/dedup keys (``sweep_key``) free
   of hashes as they are today. Already-committed records elsewhere carry
   full hashes; the reader must accept both. Update ADR-010 D9's field list
   (full -> 12-char short hashes) and the cli.md sweep text to match. No
   schema version bump needed if the reader accepts both lengths.

   **Related.** :need:`IDEA_032 <IDEA_032>` (done — shipped the sweep, ac73b18),
   ADR-010 D9 (committed store, privacy-scanned, records hold only hashes, a
   bare date and relative paths). Closing obligation: when fixed, tell the
   downstream project's session that hit it (s-1-sync-foundations, reachable
   via the No-full-suite session) so it re-runs the baseline sweep.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'red at the sweep seam: tests/test_sweep_domain.py::test_BUG_090_a_sweep_commits_on_a_project_with_device_ids_enabled (1c9c5ef, session/1626be86)'
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Fixed in 680d00a: the sweep writes 12-hex shas; readers match by prefix'
        forced: false

.. bug:: The xcode.only_testing_takes_target_class hazard fires on a full-suite run: any empty sub-suite prints Executed 0 tests
   :id: BUG_091
   :status: filed
   :severity: low
   :priority: low
   :area: adapters/swift_xcode
   :affects: COMP_ADAPTERS_SWIFT_XCODE

   **Symptom:** A full-suite run under ``swift_xcode`` that executed thousands of
   tests still carries the hazard ``xcode.only_testing_takes_target_class``
   ("-only-testing: takes Target/Class, never a folder path"). Observed
   2026-10-27 on a downstream iOS project's first baseline sweep: the committed
   sweep record lists that hazard id under ``hazards`` for the full-suite lane,
   and a sweep that files bugs repeats it in each filed body's "Hazards on the
   run:" line (``domains/sweep.py``). No filter was involved and no re-run caused
   it — ``core.sweep.needs_reruns`` returns false on a baseline.

   **Expected:** The hazard fires only when it describes the run: a filtered
   run whose ``-only-testing:`` value is folder-shaped, or a run whose TOTAL
   executed count is zero. A sub-suite's own "Executed 0 tests" line inside a
   run that executed tests does not fire it.

   **Repro:** Feed ``SwiftXcodeAdapter``'s hazard detection the combined output of
   a full xcodebuild test run that contains at least one empty sub-suite (an
   "Executed 0 tests, with 0 failures" line for that suite) and a final summary
   line with a non-zero executed count. The hazard is reported. Any real full
   suite with an empty test class or bundle does the same.

   **Suspected cause:** The hazard's ``detect`` regex in
   ``src/invariant/adapters/swift_xcode.py`` is
   ``-only-testing:[^\s']*/[^\s']*\.(swift|m)\b|Executed 0 tests``. ``Hazard``
   matches ``detect`` against the run's combined output (``adapters/base.py``), so
   the second alternative matches ANY "Executed 0 tests" line. xcodebuild prints
   one per suite, nested, so every empty sub-suite in a full run trips it.

   **Affected:** ``adapters/swift_xcode`` (the ``hazards()`` list) — see
   docs/architecture/manifest.md, "Adapters" (``hazards()`` and "A hazard is not a
   rule"). The record's ``hazards`` field — docs/architecture/state_model.md,
   "Domain: sweep" / "The record". The sweep itself: ADR-010
   (docs/adr/ADR-010-whole-suite-evidence-moves-to-a-scheduled-sweep.md).

   **Impact / why low:** Nothing is blocked or misread as green:
   ``can_overstate=False``, so the hazard never gates a coverage figure. The cost
   is a misleading annotation on a committed record and on filed bodies, which
   teaches readers to ignore the hazard where it is true. Already safe on the
   neighbouring path: ``_red_alone`` in ``domains/sweep.py`` reads a zero-executed
   re-run as red, so a re-run whose selector matched nothing cannot recover a
   red silently; this entry does not touch that.

   **Fix sketch / next step:** Red test first in ``tests/test_adapters.py``:
   output with an empty sub-suite's "Executed 0 tests" line plus a non-zero
   final total; assert the hazard is absent. Keep two positive cases green: a
   folder-shaped ``-only-testing:`` value, and a run whose only/final executed
   count is zero (``tests/test_adapters.py`` already exercises a bare "Executed 0
   tests, with 0 failures"). Fix direction: tie the zero alternative to the
   run's total rather than to any suite line — either a regex that matches only
   the last "Executed N tests" line (no later "Executed" line follows), or a
   detection hook that consults the parsed ``total``, if ``Hazard`` grows one.
   Message/annotation only; no rule, schema or record-format change.

   **Related.** :need:`BUG_088 <BUG_088>` — the scoped zero-executed refusal; it
   names this hazard as the shape of a selector that matched nothing, which is
   the case the hazard should keep firing on. :need:`IDEA_032 <IDEA_032>` / ADR-010
   — the sweep whose record carries the false hazard. :need:`BUG_038 <BUG_038>` — a
   swift-testing run can scrape as zero executed, so a total-based condition
   inherits that ambiguity until BUG_038's counting half lands.

.. bug:: Oracle panel's "N live sessions" reads as running but counts paused sessions in every section
   :id: BUG_092
   :status: fixed
   :severity: low
   :priority: low
   :area: oracle/panel
   :affects: COMP_ORACLE_PANEL

   **Fixed 2026-10-28** (commit 53e5e3d). The Projects line now reads open sessions with a paused count.

   **Symptom:** On the mobile panel a project's line under Projects read
   "2 live sessions" while the user saw only one of that project's sessions
   under Working/Idle, and read the count as wrong. Reported 2026-10-28. The
   count was right by its own definition: the second session (state
   ``isolated``, host ``host_ended``, failing tests) was on the panel under
   Problems, and its own card said "Paused" and "Its Claude window closed".
   "Live" reads as "running", so a paused session counted as live contradicts
   the card beside it.

   **Expected:** The Projects line names what it counts in the panel's plain
   language: "N open sessions" / "1 open session" / "No open session", with
   "· N paused" appended when one or more of the counted sessions has no live
   host (``host_ended`` or ``parked``). A reader can match the number to the
   session cards in Needs you, Problems, Working and Idle.

   **Repro:** Register a project with two sessions in non-ended states, one of
   them with its host ended (so it groups under Problems or Idle, not
   Working). Open the panel's Projects section or the New session sheet: the
   project line reads "2 live sessions" with no hint that one is paused.

   **Suspected cause:** Wording, not counting. ``_start_projects`` in
   ``src/invariant/oracle/panel.py`` (around lines 1173-1182) counts every
   ``ROW_SESSION`` row of the project, and the scan emits one such row per
   session whose state is not ended (``src/invariant/oracle/scan.py``, around
   lines 642-647). Host attachment plays no part, by design. The strings are
   ``sessions_none`` / ``sessions_one`` / ``sessions`` in the ``_START`` word list
   (panel.py around lines 435-437): "No live session", "1 live session",
   "{n} live sessions". Those rows are spread over four groups by ``group_of``
   (panel.py around lines 101-107 and 601-611, first match wins), so the
   count is the sum of session cards across Needs you, Problems, Working and
   Idle, which the doc never says.

   **Affected:** The oracle panel's Projects section and New session sheet —
   docs/architecture/oracle-panel.md, "7 · New session (IDEA_030)" (line 260),
   "Groups and tiles" (lines 410-415), "Fixed strings" (line 495), and "As
   built in IDEA_030 increment 2" (line 627). The live-session definition —
   docs/architecture/oracle.md, the ``session`` row (line 316).

   **Fix sketch / next step (agreed with the user):** Red test first on
   ``_start_projects``: a project document with two session rows, one whose
   host is ended; assert the line reads "2 open sessions · 1 paused". Keep
   cases for zero ("No open session"), one ("1 open session") and none paused
   (no suffix). Rename the three ``_START`` strings from "live" to "open"; add
   the "· N paused" suffix counted from session rows whose host is
   ``host_ended`` or ``parked``. Update oracle-panel.md at lines 260, 495 and 627,
   and state that the count equals the sum of session cards in Needs you,
   Problems, Working and Idle, and that open means not ended, not "a Claude
   window is attached". Wording and one derived count; no scan, schema or
   grouping change.

   **Impact / why low:** Nothing is miscounted or hidden; the paused session
   is shown, under Problems. The cost is a reader doubting a correct number.

   **Related.** :need:`IDEA_027 <IDEA_027>` — the mobile control panel this
   wording belongs to. :need:`IDEA_030 <IDEA_030>` — the New session sheet that
   introduced the Projects line.

   **Repro proof:** ``tests/test_bug_092_repro.py::test_BUG_092_projects_line_separates_paused_from_open_sessions``,
   committed e74c7ed (2026-10-28) on the session branch, strict xfail. A
   project document holds two session rows, one working (``turn_state`` ``busy``,
   a live host) and one whose host ended (``host_ended``, its host not alive);
   the real ``panel.view`` with acting and starting on groups the first under
   Working (asserted, so the scenario is the reported one) and renders the
   project's Projects line as "2 live sessions" where "2 open sessions ·
   1 paused" is expected. Red recorded through ``invariant pipeline test`` with
   the marker removed (1 executed, 1 failure, the assertion on the line).

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red test e74c7ed on session 980e0254
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fixed by 53e5e3d (session/980e0254): Projects line reads open sessions with a paused count'
        forced: false

.. bug:: Oracle "Re-run tests" can't resolve a session's own discovered values and runs the whole suite
   :id: BUG_093
   :status: fixed
   :severity: medium
   :priority: medium
   :area: oracle/actions
   :affects: COMP_ORACLE_ACTIONS

   **Fixed 2026-10-28** (commit faee8b5). Re-run now replays the session's own scoped run and values. Flagged discovered values are masked on the phone (user ruling: use it, but mask it). Records written before the fix can still show the resolved command in the snapshot.

   **Symptom:** On the mobile panel the user pressed "Re-run tests…" on a
   Problems card for a session in another registered project (state
   ``isolated``, its Claude window closed, failing tests). Reported 2026-10-28.
   It failed after about 1 s with "Failed, exit 2", and the WHY line read
   "invariant could not find a value for destination_id." The same action is
   also unscoped: it runs ``invariant pipeline test`` with no selector, so had
   it resolved it would have run that project's whole suite.

   **Expected:** The phone's re-run resolves the session's own values (here,
   the simulator that session's own shell pinned) and runs only the session's
   own scoped tests, the selector its last recorded ``pipeline test`` used,
   never the whole suite (ADR-010, ``sweep.no_full_run``). Where the session has
   no such values on record, the panel does not offer "Re-run tests…" (or
   offers Resume instead) and says why. Where a discovery does fail, the WHY
   line says the value was ambiguous or unset and names the environment
   variable to export, rather than "could not find a value".

   **Repro:** A project whose manifest discovers ``destination_id`` with a script
   that echoes ``INVARIANT_DESTINATION_ID`` if set, else the single booted
   simulator, else exits 1 on purpose (two or more booted, nothing exported).
   Each session of that project exports its own ``INVARIANT_DESTINATION_ID`` in
   its own shell. Boot two or more simulators, leave a session of that project
   with failing tests on a Problems card, and press "Re-run tests…" on the
   phone: exit 2 in about a second, WHY "could not find a value for
   destination_id". Reproduced identically from a normal shell running the
   same argv with the same environment, so it is not a launchd PATH or
   DEVELOPER_DIR issue, and the manifest behaves as designed.

   **Suspected cause:** The action never carries the session's own context.
   ``src/invariant/oracle/recommend.py`` (around lines 239-243) builds argv
   ``invariant pipeline test``, cwd the session worktree, env pinned to
   ``INVARIANT_SESSION_ID`` only; ``src/invariant/oracle/actions.py`` (around
   lines 179-191 and 240-247) runs it with child_env = the oracle's own
   environ plus ``step.env``. The per-session ``INVARIANT_DESTINATION_ID`` lives
   only in the session's own shell, so the action cannot succeed for such a
   project while two or more simulators are booted. No selector is passed,
   hence the whole-suite run. The exit code and wording are each correct on
   their own terms: ``_discover`` in ``src/invariant/core/manifest.py`` (around
   lines 717-745) raises ``ManifestError``, a ``UsageError`` (exit 2,
   ``src/invariant/core/errors.py`` around lines 38-47); line 743 already
   appends the discovery script's stderr. But ``src/invariant/oracle/panel.py``
   (around lines 459-460) maps "discovery for 'X' produced nothing" to "could
   not find a value for X", hiding that the choice was ambiguous.

   **Affected:** The oracle's recommendations and actions —
   docs/architecture/oracle.md, "``oracle/recommend``", "Confirm-first
   actions" and "Actions from the page". The panel's action sheet and WHY
   strings — docs/architecture/oracle-panel.md, "3 · Confirm (a safe step)",
   "5 · Result (failed)", "Steps" (the ``pipeline test`` row) and "As built in
   increment 5" (the discovery wording). The no-full-run ruling —
   docs/adr/ADR-010-whole-suite-evidence-moves-to-a-scheduled-sweep.md.
   Manifest discovery itself (``_discover`` in ``core/manifest.py``) is not
   documented in docs/architecture/manifest.md.

   **Fix sketch / next step (agreed with the user):**

   1. Each session records its discovered values (or a declared env
      allowlist, e.g. ``INVARIANT_DESTINATION_ID``) in session state when its
      own ``pipeline test`` runs; the oracle's ``pipeline test`` step replays them
      in ``step.env`` alongside ``INVARIANT_SESSION_ID``, so the action resolves
      the session's own simulator, never a guess.
   2. Honest fallback: until a session has those values on record, the panel
      does not offer "Re-run tests…" (or offers Resume instead) and says why.
      The WHY line for a failed discovery says the value was ambiguous or
      unset and names the variable to export, passing the discovery stderr
      through rather than rewording it away.
   3. Scope (user ruling): the step carries the selector of the session's last
      recorded ``pipeline test``, so the phone's re-run is the session's own
      scoped tests, never the whole suite.

   Red tests first: a recommend test asserting the ``pipeline test`` step's env
   carries the session's recorded values and its argv the recorded selector;
   a panel test asserting no re-run is offered when none are recorded; a
   wording test for the ambiguous-discovery WHY line. Docs to update with the
   fix: oracle.md (recommend, actions) and oracle-panel.md (Steps, the
   discovery wording).

   **Observation (not in scope):** Web actions and their output are not
   written to the oracle web log; jobs live in memory only, so a failed phone
   action leaves no trace after the oracle restarts.

   **Prior sighting.** :need:`IDEA_023 <IDEA_023>` recorded the same exit 2 at its
   live verification and attributed it to the target project's discovery,
   not the oracle; the oracle's missing per-session context is the half that
   note did not name.

   **Related.** :need:`IDEA_027 <IDEA_027>` — the mobile control panel this action
   lives on. :need:`IDEA_030 <IDEA_030>` — the panel's session flows.
   :need:`BUG_092 <BUG_092>` — sibling panel defect on the same Problems card,
   unrelated mechanism (wording of a count).

   **Repro proof:** ``tests/test_oracle_rerun_session_values.py::test_BUG_093_phone_rerun_reaches_the_sessions_own_destination``,
   committed 163ac50 (2026-10-28) on the session branch, strict xfail. A real
   project in a git repository discovers ``destination_id`` from
   ``INVARIANT_DESTINATION_ID`` and exits 1 when it is unset; its fake suite
   records the destination it was handed and fails. The session's own
   ``pipeline test --selector …``, run with its value exported, reaches the
   suite on that value (asserted, so the scenario is the reported one). The
   real scan, ``assess`` and ``step_for`` then give the Problems card's step, and
   ``Actions.perform`` runs it in-process from an oracle environment without the
   variable: it exits 2 with "discovery for 'destination_id' produced nothing"
   and never reaches the session's tests on its own destination. Red recorded
   through ``invariant pipeline test`` with the marker removed (1 executed,
   1 failure, the assertion on the destination reached). The step's argv is
   also unscoped (``invariant pipeline test``, no selector); that half is not
   asserted by this test.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red test 163ac50 on session 980e0254
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fixed by faee8b5 (session/980e0254): re-run replays the session''s own scoped run and values'
        forced: false

.. bug:: Oracle shows a session as Idle and "your turn" while its background agents or commands are still working
   :id: BUG_094
   :status: fixed
   :severity: medium
   :priority: medium
   :area: oracle/signals, oracle/panel, hosts/claude_code hooks
   :affects: COMP_ORACLE_SIGNALS, COMP_ORACLE_PANEL, COMP_HOSTS_CLAUDE_CODE

   **Fixed 2026-10-28** (commit d3658c3). Background agents and commands now keep a session under Working. Accepted gap, carried by follow-up IDEA_037: a hung background agent is not flagged stuck, and the background-work ledger lapses at 20 minutes.

   **Symptom:** On the mobile panel (reported 2026-10-28) sessions that were
   waiting on their own background subagents were listed under Idle. One card
   read ``your_turn`` ("Claude finished its turn and is waiting for you") though
   it was not the user's turn; another showed "active now" while sitting under
   Idle. In one observed instance a session's background test command (an
   ``xcodebuild`` run) had been going for about 1 h 39 min, probably hung, and
   nothing on the panel flagged it.

   **Expected:** A host whose main turn has ended but which still has
   background work in flight (a background subagent, or a Bash command started
   with ``run_in_background``) reads as working, not idle: no ``your_turn``, grouped
   under Working, with card text such as "Waiting on 1 agent" or "Waiting on 1
   background command". When that background work goes silent past the stuck
   threshold, the card says so (``stuck``), so a hung background command is
   visible.

   **Repro:** In a Claude Code host bound to an invariant session, dispatch a
   subagent in the background (or start a long Bash command with
   ``run_in_background``) and let the main turn end. Open the oracle panel while
   the background work runs: the session sits under Idle and carries
   ``your_turn``. When the background agent completes, its task-notification
   arrives as a user turn and is read as a fresh prompt.

   **Suspected cause:** ``turn_state`` models only the main turn.

   - The installed hooks (``src/invariant/hosts/claude_code.py`` around lines
     55-63) are SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop,
     SessionEnd and Notification; there is no SubagentStart/SubagentStop.
     Attention mapping is in ``claude_code.py`` around lines 1017-1052 and
     ``src/invariant/oracle/attention.py`` around lines 129-155.
   - ``turn_state`` (``src/invariant/oracle/signals.py`` around lines 153-190) uses
     the host's per-process status only when it is one of ``_STATUSES``
     (busy/waiting/idle, line 82, checked at 178). Claude Code reports ``shell``
     when the main turn has ended but a background shell is running, and
     invariant discards it. ``your_turn`` follows at lines 244-245.
   - Panel grouping (``src/invariant/oracle/panel.py`` around lines 101-106,
     579-582, 601-611) puts a host under Working only on ``turn_state`` busy.
   - Background-agent completions arrive as task-notification user turns,
     which fire UserPromptSubmit and are read as new prompts. A subagent's own
     PreToolUse/PostToolUse carry ``agent_id`` but refresh nothing.

   Second harm: the oracle gate cache (``src/invariant/oracle/gates.py`` around
   line 153) skips evaluation only while the host is mid-turn, so it can run
   read-only git rule checks in a worktree that a background reviewer is
   committing or merging in.

   **Affected:** docs/architecture/oracle.md: "``oracle/attention``" and "What
   writes it" (the hook list and attention schema), "``oracle/scan``" (Claude
   Code's per-process statuses, around line 440), "``oracle/signals``" (the
   ``stuck`` and ``your_turn`` rows, around 528/531, and the Mid-turn table around
   536-560), and "``oracle/gates``" (the gate cache, around 776).
   docs/architecture/oracle-panel.md: "Groups and tiles" (around 412-413),
   "States (chips and sentences)" (card wording, around 434-435) and the
   mid-turn row in "As built in increment 6" (around 610).

   **Related:** BUG_095 (a timed-out Stop hook leaves the turn record at
   ``running``; same turn model, opposite failure), BUG_096 (subagent transcripts
   never reach last activity, which the ``stuck`` reuse below depends on),
   BUG_087 (the Mid-turn ruling this extends), BUG_092 and BUG_093 (sibling
   panel defects reported the same day), IDEA_027 (the control panel).

   **Fix sketch / next step (agreed with the user):**

   1. A new ``turn_state`` value ``background``: taken from the host's ``shell``
      status when the host is verified alive, plus a per-host ledger of
      background work: count up on PostToolUse of Agent/Task, or of Bash with
      ``run_in_background``; count down on a task-notification with a terminal
      status (unless it says background work is still running), and on
      SubagentStop where available; clear at SessionEnd; entries expire after
      ``orphan_idle_minutes``.
   2. ``background`` counts as mid-turn: it suppresses ``your_turn``, the gate
      cache skips the host, and the panel groups it under Working with "Waiting
      on N agent(s)" / "Waiting on N background command(s)".
   3. Reuse ``stuck`` when background work goes silent past the threshold.

   Red tests first: a signals test where the host status is ``shell`` (and one
   where the ledger holds an open agent) asserting ``turn_state`` ``background``,
   no ``your_turn``, Working group; a gates test asserting the cache skips such a
   host; a test that a terminal task-notification decrements rather than
   starting a new turn. Docs to update with the fix: the oracle.md and
   oracle-panel.md sections above.

   **Repro proof:** ``tests/test_bug_094_repro.py::test_BUG_094_a_host_waiting_on_its_background_agent_is_working_not_your_turn``,
   committed 7bd124e (2026-10-28) on the session branch, strict xfail. Through
   the real ``invariant hook`` command a bound host gets a prompt, a PostToolUse
   of ``Agent`` with ``run_in_background`` (the agent launched, not finished), the
   main turn's Stop and an ``idle_prompt`` Notification, with no task-notification
   after it; the real ``scan_all``, ``assess`` and ``panel.group_of`` then put the
   session under Idle with ``your_turn`` (``turn_state`` ``idle``) where Working and
   no ``your_turn`` are expected. A control run of the same harness without the
   Stop and the Notification reads Working, so the harness can tell the two
   apart. Asserts the panel group and the signal, not ``_STATUSES`` or a ledger.
   The Claude Code ``shell`` status path is not covered by this test; the
   fix's own red tests should add it.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red test 7bd124e on session 980e0254
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fixed by d3658c3 (session/980e0254): background agents and commands keep a session under Working; follow-up IDEA_037'
        forced: false

.. bug:: Stop hook exceeding its 15 s timeout leaves the oracle turn record stuck at running
   :id: BUG_095
   :status: fixed
   :severity: medium
   :priority: medium
   :area: hosts/claude_code hooks, oracle/attention
   :affects: COMP_HOSTS_CLAUDE_CODE, COMP_ORACLE_ATTENTION

   **Fixed 2026-10-28** (commit bac2215). A Stop now records the turn's end before its closeout runs.

   **Symptom:** In another registered project, invariant's Stop hook
   (``invariant-stop.sh``) took 15.1 s against its 15 s hook timeout and was
   cancelled by the host (the transcript records ``hook_cancelled``). ``turn: ended`` was therefore never written to the attention record, which stayed at
   ``running`` until a later idle Notification happened to correct it. In the
   meantime the oracle read the host as mid-turn. Recurs whenever the Stop path
   is slow. Observed 2026-10-28.

   **Expected:** Every Stop the host delivers records ``turn: ended`` in the
   attention record, however long the rest of the Stop path (the closeout
   checks) takes, and whether or not the hook is cancelled at its timeout.

   **Repro:** Make the Stop path slow in a bound host (a project whose closeout
   checks take longer than 15 s, or a test double that sleeps past the
   timeout), end a turn, and read the attention record: ``turn`` is still
   ``running`` and ``turn_since`` is the prompt's time.

   **Suspected cause:** The Stop hook's timeout is 15 s (wired in
   ``src/invariant/hosts/claude_code.py`` ``_wire_settings``, around line 518), and
   the ``turn-ended`` attention write is not done before the slow work on the
   Stop path, so a cancellation at the timeout loses it.

   **Affected:** docs/architecture/oracle.md: "``oracle/attention``", "What
   writes it" (the ``Stop`` → ``turn-ended`` row) and "The two guarantees"; and the
   ``stuck`` / Mid-turn rules in "``oracle/signals``", which a stale ``running``
   misleads. The hook wiring in ``hosts/claude_code``.

   **Related:** BUG_094 (the oracle's turn model misses background work; this
   is the opposite failure of the same record), BUG_096 (subagent activity
   never reaches last activity), BUG_087 (a stale running turn outranking the
   host's own status, fixed), BUG_092 and BUG_093 (sibling oracle panel
   defects), IDEA_027 (the control panel these states feed).

   **Fix sketch / next step:** Write ``turn-ended`` first and cheaply, before any
   slow work on the Stop path; and/or move the slow closeout work off the
   hook's critical path; and/or raise the Stop hook's timeout. Red test first:
   a Stop whose closeout work outlasts the timeout (or is killed mid-way) still
   leaves ``turn: ended`` in the attention record. Update oracle.md "What writes
   it" / "The two guarantees" with the ordering guarantee.

   **Repro proof:** ``tests/test_bug_095_repro.py::test_BUG_095_a_stop_cancelled_at_its_timeout_still_records_the_turn_ended``,
   committed 6b00f35 (2026-10-28) on the session branch, strict xfail. A
   ``user-prompt-submit`` hook leaves the attention record at ``turn: running``;
   then a ``stop`` hook runs with its closeout step (``evaluate_event``) replaced
   by one that is cancelled at the timeout, modelled as a ``BaseException`` the
   hook command cannot catch, as it cannot catch the host's cancel. The record
   still says ``turn: running`` where ``ended`` is expected. A control with the
   same slow step returning normally records ``ended``, and the inverted
   assertion (``running`` after a cancelled Stop) passes, so the cancellation
   before the ``turn-ended`` write is the one input that flips it. Asserts the
   ``turn`` the record holds after a cancelled Stop, not the order of writes in
   ``cmd_hook`` / ``_stop`` or the 15 s timeout.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red test 6b00f35 on session 980e0254
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fixed by bac2215 (session/980e0254): a Stop records the turn''s end before its closeout runs'
        forced: false

.. bug:: Subagent transcripts are never read, so subagent work doesn't count as session activity
   :id: BUG_096
   :status: fixed
   :severity: low
   :priority: medium
   :area: hosts/claude_code_telemetry, oracle/scan
   :affects: COMP_HOSTS_CLAUDE_CODE_TELEMETRY, COMP_ORACLE_SCAN

   **Fixed 2026-10-28** (commit d100b5d). Subagent transcripts now count as host activity.

   **Symptom:** Work done by a subagent never counts as session activity: a
   host whose main turn is quiet while its subagent works shows a last activity
   as old as the main transcript's last turn, so it can read as silent or
   ``stuck`` while it is busy.

   **Expected:** As docs/architecture/oracle.md documents (around lines
   482-483, "A subagent's (``isSidechain``) entry counts too"), a subagent's
   turns move the host's last activity.

   **Repro:** Dispatch a subagent that works for several minutes while the
   main transcript gets no new turn; read the scan row's ``last_activity``: it
   stays at the main transcript's last turn.

   **Suspected cause:** Claude Code writes subagent transcripts to separate
   ``<host transcript dir>/subagents/agent-*.jsonl`` files, but the reader
   (``src/invariant/hosts/claude_code_telemetry.py`` ``paths()``, around lines
   485-504) only globs ``*.jsonl`` one level deep in each project directory. So
   subagent entries never reach ``last_activity`` (``src/invariant/oracle/scan.py``
   around lines 688-691), contradicting the doc.

   **Affected:** docs/architecture/oracle.md "``oracle/scan``" (the Last activity
   paragraph); ``hosts/claude_code_telemetry``.

   **Related:** BUG_094 (background agents not modelled in ``turn_state``; its
   ``stuck`` reuse needs subagent activity to be seen), BUG_095 (Stop timeout
   leaves the turn record at ``running``), BUG_078 (last activity read from
   mtime, fixed), BUG_082 (subagent doc reads credited to the parent host),
   BUG_092 and BUG_093 (sibling oracle defects), IDEA_027.

   **Fix sketch / next step:** Read ``subagents/agent-*.jsonl`` in the activity
   reader and include their last turns in ``last_activity``; alternatively a
   throttled ``subagent_seen`` stamp written from subagent hook calls (at most
   every 30 s). Red test first with a fixture host directory holding a main
   transcript and a newer subagent transcript, asserting the later turn wins.

   **Repro proof:** ``tests/test_bug_096_repro.py::test_BUG_096_subagent_turn_moves_the_hosts_last_activity``,
   committed fbe1387 (2026-10-28) on the session branch, strict xfail. A fixture
   Claude Code home holds a host's main transcript (last turn two hours ago) and
   ``<host id>/subagents/agent-<id>.jsonl`` (last turn one minute ago); through the
   real ``ClaudeCodeProbe`` and ``scan_project``, the host row's ``last_activity`` is
   the main transcript's turn where the subagent's is expected ("host reads idle
   for 120 min; its subagent's last turn was 1 min ago"). Asserts the row's
   value, not the glob. Note for the fix: ``activity()`` keys by ``path.stem``, and a
   subagent file's stem is ``agent-<id>``, so its turn must be credited to the
   parent host id; ``paths()`` also feeds ``sessions()``/``find()``, which should not
   gain subagent files as hosts by accident.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red test fbe1387 on session 980e0254
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fixed by d100b5d (session/980e0254): subagent transcripts count as host activity'
        forced: false

.. bug:: pipeline test and coverage run only the first declared ecosystem, so a second ecosystem's red test can never be recorded
   :id: BUG_097
   :status: fixed
   :severity: high
   :priority: high
   :area: domains/pipeline, domains/sweep, core/rules, core/manifest
   :batch: unsatisfiable-gates
   :affects: COMP_DOMAINS_PIPELINE, COMP_DOMAINS_SWEEP, COMP_CORE_RULES, COMP_CORE_MANIFEST

   **Symptom:** In a project whose manifest lists two ecosystems (a consuming
   project declares ``ecosystems: [python, node]``, node owning its ``web/**``
   tree), ``invariant pipeline test`` and ``invariant pipeline coverage`` can never
   record a run for the second ecosystem. A red test written under ``web/**`` has
   no route through the engine, so ``test.new_test_fails`` blocks for any session
   whose new test lives in the second ecosystem, and ``--force`` is the only way
   past it.

   A second face, seen by resolving the manifest at 20c3fea: with no explicit
   ``commands``, adapter defaults merge in listed order and later wins
   (docs/architecture/manifest.md, "Resolution order", layers 2-3). For
   ``[python, node]``, ``commands.test`` resolves to ``npm test``, ``commands.test_one``
   to ``npm test -- {selector}`` and ``commands.coverage`` to
   ``npm test -- --coverage``, and the python adapter is the one that reads the
   output. The primary ecosystem's run is wrong too, with nothing printed to say
   so, unless the project spells every command out.

   **Expected:** Each ecosystem a manifest declares can record a test and
   coverage run. The selector (or the touched path) decides which ecosystem a
   scoped run belongs to. That ecosystem's ``test_one`` / ``coverage_one`` runs, and
   that ecosystem's adapter reads the result (``read_run``, ``parse_coverage``,
   ``test_scope_of``). The run path then agrees with classification, which already
   stacks every declared adapter. Unchanged: the ``sweep.no_full_run`` semantics
   and the scheduled full-run floor (ADR-010).

   **Repro:** A manifest with ``project.ecosystems: [python, node]``, a node test
   file under the second ecosystem's tree, ``invariant pipeline test --selector <that test>`` during a red phase. The command substitutes the selector into
   the single ``commands.test_one`` and reads the run with
   ``get_adapter(ecosystems[0])``, so the run is either the wrong runner or read
   by the wrong adapter, and ``test.new_test_fails`` never sees a valid red for
   the node test. Manifest-level: ``Manifest.from_raw({"project": {"ecosystems": ["python", "node"]}}, ...)`` gives ``commands.test == "npm test"`` while every run
   site resolves the python adapter.

   **Suspected cause:** Every run-path site resolves only the primary adapter
   and the single command key:

   - ``src/invariant/domains/pipeline.py`` ``run_tests`` (around line 982) and the
     coverage run (around line 857): ``get_adapter(self.manifest.ecosystems[0])``
     plus ``commands.test_one`` / ``commands.coverage_one``.
   - ``src/invariant/domains/sweep.py:95``: the sweep's adapter is ``ecosystems[0]``.
   - ``src/invariant/core/rules.py:2928`` ``_full_run_in``: judges a full run with
     the primary adapter's ``test_scope_of``.

   Classification, by contrast, stacks: ``core/rules.py`` ``_ecosystem_adapters``
   (around line 4579) returns an adapter for every declared ecosystem, so
   ``is_test_file`` / ``source_files`` know the node files the run path cannot run.
   The manifest has one flat ``commands`` block, so it has no place to put a
   second ecosystem's commands, and the adapter-default merge lets the last
   ecosystem's defaults overwrite the primary's.

   **Affected:** The pipeline domain's test and coverage runs; the sweep domain;
   the full-run detector in the rules engine; the manifest's ``commands`` shape and
   its resolution order (docs/architecture/manifest.md, "Resolution order" and
   "Adapters"; ``ecosystems: [...]  # selects adapters; first is primary``). The
   gate that blocks is ``test.new_test_fails`` (docs/architecture/state_model.md).

   **Fix sketch / next step:** Run ``bug-reproducer`` first. A failing test with a
   two-ecosystem manifest that runs ``pipeline test --selector`` on a
   second-ecosystem path and asserts that the second adapter reads the run and
   its command is the one that runs. A second test asserts that the primary's
   default ``commands.test`` survives a stacked second adapter.

   The fix needs an ADR because it changes the manifest shape. Decisions it
   owes:

   - Where per-ecosystem commands live, for example ``commands.<eco>.test_one``,
     or a per-ecosystem block next to ``project.ecosystems``, and how the flat
     ``commands`` keeps meaning "primary".
   - How a selector or path resolves to an ecosystem. Use the stacked adapters'
     own ``is_test_file`` / path ownership; do not add a new glob table unless
     ownership is ambiguous.
   - What a run whose selectors span ecosystems does. Probably refuse it, naming
     both, consistent with BUG_031's refusal in ``run_tests`` (one run per
     ecosystem would hit the same "test.all_pass reads the LAST run" trap
     BUG_031 rejected).
   - Whether adapter defaults for ``commands`` should merge at all past the
     primary.

   **Not claimed by this fix:** node coverage stays inert until
   :need:`BUG_018 <BUG_018>` (the node adapter has no ``parse_coverage``) lands. The
   routing fix makes the node coverage run reachable; it does not make it
   readable. Adjacent, not a duplicate.

   **Related:** :need:`BUG_031 <BUG_031>` (refusal stance for selectors that cannot
   be honoured in one run), :need:`BUG_018 <BUG_018>`,
   :need:`IDEA_033 <IDEA_033>` (several selectors in one scoped run, swift_xcode
   only). Blocks a consuming project's web privacy rendezvous tests. That
   project's manifest wiring (node ecosystem, web test commands) follows the
   engine fix.

   **Repro proof:** ``tests/test_bug_097_repro.py::test_BUG_097_a_stacked_second_ecosystem_keeps_the_primarys_suite_command``
   and ``::test_BUG_097_a_second_ecosystem_test_is_run_and_read_by_its_own_adapter``,
   committed 26a1e6f (2026-10-28) on the session branch, strict xfail. A
   ``[python, node]`` manifest with no commands resolves ``commands.test`` to
   ``npm test`` where python's ``pytest`` is expected. A scoped ``run_tests`` of a node
   test file under ``web/`` is read by the python adapter where the node adapter is
   expected. Asserts the resolved primary command and which adapter reads the
   run, not the merge loop or ``get_adapter(ecosystems[0])``.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red test 26a1e6f on session 071dc179
        forced: false
      - from: reproduced
        to: fixed
        reason: 'Fixed in dbbaaa9 (ADR-013): each declared ecosystem runs through its own adapter'
        forced: false

.. bug:: Oracle Close leaves the Terminal tab open when Claude's child processes outlive it by a moment
   :id: BUG_098
   :status: fixed
   :severity: medium
   :priority: medium
   :area: oracle/closehost, hosts/claude_code_launch, oracle/webactions
   :batch: terminal-tab-close
   :affects: COMP_ORACLE_CLOSEHOST, COMP_HOSTS_CLAUDE_CODE_LAUNCH, COMP_ORACLE_WEBACTIONS

   **Fixed 2026-10-28** (commit 3ac5c6f).

   **Symptom:** The panel's Close on a leftover Claude window ends Claude but
   often leaves its Terminal tab open, and the user cannot see why: the result
   sheet's line ("left open because something else is running in its tab")
   is held only in the oracle server's memory, and the actions audit log
   records only that the step exited 0.

   **Expected:** As docs/architecture/oracle.md "Close host (IDEA_027
   increment 6)" (around line 1395) and docs/architecture/oracle-panel.md's
   Close row (around line 470) say: "Ends Claude in this window, then closes
   the Terminal tab." A tab Claude's own short-lived children are still
   leaving closes once they have gone; when the tab is left anyway, the reason
   is on record in the audit log's done entry for the action.

   **Repro:** On macOS, in a Terminal.app window, run a Claude host that has
   started child processes on its tty (Claude Code's ``caffeinate``, a language
   server). Make it a closeable orphan and press Close on the panel. Claude
   exits within the 10 s wait; a child outlives it by a moment; the tab stays
   open. Read the actions audit log: the done entry shows exit 0 and no
   outcome line.

   **Suspected cause:** ``close_host`` (``src/invariant/oracle/closehost.py``,
   around lines 190-233) sends one SIGTERM, polls ``kill 0`` up to 10 s, then
   asks Terminal exactly once, through ``osascript`` and ``TERMINAL_TAB_SCRIPT``
   (``src/invariant/hosts/claude_code_launch.py``, around lines 897-934), to
   close the tab on that tty. The script leaves a tab that is ``busy`` or whose
   ``processes`` hold anything but a login shell, and nothing retries. Claude's
   children share its tty, so if one is still exiting when the script runs the
   answer is ``busy <processes>`` and the tab stays. Separately, the outcome
   line is returned to the server as the step's last stdout line but is never
   written into the audit entry.

   **Affected:** docs/architecture/oracle.md "Close host (IDEA_027 increment
   6)" (``closehost``, ``hosts/claude_code_launch`` ``TerminalTabCloser`` /
   ``TERMINAL_TAB_SCRIPT``); the actions audit written by ``webactions``
   (docs/architecture/oracle.md "Actions from the page").

   **Related:** BUG_100 (handoff leaves the old window at a shell prompt;
   should reuse this closer and its polling — same batch), BUG_099 (a window
   with no per-process record cannot be closed at all), IDEA_035 (launched
   windows end with Claude, so no tab closer is needed for them; flags
   polluted hosts), BUG_092, BUG_093, BUG_094, BUG_095, BUG_096 (the current
   oracle batch, to be fixed first), BUG_068 and BUG_069 (launcher
   environment scrub, fixed), IDEA_027 (control panel, increment 6 shipped
   Close), IDEA_030 (start a session; shares the launcher).

   **Batch:** terminal-tab-close — defects where invariant ends a Claude host
   but its Terminal tab or window is not closed; fixed together through one
   tab closer with polling (members: this entry, BUG_100).

   **Fix sketch / next step:** Red test first with fake seams: Claude exits,
   the first tab query answers ``busy caffeinate``, a later one answers only
   the shell; assert the tab closes and the last line says so. Then in
   ``close_host``, after Claude has exited, poll the tab for a few seconds
   until only the shell is left, ignoring processes that were descendants of
   the Claude pid (read before the SIGTERM), and only then report ``busy``.
   Keep the one-SIGTERM, no-SIGKILL rule. Write the outcome line into the
   audit log's done entry.

   **Repro proof:** ``tests/test_bug_098_repro.py::test_BUG_098_close_closes_the_tab_once_claudes_lingering_child_has_gone``,
   committed 3d64830 (2026-10-28) on the session branch, strict xfail. Fake
   seams: Claude exits two polls after its one SIGTERM, and the fake tab
   answers ``busy login,-zsh,caffeinate`` until half a second after Claude has
   gone, then closes (it answers by the clock, not by how often it is asked).
   The real ``close_host`` prints "Claude ended; the window was left open because
   something else is running in its tab (login,-zsh,caffeinate)." where
   "Claude ended, and its Terminal tab closed." is expected. Red recorded
   through ``invariant pipeline test`` with the marker removed (1 executed,
   1 failure, the assertion on the last line). The audit-log face (the outcome
   line never reaches the actions log's done entry) is separable and not
   asserted; note that docs/architecture/state_model.md "The web actions'
   audit log" lists "no ... output" under No secrets, so recording the outcome
   line there is a documented-contract change for the fix's design.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red test 3d64830 on session 675189e4
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fixed by 3ac5c6f (session 675189e4): Close polls a busy Terminal tab and audits its outcome'
        forced: false

.. bug:: A live Claude window with no per-process status record can never be closed from the panel
   :id: BUG_099
   :status: fixed
   :severity: medium
   :priority: medium
   :area: oracle/scan, oracle/recommend, oracle/closehost
   :affects: COMP_ORACLE_SCAN, COMP_ORACLE_RECOMMEND, COMP_ORACLE_CLOSEHOST

   **Fixed 2026-10-28** (commit 5e66a5f).

   **Symptom:** A live Claude window that wrote no Claude Code per-process
   record is never offered Close on the panel. Its leftovers card shows only
   the info line "start or resume a session from that host's own terminal",
   so windows the user wants gone cannot be closed from the phone.

   **Expected:** A live, verified Claude host is closeable from the panel
   whichever source named its pid, with the same pid-reuse safety as today
   (same ``claude``, same start, same tty at the moment Close acts). Where the
   oracle truly cannot establish those facts, the card says plainly why this
   window cannot be closed from the panel, not a generic "start or resume"
   line.

   **Repro:** Start a Claude host whose environment carries an inherited
   ``CLAUDE_CODE_CHILD_SESSION=1`` (for example a Terminal.app itself started
   from a Claude tool shell; see IDEA_035). Claude Code then writes no
   per-process record for it: its sessions directory holds only the ``.key``
   file, no ``<pid>.json``. Leave it as an orphan host in a registered project
   and read the scan: the row has ``pid_source: attention``, ``tty: null``,
   ``process_start: null``, and recommend gives the info step, not
   ``oracle close-host``.

   **Suspected cause:** The close guards (docs/architecture/oracle.md
   ``oracle/recommend``, the paragraph after its table, around line 612) require the pid to come from
   Claude Code's record (``pid_source`` ``record`` or ``both``) with a start token,
   and ``close-host`` itself checks the record. With only the attention
   sidecar's pid, the scan verifies liveness and name (``kill 0`` + name check)
   but never reads the tty or the start time from ``ps``, so no start token
   exists and the row falls through to "orphan host, otherwise".

   **Affected:** docs/architecture/oracle.md ``oracle/scan`` (the host pid
   flowchart, around lines 412-425), ``oracle/recommend`` (the close guards),
   "Close host (IDEA_027 increment 6)" (the record check in ``closehost``).

   **Related:** IDEA_035 (flags the polluted host environment that causes
   the missing record), BUG_096 (activity and transcript tracking degrade on
   the same hosts), BUG_098 (Close leaves the tab open; same Close path),
   BUG_100 (handoff window left at a prompt), BUG_092, BUG_093, BUG_094,
   BUG_095 (current oracle batch), BUG_068 and BUG_069 (launcher environment
   scrub, fixed: windows launched since then are clean), BUG_086 (pid-reuse
   start comparison, fixed), IDEA_027, IDEA_030.

   **Fix sketch / next step:** Red test first with a scan fixture that has an
   attention pid, a live ``claude`` in fake ``ps``, and no per-process record;
   assert the row carries ``tty`` and ``process_start`` and recommend offers
   close-host. Then, for a live attention pid, take tty and start from the
   same ``ps`` read the record path uses (TZ=UTC, BUG_086), and let
   ``close-host`` accept that evidence in place of the record's ``procStart``,
   keeping every other refusal. Failing that, give the orphan row a specific
   reason line on the panel.

   **Repro proof:** ``tests/test_bug_099_repro.py::test_BUG_099_a_live_host_with_no_per_process_record_is_offered_close``,
   committed f36450a (2026-10-28) on the session branch, strict xfail. A
   leftover orphan host in a registered project: its attention record names
   the pid, a stand-in ``ps`` describes that pid as a live ``claude`` with a start
   and a tty, and the synthetic Claude Code sessions directory holds only the
   ``<pid>.<hash>.key`` file. ``oracle list --json`` (``scan_all`` and ``assess``)
   gives the row ``alive: true``, orphan cause ``leftover``, and recommends info
   "start or resume a session from that host's own terminal" with no argv,
   where ``invariant oracle close-host <host> --pid N --start TOKEN --tty ttysNNN`` is expected. Red recorded through ``invariant pipeline test`` with
   the marker removed (1 executed, 1 failure). The test asserts the step
   offered, not which ``pid_source`` the close guards accept. Note for the fix:
   without a record the row also has no ``cwd``, so ``rooted_here`` stays unset
   and is a third guard the fix must satisfy from some other evidence.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red test f36450a on session 675189e4
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fixed by 5e66a5f (session 675189e4): a verified live window with no process record can be closed'
        forced: false

.. bug:: Handoff ends the old Claude but leaves its Terminal window at a shell prompt
   :id: BUG_100
   :status: fixed
   :severity: low
   :priority: medium
   :area: hosts/claude_code_launch, cli/main
   :batch: terminal-tab-close
   :affects: COMP_HOSTS_CLAUDE_CODE_LAUNCH, COMP_CLI_MAIN

   **Fixed 2026-10-28** (commit 25c08fe).

   **Symptom:** After a successful ``session handoff``, the old Claude ends 20 s
   later but its Terminal window stays open at a shell prompt. Every handoff
   leaves one more idle window behind for the user to close by hand.

   **Expected:** The old session "ends itself" (IDEA_009) including its
   window: once Claude has exited, its Terminal tab closes under the same
   rules as the panel's Close (only a Terminal.app tab, only when nothing but
   its shell is left, never starting Terminal). Note that
   docs/architecture/cli.md (``session handoff``, around line 591)
   currently documents "its terminal window stays at a shell prompt", so the
   fix changes that documented line too.

   **Repro:** On macOS, run ``invariant session handoff --note "<line>"`` from a
   Claude host in a Terminal.app window. The successor window opens; about
   20 s later the old Claude exits and the old window remains at a zsh
   prompt.

   **Suspected cause:** ``close_script`` (``src/invariant/hosts/claude_code_launch.py``,
   around lines 841-853) is a detached ``sleep 20; <check>; kill -TERM <pid>``
   that sends SIGTERM only. It never asks Terminal to close the tab, though
   ``TerminalTabCloser`` / ``TERMINAL_TAB_SCRIPT`` in the same module already do
   that for the panel's Close.

   **Affected:** docs/architecture/cli.md ``session handoff`` (the
   old-session-ends paragraph); ``hosts/claude_code_launch`` (``close_script``,
   ``TerminalTabCloser``), see docs/architecture/oracle.md "Close host (IDEA_027
   increment 6)".

   **Related:** BUG_098 (the tab closer gives up on a tab whose Claude
   children are still exiting; its polling is needed here too — same batch),
   IDEA_035 (an ``exec``-ed launch line would end windows that invariant itself
   launched when Claude exits, which covers a handed-off successor's own
   later handoff), BUG_099 (windows that cannot be closed from the panel),
   BUG_092, BUG_093, BUG_094, BUG_095, BUG_096 (current oracle batch),
   BUG_068 and BUG_069 (handoff launch environment, fixed), BUG_075 (long
   note launch, fixed), IDEA_027, IDEA_030.

   **Batch:** terminal-tab-close (with BUG_098) — one tab closer with
   polling serves both.

   **Fix sketch / next step:** Fix BUG_098 first, then reuse its tab closer:
   read the tty and whether Terminal.app is in the parent chain before the
   delay, and after the SIGTERM wait for the pid to exit and run the polling
   tab close. Red test with fake seams asserting the handoff's close step
   asks for the tab on the old host's tty only after Claude has exited, and
   spares a reused pid as today. Update cli.md's handoff paragraph.

   **Repro proof:** ``tests/test_handoff.py::test_BUG_100_handoff_close_closes_the_old_terminal_tab_after_claude_exits``,
   committed 691f980 (2026-10-28) on the session branch, strict xfail. It
   captures what ``DetachedCloser().close(pid, 0)`` starts detached and runs it
   with the OS faked at its edges: a stand-in ``ps`` first on PATH describes the
   pid as ``claude`` on a Terminal tab's tty (under ``-zsh``, ``login`` and
   Terminal.app), a stand-in ``osascript`` only records, the shell's ``kill`` only
   records and marks the pid gone, and every pid is above macOS's largest.
   Asserts the symptom: once Claude has been ended, Terminal is asked to close
   the tab on the host's tty with the panel Close's ``TERMINAL_TAB_SCRIPT``.
   Today the ending path does only ``kill -TERM <pid>`` and asks for no tab:
   "the handoff ended the old Claude (pid 7340033) but never asked Terminal to
   close its tab on ttys042, so the window stays at a shell prompt; the ending
   path did only: ['kill -TERM 7340033']". Harness note for the fix: a literal
   ``osascript`` inside a ``sh -c`` string is refused by the suite's osascript
   backstop, so the tab close should go through a seam or a process that finds
   ``osascript`` on PATH; if the fix moves the tty read to its own seam, adapt
   the harness, never the assertion.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red test 691f980 on session 675189e4
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fixed by 25c08fe (session 675189e4): handoff closes the old Terminal tab once Claude has exited'
        forced: false

.. bug:: A Stop closeout cancelled at the hook timeout loses its verdict, so a stop that should be refused goes through
   :id: BUG_101
   :status: filed
   :severity: medium
   :priority: medium
   :area: hosts/claude_code hooks, cli host bridge
   :batch: inert-instruments
   :affects: COMP_HOSTS_CLAUDE_CODE, COMP_CLI_MAIN

   **Symptom:** The Stop hook's closeout (``ClaudeCodeHost._stop``: ``evaluate_event("stop")`` plus ``_spend_overrides``, in ``src/invariant/hosts/claude_code.py``, applied by ``cmd_hook`` in ``src/invariant/cli/main.py``) can run longer than the host's 15 s hook timeout (wired in ``_wire_settings``). The host then cancels the hook. No blocking response is printed, so the host lets the stop through, and no verdict is recorded anywhere. Observed 2026-10-28 in another registered project: the Stop hook took 15.1 s against 15 s and was cancelled. Whether a stop that should be refused is refused therefore depends on timing, not on the evidence.

   **Expected:** A Stop whose closeout gates would refuse is refused, however long the closeout takes. If the closeout cannot finish inside the host's timeout, the outcome fails closed: either a refusal is returned before the deadline, or a "closeout incomplete" gap is recorded that the next transition or landing refuses on until the stop gates rerun. A cancelled closeout is never a silent pass.

   **Repro:** In a bound host, make the Stop closeout slower than 15 s (a project whose stop gates take that long, or a test double whose ``evaluate_event`` outlasts a budget or is cancelled mid-way like the BUG_095 repro) with a state in which a stop gate would block. End the turn: the stop goes through, and neither a verdict nor a gap is on record.

   **Suspected cause:** The closeout runs entirely on the hook's critical path with no budget of its own, and the host's cancellation at its timeout is indistinguishable, to the host, from a hook that printed no blocking response. Nothing records that the closeout started but did not finish.

   **Affected:** docs/architecture/cli.md "Host bridge" (the ``stop`` hook and how ``cmd_hook`` applies its response); ``hosts/claude_code`` (``_stop``, ``_wire_settings``); docs/architecture/oracle.md "``oracle/attention``" (the "A delivered Stop ends the turn" rule once BUG_095 lands). ADR-007 D10 (a sidecar write never changes a verdict), which BUG_095 amends: that guarantee assumes a verdict is computed at all.

   **Related:** BUG_095 (same cancellation; its fix makes the attention record say the turn ended and keeps ``blocked`` at its last value, by user ruling, but does not recover the lost verdict; session 980e0254, not yet merged). BUG_001 (the pre-tool-use hook fails open on an internal error; the same fail-open outcome by another route). BUG_094 (the oracle's turn model). The project's model-agnostic aim: determinism comes from the engine, and a timing-dependent verdict breaks it.

   **Fix sketch / next step:** Run ``bug-reproducer`` first. Red test idea: a Stop closeout that exceeds a budget must produce a refusal or a recorded gap, never a silent pass. Directions for the architect: make the closeout fit the timeout (cheaper evaluation, a cache, or an internal budget that returns a refusal before the deadline when unfinished, i.e. fail closed); and/or record "closeout incomplete" so the next transition or landing refuses until the stop gates rerun; and/or measure closeout duration and have ``doctor`` warn when it approaches the timeout. Weigh fail-closed against BUG_001's rejected self-locking failure: a refusal must still leave a path to rerun the gates.

.. bug:: A test-only bugfix change owes docs.updated, and its only route past it is --force
   :id: BUG_102
   :status: fixed
   :severity: medium
   :priority: high
   :area: core/rules
   :batch: unsatisfiable-gates
   :slug: test-only-change-owes-docs-updated
   :affects: COMP_CORE_RULES

   **Symptom.** A ``bugfix`` pipeline whose fix touches only test code (plus the
   backlog record) cannot leave ``verifying`` for ``committed`` without ``--force``.
   ``docs.updated`` reads the test files as "source changed" and demands an
   architecture-doc edit that nobody could honestly write, since no component,
   seam or dependency changed. Observed 2026-10-28 in a Heap ``bugfix`` session,
   state ``verifying``, commit ffc5a2d, fixing BUG_UPLOAD_RETRY_REPRO_BLIND. The
   fix's changed set:

   ::

      HeapTests/Helpers/ReachabilityMirrorRestore.swift   (new, 41 lines)
      HeapTests/NetworkReachabilityEnvironmentTests.swift
      HeapTests/NetworkReachabilityExpensiveTests.swift
      HeapTests/UploadRetryRemoteActiveLibraryReproTests.swift
      docs/backlog.rst

   No app source was touched. ``invariant pipeline advance committed`` refuses:

   ::

      [BLOCK] docs.updated: source changed but no architecture doc did
              try: update the relevant doc under docs/architecture
              try: invariant pipeline advance <state> --force --reason 'no structural change'
      [skip:BLOCK] docs.diagrams_current: no doc changed
      invariant: 1 gate(s) block 'verifying' -> 'committed'

   The only offered route is ``--force``. The permission classifier denies it, and
   the user's standing rule is never to force. So the session has no lawful
   landing at all.

   **Expected.** A change confined to test code is not a structural change to
   the documented architecture. It reaches ``committed`` through a declared or
   recorded route that is not ``--force``. Same principle as BUG_002 and BUG_064:
   a blocking gate with no question to ask of this change should not be
   satisfiable only by bypass. Bugs whose defect lives in the test harness (a
   blind repro, a leaky test fixture) are exactly this case, and the ``bugfix``
   lane is where they land.

   **Repro.** In a project whose adapter classifies test files (any shipped
   ecosystem): ``invariant session start``, ``invariant pipeline start --methodology bugfix``, isolate, and walk to ``verifying`` with a committed change that touches
   only files the adapter's ``is_test_file`` claims (optionally plus a backlog
   file). ``invariant pipeline advance committed`` blocks on ``docs.updated`` as
   above. A minimal engine-level red: evaluate ``docs.updated`` over a changed set
   of only test files and expect a non-blocking verdict, with a control that one
   non-test source file and no doc change still FAILS.

   **Suspected cause.** ``docs.updated``'s source/non-source split (BUG_064 half
   (b)) holds out prose suffixes, extensionless repository metadata and
   ``methodology.gates.non_source`` globs, and nothing else. Test files are none
   of those, so they count as source. The adapters already expose
   ``is_test_file``, which coverage classification and selector routing use, but
   ``docs.updated`` does not consult it. Not yet pinned by a failing test.

   **Affected.** ``core/rules.py`` (``docs.updated``, ``_changed_set``, the non-source
   classification it shares in part with ``coverage.meets_floor``); the adapters'
   ``is_test_file``. Documented in ``docs/architecture/state_model.md`` (the
   "is it structural" bullet under the docs-first gates, BUG_064),
   ``docs/architecture/overview.md`` (non-source classification) and
   ``docs/architecture/manifest.md`` (``methodology.gates.non_source``).

   **Candidate directions (none settled).**

   - Hold test paths out of "source changed" in ``docs.updated``, by the
     adapter's ``is_test_file``, the way prose, metadata and ``non_source`` are.
     Open question: test helpers and harness seams can be documented
     architecture in some projects.
   - An acknowledgement / override route short of ``--force`` for this gate
     (BUG_049 added one for blocking hook verdicts; pipeline ack refuses block-level gates by design).
   - Applicability declared rather than inferred, per BUG_002's principle: e.g.
     the pipeline or session declares "test-only change" and the gate abstains,
     as ``docs.diagrams_current`` does for the ``docs`` methodology.
   - Interim workaround to evaluate, not to adopt as the fix: declaring the test
     tree in ``methodology.gates.non_source``. It is semantically wrong for that
     key ("no run could produce a row for it"), and it would also change what
     ``coverage.meets_floor`` holds out.

   **Impact.** Blocks the Heap session's merge and teardown, and
   BUG_TAG_AUDIT_SYNC_DRAINER queued after it in that project.

   **Related.** BUG_002 (docs-only change blocked on inapplicable gates; the
   declared-not-inferred principle), BUG_064 (half (b) taught ``docs.updated`` the
   prose / metadata / ``non_source`` hold-outs; this is the test-code case it did
   not cover), BUG_066 (repro-only session could not land its proof), BUG_026
   (the coverage floor's analogue for a test target).

   **Fix sketch / next step.** ``bug-reproducer`` first: a strict-xfail red over
   ``docs.updated`` with a test-files-only changed set, controlled by a
   source-file case that must still FAIL. Then ``feature-architect`` picks among
   the directions above; whichever lands should be stated beside the BUG_064
   bullet in ``state_model.md``.

   **Repro proof:** ``tests/test_bug_102_repro.py::test_BUG_102_a_test_only_change_lands_without_an_architecture_doc``,
   committed 16b2be1 on session bc406920 (2026-10-28), strict xfail, parametrized
   over ``python`` and ``swift_xcode`` — asserts that ``docs.updated``, evaluated on the
   session branch's committed change of only adapter-claimed test files
   (``tests/**`` / ``HeapTests/**``) plus ``docs/backlog.rst``, does not block. Today it
   returns the block-level fail "source changed but no architecture doc did",
   with ``--force`` as the only non-doc route. The suspected cause (``is_test_file``
   not consulted) is deliberately not asserted. Control
   ``test_BUG_102_control_source_beside_tests_still_needs_a_doc`` (not wrapped,
   green today): one non-test source file beside the same tests must still
   block. Red recorded through ``invariant pipeline test --phase reproducing``.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: null
        forced: false
      - from: reproduced
        to: fixed
        reason: fixed in 1e30b5f
        forced: false

.. bug:: In a stacked-ecosystem project the coverage floor reads a real code change in a non-primary file as comment-only
   :id: BUG_103
   :status: fixed
   :severity: medium
   :priority: high
   :area: core/rules, adapters/base, adapters/swift_xcode
   :batch: inert-instruments
   :affects: COMP_CORE_RULES, COMP_ADAPTERS_BASE, COMP_ADAPTERS_SWIFT_XCODE

   **Symptom:** In a project that declares stacked ecosystems with ``swift_xcode`` first (``[swift_xcode, node]``, Heap's shape per ADR-013, or ``[swift_xcode, python]``), ``coverage.meets_floor`` excuses a real code change in a ``.ts`` or ``.py`` file as a comment-only edit, and its verdict says nothing about it. Example: changing the line ``x = 'http://a' + b`` to ``x = 'http://zzz' + c`` reduces, on both revisions, to ``x = 'http:``, so the comment-only exclusion (BUG_026) holds the file out of the floor.

   **Expected:** A change to executable code in a file of any declared ecosystem is held to the coverage floor. A file is reduced only by the adapter of the ecosystem it belongs to; when no declared adapter can reduce it, the reduction is ``None`` and nothing is relaxed. A comment-only exclusion applies only where the file's own language rules say the changed text was a comment.

   **Repro:** Declare ``[swift_xcode, node]`` (or ``[swift_xcode, python]``). Change ``x = 'http://a' + b`` to ``x = 'http://zzz' + c`` in a ``.ts`` (or ``.py``) file and evaluate ``coverage.meets_floor`` against a coverage row below the floor for that file: the file is excused as comment-only instead of blocking. Confirmed so far only by an in-process snippet the feature-architect ran while designing IDEA_031 A3; no committed test exists yet.

   **Suspected cause:** ``Adapter.executable_text(source)`` takes no path, so it cannot tell which ecosystem a file belongs to. ``core/rules._executable_text`` asks the declared adapters in order and takes the first non-``None`` answer, and the ``swift_xcode`` reducer answers for any text. Swift's lexer does not treat single quotes as a string delimiter and treats ``//`` as the start of a comment, so everything after the ``//`` in a single-quoted JS/TS or Python string is discarded on both revisions and the two compare equal. This is the "reducing too much calls a real code change cosmetic" direction that the overview's adapters section warns about.

   **Affected:** docs/architecture/overview.md "``adapters/``" (stacking: "Classification asks every declared adapter, and the first with an opinion answers"; the ``executable_text`` paragraph); ``core/rules`` (``_executable_text``, ``coverage.meets_floor``'s comment-only exclusion); ``adapters/base`` and ``adapters/swift_xcode`` (``executable_text``). ADR-013 (each declared ecosystem runs through its own adapter: the run half routes by ecosystem, but this classification half still takes the first answer). ADR-012 (the model-agnostic contract: gates must be deterministic, and a gate that silently passes on a lexer mismatch is not).

   **Related:** IDEA_031 (A3, mechanical checkers, rewrites this seam). BUG_026 (introduced the comment-only exclusion and the Swift reducer). BUG_097 (same stacked-ecosystem shape on the run half, fixed by ADR-013). Batched with ``inert-instruments``: a rule whose input is silently wrong, returning a non-verdict that reads as a pass.

   **Fix sketch / next step:** By user ruling (IDEA_031 A3, DN-11): run ``bug-reproducer`` now. Red test: in a ``[swift_xcode, node]`` and a ``[swift_xcode, python]`` manifest, the single-quoted ``//`` change above is still held to the floor. The fix then lands inside IDEA_031 A3 increment 1 (session 864db3ee), which rewrites the seam: ``executable_text`` gains the file's path and answers ``None`` outside its own ecosystem, so ``_executable_text`` reaches the owning adapter or relaxes nothing.

   **Repro proof:** ``tests/test_rules.py::TestBug103AStackedProjectHoldsANonPrimaryCodeChangeToTheFloor::test_BUG_103_a_real_change_in_a_non_primary_file_is_held_to_the_floor``, parametrized over ``[swift_xcode, python]`` with ``app/mod.py`` and ``[swift_xcode, node]`` with ``app/mod.ts``. Committed 708f77b (2026-10-28) on ``session/864db3ee``, not yet on main; it lands with the A3 increment 1 merge. It asserts the symptom: changing ``x = 'http://a' + b`` to ``x = 'http://zzz' + c`` with the file at 30% against a 90% floor must block, and must not be excused as "altered no code". Today both cases pass the gate, with the detail "app/mod.ts 30% (this change altered no code in it)". The control: with ``swift_xcode`` undeclared, the same assertions pass, so stacking is the only variable. It is wrapped in a strict xfail; the fix deletes that marker and keeps the assertions. Red was recorded against session 864db3ee's tdd pipeline at ``designing``, via ``pipeline test --phase red``; the pipeline was not advanced.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'strict-xfail repro in tests/test_rules.py on session/864db3ee (708f77b): a real change in a non-primary .py/.ts file is excused as comment-only'
        forced: false
      - from: reproduced
        to: fixed
        reason: Fixed by A3.1 increment commit 6478fe3 on session/864db3ee (path-aware executable_text)
        forced: false

.. bug:: A file whose name has leading or trailing whitespace is invisible to the guidelines gates, which read it as clean
   :id: BUG_104
   :status: filed
   :severity: low
   :priority: medium
   :area: core/manifest, core/rules, guidelines domain
   :batch: inert-instruments
   :affects: COMP_CORE_MANIFEST, COMP_CORE_RULES, COMP_DOMAINS_GUIDELINES

   **Symptom:** A file whose name has leading or trailing whitespace (for example ``mod.py`` at the project root, or ``mod.py`` with a trailing space) is invisible to the whole guidelines family of gates. A guideline violation in such a file is never reported, and the gates read the empty result as clean. ``invariant guidelines baseline --record`` likewise leaves the file out of the baseline.

   **Expected:** Every file the session changes is in the subject of every gate that reads the changed set, whatever its name. A name the normaliser cannot carry faithfully is either kept with its exact spelling or makes the changed set unestablished, so the gate fails rather than passing; it is never silently dropped.

   **Repro:** Not reproduced yet. Sketch: in a project with a guidelines ruleset, commit a change to a file named with a leading or trailing space that violates a mechanical guideline, then evaluate the guidelines gate (and run ``guidelines baseline --record``). Expected red: the violation is reported. Suspected today: the gate passes and the baseline omits the file. A control with the same content under a plain name should report it.

   **Suspected cause:** ``core/manifest.project_relative`` strips leading and trailing whitespace from the path it normalises. That normaliser is right for a declared path or glob in the manifest, but it is also applied to target paths, so a real file name with edge whitespace is respelled to a name that does not exist on disk or in git's output. ``core/rules._changed_set`` and the guidelines baseline record lose the file on that respelling. This predates IDEA_031 A3: ``project_relative`` has always stripped.

   **Possible reach (suspicion, not verified):** other rules that compare targets through ``project_relative`` or read ``_changed_set`` may share the drop: ``docs.updated``, ``docs.diagrams_current``, ``coverage.meets_floor``, and ``session.own_file_set_only``. Each should be checked by the reproducer before it is claimed.

   **Affected:** docs/architecture/manifest.md (the ``project_relative`` normaliser section, and the ``_changed_set`` paragraph that names the rules reading it); docs/architecture/overview.md (``manifest`` row: "the one canonical spelling of a declared path (``project_relative``)"; ``CGUIDE -- project_relative --> MANIFEST``). ``core/manifest`` (``project_relative``), ``core/rules`` (``_changed_set``), the guidelines domain (``guidelines baseline --record``). ADR-012 (the model-agnostic contract: gates must be deterministic, and a gate that silently passes on a dropped input is not).

   **Related:** IDEA_031 (A3, mechanical checkers; found by the feature-architect while building A3 increment 5 in session 864db3ee; does not block A3). BUG_103 (also a silent pass on the change-reading seam). BUG_071 (introduced the single normaliser for declared paths). Batched with ``inert-instruments``: a rule whose input is silently wrong, returning a non-verdict that reads as a pass.

   **Fix sketch / next step:** Run ``bug-reproducer`` first: a strict-xfail test in tests/test_rules.py (and one for the baseline record) with a committed file named with a trailing space carrying a guideline violation, plus a plain-name control. The fix likely separates the two uses of ``project_relative``: whitespace stripping belongs to parsing a declaration, not to spelling a target that git or the filesystem already named. Then check each rule in the reach list above against the same fixture.

.. bug:: An untracked new file is invisible to the guidelines gates and to verdict submission, which read it as clean
   :id: BUG_105
   :status: filed
   :severity: low
   :priority: medium
   :area: core/rules, core/git, guidelines domain
   :batch: inert-instruments
   :affects: COMP_CORE_RULES, COMP_CORE_GIT, COMP_DOMAINS_GUIDELINES

   **Symptom:** A brand-new file that was never staged, in a tree with uncommitted changes, is invisible to every gate that reads ``core/rules._changed_set``: ``guidelines.mechanical_clean``, ``guidelines.verdicts_complete``, the cochange and baseline checks, and the verdict submission's P1, which refuses a citation into such a file as "not touched". A guideline violation in that file is not reported, and the gates read the empty result as clean. It surfaced in the valid-ruleset conformance scenario: its new ``app.py`` is untracked until the scenario's commit step, so the verdict-submission step had to be placed after the commit.

   **Expected:** A new file in the working tree counts as changed, subject to ``.gitignore``, for every guidelines gate and for verdict submission. An untracked, non-ignored file the session created is in the subject the same way a modified tracked file is.

   **Repro:** Not reproduced yet. Sketch: in a project with a guidelines ruleset, make a tracked change (so the tree is dirty), then create a new file with a shell command (not a declared edit) that violates a mechanical guideline, and leave it unstaged. Evaluate ``guidelines.mechanical_clean`` and submit a verdict citing the new file. Expected red: the violation is reported and the citation is accepted. Suspected today: the gate passes and the citation is refused as "not touched". Controls: the same file after ``git add`` is seen; a file matched by ``.gitignore`` stays out.

   **Suspected cause:** ``_changed_set`` is the union of ``git diff``, ``git diff --cached``, the branch's commits against its base, and the session's declared files. None of these lists an untracked file. A real host usually declares the files it edits through hooks, so the gap shows mainly when a file is created outside a declared edit: by a shell command, a script, or a scenario's write step. Note the inconsistency inside the same family: ``git.changed_lines``, which the cochange kind reads, already counts untracked files whole.

   **Design tension the fix must resolve:** docs/architecture/manifest.md states the untracked exclusion as one of three deliberate limits of ``_changed_set`` for ``privacy.clean`` at the review boundary ("nothing untracked goes into the base branch, and the moment it is added the index carries it here"), and ``test_an_untracked_file_is_not_in_the_subject`` pins it. Widening ``_changed_set`` for everyone changes that documented limit for ``privacy.clean``, ``docs.updated``, ``docs.diagrams_current`` and ``coverage.meets_floor`` too. The fix must either widen it deliberately and update that paragraph and test, or give the guidelines family (and verdict submission) its own untracked-inclusive view; that is a design call for ``feature-architect``, not settled here.

   **Affected:** docs/architecture/manifest.md (the ``_changed_set`` paragraph and its "three limits are deliberate" list); docs/architecture/overview.md (``RULES --> CFIND``, ``guidelines.mechanical_clean`` reading the changed files through ``_changed_set``; the ``git`` row's ``changed_lines``, "untracked files counted whole"). ``core/rules`` (``_changed_set``), ``core/git`` (the queries it unions), the guidelines domain (gates, baseline, verdict submission P1).

   **Related:** IDEA_031 (umbrella; found by the compliance-reviewer while reviewing A4 increment 3 in session c181f22d). BUG_104 (whitespace filenames invisible to the guidelines gates), the same family of changed-set blind spot. BUG_047 and BUG_057 (where the untracked limit was decided for ``privacy.clean``). Batched with ``inert-instruments``: a rule whose input is silently incomplete, returning an empty result that reads as a pass.

   **Fix sketch / next step:** Run ``bug-reproducer`` first: a strict-xfail test in tests/test_rules.py with a dirty tree plus an untracked new file carrying a guideline violation, asserting ``guidelines.mechanical_clean`` reports it, plus the ``git add`` and ``.gitignore`` controls; and one for verdict submission P1 accepting a citation into the untracked file. The fix likely adds ``git ls-files --others --exclude-standard`` to the source union (or to a guidelines-specific view), with the scoping decision above recorded in manifest.md.

.. bug:: An operator's edit to an agent copy installed by an earlier release is silently overwritten on upgrade
   :id: BUG_106
   :status: fixed
   :severity: medium
   :priority: medium
   :area: hosts/claude_code
   :affects: COMP_HOSTS_CLAUDE_CODE

   **Symptom:** An agent or other managed asset that was installed by an earlier invariant release and then hand-edited by the operator is silently overwritten on upgrade. ``classify_installed`` reads any installed copy whose managed marker differs from the digest this release ships as "installed from an older version", and both install paths (``invariant agents install`` and ``invariant assets install``) rewrite every file in that class. The operator's edit is lost without being named. This is the harm BUG_051 fixed for a copy edited under the current release, still present for a copy edited under an earlier one.

   **Expected:** A copy whose body no longer matches its own marker is classified as edited locally, then skipped and named, whatever release installed it. Only an unedited copy from an older release is rewritten. ``--overwrite-local`` remains the only way to replace an edited copy, and ``--dry-run`` names it as skipped.

   **Repro:** Not reproduced yet; found by reading the code. Sketch: install an asset rendered with a digest that differs from the current shipped digest (simulating an earlier release), then edit its body while leaving the marker in place. Run ``invariant agents install`` (and, separately, ``invariant assets install``). Expected red: the edit survives and the output names the file as skipped (edited locally). Suspected today: the file is rewritten and counted as installed. Control: the same older-release copy with no edit is rewritten, as designed.

   **Repro proof:** ``tests/test_authority.py::TestAnEditSurvivesWhicheverReleaseInstalledTheCopy::test_BUG_106_an_edited_older_release_copy_survives_and_is_named`` (parametrized over ``assets install`` and ``agents install``), committed cb035a7 (2026-11-01) — asserts the symptom: the operator's appended line survives the install and the output names the copy as skipped (edited locally). The copy is an older body rendered with that body's own digest, then edited with the marker left in place. Today both commands rewrite the file to the shipped version (the edit is gone; the first assertion fails). Wrapped in strict xfail: delete the marker with the fix and the assertion must pass unchanged. Control ``test_BUG_106_control_an_unedited_older_release_copy_is_rewritten`` (unwrapped, green) holds the designed rewrite of an unedited older-release copy.

   **Suspected cause:** The marker already verifies itself: it equals the digest of the marker-stripped text exactly when the file is unedited. ``classify_installed`` asks two questions in the wrong order for this case. It first asks whether the body reconstructs to what this release ships, then whether the marker holds this release's digest. A copy that fails the first check and carries another release's digest goes to "older version" without anyone checking whether its body still matches its own marker. The missing question is: does the body still hash to the digest its own marker records? If not, the copy is edited, whichever release wrote the marker.

   **Affected:** docs/architecture/cli.md, the section "A managed file the operator has edited" and its classifier diagram. That diagram currently documents the faulty edge: a marker naming "some other release" leads straight to "rewritten". ``hosts/claude_code.py`` (``classify_installed`` and the install paths that act on it). The ``assets install`` and ``agents install`` commands in the same doc.

   **Fix sketch / next step:** Run ``bug-reproducer`` first: a strict-xfail test next to the BUG_051 tests, with an older-release marker on an edited body, asserting the edit survives both install commands and is named as skipped, plus the unedited older-release control. The user's ruling: the fix is COMBINED into IDEA_031 A6 increment 1 (one asset classifier and install plan), because ``classify_installed`` is being rewritten there. The fix should add the self-consistency check (does the stripped body hash to its own marker's digest) before the "older version" branch, and update the cli.md diagram to match.

   **Related:** BUG_051 (fixed): the same-release case, whose fix introduced the three-way rule this entry corrects. IDEA_031 (umbrella; A6 "host-neutral agent definitions", whose increment 1 carries the fix). Found by source reading during the IDEA_031 A6 design pass in session 58e4b6c1, 2026-11-01. Unbatched: no open batch shares this mechanism (an asset classifier that cannot tell an operator's edit from an older release).

   **Fixed in 3b52b34:** one asset classifier asks whether the body still hashes to its own marker, so an edit to an older-release copy is kept and named.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'strict-xfail repro committed cb035a7: edited older-release copy is rewritten by assets install and agents install'
        forced: false
      - from: reproduced
        to: fixed
        reason: null
        forced: false

.. bug:: Concurrent evidence writers in one session lose each other's writes, because the pipeline record has no lock
   :id: BUG_107
   :status: fixed
   :severity: medium
   :priority: high
   :area: domains/pipeline, core/store
   :affects: COMP_DOMAINS_PIPELINE, COMP_CORE_STORE

   **Fixed 2026-11-03** (commit cfd0891). Landed on main as IDEA_031 A7.3b, with proof commit 3dcf848. The two proof tests in ``tests/test_bug_107_repro.py`` pass with their strict-xfail markers removed. The session record's sibling race is BUG_108, which this fix does not close.

   **Symptom:** Two evidence writers running at once in one session can lose each other's writes. An ``agents_run``, ``docs_read``, test run or verdict that was recorded disappears from the pipeline record, and a stale save can carry the pipeline state backwards, undoing an advance. A stale save can also drop a landing record, an override or an acknowledgement. The effect mostly fails closed and loud: a lost ``agents_run`` re-records on that agent's next call, and a lost ``docs_read`` or test run blocks at the gate and is cured by repeating it. Rarely it fails open: a lost red ``test_runs`` entry leaves an older green run as the latest, and a lost fail verdict leaves an older pass.

   **Expected:** Concurrent evidence writers in one session never lose each other's writes, and no save carries the pipeline state backwards.

   **Repro:** Not yet recorded as a committed test. The compliance-reviewer's reproduction, on a temporary state root:

   - With a forced interleave, ``record_agent_run`` and ``record_docs_read`` landed between another writer's load and save, and both were lost. A stale save also undid an advance.
   - With forked processes released on a barrier (a worst case, not a live rate), ``docs_read`` was lost 62 of 150 times against another ``docs_read``, and 96 of 150 against an audit write.
   - The window is about 2.4 ms median on an 18 KB record.

   **Repro proof:** ``tests/test_bug_107_repro.py::test_BUG_107_a_second_writers_evidence_survives_the_first_writers_save`` and ``tests/test_bug_107_repro.py::test_BUG_107_a_stale_save_does_not_undo_an_advance``, strict xfail, committed 3dcf848 on session 5cb38a2f (2026-11-02). One writer is parked between its load and its save while a real second process writes over the same temporary state root. They assert that the second writer's ``docs_read`` and ``agents_run`` survive the first writer's save, and that an advance to ``red`` still reads ``red``. Today the record ends with both lost and the state back at ``designing``. Each fix-path run waits a bounded 3 seconds for the blocked writer, then releases the first; a hang or crash fails outside the marker.

   **Suspected cause:** The pipeline record has no lock. ``PipelineDomain``'s ``current()`` loads the whole record and ``_save`` writes the whole record. The store module's docstring says per-session files need no locking because only the owning session writes them. Parallel hooks in one session break that assumption: the owner is one session, but not one writer.

   **Affected:** ``domains/pipeline`` and ``core/store``. See ``docs/architecture/state_model.md``, "Domain: pipeline", and "Storage layout", whose "Concurrency" paragraph documents the assumption this defect disproves; and the ``store`` row in ``docs/architecture/overview.md``, "Component responsibilities". Not checked here: ``docs/architecture/state_model.md``, "The one edge a hook walks: the first edit", relies on the same owner-only argument for the session record, with hooks racing on a first edit.

   **Fix sketch / next step:** Run ``bug-reproducer`` first: a two-writer test on a temporary state root, using the forced interleave so it does not depend on timing. Planned fix: hold the store's existing lock across load and save for the pipeline record. The user ruled to fix this before A7 merges, as an added increment (A7.3b) in session 5cb38a2f. The fix also has to correct the store docstring and the "Concurrency" paragraph.

   **Why now:** The race predates A7, but A7.3's ``evidence.audit`` adds saves at the moment a subagent starts, which is typically during parallel architecture-doc reads.

   **Related:** IDEA_031 (umbrella; found by the compliance-reviewer while reviewing A7 increment 3 in session 5cb38a2f, 2026-11-02). BUG_082 (filed) and BUG_040 (fixed) are the hook attribution family and do not overlap: they are about which session a write is credited to, and this is about writes to the right session being lost. Fixing BUG_082 does not close this. Unbatched: no open batch shares this mechanism (``unsatisfiable-gates`` holds gates no work can satisfy, ``inert-instruments`` holds rules whose input set is empty by construction, and this is a lost-update race on a record).

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'strict-xfail repro committed 3dcf848 on session 5cb38a2f: a stale save loses a second writer''s docs_read and agents_run and undoes an advance'
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fix cfd0891 on main (IDEA_031 A7.3b), proof 3dcf848: both repro tests pass with the strict-xfail markers removed'
        forced: false

.. bug:: Parallel writers lose session record writes: SessionDomain._save writes back a stale whole record
   :id: BUG_108
   :status: filed
   :severity: medium
   :priority: medium
   :area: domains/session, core/store
   :affects: COMP_DOMAINS_SESSION, COMP_CORE_STORE

   **Symptom:** Two processes writing one session's record at once can lose each other's writes. A file declared with ``add_files`` while another process is between its load and its save is gone from the record once that save lands. Plan entries and bindings go through the same save, so they are exposed in the same way, but only ``add_files`` was exercised.

   **Expected:** Concurrent writers in one session never lose each other's writes to the session record.

   **Repro:** Checked with a forced interleave on a temporary state root, not yet with a committed test:

   1. A process loads the session record.
   2. A second process calls ``add_files``.
   3. The first process saves. The declared file is no longer in the record.

   Status stays ``filed`` until a committed test shows this.

   **Severity reasoning:** Medium, and the impact is limited on what is known. A lost declared file or plan entry is mostly visible, and repeating the action repairs it. No gate result has been shown to flip because of this. A lost binding has not been exercised, so its effect is unknown, and that is the main reason not to rate this lower.

   **Suspected cause:** The session record has the same load-then-save-the-whole-record shape BUG_107 fixed for the pipeline record, and it has no lock. ``SessionDomain._save`` writes back whatever record its caller loaded earlier, so a stale copy overwrites anything written since. Parallel hooks and commands in one session are separate processes: the owner is one session, but not one writer.

   **Affected:** ``domains/session`` and ``core/store``. See ``docs/architecture/state_model.md``, "Domain: session", "The one edge a hook walks: the first edit", and "Storage layout", "Concurrency".

   **Doc state:** On main, both of those passages still argue that an owner is one writer, so the worst case they describe (a duplicated ``isolated -> working`` row) understates this defect. The A7 work in session 5cb38a2f rewrites both to name this exposure. That text was not yet on main when this was filed.

   **Fix sketch / next step:** Run ``bug-reproducer`` first: a two-writer test on a temporary state root, using the forced interleave so it does not depend on timing. Then two things constrain the design:

   - **Call sites.** ``_save`` has 18 call sites, many holding a session loaded far up the stack, some across gates. A lock taken only inside ``_save`` would not close this, because the stale copy was loaded before it. This size is why it was not fixed with BUG_107.
   - **Lock name.** The store names a lock ``locks/<stem>.lock``, so ``sessions/<sid>.json`` and ``pipelines/<sid>.json`` would share one, and ``flock`` does not nest across handles. A fix must change the lock name.

   The fix also has to correct the "Concurrency" paragraph and "The one edge a hook walks: the first edit".

   **Related:** BUG_107 (the pipeline-record sibling). IDEA_031 (found by the feature-architect while fixing BUG_107, increment A7.3b, session 5cb38a2f, 2026-11-02). BUG_082 (filed) and BUG_040 (fixed) are about which session a write is credited to, not about writes being lost, and do not overlap. Unbatched: no open batch shares this mechanism, and BUG_107, the only other instance, is being fixed separately, so the two will not be fixed together.

   **Scheduling:** Not scheduled. The user will decide when to take it.

.. bug:: The privacy scan reads image scale suffixes, a portrait aspect ratio, the 24:00 bound and a manifest's own allow values as findings
   :id: BUG_110
   :status: fixed
   :severity: medium
   :priority: high
   :area: privacy
   :affects: COMP_CORE_PRIVACY

   **Symptom.** A consuming iOS project cannot widen privacy.scope to its whole tree, because the scan reports four things that are not personal data. Each is a false finding on real source lines, from that project's manifest with the default patterns plus durations and device_ids.

   .. invariant: allow-privacy

   1. emails reads an image scale suffix as an address: name@2x.png in an asset catalog Contents.json (26 matches on the 2x and 3x variants). The domain position holds a scale factor and an image extension, which is no top-level domain.
   2. clock_times and durations both read the portrait video aspect ratio 9:16 as a string literal in a crop view model (a chip label list, and a switch case on the line after it). A clock-time excuse alone would only move the finding to durations, which matches the same span.
   3. durations reads the midnight bound 24:00 (a date parser's comment), and on the T24:00:00Z form clock_times also reads the tail 00:00 as a time. Hour 24 names no moment that anything observed.
   4. A value in a path-scoped privacy.allow entry is flagged when the manifest that declares it is scanned, because the entry's paths do not reach the manifest. So the allow list cannot be clean once the scope covers the manifest. The match is the IPv4 loopback in a values list scoped to the test tree.

   **Must stay findings.** A real address on a domain shaped like a scale (someone at 2x.com); a time in prose at the same value, unquoted or zero-padded; other durations such as 24:10; a value that is not declared, elsewhere in the manifest.

   **Blocks** the consuming project's scope-widening step, its config extraction and its comment and fixture wipe.

   **Repro proof:** tests/test_privacy.py::TestBUG110Repro, committed on this bug's session branch (2026-11-02), one strict-xfail test per symptom. test_BUG_110_an_image_scale_suffix_is_not_an_email asserts no finding on an asset catalog filename line and an icon filename. test_BUG_110_a_quoted_aspect_ratio_is_not_a_time asserts no finding on a quoted portrait ratio with clock_times and durations both on. test_BUG_110_the_midnight_bound_names_no_moment asserts no finding on hour 24, in a comment and in the timestamp form. test_BUG_110_a_manifest_does_not_flag_the_values_it_declares asserts no finding on a scoped entry's values line when the manifest itself is scanned.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: red at the PrivacyScanner seam, one test per symptom
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fixed by 685a7c8: four narrow excuses in the scanner; strict xfails deleted, repro green, must-stay guards added'
        forced: false

.. bug:: A doctor test expects no warning row on a current project and fails on main
   :id: BUG_111
   :status: fixed
   :severity: medium
   :priority: medium
   :area: guidelines,tests
   :affects: COMP_DOMAINS_GUIDELINES

   **Symptom.** tests/test_guidelines_current.py::TestTheCommands::test_doctor_json_carries_no_warning_on_a_current_row fails on unchanged main at 9ae2f91, alone and in a full run. The guidelines generated row is not the failure: the assertion that fails is the one that every doctor row has warn False, so some other row warns in the fixture project. Which row was not established.

   **Why it matters.** It fails test.all_pass for every session on this machine. BUG_110 landed past that gate with a forced advance by user ruling, citing this failure on the main baseline.

   **Next step.** Print the row that warns. It may read something from the host environment rather than the fixture; that is a guess, not a finding.

   **Repro proof:** tests/test_guidelines_current.py::TestTheCommands::test_doctor_json_carries_no_warning_on_a_current_row, committed 8bf6e82 (2026-10-27), red run recorded 2026-11-02 — asserts every doctor row has warn false once the guidelines outputs are rendered. No new test: this one is the proof.

   **Finding 2026-11-02: a test defect, no product defect.** The row that warns is library agents current, with all four library agents reported absent. It reads the fixture, not the host: the fixture project never installs them, and the row stays red with HOME pointed at an empty folder. Warning on an absent agent is the row's documented contract (cli.md, "The library agents current row", the A6.4 ruling). The test has been red since ce4f277 (A6.4, which added that warning and updated other doctor tests but not this one) and is green at its parent. With only the fixture changed it goes green either way: agents install before doctor, or an empty agents.library. **Fix site:** this test's fixture, not the doctor.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'red at the existing test; the warning row is library agents current, absent in the fixture: a test defect'
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fixed by 1c28719: the test fixture installs the library agents before doctor; the assertion that no row warns is unchanged'
        forced: false

.. bug:: The oracle web page clipboard test fails on main: the copy falls back to selecting the text
   :id: BUG_112
   :status: fixed
   :severity: medium
   :priority: medium
   :area: oracle,tests
   :affects: COMP_ORACLE_WEBPAGE

   **Symptom.** tests/test_oracle_webpage.py::TestCopy::test_over_https_the_clipboard_gets_the_command fails on unchanged main at 9ae2f91, alone and in a full run. Over the simulated HTTPS page the command lands in selected rather than copied, and the label stays Copy command, so the page took its plain HTTP fallback or the clipboard promise rejected in the harness.

   **Why it matters.** It fails test.all_pass for every session on this machine. BUG_110 landed past that gate with a forced advance by user ruling, citing this failure on the main baseline.

   **Next step.** The harness assigns globalThis.navigator under Node 24, which has its own navigator global; check whether that assignment takes effect. That is a suspicion, not a finding, and whether the real page is affected is not known.

   **Repro proof:** tests/test_oracle_webpage.py::TestCopy::test_over_https_the_clipboard_gets_the_command, committed d6debdb (2026-10-24), red run recorded 2026-11-02 — asserts the command reaches the clipboard and the label reads Copied. No new test: this one is the proof.

   **Finding 2026-11-02: a harness defect, the real page works.** Node 21 and later define navigator on the global as a getter with no setter, so the harness's plain assignment at tests/test_oracle_webpage.py:153 is silently dropped in a sloppy script: navigator.clipboard stays undefined and the page takes its plain HTTP branch. The test engine is the first node on PATH, else macOS's jsc (tests/test_oracle_web.py, \_javascript). Unchanged, the test passes under jsc and fails under Node 24. With only that line changed to Object.defineProperty, it passes under Node, page unchanged. The real page, in headless Chrome on a secure localhost origin, clicked with a real mouse event: label Copied, and the clipboard read back the command. Safari was not driven. The onset looks like the node on PATH: the link to Node 24 is dated 2026-10-28, after the test was added; that link's date is the evidence, not a recorded run. **Fix site:** the harness line, not src/invariant/oracle/webpage.py.

   .. invariant-history:
      - from: filed
        to: reproduced
        reason: 'red at the existing test under Node only; the harness navigator assignment does not take: a harness defect, the real page copies'
        forced: false
      - from: reproduced
        to: fixed
        reason: 'fixed by 1c28719: the page harness sets navigator with Object.defineProperty, so the test reaches the clipboard branch under Node and jsc'
        forced: false

.. bug:: A work item id minted in a worktree can collide with one minted on main or in another worktree, and the clash surfaces only at rebase
   :id: BUG_113
   :status: filed
   :severity: medium
   :priority: high
   :area: items,session
   :affects: COMP_CORE_ITEMS, COMP_DOMAINS_SESSION

   **Symptom:** ``invariant item new`` mints the next id by looking only at the checkout it runs in. A session filing an item in its worktree cannot see an id another session has since put on main or holds in its own worktree, so both spend the same number. Nothing refuses it at filing time. The clash surfaces as an add/add rebase conflict on ``docs/backlog/BUG_NNN.md`` at landing, or, if the two files never meet, as two unrelated bugs under one id.

   **Expected:** Two live sessions never get the same id, or the clash is refused when filed rather than at landing.

   **Repro:** Observed twice in one session on 2026-11-02:

   1. A privacy bug filed in session acdc0d63 took BUG_107 while session 5cb38a2f put a different BUG_107 on main, and that session's landing stopped on the conflict.
   2. After renumbering, its BUG_108 clashed with a new BUG_108 on main.

   The fix landed as :need:`BUG_110 <BUG_110>`, with its two test bugs as :need:`BUG_111 <BUG_111>` and :need:`BUG_112 <BUG_112>`. The shape to reproduce: from one commit, file an item in each of two worktrees of one repository, and both are given the same id. Status stays ``filed`` until a committed test shows this.

   **Cost:** Renumbering means rewriting the session's unpushed commits (commit messages, test names, code comments citing the id), which changes every hash, including the repro proof commit an item may cite. A pipeline's task text still carries the old id, which now names another session's bug, because pipeline state is not rewritten.

   **Suspected cause:** ``_next_id`` in ``src/invariant/core/items.py`` computes from the ids it is handed, and ``taken_ids`` supplies only those in this checkout's ``items.path`` and ``items.closed_path``. Nothing outside that working tree is consulted.

   **Affected:** ``core/items``, which owns "the ids a backend has spent ... and minting from them" (``docs/architecture/overview.md``), and ``item new`` as described in ``docs/architecture/cli.md``. On the session side, the same-number spend is only possible because each worktree holds its own copy of the store; the shared state root is described in ``docs/architecture/state_model.md``, "Storage layout".

   **History:** Not the first instance. :need:`BUG_043 <BUG_043>` records the same base-drift collision on BUG_036, renumbered to BUG_039 at finalising; that entry is about the reviewer's provenance claim and never tracked the collision itself. :need:`BUG_074 <BUG_074>` (fixed) widened the same uniqueness check to the closed store, and left it scoped to one checkout.

   **Fix sketch / next step:** Run ``bug-reproducer`` first. The fix is not prescribed. Options to record for the designer:

   - Mint against every worktree and branch of the repository.
   - Reserve ids in the shared state root rather than the working tree.

.. bug:: The docs assessment counts the architecture docs directory as an undocumented source area under the generic adapter
   :id: BUG_114
   :status: filed
   :severity: low
   :priority: medium
   :area: domains/docs, adapters
   :affects: COMP_DOMAINS_DOCS

   **Symptom:** On a project using the generic adapter, the docs assessment counts the committed architecture docs directory (the resolved ``paths.docs``) as a source area. ``invariant docs assess`` and the doctor row report that directory as an undocumented area, and ``invariant docs assess --plan`` records a ``Document docs`` deliverable for it.

   **Expected:** The resolved docs location is never a source area, under any ``docs.assess.areas.from`` (``adapter``, ``globs`` or ``declared``), so neither ``docs assess``, the doctor row, the plan nor the incremental rule ``docs.area_documented`` names it.

   **Repro:** A project on the generic adapter with no ``measured_scope``, whose architecture docs are committed in a top-level directory holding files ``source_files`` accepts. Run ``invariant docs assess``: the docs directory appears among the areas and as an ``areas`` gap. Run ``invariant docs assess --plan``: the plan carries ``Document <docs dir>``. Status stays ``filed`` until a committed test shows this.

   **Suspected cause:** The base ``source_areas`` hook (ADR-016 D2), with no ``measured_scope``, falls back to the top-level directories holding a tracked file ``source_files`` accepts. The generic adapter accepts the docs files, so the docs directory qualifies. Neither the hook nor ``_derive_areas`` in ``src/invariant/domains/docs.py`` subtracts the docs location; only dot-directories are held out. Behaviour from increment 1 (commit 21ea1c5), unchanged by increment 2 (commit 9a487f7).

   **Affected:** ``domains/docs`` (assessment and ``plan_deliverables``) and the ``source_areas`` hook in ``adapters/``, both described in ``docs/architecture/overview.md``; the decisions are ADR-016 D1, D2 and D5 (``docs/adr/ADR-016-the-architecture-docs-are-assessed-and-init-offers-to-reuse-relocate-or-write-them.md``). Part of the work tracked by :need:`IDEA_043 <IDEA_043>`.

   **History:** Found by feature-architect while building ADR-016 increment 2 (session 965d8391), open question (3) in its report. Workaround: list the docs directory in ``docs.assess.areas.exclude``.

   **Fix sketch / next step:** Run ``bug-reproducer`` first: a red test with the generic adapter, a top-level docs directory set as ``paths.docs``, asserting it is absent from the assessment's areas and from ``plan_deliverables``. The likely fix point is ``_derive_areas``, subtracting the resolved docs location after every source (so ``globs`` and ``declared`` are covered too), rather than each adapter's hook. Whether other non-source trees invariant owns (records, reports, sweeps paths) should be held out by default is a design question for the fix.

.. bug:: A change that adds a judgement guideline can never record a verdict on it: DN-6 refuses any verdict once the tree's ruleset differs from the pin
   :id: BUG_115
   :status: filed
   :severity: medium
   :priority: medium
   :area: core/rules, core/verdicts, guidelines domain
   :batch: unsatisfiable-gates
   :affects: COMP_CORE_RULES, COMP_CORE_VERDICTS, COMP_DOMAINS_GUIDELINES

   **Symptom:** A change that adds a judgement guideline to docs/guidelines/ruleset.yaml, whose ``applies_to`` covers a file the same change edits, owes a verdict on that new rule under ``guidelines.verdicts_complete``, but verdict submission refuses every verdict on it. P1 requires the tree's ruleset to have the digest the pipeline pinned at ``start``, and the change itself moved the tree's digest. The refusal offers three routes: restore the pinned ruleset (undo the change), finish this pipeline and start a fresh one, or override. So the change can never carry a recorded verdict on the rule it adds, only an override.

   Observed 2026-11-08, session 07fb9dda, commit 719a298 ("Guidelines - interview the user when their input is needed"). The pipeline pinned ruleset 9e3251963914. Adding ``conduct.interview-when-input-is-needed`` (phases ``[always]``, ``applies_to`` including CLAUDE.md) moved the tree's ruleset to e6ed413237c0. The change created CLAUDE.md, so the rule was owed. A pass citing CLAUDE.md 3 3 was refused with "the ruleset in the tree is e6ed413237c0, and the pipeline pinned 9e3251963914". The compliance-reviewer recorded ``invariant override conduct.interview-when-input-is-needed`` instead; ``verdicts_complete`` showed "1 passed by override", held, not spent (the gate is at warn here; at block the override is the only route to land).

   **Expected:** A change that edits the ruleset has a sanctioned, non-override way to record verdicts on the rules it adds or changes, and ``verdicts_complete`` can be satisfied by recorded verdicts. Candidate shapes (a design call, not settled here): an explicit, recorded re-pin within the same pipeline, or judging rules the change itself adds against the tree's ruleset while the pin still governs every rule the change did not touch.

   **Repro:** In a project with ``guidelines.ruleset`` configured: ``invariant pipeline start`` (ruleset pinned), then in the change add a judgement guideline whose phases reach review (or ``always``) and whose ``applies_to`` matches a file the change also edits. Advance to a review-role state and submit a pass on the new rule citing that file. Observed: refused under P1/DN-6 naming both digests; ``guidelines.verdicts_complete`` reports the rule owed. Control: the same verdict on a rule present in the pinned ruleset, in a change that does not edit the ruleset, is accepted.

   **Suspected cause:** Not an implementation slip but a gap in the contract. ADR-012 DN-6 rules that submission is refused while the tree's ruleset digest differs from the pin and "nothing repins"; docs/architecture/state_model.md states the same for the verdict record's ``ruleset`` field. DN-6 guards against a ruleset edited mid-pipeline to dodge a judgement, but it does not distinguish that from a change whose subject IS the ruleset, so the owed judgement on the new rule is unsatisfiable by construction. Same shape as BUG_102: a gate whose only offered route is a bypass.

   **Affected:** docs/adr/ADR-012-the-guidelines-domain-and-the-model-agnostic-contract.md (DN-6, A4, the P1 predicate); docs/architecture/cli.md (``guidelines verdict``, predicate P1); docs/architecture/state_model.md (the ruleset pin, the verdict record's ``ruleset`` field, ``guidelines.verdicts_complete`` staleness); docs/architecture/overview.md (``core/rules``: ``ruleset_pin``, ``plan_verdicts``, ``guidelines.verdicts_complete``). Owner: ``core/rules`` and ``core/verdicts``.

   **Related:** IDEA_031 (guidelines ownership umbrella; A4 is its verdict increment). BUG_102 (same mechanism, fixed). Batched with ``unsatisfiable-gates``: a gate with an owed answer that no honest change can give except by bypass.

   **Fix sketch / next step:** Run ``bug-reproducer`` first: a strict-xfail test in tests/test_rules.py (or the verdicts tests) that pins a ruleset, edits it to add a judgement rule matching a changed file, and asserts a pass on the new rule is accepted and ``guidelines.verdicts_complete`` passes without an override; plus a control that a rule the change did NOT add still refuses a verdict after an unrelated ruleset edit (DN-6's guard must survive). The fix is a DN-6 amendment for ``feature-architect``: choose between a recorded re-pin and judging change-added rules against the tree, then update ADR-012, cli.md P1 and state_model.md.

.. bug:: init creates and gitignores .claude/invariant/state while the engine's state root is the git common directory
   :id: BUG_116
   :status: filed
   :severity: low
   :priority: low
   :area: cli (init), core/manifest
   :affects: COMP_CLI_MAIN, COMP_CORE_MANIFEST

   **Symptom:** In a fresh git project, ``invariant init`` creates an empty ``.claude/invariant/state`` directory and appends ``.claude/invariant/state/`` to ``.gitignore``, while the engine writes all runtime state under the git common directory (``.git/invariant/state``). The created directory is never used and the ``.gitignore`` line ignores the wrong path. A store project created by init gets the same stray directory and marker.

   **Expected:** init creates the state root the engine actually uses (the manifest's resolved ``Manifest.state_root``: ``paths.state`` when set, else the git common directory, else ``.claude/invariant/state`` outside git), and writes a ``.gitignore`` line only when that root lies inside the working tree. A root under ``.git`` needs none, since git already ignores it.

   **Repro:** ``git init`` an empty directory, run ``invariant init``, then start any session. ``.claude/invariant/state`` exists and is empty, ``.gitignore`` carries ``.claude/invariant/state/``, and session records land under ``.git/invariant/state``. Seen in a fresh project (Darvag). Status stays ``filed`` until a committed test shows this.

   **Suspected cause:** ``cmd_init`` in ``src/invariant/cli/main.py`` hard-codes the path in two places: ``JsonStore(root / ".claude" / "invariant" / "state").ensure_root()`` and ``marker = ".claude/invariant/state/"``. Both predate the move of the default state root to the git common directory (``Manifest.state_root`` in ``src/invariant/core/manifest.py``, which keeps ``.claude/invariant/state`` only as the outside-git fallback). ADR-016 increment 2 (commit 9a487f7) moved these lines without changing them; feature-architect (session 965d8391) confirmed they are still hard-coded.

   **Affected:** ``invariant init`` (``docs/architecture/cli.md``, Setup: "create the state root, and add the state root to .gitignore"); the state root resolution (``docs/architecture/state_model.md``, Storage layout, "Why the git common directory"); ``docs/architecture/manifest.md`` (``paths.state``, whose sample value ``.claude/invariant/state`` reads like the default and may want a note when the fix lands); ``docs/architecture/overview.md``, where things live at rest.

   **Fix sketch / next step:** Run ``bug-reproducer`` first: a red test that runs init in a fresh git repository and asserts no ``.claude/invariant/state`` directory and no ``.gitignore`` marker are written, and that the resolved state root exists. Then a sibling case with a relative ``paths.state`` inside the tree (marker written, for that path) and one outside git (fallback root, marker written). The fix reads the root from the manifest init has just written (or, for a store project, the resolved one) rather than recomputing it; whether init should remove a stray marker left by earlier runs is a question for the fix design.

.. bug:: A vitest run's summary is never parsed, so a node project's red run records no failure count and can never prove red
   :id: BUG_117
   :status: filed
   :severity: high
   :priority: high
   :area: adapters/node
   :batch: unsatisfiable-gates
   :affects: COMP_ADAPTERS_NODE

   **Symptom:** On a Node project whose test runner is vitest, every recorded test run carries ``executed=None`` and ``failed=None``, with the note "no executed-test count found in the output". The run's pass/fail is read from the exit code alone. Consequences downstream, read from the consumers in ``core/rules``: ``test.new_test_fails`` refuses every vitest red run as "no failure count could be parsed from its output" (the BUG_029 guard), so the bugfix lane cannot record its proof of red on a vitest project; ``test.all_pass`` reports a failing run as "an unknown number of failing"; and the zero-executed checks (``test.all_pass``, the coverage run's executed-nothing refusal in ``domains/pipeline``) never fire, because ``None`` is not ``0``.

   **Expected:** The node adapter reads vitest's summary as it reads jest's: executed total, failed count (and passed / skipped where shown). Vitest prints ``Tests  1 failed | 1 passed (2)`` (no colon, pipe-separated parts, total in parentheses) above a ``Test Files  1 failed (1)`` line; a passing run prints ``Tests  2 passed (2)``.

   **Repro:** Feed real vitest console output to ``NodeAdapter().parse_test_output(out, "", 1)``. Verified 2026-11-08 at 3f76c73 with a two-test, one-failure vitest summary: the result is ``TestResult(passed=False, exit_code=1, total=None, failed=None, ..., note='no executed-test count found in the output')``. Observed first while watching a consuming project's first pipeline run (a vitest project).

   **Suspected cause:** ``src/invariant/adapters/node.py`` ``parse_test_output`` keys both counts on jest's summary shape only: ``r"Tests:.*?(\d+) total"`` and ``r"Tests:.*?(\d+) failed"``. Vitest's line has no ``Tests:`` colon and no ``total`` word, so neither pattern matches. The adapter's own ``is_test_file`` docstring already claims "the conventions jest, vitest and node:test all key on", so vitest is inside the adapter's stated contract; the output parser never followed.

   **Affected:** ``adapters/node`` — per ``docs/architecture/overview.md`` (section "adapters/"), the layer that knows "how to tell a real failure from a tooling failure". Consumers that go wrong on its ``None``: ``test.new_test_fails`` (``_why_not_proof``) and ``test.all_pass`` in ``core/rules``, and the coverage zero-executed check in ``domains/pipeline``.

   **Fix sketch / next step:** ``bug-reproducer`` first: a unit test feeding captured vitest summaries (failing, passing, with skipped) to ``NodeAdapter.parse_test_output`` and asserting the counts — red today. The fix adds vitest's ``Tests  … (N)`` shape alongside jest's, and must not let the ``Test Files`` line be read as the test count. Also worth checking in the same pass: the ``jest.pattern_matched_no_tests`` hazard detects only jest's wording (``No tests found``, ``Tests: 0 total``); vitest's no-tests wording is not covered (vitest exits non-zero there by default, so this is unverified as a live false green). node:test's TAP/spec summary is a third shape the parser does not read either — out of scope here unless the fix chooses a per-runner dispatch.

   **Batch.** Filed to ``unsatisfiable-gates``, beside its mechanism sibling BUG_038 (a swift-testing run scrapes as zero tests executed): both are an adapter's test-output scraper keyed to one runner's summary format, read against a second runner the adapter claims, and both leave the red-proof gate unable to pass. Not ``inert-instruments``, although the zero-executed checks also go quiet: the user-facing failure is the bugfix lane's red that can never be recorded.

   **Related:** BUG_018 (node coverage parser absent; same adapter, different method — coverage, not test counts), BUG_038 (mechanism sibling), BUG_029 (the guard that turns ``failed=None`` into a refusal — correct, not to be relaxed), ADR-013 (second-ecosystem commands, ``commands.node.*``), IDEA_038.

.. bug:: A Sphinx-needs documentation repository is detected as Python, not Sphinx
   :id: BUG_118
   :status: filed
   :severity: medium
   :priority: medium
   :area: adapters (detection), cli (init)
   :affects: COMP_ADAPTERS_BASE, COMP_ADAPTERS_PYTHON, COMP_ADAPTERS_SPHINX
   :decided_by: DEC_ADR_015_D1, DEC_ADR_018_D13

   **Symptom:** Running ``invariant init`` in a documentation repository whose own content is a Sphinx-needs project detects the ecosystem as ``python``, not ``sphinx``. The manifest gets Python's commands and adapter, so the repository's build, linkcheck and needs runs are not its gates. Reported by the user 2026-11-10.

   **Expected:** A repository whose own content is a Sphinx project is detected as ``sphinx`` (a documentation repository, :need:`ADR-015 D1 <DEC_ADR_015_D1>`, :need:`ADR-018 D13 <DEC_ADR_018_D13>`) without the user naming the ecosystem by hand, whether its ``conf.py`` sits at the root or under ``docs/``, and whether or not it also carries a ``pyproject.toml`` or ``setup.py`` used only to install Sphinx and its extensions.

   **Repro:** The user's repository layout is unknown (where ``conf.py`` sits, whether it has ``pyproject.toml``); repro owed. Two toy repositories should each show one cause: (a) a root ``conf.py`` with ``sphinx_needs`` in its extensions plus a root ``pyproject.toml`` declaring only Sphinx dependencies; (b) the content at ``docs/conf.py``, with or without a ``pyproject.toml``. Run ``invariant init`` (or ``adapters.detect``) in each: (a) yields ``python`` first, so primary; (b) does not yield ``sphinx`` at all. Status stays ``filed`` until a committed test shows this.

   **Suspected cause:** Two, found by reading the code (not yet proven).

   1. ``detect()`` in ``src/invariant/adapters/__init__.py`` checks in a fixed order (``swift_xcode``, ``python``, ``node``, ``flutter``, ``dart``, ``sphinx``) and the first match is the primary. The python adapter (``adapters/python_.py``) matches on a root ``pyproject.toml`` or ``setup.py``, which a documentation repository often has only to install Sphinx, so ``python`` wins even when ``sphinx`` is also detected.
   2. ``adapters/sphinx.py`` ``detect()`` calls ``source_dirs(skip_docs_project=True)``, which skips a ``conf.py`` at the location invariant reserves for its own docs project (``docs/``, from the default ``paths.docs``, ADR-018 D3). A documentation repository with its content at ``docs/`` is therefore never detected as ``sphinx``. ADR-018 "As built: increment 2" records this ("detected only by ``init --ecosystem sphinx``"), but no item was filed for it.

   **Affected:** the detection order in ``adapters/`` (:need:`COMP_ADAPTERS_BASE`, the adapter interface and registry), the python adapter's detection (:need:`COMP_ADAPTERS_PYTHON`) and the sphinx adapter's detection (:need:`COMP_ADAPTERS_SPHINX`), all in ``docs/architecture/overview.rst``; the ecosystem selection in ``invariant init``.

   **Workaround:** ``invariant init --ecosystem sphinx``.

   **Related:** :need:`DEC_ADR_018_D13` (documentation repositories: invariant's docs project in the store by default, ``records.docs: store``), scheduled for ADR-018 increment 8. That init does not offer to keep invariant's docs project outside such a repository is D13's work and NOT part of this bug, but the two should be built together: once D13's store default holds, ``docs/`` is no longer invariant's docs project in such a repository, which is what cause 2 needs to stop skipping it. :need:`BUG_114 <BUG_114>` is a different defect (the generic adapter's docs area in the assessment). :need:`IDEA_041 <IDEA_041>` is the umbrella for documentation repositories getting gates.

   **Fix sketch / next step:** Run ``bug-reproducer`` first: two red tests on toy repositories in the adapter tests, (a) root ``conf.py`` plus a Sphinx-only ``pyproject.toml`` asserting ``detect()`` puts ``sphinx`` first, (b) ``docs/conf.py`` asserting ``sphinx`` is detected. The fix is a design call for ``feature-architect`` alongside ADR-018 increment 8: whether a detected Sphinx content tree outranks a Python packaging file (for example when the package declares no importable source beyond Sphinx dependencies, or the ``conf.py`` loads ``sphinx_needs``), and how the skip of the docs project's source directory is resolved against D13's store default rather than the default ``paths.docs``. A second ``conf.py`` still asks rather than guesses (ADR-015 D1).

.. bug:: A privacy exemption marker stops covering its code block once docs migrate converts the page to rST
   :id: BUG_119
   :status: filed
   :severity: medium
   :priority: medium
   :area: core/privacy, core/mdconvert (docs migrate, item migrate)
   :affects: COMP_CORE_PRIVACY, COMP_CORE_MDCONVERT, COMP_DOMAINS_DOCS, COMP_CMD_SETUP, COMP_CMD_ITEMS
   :decided_by: DEC_ADR_001_D24, DEC_ADR_018_D14
   :batch: docs-migrate-output

   **Symptom:** After ``invariant docs migrate prose``, ``docs migrate adr`` or ``item migrate`` converts Markdown to rST, a privacy exemption marker that stood on its own line before a fenced code block no longer covers that block. The block's contents become ``privacy.clean`` findings. In ADR-018 increment 7 (439f86b, 2026-11-10) this produced 12 findings on invariant's own migrated backlog and docs that would have blocked the landing; two blocks (in the bodies of BUG_053 and BUG_055) were rewritten by hand as line blocks to get past it.

   **Expected:** A marker that exempted a block in the Markdown source still exempts the same content after the conversion (:need:`ADR-001 D24 <DEC_ADR_001_D24>`: a marker that owns its line is block-scoped), so a migration neither creates landing blockers nor silently drops an exemption.

   **Repro:** A Markdown page holding an own-line exemption marker (HTML comment) followed by a fenced code block whose content the privacy scan flags. Convert it with ``invariant docs migrate prose`` (or as an item body through ``item migrate --to sphinx_needs``), then run ``invariant privacy scan`` on the output: the block's lines are findings. The same scan on the Markdown source is clean.

   **Suspected cause:** ``core/mdconvert`` keeps the HTML-comment marker as an rST comment (as designed), but in rST the marker comment, the ``::`` paragraph that introduces the literal block, and the indented literal block are three separate blocks. ``core/privacy``'s block rule (``_exempt_lines``) exempts only the block right after the marker, which is now the ``::`` line, so the literal block itself is uncovered.

   **Affected:** :need:`COMP_CORE_PRIVACY` (the exemption marker and how far it reaches) and :need:`COMP_CORE_MDCONVERT` (an HTML comment kept as an rST comment), reached through :need:`COMP_DOMAINS_DOCS` and :need:`COMP_CMD_SETUP` (``docs migrate``) and :need:`COMP_CMD_ITEMS` (``item migrate``). Affects every project migrating Markdown that carries privacy markers (:need:`ADR-018 D14 <DEC_ADR_018_D14>`).

   **Fix sketch / next step:** ``bug-reproducer`` first: a unit test converting a marker plus fenced block through ``convert_prose`` with a stub pandoc (or a captured tree) and scanning the result, asserting the block's lines are exempt; red today. Two fix options for ``feature-architect``: (a) ``core/privacy`` treats a lone ``::`` line, or a directive line, as joining the block after it, so the rST shape is one exempt unit wherever it comes from (also covers hand-written rST); (b) the converter writes a literal block that follows a marker as a line block, keeping the scan unchanged. (a) touches the ADR-001 D24 rule's wording and wants a ruling; (b) is local to the converter but leaves hand-written rST with the same trap. Related: BUG_045 (the marker made block-scoped), IDEA_002 (reporting what a marker suppressed).

   **Batch:** docs-migrate-output — defects where ``docs migrate`` / ``item migrate`` writes rST that the landing gates or the build do not accept as-is, so the migration session fixes it by hand. Diagnosis method shared: migrate a toy Markdown project and check the output against the privacy scan, the strict build and the toctrees. Members: this bug, then the duplicate-toctree-entry bug.

.. bug:: docs migrate prose lists a page twice: it appends to the index's last toctree when another toctree already lists it
   :id: BUG_120
   :status: filed
   :severity: low
   :priority: low
   :area: domains/docs (docs migrate prose)
   :affects: COMP_DOMAINS_DOCS, COMP_CMD_SETUP
   :decided_by: DEC_ADR_018_D14
   :batch: docs-migrate-output

   **Symptom:** ``invariant docs migrate prose <files>`` adds each converted page to the last toctree of the nearest ``index.rst`` even when another toctree in that same index already lists the page, so the index ends up with the page listed twice. In ADR-018 increment 7 (439f86b, 2026-11-10) five duplicate entries were removed by hand.

   **Expected:** A page already listed by any toctree in the index is left where it is; migrate appends it only when no toctree in the index lists it, and re-running migrate is idempotent on the index.

   **Repro:** A docs project whose ``index.rst`` has two toctrees, the first listing ``architecture/overview``; migrate ``architecture/overview.md`` with ``invariant docs migrate prose``. The last toctree gains ``architecture/overview`` while the first still lists it.

   **Suspected cause:** The toctree helper in ``domains/docs`` (``index`` with ``entry`` appended to its last toctree) checks only the last toctree for an existing entry, or none, before appending.

   **Affected:** :need:`COMP_DOMAINS_DOCS` (the migrate writes into the index) and :need:`COMP_CMD_SETUP` (the ``docs migrate`` command), per :need:`ADR-018 D14 <DEC_ADR_018_D14>`.

   **Fix sketch / next step:** ``bug-reproducer`` first: a unit test on the toctree-append helper with a two-toctree index whose first toctree already lists the entry, asserting the index is unchanged; red today. The fix reads every toctree in the index before appending. Low impact: Sphinx tolerates the duplicate (at worst a warning), but it is hand cleanup per migration.

   **Batch:** docs-migrate-output (second member, after the privacy-marker bug; see its preamble).

.. bug:: The Sphinx starter's conf.py uses re.M, so every project init creates starts with a ruff FURB167 finding
   :id: BUG_121
   :status: filed
   :severity: low
   :priority: low
   :area: data/scaffold (init)
   :affects: COMP_DATA_SCAFFOLD, COMP_CMD_SETUP
   :decided_by: DEC_ADR_018_D11

   **Symptom:** Every project created by ``invariant init`` gets a ``docs/conf.py`` from the Sphinx starter that uses the ``re.M`` flag alias, which ruff reports as FURB167. A project linting with ruff starts with a finding it did not write. invariant's own ``docs/conf.py`` was fixed by hand to ``re.MULTILINE`` in ADR-018 increment 7 (439f86b, 2026-11-10); the template still has ``re.M``.

   **Expected:** The starter's ``conf.py`` is lint-clean under ruff's defaults, so init adds no lint finding to a new project.

   **Repro:** ``src/invariant/data/scaffold/sphinx/conf.py`` (the item-field scan's ``re.findall``) uses ``re.M``; run ``ruff check --select FURB167`` on it, or on the ``conf.py`` of any project ``invariant init`` wrote.

   **Suspected cause:** Template written with the short flag alias; known (the copy in invariant's own docs project was corrected), not propagated back to the template.

   **Affected:** :need:`COMP_DATA_SCAFFOLD` (the docs starter's templates, :need:`ADR-018 D11 <DEC_ADR_018_D11>`), written by ``init`` in :need:`COMP_CMD_SETUP`.

   **Fix sketch / next step:** ``bug-reproducer`` first: a test that runs the starter's ``conf.py`` through ruff (or asserts no ``re.M`` / short-flag use, if ruff in the suite is unwanted); red today. Fix: ``re.MULTILINE`` in the template. Projects already initialised keep the finding until fixed by hand.

   **Batch:** unbatched — no open batch shares the mechanism (a starter template's lint).

.. bug:: The quickstart guide still says init writes docs/architecture/overview.md, the retired Markdown starter
   :id: BUG_122
   :status: filed
   :severity: low
   :priority: low
   :area: user guide (init, starter)
   :affects: COMP_CMD_SETUP, COMP_DATA_SCAFFOLD
   :decided_by: DEC_ADR_018_D11

   **Symptom:** The user guide ``docs/guide/quickstart.md`` still says ``invariant init`` writes ``docs/architecture/overview.md`` (the retired Markdown starter): the table of files init writes (around line 28) and the docs-read step (around line 101, with the ``--force --reason`` example after it naming ``overview.md``). Since ADR-018 increment 4 (6267443) init writes the Sphinx-needs starter (``conf.py``, ``index.rst``, ``architecture/overview.rst`` and the rest). A new user following the guide looks for a file that is not there.

   **Expected:** The quickstart describes what init writes today: the Sphinx-needs starter under the docs location, its ``architecture/overview.rst``, and the strict build (``invariant docs build``) the docs owe.

   **Repro:** Read ``docs/guide/quickstart.md`` against the files ``invariant init`` writes in an empty project.

   **Suspected cause:** The guide was outside increment 4's and increment 7's touched set; increment 7 rewrote links into converted Markdown files but not prose describing the starter.

   **Affected:** the user guide, describing ``init`` (:need:`COMP_CMD_SETUP`) and the starter (:need:`COMP_DATA_SCAFFOLD`, :need:`ADR-018 D11 <DEC_ADR_018_D11>`; ADR-016's Markdown ``overview.md`` retired).

   **Fix sketch / next step:** A docs-only change; no failing test to write, so ``bug-reproducer`` confirms by reading the guide against init's output and can record the defect as actual. Fix: rewrite the two passages (and the example reason string) for the Sphinx-needs starter; check the rest of ``docs/guide/`` for other ``overview.md`` mentions in the same pass.

   **Batch:** unbatched — a stale user-facing doc; no open batch shares the mechanism.

.. idea:: The links pattern's TLD-like suffix rule drops a punycode IDN top-level domain
   :id: IDEA_001
   :status: done
   :area: core/privacy
   :severity: low
   :priority: low

   Found in review of BUG_044, not covered by its tests.

   The scheme arm's host test ends in ``\.[A-Za-z]{2,}`` followed by ``(?![A-Za-z0-9_-])``. A punycode internationalised top-level domain is alphanumeric and ends in a digit or a digit-bearing label, so the suffix rule cannot consume it and the end-of-host lookahead then rejects the partial match. A genuine web address in that form is therefore no longer a finding, where the pre-BUG_044 pattern did flag it.

   Measured: an address whose authority is a two-label name under a punycode top-level domain is not flagged by the scheme arm. The schemeless ``www.`` arm still flags the ``www.``-prefixed spelling of the same address, because that arm has no end-of-host lookahead, so the gap is confined to the scheme arm.

   Narrow, and strictly smaller than the false-positive flood BUG_044 removed, so it was not treated as a blocker on that change. A fix is a suffix alternative for the punycode form, plus a corpus member in the host-authority table of TestLinksNameANetworkHost so the class stays crossed against every scheme.

   .. invariant-history:
      - from: proposed
        to: accepted
        reason: 'Implemented in 7faaafa lineage (merged as cb3815b): punycode TLDs now flagged by links'
        forced: false
      - from: accepted
        to: in_progress
        reason: landed in cb3815b
        forced: false
      - from: in_progress
        to: done
        reason: shipped on main in cb3815b with a punycode corpus member in tests/test_privacy.py
        forced: false

.. idea:: A privacy scan reports nothing about what an exemption marker suppressed
   :id: IDEA_002
   :status: proposed
   :area: core/privacy
   :severity: low
   :priority: medium
   :slug: report-what-a-marker-suppressed
   :residue: REAL-CORPUS DATA POINT, from a consuming project (Heap), 2026-08-24, via BUG_053's reporter. The cost this entry predicts is already being paid and is measurable: to suppress FOUR false ip_addresses matches, that project added exemption markers that — because an exemption reaches to the end of its block (BUG_045) — make 58 lines across two files exempt from a BLOCKING gate. A 14:1 ratio of silenced lines to suppressed findings, and every one of those lines is a region where a real violation would now pass silently. The entry's argument was that a reader of the VERDICT cannot see what a marker set aside\; this measurement adds that a reader of the DIFF cannot easily see it either once the markers are numerous, which weakens BUG_045's structural answer — the reach is two blocks, so a file-wide exemption is a diff full of markers — exactly where it was strongest. Note the ratio is a property of markup with long blocks, reST with 20-line paragraphs, rather than of the exemption design, so read it as an upper bound rather than as typical. The four findings it was spent on are BUG_053's.

   Deferred out of BUG_045 deliberately, so that the scope change and a new
   reporting surface were not one diff.

   ``scan_text`` drops an exempted line before any pattern runs, so nothing
   downstream can tell "this text is clean" from "this text has three findings
   behind two markers". The verdict ``privacy.clean`` prints, and ``invariant privacy scan``'s output, are identical in both cases.

   That is the shape this repository already refuses elsewhere. ``overview.md`` says
   it of the coverage levers: *an exclusion nobody can see is indistinguishable
   from a gate that did not run*, and both ``coverage_exclude`` and
   ``coverage_baseline`` name what they set aside in the verdict for exactly that
   reason. The privacy exemption is the same kind of lever and does not.

   BUG_045's answer to the abuse case is structural — the reach is two blocks, so
   a file-wide exemption is a diff full of markers. That holds, and it only helps
   a reader who is looking at the diff. A reader looking at the *verdict* still
   sees nothing.

   Sketch: collect exempted findings alongside the reported ones rather than
   skipping the line before matching, and have the scan and the verdict end with a
   count — "N match(es) suppressed by M marker(s)" — with the detail available
   behind the existing ``--reveal``/``--verbose`` flags. Redaction rules apply
   unchanged: a suppressed match is still a match and must not be quoted by
   default. The cost to watch is the return type: ``scan_text`` returns a plain list
   today and several callers unpack it, so the count wants its own accessor rather
   than a widened tuple.

.. idea:: A stand-down switch — one temporary, reversible way to hold every gate
   :id: IDEA_003
   :status: done
   :area: core/rules
   :severity: medium
   :priority: high
   :slug: stand-down-switch

   **The want.** One switch that holds every gate, reversibly, without editing
   project policy. Today the smallest expression of "stand down while I do
   something unusual" is either a manifest edit the operator has to remember to
   revert, or ``INVARIANT_DISABLE`` with all twenty-four rule ids spelled out by
   hand.

   **Why the existing routes do not cover it.**

   - The ``loose`` profile is not off. It still blocks
     ``session.no_edit_in_main_checkout`` and ``privacy.clean``, deliberately and
     correctly — it is an observation mode, not a stand-down.
   - ``INVARIANT_DISABLE`` takes a comma-separated list of exact ids. There is no
     wildcard, no ``all``, and a mistyped id is silently ignored
     (:need:`BUG_050 <BUG_050>`).
   - An environment variable does not reach a hook mid-session anyway: the hook
     subprocess inherits what the host was launched with.
   - Editing ``enforcement.rules`` works and is permanent by construction. A
     temporary policy change that lives in the project's committed configuration
     is the wrong shape — it either gets committed by accident or gets forgotten
     and quietly becomes the project's real policy.

   **Shape.** ``invariant standdown --reason '<why>' [--until <event>]``, recorded on
   the session record where ``overrides`` already lives, so it is visible in
   ``session status`` and in the compliance report rather than being a silent hole.
   Every rule reports ``na("stood down — <reason>")`` while it holds. It ends at the
   end of the pipeline or the session, whichever the operator names, or at
   ``invariant standdown --clear``; the default should be the SHORTEST scope that is
   useful, because a stand-down nobody remembers to lift is indistinguishable from
   having uninstalled the tool.

   ``privacy.clean`` deserves an explicit decision rather than a default: it is the
   one gate whose failure cannot be undone after the fact, and this library has
   already argued that case twice (both the ``loose`` profile and the README's
   opinions section keep it blocking). Either hold it out of the stand-down, or
   require it to be named — ``--include privacy.clean`` — so that going past it is
   a separate sentence the operator wrote.

   **Why this belongs to the founding position, not to convenience.** A tool that
   cannot be told to stand down for one task gets uninstalled during that task, and
   what returns afterwards is usually nothing. The README already argues the
   adjacent half: *"a gate that fights legitimate exceptions gets the whole tool
   switched off"*. A stand-down is the supported version of the thing an operator
   would otherwise improvise, and improvised it leaves no record, while this
   leaves one.

   **Effort.** Small-to-moderate. The delivery seam exists (``Level.OFF`` is already
   honoured in ``core/rules.evaluate`` before the rule body runs) and the storage
   seam exists (``Session.overrides``). The work is the scope/expiry semantics and
   making the state loud in ``build_context``, so that no turn passes without the
   agent and the operator being told enforcement is currently held.

   **Related.** :need:`BUG_049 <BUG_049>` is the per-call case; this is the
   per-interval one. They share a seam and could be built together, but they are
   not the same want: one is "let this call through", the other is "stop asking
   for the next while".

   **Provenance.** Raised by a review of the implementation against the project's
   founding position — the operator overrides temporarily or permanently, and only
   the permanent half is currently expressible. Reported 2026-08-20.

   .. invariant-history:
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

.. idea:: invariant uninstall — a first-class way to remove what init installed
   :id: IDEA_004
   :status: done
   :area: hosts/claude_code
   :severity: low
   :priority: medium
   :slug: uninstall-command

   **The want.** ``invariant uninstall`` — remove the hook shims, unwire the entries
   this library added to the host settings, and say what is being left behind.

   **Why.** Adoption is one command. Withdrawal is hand surgery: delete each
   ``.claude/hooks/invariant-*.sh``, then hand-edit the JSON that ``_wire_settings``
   wrote — merged into the operator's own settings file, so it cannot simply be
   deleted — and know which entries were the library's. Miss one and the shim
   still fires; the shim then finds no ``invariant`` on PATH and exits silently by
   design, so the leftover is invisible until it is not. ``invariant doctor`` will
   report a half-removed install as broken and offer ``hooks install``, pushing back
   toward the state the operator was trying to leave.

   The right to stop using a tool is part of the operator's authority over their
   own project, and it is the part this library currently makes hardest. A tool
   that is easy to adopt and awkward to remove is asking for a trust it has not
   earned yet.

   **Shape.**

   ::

      invariant uninstall [--keep-manifest] [--keep-assets] [--dry-run]
        removes N hook shim(s) from .claude/hooks/
        removes N hook entr(ies) from .claude/settings.json  (yours are untouched)
        leaves  .claude/invariant.yaml           (--keep-manifest is the default)
        leaves  the state directory, with its N session records
        leaves  .claude/agents/*, docs/backlog/*, .claude/memory/*

   Two properties matter more than the command existing:

   - **It removes only what it wrote.** ``_wire_settings`` recognises its own entries
     by the shim path already; removal is the same match, and anything else in the
     settings file is left exactly as found. The same care ``_wire_settings`` takes
     with malformed JSON — refuse rather than overwrite — applies here.
   - **It never deletes the operator's data.** Sessions, work items, memories,
     architecture docs and the manifest survive by default, so uninstalling is not
     a decision about the backlog. ``--dry-run`` enumerates before anything moves.

   **Effort.** Small. The install side is one method and one settings writer; the
   inverse is symmetric and the identity test already exists.

   **Related.** :need:`IDEA_003 <IDEA_003>` is the reversible version of this and
   should be the recommended step before it — a stand-down that works is what makes
   uninstalling rare. :need:`BUG_049 <BUG_049>` is the same authority question at the
   level of a single call.

   **Provenance.** Raised by a review of the implementation against the project's
   founding position: the operator holds the authority, which includes the
   authority to leave. Reported 2026-08-20.

   .. invariant-history:
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

.. idea:: StatsDomain.resolve does not know that a session name carries its id
   :id: IDEA_005
   :status: done
   :area: domains/stats
   :severity: low
   :priority: medium
   :residue: Shipped: the identifier rung, the three name rungs, and a refusal that lists candidates rather than taking the first hit. NOT shipped, and filed as IDEA_007: joining the earlier hosts on Session.hosts. That join is what makes several records share one name, which this resolver refuses with no selector able to separate them — so IDEA_007 must ship a per-host selector with it.

   **The want.** ``invariant stats show <bare-task-name>`` and ``invariant stats archive <bare-task-name>`` again, now that a session name is an address.

   **Why.** ``SessionDomain.start`` appends the session id to the task slug, so a
   record's ``session_name`` is ``add-widget-abcd1234`` rather than ``add-widget``.
   ``StatsDomain.resolve`` matches the selector against ``host_session_id``,
   ``session_id`` and ``session_name`` by **equality**, so the bare task name — the
   one thing a person actually remembers — no longer selects anything, and the
   refusal it produces names three spellings of which the reader has none to hand.

   **The shape of the fix.** ``domains/session`` already exports the predicate:
   ``name_matches(session_name, session_id, query)`` answers the name half of
   ``resolve_name``'s ladder (full name, bare slug, name prefix) without needing a
   live set to scan — which is the case here, because a telemetry record outlives
   the session that produced it. ``domains/stats -> domains/session`` is an arrow
   ``docs/architecture/overview.md`` already draws. Note that ``name_matches``
   flattens the ladder's precedence, so a selector matching several records still
   has to be disambiguated by the caller rather than resolved to the first hit.

   **Owed by.** Three tests are wrapped in a strict ``xfail`` naming this item:
   ``tests/test_stats_domain.py::TestArchiving::test_a_record_can_be_archived_by_its_invariant_session_name``,
   ``tests/test_cli_stats.py::TestShow::test_one_session_by_name``, and
   ``tests/test_cli_stats.py::TestArchive::test_archiving_promotes_a_record``.
   Delete the markers with the fix; the assertions must pass unchanged.

   .. invariant-history:
      - from: proposed
        to: accepted
        reason: increment 5 of the session-outlives-host series takes it
        forced: false
      - from: accepted
        to: in_progress
        reason: null
        forced: false
      - from: in_progress
        to: done
        reason: the three strict-xfail assertions pass unmarked and unmodified; resolve climbs resolve_name's ladder through name_rank
        forced: false

.. idea:: A fix line annotated in parentheses cannot be pasted into a shell
   :id: IDEA_006
   :status: done
   :area: core/rules, domains/stats
   :severity: low
   :priority: low
   :residue: The scan that holds this closed now covers domains/stats as well as core/rules and domains/session. cli/main.py is still unscanned, and its refusals use the aligned two-column form (invariant session list   shows what is registered here), which this rule would reject. Widening the scan there is a separate judgement about that form, not a leftover of this entry.

   **The want.** Every command a refusal names should survive being pasted into a
   shell exactly as printed.

   **What is there now.** Two annotation forms are in use for the prose that
   follows a command in a ``fixes`` line. The ``#`` form is a shell comment, so the
   whole line runs:

   ::

      invariant session plan done <n>     # it is finished

   The parenthetical form is not, and the shell reports a syntax error on it:

   ::

      invariant session teardown  (the normal end of a session)
      invariant pipeline test --phase {phase}  (records the failing run)

   A third shape is a command named in prose without its required argument —
   ``invariant stats archive``, which the parser refuses for a missing ``session``.

   **Found by.** ``tests/test_cli.py::TestEveryCommandARuleNamesCanBeRun``, which
   puts every command string in ``core/rules`` and ``domains/session`` through the
   real argument parser. It strips BOTH annotation forms before parsing, which is
   what lets it pass over these three — so the test that would catch a future one
   is already normalising the existing ones away.

   **Why it is an idea and not a bug.** Nobody is stopped: the command up to the
   annotation is correct, and a reader who deletes the parenthesis gets what they
   wanted. The cost is one paste that fails at the moment somebody is already
   blocked, which is exactly the audience a ``fixes`` line is written for.

   **The change.** Convert the two parenthetical annotations to ``#``, give
   ``stats archive`` its ``<session>`` placeholder, and drop the parenthesis-stripping
   from the test's normaliser so the form cannot come back. Out of scope for the
   increment that found it: that one is ``cli/main.py`` only, and these live in
   ``core/rules`` and ``domains/stats``.

   .. invariant-history:
      - from: proposed
        to: accepted
        reason: 'combined into IDEA_005''s increment: same refusal-message surface, three one-line edits'
        forced: false
      - from: accepted
        to: in_progress
        reason: null
        forced: false
      - from: in_progress
        to: done
        reason: two parenthetical annotations converted, stats archive given its <session>, and the test normaliser no longer strips parentheses
        forced: false

.. idea:: The stats join reads only the current host binding, so a resumed session's earlier records claim nothing is known
   :id: IDEA_007
   :status: proposed
   :area: domains/stats
   :severity: low
   :priority: medium
   :residue: DECISION 2026-10-20 (user-approved): The identity-only join — join earlier hosts for session_id/session_name only, states = None with a new DECLINED_* code naming the real cause, shipped with a per-host selector for StatsDomain.resolve. No per-host arithmetic split.

   **The want.** A stats record from a host session that drove a session EARLIER
   should be joined to that session, and its per-state breakdown should be that
   host's share rather than the session's whole spend.

   **Why it is not just a mapping.** ``StatsDomain._bindings`` builds
   ``{session.host_session_id: session}`` from the scalar CURRENT binding, so after
   a resume every earlier host's record joins to nothing and renders
   ``DECLINED_UNJOINED`` — which BUG_024 fixed into meaning "nothing is known about
   this host session". Here something IS known: ``Session.hosts`` records every
   earlier host, oldest first. A stated wrong cause is the failure BUG_024 warns
   is harder to catch than an absence, so the reason code is wrong today.

   **The naive fix is worse than the bug, and must not be applied.** Mapping every
   id in ``hosts`` to the session makes ``_join`` call ``_breakdown(session.id)`` for
   each of N records, and ``_breakdown`` sums across ALL of that session's
   pipelines. Each of the N records would then carry the session's full per-state
   spend, so the reported total multiplies by N. The record TOTALS are safe —
   ``record.stats`` is folded from that host's own transcript — the damage is
   confined to ``states``.

   **What a real fix needs, and does not have.** A per-host filter keyed on the
   ``usage_host`` recorded on every history row and pipeline entry is the obvious
   shape, and the data is not sufficient for it as recorded:

   1. **``elapsed`` has no host.** A row records seconds spent in the state just
      left; nothing says which host session was driving. Splitting durations by
      ``usage_host`` would attribute a duration by a key that describes a different
      field.
   2. **No per-host baseline.** ``breakdown`` subtracts consecutive watermarks with
      the PIPELINE's ``usage_at_start`` as the first earlier reading. A per-host
      slice's first row has no earlier reading of its own counter, so every
      resumed host loses its first state's spend, or the fix invents a baseline
      nothing records.
   3. **The boundary row belongs to the wrong host.** ``session resume`` run from a
      shell exporting no host id cannot see the new host — the rebind happens
      afterwards, in the host adapter's PostToolUse — so the ``resumed`` row's
      ``usage_host`` is the OLD host and its ``host`` extra is null. Filtering on
      ``usage_host`` assigns that row's interval to the previous host. Nothing is
      mis-measured today, because the following transition is declined by
      ``_incommensurable``'s host check exactly as a rebind should be; what is
      missing is an owner for the reading, which is precisely what this fix
      needs.
   4. **A second axis on ``states_partial``.** Today it means "some pipelines
      predate per-state attribution". A per-host split adds "some rows belong to
      no host", and one flag cannot carry both.

   **A third shape, cheaper than either, worth costing first.** Join the earlier
   hosts for IDENTITY only — ``session_id`` and ``session_name``, so the record stops
   claiming nothing is known — and set ``states = None`` with a new ``DECLINED_*``
   code naming the real cause: the per-state attribution is recorded against the
   session, and this record is one of several host sessions that drove it. That
   fixes the wrong cause without inventing arithmetic. It is not free: it makes
   several records share one name, which ``StatsDomain.resolve`` refuses as
   ambiguous with no selector that separates them (see the refusal it already
   prints), so it needs a per-host selector shipped with it, and the report's
   session table would show N rows under one name.

   **Deferred from IDEA_005 deliberately.** That increment taught ``resolve`` the
   name ladder. This is the telemetry arithmetic, which is a different seam, and
   folding it in would have smuggled a redesign of what a per-state figure MEANS
   into a change about how a record is addressed.

.. idea:: A handoff note is offered to every unbound greeting until it goes stale, even after a session acted on it
   :id: IDEA_008
   :status: done
   :area: domains/session, hosts/claude_code, cli/main
   :priority: medium
   :severity: medium
   :effort: S

   **The want.** A handoff note stops being offered once a session has acted on
   it. Today ``invariant session handoff`` records the note on ``Session.handoffs``,
   and while it is younger than ``session.plan_stale_days`` ``latest_handoff``
   surfaces it in EVERY unbound SessionStart greeting and every unbound
   ``invariant context`` in the project — including the greeting of the very host
   session that was launched to pick it up and has already started or resumed
   the work. ADR-005 (Consequences) names this as a deliberate follow-up; D5 made
   freshness the only expiry so that nothing had to run for an old note to stop
   showing, and that is the half this idea adds a second exit to.

   **Why it matters.** A note that keeps being offered after it was acted on is
   not neutral: an unrelated session opened in the main checkout within the stale
   window is told what to do next by a note whose work is already in flight in
   another session, and may start it a second time. The handoff is the documented
   last step after merge and teardown, so the window in which a stale-but-fresh
   note is wrong is most of its life.

   **Acceptance:**

   - After a handoff, the launched host session's first ``session start`` or
     ``session resume`` in this project marks the handoff its launcher token names
     consumed; the next unbound greeting and ``invariant context`` no longer show
     it.
   - A consumed handoff stays on ``Session.handoffs`` with when it was consumed and
     by which session/host — the record is history, not a queue to pop.
   - A handoff recorded AFTER the consuming start is still offered (consumption is
     per note, not "any start silences all handoffs").
   - An unconsumed note still expires by ``session.plan_stale_days`` exactly as
     today.
   - ``invariant session handoff --dismiss`` dismisses the latest fresh note
     without starting a session (for a handoff the user decided not to act on);
     it is recorded as dismissed, not claimed.

   **Decisions (user, 2026-10-20):**

   1. **Claim signal: a launcher env token.** The handoff launcher prefixes
      ``claude`` in the ``cd <main> && …`` line it types with an environment token,
      e.g. ``INVARIANT_HANDOFF=<session-id>:<at>``. Hook subprocesses inherit the
      environment the host was started with, so the first ``session start`` /
      ``session resume`` in that host reads the token and consumes EXACTLY that
      note, on the PostToolUse binding path. This keeps the write out of
      SessionStart (which state_model.md says must not write), does not depend
      on the host exposing its session name, and is safe with parallel sessions.
      A note launched by hand (no token) is cleared only by dismiss or by
      staleness. There is deliberately NO "any start/resume after ``at``"
      fallback.
   2. **Which note is consumed:** the one the token names — never "every fresh
      note".
   3. **Dismiss:** ``invariant session handoff --dismiss`` marks the latest fresh
      note consumed and records it as dismissed. Same command, not a new
      subcommand.
   4. **Storage:** the consumed marker lives on the ``handoffs`` entry — timestamp,
      consuming session/host, and how (``claimed`` or ``dismissed``). An entry
      without the key reads as unconsumed, preserving the BUG_061 no-migration /
      round-trip contract. The write to the old (possibly torn-down) session's
      record is a standstill-style event, not an edge, like the handoff itself.

   **Risks / open questions:**

   - None open — the four questions (claim signal, which note, storage contract,
     where the write happens) are resolved by the decisions above.

   **Affects:** the session domain's ``latest_handoff`` reader and the
   ``session start`` / ``session resume`` binding path; the SessionStart greeting and
   ``invariant context`` (docs/architecture/cli.md); the ``handoffs`` field and
   "Handing off: an event, not an edge" in docs/architecture/state_model.md;
   ADR-005 D5.

   **Why now / priority:** follow-up named in ADR-005's Consequences from the
   session that shipped ``session handoff``; medium value (every unbound greeting in
   the stale window, with a real risk of duplicated work), small-to-medium effort
   (one field, one write on the binding path, one filter on the reader, plus the
   claim-signal decision).

   .. invariant-history:
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

.. idea:: session handoff ends the old Claude Code session once the successor has launched
   :id: IDEA_009
   :status: done
   :area: hosts/claude_code_launch,cli/main
   :priority: high
   :effort: S

   **The want.** After ``invariant session handoff`` has successfully launched the
   successor host session, the old Claude Code session ends itself. Once the
   successor is running the old session is not needed any more, but today it
   stays open indefinitely: on 2026-10-20 two such sessions had been sitting idle
   for about 10h after their handoffs. The launch path is
   :doc:`claude_code_launch </architecture/overview>` (ADR-005, D3 and D6), driven
   by the CLI's ``session handoff`` command (:doc:`cli.md </architecture/cli>`).

   **Why it matters.** Every handoff leaves one orphaned host session behind. It
   holds a terminal window, a host process and its bindings, and it invites the
   user to type into a session whose work has moved on, which can lead to the
   same work being started twice.

   **Acceptance:**

   - After a successful launch, the handoff output is printed in full, and a few
     seconds later the launching Claude Code process receives SIGTERM and exits.
     The terminal window stays open at a shell prompt.
   - ``--dry-run``, a refused handoff, and a failed launch never end the old
     session, so the user is never left with no session.
   - ``invariant session handoff --keep-open`` launches the successor and leaves
     the old session running.
   - If ``CLAUDE_PID`` is unset, or does not name a claude process, the handoff
     still succeeds, says that it is leaving the session open and why, and sends
     no signal.
   - No long-lived background process is left behind: the delay is a short,
     bounded, detached sleep-then-kill (or an equivalent bounded mechanism) that
     is gone within seconds of the handoff.
   - Ending the session works in any terminal and over Remote Control, because it
     signals the process rather than closing the window.

   **Decisions (user, 2026-10-20):**

   1. **Only after a successful launch.** Never on ``--dry-run``, a refusal, or a
      failed launch.
   2. **Mechanism:** after a short delay, so that the handoff output reaches the
      user first, send SIGTERM to the launching Claude Code process. Claude Code
      exposes ``CLAUDE_PID`` to its tool processes. If it is absent or not a
      claude process, say so and leave the session open.
   3. **Opt-out:** ``--keep-open``.
   4. **Bounded delay:** no long-lived background process. Use a short detached
      sleep-then-kill or an equivalent.

   **Risks / open questions:**

   - Identifying "a claude process" from a PID has to be portable (macOS and
     Linux) and must not signal an unrelated process after PID reuse. The check
     runs immediately before the signal is sent.
   - SIGTERM to the host may race the PostToolUse hook that records the handoff
     command. The delay has to cover the hook's write, or the record has to land
     before the kill is scheduled.

   **Sequencing:** after BUG_068, which touches the same module
   (``claude_code_launch``, the environment the successor is launched with).

   **Why now:** every handoff leaks a session today, and it is a small change
   (S) inside one module plus one CLI flag.

   .. invariant-history:
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
        reason: done in 317e1e8
        forced: false

.. idea:: A dispatched agent can ask, before doing its work, whether its session can write the work-item store
   :id: IDEA_010
   :status: proposed
   :priority: medium
   :effort: M
   :area: cli/main, data/agents

   **The want.** An agent whose job ends in a write to the work-item store
   (``backlog-curator``, ``bug-reproducer``) can ask, as its FIRST step, whether its
   session is able to make that write, and bail in seconds if not. Today a refusal
   surfaces only at the first write, after the agent has read the backlog,
   interviewed, scored and drafted, so each occurrence costs a whole agent run
   (BUG_032). The shape is a cheap pre-flight on the CLI
   (:doc:`cli.md </architecture/cli>`), e.g. ``invariant context --can-write items``
   or a ``doctor`` row, which the agent definitions under ``agents/``
   (:doc:`overview </architecture/overview>`) call before Phase 1.

   **Why it is not just invariant's own rules.** BUG_032's 2600-call matrix showed
   invariant itself allows these writes, so the refusal reported there most likely
   came from the HOST's own permission layer. A pre-flight that consults only
   invariant's rules would answer "yes" in exactly the case that motivated it. The
   answer must cover both layers, or say plainly which one it could not see.

   **Acceptance:**

   - An agent that cannot write the work-item store learns so from one command
     that returns in seconds, before it reads the backlog or asks the user
     anything.
   - The answer distinguishes "invariant refuses (rule named)", "host refuses",
     "both allow" and "invariant allows; host unknown" — it never reports "allowed"
     for a layer it did not actually probe.
   - The probe leaves no trace: no item file, no history row, no session state
     change, no residue in the store after it runs (the BUG_059 lesson).
   - ``backlog-curator`` and ``bug-reproducer`` run the pre-flight first and, on a
     refusal, bail with a report naming the refusing layer instead of starting
     their interview or repro.
   - In a session where the write is allowed, the pre-flight adds no visible
     noise to the agent's normal run.

   **Design question for the builder.** How to probe the host layer honestly
   without performing a real write. Candidates: an inert probe write inside the
   store (created and removed, reusing the ``InertStore`` mechanism from BUG_059
   where it fits); a dry-run hook payload; or, when neither can tell, reporting
   "invariant allows; host unknown" rather than guessing.

   **Risks / open questions:**

   - A probe write the host permits may itself prompt the user for permission —
     which is a refusal-in-waiting the pre-flight should surface, but must not
     turn into an extra prompt on every healthy run.
   - The host's permission decision can differ by tool (Edit vs Bash-driven CLI
     write); the probe must exercise the same path the agent's real write uses,
     or it answers a different question.
   - "Host unknown" must not decay into a silent pass; agents need a defined
     behaviour for it (proceed with a warning, most likely).

   **Related:** BUG_032 (origin — its entry asks for exactly this pre-flight);
   BUG_059 (``InertStore``, a reusable no-trace probe mechanism).

   **Why now:** each occurrence loses a full agent run, and it retires the
   folklore "dispatch filing agents from a non-isolated session".

.. idea:: A file can declare its coverage ceiling, distinct from a ratcheted shortfall
   :id: IDEA_011
   :status: proposed
   :priority: medium
   :effort: M
   :area: core/rules, core/manifest

   **The want.** A project can declare, per path and in a reviewable diff, that a
   file's coverage CEILING sits below the project floor, for a reason that is a
   property of the ecosystem rather than of any change: e.g. SwiftUI view bodies
   and ``Button`` action closures, which no test runner in that ecosystem executes.
   The shape is an explicit key in ``methodology.gates`` (see
   :doc:`manifest.md </architecture/manifest>`), beside ``non_source``,
   ``coverage_exclude`` and ``coverage_baseline``, and a ``coverage.meets_floor``
   verdict that names a held file as "held to its declared ceiling", distinct
   from a ratcheted shortfall.

   **Why not ``coverage_baseline``.** The 2026-10-21 repro of BUG_054 showed
   ``coverage_baseline`` can already hold such a file: at a global floor of 90, a
   file pinned to 14 passes at 14.83%, blocks at 10%, and its neighbours still
   bind at 90. So this is about meaning and reviewability, not capability.
   ``coverage_baseline`` is documented and worded as "pre-existing shortfall,
   ratcheted", i.e. debt to pay down. The consuming project's baseline-file header
   forbids seeding it with a permanent ceiling, and the verdict cannot tell a
   measured ceiling from an ordinary gap. A ceiling is a standing claim about
   the file, not debt, and a reviewer should see it as one.

   **Evidence.** BUG_054's symptom table: four view files in the consuming project
   at 0-15% under a floor of 90, so a copy-only change that authored only 100%
   covered lines could not land. The same project's ratchet record shows a
   complete extraction refactor of a sibling view topping out near 89.

   **Acceptance:**

   - A project can name one exact path and a ceiling number for it. That file
     passes at or above its ceiling and blocks below it, while every other
     touched file is still held to the global floor.
   - The verdict for a file held by a ceiling says so, in wording distinct from a
     ``coverage_baseline`` hold. A reader can tell "declared ceiling" from
     "ratcheted shortfall" without opening the manifest.
   - A ceiling never widens to a directory: a glob or a directory path is refused
     at manifest load with a message naming the key.
   - A ceiling entry with no stated reason is refused (if the builder settles on
     a required reason; see below).
   - A file with no ceiling entry behaves exactly as today. Neither
     ``coverage_baseline`` nor ``coverage_exclude`` changes meaning.

   **Design questions for the builder:**

   - Does a ceiling ratchet, so that it may only go up once the file measures
     higher, the way ``coverage_baseline`` tightens?
   - How does it order with ``non_source``, ``coverage_exclude`` and
     ``coverage_baseline`` in ``core/rules.py::_coverage_meets_floor``? BUG_054's fix
     sketch says ordering is the whole of the safety: it is asked LAST, and only of
     what the floor already rejected.
   - Must each entry carry a reason (the ecosystem property that caps it)?
   - Should a path that is in both ``coverage_baseline`` and the ceiling key be
     refused as a double statement?
   - Path spelling goes through BUG_071's ``project_relative`` normaliser, so ``x``
     and ``./x`` are one entry.

   **Related:** BUG_054 (origin; closed not_reproducible 2026-10-21, the engine
   already holds a per-path number); BUG_026 (the coverage levers); BUG_071 (path
   normaliser).

   **Why now / why this tier:**

   Medium value: it unblocks view-heavy consuming projects honestly, without
   abusing a debt ledger. M effort: one manifest key, one rule branch whose
   ordering must be proven, and verdict wording.

.. idea:: Session Oracle v1 — a machine-wide monitor of every running session
   :id: IDEA_012
   :status: proposed
   :priority: high
   :effort: L
   :tier: 2
   :area: oracle (new), hosts/claude_code, cli/main

   **The want.** One place, for the whole machine, that knows every running
   session in every invariant project and tells you when one needs you. Today
   several sessions run at once across projects, often driven remotely, and
   nothing says that one is waiting on a permission prompt, blocked by a gate,
   stuck, finished, or failing. That state is split across per-project stores
   (see :doc:`state_model.md </architecture/state_model>`), and no hook sees a
   permission prompt. The shape is a machine-wide, read-only **Session Oracle**:
   an always-on background watcher (launchd), a user-level project index, and a
   live terminal dashboard. It lists every session, shows its state, recommends
   the exact next command for each, and raises a macOS notification when a
   session needs you.

   **Settled in the interview (2026-10-22):**

   - **Scope.** Every invariant project on the machine. A project registers
     itself on ``init`` and on its first session start, and can be removed.
   - **Rows.** Invariant sessions with their Claude Code host sessions nested
     under them, plus host sessions that have no invariant session. Live, parked
     and stranded sessions are all shown.
   - **"Oracle"** means it recommends the exact next command (argv and cwd) for
     each session, not just its state.
   - **Alerts in v1:** waiting on you (a permission prompt or a question); a gate
     is blocked; stuck (no activity for 20 minutes while a turn is running);
     finished and awaiting merge; an error or a failed test run.
   - **Shown but no alert:** stranded sessions, leaked worktrees and processes,
     parked sessions, and an idle "your turn".
   - **Powers.** Every action needs confirmation first: one key, then a confirm
     modal that shows the exact command. It never answers or approves a
     permission prompt for you.
   - **Primary surface.** The live dashboard: a Textual TUI that ships as an
     optional extra (``invariant[oracle]``), with macOS notifications as the v1
     alert channel.

   **Acceptance:**

   - With 3 projects and 5 sessions open, the dashboard shows all of them within
     5 seconds.
   - A session waiting on a permission prompt turns red and you get a macOS
     notification within 30 seconds. Answering the prompt clears the row and
     re-arms the alert.
   - A project added with ``invariant init``, or by starting its first session,
     shows up with no manual step. ``oracle projects remove`` takes it out.
   - The same rising edge never alerts twice. ``invariant oracle list --json``
     matches the dashboard.
   - No scan writes to any project's state root. One broken project shows as an
     error row and does not hide the others.
   - An action runs only after you confirm the exact command it will run.

   **Architecture sketch** (from the approved plan, 2026-10-22; summarised):

   - A new ``oracle/`` package that nothing in ``core/`` or ``domains/`` imports.
     Modules: home (user-level state dir, ``$INVARIANT_HOME`` override); a project
     index keyed by state root; a per-project attention sidecar under each state
     root (one writer, atomic replace); a read-only scan over the inert store;
     pure ``classify`` and ``recommend`` functions with an injected clock; a gate
     cache that evaluates only on a fingerprint change while the session is
     idle; a 5-second watcher tick with a snapshot and a rising-edge de-dup
     ledger; a notifier seam (osascript); a launchd installer seam; and the TUI.
   - Hook changes in the Claude Code host adapter (see
     :doc:`overview.md </architecture/overview>`, ``hosts/``): a seventh hook,
     ``Notification``, plus sidecar writes on PreToolUse(AskUserQuestion),
     PostToolUse, UserPromptSubmit, Stop and SessionEnd. These writes are
     guarded and can never change a hook verdict.
   - CLI (see :doc:`cli.md </architecture/cli>`): ``invariant oracle list | watch | tui | projects list|add|remove|prune | install | uninstall | notify-test``.
   - Docs: a new ADR ("a machine-wide, read-only oracle": user-level state, the
     attention sidecar, the first stored pid, gate alert = a recorded refusal,
     confirmed subprocess actions, the first daemon, the first optional
     dependency); a new architecture page for the oracle; updates to
     overview, state_model and cli.
   - Build order, one landable TDD increment each: (1) home + project index +
     auto-registration; (2) attention sidecar + Notification hook; (3) scan,
     signals, recommend, ``oracle list``; (4) watcher, de-dup ledger, notifier,
     ``watch --once``, ``notify-test``; (5) gate cache; (6) launchd
     install/uninstall; (7) TUI behind the ``[oracle]`` extra.

   **Related:**

   - BUG_040 is a **known gap**, documented and not fixed here. A host session
     rooted in another repository is mis-attributed, so it shows as an orphan
     host row.
   - IDEA_007 is a sibling. The host-binding history decides how a resumed
     session's earlier hosts nest.
   - IDEA_010 is a sibling. A host-layer write refusal is one more "blocked"
     cause.
   - Follow-ons, each filed as its own idea: IDEA_013 (phone push), IDEA_014
     (status line and prompt-line injection), IDEA_015 (a hosted web
     dashboard), IDEA_016 (a Claude artifact dashboard), IDEA_017 (extended
     oracle actions), IDEA_018 (alerting on an idle "your turn").

   **Risks / open questions:**

   - A host that crashed without SessionEnd could stay "waiting" forever. The
     plan answers this with a pid liveness check and a process-name guard, plus
     a fallback to amber after 6 hours.
   - launchd runs with a minimal environment, and an editable install can move.
     The plist uses an absolute interpreter and an explicit PATH, and ``doctor``
     checks that the interpreter still exists.
   - The ``notification_type`` field may drift between Claude Code versions. The
     plan falls back to matching the message text.
   - Whether the host pid is present in the hook environment is unconfirmed.
     Increment 2 checks it against a real hook first, and falls back to walking
     the parent pid if it is missing.

   **Why now / why this tier:**

   High value: it touches every session, every day, and it is the only thing
   that would catch a session silently waiting on you. L effort: it is the first
   daemon, the first user-level state and the first optional dependency, plus a
   new hook. That makes it T2 (strategic), not T1.

.. idea:: Session Oracle alerts reach your phone as a push notification
   :id: IDEA_013
   :status: proposed
   :priority: medium
   :effort: S
   :tier: 5
   :area: oracle

   **The want.** When the Session Oracle (IDEA_012) raises an alert, you also
   get it on your phone, so that a session waiting on you while you are away
   from the Mac does not sit idle for hours. Sessions are often driven remotely,
   and that is exactly when a macOS notification goes unseen. The shape is a
   second implementation behind v1's notifier seam, fed by the same rising-edge
   de-dup ledger, so v1's alert rules and de-dup carry over unchanged.

   **Acceptance:**

   - An alert that would raise a macOS notification also reaches the phone
     within 30 seconds, and only once per rising edge.
   - Push is opt-in and off by default. ``invariant oracle notify-test`` exercises
     it.
   - The pushed text carries the project, the session and the condition. It
     carries no transcript content, no paths and no prompt text.
   - A failing push provider is reported in ``oracle watch`` output and does not
     delay the other notifiers.

   **Risks / open questions:**

   - Which provider to use (a self-hosted relay, a push service, or the host's
     own mobile notifications). Every option sends data off the machine, so the
     payload must stay minimal and the credential must stay out of the
     manifest.

   **Why now / why this tier:**

   Follow-on to IDEA_012. Medium value: it matters most for remote driving. S
   effort behind an existing seam. T5.

.. idea:: Status line and prompt line show which other sessions need you
   :id: IDEA_014
   :status: proposed
   :priority: medium
   :effort: M
   :tier: 5
   :area: oracle, hosts/claude_code

   **The want.** Inside any one Claude Code session, you can see at a glance
   that ANOTHER session needs you, without switching to the Session Oracle
   dashboard (IDEA_012). The shape has two parts. (1) A status-line command
   that prints a compact count read from the watcher's snapshot, e.g. "2
   waiting, 1 blocked". (2) Prompt-line injection: the existing host hooks add
   a one-line note to the context when other sessions are waiting, the same way
   invariant already injects state (see
   :doc:`overview.md </architecture/overview>`, "Injecting state instead of
   rules").

   **Acceptance:**

   - The status line shows the count of alerting sessions across all projects,
     with no more than one watcher tick of lag, and shows nothing when none are
     alerting.
   - The status line never scans the projects itself. It reads only the
     snapshot, and degrades to a blank or "watcher off" display when no
     snapshot exists.
   - The injected note names which sessions need you and the recommended
     command for each. It is never injected into the session it describes.
   - Both parts are opt-in.

   **Risks / open questions:**

   - Injection costs context tokens on every turn. It may need rate limiting, or
     a rule that it fires only on a change.

   **Why now / why this tier:**

   Follow-on to IDEA_012. Medium value. S–M effort that reuses the snapshot and
   the existing hooks. T5.

.. idea:: Session Oracle dashboard as a hosted website
   :id: IDEA_015
   :status: proposed
   :priority: medium
   :effort: L
   :tier: 3
   :area: oracle

   **The want.** You can see the Session Oracle (IDEA_012) dashboard from any
   browser, including on a phone, as a hosted website (e.g. on Vercel). Its sibling is IDEA_016. The v1
   dashboard is a local TUI. The shape is an opt-in publisher that pushes a
   redacted snapshot to a hosted endpoint, plus a read-only web page that
   renders the same rows, states and recommended commands.

   **Acceptance:**

   - With publishing on, the hosted page shows the same sessions and states as
     ``invariant oracle list --json``, within one watcher tick plus the publish
     latency.
   - The page is not readable without authentication.
   - The published snapshot holds no paths that embed a username, no transcript
     or prompt text, and no host identifiers beyond opaque session ids. A
     privacy check runs on every publish.
   - Publishing is off by default. Turning it off stops all outbound traffic.
   - The web page is read-only in this idea. Acting from the web is out of scope.

   **Route chosen for now (2026-10-22):** for remote access the user chose the
   local plus Tailscale route, IDEA_023: a page served from the Mac over the
   private tailnet, so session data never leaves their devices, with the TUI's
   confirm-first actions. This idea is not dropped. It stays open as the
   hosted alternative.

   **Risks / open questions:**

   - This is the first time invariant sends state off the machine. It needs its
     own ADR covering the trust boundary, authentication, retention, and what
     is redacted.
   - Hosting choice and cost.

   **Why now / why this tier:**

   Follow-on to IDEA_012. Medium value: remote visibility. L effort because of
   the trust boundary, not the rendering. T3 (scope expansion: a new external
   system).

.. idea:: Session Oracle dashboard as a Claude artifact
   :id: IDEA_016
   :status: proposed
   :priority: medium
   :effort: M
   :tier: 3
   :area: oracle

   **The want.** You can see the Session Oracle (IDEA_012) state as a Claude
   artifact dashboard: a live document in the Claude web app that renders the
   oracle's rows and alerts. You can read it alongside a chat, and from any
   device signed into the account. This is an alternative to the hosted website
   (IDEA_015), not a replacement for it.

   **Acceptance:**

   - An opt-in command publishes or refreshes the artifact from the watcher's
     snapshot. The artifact shows every session's project, state, alert and
     recommended command.
   - The artifact refreshes on a change in the snapshot, not on every tick.
   - The published content follows the same redaction rules as the hosted
     dashboard. No transcript or prompt text, and no paths that embed a
     username.
   - The artifact is read-only. Acting from it is out of scope.

   **Risks / open questions:**

   - What API or tool surface exists for updating an artifact from a background
     process, rather than from inside a chat turn. This may need a spike first.
   - Data leaves the machine. It shares the trust-boundary ADR with the hosted
     dashboard (IDEA_015).

   **Why now / why this tier:**

   Follow-on to IDEA_012. Medium value. M effort, with unknowns in the publish
   surface. T3 (scope expansion: a new external system).

.. idea:: Session Oracle can clean up and unstick sessions, each on confirm
   :id: IDEA_017
   :status: dropped
   :priority: medium
   :effort: M
   :tier: 5
   :area: oracle, cli/main

   **Dropped 2026-10-23:** folded into IDEA_027 (the mobile-first control panel), which builds this action list as its clean-up and unstick controls. The body below stays the spec of record for those actions.

   **The want.** The Session Oracle (IDEA_012) can clean up and unstick
   sessions, not just run their recommended next command. v1's actions run the
   recommended command after a confirm. This idea adds:

   - closing or abandoning a stranded session
   - killing a leaked background process left behind after a hand-back
   - tearing down an orphaned worktree
   - nudging or re-dispatching a stuck agent

   They are offered from the dashboard and from ``invariant oracle`` subcommands.

   **Fixed by the interview (2026-10-22):** every action needs confirmation
   first. It is one key, then a confirm modal that shows the exact command and
   its cwd. The oracle never answers or approves a permission prompt for the
   user, and nothing in this idea changes that.

   **Acceptance:**

   - Each action shows the exact command and what it will destroy, if anything,
     before it runs, and does nothing unless you confirm.
   - A destructive action (a worktree teardown, a process kill) re-checks its
     precondition at confirm time, and refuses if the session became live in
     the meantime.
   - Every action runs through the same CLI path a user would type, so the
     engine's gates still apply. The oracle gets no bypass.
   - There is no path by which the oracle answers or approves a host permission
     prompt.

   **Note 2026-10-22:** IDEA_025 carves out the first action from this list: closing a leftover orphan host on confirm, with a confirm-time check of pid, process name and Claude session id. The rest of this list stays here, and IDEA_025's check is the pattern a process kill here should reuse. IDEA_025 also records three leftover sessions and worktrees in the other registered project for this list's clean-up.

   **Risks / open questions:**

   - A process kill or teardown on a misclassified session destroys work. The
     liveness check (pid plus process-name guard) must be trustworthy before
     any destructive action ships.
   - BUG_040's mis-attributed cross-root hosts must never be offered a
     destructive action.

   **Why now / why this tier:**

   Follow-on to IDEA_012. Medium value. M effort, mostly safety checks. T5.

   .. invariant-history:
      - from: proposed
        to: dropped
        reason: 'Folded into IDEA_027 (2026-10-23): its action list is built there as the panel''s clean-up and unstick controls'
        forced: false

.. idea:: Session Oracle can alert on an idle your-turn session
   :id: IDEA_018
   :status: proposed
   :priority: low
   :effort: XS
   :tier: 6
   :area: oracle

   **The want.** The Session Oracle (IDEA_012) can ALERT when a session has gone
   idle on "your turn": the host finished its turn and is waiting for your next
   prompt. In v1 that condition is shown on the dashboard but never alerts,
   because it fires after every turn and would drown out the real signals. This
   idea makes it a separate, opt-in alert with its own delay.

   **Acceptance:**

   - With the alert enabled, a session idle on "your turn" for longer than a
     configured delay raises one notification. With it disabled (the default),
     the v1 behaviour is unchanged.
   - The alert does not fire for a session already alerting under a stronger
     condition (waiting, blocked, finished, error).
   - The alert re-arms only after a new turn starts.

   **Risks / open questions:**

   - Noise. The right default delay is unknown, and it may need a per-project
     override.

   **Why now / why this tier:**

   Follow-on to IDEA_012. Low value: polish on an existing signal. XS effort, one
   classifier branch and a threshold. T6.

.. idea:: A maintainer-ruled closure edge for an already-fixed bug, carrying its provenance
   :id: IDEA_019
   :status: done
   :priority: high
   :effort: M
   :tier: 2
   :area: data/machines/bug.yaml, core/items

   **The want.** A bug can close as "already fixed" through the engine, with a
   record of who ruled it and on what evidence. Today
   ``data/machines/bug.yaml`` (the bug machine, see
   :doc:`state_model.md </architecture/state_model>`, "Domain: work item") allows only ``filed -> reproduced | not_reproducible | wont_fix``, and ``fixed`` is reachable only from
   ``reproduced``. Sometimes ``bug-reproducer`` returns ALREADY-FIXED: the test
   passes on main, goes red with the fix reverted, and the fixing commit is
   named. The only engine route from ``filed`` is then ``not_reproducible``, which
   records the wrong meaning ("we tried and it did not fire", not "it was real
   and is fixed"). A consuming project hand-edited ``:status:`` instead, which
   left no record of the ruling at all.

   **Proposed shape.**

   - An explicit edge that carries provenance. Either ``invariant item transition <id> fixed --ruling "<who/date>" --evidence <commit>``, legal from ``filed``
     only when both are given, or a dedicated ``already_fixed`` edge or state. The
     ruling and evidence go into the history row.
   - Optional retirement in the same transition: when the backend declares a
     closed store, a closing transition moves the entry there verbatim, in one
     write.
   - A stated guarantee, enforced by tests on every backend, that a transition
     never drops or rewrites body text.

   **Acceptance:**

   - A ``filed`` bug with a named fixing commit and a ruling can reach a closed
     "fixed" meaning in one engine command, and its history shows the ruling, the
     evidence commit and that it skipped ``reproduced``.
   - The same command without a ruling or without evidence is refused, and the
     message names what is missing. ``filed -> fixed`` stays illegal otherwise.
   - ``not_reproducible`` is never the only route for an already-fixed verdict.
     The item list and ``item show`` tell the two apart.
   - On a backend with a declared closed store, a closing transition with
     retirement leaves the entry in exactly one store, its body byte-identical.
   - A transition round-trip test on each backend proves body text survives
     unchanged.

   **Architecture sketch:**

   - ``data/machines/bug.yaml``: the new edge or state, and its entry gate
     (ruling and evidence present).
   - ``core/items``: history-row fields for ruling and evidence; a backend-declared
     closed store and an atomic move. This is the same declaration BUG_074 (c)
     needs, so agree the key once.
   - ``domains/workitem`` and ``cli`` ``item transition`` (see
     :doc:`cli.md </architecture/cli>`, "Work items"): the flags.

   **Risks / open questions:**

   - A red test with the fix reverted arguably IS a reproduction. An alternative
     is to record ``reproduced`` (evidence: revert-red) and then ``fixed``
     (evidence: the commit) in one ruled command. That needs no new edge, but it
     writes a reproduction no-one ran on the shipped tree. The builder picks one
     and says why.
   - Does the ruling need a human, or is a ``bug-reproducer`` verdict enough?
     Today only ``bug-reproducer`` may record ``reproduced``.
   - Is retirement a backend capability or a manifest key? It must not make the
     markdown backend (one file per item, no archive) grow an archive it does
     not want.
   - Body-text survival was probed and held. The only rewriting seen was
     BUG_065's (a wrapped option reflowed, a blank line lost), now fixed.

   **Related:** :need:`BUG_065 <BUG_065>` (prerequisite: the transition must not
   rewrite the entry before it can be trusted to move one);
   :need:`BUG_073 <BUG_073>` (sphinx_needs drops history, where the ruling would
   live); :need:`BUG_074 <BUG_074>` (the closed-store declaration);
   :need:`BUG_066 <BUG_066>` (an earlier lifecycle gap for repro-only sessions).

   **Why now / why this tier:**

   The reporting project's curator is hand-editing status today, which defeats
   the lifecycle's audit trail. M effort: one machine edge, two history fields,
   and a backend capability. Tier 2: medium value with a strong unlock (an
   honest record for every ALREADY-FIXED verdict).

   .. invariant-history:
      - from: proposed
        to: accepted
        reason: null
        forced: false
      - from: accepted
        to: in_progress
        reason: work started in session c12456c7
        forced: false
      - from: in_progress
        to: done
        reason: shipped in cf9f356
        forced: false

.. idea:: Warn before a gated-role agent is dispatched in a session with no pipeline running
   :id: IDEA_020
   :status: done
   :priority: medium
   :effort: S
   :tier: 5
   :area: hosts/claude_code, core/rules

   **The want.** An agent in a gated role (``agents.roles`` design or review) is
   sometimes dispatched in a session with no pipeline running. Today the only
   notice arrives at PostToolUse ("was NOT recorded as evidence: session has no
   pipeline running"), after the agent has already done its work. A pipeline
   started later then finds its ``agents.*_ran`` gate unmet, and the whole dispatch
   has to be repeated. The want is a warning at PreToolUse, before the dispatch,
   so the operator can start the pipeline first or dispatch knowingly. See
   :doc:`state_model.md </architecture/state_model>`, "Agent-role gates".

   **Decided (2026-10-20):** a warning only, never a block. Agent runs recorded
   against the session are NOT carried over to a later pipeline: evidence is
   per-pipeline on purpose, and this idea does not reopen that.

   **Acceptance:**

   - Dispatching an agent whose role is design or review, in a session with no
     running pipeline, prints a warning before the agent runs. The warning names
     the agent, its role, and that its run will not count as evidence for a
     later pipeline.
   - The dispatch still proceeds. The warning does not block, ask, or need an
     override.
   - An agent in a non-gated role (e.g. the backlog curator) gets no warning.
   - With a pipeline running, a gated-role dispatch gets no warning. The
     existing PostToolUse recording is unchanged.
   - The PostToolUse "NOT recorded as evidence" notice still fires as today.

   **Risks / open questions:**

   - The role lookup at PreToolUse must read the same ``agents.roles`` mapping the
     gates use, or the warning and the gate can disagree.
   - Under a stand-down, should the warning stay quiet like other enforcement,
     or still speak because it is advisory?

   **Related:** :need:`BUG_040 <BUG_040>` (a dispatch from a host session rooted in
   another repository is recorded nowhere: a different reason a run fails to
   count).

   **Why now / why this tier:**

   Medium value: it saves a wasted full design or review dispatch, which is the
   most expensive step in a pipeline. S effort: one PreToolUse branch in
   ``hosts/claude_code.py`` ``_pre_tool_use``, reusing the role lookup in
   ``core/rules``. Tier 5.

   .. invariant-history:
      - from: proposed
        to: accepted
        reason: user approved the ordered fix queue (BUG_075, IDEA_019, BUG_074, IDEA_020)
        forced: false
      - from: accepted
        to: in_progress
        reason: work started in session 7be8db18
        forced: false
      - from: in_progress
        to: done
        reason: shipped in b5612ce
        forced: false

.. idea:: The project plan is the default source of session state: plan next, and plan-derived handoff notes
   :id: IDEA_021
   :status: proposed
   :priority: high
   :effort: L
   :tier: 3
   :area: cli/main, core/manifest, hosts/claude_code

   **The want.** The project's plan becomes the default source of session state,
   so a handoff note no longer has to carry "what is next". Today the note is the
   only place that state lives, which makes notes long (see
   :need:`BUG_075 <BUG_075>`) and lets them drift from the plan they restate. There
   is no ``plan`` command today. Maintainer direction, 2026-10-20.

   **Proposed shape.**

   1. A plan-derived next item, e.g. ``invariant plan next``. A manifest setting
      names the plan file and how to read item status, so the rule is not
      specific to one project. The reporting project's rule: the next item is
      the first non-DONE item in the lowest phase.
   2. Session start and hook context show the derived next item by default.
   3. ``session handoff`` without ``--note`` derives the note from the plan: the
      next item plus the live worktrees.
   4. Optional: a handoff check that flags note items that are tracked in
      neither the backlog nor the plan.

   **Acceptance:**

   - With a plan declared in the manifest, ``invariant plan next`` prints the
     first not-done item of the lowest phase. With every item done it says so.
     With no plan declared it exits with a message naming the manifest key.
   - The SessionStart greeting (and ``invariant context``) shows that next item
     when a plan is declared, and nothing new when none is.
   - ``session handoff`` with no ``--note`` records a note naming the plan's next
     item and each live worktree. An explicit ``--note`` still wins.
   - A plan whose status cannot be read (unknown status word, missing file) is
     reported as unreadable, never as "nothing next".
   - (Optional) The handoff check names any note line that matches no backlog
     item and no plan item.

   **Architecture sketch:**

   - ``core/manifest``: a plan key (file path, status-reading rule).
   - A plan reader behind a small protocol, so the status rule is configuration,
     not code per project.
   - ``cli`` (a new ``plan`` group; see :doc:`cli.md </architecture/cli>`) and
     ``session handoff``; ``hosts/claude_code`` greeting context.

   **Risks / open questions:**

   - How general can "how to read item status" be? A regex over lines, a
     sphinx_needs-style directive option, a markdown checkbox? Start with one
     shape and say which.
   - Is "lowest phase" a general concept, or should the ordering rule itself be
     configured?
   - The derived note must pass the same privacy screening as a typed one.
   - A derived note keeps the typed line short only once BUG_075's temp-file fix
     lands. The two compose and neither blocks the other.

   **Related:** :need:`BUG_075 <BUG_075>` (long handoff notes fail to launch; this
   idea shortens them, BUG_075 makes any length safe);
   :need:`IDEA_008 <IDEA_008>` (handoff note lifecycle).

   **Why now / why this tier:**

   Maintainer direction, and it removes the class of long, drifting notes behind
   BUG_075. L effort: a new command group, a manifest key, greeting and handoff
   integration. Tier 3: a new concept (the plan as state) across cli, manifest
   and the host.

.. idea:: Retire a closed item into items.closed_path on its closing transition
   :id: IDEA_022
   :status: proposed
   :priority: medium
   :effort: L
   :tier: 5
   :area: core/items, domains/workitem, cli/main

   **The want.** When an item reaches a closing status, ``invariant item transition`` moves the entry out of the open store and into the closed store
   that ``items.closed_path`` declares, in the same transition. This is the
   deferred half of :need:`IDEA_019 <IDEA_019>` (done, cf9f356).
   :need:`BUG_074 <BUG_074>` (fixed, 07ee5c7) made the key real, but only as
   something to read: id minting and ``item new``'s explicit-id check count the ids
   retired there, and nothing writes it. Today a project with a closed store has
   to retire an entry by hand, outside the FSM. The record of the move then sits
   in the one store the gates cannot see (state_model.md, "The store the gates
   cannot sit in front of"). The read side has the same gap: ``item list``,
   ``item show`` and ``item transition`` read only the open store, even when
   ``closed_path`` is set, so a retired entry cannot be listed, shown or looked up
   through the CLI.

   **Proposed shape.** A closing transition on a backend with ``closed_path`` set
   writes the entry, with its whole body, history and residue unchanged, into the
   closed store and removes it from the open store. The entry ends up in exactly
   one of the two stores. With ``closed_path`` unset, behaviour is unchanged: the
   markdown backend grows no archive. The read verbs consult both stores.

   **Acceptance:**

   - With ``closed_path`` set, transitioning an item to a closing status leaves it
     in the closed store and not in the open store. Its body, history and residue
     are byte-identical to what the open store held, plus the new history row.
   - A write that lands in one file and not the other is either impossible or
     detected and recoverable: no run leaves an entry in both stores, or in
     neither, without a command that names the state and the way out.
   - ``item list`` (with closed statuses), ``item show <id>`` and ``item transition``
     find an entry that lives in the closed store.
   - With ``closed_path`` unset, every existing test and every markdown backlog
     behaves exactly as before.

   **Architecture sketch:**

   - ``core/items``: a backend-level retire (write to closed, remove from open)
     and a read path that covers both stores.
   - ``domains/workitem``: ``WorkItemDomain.transition`` calls retire on entering a
     closing status, after the gates pass.
   - ``core/manifest``: ``items.closed_path`` already exists (manifest.md, "The ids a
     store has spent, and the closed store"). No new key is expected.

   **Risks / open questions:** Recorded by feature-architect in
   :doc:`state_model.md </architecture/state_model>` under "Retirement is
   deferred".

   - (a) **Atomicity.** The move touches two files. It needs to be one atomic
     outcome, or it needs a recovery path for a write that lands in one file and
     not the other, e.g. write closed first, then remove open, with a check that
     detects an id present in both stores.
   - (b) **Placement in the closed file.** The closed store has its own layout,
     so where an entry goes in it is unresolved. The ``--section`` / ``--after``
     placement BUG_074 (b) added for the open store may be reusable here.
   - (c) **Prerequisite: :need:`BUG_073 <BUG_073>`.** The only backend that wants a
     closed store today is sphinx_needs, and it does not persist item history.
     Until BUG_073 is fixed, retiring an entry there would carry its ruling and
     history into the archive as nothing.

   **Why now / why this tier:**

   It closes the gap IDEA_019 and BUG_074 left open, but only sectioned backends
   with a closed store need it, and it waits on BUG_073. That makes it a Tier 5
   item at medium priority, not Tier 2.

.. idea:: Session Oracle web dashboard over Tailscale, with confirm-first actions
   :id: IDEA_023
   :status: done
   :priority: high
   :effort: L
   :tier: 3
   :area: oracle, cli/main

   **Shipped 2026-10-23** (commits 19be448, 33bdf8b, ccec29d; HTTPS via IDEA_026,
   commit 888af9b; fixes BUG_079, BUG_083, BUG_084). Verified live the same day: a
   phone paired over HTTPS on the Mac's tailnet name (the two older http-era
   pairings were revoked). The page auto-refreshed ("updated 6s ago"). From
   mobile Safari a confirm on a session row showed the exact argv, cwd and env,
   with Cancel first and a 120 s expiry; Run it returned the output page (exit 2
   in 3.2 s). The exit 2 came from the target project's own destination
   discovery (several simulators booted, no INVARIANT_DESTINATION_ID), not from
   the oracle.

   **The want.** You can watch and steer the Session Oracle (IDEA_012) from your
   phone while you are away from the Mac. Today the only dashboard is ``invariant oracle tui``, a terminal on the Mac itself, so a session that needs you while you
   are away is invisible until you get back. The shape is a small web page served
   from the Mac by the watcher, or by a sibling process under the same launchd
   agent (:doc:`oracle.md </architecture/oracle>`, "Shipped: the launchd agent").
   You reach it from your phone over your private Tailscale network. It shows
   everything the TUI shows and offers the same confirm-first actions (ADR-007
   D18), confirmed on the page.

   **Settled with the user (2026-10-22):**

   - **Hosting.** Local plus Tailscale. The page is served from the Mac, and
     session data never leaves your devices. Nothing on the public internet can
     reach it. This is the route chosen over a hosted site (IDEA_015) for now.
   - **Remote power.** View plus confirmed actions: the rows, states, signals and
     recommended commands of ``oracle tui``, and the same action kinds the
     recommendations produce (``info``, ``run``, ``terminal``, each confirmed on the
     page). It NEVER answers a Claude Code permission prompt. Those are still
     answered in Claude Code itself, for example through Remote Control.
   - **Auth.** The network is the gate: only devices on your tailnet can reach
     the page. On top of that, each device holds a token it stores on its first
     visit.

   **Acceptance:**

   - From your phone, over Tailscale, the page shows the same rows as ``oracle tui``, and a change shows up within 5 s.
   - A session waiting on you (a permission prompt or a question) is drawn red,
     as in the TUI.
   - Acting on a row shows the exact command, its cwd and the variables it adds
     before anything runs. A confirmed action runs exactly once, even if the
     confirm is sent twice, and the page then shows its output and exit status.
   - The page refuses the same steps the TUI refuses: a step on a row waiting
     on a prompt, a command that is not an ``invariant`` command, a cwd that does
     not exist, or a kind it does not know.
   - A device without the token gets nothing: no rows and no actions. A device
     off the tailnet cannot connect at all.
   - There is no path from the page that answers or approves a permission
     prompt.

   **Architecture sketch:**

   - The page reads the same ``assess`` document the TUI's ``dashboard.load``
     reads, and acts through the same ``actions`` refusal and dispatch. It adds a
     transport, not new rules.
   - A ``terminal`` step from the page opens a new host on the Mac through the
     handoff launcher, exactly as it does from the TUI.
   - This is the first network listener invariant runs, and the first time an
     action can be confirmed from outside the Mac's own terminal. ADR-007 calls
     the oracle read-only, and D18 assumes the person is at the Mac. This idea
     needs its own ADR, or a new decision in ADR-007, for the listener, the
     token and the remote confirm.

   **Related:**

   - IDEA_012 (parent): the oracle, the snapshot, the watcher, the launchd agent
     and the confirm-first actions this builds on.
   - IDEA_017 (sibling): extended actions (clean up, unstick). Once they ship,
     they should be offered from the page under the same confirm rules.
   - IDEA_015 (alternative, not chosen for now): a hosted website. It sends a
     redacted snapshot off the machine and is read-only. This idea keeps the
     data on your devices and adds actions.
   - IDEA_016 (alternative): a Claude artifact dashboard. It is also read-only
     and also off the machine.
   - IDEA_013 (possibly overlapping): phone push. The page shows a waiting
     session only while it is open on the phone, so push may still be wanted.

   **Risks / open questions:**

   - **Bind address.** Should the server listen only on the Tailscale
     interface, or on all interfaces (the wildcard address) and rely on the
     firewall plus the token? Binding to the tailnet address alone is the safer
     default, but it must survive the interface coming up after the launchd
     agent starts.
   - **Token issue and revoke.** How is a device's token issued (for example a
     one-time pairing code shown by an ``oracle`` command on the Mac), where is it
     kept on the Mac, and how is one device revoked without resetting the rest?
   - **CSRF and replay on action POSTs.** An action must carry proof that it
     came from the page's own confirm (a per-render nonce, single use, with a
     short expiry), so that a replayed or forged request runs nothing.
   - **Phone push.** Is IDEA_013-style push still wanted once the page exists?
     (Note: the brief named IDEA_014 here, but IDEA_014 is the status line;
     phone push is IDEA_013.)
   - A ``terminal`` step opens a window on a Mac nobody is sitting at. That is the
     point, but its successor watch and its failure output must reach the page.

   **Why now / why this tier:**

   The user asked for remote management directly, and the sessions are already
   driven remotely, so this is high value. It is L effort because of the trust
   boundary (listener, token, remote confirm), not the rendering. It is Tier 3
   because it adds a new external surface, a network listener, to the oracle.

   .. invariant-history:
      - from: proposed
        to: accepted
        reason: built in 19be448, 33bdf8b, ccec29d
        forced: false
      - from: accepted
        to: in_progress
        reason: built in 19be448, 33bdf8b, ccec29d; HTTPS via IDEA_026 (888af9b)
        forced: false
      - from: in_progress
        to: done
        reason: 'Shipped 2026-10-23: 19be448, 33bdf8b, ccec29d, HTTPS via IDEA_026 (888af9b), fixes BUG_079, BUG_083, BUG_084; used live from a phone over the tailnet'
        forced: false

.. idea:: Session Oracle knows every live host's pid from Claude Code's records
   :id: IDEA_024
   :status: done
   :priority: high
   :effort: S
   :tier: 2
   :area: oracle/scan, hosts/claude_code_launch

   **The want.** The oracle knows whether every running Claude Code host is
   alive, not only the ones that have written an attention record. Today a
   host's liveness comes from the pid in its attention record, and a host that
   has not written one since the Notification hook was installed has no pid, so
   it reads ``alive = null`` (oracle.md, "Host liveness"). On 2026-10-22 that was
   most of the orphan hosts on this Mac, some with up to 16 days of uptime. With
   liveness unknown, the oracle cannot offer adopt (it requires a known-alive
   host), cannot tell a leftover host from a closed one, and cannot safely offer
   any action that signals a process.

   Claude Code keeps a per-process record, ``~/.claude/sessions/<pid>.json``,
   carrying the pid, the Claude session id, the cwd, the session name and its
   start time. Joining it with the oracle's host ids gives an authoritative
   pid-to-session map for every live host, whatever hooks it ran. It is
   undocumented Claude Code internals, so it must be optional: read it when it
   is there and parses, and fall back to today's behaviour when it is not.

   **Acceptance:**

   - A live Claude Code host that never wrote an attention record shows as
     alive, with its pid, in ``oracle list``, the TUI and the snapshot.
   - A host whose per-process record names a pid that is gone, or a pid that is
     no longer a ``claude`` process, shows as not alive.
   - With the per-process records missing, unreadable, or in an unexpected
     shape, the oracle behaves exactly as it does today (attention-record pid,
     else ``null``), and ``invariant doctor`` or the scan says the source was not
     used. Nothing crashes.
   - When both sources name a pid for one host, the attention record and the
     per-process record agree, or the row says they disagree; the oracle never
     silently picks one.
   - The oracle still imports nothing from ``hosts/``, and no scan writes
     anything.

   **Architecture sketch:**

   - Extend ``HostProbe`` (ADR-007 D11) with a host-process read, for example
     ``host_processes()`` returning ``{session_id: (pid, name, cwd, started)}``.
     ``hosts/claude_code_launch.ClaudeCodeProbe`` implements it; the scan only
     joins. The suite injects a fake probe as it does today.
   - The pid check keeps today's guard: ``os.kill(pid, 0)`` plus the
     ``is_host_process`` name check, cached as now.
   - The record's start time and name are kept on the row, so the dashboard can
     show uptime and the name the host was launched with.

   **Related:**

   - IDEA_025 (orphan management) depends on this: its classification and its
     close-host action need trustworthy liveness.
   - IDEA_017's own risk note says the liveness check must be trustworthy
     before any destructive action ships. This is that check for hosts.
   - BUG_078 (last activity from transcript mtime) is the sibling read. Both go
     through the same probe and are cheapest built together.
   - BUG_040: a host rooted in another repository is still attributed by its
     hooks' project; this idea gives it a pid, not a different project.

   **Risks / open questions:**

   - The file format is not a Claude Code contract and can change in any
     release. The fallback must be the tested default, and a shape change must
     read as "source not used", never as "host gone".
   - Is the cwd in the record good enough to attribute a host with no attention
     record to a project, or does attribution stay transcript-based?

   **Why now / why this tier:**

   It makes the oracle's host rows true for every live host, and it unblocks
   IDEA_025, which the user approved to build after IDEA_023. S effort, one
   probe method plus its fallback. Tier 2: medium value on its own, with a strong
   unlock.

   .. invariant-history:
      - from: proposed
        to: accepted
        reason: Shipped in 32b104a
        forced: false
      - from: accepted
        to: in_progress
        reason: Implemented in 32b104a
        forced: false
      - from: in_progress
        to: done
        reason: Shipped in 32b104a
        forced: false

.. idea:: Session Oracle sorts orphan hosts and closes a leftover on confirm
   :id: IDEA_025
   :status: dropped
   :priority: high
   :effort: M
   :tier: 2
   :area: oracle, cli/main, hosts/claude_code_launch

   **Dropped 2026-10-23:** folded into IDEA_027 (the mobile-first control panel), which builds this scope v1 unchanged as its orphan-host and close-host increment. The body below stays the spec of record.

   **The want.** The oracle tells you which orphan hosts are leftovers you can
   close, and lets you close one safely, instead of listing every orphan as the
   same undifferentiated ``orphan host`` row. On 2026-10-22 this Mac showed 15
   orphan rows across two projects. Joined with Claude Code's per-process
   records, they were three different things: 3 had no process at all (old
   transcripts only); 9 were live Claude Code hosts whose invariant session was
   already ``torn_down``, idle from 1.4 h to 10 days, with up to 16 days of
   uptime; and 3 were live hosts that never had an invariant session. The user
   closed 7 of the leftover hosts by hand that day, after checking each one.
   That checking is what this idea does for you.

   **Settled with the user (2026-10-22):**

   - Build it after IDEA_023 (the web dashboard).
   - Everything here is show-only: no orphan class raises an alert. An alert
     for a long-idle leftover host is an open question below.
   - Closing a host is confirm-first, like every oracle action (ADR-007 D18).

   **Scope v1:**

   - **Classify orphans** into four classes:

     - *gone*: no process. Hidden 24 h after its last activity.
     - *leftover host*: alive, its session ended, idle. Recommended action:
       close host.
     - *unmanaged host*: alive, never had an invariant session, idle.
       Recommended: start a session here, or close host.
     - *recently active unmanaged*: alive, no session, active recently. Shown
       only, with no action.

   - **Close host**, a new confirm-first action. Before sending SIGTERM it
     verifies, at confirm time, that the pid is still a ``claude`` process and that
     its Claude session id matches the row's. It reuses the handoff closer
     (``HostCloser`` / ``DetachedCloser``, overview.md and ADR-005), whose kill-time
     ``ps -p`` check already guards against a reused pid. The confirm modal says
     that ``claude --resume`` can reopen the conversation.
   - **Collapse orphans** into one expandable summary line per project in ``oracle list``, the TUI and the IDEA_023 web page, e.g. "12 orphan hosts: 9
     leftover, 3 unmanaged". Expanding it shows the rows.

   Out of scope: closing a host whose session is live, any action on a *gone*
   row, and any alert.

   **Acceptance:**

   - Each orphan row is labelled gone, leftover, unmanaged or recently active
     unmanaged, and the label matches the host's real process and session
     state.
   - A gone row is not shown once 24 h have passed since its last activity.
   - Close host on a leftover row shows the pid, the session id and the
     ``claude --resume`` note, and does nothing unless confirmed. Once confirmed,
     that host's process exits, and nothing else is signalled.
   - If at confirm time the pid is gone, is not ``claude``, or carries a
     different session id, close host refuses and says why.
   - Orphans show as one summary line per project by default in ``oracle list``,
     the TUI and the web page, and can be expanded to the individual rows.
   - No orphan class raises an alert or a notification.

   **Architecture sketch:**

   - Classification sits in ``oracle/scan`` and ``oracle/signals``, from facts the
     probe provides (IDEA_024's per-process records, BUG_078's real last
     activity). ``oracle/recommend`` gains the leftover and unmanaged rows.
   - The action layer runs only ``invariant`` commands (oracle.md, "Confirm-first
     actions"), so close host needs either a new ``invariant`` subcommand that the
     ``run`` kind calls (e.g. ``invariant oracle close-host``) or a new action kind.
     The subcommand keeps the existing refusal rules intact.
   - ADR-007 calls the oracle read-only. Sending a signal to a host is a new
     power and needs a decision recorded in ADR-007, or folded into IDEA_023's
     ADR.

   **Relation to IDEA_017 (decided: kept separate).** IDEA_017 is the general
   clean-up and unstick action set: stranded sessions, leaked background
   processes, orphaned worktrees, stuck agents. This idea is narrower and
   ready to build now: the orphan host classes, the summary line, and one
   action, close host. It is the first IDEA_017 action to ship. Its
   confirm-time process check is the pattern IDEA_017's process kill should
   reuse. IDEA_017 keeps the rest of its list.

   **Depends on:** IDEA_024 (liveness for every live host; without it most
   leftovers read ``alive = null``) and BUG_078 (last activity from the
   transcript's last real entry; without it no orphan ever reads idle).

   **Related:** IDEA_012 (parent), IDEA_017 (sibling, above), IDEA_023 (the web
   page shows the summary line and offers close host under its confirm rules),
   BUG_040 (a host rooted in another repository must never be offered close
   host on a misattributed row).

   **Risks / open questions:**

   - **Alert for a long-idle leftover?** Optionally raise a low alert for a
     leftover host idle for more than 24 h. Left open; v1 is show-only.
   - Closing the wrong host loses an in-flight turn. The pid, process name and
     session id check at confirm time is the guard; it must be tested against a
     reused pid and a mismatched session id.
   - An unmanaged host may belong to a different project (BUG_040). The class
     should be "unmanaged here", not "unmanaged anywhere".

   **Leftovers found on 2026-10-22, for the user to decide (nothing was
   removed):** these are invariant sessions and worktrees in the other
   registered project, not hosts, so they belong to IDEA_017's clean-up list
   rather than this idea's scope.

   - Session 4b7b00c1: isolated for 6 weeks. Its branch is 2 commits ahead of
     master (one is a failing repro test), 297 behind, and its worktree has 13
     uncommitted files.
   - Abandoned session 0c269de4: its leftover worktree holds 1 unmerged commit,
     "Ruling recorded - the browser always stays in Debug builds".
   - Abandoned session 292ad87d: its leftover worktree holds no commits and 1
     uncommitted edit to ADR-071.

   **Why now / why this tier:**

   Leftover hosts pile up on every machine that runs the oracle, and the user
   closed seven by hand in one sitting. Medium-high value. M effort, mostly the
   classification and the safety checks. Tier 2, because the user approved it
   next after IDEA_023 and it is the first destructive action to ship.

   .. invariant-history:
      - from: proposed
        to: dropped
        reason: 'Folded into IDEA_027 (2026-10-23): its scope v1 is built there unchanged as the orphan-host and close-host increment'
        forced: false

.. idea:: Session Oracle web page over HTTPS with a Let's Encrypt certificate from Tailscale
   :id: IDEA_026
   :status: done
   :priority: high
   :effort: M
   :tier: 2
   :area: oracle/web

   **The want.** The Session Oracle's web page (IDEA_023) is served over HTTPS,
   with a Let's Encrypt certificate that Tailscale issues for the Mac's MagicDNS
   name (``<host>.<tailnet>.ts.net``). Today the page is plain HTTP over the
   tailnet. WireGuard already encrypts the transport, but the phone's browser
   marks the page as not secure, and the pairing cookie ``invariant_oracle`` is
   ``HttpOnly`` and ``SameSite=Strict`` but not ``Secure``. ADR-008 D3 deferred HTTPS
   to a later decision; this is that decision. The user asked for it on
   2026-10-22: "You should also set it up an SSL key using Let's encrypt."

   **Settled with the user (2026-10-22):**

   - **The certificate comes from Tailscale.** Tailscale's cert command issues a
     Let's Encrypt certificate for the machine's MagicDNS name. MagicDNS is
     already on. The user must enable "HTTPS Certificates" on the DNS page of the
     Tailscale admin console. That is a manual step, and it goes in the guide
     (:doc:`The oracle on your phone </guide/oracle-web>`).
   - **The oracle's own listener serves TLS.** It keeps binding only the Mac's
     tailnet addresses (ADR-008 D1, D2).
   - **Rejected: ``tailscale serve`` as a TLS front.** It would proxy from
     loopback, which weakens the tailnet-only bind rule and the ``Host`` and
     ``Origin`` checks. (ADR-008 already lists it under "Alternatives rejected" as
     open for later; this closes it.)
   - **Renewal.** Certificates last 90 days. The page's LaunchAgent
     (``dev.invariant.oracle.web``) renews ahead of expiry and loads the new
     certificate without a restart.
   - **HTTPS only.** Once HTTPS works, plain HTTP is no longer served, and the
     cookie gains ``Secure``.
   - **Checks.** The ``Host`` and ``Origin`` checks accept the ``https`` scheme and the
     ts.net name. The pairing link printed by ``oracle web pair`` uses the https
     name.
   - **Visibility.** ``oracle status`` and ``doctor`` show the certificate's expiry.
   - **Missing certificate.** If no certificate can be had (HTTPS Certificates
     not enabled in the console), the page either refuses clearly or keeps HTTP
     with a visible warning. The architect decides which and records it in the
     ADR.
   - **Trade-off, told to the user.** Certificate Transparency logs publicly
     record the machine's ts.net name. The name is unreachable off the tailnet,
     and no session data is exposed. The ADR records this.
   - **Tests never run tailscale.** Certificate issuance goes through a seam,
     like discovery and the server today (ADR-008 D7; the ``conftest.py`` program
     guard already refuses ``tailscale``).

   **Acceptance:**

   - With HTTPS Certificates enabled for the tailnet, the phone opens the page at its
     https address on the ts.net name with no certificate warning, and
     the browser reports the connection as secure.
   - A plain-HTTP request to the listener gets no page once HTTPS is serving.
   - After pairing, the ``invariant_oracle`` cookie carries ``Secure``, ``HttpOnly``
     and ``SameSite=Strict``, and the pairing form, confirms and actions all work
     over https (the ``Origin`` check accepts the https origin of the ts.net name).
   - ``oracle web pair`` prints an https link on the ts.net name.
   - ``oracle status`` and ``doctor`` show when the certificate expires. A
     certificate within its renewal window is renewed by the running agent, and
     the next connection is served the new certificate with no restart.
   - With no certificate available, the outcome the ADR chose (a clear refusal,
     or HTTP with a visible warning) happens, and ``status`` and ``doctor`` say why.
   - The suite issues no certificate and runs no ``tailscale`` command.

   **Architecture sketch:**

   - A certificate seam next to ``_oracle_web_addresses`` and
     ``_oracle_web_server``: issue or fetch the certificate and key for the
     MagicDNS name into a private file under the oracle home (born owner-only,
     ADR-008 D14). The suite replaces it with a recorder and a test certificate.
   - ``web.WebServer`` wraps each tailnet listener in an ``ssl.SSLContext``. Reload
     without a restart means swapping the context's certificate chain (or the
     context) for new connections.
   - ``webservice.WebService``'s loop already wakes every 30 s while serving. It
     is the natural place to check expiry and renew ahead of it, and to record
     the expiry in ``oracle/web.json`` for ``web_status``.
   - ``web`` ``Host`` and ``Origin`` rules: add the https scheme and the ts.net name;
     the cookie gains ``Secure``.
   - A new ADR-008 decision (or a successor ADR) replaces D3's "Not ``Secure``"
     note: the certificate's source, the HTTPS-only rule, renewal, the
     missing-certificate behaviour and the Certificate Transparency trade-off.
   - Guide: the admin-console step, and what the expiry line in ``status`` means.

   **Depends on:** BUG_079 (the page refuses its own pairing form and actions
   because browsers send ``Origin: null``). It is being fixed first, and this
   idea's ``Origin`` change builds on that fix.

   **Related:** IDEA_023 (the web page this hardens), ADR-008 (D1, D2, D3, D7,
   D14, D15, D16, D17).

   **Risks / open questions:**

   - Missing certificate: refuse, or HTTP with a warning? The architect decides
     and records it.
   - Whether the agent can run Tailscale's cert command under launchd: the CLI
     may need privileges or the GUI app's helper on macOS, and the issued files'
     location and permissions differ by install. The seam must report a failure
     clearly rather than serve nothing silently.
   - Hot reload: ``ssl.SSLContext.load_cert_chain`` on a live context affects new
     handshakes only; confirm that is enough, or swap the context per listener.
   - The certificate names the ts.net host, not the 100.x address, so the page
     and pairing link must use the name; a phone reaching the Mac by address
     would get a name mismatch.
   - Certificate Transparency publishes the machine's ts.net name (recorded,
     accepted by the user).

   **Why now / why this tier:**

   It makes the phone page read as secure and puts ``Secure`` on the pairing
   cookie, closing the gap ADR-008 D3 left open, right after IDEA_023 shipped and
   BUG_079 makes the page usable. Value medium-high for every phone use of the
   page; effort M, within the oracle's web layer plus one seam. Tier 2.

   .. invariant-history:
      - from: proposed
        to: accepted
        reason: Designed in ADR-008 D19-D24
        forced: false
      - from: accepted
        to: in_progress
        reason: null
        forced: false
      - from: in_progress
        to: done
        reason: Shipped in the IDEA_026 commit
        forced: false

.. idea:: Session Oracle web page becomes a mobile-first control panel
   :id: IDEA_027
   :status: proposed
   :priority: high
   :effort: XL
   :tier: 3
   :area: oracle, cli/main

   **The want.** The Session Oracle web page (IDEA_023, over HTTPS by IDEA_026)
   should be a clear, mobile-first control panel for the phone. Today it is a
   terminal listing rendered as HTML. On 2026-10-23 the user said, after using
   it on an iPhone: "the UI is really not mobile friendly and unclear. It has
   to be a nice mobile friendly control panel." What they saw on the phone:

   - Internal jargon on the surface: "(isolated)", "early · bugfix:fixing",
     "pid unknown", full host UUIDs, and the confirm's environment note ("on
     top of this environment, with Claude Code's host markers and
     INVARIANT_SESSION_ID removed").
   - Raw absolute paths and argv are the main content, and they wrap mid-word.
   - The header's "2 need you" does not take you to them. There is no
     attention-first order and no summary.
   - The only control is one small "run..." per card. There is no session
     detail view and no other action.
   - A failed action dumps raw shell output (in one case a whole destination
     discovery script) instead of a plain reason.
   - There is no home-screen app (manifest, icon), no safe-area insets, and
     the button casing is inconsistent ("Run it" / "close").

   The proposed shape is a redesign of the page's view and markup
   (``oracle/web``, ``oracle/webpage``, ``oracle/webactions``; see
   :doc:`oracle.md </architecture/oracle>`, "Shipped: the web page"). It also
   takes in the scope of IDEA_017 (clean-up and unstick actions) and IDEA_025
   (orphan host classes and close host), so that the panel has real controls.
   Both entries are dropped as folded into this one, and their bodies stay the
   spec of record for those actions.

   **Settled with the user (2026-10-23):**

   - **Process.** Plan first, then design: a mobile mockup the user approves
     before any build. Then implement in increments. Everything is documented
     in a new ADR, in oracle.md and in the guide,
     :doc:`The oracle on your phone </guide/oracle-web>`.
   - **First screen.** A summary strip, e.g. "2 need you · 3 working · 1
     stale", where each count jumps to its group. Cards follow in the order
     needs-you, then stuck or orphaned, then working, then idle. Each card
     shows the project name, a short session label, the state in plain words
     ("Waiting for your answer · 12 min") and one primary button. Paths, argv,
     pids and host ids appear only in the detail view.
   - **Controls.** All four are in scope: (a) a session detail view, (b)
     IDEA_017's actions, (c) IDEA_025's close a leftover host, and (d) copy a
     command to the clipboard. (b) and (c) are folded in here.
   - **Home-screen app.** In scope: a web manifest, an icon, standalone
     display and safe-area insets.
   - **Push alerts.** Out of scope. They stay in IDEA_013 as a follow-on.
     This item's home-screen app is the precondition for iOS web push, one of
     IDEA_013's provider options.
   - **A failed action.** One plain line of reason, plus a "show full output"
     toggle.

   **Constraints that stay:** Tailscale only, paired device token, and every
   action confirmed first (ADR-007 D18, ADR-008 D8 to D13). The oracle never
   answers or approves a permission prompt, and never runs a step on a row
   waiting on one. Auto-refresh stays, with "updated N s ago" and a stale flag
   (ADR-008 D6, D18). Assets stay inline under the nonce CSP, with no CDN and
   no other origin.

   **Scope v1, in increments (order to be confirmed by the plan):**

   1. **Plan.** The increments and the decisions the ADR must record, above
      all that the oracle gains destructive powers (signal a host, kill a
      process, tear down a worktree), which amends ADR-007's read-only premise.
   2. **Design.** A static mobile mockup of the first screen, a card, the
      detail view, the confirm sheet and a failed action, drawn from the
      project's existing look. The user approves it before step 3.
   3. **Plain-language panel.** The summary strip, attention-first ordering,
      plain-word states and labels, the detail view, copy command, consistent
      button wording, and no mid-word wrapping.
   4. **Home-screen app.** Manifest, icon, standalone display, safe-area
      insets, all served under the existing CSP and token rules.
   5. **Failure reasons.** A failed action leads with a one-line reason; the
      full output sits behind a toggle.
   6. **Orphan hosts and close host** (IDEA_025's scope v1, unchanged): the
      four orphan classes, one summary line per project, and close host with
      its confirm-time pid, process-name and session-id check.
   7. **Clean-up and unstick actions** (IDEA_017's list): close or abandon a
      stranded session, kill a leaked background process, tear down an
      orphaned worktree, nudge or re-dispatch a stuck agent. Each is
      confirm-first and each re-checks its precondition at confirm time.

   Out of scope: push notifications (IDEA_013), a hosted copy (IDEA_015), and
   any change to transport, pairing or the tailnet-only rule.

   **Acceptance:**

   - On a phone-width screen, the first view shows the summary strip and the
     needs-you cards without horizontal scrolling, and tapping a count scrolls
     to that group.
   - No card or confirm in the default view shows an absolute path, an argv, a
     pid, a host UUID or an engine state name. Each is reachable from the
     session's detail view, where long values wrap at path separators rather
     than mid-word.
   - Every action IDEA_017 and IDEA_025 list can be started from the panel. It
     shows what it will run and destroy, does nothing unless confirmed, and a
     destructive one refuses at confirm time if its precondition no longer
     holds.
   - A failed action shows one plain sentence of reason. The full output
     appears only after tapping "show full output".
   - Added to the home screen, the page opens standalone with its own icon and
     respects the safe areas. It still refuses an unpaired device and still
     loads nothing from another origin.
   - The page still refreshes itself, says "updated N s ago", and flags stale
     rows. No path lets it answer a permission prompt.

   **Architecture sketch:**

   - ``oracle/web`` (the view model): plain-language labels, attention order and
     the summary counts, built server-side so the page renders rather than
     interprets.
   - ``oracle/webpage``: new markup and CSS for cards, the detail view and the
     confirm sheet, the manifest and icon routes, and the copy action, all
     under the nonce CSP.
   - ``oracle/webactions`` and ``oracle/actions``: new action kinds or ``invariant``
     subcommands for close host and the IDEA_017 actions, plus a failure-reason
     summariser beside the job output.
   - ``oracle/scan`` and ``oracle/signals``: the orphan classes (IDEA_025).
   - A new ADR (ADR-009) for the panel and the oracle's destructive powers.
     oracle.md and the oracle-web guide are updated per increment.

   **Depends on:** IDEA_024 (every live host's pid, so "pid unknown" goes and
   close host has a target), BUG_078 (real last activity, so "waiting 12 min"
   and "idle" are true), BUG_040 (a misattributed cross-root host must never be
   offered a destructive action).

   **Related:** IDEA_023 and IDEA_026 (the baseline, done), IDEA_017 and
   IDEA_025 (folded in here), IDEA_013 (push, the follow-on), IDEA_015
   (hosted alternative, unaffected). Related note, not filed: on 2026-10-23 a
   "re-run the suite" from the page failed because the target iOS project's
   own destination discovery could not choose among several booted simulators
   with no destination id set. That is a target-project gap, not the oracle's.
   Here it is only the motivating case for plain failure reasons.

   **Risks / open questions:**

   - Destructive actions from a phone raise the cost of a misclassified row.
     The confirm-time checks from IDEA_025 and IDEA_017 must ship with the
     actions, never after them.
   - Plain-language labels must not hide a fact the user needs to decide on a
     confirm. The detail view stays one tap away from every confirm.
   - Summarising a failure in one line risks naming the wrong cause. The
     summariser should say "failed, exit N" plus the last meaningful line when
     it cannot do better, and never guess.
   - iOS standalone mode keeps its own cookie store, so a home-screen install
     may need its own pairing. The plan should check this and the guide should
     say so.

   **Why now / why this tier:**

   The user drives sessions from their phone daily and asked for this
   directly. It also folds in the two queued oracle action ideas. High value,
   XL effort (a redesign, a home-screen app, a new ADR and seven increments),
   so tier 3 at high priority.

.. idea:: The coverage floor can be advisory for declared subtrees during a phase
   :id: IDEA_028
   :status: done
   :priority: high
   :effort: S
   :area: core/rules, core/manifest, cli doctor

   **The want.** A project can declare, in a reviewable manifest diff, subtrees where the coverage floor is ADVISORY: methodology.gates.coverage_advisory_paths, a list of globs matched exactly as coverage_exclude and non_source are. A touched file under one is reported with its figure, or as not measured, and never fails coverage.meets_floor; every other file keeps the blocking floor unchanged.

   **Why.** A consuming project ruled that one development phase lands new, flag-off code under two subtrees on scoped test suites, with one full regression plus coverage pass owed at the phase gate. Its coverage.meets_floor is block and its only coverage lane is a full-suite run, so a milestone touching only the new code could not land without that run or a force. Dropping the gate from the methodology cannot tell shared code from new code.

   **Acceptance:**

   - A change touching only advisory paths passes with no coverage run, and the message names the count, the key, and that a coverage run is still owed before the list is removed.
   - A mixed change still blocks on the held files exactly as before: no-record, no-row, trust, floor and ratchet.
   - An advisory file below the floor is reported and flagged, never failed; no figure is invented.
   - invariant doctor names a configured list as an ACTIVE EXCEPTION.
   - The key is accepted by validation and listed by config show --provenance; a bare string for any of the three glob lists is refused at load.

   **Shipped with:** the session that adds the key (docs: architecture/overview.md coverage levers, architecture/manifest.md schema, guide/configuration.md).

   .. invariant-history:
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

.. idea:: Oracle panel shows a session's last message and hands it off
   :id: IDEA_029
   :status: proposed
   :priority: high
   :effort: M
   :tier: 2
   :area: oracle/panel, oracle/scan, oracle/actions, hosts/claude_code_telemetry

   **The want.** The oracle's control panel shows what each session last said,
   and lets the user act on it from the phone. On 2026-10-25 the user said the
   panel works but "I cannot close the orphan sessions, and I also like to see
   their last messages in the Oracle. For example, if they are asking me to do
   some things or they claim something, it is open. So I can decide to just
   close them or to ask you to take a follow-up action or create a bug or an
   idea in the backlog." Today a card and its detail view
   (:doc:`oracle-panel.md </architecture/oracle-panel>`, screens 1, 2 and 6)
   say what state a session is in, never what its Claude window last asked or
   claimed, so the user has to walk to the Mac to read it before deciding
   anything.

   The proposed shape has two parts.

   1. **Read.** For every session row and every orphan host row, the panel
      shows the last assistant text from the host's transcript. A card shows a
      short excerpt, a few lines at most. The detail view shows the full text,
      with a "Show all" toggle for a long message. The user's last prompt is
      shown above it as context when it comes from the same read at no extra
      cost.
   2. **Hand off.** The detail view offers three actions on that message: "Ask
      Claude to follow up", "File a bug" and "File an idea". On confirm, the
      panel opens a new Claude window on the Mac, rooted in that session's
      project. The window starts with a prepared prompt that quotes the
      message and names the session id, the project and the action (follow
      up; file a bug through backlog-curator; file an idea through
      backlog-curator). Nothing is ever typed into the existing session. The
      launch reuses the existing seam: ``oracle/actions``' launch line and the
      CLI's ``_handoff_launcher`` (:doc:`oracle.md </architecture/oracle>`, the
      ``terminal`` action kind).

   Close stays where it is, in IDEA_027 increment 6 (IDEA_025's scope v1).
   This entry adds only the reading and the hand-off, so that per row the user
   picks close or a hand-off. It is filed as its own entry, linked to IDEA_027
   as its umbrella, because it is new scope the IDEA_027 increment plan does
   not list (call it increment 6b in that plan when it is scheduled).

   **Constraints that stay:** the oracle never answers or approves a
   permission prompt. Every action is confirmed first, and the confirm sheet
   shows the exact command and the full prepared prompt before anything runs
   (ADR-009 D1, D6). Tailscale only, paired devices only (ADR-008). Scans stay
   read-only. The oracle imports nothing from ``hosts/``: the transcript read
   goes through the ``HostProbe`` seam (ADR-007 D11), not into ``oracle/``.

   **New exposure, so ADR-009 needs an amendment (a new D17).** Transcript
   text on a phone is content the oracle has never served. The decision must
   settle that:

   - the text is served only to a paired device, on the same token rules as
     the rest of the page;
   - nothing is persisted beyond the snapshot: no transcript text in any
     oracle file, log or record other than the in-memory or snapshot read;
   - there is a length cap on what is read and served;
   - the text is HTML-escaped and rendered as plain text, never as markup
     (no markdown rendering, no links made live). The CSP is unchanged.

   **Acceptance:**

   - A session row and an orphan host row whose transcript has an assistant
     entry show an excerpt of its last assistant text on the card, a few lines
     at most; a row with none shows no excerpt and no placeholder text.
   - The detail view shows the full last assistant text up to the cap; a long
     message is collapsed behind "Show all". Where available, the user's last
     prompt is shown above it, labelled as the prompt.
   - Text containing markup or script (for example ``<script>`` or a markdown
     link) displays literally as characters and executes nothing; the page
     still loads nothing from another origin.
   - Choosing "Ask Claude to follow up", "File a bug" or "File an idea" shows
     a confirm sheet with the exact launch command and the full prepared
     prompt; nothing happens unless confirmed. On confirm, a new Claude window
     opens on the Mac in that session's project with that prompt, and the
     existing session receives no input.
   - An unpaired device gets no transcript text from any route, and no
     transcript text is written to disk by the oracle.
   - A row whose project attribution is not known to be right (BUG_040, a
     host rooted in another repository) offers no hand-off.

   **Architecture sketch:**

   - ``hosts/claude_code_telemetry`` / ``hosts/claude_code_launch``: the probe
     gains a capped tail read returning the last assistant text and the last
     user prompt. This is the same reader BUG_078's fix adds for the last
     timestamped user/assistant entry.
   - ``oracle/scan``: carries the text into the snapshot through ``HostProbe``.
   - ``oracle/panel``: the excerpt and the full text as plain strings, plus the
     three hand-off rows in the detail's "More actions".
   - ``oracle/webpage``: escaped plain-text rendering and the "Show all" toggle,
     under the nonce CSP.
   - ``oracle/actions`` / ``oracle/webactions``: three new hand-off kinds on the
     ``terminal`` launch path, each building its prepared prompt.
   - ADR-009 D17; oracle.md, oracle-panel.md and the oracle-web guide updated.

   **Depends on:** IDEA_030 (the shared "open a new Claude window" launcher;
   the hand-off reuses it and fills in only the prompt, added 2026-10-26).
   BUG_078 lands first or together, since its fix is the same
   transcript tail read. IDEA_024 (every live host's pid) and BUG_040 (a
   misattributed cross-root host) gate the hand-off: a row whose project is
   wrong must never be offered a hand-off under that project.

   **Related:** IDEA_027 (umbrella; close host is its increment 6), IDEA_025
   (dropped, folded into IDEA_027; the orphan classes this reads for),
   IDEA_013 (push, which could later carry the same excerpt).

   **Risks / open questions:**

   - Transcript text may hold secrets or personal data the user typed. Paired
     devices and the cap limit exposure; whether to redact anything (for
     example through the privacy patterns) is for D17 to settle.
   - The transcript JSONL format is Claude Code internals. The read must
     tolerate an unknown or partial last line and fall back to no excerpt,
     never to a wrong one.
   - A last assistant entry may be tool use with no text. Whether to walk back
     to the newest entry that has text, and how far, is for the plan.
   - A prepared prompt that quotes the message passes transcript text into a
     new session; it must be passed as data, never through a shell string.

   **Why now / why this tier:**

   The user drives sessions from the phone daily and named this as the
   missing half of deciding what to do with an open or orphaned session. High
   value; M effort (one probe read, one ADR decision, view and three action
   kinds on an existing launch seam), so tier 2 at high priority.

.. idea:: Oracle starts a new Remote Control session in any registered project
   :id: IDEA_030
   :status: done
   :priority: high
   :effort: M
   :tier: 2
   :area: oracle/actions, oracle/panel, oracle/index, hosts/claude_code_launch, cli/main

   **The want.** From the phone, start a new Claude window on the Mac, with
   Remote Control on, in any project the oracle knows. On 2026-10-26 the user
   asked that the "Oracle can start a new session with a remote control enabled
   on any of the projects that I want". Today the panel
   (:doc:`oracle-panel.md </architecture/oracle-panel>`) can only act on a
   session that already runs; starting work in another project means walking
   to the Mac, opening Terminal and typing the ``claude`` line by hand.

   The proposed shape is one launcher, a CLI command at its core, that the
   page and the TUI both run:

   ::

      invariant oracle start-session <project> [--prompt TEXT] [--name LABEL]

   It opens a new Terminal window on the Mac that runs
   ``claude --name <name> --remote-control <name>`` in the project's main
   checkout, with the first prompt if one was given. Claude then starts the
   invariant session itself, as it does today; the oracle pre-creates no
   invariant session.

   **Settled with the user (2026-10-26):**

   - **Which projects.** Registered invariant projects only, the oracle's
     project index (``oracle/index``,
     :doc:`oracle.md </architecture/oracle>`, "Shipped: home and project
     index"). A name outside that list is refused; no free path is accepted.
   - **The name.** The Remote Control name is the project plus an editable
     label, by default the project and the time. Once the new window appears
     in a scan, the panel shows it under Working by that name, with a link to
     it.
   - **Entry points on the phone.** A "New session" button in the overview's
     top bar opens a sheet with a project picker and an optional first-prompt
     field; a per-project shortcut opens the same sheet with that project
     preselected.
   - **Permission mode.** Claude's default only. There is no mode picker and
     never a bypass flag. Every permission prompt reaches the user through
     Remote Control; the oracle never answers one.
   - **Surfaces.** The CLI command (the testable core), the phone page, and a
     key in the Textual TUI (:doc:`oracle.md </architecture/oracle>`, "Shipped:
     the dashboard") that opens the same confirm.
   - **Guards that inform, not block.** The confirm sheet shows how many Claude
     windows are already open in that project and whether its main checkout
     has uncommitted changes. Neither stops the start.
   - **The shared launcher.** This is built first as the one "open a new Claude
     window" path: project, optional prompt, Remote Control, confirm-first.
     IDEA_029's hand-off (follow up, file a bug, file an idea) then reuses it
     and only fills in the prompt.

   **Constraints that stay:** every action is confirmed first, and the confirm
   shows the exact command and the full first prompt before anything runs
   (ADR-009 D1, D6). Tailscale only, paired devices only (ADR-008). Tests never
   run osascript, launchctl, a real kill or tailscale, and never touch the real
   ``~/.claude``. The prompt is passed as data, never through a shell string.

   **New power, so ADR-009 needs a new decision, D16.** The oracle starting a
   new process on the Mac at a phone's request is new: until now its confirmed
   actions run steps in a session that exists, or close one. D16 must record
   that the start is confirm-first, limited to registered projects, always in
   the main checkout, always with Remote Control on and Claude's default
   permission mode, and reachable only over Tailscale from a paired device
   (ADR-008). IDEA_029 had pencilled its transcript decision as the next
   number; it moves to D17.

   **Acceptance:**

   - ``invariant oracle start-session <project>`` for a registered project opens
     one new Terminal window in that project's main checkout running
     ``claude --name <name> --remote-control <name>``, the name being the project
     plus the label (default: project and time); with ``--prompt``, Claude starts
     with that prompt. A project not in the index is refused with a plain
     reason and nothing is launched.
   - On the phone, "New session" in the top bar and the per-project shortcut
     both open a sheet with the project picker (preselected from the shortcut),
     the editable label and an optional prompt. Nothing launches until the user
     confirms the sheet, which shows the exact command, the full prompt, the
     number of Claude windows already open in that project and whether its
     main checkout has uncommitted changes.
   - After a confirmed start, the new window appears on the overview under
     Working by its Remote Control name, with a link to it, once a scan sees
     it.
   - No path offers a permission-mode choice or passes a bypass flag, and the
     oracle answers no permission prompt in the new window.
   - A key in the TUI opens the same confirm and runs the same command.
   - An unpaired device cannot start a session from any route.

   **Architecture sketch:**

   - ``hosts/claude_code_launch``: reuse ``require_remote_control``, the
     ``--name``/``--remote-control`` pairing, the Terminal window launch and the
     CLI's ``_handoff_launcher`` seam; the new command is a second caller, not a
     second launcher.
   - ``oracle/index``: resolve a project name to its main checkout; the only
     source of launchable projects.
   - ``cli/main``: ``invariant oracle start-session``.
   - ``oracle/webactions`` and ``oracle/panel``: a new confirm-first action kind
     carrying project, label and prompt; the confirm's open-window count and
     dirty-checkout facts.
   - ``oracle/webpage``: the "New session" button, the per-project shortcut and
     the sheet, under the nonce CSP.
   - The TUI dashboard: one key binding to the same confirm.
   - ADR-009 D16; oracle.md, oracle-panel.md and the oracle-web guide updated.

   **Related:** IDEA_029 depends on this item (its hand-off reuses this
   launcher and fills in only the prompt). IDEA_027 is the panel's umbrella
   (increment 7 still open); this entry is new scope alongside it. ADR-005 is
   where the handoff launcher it reuses was decided.

   **Risks / open questions:**

   - Remote Control name collisions: two starts in the same minute with the
     default label. The plan should decide whether the label gets a suffix or
     the confirm refuses a name already in use.
   - Matching the new window to its row relies on the scan seeing the host and
     its name; until it does, the page needs a plain "starting" state rather
     than nothing, and a start that never appears needs a reason.
   - A dirty main checkout is shown, not blocked; the new session isolates
     before editing, but the user should still see it on the confirm.
   - Starting a process on the Mac from the phone widens what a stolen paired
     token can do. D16 should state that this is bounded by the registered
     list, the confirm, and Claude's own permission prompts.

   **Why now / why this tier:**

   The user drives sessions from the phone daily and asked for this directly,
   and it is the shared launcher IDEA_029 waits on. High value; M effort (one
   CLI command and one action kind on an existing launch seam, a sheet, a TUI
   key and one ADR decision), so tier 2 at high priority.

   .. invariant-history:
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

.. idea:: invariant owns the format, lifecycle and enforcement of coding guidelines
   :id: IDEA_031
   :status: in_progress
   :priority: high
   :effort: XL
   :tier: 2
   :area: domains, adapters, hosts, manifest, docs/adr

   **The want.** invariant owns the format, the lifecycle and the enforcement of
   a project's coding guidelines, including the guideline parts of its
   ``CLAUDE.md``. The project keeps owning the content. Today a consuming project
   (Heap) carries its compliance rules as prose: a 36-rule list plus a
   Boy-Scout rule, enforced by hand-edited agent copies and by whichever model
   happens to read them. Nothing checks that a rule was applied, a verdict
   cannot be audited against the diff, and editing a rule is ungated.

   **Standing goal: invariant is model-agnostic.** Determinism comes from the
   engine (state machines, mechanical checks, schema-validated evidence), never
   from pinning a model. Gates judge evidence, not which model produced it. This
   extends :need:`ADR-001 <DEC_ADR_001>`
   (scripted state over prose rules) to the guidelines themselves, and
   :need:`ADR-003 <DEC_ADR_003>` (where an agent-role
   gate may sit) to per-rule verdicts.

   **Deliverables (the numbering carries the build order):**

   - **A0 — ADR (docs lane).** The guidelines domain and the model-agnostic
     contract: ruleset file format and location; three check kinds, pattern,
     boundary (import layering) and judgement; a verdict schema with
     citations; capability tiers as hints only; a host-neutral agent format.
   - **A1 — Conformance harness.** Fixed scenarios with known verdicts (a seeded
     violation, a missing test, a docs-first breach, a vague verdict), runnable
     through any host and model. The same verdict is expected on every run.
   - **A2 — Ruleset core.** Loader, ``doctor`` validation, a generated rst
     checklist and managed, marker-delimited ``CLAUDE.md`` sections, and a drift
     rule for a hand-edited generated file.
   - **A3 — Mechanical checkers.** Pattern and boundary checks per ecosystem
     adapter (regex first), with a baseline file and a ratchet modelled on the
     coverage ratchet and on :need:`IDEA_028 <IDEA_028>`.
   - **A4 — Verdict contract.** A per-rule verdict schema from the design and
     review agents; citations validated against the diff; the gate blocks on a
     missing or vague verdict; the ruleset's version hash recorded in evidence.
   - **A5 — Phase-scoped rule injection** through the host adapter.
   - **A6 — Host-neutral agent definitions** rendered per host adapter. Project
     overlays replace edited agent copies, and ``doctor`` reports drift.
     Background: :need:`BUG_051 <BUG_051>`, :need:`BUG_021 <BUG_021>`.
   - **A7 — Capability tiers in the manifest.** Model and host recorded in
     evidence for audit only, with a test that no gate reads them.
   - **A8 — Ruleset edits gated** through the docs lane, plus an ADR when the
     edit is large.

   **Heap adoption (pointer only).** B1 classify Heap's compliance rules (36 +
   Boy-Scout) into the three kinds; B2 Heap ruleset, generated checklist and
   managed ``CLAUDE.md``; B3 baseline, then warn to block; B4 library agents plus
   an overlay, retire or thin-wrap Heap's precommit and review-and-commit agents,
   keep testflight-publisher local; B5 the conformance suite against Heap with
   two or more models. The full B entries go in Heap's own backlog, filed from a
   host session rooted in Heap, not from here.

   **Order.** The :need:`BUG_040 <BUG_040>` fix (its ADR first) and A0 come first.
   Then A1 and B1 in parallel; A2, then A3, then A4. B2 and B3 after A2 and A3.
   B4 after A6. B5 last. A5, A7 and A8 anywhere after A4. Sequencing set by the
   user on 2026-10-26: :need:`IDEA_030 <IDEA_030>` is finished before any of this
   starts, and :need:`IDEA_032 <IDEA_032>` (scheduled sweep, T1) goes ahead of the
   BUG_040 fix and A0.

   **Acceptance:**

   - A project declares its rules in one ruleset file; ``doctor`` rejects a
     malformed ruleset, and a hand-edit to a generated checklist or a managed
     ``CLAUDE.md`` section is reported as drift.
   - A pattern or boundary violation added on top of the baseline blocks the
     gate; an existing violation recorded in the baseline does not, and fixing
     one shrinks the baseline for good.
   - A review that leaves a judgement rule without a verdict, or cites a line
     the diff does not contain, cannot leave review.
   - The conformance scenarios return the same verdicts across every run and
     across at least two models and hosts.
   - Nothing in a gate's outcome changes when only the recorded model or host
     changes.

   **Architecture sketch:**

   - A new guidelines domain beside the existing domains
     (:doc:`overview.md </architecture/overview>`, "Component responsibilities").
   - Pattern and boundary checkers under the ecosystem adapters, and agent
     rendering and rule injection under ``hosts/``.
   - Ruleset location, check levels and capability tiers in the manifest
     (:doc:`manifest.md </architecture/manifest>`).

   **Risks / open questions:**

   - Blocked by :need:`BUG_040 <BUG_040>`: a cross-repository host records no
     agent-ran evidence, so A4's verdict gate would wedge the same way in any
     consuming project driven from another root.
   - How strict "vague verdict" is, mechanically, without the engine itself
     judging prose. A0 has to settle it.
   - Boundary checks per ecosystem may outgrow regex quickly; A3 starts there and
     the ADR should say where it stops.
   - The size of the umbrella: each A item is likely its own track and may later
     be split into its own entry.

   **Why now / why this tier:**

   High value (every session in every consuming project goes through the
   guidelines; it turns prose rules into gated evidence), XL effort as a
   multi-phase programme with an ADR first. T2 Strategic by the user's call.

   **A0 landed** as :need:`ADR-012 <DEC_ADR_012>`
   in commit 7c5ed6d: all thirteen questions ruled, an always-pure ratchet with
   no Boy-Scout switch. A1 to A8 are unbuilt. The item stays ``proposed`` until its
   first build increment can cite a ruling commit for the ruled route to
   ``in_progress``.

   A1 increment 1 landed (dc48851): engine-layer conformance harness, three
   scenarios. A1 increments 2–6 are tracked here, not in a session.

   - Increment 2: after A2 (ruleset fixtures, malformed-ruleset scenario). The
     malformed-ruleset scenario landed with A2 increment 1; the fixtures for the
     kinds A2 increment 2 validates are still owed.
   - Increment 3: done, landed with A3: the seeded-pattern, missing-test,
     baselined, baseline-grown and fixed-not-pruned scenarios.
   - Increment 4: the scenarios are done, landed with A4 increment 7:
     vague-verdict, judgement-fail, judgement-fail-standard, stale-verdict and
     engine-answers. The model/host permutation test landed with A7 increment 4,
     which discharges this increment.
   - Increment 5: the scripted CLI driver plus ``invariant conformance run|show``,
     after A4.
   - Increment 6: Claude Code under two or more models, after A4 and A6.

   **A3 landed** on main on 2026-10-29 from session 864db3ee, in nine
   increments; the final commit is 3d41370. What landed:

   - lexical masks and a path-aware ``executable_text``, which fixed
     :need:`BUG_103 <BUG_103>`;
   - Python import references;
   - the ``guidelines.mechanical_clean`` gate over pattern, boundary and cochange;
   - the baseline file with ``guidelines baseline --record/--prune``;
   - ``guidelines.baseline_only_shrinks``;
   - override by guideline id;
   - an edit-time advisory at PostToolUse;
   - this repository's dogfood ruleset: two layering boundary rules, no
     baseline.

   A follow-up chore (303c85b) raised ``guidelines.mechanical_clean`` to block in
   this repository. A1 increment 3's conformance scenarios landed with A3.

   User rulings: all 17 A3 design recommendations accepted, recorded in
   :need:`ADR-012 <DEC_ADR_012>`'s
   as-built notes. The dogfood ruleset is two rules covering all four
   hand-written layering scans. No ``CLAUDE.md`` for now; revisit with A5.

   Known accepted gap: removing ``guidelines.baseline`` in one landing and
   re-adding a grown one in a later landing is admitted. A8's gated ruleset
   edits close it.

   Follow-ups filed, both unscheduled: :need:`BUG_104 <BUG_104>` (a
   whitespace-named file is invisible to the gates) and
   :need:`IDEA_038 <IDEA_038>` (a node lexer).

   Next: A4 (verdict contract), in design in session c181f22d.

   **A5 closed** in session 59bb4d65, in four increments: phase-scoped
   selection and ``guidelines show --phase`` (ed52faf), the turn layer
   (991fe2a), the edit layer and ``guidelines.injection.on_edit`` (145e6a5), and
   the always-layer warning with this closure (A5.4). All three layers of
   ADR-012 ruling 8 are built: the managed ``guidelines/index`` block, the turn
   index, and the statements an edit carries beside its findings. When a
   ruleset has ``always`` rules and no instruction file carries the block,
   ``doctor`` warns and ``guidelines.generated_current`` notes it, both naming
   ``invariant guidelines render --insert guidelines/index``.

   User rulings: the twelve A5 rulings (D1 to D12), recorded in
   :need:`ADR-012 <DEC_ADR_012>`
   under D7. Per D11 this repository still has no ``CLAUDE.md``, and its ruleset
   has no always rules; revisit when one is added. Subagents get only the edit
   layer until A6 renders the agents (D9).

   **A6 closed** in session 58e4b6c1, in five increments: one asset classifier
   and one install plan, with :need:`BUG_106 <BUG_106>` fixed (3b52b34, fd92bc6),
   the neutral agent format and the Claude Code renderer (662984b), overlays and
   the ``agents.*`` keys (e971cdf), the ``agents.rendered_current`` drift rule
   (aa418f2), and the agent bodies onto the engine with this closure (A6.5).
   The shipped agents are host-neutral sources rendered per host and amended by
   overlays rather than edited copies. ``feature-architect`` reads ``invariant guidelines show --phase design --touched`` before it plans, and
   ``compliance-reviewer`` answers the guidelines through ``guidelines show --phase review --touched``, ``check guidelines.mechanical_clean``, ``guidelines verdicts show`` and one ``guidelines verdict`` per owed rule, pasting the standings into
   its report. Both fall back to the checklist on the engine's no-ruleset line.
   A test parses every command a shipped body names.

   User rulings: the fourteen A6 rulings, recorded in
   :need:`ADR-012 <DEC_ADR_012>`
   under D8. Ruling 14 holds: no gate on the design read. Ruling 13's migration
   helper (draft an overlay from an edited copy) is to be filed as its own idea.

   A6 unblocks Heap B4 (the downstream library agents and their overlays) and
   A1 increment 6 (Claude Code under two or more models). This repository's own
   installed agents are reinstalled after the merge.

   **A7 closed** in session 5cb38a2f, in five increments: the tier vocabulary and
   the manifest keys (A7.1), the tier renderer and the classifier state (A7.2), the
   audit record (A7.3), the record's lock with BUG_107 (A7.3b), and the D1 test with
   this closure (A7.4). A tier is a hint
   in the agent source with ``agents.tiers`` as the override, and it becomes a model
   only through the operator's ``host.tier_map``, empty as shipped. Model and host are
   recorded in ``evidence.audit``, which ``RuleContext`` removes before any rule runs.

   The D1 test discharges A1 increment 4's owed test and meets the acceptance line
   that a gate's outcome does not change when only the recorded model or host does.
   It is a source scan plus replays over hostile records, four configurations and a
   consistent rename. It proves engine independence only. Whether models agree is
   the driven layer's question, A1 increments 5 and 6.

   The test found one real read: a malformed ``agents_run_how`` made ``record_agent_run``
   raise and lose the run, which moved both role gates at the landing. The user
   chose to fix the writer rather than narrow the test, so a field of the wrong shape
   reads as empty. The same fix went into ``agents_run``, ``docs_read``, ``test_runs``,
   ``verdicts``, ``guidelines_landings`` and ``acknowledged``, and ``stamp_verdict_agent``
   now skips a malformed list. ``apply_acknowledgements``, the one reader that had no
   net under it, now treats a malformed ``acknowledged`` as acknowledging nothing.

   User rulings: the fourteen A7 rulings, recorded in ADR-012 under D9. This
   repository's own manifest and ``tier_map`` stay empty, and its installed agents are
   reinstalled after the merge.

   .. invariant-history:
      - from: proposed
        to: accepted
        reason: A0 landed as ADR-012 (7c5ed6d) with the user's 13 rulings; the user approved building the plan
        forced: false
        ruling: the user, 2026-10-27 (ADR-012 rulings)
        evidence: 7c5ed6d78ad62ba791721b83abaa7e4f0b837566
      - from: accepted
        to: in_progress
        reason: 'First build increment landed: A1 increment 1 in dc48851'
        forced: false

.. idea:: Whole-suite and whole-scheme coverage move to a scheduled sweep
   :id: IDEA_032
   :status: done
   :priority: high
   :effort: L
   :tier: 1
   :area: core/rules, core/manifest, cli, domains/items

   **The want.** A project can move whole-suite and whole-scheme coverage evidence
   off the per-change path and onto a scheduled sweep, and the engine enforces the
   split rather than prose. A consuming project (Heap) ruled on 2026-10-26: a
   change never runs the whole test suite or whole-scheme coverage, in any case;
   the full suite plus coverage runs once a week; every new red or floor drop
   becomes a follow-up work item.

   **Why.** In that project ``coverage.meets_floor`` is block with
   ``coverage_scope: touched``, but its coverage command cannot be narrowed with
   ``-only-testing``, because a scoped run is only a lower bound: one file measured
   65% scoped against 97% whole-scheme (that project's own coverage-scope defect).
   So every change that touches shared code is forced into a whole-scheme run of
   45 minutes or more. ``test.all_pass`` is already warn there.
   ``methodology.gates.coverage_advisory_paths`` (:need:`IDEA_028 <IDEA_028>`) covers
   only new code under declared subtrees, so it cannot answer this.

   **Proposed shape.**

   1. **Scoped per-change evidence.** A change's test evidence comes from its
      affected suites. ``test.new_test_fails`` (RED-first) still blocks, unchanged.
   2. **Gates on a cadence.** The manifest can put ``coverage.meets_floor`` and
      ``test.all_pass`` on a schedule. Per change, ``coverage.meets_floor`` then
      reports the scoped figure as advisory, or is satisfied by a fresh sweep; it
      never blocks on a scoped lower bound. Once a cadence is configured, no
      per-change gate may require whole-suite or whole-scheme evidence, and
      manifest validation refuses a config that would.
   3. **Full-run detection.** The engine detects a session running the full suite
      or the whole-scheme coverage command on a change, and refuses or warns
      (level decided in design).
   4. **Sweep command** (name decided in design, e.g. ``invariant sweep run``): runs
      the full suite plus coverage, diffs against the last recorded sweep, and
      files each new red or floor drop through ``invariant item`` with the commit
      range since the last sweep. An item still broken from an earlier sweep is
      not filed twice.
   5. **Freshness rule.** When the last recorded sweep is older than the cadence
      (7 days) plus a grace margin, the engine warns. It does not block.
   6. **Manifest surface.** A cadence value; which gates move to the schedule; an
      optional path-glob allowlist that still owes a pre-merge full run (the
      consuming project has not ruled on it; default empty). This generalises
      ``coverage_advisory_paths`` rather than adding a parallel mechanism; whether
      that key is kept or retired is a design question.

   **v1 scope (user ruling 2026-10-26).** IN: cadence mode for
   ``coverage.meets_floor`` and ``test.all_pass`` (items 1 to 3 and 6), the sweep
   command with diff and auto-filing (4), the freshness rule (5). OUT, fast
   follow: automatic bisect of a new red to its commit; until then the commit
   range is always recorded on the filed item.

   **Acceptance:**

   - With a cadence configured, a change touching shared code lands on its
     scoped evidence alone; ``coverage.meets_floor`` shows the scoped figure as
     advisory and does not block, while a new test that does not fail first
     still blocks.
   - A manifest that configures a cadence and also leaves a per-change gate
     requiring whole-suite or whole-scheme evidence is refused at load, naming
     the gate.
   - A session that runs the full suite or whole-scheme coverage command on a
     change is refused or warned, by the configured level.
   - The sweep command, run twice across a commit range that introduces one
     failing test and one floor drop, files exactly two items, each carrying the
     commit range; a third run with both still broken files nothing new.
   - With the last sweep older than 7 days plus the grace margin, the engine
     warns on every per-change check and names the age; it never blocks for it.

   **Architecture sketch:**

   - Cadence and gate-scheduling rules in ``core/`` beside the existing coverage
     levers (:doc:`overview.md </architecture/overview>`, "Component
     responsibilities"); the per-file advisory/held split already used by
     ``coverage_advisory_paths`` is the seam to generalise.
   - A new sweep subcommand (:doc:`cli.md </architecture/cli>`) that files
     through the work-item domain.
   - Cadence, scheduled gates and the allowlist in the manifest schema
     (:doc:`manifest.md </architecture/manifest>`), with ``doctor`` naming an
     active cadence.
   - Sweep records stored as evidence so freshness and the diff have a baseline.

   **Related:**

   - :need:`IDEA_028 <IDEA_028>` (done) is generalised and superseded by this.
   - :need:`IDEA_031 <IDEA_031>` sibling: shares the A3 ratchet idiom; no dependency.
   - :need:`IDEA_011 <IDEA_011>` sibling coverage lever.
   - :need:`BUG_040 <BUG_040>` sibling, not a blocker.
   - Background on unsatisfiable coverage gates: :need:`BUG_026 <BUG_026>`,
     :need:`BUG_036 <BUG_036>`, :need:`BUG_039 <BUG_039>`, :need:`BUG_085 <BUG_085>`.

   **Order.** User ruling 2026-10-26: this idea comes first on this host, then
   :need:`BUG_040 <BUG_040>` together with :need:`IDEA_031 <IDEA_031>` A0.

   **Risks / open questions:**

   - Whether "this run is the whole suite" can be detected per ecosystem without
     false positives, and whether detection refuses or warns by default.
   - The grace margin's length.
   - Keep or retire ``coverage_advisory_paths`` once cadence mode lands.
   - Whether the pre-merge full-run allowlist is wanted at all (no ruling yet).
   - A flaky test in the sweep (e.g. :need:`BUG_081 <BUG_081>`) would be filed as a
     new red; dedup by test identity has to tolerate that.

   **Closing obligation.** When the engine part lands, record the landing commit
   in this entry and send a short note back to the Heap session named
   s-1-sync-foundations saying it has shipped.

   **Landed 2026-10-26.** v1 merged to main at ac73b18, in three increments:
   e417275 (cadence mode, manifest validation, held_paths, coverage_one and
   --selector, sweep.no_full_run), 8fbf143 (invariant sweep run|status|show,
   committed records under docs/sweeps, auto-filing with sweep_key and
   sweep_range) and ac73b18 (sweep.fresh, the advisory verdict naming the last
   sweep, agent guidance, docs). Design: ADR-010. Fast follow: automatic bisect.

   **Why now / why this tier:**

   High value: every change in the consuming project that touches shared code
   currently pays a 45-minute-plus whole-scheme run. L effort (cross-layer: rules,
   manifest, a new command, evidence store). T1 Ship next, priority high, by the
   user's call.

   .. invariant-history:
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

.. idea:: swift_xcode records one scoped run over several selectors
   :id: IDEA_033
   :status: proposed
   :priority: high
   :effort: M
   :tier: 2

   Under a sweep cadence (IDEA_032, ADR-010) a change records its evidence
   scoped — ``invariant pipeline test --selector <affected tests>`` and ``invariant pipeline coverage --selector <affected tests>`` — and the compliance reviewer
   is told never to use the bare forms. A Swift project on the shipped
   ``swift_xcode`` defaults cannot follow that guidance once the affected set is
   larger than one selector: ``swift_xcode`` deliberately does not override
   ``Adapter.join_selectors``, so ``joined_selectors`` refuses any second
   ``--selector`` before anything runs (``pipeline test``, ``pipeline coverage``, and
   the sweep's re-run). Running one selector per invocation is not a workaround:
   ``test.all_pass`` reads only the LAST run, so a red leg followed by a green one
   reads as green — the reason BUG_031 rejected it. What is left is the full
   suite, which is exactly what ``sweep.no_full_run`` exists to discourage. The
   proposal: let ``swift_xcode`` record ONE scoped run over several selectors,
   revisiting BUG_031's decision with a way to know the template's shape rather
   than guessing it.

   **Acceptance:**

   - On the shipped ``swift_xcode`` defaults, ``invariant pipeline test --selector A --selector B`` runs one ``xcodebuild`` invocation that executes the tests of
     both A and B, and records a single run whose ``selectors`` lists both.
   - The same holds for ``invariant pipeline coverage --selector A --selector B``
     through ``commands.coverage_one``.
   - A project whose ``test_one`` / ``coverage_one`` template has been rewritten in a
     shape the adapter cannot recognise still gets today's refusal naming every
     selector, having run and recorded nothing — never a silently wrong command.
   - No input yields a single ``-only-testing:`` flag whose value is a
     comma-joined selector set.
   - A failing selector in the set makes the one recorded run red, so
     ``test.all_pass`` fails.

   **Architecture sketch:**

   - ``adapters/swift_xcode`` — a ``join_selectors`` that renders the repeated
     ``-only-testing:`` form, guarded so it answers only when it can tell the
     template is the shape it renders for (e.g. the template is the shipped
     default, or the manifest declares how selectors join, such as a
     per-selector fragment the placeholder expands).
   - ``domains/pipeline`` — ``joined_selectors`` unchanged in contract (adapter
     answers or the caller refuses); the sweep's re-run benefits for free.
   - Manifest — only if the chosen guard is a declared join form; then a
     documented key in docs/architecture/manifest.md.

   **Risks / open questions:**

   - How the adapter knows the template's shape. BUG_031's objection stands: a
     rendering right for the default and wrong for a rewritten template is the
     same defect one layer along. Pick between "recognise the default" and "the
     project declares the join"; the second is more general, the first needs no
     config.
   - Zero-executed ambiguity: a set where one selector matches nothing still
     executes the others, so the run's total is not zero and the unmatched
     selector goes unnoticed. Decide whether a per-selector executed count is
     needed (the ``.xcresult`` bundle can attribute tests to targets/classes) or
     whether that is a follow-up.
   - BUG_038: a swift-testing selector can scrape as zero executed; a mixed set
     inherits that until BUG_038's counting half lands.
   - xcodebuild command-line length on a large affected set.

   **Why now / why this tier:**

   IDEA_032 shipped (ac73b18) and made scoped runs the per-change norm; Swift
   projects on the defaults are the one ecosystem that cannot follow it for a
   multi-selector change, so this is a medium-value item with a strong unlock.

   **Related.** :need:`BUG_031 <BUG_031>` (the decision revisited), :need:`IDEA_032 <IDEA_032>`
   (the guidance it unblocks), :need:`BUG_038 <BUG_038>` (zero-count ambiguity under
   swift-testing). Raised by a consuming Swift project wiring IDEA_032; its
   report of a comma-joined ``-only-testing:`` value did not reproduce from
   ``--selector`` alone (that spelling is unconfirmed).

.. idea:: A sweep can opt in to filing its baseline's reds, grouped and capped
   :id: IDEA_034
   :status: proposed
   :priority: low
   :effort: M
   :area: domains/sweep

   ADR-010 ruling 7 makes the first sweep on a branch a baseline that records
   and files nothing, and that stays the default. Its consequence: a project
   that adopts the sweep with a large pre-existing red set never sees those reds
   as items — only reds that are new after the baseline are filed. Observed
   2026-10-27 on a downstream iOS project whose baseline held 845 failing
   tests; that set is now triaged by hand, outside the backlog the sweep keeps
   for everything after it. The proposal: an opt-in — a manifest setting under
   the sweep's schedule, or a ``sweep run`` flag — that files the baseline's reds,
   grouped and capped so hundreds of reds do not flood the backlog, and keyed
   by ``sweep_key`` so later sweeps never double-file them.

   **Acceptance:**

   - With the opt-in off (the default), a baseline sweep files nothing,
     exactly as today.
   - With it on, a baseline sweep with N failing tests files a bounded number of
     items: one per test class or suite (each listing its failing tests), or one
     umbrella item with a checklist — never one item per test once N is large.
   - Every failing test in the baseline is claimed by exactly one filed item's
     ``sweep_key`` set, so the next sweep reports it as still broken and files
     nothing new for it.
   - Re-running a baseline sweep with the opt-in on, against open items that
     already claim those keys, files nothing.
   - The sweep record's ``filed`` list names every item the baseline filed.

   **Architecture sketch:**

   - ``domains/sweep`` — a baseline branch of the filing step that groups failing
     ids (by class/suite from the test identity, else one umbrella) and caps the
     item count; reuses the existing dedup against open items.
   - ``core/sweep`` — the grouping and cap as a pure function beside the diff.
   - Manifest / CLI — one documented key (docs/architecture/manifest.md) or one
     flag (docs/architecture/cli.md); an ADR-010 amendment recording that ruling
     7 gained an opt-in.

   **Risks / open questions:**

   - ``sweep_key`` today is one key per item (``test:<id>``, ``flaky:<id>``,
     ``suite:red``); a grouped item must claim many keys. Decide whether an item
     carries a key list, or the group gets its own key (``class:<name>``) and the
     dedup learns that it claims its members.
   - Grouping depends on the adapter exposing a class/suite from a test id;
     where ``failing`` is null (adapter cannot read identities) only the umbrella
     form is possible.
   - Cap shape: a fixed limit with the overflow folded into the umbrella, like
     ruling 8's ``suite:red`` past the re-run cap.
   - Setting or flag: a flag is one-shot and fits a one-time baseline; a setting
     would also apply to a new branch's baseline.

   **Why now / why this tier:**

   Parked by the user's call: the default stays, the pain is one-time per
   adopting project and has a manual workaround, and the dedup-shape question
   should be settled before anyone builds it.

   **Related.** :need:`IDEA_032 <IDEA_032>` (the sweep, done) and ADR-010
   (docs/adr/ADR-010-whole-suite-evidence-moves-to-a-scheduled-sweep.md),
   rulings 7 and 8; the sweep's filing and dedup in docs/architecture/state_model.md,
   "Domain: sweep".

.. idea:: Launched windows end with Claude, and invariant flags hosts in a polluted environment
   :id: IDEA_035
   :status: done
   :priority: medium
   :effort: M
   :tier: 5
   :area: hosts/claude_code_launch, cli/main, oracle/scan, oracle/signals

   **Shipped 2026-10-28** (commit e3baef8).

   Two leftovers of how Claude windows get started cost the user every day.
   First, a window invariant launches (``session handoff``, ``oracle start-session``, the dashboard's terminal step) types ``cd <main> && env -u … /bin/sh <launch file>`` into a Terminal ``do script`` login shell, so when
   Claude exits the shell remains and the window stays at a prompt (BUG_100's
   leftover, for every launched window). Second, a host started under a
   polluted environment, from a Terminal.app that was itself started from a
   Claude tool shell before BUG_068 and BUG_069's scrub fix, inherits
   ``COLORTERM=truecolor`` and ``CLAUDE_CODE_CHILD_SESSION=1``. Observed on such
   hosts: Terminal.app, which has no 24-bit colour, renders large white or
   inverse blocks; Claude says "Transcript saving is off"; Claude Code writes
   no per-process record, so the panel cannot offer Close (BUG_099) and
   activity and transcript tracking degrade (BUG_096). Nothing in invariant
   tells the user, and the cure is simple: quit Terminal.app fully and
   relaunch it from the Dock. The proposal: (a) make the typed launch line
   ``exec env -u … /bin/sh <launch file>``, so the tab's shell is replaced and
   the window ends when Claude exits; (b) a ``doctor`` row and/or an oracle
   signal that names a host running with an inherited ``COLORTERM=truecolor``
   or ``CLAUDE_CODE_CHILD_SESSION``, with that remedy.

   **Acceptance:**

   - A window opened by ``session handoff``, ``oracle start-session`` or the
     dashboard's terminal step closes (or reports itself finished, per the
     user's Terminal profile) when its Claude exits; no shell prompt remains.
   - A launch that fails before Claude starts still leaves the by-hand line
     usable, and the existing successor watch still reports the failure.
   - On a machine with a Claude host whose own environment carries
     ``CLAUDE_CODE_CHILD_SESSION`` or ``COLORTERM=truecolor`` inherited from its
     terminal, ``invariant doctor`` prints a ``warn`` row naming the host and the
     remedy (quit Terminal.app fully, relaunch from the Dock); with no such
     host the row passes. The row never sets the exit code.
   - The oracle shows the same finding on that host's row (an info-level
     signal or a reason line on its card), so the user sees it from the phone.
   - Variables Claude Code sets for its own tool shells never trip the check:
     ``doctor`` run from a clean host's tool shell reports clean.

   **Architecture sketch:**

   - ``hosts/claude_code_launch`` — the typed line (``plan_launch``, docs/architecture/oracle.md "Start a session
     (IDEA_030)"; the line cli.md shows under ``session handoff``), now
     ``exec``-ed.
   - ``doctor`` (docs/architecture/cli.md "Setup") — one warn row.
   - ``oracle/scan`` / ``oracle/signals`` — read the host process's environment
     once per verified pid (with the 60 s fact cache) and carry a flag the
     panel words.

   **Risks / open questions:**

   - The colour cause is inference, not proven read-only: the correlation
     between ``COLORTERM=truecolor`` under Terminal.app and the white blocks was
     exact across the open windows, but no controlled toggle was run.
   - Reading another process's environment on macOS (``ps eww``) may be limited
     to the user's own processes and may be truncated; the check must judge
     the ``claude`` process's own environment, never ``doctor``'s, since Claude
     sets markers on its tool shells.
   - ``exec`` changes what a user sees when Claude crashes at start: the window
     vanishes with the error. The launch file may need to pause on a
     non-zero exit.
   - Whether a Terminal profile set to "don't close" keeps a finished window;
     that is the user's setting and fine to leave.

   **Why now / why this tier:**

   Medium value (every launched window, and the hidden cause of BUG_099 and
   part of BUG_096), small to medium effort in two known places; T5, scheduled
   by the user after the current oracle batch.

   **Related:** BUG_098 (Close leaves the tab open), BUG_099 (no record, no
   Close), BUG_100 (handoff leaves a prompt), BUG_092, BUG_093, BUG_094,
   BUG_095, BUG_096 (current oracle batch), BUG_068 and BUG_069 (launcher
   environment scrub, fixed: the source of the polluted Terminal.app),
   IDEA_027 (control panel), IDEA_030 (start a session, the launcher).

   .. invariant-history:
      - from: proposed
        to: accepted
        reason: built in session 675189e4
        forced: false
      - from: accepted
        to: in_progress
        reason: built in session 675189e4
        forced: false
      - from: in_progress
        to: done
        reason: shipped in e3baef8 (session 675189e4)
        forced: false

.. idea:: Whole-suite and sweep evidence for every declared ecosystem, not only the primary
   :id: IDEA_036
   :status: proposed
   :area: domains/pipeline,domains/sweep,core/rules
   :priority: medium

   **Context:** ADR-013 (BUG_097's fix) routes a scoped ``pipeline test`` or
   ``pipeline coverage`` to the declared ecosystem that owns its selectors, so a
   second ecosystem's red and scoped runs can be recorded and read by their own
   adapter. It deliberately leaves the WHOLE-suite surfaces with the primary
   ecosystem (ADR-013 D6):

   - ``pipeline test`` and ``pipeline coverage`` with no ``--selector`` run the
     primary's ``commands.test`` / ``commands.coverage`` only.
   - ``invariant sweep run`` runs, re-runs and reads the primary's suite only
     (``domains/sweep.py``, the adapter from ``manifest.primary_ecosystem``).
   - ``test.all_pass`` reads the last recorded run, whichever ecosystem it was.

   So in a ``[python, node]`` project nothing ever demands, or records, that the
   node suite is green as a whole, and under a sweep cadence a node file's
   advisory coverage row is "held by the sweep" that never measures it. Nothing
   here is lowered by ADR-013 (before it, the second ecosystem could not be run
   at all), but the gap is real.

   **Proposal:** whole-suite evidence for every declared ecosystem. Open
   questions: one record per ecosystem in ``test_runs`` and how ``test.all_pass``
   reads several (every declared ecosystem's last full run green, not the last
   run alone); whether ``pipeline test`` with no selector runs each ecosystem in
   turn or takes ``--ecosystem``; how a sweep record keys findings per ecosystem
   and which adapter re-runs a red alone. Node coverage stays unreadable until
   :need:`BUG_018 <BUG_018>`.

   **Related:** :need:`BUG_097 <BUG_097>`, ADR-013, ADR-010, :need:`BUG_031 <BUG_031>`.

.. idea:: The oracle closes background-work count-downs from the host transcript
   :id: IDEA_037
   :status: proposed
   :priority: medium
   :effort: M
   :tier: 5
   :area: oracle/scan, oracle/signals, oracle/attention, hosts/claude_code

   **Context:** :need:`BUG_094 <BUG_094>`'s fix (session 980e0254, not yet merged)
   adds a per-host background ledger to the oracle attention record. It counts
   up when Claude Code launches an async Agent or a background Bash command
   (PostToolUse) and counts down when a UserPromptSubmit starts with the
   task-notification tag. Measured on local transcripts, about 56% of task
   notifications (334 of 594) arrive while a turn is already running, as
   queued-command attachments, and fire no UserPromptSubmit, so the ledger
   misses roughly half its count-downs. The user therefore ruled that the
   ledger lapses after ``stuck_minutes`` (20) without host activity. The
   accepted cost: a hung background agent is never flagged ``stuck``; it falls
   back to idle after 20 minutes of silence. Hung background commands are
   still flagged through Claude Code's per-process status "shell".

   **Proposal:** the oracle scan closes ledger entries from the host's
   transcript. A new ``HostProbe`` method, used by ``oracle/scan``, checks the
   ledger's open ids against the task-notification text in the tail of the
   host's main transcript and closes the entries it finds there. Once
   count-downs are reliable, the ledger can lapse at ``orphan_idle_minutes``
   instead of ``stuck_minutes``, and a background agent silent past the stuck
   threshold is flagged ``stuck`` like a mid-turn host.

   **Acceptance:**

   - A background agent whose task notification arrives mid-turn (queued, no
     UserPromptSubmit) no longer counts as in flight on the next scan.
   - A host whose only remaining work is a background agent silent for more
     than 20 minutes is flagged ``stuck``, not idle.
   - A background agent that finishes normally never produces a ``stuck`` flag.
   - A scan with no probe, or a transcript that cannot be read, behaves as
     today (ledger lapse at ``stuck_minutes``), never worse.

   **Architecture sketch:**

   - ``HostProbe`` (ADR-007 D11) gains a transcript-tail read for notification
     ids; the Claude Code host implements it, the scan only joins.
   - ``oracle/scan`` and ``oracle/signals`` (docs/architecture/oracle.md, "the scan,
     the signals and the recommendations", "Mid-turn").
   - The attention ledger from BUG_094 (``oracle/attention``).

   **Risks / open questions:**

   - New probe contract: needs an ADR amending ADR-007's probe/scan contract
     (D11).
   - How far back the tail read must go, and its cost per scan across many
     hosts.
   - The task-notification text format is Claude Code's, not ours; a format
     change silently reverts to today's lapse behaviour.
   - Depends on BUG_094's ledger landing first.

   **Why now / why this tier:**

   Closes the one cost the user accepted in BUG_094's fix; medium value
   (hung background agents are a real but occasional case), M effort with an
   ADR, so tier 5.

   **Related:** :need:`BUG_094 <BUG_094>` (parent), :need:`BUG_096 <BUG_096>`
   (subagent transcripts, same session), ADR-007.

.. idea:: The node adapter reads JS/TS for guideline pattern and boundary checks
   :id: IDEA_038
   :status: proposed
   :priority: medium
   :effort: M
   :tier: 5
   :area: adapters/node, domains/guidelines

   **Context:** IDEA_031 A3 adds the guideline checkers per ecosystem adapter: the pattern kind, which matches within a chosen region of a file (``in: code``, ``comments`` or ``strings``), and the boundary kind (import layering). Both need the adapter to know the language: the pattern kind needs a lexical mask that separates code, comments and string literals, and the boundary kind needs the file's import references. By user ruling (IDEA_031 A3, DN-9), A3 does not build these for the node adapter, so on a node file neither check kind can give a verdict.

   **Proposal:** the node adapter gains a JS/TS lexical mask (line and block comments; single-, double- and template-literal strings, including template substitutions; regular-expression literals where they can be told from division) and relative-import references (``import ... from './x'``, ``export ... from``, ``require('./x')``, dynamic ``import('./x')``), resolved to project paths. With them, a guideline pattern scoped to ``code``, ``comments`` or ``strings`` and a boundary rule both work on ``.js``/``.ts`` files.

   **Acceptance:**

   - A pattern rule with ``in: comments`` matches text inside a ``//`` or ``/* */`` comment in a ``.ts`` file and not the same text inside a string or code.
   - A pattern rule with ``in: strings`` matches inside single-quoted, double-quoted and template-literal strings, and not inside a ``${...}`` substitution's code.
   - A boundary rule forbidding one directory from importing another reports a relative ``import``, ``export ... from``, ``require`` or dynamic ``import()`` that crosses it, naming the file and line.
   - A file the lexer cannot read confidently gets no verdict that reads as a pass; the check reports it as unchecked.

   **Risks / open questions:**

   - Regular-expression literals versus division is the classic JS lexing trap; a regex-first lexer may need a conservative fallback.
   - Whether bare package specifiers or path aliases (tsconfig ``paths``) count as references in v1, or only relative paths.
   - Node coverage is still unreadable until BUG_018; this idea does not depend on it, but a node-heavy adopter will want both.

   **Why now / why this tier:** Low-to-medium value and not scheduled: it matters only to projects that declare node and write guideline rules for it (Heap's web side, per ADR-013). A follow-up to IDEA_031 A3, filed so the gap DN-9 leaves is on record.

   **Related:** IDEA_031 (A3), ADR-013, ADR-012, BUG_018, BUG_103 (the stacked-ecosystem lexer mismatch on the coverage floor).

.. idea:: Draft an agent overlay from a hand-edited installed copy
   :id: IDEA_039
   :status: proposed
   :priority: medium
   :effort: M
   :tier: 5
   :area: cli/agents, agents overlays

   **Context:** Since IDEA_031 A6.1, ``agents install`` skips a hand-edited installed agent copy and names it, with the fix "move the edit into an overlay" (BUG_106 keeps the edit; BUG_051 stopped assets install destroying it). Moving the edit is manual today: the operator has to compare the copy with the current render, find which anchored sections changed, and hand-write a ``version: 1`` overlay against those anchors. That is tedious and easy to get wrong, and downstream adopters carrying edited copies (Heap B4, unblocked by A6) hit it first. By A6 ruling 13 no migration helper shipped in A6; the ruling asked for it to be filed as its own idea.

   **Proposal:** a command that drafts an ``agents.overlays`` file from an edited installed copy, for example ``invariant agents overlay --draft <name>``. It diffs the installed copy against what ``agents install`` would render now (source plus any existing overlay), maps each difference to the section anchors ``invariant agents show <name> --sections`` lists, and prints one operation per changed section in overlay schema v1: ``replace`` for a changed section, ``append`` where the edit only adds text at a section's end, ``insert_after`` (with a new id) for a section the copy adds, and ``description_append`` for a description that only grew. It writes nothing to the manifest. The operator reviews the draft, saves it under the ``docs/agents/<name>.yaml`` convention and names it in ``agents.overlays``; the next install then renders the same text and the copy stops reading as edited.

   **Acceptance:**

   - On an installed copy whose one section was edited, the draft holds exactly one operation, against that section's anchor, and saving it as the agent's overlay makes ``agents install --dry-run`` report the agent current, not edited.
   - An edit that only adds text at a section's end drafts as ``append``; a new heading between two sections drafts as ``insert_after`` with an id that is not already a section.
   - An edit the schema cannot express (a changed ``tools``, ``model`` or other front-matter key, or text outside any anchored section) is named in the output as not carried over, and the command does not report the draft as complete.
   - An agent with no local edits drafts nothing and says so; an agent that is not in ``agents.library`` is refused by name.
   - The draft passes the same overlay validation and privacy screen the install applies before it is shown.

   **Risks / open questions:**

   - Whether the command only prints the draft or can also write it to the conventional path when asked; v1 printing only keeps it out of the manifest and policy files.
   - How an edit to a copy that already has an overlay is drafted: a fresh full overlay, or only the delta on top of the existing one.
   - An edit that straddles a section boundary may only be expressible as two ``replace`` operations; the draft should prefer correctness over the smallest overlay.

   **Why now / why this tier:** Medium value for a narrow group: adopters with edited copies, Heap B4 the first of them, once each per agent. Effort S to M, inside the agents install plan and renderer that A6 built, so tier 5. It is the follow-up A6 ruling 13 asked for.

   **Related:** IDEA_031 (A6 ruling 13; Heap B4), BUG_106, BUG_051, ADR-012 D8 and its A6 as-built notes, cli.md (agents install, agents show), manifest.md ("Agents: the library, the project's own, and overlays").

.. idea:: A project's records can live in a home store, outside its repository
   :id: IDEA_040
   :status: proposed
   :priority: high
   :effort: XL
   :tier: 3
   :area: core/manifest, hosts/claude_code, core/rules, session landing

   **Context:** Adopting invariant commits its files to the repository it governs: the manifest and host assets under ``.claude/``, and docs and backlog under the configured paths. That is wrong for a repository whose content must stay its own. The user wants invariant on four cloned git repositories that are themselves documentation projects (Sphinx, sphinx-needs), with invariant's own documents kept out of them entirely.

   **Proposal:** opt in per project to keep its invariant records in a store under ``~/.invariant``, the way a host keeps per-project data under its own home directory. invariant and Heap keep their in-repo layout.

   - One folder per project under ``~/.invariant`` (which already holds ``projects.json`` and oracle state), keyed on repo identity, because a path key breaks when the repo moves.
   - Each store is its own git repo. A remote is optional, so the user can link and share it or not; publishing stays off by default.
   - An optional pointer in the repo names the docs remote, so the store can be pulled at start. It is the one small file that lands in the repo. A repo that must stay untouched links from the home side instead.
   - Pull at start: an offline or failed pull starts from the local copy with a clear warning; a diverged local copy stops and asks the user, with no automatic merge.

   Out of v1: one home project listing several repos (see the shared-parent option below). It stays a possible later extension.

   **Interview rulings (2026-11-05):** the user wants the design fully configurable and generically designed. The design is split over two ADRs, both being drafted as Proposed: ADR-014 for the store (this entry) and ADR-015 for docs-repo gates (IDEA_041). These rulings supersede the Proposal above where they differ.

   - **Per-type locations.** Each record type gets its own location setting: manifest, architecture docs, backlog, guidelines, memory, reports, sweeps, hooks and agents. In-repo stays the default for every one.
   - **Repo-to-store mapping.** Any mapping: a store can serve one repo or many, and a repo names its store.
   - **Store location.** Any path, with ``~/.invariant`` as the default. Git is the only sync mechanism, with an optional remote. No pluggable backend.
   - **Config levels.** User defaults, overridden by the store, overridden by an optional repo pointer, with provenance reported for each setting. The repo level is never required.
   - **Repo identity.** The normalised origin URL, falling back to a generated ID kept in the repo's local git config (not committed).
   - **Multi-repo sessions.** Designed now so nothing blocks them, built later. The first build is one repo per session plus its store.
   - **Sync.** Configurable. Defaults: pull at start on, push after landing off, warn and continue when offline, stop on divergence.
   - **Landing.** The store lands first, then the repo. A half-landed state is recorded and finished on resume, with no rollback.
   - **Team use.** Before landing, the session's store changes are replayed on top of the remote; a same-file conflict stops the landing and is never auto-resolved.
   - **Privacy.** Every store commit is scanned before push, with the store's own config. A store with no remote may relax this.
   - **Migration.** One command moves a project's records into a store or back, keeping history.
   - **Hosts.** Host-neutral design; each host adapter wires itself. Claude Code first.

   **Options compared and rejected (tested 2026-11-05 on throwaway repos):**

   - Each repo separately, with invariant's paths moved to a hidden ``.invariant`` folder. This works today: doctor passed, session start and isolate worked, the docs folder was untouched; the full pipeline to merge and a real Sphinx build were not run. But ``.claude/`` and ``.invariant/`` still get committed to each repo, and init always scaffolds ``docs/architecture/overview.md`` into the Sphinx folder. Keeping those files out of git fails: the session worktree reports no ``.claude/invariant.yaml``.
   - A shared parent directory with the repos nested inside. Unsupported. Init and doctor succeed in a non-git folder, but session start cannot derive a base branch, and with ``--base`` given, isolate fails with "not a git repository". From inside a nested clone the walk-up finds the parent's manifest and fails the same way. Making the parent a git repo lets sessions run, but the worktree holds none of the clones. It would redefine what a project is across session and gate code.

   **Acceptance:**

   - A project linked from the home side, with no invariant file in its repo, runs a session from start through isolate, gates and merge. Afterwards its git history and working tree hold only the project's own changes, init has scaffolded nothing into its docs folder, and moving the repo to another path finds the same store.
   - The store is a git repo that works with no remote. Linking a remote makes it shareable, and nothing is pushed unless publishing was turned on.
   - With the pointer set, a fresh machine pulls the store before work starts. An offline or failed pull starts from the local copy and warns; a diverged local copy stops and asks, leaving both sides unchanged.
   - A session that changes the project and owes a doc update is held by the docs gate until the store carries that update, and clears it once it does. If only one of the project repo and the store lands, the operator is told which, and can finish the other without redoing the work.
   - Added 2026-11-05 from the interview rulings. The migrate command moves a project's records into a store and back again, and both directions keep each file's git history. Every record type's location can be set on its own, and the effective value of each setting is reported with the level it came from (user, store or repo pointer), with no repo pointer required.
   - Added 2026-11-05. A landing that stops after the store lands and before the repo does is recorded, and the next resume finishes the repo side without redoing or rolling back the store side. With a store remote, a same-file conflict while replaying the session's store changes stops the landing and changes neither side.
   - A project with the in-repo layout behaves as it does today, with each hook firing once. A repo that is not registered sees no output from any user-level hook.

   **Engine work known so far (from reading code, not run, unless noted):**

   - Manifest location is fixed at ``.claude/invariant.yaml``: ``MANIFEST_RELPATH`` in ``core/manifest.py`` and ``Manifest.find_root``, 28 references across core, CLI and oracle. Absolute store paths already resolve (``Manifest.path_for``; tested).
   - Hooks and agents: ``hosts/claude_code.py`` wires ``$CLAUDE_PROJECT_DIR/.claude/hooks`` and installs agents into the repo's ``.claude``. They would move to user-level host settings.
   - Gates that read the branch: ``_docs_updated`` in ``core/rules.py`` looks for changes under the docs root in the session diff, which a home store never appears in. The baseline gate's base-copy reader, also in ``core/rules.py``, reads the manifest from the base branch through ``MANIFEST_RELPATH``.
   - A session changes a project repo and a docs repo, so landing has to handle one succeeding without the other.
   - Docs-only projects: doctor fails on unset ``commands.build`` and ``commands.test``. Moved to IDEA_041 (docs ecosystem adapter, commands derived from CI) on 2026-11-05.

   **Build order agreed:** (1) an ADR (now ADR-014; docs-repo gates split out to ADR-015 and IDEA_041) settling manifest lookup, the docs gate over a home store, pull rules and partial landing, before any code; (2) manifest lookup and host wiring, proven on one of the four repos; (3) move stores and adapt gates; (4) later, multi-repo sessions over one store, which ADR-014 designs for but the first build does not ship.

   **Risks / open questions (for ADR-014):**

   - Resolved 2026-11-05: repo identity is the normalised origin URL, falling back to a generated ID in the repo's local git config. Still for ADR-014: the store folder is any configured path, so a derived default folder name must not carry the URL or a username.
   - Resolved 2026-11-05, moved to IDEA_041: the docs adapter declares coverage not applicable, explicitly. BUG_002 and BUG_064 handled gates inapplicable to a docs-only change, not to a docs-only project.
   - Added at filing, still open after the 2026-11-05 interview (the team-use replay ruling covers landing, not in-session isolation). How sessions isolate inside a store: one store serves parallel sessions, but it is not checked out per worktree, so the isolation gates and the own-file-set check have no tree to judge.
   - Added at filing, still open. Attribution: ADR-011 re-roots a hook only for a path matching a registered live worktree, so a read or edit in a store path names no session. ``docs.read_architecture`` depends on this.
   - Resolved 2026-11-05: every store commit is scanned before push with the store's own config; a store with no remote may relax this. Still for ADR-014: where that scan sits relative to the project's commit and review-boundary screening.
   - Added at filing, still open. Whether ``items.exempt`` (BUG_032) and "Declared stores are writable stores" still hold for a store outside the repo, and whether the oracle's index, keyed by state root, is affected. Runtime state already sits in the git common directory, outside the tree.

   **Why now / why this tier:** The user prioritised this as the next work, to adopt invariant on the four repos. Tier 3 because it redefines where a project's records live across manifest, hosts, gates and landing: effort XL and multi-phase.

   **Related:** ADR-014 (the store, being drafted), ADR-015 and IDEA_041 (docs-repo gates, same adopters), ADR-011 and BUG_040 (cross-project recording), ADR-013 (per-ecosystem commands; the Sphinx build is now IDEA_041's adapter), BUG_032, BUG_072 (exempt stores), installation.md ("Several projects, one machine"), overview.md ("Where things live at rest"), manifest.md ("Declared stores are writable stores", "Resolution order"), state_model.md ("Attribution"), oracle.md (``ProjectIndex``).

.. idea:: Docs repositories get gates: a Sphinx adapter, commands derived from CI
   :id: IDEA_041
   :status: proposed
   :priority: high
   :effort: L
   :tier: 2
   :area: adapters, core/manifest, domains/sweep, core/rules, setup

   **Context:** invariant's gates assume a code project. A documentation repository has no test suite or coverage in that sense: doctor fails on unset ``commands.build`` and ``commands.test`` (IDEA_040, "Engine work known so far"), and no adapter knows what a build or a test means for a Sphinx project. The first adopters are the user's four Sphinx and sphinx-needs documentation repositories, the same ones IDEA_040 moves records out of. Their CI already defines how each one is built and checked, but invariant has no way to read that and has to be told by hand.

   **Proposal:** a docs ecosystem adapter, gate commands proposed from the repo's own CI and kept current, and a time budget that moves a heavy full run to the scheduled sweep. Rulings from the user interview of 2026-11-05; the design is settled in ADR-015 (being drafted, Proposed), alongside ADR-014 for IDEA_040's store.

   - **A docs/Sphinx ecosystem adapter** (ADR-013's adapter contract). Build is a strict ``sphinx-build`` (warnings as errors). Test is a link check plus sphinx-needs validation. Coverage is not applicable, and the adapter says so explicitly, so the coverage gates report "not applicable" rather than failing or passing silently.
   - **CI derivation at setup.** invariant reads the repo's CI definition (GitHub Actions, GitLab CI, a Jenkinsfile and so on), proposes build and test commands, and the user accepts them into config. Gates run only accepted commands, never a derived one the user has not seen.
   - **An update command** reruns the derivation when the pipeline, setup or scripts change, and proposes the differences for acceptance. The user asked for this explicitly. The engine also detects drift between the CI inputs and what was last derived, and says so, so a stale derivation does not go unnoticed.
   - **Heavy tests.** Derivation measures how long the full run takes and judges whether a full run is needed per change. Above a configurable budget, the session gate runs only change-related tests and the full suite moves to the scheduled sweep. This generalises ADR-010's split from coverage to any suite past the budget.

   **Acceptance:**

   - One of the four documentation repos, with the docs ecosystem declared, passes doctor with no hand-written build or test command, and a session through gates and merge runs the strict build, the link check and sphinx-needs validation. Coverage gates report "not applicable, by the adapter", not a pass or a failure.
   - Setup on a repo with a CI definition proposes build and test commands drawn from it, and nothing is written to config or run by a gate until the user accepts. A rejected proposal leaves config unchanged.
   - After a change to the CI definition or a script it calls, invariant reports that the derivation is stale. The update command shows the proposed differences and applies only what the user accepts.
   - A suite whose measured full run exceeds the configured budget gates each session on change-related tests only and runs in full on the sweep; one under the budget keeps running in full per session. The budget and the measured time are both visible in config or status.
   - A warning-producing Sphinx build fails the build gate; a broken link or an invalid need fails the test gate, naming what failed.

   **Architecture sketch:**

   - A new adapter under ``adapters/`` implementing ADR-013's contract: ``default_commands``, ``parse_test_output`` for the link check and needs validation, and an explicit "not applicable" for coverage.
   - A CI reader per CI format feeding a proposal; accepted commands land in the manifest's ``commands.*`` (or ``commands.<eco>.*`` when the docs ecosystem is not the primary).
   - A record of what was last derived and from which inputs, so drift is a comparison, not a re-derivation.
   - Change-related test selection for docs (pages, needs and links touched by the diff), and the budget wired into ADR-010's cadence split.

   **Risks / open questions (for ADR-015):**

   - How generic CI reading can be: CI files call scripts, matrices and reusable actions. The ADR should say what is read, what is only reported as "could not derive", and that a partial derivation is a proposal, never a silent default.
   - What counts as a change-related test for docs: a link check is cheap per page, but sphinx-needs validation may need the whole project. Derivation has to judge this per repo.
   - How "coverage not applicable" sits with the coverage gates and ``sweep.no_full_run`` without becoming a general bypass for code projects.
   - Whether the docs ecosystem is ever a secondary one (``commands.<eco>.*``) or, in the first adopters, always the primary.
   - Measuring the full run's duration needs a full run at setup; on a heavy suite that is itself slow.

   **Why now / why this tier:** part of the same adoption as IDEA_040: the four repos cannot pass doctor or gate a session without it. High value; effort L because it spans a new adapter, CI reading across several formats with an accept-and-update flow, drift detection, and a generalisation of ADR-010. Tier 2 rather than 3 because it extends existing frameworks (adapters, the sweep) rather than redefining where records live.

   **Related:** IDEA_040 (home store; same adopters, sibling ADR-014), ADR-015 (being drafted), ADR-010 (whole-suite evidence moves to a scheduled sweep), ADR-013 (each declared ecosystem runs through its own adapter), IDEA_032, IDEA_036 (whole-suite evidence per ecosystem), manifest.md ("Adapters", "Several ecosystems", "A sweep cadence"), BUG_002, BUG_064 (gates inapplicable to a docs-only change).

.. idea:: Every session-scoped command takes the session as an argument
   :id: IDEA_042
   :status: done
   :priority: high
   :effort: M
   :tier: 1
   :area: cli/main, domains/session, oracle/recommend, hosts/claude_code

   **Context:** reported by the user on 2026-11-06. When a pipeline or an agent has to name its session to an ``invariant`` command, it writes ``INVARIANT_SESSION_ID=<id> invariant <command>``. A Claude Code permission rule matches a command by its prefix, so a line that starts with an environment assignment is not covered by ``Bash(invariant:*)``, and every such call raises a permission prompt. The user wants to grant ``Bash(invariant:*)`` once and stop being asked.

   The prefix form is not an agent habit; the engine recommends it, because several session-scoped commands cannot name their session any other way. Measured from ``--help`` at 5900105:

   - No ``--id``: ``pipeline start``, ``pipeline test``, ``pipeline gates``, ``pipeline advance``, ``pipeline status``, ``check``, and the session-resolving ``guidelines`` subcommands (``verdict``, ``verdicts``, ``baseline``).
   - Has ``--id``: ``pipeline coverage``, ``session plan show``, ``session files add``, ``session status``, ``session isolate``, ``session merge``, ``session teardown``, ``session transition``, ``session sync``, ``session handoff``, ``context``, ``standdown``, ``override``.

   Three places print the prefix form as advice:

   - ``domains/session.py``, the ambiguity message (near line 2165), lists ``INVARIANT_SESSION_ID=<id> invariant <command>`` next to ``invariant <command> --id <id>``.
   - ``oracle/recommend.py``, module docstring (near line 23): where a command cannot take ``--id`` (``pipeline gates``, ``pipeline test``), the recommendation carries ``INVARIANT_SESSION_ID`` in ``env``.
   - ``hosts/claude_code.py``, the ``_UNPROVEN_DISPATCH`` hint (near line 2067), ends "or set INVARIANT_SESSION_ID".

   **Proposal:** every command that resolves a session takes the session as an argument, with one spelling, and the engine recommends only that form.

   - One flag on every session-resolving command. ``--id`` is already the spelling on 13 commands and the one ``cli.md`` documents, so it is the default choice unless the design settles on a global option.
   - The argument outranks the environment variable, and the variable outranks working-directory and host-binding resolution, as today. If both are given and they disagree, the command refuses and names both, rather than picking one silently.
   - ``INVARIANT_SESSION_ID`` stays a working fallback, so existing scripts and pipelines keep their behaviour. No hint, refusal, recommendation or shipped agent instruction prints it as a command line to copy.
   - The ambiguity message, the oracle's recommendations and the dispatch hint name the argument form. The oracle's ``env`` route for ``pipeline gates`` and ``pipeline test`` goes away once both take ``--id``.
   - A test walks the CLI parser and fails when a command that resolves a session lacks the flag, so a new command cannot reopen the gap.

   **Acceptance:**

   - Every command that resolves a session accepts ``--id <id>`` and acts on that session from a directory that would otherwise resolve to another session or to none.
   - No message, oracle recommendation or installed agent text contains a line beginning ``INVARIANT_SESSION_ID=``. A grep of the shipped sources and assets for that string finds only the fallback's own resolution code and its documentation.
   - A command run with ``INVARIANT_SESSION_ID`` exported and no ``--id`` behaves exactly as it does today.
   - ``--id A`` with ``INVARIANT_SESSION_ID=B`` exported is refused with a message naming both, and records nothing.
   - With only ``Bash(invariant:*)`` allowed in Claude Code, a full session (start, isolate, pipeline start, test, gates, advance, merge, teardown) driven by the shipped agents raises no permission prompt for an ``invariant`` call.

   **Architecture sketch:**

   - ``cli/main`` parser: the flag on the missing subcommands, or one shared parent parser or global option that every session-resolving subcommand inherits. A shared parent is what makes the parser-walk test possible.
   - ``domains/session`` resolution already takes an explicit id. The change is passing it through, adding the conflict refusal, and rewriting the ambiguity message.
   - ``oracle/recommend``: steps carry ``--id`` in the argv instead of ``env``. ``oracle-panel.md`` and ``oracle.md`` describe what a step carries.
   - ``hosts/claude_code``: rewrite ``_UNPROVEN_DISPATCH``.
   - ``docs/architecture/cli.md`` (Conventions): state the rule once, that every session-resolving command takes ``--id`` and the variable is a fallback.

   **Risks / open questions:**

   - Flag shape: per-subcommand ``--id``, or a global ``invariant --id <id> <command>``. The global form also matches ``Bash(invariant:*)``, but argparse places globals before the subcommand, and existing ``--id`` flags would then exist in two places.
   - Which commands count as session-resolving. ``check`` and the ``guidelines`` subcommands resolve from the working directory; the audit has to list each one rather than infer from the name.
   - ``_UNPROVEN_DISPATCH`` is about a hook, which per BUG_003 never sees a variable put in front of a tool call. "Set INVARIANT_SESSION_ID" there may already be advice nobody can follow, so the fix may be to drop it rather than translate it into ``--id``.
   - Whether the recommended form should be configurable, given the preference for configurable behaviour with safe defaults. Proposed default: recommend ``--id``, keep honouring the variable, and add no setting unless a host is found that needs the prefix form.
   - ``--id`` is also a work-item and rule id in some commands (``--rule ID``, ``item`` ids). The flag's help text should say "session id" so the two are not confused.

   **Why now / why this tier:** every pipeline step that has to name its session costs the user a permission prompt today, in every session and every project, and the fix is mostly mechanical in one layer (CLI flags, three message sites, one doc section). High value, effort M, so Tier 1. It also removes the hazard BUG_035 had to defend against (an exported variable inherited by a child suite), because an argument is not inherited.

   **Related:** BUG_003 (the variable does not reach a hook; the ambiguity message was reworded to name ``--id`` first), df464a8 (the earlier ``--id`` dead-end fix BUG_003 cites), BUG_035 (an exported ``INVARIANT_SESSION_ID`` leaked into the spawned suite), BUG_028 (dispatch attribution; source of the ``_UNPROVEN_DISPATCH`` hint), BUG_023 (another session-identity route issue, unrelated mechanism), BUG_093 (oracle re-run steps carry discovery values in ``env``; those are not session ids and stay), cli.md ("Conventions", "Session", "Pipeline"), oracle.md.

   .. invariant-history:
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

.. idea:: init assesses the architecture docs and offers to reuse, relocate or write them
   :id: IDEA_043
   :status: proposed
   :priority: high
   :effort: L
   :tier: 2
   :area: cli/main, core/rules, domains, core/manifest

   **Context:** reported by the user on 2026-11-06. They initialised invariant on a project whose ``paths.docs`` directory already existed. As designed (cli.md, ``init``), init leaves an existing directory alone and writes the starter ``overview.md`` only when the directory is absent. So nothing happened. Nothing tells the user whether the docs that are there are enough for the docs-first gate (``docs.read_architecture``) and the design agents to work from. Nothing offers to put invariant's docs somewhere else, and nothing offers to write the missing docs. The user expected init to notice the existing directory and offer a choice, and in general to check that the architecture docs are comprehensive enough, and offer to create them if not.

   Scope is invariant's architecture docs, the ones ``paths.docs`` names and the docs-first gate reads. It is not the project's own README or docs site, except where the user points invariant at existing documentation to adopt (below).

   **Proposal (interview of 2026-11-06):**

   - **A deterministic docs assessment the engine owns.** No model judgement in the check itself, so it is model-agnostic and gives the same verdict on every run. It checks, per area: the docs exist under the resolved docs location, carry the sections the starter defines (layers, component responsibilities, compliance checklist), and name each top-level source area the ecosystem adapter reports. Links between docs also resolve. The result is a list of gaps by area, not a single pass or fail.
   - **It runs in three places:** at init, as a ``doctor`` row, and as its own command (for example ``invariant docs assess``). The doctor row is informational by default, so an undocumented project does not turn doctor red. Its level is configurable like any other rule.
   - **An existing directory gets a choice at init.** When ``paths.docs`` already exists, init offers to reuse it as invariant's docs, or to configure another location for invariant's docs, including an ADR-014 store (``records.docs``). Init only offers and never moves anything on its own.
   - **Gaps get an offer, never a forced write.** Documenting a whole project at the start can be costly, so the user chooses:

     - **All at once:** a docs session, run through the normal pipeline, that writes every missing area.
     - **Incremental:** the gaps are recorded, and each area is documented when a session first touches it. The assessment then shrinks over time.
     - **Not now:** nothing is written. The gap list stays visible in doctor and in the assess command.

   - **Adopting existing documentation.** The user can point invariant at documentation that already exists. Configuration then decides between two modes:

     - **In place:** invariant reads and updates that documentation where it lives.
     - **Copy:** invariant copies it into its own docs location, the repo path or the store, and edits the copy from then on.

   - **Where created docs go:** wherever ``paths.docs`` / ``records.docs`` resolves. That is the repo by default, and the store when ADR-014 configures one. No separate setting.
   - **Safe defaults keep today's behaviour.** A non-interactive init (``--dry-run``, ``--json``, or no terminal) does exactly what it does now, and reports the assessment as text. Every offer is skipped there.

   **Acceptance:**

   - ``init`` on a project whose ``paths.docs`` exists and holds unrelated content asks whether to reuse it or to configure another location. Choosing a store results in ``records.docs`` pointing at it, and nothing in the existing directory changes.
   - ``init`` on a project with no docs and with docs that cover half its source areas reports the gaps by area. In each case it offers all-at-once, incremental and not-now, and writes nothing unless one of the first two is chosen.
   - ``invariant docs assess`` and the doctor row give the same gap list on two runs over an unchanged tree, and the list shrinks when a missing area's doc is added.
   - With the incremental path chosen, a session touching an undocumented area is asked to document that area. An area already documented is not asked again.
   - Pointing invariant at existing documentation in copy mode leaves the original untouched and makes the copy the one the docs-first gate reads. In in-place mode, the gate reads and the agents update the original.
   - A non-interactive ``init`` on any of the above writes the same files as today and prompts for nothing.

   **Architecture sketch:**

   - A docs assessment in ``domains/`` (or the docs rules in ``core/rules``) reading the resolved docs location and the adapter's source areas. It returns per-area gaps, and ``doctor``, ``init`` and the new command all render the same result.
   - ``init``: the reuse-or-relocate offer when ``paths.docs`` exists, and the all-at-once / incremental / not-now offer. It writes ``records.docs``, the adoption mode and the chosen path into the manifest.
   - New settings, with names to settle in design: the adoption mode (in place or copy) with the source path, the onboarding path (all at once, incremental or off), and the assessment row's level. Defaults: off and informational, matching today.
   - Incremental path: the recorded gap list, joined to ``docs.read_architecture`` / ``docs.updated``, so a session touching a listed area owes that area's doc.
   - ``docs/architecture/cli.md`` (``init``, ``doctor``, a new docs command) and ``manifest.md`` (the new keys, ``records.docs``).

   **Risks / open questions:**

   - What counts as "comprehensive" has to stay mechanical: required sections, every source area named, and links resolve. Anything beyond that is a judgement, and the user chose deterministic checks. The required sections and how areas are derived need to be configurable, because the starter's shape is not every project's.
   - Incremental onboarding adds a new obligation to sessions. It must not make a session that touches an undocumented area unmergeable by default. The open question is whether it is a warn or a block, and who decides.
   - In-place adoption of a project's own documentation can mean editing Sphinx or another non-markdown format (IDEA_041's adopters). ``docs.format`` and the docs lock (ADR-014 D5) have to cover that path.
   - Copy mode forks the documentation. Whether invariant ever reports drift between the original and its copy is undecided.
   - The all-at-once path is a large docs session. Its cost and its review lane (docs lane, chore lane) need placing in the methodology.
   - Depends on ADR-014 / IDEA_040 for the store option. The reuse-or-relocate offer can ship before the store does, with only repo locations.

   **Why now / why this tier:** every new adopter meets this on day one, and today the first experience is either silence (directory exists) or a starter template with no sense of what is missing. It also feeds the store adoption IDEA_040 and IDEA_041 are building. High value. Effort L: interactive init offers, a new assessment shared by three entry points, two adoption modes, and an incremental obligation wired into the docs gates. Tier 2 rather than 1 because of the size, and because it leans on ADR-014's store.

   **Related:** :need:`ADR-016 <DEC_ADR_016>` (the design, Proposed), IDEA_040 (home store; ``records.docs``), IDEA_041 (docs repositories; in-place docs may be Sphinx), ADR-014 (records in a store, D5 docs lock), IDEA_004 (uninstall, the other end of adoption), BUG_002 and BUG_064 (gates inapplicable to docs-only changes), cli.md ("Setup": ``init``, ``doctor``), manifest.md, overview.md ("Where things live at rest").

   **Design rulings (user, 2026-11-07, interview rounds 1 and 2 before the ADR):**

   1. **Incremental path.** A session touching a source area with no architecture doc owes that area's doc through a new rule. It warns under ``standard``, and config or the ``strict`` profile can raise it to block. A session is never unmergeable by default.
   2. **Comprehensive, deterministically.** Three checks: required sections, every source area named, links resolve. Each check, the section list, and how areas are derived (the adapter's source roots, globs, or a declared list) are settings. The defaults are the starter ``overview.md``'s shape.
   3. **Copy drift.** At copy time, record the original's hashes. ``doctor`` and ``docs assess`` report originals changed since then, naming the files. Never re-sync unless the user re-runs the adoption.
   4. **All at once.** ``init`` records a deliverable plan with one line per undocumented area. Each area is written and reviewed through the ``docs`` lane, so the cost stays visible and the work is resumable.
   5. **Formats.** Section and link reading go through a per-format reader: markdown now, and rST (Sphinx) alongside ADR-015's sphinx adapter. Any other format is refused for in-place mode with a reason; copy mode still works for it.
   6. **Config levels.** Every new key resolves at the user, store and repo levels, with provenance (ADR-014 D3).
   7. **Increments.**

      1. The assessment, ``invariant docs assess`` and the doctor row, all read-only.
      2. ``init``'s reuse-or-relocate and onboarding offers, with repo and store locations (the store through ADR-014's ``store init`` / ``link``).
      3. The incremental rule.
      4. In-place and copy adoption, with drift reporting.

   8. **Next step.** Draft the ADR from these rulings. Usage was nearly exhausted on 2026-11-07, so it was not drafted then.

.. idea:: invariant init runs an onboarding questionnaire that shows every default and lets the user configure it
   :id: IDEA_044
   :status: proposed
   :priority: high
   :effort: L
   :tier: 2
   :area: cli/main, core/manifest, hosts

   **Asked by the user, 2026-11-07:** "design an onboarding for invariant init. So we take user to a questionnaire, show the defaults, and user can give inputs and configure everything."

   **What:** an interactive onboarding for ``invariant init``. It walks the user through the configuration as a questionnaire: ecosystem and commands, profile, lane, coverage floor, agents, guidelines ruleset or checklist, docs location and onboarding (IDEA_043), records locations and store (ADR-014), and privacy scope. Each question shows the resolved default and its provenance, and the user accepts it or enters a value. The answers are written at the level the user picks (user, store or repo; ADR-014 D3).

   **Safe default:** a non-interactive ``init`` (``--dry-run``, ``--json``, ``--yes``, or no terminal) behaves exactly as today. The questionnaire is skipped, and the defaults it would have shown are printed.

   **Open questions for the design interview:** which questions are asked by default and which are behind an advanced step; whether the questionnaire can be re-run later (for example ``invariant configure``) to change answers; host-neutral rendering (terminal prompts first); and validation, where every answer goes through the same manifest validation as a hand-written value, refused at entry with the reason.

   **Related:** :need:`ADR-017 <DEC_ADR_017>` (the design, Accepted 2026-11-08; the rulings of the interview and of acceptance are recorded there), IDEA_043 (its docs offers become one step of this questionnaire), IDEA_040 / ADR-014 (levels, ``store init``), IDEA_041 / ADR-015 (commands derived from CI could pre-fill answers), IDEA_042.

.. idea:: Architecture and design live only in a strict-building Sphinx-needs project, in every managed project
   :id: IDEA_045
   :status: done
   :priority: high
   :effort: XL
   :tier: 1
   :area: domains/docs, adapters (the ADR-015 sphinx adapter), core/items, cli/main, core/rules

   **Asked by the user, 2026-11-08:** "I want the architecture of the projects with the documentation first rule that we are enforcing in the projects managed by invariant to be done on Sphinx needs. And this is a must. Also, the documentation must stay buildable. And invariant does not create or let Claude create design related documents in anywhere else out of the Sphinx needs documentation. For any projects, the reason behind is I want to leverage from the ID structure of Sphinx needs and having the more traceable documentation that we can remember our decisions in future." Also: "This has been already enforced on the heap project and worked really well." And: "all other invariants development or sessions must finish their jobs and wait for this decision to be implemented. This is very important here."

   **Problem:** today the docs-first rule only says that architecture docs exist and are updated. Where design lives, and in what shape, is left open. Decisions are scattered across Markdown ADR files, backlog item bodies, plans and agent reports. Nothing gives a decision a stable ID, nothing links it to the element it shapes, and nothing checks that the docs still build. A decision made months ago can only be found by searching. The Heap project already keeps its architecture as sphinx-needs (rST, typed element needs, typed links, items as needs) and it has worked well. The user wants that to be the rule for every project invariant manages.

   **What:** every invariant-managed project keeps its architecture and design as a buildable Sphinx project with sphinx-needs, and invariant refuses design content anywhere else. This is a rule, not an option. It amends ADR-016 (the Markdown starter, and docs offers that are "offered, never forced") for the docs format, and builds on ADR-015's sphinx adapter and the existing sphinx_needs items backend.

   **Rulings (user interview, three rounds, 2026-11-08; decided):**

   1.  Scope: every invariant-managed project, invariant itself included. A must, not a configurable option.
   2.  Source format: reStructuredText.
   3.  Buildable is a gate: a strict sphinx-build (``-W``, plus sphinx-needs ``needs_warnings``) runs on every change that touches the docs.
   4.  Existing projects without sphinx-needs docs (invariant, dang, and Heap's gaps) are owed a migration session. Until it lands, design content written elsewhere warns. After it lands, it blocks.
   5.  Need types in v1: decisions (each ADR and each of its D-rulings), architecture elements, requirements/specs, bugs, ideas, guideline rules.
   6.  "Design" means architectural and software design plus UI decisions (screens, flows, design-system choices), all as needs. Visuals and mockups may live in design tools, but only linked from a need, never as the record of a decision.
   7.  Refused homes for design content: standalone ADR files outside the Sphinx project (for example ``docs/adr/*.md``); plans and agent reports (drafts are allowed, but the session cannot merge until its decisions are written as needs); design text in backlog items (items keep problem and status, and rulings move to decision needs linked by ID). Memory files and external docs are not refused homes.
   8.  Need-type schema: an engine baseline (decision, requirement, element types, bug, idea, rule) with fixed prefixes the gates rely on, plus per-project extension types in configuration (like Heap's view/service/model types).
   9.  Traceability gate: the strict build plus configured required-link rules (for example, every decision links to an element it shapes, and every bug links to the element it affects).
   10. Diagrams: PlantUML and Mermaid are both allowed, chosen case by case.

   **Acceptance:**

   - ``invariant init`` on a new project writes a Sphinx + sphinx-needs rST docs project with the baseline need types, and a strict build of it passes. No Markdown architecture starter is offered.
   - A change that breaks the strict docs build (a Sphinx warning, a sphinx-needs warning, a dangling need link) cannot land.
   - A decision need with no link to an element it shapes, or a bug need with no link to the element it affects, fails the traceability gate with the rule named.
   - A session that writes design content to a refused home (a ``docs/adr/*.md`` file, a design ruling in a backlog item body, an undrafted plan or report decision) warns before its project's migration lands and blocks after it. A session holding draft decisions in a plan or report cannot merge until they exist as decision needs.
   - invariant itself is migrated: its ADR-001..ADR-017 and their D-rulings, its architecture docs, its ideas, bugs and guideline rules are needs in a strict-building Sphinx project. Its config reads ``docs.format: sphinx`` and ``items.backend: sphinx_needs``.
   - A project can declare extension need types in configuration (for example view/service/model) and the gates accept them next to the baseline.

   **Architecture sketch:**

   - A new ADR, amending ADR-016 (docs format becomes Sphinx/rST, mandatory) and building on ADR-015 (sphinx adapter) and ADR-012 (guideline rules become needs).
   - Docs domain: the baseline need-type schema with fixed prefixes, configured extension types, required-link rules, and the refused-homes check.
   - The sphinx adapter (ADR-015, not built yet): a strict build command as a gate on docs-touching changes.
   - The sphinx_needs items backend becomes load-bearing for every project: BUG_073 (history never persisted) and any BUG_006 residue must be closed first or as part of it.
   - init and the ADR-017 questionnaire: the docs-format question goes away (it is fixed), and the starter becomes a Sphinx project.
   - Migration: a per-project migration session (invariant, dang, and Heap's gaps: no decision/requirement/rule types, and a Makefile build without ``-W``), with the warn-then-block switch.

   **Reference implementation:** Heap's ``docs/conf.py`` (sphinx_needs + plantuml, rST, element types COMP\_/VIEW\_/SVC\_/MOD\_/UTIL\_/IF\_/DF\_, IDEA\_/BUG\_ as needs, typed links uses/contains/presents/persists_to, ADRs as rST pages under ``docs/architecture/adr``; ``items.backend: sphinx_needs``, ``docs.format: sphinx``).

   **Risks / open questions:**

   - Fixed prefixes for the baseline types versus projects that already use other prefixes (Heap uses COMP\_ and others; is the element baseline one type or several?). For the ADR design interview.
   - How the refused-homes check tells design text in a backlog item body apart from a problem statement. A mechanical check versus a judgement guideline.
   - Migrating invariant's 17 ADRs, Markdown architecture docs and about 160 Markdown items to rST needs is large. A converter may be needed, and BUG_073 must be fixed before items move.
   - The sphinx-build toolchain becomes a dependency of every managed project, including non-Python ones (dang is Flutter).
   - The interaction with ADR-014 stores (records outside the repository) and IDEA_043's reuse/relocate offers.

   **FREEZE — this item blocks all other open invariant work.** User ruling, 2026-11-08: every other invariant development item and session finishes its current job and waits until this is implemented. The live session 4b3af513 (ADR-017 increment 2) may finish its increment. Nothing new starts until this item is done. Blocks: all open IDEA\_ and BUG\_ items in this backlog, including IDEA_041, IDEA_043, IDEA_044 and IDEA_040.

   **Related:** :need:`ADR-018 <DEC_ADR_018>` (the design, accepted 2026-11-09; rulings 1 to 10 of 2026-11-08 and 11 to 19 of 2026-11-09 are recorded there, with the increment plan), IDEA_041 / :need:`ADR-015 <DEC_ADR_015>` (the sphinx adapter this builds on), IDEA_043 / :need:`ADR-016 <DEC_ADR_016>` (amended: Markdown starter and "offers never forced" for the docs format), IDEA_044 / :need:`ADR-017 <DEC_ADR_017>` (the docs-format question becomes fixed), IDEA_040 / :need:`ADR-014 <DEC_ADR_014>` (where the docs project and its needs live), :need:`ADR-012 <DEC_ADR_012>` (guideline rules become needs), BUG_006 and BUG_073 (sphinx_needs backend defects that become load-bearing).

   **Why now / why this tier:** the user ruled it a must for every project and froze all other invariant work until it lands. Highest value: every decision in every managed project gets a stable, linked, buildable home. The rubric alone would put an XL architectural item in Tier 3. It is Tier 1 on the user's explicit ruling.

   .. invariant-history:
      - from: proposed
        to: accepted
        reason: ADR-018 accepted 2026-11-09 (738a20a) records the design and its increment plan
        forced: false
      - from: accepted
        to: in_progress
        reason: ADR-018 increments 1 to 6 landed (46b27eb to 48a1eb3)
        forced: false
      - from: in_progress
        to: done
        reason: 'ADR-018 increment 7: invariant''s own docs are an enforced, strict-building sphinx-needs project; every acceptance bullet met; the freeze lifts (ruling 15). Increment 8 (documentation repositories, stores, Heap and dang) is scheduled in ADR-018''s increment table'
        forced: false

.. idea:: The dart adapter names failing tests and reads a dart test run's scope
   :id: IDEA_046
   :status: proposed
   :priority: low
   :effort: S
   :area: adapters/dart

   **Context:** ADR-019 D7 left three adapter questions at the base answer for ``dart`` and ``flutter``: ``failing_test_ids`` (the sweep files one ``suite:red`` for a red run instead of one key per test), ``test_scope_of`` (only the bare template is a full run; any extra token is "cannot tell" for ``sweep.no_full_run``), and ``expected_failures_are_strict`` (cannot tell).

   **Proposal:** read the reporter's "Failing tests:" list (``<file>: <test name>``) into selectors ``commands.test_one`` takes back, for example ``<file> --plain-name "<name>"``, and only when the template is known to accept that shape. Read ``dart test`` / ``flutter test`` narrowing arguments (a positional path, ``--name``/``-n``, ``--plain-name``/``-N``, ``--tags``/``-t``, ``--exclude-tags``/``-x``) as scoped, and known non-narrowing ones (``--reporter``, ``--coverage-path``, ``--coverage``, ``-j``) as leaving the run full.

   **Acceptance:**

   - A sweep over a red dart run files one key per failing test, each re-runnable alone through ``commands.test_one``.
   - ``sweep.no_full_run`` treats ``dart test --coverage-path=x`` as full and ``dart test test/a_test.dart`` as scoped; anything unread stays "cannot tell".

   **Risks / open questions:**

   - A test name holding quotes or regex characters must survive the shell and ``--plain-name`` exactly.
   - A project whose ``test_one`` runs a script taking package directories (not test files) cannot take a per-test selector; the answer must then be ``None``.

   **Why now / why this tier:** Low: only matters once a Dart project runs a sweep cadence. Filed so ADR-019 D7 is on record.

   **Related:** ADR-019, ADR-010 D5, IDEA_036.

.. idea:: The dart adapter reads Dart for guideline checks, comment-only edits and workspace source areas
   :id: IDEA_047
   :status: proposed
   :priority: low
   :effort: M
   :area: adapters/dart

   **Context:** ADR-019 D7 built no lexical knowledge into the ``dart`` and ``flutter`` adapters beyond the declaration reader ``carries_executable_code`` uses. So on a ``.dart`` file a guideline ``pattern`` rule reading ``code``, ``comments`` or ``strings`` and a ``boundary`` rule cannot be answered, a comment-only ``.dart`` edit is still held to the coverage floor (no ``executable_text``), and ``source_areas`` is the base answer (top-level directories holding ``.dart``), which names ``test`` and ``tool`` as areas in a pub workspace.

   **Proposal:** a Dart region reader (line, doc and nestable block comments; single, double, triple and raw strings; ``${}`` interpolation as code) shared by ``lexical_mask`` and ``executable_text``; ``references`` from ``import``/``export``/``part`` URIs, resolving relative paths and ``package:<own package>/`` to ``lib/``; ``source_areas`` as the ``lib/src/*`` directories of the root package and of each workspace member.

   **Acceptance:**

   - A pattern rule with ``in: comments`` matches inside ``//`` and nested ``/* */`` comments in a ``.dart`` file and not the same text inside a string.
   - A comment-only ``.dart`` edit is set aside by ``coverage.meets_floor`` as comment-only.
   - A boundary rule forbidding ``lib/src/ui`` from importing ``lib/src/data`` reports a ``package:`` or relative import that crosses it.
   - ``source_areas`` of a pub workspace names each member's ``lib/src`` subdirectories and never ``test``.

   **Risks / open questions:**

   - ``${}`` nesting inside strings is the lexing trap; the reader must answer ``None`` rather than guess.
   - Conditional imports (``if (dart.library.io)``) name two targets; whether both count as references.

   **Why now / why this tier:** Low: matters once a Dart project writes guideline rules or hits a comment-only coverage block. Filed so ADR-019 D7 is on record.

   **Related:** ADR-019, ADR-012 D4, IDEA_038 (the same gap for node), BUG_114.
