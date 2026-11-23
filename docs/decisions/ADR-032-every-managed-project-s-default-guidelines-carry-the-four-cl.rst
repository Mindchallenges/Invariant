.. _adr-032:

ADR-032 — Every managed project's default guidelines carry the four clean-architecture layers, SOLID, a pattern catalogue and docs-first
========================================================================================================================================

.. dec:: Every managed project's default guidelines carry the four clean-architecture layers, SOLID, a pattern catalogue and docs-first
   :id: DEC_ADR_032
   :status: accepted
   :amends: DEC_ADR_012_D2, DEC_ADR_012_D3, DEC_ADR_012_D4, DEC_ADR_012_D5, DEC_ADR_018_D11, DEC_ADR_020_D10, DEC_ADR_021_I3
   :affects: COMP_CORE_GUIDELINES, COMP_DOMAINS_GUIDELINES, COMP_CMD_GUIDELINES, COMP_CORE_VERDICTS, COMP_CORE_RULES, COMP_DATA_SCAFFOLD, COMP_DATA_PROFILES, COMP_DATA_AGENTS, COMP_ADAPTERS_DART, COMP_ADAPTERS_NODE, COMP_ADAPTERS_SWIFT_XCODE, COMP_DOMAINS_PIPELINE, COMP_DOMAINS_QUEUE, COMP_CORE_QUEUE, COMP_CMD_QUEUE, COMP_CMD_SETUP

   - **Status:** Accepted (ruling: user, 2026-11-22). Rulings 1 to 13 are
     the user's: 1 to 8 from two interview rounds, 9 and 10 from two later
     messages, 11 (invariant's layer map) and 12 (the plan approved, BUG_115
     first) from the answer to this record's own interview, and 13 (a
     paused rule and a retired one) from the question increment 2's build
     raised, all on 2026-11-22. The decisions below carry the rulings out;
     no question is open.
   - **Date:** 2026-11-22
   - **Backlog:** IDEA_065. The factual baseline is
     :doc:`/audits/default-guidelines-gap-2026-11`.
   - **Supersedes:** nothing. Amends ADR-012's ownership table
     (:need:`DEC_ADR_012_D2`), its ruleset schema (:need:`DEC_ADR_012_D3`),
     its check kinds (:need:`DEC_ADR_012_D4`) and its verdict rules
     (:need:`DEC_ADR_012_D5`); ADR-018's starter (:need:`DEC_ADR_018_D11`);
     ADR-020's ruleset place (:need:`DEC_ADR_020_D10`, the starter now
     writes more than two rules there); and ADR-021's
     ``config.rulings_by_user`` (:need:`DEC_ADR_021_I3`), whose reach grows
     to the ruleset. Builds on ADR-012 D6 (the ratchet) unchanged.
   - **Related:** IDEA_038 and IDEA_047 (the node and dart adapters reading
     their languages for guideline checks; increment 5 carries their
     ``imports`` half), IDEA_061 (requirements traceability; untouched here).

Context
-------

On 2026-11-22 the user asked that every project invariant manages follows,
by default, the 4-layer clean architecture, the SOLID principles and a
catalogue of 32 design patterns; keeps its architecture, decisions, backlog
and plan as Sphinx documentation in its own repository; and works docs-first,
so that the design documents are always level with or ahead of the code. The
architect and the compliance reviewer at least are held to these rules.

The gap audit (:doc:`/audits/default-guidelines-gap-2026-11`) found:

- The starter ruleset (``src/invariant/data/scaffold/guidelines/ruleset.yaml``)
  holds the two design-record judgement rules and nothing else. No layer,
  SOLID principle or pattern is named in any shipped rule.
- invariant's own ruleset holds five rules. Two are import bans around the
  oracle; nothing maps ``core``, ``domains``, ``adapters``, ``hosts``,
  ``oracle`` and ``cli`` onto named layers.
- ``docs.read_architecture`` proves the docs were read, and ``docs.updated``
  that a structural change touched them somewhere in the same change. Nothing
  proves the design documents were written *before* the code.
- Only the Python adapter answers the ``imports`` engine, so a layer rule in
  a Swift, Dart or TypeScript project can only be judgement.
- The queue (``docs/queue.json``) and the session plans are not
  documentation.
- The other managed projects (Heap, Darvag, and the rest) set no ruleset and
  keep hand-written checklists, so every ``guidelines.*`` gate is N/A there.

The engine pieces this builds on already exist: one machine-readable ruleset
per project with generated checklists and rule needs (ADR-012 D3,
ADR-018 D12), five check kinds (D4), cited judgement verdicts (D5), the
ratchet baseline (D6), phase-scoped injection (D7), and the user-only ruling
record for loosening a built-in rule (ADR-021 D6, D7).

Rulings
-------

The user's answers to the two interview rounds (2026-11-22):

.. _adr-032-ruling-1:

**Ruling 1 — the layers.** The default layer names are Entities, Use Cases,
Interface Adapters, and Frameworks & Drivers (Uncle Bob's). Each project
maps its folders or globs onto them in its ruleset, and the engine derives
"an inner layer never depends on an outer one" from the mapping. The names
are renameable per project.

.. _adr-032-ruling-2:

**Ruling 2 — the catalogue.** The 23 Gang-of-Four patterns plus Repository,
Unit of Work, Dependency Injection, Service Layer, Data Mapper,
Specification, Domain Event, Value Object and Null Object: 32 in all. Shipped
as a data file a project can override.

.. _adr-032-ruling-3:

**Ruling 3 — pattern use.** Every new abstraction the architect introduces
names the catalogue pattern it applies, or states why none fits; the
reviewer judges it with a citation. A judgement rule, warn by default.

.. _adr-032-ruling-4:

**Ruling 4 — docs ahead of code.** A new mechanical gate: in a pipeline, a
structural change's design-doc edit (an architecture doc or a decision need)
is recorded before the first source edit of the build phase. A blocker in
the standard and strict profiles, warn in loose.

.. _adr-032-ruling-5:

**Ruling 5 — where the rules live.** The defaults are copied into each
project's own ruleset, at ``invariant init`` and by a migrate or adopt
command for existing projects; they are not an inherited layer. A later
engine change reaches a project when it runs the command again.

.. _adr-032-ruling-6:

**Ruling 6 — layering outside Python.** ``imports`` engines are added for
Dart and TypeScript (import statements resolved to files). For Swift, whose
imports name modules rather than folders, a project registers its own
fitness test (Heap already has one) as the layer rule's evidence. Judgement
fills any gap until an engine exists.

.. _adr-032-ruling-7:

**Ruling 7 — existing code.** The ratchet baseline (``invariant guidelines
baseline``): existing findings are recorded, new violations block, and the
Boy-Scout rule pays the old ones down. Each project's hand-written checklist
folds into its ruleset during its rollout.

.. _adr-032-ruling-8:

**Ruling 8 — the plan as documentation.** The engine renders the queue and
the open session plans into a generated plan page in each project's Sphinx
tree, built strictly. The JSON stays the machine source. A hand-written
release or roadmap plan stays beside it where one exists.

Given after the interview, the same day:

.. _adr-032-ruling-9:

**Ruling 9 — SOLID in its own right.** "Don't forget the solid principles."
SOLID is part of every project's default ruleset in its own right, not folded
into the layer or pattern rules: five named rules, one per principle, each
with its own statement, rationale and fix guidance, judged with cited
evidence in the design, review and landing phases, by feature-architect at
design and Boy-Scout time and by compliance-reviewer at review. Dependency
inversion is also backed mechanically by the derived layer-direction rule
where an ``imports`` engine exists.

.. _adr-032-ruling-10:

**Ruling 10 — enforced, and only the user makes an exception.** In the
user's words: "It should not be an unbreakable rule, but it should be
enforced, and only the user can decide to do it another way or make an
exception or changing the guidelines which we just agreed to copy the
guidelines to each project so user can configure it and change it or alter
it if he doesn't like it." So the five SOLID rules are blockers. A SOLID
finding stops the pipeline until the code is fixed, the user grants an
exception recorded as a ruling naming the user and the date, or the user
changes the rule in the project's own ruleset (lowering its severity,
turning it off, or rewording it). An agent never waives, marks N/A or
downgrades such a finding on its own judgement. The same holds for every
default rule this record adds: an agent proposes an exception, and the user
rules on it. Rulings 3 and 4 keep their severities.

The user's answers to this record's interview, the same day:

.. _adr-032-ruling-11:

**Ruling 11 — invariant's layer map.** By package (option A): Entities is
``core/``, Use Cases is ``domains/``, Interface Adapters is ``adapters/``
and ``hosts/``, Frameworks & Drivers is ``oracle/`` and ``cli/``. All of
``oracle/`` is Frameworks & Drivers (option O1).

.. _adr-032-ruling-12:

**Ruling 12 — the plan is approved, BUG_115 first.** "Approve, BUG_115
first." The increments are approved as written. BUG_115 (a change that adds
a judgement guideline can never record a verdict on it, since DN-6 refuses
any verdict once the tree's ruleset differs from the pin) is fixed in its
own session before increment 1a, then increments 1a to 7 run in order.

Given during increment 2's build, the same day:

.. _adr-032-ruling-13:

**Ruling 13 — off and retired are two routes.** Asked where the ruling of a
rule turned off is recorded, the user answered: "I think we need both of the
option one and two so if we move it to the retired list for the sake of
explicitly, it will be beneficial and having a severity attribute is also
helpful to define how severe the ruling should be enforced", and on the
follow-up chose two separate routes. ``invariant guidelines loosen <rule-id>
--to off`` keeps the rule in place with ``severity: off`` and a fresh
``ruled``: the rule is paused, never owed or evaluated, and can be raised
again. ``invariant guidelines retire <rule-id>`` removes the rule into a
version-2 top-level ``retired: [{id, ruled}]`` list, gone for good. Both
record the user's ruling, and ``adopt`` adds back neither: a paused rule is
present, and a retired id is skipped.

Decisions
---------

