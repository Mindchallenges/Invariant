.. _adr-031:

ADR-031 — A finished session ends its own window, and a window the user keeps no longer holds the chain
=======================================================================================================

.. dec:: A finished session ends its own window, and a window the user keeps no longer holds the chain
   :id: DEC_ADR_031
   :status: proposed
   :amends: DEC_ADR_027, DEC_ADR_027_D5, DEC_ADR_027_D8, DEC_ADR_027_D10, DEC_ADR_027_D22, DEC_ADR_027_D30, DEC_ADR_027_D47, DEC_ADR_027_D53, DEC_ADR_027_D54, DEC_ADR_027_D59, DEC_ADR_027_D61, DEC_ADR_005, DEC_ADR_005_D7
   :affects: COMP_DOMAINS_SESSION, COMP_CMD_SESSION, COMP_CLI_HOST_CLOSE, COMP_ORACLE_CHAINSTEP, COMP_ORACLE_CHAIN, COMP_ORACLE_ATTENTION, COMP_ORACLE_STARTSESSION, COMP_CORE_MANIFEST, COMP_DOMAINS_QUEUE, COMP_CORE_QUEUE, COMP_CMD_QUEUE, COMP_DATA_AGENTS, COMP_ORACLE_CHAINVIEW, COMP_ORACLE_PANEL, COMP_CMD_ORACLE

   - **Status:** Proposed (2026-11-22). Rulings 1 to 12 are the user's:
     1 to 3 settled on IDEA_064 when it was filed, 4 to 12 the answers to the
     design interview of the same day. The design-pass readings, each marked
     as such, await the user's approval of the plan; no increment is queued
     before it.
   - **Date:** 2026-11-22
   - **Supersedes:** nothing. Amends :ref:`ADR-027 ruling 7
     <adr-027-ruling-7>` and :ref:`ruling 21 <adr-027-ruling-21>`, with
     :need:`DEC_ADR_027_D5`, :need:`DEC_ADR_027_D8`,
     :need:`DEC_ADR_027_D10`, :need:`DEC_ADR_027_D22`,
     :need:`DEC_ADR_027_D30`, :need:`DEC_ADR_027_D47`,
     :need:`DEC_ADR_027_D53`, :need:`DEC_ADR_027_D54`,
     :need:`DEC_ADR_027_D59` and :need:`DEC_ADR_027_D61`; deprecates
     :need:`DEC_ADR_005` (``session handoff``) and reuses
     :need:`DEC_ADR_005_D7`'s closer for a second caller. Builds on
     :need:`DEC_ADR_030_D6` unchanged.

Context
-------

ADR-027 moved the choice of what runs next to the oracle: a finishing
session queues its successor and ends, and the watcher's chain step starts the
next queue entry once the project is free (ruling 7). "Free" means one at a
time: no live session and no live Claude window in the project
(:need:`DEC_ADR_027_D5`). A chained session that lands is torn down, but its
window stays open, and that window holds the project. Ruling 21 (IDEA_062)
lets the oracle close such a window: after 5 quiet minutes it types a
question into the tab, and closes the window when the session answers with
``invariant oracle chain consent --close``. A window the user typed into
after the teardown is the user's: it is never asked or closed, and it holds
the project until the user closes it.

On 2026-11-22 this stalled invariant's own chain. A landed window showed
"landed: users" in ``oracle chain status`` and three queued entries could
not start. Meanwhile another managed project rolled on without the stall,
because each of its finished sessions ran ``session handoff``, which opens
the next window and ends its own (:need:`DEC_ADR_005_D7`). That path is the
one ruling 7 retired for agents. It also bypasses the chain: its starts
honour no cap, follow no queue order, and are not counted, so chain status
showed no starts in that project on a day when several sessions ran.

The user asked for one built-in mechanism in which a finished session ends
its own window and the oracle starts the next queue entry, for every managed
project (IDEA_064).

Rulings
-------

Settled with the user on IDEA_064 (2026-11-22):

.. _adr-031-ruling-1:

**Ruling 1 — a final landing ends the window.** After a final landing (the
teardown of a session whose plan is complete) the session exits on its own.
Its consent to close is an engine record made at the landing, never read
from transcript text, and its window ends the way a handoff ends a window
(:need:`DEC_ADR_005_D7`'s closer). The oracle sees the window gone and starts
the next queue entry within seconds, under the chain's caps, queue order and
start count. The oracle still decides what runs next; ruling 7 stays in that
sense.

.. _adr-031-ruling-2:

**Ruling 2 — a window the user typed into stays open but stops holding the
chain.** A landed window the user typed into after the landing stays open
and is never closed, and it no longer counts against one at a time: the next
session starts beside it. This amends ADR-027 ruling 21 and "an open window
holds the chain".

.. _adr-031-ruling-3:

**Ruling 3 — one mechanism for every managed project.** A project still
chaining through ``session handoff`` moves to queue-and-end in its own
session, not in invariant's repository, and its hand-off instructions are
retired.

The user's answers to the design interview (2026-11-22):

.. _adr-031-ruling-4:

**Ruling 4 — telling a typed-into window apart.** The engine stamps the
person's prompt count (the attention record's ``prompts``) at the teardown,
and the closer checks it again just before it ends the host. A prompt after
the landing cancels the exit, and the window becomes the user's. Prompts made
during the session itself do not keep it open. In the user's words, also
"an ongoing prompt before landing so if the landing of the session's own job
came, but there are still tasks that are running it should wait for them to
land, and then session should not be closed automatically". So a window
with work still running at the landing (a turn in progress beyond the
landing's own, background tasks, a prompt the user sent) is never closed
automatically.

.. _adr-031-ruling-5:

**Ruling 5 — what ongoing work holds.** For that ongoing-work case, the chain
holds the project only until the running work finishes (the turn idle, no
background tasks). Then the window counts as the user's, stays open, and the
next session starts beside it.

.. _adr-031-ruling-6:

**Ruling 6 — bounds.** There is no cap on landed windows the user keeps; they
never hold the project. Only a landed chained window qualifies. A window the
user started by hand still holds its project as it does today. The machine
cap and the daily cap still bound starts.

.. _adr-031-ruling-7:

**Ruling 7 — ruling 21 becomes the fallback.** Ruling 21's quiet period, its
typed consent ask and its phone notice (Close, Leave open) are kept, as a
fallback only: for a landed chained window that did not end itself (the host
has no closer, the exit failed, the setting is off, a session older than
this record). How Leave open treats the chain is left to the design, weighed
against ruling 2 (:need:`DEC_ADR_031_D9`).

.. _adr-031-ruling-8:

**Ruling 8 — whose windows end themselves.** By default only a chained
session ends its own window; a session the user started by hand keeps its
window. It is configurable per project: ``oracle.chain.end_window: chained |
all | off``, default ``chained``. ``all`` includes hand-started sessions and
sessions a handoff opened; ``off`` ends none, and ruling 21's ask is then the
only way a landed chained window closes.

.. _adr-031-ruling-9:

**Ruling 9 — a follow-up goes on the queue by rank.** In the user's words:
"the handoff idea was something before we introduced the queue on Oracle and
then normal scenario there is no need for handoff by the session because
Oracle knows what to run next, but if session needs a follow up, which is
not in the queue, so the session can add a new queue [entry], which will get
prioritized according to its priority in the queue." Settled: a session's
follow-up is placed by the rank of its item (tier, priority, id:
:need:`DEC_ADR_027_D47`'s rule, ``core/queue.rank_key``) among the ranked
entries; hand-queued entries keep their places. A follow-up with no work item
(a free label) must name a priority (``--priority high|medium|low``) and is
placed as an item of that priority.

.. _adr-031-ruling-10:

**Ruling 10 — handoff is deprecated.** ``session handoff`` keeps working for
now, for the user's own use (context resets), marked deprecated, and is
removed in a later ADR once nothing uses it. No agent instruction and no
shipped template names it. A window a handoff opens is counted in chain
status and holds one at a time like any window started by hand.

.. _adr-031-ruling-11:

**Ruling 11 — chain status counts every start.** Chain status shows every
session started in the project ("N started today (M chained)"). The daily
cap counts chained starts only.

.. _adr-031-ruling-12:

**Ruling 12 — the model guard is unchanged.** Every new window keeps
ADR-030's forbidden-model guard: every launch goes through
``_open_claude_window`` with ``--model``, and is refused when the model is
forbidden (:need:`DEC_ADR_030_D6`). A self-exit opens no window.

Decisions
---------

.. dec:: The engine records the landing, the CLI schedules the exit through a host seam, the oracle follows
   :id: DEC_ADR_031_D1
   :status: proposed
   :part_of: DEC_ADR_031
   :affects: COMP_DOMAINS_SESSION, COMP_CMD_SESSION, COMP_CLI_HOST_CLOSE, COMP_ORACLE_CHAINSTEP

   Each part has the owner ADR-027 D9 and ADR-007 already give its kind:

   - **The consent is an engine record.** ``domains/session`` writes the
     landing record on the session record, in the teardown's own save
     (:need:`DEC_ADR_031_D2`). It names no host and no model: a host session
     id, a prompt count, task ids and a few words.
   - **The exit is the CLI's, through a host seam.** ``cmd_session_teardown``
     asks the seam ``_landing_closer`` for the host that ran it. For Claude
     Code that is the process ``CLAUDE_PID`` names, ended by
     ``cli/host_close``'s detached closer (:need:`DEC_ADR_031_D4`,
     :need:`DEC_ADR_031_D5`). ``CLAUDE_PID``, the Terminal tab and the
     per-process records stay in ``hosts/claude_code_launch`` and in
     ``cli/host_close``'s ``claude_code_seams``, where they are today.
   - **The oracle follows.** The chain step reads the landing record through
     an inert store, as ``chain.session_landed`` reads the teardown's
     heartbeat today, and acts on what it says (:need:`DEC_ADR_031_D6` to
     :need:`DEC_ADR_031_D9`). It writes no project record and types nothing
     on this path.
   - No arrow is added: ``domains/`` imports neither ``oracle/`` nor
     ``hosts/``; the CLI reads the attention record (``oracle/attention``)
     and hands the counts down as values; ``oracle/`` imports no host. The
     exit does not need the oracle to be running.

.. dec:: A final teardown writes the landing record, the consent the closer acts on
   :id: DEC_ADR_031_D2
   :status: proposed
   :part_of: DEC_ADR_031
   :affects: COMP_DOMAINS_SESSION, COMP_CMD_SESSION, COMP_ORACLE_ATTENTION

   *The design pass's reading of ruling 1's "recorded at the landing": the
   command is ``session teardown`` itself, the one the compliance-reviewer
   already runs in FINAL mode (Phase 8d), so no agent text changes for it.*

   - ``invariant session teardown`` on a session whose plan is complete (no
     open deliverable, or no plan: :need:`DEC_ADR_027_D1`'s "done") writes
     ``landing`` on the session record in the same save that moves it to
     ``torn_down``. A teardown with a deliverable open (only a forced merge
     gets there) writes none: that session did not finish, and it ends no
     window.
   - ``landing`` holds: ``at`` (the teardown's epoch, the ``heartbeat`` it
     stamps); ``host`` (the bound host session id); ``prompts``, the person's
     prompt count from the host's attention record at that moment, ``null``
     when the record cannot be read; ``background``, the ids of the open
     entries in that record's background ledger, ``null`` likewise;
     ``carrier``, the work that ran the teardown (``main`` for the window's
     own turn, ``task`` with its id for a background task, ``null`` while
     unknown), supplied by the host (:need:`DEC_ADR_031_D5`); ``exit``,
     what was decided (:need:`DEC_ADR_031_D3`): ``scheduled``, ``kept_open``
     (``--keep-open``), ``no_closer`` (no host the seam can end), ``off`` (the
     setting) or ``not_chained``; ``wait``, the bound of
     :need:`DEC_ADR_031_D5` in seconds; and ``outcome`` and ``outcome_at``,
     ``null`` until the closer writes them.
   - ``--keep-open`` on ``session teardown`` opts one landing out, as on
     ``session handoff``. It records ``kept_open``, so a chained window that
     did not end itself still meets ruling 21's fallback.
   - The landing's own work is not ongoing work: the turn that ran the
     teardown, and the background task that carried it when the landing runs
     in one (the compliance-reviewer usually does). Its prompt is already in
     the stamp, and its end is what the closer waits for.
   - The record is kept across builds that predate it by the round-trip
     contract (BUG_061), as ``handoffs`` and ``reports`` are.
   - *Weighed against:* a new command the reviewer runs after the teardown
     (one more step an agent can forget), and recording the consent in the
     chain ledger (a project with no oracle running still ends its window, and
     the oracle is a reader of session records, not their writer).

.. dec:: Whose landing schedules an exit is oracle.chain.end_window, judged at the teardown
   :id: DEC_ADR_031_D3
   :status: proposed
   :part_of: DEC_ADR_031
   :affects: COMP_CORE_MANIFEST, COMP_CMD_SESSION, COMP_ORACLE_CHAINSTEP

   - ``oracle.chain.end_window`` is ``chained`` (the default), ``all`` or
     ``off`` (ruling 8). ``oracle.chain.end_window_wait`` is minutes, a number
     above 0, default 10 (:need:`DEC_ADR_031_D5`). Anything else is refused at
     load. Both are per project, read at the teardown into the landing record,
     so a change applies from the next landing (as :need:`DEC_ADR_027_D60`'s
     numbers are).
   - **Chained** is a fact of the chain ledger: the session is the one bound
     to its project's ``current`` chained start (:need:`DEC_ADR_027_D15`).
     The teardown command reads the ledger without its lock
     (``chainstep.read_ledger``, as ``oracle next`` reads it). A ledger that
     cannot be read, or no oracle home, reads as not chained, so under
     ``chained`` nothing is ended: it errs toward an open window.
   - ``all`` schedules the exit for every final landing: chained,
     hand-started and handoff-opened sessions alike. ``off`` schedules none.
   - *The design pass's reading:* the setting is the project's, under
     ``oracle.chain``, because the windows it ends are the chain's to count;
     the machine's user configuration may set it for every project in the
     usual layering.

.. dec:: The exit is a host seam; Claude Code's is the handoff's detached closer on CLAUDE_PID
   :id: DEC_ADR_031_D4
   :status: proposed
   :part_of: DEC_ADR_031
   :affects: COMP_CMD_SESSION, COMP_CLI_HOST_CLOSE, COMP_HOSTS_CLAUDE_CODE_LAUNCH

   - ``_landing_closer`` is a seam beside ``_handoff_closer``, which the suite
     replaces for every test, so no test ends a process. It answers the host
     to end, or none. For ``host.kind: claude_code`` it is the process
     ``CLAUDE_PID`` names (``claude_code_launch.HOST_PID_ENV``), accepted only
     when ``ps`` names it ``claude`` and its host session is the landing's
     ``host``. A subagent's shell call (the compliance-reviewer's) carries its
     window's ``CLAUDE_PID``, so the reviewer's teardown names the window that
     ran it.
   - With a host to end, the command starts ``cli/host_close`` as a detached
     process (``DetachedCloser``, its own session, none of the command's
     streams) in a new landing mode, handing it the pid, the session and its
     state root, the stamp and the wait. It prints one line saying the window
     will end once the landing's own work is over, and how to keep it (``--keep-open``).
   - No host to end (no ``CLAUDE_PID``, a pid that is not ``claude``, another
     host session, a host kind with no closer) records ``no_closer`` and ends
     nothing. A chained window then meets ruling 21's fallback.
   - The end itself is :need:`DEC_ADR_005_D7`'s, as amended by BUG_100: the
     same-process check (still ``claude``, the same start, the same tty), then
     ``closehost.end_then_close_tab``. Over Remote Control or outside
     Terminal.app, Claude ends and the window is left as the handoff leaves it.
   - **The model guard (ruling 12).** A self-exit opens no window, so it meets
     no model. Every window the chain opens next still goes through
     ``_open_claude_window`` with ``--model``, and a forbidden one is refused
     (:need:`DEC_ADR_030_D6`), unchanged.

.. dec:: The closer waits for the landing turn to end, checks the stamp again, and only then ends the host
   :id: DEC_ADR_031_D5
   :status: proposed
   :part_of: DEC_ADR_031
   :affects: COMP_CLI_HOST_CLOSE, COMP_ORACLE_ATTENTION, COMP_ORACLE_SIGNALS, COMP_DOMAINS_SESSION

   The closer runs in the CLI layer, so it may read ``oracle/attention`` and
   ``oracle/signals``; it judges the window with ``signals.turn_state`` over
   the host's own attention record and per-process record, the one answer
   every reader uses (BUG_087). It waits at least
   ``claude_code_launch.CLOSE_DELAY_SECONDS`` (the hook budget of
   :need:`DEC_ADR_005_D7`), then looks every few seconds until ``wait``
   (``oracle.chain.end_window_wait``) has passed. It writes exactly one
   ``outcome`` on the landing record, through ``domains/session`` as a
   standstill write (the shape :need:`DEC_ADR_005_D6` gave a consumed note):

   **Telling the landing's own work from other work** (rulings 4 and 5).
   The engine only stamps and compares; the host supplies the facts:

   - The stamp (:need:`DEC_ADR_031_D2`) holds the ids of the background
     entries open at the teardown. The ``carrier`` says which of them, if
     any, carried the landing. The host adapter writes it on the landing
     record from the hook that saw the teardown's shell call, through
     ``domains/session``, within that hook's budget and so before the closer's
     first look. For Claude Code that is ``PostToolUse`` of the call: a
     subagent's call carries ``agent_id``, which the adapter keys its
     background ledger entry by, so the carrier is that entry (``task``); a
     call with no ``agent_id`` is the window's own turn (``main``). Proving
     that the entry and the call name the same id is increment 1's.
   - **Other work at the landing** is every stamped entry except the
     carrier's: all of them for ``main``, all but one for ``task``. A
     carrier that is ``null`` at the first look, or names an entry the stamp
     does not hold, is unknown, and then every stamped entry counts as other
     work: a doubt keeps the window open. A landing with no entry open needs
     no carrier.
   - **Waiting for the landing's own work.** The closer waits until the
     carrier's entry has left the background ledger (for ``task``) and the
     window's turn is idle: the parent's turn that the task's completion
     notice starts, in which it relays the report, is the landing's too. A
     completion notice is never counted as the person's prompt (the
     attention record counts only the person's turns), so it neither cancels
     the exit nor needs a carve-out.
   - **Other work after the landing** is whatever is open once the
     landing's own work is over: a background entry the stamp did not hold
     (the parent started more work), a permission prompt or a question, or a
     turn still running when ``wait`` runs out.

   +-------------+-------------------------------------------------------------+
   | Outcome     | When                                                        |
   +=============+=============================================================+
   | ``users``   | at any look, the prompt count is above the stamp: the       |
   |             | person typed after the landing (ruling 4); nothing is ended |
   +-------------+-------------------------------------------------------------+
   | ``ongoing`` | other work at the landing; or, once the landing's own work  |
   |             | is over, other work after it; or the landing's own work not |
   |             | over when ``wait`` runs out. Nothing is ended (ruling 4)    |
   +-------------+-------------------------------------------------------------+
   | ``unread``  | the stamp was ``null``, or the attention record cannot be   |
   |             | read at a look: nothing proves the window is free, so       |
   |             | nothing is ended                                            |
   +-------------+-------------------------------------------------------------+
   | ``ended``   | the landing's own work is over, the turn is idle (not busy, |
   |             | not waiting, no background work), the prompt count equals   |
   |             | the stamp when read again immediately before the signal,    |
   |             | and the process is still the same ``claude``: the host is   |
   |             | ended and its tab closed                                    |
   +-------------+-------------------------------------------------------------+
   | ``failed``  | the end raised, or the process was no longer the same one   |
   +-------------+-------------------------------------------------------------+

   - Every doubt keeps the window open: an unreadable record, an unknown
     carrier beside open work, an unknown liveness, a reused pid. Nothing here
     ever ends a window because a read failed.
   - The two reads of the count (the last look and the one just before the
     signal) leave a gap of well under a second in which a prompt sent cannot
     be seen. That is the risk :need:`DEC_ADR_027_D55` already took for
     someone typing without sending.
   - *The design pass's reading:* at most one task carries a landing, so the
     carrier names one entry. A host that cannot name it still ends a window
     whose landing ran with nothing else open; with work open, it keeps the
     window, and ruling 21's fallback or :need:`DEC_ADR_031_D8` settles it.

