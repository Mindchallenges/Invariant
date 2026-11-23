.. _adr-030:

ADR-030 — Development never runs on a forbidden model: a window model, a forbidden-model list, and the one audit read a gate may make
=====================================================================================================================================

.. dec:: Development never runs on a forbidden model: a window model, a forbidden-model list, and the one audit read a gate may make
   :id: DEC_ADR_030
   :status: accepted
   :amends: DEC_ADR_012_D1, DEC_ADR_012_D9, DEC_ADR_009_D16
   :affects: COMP_CORE_RULES, COMP_CORE_MANIFEST, COMP_DOMAINS_PIPELINE, COMP_HOSTS_CLAUDE_CODE, COMP_HOSTS_CLAUDE_CODE_LAUNCH, COMP_ORACLE_STARTSESSION, COMP_ORACLE_GATES, COMP_ORACLE_TUI, COMP_ORACLE_WEBACTIONS, COMP_ORACLE_WEBPAGE, COMP_ORACLE_PANEL, COMP_CLI_MAIN

   - **Status:** Accepted (ruling: user, 2026-11-21). The five rulings below
     are the user's, given on 2026-11-21 when :need:`BUG_159 <BUG_159>` was
     filed. D3's matching and unknown-model rules and D2's adapter defaults
     are the orchestrator's decisions, stated here so the user may overrule
     them; the rest are the design pass's readings, each marked as such.
     Built with BUG_159's fix.
   - **Date:** 2026-11-21
   - **Supersedes:** nothing. Amends :need:`DEC_ADR_012_D1` item 2 and
     :need:`DEC_ADR_012_D9` (a gate may now read whether the configured list
     forbids an audited model, D4), and :need:`DEC_ADR_009_D16` (the start
     command line gains ``--model``, D6).

Context
-------

On 2026-11-21 development sessions and their agents ran on Haiku, and the
audit recorded it for the design, review and repro agents of two managed
projects. Nothing in invariant could have stopped it: every window the oracle
opens passed no model and followed the host's global default; ``host.tier_map``
was empty everywhere, so every agent inherited its window's model; and no gate
read the observed model, because :need:`DEC_ADR_012_D9` keeps the audit record
out of every rule's inputs. The cause trail is :need:`BUG_157 <BUG_157>`'s.

Rulings
-------

Given by the user on 2026-11-21 (BUG_159):

.. _adr-030-ruling-1:

**Ruling 1 — never on Haiku.** Development never runs on Haiku. The default
for development is Sonnet or Opus.

.. _adr-030-ruling-2:

**Ruling 2 — the tier map.** ``host.tier_map``: deep to opus, standard to
sonnet, light to sonnet, set at the user level of the invariant configuration
(``~/.invariant/config.yaml`` ``defaults``), so every managed project gets it;
a project may override it. The agents of every managed project are
re-rendered.

.. _adr-030-ruling-3:

**Ruling 3 — the window model.** Oracle windows take a host-side model
setting, default opus. The Claude Code adapter passes it as ``--model`` on
every launch: chained, triage, reproduce and user starts. When the user starts
a session (``oracle start-session``, the TUI, the phone page), the user can
choose the model; otherwise the setting's default applies. The oracle never
imports ``hosts/``; only the CLI joins them.

.. _adr-030-ruling-4:

**Ruling 4 — the forbidden list.** A forbidden-model list, default
``[haiku]``, at the user level. A design, review or repro agent whose observed
model is on it blocks the gate it would satisfy. A launch whose model is
forbidden is refused: a window launch, and an agent dispatch whose asked
model is forbidden where the host hook can see it.

.. _adr-030-ruling-5:

**Ruling 5 — model-agnostic.** Model names live only in configuration and
``hosts/``, never in engine logic (``core/``, ``domains/``, ``oracle/``). The
engine compares configured strings; it does not know model families.

Decisions
---------

