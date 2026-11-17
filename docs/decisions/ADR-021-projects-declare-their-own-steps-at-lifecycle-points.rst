.. _adr-021:

ADR-021 — Projects declare their own steps at lifecycle points
==============================================================

.. dec:: Projects declare their own steps at lifecycle points
   :id: DEC_ADR_021
   :status: accepted
   :amends: DEC_ADR_001
   :affects: COMP_CORE_MANIFEST, COMP_CORE_RULES, COMP_CORE_MACHINES, COMP_DOMAINS_SESSION, COMP_DOMAINS_PIPELINE, COMP_CMD_SESSION, COMP_CMD_RULES, COMP_HOSTS_CLAUDE_CODE

   - **Status:** Accepted (2026-11-15) at the user's direction. Rulings 1
     and 2 were given by the user on 2026-11-15, by interview. Rulings 3 to 6
     were given the same day, in the user's review of this record's first
     draft, by interview. Any choice beyond a ruling is marked
     *recommended*. No question is open. Increment 1 built (2026-11-15),
     "As built: increment 1" below. Increment 2 built (2026-11-15), "As
     built: increment 2" below. Increment 3 built (2026-11-16), "As built:
     increment 3" below; every increment is built.
   - **Date:** 2026-11-15
   - **Supersedes:** nothing. Amends
     :need:`ADR-001 <DEC_ADR_001>` D25 (a permanent route past a built-in
     rule is recorded as a ruling, D7 here). Will amend
     ``docs/architecture/manifest.rst`` ("Levels", "Validation"),
     ``docs/architecture/overview.rst`` (``COMP_CORE_RULES``,
     ``COMP_DOMAINS_SESSION``), ``docs/architecture/state_model.rst``
     (``COMP_MACHINE_SESSION``) and ``docs/architecture/cli.rst`` (a ``step``
     command, ``rules loosen``, ``rules carry-over``, ``session merge``,
     ``session teardown``, ``session handoff``) in the increments that build
     it.

Context
-------

A project can configure invariant only through fixed keys. ``commands`` takes
the names in ``COMMAND_NAMES`` (``build``, ``test``, ``lint``, ``coverage`` and
their variants). The rules are the registered ones, which a profile and
``enforcement.rules`` can only re-level. The engine runs a command only for
``test`` and ``coverage`` (``run_command``, recorded by ``_record_test_run``).
``methodology.extra_machines`` can add a machine, but not a command that runs at
a point the session machine owns.

A project adopting invariant asked for "install the newest build on my phone
after every merge". The engine could not express it: no key names a command
that runs after a merge, and no rule can be declared by the project. It is not
the last such request. A project will also want a lint before landing, a
changelog check before merge, or a notification at handoff. A one-off
``after_merge`` key would answer the first request and leave the rest. So the
mechanism is general: a project declares a step, and the step is a rule.

Such a step often cannot run when it falls due. The phone is unplugged, or the
device is elsewhere. Failing the merge for that would punish the work for the
absence of a device. Ignoring it would lose the obligation. The step therefore
has a third outcome besides pass and fail: *owed*.

Letting a project add rules also raises the other direction. Today a project
can set most built-in rules to ``warn`` or ``off`` through
``enforcement.rules``, and nothing records why. Only the floored rules
(``_floored``: ``docs.mandate_kept`` everywhere, and the mandate's rules in an
enforced project) refuse that. ADR-001 D25 says every route past a gate is
recorded. The permanent route through configuration was the exception.

Rulings
~~~~~~~

1. **Loosening a built-in needs a ruling.** A configuration that sets a
   built-in rule below its level in the ``standard`` profile, or replaces a
   built-in gate, must carry a recorded ruling: who gave it, when, and why.
   Validation refuses the configuration without it. Floored rules stay
   impossible to loosen through configuration. Lowerings that exist today are
   carried over by a one-time ruling recorded at migration. (User,
   2026-11-15.)
2. **A failing block step at an after-point holds the next transition.** The
   action it follows has already happened and cannot be refused. So a
   ``block`` step after a merge that fails holds ``teardown`` and ``handoff``
   until it passes or exits *owed*. An owed step holds nothing; it records a
   debt. (User, 2026-11-15.)
