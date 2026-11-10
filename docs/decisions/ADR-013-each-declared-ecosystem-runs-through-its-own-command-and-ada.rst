.. _adr-013:

ADR-013 — Each declared ecosystem runs through its own command and adapter
==========================================================================

.. dec:: Each declared ecosystem runs through its own command and adapter
   :id: DEC_ADR_013
   :status: accepted
   :affects: COMP_ADAPTERS_BASE, COMP_CORE_MANIFEST

   - **Status:** Accepted
   - **Date:** 2026-10-28
   - **Supersedes:** nothing. Amends ``docs/architecture/manifest.md``, "Full
     schema" (``commands``) and "Resolution order", which said both "first is
     primary" and "additional adapters, in listed order, later wins". Fixes
     BUG_097. Leaves IDEA_036 (whole-suite evidence for every ecosystem) open,
     and BUG_018 (node coverage cannot be read) where it was.

Context
-------

A manifest may list several ecosystems (``project.ecosystems: [python, node]``), and classification has always stacked them: ``core/rules``
``_ecosystem_adapters`` asks every declared adapter whether a path is a test
file, source, or inert. The run path never did. ``pipeline test``, ``pipeline coverage``, the sweep and the hook's full-run detector each resolved
``get_adapter(ecosystems[0])`` and the single flat ``commands.*`` block (BUG_097).

Two faces followed, and the bug-reproducer proved both (26a1e6f):

1. **The primary's own run was wrong.** Adapter defaults merged in listed
   order with later wins, so for ``[python, node]`` with no explicit commands,
   ``commands.test`` resolved to ``npm test`` while the python adapter read its
   output. Nothing said so.
2. **The second ecosystem had no route.** ``pipeline test --selector web/src/render.test.ts`` substituted the selector into the one
   ``commands.test_one`` and read the run with the primary's adapter, so a red
   test under the second ecosystem's tree could never be recorded and
   ``test.new_test_fails`` could only be passed with ``--force``.

The manifest document contradicted itself on which is right: the schema
comment says the first ecosystem is primary, and "Resolution order" layer 3
let every later adapter overwrite it.

Decisions
---------

