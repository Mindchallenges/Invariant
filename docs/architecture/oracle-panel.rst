The oracle's mobile control panel
=================================

The design of record for the oracle web page as a mobile control panel
(IDEA_027). The decisions behind it are ADR-009's. The page it replaces, and
the server, transport and pairing it keeps, are in :doc:`oracle.md </architecture/oracle>`,
"Shipped: the web page". The person-facing setup is the guide's
:doc:`The oracle on your phone </guide/oracle-web>`.

**Status: increments 3 to 6 shipped.** The user approved the mockup on
2026-10-23. Increments 3 to 7 build it ("Increment plan" below). Increment
3, the plain-language panel, increment 4, the home-screen app, increment 5,
failure reasons, and increment 6, orphan classes and close host, are what
the page serves now; "As built in increment 3" to "As built in increment 6"
below record where they fill in what this page left open. Increment 7 is not
built. IDEA_030's increment 2 added the "New session" sheet and the
"Starting…" card ("7 · New session" and "As built in IDEA_030 increment 2"
below; ADR-009 D16). ADR-027's increment 3 added the chain: its cards under
Needs you, a chain line on each project's row, and the project Chain screen
("8 · Chain" and "As built for ADR-027 increment 3" below), built from this
page's tokens and components with no mockup round (ADR-027 ruling 16).
ADR-027's increment 4 added web push: a Notifications row at the end of the
overview, where this device turns the Mac's alerts and the chain's notices on
or off ("As built for ADR-027 increment 4" below; ruling 17), built the same
way. ADR-027's increment 5 put the backlog on the queue: a queue row now also
says an entry came from the backlog, what it serves and how its reproduction
went, and a reproducing start says so ("As built for ADR-027 increment 5"
below), with no new screen. ADR-029 (IDEA_060, accepted 2026-11-20) is the
page talking to running sessions. Its increment 1 is built: a live window's
last message on its card, its leftover row and its detail ("As built for
ADR-029 increment 1" below), the Conversation screen (increment 2), sending
a message (increment 3) and Stop and slash commands (increment 4) are built
too, each in its "As built for ADR-029" section below. Answering prompts and
a Phone rules screen are planned ("Planned: talking to sessions" below). Until it is built, every "never
answers prompts" on this page describes the panel as it is.

**Visual reference.** The approved mockup is a private claude.ai artifact of
six 390-pixel-wide phone screens. The scratch files it was drawn from did not
outlive the session that made them. This page copies every value the build
needs, so it is the record: where it and the artifact disagree, this page
wins.

.. invariant: allow-privacy

Mockup: https://claude.ai/artifact/M8PBU7JKuUxbYmV6MLViKK (screens 1 to 6:
overview, session detail, confirm, destructive confirm, failed result,
leftovers).

Where it sits
-------------

The panel changes three modules and adds one. The wording table, the
grouping and the summary counts are pure functions of the ``assess`` document,
so they get a module of their own, ``oracle/panel``, which ``web.view`` calls.
It imports nothing that runs anything: no ``actions``, no ``webactions``, no
``hosts/``. The page renders what the view returns and holds no vocabulary
(ADR-009 D2, D3).

.. mermaid::

   graph TD
       CLI[cli — invariant oracle web]
       subgraph oracle["oracle/"]
           WEB["web (view, routes; inc 4, shipped: manifest, icons)"]
           PANEL["panel (inc 3, shipped: groups, summary, wording table)"]
           WEBPAGE["webpage (inc 3, shipped: cards, detail, sheets; inc 4, shipped: manifest, icon)"]
           WEBACT["webactions (inc 5, shipped: failure summariser; inc 6, shipped: close's outcome)"]
           ACTIONS[actions]
           DASH[dashboard]
           SIGNALS["signals / recommend (inc 6, shipped: orphan classes, close host)"]
           SCAN["scan (inc 6, shipped: rooted_here, claimed_elsewhere)"]
           START["startsession (IDEA_030 inc 2: Starter, find_started)"]
           CHAINVIEW["chainview (ADR-027 inc 3: ChainReader, offers, derive)"]
           PUSH["push (ADR-027 inc 4: the key, this device's subscription)"]
           TALK["talk (ADR-029 inc 1: SessionTalk, last_message)"]
       end
       CLI --> WEB
       WEB --> PANEL
       WEB --> WEBPAGE
       WEB --> WEBACT
       PANEL --> SCAN
       PANEL --> SIGNALS
       WEBACT --> PANEL
       WEBACT --> ACTIONS
       SIGNALS --> SCAN
       WEBACT --> START
       PANEL --> START
       CLI -. injects the Starter into .-> WEBACT
       CLI -. injects the ChainReader into .-> WEBACT
       WEBACT --> CHAINVIEW
       PANEL --> CHAINVIEW
       WEB -- "the Notifications row's facts; subscribe, unsubscribe" --> PUSH
       WEB -- "each live window's last message (ADR-029 inc 1)" --> TALK
       CLI -. injects SessionTalk into .-> WEB

``webactions → panel`` is for the confirm's and the result's words: the
confirm sheet's title, sentence and button come from the same table as the
card's button, and the result's "Why" is worded by ``panel.why_words`` from
what ``webactions.evidence`` read in the output. The new arrows are ``web → panel``, ``panel → scan`` (for
``row_key`` and the row kinds), ``panel → signals`` (the signal kinds and
``recommend``'s step kinds) and ``webactions → panel``. IDEA_030 adds
``webactions → startsession`` (the ``Starter`` the CLI injects, and
``find_started``) and ``panel → startsession`` (``find_started`` and the ``start``
kind), both pure. As built, the panel
needs nothing from ``dashboard``, so the ``panel → dashboard`` arrow this page
first drew is not there, and ``web`` no longer reads ``scan`` or ``signals``
itself: the two arrows ``web → scan`` and ``web → signals`` went with the old
per-project view.
ADR-027's increment 3 adds ``webactions → chainview`` (the reader the CLI
injects, read for every state, confirm and action, and ``derive``) and
``panel → chainview`` (the action names only: the panel words the read it is
handed and reads nothing itself).
ADR-029's increment 1 adds ``web → talk``: the web server reads each live
window's last message through the ``SessionTalk`` the CLI injects and hands
the texts to ``panel.view``. The panel names which window each row's text is
read from (``excerpt_hosts``) and reads nothing itself, so there is no
``panel → talk`` arrow.

Screens
-------

.. mermaid::

   stateDiagram-v2
       [*] --> Overview
       Overview --> Overview: tap a count (scroll to its group)
       Overview --> Detail: tap a card, or Details
       Overview --> Leftovers: Leftovers tile, or the leftovers card
       Overview --> Confirm: a card's primary button
       Detail --> Confirm: suggested step, or a More actions row
       Detail --> Destructive: a red More actions row (inc 7)
       Leftovers --> Destructive: Close… / Remove… (inc 6, 7)
       Confirm --> Result: Run
       Destructive --> Result: the red button
       Confirm --> Detail: Cancel
       Destructive --> Detail: Cancel
       Result --> Detail: Done
       Destructive --> Leftovers: Cancel (a close from the leftovers, inc 6)
       Result --> Leftovers: Done (a close from the leftovers, inc 6)
       Overview --> NewSession: New session… (top bar), or a project's New session…
       NewSession --> NewSession: an edit (the Mac is asked again)
       NewSession --> Result: Start session
       NewSession --> Overview: Cancel
       Overview --> Result: a Starting… card
       Overview --> Chain: a project's row, or a chain card's Details
       Overview --> Confirm: a chain card's button (inc 3)
       Chain --> Confirm: up, down, Remove…, Start…, Pause or Resume chain…, Continue…
       Chain --> Destructive: Retry…, Close… (a stalled chained start)
       Chain --> Confirm: Leave open… (a landed window, ruling 21)
       Chain --> Destructive: Close… (a landed window, ruling 21)
       Confirm --> Chain: Cancel (an action from the Chain screen)
       Destructive --> Chain: Cancel (an action from the Chain screen)
       Result --> Chain: Done (an action from the Chain screen)
       Detail --> Overview: back
       Leftovers --> Overview: back
       Chain --> Overview: back

The detail, leftovers and Chain screens are views inside the one page, not new
routes: they are drawn from the state document the page already polls, so
they refresh with it. Back returns to the scroll position the person left.
The confirm, destructive confirm and result are bottom sheets over the
screen they were opened from.

1 · Overview
~~~~~~~~~~~~

.. ui:: Panel screen: Overview
   :id: UI_PANEL_OVERVIEW
   :uses: COMP_ORACLE_PANEL

   The control panel's screen showing the sessions grouped by what they need
   from you, with the counts and the leftovers. ``oracle/panel`` decides its
   words and ``oracle/webpage`` draws it.


Top to bottom, with 20 px between blocks and a 16 px side margin:

1. **Header.** "Sessions" as the title, and under it the freshness line: a
   7 px green dot and "Live · updated N s ago". When stale it reads "Stale ·
   updated N s ago" in red, with a red dot. On the right, a round 44 px
   Refresh button that reads at once.
2. **Summary strip.** Four equal tiles in one row: Need you (amber), Problems
   (red), Working (blue), Leftovers (grey). Each tile is a count over a
   label. The first three are anchor links to their group, the last opens
   the Leftovers screen. A count of 0 still shows, so the strip never moves.
3. **Groups**, in order: Needs you, Problems, Working, Idle. Each has an
   uppercase heading in its accent colour. A group with no rows is not
   drawn. Idle has no tile and uses the compact card.
4. **Leftovers card**, last: "N leftovers to tidy", a one-line breakdown
   ("8 Claude windows with no session, 2 left-over folders") and a chevron.
   Not drawn when there are none.

A plain-HTTP warning, the watcher banner and a read error keep their place
under the header (oracle.md, "What the page shows").

**Notifications** (ADR-027 increment 4), the overview's last block, after
Projects: the section label "Notifications" and one 64 px list row, "Alerts
on this device" over a line that says what this device gets, and one pill,
"Turn on" or "Turn off", which acts at once (no confirm sheet: the browser's
own permission prompt is the confirm). Drawn whenever the Mac's page knows
about push, so a Mac that cannot send them says why, with no pill.

**Full card** (Needs you, Problems). From top to bottom:

- a meta row: the project name, uppercase, on the left, and the age of the
  condition ("4 min") on the right;
- the session's title: its name without the trailing id, else its task;
- one or more state chips (wording table, "States");
- an optional note line (secondary text), such as "Its Claude window closed.
  Fixing a bug." or the permission note;
- a button row: the primary button (blue, flexible width) and Details
  (92 px, secondary). A row with nothing to run shows only Details, full
  width. A row waiting for input gets "Reply…" (ADR-029 increment 3) and one
  waiting on a question the page read gets "Answer…" (increment 5), which
  open its conversation at the prompt (ADR-029 D10). A row waiting on a
  permission prompt, or on a question that cannot be read, shows no
  buttons, and the whole card opens the detail view (ADR-009 D8) until
  increment 6.

**Compact card** (Working, Idle). A single tappable row: the project
(uppercase), the title, and a line "``<phase>`` · active N min ago", with a
chevron. It opens the detail view.

2 · Session detail
~~~~~~~~~~~~~~~~~~

.. ui:: Panel screen: Session detail
   :id: UI_PANEL_SESSION_DETAIL
   :uses: COMP_ORACLE_PANEL

   The control panel's screen showing one session: its lane and phase, its
   recent events and the steps it offers. ``oracle/panel`` decides its words
   and ``oracle/webpage`` draws it.


1. A back link, "‹ Sessions", 44 px tall.
2. The kicker "``<project>`` · ``<lane>``" (uppercase), the title (28 px), and the
   state chips with ages ("Paused 3 h").
3. **What is going on**: a card with one to three plain sentences composed
   from the wording table. Example: "The last test run had 52 failures. The
   Claude window working on it closed 3 hours ago, so nothing is happening
   until you resume it."
4. **Suggested next step**: the step's name ("Re-run the tests"), one
   sentence on what it does and does not touch, then the 48 px primary
   button and a 48 px square Copy button.
5. **More actions**: a grouped list of 56 px rows, each a title and a
   one-line consequence, with a chevron. "Resume in a new Claude window"
   (Opens Terminal on your Mac) from increment 3. From increment 7, the red
   rows: "Abandon session" (Keeps its folder and branch) and "Remove its
   folder and branch" (Deletes unmerged work). A row is shown only when its
   action is allowed on this row now.
6. **Recent**: dated events of this session, newest first, as "``<event>``" and
   a local time on the right. Only events with a recorded time (ADR-009 D4).
7. **Technical details**, closed by default. Session id, state and lane
   ("Session 4b7b00c1 · state isolated · lane bugfix:fixing"), the worktree
   path in a horizontal scroller, and the host id with its pid or "process
   id not recorded". This is the only place engine names appear.

3 · Confirm (a safe step)
~~~~~~~~~~~~~~~~~~~~~~~~~

.. ui:: Panel screen: Confirm (a safe step)
   :id: UI_PANEL_CONFIRM_SAFE
   :uses: COMP_ORACLE_PANEL

   The control panel's screen showing the sheet that confirms a safe step, with
   the exact command. ``oracle/panel`` decides its words and ``oracle/webpage``
   draws it.


A bottom sheet with a 24 px top radius, over the dimmed screen (28 %
opacity of the content behind):

1. A grabber, 36 by 5 px.
2. The title, a question ("Re-run the tests?"), 22 px bold, and one
   sentence: "Runs the tests this session last ran in Heap, in its own folder
   and with its own settings, and records the result. Nothing is changed or
   deleted."
3. **Command** block: an uppercase "Command" label with a Copy button on
   the right, the command in 15 px monospace (horizontal scroller), and the
   place in plain words: "In Heap › session folder 4b7b00c1".
4. A closed toggle, "Exact folder and environment": the exact ``cwd`` (in a
   scroller), each added variable as ``NAME=value``, and "Runs as you on the
   Mac, without the marks of any Claude window." A value the project's
   privacy patterns flag is shown masked, followed by "(masked; the
   session's recorded value is used when it runs)", and the Command block's
   line is then a comment, "# Not runnable as shown, NAME masked: …"
   (BUG_093).
5. The Run button (primary, 52 px, named for the deed: "Run tests") over
   Cancel (secondary, 52 px). Cancel holds focus when the sheet opens.
6. "This confirm expires in M:SS", counting down from the nonce's 120 s.
   At zero, Run is disabled and the line becomes "This confirm expired.
   Look again".

A terminal step adds "Opens Terminal on your Mac" to its sentence and
shows the launch line inside the toggle.

4 · Confirm (a destructive step)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. ui:: Panel screen: Confirm (a destructive step)
   :id: UI_PANEL_CONFIRM_DESTRUCTIVE
   :uses: COMP_ORACLE_PANEL

   The control panel's screen showing the sheet that confirms a destructive
   step, its facts checked again when pressed. ``oracle/panel`` decides its
   words and ``oracle/webpage`` draws it.


As screen 3, except:

1. The sentence ends with the cost: "Ends this session and deletes its work
   that was never merged. This cannot be undone."
2. **This deletes**: a red-ground panel listing each thing that goes, one
   line each, counted from a fresh read: "The session folder
   Heap-wt-4b7b00c1", "The branch session/4b7b00c1, with 3 commits not on
   main", "2 changed files that were never committed".
3. **Checked just now**: "No Claude window is using this session" and
   "Checked again the moment you press Remove".