3. **Only the user records a ruling.** It is written by a command that needs
   a terminal confirmation, and an agent's edit of a ruling is refused (D7).
   (User, 2026-11-15.)
4. **One pass clears every debt of its step.** Debts belong to the project
   and outlive the session (D4). (User, 2026-11-15.)
5. **Existing lowerings warn for one release** before they are refused
   (D8). (User, 2026-11-15.)
6. **The loose profile is a loosening**, and needs one ruling keyed
   ``profile`` (D6). (User, 2026-11-15.)

Decisions
---------

.. dec:: A step is declared under steps, by name
   :id: DEC_ADR_021_D1
   :status: accepted
   :part_of: DEC_ADR_021
   :affects: COMP_CORE_MANIFEST

   A new top-level manifest section, ``steps``, maps a step name to its
   declaration:

   .. code-block:: yaml

      steps:
        install_phone:
          at: session.after_merge
          run: tool/install_iphone.sh
          level: block
          owed_exit: 75
          timeout: 1800
          summary: install the newest main on the owner's phone

   - ``at`` (required) is one lifecycle point (D2).
   - ``run`` (required) is a shell command. It is filled from ``vars`` the
     way ``command_for`` fills a command, and it runs through ``run_command``,
     which gains the step's timeout and environment (D2).
   - ``level`` is ``block``, ``warn`` or ``off``. The default is ``block``
     (*recommended*: a project that declares a step means it). A project's
     own step is the project's to re-level, so lowering it needs no ruling.
   - ``owed_exit`` is an exit code that means "cannot run now" (D4). When it
     is unset, no exit code means owed. ``75`` (``EX_TEMPFAIL``) is the
     suggested value. ``0`` is refused, because it already means pass.
   - ``timeout`` is in seconds, 600 by default. A step that runs past its
     timeout has failed, not owed. A hang is a defect, not an absent device.
   - ``summary`` is the one line the rule shows.

   The name must match ``[a-z][a-z0-9_]*``. Unknown keys in a declaration are
   refused (``_refuse_unknown``), so a typo cannot load as a silent nothing
   (BUG_050's shape). ``steps`` resolves through the same levels as every
   other section, by name (*recommended*).

   Rejected: free-form names under ``commands``. ``commands`` is read by
   adapters by name, and a command there has no point at which it runs.

.. dec:: The lifecycle points
   :id: DEC_ADR_021_D2
   :status: accepted
   :part_of: DEC_ADR_021
   :affects: COMP_CORE_MACHINES, COMP_DOMAINS_SESSION, COMP_DOMAINS_PIPELINE

   A point is either *before* an action, where a failing ``block`` step
   refuses the action, or *after* it, where ruling 2 applies.

   +------------------------------+---------+------------------------------------------+----------------------------+
   | ``at``                       | kind    | runs                                     | working directory          |
   +==============================+=========+==========================================+============================+
   | ``pipeline.enter.<state>``   | before  | as an entry gate of ``<state>`` in the   | the session worktree       |
   |                              |         | project's pipeline machine; "commit" is  |                            |
   |                              |         | ``pipeline.enter.committed`` in ``tdd``  |                            |
   +------------------------------+---------+------------------------------------------+----------------------------+
   | ``session.before_merge``     | before  | in ``session merge``, before the         | the session worktree       |
   |                              |         | fast-forward                             |                            |
   +------------------------------+---------+------------------------------------------+----------------------------+
   | ``session.after_merge``      | after   | in ``session merge``, after the          | the main checkout, at the  |
   |                              |         | fast-forward                             | merged commit              |
   +------------------------------+---------+------------------------------------------+----------------------------+
   | ``session.before_teardown``  | before  | in ``session teardown``                  | the session worktree       |
   +------------------------------+---------+------------------------------------------+----------------------------+
   | ``session.before_handoff``   | before  | in ``session handoff``, before the note  | the main checkout          |
   |                              |         | is recorded                              |                            |
   +------------------------------+---------+------------------------------------------+----------------------------+

   A ``pipeline.enter`` step is injected into the loaded machine as an entry
   gate of the state it names, so it works with any lane that has the state.
   This is by state id, beside ``inject_gates``, which attaches by role, and
   ``inject_beside``, which attaches by gate. A pipeline may start in any lane
   (``pipeline start --methodology``), so validation refuses a ``<state>``
   that no lane in ``pipeline_ids`` has, not only one the configured
   ``methodology.pipeline`` lacks.

   A step receives ``INVARIANT_STEP``, ``INVARIANT_STEP_SESSION``,
   ``INVARIANT_WORKTREE`` and, after a merge, ``INVARIANT_MERGED_SHA`` in its
   environment, beside ``child_env()``. The session is not passed as
   ``INVARIANT_SESSION_ID``: ``child_env()`` strips that variable from every
   command it spawns, because a child program that inherits it records
   against the live session (BUG_035).

   Not points, and why: a host's ``stop`` event is a turn ending, not a
   lifecycle point, and running a command there would run it on every turn.
   Session start is left for a later need (*recommended*), because no
   request needs it yet.

.. dec:: A step is a rule, judged from its recorded runs
   :id: DEC_ADR_021_D3
   :status: accepted
   :part_of: DEC_ADR_021
   :affects: COMP_CORE_RULES, COMP_CMD_RULES

   Each declared step registers a rule ``step.<name>`` when the manifest
   loads. Its level is the step's ``level``, and its summary is the step's
   ``summary``. ``rules list`` shows it with its declaration's provenance, and
   ``invariant check`` judges it.

   - **The lifecycle command runs the step, and the rule reads the record.**
     ``session merge``, ``teardown``, ``handoff`` and ``pipeline advance`` run
     the steps due at their point and record each run: command, exit code,
     outcome (``pass``, ``fail``, ``owed``), commit, duration and the tail of
     its output. ``check`` and the hooks never run a step's command. They read
     the record, as ``test.all_pass`` reads ``test_runs``.
   - **A run counts at its commit.** For a before-point, the rule passes
     only on a ``pass`` (or an ``owed``, D4) recorded at the commit being
     judged and over the same uncommitted diff, so a run from before the last
     edit, committed or not, does not count (*recommended*). For
     ``session.after_merge``, the commit is the merged one.
   - **A step can be run by hand.** ``invariant step run <name>`` runs one
     step and records it. That is how a failed after-merge step is re-run
     once it is fixed (ruling 2). ``invariant step list`` shows each step, its
     point, its level, and its last outcome.

   This departs from :need:`ADR-012 <DEC_ADR_012>` ruling 4, which kept the
   rule registry fixed and rejected minting one rule per guideline. The user
   asked for each step to be a rule, so a step can be levelled, overridden
   and listed on its own like any built-in. The departure is bounded: step
   rules live only in the ``step.`` namespace, so they never collide with a
   registered id, and every profile-wide check of the registry (such as each
   rule being graded by every profile) covers the fixed rules only.

.. dec:: Owed is recorded as a debt, never held against the work
   :id: DEC_ADR_021_D4
   :status: accepted
   :part_of: DEC_ADR_021
   :affects: COMP_DOMAINS_SESSION, COMP_CMD_SESSION

   A run that exits with the step's ``owed_exit`` is ``owed``. The rule passes
   on it, and a debt is recorded:

   - **Where:** in the project's records, beside the session records, keyed
     by step name. The debt names the session, the commit and the run's last
     output line. It outlives the session (ruling 4), because the
     obligation is the project's, not the sitting's.
   - **What shows it:** ``session status``, ``step list``, the oracle panel,
     and the handoff. ``session handoff`` appends the open debts to the note,
     so the next session starts knowing them.
   - **What clears it:** a later ``pass`` of the same step clears every open
     debt of that step (ruling 4). "Install the newest build" owed three
     times is discharged by one install of the newest build. A step whose
     debts must be cleared one by one is not asked for yet.
   - **It holds nothing.** No transition waits on a debt. An absent device
     never stops the work. The record keeps the obligation visible instead.

   A step never decides "owed" by itself in any other way. A timeout, a
   missing command or a crash is ``fail``.

.. dec:: A failing after-merge block step holds teardown and handoff
   :id: DEC_ADR_021_D5
   :status: accepted
   :part_of: DEC_ADR_021
   :affects: COMP_MACHINE_SESSION, COMP_DOMAINS_SESSION, COMP_CMD_SESSION

   This carries out ruling 2. The merge stands. A ``block`` step at
   ``session.after_merge`` whose latest run at the merged commit is ``fail``
   is added to the gates of ``torn_down``, and ``session handoff`` refuses
   while it fails. ``session teardown`` today removes the worktree and the
   branch before ``_advance`` evaluates ``torn_down``'s gates, so it asks
   them first through ``pending_gates``, as ``session merge`` does, and a
   refusal removes nothing. ``session.before_teardown`` runs at the same
   moment, in the worktree it is about to remove. The refusal names the
   step, the run's tail and ``invariant step run <name>``. A ``warn`` step's failure is shown and holds
   nothing. The recorded routes of ADR-001 D25 (``override``, ``standdown``)
   pass it as they pass any gate, and they are recorded. ``merged ->
   abandoned`` stays legal and ungated (BUG_037), so abandoning a merged
   session also steps past the hold. The failed run stays on record and
   ``step list`` keeps showing it until a run passes.

.. dec:: A ruling is required for loosening any built-in
   :id: DEC_ADR_021_D6
   :status: accepted
   :part_of: DEC_ADR_021
   :affects: COMP_CORE_MANIFEST, COMP_CORE_RULES

   This carries out ruling 1. A resolved configuration *loosens* a built-in
   when:

   - it sets a registered, non-step rule to a level below that rule's level
     in the shipped ``standard`` profile;
   - it chooses the ``loose`` profile, which loosens every rule it lowers
     (ruling 6: one ruling covers the profile, keyed ``profile``);
   - an ``extra_machines`` entry shadows a shipped machine and so replaces its
     gates (keyed ``machine.<name>``).

   Each loosening needs an entry under ``enforcement.rulings``, keyed by what
   it loosens:

   .. code-block:: yaml

      enforcement:
        rules:
          coverage.meets_floor: "off"
        rulings:
          coverage.meets_floor:
            by: owner
            date: 2026-11-15
            reason: no coverage tool runs on this project yet

   ``validate()`` refuses a loosening with no ruling, and a ruling with no
   ``by``, ``date`` or ``reason``. The refusal names the command that records
   one (D7). A ruling for something not loosened is refused as stale, so the
   record cannot drift from the configuration. Floored rules keep
   ``_validate_floor``: no ruling lowers them (ruling 1). ``strict`` and
   raising a level need nothing.

   The gates injected by role (``DOCS_FIRST_GATES``, ``AGENT_ROLE_GATES`` and
   the rest in ``core/machines``) ride a shadowing machine as they ride a
   shipped one, so a shadow replaces only the gates its own file declares.
   An injected gate is loosened by its level, which the first case covers.
   ``agents.design_agent_ran`` and ``agents.review_agent_ran`` are ``warn``
   in ``standard``, so only ``off`` loosens them.

   ``INVARIANT_DISABLE`` and ``INVARIANT_PROFILE`` are per-invocation routes,
   not configuration. ``validate()`` runs after ``_apply_env`` has merged
   them, so the loosening is judged on the configured levels without them.
   Judged with them, an escape hatch would be refused at load, where the
   manifest's "Validation" has ``INVARIANT_DISABLE`` warn and proceed. Both
   are left as they are, and ``INVARIANT_DISABLE`` is filed as an idea.

.. dec:: A ruling is recorded by the user, not written by the agent
   :id: DEC_ADR_021_D7
   :status: accepted
   :part_of: DEC_ADR_021
   :affects: COMP_CMD_RULES, COMP_HOSTS_CLAUDE_CODE

   This carries out ruling 3. A ruling the agent can type into the manifest
   is the agent ruling for the user. So:

   - ``invariant rules loosen <key> --to <level> --by <who> --reason <why>``
     writes both the level and the ruling, dated today. It asks for
     confirmation on a terminal and refuses without one, so the user runs it
     (in Claude Code, as ``! invariant rules loosen …``).
   - The ``pre-tool-use`` hook refuses an agent's edit that adds or changes
     an ``enforcement.rulings`` entry, naming that command.
   - A configuration change that adds a ruling shows in the diff, and the
     ruling's ``by`` and ``date`` show in ``config show --provenance``.

   This amends ADR-001 D25: the permanent route past a built-in rule stays
   open, and is now recorded as well.

   Rejected: letting the agent write the ruling with a ``--ruling`` flag, as
   ``item transition`` does. An item's ruling closes one item, while a
   loosening ruling lowers a rule for every session after it.

.. dec:: Existing lowerings are carried over once
   :id: DEC_ADR_021_D8
   :status: accepted
   :part_of: DEC_ADR_021
   :affects: COMP_CORE_MANIFEST, COMP_CMD_RULES

   ``invariant rules carry-over`` writes a ruling for each loosening the
   configuration already has, with ``by: carried over``, the date it runs, and
   the comment above the entry as the reason when there is one. It runs under
   D7's confirmation. Until a project runs it, ``validate()`` *warns* rather
   than refuses, for one release (ruling 5), so that upgrading invariant
   does not break a project mid-session. The ``loose`` profile of any
   adopting project is the known case. invariant's own configuration
   loosens nothing: its ``coverage.meets_floor: warn`` is the ``standard``
   level, and its other entries raise a level.

.. dec:: The build order
   :id: DEC_ADR_021_D9
   :status: accepted
   :part_of: DEC_ADR_021
   :affects: COMP_CORE_MANIFEST, COMP_DOMAINS_SESSION

   1. **Steps at before-points and owed.** D1, D2 without
      ``session.after_merge``, D3 and D4: the ``steps`` section, ``step.<name>``
      rules, run records, ``step run`` and ``step list``, debts and their
      display. It proves a block step refusing each before-point, a warn step
      not refusing, an owed exit recording a debt and passing, a later pass
      clearing it, a timeout failing, and an unknown key or state refused.
   2. **After-merge.** ``session.after_merge`` and D5. It proves the merge
      standing with a failing step, teardown and handoff refused, ``step run``
      releasing them, and an owed run releasing them. The first consumer is
      Dang's ``install_phone`` step, set in Dang's own session.
   3. **Rulings.** D6, D7 and D8. It proves each loosening refused without a
      ruling, a stale ruling refused, a floored rule refused with a ruling,
      ``rules loosen`` refusing without a terminal, the hook refusing an
      agent's edit, and ``carry-over`` on a configuration that lowers a rule
      and on one that chooses ``loose``.

As built: increment 1 (2026-11-15)
----------------------------------

D1, D2 without ``session.after_merge``, D3 and D4, as the architecture docs
describe them (``manifest.rst``, "A project's own steps", "Levels" and
"Validation"; ``state_model.rst``, "A project's own steps", the session
domain and the storage layout; ``overview.rst``, ``core/steps``,
``domains/steps`` and the rows they touch; ``cli.rst``, ``step run``,
``step list`` and the lifecycle commands). Where the build settles what the
text left open:

.. dec:: ADR-021 increment 1 as built: where a step's rule lives, what a run is about, and who runs it
   :id: DEC_ADR_021_I1
   :status: accepted
   :part_of: DEC_ADR_021
   :affects: COMP_CORE_MANIFEST, COMP_CORE_RULES, COMP_CORE_MACHINES, COMP_CORE_FSM, COMP_CORE_STEPS, COMP_DOMAINS_STEPS, COMP_DOMAINS_SESSION, COMP_DOMAINS_PIPELINE, COMP_CMD_SESSION, COMP_CMD_RULES, COMP_ORACLE_SCAN, COMP_ORACLE_PANEL

   - **A step's rule is resolved, never registered.** ``core/rules.step_spec``
     builds ``step.<name>`` from the manifest at each lookup, and
     ``evaluate``, ``rule_spec`` and ``rules_for`` consult it. The registry is
     one per process and a process may judge several projects (the oracle
     does), so registering a step at load would show one project's steps in
     another's. The registry and every profile-wide check of it stay the
     fixed rules (D3's bound).
   - **Read in core, run in a domain.** ``core/steps`` holds the
     declarations, the load's refusals, the points, the tree a step judges
     and the record's reads; ``domains/steps`` runs a step and writes the
     record. The rule reads ``core/steps`` (``RULES --> CSTEPS``), not the
     domain this record's diagram drew, so ``core`` imports no domain for it.
   - **The record.** ``steps.json`` under the state root, per step the last
     20 runs and the open debts, written under its own lock. A pass clears
     the debts and the run records how many (``cleared``).
   - **What a run is about.** Its tree's commit and a digest of ``git diff
     HEAD`` and every untracked file git does not ignore
     (``core/git.tree_signature``), taken before and after the run. A run
     counts when the commit matches and either digest does, since a step
     may write its own output; any other edit after it, committed or not,
     leaves no matching run.
   - **Who runs a step.** The command of the edge, before the edge's gates:
     ``pipeline advance`` (``pipeline.enter``), ``session merge`` and
     ``session transition`` into ``merged`` (``before_merge``), ``session
     teardown`` and ``session transition`` into ``torn_down``
     (``before_teardown``), and ``session handoff`` (``before_handoff``,
     judged there, since the handoff is no edge). A forced edge runs none;
     its gates are passed and recorded as any are. Steps run before the
     other gates are asked, so a step can run on an edge another gate
     refuses. ``off`` (by the step's level, ``enforcement.rules`` or
     ``INVARIANT_DISABLE``) does not run.
   - **Teardown asks first, pulled forward from increment 2.** D5 assigns
     asking ``torn_down``'s gates before anything is removed to increment 2.
     This increment builds it now, because ``before_teardown`` needs it: a
     step judged after the worktree is gone could not read its tree. So
     ``session teardown`` asks ``torn_down``'s gates before it removes
     anything, and the advance after the removal reuses those verdicts.
     Increment 2 adds the after-merge hold to the same gates.
     ``teardown --force`` stays about an occupied worktree.
   - **At load.** ``session.after_merge`` is refused, naming increment 2. A
     ``pipeline.enter.<state>`` naming a state no lane has an edge into is
     refused, naming the lanes that start in it: a pipeline starts in its
     initial state and is never advanced into it, so the step would load and
     never run. An initial state another lane enters (``designing``, entered
     in ``spec-first``) stays a point. A
     ``run`` placeholder neither ``vars`` nor ``discover`` fills is refused,
     as a typo would otherwise load and fail every run. ``owed_exit`` is 1 to
     255. A tree that does not exist fails the run.
   - **Debts shown.** ``session status`` prints one ``owed`` line per step;
     ``session handoff`` appends "· owed steps: <step> (<count>, since
     <sha>)" to the note after vetting it, never a run's output; the oracle
     carries ``step_debts`` on every live session row of the project, left
     out when empty so a project with no steps reads as before, and the
     panel names them under "Technical details".
   - **Routes.** ``pipeline ack`` takes a ``warn`` step's rule. ``invariant
     override`` does not, since a step's rule binds no hook event. Teardown
     and handoff get no forced route past a ``block`` step (ruling 2's
     shape): the step holds them until it passes or exits owed, and
     ``invariant step run`` is the route. ``invariant standdown`` remains
     the recorded ADR-001 D25 route, as D5 says.

As built: increment 2 (2026-11-15)
----------------------------------

``session.after_merge`` and D5, as the architecture docs describe them
(``manifest.rst``, "A project's own steps" and "Validation";
``state_model.rst``, the session domain and "A project's own steps";
``overview.rst``, ``core/steps``, ``domains/steps``, ``domains/session``
and the rows they touch; ``cli.rst``, ``session merge``, ``session
teardown``, ``session handoff`` and ``step run``). No arrow moves. Where the
build settles what the text left open:

.. dec:: ADR-021 increment 2 as built: what holds a merged session, and what releases it
   :id: DEC_ADR_021_I2
   :status: accepted
   :part_of: DEC_ADR_021
   :affects: COMP_CORE_STEPS, COMP_CORE_RULES, COMP_CORE_MACHINES, COMP_DOMAINS_STEPS, COMP_DOMAINS_SESSION, COMP_CMD_SESSION

   - **The merged commit is read from git.** It is the session branch's
     tip, which the fast-forward made the base's, and the rule judges it
     only while the session is ``merged`` or ``pushed``. A session that has
     not merged, or whose branch git cannot read, is never held. No field
     is added to the session record.
   - **What holds, and what releases: fail closed.** The newest run at the
     merged commit or at a later commit containing it decides
     (``core/steps.holding_run``): a ``pass`` or ``owed`` releases the hold,
     since one install of the newest build covers every merge before it,
     and a ``fail`` holds, since a newer build that fails carries this
     merge too. No such run holds as well: the parent ruled on 2026-11-15,
     under ADR-001 D25 (every route past a gate is recorded), that a route
     which runs no step must not leave the session unheld. So a forced
     merge, a ``session transition merged`` after a merge made by hand, and
     a session merged before the step was declared are each held until one
     ``invariant step run`` passes or exits owed. A landing whose base had
     already moved past the branch is judged by the run the merge makes at
     the base's newer commit, which contains the merge, so a fail there
     holds. Nothing is exempt. The
     main checkout's uncommitted digest is not read: the commit is the
     merged one (D3).
   - **The merge exits 0.** It stands, so ``session merge`` reports a
     failing after-merge step as a line naming the hold and ``invariant
     step run <name>``, and exits clean; a forced merge names each
     ``block`` step it did not run, and the same fix; ``merge && teardown`` then stops
     at the teardown's refusal, which removes nothing.
   - **Who runs it.** ``session merge`` (and ``session resume``, which
     finishes a half-landed merge through the same landing) runs the
     ``session.after_merge`` steps once the session is ``merged``, in the
     main checkout, at its HEAD: the merged commit, or a later one when the
     base already contained the branch. A forced merge runs none, and
     ``session transition merged`` runs none, since it walks the record and
     runs no fast-forward and D2 places the point after the fast-forward;
     each is then held (fail closed, above), not passed. The edge into
     ``torn_down`` and ``session handoff`` judge the hold and never run the
     step; ``invariant step run`` runs it, in the main checkout, and is
     the one unforced release.
   - **The merged sha.** ``INVARIANT_MERGED_SHA`` is the main checkout's
     commit when the step runs: the merged one at the merge, and the newest
     when ``step run`` runs it again.
   - **Routes.** The routes past the hold are the recorded ones:
     ``invariant step run``; a forced ``session transition torn_down``,
     which passes the hold and records the override naming
     ``step.<name>``; ``invariant
     standdown`` passes it as it passes any gate; ``merged -> abandoned``
     stays ungated (BUG_037). ``session teardown --force`` stays about an
     occupied worktree.
   - **Limit.** The hold reads the step's last 20 runs (increment 1's
     record). Runs at commits not containing the merge do not count, so
     twenty of them push the deciding run out, and the session reads as
     having no run: held, never released, by the record's cap.

