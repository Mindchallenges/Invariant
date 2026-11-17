.. _adr-023:

ADR-023 — An item's fields change through item set, recorded as a field row in its history
==========================================================================================

.. dec:: An item's fields change through item set, recorded as a field row in its history
   :id: DEC_ADR_023
   :status: accepted
   :affects: COMP_DOMAINS_WORKITEM, COMP_CMD_ITEMS, COMP_ITEMS_SPHINX_NEEDS, COMP_ITEMS_MARKDOWN, COMP_ITEMS_JSON

   - **Status:** Accepted (2026-11-16). IDEA_051 left the command's shape,
     and whether a closed item's fields may change, to ``feature-architect``;
     this record is those choices, built in the same change.
   - **Date:** 2026-11-16
   - **Supersedes:** nothing. Extends the work-item domain's write surface
     (``docs/architecture/cli.rst``, "Work items") and the history rows the
     backends store (``docs/architecture/manifest.rst``, "The ``sphinx_needs``
     round trip").

Context
-------

``invariant item`` could file an item, move its status and record its
residue, but nothing could change a field once filed: ``affects``,
``severity``, ``priority``, ``batch``, ``tier``, ``effort``, ``area`` and
``decided_by`` were fixed at ``item new``. A consuming project (Dang,
2026-11-15) needed to add a newly declared rule need to the ``affects`` of two
open bugs, and its own instructions allow backlog writes only through
``invariant item``, so the only route, a hand edit of the backlog, was closed
to it (IDEA_051). The same gap hits this repository whenever a bug is
re-batched, re-prioritised on a ruling, or linked to a decision that did not
exist when it was filed.

Three things had to be settled: the command's shape, how a change is
recorded so the gates that read history do not mistake it for a status move,
and whether a closed item's fields may change at all.

.. dec:: item set changes fields: --field sets, --add and --remove edit a list, --unset drops
   :id: DEC_ADR_023_D1
   :status: accepted
   :part_of: DEC_ADR_023
   :affects: COMP_CMD_ITEMS, COMP_DOMAINS_WORKITEM, COMP_ITEMS_SPHINX_NEEDS

   ::

      invariant item set <id> [--field k=v]... [--add k=v]... [--remove k=v]...
                              [--unset k]... --reason <text> [--evidence <commit>]

   - **One subcommand, four operations, one write.** ``--field k=v`` replaces
     the value, spelled as ``item new --field`` spells it. ``--add k=v`` and
     ``--remove k=v`` treat the field as a comma-separated list, the form
     ``affects`` and ``decided_by`` take: ``--add`` appends each value not
     already there and creates the field if absent, ``--remove`` drops each
     named value and removes the field once it is empty. ``--unset k`` drops
     the field. Every operation in the command is checked before anything is
     written, and the store is saved once.
   - **Weighed against** a ``key+=X`` / ``key-=X`` grammar inside one
     ``--field`` (the requester's sketch) and a one-field ``item field <id>
     <key>`` command. The first makes ``-`` and ``+`` meaningful in a key and a
     value, which a value is free to contain; the second takes a command per
     field. Separate flags keep each operation's operand plain ``k=v``, and
     ``item set`` keeps the requester's verb.
   - **Refused, nothing written:** no operation given; ``--reason`` missing (the
     row exists to say why); ``id`` (other needs name the item by it; file a
     new item instead); ``status`` (naming ``item transition``); ``type``,
     ``title``, ``residue`` (naming ``item residue``) and ``history``; a key
     that is not a plain name (``[A-Za-z_][A-Za-z0-9_-]*``); a key the
     backend cannot hold as a field (on ``sphinx_needs``, the options the need
     directive itself owns, such as ``tags``, ``hide`` and ``delete``); a key
     under two different operations, or given twice to ``--field`` or
     ``--unset``; a value holding a line break; an empty ``--field`` value
     (``--unset`` drops a field); ``--remove`` of a value the field does not
     hold, and ``--unset`` of a field that is absent, as ``item residue
     --clear`` refuses a text that is not there. The item type adds no
     refusal of its own: the machines declare no field schema, and ``item
     new`` accepts any other key.
   - **The gates item new applies.** The text the command adds, each new
     value and the reason, is asked of ``items.decisions_linked`` exactly as
     ``item new`` asks it of its fields: a ``DEC_``, ``REQ_``, ``UI_`` or
     ``RULE_`` id no recorded build holds refuses the write. There is no
     ``--force``.
   - **A change that changes nothing is not written.** ``--field`` to the
     value already held and ``--add`` of a value already listed leave the
     field as it was; a command whose every operation does that writes
     nothing and records no row, and exits 0.
   - **Provenance as IDEA_052 built it.** ``--evidence`` names the commit the
     change rests on, resolved through ``core/git.resolve_commit`` before
     anything is written and recorded as a full hash
     (:need:`DEC_ADR_022_D1`). ``--ruling`` is not offered: a ruling buys a
     transition a route, and a field change has no route to buy.
   - **The store's exemption, unchanged.** The command writes the items
     store and nothing else, through the backend ``new`` and ``transition``
     write through, so ``items.exempt`` covers it the same way.

.. dec:: A field change is a history row with no from and to
   :id: DEC_ADR_023_D2
   :status: accepted
   :part_of: DEC_ADR_023
   :affects: COMP_DOMAINS_WORKITEM, COMP_ITEMS_SPHINX_NEEDS, COMP_ITEMS_MARKDOWN, COMP_ITEMS_JSON

   - **The row.** One per field the command changed, appended to the item's
     history in the order the fields were named::

        - field: affects
          was: COMP_CORE_ITEMS
          now: COMP_CORE_ITEMS, COMP_CORE_RULES
          reason: the rule check turned out to be the owner
          date: '2026-11-16'
          evidence: 0123456789abcdef0123456789abcdef01234567

     ``was`` and ``now`` are the field's whole value before and after, null
     where the field was or became absent, so the row reads on its own
     without replaying the operations. ``date`` is the day the change was
     made. ``evidence`` is present only when given.
   - **It carries no ``from`` and no ``to``**, and that is what makes it not a
     status move. ``item.history_matches_status`` walks only rows naming a
     ``to`` and ``item.bug_reproduced_before_fix`` looks for ``to:
     reproduced``, so both read a field row as the nothing it is to them,
     with no change to either rule. A ``to`` key on a field row, a status
     column with a null, was rejected: it would put the distinction in
     every reader instead of in the row.
   - **Every backend stores it as it stores any row**: in the markdown
     frontmatter, the JSON document, and the ``sphinx_needs`` entry's
     ``.. invariant-history:`` block, whose marker and reading are unchanged.
     On ``sphinx_needs`` the save is the patch BUG_065 fixed: the lines of the
     options changed and the history block are the only lines rewritten, and
     the re-read before writing now also refuses a removed field that would
     read back present.
   - ``item show`` prints a field row as ``<field>: <was> -> <now>`` with the
     reason, the date and the commit.

.. dec:: A closed item's fields may change
   :id: DEC_ADR_023_D3
   :status: accepted
   :part_of: DEC_ADR_023
   :affects: COMP_DOMAINS_WORKITEM, COMP_CMD_ITEMS

   - ``item set`` works on an item in any state of its machine, terminal
     states included. IDEA_051 left this open.
   - **Why allowed.** A terminal state fixes the status, which is what the
     machine guards; a field is how the item is linked and filed. The cases
     that reach a closed item are traceability ones: a ``decided_by`` that
     names a decision written after the close, or an ``affects`` that must
     follow a renamed element, which ``docs.needs_traceable`` and the strict
     build then require of closed entries as much as open ones. Refusing
     would send exactly those writes back to the hand edit IDEA_051 exists to
     remove.
   - **Why it is safe.** Nothing about what was proven is lost: the field
     row keeps the old value beside the new one, with the reason, the date
     and any commit, so a closed item's record of what it was filed as is
     still in its history. The status, the residue and the status history are
     out of the command's reach whatever the state.