.. dec:: Flat commands.* are the primary's; each further ecosystem has its own block
   :id: DEC_ADR_013_D1
   :status: accepted
   :part_of: DEC_ADR_013
   :affects: COMP_ADAPTERS_BASE, COMP_CORE_MANIFEST

   .. code:: yaml

      project:
        ecosystems: [python, node]   # the first is primary
      commands:
        test: uv run pytest          # flat keys: the primary's commands, as before
        test_one: uv run pytest {selector}
        node:                        # a further ecosystem's commands, same names
          test_one: npx vitest run {selector}
          coverage_one: npx vitest run --coverage {selector}

   A key under ``commands`` whose value is a mapping is a further declared
   ecosystem's command block, holding the same command names as the flat keys.
   Its adapter's defaults fill it; explicit values override it key by key. A
   single-ecosystem manifest has no such block and resolves exactly as before.

   Refused at load, because each would load and never be read (BUG_050's shape):
   a block naming the primary ("its commands are the flat keys"), a block
   naming an ecosystem ``project.ecosystems`` does not declare, a non-mapping
   value for a declared further ecosystem, a command name the block does not
   know, and a non-string, non-null command.

   Rejected: a per-ecosystem block beside ``project.ecosystems`` (it would split
   one concept, "the commands", across two top-level keys), and nesting the
   primary too (``commands.python.*``), which would be a breaking migration of
   every existing manifest for no gain.

.. dec:: A scoped run goes to the ecosystem whose adapter owns its selectors
   :id: DEC_ADR_013_D2
   :status: accepted
   :part_of: DEC_ADR_013
   :affects: COMP_DOMAINS_PIPELINE

   ``domains/pipeline.ecosystem_of`` decides whose run it is:

   - No selector, or a single-ecosystem manifest: the primary. No routing is
     attempted, so a single-ecosystem project cannot be affected by it.
   - Otherwise each selector goes to the one declared adapter that claims it,
     through ``Adapter.owns_selector``. The base answer reads the selector as a
     path and asks the adapter's own ``is_test_file``, the same convention coverage
     classification uses, so the run path and classification cannot disagree.
     Python strips a node id's ``::`` suffix first. A selector no adapter claims
     (``-k engine``, an Xcode ``-only-testing:`` id) goes to the primary.
   - Not from ``source_files``: the base answer there keeps every path, and a
     generic adapter claiming every selector would make every run ambiguous.

   That ecosystem's ``test_one`` / ``coverage_one`` / ``coverage_report`` runs, and
   that ecosystem's adapter joins the selectors, reads the run, judges a strict
   expected failure in the red phase, and parses coverage. Each ``test_runs``
   entry and the ``coverage_run`` record name the ``ecosystem``. No glob table was
   added; ownership is not ambiguous in any shipped adapter pair.

.. dec:: Selectors spanning ecosystems, or claimed twice, are refused
   :id: DEC_ADR_013_D3
   :status: accepted
   :part_of: DEC_ADR_013
   :affects: COMP_CORE_RULES

   A run has one command and one adapter to read it. Selectors landing in two
   ecosystems are refused before anything runs, naming each ecosystem's
   selectors. One run per ecosystem was rejected for BUG_031's reason:
   ``test.all_pass`` reads the LAST run, and a partial run would be taken as the
   state of the whole requested scope. A selector two declared adapters both
   claim (for example ``[swift, swift_xcode]``, one adapter under two names) is
   refused the same way.

.. dec:: The primary wins every key; the resolution order says so
   :id: DEC_ADR_013_D4
   :status: accepted
   :part_of: DEC_ADR_013
   :affects: COMP_ADAPTERS_BASE, COMP_CORE_MANIFEST

   Layer 3 of "Resolution order" is restated. A further adapter's ``commands`` go
   to its own block (D1). Anything else it supplies fills only a key no adapter
   has set yet, so the primary wins every key it names. "Later wins" still holds
   for every layer, because layer 3 never writes over layer 2: it writes disjoint
   keys, or gaps. A repeated ecosystem is declared once.

   The visible change is confined to manifests declaring more than one ecosystem
   with no explicit flat commands. Their flat commands become the primary's
   defaults instead of the last ecosystem's. That is the defect being fixed, not
   a migration. This repository and every single-ecosystem manifest resolve
   byte for byte as before.

.. dec:: Full-run detection is judged per ecosystem; no gate is lowered
   :id: DEC_ADR_013_D5
   :status: accepted
   :part_of: DEC_ADR_013
   :affects: COMP_CORE_RULES

   ``sweep.no_full_run``'s hook surface (``core/rules._full_run_in``) now asks each
   declared ecosystem about its own templates with its own adapter's
   ``test_scope_of``, so ``npm test`` in a ``[python, node]`` project is a full run of
   node's suite, as ``pytest`` is of python's. Only a definite ``"full"`` fires, as
   before. The CLI surface is unchanged: ``pipeline test`` or ``pipeline coverage``
   with no selector is the primary's whole suite, and is asked about first.

   ``test.new_test_fails``, ``test.all_pass`` and ``coverage.meets_floor`` read the
   same evidence with the same logic. What changed is that a second ecosystem
   can now produce it. The state model's gate meanings do not move.

.. dec:: Whole-suite surfaces stay with the primary, and say so
   :id: DEC_ADR_013_D6
   :status: accepted
   :part_of: DEC_ADR_013
   :affects: COMP_CORE_RULES, COMP_CMD_PIPELINE, COMP_CMD_SWEEP

   ``pipeline test`` / ``pipeline coverage`` with no selector and ``invariant sweep run`` run the primary's suite only, read by the primary's adapter. That was
   the documented meaning of the flat commands and is now also what runs. Nothing
   yet demands or records that a further ecosystem's suite is green as a whole,
   and under a sweep cadence a further ecosystem's file is advisory against a
   sweep that does not run it. This was true before (the second ecosystem could
   not be run at all). Closing it means a record per ecosystem and a
   ``test.all_pass`` that reads several. That is a design of its own, filed as
   IDEA_036.

.. dec:: Node coverage becomes reachable, not readable
   :id: DEC_ADR_013_D7
   :status: accepted
   :part_of: DEC_ADR_013
   :affects: COMP_ADAPTERS_NODE, COMP_ADAPTERS_BASE, COMP_CORE_MANIFEST

   A scoped node coverage run now runs ``commands.node.coverage_one``. The node
   adapter still inherits the no-op ``parse_coverage`` (BUG_018), so the run is
   refused as "produced no per-file measurements", which is the existing refusal
   and records nothing. BUG_018 is adjacent and not claimed here.

Consequences
------------

- The consuming project's ``[python, node]`` manifest can record a node red test
  with ``pipeline test --phase red --selector web/...`` once its ``commands.node``
  block names its runner.
- ``invariant config show`` lists ``commands.<ecosystem>.*`` with provenance
  (``adapter:<eco>`` or ``manifest``), through the existing flattening.
- An ecosystem whose selectors are not paths (Xcode's ``-only-testing:`` ids) is
  claimed by nobody, so it routes correctly only as the primary. That limit
  is stated in ``manifest.md``.