.. dec:: Owners: the ruleset carries the content, core/guidelines derives, domains/guidelines writes, the gates judge
   :id: DEC_ADR_032_D1
   :status: accepted
   :part_of: DEC_ADR_032
   :affects: COMP_CORE_GUIDELINES, COMP_DOMAINS_GUIDELINES, COMP_CMD_GUIDELINES, COMP_CORE_RULES, COMP_CORE_VERDICTS, COMP_DATA_SCAFFOLD

   Every responsibility here already has an owner in
   :doc:`/architecture/overview`; none is forked.

   - **The defaults are content, so they are a ruleset.** ADR-012 D2 gives
     rule content to the project and the schema to invariant. The defaults
     are the starter ruleset invariant ships
     (``data/scaffold/guidelines/ruleset.yaml``) plus a pattern catalogue
     beside it (``data/scaffold/guidelines/patterns.yaml``,
     :need:`DEC_ADR_032_D5`). They are the one copy of the defaults: the
     starter at ``init`` and the adopt command (:need:`DEC_ADR_032_D8`) both
     read them, and nothing else holds a second.
   - **The schema and the derivation are ``core/guidelines``'s.** The layer
     map's validation and the boundary rules derived from it
     (:need:`DEC_ADR_032_D2`), the ``owner`` and ``ruled`` keys
     (:need:`DEC_ADR_032_D7`) and the catalogue's validation are pure
     functions over the parsed ruleset, beside the five check kinds.
   - **Writing a project's ruleset is ``domains/guidelines``'.** The adopt
     merge, the user's exception and its recording (D7, D8) live there;
     ``domains/docs``' starter calls it for a new project, so ``init`` and
     ``adopt`` share one writer.
   - **The gates are ``core/rules``'.** ``docs.ahead_of_code``
     (:need:`DEC_ADR_032_D6`), ``guidelines.blocking_verdicts`` and
     ``guidelines.user_rules_ruled`` (D7) are registered rules; the
     submission refusals are ``core/verdicts``' predicates.
   - **The engines are the adapters'.** Dart and TypeScript answer
     ``references`` (``adapters/base``' existing seam); the ``fitness``
     engine reads recorded test runs and needs no adapter
     (:need:`DEC_ADR_032_D3`).
   - **The plan page is the queue's.** ``core/queue`` renders it,
     ``domains/queue`` writes it (:need:`DEC_ADR_032_D11`).
   - **Arrows.** ``DOCSD --> GDOM`` is added (the starter calls the adopt
     writer). Every other arrow these decisions use exists. The overview's
     Layers diagram gains it in increment 2.

.. dec:: Ruleset version 2: a layer map, and one direction rule derived per layer
   :id: DEC_ADR_032_D2
   :status: accepted
   :part_of: DEC_ADR_032
   :amends: DEC_ADR_012_D3
   :affects: COMP_CORE_GUIDELINES, COMP_CMD_GUIDELINES

   Ruling :ref:`1 <adr-032-ruling-1>`.

   - **The version.** The ruleset schema becomes version 2. A version-1
     ruleset loads as before, with no layers. A build that reads only
     version 1 refuses a version-2 file by its version, which says why,
     rather than by an unknown key.
   - **Paused and retired (ruling** :ref:`13 <adr-032-ruling-13>`\ **).**
     Version 2 also reads ``severity: off`` on a rule or the map (paused:
     kept, never evaluated) and a top-level ``retired: [{id, ruled}]`` list
     of rules the user removed (:need:`DEC_ADR_032_D7`).
   - **The map.** An optional top-level ``layers`` block lists the layers
     innermost first. Each has an ``id``, a ``title`` (renameable) and
     ``paths`` (globs, project-relative, as ``applies_to``), and may carry
     ``exclude`` globs. The block may set ``severity`` (default
     ``blocker``), ``phases`` (default design, review and landing),
     ``evidence`` (:need:`DEC_ADR_032_D3`) and per-layer ``exceptions``. The
     starter's block names the four default layers with empty ``paths``.

     .. code-block:: yaml

        layers:
          severity: blocker
          map:
            - id: entities
              title: Entities
              paths: ["src/app/domain/**"]
            - id: use-cases
              title: Use Cases
              paths: ["src/app/usecases/**"]
            - id: interface-adapters
              title: Interface Adapters
              paths: ["src/app/adapters/**"]
            - id: frameworks-drivers
              title: Frameworks & Drivers
              paths: ["src/app/cli/**", "src/app/web/**"]

   - **The derivation.** At load, ``core/guidelines`` derives one rule per
     layer that has an outer layer: id ``layers.<layer id>-depends-inward``,
     section ``layers``, applies to that layer's ``paths`` minus its
     ``exclude``, ``check: {kind: boundary, engine: <the layer's engine>,
     forbid: <every outer layer's paths>}``. The statement and the fix
     guidance are written from the titles ("Nothing in Use Cases imports
     Interface Adapters or Frameworks & Drivers; to fix, define the shape
     Use Cases needs as an interface in Use Cases, implement it outward, and
     inject it"). A derived rule is a rule like any other: rendered into the
     checklist and the rule needs with a "derived from the layer map" note,
     selected by ``guidelines show``, baselined, overridable only as D7
     says.
   - **A file in two layers** is a finding of the derived rules, unanswered
     and so failing, naming both layers; the fix is an ``exclude``. A source
     file in no layer is named by ``guidelines show --layers`` and
     ``doctor``, never a finding: an unmapped folder (Heap's ``Services/``
     today) is the project's to place.
   - **A layer with no ``paths``** is valid (the starter's state) and
     derives nothing for it; ``guidelines.ruleset_valid`` says "layer
     <title> maps no files" as a note, so a new project is told to map its
     folders and is not blocked by an empty map.
   - **Not derived:** rules within one layer, or between two layers both
     inner of a third. A project that wants a finer rule writes it as a
     boundary rule of its own, as invariant's two oracle bans are today.

.. dec:: A layer's evidence: the imports engine, a registered fitness test, or judgement
   :id: DEC_ADR_032_D3
   :status: accepted
   :part_of: DEC_ADR_032
   :amends: DEC_ADR_012_D4
   :affects: COMP_CORE_GUIDELINES, COMP_CORE_RULES, COMP_ADAPTERS_DART, COMP_ADAPTERS_NODE, COMP_ADAPTERS_SWIFT_XCODE, COMP_ADAPTERS_BASE

   Ruling :ref:`6 <adr-032-ruling-6>`, and the mechanical half of ruling
   :ref:`9 <adr-032-ruling-9>`.

   - **``imports``, where an adapter answers it.** The derived rule uses
     ``engine: imports`` by default. ``adapters/dart`` and ``adapters/node``
     gain ``references`` (``import``/``export``/``part`` directives for Dart,
     ``import``/``export``/``require``/dynamic ``import()`` with a string
     literal for TypeScript and JavaScript), resolved to project files
     through ``package:`` names and the package's ``lib/`` for Dart, and
     ``tsconfig.json``'s ``baseUrl``/``paths`` and relative specifiers for
     TypeScript. An import that resolves outside the project is outside the
     kind (DN-A2); one that cannot be resolved makes the file unanswerable,
     never clean. Each lists ``imports`` in ``guideline_engines``.
   - **``fitness``, a project's own test.** A new boundary engine,
     ``engine: fitness``, with a ``test`` selector in place of ``forbid``.
     It needs no adapter: it reads the pipeline's recorded runs
     (``test_runs``) and answers from the latest run that included the
     selector over the final tree: passed is clean for every file the rule
     applies to, failed is one finding naming the test, no such run is
     unanswerable and fails with the ``invariant pipeline test --selector``
     line that records one. Swift projects name their layering fitness test
     here (Heap's ``ArchLayeringFitnessTests``); any ecosystem may. Its
     findings are not baselined by invariant: the test keeps its own
     ratchet.
   - **``judgement``, until either exists.** The layer block's ``evidence:
     judgement`` derives judgement rules (``evidence: citation``) with the
     same statements. ``guidelines.ruleset_valid`` already refuses an
     ``imports`` rule no configured adapter answers and names judgement as
     the alternative; it now names ``fitness`` too.
   - **Dependency inversion, mechanically.** Where the derived rules run on
     ``imports`` or ``fitness``, they are the mechanical check of the part of
     dependency inversion an import graph can see: no inner layer names an
     outer one. ``solid.dependency-inversion`` (:need:`DEC_ADR_032_D4`) stays
     a judgement rule for what imports cannot see: a high-level policy that
     constructs its own concrete collaborators within a layer, a
     service-locator lookup, an interface owned by the implementer instead of
     the user.

.. dec:: SOLID: five enforced judgement rules, one per principle
   :id: DEC_ADR_032_D4
   :status: accepted
   :part_of: DEC_ADR_032
   :affects: COMP_DATA_SCAFFOLD, COMP_CORE_GUIDELINES, COMP_CORE_VERDICTS

   Rulings :ref:`9 <adr-032-ruling-9>` and :ref:`10 <adr-032-ruling-10>`.
   Five rules in a ``solid`` section of the starter, copied into invariant's
   own ruleset in increment 1. Each:

   - ``severity: blocker``, ``owner: user`` (D7), ``phases: [design, review,
     landing]``, ``applies_to`` the project's source globs (the starter
     writes the adapter's source globs; ``adopt`` asks),
     ``check: {kind: judgement, evidence: citation, scope: diff}``.
   - **N/A only on facts the engine checks.** The only
     ``not_applicable_when`` is the mechanical ``no_executable_change`` (a
     rename, a comment or a format change). No claim-form condition: an
     agent cannot answer one of these rules N/A (D7).

   The five (statement, then the rationale and fix guidance the rule
   carries):

   - ``solid.single-responsibility`` — **Single responsibility.** Each module,
     type and function a change adds or edits has one reason to change: it
     answers to one owner, named in the architecture docs. *Fix:* split the
     unit along its owners and name each part's owner in the docs; a unit
     doing two things for two callers is two units.
   - ``solid.open-closed`` — **Open/closed.** New behaviour is added by
     extension (a new implementation of an existing seam, a new data entry,
     a new strategy), not by growing a conditional over kinds inside code
     that already works. *Fix:* where a change adds a branch on a type or
     kind tag, introduce or reuse the seam and add the new case as its
     implementation.
   - ``solid.liskov-substitution`` — **Liskov substitution.** Every
     implementation of an interface or subclass honours its contract: it
     accepts what the base accepts, promises what it promises, and raises
     nothing the base does not declare. No caller checks which
     implementation it holds. *Fix:* where an implementation narrows input,
     weakens output or is type-checked by a caller, change the contract in
     the docs or stop implementing it.
   - ``solid.interface-segregation`` — **Interface segregation.** A client
     depends only on the operations it uses. An interface a change adds or
     widens is shaped by its callers, and no implementation is forced to
     stub operations it cannot honour. *Fix:* split the interface by client
     role; let each caller depend on its own role.
   - ``solid.dependency-inversion`` — **Dependency inversion.** Policy
     depends on abstractions it owns; details implement them and are
     injected at the composition root. No high-level unit constructs or
     looks up a concrete detail. *Fix:* define the abstraction where the
     policy lives, implement it outward, and inject it from the entry point.
     The import-visible half is the derived layer rules' (D3).

   The rationale each rule carries says what it protects (change isolated to
   one owner; working code left untouched; substitutable seams; narrow
   coupling; testable policy) and cites the overview's component
   responsibilities as the place a unit's owner is named.

.. dec:: The pattern catalogue is a data file; a new abstraction names its pattern or says why none fits
   :id: DEC_ADR_032_D5
   :status: accepted
   :part_of: DEC_ADR_032
   :affects: COMP_DATA_SCAFFOLD, COMP_CORE_GUIDELINES, COMP_CMD_GUIDELINES

   Rulings :ref:`2 <adr-032-ruling-2>` and :ref:`3 <adr-032-ruling-3>`.

   - **The file.** ``data/scaffold/guidelines/patterns.yaml``: version 1,
     32 entries, each ``id`` (``gof.abstract-factory``,
     ``enterprise.repository``, ...), ``name``, ``family`` (creational,
     structural, behavioural for the 23 Gang-of-Four patterns; enterprise or
     domain for Repository, Unit of Work, Dependency Injection, Service
     Layer, Data Mapper, Specification, Domain Event, Value Object, Null
     Object), a one-paragraph ``intent`` written for this file, and
     ``use_when`` / ``avoid_when`` lines. No text is copied from a book.
   - **Copied, then the project's.** As the rules are (ruling 5), the file
     is copied beside the project's ruleset, and the ruleset names it
     (top-level ``catalogue: patterns.yaml``, version 2). The project may add,
     drop or reword entries; ``guidelines.ruleset_valid`` validates the
     file's shape and refuses a duplicate id. A project with no
     ``catalogue`` key has none, and the pattern rule then says "no
     catalogue configured" instead of inventing one.
   - **Rendered.** ``invariant guidelines render`` writes the catalogue as a
     generated page beside the rules page (``patterns.rst``, managed marker,
     one anchor per id), checked by ``guidelines.generated_current`` as the
     rules page is. A verdict cites ``patterns.rst#<id>``.
   - **The rule.** ``patterns.named-or-declined``: "Every new abstraction a
     change introduces (an interface, protocol, base class, or a type that
     mediates between two others) names in its design the catalogue pattern
     it applies, or states why none fits." ``severity: warn``, ``owner:
     user``, phases design and review, judgement with citation, scope diff;
     its one N/A is mechanical (``no_added_match`` on the adapter's
     declaration forms where a pattern rule can express them, else none).
     The architect writes the naming in the design; the reviewer cites it.

.. dec:: docs.ahead_of_code: the design docs are recorded as changed when the design state is left
   :id: DEC_ADR_032_D6
   :status: accepted
   :part_of: DEC_ADR_032
   :affects: COMP_CORE_RULES, COMP_DOMAINS_PIPELINE, COMP_CMD_PIPELINE, COMP_DATA_PROFILES

   Ruling :ref:`4 <adr-032-ruling-4>`.

   - **The record, taken at the boundary.** When a pipeline advances out of
     a state with the ``design`` role into one without it, the advance
     records ``design_snapshot`` evidence: which design documents differ
     from the merge base at that moment (paths and content digests), and
     whether any source file does. Design documents are the files under
     ``paths.docs`` and the pages holding a need of the decision role
     (``core/needs``); under a store docs lock, the store leg as
     ``docs.updated`` reads it, where the doc is already committed by design's
     end. The snapshot is the tree's, taken by the engine at the advance, so
     it needs no host hook and is the same for every host.
   - **The judgement, where structure is known.** ``docs.ahead_of_code`` is a
     registered rule evaluated with ``docs.updated``, on entering the review
     role's state. It reuses ``docs.updated``'s one reading of "structural
     change" (the same function, not a second copy). Not structural: clean.
     Structural and the snapshot shows at least one design document changed:
     clean, naming them. Structural and the snapshot shows none: fail, "the
     design documents were not changed before the build began". No snapshot
     (a methodology with no design-role state, such as ``chore``, or a
     pipeline started before this build): N/A, saying which.
   - **Said early, where it is still cheap.** The advance out of design
     prints the snapshot, and with none changed says that a structural
     change will fail ``docs.ahead_of_code`` at review: write the doc now.
   - **Levels.** ``block`` in ``standard`` and ``strict``, ``warn`` in
     ``loose``. Lowering it is a loosening that needs the user's ruling
     (ADR-021 D6), as for every registered rule.
   - **Known limit.** Source edited during the design state itself is not
     judged here; the reviewer's ``docs.design-before-code`` judgement rule
     covers it, and covers whether the doc edit actually describes the
     change.
   - **The judgement rule beside it.** ``docs.design-before-code`` joins
     the starter's ``docs`` section: "Read the architecture docs, update the
     design documents (diagram, component responsibilities, decision needs)
     to describe the change, then implement; the design documents stay level
     with or ahead of the code." ``severity: warn``, ``owner: user``, phases
     design and review, judgement with citation, scope diff, no N/A.

.. dec:: Only the user excepts, loosens or rewords a default rule; an agent proposes
   :id: DEC_ADR_032_D7
   :status: accepted
   :part_of: DEC_ADR_032
   :amends: DEC_ADR_012_D5, DEC_ADR_021_I3
   :affects: COMP_CORE_GUIDELINES, COMP_DOMAINS_GUIDELINES, COMP_CMD_GUIDELINES, COMP_CORE_VERDICTS, COMP_CORE_RULES, COMP_HOSTS_CLAUDE_CODE, COMP_DATA_PROFILES

   Ruling :ref:`10 <adr-032-ruling-10>`. The ruling's record has the shape
   ADR-021 D6 already gives a loosening (``by``, ``date``, ``reason``),
   recorded by the user at a terminal as ADR-021 D7 records one.

   - **``owner: user``.** A rule key in version 2, default ``project``.
     Every rule this record adds carries ``owner: user``, the layer map
     included. It marks the rules whose exceptions and changes only the user
     makes.
   - **The three routes past a finding**, and no fourth:

     1. *Fix the code.*
     2. *A user-ruled exception.* ``invariant guidelines except <rule-id>
        <path> [--fingerprint <fp>] --by <who> --reason <why>`` writes an
        ``exceptions`` entry on the rule with ``ruled: {by, date, reason}``,
        dated today. Like ``rules loosen``, it asks for confirmation on a
        terminal and refuses without one (no ``--yes``, ``--json`` refuses),
        so the user runs it. An exception on an ``owner: user`` rule without
        ``ruled`` is refused at load.
     3. *The user changes the rule* in the project's ruleset: lowers its
        severity, turns it off, narrows it or rewords it. The changed rule
        carries a fresh ``ruled: {by, date, reason}``. ``invariant
        guidelines loosen <rule-id> --to warn|off`` writes the common case
        at a terminal; a hand edit by the user is equally valid. Per ruling
        :ref:`13 <adr-032-ruling-13>`, ``off`` is a severity (version 2): the
        rule stays, paused, never owed or evaluated, and may be raised
        again; ``invariant guidelines retire <rule-id>`` removes it into the
        ruleset's ``retired: [{id, ruled}]`` list instead, for good. The
        ruling of a retired rule is its ``retired`` entry.

   - **What an agent cannot do, mechanically.**

     - *N/A.* ``core/verdicts`` refuses a submitted ``na`` verdict on an
       ``owner: user`` rule; only the engine's mechanical N/A conditions
       answer one N/A. A claim-form ``not_applicable_when`` on such a rule is
       refused at load.
     - *A waiver by grant.* ``invariant override <rule-id>`` naming an
       ``owner: user`` guideline needs a terminal confirmation with
       ``--by``, as ``rules loosen`` does, and is refused without one.
     - *A waiver by edit.* ``config.rulings_by_user`` (ADR-021 I3) extends
       its reach to the configured ruleset and catalogue: it refuses an
       agent's Edit, Write or MultiEdit that adds or changes a ``ruled``
       entry. Its level and its "block in every profile" are unchanged.
     - *A silent change.* ``guidelines.user_rules_ruled``, a new registered
       rule at ``block`` in every profile, compares the ruleset with the
       base branch's copy (as ``guidelines.baseline_only_shrinks`` does): an
       ``owner: user`` rule or the layer map removed, lowered, narrowed,
       reworded, or given an exception, without a ``ruled`` entry that is new
       in the same change, fails; a rule removed is ruled by a ``retired``
       entry for it that is new in the change (ruling 13). Raising a severity or widening a rule needs
       nothing (as ADR-021 D6 treats raising a level).

   - **A blocker judgement finding blocks.** ``guidelines.verdicts_complete``
     stays ``warn`` in ``standard``, so a blocker's ``fail`` would only warn
     there. ``guidelines.blocking_verdicts``, a new registered rule (``block``
     in ``standard`` and ``strict``, ``warn`` in ``loose``), fails on a
     missing, stale or ``fail`` verdict for a judgement rule at ``blocker``
     severity that the change owes, unless a user-ruled exception covers the
     path. It reads ``core/verdicts.owed`` and ``read_standings``, so it and
     ``verdicts_complete`` cannot disagree on what is owed.
   - **What an agent does instead.** It proposes: the report names the
     finding, the exception it suggests and why, and the exact ``invariant
     guidelines except`` line for the user to run. The agent templates say
     so (:need:`DEC_ADR_032_D10`).
   - **Per project, configurable.** Every severity here is the default
     copied into the project's ruleset; the project's user changes it
     through route 3. Nothing in the engine pins a project to the defaults.

.. dec:: The defaults are copied: the starter at init, guidelines adopt for an existing project, again on each engine change
   :id: DEC_ADR_032_D8
   :status: accepted
   :part_of: DEC_ADR_032
   :amends: DEC_ADR_018_D11, DEC_ADR_020_D10
   :affects: COMP_DOMAINS_GUIDELINES, COMP_CMD_GUIDELINES, COMP_DOMAINS_DOCS, COMP_CMD_SETUP, COMP_DATA_SCAFFOLD

   Ruling :ref:`5 <adr-032-ruling-5>`.

   - **``origin``.** Each default rule carries ``origin: {set: defaults,
     version: <n>, digest: <12 hex>}`` (version 2 key): the defaults'
     version it was copied from, and the digest of the rule as shipped. It
     is how a later run tells "the project's copy, unchanged" from "the
     project's copy, changed by the user" without guessing.
   - **``init``.** The starter (ADR-018 D11, at ADR-020 D10's place) writes
     the whole default set, layers with empty ``paths``, SOLID, patterns,
     docs, and copies the catalogue, through the adopt writer.
   - **``invariant guidelines adopt``** for an existing project:

     - With no ruleset: writes one at ADR-020 D10's place, sets
       ``guidelines.ruleset``, copies the catalogue.
     - With one: adds each default rule whose id is absent and the layer
       block if absent, and never rewrites a rule the project has. A default
       whose shipped digest moved while the project's copy still matches its
       old ``origin.digest`` is listed as updatable (``--update <id>``
       replaces it); one the user changed is listed as diverged and left.
     - ``--layer <id>=<glob>`` (repeatable) fills the map; without it the
       map stays empty and the run says so.
     - Records the initial baseline for the rules it added, in the same
       change, as ADR-012 D6 requires of a ruleset edit that introduces a
       rule, and runs ``guidelines render``.
     - Runs at a terminal, like ``rules loosen``: adopting is the user's
       decision, so the copied rules are the user's from the start.
       ``--dry-run`` prints the plan anywhere.

   - **Re-running is how an engine change arrives.** A later release with
     changed defaults reaches a project only when ``adopt`` runs there;
     ``doctor`` says "N default rule(s) updatable" when the shipped version
     is newer than the newest ``origin.version`` in the ruleset.

.. dec:: Existing code is baselined; each project's checklist folds into its ruleset in its rollout
   :id: DEC_ADR_032_D9
   :status: accepted
   :part_of: DEC_ADR_032
   :affects: COMP_CORE_GUIDELINES, COMP_DOMAINS_GUIDELINES

   Ruling :ref:`7 <adr-032-ruling-7>`.

   - Mechanical findings of the derived layer rules are baselined by the
     existing ``invariant guidelines baseline`` (D6's pure ratchet,
     unchanged): pre-existing findings pass, new ones block, a fixed one is
     pruned in the change that fixes it.
   - Judgement rules have no baseline (ADR-012 D6's kinds). A SOLID finding
     on old code surfaces only when a change touches that code, through a
     verdict on the diff (``scope: diff``); the Boy-Scout rule pays it down
     there, or the user rules an exception (D7).
   - A project's hand-written checklist (Heap's ``compliance_rules.rst``,
     Darvag's checklist in ``overview.md``) folds into its ruleset during
     its own rollout session (:need:`DEC_ADR_032_D13`): each prose rule
     becomes a rule with an id, the checklist page becomes the generated
     one, and a rule the defaults already carry is dropped from the prose in
     favour of the default.

.. dec:: feature-architect and compliance-reviewer name layers, each SOLID principle, patterns and docs-ahead in their steps
   :id: DEC_ADR_032_D10
   :status: accepted
   :part_of: DEC_ADR_032
   :affects: COMP_DATA_AGENTS

   Rulings :ref:`3 <adr-032-ruling-3>`, :ref:`9 <adr-032-ruling-9>` and
   :ref:`10 <adr-032-ruling-10>`. The rules reach every agent through the
   ruleset (ADR-012 D7); these steps make the two roles that answer for
   them say so explicitly. The text names no host and no model.

   - **feature-architect**, Phase 2 (the design): each affected component's
     layer, and each new or changed arrow shown to point inward; a
     **SOLID** block naming the five principles one by one (single
     responsibility, open/closed, Liskov substitution, interface
     segregation, dependency inversion) and how the design meets each; each
     new abstraction's catalogue pattern or why none fits; the design docs
     it updates before leaving the design state. Phase 4 (Boy-Scout) applies
     the same five principles to every touched file. A finding it would
     otherwise waive goes in the report as a proposed exception with the
     ``invariant guidelines except`` line for the user, never as an N/A.
   - **compliance-reviewer**, Phase 2 (the review): a cited verdict on each
     of the five SOLID rules by name, the derived layer rules' findings, the
     pattern rule, ``docs.design-before-code``, and ``docs.ahead_of_code``'s
     result. The same refusal to waive, and the same proposal shape.
   - Other agents may read the rules; no other template changes.
   - The templates are re-rendered (``invariant agents install``) in the same
     increment, in invariant; each other project re-renders in its rollout.

.. dec:: The plan page: the queue and the open session plans, rendered into the docs project
   :id: DEC_ADR_032_D11
   :status: accepted
   :part_of: DEC_ADR_032
   :affects: COMP_CORE_QUEUE, COMP_DOMAINS_QUEUE, COMP_CMD_QUEUE, COMP_CORE_PLAN, COMP_CORE_RULES

   Ruling :ref:`8 <adr-032-ruling-8>`. The generated-page precedent is the
   rules page (``invariant guidelines render``, ADR-018 D12): one machine
   source, a managed marker holding the digest, never hand-edited.

   - **The page.** ``<docs project>/plan.rst``, written by ``invariant
     queue render`` (``core/queue`` renders, ``domains/queue`` writes, under
     ``records.queue``' home): the queue in order (entry, kind, linked item
     as a ``:need:`` reference, status), then each open session's
     deliverable plan (``core/plan``'s rendering) as of the render, dated.
     A linked item that is not a need is plain text, so the page builds
     strictly.
   - **When.** ``queue add``, ``queue`` edits and the mirror re-render the
     page; ``session merge`` re-renders it as the landing's last docs write.
   - **Currency.** ``docs.plan_current`` (``warn`` everywhere) fails when the
     page's queue part does not match the queue record's digest. The session
     plans are engine state, local to a machine, so their part is a dated
     snapshot and is not checked for currency.
   - **Beside a hand-written plan.** A project's own release plan or
     roadmap (Heap's ``release_plan.rst``) is untouched; the starter's
     index links the generated page, and a project links both.

.. dec:: invariant adopts first, its packages mapped onto the four layers by package
   :id: DEC_ADR_032_D12
   :status: accepted
   :part_of: DEC_ADR_032
   :affects: COMP_CORE_GUIDELINES

   Ruling :ref:`11 <adr-032-ruling-11>`. invariant's own ruleset adopts the
   defaults first (increment 1b): SOLID, patterns,
   ``docs.design-before-code``, the catalogue, the layer map below and a
   baseline of its derived findings. Its two oracle bans stay as project
   rules beside the derived ones.

   - **The map.**

     - Entities: ``src/invariant/core/**``
     - Use Cases: ``src/invariant/domains/**``
     - Interface Adapters: ``src/invariant/adapters/**``,
       ``src/invariant/hosts/**``
     - Frameworks & Drivers: ``src/invariant/oracle/**``,
       ``src/invariant/cli/**`` (all of ``oracle/``, chain included)

   - **What it baselines.** The import graph read for this record has
     about nine inward-pointing imports that the derived rules will find:
     ``core/rules`` importing ``adapters`` (``get_adapter``, ``Reference``),
     ``core/manifest`` and ``core/questionnaire`` importing ``adapters`` at
     call time, ``core/rules``' three call-time imports of ``domains``, and
     ``domains/pipeline``, ``domains/sweep`` and ``domains/docs`` importing
     ``adapters``. They are recorded in the baseline and paid down by the
     Boy-Scout rule, chiefly by moving the adapter seam inward behind an
     injected interface; the exact entries are whatever ``invariant
     guidelines baseline`` records in increment 1b.
   - **Rejected at the interview:** the adapter seam placed in Use Cases
     (B), ``core/``'s I/O modules placed in Interface Adapters (C), and
     ``oracle/chain``, ``chainstep`` and ``attention`` placed in Use Cases
     (O2).
   - Increment 1b writes the map into :doc:`/architecture/overview`'s
     Layers section as well as the ruleset.

.. dec:: The rollout: one session per other managed project, in that project
   :id: DEC_ADR_032_D13
   :status: accepted
   :part_of: DEC_ADR_032
   :affects: COMP_CMD_GUIDELINES, COMP_DATA_AGENTS

   - After invariant's increments land, each other managed project (Heap,
     Darvag, and the rest) gets one rollout session, started in its own
     project and queued there (``invariant queue add --project <name>``),
     never run from this repository.
   - Its brief: run ``invariant guidelines adopt`` with the project's layer
     map (Heap names its fitness test as the layer evidence, ruling 6);
     baseline; fold the hand-written checklist into the ruleset (D9);
     re-render agents; render the plan page. A project whose docs are not
     yet Sphinx (Darvag) first takes ADR-018 D14's migration, in its own
     session.
   - This repository records no other project's layer map, checklist or
     content; each brief is written when it is queued, in the project it
     serves.

Increments
----------

Approved by the user on 2026-11-22 (ruling :ref:`12 <adr-032-ruling-12>`),
BUG_115 first. Smallest provable slice first. Each lands on its own; later
ones need the earlier only for the whole ruling set to hold.

.. list-table::
   :header-rows: 1
   :widths: 6 44 50

   * - Inc
     - Builds
     - Deliverable test
   * - 0
     - BUG_115, in its own session (bugfix lane, reproduced first): a
       change that adds a judgement guideline can record a verdict on it.
       Not part of this record's design; it comes first because increments
       1b and 2 add judgement rules under a pinned ruleset.
     - BUG_115's committed reproduction test passes with its
       expected-failure wrapper removed and its assertion unchanged.
   * - 1a
     - Ruleset version 2 in ``core/guidelines``: the layer map and its
       derived direction rules (D2), ``owner``, ``ruled``, ``origin``,
       ``catalogue`` keys; the catalogue file and its validation and
       rendering (D5); the starter's default set: layers (empty), the five
       SOLID rules (D4), ``patterns.named-or-declined``,
       ``docs.design-before-code``, the two existing docs rules.
     - A toy ruleset with a four-layer map derives three direction rules
       whose ``forbid`` lists every outer layer; a Python file in Use Cases
       importing Interface Adapters is a ``guidelines.mechanical_clean``
       finding; a file in two layers is unanswerable; version 1 loads
       unchanged. The starter carries the five ``solid.*`` rules at
       ``blocker`` with ``owner: user``, each with its own statement and
       rationale, and the 32-entry catalogue validates; a fresh ``init``
       renders both pages and builds strictly.
   * - 1b
     - invariant adopts: its ruleset gains the layer map (D12, ruling 11),
       the five SOLID rules, the pattern rule, ``docs.design-before-code``,
       the catalogue, and a baseline of its derived findings; rendered with
       ``invariant guidelines render``, never hand-edited; the overview's
       Layers section names the four layers.
     - ``guidelines.ruleset_valid`` and ``mechanical_clean`` pass on this
       repository with the baseline; ``invariant guidelines show --phase
       review`` prints the five SOLID rules by name; adding a ``cli``
       import to a ``core`` module in a test tree is a new finding.
   * - 2
     - User-only exceptions and changes (D7): ``guidelines except`` and
       ``guidelines loosen`` at a terminal, the ``na`` refusal, the grant's
       terminal confirmation, ``config.rulings_by_user`` over the ruleset,
       ``guidelines.user_rules_ruled`` and ``guidelines.blocking_verdicts``
       with their profile levels; ``guidelines adopt`` (D8) and the starter
       calling it; ``DOCSD --> GDOM`` in the overview.
     - An agent-submitted ``na`` verdict on ``solid.single-responsibility``
       is refused at submission; an agent's ``invariant override`` on it
       without a terminal is refused; an agent's edit adding an exception
       to the ruleset is refused by ``config.rulings_by_user``; a ruleset
       lowering ``solid.open-closed`` with no new ``ruled`` fails
       ``guidelines.user_rules_ruled``. A current ``fail`` verdict blocks
       ``guidelines.blocking_verdicts`` under ``standard``; after the user's
       ``guidelines except`` (terminal, ``ruled: {by, date, reason}``) the
       same finding passes. ``adopt`` on a toy project with no ruleset
       writes the defaults and a baseline; run again it changes nothing; a
       shipped digest bump lists the rule as updatable, a user-edited copy
       as diverged.
   * - 3
     - ``docs.ahead_of_code`` (D6): the design snapshot at the advance out
       of design, the gate beside ``docs.updated`` sharing its structural
       reading, the early notice, levels in the three profiles.
     - A tdd pipeline that edits ``docs/architecture`` in design and source
       in green passes; one that edits only source fails under
       ``standard`` and warns under ``loose``; a ``chore`` pipeline is N/A;
       a store-docs project reads the committed store leg.
   * - 4
     - The agent templates (D10): feature-architect's Phase 2 and Phase 4,
       compliance-reviewer's Phase 2, each naming the five SOLID principles,
       the layers, patterns and docs-ahead, and the propose-never-waive
       shape; agents re-rendered.
     - A template test asserts each of the five principle names, the
       layer, pattern and docs-ahead steps and the ``guidelines except``
       proposal appear in both rendered agents; ``agents.rendered_current``
       passes.
   * - 5
     - ``imports`` for Dart and TypeScript (``references`` in
       ``adapters/dart`` and ``adapters/node``); the ``fitness`` engine
       (D3).
     - Toy Dart and TypeScript trees: a Use Cases file importing an outer
       layer is a finding, through ``package:``, ``tsconfig`` paths and
       relative specifiers; a third-party import is not; an unresolvable one
       is unanswerable. A ``fitness`` rule passes on a recorded green run of
       its selector, fails on a red one, and fails unanswered with none.
   * - 6
     - The plan page (D11): ``invariant queue render``, the re-render on
       queue edits and at ``session merge``, ``docs.plan_current``, the
       starter's index entry; invariant's own page rendered.
     - A toy queue renders a page that builds strictly with items as need
       references; editing the queue without rendering fails
       ``docs.plan_current``; this repository's page matches its queue.
   * - 7
     - Rollout (D13): one session per other managed project, in that
       project.
     - Per project: ``guidelines.ruleset_valid`` and the strict docs build
       pass there with the defaults, a baseline, and the plan page.

As built
--------

.. dec:: ADR-032 increment 1a as built: ruleset version 2, the catalogue and the starter's default set
   :id: DEC_ADR_032_I1A
   :status: accepted
   :part_of: DEC_ADR_032
   :affects: COMP_CORE_GUIDELINES, COMP_CORE_FINDINGS, COMP_CORE_RULES, COMP_DOMAINS_GUIDELINES, COMP_DOMAINS_DOCS, COMP_DATA_SCAFFOLD, COMP_ADAPTERS_BASE

   The build settles what D2, D4, D5, D7 and D8 left open for this
   increment:

   - **Versions.** This build reads versions 1 and 2. A version it does not
     read is refused by that one problem, before any key is judged, so a later
     version is never reported as a list of unknown keys. A version-2 key in a
     version-1 file is an unknown key there, named as a version-2 key.
   - **Layer membership is one reading.** ``covers`` (under ``applies_to``,
     outside ``exclude``) is what ``meets``, the evaluators and the baseline
     scans ask. A derived rule's ``forbid`` lists every outer layer's
     ``paths`` as D2 says; a reference crosses only when its target is in an
     outer layer after that layer's ``exclude``, so an exclude moves a file
     out of a layer for both sides of the rule. A layer derives a rule only
     when it maps files and an outer layer does.
   - **Two layers.** A changed file the map places in two layers is one
     unanswerable finding of each derived rule that covers it, naming the
     layers and ``exclude`` as the fix. It is mechanical only: a judgement
     derived rule is answered by its verdict.
   - **D7's two load-time refusals are built here**: an exception without
     ``ruled`` and a claim-form ``not_applicable_when`` on an ``owner: user``
     rule (and an exception on an ``owner: user`` map). They are validation
     of the keys this increment adds, so a version-2 ruleset that loads now
     still loads when increment 2 lands. Every other part of D7 is
     increment 2's.
   - **The fix guidance is the statement's second paragraph** ("Fix: ..."),
     for the SOLID rules and the derived rules alike: the schema gains no fix
     key, and the edit layer and ``guidelines show`` carry the fix with the
     rule.
   - **``origin.digest``** is ``origin_digest``: the first twelve hex of the
     SHA-256 of the rule's mapping as written into the project, ``origin``
     left out, as canonical JSON. The starter stamps each default rule and
     the map with it after filling the template, so the digest is of the
     project's copy.
   - **The catalogue path** is resolved from the ruleset's own folder
     (``catalogue: patterns.yaml``), read through the same tree or revision
     the ruleset is; ``use_when`` and ``avoid_when`` are lists of lines. Its
     digest is not part of the ruleset's, as a registry's is not.
   - **The source globs** the default rules apply to are the configured
     ecosystems' ``Adapter.source_globs``, spelled from ``source_suffixes``,
     the one list each adapter's ``source_files`` filters on; ``**`` where no
     ecosystem answers.
   - **No catalogue, no page.** ``guidelines render`` writes ``patterns.rst``
     only where the ruleset names a catalogue; the starter always writes one,
     from no catalogue where the project's own ruleset is kept, because its
     toctree names it.

.. dec:: ADR-032 increment 1b as built: invariant's ruleset takes the default set through a merge, its layer map and a baseline
   :id: DEC_ADR_032_I1B
   :status: accepted
   :part_of: DEC_ADR_032
   :affects: COMP_CORE_GUIDELINES, COMP_DOMAINS_GUIDELINES, COMP_DOMAINS_DOCS

   The build settles what D8 and D12 left open for invariant's own
   adoption:

   - **The defaults are merged, not retyped.** No rule is typed into
     ``docs/guidelines/ruleset.yaml`` by hand. ``domains/docs``'
     ``default_ruleset`` is the starter's one fill of the shipped template
     (the configured ecosystems' source globs, the backlog's path), and the
     starter now writes from it. ``core/guidelines``' ``merge_defaults`` adds
     to a project's ruleset text, as text, whatever of that set it lacks:
     the version raised to the defaults', ``catalogue`` after the
     ``version`` line, each absent section at the end of ``sections``, the
     layer block before ``rules`` with each named layer's ``paths`` filled
     from a given map, and each absent rule at the end of ``rules``. Every
     byte the project wrote is kept, so its comments and folded statements
     survive. The added ``origin`` digests are stamped afterwards with
     ``stamp_origins``, so each is the digest of the project's copy, as in
     :need:`DEC_ADR_032_I1A`. The result must read back as exactly the
     project's ruleset plus those additions, or the merge is refused.
     A map given where the project already has a layer block, and a layer
     id the defaults do not name, are refused too.
     ``GuidelinesDomain.merge_defaults`` writes it: handed the defaults' and
     the catalogue's text by its caller, it validates the result against
     the rule registry, admits both paths, writes the ruleset, and copies
     the catalogue beside it only where none is there. It is the seed of
     increment 2's adopt writer. No command calls it yet: increment 2's
     ``guidelines adopt`` composes it, and in this increment it was driven
     once over invariant's ruleset with the map below.
   - **What the project had stays as it wrote it.** The ``layers`` section
     keeps its title (Layering), the two oracle bans stay project rules
     beside the derived ones, and the conduct rule and the two docs rules are
     kept, as before, without ``origin``: the engine did not copy them, so it
     makes no claim about where they came from.
   - **What the copied rules apply to.** The five ``solid.*`` rules,
     ``patterns.named-or-declined`` and ``docs.design-before-code`` apply to
     the python adapter's source glob, ``**/*.py``, the same answer the
     starter gives, which includes tests and ``docs/conf.py``.
   - **The map** is :need:`DEC_ADR_032_D12`'s, by package, with ``**`` globs:
     ``src/invariant/core/**``; ``src/invariant/domains/**``;
     ``src/invariant/adapters/**`` and ``src/invariant/hosts/**``;
     ``src/invariant/oracle/**`` and ``src/invariant/cli/**``. The overview's
     Layers section names the same four layers and packages.
   - **The baseline.** ``guidelines.baseline`` is set to
     ``docs/guidelines/baseline.txt``, and ``invariant guidelines baseline
     --record`` recorded the derived rules' findings there: twelve entries,
     not D12's estimated nine. Seven are Entities findings: five in
     ``core/rules`` (``adapters`` twice, ``domains`` three times), one in
     ``core/manifest`` and one in ``core/questionnaire``. Five are Use Cases
     findings: three in ``domains/docs`` (``adapters`` once, and
     ``adapters/sphinx`` on two import lines, which D12 counted as one), one
     in ``domains/pipeline`` and one in ``domains/sweep``. They are left in
     place here, and the Boy-Scout rule pays them down.
   - **The catalogue page** is a document of the docs project, so the
     index's Guidelines toctree names ``patterns`` beside ``rules`` and
     ``checklist`` and the strict build stays green.
   - **Severity, until increment 2.** The SOLID rules are copied as
     ``blocker`` and are owed a cited verdict on every change to a Python
     file. Their ``fail`` blocks only once increment 2's
     ``guidelines.blocking_verdicts`` exists. Until then
     ``guidelines.verdicts_complete``, at ``warn`` in ``standard``, reports
     it.

.. dec:: ADR-032 increment 2 as built: the user's exceptions and changes, the two gates, and guidelines adopt
   :id: DEC_ADR_032_I2
   :status: accepted
   :part_of: DEC_ADR_032
   :affects: COMP_CORE_GUIDELINES, COMP_CORE_VERDICTS, COMP_CORE_RULES, COMP_CORE_RULINGS, COMP_DOMAINS_GUIDELINES, COMP_DOMAINS_DOCS, COMP_CMD_GUIDELINES, COMP_CMD_RULES, COMP_DATA_PROFILES

   The build settles what D7 and D8 left open for this increment:

   - **Every edit of a ruleset is text, read back.** ``core/guidelines``
     writes an exception (``add_exception``), a lowered severity with its
     ruling (``lower_severity``) and a shipped rule over an updatable copy
     (``replace_default``) as line edits that keep every other byte, as
     ``merge_defaults`` does; a result that does not read back as exactly
     the ruleset meant is refused, and the command prints the entry to add
     by hand. ``domains/guidelines`` plans each edit, validates the result
     against the rule registry, and writes it only while the file is still
     the one the plan read, admitted by the docs lock like every other
     write into the policy tree.
   - **Where an exception goes.** On a declared rule, its ``exceptions``;
     on a rule derived from the layer map, its layer's ``exceptions``, the
     one place a derived rule's exceptions come from. The exception's
     ``reason`` and its ruling's ``reason`` are the one ``--reason``; its
     ``by`` is ``--by``, its ``date`` today. An exception the rule already
     carries for the same path and fingerprint is refused.
   - **What loosen lowers.** ``--to warn`` on a declared rule at
     ``blocker`` writes ``severity: warn`` and the rule's ``ruled``,
     replacing an older one. The map's severity is the map's, so ``loosen
     layers --to warn`` lowers it and writes ``layers.ruled``; a derived
     rule's id is refused naming that route. A rule already at ``warn`` is
     refused: raising needs nothing. ``--to off`` (ruling 13) writes
     ``severity: off`` the same way, from ``blocker`` or ``warn``, the map
     included.
   - **Paused and retired (ruling 13).** ``off`` is a version-2 severity of a
     rule or the map; a version-1 ruleset refuses it. A rule at ``off``, and
     a rule derived from a map at ``off``, is held apart from the rules
     (``Ruleset.paused``): no gate evaluates it, no verdict is owed on it,
     ``guidelines show`` and the generated pages leave it out, and
     ``by_id`` does not find it; ``any_rule`` does, for the edits, the
     comparison and the baseline. Its baseline entries are kept as they
     are, never stale and never pruned, so raising it again finds its
     findings baselined. ``retired`` is a version-2 top-level list of
     ``{id, ruled}``, both required; an id the ruleset still declares is
     refused at load. ``guidelines retire`` takes a declared rule out
     (``core/guidelines.retire_rule``: its lines removed, the entry appended
     to ``retired``, read back) under the same terminal confirmation; a
     derived rule's id is refused naming ``loosen layers --to off``.
   - **The terminal.** ``except``, ``loosen``, ``retire`` and ``adopt`` share ``rules
     loosen``' confirmation: stdin a terminal, no ``--json``, no ``--yes``;
     ``adopt --dry-run`` prints its plan anywhere. ``invariant override``
     naming an ``owner: user`` guideline needs ``--by`` and the same
     terminal, and the grant records ``by``. That grant is the user's, so
     ``guidelines.blocking_verdicts`` honours it as ``verdicts_complete``
     does, and the advance it lets through spends it once.
   - **N/A.** P5 refuses any ``na`` on an ``owner: user`` rule, at
     submission and when the gate checks a record again; the engine's
     mechanical conditions still answer such a rule N/A on their own.
   - **``config.rulings_by_user``'s reach.** Beside the config levels, the
     configured ruleset (its policy-tree path, in any checkout) and the
     catalogue it names. ``core/rulings.changed_ruled`` reads every
     ``ruled`` mapping in the file, each list item keyed by its ``id``, else
     its ``path`` and ``fingerprint``, so a reordering is no change, and
     names each one the edit adds or changes; one removed passes.
   - **What ``guidelines.user_rules_ruled`` reads as a change.** The base
     copy is read where ``guidelines.baseline_only_shrinks`` reads the
     baseline's, at the path the base's own manifest names; no ruleset
     there is no base rule. ``core/guidelines.unruled_changes`` compares
     each declared ``owner: user`` rule of the base, and an ``owner: user``
     map: removed; ``severity`` lowered; ``owner`` made the project's;
     ``phases`` or ``applies_to`` losing an entry, ``exclude`` gaining one,
     or ``check`` changed other than by dropping an N/A condition
     (narrowed); ``title``, ``statement`` or ``rationale`` changed
     (reworded); for the map, a layer dropped, retitled or moved, losing a
     path or gaining an exclude, or its ``evidence`` or ``phases``
     changed. Each needs the rule's (or the map's) ``ruled`` set and
     different from the base's; severity is ranked ``blocker``, ``warn``,
     ``off``. An exception added needs its own ``ruled``, equal to none that
     rule or layer held at the base. Adding a rule, a layer, a path or a
     phase needs nothing. A removed rule needs a ``retired`` entry for its
     id whose ``ruled`` is set and is not one the base's ``retired`` held for
     it (ruling 13).
   - **What ``guidelines.blocking_verdicts`` covers.** The standings of
     ``read_standings`` at ``blocker`` severity: missing, stale or a current
     ``fail``, unless a grant passes it. "A user-ruled exception covers the
     path" is DN-3's one reading: a path exception sets the file aside, so a
     rule that only excepted files meet is not owed. It and
     ``guidelines.user_rules_ruled`` ride the landing entry beside the
     other guideline gates.
   - **``adopt``.** With no ruleset it writes the default set at
     ``ruleset_location(docs_folder)`` (ADR-020 D10), the map filled from
     ``--layer``, the catalogue beside it, and sets ``guidelines.ruleset``
     and, where unset, ``guidelines.baseline`` (``baseline.txt`` beside the
     ruleset) at the project's own level, as ``docs adopt`` writes its
     settings; then records the whole baseline and renders. With one it is
     ``merge_defaults``, then ``baseline --record --rule`` for each pattern
     or boundary rule it added (with none, nothing is recorded), then
     render. A run that adds and updates nothing writes nothing. Each
     default the project holds with an ``origin`` is ``current``,
     ``updatable`` (the copy's digest is its ``origin.digest`` and the
     shipped rule's, filled for the project, is not) or ``diverged`` (the
     copy's digest is not its ``origin.digest``); the map's shipped digest
     is taken with the project's paths. ``--update`` takes an updatable
     rule; the map holds the project's paths, so it is listed and changed
     by hand.
   - **One writer for init and adopt.** ``domains/guidelines.fresh_defaults``
     is the default set's files as a project with no ruleset gets them: the
     filled template with its map and stamped origins
     (``core/guidelines.fill_defaults``), parsed, and the catalogue beside
     it. The starter's ruleset and catalogue are that (``DOCSD --> GDOM``),
     and the starter's own parse through ``core/rules`` (``DOCSD -. RULES``)
     is gone with it.
   - **``doctor``.** A ``guidelines defaults`` row when the ruleset holds a
     copied default and the shipped ``origin.version`` is newer than the
     newest there (a ruleset holding none has adopted nothing to update): "N default
     rule(s) updatable", naming ``invariant guidelines adopt --update``.
   - **Ruling 13 in adopt and the hook.** ``merge_defaults`` treats a
     retired id as held, so ``adopt`` never adds it back; a paused default
     is present, so it is never added either (its copy reads as diverged).
     ``config.rulings_by_user`` reads a ``ruled`` inside a ``retired`` entry
     as it reads every other (``retired[<id>].ruled``).

.. dec:: ADR-032 increment 3 as built: the design snapshot, the early notice and docs.ahead_of_code
   :id: DEC_ADR_032_I3
   :status: accepted
   :part_of: DEC_ADR_032
   :affects: COMP_CORE_RULES, COMP_CORE_NEEDS, COMP_CORE_MACHINES, COMP_DOMAINS_PIPELINE, COMP_CMD_PIPELINE, COMP_DATA_PROFILES, COMP_DATA_CONFORMANCE

   The build settles what :need:`DEC_ADR_032_D6` left open:

   - **Where the gate rides.** ``core/machines`` injects
     ``docs.ahead_of_code`` by role, on the entry of every state with the
     ``review`` role (``DOCS_FIRST_GATES``): tdd's and chore's
     ``reviewing``, bugfix's ``verifying``, the docs lanes' ``reviewing``.
     ``docs.updated`` stays on the landing entry. "Evaluated with
     ``docs.updated``" is read as sharing its reading, not its state: the
     review entry is where D6 places the judgement, before the reviewer
     runs. A state reached only by a retreat into review is entered the same
     way, and the gate answers the same there.
   - **The record.** ``evidence["design_snapshots"]``, a list. An advance
     from a state with the ``design`` role into one with neither the
     ``design`` nor the ``abandon`` role appends one entry, forced or not:
     ``from``, ``to``, ``step`` (the index of its history row), ``leg``
     (``repo`` or ``store``), ``answerable`` and ``problem``, ``docs`` (each
     ``{path, digest}``) and ``source_changed``. It is taken outside the
     record's lock once the gates have passed or been forced, as a gate
     evaluation is, and appended in the save that writes the history row. An
     edge into an ``abandon`` state takes none: no build follows it.
   - **What differs from the merge base.** On the repository leg, git's
     working tree with untracked files (``git.dirty_paths``) and the branch's
     commits since the merge base (``git.branch_files``, at the tip
     ``_changed_set`` names). Not the session's declared file set: a declared
     path is one the session means to touch, not one that differs. An
     unreadable status or commit range makes the snapshot unanswerable,
     naming why, never empty.
   - **What a design document is.** A differing file under ``paths.docs`` in
     the tree in hand, or an ``.rst`` page whose directives
     (``core/docformat.rst_directives``) include a directive of the decision
     role (``core/needs.directives_of`` over the project's types). A deleted
     file under ``paths.docs`` counts with a ``null`` digest; a deleted page
     elsewhere is not read. The digest is the first twelve hex of the
     SHA-256 of the file's bytes.
   - **Under a store docs lock.** With ``records.docs: store`` the design
     documents are the store leg's, counted exactly as ``docs.updated``
     counts a store doc, through one function (``_store_docs_counted``):
     committed under a span of its unit that also holds a read of it, or new
     in that span. An uncommitted doc, or one with no read, does not count.
     The path is the store-relative one. "Source changed" is still read on
     the repository leg.
   - **One structural reading.** ``_structural_change`` is what
     ``docs.updated`` used to compute inline: the changed files outside
     ``paths.docs``, less prose, the declared ``non_source`` globs and the
     test code an adapter claims. ``docs.updated``, ``docs.area_documented``
     (whose inline copy it replaces), the snapshot's ``source_changed`` and
     ``docs.ahead_of_code`` all call it.
   - **The judgement, in order.** N/A with no pipeline, on a methodology
     with no ``design`` state (naming it), and on a pipeline that left its
     design state with no snapshot recorded (begun before this build). An
     edge leaving a design state straight into review is judged on the
     snapshot that edge takes, read live. Then, as ``docs.updated``, over
     its changed set with the untracked files the snapshot reads added (at
     the review entry a new module may not be added to git yet, and it is
     structural all the same): an unestablished changed set fails, nothing
     changed is N/A, and a change with no structural source is clean,
     naming what was held out. Then the
     snapshots in order: an unanswerable one fails, naming its problem; the
     first that shows a design document changed passes, naming them, unless
     an earlier one showed source changed with none, which fails; none fails,
     "the design documents were not changed before the build began". The
     fixes name the docs to update, since ``docs.updated`` still owes them,
     and a forced advance with a reason.
   - **Several snapshots.** A lane that can re-enter its design state
     (spec-first, the docs lanes) records one per exit. A second design pass
     that writes the docs still passes; source written between two exits is
     the known limit D6 names, and the reviewer's ``docs.design-before-code``
     judges it.
   - **The notice.** ``invariant pipeline advance`` prints the snapshot the
     advance took (``core/rules.design_snapshot_notice``): the documents
     changed, or, with none, that a structural change will fail
     ``docs.ahead_of_code`` at review and to write the doc now, or why it
     could not be read. ``--json`` carries it as ``design_snapshot``.
   - **Levels.** ``block`` in ``standard`` and ``strict``, ``warn`` in
     ``loose``.
   - **The conformance scenarios are a known-answer change.** The thirteen
     shipped scenarios that update the architecture doc wrote it while
     reviewing, after the code, which the gate now refuses. Each writes it in
     design instead, before ``pipeline advance red``, and pins
     ``docs.ahead_of_code: pass`` on its reviewing edge; nothing else in them
     moves.
   - **``docs.design-before-code``** is in the starter's and invariant's
     rulesets since increments 1a and 1b, unchanged here.

.. dec:: ADR-032 increment 4 as built: the architect and the reviewer name the layers, each SOLID principle, patterns and docs-ahead, and propose every exception
   :id: DEC_ADR_032_I4
   :status: accepted
   :part_of: DEC_ADR_032
   :affects: COMP_DATA_AGENTS

   The build settles what :need:`DEC_ADR_032_D10` left open. Only the two
   sources under ``data/agents`` change, and the installed copies are
   re-rendered from them; no engine code changes, so the steps quote the
   engine's names and commands and compute nothing.

   - **Where a layer comes from.** The rules ``guidelines show`` prints at
     the design phase: each derived ``layers.<layer id>-depends-inward``
     names its layer's title and paths, and ``guidelines show --phase
     design --files <path>`` places one file. The titles are the project's
     (default Entities, Use Cases, Interface Adapters, Frameworks &
     Drivers). A file no derived rule covers is written "unmapped", and a
     project with no map or an empty one is told so, never given an
     invented layer; an unmapped file is the project's to place (D2).
   - **The architect's design** (Phase 2) gains three items beside the
     existing ones: the layer column on each affected component and an
     inward check on each new or changed arrow (an outward arrow is a
     redesign, or a proposed exception, never a note); a **SOLID** block
     naming single responsibility, open/closed, Liskov substitution,
     interface segregation and dependency inversion one by one, each with
     how the design meets it; and a **Patterns** line per new abstraction,
     naming its catalogue id (``patterns.rst#<id>``) or one sentence on why
     none fits, or "no catalogue configured" where the ruleset names none.
     Its docs item becomes the design documents it edits *before* the
     first advance out of the design state, which is what
     ``docs.ahead_of_code`` reads at review and what the advance's notice
     prints. Phase 7 stays after the code as the reconcile pass: the docs
     still describe what was built, and the strict docs build.
   - **The architect's Boy-Scout** (Phase 4) applies the five principles by
     name to every touched file, beside the rules Phase 1c printed.
   - **The reviewer's review** (Phase 2) gains a step after the verdicts:
     the five ``solid.*`` verdicts by id, the derived layer rules' findings
     out of ``guidelines.mechanical_clean``, ``patterns.named-or-declined``,
     ``docs.design-before-code``, and ``invariant check
     docs.ahead_of_code`` pasted as printed. A ``fail`` of
     ``docs.ahead_of_code`` is never forced past by an agent.
   - **Propose, never waive.** Both templates say it in the same words: a
     finding on an ``owner: user`` rule that the agent would otherwise
     waive is never answered N/A, never downgraded and never left out; it
     goes under **PROPOSED EXCEPTIONS** in the report with the finding,
     the reason and the exact ``invariant guidelines except <rule-id>
     <path> [--fingerprint <fp>] --by <who> --reason <why>`` line, ``--by``
     left for the user. Where the rule itself looks wrong, route 3 is named
     the same way (``guidelines loosen``, ``guidelines retire``), as the
     user's. The agent never runs any of the three.
   - **Degrading.** With no ruleset, the five principles are reviewed by
     hand as checklist lines, at the checklist's severity or ``[WARN]``
     where it names none, and the report names ``invariant guidelines
     adopt``; nothing can be excepted, so a finding the agent cannot fix is
     ``[NEEDS-USER]``. With a ruleset that does not carry a ``solid.*``
     rule (retired, paused or never adopted), that principle is reported as
     not in the ruleset and owes no verdict: the user's ruleset decides.
     ``docs.ahead_of_code``'s own N/A (no design state, no snapshot) is
     pasted as the engine prints it.
   - **Pinned** by ``tests/test_agent_guideline_steps.py`` over both sources
     and their rendered form, and by ``tests/test_agent_bodies.py``, whose
     command scan now reads an optional ``[...]`` group both with and
     without its contents, so the ``except`` line parses as written.

.. dec:: ADR-032 increment 5 as built: the imports engine for Dart and TypeScript, and the fitness engine
   :id: DEC_ADR_032_I5
   :status: accepted
   :part_of: DEC_ADR_032
   :affects: COMP_ADAPTERS_DART, COMP_ADAPTERS_NODE, COMP_ADAPTERS_BASE, COMP_CORE_GUIDELINES, COMP_CORE_FINDINGS, COMP_CORE_RULES, COMP_DOMAINS_PIPELINE

   The build settles what :need:`DEC_ADR_032_D3` left open. The seam is the
   one the Python adapter answers, ``Adapter.references``; no adapter, core
   or domain gains an import of an outer layer.

   - **Where the readers live.** ``adapters/dart_imports`` and
     ``adapters/node_imports`` read and resolve; ``DartAdapter.references``
     (``FlutterAdapter`` inherits it) and ``NodeAdapter.references`` ask them,
     each for its own suffixes only, and both list ``imports`` in
     ``guideline_engines``. Each module reads only what its language says, so
     the mask half of IDEA_038 and IDEA_047 can later share the lexers.
   - **Dart directives.** Read from the top of the file to the first
     declaration, since Dart allows directives nowhere else: ``library``,
     ``import``, ``export`` and ``part`` (``part of`` names no file), after
     any annotations. Adjacent string literals are one URI, and every URI of a
     conditional import counts (IDEA_047's open question: both targets are
     references). An unfinished comment or string, or a URI holding an
     interpolation, makes the file unanswerable.
   - **Dart resolution.** ``dart:`` is the SDK's, outside the project. The
     project's packages are the ``pubspec.yaml`` files in the tree (no dot
     folder, no ``build/`` or ``node_modules/``), each by its ``name:``; ``package:<name>/<path>``
     of one of them is ``<its folder>/lib/<path>``, a name two of them claim
     is unresolvable, and any other name is a dependency, outside the
     project. A relative URI resolves from the file's folder; one leaving the
     project is outside it. Any other scheme is unresolvable. A project target
     that is not a file makes the file unanswerable, a generated ``part``
     not yet built included.
   - **TypeScript and JavaScript forms.** One lexer reads comments, the three
     quotes, template literals with nested substitutions, a regular
     expression told from a division by the token before it, and in
     ``.tsx`` and ``.jsx`` JSX text, attributes and expression containers; a
     file it cannot finish is unanswerable. Every static ``import`` (with
     ``type``, default, named, namespace or for its effect alone), ``export
     ... from``, ``require('x')`` and ``import('x')`` counts, at any depth; a
     template literal without a substitution is a string literal. A
     ``require`` or ``import()`` of anything but a literal is not a reference,
     as Python's ``importlib.import_module`` is not; nor are ``import.meta`` and
     a member named ``require`` or ``import``.
   - **TypeScript resolution.** A specifier starting ``./`` or ``../`` (or
     ``.`` or ``..``) is relative. Any other is read through the nearest
     ``tsconfig.json``, else ``jsconfig.json``, at or above the file inside
     the project, as JSON with comments and trailing commas, its relative
     ``extends`` chain followed (a package's ``extends`` contributes nothing):
     ``compilerOptions.paths`` by the longest matching prefix, each
     substitution tried in order from ``baseUrl``, else from the config's
     folder; then ``baseUrl``. A matched pattern other than the bare ``*`` that
     resolves to no file makes the file unanswerable, since an alias is the
     project's own; a bare specifier nothing resolves is a dependency or a
     ``node:`` builtin, outside the project. A candidate is the file itself,
     the TypeScript source a ``.js``, ``.jsx``, ``.mjs`` or ``.cjs`` specifier
     names, the candidate with ``.ts``, ``.tsx``, ``.d.ts``, ``.js``, ``.jsx``,
     ``.mts``, ``.cts``, ``.mjs`` or ``.cjs`` appended, then a folder's
     ``index`` with each. A relative specifier that resolves to nothing makes
     the file unanswerable. **Known limit:** a workspace package imported by
     its package name resolves through ``node_modules`` and is read as
     outside the project, and ``package.json``'s ``exports`` and ``main`` are
     not read.
   - **``fitness``, as a check.** ``{kind: boundary, engine: fitness, test:
     <selector>}``: a version-2 engine and key, ``test`` in place of
     ``forbid`` and ``only`` (giving either is refused, so is ``test`` on
     ``imports``), needing no adapter. As the layer's evidence, ``layers:
     {evidence: fitness, test: <selector>}``: ``test`` is required with
     ``fitness`` and refused without it, and every derived rule carries it. A
     file the map places in two layers stays an unanswerable finding of the
     derived rules (D2), whatever the evidence. The map's ``test`` changed is
     a narrowing ``guidelines.user_rules_ruled`` reads as it reads its
     ``evidence``.
   - **Which run answers.** Every ``test_runs`` record now signs its tree as
     ``coverage_run`` does (ADR-026 D1, ``git.tree_signature`` before and
     after the run). A run includes the selector when its ``selectors`` name
     it exactly or it ran the whole suite; it is over the final tree when the
     tree held still during it and the tree the gate judges has its commit
     and digest now, the reading a step's run gets (ADR-021 D3). The latest
     such run decides: passed, with tests executed and no hazard, is clean for
     every file the rule applies to; failed is one ``new`` finding of the
     rule, on its first changed file, naming the selector as its target;
     passed with nothing executed or with a hazard is unanswerable, naming
     why; none is unanswerable, its fix ``invariant pipeline test --selector
     <selector>``, saying whether runs of it exist over an older tree. A
     commit moves the tree, as it does for a step, so at the landing a run
     recorded after the commit answers; ``guidelines.mechanical_clean`` warns
     in ``standard``.
   - **Patterns.** ``FitnessAnswer`` is a value object (``patterns.rst#domain.value-object``):
     frozen, equal by its outcome and note. The ``Fitness`` callable is
     dependency injection (``patterns.rst#enterprise.dependency-injection``):
     ``core/findings`` owns the shape and ``guidelines.mechanical_clean``
     supplies the reader. The two import readers are plain functions behind
     the existing ``Adapter.references`` seam, so they add no abstraction of
     their own.
   - **Not baselined.** A fitness finding has no fingerprint, so no baseline
     entry can hold it; ``baseline --record`` skips a fitness rule,
     ``--rule`` naming one is refused, and an entry naming one fails the
     baseline's reading, each saying the test keeps its own ratchet.
   - **Who asks.** Only ``guidelines.mechanical_clean`` hands the runs to
     ``core/findings`` (``fitness_answer`` over ``evidence["test_runs"]`` and
     the tree's signature, taken once and only when a fitness rule is asked).
     The edit-time advisory and the baseline scans hand none, and a fitness
     rule is then not evaluated: no test is run for them, and a finding
     reported at every edit would say nothing the run does not.
   - **The refusals name the alternatives.** ``guidelines.ruleset_valid``'s
     refusal of an ``imports`` rule no configured adapter answers names the
     adapters that do (python, dart, flutter, node) and both alternatives,
     judgement and ``fitness`` with a test; the layer map's refusal names
     ``evidence: judgement`` and ``evidence: fitness`` with ``layers.test``.

.. dec:: ADR-032 increment 6 as built: the plan page, its re-renders and docs.plan_current
   :id: DEC_ADR_032_I6
   :status: accepted
   :part_of: DEC_ADR_032
   :affects: COMP_CORE_QUEUE, COMP_DOMAINS_QUEUE, COMP_CMD_QUEUE, COMP_CMD_SESSION, COMP_CMD_SETUP, COMP_CORE_PLAN, COMP_CORE_RULES, COMP_CORE_GUIDELINES, COMP_CORE_DOCLOCK, COMP_DOMAINS_DOCS, COMP_DATA_SCAFFOLD, COMP_DATA_PROFILES, COMP_DATA_AGENTS

   The build settles what :need:`DEC_ADR_032_D11` left open. ``core/queue``
   renders and ``domains/queue`` writes, as D1 placed them; the marker and its
   digest are the rules page's, not a second copy.

   - **The queue's digest.** The record exposed none, so
     ``core/queue.queue_digest`` is ``core/guidelines.digest_of`` (twelve hex)
     of the record as canonical JSON: ``to_dict`` with its keys sorted and no
     whitespace. A reformatted file is no change; every field the record
     holds, ``next``, ``mirrored`` and unknown keys included, is.
   - **The page** (``core/queue.render_page``). The managed marker line
     (``core/guidelines.managed_text``, the line and digest ``guidelines
     render`` writes, so ``split_managed`` reads both pages), then an rST
     comment ``.. invariant-queue: <digest>`` naming the queue it was
     rendered from, the title, and a line naming the queue file and the
     command. **Queue:** every entry in the record's order, done and failed
     ones included, as a list table: position, entry id, ref, kind, linked
     item, status. The kind is ``triage`` (``triage_of`` set), ``retry``
     (``retry_of`` set), ``item`` (the ref is a work item id) or ``label``.
     The linked item is the ref when it is a work item id, else ``serves``,
     else ``none``; it is a ``:need:`` reference when that id is declared as
     a need in the docs project the page is built in, else plain text. The
     status is the entry's state, with ``, proposed`` for an entry in
     ``propose`` mode. Every free text (a ref, a label, a session's name) is
     written with each punctuation character escaped and its whitespace
     collapsed, so no ref can open inline markup or a reference and the page
     builds strictly whatever was queued. An empty queue is one sentence.
     **Session plans:** "as of" an ISO date (no clock time), each live
     session that records at least one deliverable, ordered by name then id,
     under a heading of its name and id, its plan as ``core/plan.render_lines``
     writes it, in a literal block. With none, one sentence; rendered with
     no date (the starter), a sentence saying the part was not read.
   - **Which ids are needs.** ``domains/queue`` reads the docs project's
     ``.rst`` files (hidden folders and ``_build`` skipped) and collects the
     ``:id:`` of every directive of a declared need type
     (``core/needs.types_of``, ``core/docformat.rst_directives``). Whether a
     ref is a work item id is the domain's own test (``_item_shaped``),
     handed to the renderer as a predicate.
   - **Where the page lives** (``core/queue.plan_page``). ``plan.rst`` in the
     docs project's source directory read against the main checkout
     (``core/needs.srcdir_of`` at ``main_root``), the checkout
     ``queue_path`` reads: the page sits beside the queue it renders and is
     committed with it. Under ``records.docs: store`` that is the store's
     docs project, and there the page is a data path of the queue record
     (``core/doclock.data_paths``), written at any time outside the docs
     lock as the queue file and the backlog page are (ADR-014 ruling 20).
     The page lives only where ``records.docs`` and ``records.queue`` name
     the same home: with the two apart, the page would be written into a
     tree nothing commits it from, so there is none, and the reason names
     both settings. With ``docs.format`` other than ``sphinx`` or no source
     directory there is none either.
   - **The write** (``QueueDomain.render``, ``PageRendered``,
     ``core/queue.PageRefused``). The queue is read and the page written
     under the queue's lock, so a page and the record it names cannot
     interleave with another edit. The text is privacy-scanned and refused
     whole on a finding. A file there without the marker is refused, never
     overwritten: a hand-written ``plan.rst`` is the project's. A current
     page is not written; a stale or hand-edited one is, and the result
     names which (``core/guidelines.managed_state``, which ``guidelines
     render``'s statuses now read too).
   - **When.** Every write of ``domains/queue`` (``add``, ``link``,
     ``begin``, ``mark``, ``fail``, ``requeue``, ``remove``, ``move``,
     ``after_reproduce``, and ``mirror`` when it wrote) renders again inside
     the same lock, after the record is written. The render never undoes or
     fails the edit: a refusal is kept on the domain (``notes``) and the CLI
     prints it on stderr. ``invariant queue render [--project]`` renders on
     demand. ``session merge``, and ``session resume`` finishing a landing,
     render after the merge and the ``session.after_merge`` steps, from the
     main checkout's queue as it stands once the branch is merged: the
     landing's last docs write, which never fails the landing and prints one
     line.
   - **``docs.plan_current``.** Registered at ``warn`` in ``loose``,
     ``standard`` and ``strict``, bound to no event and injected into no
     pipeline: the page lives in the main checkout and moves with queue
     edits a session's change does not make. It is reached as
     ``docs.assessment`` is, through a ``doctor`` row (``plan page``, one of
     ``DOCTOR_ROW_RULES``, absent where the rule is N/A), and ``invariant
     check`` reports it. N/A with no page home (naming why) and with neither a queue record
     nor a page. It fails when the queue exists and the page does not, when
     the page carries no marker, when it was edited by hand, and when the
     digest it names is not the queue's (stale). The session-plan part is
     not checked.
   - **Committed with the queue.** The reviewer's memory/config sync, which
     commits ``paths.queue`` under ``records.queue: repo``
     (:need:`DEC_ADR_027_D22`), commits the page with it; the
     compliance-reviewer source says so and the agents are re-rendered. A
     session branch does not commit the page once it is on the base branch,
     as it does not commit the queue.
   - **The starter** writes ``plan.rst`` from the empty queue with no
     session part, and its index's toctree names ``plan`` after
     ``backlog``. A hand-written release plan or roadmap is not touched.
   - **invariant's page** is ``docs/plan.rst``, rendered in this change from
     the committed ``docs/queue.json`` and named by a Plan toctree in
     ``docs/index.rst``. The landing that brings this build runs the build
     before it, so the main checkout's page is rendered again at the next
     queue edit; until then ``docs.plan_current`` warns.
   - **Patterns.** ``SessionPlan`` and ``PageRendered`` are value objects
     (``patterns.rst#domain.value-object``). The work-item test handed to the
     renderer is dependency injection
     (``patterns.rst#enterprise.dependency-injection``): ``core/queue``
     cannot import a domain. No other abstraction is added.

Consequences
------------

- A new project starts with the four layers named (unmapped until it maps
  its folders), the five SOLID rules as blockers, the pattern rule and the
  docs-first rules, and a catalogue it may edit; an existing project gets
  the same by running ``adopt``, and later defaults by running it again.
- SOLID findings stop a pipeline in ``standard``. The only ways on are a
  fix, a user-ruled exception, or the user changing the rule. This adds
  user turns where an agent used to decide; the agents' reports carry the
  exact command so the turn is one line.
- Docs-first is proven for every structural change in a pipeline with a
  design state, in every host, from the tree at the design boundary. A
  project's docs are now provably level with or ahead of its code at that
  boundary; the review still judges that they describe it.
- Layer direction is mechanical in Python, Dart and TypeScript, mechanical
  through a project's own test in Swift, and judgement elsewhere.
- The ruleset schema moves to version 2; an older invariant refuses a
  version-2 ruleset by its version.
- The plan becomes a generated docs page in every project; the JSON is
  unchanged.