4. The exact command behind a closed "Exact command" toggle.
5. Cancel first, then the red button named for the deed ("Remove folder and
   branch").
6. The expiry line, as screen 3.

5 · Result (failed)
~~~~~~~~~~~~~~~~~~~

.. ui:: Panel screen: Result (failed)
   :id: UI_PANEL_RESULT_FAILED
   :uses: COMP_ORACLE_PANEL

   The control panel's screen showing a step that failed, worded from
   invariant's own line. ``oracle/panel`` decides its words and
   ``oracle/webpage`` draws it.


1. A 40 px round red mark with a cross, the headline ("The tests did not
   start", 20 px bold), and under it "``<step>`` · stopped after N s".
2. **Why**: one sentence (ADR-009 D9).
3. **Show full output**, closed: the output's tail in 12 px monospace,
   ``white-space: pre``, at most 160 px tall, scrolling inside.
4. Done (secondary, 52 px), which returns to the detail view.

A successful result uses the same sheet with a green tick, "Done in N s" and
the same output toggle. A terminal step's result says "Opened in a new
window" or why it was not seen alive.

6 · Leftovers
~~~~~~~~~~~~~

.. ui:: Panel screen: Leftovers
   :id: UI_PANEL_LEFTOVERS
   :uses: COMP_ORACLE_PANEL

   The control panel's screen showing left-over Claude windows and folders, and
   closing them. ``oracle/panel`` decides its words and ``oracle/webpage``
   draws it.


1. Back link, title "Leftovers", and "Things that outlived their session.
   None of them needs you; tidy them when you like."
2. **Claude windows with no session**, with its count: 64 px rows of
   "``<project>`` · idle 2 days" over "Still open on the Mac · window 27885f4e",
   each with a "Close…" pill (increment 6). The first three show, then
   "Show N more".
3. **Left-over folders**, with its count: "``<project>`` · session 0c269de4"
   over "Abandoned · 1 commit not on main", with "Remove…": red-tinted when
   it holds unmerged work, grey when it holds none (increment 7).
4. A note card: "Close and Remove always ask first, show what goes, and
   check again that nothing is using it the moment you confirm."

As built in increment 6, a Claude window's row carries its pill when its
step is one the panel runs: "Close…" for a host every close guard lets go,
"Attach…" for an unmanaged one that is the only live window the one
session here without a window could take. A row with nothing to run has no
pill, and the folders have none until increment 7. A live leftover or
unmanaged window that fails a close guard says which on its row, after the
window: "Still open on the Mac · window 27885f4e · cannot close from here:
invariant could not read its folder" (BUG_099). A live window that
inherited Claude's markers from its Terminal.app (IDEA_035) says so last:
"… · Terminal.app needs a restart: quit it fully and relaunch it from the
Dock".

7 · New session (IDEA_030)
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. ui:: Panel screen: New session (IDEA_030)
   :id: UI_PANEL_NEW_SESSION
   :uses: COMP_ORACLE_PANEL

   The control panel's screen showing starting a new session in a registered
   project. ``oracle/panel`` decides its words and ``oracle/webpage`` draws it.


A bottom sheet, built like screen 3, opened by "New session…" in the
overview's top bar (left of Refresh; 44 px tall, radius 22, ``surface-button``,
15 px semibold; hidden on the detail and leftovers screens and on a page that
cannot act) or by a project's "New session…" pill in the **Projects**
section, the overview's last block: one 64 px list row per registered project,
its name over "N open sessions", and the pill. The pill opens the sheet with
that project chosen; the top bar's chooses the first. Open means not ended,
not "a Claude window is attached": the count is every session row of the
project, so it equals the sum of that project's cards in Needs you, Problems,
Working and Idle. When any of them has no live host (its host ended, or its
card reads "Paused": ``parked`` or ``host_gone``), the line adds " · N paused",
as in "2 open sessions · 1 paused" (BUG_092). A session waiting on its
background work has a live host and is not paused.

1. Grabber, the title "Start a new session?", and the sentence "A new
   Terminal window on the Mac runs Claude with Remote Control in ``<project>``'s
   main folder."
2. **Project**: a picker of the registered projects, the snapshot's list.
3. **Name**: the label, a text field holding the time label ("1026-0049")
   until edited, and under it the full name in 13 px monospace, "Remote
   Control name: invariant-1026-0049".
4. **First message (optional)**: a three-line text area, and the note
   "Claude starts with this message. Leave it empty to start without one."
   Then **Model (optional)** (ADR-030 D6): a text field like the label's,
   placeholder "Leave it empty for the default.", and under it "Runs on
   ``<model>``", the model the Mac's answer says the window runs on. A blank
   field is not sent, so the Mac answers with the configured model; the nonce
   binds the model shown.
5. **Checked just now**: an inset block, "N Claude windows already open here"
   (or "No Claude window is open here") and "Main folder has uncommitted
   changes (N files)" (or "has no uncommitted changes", or "Could not tell
   …"). Neither blocks.
6. A refusal, when the Mac refuses what the fields say: one red line, "Not
   run: ``<reason>``". The fields keep what was typed.
7. A closed toggle, "Exact command and folder": the Command block (the
   ``claude`` line with the first message quoted, Copy, "In ``<project>`` › main
   checkout") and the folder.
8. "Start session" (primary, 52 px) over Cancel, which holds focus, and the
   expiry line.

Fields are 16 px (so iOS does not zoom), at least 44 px tall and the sheet's
full width. Each edit disables "Start session" and asks the Mac again 400 ms
after the typing stops; the answer refills the name, the facts and the
command in place and re-arms the button with a new nonce. What is posted is
always what the sheet shows.

**The Starting… card.** After "Start session", the result sheet shows the
start like any action ("Running…", then "Opened in a new window" and "Pick
it up from Remote Control on your phone as ``<name>``."). On the overview the
start is a compact card at the top of **Working**: the project, the name, and
"Starting…". Tapping it opens the start's result. Once a scan sees a live
Claude window launched by that name, the card is gone and the window's own
row is drawn there instead, titled by the name; its detail says "Claude is
running in a new window on the Mac. Pick it up from Remote Control on your
phone as ``<name>``." A window not seen a minute after the start, or a launch
that failed, turns the card into a failure: a red "Did not start" chip and
the reason, and tapping it opens the failed result (screen 5) with that
reason under "Why".

8 · Chain (ADR-027 increment 3)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. ui:: Panel screen: Chain
   :id: UI_PANEL_CHAIN
   :uses: COMP_ORACLE_PANEL

   The control panel's screen showing one project's chain: what it is doing,
   the chained start it follows, the proposals and the queue, each with the
   actions it offers now. ``oracle/panel`` decides its words from
   ``oracle/chainview``'s read and ``oracle/webpage`` draws it.


Opened from a project's row under **Projects** (its text is the button; New
session… keeps its pill) or from a chain card's Details. Top to bottom, the
detail screen's spacing (18 px between blocks):

1. The freshness line, then the back link, "‹ Sessions", 44 px tall.
2. The kicker "Chain" (uppercase), the project's name as the 28 px title,
   and one status chip: "Running Q4" or "Starting Q4" (working tone),
   "Paused" or "Held" (needs), "Waiting" or "Idle" (idle), "Cannot be read"
   (problem).
3. **What is going on**: a card with one sentence for the status and, when
   the page may act, one button: "Resume chain…" (primary) while paused,
   else "Pause chain…" (secondary). The sentence for a pause says who paused
   it, never the raw reason, which is under Technical details.
4. **Now**, while the chain follows a start: a card with the label "Now",
   "Q4 · <ref>" as the step name (17 px semibold) and its line ("Running in a
   window on the Mac", "Opening its window on the Mac", or the stall:
   "Stalled at a usage limit · nudged 30 min ago"). With a recorded stall the
   buttons "Continue…" (primary), "Retry…" and "Close…" (secondary) follow,
   in a row that wraps.
   While the chain follows no start but the window of the last one that
   landed is still open (ruling 21), Now shows that window instead: "Q4 ·
   <ref>" and its line ("Landed · asked whether its window may close"), and,
   once its notice was sent, "Close…" (primary) and "Leave open…"
   (secondary).
5. **Proposed**: a list of 64 px rows, the proposal's ref (or a backlog
   item's title) over where it came from, each with a "Start…" pill.
6. **Queue**: a list row per queued entry, in order: "Q5 · <ref>" (16 px),
   a secondary line ("Offered, never started on its own · Triage of Q3 ·
   Added by you", or, for an entry the backlog mirror wrote, "Serves IDEA_60
   · Reproduction saw nothing (1 of 2 runs) · Added from the backlog"), the brief in at most two lines, then, right-aligned and
   wrapping under the text when the row is narrow, a 44 px icon button with
   a chevron up, one with a chevron down, and the "Remove…" pill. The first
   queued entry has no up and the last no down. An empty queue is one row,
   "Nothing is queued."
7. **Recent**: this device's chain actions in this project, "<deed>:
   <result>" with the local time, as a session's Recent.
8. **Technical details**, closed: the chained start's id, state, session
   and window name, the pause's who, when and reason, the last five done or
   failed entries with a failure's reason, and a ledger or queue error.

Every button opens the confirm sheet (screen 3), or the destructive one for
Retry and Close (screen 4), over the Chain screen; Cancel and Done return to
it. A refresh redraws it in place; when its project is gone from the read it
falls back to the overview.

**The cards under Needs you** are full cards (screen 1) with the kicker the
project's name and one needs chip: "Proposed" with "Start…"; "Still stalled"
or "Not nudged" with "Continue…", "Retry…" and "Close…" and Details in a
two-column grid; "Window open" with "Close…" and "Leave open…" (a landed
window the oracle could not settle, ruling 21); "Chain paused" with "Resume
chain…"; and one card for every
project, kicker "Every project", "Held" with "Go ahead…" while the weekly
reading is unknown, and no button when it is over the cap. A chain card's
Details opens its project's Chain screen; the usage card's opens a detail
view with the reading under Technical details.

Components
----------

+--------------------+-------------------------------------+-------------+
| Component          | Size and shape                      | Used on     |
+====================+=====================================+=============+
| Freshness line     | 13 px, 7 px dot (6 px gap)          | 1           |
+--------------------+-------------------------------------+-------------+
| Icon button        | 44 × 44, radius 22,                 | 1 (refresh) |
|                    | ``surface-button``                  |             |
+--------------------+-------------------------------------+-------------+
| Summary tile       | grid of 4, gap 8; padding 10 × 8,   | 1           |
|                    | radius 14, min height 44; count     |             |
|                    | 24/28 bold, label 12 semibold       |             |
+--------------------+-------------------------------------+-------------+
| Group heading      | 13 px semibold, uppercase, tracking | 1, 2, 6     |
|                    | 0.6 px, accent colour               |             |
+--------------------+-------------------------------------+-------------+
| Card               | ``surface-card``, radius 18,        | 1, 2        |
|                    | padding 14 × 16, inner gap 8        |             |
+--------------------+-------------------------------------+-------------+
| Quiet card         | ``surface-quiet``, 1 px ``border``, | 1, 2, 6     |
|                    | radius 18                           |             |
+--------------------+-------------------------------------+-------------+
| State chip         | 13 px semibold, padding 4 × 10,     | 1, 2        |
|                    | radius 10, tinted ground and accent |             |
|                    | text                                |             |
+--------------------+-------------------------------------+-------------+
| Primary button     | ``accent-primary``, white text,     | 1, 2, 3     |
|                    | radius 12, height 44 (card), 48     |             |
|                    | (detail); 52 and radius 14 in a     |             |
|                    | sheet; 16/17 px semibold            |             |
+--------------------+-------------------------------------+-------------+
| Secondary button   | ``surface-button`` (card) or        | all         |
|                    | ``surface-pill`` (sheet), same      |             |
|                    | sizes                               |             |
+--------------------+-------------------------------------+-------------+
| Destructive button | ``accent-destructive``, white text, | 4           |
|                    | 52 px, radius 14                    |             |
+--------------------+-------------------------------------+-------------+
| Pill button        | visible 36 px tall, padding 0 × 14, | 6           |
|                    | radius 10, 15 px semibold; hit area |             |
|                    | padded to 44 (ADR-009 D12)          |             |
+--------------------+-------------------------------------+-------------+
| List row           | min height 56 (actions) or 64       | 2, 6        |
|                    | (leftovers), padding 0 or 10 × 16,  |             |
|                    | 1 px ``border`` between rows        |             |
+--------------------+-------------------------------------+-------------+
| Disclosure         | ``<details>``, summary min height   | 2, 3, 4, 5  |
|                    | 44 to 52, 14 to 15 px semibold      |             |
|                    | ``text-tertiary``                   |             |
+--------------------+-------------------------------------+-------------+
| Bottom sheet       | ``surface-sheet``, top radius 24,   | 3, 4, 5     |
|                    | padding 10 × 16 × 34 plus the       |             |
|                    | bottom safe area, gap 16; grabber   |             |
|                    | 36 × 5, radius 3, ``grabber``       |             |
+--------------------+-------------------------------------+-------------+
| Inset block        | ``surface-inset`` (or               | 3, 4, 5     |
|                    | ``tint-problem`` for "This          |             |
|                    | deletes"), radius 14, padding 12 ×  |             |
|                    | 14                                  |             |
+--------------------+-------------------------------------+-------------+
| Monospace value    | ``ui-monospace, Menlo, monospace``, | 2, 3, 4, 5  |
|                    | 13 to 15 px, single-line scroller   |             |
|                    | or separator wrap (ADR-009 D12)     |             |
+--------------------+-------------------------------------+-------------+
| Back link          | 44 px tall, 17 px, ``link``,        | 2, 6, 8     |
|                    | chevron 20 px                       |             |
+--------------------+-------------------------------------+-------------+
| Entry controls     | the icon button (44 × 44, a 20 px   | 8           |
| (inc 3)            | chevron up or down) and the pill,   |             |
|                    | gap 8, right-aligned, wrapping      |             |
|                    | under the row's text                |             |
+--------------------+-------------------------------------+-------------+
| Card button grid   | the card's buttons in two columns,  | 1           |
| (inc 3)            | gap 8, for a card with more than    |             |
|                    | one action                          |             |
+--------------------+-------------------------------------+-------------+

Icons are inline SVG, 18 to 20 px, stroke 2 (2.2 for the back chevron, 2.4
for the result's cross), round caps and joins: refresh, chevron right,
chevron left, chevron down, chevron up (inc 3), copy, cross, tick.

Tokens
------

The values are the mockup's, exactly. The page declares them once, as CSS
custom properties.

Colour
~~~~~~

+------------------------+----------------------+----------------------+
| Token                  | Value                | Use                  |
+========================+======================+======================+
| ``bg``                 | ``#0B0C0E``          | page ground          |
+------------------------+----------------------+----------------------+
| ``bg-under-sheet``     | ``#050506``          | ground behind an     |
|                        |                      | open sheet           |
+------------------------+----------------------+----------------------+
| ``surface-card``       | ``#16181B``          | cards, list groups   |
+------------------------+----------------------+----------------------+
| ``surface-quiet``      | ``#121417``          | the leftovers card,  |
|                        |                      | Technical details,   |
|                        |                      | the note card        |
+------------------------+----------------------+----------------------+
| ``surface-sheet``      | ``#17191C``          | bottom sheets        |
+------------------------+----------------------+----------------------+
| ``surface-inset``      | ``#0F1113``          | command block, Why,  |
|                        |                      | full output, Checked |
|                        |                      | just now             |
+------------------------+----------------------+----------------------+
| ``surface-button``     | ``#1F2226``          | icon button,         |
|                        |                      | Details, Copy        |
|                        |                      | (detail)             |
+------------------------+----------------------+----------------------+
| ``surface-pill``       | ``#23262A``          | Cancel, Done, Copy   |
|                        |                      | (sheet), grey pills, |
|                        |                      | Paused chip ground   |
+------------------------+----------------------+----------------------+
| ``tile-idle``          | ``#1A1C1F``          | Leftovers tile       |
|                        |                      | ground               |
+------------------------+----------------------+----------------------+
| ``border``             | ``#24272B``          | row separators,      |
|                        |                      | quiet-card border    |
+------------------------+----------------------+----------------------+
| ``grabber``            | ``#3A3E44``          | sheet grabber        |
+------------------------+----------------------+----------------------+
| ``text``               | ``#F2F3F5``          | primary text         |
+------------------------+----------------------+----------------------+
| ``text-secondary``     | ``#A4AAB0``          | meta, notes, labels  |
+------------------------+----------------------+----------------------+
| ``text-tertiary``      | ``#C4C9CE``          | sheet sentences,     |
|                        |                      | grey chip text,      |
|                        |                      | disclosure summaries |
+------------------------+----------------------+----------------------+
| ``chevron``            | ``#8A9097``          | list chevrons        |
+------------------------+----------------------+----------------------+
| ``link``               | ``#8FC1FF``          | back link, Show N    |
|                        |                      | more (hover/pressed  |
|                        |                      | ``#B9D8FF``)         |
+------------------------+----------------------+----------------------+
| ``live``               | ``#4ADE80``          | the freshness dot    |
|                        |                      | when fresh           |
+------------------------+----------------------+----------------------+
| ``accent-needs``       | ``#FFC266`` on       | Need you             |
|                        | ``tile-needs``       |                      |
|                        | ``#2B2114``, chip    |                      |
|                        | ``#3A2A12``          |                      |
+------------------------+----------------------+----------------------+
| ``accent-problem``     | ``#FF8F85`` on       | Problems,            |
|                        | ``tile-problem``     | destructive text,    |
|                        | ``#2A1716``, chip    | This deletes ground  |
|                        | ``#3A1A18``          |                      |
+------------------------+----------------------+----------------------+
| ``accent-working``     | ``#8FC1FF`` on       | Working              |
|                        | ``tile-working``     |                      |
|                        | ``#14202D``, chip    |                      |
|                        | ``#142436``          |                      |
+------------------------+----------------------+----------------------+
| ``accent-idle``        | ``#C4C9CE`` on       | Idle, Leftovers      |
|                        | ``tile-idle``        |                      |
|                        | ``#1A1C1F``, chip    |                      |
|                        | ``#23262A``          |                      |
+------------------------+----------------------+----------------------+
| ``accent-primary``     | ``#2563EB``          | primary buttons      |
+------------------------+----------------------+----------------------+
| ``accent-destructive`` | ``#B91C1C``          | the destructive      |
|                        |                      | button               |
+------------------------+----------------------+----------------------+

The stale freshness line and its dot use ``accent-problem``. The success tick
uses ``live`` on a ``#13261A`` ground; the mockup draws no success sheet, so
this one pairing is derived.

Type
~~~~

System fonts only: ``-apple-system, system-ui, "Segoe UI", sans-serif``;
monospace ``ui-monospace, Menlo, monospace``.

+------------------+------------------+-----------+------------------+
| Role             | Size / line      | Weight    | Notes            |
|                  | height           |           |                  |
+==================+==================+===========+==================+
| Overview title   | 32 / 38          | 700       | tracking −0.4 px |
+------------------+------------------+-----------+------------------+
| Detail and       | 28 / 34          | 700       | tracking −0.3 px |
| leftovers title  |                  |           |                  |
+------------------+------------------+-----------+------------------+
| Sheet title      | 22 / 28          | 700       |                  |
+------------------+------------------+-----------+------------------+
| Result headline  | 20 / 26          | 700       |                  |
+------------------+------------------+-----------+------------------+
| Tile count       | 24 / 28          | 700       |                  |
+------------------+------------------+-----------+------------------+
| Card title, step | 17 / 22          | 600       |                  |
| name             |                  |           |                  |
+------------------+------------------+-----------+------------------+
| Back link, sheet | 17               | 400 / 600 |                  |
| buttons          |                  |           |                  |
+------------------+------------------+-----------+------------------+
| List row title,  | 16               | 400 / 600 |                  |
| card buttons     |                  |           |                  |
+------------------+------------------+-----------+------------------+
| Body sentence    | 15 / 22          | 400       |                  |
+------------------+------------------+-----------+------------------+
| Note, secondary  | 14 / 20          | 400       |                  |
| line             |                  |           |                  |
+------------------+------------------+-----------+------------------+
| Chip, freshness, | 13               | 600 / 400 |                  |
| meta value       |                  |           |                  |
+------------------+------------------+-----------+------------------+
| Labels           | 12 to 13         | 600       | tracking 0.4 to  |
| (uppercase)      |                  |           | 0.6 px           |
+------------------+------------------+-----------+------------------+
| Full output      | 12 / 18          | 400       | monospace        |
+------------------+------------------+-----------+------------------+

Space, radius, size
~~~~~~~~~~~~~~~~~~~

- Side margin 16. Gaps: 20 between overview blocks, 18 on detail and
  leftovers, 16 inside a sheet, 10 between cards of a group, 8 inside a
  card.
- Radii: 28 phone frame (mockup only), 24 sheet top, 18 card, 14 tile,
  inset block and sheet button, 12 card button, 10 chip and pill, 8 the
  small Copy.
- Safe areas: the page pads by ``env(safe-area-inset-*)`` (top 58 and bottom
  34 in the mockup's 390 × 844 frame, which are the inset plus the margin).
  A sheet adds the bottom inset to its own 34 px.
- Minimum hit area 44 × 44 for every control (ADR-009 D12).

Wording
-------

One table in ``oracle/panel``, and the only place these strings are spelled
(ADR-009 D3). Buttons are sentence case. A button that opens a confirm ends
with "…". A button that acts is named for the deed.

Groups and tiles
~~~~~~~~~~~~~~~~

+-----------+-----------+------------+---------------------------------+
| Group     | Heading   | Tile label | Who is in it                    |
+===========+===========+============+=================================+
| needs     | Needs you | Need you   | ``waiting`` (permission,        |
|           |           |            | question, input); ``finished``  |
|           |           |            | (reviewed, ready);              |
|           |           |            | ``docs_granted`` (ADR-014 D5,   |
|           |           |            | increment 4b)                   |
+-----------+-----------+------------+---------------------------------+
| problems  | Problems  | Problems   | the oracle itself when it is    |
|           |           |            | older than the installed        |
|           |           |            | invariant (one card, first,     |
|           |           |            | BUG_089); ``error`` (tests,     |
|           |           |            | degraded, scan);                |
|           |           |            | ``gate_blocked``; ``stuck``;    |
|           |           |            | ``host_gone``; ``stranded``     |
+-----------+-----------+------------+---------------------------------+
| working   | Working   | Working    | the row's ``turn_state`` is     |
|           |           |            | ``busy`` (the current host is   |
|           |           |            | mid-turn and working,           |
|           |           |            | ``signals.turn_state``,         |
|           |           |            | BUG_087) or ``background`` (its |
|           |           |            | turn ended while work it        |
|           |           |            | started in the background runs  |
|           |           |            | on, BUG_094), nothing above     |
+-----------+-----------+------------+---------------------------------+
| idle      | Idle      | (no tile)  | every other live session:       |
|           |           |            | ``your_turn``, ``parked``,      |
|           |           |            | ``teardown_pending``, no signal |
+-----------+-----------+------------+---------------------------------+
| leftovers | Leftovers | Leftovers  | ``orphan_host`` and             |
|           |           |            | ``leaked_worktree`` rows with   |
|           |           |            | no alert                        |
+-----------+-----------+------------+---------------------------------+

States (chips and sentences)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

+----------------------+-------------------+---------+-------------------+
| Fact                 | Chip              | Tone    | Sentence for      |
|                      |                   |         | "What is going    |
|                      |                   |         | on"               |
+======================+===================+=========+===================+
| ``waiting``,         | Waiting for your  | needs   | Claude asked for  |
| permission           | permission        |         | permission N min  |
|                      |                   |         | ago. Answer it in |
|                      |                   |         | Claude on your    |
|                      |                   |         | Mac or phone.     |
+----------------------+-------------------+---------+-------------------+
| ``waiting``,         | Waiting for your  | needs   | Claude asked you  |
| question             | answer            |         | a question N min  |
|                      |                   |         | ago. Answer it    |
|                      |                   |         | here or on the    |
|                      |                   |         | Mac.              |
+----------------------+-------------------+---------+-------------------+
| ``waiting``, input   | Waiting for you   | needs   | Claude is waiting |
| (only Claude Code's  |                   |         | for your next     |
| own status says it   |                   |         | message.          |
| waits, BUG_087)      |                   |         |                   |
+----------------------+-------------------+---------+-------------------+
| ``finished``,        | Finished, ready   | needs   | The review checks |
| reviewed             | to merge          |         | passed. It can be |
|                      |                   |         | merged.           |
+----------------------+-------------------+---------+-------------------+
| ``finished``, ready  | Finished, ready   | needs   | Its plan is       |
|                      | for review        |         | complete and its  |
|                      |                   |         | lane has landed.  |
|                      |                   |         | It is ready for   |
|                      |                   |         | the review        |
|                      |                   |         | checks.           |
+----------------------+-------------------+---------+-------------------+
| ``docs_granted``     | Its docs are      | needs   | The docs it was   |
| (ADR-014 D5,         | free, go on       |         | waiting for are   |
| increment 4b)        |                   |         | now its own. Tell |
|                      |                   |         | Claude to go on:  |
|                      |                   |         | it reads them     |
|                      |                   |         | again before it   |
|                      |                   |         | designs.          |
+----------------------+-------------------+---------+-------------------+
| ``docs_queued``      | Waiting for the   | idle    | Another session   |
| (a marker)           | docs              |         | is working on the |
|                      |                   |         | docs it needs. It |
|                      |                   |         | goes next when    |
|                      |                   |         | they finish.      |
+----------------------+-------------------+---------+-------------------+
| ``error``, tests     | N tests failing   | problem | The last test run |
|                      | (else Tests       |         | had N failures.   |
|                      | failing)          |         |                   |
+----------------------+-------------------+---------+-------------------+
| ``gate_blocked``     | Blocked by a      | problem | A check refused   |
|                      | check             |         | the last step:    |
|                      |                   |         | <rule, in plain   |
|                      |                   |         | words where the   |
|                      |                   |         | table knows it>.  |
+----------------------+-------------------+---------+-------------------+
| ``stuck``            | No progress for N | problem | Claude has been   |
|                      | min               |         | working for N min |
|                      |                   |         | with no sign of   |
|                      |                   |         | progress.         |
+----------------------+-------------------+---------+-------------------+
| ``stuck``,           | No progress for N | problem | Its background    |
| background (BUG_094) | min               |         | work has shown no |
|                      |                   |         | sign of progress  |
|                      |                   |         | for N min.        |
+----------------------+-------------------+---------+-------------------+
| ``error``, degraded  | A hook failed     | problem | One of            |
|                      |                   |         | invariant's hooks |
|                      |                   |         | failed in this    |
|                      |                   |         | session.          |
+----------------------+-------------------+---------+-------------------+
| ``error``, scan      | Cannot read this  | problem | invariant cannot  |
|                      | project           |         | read this         |
|                      |                   |         | project's         |
|                      |                   |         | settings.         |
+----------------------+-------------------+---------+-------------------+
| the oracle older     | Restarting        | problem | invariant was     |
| than the install,    |                   |         | updated on the    |
| ``changed``          |                   |         | Mac after the     |
| (BUG_089)            |                   |         | oracle started.   |
|                      |                   |         | The oracle        |
|                      |                   |         | restarts itself   |
|                      |                   |         | to catch up,      |
|                      |                   |         | within a minute.  |
|                      |                   |         | Then: Until then  |
|                      |                   |         | it cannot read N  |
|                      |                   |         | projects:         |
|                      |                   |         | ``<names>``. (1   |
|                      |                   |         | project)          |
+----------------------+-------------------+---------+-------------------+
| the oracle older     | Restart needed    | problem | The oracle on the |
| than the install,    |                   |         | Mac is older than |
| ``rules`` only       |                   |         | the invariant     |
|                      |                   |         | installed there.  |
|                      |                   |         | Restarting it     |
|                      |                   |         | fixes this;       |
|                      |                   |         | Technical details |
|                      |                   |         | has the command.  |
|                      |                   |         | Then the same     |
|                      |                   |         | "Until then"      |
|                      |                   |         | sentence          |
+----------------------+-------------------+---------+-------------------+
| ``host_gone``        | Paused            | idle    | Its Claude window |
|                      |                   |         | closed without    |
|                      |                   |         | saying so, N ago, |
|                      |                   |         | so nothing is     |
|                      |                   |         | happening until   |
|                      |                   |         | you resume it.    |
+----------------------+-------------------+---------+-------------------+
| ``stranded``         | Its folder is     | problem | The session's     |
|                      | gone              |         | folder no longer  |
|                      |                   |         | exists.           |
+----------------------+-------------------+---------+-------------------+
| ``turn_state``       | Claude is working | working | Claude is working |
| ``busy``             |                   |         | on it; active N   |
|                      |                   |         | min ago.          |
+----------------------+-------------------+---------+-------------------+
| ``turn_state``       | Waiting on N      | working | Claude finished   |
| ``background``       | agent(s), Waiting |         | its turn and is   |
| (BUG_094)            | on N background   |         | waiting on its    |
|                      | command(s), or    |         | background work;  |
|                      | both joined by    |         | active N min ago. |
|                      | "and" ("Waiting   |         |                   |
|                      | on 1 agent and 2  |         |                   |
|                      | background        |         |                   |
|                      | commands");       |         |                   |
|                      | Waiting on        |         |                   |
|                      | background work   |         |                   |
|                      | when the row      |         |                   |
|                      | carries no count  |         |                   |
+----------------------+-------------------+---------+-------------------+
| ``your_turn``        | Waiting for your  | idle    | Claude finished   |
|                      | next message      |         | its turn and is   |
|                      |                   |         | waiting for you.  |
+----------------------+-------------------+---------+-------------------+
| ``parked``           | Paused            | idle    | No Claude window  |
|                      |                   |         | is working on it. |
|                      |                   |         | Resume it to go   |
|                      |                   |         | on.               |
+----------------------+-------------------+---------+-------------------+
| ``teardown_pending`` | Merged, ready to  | idle    | It is merged. Its |
|                      | clean up          |         | folder and branch |
|                      |                   |         | can be removed.   |
+----------------------+-------------------+---------+-------------------+
| ``inherited_env``    | Terminal.app      | idle    | This Claude       |
| (IDEA_035)           | needs a restart   |         | inherited         |
|                      |                   |         | settings from its |
|                      |                   |         | Terminal.app that |
|                      |                   |         | are meant for a   |
|                      |                   |         | Claude window's   |
|                      |                   |         | own tools, so it  |
|                      |                   |         | may draw white    |
|                      |                   |         | blocks and keep   |
|                      |                   |         | no transcript.    |
|                      |                   |         | Quit Terminal.app |
|                      |                   |         | fully and         |
|                      |                   |         | relaunch it from  |
|                      |                   |         | the Dock.         |
+----------------------+-------------------+---------+-------------------+
| unknown              | (none)            | —       | (none)            |
+----------------------+-------------------+---------+-------------------+

**As built with ADR-029 increment 5** (:need:`DEC_ADR_029_D45`). "This panel
never answers prompts." is gone from every waiting sentence: the panel answers
a question and replies to a window waiting for input. The permission
sentence keeps "Answer it in Claude on your Mac or phone." until increment 6
answers permission prompts and words it "Answer it here or on the Mac.".

Lanes and phases
~~~~~~~~~~~~~~~~

+----------------+---------+-------------------------------------------+
| Methodology    | Lane    | Phases                                    |
+================+=========+===========================================+
| ``tdd``        | feature | designing: Designing; red: Writing tests; |
|                |         | green: Making tests pass; refactor:       |
|                |         | Tidying up; reviewing: In review          |
+----------------+---------+-------------------------------------------+
| ``bugfix``     | bug fix | triaging: Looking at the bug;             |
|                |         | reproducing: Reproducing the bug;         |
|                |         | reproduced: Bug reproduced;               |
|                |         | not_reproducible: Could not reproduce;    |
|                |         | fixing: Fixing a bug; verifying: Checking |
|                |         | the fix                                   |
+----------------+---------+-------------------------------------------+
| ``chore``      | chore   | doing: Working; reviewing: In review      |
+----------------+---------+-------------------------------------------+
| ``spec-first`` | feature | designing: Designing; specified: Spec     |
|                |         | written; implementing: Building;          |
|                |         | reviewing: In review                      |
+----------------+---------+-------------------------------------------+
| ``docs``       | docs    | drafting: Writing docs; reviewing: In     |
|                |         | review                                    |
+----------------+---------+-------------------------------------------+
| ``docs-``      | docs    | drafting: Writing docs; reviewing: In     |
| ``project``    |         | review                                    |
+----------------+---------+-------------------------------------------+
| ``none``       | (none)  | working: Working                          |
+----------------+---------+-------------------------------------------+
| any other      | (none)  | (none)                                    |
+----------------+---------+-------------------------------------------+

A terminal or abandoned phase is never shown on a live card. A machine a
project defines itself gets no phrase, unless its state name is one of
these (ADR-009 D3).

Steps
~~~~~

+----------------------------------------------------+---------------+-------------+-----------------+-------------+
| Recommended command                                | Card button   | Confirm     | Confirm         | Run button  |
|                                                    |               | title       | sentence        |             |
+====================================================+===============+=============+=================+=============+
| ``pipeline test --id … --selector …`` (the         | Re-run tests… | Re-run the  | Runs the tests  | Run tests   |
| session's last scoped run, BUG_093)                |               | tests?      | this session    |             |
|                                                    |               |             | last ran in     |             |
|                                                    |               |             | ``<project>``,  |             |
|                                                    |               |             | in its own      |             |
|                                                    |               |             | folder and with |             |
|                                                    |               |             | its own         |             |
|                                                    |               |             | settings, and   |             |
|                                                    |               |             | records the     |             |
|                                                    |               |             | result. Nothing |             |
|                                                    |               |             | is changed or   |             |
|                                                    |               |             | deleted.        |             |
+----------------------------------------------------+---------------+-------------+-----------------+-------------+
| ``pipeline gates --id``                            | Show          | Show what   | Lists the       | Show        |
|                                                    | blockers…     | is blocking | checks this     | blockers    |
|                                                    |               | it?         | session still   |             |
|                                                    |               |             | owes and what   |             |
|                                                    |               |             | clears each.    |             |
|                                                    |               |             | Nothing is      |             |
|                                                    |               |             | changed.        |             |
+----------------------------------------------------+---------------+-------------+-----------------+-------------+
| ``context --id``                                   | Show its      | Show what   | Prints the      | Show        |
|                                                    | instructions… | the session | session's       |             |
|                                                    |               | is being    | current         |             |
|                                                    |               | told?       | instructions.   |             |
|                                                    |               |             | Nothing is      |             |
|                                                    |               |             | changed.        |             |
+----------------------------------------------------+---------------+-------------+-----------------+-------------+
| ``session review --id``                            | Review…       | Run the     | Runs the review | Run review  |
|                                                    |               | review      | checks and      |             |
|                                                    |               | checks?     | marks the       |             |
|                                                    |               |             | session         |             |
|                                                    |               |             | reviewed if     |             |
|                                                    |               |             | they pass.      |             |
|                                                    |               |             | Nothing is      |             |
|                                                    |               |             | merged.         |             |
+----------------------------------------------------+---------------+-------------+-----------------+-------------+
| ``session merge --id``                             | Merge…        | Merge this  | Merges the      | Merge       |
|                                                    |               | session?    | session's       |             |
|                                                    |               |             | branch from the |             |
|                                                    |               |             | main checkout.  |             |
|                                                    |               |             | Its folder and  |             |
|                                                    |               |             | branch stay     |             |
|                                                    |               |             | until clean-up. |             |
+----------------------------------------------------+---------------+-------------+-----------------+-------------+
| ``session teardown --id``                          | Clean up…     | Remove the  | Removes the     | Remove      |
|                                                    |               | folder and  | merged          | folder and  |
|                                                    |               | branch?     | session's       | branch      |
|                                                    |               |             | folder and      |             |
|                                                    |               |             | branch.         |             |
|                                                    |               |             | (Destructive    |             |
|                                                    |               |             | sheet: lists    |             |
|                                                    |               |             | the folder and  |             |
|                                                    |               |             | the branch.)    |             |
+----------------------------------------------------+---------------+-------------+-----------------+-------------+
| ``session reap --dry-run``                         | Check         | See what    | Lists the       | Show        |
|                                                    | clean-up…     | clean-up    | sessions whose  |             |
|                                                    |               | would       | folder and      |             |
|                                                    |               | abandon?    | branch are both |             |
|                                                    |               |             | gone. Nothing   |             |
|                                                    |               |             | is changed.     |             |
+----------------------------------------------------+---------------+-------------+-----------------+-------------+
| ``session resume --id`` (terminal)                 | Resume…       | Resume in a | Opens Terminal  | Open window |
|                                                    |               | new Claude  | on your Mac and |             |
|                                                    |               | window?     | starts Claude   |             |
|                                                    |               |             | in this         |             |
|                                                    |               |             | session's       |             |
|                                                    |               |             | folder. You can |             |
|                                                    |               |             | pick it up from |             |
|                                                    |               |             | Remote Control  |             |
|                                                    |               |             | on your phone.  |             |
+----------------------------------------------------+---------------+-------------+-----------------+-------------+
| ``session resume --id --force`` (terminal)         | Resume…       | Take over   | Its last Claude | Open window |
|                                                    |               | in a new    | window closed   |             |
|                                                    |               | Claude      | without saying  |             |
|                                                    |               | window?     | so. Opens       |             |
|                                                    |               |             | Terminal on     |             |
|                                                    |               |             | your Mac and    |             |
|                                                    |               |             | starts Claude   |             |
|                                                    |               |             | there to take   |             |
|                                                    |               |             | over.           |             |
+----------------------------------------------------+---------------+-------------+-----------------+-------------+
| ``session adopt <host> --id``                      | Attach…       | Attach this | Binds the open  | Attach      |
|                                                    |               | window to   | Claude window   |             |
|                                                    |               | its         | to the one      |             |
|                                                    |               | session?    | session here    |             |
|                                                    |               |             | that has none.  |             |
+----------------------------------------------------+---------------+-------------+-----------------+-------------+
| ``oracle close-host <host> --pid --start [--tty]`` | Close…        | Close this  | Ends Claude in  | Close       |
|                                                    |               | Claude      | this window,    | window      |
|                                                    |               | window?     | then closes the |             |
|                                                    |               |             | Terminal tab.   |             |
|                                                    |               |             | You can reopen  |             |
|                                                    |               |             | the             |             |
|                                                    |               |             | conversation    |             |
|                                                    |               |             | later with      |             |
|                                                    |               |             | claude          |             |
|                                                    |               |             | --resume.       |             |
|                                                    |               |             | (Destructive    |             |
|                                                    |               |             | sheet: "This    |             |
|                                                    |               |             | ends" lists the |             |
|                                                    |               |             | window and its  |             |
|                                                    |               |             | process.)       |             |
+----------------------------------------------------+---------------+-------------+-----------------+-------------+
| ``doctor``                                         | Check setup…  | Run         | Checks this     | Run check   |
|                                                    |               | invariant's | project's hooks |             |
|                                                    |               | setup       | and settings.   |             |
|                                                    |               | check?      | Nothing is      |             |
|                                                    |               |             | changed.        |             |
+----------------------------------------------------+---------------+-------------+-----------------+-------------+
| a start (``oracle start-session``, IDEA_030)       | New session…  | Start a new | A new Terminal  | Start       |
|                                                    |               | session?    | window on the   | session     |
|                                                    |               |             | Mac runs Claude |             |
|                                                    |               |             | with Remote     |             |
|                                                    |               |             | Control in      |             |
|                                                    |               |             | ``<project>``'s |             |
|                                                    |               |             | main folder.    |             |
+----------------------------------------------------+---------------+-------------+-----------------+-------------+
| ``info`` step, or waiting                          | (no button)   | —           | —               | —           |
+----------------------------------------------------+---------------+-------------+-----------------+-------------+
| failing tests with nothing to replay (``info``,    | (no button;   | —           | —               | —           |
| ``reason`` ``rerun_unscoped`` or                   | the detail's  |             |                 |             |
| ``rerun_unrecorded``, BUG_093)                     | "What is      |             |                 |             |
|                                                    | going on"     |             |                 |             |
|                                                    | says why)     |             |                 |             |
+----------------------------------------------------+---------------+-------------+-----------------+-------------+

Increment 6 added close host. Increment 7 adds rows here for kill a leaked
process, abandon a session, remove an orphaned worktree, and nudge or
re-dispatch a stuck agent, each with its "This deletes" lines.

The chain's actions (ADR-027 increment 3, ``panel._CHAIN_STEPS``). A title
or sentence names the entry, its ref, the neighbour a move passes and the
project:

+-------------------+---------------+------------------+-----------------------------+--------------+
| Command           | Button        | Confirm title    | Confirm sentence            | Run button   |
+===================+===============+==================+=============================+==============+
| ``queue move Qn   | (up chevron,  | Move Q5 up?      | Puts Q5, <ref>, before Q4,  | Move up      |
| --to N            | "Move Q5      |                  | <ref>, in <project>'s       |              |
| --if-queued-at    | up…")         |                  | queue. Nothing starts and   |              |
| P``, N before P   |               |                  | nothing else moves.         |              |
+-------------------+---------------+------------------+-----------------------------+--------------+
| the same, N after | (down         | Move Q5 down?    | Puts Q5, <ref>, after Q6,   | Move down    |
| P                 | chevron)      |                  | <ref>, …                    |              |
+-------------------+---------------+------------------+-----------------------------+--------------+
| ``queue remove Qn | Remove…       | Take Q5 off the  | Removes Q5, <ref>, from     | Remove from  |
| --if-queued-at    |               | queue?           | <project>'s queue, so the   | queue        |
| P``               |               |                  | chain never starts it. The  |              |
|                   |               |                  | work item itself is not     |              |
|                   |               |                  | touched; queue it again to  |              |
|                   |               |                  | bring it back.              |              |
+-------------------+---------------+------------------+-----------------------------+--------------+
| ``oracle chain    | Start…        | Start Q5 now?    | A new Terminal window on    | Start        |
| start <key>       |               | (or the item's   | the Mac runs Claude with    | session      |
| --entry Qn`` or   |               | id)              | Remote Control in           |              |
| ``--item <id>``   |               |                  | <project>'s main folder, on |              |
|                   |               |                  | <ref or title>. The chain   |              |
|                   |               |                  | follows it as its next      |              |
|                   |               |                  | session.                    |              |
+-------------------+---------------+------------------+-----------------------------+--------------+
| ``oracle chain    | Pause chain…  | Pause the chain  | Nothing more starts on its  | Pause chain  |
| pause <key>``     |               | in <project>?    | own in <project> until you  |              |
|                   |               |                  | resume it. A session        |              |
|                   |               |                  | already running goes on.    |              |
+-------------------+---------------+------------------+-----------------------------+--------------+
| ``oracle chain    | Resume chain… | Resume the chain | The chain starts queued     | Resume chain |
| resume <key>``    |               | in <project>?    | work in <project> again.    |              |
|                   |               |                  | Nothing that failed is      |              |
|                   |               |                  | retried.                    |              |
+-------------------+---------------+------------------+-----------------------------+--------------+
| ``oracle chain    | Go ahead…     | Let the chain go | The plan's weekly usage     | Go ahead     |
| greenlight``      |               | ahead?           | cannot be read, so nothing  |              |
|                   |               |                  | starts on its own. Go ahead |              |
|                   |               |                  | lets every project's chain  |              |
|                   |               |                  | start queued work until a   |              |
|                   |               |                  | fresh reading arrives,      |              |
|                   |               |                  | which judges again.         |              |
+-------------------+---------------+------------------+-----------------------------+--------------+
| ``oracle chain    | Continue…     | Type continue    | Types one more "continue"   | Type         |
| continue <key>``  |               | again?           | into the Claude window      | continue     |
|                   |               |                  | working on <ref> and        |              |
|                   |               |                  | watches it again from now.  |              |
|                   |               |                  | Nothing is closed.          |              |
+-------------------+---------------+------------------+-----------------------------+--------------+
| ``oracle chain    | Retry…        | Close the window | Ends Claude in the stalled  | Close and    |
| retry <key>``     |               | and start Q5     | window and closes its       | retry        |
|                   |               | afresh?          | Terminal tab, then puts Q5, |              |
|                   |               |                  | <ref>, back at the head of  |              |
|                   |               |                  | <project>'s queue to start  |              |
|                   |               |                  | in a new window. The        |              |
|                   |               |                  | session stays parked for    |              |
|                   |               |                  | you to land or abandon.     |              |
|                   |               |                  | (Destructive sheet: "This   |              |
|                   |               |                  | ends" names the window and  |              |
|                   |               |                  | its process.)               |              |
+-------------------+---------------+------------------+-----------------------------+--------------+
| ``oracle chain    | Close…        | Close the        | Ends Claude in the window   | Close and    |
| close <key>``     |               | stalled window?  | working on <ref> and closes | mark failed  |
|                   |               |                  | its Terminal tab, then      |              |
|                   |               |                  | marks Q5 failed, closed by  |              |
|                   |               |                  | you. Nothing is retried;    |              |
|                   |               |                  | the chain goes on to the    |              |
|                   |               |                  | next queued work. The       |              |
|                   |               |                  | session stays parked for    |              |
|                   |               |                  | you to land or abandon.     |              |
|                   |               |                  | (Destructive sheet.)        |              |
+-------------------+---------------+------------------+-----------------------------+--------------+
| ``oracle chain    | Close…        | Close Q5's       | Ends Claude in the window   | Close window |
| close-window      |               | window?          | where <ref> landed and      |              |
| <key>``           |               |                  | closes its Terminal tab.    |              |
| (ruling 21)       |               |                  | Its work is merged; the     |              |
|                   |               |                  | chain in <project> goes on  |              |
|                   |               |                  | to the next queued work.    |              |
|                   |               |                  | (Destructive sheet: "This   |              |
|                   |               |                  | ends" names the window and  |              |
|                   |               |                  | its process.)               |              |
+-------------------+---------------+------------------+-----------------------------+--------------+
| ``oracle chain    | Leave open…   | Leave Q5's       | Keeps the window where      | Leave open   |
| leave-open        |               | window open?     | <ref> landed open. The      |              |
| <key>``           |               |                  | oracle does not ask it      |              |
| (ruling 21)       |               |                  | again, and the chain in     |              |
|                   |               |                  | <project> waits until you   |              |
|                   |               |                  | close the window yourself.  |              |
+-------------------+---------------+------------------+-----------------------------+--------------+

Fixed strings
~~~~~~~~~~~~~

+----------------------------------+-----------------------------------+
| Where                            | Text                              |
+==================================+===================================+
| Overview title                   | Sessions                          |
+----------------------------------+-----------------------------------+
| Freshness, fresh / stale         | Live · updated N s ago / Stale ·  |
|                                  | updated N s ago                   |
+----------------------------------+-----------------------------------+
| Leftovers card                   | N leftovers to tidy; "N Claude    |
|                                  | windows with no session, N        |
|                                  | left-over folders"                |
+----------------------------------+-----------------------------------+
| Waiting note                     | A question: its first line, or    |
|                                  | "Can't read this prompt here —    |
|                                  | use the Mac."; input: Claude is   |
|                                  | waiting for your next message.; a |
|                                  | permission prompt, or a question  |
|                                  | not read: Answer it in Claude on  |
|                                  | your Mac or phone. (ADR-029       |
|                                  | increment 5)                      |
+----------------------------------+-----------------------------------+
| Detail sections                  | What is going on · Suggested next |
|                                  | step · More actions · Recent ·    |
|                                  | Technical details                 |
+----------------------------------+-----------------------------------+
| Confirm                          | Command · Copy · Exact folder and |
|                                  | environment · Runs as you on the  |
|                                  | Mac, without the marks of any     |
|                                  | Claude window. · Cancel · This    |
|                                  | confirm expires in M:SS · This    |
|                                  | confirm expired. Look again       |
+----------------------------------+-----------------------------------+
| A masked variable (BUG_093)      | After its ``NAME=value``: "       |
|                                  | (masked; the session's recorded   |
|                                  | value is used when it runs)".     |
|                                  | Copy and the Command line: "# Not |
|                                  | runnable as shown, ``<names>``    |
|                                  | masked: " before the line         |
+----------------------------------+-----------------------------------+
| No re-run offered (BUG_093)      | no scoped run: The phone does not |
|                                  | re-run these tests: no scoped     |
|                                  | test run is on record for this    |
|                                  | session, and the panel never runs |
|                                  | a whole test suite. Run its tests |
|                                  | from the session's own window. ·  |
|                                  | a run older than the record: The  |
|                                  | phone does not re-run these tests |
|                                  | yet: the last scoped run was      |
|                                  | recorded before invariant kept    |
|                                  | the settings it found, so it      |
|                                  | cannot be repeated the same way.  |
|                                  | Run its tests once from the       |
|                                  | session's own window.             |
+----------------------------------+-----------------------------------+
| Destructive confirm              | This deletes · Checked just now · |
|                                  | No Claude window is using this    |
|                                  | session · Checked again the       |
|                                  | moment you press ``<deed>`` ·     |
|                                  | Exact command · This cannot be    |
|                                  | undone.                           |
+----------------------------------+-----------------------------------+
| Close host confirm (inc 6)       | This ends · Claude in             |
|                                  | ``<project>`` · window <first 8>  |
|                                  | · Process ``<pid>`` · idle        |
|                                  | ``<age>`` · Checked just now · No |
|                                  | invariant session is using this   |
|                                  | Claude window · Checked again the |
|                                  | moment you press Close window     |
+----------------------------------+-----------------------------------+
| Result                           | Why · What happened · Show full   |
|                                  | output · Done · stopped after N s |
|                                  | · Done in N s                     |
+----------------------------------+-----------------------------------+
| Failure fallback                 | Failed, exit N.                   |
|                                  | ``<last meaningful line>``        |
+----------------------------------+-----------------------------------+
| Leftovers                        | Things that outlived their        |
|                                  | session. None of them needs you;  |
|                                  | tidy them when you like. · Claude |
|                                  | windows with no session ·         |
|                                  | Left-over folders · Show N more · |
|                                  | Close… · Remove…                  |
+----------------------------------+-----------------------------------+
| Not closeable (BUG_099)          | ``<state>`` · window <first 8> ·  |
|                                  | cannot close from here:           |
|                                  | ``<why>``, the why as in "A       |
|                                  | window that fails a close guard"  |
+----------------------------------+-----------------------------------+
| Leftover classes (inc 6)         | leftover host: Still open on the  |
|                                  | Mac · unmanaged: Open, never had  |
|                                  | a session · recently active       |
|                                  | (leftover or unmanaged): Active,  |
|                                  | no session · gone: Already closed |
|                                  | · record only · unknown: Process  |
|                                  | id not recorded (no pid), Still   |
|                                  | open on the Mac (alive, no        |
|                                  | activity known) · process ids     |
|                                  | disagree: Its process ids         |
|                                  | disagree                          |
+----------------------------------+-----------------------------------+
| New session (IDEA_030)           | New session… · Projects · Project |
|                                  | · Name · Remote Control name:     |
|                                  | ``<name>`` · First message        |
|                                  | (optional) · Claude starts with   |
|                                  | this message. Leave it empty to   |
|                                  | start without one. · Exact        |
|                                  | command and folder · Start        |
|                                  | session · N Claude windows        |
|                                  | already open here / 1 Claude      |
|                                  | window already open here / No     |
|                                  | Claude window is open here · Main |
|                                  | folder has uncommitted changes (N |
|                                  | files) / (1 file) / Main folder   |
|                                  | has no uncommitted changes /      |
|                                  | Could not tell whether the main   |
|                                  | folder has uncommitted changes ·  |
|                                  | No open session / 1 open session  |
|                                  | / N open sessions, with " · N     |
|                                  | paused" appended when nonzero     |
+----------------------------------+-----------------------------------+
| Starting card (IDEA_030)         | Starting… · Did not start · The   |
|                                  | window did not appear on the Mac  |
|                                  | within a minute; check the Mac (a |
|                                  | trust prompt or a login may be    |
|                                  | waiting). · Claude is running in  |
|                                  | a new window on the Mac. Pick it  |
|                                  | up from Remote Control on your    |
|                                  | phone as ``<name>``.              |
+----------------------------------+-----------------------------------+
| Chain line (ADR-027 inc 3)       | Chain: running ``<entry>`` /      |
|                                  | starting ``<entry>`` / paused /   |
|                                  | held / waiting / idle / cannot be |
|                                  | read, then · N queued and · N     |
|                                  | proposed when nonzero             |
+----------------------------------+-----------------------------------+
| Chain screen (ADR-027 inc 3)     | Chain · Now · Proposed · Queue ·  |
|                                  | Running ``<entry>`` / Starting    |
|                                  | ``<entry>`` / Paused / Held /     |
|                                  | Waiting / Idle / Cannot be read · |
|                                  | Nothing is queued. · ``<entry>``  |
|                                  | · ``<ref>`` · Offered, never      |
|                                  | started on its own · Triage of    |
|                                  | ``<entry>`` · Retry of            |
|                                  | ``<entry>`` · Added by you /      |
|                                  | Added by the oracle / Added by a  |
|                                  | session / Added from the backlog  |
|                                  | (inc 5) · Serves ``<item>`` ·     |
|                                  | Reproduction saw nothing (N of 2  |
|                                  | runs) · Move ``<entry>`` up… /    |
|                                  | Move ``<entry>`` down… (the icon  |
|                                  | buttons' labels) · From the       |
|                                  | queue: it starts only when you    |
|                                  | start it. · The backlog's first   |
|                                  | ready item, offered because       |
|                                  | nothing is queued. · Running in a |
|                                  | window on the Mac · Reproducing   |
|                                  | in a window on the Mac (inc 5) ·  |
|                                  | Opening its                       |
|                                  | window on the Mac · Stalled at a  |
|                                  | usage limit / Stalled on an error |
|                                  | / Stalled, then · nudged N ago /  |
|                                  | · nudged just now / · still       |
|                                  | stalled after one nudge / ·       |
|                                  | nudged once it resets / · not     |
|                                  | nudged                            |
+----------------------------------+-----------------------------------+
| Chain screen's What is going on  | The chain started ``<entry>``,    |
|                                  | ``<ref>``, and follows it until   |
|                                  | its session ends. · The chain is  |
|                                  | opening a window on the Mac for   |
|                                  | ``<entry>``, ``<ref>``. · The     |
|                                  | chain is paused. ``<who>``        |
|                                  | Nothing starts on its own until   |
|                                  | you resume it. · The plan's       |
|                                  | weekly usage holds the chain, so  |
|                                  | queued work waits. ``<the hold>`` |
|                                  | · 1 entry is queued. It starts    |
|                                  | when nothing else runs in         |
|                                  | ``<project>``. / N entries are    |
|                                  | queued. The next one starts when  |
|                                  | nothing else runs in              |
|                                  | ``<project>``. · Nothing is       |
|                                  | queued in ``<project>``. ·        |
|                                  | invariant cannot read this        |
|                                  | project's chain now. Who paused   |
|                                  | it: It was paused by hand. / A    |
|                                  | session reported that it is       |
|                                  | blocked or needs you. / A triage  |
|                                  | failed, so nobody is left to      |
|                                  | judge the failure. / It was       |
|                                  | paused for a reason Technical     |
|                                  | details names.                    |
+----------------------------------+-----------------------------------+
| Chain cards (ADR-027 inc 3)      | Proposed · Still stalled · Not    |
|                                  | nudged · One nudge did not move   |
|                                  | it. Continue types once more;     |
|                                  | Retry and Close end its window. · |
|                                  | It could not be nudged: its       |
|                                  | Terminal tab was not found.       |
|                                  | Continue, Retry or Close. · It    |
|                                  | could not be nudged: typing into  |
|                                  | its tab failed. Continue, Retry   |
|                                  | or Close. · The chain is paused · |
|                                  | Chain paused · Every project ·    |
|                                  | The weekly usage cannot be read · |
|                                  | The weekly usage is over the cap  |
|                                  | · Held · No reading yet: the      |
|                                  | status line that records it is    |
|                                  | not installed. / The last reading |
|                                  | has no weekly figure. / The last  |
|                                  | reading is N old., then Nothing   |
|                                  | starts on its own until a fresh   |
|                                  | reading arrives; Go ahead lets    |
|                                  | the chain start queued work until |
|                                  | then. · N% of the week is used,   |
|                                  | at or over the cap of N%. Nothing |
|                                  | starts on its own until it falls; |
|                                  | a start by hand still runs.       |
+----------------------------------+-----------------------------------+
| Chain's destructive sheet        | This ends · Claude in             |
|                                  | ``<project>`` · window <first 8>  |
|                                  | · Process ``<pid>`` · idle        |
|                                  | ``<age>`` / Its window has        |
|                                  | already closed; nothing is ended  |
|                                  | · Checked just now · It is still  |
|                                  | the chain's current start, and    |
|                                  | still stalled · Checked again the |
|                                  | moment you press ``<deed>`` · On  |
|                                  | the Mac, for every project (the   |
|                                  | greenlight's place)               |
+----------------------------------+-----------------------------------+
| Chain's Technical details        | Chain start ``<id>`` · state      |
|                                  | ``<state>`` · session ``<id>`` ·  |
|                                  | window ``<name>`` · Paused by     |
|                                  | ``<by>`` since ``<when>``:        |
|                                  | ``<reason>`` · ``<entry>``        |
|                                  | ``<ref>`` · done / failed:        |
|                                  | ``<reason>`` · Queue error:       |
|                                  | ``<error>`` · Ledger error:       |
|                                  | ``<error>``; the usage card's:    |
|                                  | Weekly usage N% · five-hour N% ·  |
|                                  | cap N% · Read at ``<when>`` / No  |
|                                  | reading recorded · Go ahead given |
|                                  | ``<when>``                        |
+----------------------------------+-----------------------------------+
| Notifications (ADR-027 inc 4)    | Notifications · Alerts on this    |
|                                  | device · Turn on · Turn off ·     |
|                                  | Asking this device… · On: the     |
|                                  | Mac's alerts and the chain's      |
|                                  | notices reach this device. · On,  |
|                                  | but the last one did not arrive:  |
|                                  | ``<why>``. · Off. Turn them on to |
|                                  | get the Mac's alerts and the      |
|                                  | chain's notices here as well. ·   |
|                                  | The Mac cannot send them:         |
|                                  | invariant's oracle extra is not   |
|                                  | installed there. · They need the  |
|                                  | page over HTTPS, which the Mac is |
|                                  | not serving yet. · The Mac cannot |
|                                  | read where it keeps them. · Add   |
|                                  | this page to your Home Screen,    |
|                                  | then turn them on from there. ·   |
|                                  | This browser cannot receive them. |
|                                  | · This browser blocks them for    |
|                                  | this page; allow them in its      |
|                                  | settings. · That did not work:    |
|                                  | ``<error>``                       |
+----------------------------------+-----------------------------------+
| Back                             | Sessions                          |
+----------------------------------+-----------------------------------+
| Technical details                | Session ``<id>`` · state          |
|                                  | ``<state>`` · lane                |
|                                  | ``<methodology>``:``<phase>``;    |
|                                  | Host ``<id>`` · process id        |
|                                  | ``<pid>`` / process id not        |
|                                  | recorded / process ids disagree:  |
|                                  | ``<source>`` ``<pid>`` (<alive,   |
|                                  | gone or unknown>), …; in a store  |
|                                  | project (ADR-014 D5, increment    |
|                                  | 4b): Docs lock · holds ``<unit>`` |
|                                  | · <age> · queue ``<ids>`` ·       |
|                                  | stale: <why>; Docs lock · queued  |
|                                  | for ``<unit>`` at position N      |
|                                  | behind ``<id>``                   |
+----------------------------------+-----------------------------------+
| The oracle out of date (BUG_089) | Card: kicker Session Oracle · The |
|                                  | oracle is out of date · Not read: |
|                                  | ``<names>``. Technical details:   |
|                                  | Started from ``<digest>``,        |
|                                  | installed ``<digest>`` · Rules    |
|                                  | this build lacks: ``<ids>`` ·     |
|                                  | ``<project>``: ``<load error>`` · |
|                                  | Restart by hand: invariant oracle |
|                                  | install, and invariant oracle     |
|                                  | install --web for this page       |
+----------------------------------+-----------------------------------+

Times
~~~~~

Ages are relative and short: "now" under a minute, "N min" under an hour,
"N h" under two days, then "N days". "Active N min ago" on a compact card.
"Recent" shows the local clock time for today, and the date otherwise. An
age is shown only when the fact has a recorded time (BUG_078 makes the last
activity true).

Behaviour rules
---------------

- **Order.** Groups as above. Within a group: severity (high, medium, low,
  info), then the oldest condition first. Ties keep the document's order.
- **No mid-word breaks.** ``overflow-wrap: normal; word-break: normal``
  everywhere. Paths and commands are single-line scrollers, or carry a
  ``<wbr>`` after ``/``, ``-``, ``_`` and ``.`` where they wrap (ADR-009 D12).
- **The first view fits.** At 390 px wide, the strip and the Needs you cards
  need no horizontal scrolling.
- **Refresh.** Unchanged (ADR-008 D6, D18): the timer at the snapshot's
  interval, the backoff, the freshness line and the stale flag. An open
  sheet, an open disclosure and the scroll position survive a refresh.
- **Copy.** ``navigator.clipboard.writeText`` over HTTPS. Over plain HTTP the
  button selects the command's text instead (ADR-009 D5).
- **Expiry.** The sheet counts down from the confirm's ``expires_in``.
- **Waiting rows** never get a step's button (ADR-009 D8). A row waiting
  for input gets "Reply…" and one waiting on a question the page read gets
  "Answer…", which open the conversation and run no step (ADR-029 D10,
  increments 3 and 5); a permission prompt's is increment 6's.
- **Safe areas** on every screen and sheet (ADR-009 D10).

Where the build departs from the mockup
---------------------------------------

The mockup's content is illustrative. The build follows this page where they
differ:

+-----------------------------------------------+------------------------------------------------------------+------------------------+
| Mockup                                        | Build                                                      | Why                    |
+===============================================+============================================================+========================+
| "Close…" and "Remove…" pills 36 px, sheet     | visible size kept, hit area 44 px                          | ADR-009 D12            |
| Copy 32 px                                    |                                                            |                        |
+-----------------------------------------------+------------------------------------------------------------+------------------------+
| Result "Why": "Heap could not choose a        | a cause only when invariant wrote it, else "Failed, exit   | The mockup's sentence  |
| simulator: 4 are running…"                    | N" and the last meaningful line                            | names a cause the      |
|                                               |                                                            | output does not state  |
|                                               |                                                            | (ADR-009 D9)           |
+-----------------------------------------------+------------------------------------------------------------+------------------------+
| Destructive command                           | no ``--force``; run from the main checkout                 | ``--force`` overrides  |
| ``invariant session teardown --id … --force`` |                                                            | a refusal the oracle   |
|                                               |                                                            | must keep (ADR-009 D7) |
+-----------------------------------------------+------------------------------------------------------------+------------------------+
| "Abandon session"                             | a pinned                                                   | ``session transition`` |
|                                               | ``invariant session transition abandoned --id <session>``, | took no ``--id`` when  |
|                                               | or a new subcommand, decided in increment 7                | this was written; it   |
|                                               |                                                            | takes one now, as      |
|                                               |                                                            | every                  |
|                                               |                                                            | session-resolving      |
|                                               |                                                            | command does           |
|                                               |                                                            | (IDEA_042)             |
+-----------------------------------------------+------------------------------------------------------------+------------------------+
| A "Forget" button on a gone host              | no button; gone rows age out after 24 h                    | IDEA_025 puts any      |
|                                               |                                                            | action on a gone row   |
|                                               |                                                            | out of scope.          |
|                                               |                                                            | Forgetting a record is |
|                                               |                                                            | an open question       |
+-----------------------------------------------+------------------------------------------------------------+------------------------+
| No Idle group drawn                           | an Idle group after Working, compact cards, no tile        | The user's order is    |
|                                               |                                                            | needs you, problems,   |
|                                               |                                                            | working, idle          |
+-----------------------------------------------+------------------------------------------------------------+------------------------+
| No success sheet drawn                        | the result sheet with a tick                               | Derived from screen 5  |
+-----------------------------------------------+------------------------------------------------------------+------------------------+

As built in increment 3
-----------------------

Where the build had to decide something this page left open. Each is in
``oracle/panel``'s table, the only place the strings are spelled.

+----------------------------------+----------------------------------+
| Question                         | As built                         |
+==================================+==================================+
| The title of a row that is not a | "A Claude window with no         |
| session (an alerting orphan      | session", "A record invariant    |
| host, an unreadable record)      | cannot read"; a session with     |
|                                  | neither a name nor a task is     |
|                                  | "Unnamed session". The name      |
|                                  | drops its trailing id, and       |
|                                  | dashes become spaces             |
+----------------------------------+----------------------------------+
| "Paused" for a window that       | "Its Claude window closed        |
| closed without saying so         | without saying so, so nothing is |
|                                  | happening until you resume it."  |
|                                  | The "N ago" form waits for a     |
|                                  | true time: the only one today is |
|                                  | the attention record's           |
|                                  | modification time, which BUG_078 |
|                                  | shows is not one                 |
+----------------------------------+----------------------------------+
| A sentence whose time or count   | Its short form: "Claude asked    |
| is unknown                       | for permission.", "The last test |
|                                  | run failed.", "A check refused   |
|                                  | the last step." (the rules it    |
|                                  | knows are named, in plain words, |
|                                  | from a short table of the common |
|                                  | gates; an unknown rule is never  |
|                                  | named)                           |
+----------------------------------+----------------------------------+
| "Its Claude window closed" on a  | the note for a ``parked`` or     |
| card                             | ``host_gone`` row, followed by   |
|                                  | the phase ("Its Claude window    |
|                                  | closed. Fixing a bug.")          |
+----------------------------------+----------------------------------+
| A compact card with no phase or  | "Active N min ago" alone,        |
| no activity                      | "Active now" under a minute, or  |
|                                  | nothing                          |
+----------------------------------+----------------------------------+
| Leftover rows before increment 6 | A Claude window: "``<project>``  |
|                                  | · idle N" over "Still open on    |
|                                  | the Mac", "Already closed ·      |
|                                  | record only" or "Process id not  |
|                                  | recorded", then "· window <first |
|                                  | 8 of its id>". A folder:         |
|                                  | "``<project>`` ·                 |
|                                  | ``<folder name>``" over "On      |
|                                  | branch ``<branch>``" or "Not on  |
|                                  | a branch". The card's breakdown  |
|                                  | is singular for one ("1 Claude   |
|                                  | window with no session")         |
+----------------------------------+----------------------------------+
| The never-read freshness line    | "Stale · not updated yet"        |
+----------------------------------+----------------------------------+
| A failed refresh                 | a red line under the header,     |
|                                  | "Refresh failed N times in a row |
|                                  | (``<reason>``). Retrying."       |
+----------------------------------+----------------------------------+
| The result before increment 5    | "Running…", "Done in N s",       |
|                                  | "Failed, exit N" (or "Failed"    |
|                                  | for a window that did not open), |
|                                  | "Opened in a new window", the    |
|                                  | deed and "stopped after N s"     |
|                                  | under it, the output behind      |
|                                  | "Show full output". No "Why"     |
|                                  | yet: that is the summariser of   |
|                                  | increment 5                      |
+----------------------------------+----------------------------------+
| "Recent" entries                 | "Claude asked for permission",   |
|                                  | "Claude asked you a question",   |
|                                  | "A check refused the last step", |
|                                  | "A hook failed", "Claude started |
|                                  | working", "Claude finished its   |
|                                  | turn", "Its plan was updated",   |
|                                  | and "``<deed>``: ``<result>``"   |
|                                  | for this device's actions        |
+----------------------------------+----------------------------------+
| More actions                     | Not drawn in increment 3. The    |
|                                  | server derives only a row's      |
|                                  | recommended step (ADR-008 D9),   |
|                                  | so a second action such as       |
|                                  | resume, when it is not the       |
|                                  | suggestion, needs a route of its |
|                                  | own; increment 7 adds the        |
|                                  | section with its rows            |
+----------------------------------+----------------------------------+
| The destructive sheet for "Clean | ``session teardown`` is the one  |
| up…"                             | destructive step that exists     |
|                                  | today. Its sheet lists the       |
|                                  | folder and the branch, ends its  |
|                                  | sentence with "This cannot be    |
|                                  | undone.", puts Cancel first and  |
|                                  | the command behind "Exact        |
|                                  | command". "Checked just now"     |
|                                  | waits for increment 7's          |
|                                  | re-check, so it is not shown     |
+----------------------------------+----------------------------------+
| Freshness on the detail and      | The freshness line stays above   |
| leftovers screens                | the back link on every screen,   |
|                                  | so a stale read is never out of  |
|                                  | sight. The title and Refresh     |
|                                  | show on the overview only        |
+----------------------------------+----------------------------------+
| Copy feedback                    | the sheet's Copy reads "Copied"; |
|                                  | the detail's icon button's label |
|                                  | becomes "Copied"                 |
+----------------------------------+----------------------------------+

As built in increment 4
-----------------------

The home-screen app (ADR-009 D10). Where the build decided what this page
left open:

+------------------------+---------------------------------------------+
| Question               | As built                                    |
+========================+=============================================+
| The app's name         | "Session Oracle", short name and            |
|                        | home-screen title "Oracle", both in         |
|                        | ``panel.WORDS`` (``app_name``,              |
|                        | ``app_short_name``)                         |
+------------------------+---------------------------------------------+
| The manifest           | ``/manifest.webmanifest``:                  |
|                        | ``display: standalone``, ``id``,            |
|                        | ``start_url`` and ``scope`` ``/``,          |
|                        | ``background_color`` and ``theme_color``    |
|                        | the ``bg`` token (``#0B0C0E``), and the     |
|                        | 192- and 512-pixel icons. Linked from the   |
|                        | panel only, with                            |
|                        | ``crossorigin="use-credentials"``           |
+------------------------+---------------------------------------------+
| The icon               | The summary strip's four accents            |
|                        | (``accent-needs``, ``accent-problem``,      |
|                        | ``accent-working``, ``accent-idle``) as a   |
|                        | two-by-two grid of rounded tiles on the     |
|                        | ``bg`` ground, all of it inside the central |
|                        | circle of radius 0.4 a maskable icon keeps. |
|                        | Drawn by ``webpage.icon`` as an 8-bit RGB   |
|                        | PNG with ``zlib``, edges smoothed from the  |
|                        | tiles' signed distance; deterministic, no   |
|                        | file, no dependency. Sizes 180              |
|                        | (``apple-touch-icon``), 192 and 512, each   |
|                        | at its own path                             |
+------------------------+---------------------------------------------+
| The iOS tags           | ``apple-mobile-web-app-capable``, status    |
|                        | bar ``black-translucent`` (the page draws   |
|                        | under it and pads by the top inset),        |
|                        | ``apple-mobile-web-app-title``, and         |
|                        | ``theme-color`` the ``bg`` token. On the    |
|                        | panel only; the "not paired", pairing and   |
|                        | message pages carry none of the app's head  |
+------------------------+---------------------------------------------+
| Safe areas             | ``viewport-fit=cover`` on every page, so    |
|                        | ``env(safe-area-inset-*)`` is not zero; the |
|                        | body pads by all four insets plus its       |
|                        | margins, and the bottom sheet pads by the   |
|                        | bottom inset plus 34 px and by the side     |
|                        | insets plus 16 px                           |
+------------------------+---------------------------------------------+
| The token              | Unchanged (ADR-008 D4): the manifest and    |
|                        | icon routes answer a paired device and      |
|                        | nobody else, and read nothing from the      |
|                        | machine                                     |
+------------------------+---------------------------------------------+
| Pairing inside the app | The "not paired" page links "Enter a        |
|                        | pairing code" to ``/pair``, which is inside |
|                        | the app's scope, so a code from             |
|                        | ``oracle web pair`` can be typed in the     |
|                        | standalone app itself                       |
+------------------------+---------------------------------------------+

The two iOS checks of ADR-009 D10 could not be made on a device in this
increment. The user made both on an iPhone on 2026-10-25: the four-tile icon
appeared on the home screen (iOS sends the cookie for the touch icon), and
the home-screen app opened without asking to pair again (it shares Safari's
pairing). The token rule stands unchanged.

As built in increment 5
-----------------------

Failure reasons (ADR-009 D9). Where the build decided what this page left
open:

+----------------------------------+----------------------------------+
| Question                         | As built                         |
+==================================+==================================+
| Where the reason is made         | ``webactions.evidence`` reads    |
|                                  | the job's kept output (the same  |
|                                  | tail "Show full output" shows)   |
|                                  | for two facts: the last line     |
|                                  | that starts with ``invariant:``  |
|                                  | at the start of a line, which is |
|                                  | the line the CLI writes for an   |
|                                  | error it raised, and the last    |
|                                  | meaningful line.                 |
|                                  | ``panel.why_words`` words them,  |
|                                  | so the sentence's every word is  |
|                                  | in the panel's table.            |
|                                  | ``webactions.summarise`` joins   |
|                                  | the two, once, when the job      |
|                                  | ends; the job keeps it as        |
|                                  | ``words.why``                    |
+----------------------------------+----------------------------------+
| What counts as invariant's own   | Only ``invariant: <message>`` at |
| line                             | the very start of a line, after  |
|                                  | colour codes are removed; an     |
|                                  | indented one, or another prefix  |
|                                  | such as ``myinvariant:``, is the |
|                                  | target's text. The last such     |
|                                  | line wins, and only its first    |
|                                  | line is read, so a multi-line    |
|                                  | message (a discovery's shell     |
|                                  | script) never reaches the        |
|                                  | sentence                         |
+----------------------------------+----------------------------------+
| The causes the table words       | A discovery that produced        |
|                                  | nothing: "invariant could not    |
|                                  | get a value for ``<key>``: its   |
|                                  | discovery found none, or could   |
|                                  | not choose between several."     |
|                                  | (BUG_093: a discovery that       |
|                                  | refuses to guess between         |
|                                  | several, such as several booted  |
|                                  | simulators, produces nothing     |
|                                  | too, and the sentence used to    |
|                                  | hide that.) A discovery that     |
|                                  | timed out: "invariant ran out of |
|                                  | time finding a value for         |
|                                  | ``<key>``." Any other own line   |
|                                  | is quoted, not reworded:         |
|                                  | "invariant said: ``<message>``"  |
+----------------------------------+----------------------------------+
| The fallback                     | "Failed, exit N.                 |
|                                  | ``<last meaningful line>``", or  |
|                                  | "Failed. ``<line>``" when there  |
|                                  | is no exit status (a timeout, a  |
|                                  | command that could not start, a  |
|                                  | window that did not open, a      |
|                                  | runner that raised). Output with |
|                                  | nothing in it reads "… It        |
|                                  | printed nothing."; output that   |
|                                  | is all noise reads "… See the    |
|                                  | full output."                    |
+----------------------------------+----------------------------------+
| The last meaningful line         | Not blank, not a traceback's     |
|                                  | header, frame, source line or    |
|                                  | caret line, not "During handling |
|                                  | of the above exception…", not a  |
|                                  | progress bar (block characters,  |
|                                  | ``[=====>   ]``, a trailing      |
|                                  | ``[ 50%]``, \`45%                |
+----------------------------------+----------------------------------+
| Its length                       | At most 160 characters of the    |
|                                  | quoted line, ending in "…" when  |
|                                  | cut                              |
+----------------------------------+----------------------------------+
| Success, and a running job       | ``words.why`` is empty, and the  |
|                                  | sheet draws no "Why" panel       |
+----------------------------------+----------------------------------+
| The sheet                        | The "Why" panel is an inset      |
|                                  | block under the headline,        |
|                                  | labelled from ``WORDS.why``,     |
|                                  | with the sentence as text; "Show |
|                                  | full output" stays closed under  |
|                                  | it, and the output is unchanged  |
|                                  | (the existing 64 KiB tail)       |
+----------------------------------+----------------------------------+
| The headline                     | Unchanged from increment 3       |
|                                  | ("Failed, exit N", "Failed");    |
|                                  | the mockup's "The tests did not  |
|                                  | start" would name a cause the    |
|                                  | output does not state            |
+----------------------------------+----------------------------------+

As built for BUG_093: the session's own re-run
----------------------------------------------

The phone's "Re-run tests…" re-runs the session's own scoped tests, with the
values its own run found (oracle.md, "``oracle/recommend``"). Where the build
decided what this page left open:

+----------------------------------+--------------------------------------------+
| Question                         | As built                                   |
+==================================+============================================+
| What runs                        | ``invariant pipeline test --id <session>`` |
|                                  | with each selector of the session's newest |
|                                  | scoped run, in its folder, with            |
|                                  | ``INVARIANT_<KEY>`` for each value that    |
|                                  | run's discovery resolved. The session      |
|                                  | rides in the argv, never in the            |
|                                  | environment (IDEA_042), so the Copy line   |
|                                  | starts with ``invariant`` unless a         |
|                                  | discovery value leads it. Never the whole  |
|                                  | suite                                      |
+----------------------------------+--------------------------------------------+
| A value the privacy patterns     | Shown masked everywhere the panel shows    |
| flag                             | it: the confirm's "Exact folder and        |
|                                  | environment", the Command line and Copy,   |
|                                  | and the detail's Copy. The real value      |
|                                  | reaches only the command's own             |
|                                  | environment, when it runs, read from the   |
|                                  | one run that was confirmed                 |
+----------------------------------+--------------------------------------------+
| The Copy line with a masked      | A shell comment, "# Not runnable as shown, |
| value                            | ``<names>`` masked: ``<line>``": pasted,   |
|                                  | it runs nothing, since the line shown is   |
|                                  | not the command that runs                  |
+----------------------------------+--------------------------------------------+
| A value nothing flags            | Shown as before, ``NAME=value``            |
+----------------------------------+--------------------------------------------+
| Nothing to replay                | No button on the card or in the detail;    |
|                                  | the detail's "What is going on" ends with  |
|                                  | the reason, from the "No re-run offered"   |
|                                  | fixed strings                              |
+----------------------------------+--------------------------------------------+
| The step's sentence              | "Runs the tests this session last ran in   |
|                                  | ``<project>``, in its own folder and with  |
|                                  | its own settings, …": "test suite" was no  |
|                                  | longer true                                |
+----------------------------------+--------------------------------------------+
| The audit log                    | Unchanged: the argv and cwd, and no        |
|                                  | environment                                |
+----------------------------------+--------------------------------------------+

As built in increment 6
-----------------------

Orphan classes and close host (IDEA_025 scope v1, ADR-009 D15). Where the
build decided what this page left open:

+----------------------------------+-------------------------------------------------------------------------------+
| Question                         | As built                                                                      |
+==================================+===============================================================================+
| Where the class is made          | ``signals.orphan_class``, the ``orphan_host`` marker's ``cause``: ``gone``    |
|                                  | (its process is gone), ``unknown`` (no pid that can be asked, a conflict, or  |
|                                  | no activity known), ``active`` (alive and heard from within an hour,          |
|                                  | ``Thresholds.orphan_idle_minutes``, or mid-turn by ``signals.turn_state``:    |
|                                  | working, or waiting on the person), ``leftover`` (alive, idle, its session    |
|                                  | ended), ``unmanaged`` (alive, idle, never bound here). None alerts            |
+----------------------------------+-------------------------------------------------------------------------------+
| What mid-turn means (BUG_087)    | ``signals.turn_state``, the one answer the class, the ``waiting``, ``stuck``  |
|                                  | and ``your_turn`` signals, the gate cache and the Working group read. When    |
|                                  | Claude Code's per-process record verified the host (alive), its ``status``    |
|                                  | outranks the attention record: ``busy`` is working, ``idle`` is not mid-turn, |
|                                  | ``waiting`` is waiting on the person. Without it, a permission prompt or a    |
|                                  | question is waiting, an idle prompt ends the turn, and a ``running`` turn     |
|                                  | counts only while the host was heard from within ``orphan_idle_minutes``: a   |
|                                  | missed Stop leaves it ``running`` for ever, and six idle windows read         |
|                                  | "Active, no session" for up to 44 hours. BUG_094 adds ``background``: Claude  |
|                                  | Code's ``shell`` status, or an open entry in the attention record's           |
|                                  | background ledger, after the main turn ended. It is mid-turn, so the class is |
|                                  | ``active``, the gate cache waits, and the row is under Working, never         |
|                                  | ``your_turn`` (oracle.md, "Mid-turn")                                         |
+----------------------------------+-------------------------------------------------------------------------------+
| A window Claude says is waiting  | Never closeable: it is ``active``, and it raises the existing ``waiting``     |
|                                  | alert (cause ``input`` when the hooks recorded no prompt), so it sits in      |
|                                  | Needs you as "Waiting for you" with no button, never on the leftovers screen, |
|                                  | and ``recommend`` offers nothing to run (ADR-009 D8)                          |
+----------------------------------+-------------------------------------------------------------------------------+
| Who is offered Close             | A ``leftover`` or ``unmanaged`` row that is alive, whose pid is the           |
|                                  | per-process record's (``pid_source`` ``record`` or ``both``), or an attention |
|                                  | pid the scan verified with ``ps`` and ``lsof`` (``attention+ps``, BUG_099),   |
|                                  | with a start token and no ``pid_conflict``, whose ``cwd`` (the record's, or   |
|                                  | the one ``lsof`` read) is this project's root, a folder under it or one of    |
|                                  | its worktrees (``rooted_here``), and that no live session in another          |
|                                  | registered project lists (``claimed_elsewhere``, BUG_040). ``recommend``      |
|                                  | makes the close its step; an unmanaged host is offered Attach instead only    |
|                                  | when the match is unique both ways: the project has exactly one session it    |
|                                  | could take, and no other live orphan host here could take that session        |
|                                  | (``adopt_rivals``). Six windows that all list one session are each offered    |
|                                  | Close                                                                         |
+----------------------------------+-------------------------------------------------------------------------------+
| The pill                         | On the leftovers screen, by the row: the step's button ("Close…" or           |
|                                  | "Attach…") when the page may act, 36 px visible and 44 px to tap; it opens    |
|                                  | the confirm sheet over the leftovers screen, and Cancel returns there         |
+----------------------------------+-------------------------------------------------------------------------------+
| The sheet                        | The destructive sheet, with "This ends" for its red panel: "Claude in         |
|                                  | ``<project>`` · window <first 8>" and "Process ``<pid>`` · idle ``<age>``"    |
|                                  | (the age only when the last activity is known), "Checked just now" with its   |
|                                  | two lines, Cancel first, "Close window" in red, and the command behind "Exact |
|                                  | command". The sentence carries the ``claude --resume`` note IDEA_025 asks for |
+----------------------------------+-------------------------------------------------------------------------------+
| What runs                        | ``invariant oracle close-host <host> --pid N --start TOKEN [--tty ttysNNN]``, |
|                                  | a ``run`` step in the project root. It checks every fact again, sends one     |
|                                  | SIGTERM, waits up to 10 s, never sends SIGKILL, and closes the tab only once  |
|                                  | Claude has gone, asking a tab still busy with Claude's lingering children     |
|                                  | again for up to 5 s (BUG_098; oracle.md, "Close host")                        |
+----------------------------------+-------------------------------------------------------------------------------+
| The result                       | A refusal is the "Why" panel ("invariant said: nothing was done: process      |
|                                  | 41001 has already exited"). A close that ran shows "What happened", the       |
|                                  | command's last line: "Claude ended, and its Terminal tab closed.", "… its     |
|                                  | Terminal window closed.", "Claude ended, and no Terminal tab is left on       |
|                                  | ``<tty>``: its window closed with it, or was already closed." (IDEA_035),     |
|                                  | "Claude ended; the window was left open because …", or the Automation         |
|                                  | sentence. The job's ``words`` gain ``outcome`` (``panel.outcome_words``),     |
|                                  | empty for every other step. The audit log's done line keeps only that         |
|                                  | outcome's code, ``tab`` (BUG_098; state_model.md, "The web actions' audit     |
|                                  | log")                                                                         |
+----------------------------------+-------------------------------------------------------------------------------+
| A window that fails a close      | No pill; its row's line gains "· cannot close from here: ``<why>``" after the |
| guard (BUG_099)                  | window, by the step's ``reason`` (``panel._NO_CLOSE``): ``close_ps``          |
|                                  | "invariant could not read its process", ``close_start`` "invariant could not  |
|                                  | read when its process started", ``close_newer`` "its process may be a newer   |
|                                  | one", ``close_records`` "invariant could not read Claude's process records",  |
|                                  | ``close_other`` "its process now runs another Claude conversation",           |
|                                  | ``close_cwd`` "invariant could not read its folder", ``close_not_rooted`` "it |
|                                  | was started outside this project", ``close_claimed`` "a session in another    |
|                                  | project uses it". The generic "start or resume" line is left to the rows no   |
|                                  | guard applies to                                                              |
+----------------------------------+-------------------------------------------------------------------------------+
| A conflict row                   | "Its process ids disagree" on the leftovers screen, and "process ids          |
|                                  | disagree: attention 3 (gone), record 4 (alive)" under Technical details,      |
|                                  | instead of "process id not recorded"                                          |
+----------------------------------+-------------------------------------------------------------------------------+
| Close beside Attach (a known     | Not offered. A row has one step, the one ``recommend`` derives, and the       |
| gap)                             | confirm and the action are keyed by the row alone (ADR-008 D9), so a window   |
|                                  | offered "Attach…" cannot also reach "Close…" from the panel. A second action  |
|                                  | per row needs the route of its own that "More actions" waits for: a step      |
|                                  | selector bound into the nonce, re-derived on the server, and a second pill on |
|                                  | the leftovers row. Until then the unique-attach rule keeps Attach rare, and a |
|                                  | window that is attached can be closed through its session. Asked for          |
|                                  | 2026-10-25; left for increment 7                                              |
+----------------------------------+-------------------------------------------------------------------------------+

As built for IDEA_035: a host in a polluted Terminal.app
--------------------------------------------------------

A Claude window whose own environment inherited ``CLAUDE_CODE_CHILD_SESSION``
or ``COLORTERM=truecolor`` from its Terminal.app (oracle.md, "What a host
inherited from its terminal") raises ``inherited_env``, an info marker that
never alerts and moves no card between groups. A session's card shows the
chip "Terminal.app needs a restart" in the idle tone, and its detail the
sentence in the States table above, so the cause of the white blocks and the
missing transcript, and the cure, reach the phone. A leftover window's row
ends with "· Terminal.app needs a restart: quit it fully and relaunch it
from the Dock", after any close guard. The variable names stay out of the
panel's words; the signal's detail, the TUI and ``doctor`` carry them. A close
that finds no Terminal tab once Claude has ended now says "Claude ended, and
no Terminal tab is left on ``<tty>``: its window closed with it, or was already
closed.", because a window invariant launched ends with its Claude.

As built in IDEA_030 increment 2
--------------------------------

Start a new session from the phone (ADR-009 D16). Where the build decided
what this page and IDEA_030 left open:

+----------------------------------+---------------------------------------------------------+
| Question                         | As built                                                |
+==================================+=========================================================+
| Where the per-project shortcut   | The overview groups by attention, not by project, so    |
| lives                            | there is no project header to put it on. A **Projects** |
|                                  | section after the leftovers card lists every registered |
|                                  | project (the snapshot's list, so an unregistered folder |
|                                  | is never offered) with its open sessions counted, the   |
|                                  | paused ones named (BUG_092), and a "New session…" pill  |
+----------------------------------+---------------------------------------------------------+
| The top bar's button             | "New session…": it opens a confirm, so it ends with "…" |
|                                  | (the wording rule above)                                |
+----------------------------------+---------------------------------------------------------+
| The routes                       | ``GET /api/start/confirm?project=<key>&label=&prompt=`` |
|                                  | answers the sheet and a nonce;                          |
|                                  | ``POST /api/start {project, nonce}`` starts it, from    |
|                                  | this page's ``Origin`` only. A server without a         |
|                                  | starter, and a read-only one, answers neither, and the  |
|                                  | view's ``start`` is ``null`` (oracle.md, "From the      |
|                                  | phone")                                                 |
+----------------------------------+---------------------------------------------------------+
| How the name is shown before the | The Mac answers it: the slug and the ``-2`` suffix are  |
| start                            | ``startsession.session_name``'s, never the page's. The  |
|                                  | page asks again after each pause in the typing, and     |
|                                  | "Start session" waits for the answer                    |
+----------------------------------+---------------------------------------------------------+
| When the card ends               | When ``startsession.find_started`` finds a live host in |
|                                  | the project whose ``launched_name`` is the name.        |
|                                  | ``RemoteActions.observe`` marks the job ``appeared`` on |
|                                  | each state read, so a window that appeared and closed   |
|                                  | again leaves nothing rather than a false "did not       |
|                                  | appear"                                                 |
+----------------------------------+---------------------------------------------------------+
| Where the window is drawn once   | Its orphan-host row, titled by the name, under Working  |
| seen                             | while its orphan class is ``active`` (alive and heard   |
|                                  | from within the hour); it is not also counted as a      |
|                                  | leftover. Quiet longer than that, it is a leftover      |
|                                  | again. A session that took the window is drawn as that  |
|                                  | session                                                 |
+----------------------------------+---------------------------------------------------------+
| The minute                       | ``panel.START_APPEARS_WITHIN``, 60 s from the start,    |
|                                  | against the document's own ``now``, so a stale snapshot |
|                                  | never fails a start early                               |
+----------------------------------+---------------------------------------------------------+
| How long a failure stays         | ``panel.START_FAILURE_SHOWN``, an hour from the start;  |
|                                  | after it the audit log has it                           |
+----------------------------------+---------------------------------------------------------+
| Where the starts are kept        | With every action's result, in ``RemoteActions``'       |
|                                  | memory (the last 20 per server), so a pending start     |
|                                  | survives a page refresh and a reload, and is listed for |
|                                  | its device only. A restart of the server forgets it, as |
|                                  | it forgets any result (the audit log keeps both lines)  |
+----------------------------------+---------------------------------------------------------+
| A launch that failed             | The job's own "Why", quoting invariant's sentence       |
|                                  | ("invariant said: the launch did not start a successor: |
|                                  | …"); the card's line is the same sentence               |
+----------------------------------+---------------------------------------------------------+

As built for ADR-027 increment 3: the chain on the phone
--------------------------------------------------------

The chain's cards, its line on each project's row and the project Chain
screen (ADR-027 rulings 14 to 16, D31 to D37), drawn from this page's tokens
and components. Where the build decided what the rulings left open:

+----------------------------------+-------------------------------------------------------+
| Question                         | As built                                              |
+==================================+=======================================================+
| Where the chain comes from       | ``oracle/chainview.ChainReader``, which the CLI hands |
|                                  | the page's ``RemoteActions``: on every                |
|                                  | ``/api/state``, the chain ledger without its lock,    |
|                                  | each project's queue and the weekly-usage hold. The   |
|                                  | panel words it into ``view.chain`` (each project's    |
|                                  | screen), ``view.projects`` (each row's chain line)    |
|                                  | and the Needs-you cards. Without a reader, both are   |
|                                  | ``null`` and no chain card is drawn                   |
+----------------------------------+-------------------------------------------------------+
| How an action is named           | ``<project key>|chain:<action>[:<entry or item>]``,   |
|                                  | the greenlight's ``oracle|chain:greenlight``. The     |
|                                  | page sends it to ``/api/confirm`` and ``/api/act`` as |
|                                  | a row's key, and nothing else (ruling 14)             |
+----------------------------------+-------------------------------------------------------+
| A button is drawn when           | the read offers the action now (``offered``), and the |
|                                  | page may act; the server derives a step for nothing   |
|                                  | else, so the button and the run never disagree        |
+----------------------------------+-------------------------------------------------------+
| A queue that changes under a     | The step carries the position the read saw            |
| confirm                          | (``--if-queued-at``): a step derived differently at   |
|                                  | the press is a 409, and the queue refuses a moved or  |
|                                  | started entry under its lock (exit 2, "nothing was    |
|                                  | moved")                                               |
+----------------------------------+-------------------------------------------------------+
| Which proposals are cards        | Those the chain step made and that are still open:    |
|                                  | the ledger's ``proposed``, its entry still queued or  |
|                                  | its item named by no entry. Every queued propose      |
|                                  | entry is on the Chain screen with Start… whether or   |
|                                  | not the step has reached it                           |
+----------------------------------+-------------------------------------------------------+
| A stall's card                   | Only once the nudge did not clear it ("Still          |
|                                  | stalled") or could not be made ("Not nudged"); the    |
|                                  | three actions sit with Details in a two-column grid.  |
|                                  | Before that the Chain screen's Now says it, and the   |
|                                  | session's own Problems card says it is stalled        |
+----------------------------------+-------------------------------------------------------+
| The usage card                   | One, for every project, while the step has said the   |
|                                  | hold and it still holds. Go ahead… only for an        |
|                                  | unknown reading; over the cap it shows the            |
|                                  | percentages and no button. Its Details is a detail    |
|                                  | view, the reading under Technical details             |
+----------------------------------+-------------------------------------------------------+
| A pause's reason                 | Said by who paused it; the reason itself, which can   |
|                                  | quote a report's note or a triage's failure, only     |
|                                  | under Technical details                               |
+----------------------------------+-------------------------------------------------------+
| Waiting                          | A fifth status beside ruling 15's examples: work is   |
|                                  | queued and nothing holds it but one at a time, a cap  |
|                                  | or the hours                                          |
+----------------------------------+-------------------------------------------------------+
| Move up and down                 | Two 44 px icon buttons with a chevron, labelled "Move |
|                                  | Q5 up…" and "Move Q5 down…" for a screen reader, so   |
|                                  | the row keeps room for its text at 390 px             |
+----------------------------------+-------------------------------------------------------+
| One action per project           | A chain action's job is keyed by the project's chain, |
|                                  | so a second one waits for the first's result          |
+----------------------------------+-------------------------------------------------------+
| A project gone from the read     | Its Chain screen falls back to the overview, as a     |
|                                  | detail whose session has gone does                    |
+----------------------------------+-------------------------------------------------------+

As built for ADR-027 increment 4: web push
------------------------------------------

The Notifications row (ruling 17, :need:`DEC_ADR_027_D41`,
:need:`DEC_ADR_027_D42`), drawn from this page's tokens and components with
no mockup round, as ruling 16 has it. Where the build decided what the
ruling left open:

+----------------------------------+-------------------------------------------------------+
| Question                         | As built                                              |
+==================================+=======================================================+
| Where it lives                   | The overview's last block, after Projects: a section  |
|                                  | label and one list row with a row pill, the           |
|                                  | components the Projects section already uses. It is   |
|                                  | this device's setting, so it is not a card and never  |
|                                  | joins Needs you                                       |
+----------------------------------+-------------------------------------------------------+
| Confirm                          | None. Turning push on runs nothing on the Mac; the    |
|                                  | browser's own permission prompt is the confirm, and   |
|                                  | it must come from the tap itself, which a sheet's     |
|                                  | second tap would not keep on every browser. The pill  |
|                                  | is named for the deed, without "…"                    |
+----------------------------------+-------------------------------------------------------+
| What the server knows            | ``view.push``: whether the Mac offers push now, why   |
|                                  | not (``line``), the VAPID public key, whether this    |
|                                  | device has a subscription, and the row's line while   |
|                                  | it is on (``line_on``, with the last delivery error   |
|                                  | when there was one). ``null`` from a server built     |
|                                  | without push, and then no row                         |
+----------------------------------+-------------------------------------------------------+
| What only the browser knows      | Whether it has the push API (none: "This browser      |
|                                  | cannot receive them", or, on an iPhone outside the    |
|                                  | home-screen app, "Add this page to your Home          |
|                                  | Screen…"), whether the permission is blocked, and     |
|                                  | whether this browser holds a subscription, read again |
|                                  | on every refresh. The row says On only when the Mac   |
|                                  | has this device's subscription and this browser holds |
|                                  | one; the words are ``WORDS``'                         |
+----------------------------------+-------------------------------------------------------+
| The service worker               | Registered once the Mac offers push, from             |
|                                  | ``/sw.js`` (scope ``/``). It shows a push's title,    |
|                                  | its subtitle and message as the body, silent when the |
|                                  | Mac's banner is, with the 192-pixel icon; a tap       |
|                                  | focuses the open panel, else opens it                 |
+----------------------------------+-------------------------------------------------------+
| A key the browser subscribed     | Subscribing again with the Mac's key fails; the       |
| with before                      | script drops the old subscription and subscribes once |
|                                  | more                                                  |
+----------------------------------+-------------------------------------------------------+
| Turn off                         | Unsubscribes this browser and asks the Mac to drop    |
|                                  | that endpoint (every one of the device's when the     |
|                                  | browser holds none); allowed over plain HTTP too, so  |
|                                  | a device can always leave                             |
+----------------------------------+-------------------------------------------------------+

What could not be checked here, and is owed on a phone: that iOS sends the
device's cookie when it fetches ``/sw.js`` (the service worker sits behind the
token, ADR-008 D4, as the manifest and icons do), and that a push reaches the
home-screen app and shows its text.

As built for ADR-027 increment 5: the backlog on the queue
----------------------------------------------------------

The queue now holds the backlog (rulings 18 to 20, :need:`DEC_ADR_027_D52`).
Nothing new is drawn: a mirrored entry is a queue row like any other, with
move up, move down and Remove…, and the words below join ``panel.WORDS``'s
chain set. Removing a mirrored entry keeps it off the queue until its item
closes and opens again (:need:`DEC_ADR_027_D46`).

+----------------------------------+----------------------------------------------+
| Where                            | As built                                     |
+==================================+==============================================+
| Queue row, secondary line        | "Added from the backlog" for an entry the    |
|                                  | mirror wrote (in place of "Added by …");     |
|                                  | "Serves <item>" when the entry serves one;   |
|                                  | "Reproduction saw nothing (N of 2 runs)"     |
|                                  | after a reproduce-first run that recorded    |
|                                  | nothing. In that order, after "Offered,      |
|                                  | never started on its own" and the triage and |
|                                  | retry words, joined by " · "                 |
+----------------------------------+----------------------------------------------+
| Now card, the step's line        | "Reproducing in a window on the Mac" while a |
|                                  | reproduce-first start runs unstalled; a      |
|                                  | stall reads as before                        |
+----------------------------------+----------------------------------------------+
| Proposed, cards                  | A bug owing a live repro, or unseen twice,   |
|                                  | is a propose entry: its card and Start… are  |
|                                  | increment 3's                                |
+----------------------------------+----------------------------------------------+

As built for ADR-027 ruling 21: a landed window asks to close
-------------------------------------------------------------

A chained session that landed keeps its window, which holds its project
until it closes (IDEA_062, :need:`DEC_ADR_027_D61`). The oracle asks the
session itself; only when that cannot settle it does the phone ask the user.
Built from the panel's existing components with no mockup round (ruling 16):
one more card kind under Needs you, the Now card of the Chain screen, and the
two sheets. Every word below is ``panel``'s chain set.

+----------------------------------+----------------------------------------------+
| Where                            | As built                                     |
+==================================+==============================================+
| Needs-you card                   | Kicker the project's name, title the work's  |
|                                  | ref, one needs chip "Window open", and a     |
|                                  | note: the why, then "Close ends it; Leave    |
|                                  | open keeps it, and the chain waits." The     |
|                                  | whys: "It landed, but did not answer when    |
|                                  | asked whether its window may close." / "It   |
|                                  | landed, and said not yet N times:            |
|                                  | “<note>”." / "It landed, but the             |
|                                  | oracle could not ask it: its Terminal tab    |
|                                  | was not found, or typing failed." / "It      |
|                                  | landed, and the oracle cannot tell whether   |
|                                  | you typed in its window since." / "It agreed |
|                                  | to close, but closing its window failed." /  |
|                                  | "It landed, and its window still holds the   |
|                                  | project." Close… and Leave open… sit with    |
|                                  | Details in the stall card's two-column grid; |
|                                  | it ranks with a stall, its age from the      |
|                                  | notice                                       |
+----------------------------------+----------------------------------------------+
| When it is a card                | Only once the oracle sent its notice (no     |
|                                  | answer, the last "not yet", an ask that      |
|                                  | could not be typed, a record it cannot read, |
|                                  | a close that failed). A window still being   |
|                                  | asked, closing on consent, left open, or the |
|                                  | user's own is no card: nothing is asked of   |
|                                  | the user                                     |
+----------------------------------+----------------------------------------------+
| Now card, the step's line        | "Landed · its window is asked to close once  |
|                                  | it is quiet" / "Landed · said not yet:       |
|                                  | “<note>”; asked again later" /               |
|                                  | "Landed · asked whether its window may       |
|                                  | close" / "Landed · closing its window, as it |
|                                  | agreed" / "Landed · its window is still open |
|                                  | and waits for you" / "Landed · left open by  |
|                                  | you; the chain waits until you close it" /   |
|                                  | "Landed · you typed in its window, so it is  |
|                                  | yours; the chain waits until you close it".  |
|                                  | Shown only while the chain follows no start  |
+----------------------------------+----------------------------------------------+
| Close…                           | The destructive sheet (ruling 14, D37's):    |
|                                  | "Close Q5's window?", "This ends" with the   |
|                                  | window and its process from the fresh scan,  |
|                                  | or "Its window has already closed; nothing   |
|                                  | is ended"; Checked just now: "It is still    |
|                                  | the window where Q5 landed, and it still     |
|                                  | waits for you" · "Checked again the moment   |
|                                  | you press Close window"                      |
+----------------------------------+----------------------------------------------+
| Leave open…                      | The safe sheet, "Leave Q5's window open?",   |
|                                  | its place "In <project> › main checkout"     |
+----------------------------------+----------------------------------------------+
| Keys                             | ``<project key>|chain:close-window`` and     |
|                                  | ``|chain:leave-open``, offered only while    |
|                                  | the landed window waits for the user; the    |
|                                  | deeds are "Close window" and "Leave open"    |
+----------------------------------+----------------------------------------------+

As built for BUG_089: the oracle out of date
--------------------------------------------

When the document's ``oracle.skew`` is set, the oracle running the page is
older than the invariant installed under it (:doc:`oracle.md </architecture/oracle>`, "An older
oracle"). ``panel.view`` then puts one card first under Problems, with the row
key ``oracle|build``, which no project row can have, and a detail under the same
key; the page draws both with the components it has, so the script is
unchanged.

+-------------------+--------------------------------------------------+
| Element           | Rule                                             |
+===================+==================================================+
| The card          | Kicker "Session Oracle", title "The oracle is    |
|                   | out of date", one problem chip ("Restarting"     |
|                   | when the installed tree changed, so the oracle   |
|                   | restarts itself; "Restart needed" when only a    |
|                   | rule gap shows it), the note "Not read:          |
|                   | ``<names>``" when it left projects unread, no    |
|                   | button, not a tap card                           |
+-------------------+--------------------------------------------------+
| The count         | It counts once on the Problems tile and in the   |
|                   | document title, however many projects it left    |
|                   | unread                                           |
+-------------------+--------------------------------------------------+
| No project blamed | A project left unread has no card at all: never  |
|                   | "Cannot read this project" for the oracle's own  |
|                   | age                                              |
+-------------------+--------------------------------------------------+
| The detail        | "What is going on" says what happened and        |
|                   | whether it restarts itself, then which projects  |
|                   | it cannot read until then; no step; "Technical   |
|                   | details" carries both stamps, the rule ids this  |
|                   | build lacks, each unread project's load error,   |
|                   | and the commands that restart it by hand         |
+-------------------+--------------------------------------------------+

As built for ADR-029 increment 1: a window's last message
---------------------------------------------------------

IDEA_029's Read half (:need:`DEC_ADR_009_D17`, :need:`DEC_ADR_029_D11`). A
live Claude window's last message is drawn on its card, its row on the
Leftovers screen and its detail, from this page's tokens and components with
no new screen. The server side is :doc:`oracle.md </architecture/oracle>`,
"Shipped: a window's last message".

+----------------------------------+----------------------------------------------+
| Where                            | As built                                     |
+==================================+==============================================+
| Full card (Needs you, Problems)  | Under the chips and before the note, an      |
|                                  | inset block (``surface-inset``, radius 14,   |
|                                  | padding 12 × 14, 4 px gap) holding the label |
|                                  | "Claude" (13 px semibold ``text-tertiary``)  |
|                                  | over Claude's last text in 15 px             |
|                                  | ``text-secondary``, at most three lines, cut |
|                                  | with an ellipsis (``-webkit-line-clamp:      |
|                                  | 3``). The card carries at most the text's    |
|                                  | first 600 characters                         |
+----------------------------------+----------------------------------------------+
| Compact card (Working, Idle)     | One line of the text under its phase line,   |
|                                  | 13 px ``text-tertiary``, cut with an         |
|                                  | ellipsis                                     |
+----------------------------------+----------------------------------------------+
| Leftovers, a window's row        | The compact card's one line, under the row's |
|                                  | state line (:need:`DEC_ADR_029_D18`): the    |
|                                  | person reads it to decide on Close…          |
+----------------------------------+----------------------------------------------+
| Session detail, **Last message** | A section after "What is going on": its      |
|                                  | section label, then the prompt Claude's text |
|                                  | answers under "You" when the read has it,    |
|                                  | then the text under "Claude", each in its    |
|                                  | own inset block. Past 1200 characters the    |
|                                  | text shows its first 1200 and an ellipsis,   |
|                                  | and a "Show all" link button (44 px) opens   |
|                                  | the rest in place; it stays open across      |
|                                  | refreshes until the person leaves the        |
|                                  | detail. "Open conversation" ends it since    |
|                                  | increment 2 (below)                          |
+----------------------------------+----------------------------------------------+
| Text                             | The session's own, set with ``textContent``, |
|                                  | never markup: a ``<script>`` or a markdown   |
|                                  | link shows as its characters. Its line       |
|                                  | breaks are kept (``pre-wrap``), and a word   |
|                                  | too long for the line breaks rather than     |
|                                  | pushing the screen sideways, unlike the      |
|                                  | panel's own words. It is not a label, so it  |
|                                  | is outside the wording table (ADR-029 D11)   |
+----------------------------------+----------------------------------------------+
| None                             | A row with no text from Claude, a gone       |
|                                  | window, or a read that stopped at a shape    |
|                                  | the host does not know draws no block and no |
|                                  | placeholder                                  |
+----------------------------------+----------------------------------------------+

The labels join ``panel.WORDS``: "Claude", "You", "Last message" and "Show
all". The page drops the text after 60 seconds in the background, as built
with increment 2 (below).

As built for ADR-029 increment 2: the Conversation
--------------------------------------------------

Screen 9's reading parts (:need:`DEC_ADR_029_D11`, :need:`DEC_ADR_009_D17`),
from this page's tokens and components; the composer, Stop, the answers and a
send's states are increments 3 to 6 and are not drawn. The server side is
:doc:`oracle.md </architecture/oracle>`, "Shipped: a window's conversation".

+----------------------------------+----------------------------------------------+
| Where                            | As built                                     |
+==================================+==============================================+
| Session detail, **Open           | A 48 px secondary button, full width, ending |
| conversation**                   | the Last message section; with no last       |
|                                  | message it stands alone in the same place.   |
|                                  | Drawn when the server reads conversations    |
|                                  | and the row has a live window                |
|                                  | (:need:`DEC_ADR_029_D23`). A leftover        |
|                                  | window's row has no detail, so no way in     |
+----------------------------------+----------------------------------------------+
| 1 · Top                          | The freshness line (the page's own, as on    |
|                                  | every screen), then the back link "‹         |
|                                  | ``<title>``", 44 px, back to the detail      |
+----------------------------------+----------------------------------------------+
| 2 · Head                         | The kicker "``<project>`` · Conversation"    |
|                                  | (uppercase), the title (22 px bold), the     |
|                                  | state chips with their ages, as the detail's |
+----------------------------------+----------------------------------------------+
| 3 · The turns                    | Oldest first, newest at the bottom, 12 px    |
|                                  | apart. The person's turn a quiet card        |
|                                  | (``surface-quiet``, ``border``) under "You", |
|                                  | Claude's a card (``surface-card``) under     |
|                                  | "Claude", each label 13 px semibold          |
|                                  | ``text-tertiary`` over the text in 15 px,    |
|                                  | line breaks kept, a long word broken. A tool |
|                                  | call is one line of 13 px monospace in       |
|                                  | ``text-tertiary``, no card, in a single-line |
|                                  | scroller ("Ran git status"). A text cut at   |
|                                  | the cap ends with "Cut here: the rest is on  |
|                                  | the Mac" (13 px ``text-tertiary``). A live   |
|                                  | window with no turns yet shows none, and no  |
|                                  | placeholder                                  |
+----------------------------------+----------------------------------------------+
| Bottom and "New messages"        | It opens scrolled to the bottom and stays    |
|                                  | there as turns arrive while the view is      |
|                                  | within 48 px of it. Scrolled up, a changed   |
|                                  | read leaves the scroll alone and floats the  |
|                                  | pill "New messages" at the foot (44 px,      |
|                                  | radius 22, ``surface-button``, 15 px         |
|                                  | semibold in ``link``, 24 px above the bottom |
|                                  | safe area): a tap scrolls down and puts it   |
|                                  | away, as scrolling to the bottom does        |
+----------------------------------+----------------------------------------------+
| Refresh                          | Asked when the screen opens and 2 s after    |
|                                  | each answer, with the last cursor, only      |
|                                  | while it is open and the page visible; at    |
|                                  | once when the page is seen again. An         |
|                                  | unchanged read redraws nothing new; a failed |
|                                  | one keeps what is shown and waits twice as   |
|                                  | long each time, up to 60 s                   |
|                                  | (:need:`DEC_ADR_029_D24`)                    |
+----------------------------------+----------------------------------------------+
| Off: cannot read                 | A quiet card, "Can't read this session here  |
|                                  | — use the Mac.", at the top of the turns, in |
|                                  | place of anything older; the newer turns     |
|                                  | stand under it (:need:`DEC_ADR_029_D22`)     |
+----------------------------------+----------------------------------------------+
| Off: gone                        | A quiet card, "The Claude window is gone.",  |
|                                  | in place of the turns                        |
+----------------------------------+----------------------------------------------+
| In the background                | Hidden for 60 s, the page clears every       |
|                                  | session text it holds, the overview's        |
|                                  | excerpts with the conversation, from the     |
|                                  | screen and from memory; coming back shows    |
|                                  | "Reading again…" (14 px ``text-secondary``)  |
|                                  | until the next read                          |
+----------------------------------+----------------------------------------------+
| Text                             | Set with ``textContent``, never markup, as   |
|                                  | for the excerpt                              |
+----------------------------------+----------------------------------------------+

The labels join ``panel.WORDS``: "Open conversation", "Conversation", "New
messages", "Cut here: the rest is on the Mac", "Reading again…", "Can't read
this session here — use the Mac." and "The Claude window is gone.".

As built for ADR-029 increment 3: sending
-----------------------------------------

Screen 9's composer and a send's states, and the waiting card's "Reply…"
(:need:`DEC_ADR_029_D2`, :need:`DEC_ADR_029_D10`, :need:`DEC_ADR_029_D30`,
:need:`DEC_ADR_029_D31`), from this page's tokens and components; Stop, the
answers and the confirm sheet for a permission are increments 4 to 6 and are
not drawn. The server side is :doc:`oracle.md </architecture/oracle>`,
"Shipped: sending to a window".

+----------------------------------+----------------------------------------------+
| Where                            | As built                                     |
+==================================+==============================================+
| Needs you card waiting for       | A primary button, "Reply…", before Details,  |
| input, **Reply…**                | where the page sends and the row has a live  |
|                                  | window it reads; it opens the Conversation   |
|                                  | with the composer focused. A card waiting on |
|                                  | a question or a permission keeps ADR-009     |
|                                  | D8's card ("Answer…" is increment 5's)       |
+----------------------------------+----------------------------------------------+
| 5 · The composer                 | Fixed above the bottom safe area on          |
|                                  | ``surface-sheet`` (radius 18 at the top),    |
|                                  | outside the screen the 2-second read         |
|                                  | redraws, so the draft is never taken: a text |
|                                  | area (16 px, radius 22, ``surface-inset``,   |
|                                  | 44 px tall, growing to five lines) and a 44  |
|                                  | px round primary Send button (an up arrow,   |
|                                  | labelled "Send"). Its placeholder is         |
|                                  | "Message Claude", or "Message Claude — sent  |
|                                  | when this turn ends" while a turn runs. The  |
|                                  | turns keep 96 px clear above it, and "New    |
|                                  | messages" floats above it                    |
+----------------------------------+----------------------------------------------+
| A refusal at the tap             | "Not sent: ``<reason>``", 13 px in           |
|                                  | ``accent-problem`` above the text area; the  |
|                                  | text stays in it                             |
+----------------------------------+----------------------------------------------+
| A send                           | After the turns, a "You" quiet card with the |
|                                  | text the page holds and its state line under |
|                                  | it: 13 px, ``text-secondary``, ``working``   |
|                                  | or ``problem`` by its tone, and its one      |
|                                  | action as a link button with a 44 px hit     |
|                                  | area after a "·": Undo while held, Cancel    |
|                                  | while queued. Undo gives the text back to an |
|                                  | empty composer. The wait for quiet and the   |
|                                  | typing read "Sending…" with no action. A     |
|                                  | delivered send shows its line alone, as its  |
|                                  | turn arrives above                           |
+----------------------------------+----------------------------------------------+
| Off                              | In the composer's place, 14 px               |
|                                  | ``text-secondary`` under the turns: "Send    |
|                                  | from here works for Terminal windows only."  |
|                                  | or "Claude is not running in this window."   |
|                                  | A session the page cannot read, or a gone    |
|                                  | window, shows its card (increment 2) and no  |
|                                  | composer                                     |
+----------------------------------+----------------------------------------------+
| Restarted                        | A send of this page the server under a new   |
|                                  | boot does not list reads "Dropped, not       |
|                                  | delivered: the oracle restarted"             |
+----------------------------------+----------------------------------------------+
| In the background                | After 60 s hidden the sends' texts go with   |
|                                  | every other session text; their lines stay.  |
|                                  | The draft stays in the composer              |
+----------------------------------+----------------------------------------------+

A send's lines are the state table below (screen 9), with "Not sent:" worded
by its reason ("The session changed since you looked." followed by what
changed: "A new turn began.", "Claude restarted in it.", "It moved to another
terminal.", "It is another Claude window now."). The labels join
``panel.WORDS``: "Reply…", "Message Claude", "Message Claude — sent when
this turn ends", "Send", "Undo", each state's line, each reason, and the
alert's "A message from the phone was not confirmed".

As built for ADR-029 increment 4: Stop and slash commands
---------------------------------------------------------

Screen 9's Stop (:need:`DEC_ADR_029_D35`, :need:`DEC_ADR_029_D36`) and slash
commands sent from the composer (:need:`DEC_ADR_029_D37` to D39), from this
page's tokens and components. The answers and the confirm sheet for a
permission are increments 5 and 6. The server side is :doc:`oracle.md
</architecture/oracle>`, "Shipped: stopping a turn and slash commands".

+----------------------------------+----------------------------------------------+
| Where                            | As built                                     |
+==================================+==============================================+
| 5 · Stop                         | A 44 px round secondary button               |
|                                  | (``surface-button``, a filled square icon in |
|                                  | ``text``, labelled "Stop") in the composer   |
|                                  | row between the text area and Send, while    |
|                                  | the server offers it: a turn runs in a       |
|                                  | Terminal tab where Claude runs, with no      |
|                                  | question or permission prompt open           |
+----------------------------------+----------------------------------------------+
| Stop alone                       | Where send is off and Stop is offered (a     |
|                                  | session the page cannot read, ruling 11),    |
|                                  | the composer shows Stop alone, with no text  |
|                                  | area and no Send                             |
+----------------------------------+----------------------------------------------+
| A tap                            | One tap, no sheet; the draft in the text     |
|                                  | area stays as it is. The stop shows after    |
|                                  | the turns as a "You" quiet card reading      |
|                                  | "Stop" with a send's state line: "Sending… · |
|                                  | Undo" for 5 s, then "Sent · checking",       |
|                                  | "Delivered" (its line alone) or "Not         |
|                                  | confirmed — check the session on the Mac".   |
|                                  | Undo removes it and leaves the draft alone   |
+----------------------------------+----------------------------------------------+
| Refused                          | "Not sent: ``<reason>``" as for a send, at   |
|                                  | the tap above the text area, or on the       |
|                                  | stop's card: "Claude's turn had already      |
|                                  | ended.", "Claude is waiting on a prompt —    |
|                                  | answer it on the Mac.", "Someone is typing   |
|                                  | on the Mac.", "The session changed since you |
|                                  | looked." with what changed                   |
+----------------------------------+----------------------------------------------+
| After it                         | The turns show Claude Code's own "[Request   |
|                                  | interrupted by user]" as the person's turn,  |
|                                  | as written in the transcript                 |
+----------------------------------+----------------------------------------------+
| A slash command                  | Typed in the composer and sent as a message: |
|                                  | the same card, Undo and state lines. A       |
|                                  | command that opens a menu when given no      |
|                                  | arguments is refused at the tap, "Not sent:  |
|                                  | This command opens a menu — use it on the    |
|                                  | Mac.", and stays in the text area            |
+----------------------------------+----------------------------------------------+
| A command in the turns           | A command, from the phone or the Mac, shows  |
|                                  | as the person's turn, ``/name args``; its    |
|                                  | output is not shown                          |
+----------------------------------+----------------------------------------------+

The labels join ``panel.WORDS``: "Stop", "Claude's turn had already ended.",
"Claude is waiting on a prompt — answer it on the Mac." and "This command
opens a menu — use it on the Mac."; "Slash commands are sent from the Mac for
now." is gone.

As built for ADR-029 increment 5: answering a question
------------------------------------------------------

Screen 9's question block and the waiting card's "Answer…"
(:need:`DEC_ADR_029_D8`, :need:`DEC_ADR_029_D10`, :need:`DEC_ADR_029_D45`,
:need:`DEC_ADR_029_D46`), from this page's tokens and components. A
permission prompt's block and its confirm sheet are increment 6. The server
side is :doc:`oracle.md </architecture/oracle>`, "Shipped: answering a
question".

+----------------------------------+----------------------------------------------+
| Where                            | As built                                     |
+==================================+==============================================+
| Needs you card waiting on a      | A primary button, "Answer…", before Details, |
| question, **Answer…**            | where the page answers, the row has a live   |
|                                  | window and the server read its question; it  |
|                                  | opens the Conversation at the question. The  |
|                                  | card's note is the question's first line     |
|                                  | (the session's text, at most 200 characters, |
|                                  | set as text). A question that cannot be read |
|                                  | keeps ADR-009 D8's card, its note "Can't     |
|                                  | read this prompt here — use the Mac."        |
+----------------------------------+----------------------------------------------+
| 4 · What Claude is waiting on    | After the turns and the sends, a card on     |
|                                  | ``tile-needs`` (16 px between questions):    |
|                                  | each question's text in 17 px semibold, then |
|                                  | one 48 px secondary button per choice, full  |
|                                  | width, its label over its description (13 px |
|                                  | ``text-secondary``) when it has one          |
+----------------------------------+----------------------------------------------+
| A lone single-select question    | A tap on a choice sends it at once (one tap, |
|                                  | Undo). "Other…", a 48 px secondary button,   |
|                                  | opens a one-line 16 px field and a 48 px     |
|                                  | primary "Send"                               |
+----------------------------------+----------------------------------------------+
| Several questions, or a          | Each choice is a toggle (``aria-pressed``,   |
| multi-select                     | ``accent-primary`` when on): one per         |
|                                  | single-select question, any number for a     |
|                                  | multi-select. "Other…" opens the question's  |
|                                  | field. One 48 px primary "Send answer" ends  |
|                                  | the block, enabled once every question has   |
|                                  | an answer, and sends them together           |
+----------------------------------+----------------------------------------------+
| While it is chosen               | The block keeps the choices and the "Other"  |
|                                  | text across the 2-second reads; a read waits |
|                                  | while the person types in "Other". A new     |
|                                  | question starts afresh                       |
+----------------------------------+----------------------------------------------+
| A send                           | The block goes, and the answer shows as a    |
|                                  | "You" quiet card with the chosen labels (one |
|                                  | line per question) and a send's state line,  |
|                                  | as a message's; Undo brings the block back   |
|                                  | as it was                                    |
+----------------------------------+----------------------------------------------+
| Refused                          | "Not sent: ``<reason>``", 13 px in           |
|                                  | ``accent-problem`` at the foot of the block, |
|                                  | or on the answer's card: "Claude is no       |
|                                  | longer waiting on that question.", "Someone  |
|                                  | is typing on the Mac.", "The session changed |
|                                  | since you looked." with what changed         |
+----------------------------------+----------------------------------------------+
| Off: cannot read                 | A quiet card, "Can't read this question here |
|                                  | — use the Mac.", in the block's place        |
+----------------------------------+----------------------------------------------+
| Text                             | Every question, label, description and       |
|                                  | "Other" set with ``textContent``, never      |
|                                  | markup. "Chat about this" is not offered     |
+----------------------------------+----------------------------------------------+

The labels join ``panel.WORDS``: "Answer…", "Send answer", "Other…", "Your
answer", "Can't read this question here — use the Mac.", "Can't read this
prompt here — use the Mac.", "Claude is no longer waiting on that question."
and "That answer does not fit the question."; the waiting sentences are
those of "States" above.

Planned: talking to sessions (ADR-029)
--------------------------------------

**Planned, not built, after increments 1 to 5.** ADR-029 (IDEA_060) is
accepted (2026-11-20) and its increments 6 and 7 are queued; increments 1 to
5 are built (above). Its screens are built from this page's tokens and
components, with no mockup round, as ADR-027 ruling 16 did for the chain, and
the user reviewed them with the plan (:need:`DEC_ADR_029_D13`). The server
side is :doc:`oracle.md </architecture/oracle>`, "Planned: talking to
sessions". "Inc" below is ADR-029's increment.

.. mermaid::

   stateDiagram-v2
       Overview --> Detail: tap a card
       Overview --> Conversation: Reply… (inc 3, built), Answer… on a question (inc 5, built) or on a permission (inc 6)
       Detail --> Conversation: Open conversation (inc 2, built)
       Conversation --> Conversation: Send and Undo (inc 3, built), Stop (inc 4, built), a question's answer (inc 5, built)
       Conversation --> Confirm: a permission choice (inc 6)
       Confirm --> Conversation: Allow or Deny, or Cancel
       Conversation --> Detail: back
       Overview --> PhoneRules: Phone rules row (inc 6)
       PhoneRules --> Destructive: Remove…
       Destructive --> PhoneRules: Cancel, or Done after the result
       PhoneRules --> Overview: back

9 · Conversation (inc 2 to 6)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A view inside the page, like the detail, opened from a session's detail or
from a waiting card's "Answer…" or "Reply…". Items 1 to 3, "In the
background" and the cannot-read card are built (increment 2, "As built for
ADR-029 increment 2" above); item 5 without Stop, "Reply…", a send's states
and "Off, and why" are built (increment 3, "As built for ADR-029 increment
3" above); Stop is built (increment 4, "As built for ADR-029 increment 4"
above); a question's block and "Answer…" are built (increment 5, "As built
for ADR-029 increment 5" above); a permission prompt's block is planned. Top
to bottom:

1. The freshness line, then the back link "‹ ``<title>``" to the detail,
   44 px.
2. The kicker "``<project>`` · Conversation" (uppercase), the title (22 px
   bold) and the state chips.
3. **The turns**, oldest first, newest at the bottom, 12 px apart. The
   user's turn is a quiet card (``surface-quiet``) under the label "You";
   Claude's text a card (``surface-card``) under "Claude"; a tool call one
   line of 13 px monospace in ``text-tertiary`` with no card, in a
   single-line scroller ("Ran ``git status``"). A text cut at the cap ends
   with "Cut here: the rest is on the Mac". The screen opens scrolled to
   the bottom and stays there as turns arrive, unless the person scrolled
   up; then a pill, "New messages", floats above the composer and scrolls
   down.
4. **What Claude is waiting on** (inc 5, built for a question; inc 6), the
   last block, with the needs tone (``tile-needs`` ground):

   - A question (built, "As built for ADR-029 increment 5" above): its text
     (17 px semibold), then one 48 px secondary button per choice, full
     width. A multi-select shows its choices as toggles and one primary
     "Send answer". "Other…" opens a 16 px text field and "Send". Several
     questions in one prompt are stacked, and one "Send answer" sends them
     together.
   - A permission prompt: "Claude asks to use ``<tool>``", what it asks in a
     monospace inset block (a scroller), and one 48 px button per choice
     the prompt offers, in its order. Each opens the confirm sheet below.

5. **The composer** (inc 3, built), fixed above the bottom safe area on
   ``surface-sheet``: a text area (16 px, so iOS does not zoom; 44 px tall,
   growing to five lines) and a 44 px round primary Send button. While a
   turn runs, a 44 px round secondary Stop button (a filled square icon,
   inc 4, built) stands beside it, and the placeholder reads "Message Claude
   — sent when this turn ends".

**A send's state** (inc 3, built) shows under its own turn in 13 px, with
the link colour for its one action (a 44 px hit area):

+-----------------------+---------------------------------------------+---------+
| State                 | Line                                        | Tone    |
+=======================+=============================================+=========+
| held (5 s)            | Sending… · Undo                             | idle    |
+-----------------------+---------------------------------------------+---------+
| queued                | Queued: sent when Claude's turn ends ·      | working |
|                       | Cancel                                      |         |
+-----------------------+---------------------------------------------+---------+
| typed, waiting for    | Sent · checking                             | idle    |
| the receipt           |                                             |         |
+-----------------------+---------------------------------------------+---------+
| delivered             | Delivered                                   | idle    |
+-----------------------+---------------------------------------------+---------+
| not confirmed         | Not confirmed — check the session on the    | problem |
|                       | Mac                                         |         |
+-----------------------+---------------------------------------------+---------+
| refused               | Not sent: ``<one plain reason>``            | problem |
+-----------------------+---------------------------------------------+---------+
| dropped               | Dropped, not delivered (the oracle          | problem |
|                       | restarted, or too many were queued)         |         |
+-----------------------+---------------------------------------------+---------+

A held or queued send keeps its text on the page, in memory only, until it
is delivered or gone; a reload forgets it (ADR-009 D17).

**Off, and why** (inc 3, built). Each in place of what it turns off:

- A session the host layer cannot read: one quiet card, "Can't read this
  session here — use the Mac." No composer and no answers; Stop stays while
  a turn runs.
- A window with no Terminal tab (Remote Control, an editor, another
  terminal): the composer is replaced by "Send from here works for Terminal
  windows only." The turns still show.
- A tab where only a shell runs: "Claude is not running in this window."
- **In the background.** Once the page has been hidden for 60 seconds the
  turns are cleared from the screen and from memory; coming back shows
  "Reading again…" until the next read.

Confirm a permission answer (inc 6)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Screen 3's sheet, over the Conversation:

1. The title, the choice's question: "Allow Claude to run this once?",
   "Always allow this?", "Deny this?". The sentence names the session: "In
   ``<project>`` › ``<title>``."
2. What it asks: the tool and its input in a monospace inset block.
3. For an allow-always, an inset block **Adds this rule**: the rule as the
   prompt words it in monospace, and "Kept in this project's Claude
   settings. Listed under Phone rules, where you can remove it."
4. The deed button, named for the choice: "Allow once", "Always allow",
   "Deny" (primary; Deny secondary), over Cancel, which holds focus, and
   the expiry line. There is no Copy: nothing here is a command.

The result returns to the Conversation, where the answer's state shows as a
send's does.

10 · Phone rules (inc 6)
~~~~~~~~~~~~~~~~~~~~~~~~

Reached from a 64 px list row after **Notifications** on the overview,
"Phone rules" over "N rules added from phones" (no row while there are
none):

1. The back link "‹ Sessions", the title "Phone rules", and "Every 'always
   allow' given from a phone. Rules added at the Mac are not listed here."
2. One 64 px row per rule: the rule in 15 px monospace (a scroller) over
   "``<project>`` · from ``<device>`` · ``<date>``", with a "Remove…" pill.
3. "Remove…" opens screen 4's destructive sheet: "Remove this rule?", **This
   deletes** "The rule ``<rule>`` from ``<project>``'s Claude settings",
   Cancel first, then "Remove rule". A rule already gone at the Mac is
   reported as gone, and its row goes.

Words
~~~~~

The fixed strings still planned join ``panel.WORDS`` when their increment is
built; the built ones are listed under each "As built" section above.

+----------------------------------+-----------------------------------+
| Where                            | Text                              |
+==================================+===================================+
| Waiting card button (inc 6)      | Answer… (a permission)            |
+----------------------------------+-----------------------------------+
| Waiting sentence, replacing the  | Claude asked for permission N min |
| built one (inc 6)                | ago. Answer it here or on the     |
|                                  | Mac.                              |
+----------------------------------+-----------------------------------+
| Permission block (inc 6)         | Claude asks to use ``<tool>``     |
+----------------------------------+-----------------------------------+
| Permission sheet                 | Allow Claude to run this once? ·  |
|                                  | Always allow this? · Deny this? · |
|                                  | Adds this rule · Allow once ·     |
|                                  | Always allow · Deny               |
+----------------------------------+-----------------------------------+
| Phone rules                      | Phone rules · N rules added from  |
|                                  | phones · Remove this rule? ·      |
|                                  | Remove rule                       |
+----------------------------------+-----------------------------------+

Increment plan
--------------

+---+-----------------------+---------------------+---------------------+
| # | Increment             | Session             | Acceptance (from    |
|   |                       |                     | IDEA_027)           |
+===+=======================+=====================+=====================+
| 1 | Plan                  | done                | The decisions are   |
|   |                       |                     | ADR-009's           |
+---+-----------------------+---------------------+---------------------+
| 2 | Mockup, approved      | done                | This page records   |
|   | 2026-10-23            |                     | it                  |
+---+-----------------------+---------------------+---------------------+
| 3 | Plain-language panel: | **shipped**         | First view fits 390 |
|   | ``oracle/panel``, the | (session 80e81a07)  | px and a count      |
|   | groups, strip,        |                     | scrolls to its      |
|   | wording, detail view, |                     | group; no           |
|   | copy, button wording, |                     | default-view string |
|   | no mid-word wrap      |                     | holds a path, argv, |
|   |                       |                     | pid, host UUID or   |
|   |                       |                     | engine state name;  |
|   |                       |                     | each is in          |
|   |                       |                     | Technical details   |
+---+-----------------------+---------------------+---------------------+
| 4 | Home-screen app:      | **shipped**         | Opens standalone    |
|   | manifest, icon,       | (session 80e81a07); | with its icon and   |
|   | standalone, safe      | both iOS checks     | respects safe       |
|   | areas; the two iOS    | verified on an      | areas; still        |
|   | checks of ADR-009 D10 | iPhone 2026-10-25   | refuses an unpaired |
|   |                       |                     | device; loads       |
|   |                       |                     | nothing from        |
|   |                       |                     | another origin      |
+---+-----------------------+---------------------+---------------------+
| 5 | Failure reasons: the  | **shipped**         | A failed action     |
|   | summariser and the    | (session 80e81a07)  | shows one plain     |
|   | result sheet          |                     | sentence; the full  |
|   |                       |                     | output only after   |
|   |                       |                     | "Show full output"  |
+---+-----------------------+---------------------+---------------------+
| — | IDEA_024 (every live  | **shipped**         | their own           |
|   | host's pid) and       | (BUG_078; IDEA_024  | acceptance          |
|   | BUG_078 (true last    | in session          |                     |
|   | activity)             | 53d33984)           |                     |
+---+-----------------------+---------------------+---------------------+
| 6 | Orphan classes and    | **shipped**         | IDEA_025's          |
|   | close host (IDEA_025  | (session d7dfd77d)  | acceptance; refuses |
|   | scope v1): the        |                     | at confirm time on  |
|   | classes, the Close…   |                     | a gone, renamed or  |
|   | pill, the destructive |                     | mismatched pid.     |
|   | sheet,                |                     | IDEA_025's one      |
|   | ``oracle close-host`` |                     | summary line per    |
|   |                       |                     | project in          |
|   |                       |                     | ``oracle list`` and |
|   |                       |                     | the TUI is not      |
|   |                       |                     | built (the web      |
|   |                       |                     | page's leftovers    |
|   |                       |                     | card and screen are |
|   |                       |                     | its summary there)  |
+---+-----------------------+---------------------+---------------------+
| 7 | Clean-up and unstick  | later               | IDEA_017's          |
|   | actions (IDEA_017's   |                     | acceptance; every   |
|   | list)                 |                     | action shows what   |
|   |                       |                     | it runs and         |
|   |                       |                     | destroys, and       |
|   |                       |                     | refuses when its    |
|   |                       |                     | precondition no     |
|   |                       |                     | longer holds        |
+---+-----------------------+---------------------+---------------------+

Every increment keeps: Tailscale only, a paired device token, every action
confirmed first, no path to answer a prompt, auto-refresh with "updated N s
ago" and the stale flag, and every asset inline under the nonce CSP.
ADR-029 (accepted; increment 1, the last message, increment 2, the
conversation, increment 3, sending a message, increment 4, Stop and slash
commands, and increment 5, answering a question, built) changes two of these
for its own path, not for actions, from its increments 3 to 6: a message, an
interrupt and a question answer take one tap with Undo (built), and the page
gains a path that answers prompts (a question's built, a permission prompt's
in increment 6). Its increments are in ADR-029, "Increments".

Open questions
--------------

- **Settled 2026-10-25: the two iOS checks.** On an iPhone the four-tile icon
  appeared on the home screen and the home-screen app kept Safari's pairing,
  so the icon and manifest stay behind the token (ADR-008 D4) and no
  amendment is needed. The "Enter a pairing code" link stays as the way in
  for a device whose app does not share its browser's cookie.
- **"Forget" a gone record.** Not specified by IDEA_025 or IDEA_017. Left
  out until the user asks for it.
- **"Recent".** Settled in increment 3: the events with a recorded time are
  the current host's attention record (the prompt's ``since``, a refusal's and
  a broken hook's ``since``, the turn's ``turn_since``), the session's
  ``plan_touched``, and this device's own actions on the session. A test run
  has no recorded time, so it is not listed. The section is omitted when
  there are none.
