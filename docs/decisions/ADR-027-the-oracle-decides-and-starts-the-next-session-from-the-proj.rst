.. _adr-027:

ADR-027 — The oracle decides and starts the next session from the project's queue
=================================================================================

.. dec:: The oracle decides and starts the next session from the project's queue
   :id: DEC_ADR_027
   :status: accepted
   :affects: COMP_CMD_ORACLE, COMP_ORACLE_WATCHER, COMP_ORACLE_STARTSESSION, COMP_CMD_SESSION, COMP_DOMAINS_SESSION, COMP_CORE_MANIFEST, COMP_ORACLE_USAGE, COMP_HOSTS_CLAUDE_CODE_USAGE, COMP_ORACLE_CHAINVIEW, COMP_ORACLE_PANEL, COMP_ORACLE_NOTIFY, COMP_ORACLE_PUSH, COMP_DOMAINS_QUEUE, COMP_CORE_QUEUE, COMP_MACHINE_BUG, COMP_MACHINE_IDEA

   - **Status:** Accepted (2026-11-18). The rulings below are the user's,
     from a four-round interview on IDEA_057 the same day.
   - **Date:** 2026-11-18
   - **Supersedes:** nothing. Amends ADR-009 D1 and D16 (every oracle action
     is confirmed first) for one action, the chained start (D4), and narrows
     ADR-007's read-only stance as ADR-009 already did. Builds on ADR-005
     (handoff), ADR-009 D16 (``oracle start-session``) and ADR-014 D1 (each
     record type has a location).

Context
-------

Today the next session is started by the session that ends. Its brief carries
a "then:" step, and the agent runs ``invariant oracle start-session`` (ADR-009
D16) or ``invariant session handoff`` (ADR-005) once its work has landed.
Which work comes next lives in the brief, in a memory note or in a chat
message, and an agent that forgets the step, or stops for the user, ends the
chain with nothing recording that it ended.

On 2026-11-18 the user asked to "give the control of firing the next session
to the Oracle ... when a session finishes, it will inform Oracle that work is
finished, and Oracle will decide what to run next according to the project's
plan and priorities, and starts the next session." The oracle already sees
every session in every registered project (ADR-007) and can already open a
Claude window in one (ADR-009 D16). What it lacks is a record of what comes
next, a reported end, a decision rule, and the standing permission to act on
it without a tap each time.

Rulings
-------

.. _adr-027-ruling-1:

**Ruling 1 — finished.** A session torn down with its plan complete counts as
finished; the scan sees it, with no new step. A ``session report`` command
covers the other endings (blocked, needs the user), and those stop the chain
for that project.

.. _adr-027-ruling-2:

**Ruling 2 — the plan.** Each project has an ordered queue of work that the
user and sessions add to. When it is empty, the oracle falls back to the
backlog ranking. The finishing session may suggest its successor, which goes
on the queue.

**Amended by** :ref:`ruling 18 <adr-027-ruling-18>`: the backlog is on the
queue too, mirrored there entry by entry, so the fallback is reached only for
work the mirror does not hold.

.. _adr-027-ruling-3:

**Ruling 3 — the queue's home.** A record in the project's records (the store
where the project has one, like the backlog), edited with ``invariant queue``
by the user or a session, and shown on the phone page, which can reorder and
remove entries.

.. _adr-027-ruling-4:

**Ruling 4 — autonomy.** A per-project setting: auto (start, then notify) or
propose (a phone card the user taps). The default is auto for queued work and
propose for work taken from the backlog fallback. Every start is recorded.

**Amended by** :ref:`ruling 19 <adr-027-ruling-19>`: the default for backlog
work is auto too, for every project, and an entry the mirror put on the queue
is backlog work (:ref:`ruling 18 <adr-027-ruling-18>`). **Amended by**
:ref:`ruling 20 <adr-027-ruling-20>`: a filed bug's start is a reproduction
first.

.. _adr-027-ruling-5:

**Ruling 5 — limits**, each changeable: one session at a time per project;
stop on failure; a daily cap of 20 oracle-started sessions per project; an
active-hours window, by default always; a machine-wide cap, loose, by default
10. The user added (2026-11-18): "when the weekly usage reached 95 or is above
95%, stop running new sessions automatically. Of course, sessions can be still
start manually through Oracle." When the weekly usage cannot be read, the
oracle holds and notifies, "but I should be able to reset and give it a
greenlight to go ahead".

.. _adr-027-ruling-6:

**Ruling 6 — stalls.** "If the session is not making progress for hours,
Oracle needs to check what it is doing", and tell a failure from a stop at the
five-hour limit. The oracle asks the stalled session to continue once (after
the limit resets, for a limit stop); if it is still stalled, it notifies, and
the user chooses Retry, Continue or Close from the phone. The fuse is 2 hours.

.. _adr-027-ruling-7:

**Ruling 7 — the old path.** Briefs stop telling agents to launch the next
session; a session puts its suggested successor on the queue and ends.
``oracle start-session`` and ``session handoff`` stay for the user and for
mid-task context resets, and their windows count toward the one-at-a-time
limit.

**To be amended by** (proposed) :ref:`ADR-031 ruling 1 <adr-031-ruling-1>` and :ref:`ruling 10
<adr-031-ruling-10>`: a session still puts its successor on the queue and
ends, and now its final landing ends its own window (:need:`DEC_ADR_031_D2`);
``session handoff`` is deprecated and named by no shipped text
(:need:`DEC_ADR_031_D11`).

.. _adr-027-ruling-8:

**Ruling 8 — phone notifications.** IDEA_013 (web push from the oracle's
page) folds into this record as a later increment. Until then, chain events
notify through the existing notifier.

.. _adr-027-ruling-9:

**Ruling 9 — failure triage (2026-11-18).** Amends ruling 5's "stop on
failure" and D6. The user rejected "a failure pauses the chain" and gave this
rule instead, answering the questions increment 2's design put:

- A chained start that fails (the launch fails or lands outside the main
  checkout, no session is bound 10 minutes after the window appeared, the
  window goes without a teardown or a report, the session is torn down with
  its plan open or abandoned, a reservation goes stale) does **not** pause the
  chain. Its entry is marked ``failed`` with the reason, and the oracle,
  through the CLI's recorder and never writing itself, queues a triage entry
  at the head: ref ``triage Qn``, with a brief that runs the triage. The
  triage starts as auto, under the usual limits.
- The triage session files the failure as a bug (backlog-curator) with the
  evidence, reproduces it (bug-reproducer), and judges whether carrying on
  with other sessions would harm the project. If the bug is severe, it queues
  the fix at the head as a **proposal**, an entry of its own mode that is
  never started automatically and waits for ``oracle chain start``; the chain
  keeps running other work meanwhile. If carrying on is not safe, the session
  reports ``needs-user``, which pauses the chain and notifies the user.
  Otherwise the failed entry gets **one** retry after the triage: it goes back
  on the queue right behind the triage. A second failure of the same work
  marks it failed and the chain moves on; that failure is triaged too, but
  not retried again.
- The chain stops and waits for the user only on a ``blocked`` or
  ``needs-user`` report (the report pause, which outlives the session until
  ``oracle chain resume``), or when nothing is left to start. There is no
  automatic "N failures in a row" breaker.
- ``oracle chain pause`` and ``resume`` stay: pause by hand, resume clears a
  pause. Resume retries nothing; the triage rule owns retries.

The other questions of the same design were answered the same day: a
proposal is a notice plus ``invariant oracle chain start`` (D16); the chain
step runs in every ``oracle watch``, ``--no-chain`` opting one out (D13);
the parent session reports a stop, never the subagents (D22); ``queue add``
and ``queue list`` take ``--project`` (D22); the first prompt and the triage
brief are templates (D19); the repository queue is committed by the
reviewer's memory/config sync (D22).

.. _adr-027-ruling-10:

**Ruling 10 — the usage source (2026-11-18).** Claude Code 2.1.291 writes no
usage file. Its status-line command gets JSON on stdin with
``rate_limits.five_hour`` and ``rate_limits.seven_day``, each with
``used_percentage`` and ``resets_at``. The source is an **opt-in** status
line: ``invariant hook statusline`` records each reading, host-neutral as a
``plan_usage`` fact (weekly and five-hour percentages, their reset times,
when it was read), in the oracle home, and prints a short line such as
"week 41% · 5h 12%". The user installs it with a command that refuses to
replace an existing ``statusLine`` in ``~/.claude/settings.json``; nothing
installs it automatically. Without it every reading is missing, so D5's rule
applies: a missing reading, or one older than 30 minutes, counts as over the
cap and holds auto-starts with one notice, until ``invariant oracle chain
greenlight`` lifts the unknown-reading hold until a fresh reading arrives. A
fresh reading at or over ``chain.weekly_usage_max`` (default 95, the user
config's top-level ``chain`` block, beside ``machine_cap``) holds again.
Under the weekly hold no auto-start and no proposal is made (D5). A manual
start (``oracle chain start``, start-session) is never refused. The Claude
Code JSON parsing belongs in ``hosts/claude_code*``. The oracle reads the
host-neutral fact through an injected seam: no oracle → hosts import, and
only the CLI imports the oracle.

.. _adr-027-ruling-11:

**Ruling 11 — stall scope (2026-11-18).** Every live session gets the new
``stalled`` signal (``oracle/signals``, dashboards and page). It is raised
after ``oracle.chain.stall_after`` (default 2 h) with no transcript activity,
commit or record change, while the session is not waiting on a permission
prompt or a question. Only a session the chain started (a chained start in
the ledger) gets the one "continue" typed in. A stalled hand-started session
gets a notice only. Diagnosis per D7: the last turn's end shows an error, the
five-hour limit (with reset time; the nudge waits until after the reset) or
no known cause. The nudge goes through a host seam, injected by the CLI.
Windows are Terminal.app tabs opened by ``do script``
(``hosts/claude_code_launch.py``; close-host already finds a tab by tty), so
typing "continue" into the host's tab is the mechanism. Where the tab cannot
be found, no nudge is sent and the user is notified. Once per stall,
recorded in the ledger.

.. _adr-027-ruling-12:

**Ruling 12 — after the nudge (2026-11-18).** Amends D7's "the chain stays
paused": a stall **never** pauses the chain. Still stalled one fuse after
the nudge means a notice ("still stalled after one nudge: Retry, Continue or
Close"). One at a time already holds the project while the window lives. The
three actions get CLI verbs now (the phone buttons are increment 3):

- **Continue:** type one more "continue" and re-arm the fuse.
- **Retry:** close the stuck window and put the same work back at the head of
  the queue, started fresh on the next tick. No triage session. A user's
  Retry is not ruling 9's "one retry".
- **Close:** close the window and mark the entry failed (reason: closed by
  the user after a stall). No triage. The chain goes on to the next queued
  work.
- If the user never answers and the window disappears anyway (Terminal
  quit, reboot), it is a normal failure: ruling 9's triage plus one retry,
  and the chain goes on.

Close and Retry reuse close-host's seams (IDEA_027 increment 6). The queue
writes go through ``ChainHands`` and ``domains/queue`` (D18): the oracle
writes no record.

.. _adr-027-ruling-13:

**Ruling 13 — a finished chained session stops holding its project
(2026-11-18).** Amends D5's one at a time. Increment 2b's design asked what
happens to the stuck invariant session after Close or Retry, since D5 held
every chained start while any live session exists. The user chose: a chained
session the chain has finished with (failed, closed by the user after a
stall, or retried) stops holding its project. It stays parked, its worktree
intact, for the user to land or abandon. The chain starts the next work
beside it on the next tick, and the ledger records which sessions it
released. A session started by hand still holds its project as before. One
rule for every ended chained start: it covers increment 2's window-gone
failure and the unanswered window that disappears too.

**Amended by** :ref:`ruling 21 <adr-027-ruling-21>`: a chained session that
finished well is released too, once it consented and the oracle closed its
window (:need:`DEC_ADR_027_D58`).

.. _adr-027-ruling-14:

**Ruling 14 — confirm everything (2026-11-18).** ADR-009 D2 stands unamended
for the chain: every chain action from the phone (move up, move down, remove,
start a proposal, pause, resume, greenlight, continue, retry, close) goes
through the confirm sheet with its command, nonce and expiry, the same as
every other action. Retry and Close end a window, so they use the
destructive sheet, the way close-host's does. The user picked this over a
one-tap option for reversible actions and over an edit mode with one Save.

.. _adr-027-ruling-15:

**Ruling 15 — cards plus a project Chain screen (2026-11-18).** Whatever
needs the user is a card under Needs you: a proposal (a queue propose entry,
a backlog proposal, an outside-hours proposal), a stall the nudge did not
clear or could not nudge (with Continue, Retry and Close), the weekly-usage
hold that needs a greenlight (an unknown reading; the "over" hold is shown
but offers no greenlight, since that is refused), and a paused chain
(Resume). Each row in the overview's Projects section gains a chain line
(for example "Chain: running Q4 · 3 queued", or paused, held, idle) and opens
a per-project Chain screen. That screen holds the queue (each entry with move
up, move down and remove), the proposals with Start, Pause or Resume, the
current chained start and its stall actions. It is a view inside the one
page, like the detail and leftovers screens.

.. _adr-027-ruling-16:

**Ruling 16 — the design system, no mockup round (2026-11-18).** Build from
the panel's existing tokens and components (cards, chips, list rows, pills,
bottom sheets, the back link, the type scale), and write every new screen,
component use, string and behaviour into
:doc:`oracle-panel.md </architecture/oracle-panel>` as the record. The user
reviews the result on the phone.

.. _adr-027-ruling-17:

**Ruling 17 — web push (2026-11-19).** The user's answers from increment 4's
interview, which settle ruling 8's later increment:

- **Dependency.** ``cryptography`` joins the ``oracle`` optional extra: not
  core, and not ``pywebpush``. Push works only where the extra is installed;
  without it the panel offers no push and says why.
- **Payload.** The same text the Mac banner shows: the title ``invariant:
  <project>``, the row's label (the subtitle) and the notice's one-line
  message. Never brief text, prompt text, transcript content or paths. End
  to end encrypted (RFC 8291 ``aes128gcm``), so the push service relays
  ciphertext only.
- **Scope.** Everything the Mac gets: every oracle alert and every chain
  notice, under the same rising-edge de-dup and owed-until-delivered ledgers.
  One notifier behind the seam, with no filter of its own.
- **The Mac banner.** Both, always. Fan out to the macOS notifier and every
  subscribed device; a failure on one channel neither delays nor blocks the
  other.

IDEA_013's acceptance holds beside it: a notification reaches the phone within
30 s and once per rising edge; push is opt-in and off by default; ``invariant
oracle notify-test`` exercises it; a failing push provider is reported in
``oracle watch``'s output and does not delay the other notifiers; and the
credential, the VAPID private key, stays out of the manifest, machine-local in
the oracle home like the device tokens.


.. _adr-027-ruling-18:

**Ruling 18 — the backlog on the queue (2026-11-20).** Amends rulings 2 and 4.
Every open backlog item is mirrored onto the queue automatically: an entry
when an item is filed; the open items of today added once, in backlog rank;
shown and reordered on the phone like any entry; removed when the item
closes. The user's answers of the same day:

- **What is mirrored.** A bug in ``fixed`` (non-terminal, awaiting its live
  check) is not. The mirrored states are configurable per project; the
  default is filed and reproduced bugs and proposed, accepted and in-progress
  ideas: open, and not fixed.
- **Link and skip.** A queue entry may name the item it serves (a free label
  ``ADR-029 inc 1`` serves ``IDEA_060``). The mirror skips an item a live
  (queued or started) entry serves. The backfill links today's ADR-increment
  entries to their items (Q10 to Q16, ``ADR-029 inc 1`` to ``inc 7``, serve
  ``IDEA_060``). An in-progress item with a live session is skipped too.
- **Where a new item goes.** A newly filed item's entry is inserted by
  backlog rank (tier, priority, natural id: :need:`DEC_ADR_027_D12`'s rank)
  among the mirrored entries; hand-queued entries keep their places. The
  backfill goes in rank order behind what is queued today.

.. _adr-027-ruling-19:

**Ruling 19 — backlog work starts on its own (2026-11-20).** Amends ruling 4.
invariant's ``oracle.chain.backlog`` becomes ``auto``, under the usual limits
(one at a time, the daily cap, the machine cap, the hours, the weekly usage
hold); a session that needs the user interviews them. The user's answer: the
shipped defaults turn on everywhere, mirroring on, ``oracle.chain.backlog``
auto and reproduce-first on, for every project; a project opts out in its
manifest.

.. _adr-027-ruling-20:

**Ruling 20 — a bug is reproduced first (2026-11-20).** Amends rulings 2 and
4. In the user's words: "We should only try to reproduce the bug and confirm
it's red when it reaches its priority and we want to work on it, so it has to
be reproduced and when it's confirmed red, then it should start right away to
fixing it." So a filed, unreproduced bug's entry, when its turn comes in
rank, starts a bug-reproducer session first, never at filing time:

- **RED-CONFIRMED:** the fix is queued at the head and starts right away
  (the next tick, under the usual limits).
- **ALREADY-FIXED or not reproducible:** the item is closed by that verdict
  (the agent's existing routing) and its entry leaves the queue.
- **DEVICE-ONLY:** the entry stays, as a propose-mode entry awaiting the
  user's live repro.
- **HARNESS-BLIND:** one more reproducer run, with the suggested harness, is
  queued; a second HARNESS-BLIND becomes a propose entry.
- Neither of the last two pauses the chain.

.. _adr-027-ruling-21:

**Ruling 21 — a finished chained window is closed once its session consents
(2026-11-20).** Amends :ref:`ruling 13 <adr-027-ruling-13>`, D5 and D30 for
IDEA_062. Ruling 13 releases only a chained start that failed or that the user
closed or retried, so a session that finished well keeps its window, and the
window holds its project under one at a time. The user chose option 2 of
three: the oracle closes a finished chained window itself, but only after
asking the session in its own tab and getting its consent. In the user's
words: "Oracle can send a message and ask the session if the session is
finished and confirms that Oracle can close it, like exactly what I'm doing
with the sessions." The interview of the same day settled the rest:

- **Asking.** Once the chained session is finished (D1's teardown with its
  plan complete) and its window has been quiet for 5 minutes, the oracle
  types one line into the session's own tab, the route of D28's "continue":

  ``invariant oracle: this session has landed and the chain wants its window
  back. If you have nothing more to do, run: invariant oracle chain consent
  <project> --close   Otherwise run: invariant oracle chain consent <project>
  --not-yet --note "<why, one line>"   Answer only with that command.``

- **Answering.** The session answers by running that command, which records
  consent, or "not yet" with its note, in the chain ledger as an engine
  record. Consent is never read from transcript text.
- **On consent** the oracle closes the window through close-host's path
  (IDEA_027) and the chain starts the next work on the next tick.
- **On "not yet"** the note is kept and the oracle asks again 30 minutes
  later, three asks in all. After the third "not yet" it stops asking and
  sends the phone notice below, with the latest note.
- **On no answer**, 10 minutes after any ask with neither record made, or
  when the line could not be typed at all (no tab, a shell-only tab), one
  phone notice offers **Close** and **Leave open**, and no more asks are
  made. The oracle never closes a window silently.
- **Leave open** means the window holds its project, as D5 already says of
  any live Claude window: the oracle never asks it again, and the chain waits
  until the user closes it.
- **The user's window.** A window where the user has typed a turn since the
  teardown is the user's: it is never asked and never closed, it holds the
  project, and the user gets exactly one notice saying so.
- Every ask, answer, notice and close is in the chain ledger.

**To be amended by** (proposed) :ref:`ADR-031 ruling 2 <adr-031-ruling-2>` and :ref:`ruling 7
<adr-031-ruling-7>`: a chained session's final landing ends its own window,
and this ruling's ask, consent and notice are the fallback for a window that
did not end itself (:need:`DEC_ADR_031_D9`). The user's window, and one left
open, no longer hold the project (:need:`DEC_ADR_031_D7`).

Decisions recorded
------------------

.. dec:: A session ends as done by teardown with a complete plan, or by a report
   :id: DEC_ADR_027_D1
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_DOMAINS_SESSION, COMP_CMD_SESSION, COMP_ORACLE_SCAN

   - **Done by teardown.** A session whose machine reaches ``torn_down``
     through a merge, with every deliverable on its plan done, is ``done``.
     The oracle derives it from the session record (ADR-007's "derived from
     its machine, never named"); nothing new is written.
   - **Reported.** ``invariant session report --outcome done|blocked|needs-user
     [--note TEXT] [--id ID]`` records an end as an event, not an edge, the
     way ADR-005 D2 records a handoff: a ``reports`` list on the session
     record and a standstill row in ``history``. It is legal in every state.
     The note is vetted by the privacy scanner and bounded to one line, as a
     handoff note is (ADR-005 D1).
   - ``done`` reported on a session that is not yet torn down means "my part
     is finished, the landing is the reviewer's"; the chain still waits for
     the teardown before starting the next one, so one-at-a-time holds.
   - ``blocked`` and ``needs-user`` pause the project's chain (D6).
   - A session with no report and no teardown has not ended; whether it is
     stalled is D7's question, not this one's.
   - **Weighed against:** a host Stop hook as the signal. It is
     host-specific, fires on every idle turn, and cannot tell "finished" from
     "waiting for you"; the engine's own record can.

.. dec:: The queue is a record type with a location, edited through invariant queue
   :id: DEC_ADR_027_D2
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_CORE_MANIFEST, COMP_CLI_MAIN

   - ``queue`` is a record type under ADR-014 D1's ``records`` block, with a
     path key ``paths.queue``. Its location defaults to the backlog's, so a
     store project keeps its queue in the store and a repo project in the
     repository. It is committed, so it carries no clock time (ADR-007's rule
     for committed records).
   - An entry is an ordered row: an ``id`` minted by the queue, a ``ref`` (a
     work-item id, an ADR increment such as ``ADR-027 inc 2``, or a free
     label), an optional ``brief`` (the first prompt; privacy-scanned and
     bounded, as a handoff note is), ``added_by`` (``user`` or the adding
     session's id), and a ``state``: ``queued``, ``started`` (with the
     session it started), ``done`` or ``failed`` (with the reason).
   - ``invariant queue add REF [--brief TEXT] [--at N]``, ``queue list``,
     ``queue remove ID``, ``queue move ID --to N``. A work-item ``ref`` must
     name an item that exists and is not closed; a free label is accepted as
     is.
   - Writes go under a lock and an atomic replace, as every other record's do;
     the oracle only reads it (D9).

.. dec:: What runs next is a pure decision over the queue, the backlog and the limits
   :id: DEC_ADR_027_D3
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_CMD_ORACLE

   - ``decide`` is a pure function in ``oracle/``: it takes one project's queue,
     its open backlog items, its chain settings, the scan's view of its
     sessions and windows, the chain ledger (D5) and an injected clock, and
     returns one verdict: ``start`` (the entry or item, the brief, and whether
     it is ``auto`` or ``propose``), ``wait`` (with the limit that holds it),
     ``paused`` (with the reason), or ``idle`` (nothing to do).
   - **Order:** the queue's first ``queued`` entry; when there is none, the
     backlog fallback: open items whose status the project's machine marks as
     ready to work (by default ``proposed``/``accepted`` ideas and
     ``reproduced`` bugs), ranked by tier, then priority, then id. An item
     already on the queue or already started is skipped.
   - ``invariant oracle next <project> [--json]`` prints the verdict and every
     reason behind it, and starts nothing. It is the testable core the
     watcher's chain step and the phone page both run.
   - **Amended by** :ref:`ruling 18 <adr-027-ruling-18>` and :ref:`ruling 19
     <adr-027-ruling-19>`: an entry the backlog mirror wrote takes
     ``oracle.chain.backlog``'s mode, and the fallback also skips an item a
     live entry serves (:need:`DEC_ADR_027_D48`, :need:`DEC_ADR_027_D49`).
     **Amended by** :ref:`ruling 20 <adr-027-ruling-20>`: a queued entry
     naming an unreproduced bug starts reproduce-first
     (:need:`DEC_ADR_027_D50`).

.. dec:: A project's chain setting is the standing confirmation for a chained start
   :id: DEC_ADR_027_D4
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_CORE_MANIFEST, COMP_ORACLE_WATCHER, COMP_ORACLE_STARTSESSION

   - Per project, ``oracle.chain.queue`` and ``oracle.chain.backlog`` each take
     ``auto``, ``propose`` or ``off``; the defaults are ``auto`` and
     ``propose``. With an empty queue, nothing starts on its own until the user
     or a session queues something.
   - ``auto`` amends ADR-009 D1 for this action only: the project's setting is
     the confirmation, given once, in advance. The start is the same launch
     ``oracle start-session`` makes (ADR-009 D16): Claude's default permission
     mode, never a bypass flag, the oracle never answering a permission prompt.
   - ``propose`` is a card on the phone page and a notice; one tap runs it
     through the confirm sheet start-session already has.
   - Every chained start is recorded: on the queue entry (``started`` and the
     session) and in the chain ledger, with what decided it.
   - **Amended by** :ref:`ruling 19 <adr-027-ruling-19>`: the default of
     ``oracle.chain.backlog`` is ``auto``, and it governs the entries the
     backlog mirror writes (:need:`DEC_ADR_027_D49`).

.. dec:: Limits hold auto-starts only; a manual start is never refused
   :id: DEC_ADR_027_D5
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_WATCHER, COMP_CORE_MANIFEST, COMP_ORACLE_HOME

   - **One at a time per project:** no chained start while the project has a
     live session that is not torn down, or a live Claude window, however it
     was started (start-session, handoff or by hand).
   - **Daily cap:** at most ``oracle.chain.daily_cap`` (default 20) chained
     starts per project per local day.
   - **Machine cap:** at most ``chain.machine_cap`` (default 10) live
     oracle-started sessions across all projects, in the oracle home's own
     configuration.
   - **Active hours:** ``oracle.chain.hours``, default always; outside the
     window an ``auto`` start becomes a proposal.
   - **Weekly usage:** at or above ``chain.weekly_usage_max`` (default 95 %)
     of the plan's weekly usage, no auto-start and no proposal is made, and
     one notice says why. The reading comes through the host probe (ADR-007
     D11), a host-neutral ``plan_usage`` fact (weekly and five-hour percentages,
     reset times, when it was read). A reading that is missing or older than
     30 minutes counts as over the cap, with a notice, until the user gives a
     **greenlight** (``invariant oracle chain greenlight``, or the phone),
     which lifts the unknown-reading hold until a fresh reading arrives; a
     fresh reading at or over the cap holds again.
   - The ledger of chained starts, pauses, greenlights and nudges is
     machine-local state in the oracle home (``oracle/chain.json``), derived
     and uncommitted, under the home's lock, as the alert ledger is.
   - None of these limits ever refuses a start the user asks for
     (start-session, the phone's New session, the TUI); ADR-009 D16's facts
     inform and do not block.
   - **Amended by** :ref:`ruling 10 <adr-027-ruling-10>`: the reading's
     source is an opt-in status line (:need:`DEC_ADR_027_D24`), and the hold
     and the greenlight are :need:`DEC_ADR_027_D25`'s. **Amended by**
     :ref:`ruling 13 <adr-027-ruling-13>`: a chained session the chain has
     finished with no longer holds its project under one at a time
     (:need:`DEC_ADR_027_D30`).
   - **Amended by** :ref:`ruling 21 <adr-027-ruling-21>`: the live window of
     a chained session that landed still holds its project, but the oracle
     asks the session whether it may close it, and closes it on its consent
     (:need:`DEC_ADR_027_D53` to :need:`DEC_ADR_027_D61`).
   - **To be amended by** (proposed) :ref:`ADR-031 ruling 2 <adr-031-ruling-2>`: a landed
     chained window the user keeps no longer holds its project, uncapped
     (:need:`DEC_ADR_031_D7`).

.. dec:: A failure pauses the project's chain until the user resumes it
   :id: DEC_ADR_027_D6
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_WATCHER, COMP_CMD_ORACLE

   - The chain pauses on: a ``blocked`` or ``needs-user`` report; a chained
     launch that fails or lands outside the main checkout (BUG_129's watch); a
     window that exits without a teardown or a report; a chained start with no
     invariant session bound 10 minutes after the window appeared; and a stall
     that the nudge did not clear (D7).
   - The entry stays where it is, marked ``failed`` with the reason, and one
     notice says why. ``invariant oracle chain resume <project>`` (or the
     phone) un-pauses; ``oracle chain pause <project>`` pauses by hand.
   - There is no automatic retry of a failed launch.
   - **Amended by** :ref:`ruling 9 <adr-027-ruling-9>`: of these, only a
     ``blocked`` or ``needs-user`` report pauses. A failed chained start is
     triaged and retried once instead (:need:`DEC_ADR_027_D18`), and a failed
     triage pauses (:need:`DEC_ADR_027_D23`).

.. dec:: A stalled session is diagnosed, nudged once, then handed to the user
   :id: DEC_ADR_027_D7
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_SIGNALS, COMP_ORACLE_WATCHER, COMP_HOSTS_CLAUDE_CODE_LAUNCH

   - **Stalled** is a new signal: a live session with no transcript activity,
     commit or record change for ``oracle.chain.stall_after`` (default 2 h)
     while it is not waiting on the user. A permission prompt or a question is
     waiting, not a stall.
   - **Diagnosis** reads the host probe for the last turn's end: an error, the
     five-hour usage limit (with its reset time), or no known cause.
   - **Nudge:** the oracle sends the session one "continue" through a host
     seam, after the reset time for a limit stop. Typing into a running session
     is a new power (IDEA_029 said nothing is typed into a session); it is
     confined to this one message, once per stall, recorded in the ledger.
   - Still stalled one fuse after the nudge: a notice, and the phone offers
     Retry, Continue and Close (Close is IDEA_027's close host). The chain
     stays paused (D6) until the user acts.
   - **Amended by** :ref:`ruling 11 <adr-027-ruling-11>` and :ref:`ruling 12
     <adr-027-ruling-12>`: every live session can be stalled, only a chained
     one is nudged, and a stall never pauses the chain; Continue, Retry and
     Close are CLI verbs (:need:`DEC_ADR_027_D26` to :need:`DEC_ADR_027_D29`).

.. dec:: Sessions queue their successor; the oracle starts it
   :id: DEC_ADR_027_D8
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_DATA_AGENTS, COMP_CMD_SESSION

   - A brief's "then:" step becomes ``invariant queue add <ref> --brief ...``
     run by the finishing session; the shipped agents' landing step says so,
     and no agent text tells an agent to run ``oracle start-session``.
   - ``oracle start-session`` and ``session handoff`` keep their meaning for the
     user and for a mid-task context reset (a handoff continues the same
     session, so the chain still waits on it).
   - **To be amended by** (proposed) :ref:`ADR-031 ruling 9 <adr-031-ruling-9>` and
     :ref:`ruling 10 <adr-031-ruling-10>`: the successor is placed by rank
     (:need:`DEC_ADR_031_D10`), and ``session handoff`` is deprecated
     (:need:`DEC_ADR_031_D11`).

.. dec:: The engine owns the queue and the report; the oracle reads and decides; the CLI launches
   :id: DEC_ADR_027_D9
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_CLI_MAIN, COMP_CMD_ORACLE, COMP_ORACLE_SCAN

   - The queue record and ``session report`` are engine code (``core/`` and
     ``domains/``), written by the CLI; the oracle reads them read-only, as the
     scan reads sessions.
   - ``decide`` lives in ``oracle/`` and imports no host; the chained start
     goes through the launcher the CLI injects (``_open_claude_window``, as
     close-host's seams do), so no ``oracle → hosts`` arrow appears and only
     the CLI imports the oracle.
   - Chain notices go through the notifier seam (ADR-007), so web push
     (increment 4) is one more notifier, not a new path.

Decisions made building increment 1
-----------------------------------

.. dec:: The queue is one JSON file in the main checkout, its ids never reused
   :id: DEC_ADR_027_D10
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_CORE_QUEUE, COMP_DOMAINS_QUEUE, COMP_CORE_MANIFEST

   - ``paths.queue`` defaults to ``docs/queue.json``: one JSON document,
     ``{"version": 1, "next": N, "entries": [...]}``, written with sorted keys
     and no clock time. A key this build does not know, on the document or on
     an entry, is written back as it was read (the BUG_061 contract).
   - ``records.queue`` left unset takes ``records.backlog``'s value when the
     configuration loads, so "defaults to the backlog's" holds for a store
     project and a repo project alike; ``config show`` names where it came
     from.
   - A queue kept in the repository is read and written in the **main
     checkout**, whatever worktree the command runs in: a project has one
     queue, and it is the one the oracle reads from the registered root. A
     queue kept in a store is in the store's main checkout already. The
     backlog's own resolution is unchanged.
   - An entry's id is ``Q<n>``, minted from ``next``; a removed id is never
     issued again. Positions (``--at``, ``--to``) count from 1 over the whole
     list.
   - A ``ref`` shaped like one of the project's work-item ids
     (``<items.id_prefix>IDEA_…`` or ``BUG_…``) must name an item that exists
     and is in no terminal state of its machine; any other ``ref`` is a free
     label. A ``ref`` that a ``queued`` or ``started`` entry already holds is
     refused: one entry per piece of work.
   - The ``ref`` and the ``brief`` are vetted by the handoff note's own check
     (one function): one line, bounded, no exemption marker, and refused on a
     privacy finding. A ``ref`` is bounded to 200 characters, a ``brief`` to a
     handoff note's 2000.
   - The read-modify-write holds the state root's ``locks/queue.lock`` and
     replaces the file atomically.
   - **To be amended by** (proposed) :ref:`ADR-031 ruling 9 <adr-031-ruling-9>`: an entry a
     session adds with no ``--at`` is placed by rank, not appended
     (:need:`DEC_ADR_031_D10`).

.. dec:: A report is a record entry the scan carries on the session's row
   :id: DEC_ADR_027_D11
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_DOMAINS_SESSION, COMP_ORACLE_SCAN

   - Each ``session report`` appends ``{"outcome", "note", "at", "state"}`` to
     the record's ``reports``; ``at`` is the epoch reading ``heartbeat`` holds,
     in the machine-local state root, and ``note`` is ``null`` without
     ``--note``. The standstill row in ``history`` carries ``report:
     <outcome>``. A finished session is saved and not registered again, as a
     handoff is.
   - The session is the one ``session handoff`` would record on: ``--id``,
     else a session the call proves, else the most recently moved one here.
   - The scan's session row carries the latest entry as ``report``, and leaves
     the key out of the row's document when there is none.

.. dec:: What increment 1's decide judges, and how it ranks the backlog
   :id: DEC_ADR_027_D12
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAIN, COMP_CORE_MANIFEST

   - **Ready** is a state role: the shipped idea machine marks ``proposed`` and
     ``accepted`` ``ready``, the bug machine ``reproduced``. A project's own
     item machine that marks no state ``ready`` offers no backlog work.
   - **Rank:** ``tier`` ascending (a missing or non-numeric tier last), then
     ``priority`` ``high``, ``medium``, ``low`` (any other or none last), then
     the id in natural order, so ``IDEA_9`` comes before ``IDEA_10``.
   - **Off** is ``idle``, with the setting as the reason. A queue head under
     ``oracle.chain.queue: off`` does not fall through to the backlog, so the
     user's order is never jumped.
   - **Paused** in increment 1 is a live session whose latest report is
     ``blocked`` or ``needs-user``. Keeping the pause past that session's
     teardown, and ``resume``, are the ledger's (increment 2).
   - **Wait** is one at a time: any live session (not ended), or any live
     Claude window the scan knows of; a live session that reported ``done``
     waits for its teardown. A wait names the candidate it holds back.
   - **Hours:** ``oracle.chain.hours`` is ``null`` (always) or ``[start,
     end]``, two whole local hours from 0 to 24, wrapping past midnight when
     ``start`` is later than ``end``; outside it an ``auto`` start is reported
     as ``propose``. Whole hours, so no clock time is written in a
     configuration file.
   - ``oracle next`` exits 0 whatever it decides: it is a query.
   - **Amended by** :ref:`ruling 18 <adr-027-ruling-18>`: the rank is
     ``core/queue.rank_key`` now, since the engine's mirror inserts by it too
     (:need:`DEC_ADR_027_D47`); a ``mirror`` role beside ``ready`` marks what
     goes on the queue (:need:`DEC_ADR_027_D45`), and a ``reproduce`` role what
     is reproduced first (:need:`DEC_ADR_027_D50`).

Decisions made building increment 2
-----------------------------------

.. dec:: The chain step runs in every oracle watch, after the alerts, through a reservation
   :id: DEC_ADR_027_D13
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_WATCHER, COMP_ORACLE_CHAINSTEP, COMP_CMD_ORACLE

   - ``oracle watch`` runs the chain step on every tick, in the LaunchAgent's
     loop and in ``--once`` alike; ``--no-chain`` opts a watcher out. Each
     project's ``oracle.chain`` settings are the switch (the user's answer of
     2026-11-18).
   - The step runs after the alert ledger, so a launch never delays an alert.
     It follows, decides and reserves under ``locks/chain.lock``, releases the
     lock, and launches outside it: a launch takes up to the successor watch's
     15 s plus its 3 s grace.
   - A reservation (``current.state: launching``, with the reserving process's
     pid) keeps the daemon, a ``watch --once`` and ``oracle chain start`` from
     starting one piece of work twice. One whose process is gone, or that is
     older than 120 s, was never finished: it is a failure
     (:need:`DEC_ADR_027_D18`).
   - A project with a live session, a live Claude window, a pause or a start
     to follow is not read at all, so the five-second tick reads no queue and
     no backlog while work runs.

.. dec:: The chain ledger halts the chain when it cannot be read; it is never replaced
   :id: DEC_ADR_027_D14
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAINSTEP

   - ``<oracle home>/oracle/chain.json`` is machine-local derived state with
     clock times, as ``alerts.json`` is: per project the ``paused`` record,
     the last ``resumed``, the ``current`` start it follows, the ``proposed``
     and ``held`` notices already given, and the last 100 ``starts``; and the
     ``notices`` owed to a notifier that could not deliver them.
   - Unlike the alert ledger, one that cannot be read, or is a newer
     version, is never replaced: replacing it would lift every pause and
     reset the caps. The step then does nothing in any project, logs it, and
     notifies once; ``oracle chain status`` exits 3 naming it, and ``oracle
     next`` reads it as a pause.

.. dec:: A chained start is followed by its window's name or pid to its session's end
   :id: DEC_ADR_027_D15
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAINSTEP, COMP_ORACLE_CHAIN

   - **Bound:** the session row whose host was launched with the start's
     name, or has the pid the launch returned (the fallback for a scan whose
     per-process records are not used). The entry gets that session.
   - **Done:** the bound session has ended and its record, read through an
     inert store (``chain.session_end``), is in a terminal state that is not
     the abandon one, with no deliverable open: a complete plan, or none.
   - **Failed:** torn down with a deliverable open (only a forced merge gets
     there), abandoned, or a record that cannot be found; no session bound
     10 minutes after the window appeared; the window gone for 60 s with no
     teardown and no report, which is every host of the session (or of the
     unbound window) seen not alive and no other live Claude window in the
     project. Unknown liveness is never gone, and a session that reported
     ``done`` waits for its teardown.

.. dec:: A user's start accepts a proposal; the caps hold auto-starts only
   :id: DEC_ADR_027_D16
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAIN, COMP_ORACLE_CHAINSTEP, COMP_CMD_ORACLE, COMP_CORE_HOME

   - A proposal is a notice, said once per piece of work, naming
     ``invariant oracle chain start <project> [--entry Qn]``. That command is
     the user's start: the named entry, else the queue's first queued entry
     whatever its mode, else the backlog's first ready item no entry names.
     No limit holds it (D5). It is recorded as a chained start
     (``mode: accepted``), so the chain follows it, and it is refused (exit 2)
     only with nothing to start or while the project already has a start to
     follow.
   - **Daily cap:** the starts the ledger holds for the project whose local
     calendar day (the machine's zone, from midnight) is today; every start
     counts, a failed launch included. **Machine cap:** the projects that
     have a chained start to follow; a window started by hand is not
     counted, and still holds its own project through one at a time. Either
     cap turns an ``auto`` start into a ``wait`` naming the cap, said once
     per cap and day; a proposal is never held.
   - ``chain.machine_cap`` (default 10) is a top-level ``chain:`` block of
     the user config, ``~/.invariant/config.yaml``: D5's "the oracle home's
     own configuration", read literally. ``oracle.chain.daily_cap``
     (default 20) is the project's.

.. dec:: A report pause outlives its session until resume, and resume retries nothing
   :id: DEC_ADR_027_D17
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAINSTEP, COMP_ORACLE_CHAIN, COMP_CMD_ORACLE

   - A live session's ``blocked`` or ``needs-user`` report, made after the
     last resume, writes the ledger's pause (``by: report``); the pause stays
     after the session is torn down. ``resume`` lifts it and records the
     moment, and a report made at or before it no longer pauses.
   - ``oracle chain pause <project> [--reason]`` keeps the first reason when
     the chain is already paused. ``resume`` of a chain that is not paused
     says so and exits 0. Neither retries anything (ruling 9).

.. dec:: The oracle writes no record: the CLI's hands mark, triage and retry
   :id: DEC_ADR_027_D18
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAINSTEP, COMP_DOMAINS_QUEUE, COMP_CORE_QUEUE, COMP_CMD_ORACLE

   - The step's writes go through ``ChainHands``, which the CLI builds over
     ``domains/queue``: ``begin`` (the entry ``started``, or a backlog start
     minted as an entry ``added_by: oracle``, so every chained start is on
     the queue, D4), ``mark`` and ``fail``. The launch is ``oracle
     start-session``'s own two steps.
   - ``fail`` makes ruling 9 one locked edit: the entry ``failed`` with a
     short reason (no path and no launcher text, since the queue is
     committed; the detail stays in the ledger), a retry at the head when one
     is owed (the same ref and brief, ``retry_of`` the first entry of that
     work), and the triage ``triage Qn`` in front of it (``triage_of``), its
     brief the project's triage template, vetted as any brief. A refusal
     writes nothing and pauses the chain, since nobody would judge the
     failure.
   - The backlog fallback skips an item any entry names, in any state, so an
     item still ready after its session ended does not start again. This
     narrows :need:`DEC_ADR_027_D12`'s "no queued or started entry holds".
   - **Amended by** :ref:`ruling 18 <adr-027-ruling-18>`: the mirror keeps
     this skip (an item any entry names is not mirrored again), and both the
     mirror and the fallback also skip an item a live entry serves
     (:need:`DEC_ADR_027_D46`, :need:`DEC_ADR_027_D48`). The mirror's and
     the reproduction's writes are hands too: ``mirror`` and ``reproduced``.

.. dec:: A chained window is named by its ref; its prompt and the triage brief are templates
   :id: DEC_ADR_027_D19
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAIN, COMP_CORE_MANIFEST

   - The window's label is the work's ref, cut to 40 characters (the entry
     id when it has no letter or digit); ``start-session`` names it
     ``<project>-<label>``, with ``-2`` on a clash.
   - The first prompt is the entry's brief; without one,
     ``oracle.chain.prompt`` filled with ``{ref}``, ``{title}`` and
     ``{entry}``. The triage entry's brief is ``oracle.chain.triage_prompt``
     filled with ``{ref}``, ``{entry}``, ``{reason}`` and ``{retry}`` (``none``
     when no retry is left). Both default to shipped one-line sentences (null
     in the configuration); the triage one names the backlog-curator and
     bug-reproducer agents, ``queue add --propose`` and ``session report
     --outcome needs-user``. A template that names another placeholder, or is
     not one line, is refused at load.
   - **Amended by** :ref:`ruling 20 <adr-027-ruling-20>`: a third template,
     ``oracle.chain.reproduce_prompt``, is a reproduce-first start's first
     prompt (:need:`DEC_ADR_027_D50`).
   - **Amended by** :need:`DEC_ADR_027_D62` (BUG_158): each shipped template
     opens with the session start, so a default-prompt start can bind.

.. dec:: A queue entry has its own propose mode
   :id: DEC_ADR_027_D20
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_CORE_QUEUE, COMP_DOMAINS_QUEUE, COMP_CMD_QUEUE, COMP_ORACLE_CHAIN

   - An entry may carry ``mode: propose`` (``queue add --propose``): it is
     offered and never started on its own, whatever ``oracle.chain.queue``
     says, and the first queued entry of no mode behind it runs meanwhile.
     ``triage_of`` and ``retry_of`` came with it. All three are left out
     while unset, and an older build keeps them as unknown keys (BUG_061).
   - ``decide`` carries the queued propose entries on every verdict as
     ``proposals``, and the step notifies each once.
   - **Amended by** :ref:`ruling 19 <adr-027-ruling-19>`: under
     ``oracle.chain.backlog: propose`` the first queued mirrored entry rides
     ``proposals`` too (:need:`DEC_ADR_027_D49`); :ref:`ruling 20
     <adr-027-ruling-20>` makes a bug whose live repro is owed, or that two
     runs could not see, a propose entry (:need:`DEC_ADR_027_D51`).

.. dec:: Chain notices are owed until delivered
   :id: DEC_ADR_027_D21
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAINSTEP, COMP_ORACLE_NOTIFY

   - A start and a cap that holds (silent), and a proposal, a failure with
     its triage, a pause and an unreadable ledger (each with a sound), go
     through the notifier seam, titled ``invariant: <project>``. One that
     cannot be delivered, or has no notifier (``--no-notify``), stays owed in
     the ledger, at most 50, and the next tick that can deliver sends it.

.. dec:: Sessions queue their successor through the reviewer; the parent reports a stop
   :id: DEC_ADR_027_D22
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_DATA_AGENTS, COMP_CMD_QUEUE

   - The compliance-reviewer's parent instruction names the successor under
     ``THEN:``, and tells the parent to run ``session report --outcome
     needs-user`` when it stops for the user and ``--outcome blocked`` when
     the session ends unable to land; subagents never report. In FINAL mode
     the reviewer queues the successor with ``invariant queue add`` after the
     merge and before the teardown, never ``oracle start-session``.
   - ``queue add`` and ``queue list`` take ``--project <name|key|root>``,
     matched against the oracle's index, so a successor in another project
     is queued from where the session stands.
   - The reviewer's memory/config sync also commits ``paths.queue`` when
     ``records.queue`` is ``repo`` (``Queue: <change>``): the chain's writes
     land in the main checkout and ride the next landing.
   - The rewrite is minimal: no new section anchor, one paragraph and two
     parent items.
   - **To be amended by** (proposed) :ref:`ADR-031 ruling 9 <adr-031-ruling-9>` and
     :ref:`ruling 10 <adr-031-ruling-10>`: the queued successor goes in by
     rank, and the reviewer's text no longer names ``session handoff``
     (:need:`DEC_ADR_031_D10`, :need:`DEC_ADR_031_D11`).

.. dec:: A triage that fails pauses the chain
   :id: DEC_ADR_027_D23
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAINSTEP, COMP_ORACLE_CHAIN

   - A failed start whose entry is itself a triage (``triage_of``) is marked
     failed and pauses the chain with a notice: nobody is left to judge it.
     The user did not state this case; it is derived as ruling 9's sensible
     default, and declared here.

Decisions made building increment 2b
------------------------------------

.. dec:: The plan's usage is a host-neutral fact the status line records in the oracle home
   :id: DEC_ADR_027_D24
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_USAGE, COMP_HOSTS_CLAUDE_CODE_USAGE, COMP_CLI_MAIN

   - ``hosts/claude_code_usage.reading_from_statusline`` reads
     ``rate_limits.seven_day`` and ``rate_limits.five_hour`` into ``{weekly,
     weekly_resets_at, five_hour, five_hour_resets_at, read_at, host}``,
     percentages and epoch seconds. A reset given in milliseconds or as ISO
     8601 is read too, since the format was not provable offline. A payload
     with neither percentage is no reading; a window it leaves out is
     ``null``, and a reading with no weekly figure counts as missing.
   - ``invariant hook statusline`` is that status line. It needs no manifest,
     records through ``oracle/usage.record_usage`` into ``<oracle
     home>/oracle/usage.json`` (an atomic replace, so several windows may
     record at once), and prints ``week 41% · 5h 12%``, ``usage ?`` without
     a reading. Whatever fails, it prints and exits 0: a failing status line
     shows an error on every refresh. A reading whose figures have not
     changed is written again only once the recorded one is 60 s old.
   - ``invariant hooks statusline`` is the opt-in: it sets ``statusLine`` to
     ``{"type": "command", "command": "invariant hook statusline"}`` in the
     user's Claude Code ``settings.json`` (``$CLAUDE_CONFIG_DIR`` or
     ``~/.claude``), says so when it is already that, and refuses (exit 2),
     writing nothing, when another ``statusLine`` is there or the file is not
     a JSON object.
   - The chain step reads the fact through ``ChainStep.usage``, the
     ``_oracle_plan_usage`` seam the CLI injects; ``oracle/usage`` imports
     no host.

.. dec:: The weekly hold is machine-wide, said once, and a greenlight is spent by a newer reading
   :id: DEC_ADR_027_D25
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_USAGE, COMP_ORACLE_CHAINSTEP, COMP_ORACLE_CHAIN, COMP_CORE_HOME, COMP_CMD_ORACLE

   - ``oracle/usage.hold`` is pure: a reading under 30 minutes old with a
     weekly figure at or over ``chain.weekly_usage_max`` is ``over``; under
     it, nothing is held. Any other reading is ``unknown``, unless a
     greenlight was given and no reading newer than it has arrived. A newer
     reading spends the greenlight, which the step then clears.
   - The hold is judged once a tick for the whole machine, since the usage is
     the plan's. ``decide`` takes it as ``usage_hold``, after one at a time
     and before the caps, and answers a ``wait`` naming ``weekly_usage`` for
     an ``auto`` start, a proposal, and queued ``propose`` entries alike; with
     nothing to start or propose it changes nothing. The step notifies no
     proposal under it.
   - One notice per hold, titled ``invariant: chain``, said the first tick
     it holds something and again only when its kind changes; the ledger's
     ``usage.held`` is cleared once nothing is held.
   - ``oracle chain greenlight`` records ``usage.greenlight`` in the ledger
     only while the hold is ``unknown``. It is refused (exit 2) while a fresh
     reading is at or over the cap, and records nothing when nothing is held.
     ``oracle next`` and ``oracle chain status`` show the reading and the
     hold. ``chain.weekly_usage_max`` is a percentage above 0 and at most
     100.
   - A step built without a ``usage`` reader is never held: the CLI always
     injects one, and its missing reading holds.

.. dec:: Stalled is an alert on a live session whose host is known alive
   :id: DEC_ADR_027_D26
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_SIGNALS, COMP_ORACLE_SCAN, COMP_ORACLE_PANEL, COMP_CORE_MANIFEST

   - ``stalled`` is an alert of medium severity on a ``session`` row: the
     current host is known alive, the row's ``last_activity`` is at least
     ``oracle.chain.stall_after`` hours old, and ``turn_state`` is not
     ``waiting``. ``last_activity`` already joins the transcripts (a commit is
     the host's own turn, so it is there), the attention record, the
     heartbeat and the plan, so no git read is added to the tick.
   - A host whose liveness cannot be asked, no host, and a gone host do not
     stall: nothing says a window is there to nudge, and ``parked`` or
     ``host_gone`` already speak for them.
   - Its discriminator is when the quiet began, so a stall alerts once; its
     cause is the diagnosis (:need:`DEC_ADR_027_D27`). The watcher's alert is
     the hand-started session's notice. The panel groups it under Problems,
     and its recommendation is the session's context, as for ``stuck``.
   - ``oracle.chain.stall_after`` is hours, a number above 0 (``0.5`` is
     half an hour), default 2; the scan puts it on each session row as
     ``stall_after``.

.. dec:: A stall is diagnosed from the transcript's last turn, a limit by its quota window
   :id: DEC_ADR_027_D27
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_HOSTS_CLAUDE_CODE_TELEMETRY, COMP_ORACLE_SCAN, COMP_ORACLE_SIGNALS

   - Claude Code writes a failed request as an ``assistant`` entry with
     ``isApiErrorMessage``, an ``error`` code and, for a usage limit,
     ``quotaLimits`` with ``rateLimitType`` (``five_hour`` or ``seven_day``)
     and ``resetsAt`` in epoch seconds. ``TranscriptTurn`` carries them, and
     ``ClaudeCodeTranscripts.turn_ends`` answers each host whose last turn
     was one, from the tail ``activity`` already read and remembered.
   - The scan asks it through the optional ``TranscriptIndex.turn_ends`` and
     puts it on ``HostView.turn_end``. A limit stops as ``limit``, the weekly
     one too; any other failed request is ``error``; anything else is
     ``unknown``.

.. dec:: One continue is typed into the chained session's tab; a stall soon after it is the same stall
   :id: DEC_ADR_027_D28
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAINSTEP, COMP_HOSTS_CLAUDE_CODE_LAUNCH, COMP_CLI_MAIN

   - Only the session bound to a project's ``current`` chained start is
     nudged, and not once it has made a report (its report says it ended its
     part; a stop report already pauses). The stall lives on
     ``current.stall`` while the signal lasts.
   - A limit stop's nudge waits until 60 s past its ``resetsAt``; one with
     no reset known is nudged at once, as an error or an unknown stop is.
   - The nudge is reserved under the chain lock and typed outside it, as a
     launch is: ``TerminalTyper`` finds the tab by the host's tty, refuses a
     tab where only a shell runs (``no_claude``), sends ``do script
     "continue"`` and, half a second later, a lone Return, which submits
     the line if the first arrived as a paste. No tty, hands without a
     typer, or a typer that fails: no nudge, and a notice naming the three
     verbs.
   - **Still stalled:** one fuse after a nudge with the stall unbroken, or a
     new stall whose quiet began within one fuse of the last nudge, gets the
     notice "still stalled after one nudge" once, naming the verbs, and no
     second nudge. A stall that begins later than that, after the session
     went a whole fuse past the nudge, is a new stall and is nudged.

.. dec:: Continue, Retry and Close need a recorded stall, and Retry puts the same entry back
   :id: DEC_ADR_027_D29
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAINSTEP, COMP_DOMAINS_QUEUE, COMP_CMD_ORACLE

   - ``invariant oracle chain continue|retry|close <project>`` act on the
     project's ``current`` chained start and are refused (exit 2) without
     one, or while no stall is recorded on it. A fresh scan finds the
     session's live window.
   - **Continue** types once more (exit 1, nothing recorded, when it cannot)
     and re-arms the fuse.
   - **Retry** and **Close** end the window through ``closehost.close_host``
     with close-host's seams, every fact checked again; the session's own
     binding does not refuse it, since that session is the one given up. A
     window already gone is said, not refused; one that will not close
     raises, and nothing is recorded.
   - **Retry** is ``domains/queue.requeue``: the same entry back at the
     head, ``queued``, its session cleared, its ``retry_of`` and
     ``triage_of`` as they were, so a later failure of it still owes ruling
     9's one retry. **Close** marks the entry ``failed``, reason "closed by
     the user after a stall", with no triage. Neither pauses the chain.

.. dec:: The ledger keeps the sessions it released; they hold nothing
   :id: DEC_ADR_027_D30
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAINSTEP, COMP_CMD_ORACLE

   - Ruling 13 is the ledger's ``released``: per project, the last 100
     session ids whose chained start failed (every ruling 9 failure, the
     window-gone one included) or that the user closed or retried. One at a
     time skips those sessions' rows in the step and in ``oracle next``; a
     live Claude window still holds the project, whoever's it is.
   - Nothing is written to the released session's record: it stays parked
     for the user to land or abandon.
   - **Amended by** :ref:`ruling 21 <adr-027-ruling-21>`: a landed session
     whose window the oracle closed, on its consent or by the user's Close, is
     released too (:need:`DEC_ADR_027_D58`, :need:`DEC_ADR_027_D59`).
   - **To be amended by** (proposed) :ref:`ADR-031 ruling 2 <adr-031-ruling-2>`: a live
     landed chained window the user keeps no longer holds the project; the
     ledger's ``kept`` names it (:need:`DEC_ADR_031_D7`).

Decisions made building increment 3
-----------------------------------

.. dec:: The phone reads the chain through one reader the CLI injects, which also says what is offered
   :id: DEC_ADR_027_D31
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAINVIEW, COMP_ORACLE_WEBACTIONS, COMP_ORACLE_WEB, COMP_CMD_ORACLE

   - ``oracle/chainview.ChainReader`` reads, on every ``/api/state`` and every
     chain confirm and action: the chain ledger without its lock
     (``chainstep.read_ledger``, as ``oracle next`` reads it), each
     registered project's queue (its path found again once a minute, since
     finding it reads the manifest and asks git), and the weekly-usage hold
     through the same ``usage`` seam and ``chain.weekly_usage_max`` the chain
     step is given. It writes nothing. The CLI builds it in ``oracle web``
     (``_oracle_chain_reader``) and hands it to ``RemoteActions``.
   - Each project's read carries its status (running, starting, paused,
     held, waiting, idle, or unread) and the actions it offers now; the
     machine's read carries the greenlight while the hold is ``unknown``.
     The panel draws a button only for an offered action, and the server
     derives a step only for one, so what is shown and what may run come
     from one place.
   - A server without the reader (a read-only one, or one built without it)
     shows no chain and offers none of its actions, as ``start`` is ``null``
     there. A ledger that cannot be read offers nothing anywhere; a queue
     that cannot be read offers nothing in its project. Both are said under
     the Chain screen's Technical details.
   - *Weighed against:* putting the chain in the watcher's snapshot. The
     snapshot is up to one tick old and the confirm must derive from a fresh
     read anyway; reading the ledger and a few small queue files per refresh
     costs little.

.. dec:: A chain action is a run step on the page's own confirm and act, named by its chain key
   :id: DEC_ADR_027_D32
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_WEBACTIONS, COMP_ORACLE_CHAINVIEW, COMP_ORACLE_WEB

   - The key is ``<project key>|chain:<action>[:<entry or item>]``; the
     greenlight's project is ``oracle``, since it is the machine's. No scan
     row's key has a ``chain:`` subject, so ``GET /api/confirm`` and ``POST
     /api/act`` take both, and the page needs no new route.
   - The server derives the step from a fresh scan and a fresh chain read
     (``chainview.derive``): the ``invariant`` argv, run in the project's
     registered root (the oracle home for the greenlight), with no added
     environment. The nonce binds ``command_of`` that step; at the action a
     step derived differently is a 409 ("the chain changed since it was
     confirmed"), and an action no longer offered is a 409 naming why.
     ``refusal``'s checks, the audit lines and the 600-second runner limit
     are a row's.
   - One chain action runs per project at a time: its job is keyed
     ``<project key>|chain``. The Chain screen lists this device's chain
     jobs under Recent.
   - The verbs are increments 2 and 2b's (``queue move``, ``queue remove``,
     ``oracle chain start``, ``pause``, ``resume``, ``greenlight``,
     ``continue``, ``retry``, ``close``); a pause from the phone carries no
     ``--reason``, so it reads "paused by hand".

.. dec:: A move or a remove carries the position the confirm saw, checked under the queue's lock
   :id: DEC_ADR_027_D33
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_CORE_QUEUE, COMP_DOMAINS_QUEUE, COMP_CMD_QUEUE, COMP_ORACLE_CHAINVIEW

   - ``invariant queue move ID --to N --if-queued-at P`` and ``queue remove
     ID --if-queued-at P`` refuse (exit 2), writing nothing, unless entry ID
     is still ``queued`` at position P, checked in the same locked edit
     (``Queue.expect``). Positions count over the whole list, as ``--to``
     does (D10).
   - Move up puts the entry at the position of the queued entry before it,
     move down at the position of the queued entry after it, so a ``done``
     or ``failed`` entry between them never absorbs the move. The first
     queued entry offers no move up and the last no move down.
   - The server checks the derived argv again at the action (D32), and the
     queue checks the position again when the command runs: a queue that
     changed in between refuses rather than moving or removing the wrong
     place. An entry the chain started in between is refused too.
   - *Weighed against:* naming the neighbour (``--before Qn``). The guard
     says "still where the confirm saw it", which is what can go stale.

.. dec:: A proposal's Start is the user's start of that entry, or of that backlog item
   :id: DEC_ADR_027_D34
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_CMD_ORACLE, COMP_ORACLE_CHAIN, COMP_ORACLE_CHAINVIEW

   - A queue proposal starts with ``oracle chain start <key> --entry Qn``. A
     backlog proposal starts with the new ``--item <id>``: a ready backlog
     item no queue entry names, else exit 2. ``--entry`` and ``--item`` are
     exclusive. Without it the phone could not start the item it showed: a
     bare ``chain start`` takes the queue's first queued entry first, a
     propose entry included.
   - Start is offered only while the project has no chained start to follow,
     since ``chain start`` refuses one then; it is offered for every queued
     propose-mode entry, every queue proposal the step made, and the step's
     backlog proposal. No limit holds it (D5).

.. dec:: The chain's Needs-you cards are what the step said and what the user must answer
   :id: DEC_ADR_027_D35
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_PANEL, COMP_ORACLE_CHAINVIEW

   - **A proposal** card is a proposal the chain step made (the ledger's
     ``proposed``) that is still open: its entry still queued, or its backlog
     item still named by no entry. A queue propose entry the step has not
     reached yet (a session is live) is on the Chain screen, not a card, so
     the cards say what the oracle proposed, as its notices do.
   - **A stall** card is a recorded stall on the current chained start that
     the nudge did not clear ("Still stalled") or could not make ("Not
     nudged": no tab, or typing failed), with Continue, Retry and Close. A
     stall still waiting for its nudge, or for its usage limit to reset, is
     no card; the session's own Problems card already says it is stalled.
   - **A paused** chain is a card with Resume chain…, its reason said by who
     paused it (by hand, a session's stop report, a failed triage); the raw
     reason is under Technical details, since it names engine words.
   - **The weekly-usage hold** is one card for every project, shown while
     the step has said it (the ledger's ``usage.held``) and it still holds:
     ``unknown`` with Go ahead…, ``over`` with no button and the percentages.
     Its Details is a detail view with the reading under Technical details.
   - Within Needs you a stall ranks with medium severity, the pause and the
     hold low, a proposal last; each card's age is when its condition began.

.. dec:: Each project's row carries a chain line and opens a Chain screen inside the page
   :id: DEC_ADR_027_D36
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_PANEL, COMP_ORACLE_WEBPAGE

   - The line is "Chain: <status>", the status "running Q4", "starting Q4",
     "paused", "held" (the usage hold and work queued), "waiting" (work
     queued, held back by one at a time, a cap or the hours), "idle" or
     "cannot be read", then " · N queued" and " · N proposed" when nonzero.
     "waiting" is derived: ruling 15 named running, paused, held and idle as
     examples, and queued work that has not started is none of them.
   - The row's text is a button that opens the project's Chain screen; New
     session… keeps its pill. The section is drawn when the page may start a
     session or reads the chain.
   - The screen, top to bottom: the back link, "Chain" and the project's
     name with its status chip; What is going on, with Pause chain… or
     Resume chain…; Now, the chained start with its line and, when stalled,
     Continue…, Retry… and Close…; Proposed, each with Start…; Queue, every
     queued entry with its id and ref, who added it, its brief (two lines at
     most), move up and move down (44 px icon buttons) and Remove…; Recent;
     and Technical details. Done and failed entries are not listed; the last
     five, with a failure's reason, are under Technical details.

.. dec:: Retry and Close are destructive sheets naming the stalled window; the rest are safe sheets
   :id: DEC_ADR_027_D37
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_PANEL, COMP_ORACLE_WEBACTIONS

   - Retry ("Close and retry") and Close ("Close and mark failed") show
     "This ends" with the stalled session's window and its process, from the
     fresh scan the confirm reads, or "Its window has already closed;
     nothing is ended"; "Checked just now" says it is still the chain's
     current start and still stalled, checked again on the press; Cancel
     first and the command behind "Exact command".
   - Every other chain action is D6's safe sheet, its place "In <project> ›
     main checkout", or "On the Mac, for every project" for the greenlight.
     Continue is safe: it types one line and closes nothing.

Decisions made building increment 4
-----------------------------------

.. dec:: Push fans out beside the Mac banner; delivered once any channel delivered
   :id: DEC_ADR_027_D38
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_NOTIFY, COMP_ORACLE_PUSH, COMP_CMD_ORACLE

   - ``_oracle_notifier`` returns a ``FanOutNotifier`` of the Mac banner and,
     where ``cryptography`` is installed, the push notifier, in that order, so
     a slow push service never delays the banner (ruling 17). Each channel is
     tried on its own: one raising never stops the next. Without the extra
     it is the Mac alone.
   - **Delivered** means some channel delivered. The alert ledger and the
     chain's owed notices re-send what no channel delivered; one the Mac
     showed is not owed again, or the Mac would show it twice and break "once
     per rising edge". A channel that failed beside one that delivered is
     reported (``oracle watch: push to iPhone could not notify: …`` on the
     watch's stderr, and on that device's row) and not retried: the push
     service's own TTL already holds a notification for a phone that is off,
     which is the common miss. A channel with nobody to send to
     (``NobodyToNotify``) is neither a delivery nor a failure.
   - **Bounded.** Each POST has a 5 s timeout. An endpoint that could not be
     reached, or answered 429 or a 5xx, rests 60 s, so a push service that
     hangs costs one timeout per device a minute, never one per notification
     in a tick. One device failing never stops another.
   - *Weighed against:* owing each channel separately in both ledgers. It
     would re-send a push the service refused once, at the cost of a second
     ledger shape in ``alerts.json`` and ``chain.json`` for a failure the
     service's TTL mostly covers.

.. dec:: The VAPID key and the subscriptions live in web/push.json, bound to a paired device
   :id: DEC_ADR_027_D39
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_PUSH, COMP_ORACLE_DEVICES, COMP_CMD_ORACLE

   - ``<oracle home>/web/push.json`` holds the VAPID key pair (made the first
     time the page offers push) and the subscriptions: a record beside
     ``devices.json``, owner-only from creation, written under
     ``locks/push.lock``, a newer version or a malformed shape refused, never
     in a manifest (ruling 17's credential rule).
   - A subscription belongs to the device whose token posted it: one endpoint
     to one device, at most four per device (a browser and its home-screen
     app, with room). ``oracle web revoke`` drops the device's; a send also
     skips any subscription whose device is no longer paired, and sends
     nothing when ``devices.json`` cannot be read (it fails closed, as
     ``verify`` does).
   - The watcher notes each send's result on its subscription (``last_ok``,
     or ``last_error`` and when), which the device's row shows; a 404 or 410
     removes it.

.. dec:: The payload is the banner's text, sealed per RFC 8291 and signed per RFC 8292
   :id: DEC_ADR_027_D40
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_PUSH

   - The payload is ``{title, subtitle, message, silent}``: the
     ``Notification``'s three texts, cut to 120, 160 and 600 characters so
     they fit one record, and whether the banner is silent. Nothing else.
   - It is encrypted for the browser's ``p256dh`` and ``auth`` as one
     ``aes128gcm`` record of 4096 bytes, with a fresh salt and sender key per
     send. The POST carries ``TTL: 43200`` (12 hours), ``Urgency: high`` for a
     banner that sounds and ``normal`` for a silent one, and ``Authorization:
     vapid t=…, k=…``: an ES256 token valid 12 hours, ``aud`` the push
     service's origin, ``sub`` the page's own https origin, the one the device
     subscribed from. The ``sub`` contact is required by Apple's service; the
     page's origin names nothing a phone's push service does not already see
     the Mac send from, and needs no new setting.
   - An endpoint is kept only when it is https, port 443, at
     ``push.apple.com``, ``fcm.googleapis.com``, ``push.services.mozilla.com``
     or ``notify.windows.com``, or a host below one, with a P-256 ``p256dh``
     and a 16-byte ``auth``. A paired device cannot point the oracle's POSTs
     at another host.

.. dec:: The page offers push over HTTPS only, through a service worker behind the token
   :id: DEC_ADR_027_D41
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_WEB, COMP_ORACLE_WEBPAGE, COMP_ORACLE_PUSH

   - Push is offered when the Mac has ``cryptography`` and serves the page
     over HTTPS: a service worker and the Push API need a secure context.
     Otherwise ``/api/state``'s view says why, and ``/sw.js`` and subscribing
     are refused (404, 409).
   - ``GET /sw.js`` is the service worker, inline in ``webpage``, behind the
     device token like every route (ADR-008 D4 unchanged), with its own
     ``default-src 'none'; img-src 'self'``. The page's policy gains only
     ``worker-src 'self'``. The worker fetches nothing: it shows the pushed
     text and opens the panel on a tap.
   - ``POST /api/push/subscribe`` and ``/api/push/unsubscribe`` take JSON from
     the page's own ``Origin`` only, as an action does. Subscribing is the
     device's own setting, not an oracle action: it runs nothing on the Mac,
     so it has no confirm sheet; the browser's permission prompt is its
     confirm, and it must follow the tap directly. Unsubscribing works over
     plain HTTP too, so a device can always leave.

.. dec:: The Notifications row: one list row and one pill, after Projects
   :id: DEC_ADR_027_D42
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_PANEL, COMP_ORACLE_WEBPAGE

   - The overview ends with a "Notifications" section: one list row, "Alerts
     on this device" over its state, and a row pill, "Turn on" or "Turn off",
     built from the Projects section's components (ruling 16, no mockup). The
     words are ``panel.WORDS``; what only the browser knows (no push API, an
     iPhone outside the home-screen app, a blocked permission, this browser's
     own subscription) the page words from them.
   - The row reads On only when the Mac holds this device's subscription and
     this browser holds one, so a browser beside the subscribed home-screen
     app is offered Turn on rather than told it is on.

.. dec:: notify-test reports each channel; the extra joins the dev group
   :id: DEC_ADR_027_D43
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_CMD_ORACLE, COMP_ORACLE_NOTIFY

   - ``oracle notify-test`` prints each channel's outcome (the Mac banner,
     each device with push on, "no device has turned push on", or why push is
     not available) and exits 1 when any channel failed, even when another
     delivered: it is a check. JSON adds ``channels``.
   - ``oracle web devices`` says which devices have notifications on; ``web
     revoke`` reports the subscriptions it dropped.
   - ``cryptography`` is also in the ``dev`` dependency group, as ``textual``
     is, so the suite runs the real encryption rather than skipping it; the
     push tests skip cleanly where it is missing. The POST is the
     ``_oracle_push_post`` seam, which the suite replaces for every test.

Decisions made building increment 5
-----------------------------------

.. dec:: The mirror is the engine's reconcile over the main checkout's backlog
   :id: DEC_ADR_027_D44
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_DOMAINS_QUEUE, COMP_ORACLE_CHAINSTEP, COMP_CMD_QUEUE

   - ``domains/queue.QueueDomain.mirror`` is one idempotent reconcile of the
     queue with the backlog, under the queue's lock, writing nothing when
     nothing changed. It is the only writer of mirrored entries.
   - It runs from two places. The chain step has the hands run it
     (``ChainHands.mirror``) for an idle project, before it decides, at most
     once a minute per project (the ledger's ``mirrored_epoch``); what it
     changed is an action line, with no notice. ``invariant queue mirror
     [--project]`` runs it by hand, for a machine with no watcher.
   - It reads the backlog of the main checkout (or the store), the one the
     oracle reads. An item filed in a session's worktree reaches the queue
     when it lands there, so a session's own ``queue add`` of an item it
     filed (a triage's ``--propose`` fix) comes first, and the chain never
     starts an item the main checkout does not hold yet.
   - *Weighed against:* mirroring in ``item new`` and ``item transition``.
     Items are filed in worktrees, so a hook there would queue items the
     chain could not start yet; and a store project's hook would take the ref
     a triage is about to queue as a proposal.

.. dec:: What is mirrored is a state role, or the project's list
   :id: DEC_ADR_027_D45
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_MACHINE_BUG, COMP_MACHINE_IDEA, COMP_CORE_MANIFEST, COMP_DOMAINS_QUEUE

   - An item machine marks the states the mirror puts on the queue with the
     ``mirror`` role, as it marks ready work with ``ready`` (D12): the shipped
     bug machine ``filed`` and ``reproduced``, the idea machine
     ``proposed``, ``accepted`` and ``in_progress``. ``fixed`` and
     ``verified`` carry none, so a fixed bug awaiting its live check is not
     mirrored (ruling 18).
   - ``oracle.chain.mirror_states`` replaces the roles for a project: null,
     or an item type mapped to its states; a type it leaves out is not
     mirrored. The shape is refused at load; a state its machine does not
     have, or a terminal one, is refused when the mirror runs, naming the
     open states, since a project's own machine is read then.
     ``oracle.chain.mirror: false`` turns the mirror off.

.. dec:: A mirrored entry is added once per open spell and leaves when its item does
   :id: DEC_ADR_027_D46
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_DOMAINS_QUEUE, COMP_CORE_QUEUE

   - A mirrored entry is ``added_by: backlog``. The queue's ``mirrored`` list
     names each item the mirror has added in its current open spell, so an
     entry the user removed from the phone stays off; an item that leaves
     the mirrored states leaves the list, so one reopened is mirrored again.
   - An item is skipped while any entry names it in any state (D18 kept:
     done work is not queued again; ruling 9 owns retries and D8 successors),
     while a live entry serves it (ruling 18), and while a live session names
     it by its name or an open deliverable. That is the engine's only link
     from a session to an item, so ruling 18's "an in-progress item with a
     live session" is read this way, for an item in any mirrored state.
   - A **queued** mirrored entry leaves when its item has left the mirrored
     states (closed, dropped, fixed), or when another live entry names or
     serves its item. A started one is the chain's to follow. An entry
     whose item cannot be found is left alone: the item may not have landed.

.. dec:: A mirrored entry goes in by backlog rank; hand-queued entries keep their places
   :id: DEC_ADR_027_D47
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_DOMAINS_QUEUE, COMP_CORE_QUEUE, COMP_ORACLE_CHAIN

   - An item is inserted before the first **queued mirrored** entry whose
     item ranks after it, else at the end. Hand-queued, started, done and
     failed entries are never moved, so a first run puts every open item
     behind whatever is queued, in rank order, and a newly filed one finds
     its rank among the mirrored ones. An entry the user moved stays where
     it was moved; the next item is placed by the entries around it.
   - The rank is D12's, now ``core/queue.rank_key`` (``natural`` with it),
     which ``oracle/chain.rank`` calls, so the engine and the oracle rank by
     one function.
   - **To be amended by** (proposed) :ref:`ADR-031 ruling 9 <adr-031-ruling-9>`: entries a
     session placed by rank are ranked entries too (:need:`DEC_ADR_031_D10`).

.. dec:: An entry may name the item it serves
   :id: DEC_ADR_027_D48
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_CORE_QUEUE, COMP_DOMAINS_QUEUE, COMP_CMD_QUEUE, COMP_ORACLE_CHAIN

   - An entry's optional ``serves`` names an open work item: ``invariant queue
     add REF --serves ITEM``, or ``invariant queue link ID ITEM`` (``--clear``
     unlinks). ``queue list`` shows it. ``Queue.serving`` is the live entry
     naming or serving an item.
   - The mirror skips an item a live entry serves, and the backlog fallback
     and ``oracle chain start --item`` pass over it too.
   - The backfill's links (Q10 to Q16 serving ``IDEA_060``) are commands the
     user runs, not inferred from a label: nothing in ``ADR-029 inc 1`` names
     its idea.

.. dec:: Mirrored entries are backlog work; the shipped defaults are on
   :id: DEC_ADR_027_D49
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAIN, COMP_CORE_MANIFEST

   - An entry ``added_by: backlog`` starts under ``oracle.chain.backlog``,
     any other under ``oracle.chain.queue``. Under ``propose`` the first
     queued mirrored entry rides the verdict's ``proposals`` (one, not the
     whole backlog) and is said once; under ``off`` mirrored entries are
     passed over. Either way hand-queued work behind them runs, so the
     user's order is never held by work they did not queue.
   - The shipped defaults (ruling 19): ``oracle.chain.backlog: auto``,
     ``mirror: true``, ``reproduce_first: true``, every project; a project
     sets any of them in its manifest to opt out.

.. dec:: A filed bug's entry starts reproduce-first, from a template
   :id: DEC_ADR_027_D50
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAIN, COMP_ORACLE_CHAINSTEP, COMP_MACHINE_BUG, COMP_CORE_MANIFEST

   - The ``reproduce`` role marks a state whose item is reproduced before it
     is fixed: the shipped bug machine's ``filed``. A queued entry whose ref
     is such an item starts reproduce-first when
     ``oracle.chain.reproduce_first`` is on, whoever queued it, and through
     ``oracle chain start`` too (``chain.reproducing``).
   - Its first prompt is ``oracle.chain.reproduce_prompt``, filled with
     ``{ref}``, ``{title}``, ``{entry}`` and ``{attempt}`` (1, or 2 after a
     run that saw nothing), whatever the entry's brief, which is the fix's.
     Null takes the shipped sentence, which names the bug-reproducer agent
     and tells the session to land the verdict on the item and not to fix
     or queue anything; the agent names stay in the template (D19).
   - The ledger's ``current`` records ``reproduce`` and ``residue``, the
     residue the bug owed when the start was made.

.. dec:: The verdict is read from the item when the session lands, and the same entry carries on
   :id: DEC_ADR_027_D51
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAIN, COMP_ORACLE_CHAINSTEP, COMP_DOMAINS_QUEUE, COMP_CORE_QUEUE

   - When a reproduce-first session ends done, ``chain.reproduce_outcome``
     reads its bug from the registered root: in a ``ready`` state is ``red``
     (RED-CONFIRMED moved it to ``reproduced``); in no ``reproduce`` state, or
     gone, is ``closed`` (ALREADY-FIXED's ruled ``fixed``, or
     ``not_reproducible``); still unreproduced and owing more residue than
     at the start is ``owed`` (DEVICE-ONLY's live leg); otherwise ``blind``
     (HARNESS-BLIND writes only a note). No verdict word is parsed: the
     agent's own routing writes the item, and the item is the record.
   - ``ChainHands.reproduced`` routes it in one locked edit
     (``domains/queue.after_reproduce``): ``red`` puts the same entry back
     at the head, queued, so the fix starts on this or the next tick under
     the usual limits; ``closed`` marks it done; ``owed`` makes it a propose
     entry where it stands; ``blind`` adds one to its ``blind`` and puts it
     at the head, and a second makes it a propose entry. One entry carries
     a bug from reproduction to fix; the ledger's starts keep each session.
   - A verdict that cannot be read, or hands without ``reproduced``, mark
     the entry done as before. A reproduce session that fails is ruling 9's.
     Nothing here pauses the chain.

.. dec:: The phone says where an entry came from, what it serves and that a start reproduces
   :id: DEC_ADR_027_D52
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_PANEL, COMP_ORACLE_CHAINVIEW

   - A queue row's secondary line adds "Added from the backlog", "Serves
     <item>" and "Reproduction saw nothing (N of 2 runs)"; the Now line of a
     reproduce-first start reads "Reproducing in a window on the Mac". The
     chain read carries ``serves``, ``blind`` and ``reproduce``. No new
     screen, component or action: a mirrored entry moves and is removed like
     any other (ruling 16's design system).

Decisions made building IDEA_062 (ruling 21)
--------------------------------------------

.. dec:: A landed chained window is followed in the ledger's landed until it closes
   :id: DEC_ADR_027_D53
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAINSTEP, COMP_ORACLE_CHAIN

   - The tick that marks a chained start ``done`` (D15) writes the project's
     ``landed``: the start's id, entry, ref, session, window name and pid, the
     teardown time (``landed_epoch``, the session record's last ``heartbeat``
     through ``chain.session_landed``, else the tick's time), the limits of
     :need:`DEC_ADR_027_D60`, and the record of every ask, answer, notice and
     close: ``asks`` (``at``, ``code``), ``answers`` (``answer``, ``note``,
     ``at``), ``notice`` (``why``, ``at``, ``note``, ``detail``), ``decided``
     and ``closed``. One per project; a start the user makes beside it is
     followed on its own.
   - Each tick finds the window by its launched name or pid, as D15 binds a
     start, on an orphan host row or a session row. Not seen alive for
     ``GONE_SECONDS`` (60 s) it ends as ``gone``, with no notice. While it
     lives, D5's live-window rule holds the project; ``decide`` is unchanged.
   - An ended record moves to ``landings``, the last 20 per project, with its
     ``outcome``: ``closed`` (on consent), ``closed_by_user`` or ``gone``.
   - Hands without a closer follow no landed window: nothing could close it,
     and the window holds the project as before.
   - **To be amended by** (proposed) :ref:`ADR-031 ruling 1 <adr-031-ruling-1>`: ``landed``
     carries the session's landing record, a window that ended itself ends
     ``ended_itself``, and ``ending`` and ``ongoing`` join its states
     (:need:`DEC_ADR_031_D6`, :need:`DEC_ADR_031_D8`).

.. dec:: The user's window is told from the person's prompts the attention record counts
   :id: DEC_ADR_027_D54
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_ATTENTION, COMP_ORACLE_CHAINSTEP

   - The attention record gains ``prompts``, the turns the person started
     (``UserPromptSubmit`` of a prompt; a background task's notice is not
     one), and ``prompted``, when the last began. A record written before
     them counts from nothing.
   - Before the first ask, a prompt later than the teardown makes the window
     the user's. From the first ask on, more prompts than the count at the
     first ask plus the asks typed do: the oracle's own line is one prompt
     per ask. A live session row holding the window is someone's turn too.
   - A window whose attention record cannot be read, or whose last activity is
     unknown, cannot be judged: it is never asked, and the notice goes to the
     user (``unread``). So a late or wrong read leads to asking the user,
     never to a close.
   - The user's window (``users``) gets one notice saying it holds the
     project, is never asked and never closed, and offers no Close.
   - **To be amended by** (proposed) :ref:`ADR-031 ruling 2 <adr-031-ruling-2>`: the user's
     window no longer holds the project; it moves to the ledger's ``kept``
     (:need:`DEC_ADR_031_D7`, :need:`DEC_ADR_031_D9`).
   - *Weighed against:* reading the transcript's last user entry, which needs
     a new host read and cannot tell the oracle's typed line from the user's.

.. dec:: Quiet is an idle turn and no activity for close_ask_after minutes
   :id: DEC_ADR_027_D55
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAINSTEP, COMP_ORACLE_SIGNALS

   - The window's row reads ``turn_state`` ``idle`` (``signals.turn_state``:
     not busy, not waiting on a prompt or a question, no background work) and
     its ``last_activity``, which joins the transcript and the attention
     record, is at least ``oracle.chain.close_ask_after`` minutes old.
   - Someone typing into Claude's input box without sending it cannot be
     seen; the quiet minutes stand for "nobody typing", the risk D28 already
     took for the "continue".

.. dec:: The ask is ruling 21's line, reserved under the lock and typed by the nudge's typer
   :id: DEC_ADR_027_D56
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAINSTEP, COMP_ORACLE_CHAIN, COMP_HOSTS_CLAUDE_CODE_LAUNCH

   - ``chain.ask_line`` is the ruling's line with the project's registered
     name. It is reserved under the chain lock (``asking``) and typed outside
     it through ``ChainHands.nudge``: ``TerminalTyper`` by the window's tty,
     refusing a tab where only a shell runs, then a lone Return.
   - Typed is ``asked``. No tty, hands without a typer, or a typer answering
     anything but ``typed`` is the notice (``not_typed``); so is an ask a
     stopped watcher left ``sending`` for 120 s.
   - An ask is an action line and a ledger record, not a notification.
   - After a "not yet" the next ask waits ``close_ask_again`` minutes from
     the answer, and for the window to be quiet again.

.. dec:: Consent is an engine record the CLI writes, answered from the window asked
   :id: DEC_ADR_027_D57
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_CMD_ORACLE, COMP_ORACLE_CHAINSTEP, COMP_DOMAINS_SESSION

   - ``invariant oracle chain consent <project> --close | --not-yet --note
     TEXT`` appends the answer to ``landed.answers`` under the chain lock
     (``chainstep.consent``). The note is vetted by the handoff note's check
     (``vet_line``): one line, bounded, privacy-scanned. ``--note`` goes only
     with ``--not-yet``.
   - Refused (exit 2), writing nothing: no landed window; no ask typed yet;
     the user's window or one left open; ``CLAUDE_PID`` naming another Claude
     than the window asked. A command with no ``CLAUDE_PID`` (the user in a
     shell) is accepted. A second ``--close`` says it is recorded already.
   - A "not yet" schedules the next ask; the one answering the last ask is
     the notice, with its note.
   - An answer after the notice is kept: ``--close`` still closes, since the
     session's consent is what the ruling asks for; ``--not-yet`` asks
     nothing more.

.. dec:: The watcher closes a consenting window through close-host's path, then releases it
   :id: DEC_ADR_027_D58
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAINSTEP, COMP_ORACLE_CLOSEHOST, COMP_CMD_ORACLE

   - ``consent`` only records. The consenting Claude is the command's own
     parent, and ending it from inside that tool call would end the command
     with it. The next tick whose window's turn is idle, or 60 s after the
     consent at the latest, reserves the close (``closing``) and closes
     outside the lock through ``ChainHands.close``: ``_chain_closer``,
     ``closehost.close_host`` with close-host's seams, every fact checked
     again, as D29's Retry and Close do.
   - Closed: the landing ends ``closed``, its session joins ``released``
     (D30) and a silent notice says so; the next tick starts the next work.
     A close that raises, or one a stopped watcher left ``closing`` for 120
     s, is the notice (``close_failed``).
   - A turn the user typed after the consent still makes the window the
     user's, and it is not closed.

.. dec:: Close and Leave open are the user's verbs on a window whose notice was sent
   :id: DEC_ADR_027_D59
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_CMD_ORACLE, COMP_ORACLE_CHAINSTEP

   - The notice, one per landed window and with a sound, says why (no answer
     within ``close_answer_within`` minutes; "not yet" ``close_asks`` times,
     with the latest note; the ask could not be typed; the oracle cannot tell
     whether the user typed; the close failed) and names ``invariant oracle
     chain close-window <project>`` and ``leave-open <project>``.
   - ``close-window`` and ``leave-open`` are refused (exit 2) unless the
     landed window waits for the user (``notified``). Close finds the window
     in a fresh scan (``_chain_landed_host``) and ends it through the hands'
     closer; the landing ends ``closed_by_user`` and its session is
     released; a window already gone is said. Leave open records
     ``left_open``: never asked again, it holds its project until the user
     closes it, and then ends ``gone``.
   - *Weighed against:* widening D29's ``close``, which needs a recorded stall
     and marks its entry failed; a landed entry is done.
   - **To be amended by** (proposed) :ref:`ADR-031 ruling 7 <adr-031-ruling-7>`: Leave open
     moves the window to the ledger's ``kept``, and it no longer holds the
     project (:need:`DEC_ADR_031_D9`).

.. dec:: The four numbers are oracle.chain.close_*, read when the window lands
   :id: DEC_ADR_027_D60
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_CORE_MANIFEST, COMP_ORACLE_CHAIN

   - ``oracle.chain.close_ask_after`` (5), ``close_ask_again`` (30) and
     ``close_answer_within`` (10) are minutes, numbers above 0;
     ``close_asks`` (3) is a whole number of at least 1. Anything else is
     refused at load. The user's standing preference: every number is a
     per-project setting.
   - They are read once, when the window lands, into ``landed.limits`` (in
     seconds): a change applies from the next landing.

.. dec:: The phone shows a landed window on the Chain screen and, once noticed, as a card
   :id: DEC_ADR_027_D61
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_ORACLE_CHAINVIEW, COMP_ORACLE_PANEL

   - The chain read carries ``landed`` (its state, the notice's why, the
     latest note, the asks typed) and offers ``close-window`` and
     ``leave-open`` only while it is ``notified``; their keys are
     ``chain:close-window`` and ``chain:leave-open``, and ``derive`` names the
     window from the fresh scan.
   - A "Window open" card under Needs you, ranked with a stall, says why and
     carries Close… and Leave open… (ruling 15). The user's window and one
     left open are no card: nothing is asked of the user.
   - While the chain follows no start, the Chain screen's Now shows the
     landed window, its line and, when noticed, the two buttons.
   - Close is the destructive sheet naming the window, as D37's; Leave open
     is the safe sheet (ruling 14). Every word is in
     :doc:`oracle-panel.md </architecture/oracle-panel>`.
   - **To be amended by** (proposed) :ref:`ADR-031 ruling 2 <adr-031-ruling-2>`: the card and
     the sheet say the chain goes on beside a kept window, and Now shows a
     window ending itself or still working (:need:`DEC_ADR_031_D13`).

.. dec:: The shipped first prompts open with the session start
   :id: DEC_ADR_027_D62
   :status: accepted
   :part_of: DEC_ADR_027
   :affects: COMP_CORE_MANIFEST, COMP_ORACLE_CHAIN

   - BUG_158: the chain step binds a window only to a session row whose host
     is that window (:need:`DEC_ADR_027_D15`), and only the window's own
     ``invariant session start`` creates the row. A start on a shipped
     default prompt carried no such step, so it never bound and failed at the
     bind timeout. The bind is unchanged; the defaults now carry the step.
   - Each of ``DEFAULT_CHAIN_PROMPT``, ``DEFAULT_TRIAGE_PROMPT`` and
     ``DEFAULT_REPRODUCE_PROMPT`` opens with "First run invariant session
     start <task>, invariant session isolate --execute and invariant session
     plan add", the steps a user-written brief that binds carries, then its
     former sentence. Each stays one line and names only its own placeholders.
   - The task names derive from the placeholders and carry the entry, so a
     retry (a new entry) or a later attempt never meets a parked session of
     the same task: ``{ref}-{entry}`` for a start, ``triage-{entry}`` for a
     triage (the failed entry), ``reproduce-{ref}-{entry}`` for a
     reproduction.
   - A project's own ``prompt``, ``triage_prompt`` or ``reproduce_prompt``
     is its own: the engine does not add the step to it.
   - *Weighed against:* the chain step running the session start itself
     before it launches the window, which would bind a session the window
     never created and move session ownership into the oracle.

Increments
----------

+-----+------------------------------------------------------------------+
| Inc | Builds                                                           |
+=====+==================================================================+
| 1   | The queue record and ``invariant queue``; ``session report``;    |
|     | the pure ``decide`` and ``invariant oracle next`` (D1–D3, the    |
|     | limits it can judge without a ledger). Nothing starts on its     |
|     | own.                                                             |
+-----+------------------------------------------------------------------+
| 2   | The watcher's chain step: auto and propose, the ledger, daily    |
|     | and machine caps, hours, pause/resume, failure pauses (D4–D6);   |
|     | the agents' landing step queues instead of launching (D8).       |
+-----+------------------------------------------------------------------+
| 2b  | The ``plan_usage`` probe and the weekly-usage hold and           |
|     | greenlight (D5); stall detection and the nudge (D7); Continue,   |
|     | Retry and Close; released sessions (rulings 10 to 13, D24 to     |
|     | D30).                                                            |
+-----+------------------------------------------------------------------+
| 3   | The phone panel: the queue (reorder, remove), proposals,         |
|     | pause/resume, greenlight, the stall actions (rulings 14 to 16,   |
|     | D31 to D37). **Built.**                                          |
+-----+------------------------------------------------------------------+
| 4   | Web push from the oracle's page (IDEA_013) as a notifier, fanned |
|     | out beside the Mac banner; the panel's opt-in per paired device  |
|     | (ruling 17, D38 to D43). **Built.**                              |
+-----+------------------------------------------------------------------+
| 5   | The whole backlog reaches the chain: the mirror, ``serves``,     |
|     | backlog work auto by default, a filed bug reproduced first and   |
|     | its verdict routed (rulings 18 to 20, D44 to D52). **Built.**    |
+-----+------------------------------------------------------------------+
| 6   | A landed chained window is asked whether it may close, closed on |
|     | its consent, and handed to the user with Close and Leave open    |
|     | otherwise (ruling 21, IDEA_062, D53 to D61). **Built.**          |
+-----+------------------------------------------------------------------+

Consequences
------------

- What comes next is a record, not a line in a brief: the user can see and
  reorder it from the phone, and a chain that stops says why.
- The oracle gains its second standing power (after ADR-009's confirmed
  actions): starting a session without a tap, bounded by the project's
  setting and the limits, and typing one "continue" into a stalled session.
- Until increment 2 lands, sessions still start their successor by hand;
  increment 1's ``oracle next`` shows what the oracle would have done.
- From increment 2 the oracle starts queued work on its own in every
  registered project, and offers each project's first ready backlog item
  once, as a proposal. A failure costs a triage session and one retry, not a
  stopped chain (ruling 9); only a stop report, a failed triage or an empty
  queue stops it, and ``oracle chain pause`` stops it by hand.
- The repository queue gains writes from the watcher in the main checkout;
  they stay uncommitted until the next landing's memory/config sync commits
  them.
- From increment 2b nothing starts or is proposed on its own until the user
  installs the status line (``invariant hooks statusline``) or gives a
  greenlight: without a reading the weekly hold is ``unknown``. A machine
  with no session running for 30 minutes has no fresh reading either, so the
  first chained start after a quiet spell may wait for a greenlight.
- The oracle types into a running session for the first time: one
  "continue" per stall, only into a chained session's own tab. A chained
  session given up on stays parked beside the next one, so its worktree is
  the user's to land or abandon.
- From increment 3 the phone shows each project's chain and acts on it,
  every action confirmed with its command (ruling 14): the queue can be
  reordered and trimmed, a proposal started, the chain paused or resumed,
  the usage hold lifted and a stall answered without the Mac. A queue edited
  on the Mac between a confirm and its press refuses instead of moving the
  wrong entry.
- From increment 4 every alert and chain notice also reaches each phone that
  turned push on, as ciphertext through its browser's push service: the
  oracle's first notifications that leave the Mac, limited to the banner's
  own text (ruling 17). Push needs the ``invariant[oracle]`` extra and the
  page over HTTPS; on an iPhone, the home-screen app. A push that fails while
  the Mac banner showed is reported, not re-sent.
- From increment 5 every open backlog item is on its project's queue, and
  the chain works through it on its own, in rank, one at a time under the
  limits; a project opts out in its manifest. A filed bug costs a
  reproduction session before its fix, and its fix starts as soon as the
  reproduction lands red; a bug the reproduction closed leaves the queue, and
  one owing a live repro or unseen twice waits for the user as a proposal.
  The repository queue gains many entries ``added_by: backlog`` on the first
  idle tick after landing, committed by the next landing's sync.
- From increment 6 (ruling 21) a chained session that lands no longer holds
  its project until someone closes its window by hand: once the window has
  been quiet the oracle types one question into it, and closes it when the
  session answers ``invariant oracle chain consent <project> --close``. The
  oracle types into a finished session's tab, up to three times; a window the
  user typed in is never asked, and one that does not answer reaches the
  phone with Close and Leave open, never a silent close. Every attention
  record now counts the person's prompts.