.. dec:: Which model development runs on is configuration the host acts on; the engine only compares text
   :id: DEC_ADR_030_D1
   :status: accepted
   :part_of: DEC_ADR_030
   :affects: COMP_CORE_RULES, COMP_HOSTS_CLAUDE_CODE, COMP_HOSTS_CLAUDE_CODE_LAUNCH, COMP_CLI_MAIN

   Rulings 1 to 5 become three guards and one setting, each owned where
   ADR-012 D1 and ADR-027 D9 already put such things:

   - **What a window runs on** is ``host.window_model``, which the Claude Code
     adapter passes as ``--model`` (D2, D6).
   - **What an agent runs on** is ``host.tier_map``, which the adapter already
     renders into each agent's ``model`` line (D8).
   - **What may never run** is ``host.forbidden_models``, checked at three
     moments: a window launch and a subagent dispatch, which are refused
     (D6, D7), and the gates a design, review or repro agent's work would
     satisfy, which fail on what the audit observed (D4, D5).
   - Every model name lives in the adapter's defaults and the configuration.
     ``core/``, ``domains/`` and ``oracle/`` compare configured text with one
     function (D3) and name no model; a test holds them to it.

.. dec:: Two host keys, each null for the host adapter's default
   :id: DEC_ADR_030_D2
   :status: accepted
   :part_of: DEC_ADR_030
   :affects: COMP_CORE_MANIFEST, COMP_HOSTS_CLAUDE_CODE, COMP_CLI_MAIN

   - ``host.window_model``: one line of text, the model a window invariant
     opens runs on. ``host.forbidden_models``: a list of lines of text. Both
     are ``null`` in the engine's defaults, which means the host adapter's
     default, as ``guidelines.instruction_files`` does (ADR-012 D3), so
     ``core/manifest`` holds no model name (ruling 5). ``[]`` is a written
     choice to forbid nothing; only ``null`` takes the default. A wrong shape
     is refused at load, naming the key; what a value says is never checked.
   - **The adapter's defaults (the orchestrator's decision).** Claude Code's
     adapter ships ``WINDOW_MODEL = "opus"`` and
     ``FORBIDDEN_MODELS = ("haiku",)`` in ``hosts/claude_code``, so a machine
     with no user configuration is protected. The user level and the project
     override them in the usual layering (ADR-014 D3). Rulings 3 and 4 put the
     setting at the user level; the adapter's default is that level's value
     until the user writes one.
   - The CLI joins them: ``host_forbidden_models(manifest)`` answers the list
     in force for ``host.kind`` (the configured list, else the adapter's), as
     ``host_instruction_files`` does, and ``_window_model(root, chosen)`` the
     window model and the list for a window opened in ``root``. A host kind
     with no adapter has no defaults: only a configured list forbids anything.
   - :need:`DEC_ADR_012_D9`'s "no host adapter ships a tier-to-model table of
     its own" still holds: the adapter ships a window default and a list, not
     a tier map. Ruling 2's map lives in the user's configuration.

.. dec:: Matching is a case-insensitive substring of configured text; an unknown model is no fact
   :id: DEC_ADR_030_D3
   :status: accepted
   :part_of: DEC_ADR_030
   :affects: COMP_CORE_RULES, COMP_DOMAINS_PIPELINE, COMP_CLI_MAIN

   *The orchestrator's decisions; the user may overrule them.*

   - **Matching.** An entry forbids a model when it is a case-insensitive
     substring of the model's text (``core/rules.forbidden_entry``), so
     ``haiku`` forbids ``claude-haiku-5-5`` without the engine knowing a
     family. The one function answers the gates, the launch and the dispatch
     alike. Text that is empty, or not text, matches nothing.
   - **Unknown model.** An agent is judged on every model the audit observed
     for it in the pipeline, else on the model it was asked to run on, else
     not at all: the gate passes on the role alone, and ``pipeline status``
     names the gap (``models  not checked against the forbidden list ...``,
     from ``domains/pipeline.unchecked_agents``). A host that reports no model
     keeps working.

.. dec:: The audit's one reader on the way into a rule (amends ADR-012 D1 item 2 and D9)
   :id: DEC_ADR_030_D4
   :status: accepted
   :part_of: DEC_ADR_030
   :amends: DEC_ADR_012_D1, DEC_ADR_012_D9
   :affects: COMP_CORE_RULES, COMP_DOMAINS_PIPELINE, COMP_ORACLE_GATES, COMP_CLI_MAIN

   Ruling 4 needs a gate to block on an observed model, where D1 item 2 and D9
   say no rule reads one. The narrowest way through:

   - ``RuleContext`` takes ``forbidden_models``, the list in force, which the
     CLI supplies on every context it builds, to the pipeline domain and to
     the oracle's gate cache (``evaluate_row``'s ``forbidden_for``), as it
     supplies the instruction files. In ``__post_init__``, before
     ``_without_audit`` removes the record, ``_forbidden_runs`` derives
     ``forbidden_agents``: each audited agent whose model the list forbids,
     with the value that matched. The audit record is then removed as before.
   - A rule reads ``forbidden_agents`` and nothing else of the audit. So a
     verdict depends on which model ran only through membership in the
     configured list. Every other value of the record (host, tier, a model
     the list does not forbid, runs) and every verdict stamp still changes no
     outcome.
   - **D1 item 2, as amended:** the model and the host are recorded for audit,
     and no rule reads them, except whether a recorded model is one the
     configured forbidden-model list holds. **D9, as amended:** tiers stay
     hints read by no gate; the audit record still never reaches a gate's
     inputs, and the one fact derived from it does.
   - **The D1 test, as amended.** The scan's ``ALLOWED_READS`` names
     ``_forbidden_runs`` reading the record, and only it. The replays go on
     permuting every identity field, and none of their hostile values is in
     the configured list, so membership is the one thing they hold fixed; a
     test shows a member and a non-member of the list giving different
     outcomes, and two non-members the same one.
   - **Any observed model taints (the design pass's reading).** The audit
     keeps an agent's observed models as a set, not per run, and the forbidden
     run's work is in the pipeline whatever ran after it. So an agent counts
     for nothing once any of its observed models is forbidden, and a re-run
     does not clear it. The way past a run the operator stands by is a
     forced advance with its reason recorded (``invariant pipeline advance
     <state> --force --reason``), which the failure's fix names, because none
     of the three gates fires at a host event, so no ``invariant override``
     grant reaches them; the alternative is abandoning the pipeline.

.. dec:: The gates a forbidden model blocks, in every profile: the role gates, and the red proof for the repro agent
   :id: DEC_ADR_030_D5
   :status: accepted
   :part_of: DEC_ADR_030
   :affects: COMP_CORE_RULES

   - ``agents.design_agent_ran`` and ``agents.review_agent_ran``: a role
     member that ran counts only when ``forbidden_agents`` does not name it.
     When every member that ran is forbidden, the gate fails naming each one
     and its model; with none run, it fails as before.
   - **The repro agent (the design pass's choice, which the orchestrator left
     to it).** The bug-reproducer has no role gate; what it produces is the
     red run, and ``test.new_test_fails`` is the gate that run satisfies, on
     the edges into ``reproduced`` and ``fixing`` (and into ``green`` in the
     ``tdd`` lane, where the design agent writes it). So that gate fails when
     a red run is on record and any agent of the pipeline was observed on a
     forbidden model. The edge into ``reproduced`` was weighed and not
     chosen: its other gate, ``privacy.clean``, says nothing about who did
     the work.
   - **It blocks in every profile (ruling 4; the review's correction).** A
     ruling says a forbidden agent BLOCKS the gate it would satisfy, and every
     shipped profile holds both role gates at ``warn``, and
     ``test.new_test_fails`` at ``warn`` in ``standard`` and ``loose``. So the
     failure carries a floor: ``Verdict.floor``, set to ``block`` by
     ``_forbidden_model_verdict`` alone, and ``evaluate`` delivers a failing
     verdict at the higher of the gate's configured level and its floor
     (``_delivered_level``). A forbidden-model failure therefore stops the
     advance at ``warn`` and at ``ask`` alike, and no acknowledgement passes
     it, since acknowledgements pass only a failure delivered below
     ``block``. The same gate failing for any other reason (no red run, no
     role member ran) keeps its configured level, and a pass or a skip is
     untouched. A dedicated rule at ``block`` was weighed and not chosen: it
     would sit beside the gate it duplicates, on every edge that gate sits on,
     in every shipped machine and profile, to say what the gate already says.
   - **A gate the project turned off stays off.** ``off`` evaluates nothing,
     so there is no failure to raise. Turning a gate off is the user's
     configuration, as ``host.forbidden_models`` itself is (``[]`` forbids
     nothing).

.. dec:: Every window runs on --model, the person may choose it, and a forbidden one is refused (amends ADR-009 D16)
   :id: DEC_ADR_030_D6
   :status: accepted
   :part_of: DEC_ADR_030
   :amends: DEC_ADR_009_D16
   :affects: COMP_HOSTS_CLAUDE_CODE_LAUNCH, COMP_ORACLE_STARTSESSION, COMP_ORACLE_TUI, COMP_ORACLE_WEBACTIONS, COMP_ORACLE_WEBPAGE, COMP_ORACLE_PANEL, COMP_CLI_MAIN

   - ``plan_launch`` takes ``model`` and the forbidden list, and the command
     line becomes ``claude --name <name> --remote-control <name> --model
     <model> [prompt]``. ``require_allowed_model`` refuses a model that is
     blank, holds a space or a line break, starts with ``-``, or is one the
     list forbids, before any plan is built.
   - Every window goes through ``_open_claude_window``, which resolves the
     model at the window's root (``_window_model``): the person's choice,
     else ``host.window_model`` over the user level, else the adapter's
     default. So chained, triage, reproduce and user starts, the dashboard's
     terminal step and ``session handoff`` all carry it, and every preview
     (``--dry-run``, the confirm sheet, the TUI modal) shows the same argv.
   - **The person's choice.** ``oracle start-session --model <m>``; an
     optional model field in the TUI's "New session" modal and on the phone
     page's sheet, blank for the default. The oracle carries the person's
     text and the prepared answer's ``model`` and never names a model:
     ``Starter.prepare`` takes the model as its fifth argument and the CLI
     resolves it. The sheet's nonce and the TUI's Start bind the model the
     confirm showed, so a window never runs on a model the person did not see.
   - The oracle imports no host and the CLI alone joins them, as
     :need:`DEC_ADR_027_D9` requires.

.. dec:: A dispatch whose asked model is forbidden is refused at the hook
   :id: DEC_ADR_030_D7
   :status: accepted
   :part_of: DEC_ADR_030
   :affects: COMP_HOSTS_CLAUDE_CODE

   - Claude Code's ``PreToolUse`` denies a subagent dispatch whose asked
     model, ``tool_input.model``, else the ``model`` line of the agent's
     installed copy (the audit's ``asked``), the list forbids. It runs before
     the rules, in the host, because the asked model is a host fact a rule may
     not read; the reason names the agent, the model, the entry and the keys
     to change. A dispatch whose model the hook cannot see is not refused,
     and a failure to read refuses nothing: the gates still judge what the
     run was observed on.
   - There is no one-shot override: the route is the configuration
     (``host.forbidden_models``, ``host.tier_map``), which is the user's.

.. dec:: Ruling 2 is configuration on the machine, rendered after the merge
   :id: DEC_ADR_030_D8
   :status: accepted
   :part_of: DEC_ADR_030
   :affects: COMP_HOSTS_CLAUDE_CODE

   - The tier map needs no engine change: ``host.tier_map`` already resolves
     from the user level. This machine's ``~/.invariant/config.yaml`` carries
     ``version: 1`` and ``defaults: {host: {tier_map: {deep: opus, standard:
     sonnet, light: sonnet}}}``. The A7 ruling 4 still holds for what ships:
     the shipped map and this repository's own manifest stay empty.
   - This repository's agents are re-rendered by ``invariant agents install``
     after the merge, in the main checkout; each other managed project's are
     a follow-up chore.

As built
--------

BUG_159's fix built D2 to D8 in one change. ``tests/test_bug_159_repro.py``
is the acceptance test, unchanged but for its expected-failure markers;
``tests/test_forbidden_models.py`` covers the matching, the context, the
gates, the status line, the keys, the launch, the dispatch refusal and the
Starter; ``tests/test_conformance_engine.py`` carries D4's amendment to the
scan and the membership test.
