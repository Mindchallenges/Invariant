.. _adr-022:

ADR-022 — A transition names the commit it rests on without a ruling
====================================================================

.. dec:: A transition names the commit it rests on without a ruling
   :id: DEC_ADR_022
   :status: accepted
   :affects: COMP_DOMAINS_WORKITEM, COMP_CMD_ITEMS, COMP_MACHINE_BUG

   - **Status:** Accepted (2026-11-15). IDEA_052 left the spelling of the
     option to ``feature-architect``; this record is that choice, built in
     the same change.
   - **Date:** 2026-11-15
   - **Supersedes:** nothing. Amends the IDEA_019 shape described in
     ``docs/architecture/state_model.rst`` ("An already-fixed bug closes
     through a ruling"), where ``--ruling`` and ``--evidence`` came as a pair.

Context
-------

``item transition`` took ``--ruling`` and ``--evidence`` as one pair, built for
IDEA_019's ruled closure of a bug that was already fixed while it sat in
``filed``. So the ordinary ``reproduced -> fixed`` edge could not name the
commit that fixed the bug without a ruling nobody had made: ``--evidence``
alone was refused with "--evidence needs --ruling". The fix commit lived only
in prose (the curator's ``**Fixed <date>** (commit <hash>)`` line), where
nothing checks that it resolves. Reported from Dang (IDEA_052).

.. dec:: --evidence stands alone; only the ruled route needs --ruling
   :id: DEC_ADR_022_D1
   :status: accepted
   :part_of: DEC_ADR_022
   :affects: COMP_DOMAINS_WORKITEM, COMP_CMD_ITEMS, COMP_MACHINE_BUG

   - **The option keeps its name.** ``--evidence <commit>`` is the commit a
     transition rests on, given with or without a ruling. It is resolved to
     a full hash through ``core/git.resolve_commit`` before anything is
     written, a name that is no commit here is refused, and the hash is
     recorded on every history row the command writes, as a ruled
     closure's commit already was. A second option (``--commit``) was
     weighed and not taken: it would name the same field two ways, and the
     history row already calls it ``evidence``.
   - **The pairing holds one way only.** ``--ruling`` still needs
     ``--evidence``: a ruling with no commit is an assertion nothing can
     check. ``--evidence`` no longer needs ``--ruling``.
   - **A commit buys no route.** Only a ruling walks a missing edge through
     the one state between (``_one_step_between``). ``filed -> fixed`` given
     a commit and no ruling is refused as before, and the refusal names the
     ruled route; with a ruling it behaves as it did. On an edge the machine
     has, the commit annotates that one row and nothing else changes.
   - **No new field, no backend change.** The row carries ``evidence`` alone,
     the key a ruled row already had, so every backend stores it as it
     stores any row, ``sphinx_needs`` in its ``.. invariant-history:`` block,
     and ``item show`` prints it as it prints a ruled row's commit.
   - **Out of scope.** Whether a ``fixed`` transition must name a commit is
     not decided here (IDEA_052's open question).
