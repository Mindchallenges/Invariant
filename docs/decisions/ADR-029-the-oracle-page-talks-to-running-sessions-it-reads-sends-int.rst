.. _adr-029:

ADR-029 — The oracle page talks to running sessions: it reads, sends, interrupts and answers prompts
====================================================================================================

.. dec:: The oracle page talks to running sessions: it reads, sends, interrupts and answers prompts
   :id: DEC_ADR_029
   :status: accepted
   :amends: DEC_ADR_007, DEC_ADR_008, DEC_ADR_009
   :affects: COMP_ORACLE_PANEL, COMP_ORACLE_WEB, COMP_ORACLE_WEBPAGE, COMP_ORACLE_WEBACTIONS, COMP_ORACLE_SCAN, COMP_ORACLE_ATTENTION, COMP_ORACLE_DEVICES, COMP_ORACLE_NOTIFY, COMP_ORACLE_TALK, COMP_HOSTS_CLAUDE_CODE, COMP_HOSTS_CLAUDE_CODE_LAUNCH, COMP_HOSTS_CLAUDE_CODE_TELEMETRY, COMP_HOSTS_CLAUDE_CODE_TALK, COMP_CMD_ORACLE, COMP_CLI_MAIN

   - **Status:** Accepted, partly implemented (ruling: user, 2026-11-20).
     The rulings below are the user's: the scope of IDEA_060, settled on
     2026-11-19, and the answers to its open questions, given on 2026-11-20.
     The user approved the plan on 2026-11-20, with the numbers it names (a
     card's three lines, the "Show all" fold past 1200 characters, the 2 MiB
     walk-back, the 20 000-character entry cap and the rest), and its
     increments were queued that day. Increment 1, the last-message excerpt
     (D11's first bullet, ADR-009 D17), is built, with D14 to D18 recording
     how; increment 2, the live conversation view (D11's second bullet), is
     built, with D19 to D24 recording how; increment 3, sending a message
     (D1 to D7, and the Needs you row's "Reply…" of D10), is built, with D25
     to D33 recording how; increment 4, the interrupt (Stop) and slash
     commands sent as messages, is built, with D34 to D39 recording how and
     ruling 15 the user's answer on commands that open a menu; increment 5,
     answering a question (D8, and the Needs you row's "Answer…" of D10), is
     built, with D40 to D46 recording how; increments 6 and 7 are queued and
     not built.
   - **Date:** 2026-11-20
   - **Supersedes:** nothing. It amends three accepted records, each in the
     decision that says otherwise, and says so there (D1 to D3, D5, and D10
     to D12):

     - ADR-007 D18, "the oracle never answers a prompt", and ADR-009 D8, "a
       row waiting on a permission prompt has no run button … this panel
       never answers prompts". A paired device may now type into a live
       Claude window and answer what it waits on (D1, D7, D8).
     - ADR-009 D2, the attention groups. The user named it; IDEA_060 labels
       it "every action is confirmed first", which is not what D2 says. D2
       is the grouping of the first screen. What changes in it is the Needs
       you rows that wait on a prompt: they now carry an answer (D10). The
       confirm-first rule lives in ADR-007 D18, ADR-009 D1 and D6 and
       ADR-008 D8 to D10, and ADR-027 ruling 14 used the same "D2" label for
       it. This record amends those decisions where one tap with Undo departs
       from them (D2), and amends D2 for what it does say.
     - ADR-009 D3, whose rule that nothing on the default view carries a
       path or an engine name cannot hold for a session's own text (D11), and
       ADR-009 D16, whose bound on a stolen token said "the oracle answers
       none" (D12).
     - ADR-008 D9 (the client names only a row), D10 (the nonce), and D13
       (the audit line), for the talk path's own forms of each (D2, D5).
     - ADR-007 D5, the attention sidecar's fields, which gain the receipt's
       hash (D3).

     It also writes ADR-009 D17, the transcript decision IDEA_029 reserved
     there (:need:`DEC_ADR_009_D17`), and leaves ADR-027 D28's chain nudge as
     it is, sharing only the typist's lock with it (D6).

Context
-------

The user drives Claude sessions from the phone through Claude Code's Remote
Control, which needs a claude.ai login on the device. Without that login
there is no way to read or answer a running session from the phone. The
oracle's page (ADR-008, ADR-009) shows each session's state and never its
conversation, and it has no route into a session on purpose: ADR-007 D18 and
ADR-009 D8 say the oracle never answers a prompt, and ADR-008 D8 says no
route can reach one. The only text the oracle has ever typed into a session
is the chain's one "continue" for a stalled chained session (ADR-027 D28),
through ``TerminalTyper`` in ``hosts/claude_code_launch``.

On 2026-11-19 the user asked for the page to "communicate with the running
sessions, basically similar to what I can do with Claude Code remote control,
but just over the web", and settled its scope with IDEA_060 the same day.
IDEA_060 takes in IDEA_029's Read half (a session's last message on its card
and detail, and the transcript decision ADR-009 reserved as D17) as its
first increment; IDEA_029 keeps its Hand-off half.

Typing into a terminal is the oracle's riskiest power yet: a keystroke can
reach the wrong tab, answer a prompt that changed, or collide with the
person typing on the Mac. IDEA_060 asked that a misdelivered keystroke be
impossible or detectable, not just unlikely, and that a read the oracle
cannot interpret show nothing rather than something wrong. The user answered
those questions on 2026-11-20 (rulings 9 to 14).

Rulings
-------

Settled with the user on 2026-11-19 (IDEA_060):

.. _adr-029-ruling-1:

**Ruling 1 — why.** No dependence on claude.ai at all. The oracle page is
the main remote route, so it covers what Remote Control is used for, not
only the times Remote Control is unavailable.

.. _adr-029-ruling-2:

**Ruling 2 — reading.** Opening a session shows a live conversation view:
the recent turns (the user's messages and Claude's text replies), tool calls
as one-line summaries, newest at the bottom, refreshing on its own. Cards
show only a last-message excerpt. IDEA_029's Read half, the excerpt and its
transcript decision, is the first increment.

.. _adr-029-ruling-3:

**Ruling 3 — sending.** To any live Claude window the oracle sees:
invariant sessions and orphan windows alike. When Claude waits for input,
the message is delivered at once. Mid-turn, the oracle queues it and
delivers it when the turn ends. A tab where only a shell runs is always
refused.

.. _adr-029-ruling-4:

**Ruling 4 — also in v1.** Interrupting Claude mid-turn (the Esc key), and
slash commands sent as messages (for example ``/compact``). Out of v1:
expanding a tool call's input and output, launching new sessions without
Remote Control, and attachments.

.. _adr-029-ruling-5:

**Ruling 5 — answering.** Both interactive questions (multiple choice,
including the free-text "Other") and permission prompts, with every choice
the prompt offers, "allow always" included. This reverses "This panel never
answers prompts".

.. _adr-029-ruling-6:

**Ruling 6 — confirmation.** Free messages and question answers send on one
tap, with a few seconds of Undo before delivery. A permission answer always
goes through a confirm sheet that names the exact choice and the session.

.. _adr-029-ruling-7:

**Ruling 7 — privacy and record.** The conversation is shown as it is, to
paired devices only, as plain text. The oracle stores none of it beyond the
live read. The audit log records each send's device, session, time, length
and hash, never the text.

.. _adr-029-ruling-8:

**Ruling 8 — priority.** Tier 1, high: built right after IDEA_057's
increment 4 (ADR-027), which has shipped.

Answered by the user on 2026-11-20:

.. _adr-029-ruling-9:

**Ruling 9 — misdelivery and a stale answer: fingerprint and receipt.**
Each send, answer and interrupt is bound to the host id, the pid, the
process start token, the tty and the waiting record's identity (its kind
and since). A fresh read just before typing refuses on any difference.
After typing, the next hook (prompt submitted, prompt answered) must arrive
carrying the matching hash within a few seconds; otherwise the send shows
"not confirmed" and alerts the user. Never an automatic retry, which would
risk double delivery.

.. _adr-029-ruling-10:

**Ruling 10 — typing on the Mac: hold, then refuse.** A due send waits until
the tab has been quiet for a few seconds. If it stays busy past about a
minute, it is refused with "Someone is typing on the Mac", and nothing is
typed.

.. _adr-029-ruling-11:

**Ruling 11 — Claude Code's transcript and prompt UI drift: fail closed per
session.** The host layer recognises the shapes it knows. Anything it cannot
read shows "Can't read this session here — use the Mac"; send and answer are
off for that session, and interrupt stays. ``doctor`` flags the drift with
the Claude Code version. Never show something wrong.

.. _adr-029-ruling-12:

**Ruling 12 — "allow always" from the phone: revocation plus a phone-rule
list.** Every "allow always" given from the phone is recorded (device,
session, rule) and listed on the page and in the CLI, each removable. The
confirm sheet for an allow-always names the exact rule it adds. Device
revocation stays the main control.

.. _adr-029-ruling-13:

**Ruling 13 — secrets in the shown conversation.** No masking, and no
storage anywhere: served with ``no-store`` so the browser keeps no copy,
never put in a web push payload or a notification, dropped from the page
after it has been in the background for a while. The server holds only the
live read.

.. _adr-029-ruling-14:

**Ruling 14 — the mid-turn queue.** Memory only, in a bounded ring buffer,
so heavy traffic cannot grow memory without bound. A server restart drops
the queued sends, and the page shows each as "dropped, not delivered". The
conversation itself is pulled on demand from the host's transcript whenever
the user opens a session or reloads, and never retained.

Answered by the user on 2026-11-20, during increment 4:

.. _adr-029-ruling-15:

**Ruling 15 — a slash command that opens a menu: refused at the tap.** A
slash command Claude Code opens a menu for when it is given no arguments is
refused at the tap with "This command opens a menu — use it on the Mac." The
list of such commands lives in the host layer with the Claude Code version it
was seen on. Every other slash command is typed. Its receipt is the
transcript's echo or the plain entry; ``/clear`` is confirmed by the new
session in the same process, and ``/exit`` by the process ending.

Decisions recorded
------------------

.. dec:: Typing into a live Claude window is a second power beside the confirmed action
   :id: DEC_ADR_029_D1
   :status: accepted
   :part_of: DEC_ADR_029
   :amends: DEC_ADR_007_D18, DEC_ADR_009_D1, DEC_ADR_009_D8, DEC_ADR_008_D8
   :affects: COMP_ORACLE_WEBACTIONS, COMP_ORACLE_ACTIONS, COMP_ORACLE_WEB, COMP_HOSTS_CLAUDE_CODE_LAUNCH

   Rulings :ref:`3 <adr-029-ruling-3>`, :ref:`4 <adr-029-ruling-4>` and
   :ref:`5 <adr-029-ruling-5>`.

   Until now the oracle had one power that changes anything: the confirmed
   action, an ``invariant`` argv run with no shell (ADR-007 D18, ADR-009 D1).
   This record adds a second, **session input**: what a paired device asked
   to be typed into one live Claude window. It is a separate path with its
   own module (``oracle/talk``, D4), its own routes (D5) and its own audit
   lines, and it never runs a program.

   - **What may be typed** is one of four inputs and nothing else: a
     ``message`` (free text, which includes a slash command, ruling 4), an
     ``interrupt`` (the Esc key), a ``question`` answer (a choice, several
     for a multi-select, or "Other" with its text) and a ``permission``
     answer (one of the choices the prompt offers). No input is ever a shell line, and the text is
     data: it is handed to the host layer as an argument and reaches the tab
     as typed characters, never as part of a script or a command.
   - **Where.** A live Claude window the fresh scan verifies: a session's
     current host or an orphan host, with a pid from Claude Code's record or
     an ``attention+ps`` pid (IDEA_024, BUG_099), a start token and a tty,
     running in a Terminal.app tab the host layer finds by that tty. A tab
     where only a shell runs is refused (``no_claude``, as the nudge's). A
     window with no tty (Remote Control only, an editor) or in another
     terminal application can be read (D11) and not typed into; its detail
     says "Send from here works for Terminal windows only". A ``conflict``
     row, a gone host and a host of unknown liveness offer nothing.
   - **Who.** A paired device (ADR-008 D3, D4), over the tailnet only
     (ADR-008 D1). Nothing is typed on a signal of the oracle's own; the
     chain's nudge stays ADR-027 D28's and is not this path.
   - **What stays.** The confirmed action is unchanged: ``refusal`` still
     refuses a runnable step on a row that waits on a prompt, and no
     ``invariant`` argv can answer one. ADR-008 D8's sentence "nothing on the
     page can answer a permission prompt: there is no route to one" holds for
     the action routes and no longer for the page: the talk routes are that
     route, under D3 and D9.

.. dec:: One tap with Undo for messages, answers to questions and interrupts; a confirm sheet for permission answers
   :id: DEC_ADR_029_D2
   :status: accepted
   :part_of: DEC_ADR_029
   :amends: DEC_ADR_007_D18, DEC_ADR_009_D1, DEC_ADR_009_D6, DEC_ADR_008_D9, DEC_ADR_008_D10
   :affects: COMP_ORACLE_PANEL, COMP_ORACLE_WEBPAGE, COMP_ORACLE_WEBACTIONS

   Ruling :ref:`6 <adr-029-ruling-6>`.

   **This departs from confirm-first, and says so plainly.** ADR-007 D18 made
   every action two steps ("an action is never one key"), ADR-009 D1 and D6
   kept that on the phone with a sheet that shows the command, and ADR-008
   D9 and D10 made the server derive what runs and spend a nonce issued with
   the confirm. A message, a slash command, a question answer and an
   interrupt now take one tap and no sheet:

   - **Undo, held on the server.** The tap posts the input, and the server
     holds it for 5 seconds before it is due. The page shows it as "Sending…
     Undo" for those seconds; Undo cancels it on the server and nothing is
     typed. A page that closes or loses the connection in that time does not
     cancel it: the tap was the decision, and the window is for changing
     one's mind. Nothing reaches the session before the window ends.
   - **The interrupt** takes the same one tap and the same Undo. It stops a
     turn, which the person can resume with a message; it deletes nothing.
   - **A permission answer always goes through a confirm sheet**, as an
     action does: ADR-009 D6's bottom sheet, naming the session, the tool and
     what it asks, and the exact choice ("Allow this once", "Allow always:
     ``<the rule>``", "Deny"), with a nonce bound to the device, the row, the
     choice and the fingerprint (D3), good for 120 seconds, spent by the
     first answer that presents it (ADR-008 D10's rules). It has no Undo
     window: the sheet was the second step, and delivery is due at once.
   - **What ADR-008 D9 still means here.** The client names the row and the
     input, and the server derives the target: which host, pid, start and
     tty, from its own fresh scan. The text of a message or of an "Other"
     answer is the one thing a client sends that is used as sent, as data. A
     choice is named by its index in what the server read, and an index the
     fresh read does not offer is refused.
   - **The one-tap nonce.** Every one-tap input carries a talk nonce, issued
     with the conversation read (D5), bound to the device, the row and the
     fingerprint that read saw. One is live per device and row, replaced by
     each read, good for 120 seconds, spent by the first input that presents
     it. ADR-008 D10's ``Origin`` and ``Sec-Fetch-Site`` rules and its body
     form apply unchanged, with a body of at most 32 KiB and a message of at
     most 8000 characters.

   *Rejected:* a confirm sheet for every message. The user chose one tap
   with Undo (ruling 6); a sheet per message is the friction Remote Control
   does not have. *Rejected:* Undo on the device only. A page that dies
   could neither undo nor deliver, and a delay kept on the phone would let
   the phone's clock decide what the Mac types.

.. dec:: A fingerprint checked just before typing, and a receipt the next hook must carry
   :id: DEC_ADR_029_D3
   :status: accepted
   :part_of: DEC_ADR_029
   :amends: DEC_ADR_007_D5
   :affects: COMP_ORACLE_ATTENTION, COMP_HOSTS_CLAUDE_CODE, COMP_ORACLE_WEBACTIONS, COMP_CLI_MAIN

   Ruling :ref:`9 <adr-029-ruling-9>`.

   - **The fingerprint** is the host id, the pid, the process start token,
     the tty, and the waiting identity: the attention record's ``waiting``
     kind and ``since``, or, for a host that is not waiting on anything, its
     ``turn`` and ``turn_since``. The read that showed the person the session
     records it; the input carries it (through the nonce, D2); the server
     reads it again from a fresh scan and the host's facts (``ps``, the
     per-process record) **just before typing**, after the Undo window and
     after the quiet wait (D6). Any difference refuses, and nothing is typed:
     "The session changed since you looked: <what changed>". A question or a
     permission answer is refused the same way when the prompt it answers is
     no longer the one waiting ("Claude is no longer waiting on that
     question").
   - **The receipt.** After typing, the host's next hook must carry the
     input's hash within 5 seconds: ``UserPromptSubmit`` for a message, the
     answered tool's hook for a question or a permission answer. The hook
     side records, on the attention sidecar, ``received``: ``{kind, hash,
     at}``, the SHA-256 of the text as Claude Code reports it, never the
     text. This amends ADR-007 D5's list of what the sidecar holds. The host
     layer names, per input kind, which hook or transcript entry is the
     evidence and how its text is normalised before hashing; where Claude
     Code fires no hook for an input (a built-in slash command, an
     interrupt), the evidence is the transcript's next entry for that input,
     read the same way. Matching evidence is "Delivered".
   - **Not confirmed** is a send with no matching evidence in the window,
     or evidence carrying another hash. It is never sent again. The page
     shows "Not confirmed — check the session on the Mac" on that send, and
     the watcher's notifier raises one alert (Mac banner and push, ADR-027
     ruling 17) naming the session and "a message from the phone was not
     confirmed", never its text.
   - **Why a hash in the sidecar is no new exposure.** The sidecar lives in
     the project's state root, never committed, on the Mac whose transcript
     already holds the full text. A short answer's hash ("yes") can be
     guessed; that guess reveals nothing the transcript beside it does not.
   - *Rejected:* a retry after "not confirmed". A keystroke that landed
     without a receipt (a hook that failed) would be typed twice (ruling 9).
     *Rejected:* trusting the transcript alone, with no hook. Claude Code
     writes the transcript after the turn starts, often seconds later; the
     hook is immediate and is the shape the user chose.

.. dec:: The oracle defines the talk seams; Claude Code's reading and typing live in the host layer
   :id: DEC_ADR_029_D4
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_TALK, COMP_ORACLE_WEB, COMP_ORACLE_WEBACTIONS, COMP_HOSTS_CLAUDE_CODE_TALK, COMP_HOSTS_CLAUDE_CODE_LAUNCH, COMP_HOSTS_CLAUDE_CODE_TELEMETRY, COMP_CLI_MAIN

   ADR-007 D11's shape again: the oracle names what it needs, a host module
   supplies it, and the CLI joins the two. Nothing in ``oracle/`` imports
   ``hosts/``; only the CLI imports the oracle.

   - **``oracle/talk``** (new) owns the host-neutral part: the input kinds,
     the fingerprint and its comparison, the Undo hold, the mid-turn queue
     (D7), the delivery loop, the receipt wait, the talk nonces, and the
     ``SessionTalk`` protocol it is handed. It reads the scan and the
     attention store, and it writes nothing but its audit lines (D5).
   - **``SessionTalk``**, the seam, has these methods; each answers a
     host-neutral value or ``Unreadable(reason, version)``:

     - ``conversation(host, after)``: the recent turns as entries (role,
       text, a tool call's one-line summary, the entry's time), D11's caps
       applied by the reader;
     - ``pending(host)``: the question (its questions and choices, whether
       it is multi-select, whether it has "Other") or the permission prompt
       (the tool, what it asks, each choice in order, and for an
       allow-always the rule as the prompt names it) the host waits on now;
     - ``quiet(host)``: whether the tab has been unchanged and its input line
       empty, D6;
     - ``deliver(host, input)``: type it, or refuse with a reason
       (``no_claude``, ``no_tab``, ``not_allowed``, ``unknown_shape``);
     - ``receipt(host, input, since)``: the evidence of D3;
     - ``rules(project)`` and ``remove_rule(project, rule)``: the
       allow-always rules Claude Code keeps for a project, D9.

   - **``hosts/claude_code_talk``** (new) implements it for Claude Code: the
     transcript JSONL read (through ``claude_code_telemetry``'s tail reader,
     which already returns a turn's role and text), the reading of a pending
     question from the ``AskUserQuestion`` call in the transcript, the
     reading of a permission prompt from the tab's visible contents together
     with the pending tool call, the keystrokes each answer is (through
     ``claude_code_launch``'s ``TerminalTyper``, extended to send named keys
     as ``on run argv`` arguments, never text inside a script), the receipt,
     and Claude Code's permission settings files. Every shape it knows is a
     named pattern with the Claude Code versions it was seen on; anything
     else is ``Unreadable`` (D11).
   - **The CLI joins them** through one seam, ``_oracle_talk``, which ``oracle
     web`` hands to the server, and which the suite replaces for every test
     with a recorder that types nothing. The ``osascript`` guard in
     ``conftest.py`` stays the backstop.

.. dec:: Talk routes beside the action routes, and audit lines without the text
   :id: DEC_ADR_029_D5
   :status: accepted
   :part_of: DEC_ADR_029
   :amends: DEC_ADR_008_D13
   :affects: COMP_ORACLE_WEB, COMP_ORACLE_WEBACTIONS, COMP_ORACLE_DEVICES

   Ruling :ref:`7 <adr-029-ruling-7>`.

   - ``GET /api/session?row=<key>[&after=<cursor>]``: the conversation
     (D11), the pending question or prompt, whether send, answer and
     interrupt are offered now and why not, this device's sends to the row
     with their states, and a talk nonce (D2).
   - ``POST /api/talk``: ``{row, nonce, kind, text | choice}``; 202 with a
     send id. ``POST /api/talk/cancel``: ``{id}``, the Undo or the removal of
     a queued send, by the device that made it.
   - ``GET /api/talk/confirm?row=<key>&choice=<n>``: a permission answer's
     sheet and its nonce (D2).
   - ``GET /api/rules`` and ``POST /api/rules/remove``: the phone-rule list
     and a removal, the latter through the confirm sheet (D9).
   - All behind the token (ADR-008 D4), with the ``Origin`` rules of an
     action for every ``POST``; a refusal is a 409 with one plain reason.
   - **Audit.** Each send appends JSON lines to ``web/actions.log``, the log
     ADR-008 D13 keeps, with ``event`` one of ``talk-held``, ``talk-queued``,
     ``talk-typed``, ``talk-delivered``, ``talk-not-confirmed``,
     ``talk-cancelled``, ``talk-dropped`` and ``talk-refused``, and ``at``,
     ``send``, ``device`` and ``device_name``, ``row``, ``host``, ``kind``,
     ``length`` (characters), ``hash`` (SHA-256 of the text as typed) and,
     for a refusal, the reason's code. Never the text, a choice's label or a
     rule's text: a permission answer records the choice's index and kind.
     "No record, no action" holds: a ``talk-held`` line that cannot be
     written refuses the send (503).

.. dec:: Hold while the Mac is in use, then refuse; one typist per tab
   :id: DEC_ADR_029_D6
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_WEBACTIONS, COMP_HOSTS_CLAUDE_CODE_LAUNCH, COMP_ORACLE_HOME

   Ruling :ref:`10 <adr-029-ruling-10>`.

   - **Quiet.** A due input waits until ``quiet(host)`` has answered quiet for
     3 seconds: the tab's visible contents unchanged across reads half a
     second apart, and Claude Code's input line empty. Text the person left
     half-typed on the Mac counts as busy, because typing would join it.
   - **Refused after a minute.** Busy for 60 seconds from when the input
     fell due is a refusal, "Someone is typing on the Mac", and nothing is
     typed. A queued send refused this way is not queued again.
   - **A quiet answer the host cannot give** (it cannot read the tab) is
     ``Unreadable``, and send and answer are off for that session (D11). The
     interrupt does not wait for quiet: Esc is the one input that cannot
     join text, and stopping a turn is its point.
   - **One typist per tab.** Every typing into a tab, the talk path's and
     the chain's nudge (ADR-027 D28) alike, takes ``locks/type-<tty>.lock``
     in the oracle home for the fingerprint check, the typing and the half
     second after it. The web server and the watcher are separate processes,
     so the lock is a file lock. The nudge is otherwise unchanged.

.. dec:: Mid-turn sends wait in a bounded ring in memory and go out one per turn end
   :id: DEC_ADR_029_D7
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_WEBACTIONS, COMP_ORACLE_WEBPAGE

   Rulings :ref:`3 <adr-029-ruling-3>` and :ref:`14 <adr-029-ruling-14>`.

   - A message whose Undo window ends while the host's turn runs is queued.
     The queue is the server's memory only: at most 8 sends per host and 64
     across the server, in a ring. A send past either bound drops the oldest
     queued one of that host (or of the server), which the page shows as
     "Dropped, not delivered", with a ``talk-dropped`` audit line.
   - A delivery loop in the web server checks each host with a queued send
     once a second, reading only its attention record and its pid. When the
     turn has ended and the host waits for input, the oldest send is due: D3's
     fingerprint, D6's quiet, typing, D3's receipt. The next queued send waits
     for the turn that one starts to end. A queued send can be cancelled
     until it is due.
   - The fingerprint a queued message carries is the turn's (``turn`` and
     ``turn_since`` when it was queued). Delivery checks the host, pid, start
     and tty against it and that the turn it waited for has ended, not that
     no other turn ran: the person may answer at the Mac while it waits, and
     a message meant for "when the turn ends" is still meant then.
   - An interrupt and an answer are never queued: an interrupt is typed at
     once, and an answer is refused unless its prompt is waiting now.
   - **A restart drops the queue.** The server reports a boot id with every
     ``/api/state``. A page holding a send under an older boot id shows it
     "Dropped, not delivered: the oracle restarted". The audit log keeps its
     ``talk-queued`` line with no delivery after it.

.. dec:: A question is answered by its choice; "Other" is a message to the question
   :id: DEC_ADR_029_D8
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_PANEL, COMP_ORACLE_WEBPAGE, COMP_HOSTS_CLAUDE_CODE_LAUNCH

   Rulings :ref:`5 <adr-029-ruling-5>` and :ref:`6 <adr-029-ruling-6>`.

   - ``pending`` reads the question from the transcript's pending
     ``AskUserQuestion`` call: each question, its choices in order, whether it
     is multi-select, and that "Other" is offered. Several questions in one
     call are answered together, in one send.
   - The page draws the choices as buttons (a multi-select as toggles and one
     "Send answer"), and "Other" opens a text field. One tap, Undo (D2).
   - The host layer turns the answer into the keys the prompt takes, from
     the shape it recognised. A shape it does not know is ``Unreadable``, and
     the question shows "Can't read this question here — use the Mac".
   - The waiting identity in the fingerprint (``question`` and its
     ``since``) refuses an answer to a question that has been answered or
     replaced, as acceptance asks.

.. dec:: A permission answer names its choice on a sheet; every allow-always from the phone is listed and removable
   :id: DEC_ADR_029_D9
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_PANEL, COMP_ORACLE_WEBACTIONS, COMP_ORACLE_DEVICES, COMP_CMD_ORACLE, COMP_HOSTS_CLAUDE_CODE

   Rulings :ref:`5 <adr-029-ruling-5>`, :ref:`6 <adr-029-ruling-6>` and
   :ref:`12 <adr-029-ruling-12>`.

   - ``pending`` reads the prompt: the tool and its input from the pending
     tool call in the transcript, and the choices from the prompt in the tab,
     in order, as the host layer recognises them. Every choice is offered,
     "allow always" included; one the host layer cannot name exactly is
     ``Unreadable`` for the whole prompt, so the page never offers a choice it
     would mis-describe.
   - **The confirm sheet** (D2) leads with the question ("Allow Claude to run
     this?"), the session, the tool and what it asks in a monospace block, and
     the choice as its deed button. For an allow-always it names the rule
     the choice adds, as the prompt words it, and says where Claude keeps it
     ("Saved in this project's Claude settings; listed under Phone rules").
   - **The phone-rule list.** ``<oracle home>/web/phone-rules.json`` is a
     record beside ``devices.json``: owner-only from creation, under its own
     lock, never committed. Each entry is ``{id, device, device_name,
     project, session, host, rule, settings, at}``, where ``rule`` is the
     exact rule string Claude Code wrote and ``settings`` which settings file
     holds it. The exact string comes from the receipt: the host layer reads
     the project's permission settings before and after delivery, and the
     one rule that appeared is the entry. None or more than one appearing is
     "not confirmed" (D3), with the files' rules recorded as found.
   - **Listed and removable.** The page's Phone rules screen and ``invariant
     oracle web rules`` list the entries; ``invariant oracle web rules remove
     <id>`` and the page's "Remove…", a confirm sheet, remove the rule from
     the settings file through the host layer and then the entry. A rule the
     person already removed at the Mac is reported as gone and its entry
     dropped. A rule added at the Mac is never listed: the list is the
     phone's.
   - **Revocation stays the main control.** ``oracle web revoke <id>`` drops
     the device's queued sends at once and prints the phone rules that device
     added, each with its ``rules remove`` line. It does not remove them on
     its own: a rule the person has come to rely on is theirs to drop.

.. dec:: The Needs you rows that wait on a prompt carry their answer
   :id: DEC_ADR_029_D10
   :status: accepted
   :part_of: DEC_ADR_029
   :amends: DEC_ADR_009_D2, DEC_ADR_009_D8
   :affects: COMP_ORACLE_PANEL, COMP_ORACLE_WEBPAGE

   ADR-009 D2's groups, order and strip are unchanged. A Needs you row that
   waits on a question or a permission prompt, or for input, now has a
   primary button, "Answer…" or "Reply…", that opens the session's
   conversation at its pending prompt; the card's note becomes the prompt's
   first line in plain text. ADR-009 D8's "this panel never answers prompts"
   is retired, and its other half stands: ``recommend`` still offers no step
   to run on such a row, and ``refusal`` still refuses one. A session whose
   prompt is ``Unreadable`` keeps D8's card, with "Can't read this prompt
   here — use the Mac" as its note.

.. dec:: The conversation and the excerpt: read on demand, capped, plain text, never stored; fail closed
   :id: DEC_ADR_029_D11
   :status: accepted
   :part_of: DEC_ADR_029
   :amends: DEC_ADR_009_D3
   :affects: COMP_ORACLE_PANEL, COMP_ORACLE_WEB, COMP_ORACLE_WEBPAGE, COMP_ORACLE_SCAN, COMP_HOSTS_CLAUDE_CODE_TELEMETRY, COMP_CMD_ORACLE

   Rulings :ref:`2 <adr-029-ruling-2>`, :ref:`11 <adr-029-ruling-11>`,
   :ref:`13 <adr-029-ruling-13>` and :ref:`14 <adr-029-ruling-14>`. The
   exposure itself is ADR-009 D17 (:need:`DEC_ADR_009_D17`); this decision
   is how the page reads and draws it.

   - **The excerpt** (increment 1). ``panel.view`` is handed, from the web
     server's own read, each live host's last text: the newest ``assistant``
     entry with text, walking back within the tail reader's 2 MiB budget, and
     the user's last prompt above it when the same read has it. A card shows
     the excerpt in at most three lines; the detail shows it whole up to the
     cap, folded behind "Show all" past 1200 characters. A row with none shows
     nothing, not a placeholder. The excerpt is never in the ``assess``
     document, so never in the watcher's snapshot, ``oracle list`` or the
     TUI.
   - **The conversation** (increment 2). Opening a session reads its host's
     transcript on demand: the last 40 entries, each text cut at 20 000
     characters with "Cut here: the rest is on the Mac", a tool call as one
     line of at most 160 characters ("Ran ``git status``", "Edited
     ``src/…/web.py``"), newest at the bottom. While it is open and visible
     the page asks again every 2 seconds with ``after``, so a new turn
     appears without a reload. A response is at most 512 KiB.
   - **Not ADR-009 D3's words.** The excerpt and the conversation are the
     session's own text, not labels, so they are outside the wording table
     and outside D3's check that the default view holds no path, argv, pid or
     engine name. They are drawn in their own blocks, marked as Claude's or
     the user's; every label around them still comes from ``panel.WORDS``.
   - **Fail closed, per session** (ruling 11). The host layer reads only the
     shapes it knows. An entry it cannot read stops the read at that point
     and the conversation shows "Can't read this session here — use the Mac"
     in place of anything after it; an unreadable pending prompt turns off
     send and answer for that session, and interrupt stays. A partial last
     line is not unreadable: it is the line being written, and it is left
     out. ``doctor`` adds an ``oracle talk`` row, a warning, never a
     failure, naming the Claude Code version and what it could not read, from
     the web server's report (``oracle/web.json``).

.. dec:: What a stolen paired token can now do
   :id: DEC_ADR_029_D12
   :status: accepted
   :part_of: DEC_ADR_029
   :amends: DEC_ADR_009_D16
   :affects: COMP_ORACLE_DEVICES, COMP_ORACLE_WEB, COMP_CMD_ORACLE

   ADR-009 D16 bounded a stolen token: it could run recommended steps, close
   leftover windows and open new Claude windows, "and the oracle answers
   none" of their permission prompts. That last bound is gone. With this
   record a stolen token, still reached only over the tailnet (ADR-008 D1),
   can also:

   - read the recent conversation of every live Claude window the oracle
     sees, secrets the person typed included (no masking, ruling 13);
   - type messages and slash commands into any live Claude window in a
     Terminal tab, and so ask Claude to do anything Claude may do under the
     permissions it has;
   - answer Claude's permission prompts, "allow always" included, which
     widens what Claude may do without asking in that project.

   It is bounded as follows:

   - **No shell.** Only a tab where Claude runs is typed into; a shell-only
     tab is refused, and the input reaches Claude as Claude's input, never a
     shell's. A start (ADR-009 D16) still never carries a permission-mode or
     bypass flag.
   - **Seen.** Every input is an audit line with its device (D5). Every
     allow-always is on the phone-rule list with its device, removable (D9).
     A send also shows in the session itself, as the user's turn, to anyone
     at the Mac or in Remote Control.
   - **Nothing kept to steal later.** The conversation is not stored on the
     Mac by the oracle nor in the browser (ADR-009 D17); a stolen token reads
     only what is live while it is valid.
   - **One revocation ends it.** ``oracle web revoke`` ends the token, drops
     the device's queued sends and lists its phone rules for removal (D9).
     The person at the Mac stays able to refuse a prompt first and to stop a
     turn.

   The user chose revocation as the main control (ruling 12); this record
   adds no second factor and no per-send approval on the Mac.

.. dec:: The talk path ships in seven increments, the read first
   :id: DEC_ADR_029_D13
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_PANEL, COMP_ORACLE_WEBACTIONS, COMP_HOSTS_CLAUDE_CODE

   See "Increments" below. Each increment ships on its own and keeps every
   earlier one working. Fail-closed reading ships with each reader, never
   after it: increment 7 adds only ``doctor``'s view of drift. The design of
   every new screen and string is built from the panel's existing tokens and
   components and written into :doc:`oracle-panel.md
   </architecture/oracle-panel>` ("Planned: talking to sessions"), as
   ADR-027 ruling 16 did for the chain; the user reviews it with this plan.

Decisions recorded with increment 1
-----------------------------------

Increment 1 settled what the plan left to the build. Each is inferred from
the rulings and the decisions above, not asked.

.. dec:: The last message is Claude's newest text and the prompt it answers
   :id: DEC_ADR_029_D14
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_TALK, COMP_ORACLE_PANEL

   D11's excerpt is "the newest ``assistant`` entry with text, … and the
   user's last prompt above it". ``oracle/talk.last_message`` reads it as the
   newest prompt **before** that text: the one Claude's text answers. A
   prompt sent after it (a turn under way) is not shown above it as its
   context, since "You" above "Claude" reads as the question and its answer.
   With no prompt in the read, Claude's text shows alone; with no text from
   Claude, the row shows nothing (D11). The oracle cuts each text at ADR-009
   D17's 20 000 characters itself as well, whatever the host's reader did.

.. dec:: A user entry Claude Code wrote itself is not the person's prompt
   :id: DEC_ADR_029_D15
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_HOSTS_CLAUDE_CODE_TALK

   Claude Code writes ``user`` entries the person never typed: a tool's
   result, its own notes (``isMeta``), a compaction summary, an entry kept
   only in the transcript, a background task's notice and a local or slash
   command's echo, which open with one of its own tags
   (``<task-notification>``, ``<command-name>``, ``<local-command-stdout>``
   and the rest). ``hosts/claude_code_talk`` skips them, as it skips a
   subagent's entry (``isSidechain``), bookkeeping records and an entry with
   no text: none is "You", and none is the conversation. A slash command the
   person typed is therefore not shown as "You" in increment 1; how a slash
   command reads in the conversation is increment 4's (D2, D3).

.. dec:: The read stops at the first shape it does not know, and keeps only what is newer
   :id: DEC_ADR_029_D16
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_HOSTS_CLAUDE_CODE_TALK, COMP_ORACLE_TALK

   D11's fail-closed rule, made exact for a read that walks backwards. The
   known shapes are named in the host module: content a string or a list of
   the blocks Claude Code 2.1 writes (``text``, ``thinking``,
   ``redacted_thinking``, ``tool_use``, ``server_tool_use``,
   ``web_search_tool_result`` from Claude; ``text``, ``tool_result``,
   ``image``, ``document`` from the person). A complete line that is not
   JSON, not an object, a turn with no message, content of another type, an
   unknown block, a text block that is not text, or a shown entry with no
   time stops the read there, as ``Unreadable`` with the reason and the
   entry's Claude Code version. The entries already read, which are newer,
   stand; nothing older is read, so an older text is never shown as the
   newest. A partial last line is the line being written and is left out,
   not unreadable (D11). For the excerpt, a stop before any text from Claude
   is no excerpt, never a placeholder.

.. dec:: A window's transcript is found by its host id, and an unchanged one is not read again
   :id: DEC_ADR_029_D17
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_HOSTS_CLAUDE_CODE_TALK, COMP_ORACLE_WEB, COMP_CLI_MAIN

   The seam names a window by ``TalkHost(host_id)``. Claude Code's reader
   finds ``<host id>.jsonl`` in any directory under its home's
   ``projects/``, not only the directories of the project the oracle filed
   the window under, so a window rooted in another repository (BUG_040)
   still shows its own text. An id that could name any other file (anything
   but letters, digits and dashes, or longer than 128) reads nothing. Each
   read is kept in memory against the transcript's size and modification
   time, for at most 64 transcripts, so a page refreshing every few seconds
   reads a quiet window once; this is the "transcript reader's in-memory
   tail" ADR-009 D17 allows, and nothing is written. The web server reads
   only the windows whose liveness is ``True``, the current one of each
   session row and each orphan host row, each on its own: one that fails
   shows nothing and costs the others nothing. The CLI's ``_oracle_talk`` is
   read-only in this increment, like ``_oracle_cache``, and a test that
   serves the state from the app ``oracle web`` built replaces it; the
   suite-wide recorder D4 names arrives with ``deliver`` in increment 3.

.. dec:: Where the excerpt is drawn: full and compact cards, a leftover window's row, and the detail
   :id: DEC_ADR_029_D18
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_PANEL, COMP_ORACLE_WEBPAGE

   The design of record (:doc:`oracle-panel.md </architecture/oracle-panel>`,
   "As built for ADR-029 increment 1") draws it on the full card (three
   lines), the compact card (one line) and the detail. A leftover window's
   row on the Leftovers screen is where a quiet orphan is closed, and the
   person reads its last message to decide that (IDEA_029), so it carries
   the compact card's one line too. A card carries at most the first 600
   characters, enough for three lines on any phone; the line clamp decides
   what shows. The text keeps its line breaks and may break a word too long
   for the line, unlike the panel's own words, which never break mid-word.
   The page's drop after 60 seconds in the background (ADR-009 D17) is
   increment 2's, with the conversation it guards, as the increments table
   has it.

Decisions recorded with increment 2
-----------------------------------

Increment 2 settled what the plan left to the build. Each is inferred from
the rulings, the decisions above and the design of record
(:doc:`oracle-panel.md </architecture/oracle-panel>`, "9 · Conversation"), not
asked.

.. dec:: One host read serves the excerpt and the conversation; the screen shows the newest 40
   :id: DEC_ADR_029_D19
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_HOSTS_CLAUDE_CODE_TALK, COMP_ORACLE_TALK

   D11 asks for the conversation's tool calls as one line each, and for the
   excerpt to walk back within the 2 MiB budget to Claude's newest text.
   ``SessionTalk.conversation`` stays the seam's one method and now returns
   Claude's tool calls too, as entries of the role ``tool`` whose text is
   their line (D20). Counting those against D11's 40 entries would lose the
   excerpt behind a long run of tool calls, so the host's read ends once it
   holds 40 entries **and** Claude's newest text, reading on within its byte
   budget and its 512 KiB of text until it has one; ``oracle/talk.turns``
   serves the newest 40 of what was read, each capped again. One read, kept in
   memory as D17 says, answers both the state read's excerpt and the screen.
   The page's ``after`` (D21) is the oracle's, not passed to the host: the
   seam grows nothing in this increment.

.. dec:: A tool call's one line: a verb per Claude Code tool, "Used <name>" for the rest
   :id: DEC_ADR_029_D20
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_HOSTS_CLAUDE_CODE_TALK

   ``hosts/claude_code_talk`` words each ``tool_use`` and ``server_tool_use``
   block by a table of Claude Code's tools: Bash "Ran", Read "Read", Edit,
   MultiEdit and NotebookEdit "Edited", Write "Wrote", Glob and Grep "Searched
   for", WebFetch "Fetched", WebSearch and the server's ``web_search``
   "Searched the web for", Agent and Task "Started an agent:", each with the
   one input field that says what it acted on; TodoWrite is "Updated the
   to-do list" and AskUserQuestion "Asked you a question". Any other tool, a
   Model Context Protocol tool included, or a known one without its field, is
   "Used <name>": less said, never wrong. A path under the entry's own
   ``cwd`` is shown from there; an argument shows its first line only, never a
   line break; the line is at most 160 characters, a path cut in its middle so
   the file name stays ("Edited src/…/web.py") and anything else at its end.
   The line is drawn in monospace, so it carries no quotes. A tool call
   naming no tool, or whose input is not an object, is a shape the module
   does not know and stops the read (D16).

.. dec:: ``after`` names what was served; the same read answers "unchanged"
   :id: DEC_ADR_029_D21
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_WEB, COMP_ORACLE_TALK, COMP_ORACLE_WEBPAGE

   D5 names ``GET /api/session?row=<key>[&after=<cursor>]`` and leaves the
   cursor open. It is a digest of what the last answer served (whether the
   window is live, the turns, whether the read stopped among them). A read
   whose digest equals ``after`` answers ``unchanged: true`` with the head,
   what is offered and the same cursor, and no turns and no text; any other
   read is the whole conversation again, which the page draws in place of
   the old. Appending only what is newer would need entries to have stable
   identities the transcript does not promise, and a stop that appears, or
   turns that fall out of the newest 40, would leave the page showing what
   the read no longer holds. A response is cut to 512 KiB as encoded by
   dropping its oldest turns. The rest of D5's response waits for the
   increment that has it: no talk nonce (D2 issues one for a one-tap input,
   and increment 3 has the first), no sends and no pending prompt; ``offered``
   is send, answer and interrupt all off, with ``why`` one of
   ``reading_only``, ``unreadable`` and ``gone``.

.. dec:: A stop shows its notice above the newer turns; a gone window says so in their place
   :id: DEC_ADR_029_D22
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_TALK, COMP_ORACLE_WEBPAGE, COMP_ORACLE_WEB

   D11 and D16 make it exact: the read stops at an entry it cannot read, the
   newer entries stand, and the notice takes the place of what lies beyond.
   On the screen, oldest first, that is "Can't read this session here — use
   the Mac." as a quiet card at the top, the newer turns under it, never an
   older turn. A stop older than the newest 40 shows nothing, as nothing shown
   depends on it; an entry of a role ``oracle/talk`` does not know stops the
   turns the same way; a read that raises, for any reason, is unreadable as a
   whole, never an error page. A row whose window is no longer live reads
   nothing and shows "The Claude window is gone." in place of the turns, as
   does a row the document no longer has (404). A live window with no turns
   yet shows none and no placeholder, as D11 has it for the excerpt.

.. dec:: "Open conversation" on every detail whose window the page reads
   :id: DEC_ADR_029_D23
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_PANEL, COMP_ORACLE_WEBPAGE

   The design of record puts the 48 px secondary button at the end of the
   detail's Last message. It is drawn wherever the web server reads
   conversations and the row has a live window it would read (``panel``'s
   ``excerpt_hosts``): at the end of Last message, and alone in the same place
   when there is no last message, so a session whose read stopped before any
   text can still be opened to see why. ``/api/session`` serves any row with a
   live window, an orphan's included (ruling 3), but a leftover window's row
   on the Leftovers screen has no detail, and the design reaches the
   Conversation only from a detail (and, with increments 3 and 5, a waiting
   card), so it gains no entry point here.

.. dec:: The screen asks every 2 s while it is open and seen, keeps to the bottom, and drops every text after a minute away
   :id: DEC_ADR_029_D24
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_WEBPAGE

   The page asks ``/api/session`` when the screen opens and 2 seconds after
   each answer, only while the screen is open and the page visible, and at
   once when the page is seen again; a failed read keeps what was shown and
   waits twice as long each time, up to the panel's 60 seconds. It opens
   scrolled to the bottom; a changed read keeps it there when the view was
   within 48 px of the bottom, and otherwise leaves the scroll alone and shows
   the "New messages" pill, which scrolls down and goes away, as does
   scrolling to the bottom. ADR-009 D17's drop covers every session text the
   page holds, not only the conversation: after 60 seconds hidden it clears
   the state read (the view, whose cards carry the excerpts) and the
   conversation from the screen and from memory, keeping only how fresh the
   last read was, and shows "Reading again…" until the next read. A browser
   that suspended the timer has the drop made when the page is seen again.

Decisions recorded with increment 3
-----------------------------------

Increment 3 settled what the plan left to the build. Each is inferred from
the rulings, the decisions above and the design of record
(:doc:`oracle-panel.md </architecture/oracle-panel>`, "9 · Conversation" and
its Words), not asked; D31 was settled by the session that queued the build,
from the design of record.

.. dec:: The seam grows four methods for a message; the oracle reads the receipt itself
   :id: DEC_ADR_029_D25
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_TALK, COMP_HOSTS_CLAUDE_CODE_TALK, COMP_HOSTS_CLAUDE_CODE_LAUNCH, COMP_CLI_MAIN

   D4 names ``quiet``, ``deliver`` and ``receipt``. A message needs these of
   the host, and nothing more:

   - ``quiet(host)`` answers **one look** at the tab: a digest of what it
     shows and whether the input line is empty, why it cannot be typed into
     (``no_tab``, ``no_claude``, ``not_allowed``), or ``Unreadable``.
     ``oracle/talk`` repeats it half a second apart and times D6's three
     seconds and its minute itself, so the timing is the oracle's and one rule.
   - ``deliver(host, text)`` types it and answers ``(code, detail)``.
     ``TalkHost`` gains the window's ``tty``, which typing and looking need;
     the transcript is still found by the host id.
   - ``digest(kind, text)`` is the hash the host's receipt will carry for
     that text, normalised as the host reports it, and ``refuses(kind,
     text)`` the host's reason a text cannot be typed as a message (D26).
   - The receipt itself is read by the oracle: ``received`` lives on the
     attention sidecar, which is the oracle's record (ADR-007 D5), so
     ``oracle/talk`` reads it through an inert store on the row's state root
     and compares it with ``digest``. ``receipt(host, input, since)``, the
     transcript's evidence for an input that fires no hook, arrives with
     increment 4, which has the first such input.

.. dec:: A message is text and line breaks; one that would switch Claude's input mode is refused at the tap
   :id: DEC_ADR_029_D26
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_TALK, COMP_HOSTS_CLAUDE_CODE_TALK, COMP_ORACLE_PANEL

   Refused at the tap, before the hold, with a ``talk-refused`` line where a
   send was made and nothing typed:

   - **Control characters.** A message carries text and line breaks only.
     Any other control character (Esc, Ctrl-C, a tab, which Claude Code reads
     as a key) is refused (400), so a message can never act as a key.
   - **A message starting with "/"** is refused in this increment with
     "Slash commands are sent from the Mac for now." Ruling 4 puts slash
     commands in v1, and D3 makes a built-in command's evidence the
     transcript's next entry, which is increment 4's receipt; typed now, a
     ``/compact`` would read "not confirmed" and raise an alert every time.
     Increment 4 lifted this refusal (D37).
   - **A message starting with "!", "#" or "&"** is refused as a command to
     Claude Code, not a message: Claude Code 2.1 runs a ``!`` line in the
     shell, saves a ``#`` line to memory and sends an ``&`` line to a
     background task. D12's "the input reaches Claude as Claude's input,
     never a shell's" rules the first out, and the others are not messages.
     The characters are the host layer's (``MODE_PREFIXES``); leading blanks
     are not counted.
   - Line breaks travel inside the text, typed as ``TerminalTyper`` types the
     chain's nudge (ADR-027 D28: the line, then a lone Return). A message
     that Claude Code took as two prompts carries another hash in its
     receipt and reads "not confirmed" (D3): detected, never retried.

.. dec:: The receipt is the prompt's normalised hash, new since the typing, within five seconds
   :id: DEC_ADR_029_D27
   :status: accepted
   :part_of: DEC_ADR_029
   :amends: DEC_ADR_007_D5
   :affects: COMP_ORACLE_ATTENTION, COMP_HOSTS_CLAUDE_CODE, COMP_HOSTS_CLAUDE_CODE_TALK, COMP_ORACLE_TALK, COMP_CLI_MAIN

   - **Written by the hook path.** ``UserPromptSubmit``'s signal carries the
     SHA-256 of its ``prompt`` with line endings made ``\n`` and both ends
     trimmed (``claude_code_talk.prompt_digest``), and the CLI's one guarded
     attention write records ``received: {kind: "message", hash, at}`` with
     the turn it starts. Every prompt does, a prompt typed at the Mac
     included, so a send overtaken there reads as another hash. A prompt
     with no text leaves the last receipt as it was.
   - **Matched by the oracle.** A receipt counts when it differs from the one
     the sidecar held just before the typing and its ``at`` is no more than 2
     seconds before the typing (``at`` is to the second). Its hash equal to
     the message's digest is "Delivered"; another hash is "Not confirmed"
     (``other_hash``); none within 5 seconds of the typing is "Not confirmed"
     (``no_receipt``).
   - **The alert** is one notification through the watcher's notifier seam,
     so the Mac banner and web push (ADR-027 ruling 17): "invariant:
     ``<project>``", the session's title, and "A message from the phone was
     not confirmed". Never the text, and never a second send.
   - A typer that failed may have typed: its send waits for the receipt like
     a typed one, with ``reason: failed`` on its ``talk-typed`` line.

.. dec:: A message is typed only while Claude waits for input; a turn's end is read from the record, the fresh scan decides
   :id: DEC_ADR_029_D28
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_TALK, COMP_ORACLE_WEBACTIONS

   Typing a message into a question or a permission prompt would answer it
   (a Return picks the highlighted choice), so "waits for input" is exact:

   - **Ready** is Claude Code's own status, where its per-process record
     vouched for the pid (BUG_087): ``idle`` or ``shell``; ``busy`` and
     ``waiting`` (a prompt is open) are not. Otherwise the attention record:
     its turn ended or it sits at the idle prompt, with no question or
     permission waiting. A record that says nothing is not ready.
   - **A send read while ready** is for now: at its due time the fresh scan
     must show the same window and the same waiting identity, ready, or it
     is refused, "The session changed since you looked. A new turn began."
     (D3, and Remote Control overtaking it, per the Consequences).
   - **A send read mid-turn, or queued,** is for when the turn ends (D7): the
     fresh scan checks the window, its process and its terminal, and that it
     is ready; not ready, it goes back to the ring and is looked at again
     after 5 seconds.
   - **The loop's cheap read** (D7) is the attention record: a queued send
     falls due when it reads ready and its ``turn_since`` has moved past the
     one the last typing into that window saw, so one send goes per turn end.
     A window whose Stop was missed reads running there for ever, so its
     queued send waits until it is cancelled or dropped; the page shows it
     queued, never typed.
   - The interrupt (increment 4) is not held to this; it is the one input
     meant for a running turn.

.. dec:: Quiet is Claude Code's input box, unchanged and empty; the lock is taken after it
   :id: DEC_ADR_029_D29
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_HOSTS_CLAUDE_CODE_TALK, COMP_HOSTS_CLAUDE_CODE_LAUNCH, COMP_ORACLE_TALK, COMP_ORACLE_CHAINSTEP

   - **What is looked at.** ``TerminalReader`` reads the tab's visible text
     (``contents``, through ``on run argv``, typing nothing). The host layer
     finds Claude Code's input box in its last 30 lines: a prompt line (``>``
     or ``❯``) under a rule of box lines, up to the next rule. The digest is
     of the box alone, so a status line that changes on its own is not taken
     for somebody typing; the line is empty when nothing follows the prompt
     sign, or only its ``Try "…"`` hint, and nothing sits between it and the
     rule under it. A tab with no box it knows is ``Unreadable``: the send is
     refused and send is off for that window's process (ruling 11).
   - **Remembered.** A tab that answered ``no_tab`` or ``no_claude`` turns
     send off for that window's process, with its line in the composer's
     place ("Send from here works for Terminal windows only.", "Claude is not
     running in this window."); a new process starts afresh.
   - **The typist's lock** is taken after the quiet wait, not before, and
     held for the fresh scan, the typing and the half second after it, as D6
     says; a minute of waiting for quiet under it would hold the chain's
     nudge out of the tab. A typist waits at most 10 seconds for another to
     leave; the page's send is then refused "Someone is typing on the Mac",
     and the chain's nudge reads ``busy`` and is said, not typed.

.. dec:: The page keeps its draft, its sends' text and their states, and nothing after a minute away
   :id: DEC_ADR_029_D30
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_WEBPAGE, COMP_ORACLE_PANEL, COMP_ORACLE_WEB

   - **The composer** sits in its own element, fixed above the bottom safe
     area, outside the screen the page redraws every 2 seconds, so a read
     never takes the person's draft or their place in it.
   - **A send** shows under the turns as a "You" quiet card with the text the
     page holds and its state line, worded by the design of record's table.
     The wait for quiet and the typing show "Sending…" with no Undo: the
     window for changing one's mind has closed. A delivered send shows its
     line alone, as its turn arrives in the transcript. A finished send is
     listed for 10 minutes, and the server keeps at most 64.
   - **A refusal at the tap** keeps the text in the composer, with "Not sent:
     ``<reason>``" above it. **Undo** gives the text back to an empty
     composer.
   - **The text the page holds** for a send is in memory only, gone with a
     reload, and dropped with every other session text after 60 seconds in
     the background (D24); the send's line stays.
   - **The boot id** rides on ``/api/state``, ``/api/session`` and the tap's
     answer; a send of this page that the server under a new boot does not
     list shows "Dropped, not delivered: the oracle restarted" (D7).

.. dec:: "Reply…" is increment 3's: a Needs you card waiting for input opens the composer
   :id: DEC_ADR_029_D31
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_PANEL, COMP_ORACLE_WEBPAGE

   Settled by the session that queued the build, from the design of record:
   :doc:`oracle-panel.md </architecture/oracle-panel>` gives the waiting
   card's button to "inc 3, 5" and draws Overview to Conversation with
   "Reply… (inc 3)", while the Increments table below named only "Answer…"
   (increment 5). "Reply…" opens the composer, which this increment builds,
   and needs nothing from increment 5, so it lands here and the table's row
   3 names it; "Answer…" stays in row 5.

   - It is the primary button of a Needs you card whose row waits on the
     person with no question or permission prompt recorded (the ``waiting``
     signal's cause ``input``), where the page sends and the row has a live
     window it reads, with Details beside it. It opens the Conversation with
     the composer focused.
   - Claude Code's own ``waiting`` status is what raises that card, and it
     means a prompt is open (BUG_087), so D28 queues a reply there until the
     turn ends rather than typing into the prompt. The card's note stays
     ADR-009 D8's until increment 5 words the prompt.

.. dec:: The ring holds at the tap too; a revoked device's waiting sends are dropped at the next beat
   :id: DEC_ADR_029_D32
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_TALK, COMP_ORACLE_WEB, COMP_ORACLE_DEVICES

   - D7's bounds count held sends as well as queued ones, so a burst of taps
     cannot grow memory either: past 8 for a window or 64 for the server, the
     oldest queued send is dropped ("Dropped, not delivered: too many were
     queued"), and when every one is still held the tap is refused, "Too many
     messages are waiting to be sent."
   - D12's revocation "drops the device's queued sends at once": ``oracle web
     revoke`` runs in another process, so the delivery loop asks the device
     store on each beat while a send waits, and drops the waiting sends of a
     device no longer paired (``talk-dropped``, reason ``revoked``). A store
     that cannot be read keeps the send. The phone-rule listing stays
     increment 6's.
   - Only the talk routes take D2's 32 KiB body; every other route keeps
     ADR-008 D10's 4 KiB. The nonce is compared in constant time and replaced
     by every read, an unchanged one included.

.. dec:: The suite's talk seam types nothing; the delivery loop starts with the first tap
   :id: DEC_ADR_029_D33
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_CLI_MAIN, COMP_ORACLE_TALK, COMP_ORACLE_WEB

   - D4's suite-wide recorder is ``oracle/talk.RecordingTalk``, installed for
     every test by ``conftest.py`` in place of ``_oracle_talk`` (owed since
     D17): it reads what a test gives it, answers a quiet look, and records
     each text it would have typed. ``_oracle_talk`` is no longer read-only.
     The ``osascript`` guard stays the backstop.
   - ``oracle web`` builds one ``Talker`` beside ``RemoteActions`` and hands
     it to every listener it makes, so a page served again after a tailnet
     change keeps the sends. Its loop thread starts with the first tap, so a
     server nobody sends from runs none.
   - A restart to a newer build (BUG_089) waits for running actions, not for
     a send being typed; the sends it drops are D7's.

Decisions recorded with increment 4
-----------------------------------

Increment 4 settled what the plan left to the build. Each is inferred from
the rulings, the decisions above and what Claude Code 2.1.291 was seen to do
in a terminal on 2026-11-20, not asked, except ruling 15, which the user
answered when the build asked (D37 records it).

.. dec:: The interrupt is Esc in the form Claude Code reads it, typed as a named key
   :id: DEC_ADR_029_D34
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_HOSTS_CLAUDE_CODE_LAUNCH, COMP_HOSTS_CLAUDE_CODE_TALK

   Ruling 4 names the Esc key and D4 a typer that sends named keys as
   ``on run argv`` arguments. Terminal's ``do script`` ends every text it
   types with a Return, so a bare Esc arrives as Esc and Return together,
   which Claude Code reads as Option-Return and answers with a line break in
   the input box, not an interrupt (measured). The key is therefore Esc in
   its CSI-u form, ``ESC [ 27 u``, which Claude Code 2.1 reads as Esc: it
   stops a running turn, and on an open permission prompt it declines the
   tool and stops the turn. The Return after it does nothing in a running
   turn, on an open prompt or menu, or on an empty input line (measured).

   - ``TerminalTyper.key(tty, name)`` types one key of ``NAMED_KEYS``
     (``escape``): the name is the script's argument, the key's bytes are
     built inside ``TERMINAL_KEY_SCRIPT``, and any other name runs nothing.
     The shell-only refusal is the message's.
   - *Rejected:* Ctrl-C. Its Return selected the highlighted "Yes" of an open
     permission prompt and ran the tool (measured), and at an idle prompt it
     clears the person's draft. *Rejected:* bringing the tab to the front and
     pressing Esc through System Events. It needs a second macOS permission,
     takes the Mac's focus from the person, and a keystroke that lands in
     another application is a misdelivery nothing would detect (ruling 9).

.. dec:: Stop types only into the running turn the person saw, with no prompt open and an empty input line
   :id: DEC_ADR_029_D35
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_TALK, COMP_ORACLE_WEB, COMP_ORACLE_WEBPAGE, COMP_ORACLE_PANEL

   D2, D3, D6, D7 and D28 made exact for the interrupt.

   - **Offered** while the window's turn runs (D28's not ready), in a
     Terminal tab where Claude runs, with no question or permission prompt
     open (the record's ``waiting`` or Claude Code's own ``waiting`` status).
     It stays offered when the session cannot be read (ruling 11), and a tab
     whose input box was unreadable keeps it; only a tab without Claude in it
     turns it off. The read issues the talk nonce for it even where send is
     off.
   - **The tap** is D2's: one tap, the talk nonce, 5 seconds of Undo held on
     the server, an audit line of the kind ``interrupt`` with no text. A read
     that showed Claude waiting for input or on a prompt is refused at the
     tap ("Claude's turn had already ended.", "Claude is waiting on a prompt
     — answer it on the Mac.").
   - **Due** at the end of the Undo window, typed at once, never queued (D7)
     and with no wait for quiet (D6); a window with a delivery under way waits
     one beat. Under the typist's lock the fresh scan must find the same
     window, process and terminal, the turn still running, no prompt open and
     the same ``turn`` and ``turn_since``; a turn that ended in the Undo window
     is refused, "Claude's turn had already ended.", and nothing is typed.
   - **One look, not a quiet wait.** The interrupt does not wait three
     seconds, since stopping is its point, but one look at the tab must find
     Claude Code's input box with its line empty: Esc with text on the line
     and the Return after it would send that text as a prompt (measured).
     Text there refuses, "Someone is typing on the Mac."; a tab with no box
     the host knows (a menu or a prompt drawn over it) refuses as unreadable
     without turning Stop off.
   - *Not offered on an open prompt.* Esc there declines the tool, which is
     a permission answer the confirm sheet of increment 6 gives with its
     choices; and the prompt hides the input line the look must see empty.

.. dec:: The interrupt's receipt is the transcript's next entry of the person's; the seam grows interrupt and receipt
   :id: DEC_ADR_029_D36
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_TALK, COMP_HOSTS_CLAUDE_CODE_TALK, COMP_ORACLE_WEBPAGE, COMP_ORACLE_PANEL

   - **No hook fires for an interrupt**, not even ``Stop`` (measured), so its
     evidence is the transcript's (D3). ``SessionTalk.receipt(host, kind,
     since)`` answers the first entry of the person's written since the key:
     Claude Code's own marker, "[Request interrupted by user]" or "…for tool
     use", written within milliseconds of the key, is the interrupt's digest
     and reads "Delivered"; a prompt in its place carries another hash and
     reads "Not confirmed"; nothing within 5 seconds reads "Not confirmed",
     with D27's one alert. A line it cannot read ends the search with no
     evidence. ``SessionTalk.interrupt(host)`` types the key (D34).
   - **The attention record keeps "running"** after an interrupt, since
     ``Stop`` does not fire; Claude Code's own status (BUG_087) is what reads
     it idle. Where that status is not vouched for, Stop stays offered, and a
     second Stop finds an empty idle line, where the key does nothing, and
     reads "Not confirmed": said, never wrong.
   - **On the page** Stop is D2's 44 px round secondary button with a filled
     square, beside Send in the composer, and alone in the composer's place
     when send is off. It never touches the draft. A stop shows as a "You"
     quiet card reading "Stop" with the send's state line and Undo, and the
     conversation then shows Claude Code's marker as written. "Stop", "Claude's
     turn had already ended." and "Claude is waiting on a prompt — answer it
     on the Mac." join ``panel.WORDS``.

.. dec:: Slash commands are messages; one that opens a menu is refused at the tap
   :id: DEC_ADR_029_D37
   :status: accepted
   :part_of: DEC_ADR_029
   :amends: DEC_ADR_029_D26
   :affects: COMP_HOSTS_CLAUDE_CODE_TALK, COMP_ORACLE_TALK, COMP_ORACLE_PANEL

   Rulings :ref:`4 <adr-029-ruling-4>` and :ref:`15 <adr-029-ruling-15>`.
   D26's refusal of a message opening with "/" is lifted; "!", "#" and "&"
   stay refused.

   - **Typed as a message.** A slash command is D1's message: one tap and
     Undo, typed only while Claude waits for input (D28), after quiet (D6),
     queued while a turn runs (D7), as the text and then a lone Return (D26).
     Claude Code runs the command on the first Return and the second does
     nothing (seen in a pseudo-terminal on 2.1.291).
   - **Refused when it opens a menu** (ruling 15). ``hosts/claude_code_talk``
     names the commands Claude Code 2.1.291 was seen to open a menu or
     dialog for when typed alone (``MENU_COMMANDS``, ``MENU_COMMANDS_SEEN_ON``):
     add-dir, config, cost, effort, export, help, hooks, mcp, memory, model,
     permissions, plugin, privacy-settings, release-notes, resume, rewind,
     sandbox, status, tasks, theme and usage. Alone, one is refused at the tap
     (``menu_command``, "This command opens a menu — use it on the Mac.");
     with arguments (``/model sonnet``) it is typed. A command not on the
     list is typed; if it opens a menu all the same, its receipt reads "Not
     confirmed" and D29 turns send off for the window: said, never wrong.

.. dec:: A command's receipt: the hook where one fires, else the window's evidence, followed past /clear and /exit
   :id: DEC_ADR_029_D38
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_TALK, COMP_HOSTS_CLAUDE_CODE_TALK

   D3's "where Claude Code fires no hook, the evidence is the transcript's
   next entry for that input", made exact from what 2.1.291 writes.

   - **The hook first.** A custom command or a skill fires
     ``UserPromptSubmit`` with the text as typed (``/hello there``), so D27's
     receipt confirms it as a message. A built-in command fires none.
   - **Else the window's evidence.** For every message the oracle reads the
     hook's receipt first and, while none has come, asks
     ``SessionTalk.receipt``: the first entry of the person's written since
     the typing. A command's echo (``<command-name>``, ``<command-args>``, as
     the person's entry or, for a command that opened a menu, a ``system``
     ``local_command`` record) is read as ``/name args`` and hashed as a
     prompt; ``/compact`` writes its plain text at once, while its echo comes
     after the compaction, which can take a minute, so the plain entry
     confirms it within D3's 5 seconds.
   - **``/clear``** ends the window's session and starts a new one in the same
     process, whose transcript holds the echo. ``TalkHost`` gains the
     window's ``pid``; the host reads Claude Code's per-process record for
     the session it runs now and looks there.
   - **``/exit``** writes nothing. Its evidence is the window's process having
     ended, with its own digest (``ENDED_DIGEST``), which only ``/exit`` is
     hashed as: a process that ends after another message reads "Not
     confirmed".
   - After ``/clear`` a session row's window is the new session, whose
     conversation the page shows; after ``/exit`` the row reads "The Claude
     window is gone.". The send keeps its "Delivered".

.. dec:: A command reads in the conversation as the person's turn, its output not shown
   :id: DEC_ADR_029_D39
   :status: accepted
   :part_of: DEC_ADR_029
   :amends: DEC_ADR_029_D15
   :affects: COMP_HOSTS_CLAUDE_CODE_TALK

   D15 left how a slash command reads to this increment. A command's echo is
   the person's turn, ``/name args``, from the Mac or the phone alike; its
   output (``<local-command-stdout>``) and Claude Code's caveat stay skipped,
   since ruling 2's conversation is the person's messages and Claude's
   replies. ``/compact``'s plain entry and its echo just after it are one turn.
   An echo without a time is skipped, as before.

Decisions recorded with increment 5
-----------------------------------

Increment 5 settled what the plan left to the build. Each is inferred from
the rulings, the decisions above, the design of record
(:doc:`oracle-panel.md </architecture/oracle-panel>`, "9 · Conversation" and
its Words) and what Claude Code 2.1.330 was seen to do in a pseudo-terminal on
2026-11-22, not asked.

.. dec:: A question is answered by arrow keys and Terminal's own Return, one step a script, the tab read after each
   :id: DEC_ADR_029_D40
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_HOSTS_CLAUDE_CODE_TALK, COMP_HOSTS_CLAUDE_CODE_LAUNCH

   D8 leaves the keys to the host layer, "from the shape it recognised".
   Measured on Claude Code 2.1.330 (``QUESTION_SEEN_ON``), the
   ``AskUserQuestion`` view takes the arrow keys and Return: Return picks the
   highlighted choice of a single-select question (and moves to the next
   question), ticks or clears the highlighted box of a multi-select one, and
   on its Submit row moves on; after several questions, or a lone
   multi-select, a "Review your answers" screen waits on its Submit answers.
   Arrow keys sent together in one write are each read as a key; typed on the
   "Type something" row, text becomes the "Other" answer. Three things are
   not used, each seen to go wrong: digits sent together are read as no key,
   a line feed is not a Return (two of them walked the highlight onto "Chat
   about this", which declines the question), and a Return on an empty
   "Type something" row declines the question.

   - **One step, one script.** Terminal's ``do script`` ends every text with
     a Return (D34), so each step is one script of arrow keys and, for
     "Other", its text, whose Return is the step's own: the choice, the tick,
     the Submit row, the review's Submit answers. ``TerminalTyper.keys``
     types it: the moves' names and the text are ``on run argv`` arguments,
     their bytes made in ``TERMINAL_KEYS_SCRIPT``; no moves and no text is a
     lone Return.
   - **Read after every step.** The tab is read before the first key and
     after each step until it shows the step's effect (``question_screen``):
     the next question, the ticked box, the "Other" text, the review, or no
     question left. A screen that did not follow stops the typing there, as
     "not confirmed" by its receipt: keys have gone in, so it is never
     retried. An "Other" text whose Return was not taken (seen with a
     multi-select) is given one lone Return once the tab shows the text on
     its row.
   - The moves start from the highlight the tab shows, so a highlight moved
     at the Mac is followed, not assumed. The real-Terminal check owed since
     increment 3 covers these keys too.

.. dec:: ``pending`` reads the open call from the transcript; the tab must show that question, nothing chosen
   :id: DEC_ADR_029_D41
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_HOSTS_CLAUDE_CODE_TALK, COMP_ORACLE_TALK

   - **The read.** ``SessionTalk.pending`` is the window's newest
     ``AskUserQuestion`` call that no tool result answers, read back from the
     transcript (``read_pending``); a subagent's call is not the window's, and
     a person's prompt newer than any call ends the search. Its known shape is
     one to 8 questions, each with its text, header, a boolean
     ``multiSelect`` and two to 16 choices of a label and a description; a
     key it does not know, a value of another type, or a question or label
     named twice is ``Unreadable``, as is a line that is not JSON (ruling 11).
     "Other" is offered on every question: Claude Code 2.1.330 draws its
     "Type something" row on each.
   - **The tab.** ``quiet`` now says what the tab ``shows``: Claude's input
     box, or the question view, whose ``empty`` is that nothing on it is
     chosen (no tab ticked, no box, no "Other" text, not the review). D6's
     quiet is that view unchanged and untouched for 3 seconds; a view someone
     began to answer is busy, refused after a minute ("Someone is typing on
     the Mac."). A question on the tab is never quiet for a message, and the
     input box back is the question answered or dismissed at the Mac. Before
     the first key the view must show the call's first question, its words
     and its choices' labels in order; a view the host does not know is
     ``unknown_shape`` and turns send and answer off for the window (D11).

.. dec:: An answer names choices by index and an optional one-line "Other"; audited by index
   :id: DEC_ADR_029_D42
   :status: accepted
   :part_of: DEC_ADR_029
   :amends: DEC_ADR_029_D5
   :affects: COMP_ORACLE_TALK, COMP_ORACLE_WEB, COMP_ORACLE_WEBACTIONS

   D5's ``choice`` for a question is ``answers``: one entry per question,
   ``{choices: [index...], other: text | null}``, as JSON. A single-select
   question takes one choice or an "Other"; a multi-select one any number of
   distinct choices and an "Other", at least one in all. "Other" is one line
   (the page's field is one line, and a line break would be a Return in the
   view) of at most 8000 characters, with D26's control characters refused.

   - **Checked twice** (D2). The read binds the question it served to the
     talk nonce; the tap's indexes are checked against that read (a misfit is
     400, ``answer``). At its due time a fresh ``pending`` must be the same
     call with the same words and choices, or it is refused, "Claude is no
     longer waiting on that question.".
   - **Audited by index.** The audit line's ``choice`` is each question's
     indexes, "Other" as the index after its last choice; ``length`` is the
     "Other" texts' length and ``hash`` the SHA-256 of the indexes and those
     texts. Never a label, a question or an "Other" text. The page holds what
     it shows on the send's "You" card (the chosen labels) in memory only, as
     a message's text.

.. dec:: The answer's receipt is the PostToolUse of AskUserQuestion; the transcript's result is the window's evidence
   :id: DEC_ADR_029_D43
   :status: accepted
   :part_of: DEC_ADR_029
   :amends: DEC_ADR_007_D5, DEC_ADR_029_D27
   :affects: COMP_HOSTS_CLAUDE_CODE, COMP_HOSTS_CLAUDE_CODE_TALK, COMP_ORACLE_ATTENTION, COMP_CLI_MAIN, COMP_ORACLE_TALK

   D3's "the answered tool's hook". ``PostToolUse`` of ``AskUserQuestion``
   carries the call's id, its questions and Claude Code's ``answers`` (a
   question's text to its answer: the label, the "Other" text, or a
   multi-select's joined by ", "). ``claude_code_talk.answer_digest`` hashes
   the id and, per question in order, its text and the answer's parts split
   at ", ", trimmed and sorted, so the order a box was ticked in does not
   count; the oracle computes the same from the picks it typed.

   - The hook path records ``received: {kind: "question", hash, at}`` with
     the write that clears ``waiting``; ``received`` gains the kind
     ``question`` (:doc:`state_model.md </architecture/state_model>`).
   - Matched as D27's: new since the typing, within 5 seconds, the same hash
     is "Delivered", another hash or none "Not confirmed" with one alert.
   - While no hook has answered, ``SessionTalk.receipt(host, "question",
     since)`` reads the transcript's tool result for the call (its
     ``toolUseResult``), hashed the same way: D38's window evidence.

.. dec:: An answer is due at the end of its Undo, never queued, typed only while the same question waits
   :id: DEC_ADR_029_D44
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_TALK, COMP_ORACLE_WEB

   D7's "an answer is refused unless its prompt is waiting now", made exact.

   - **Offered** while the window's attention record says a question waits
     (``waiting`` of the kind ``question``), in a Terminal tab nothing turned
     off, when the session and the question both read. A window whose record
     says nothing, though Claude Code's own status says it waits, is not
     answered: the fingerprint's waiting identity is the record's (D8), and
     without it nothing would tell this question from the next.
   - **Due** at the end of the Undo window and never queued; a window with a
     delivery under way waits one beat. After quiet (D41), under the
     typist's lock, the fresh scan must find the same window, process and
     terminal, still waiting on a question with the same ``since``, and a
     fresh ``pending`` the same call (D42); otherwise "Claude is no longer
     waiting on that question." and nothing is typed.
   - What the host found before its first key refuses: busy ("Someone is
     typing on the Mac."), answered, a view it does not know (unreadable, and
     answer off for the window), or no tab. Once keys have gone in, the
     answer waits for its receipt like a typed message.

.. dec:: "Answer…" is built for a question in increment 5; a permission row keeps its card until increment 6
   :id: DEC_ADR_029_D45
   :status: accepted
   :part_of: DEC_ADR_029
   :amends: DEC_ADR_029_D10
   :affects: COMP_ORACLE_PANEL, COMP_ORACLE_WEB, COMP_ORACLE_WEBPAGE

   The design of record's Words give the waiting card "Answer… (a question
   or a permission, inc 5)", and the Increments table gives permission
   answers to increment 6. A button on a permission row now would open a
   Conversation with nothing to answer, so "Answer…" lands for a question
   here, and for a permission prompt with its confirm sheet in increment 6.

   - **The card.** A Needs you card waiting on a question that the page can
     answer (``oracle/web`` read it, the page sends, the window is live)
     has the primary button "Answer…", before Details, opening the
     Conversation at its question. Its note is the question's first line, at
     most 200 characters, the session's own text drawn as text (D10); a
     question that cannot be read keeps ADR-009 D8's card with "Can't read
     this prompt here — use the Mac." as its note. ``/api/state`` reads each
     waiting question for this, each on its own.
   - **The sentences.** "This panel never answers prompts." is gone. A
     question reads "Claude asked you a question N min ago. Answer it here or
     on the Mac."; waiting for input "Claude is waiting for your next
     message." (its card's note too); a permission prompt "Claude asked for
     permission N min ago. Answer it in Claude on your Mac or phone." until
     increment 6 words it.

.. dec:: The page's question block: choices as 48 px buttons, one tap for a lone single-select, toggles and Send answer otherwise
   :id: DEC_ADR_029_D46
   :status: accepted
   :part_of: DEC_ADR_029
   :affects: COMP_ORACLE_WEBPAGE, COMP_ORACLE_PANEL

   Screen 9's item 4 for a question, built from the panel's tokens.

   - The block is a card on ``tile-needs``, the needs tone's ground (the
     design's ``tint-needs``; the tokens have no such name). Each question's
     text in 17 px semibold, then one 48 px secondary button per choice,
     full width, its label over its description in 13 px when it has one
     (Claude Code shows the description, and a label alone can be
     ambiguous).
   - A lone single-select question sends on the tap of a choice (D2's one
     tap). Several questions, or a multi-select, make each choice a toggle
     (``aria-pressed``, the primary colour when on) and one primary "Send
     answer", enabled once every question has an answer. "Other…" opens a
     16 px one-line field, and for a lone single-select its own "Send".
   - The block keeps what was chosen across the two-second reads, and a read
     waits while the person types in "Other"; a new question starts afresh.
     While its answer is held, being typed or checked, the block is hidden
     and the send shows as a "You" card with the chosen labels and a send's
     line; Undo brings the block back as it was.
   - "Chat about this", which declines the question in Claude Code, is not
     offered: ruling 5 asks for the choices and "Other", and a decline from
     the phone is a step the person did not ask for.

Increments
----------

+-----+------------------------------------------------------------+-------------+
| Inc | Builds                                                     | Waits for   |
+=====+============================================================+=============+
| 1   | IDEA_029's Read half: the last-message excerpt on cards    | this        |
|     | and in the detail, read by the web server, never in the    | record's    |
|     | ``assess`` document (ADR-009 D17, D11's excerpt). The      | approval;   |
|     | ``SessionTalk`` seam with ``conversation`` only, and       | **built**   |
|     | ``hosts/claude_code_talk``'s transcript reader, fail       | (D14 to     |
|     | closed.                                                    | D18)        |
+-----+------------------------------------------------------------+-------------+
| 2   | The live conversation view: ``GET /api/session``, the      | 1;          |
|     | Conversation screen, its 2-second refresh, the drop after  | **built**   |
|     | the page is in the background (D11, ADR-009 D17).          | (D19 to     |
|     | Reading only.                                              | D24)        |
+-----+------------------------------------------------------------+-------------+
| 3   | Send: the composer, Undo held on the server, the talk      | 2;          |
|     | nonce, the fingerprint and the receipt (``received`` on    | **built**   |
|     | the sidecar), "not confirmed" and its alert, quiet and the | (D25 to     |
|     | one-minute refusal, the typist's lock, the mid-turn ring,  | D33)        |
|     | the shell-only refusal, the audit lines (D1 to D7); the    |             |
|     | Needs you row's "Reply…" (D10, D31).                       |             |
+-----+------------------------------------------------------------+-------------+
| 4   | Interrupt and slash commands: the Esc key and the receipts | 3;          |
|     | for inputs that fire no hook (D2, D3); a command that      | **built**   |
|     | opens a menu refused at the tap (ruling 15).               | (D34 to     |
|     |                                                            | D39)        |
+-----+------------------------------------------------------------+-------------+
| 5   | Question answers: ``pending`` for a question, the choices, | 3;          |
|     | multi-select and "Other"; the Needs you row's "Answer…"    | **built**   |
|     | (D8, D10).                                                 | (D40 to     |
|     |                                                            | D46)        |
+-----+------------------------------------------------------------+-------------+
| 6   | Permission answers: ``pending`` for a prompt, the confirm  | 5           |
|     | sheet, the allow-always receipt, ``web/phone-rules.json``, |             |
|     | the Phone rules screen, ``oracle web rules`` and its       |             |
|     | removal, revoke's listing (D9, D10, D12).                  |             |
+-----+------------------------------------------------------------+-------------+
| 7   | Drift in ``doctor``: the ``oracle talk`` row with the      | 3           |
|     | Claude Code version and what could not be read (D11).      |             |
+-----+------------------------------------------------------------+-------------+

Consequences
------------

- The oracle types into running sessions at a phone's request, for the first
  time beyond the chain's one "continue". Its second power is session input,
  next to confirmed actions, with its own routes, fingerprint, receipt and
  audit lines.
- The page carries the sessions' own text for the first time. It is the
  most sensitive thing the oracle serves, and it is never written anywhere by
  the oracle: not on the Mac, not in the browser, not in a push.
- A misdelivered keystroke is prevented by the fingerprint and the quiet
  wait, and detected by the receipt; a send that cannot be confirmed is said,
  never retried.
- Claude Code's internals are read in one host module, by named shapes. A
  Claude Code update that changes them turns send and answer off for the
  affected sessions until the module learns the new shape; the person sees
  why on the page and in ``doctor``.
- A lost phone is a bigger loss than before (D12). Revocation ends it, and
  the phone-rule list shows what it changed.
- Remote Control stays usable beside the page: an input from either arrives
  as the user's turn, and the fingerprint refuses a page input that a Remote
  Control input overtook.

Alternatives rejected
---------------------

- **Drive Claude Code through a programmatic interface instead of the tab.**
  A session the user started in Terminal has no such interface open; the
  oracle would have to own every session's process, which ADR-007's
  read-only scan and the user's own windows rule out.
- **Mask secrets in the conversation.** The user chose no masking
  (ruling 13). The exposure is bounded by pairing and by keeping nothing.
- **Keep the conversation in the snapshot** so the page reads it without a
  transcript read per request. It would put the sessions' text on disk in
  the oracle home (ruling 13).
- **Keep the mid-turn queue on disk** to survive a restart. The user chose
  memory only (ruling 14); a queued send outliving its context could arrive
  in a turn it no longer fits.
- **A per-send approval on the Mac, or a second factor for permission
  answers.** It would defeat the remote route; the user chose revocation and
  the phone-rule list (ruling 12).
