invariant documentation
=======================

Deterministic, scripted state for agent workflows. The user guide is for
anyone adopting the tool in a project of their own. The rest is invariant's
docs project (ADR-018): the architecture pages are the authoritative
dependency map, which the docs-first gate reads, and every decision,
architecture element, guideline rule and work item on them is a need with an
id, linked to what it shapes. ``invariant docs build`` builds it strictly, and
a change that touches it lands only on a green build. The audits record what
was checked against the mission and when.

.. toctree::
   :maxdepth: 1
   :caption: User guide

   guide/index

.. toctree::
   :maxdepth: 1
   :caption: Architecture

   architecture/overview
   architecture/state_model
   architecture/manifest
   architecture/cli
   architecture/oracle
   architecture/oracle-panel

.. toctree::
   :maxdepth: 1
   :caption: Decisions

   decisions/index

.. toctree::
   :maxdepth: 1
   :caption: Guidelines

   rules
   checklist

.. toctree::
   :maxdepth: 1
   :caption: Backlog

   backlog

.. toctree::
   :maxdepth: 1
   :caption: Audits

   audits/mission-audit-2026-08
   audits/authority-audit-2026-08