.. dec:: The chain step follows the landing record, and a window gone after its own exit is not a failure
   :id: DEC_ADR_031_D6
   :status: proposed
   :part_of: DEC_ADR_031
   :affects: COMP_ORACLE_CHAINSTEP, COMP_ORACLE_CHAIN

   - The tick that marks a chained start done (:need:`DEC_ADR_027_D53`) reads
     the session's landing record (``chain.session_landing``, the inert read
     ``chain.session_landed`` already makes, widened to return the record)
     and puts it on ``landed`` as ``exit``. Two states join ruling 21's:
     ``ending`` (an exit scheduled, no outcome yet) and ``ongoing``; one
     outcome joins ``landings``: ``ended_itself``.
   - **Ending.** The step asks nothing and types nothing. The window gone
     after an ``ended`` outcome ends the landing ``ended_itself`` on that
     tick, with no ``GONE_SECONDS`` wait, so the next start comes on the next
     tick under the usual limits (ruling 1's "within seconds"). A window gone
     with no outcome yet ends ``ended_itself`` after ``GONE_SECONDS``, as
     today's ``gone``. Both release the session (:need:`DEC_ADR_027_D30`) and
     say so with a silent notice.
   - **Not D15's failure.** :need:`DEC_ADR_027_D15`'s window-gone failure
     judges only the ``current`` start, and only while its session is not
     torn down: once the session row is gone, ``chain.session_end`` reads the
     record first and a teardown with the plan complete is ``done``. A
     self-exit always follows the teardown, so it is followed under
     ``landed`` and can never be read as that failure. A window that dies
     before its teardown is that failure still, unchanged.
   - **The other outcomes.** ``users`` moves the window to ``kept``
     (:need:`DEC_ADR_031_D7`); ``ongoing`` is :need:`DEC_ADR_031_D8`'s;
     ``unread``, ``failed``, and an exit never scheduled (``kept_open``,
     ``no_closer``, ``off``, a session with no landing record) take ruling
     21's fallback from ``watching`` (:need:`DEC_ADR_031_D9`).
   - **A closer that never answered.** ``ending`` with no outcome once
     ``wait`` plus 120 s has passed since the landing (the closer died, the
     Mac slept) is the fallback too: ``watching``, then the ask once quiet.
   - The step keeps reading the person's prompts on every landed window, as
     :need:`DEC_ADR_027_D54` does: a prompt after the landing makes an
     ``ending`` window the user's even before the closer looks.

.. dec:: A window the user keeps is listed in the ledger's kept and holds nothing
   :id: DEC_ADR_031_D7
   :status: proposed
   :part_of: DEC_ADR_031
   :affects: COMP_ORACLE_CHAINSTEP, COMP_ORACLE_CHAIN, COMP_ORACLE_STARTSESSION, COMP_CMD_ORACLE

   - The project's ledger gains ``kept``: one record per landed chained
     window the user keeps (``session``, ``host``, ``name``, ``pid``,
     ``since``, ``why``: ``users``, ``ongoing`` once its work finished, or
     ``left_open``). The landing ends with that outcome in ``landings``, so
     ``landed`` is free for the next start's landing.
   - One at a time skips a live host that ``kept`` names, in the step and in
     ``oracle next`` alike: ``startsession.open_windows`` takes the hosts to
     pass over, as the step's session rule passes over ``released``
     sessions. A kept record leaves when its window is seen gone for
     ``GONE_SECONDS``.
   - **No cap** (ruling 6). The machine cap counts chained starts being
     followed (``current``), which a kept window is not, and the daily cap
     counts starts, which it is not either.
   - **Only landed chained windows** (ruling 6). A window started by hand, by
     ``start-session`` or by a handoff, holds its project as before, also
     under ``end_window: all``: that setting ends such a window, it does not
     release one the user keeps.
   - **A session started in a kept window holds.** If the user runs
     ``invariant session start`` there, its live session row holds the
     project under :need:`DEC_ADR_027_D5`'s session rule like any live
     session; the kept record only stops the window itself from holding.
   - Amends :need:`DEC_ADR_027_D5`'s "or a live Claude window, however it was
     started" and :need:`DEC_ADR_027_D30`'s "a live Claude window still holds
     the project, whoever's it is" for these windows only.

.. dec:: Ongoing work holds the project until the window is idle, then the window is the user's
   :id: DEC_ADR_031_D8
   :status: proposed
   :part_of: DEC_ADR_031
   :affects: COMP_ORACLE_CHAINSTEP, COMP_ORACLE_SIGNALS

   - A landing whose outcome is ``ongoing`` is followed in the ``ongoing``
     state, and it holds the project under one at a time while its window
     lives (ruling 5). The step never asks it, never types into it and never
     closes it.
   - The first tick whose row reads ``turn_state`` ``idle`` (not busy, not
     waiting on the person, no background work) moves it to ``kept`` with
     ``why: ongoing``, and the chain goes on beside it. A prompt the user
     sends meanwhile changes nothing until the window is idle: the running
     work is what holds.
   - A window gone while ongoing ends the landing ``gone``, and the chain goes
     on.
   - An attention record that cannot be read while ongoing is
     :need:`DEC_ADR_027_D54`'s ``unread``: the notice goes to the user, and
     the window holds until the user answers (:need:`DEC_ADR_031_D9`).
     *The design pass's reading:* an unreadable window errs toward an open
     window and a waiting chain, never toward a close and never toward two
     sessions working in one project unseen.

.. dec:: Ruling 21 is the fallback; Leave open, like the user's window, stops holding
   :id: DEC_ADR_031_D9
   :status: proposed
   :part_of: DEC_ADR_031
   :affects: COMP_ORACLE_CHAINSTEP, COMP_CMD_ORACLE

   - A landed chained window that did not end itself (:need:`DEC_ADR_031_D6`'s
     list) is followed exactly as :need:`DEC_ADR_027_D53` to
     :need:`DEC_ADR_027_D60` say: quiet ``close_ask_after`` minutes, the typed
     ask, consent and the close, the notice with Close and Leave open. While
     it is watched, asked, consented, closing or notified it holds the
     project, as today: the oracle still expects to settle it.
   - **The user's window** (:need:`DEC_ADR_027_D54`'s ``users``) moves to
     ``kept``: it is never asked and never closed, and it no longer holds
     (ruling 2). Its one notice now says the chain goes on beside it.
   - **Leave open stops holding** (*the design pass's reading, which the user
     may overrule*). Ruling 6 says the windows a user keeps never hold, and
     Leave open is the user keeping a window by an explicit answer, which is
     stronger evidence than a typed prompt. So ``leave-open`` moves the window
     to ``kept`` with ``why: left_open``, and the chain goes on beside it.
     Holding it would leave the fallback the one path that still stalls the
     chain on a window the user chose to keep.
   - An unanswered notice still holds, until Close or Leave open: the user
     has not chosen, and the window may be working.
   - Amends :need:`DEC_ADR_027_D54` (the user's window "holds the
     project") and :need:`DEC_ADR_027_D59` (Leave open "holds its project
     until the user closes it").

.. dec:: A session's follow-up goes on the queue by rank; a free label names its priority
   :id: DEC_ADR_031_D10
   :status: proposed
   :part_of: DEC_ADR_031
   :affects: COMP_DOMAINS_QUEUE, COMP_CORE_QUEUE, COMP_CMD_QUEUE, COMP_ORACLE_CHAIN

   - ``invariant queue add`` run by a session (``added_by`` the session's id,
     :need:`DEC_ADR_027_D2`), with no ``--at``, places the entry by rank: before
     the first **queued ranked** entry that ranks after it, else at the end,
     :need:`DEC_ADR_027_D47`'s rule through ``core/queue.rank_key``. Ranked
     entries are the mirror's (``added_by: backlog``) and the ones placed by
     rank (``placed: rank`` on the entry). Hand-queued entries (``added_by:
     user``), the oracle's, and started, done and failed entries are never
     moved and never ranked against.
   - A work-item ``ref`` ranks by its item's tier, priority and id, read from
     the main checkout's backlog (or the store), as the mirror reads it. An
     item the main checkout does not hold yet (filed in the session's
     worktree) ranks as its ``--priority`` says, else last.
   - A free label from a session is refused (exit 2, nothing written) without
     ``--priority high|medium|low``. It ranks as an item of that priority,
     its tier taken from ``--tier N`` when given, else from the item it
     ``--serves``, else none, and an equal rank goes behind the entries
     already there. *The design pass's reading:* a free label with no tier
     ranks after every tiered item, as :need:`DEC_ADR_027_D12` ranks a
     missing tier; ``--tier`` or ``--serves`` places it among them. The
     entry keeps ``rank: {tier, priority}`` so the mirror places later items
     around it.
   - ``--at N`` from a session still places the entry exactly there, and it
     is then a hand-placed entry: the ruling's "by rank" is the default, not
     a ban. A user's ``queue add`` (no session bound) appends, as
     :need:`DEC_ADR_027_D10` says.
   - The mirror ranks a new item against the session-placed entries too, so
     an item and a follow-up of the same rank keep their order by id.
   - Amends :need:`DEC_ADR_027_D8` and :need:`DEC_ADR_027_D22` (the
     successor under ``THEN:`` is placed by rank, not appended),
     :need:`DEC_ADR_027_D10` (the append default, for a session's entry) and
     :need:`DEC_ADR_027_D47` (the ranked entries).

.. dec:: session handoff is deprecated, and no shipped text names it
   :id: DEC_ADR_031_D11
   :status: proposed
   :part_of: DEC_ADR_031
   :affects: COMP_CMD_SESSION, COMP_DATA_AGENTS, COMP_CORE_MANIFEST

   - ``invariant session handoff`` keeps working unchanged, for the user's own
     context resets (ruling 10). It prints one line on stderr before it acts:
     deprecated, a successor goes on the queue (``invariant queue add``), and
     the oracle starts it. ``--help`` says the same. It exits as before.
   - No shipped agent, skill or template names ``session handoff``: the
     compliance-reviewer's successor paragraph loses its "or ``invariant
     session handoff``", and any other shipped mention goes. A test reads the
     shipped data and fails on the name.
   - A window a handoff opens is a window started by hand: it holds its
     project under one at a time (:need:`DEC_ADR_027_D5`, unchanged) and is
     counted in chain status (:need:`DEC_ADR_031_D12`). Under ``end_window:
     all`` its final landing ends it as any other.
   - :need:`DEC_ADR_005_D7`'s closer stays, and now has two callers: the
     handoff and the landing (:need:`DEC_ADR_031_D4`).
   - Removing the command is a later ADR, once no managed project's
     instructions use it (ruling 3, :need:`DEC_ADR_031_D14`).
   - Amends ADR-027 ruling 7 and :need:`DEC_ADR_027_D8` ("``session handoff``
     keeps its meaning for the user") and :need:`DEC_ADR_005`.

.. dec:: Chain status counts every session started in the project; the daily cap counts chained starts
   :id: DEC_ADR_031_D12
   :status: proposed
   :part_of: DEC_ADR_031
   :affects: COMP_CMD_ORACLE, COMP_ORACLE_CHAINSTEP, COMP_DOMAINS_SESSION

   - ``oracle chain status`` shows "N started today (M chained)" per project.
     N is the project's sessions whose record was created on the machine's
     local day, read by the CLI through an inert store from the project's
     state root (torn-down and abandoned records included); M is the
     ledger's starts that day, the number :need:`DEC_ADR_027_D16`'s daily cap
     already counts. ``--json`` gives both, ``sessions_today`` and
     ``chained_today``.
   - The daily cap still counts M only (ruling 11). A project that chains
     through handoff shows its true starts in N with M at 0, which is what
     IDEA_064 found.
   - *The design pass's reading:* "started" is an invariant session started
     (its record created), not a window opened, since a window with no
     session leaves no record to count after it closes.

.. dec:: The phone says a window ended itself, and that a kept window lets the chain go on
   :id: DEC_ADR_031_D13
   :status: proposed
   :part_of: DEC_ADR_031
   :affects: COMP_ORACLE_CHAINVIEW, COMP_ORACLE_PANEL

   The words, settled here and written into :doc:`oracle-panel.md
   </architecture/oracle-panel>` when increment 4 builds them; no new screen,
   component or action (ADR-027 ruling 16):

   - The chain read carries ``landed``'s new states and the project's
     ``kept`` count, and the two counts of :need:`DEC_ADR_031_D12`.
   - **Now, while the chain follows no start**: "Landed · ending its own
     window" (``ending``); "Landed · work is still running in its window; the
     chain waits until it finishes" (``ongoing``). A window that ended itself
     is no longer shown.
   - **The kept window** is no longer shown on Now, since it holds nothing.
     The project's chain line adds " · N windows kept open" when nonzero.
   - **The Window open card** (fallback only): its note becomes "Close ends
     it; Leave open keeps it, and the chain goes on beside it." Its whys are
     unchanged. The "It landed, and its window still holds the project." why
     goes, since the user's window holds nothing.
   - **The Leave open sheet** says "The chain goes on beside it."
   - **What is going on** gains "N started today (M chained)".
   - Amends :need:`DEC_ADR_027_D61`.

.. dec:: Each other managed project moves to queue-and-end in a session of its own
   :id: DEC_ADR_031_D14
   :status: proposed
   :part_of: DEC_ADR_031
   :affects: COMP_DATA_AGENTS

   - Ruling 3's migration is a chore per managed project, queued in that
     project (``invariant queue add --project <name>``) and run in its own
     session, never in this repository. Its brief: retire the project's
     instructions that tell a session to hand off (its instruction files, its
     briefs, its memory), re-render its agents (``invariant agents
     install``), and check that its chain status counts its starts.
   - This repository records no other project's name, instructions or
     content: the chore's brief is written when it is queued, in the project
     it serves.
   - Until a project has moved, its handoff windows hold its project as any
     window started by hand (:need:`DEC_ADR_031_D11`), and its chain status
     counts them.

Increments
----------

Each increment lands on its own; the ones before it are not needed for it to
be correct, only for the whole ruling set to hold.

+-----+------------------------------------------------------------------+
| Inc | Builds                                                           |
+=====+==================================================================+
| 1   | The landing record at a final ``session teardown``, ``--keep-    |
|     | open``, ``oracle.chain.end_window`` and ``end_window_wait``; the |
|     | ``_landing_closer`` seam, the host hook's ``carrier`` and        |
|     | ``cli/host_close``'s landing mode (wait for the landing's own    |
|     | work, check, end, outcome); "N started today (M chained)" in     |
|     | chain status (D1 to D5, D12). Alone it already ends a finished   |
|     | chained window, and today's step reads it as ``gone`` after      |
|     | ``GONE_SECONDS`` and starts the next entry.                      |
+-----+------------------------------------------------------------------+
| 2   | The chain step: the landing record on ``landed``, ``ending`` and |
|     | ``ended_itself`` with no wait, ``ongoing`` held until idle,      |
|     | ``kept`` and one at a time passing over it, ruling 21 as the     |
|     | fallback with the user's window and Leave open kept (D6 to D9);  |
|     | the ledger's shape in :doc:`state_model.md                       |
|     | </architecture/state_model>`.                                    |
+-----+------------------------------------------------------------------+
| 3   | A session's follow-up placed by rank, ``--priority`` and         |
|     | ``--tier`` on ``queue add``; ``session handoff`` deprecated; the |
|     | shipped agents and templates rid of its name, agents re-rendered |
|     | (D10, D11).                                                      |
+-----+------------------------------------------------------------------+
| 4   | The phone: Now's new lines, the kept count, the card's and the   |
|     | sheet's words, the start counts (D13), written into              |
|     | :doc:`oracle-panel.md </architecture/oracle-panel>`.             |
+-----+------------------------------------------------------------------+
| 5   | Per managed project, in that project's own session: hand-off     |
|     | instructions retired, agents re-rendered (D14). One chore each,  |
|     | queued there.                                                    |
+-----+------------------------------------------------------------------+

Consequences
------------

- A chained session that lands ends its own window once the landing's own
  work (its last turn, and the background agent that carried the landing) is
  over, and the oracle starts the next queue entry on the next tick, without
  anyone at the Mac. The consent is the teardown's engine record; nothing
  reads a transcript to decide a close.
- invariant ends a host at the end of every chained landing, not only at a
  handoff: a second caller of the closer whose rules BUG_100 settled. A
  window that cannot be shown free (a prompt after the landing, work
  running, a record that cannot be read) is never ended.
- One at a time is relaxed for one kind of window only: a landed chained
  window the user keeps. Such windows are uncounted and uncapped, so a user
  who keeps many has many open; the chain's own starts are still one at a
  time, under the daily and machine caps.
- Ruling 21's typed ask survives as a fallback, so a host with no closer, a
  project with ``end_window: off`` and a session from before this record
  still settle through it.
- A session's follow-up no longer jumps the backlog by being appended: it
  waits for its rank, and a free label must say how urgent it is.
- ``session handoff`` prints a deprecation line on every run and leaves the
  shipped instructions. Projects that still chain through it keep working,
  now counted in chain status, until their own migration session lands.
- The session record gains ``landing``; the chain ledger gains ``kept`` and
  two landed states (increment 2). Both are machine-local and never
  committed.