As built: increment 3 (2026-11-16)
----------------------------------

D6, D7 and D8, as the architecture docs describe them (``manifest.rst``,
"Loosening a built-in", "Validation" and the floor; ``overview.rst``,
``core/rulings``, ``core/manifest`` and ``core/rules``; ``cli.rst``,
``rules loosen``, ``rules carry-over``, ``rules list``, ``config show``,
``doctor`` and the ``pre-tool-use`` hook). Where the build settles what the
text left open:

.. dec:: ADR-021 increment 3 as built: when the refusal is in force, where a ruling is written, and what refuses an agent's
   :id: DEC_ADR_021_I3
   :status: accepted
   :part_of: DEC_ADR_021
   :affects: COMP_CORE_MANIFEST, COMP_CORE_RULINGS, COMP_CORE_RULES, COMP_CMD_RULES, COMP_HOSTS_CLAUDE_CODE

   - **One module answers what loosens.** ``core/rulings`` holds the
     loosenings, the load's judgement, the plans and the write;
     ``Manifest.validate`` asks it at call time, after the floor. The
     configured levels are kept before ``_apply_env`` (``configured``), so
     ``INVARIANT_PROFILE`` and ``INVARIANT_DISABLE`` are judged by neither the
     loosening nor the staleness of a ruling (D6). A rule a level sets no
     lower than the chosen profile holds it at rides the profile's ruling;
     a level below the profile's needs its own. ``ask`` is below ``block``.
   - **When the refusal is in force (D8's "until a project runs it").** Once
     any level of the project carries ``enforcement.rulings``, an empty
     mapping included. ``rules loosen`` and ``rules carry-over`` write it, and
     ``carry-over`` on a configuration that loosens nothing writes it empty
     for that reason. Until then an unruled loosening loads, held on
     ``Manifest.unruled``; ``doctor``'s ``rulings`` row warns and ``rules
     list`` names it, and nothing is printed at load (a manifest is read on
     every command). The release after this one removes the warning.
   - **Where a ruling is written.** The project's own level: the in-repo
     manifest, or the repository's ``store.repos.<key>`` block of the store's
     ``invariant.yaml``, so a store serving several repositories is not
     loosened for the others. A ruling at the user level is refused at load,
     since the user level is every project's default and a ruling records
     one project's decision; a loosening there is ruled in the project.
   - **In place, or not at all.** Every configuration carries comments, and
     the existing writers refuse a commented file. The ruling commands edit
     the file line by line, keep every other line, keep a replaced level's
     trailing comment, and write only a result that reads back as exactly
     the old settings with the new entries; otherwise they print the entries
     to add by hand. A file changed while the terminal's question was asked
     is not written over. The configuration is loaded again after the write,
     and a refusal, or any other failure, puts the file back. Both commands
     load under
     ``rulings.collecting``, so they repair a configuration the refusal
     stops.
   - **The terminal.** Standard input must be a terminal, as for ``store
     clone``; ``--json`` refuses too, and there is no ``--yes``. Whether a
     host's own shell escape (Claude Code's ``!``) gives the command a
     terminal is the host's; where it does not, the user runs the command in
     a terminal of their own, which the refusal says.
   - **What refuses an agent's edit.** A registered rule,
     ``config.rulings_by_user``, ``block`` in every profile, evaluated at
     ``pre-tool-use`` with the rest, since ``hosts/claude_code`` decides no
     rule. Lowering it is itself a loosening that needs a ruling. It reads
     Edit, Write and MultiEdit on an in-repo manifest in any checkout, the
     project's store ``invariant.yaml`` and the user level; a shell command
     writing the file is not screened, as for every edit rule.
   - **Surfaces.** ``config show --provenance`` prints each ruling's ``by``,
     ``date`` and ``reason`` with its level's label, a YAML date read as its
     ISO text so ``--json`` carries it. ``rules list`` marks each loosened
     rule with its ruling and adds ``loosenings`` under ``--json``.
   - **What upgrading changes.** invariant's own configuration loosens
     nothing and reads as before. A project that lowers a rule or chooses
     ``loose`` gains ``doctor``'s warning row and ``rules list``'s marks, and
     loads as before. Every project gains the ``config.rulings_by_user``
     row in ``rules list``.

