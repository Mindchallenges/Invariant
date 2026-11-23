Default guidelines gap — November 2026
======================================

On 2026-11-22 the user asked that every project invariant controls follow,
by default, the 4-layer clean architecture, the SOLID principles and a
catalogue of 32 design patterns. Each project keeps its architecture, design
decisions, backlog and plan as Sphinx documentation inside its own repository
and works docs-first: read the documentation, update the design documents,
then implement. This way the design documents always stay equal to or ahead of
the code. At least feature-architect and compliance-reviewer enforce these
rules. This page compares that request with what invariant shipped and held on
that date (main at the commit after 72a03af). The ADR that closes the gap records the user's
rulings.

What invariant ships to a new project
-------------------------------------

.. list-table::
   :header-rows: 1
   :widths: 25 35 40

   * - Requirement
     - Today
     - Gap
   * - 4-layer clean architecture
     - The starter ruleset (``src/invariant/data/scaffold/guidelines/ruleset.yaml``)
       holds two judgement rules, both on design records. It names no layers.
     - No layer model, no layer mapping, no dependency-direction rule.
   * - SOLID
     - Not named in any ruleset. The agent templates mention "clean
       architecture & dependency injection" only as a section a project's own
       checklist typically has.
     - No rule. A project without its own checklist is never asked.
   * - 32 design patterns
     - No catalogue anywhere in the engine.
     - No catalogue, and no rule that binds a design to one.
   * - Architecture, decisions, backlog in Sphinx
     - ADR-018 makes sphinx-needs rST mandatory. ``init`` writes a Sphinx
       starter. ``docs.builds_strict`` and ``docs.needs_traceable`` gate it,
       and the backlog can be sphinx-needs items.
     - Covered for new projects. An existing project is held to it only once
       migrated.
   * - Plan in Sphinx
     - The queue is ``docs/queue.json`` and session plans live in engine
       state. Neither is rendered into the docs.
     - The plan is not documentation.
   * - Docs-first: read the docs
     - ``docs.read_architecture`` is an exit gate of the design state, a
       blocker under the standard and strict profiles.
     - Covered.
   * - Docs-first: update design docs before implementing
     - ``docs.updated`` requires a structural change to update the
       architecture docs somewhere in the same change.
       ``docs.diagrams_current`` checks the diagrams.
     - Order is not checked. The docs may follow the code within one change,
       so they are never proven to be ahead.
   * - Mechanical checks per ecosystem
     - Only the Python adapter offers the ``imports`` engine. Swift, Dart and
       Node offer none, or only ``mask``.
     - A layer rule in a non-Python project can only be a judgement.
   * - Enforced by architect and reviewer
     - Both agents apply whatever ruleset ``invariant guidelines show``
       prints, or else the project's own checklist.
     - They enforce nothing on architecture shape unless a project wrote it
       down itself.

What invariant holds for itself
-------------------------------

``docs/guidelines/ruleset.yaml`` (ruleset 02ed45e4db9d) holds five rules: two
import bans around the oracle (``layers.*``), one conduct rule and the two
design-record rules. invariant's packages (``core``, ``domains``,
``adapters``, ``hosts``, ``oracle``, ``cli``) form a layered design that
``docs/architecture/overview.rst`` draws. No rule maps them onto four named
layers or checks the direction between them beyond the oracle bans. The
architecture, the decisions (ADR-001 to ADR-031) and the backlog are Sphinx.
The plan (``docs/queue.json``) is not.

What the other managed projects hold
------------------------------------

None of them sets ``guidelines.ruleset``, so every ``guidelines.*`` rule is
N/A there. Each keeps a hand-written checklist instead. ``docs.read_architecture``
applies to all of them as the engine default.

Heap (Swift and SwiftUI)
   ``docs/architecture/compliance_rules.rst`` holds a Boy-Scout rule plus 37
   numbered prose rules, with clean architecture and DI in section B. Its own
   4-ring layering (Frameworks, Views, ViewModels, Domain) is defined in
   ``scripts/arch_layering.yml`` and enforced by a fitness test with a ratchet
   baseline (its ADR-018). SOLID appears only as an audit grade, and there is
   no pattern catalogue. The docs are Sphinx with sphinx-needs: architecture,
   ADRs, backlog, and a release plan at ``docs/release_plan.rst``.
   ``Services/`` is not placed in any ring.

Darvag (Next.js and TypeScript)
   A checklist of about 45 bullets without ids sits in
   ``docs/architecture/overview.md``, beside a layer diagram and a
   dependency-rule table. There is no SOLID and no pattern catalogue. The docs
   are Markdown only, with no Sphinx, no ADR directory and no backlog or plan
   document, so the ADR-018 mandate has not reached it yet.

The remaining managed projects are compared in their own repositories during
their rollout sessions.

Open points settled by interview
--------------------------------

The user answered two interview rounds on 2026-11-22. The answers covered the
layer names and how a project maps onto them, which 32 patterns are in the
catalogue and how it binds a design, whether docs ahead of code is a
mechanical gate, where the default rules live, how layering is checked outside
Python, what happens to existing violations, and how the plan becomes
documentation. The ADR records each answer as a ruling.