Consequences
------------

- A project can enforce a check invariant does not know about, at a point
  invariant owns, with the same record and the same routes as a built-in
  rule.
- A device-bound step (an install, a hardware test) no longer stops the work
  or disappears. It becomes a debt that the next sessions see until a run
  passes.
- A project that lowers a built-in rule says who decided it and why, and an
  agent cannot decide it alone.
- **Cost.** A step's command runs inside the lifecycle command. A slow step
  makes ``session merge`` slow, bounded by its ``timeout``.
- **Limit.** A step's command is trusted the way ``commands.test`` is: the
  manifest is the project's, and invariant runs what it names.
- **Limit.** A lower level can re-level a step that a higher level declared.
  Guarding a project's own steps against the user level is not asked for, and
  is filed.

.. mermaid::

   graph TD
       MANIFEST["core/manifest: steps, enforcement.rulings (NEW)"] --> RULES["core/rules: step.&lt;name&gt; (NEW)"]
       SESSION["domains/session: merge, teardown, handoff run steps (NEW)"] --> STEPS["domains/steps: run, record, debts (NEW)"]
       PIPELINE[domains/pipeline] --> STEPS
       RULES --> STEPS
       STEPS --> RUN["run_command"]
       CLI["cli: step run/list, rules loosen/carry-over (NEW)"] --> STEPS
       HOOK["hosts/claude_code: refuse ruling edits (NEW)"] --> MANIFEST
