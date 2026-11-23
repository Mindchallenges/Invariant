The Session Oracle
==================

A machine-wide view of every running session in every invariant project. It
says which one needs you and recommends the exact next command for each one
(IDEA_012). The decisions behind it are recorded in ADR-007.

The oracle was built in seven increments, each landing on its own. All seven
have shipped, so v1 is complete and this page describes the current
behaviour. The follow-ons (phone push, a status line, web and artifact
dashboards, more actions, an idle alert) are their own ideas, IDEA_013 to
IDEA_018, and IDEA_023, the web dashboard over Tailscale, whose three
increments have shipped: the page, confirm-first actions from it, and the
page served always by a second LaunchAgent ("Shipped: the web page" below,
ADR-008). IDEA_026 serves that page over HTTPS with Tailscale's certificate
("Over HTTPS" below, ADR-008 D19–D24). IDEA_027 is turning that page into a
mobile control panel: its increments 3, the plain-language panel, 4, the
home-screen app, 5, failure reasons, and 6, orphan classes and close host,
have shipped ("The control panel" below, ADR-009), and increment 7 is to
come. IDEA_030 lets the oracle start a new Claude window, Remote Control on,
in any registered project; its CLI core and shared launcher, the phone's
"New session" sheet and the dashboard's ``n`` have shipped ("Start a session"
below, ADR-009 D16). IDEA_057 moves the choice of the next session to the
oracle (ADR-027); its increment 1, the queue, ``session report`` and
``oracle next``, which decides and starts nothing, has shipped ("What runs
next" below), and so has increment 2, the watcher's chain step that starts it
("The chain step" below), and increment 2b, the plan's usage, its hold and
stalls ("Usage and stalls" below), increment 3, the chain on the phone ("The
chain on the phone" below), and increment 4, web push, which folds IDEA_013
in: every alert and chain notice reaches each phone that turned it on ("Web
push" below). IDEA_060 is the page talking to running sessions: reading
their conversation, sending to them, interrupting them and answering their
prompts. Its record, ADR-029, is accepted; its increment 1, a live window's
last message on its card and in its detail, has shipped ("Shipped: a
window's last message" below), and so has increment 2, the live
conversation view ("Shipped: a window's conversation" below), increment 3,
sending a message ("Shipped: sending to a window" below), and increment 4's
interrupt and slash commands ("Shipped: stopping a turn and slash commands"
below); increments 5 to 7 are planned ("Planned: talking to sessions" below).

+---+----------------------------------------------------+-------------+
| # | Increment                                          | Status      |
+===+====================================================+=============+
| 1 | Home, project index, ``oracle projects`` commands, | **shipped** |
|   | auto-registration                                  |             |
+---+----------------------------------------------------+-------------+
| 2 | Attention sidecar and the ``Notification`` hook    | **shipped** |
+---+----------------------------------------------------+-------------+
| 3 | Read-only scan, ``classify``, ``recommend``,       | **shipped** |
|   | ``oracle list``                                    |             |
+---+----------------------------------------------------+-------------+
| 4 | Watcher, rising-edge de-dup ledger, notifier,      | **shipped** |
|   | ``watch``, ``notify-test``                         |             |
+---+----------------------------------------------------+-------------+
| 5 | Gate cache for recommendation detail               | **shipped** |
+---+----------------------------------------------------+-------------+
| 6 | launchd ``install`` / ``uninstall`` / ``status``   | **shipped** |
+---+----------------------------------------------------+-------------+
| 7 | Textual TUI behind the ``invariant[oracle]``       | **shipped** |
|   | extra, confirm-first actions                       |             |
+---+----------------------------------------------------+-------------+

Where it sits
-------------

.. mermaid::

   graph TD
       CLI[cli — invariant oracle …, init, hook, gated commands]
       HOST["hosts/claude_code"]

       subgraph oracle["oracle/"]
           HOME[home]
           INDEX[index]
           ATTN[attention]
           SCAN[scan]
           SIGNALS["signals / recommend"]
           WATCH[watcher]
           GATES[gates]
           NOTIFY[notify]
           LAUNCHD[launchd]
           DASH[dashboard]
           ACTIONS[actions]
           TUI["tui (Textual, the [oracle] extra)"]
           WEB["web (IDEA_023, ADR-008)"]
           PANEL["panel (IDEA_027, ADR-009)"]
           WEBPAGE[webpage]
           WEBACT["webactions (IDEA_023 inc 2; failure summary, IDEA_027 inc 5)"]
           WEBSVC["webservice (IDEA_023 inc 3)"]
           TLS["tls (IDEA_026)"]
           DEVICES[devices]
           CLOSEHOST["closehost (IDEA_027 inc 6)"]
           START["startsession (IDEA_030; inc 2: Starter, find_started; inc 3: Starter.again)"]
           BUILD["build (BUG_089)"]
           CHAIN["chain (IDEA_057, ADR-027 inc 1): decide"]
           CHAINSTEP["chainstep (ADR-027 inc 2, ruling 9; inc 2b: usage hold, stalls, release)"]
           USAGE["usage (ADR-027 inc 2b, ruling 10): plan_usage, hold"]
           PUSH["push (ADR-027 inc 4, ruling 17): web push"]
           TALK["talk (ADR-029 inc 1): SessionTalk, last_message"]
       end

       subgraph core["core/"]
           STORE[store]
           MANIFEST[manifest]
           GIT[git]
           RULES[rules]
           PRIVACY[privacy]
           QUEUE["queue (ADR-027 D2)"]
       end

       DOMAINS["domains/ (session, pipeline)"]

       CLI --> HOME
       CLI --> INDEX
       CLI --> ATTN
       CLI --> SCAN
       CLI --> SIGNALS
       CLI -- reads the AttentionSignal left by --> HOST
       CLI --> WATCH
       CLI --> NOTIFY
       CLI -. injects ClaudeCodeProbe from .-> HOST
       CLI -. injects applescript_string from .-> HOST
       ATTN --> STORE
       INDEX --> STORE
       INDEX --> MANIFEST
       INDEX --> GIT
       SCAN --> DOMAINS
       SCAN --> INDEX
       SCAN --> ATTN
       SCAN --> STORE
       SCAN --> MANIFEST
       SCAN --> GIT
       SIGNALS --> SCAN
       WATCH --> SCAN
       WATCH --> SIGNALS
       WATCH --> INDEX
       WATCH --> NOTIFY
       CLI --> GATES
       WATCH --> GATES
       GATES --> SCAN
       GATES --> DOMAINS
       GATES --> STORE
       GATES --> MANIFEST
       GATES --> RULES
       GATES --> GIT
       GATES -- "reads turn_state (BUG_087)" --> SIGNALS
       SIGNALS -. reads the GateReport shape of .-> GATES
       CLI --> LAUNCHD
       LAUNCHD -. its plist runs oracle watch .-> WATCH
       CLI -. imports only inside oracle tui .-> TUI
       CLI --> DASH
       CLI --> ACTIONS
       CLI -. injects without_host_markers and the handoff launcher .-> HOST
       TUI --> DASH
       TUI --> ACTIONS
       DASH --> WATCH
       DASH --> SIGNALS
       DASH --> SCAN
       ACTIONS --> SIGNALS
       ACTIONS -- "reads a masked value from its recorded run, at perform time (BUG_093)" --> SCAN
       SCAN -- "masks what the project's patterns flag (BUG_093)" --> PRIVACY
       CLI --> WEB
       CLI --> DEVICES
       WEB --> DASH
       WEB --> DEVICES
       WEB --> WEBPAGE
       WEB --> PANEL
       WEB --> WATCH
       PANEL --> SCAN
       PANEL --> SIGNALS
       WEBPAGE --> WATCH
       DEVICES --> INDEX
       DEVICES --> HOME
       WEB --> WEBACT
       WEBACT --> ACTIONS
       WEBACT --> DEVICES
       WEBACT --> SCAN
       WEBACT --> HOME
       WEBACT --> SIGNALS
       WEBACT --> PANEL
       CLI --> WEBSVC
       LAUNCHD -. its second plist runs oracle web --wait .-> WEBSVC
       WEBSVC --> WEB
       WEBSVC --> DEVICES
       WEBSVC --> WEBACT
       WEBSVC --> SCAN
       WEBSVC --> INDEX
       CLI --> TLS
       WEBSVC --> TLS
       TLS --> WEB
       TLS --> HOME
       WEB -. serves the Served of .-> TLS
       CLI --> CLOSEHOST
       CLOSEHOST --> SCAN
       WEBACT -- "outcome_word: a close's last line to its code, for the audit (BUG_098)" --> CLOSEHOST
       CLI -. injects the record reader, ps, SIGTERM and the Terminal tab closer from .-> HOST
       CLI --> START
       START --> SCAN
       START --> GIT
       START --> DOMAINS
       CLI -. "opens the window, reads the names in use, through" .-> HOST
       WEBACT --> START
       PANEL --> START
       CLI -. "injects a Starter (its own prepare and launch) into" .-> WEBACT
       TUI --> START
       CLI -. "injects the same Starter into" .-> TUI
       SCAN -- "ScanCache.build: is this process older than the install?" --> BUILD
       BUILD --> RULES
       BUILD --> MANIFEST
       WEBSVC --> BUILD
       CLI -- "stamp for status and doctor; reexec through _oracle_restart" --> BUILD
       CLI -- "oracle next, chain start: decide, read_inputs, by_hand" --> CHAIN
       CHAIN -- "read_queue, queue_path: read only" --> QUEUE
       CHAIN -- "ROW_SESSION" --> SCAN
       CHAIN -- "read_inputs: the backlog; session_end: an ended session, inert" --> DOMAINS
       CLI -- "oracle next: QUEUED" --> QUEUE
       WATCH -- "the chain step, after the alerts" --> CHAINSTEP
       CHAINSTEP --> CHAIN
       CHAINSTEP -- "open_windows" --> START
       CHAINSTEP -- "oracle/chain.json, locks/chain.lock" --> HOME
       CHAINSTEP --> NOTIFY
       CLI -- "oracle chain status, pause, resume, start" --> CHAINSTEP
       CLI -. "injects ChainHands: start-session's launch; domains/queue's begin, mark, fail" .-> CHAINSTEP
       CLI -- "hook statusline: record_usage; read_usage, weekly_max" --> USAGE
       USAGE -- "oracle/usage.json" --> HOME
       CHAINSTEP -- "hold, spent" --> USAGE
       CLI -- "oracle chain greenlight, continue, retry, close" --> CHAINSTEP
       CLI -. "injects the plan_usage reader; ChainHands' nudge (TerminalTyper), close (close_host) and requeue" .-> CHAINSTEP
       CLI -. "parses the status line, installs it, types into a tab, through" .-> HOST
       CLI -- "_oracle_notifier: FanOutNotifier of the Mac and push; revoke: drop_device" --> PUSH
       CLI -. "injects the POST (_oracle_push_post: urllib_post)" .-> PUSH
       PUSH -- "PushNotifier is a Notifier; NobodyToNotify, Outcome" --> NOTIFY
       PUSH -- "web/push.json, locks/push.lock" --> HOME
       PUSH -- "a send skips an unpaired device" --> DEVICES
       WEB -- "the key and this device's subscription, subscribe, unsubscribe" --> PUSH
       WEB -- "each live window's last message, for the view (ADR-029 inc 1)" --> TALK
       CLI -. "injects SessionTalk (_oracle_talk: ClaudeCodeTalk) from" .-> HOST

**The dependency rule.** ``oracle/`` is a layer above ``domains/`` and below the
CLI. Nothing in ``core/``, ``domains/`` or ``adapters/`` imports it, and a test scans
those packages to enforce this. The engine therefore behaves identically
whether the oracle is used or not.

The SessionStart registration and the attention writes are both wired in the
CLI's ``hook`` command and not in ``hosts/claude_code``. That keeps the host
adapter free of the new layer: the host says what an event means, and the CLI
writes it (ADR-007 D6). A test scans ``hosts/`` for oracle imports, beside the
one that scans ``core/``, ``domains/`` and ``adapters/``.

Shipped: home and project index
-------------------------------

``oracle/home``
~~~~~~~~~~~~~~~

``oracle_home()`` returns ``$INVARIANT_HOME`` when it is set and not empty, and
``~/.invariant`` otherwise. The value is resolved on every call and never cached.
The function only names the directory: it does not create it and does not read
it. It asks ``core/home.invariant_home``, the one resolution of that directory,
which the manifest also reads for the user level of the configuration
(ADR-014 D3), so the index and ``config.yaml`` always share a home.

The test suite sets ``INVARIANT_HOME`` to a fresh temporary directory for every
test (``tests/conftest.py``). The variable is exported, so a hook shim that a
test runs as a subprocess inherits it too. No test can write the developer's
real index.

``oracle/index``
~~~~~~~~~~~~~~~~

``ProjectIndex(home)`` manages ``projects.json``. The format and its invariants
are specified in :doc:`state_model.md </architecture/state_model>`, "User-level state". In
summary:

- An entry is ``{key, root, name, via}``. ``key`` is ``Manifest.state_root``, so all
  of a repository's worktrees, and several manifests in one repository,
  collapse to one entry.
- ``root`` is the main checkout's spelling of the project. A registration made
  from inside a linked worktree records the same relative position in the main
  checkout, provided a manifest exists there.
- No clock times. The format also keeps keys it does not know, and refuses a
  newer ``version`` instead of rewriting it.
- Writes take ``JsonStore.locked``, through ``HomeStore``, the home's store,
  which the watcher shares. The home holds ``projects.json``, ``locks/`` and the
  watcher's ``oracle/``. A project state root's ``sessions/`` and ``pipelines/`` are
  never laid out here.

+----------------------------------+----------------------------------+
| Operation                        | Behaviour                        |
+==================================+==================================+
| ``register(manifest, via)``      | Reads first. Locks only when the |
|                                  | key is missing, and checks again |
|                                  | under the lock. Returns          |
|                                  | ``(entry, added)``. An existing  |
|                                  | entry is returned unchanged.     |
+----------------------------------+----------------------------------+
| ``match(selector)`` /            | Tries the selector as a key,     |
| ``remove(selector)``             | then a root (relative or         |
|                                  | absolute), then a name. The      |
|                                  | first rung with a hit wins. A    |
|                                  | name shared by several entries   |
|                                  | is refused, and so is a selector |
|                                  | that matches nothing.            |
+----------------------------------+----------------------------------+
| ``prune(dry_run)``               | Drops the entries whose ``root`` |
|                                  | no longer has a manifest. The    |
|                                  | manifest is checked again under  |
|                                  | the lock. If nothing is stale,   |
|                                  | no lock is taken.                |
+----------------------------------+----------------------------------+

**Built (:need:`ADR-014 <DEC_ADR_014>` D10, increment 2).** A store project has no
``.claude/invariant.yaml`` in its root, so the five checks that tested
``root / MANIFEST_RELPATH`` for existence (``ProjectEntry.has_manifest``,
``_canonical_root``, ``oracle/scan`` twice, ``oracle/gates``) ask
``Manifest.is_project_root``, which asks ``Manifest.locate``; otherwise ``prune``
would drop a store project and ``oracle list`` would show it as an ALERT. An
in-repo root still answers by the file's existence alone, and a project that
is found and does not load is still a project, as a manifest that exists and
does not parse always was. ``register`` names an entry after ``project.name``
when the project's own repo or store level sets it (``set_by_project``), else
after its root. The key, ``Manifest.state_root``, is unchanged.

How a project gets in
~~~~~~~~~~~~~~~~~~~~~

+------------------------------------------+-------------------+------------------------+
| Route                                    | ``via``           | Failure handling       |
+==========================================+===================+========================+
| ``invariant init`` (not ``--dry-run``)   | ``init``          | A failure is reported  |
|                                          |                   | in the output and the  |
|                                          |                   | JSON ``oracle`` key.   |
|                                          |                   | The command still      |
|                                          |                   | succeeds, because the  |
|                                          |                   | project is             |
|                                          |                   | initialised.           |
+------------------------------------------+-------------------+------------------------+
| ``invariant hook session-start`` (not    | ``session-start`` | A failure is           |
| the probe)                               |                   | swallowed. The hook's  |
|                                          |                   | response is computed   |
|                                          |                   | first and cannot       |
|                                          |                   | change.                |
+------------------------------------------+-------------------+------------------------+
| ``invariant oracle projects add [PATH]`` | ``manual``        | This is the command,   |
|                                          |                   | so a failure is its    |
|                                          |                   | error: exit 2 for no   |
|                                          |                   | manifest, exit 3 for a |
|                                          |                   | broken index.          |
+------------------------------------------+-------------------+------------------------+

The CLI surface is in :doc:`cli.md </architecture/cli>`, "Oracle".

Shipped: the attention sidecar
------------------------------

``oracle/attention``
~~~~~~~~~~~~~~~~~~~~

``AttentionStore(store)`` reads and writes
``<state_root>/attention/<host-id>.json``, one record per host session. The
format is specified in :doc:`state_model.md </architecture/state_model>`, "Attention and host
liveness". In summary:

- ``waiting`` is ``{kind, message, since}``, where ``kind`` is ``permission``,
  ``question`` or ``idle``, and ``message`` is the host's raw text. ``blocked`` is
  ``{rules, surface, since}``, where ``surface`` is ``stop`` or ``cli``. Then ``turn``
  (``running`` or ``ended``) and ``turn_since``, ``ended``, ``pid``, ``degraded``
  (``{event, error, since}``), and ``background``, the ledger of work the main
  turn started in the background and has not heard finish: ``{<task id>: {kind, since}}``, where ``kind`` is ``agent`` or ``command`` (BUG_094). It keeps
  at most 32 entries, oldest out.
- The store is built on the caller's ``JsonStore``, so the probe's inert store
  discards these writes along with every other.
- One writer per file, atomic replace, no lock. Unknown keys are kept. A
  record that cannot be read is replaced by the next write.
- A host id must be a plain name before it becomes a file name.

What writes it
~~~~~~~~~~~~~~

The host adapter turns each hook event into an ``AttentionSignal``. The CLI
applies the signal through one guarded helper, ``_record_attention``.

+------------------------+------------------------+-----------------------------+
| Event                  | Signal                 | Effect on the record        |
+========================+========================+=============================+
| ``Notification`` (new, | ``waiting``            | ``waiting`` set.            |
| the 7th hook)          |                        | ``notification_type``       |
|                        |                        | ``permission_prompt`` →     |
|                        |                        | ``permission``,             |
|                        |                        | ``elicitation_dialog`` →    |
|                        |                        | ``question``,               |
|                        |                        | ``idle_prompt`` → ``idle``. |
|                        |                        | When the type is missing or |
|                        |                        | unknown, the message is     |
|                        |                        | matched instead. A          |
|                        |                        | notification that matches   |
|                        |                        | nothing writes nothing.     |
+------------------------+------------------------+-----------------------------+
| ``PreToolUse``,        | ``waiting``            | ``waiting: question``, with |
| ``AskUserQuestion``    |                        | the first question's text.  |
|                        |                        | Not when the hook denies    |
|                        |                        | the call.                   |
+------------------------+------------------------+-----------------------------+
| ``PostToolUse``        | ``answered``           | ``waiting`` cleared. Reads  |
|                        |                        | first, and writes only if   |
|                        |                        | something was waiting.      |
+------------------------+------------------------+-----------------------------+
| ``PostToolUse`` of a   | ``background-started`` | A ledger entry opened under |
| launch into the        |                        | the task id, ``waiting``    |
| background             |                        | cleared. A launch is an     |
|                        |                        | ``Agent`` or ``Task`` call  |
|                        |                        | answered as launched        |
|                        |                        | (``isAsync``, or            |
|                        |                        | ``status: async_launched``; |
|                        |                        | the id is ``agentId``), or  |
|                        |                        | a ``Bash`` call answered    |
|                        |                        | with a                      |
|                        |                        | ``backgroundTaskId``. A     |
|                        |                        | subagent's own call (the    |
|                        |                        | payload carries             |
|                        |                        | ``agent_id``) is not one:   |
|                        |                        | its completion notifies the |
|                        |                        | subagent.                   |
+------------------------+------------------------+-----------------------------+
| ``UserPromptSubmit``   | ``prompt``             | ``turn: running``.          |
|                        |                        | ``waiting``, ``blocked``    |
|                        |                        | and ``degraded`` cleared,   |
|                        |                        | ``ended: false``. The       |
|                        |                        | ledger is kept.             |
|                        |                        | ``prompts`` counts one more |
|                        |                        | and ``prompted`` is now     |
|                        |                        | (ADR-027 ruling 21).        |
+------------------------+------------------------+-----------------------------+
| ``UserPromptSubmit``   | ``background-done``    | The entries it names        |
| of a task-notification |                        | closed; ``turn: running``,  |
|                        |                        | ``waiting`` cleared.        |
|                        |                        | ``blocked`` and             |
|                        |                        | ``degraded`` are kept,      |
|                        |                        | because the person has not  |
|                        |                        | spoken.                     |
+------------------------+------------------------+-----------------------------+
| ``Stop``, on arrival   | ``turn-stopped``       | ``turn: ended``,            |
|                        |                        | ``waiting`` cleared.        |
|                        |                        | Written before the closeout |
|                        |                        | runs (BUG_095). ``blocked`` |
|                        |                        | is left alone.              |
+------------------------+------------------------+-----------------------------+
| ``Stop``               | ``turn-ended``         | ``turn: ended``,            |
|                        |                        | ``waiting`` cleared.        |
|                        |                        | ``blocked`` set to the      |
|                        |                        | closeout's blocking rule    |
|                        |                        | ids, or, on a clean stop,   |
|                        |                        | cleared if the Stop had set |
|                        |                        | it. A CLI block is kept.    |
+------------------------+------------------------+-----------------------------+
| ``SessionEnd``         | ``host-ended``         | ``ended: true``. Everything |
|                        |                        | else cleared, including     |
|                        |                        | ``pid`` and the ledger.     |
+------------------------+------------------------+-----------------------------+
| ``invariant hook``     | —                      | ``degraded`` set, with the  |
| handler raised         |                        | event and the error.        |
+------------------------+------------------------+-----------------------------+
| ``pipeline advance``,  | —                      | ``blocked: {surface: cli}`` |
| ``session transition`` |                        | on the bound host's record, |
| / ``review`` /         |                        | if the session has a live   |
| ``merge`` refused      |                        | host. Cleared when one of   |
|                        |                        | those commands later        |
|                        |                        | succeeds.                   |
+------------------------+------------------------+-----------------------------+

``SessionStart`` writes nothing. The rule that a hook must not write on every
start still holds.

**What a task-notification is** (BUG_094). Claude Code tells the main turn
that background work ended with a user turn of its own, which fires
``UserPromptSubmit`` like a prompt. Its text starts with ``<task-notification>``
and holds one block per task, each with the task's ``<task-id>`` (the id the
launch answered with) and a ``<status>``. The host adapter reads those tags
and nothing else of the text, with one exception: a block whose ``<note>``
says the agent stopped "with background work of its own still running" will
notify again, so it closes nothing. A status of ``completed``, ``failed``,
``killed`` or ``stopped`` closes the task; any other closes nothing. A prompt
that does not start with the tag is the person speaking, whatever it
quotes. These are Claude Code internals, not a contract: if the tags change,
entries stop closing, and the ledger's lapse ("Mid-turn" below) bounds what
that costs.

**What the ledger misses.** A notification that arrives while a turn is
already running is absorbed into that turn, and no hook fires for it. On
this machine about half the notifications in recent transcripts arrived
that way, so an entry often outlives its task. The reader therefore lets
the ledger lapse after ``stuck_minutes`` without activity, and a verified
Claude Code status answers for the commands: it reads ``shell`` exactly while
a background shell runs.

A Stop writes twice. The host adapter's ``arrival_attention`` names what an
event says before any work runs, and only a Stop says anything then: the turn
is over. The CLI records that ``turn-stopped`` signal first, and only then
calls the handler, whose closeout evaluation is the slow part of the Stop
path. The handler's ``turn-ended`` signal then adds the closeout's verdict.
When the closeout never finishes, ``blocked`` keeps its last value. That value
is a CLI refusal from this turn, or the block an earlier Stop in this turn
recorded. A closeout that was cancelled refused nothing, and it lifted
nothing either. The user confirmed this choice: on a cancelled closeout,
``blocked`` keeps its last value.

The three guarantees
~~~~~~~~~~~~~~~~~~~~

**A failed write never changes a verdict.** The helper neither reads nor
returns the response, and it swallows every failure. Every write runs after
the response is computed and before it is printed, except a Stop's arrival.
That write runs before the closeout and cannot see the response either. The
suite checks this by breaking the store and comparing the output byte for
byte (ADR-007 D10).

**A delivered Stop ends the turn** (BUG_095). The host cancels a hook at its
15 s timeout, and a cancelled process writes nothing more. So ``turn: ended``
is written before the closeout runs, and a Stop whose closeout outlasts the
timeout still leaves the turn ended. A stale ``running`` would otherwise read
as mid-turn (``busy``, then ``stuck``) until a later event corrected it. The
timeout itself is unchanged. The fix does not depend on it, and a longer
timeout would only make the host wait longer on a slow closeout.

**The probe writes nothing.** ``doctor``'s probe payloads, including the new
``Notification`` one, skip the helper outright. They are run against the inert
store anyway.

The host pid
~~~~~~~~~~~~

The hook records ``CLAUDE_PID`` whenever the environment carries it. Without it,
a pid already recorded stands, and a host with no pid yet walks the parent
chain with ``ps`` to the first process named ``claude``
(``claude_code_launch.find_host_pid``). Both paths exist because both were seen
on this machine (ADR-007 D7). The CLI refusal path records no pid.

Shipped: the scan, the signals and the recommendations
------------------------------------------------------

``oracle/scan``
~~~~~~~~~~~~~~~

``scan_project(entry, clock, cache)`` reads one registered project and returns a
``ProjectSnapshot``; ``scan_all(index, clock, cache, select)`` does it for every
project, or for the one ``select`` names (by key, root or name, as ``projects remove`` matches). Nothing in a scan writes:

- sessions, pipelines and attention records are read through an ``InertStore``
  on the entry's key, the state root, with the domains' own record types and
  lookups;
- the manifest is loaded from the recorded root only, never by walking upward,
  so a root that lost its manifest is an error and not some enclosing
  project's sessions;
- git is asked ``worktree list`` and nothing else.

The suite builds three projects and five live sessions and fingerprints every
file under every state root and the oracle home before and after two scans
(path, size, mtime). Nothing moves.

**Rows.** One shape, four kinds, so the classifier reads them all the same way.

+--------------+-------------------------------------------------------+
| Kind         | What it is                                            |
+==============+=======================================================+
| ``session``  | A live session: any state the project's session       |
|              | machine does not call terminal or abandoned. The      |
|              | registry is not consulted; ``sessions/*.json`` is     |
|              | enumerated, because the registry can lag the records. |
|              | Its hosts are nested under it, the current binding    |
|              | first and then ``hosts``, oldest first. Live here     |
|              | means not ended, whether or not a host is attached;   |
|              | the panel's Projects line calls these open sessions   |
|              | and names the paused ones apart (BUG_092).            |
+--------------+-------------------------------------------------------+
| ``host``     | An orphan host: an attention record or a transcript   |
|              | that no live session claims, shown while the host is  |
|              | alive or for 24 hours after its last sign of life. A  |
|              | host bound only to an ended session counts, and the   |
|              | row names that session. A record whose host said      |
|              | ``SessionEnd`` does not. The row lists the project's  |
|              | live, not-stranded sessions with no live binding as   |
|              | ``adoptable``.                                        |
+--------------+-------------------------------------------------------+
| ``worktree`` | A linked worktree on disk that no live session        |
|              | records.                                              |
+--------------+-------------------------------------------------------+
| ``error``    | A project that cannot be read (no manifest at the     |
|              | root, a manifest that does not parse, a machine that  |
|              | does not load), or one session record that cannot be. |
|              | One error row never hides another row. A project an   |
|              | older oracle cannot read is not one ("An older        |
|              | oracle" below).                                       |
+--------------+-------------------------------------------------------+

**An older oracle** (BUG_089). The watcher and the web page are long-lived,
and the install is usually editable, so a merge on the main checkout changes
the code and data files under them while they keep the modules they started
with. An older rule registry then reads a newer shipped profile, the manifest
refuses it, correctly, as naming a rule "this build" does not carry, and
every project used to become an ``error`` row: "Cannot read this project", on
every card. ``oracle/build`` tells the two apart:

+---------------+--------------------------+--------------------------+
| Signal        | How                      | Cost                     |
+===============+==========================+==========================+
| The stamp     | ``BuildWatch`` hashes    | About 75 files, under 1  |
|               | the relative path, size  | ms                       |
|               | and modification time of |                          |
|               | every file under the     |                          |
|               | installed package, code  |                          |
|               | and ``data/``            |                          |
|               | (``__pycache__`` left    |                          |
|               | out), when it is made,   |                          |
|               | which is when            |                          |
|               | ``ScanCache`` is: a      |                          |
|               | long-lived command makes |                          |
|               | its cache when it        |                          |
|               | starts. Each             |                          |
|               | ``scan_all`` stamps the  |                          |
|               | tree again; a different  |                          |
|               | digest is ``changed``.   |                          |
+---------------+--------------------------+--------------------------+
| The rule gaps | The rule ids the shipped | Three small YAML files,  |
|               | profiles on disk name    | parsed again only when   |
|               | that this process's      | one changes              |
|               | registry does not carry: |                          |
|               | ``rules``. Needs no      |                          |
|               | start stamp, so it holds |                          |
|               | for any process.         |                          |
+---------------+--------------------------+--------------------------+

The package version is no signal: ``__version__`` is a constant in the source
and a merge does not move it. A build stamp would need a build step, and an
editable install has none.

With either signal, ``scan_all`` hands the ``Skew`` to every project's read. A
project whose load failed the way an older build fails is then ``unread``, with
the load's error, and has no rows: under ``rules`` alone, a manifest refusing
one of the gap ids (``UnknownRuleError``, a ``ManifestError`` that keeps the id);
once the tree has ``changed``, any failed load, because an older build cannot
tell a manifest it cannot read from one it reads wrongly, and the restarted
oracle reports the project's own errors. A root with no manifest stays an
error row, and so does a session record that cannot be read. ``assess`` says
the skew once, as the document's ``oracle``: ``{skew, unread}``, the skew's
reasons, both stamps, the gap ids and a detail, and the names of the projects
left unread. It raises no signal, so nothing notifies; the watcher keeps an
unread project's ledger keys, as for any project it could not read; the panel
shows one Problems card (:doc:`oracle-panel.md </architecture/oracle-panel>`, "States");
``oracle list`` prints one line under its table.

.. mermaid::

   flowchart TD
       S["scan_all"] --> C{"cache.build.check()"}
       C -- "same stamp, no gap" --> N["skew none: a failed load is an error row"]
       C -- "rules only" --> R{"UnknownRuleError naming a gap id?"}
       C -- "changed" --> M{"no manifest at the root?"}
       R -- "yes" --> U["project unread, no rows"]
       R -- "no" --> E["error row"]
       M -- "yes" --> E
       M -- "no" --> U
       U --> A["assess: document oracle.skew and oracle.unread, said once"]

**Stages, not state names.** A project may shadow the session machine, so the
scan never names a state. ``session_stages(machine)`` derives where each state
sits from the machine's structure:

+--------------+-----------------------------+---------------------------+
| Stage        | Derived as                  | Shipped machine           |
+==============+=============================+===========================+
| ``ended``    | terminal, or carrying the   | ``torn_down``,            |
|              | ``abandon`` role            | ``abandoned``             |
+--------------+-----------------------------+---------------------------+
| ``landed``   | the merge — the state whose | ``merged``, ``pushed``    |
|              | entry gates include         |                           |
|              | ``session.ff_merge_target`` |                           |
|              | — and every non-terminal    |                           |
|              | state reachable after it    |                           |
+--------------+-----------------------------+---------------------------+
| ``reviewed`` | a state with an edge into   | ``reviewed``              |
|              | the merge                   |                           |
+--------------+-----------------------------+---------------------------+
| ``working``  | a state with an edge into a | ``working``               |
|              | reviewed one                |                           |
+--------------+-----------------------------+---------------------------+
| ``early``    | everything else             | ``registered``,           |
|              |                             | ``isolated``              |
+--------------+-----------------------------+---------------------------+

A pipeline is read the same way: ``landed`` is terminal and not the abandon
state, and the red phase is the machine's ``red_phase`` (else ``red``, the rule's
own fallback).

**Host liveness.** A host is alive when its pid exists (``os.kill(pid, 0)``,
every scan) and the process is still the host (a process-name check, kept
for 60 seconds and dropped the moment the pid is seen dead). A reused pid is
therefore "not alive", and a host that died without ``SessionEnd`` stops reading
as waiting. No pid known means liveness is unknown (``null``), which no signal
treats as gone.

**Where the pid comes from** (IDEA_024). Two sources name a host's pid: its
attention record, written by the hooks, and Claude Code's per-process record,
``<claude home>/sessions/<pid>.json``, which every running Claude Code process
keeps whatever hooks it ran. The scan joins the second to the hosts it already
knows by host session id (the record's ``sessionId``), so a host that never
wrote an attention record still has a pid. The join adds no host: a project's
hosts are still its attention records and its transcripts, so attribution
stays transcript-based, and the record's ``cwd`` is shown, not used to place a
host.

A recorded pid gets one check more than an attention pid. One ``ps -o tty=,lstart=,comm=`` (``ClaudeCodeProbe.process_facts``) answers the name, the
process's start and its terminal; when the record's ``procStart`` and the
process table both give a start, they must be equal, because a reused pid can
be another ``claude``. Claude Code writes ``procStart`` in UTC and ``ps`` prints
``lstart`` in local time, so ``ps`` runs with ``TZ=UTC`` (the rest of its
environment unchanged) and both sides spell one instant the same way; neither
is converted between zones (BUG_086: compared across zones, every live host
on a machine outside UTC read as a reused pid). A start either side cannot read (another locale's
spelling, a missing field) is not compared. The facts are kept for 60 seconds
and dropped when the pid is seen dead, as the name check is. A live pid ``ps``
cannot describe is unknown, not gone.

.. mermaid::

   flowchart TD
       A["host id"] --> R{"per-process records for it?"}
       R -- "none" --> P{"attention pid?"}
       P -- "no" --> U["pid null, alive null"]
       P -- "yes" --> AP["pid_source attention: kill 0 + name check"]
       AP --> O{"a live orphan?"}
       O -- "no" --> AK["pid_source attention"]
       O -- "yes" --> V["one ps (TZ=UTC): claude, start, tty"]
       V -- "cannot describe / no start" --> UV["pid_source attention, unverified ps / start"]
       V -- "not claude" --> G["alive false: a reused pid"]
       V -- "described" --> W{"started before the attention record's last write?"}
       W -- "no" --> UN["unverified newer"]
       W -- "yes" --> RR{"records read, none names this pid?"}
       RR -- "not read" --> UR["unverified records"]
       RR -- "another host's record" --> UO["unverified other"]
       RR -- "yes" --> L{"lsof: its cwd?"}
       L -- "cannot read" --> UC["unverified cwd"]
       L -- "read" --> AV["pid_source attention+ps: start, tty and cwd kept"]
       R -- "two or more alive" --> C["pid_source conflict: pid null, alive null, every candidate listed"]
       R -- "one alive, or the newest dead one" --> J{"attention pid differs?"}
       J -- "yes" --> C
       J -- "no attention pid" --> RP["pid_source record"]
       J -- "same pid" --> BP["pid_source both"]
       RP --> CHK["kill 0 + one ps (TZ=UTC): claude, same start; tty kept"]
       BP --> CHK

**An attention pid no record describes** (BUG_099). A host whose
environment inherited ``CLAUDE_CODE_CHILD_SESSION`` (a Terminal.app itself
started from a Claude tool shell, IDEA_035) writes no per-process record,
so only its attention record names its pid. For such a host the scan reads
the evidence the record would have given, and only when the row is a live
orphan, because only an orphan can be offered Close: one ``ps`` (the same
``process_facts`` read, kept for 60 seconds) for the name, the start and the
tty, and one ``lsof -a -p PID -d cwd -Fn`` (``ClaudeCodeProbe.process_cwd``,
kept for 60 seconds, dropped with the pid) for its working directory. What
ties the pid to the host, the job the record's ``sessionId`` does, is the
attention record itself: one host writes it, so a process that started
before its last write is the process that wrote the pid, not a later one on
a reused pid. ``ProcessFacts.started`` gives the start as epoch seconds for
that one ordering; the start token is still compared for equality only. The
records must have been read and none may name the pid for another host (a
``/clear`` or a resume moves one process to another conversation). All of it
read, the row's ``pid_source`` is ``attention+ps``, with ``process_start``, ``tty``
and ``cwd`` filled. The first fact that fails is kept as ``unverified`` (``ps``,
``start``, ``newer``, ``records``, ``other``, ``cwd``) and the pid stays ``attention``;
``ps`` naming another program makes the host not alive, as on the record
path. A host a live session holds, or one that is gone, costs nothing more.

**What a host inherited from its terminal** (IDEA_035). A Terminal.app that
was itself started from a Claude tool shell, before BUG_068's scrub, hands
``CLAUDE_CODE_CHILD_SESSION`` and ``COLORTERM=truecolor`` to every window it
opens. A Claude started there writes no transcript and no per-process record
(the case above) and draws colour Terminal.app cannot show, as large white
or inverse blocks. The cure is the user's: quit Terminal.app fully and
relaunch it from the Dock. The scan names such a host on its row so the
user sees it, from the phone too. For each live host whose pid is its own,
the probe's ``inherited_markers(pid)`` (``ClaudeCodeProbe``, through
``ScanCache.inherited``, kept for 60 seconds and dropped with the pid) reads
the ``claude`` process's OWN environment, as it was started: ``ps -ww -o command=`` and ``ps -E -ww -o command=`` on that pid, and only what follows the
argv, so a note on the command line that mentions a marker is not one. Only
the two names count, ``COLORTERM`` only as ``truecolor``, and only when the
parent chain shows Terminal.app started the host with no other ``claude``
between: iTerm and editors set ``COLORTERM`` themselves, and a Claude started
by a Claude inherits by design. The row's host gets ``inherited_env``, the
names found, sorted (``[]`` for none, ``null`` when unread); no other variable
and no value is read into the scan. "Its own pid" is the record's
(``record``, ``both``), or an attention pid ``ps`` says is ``claude`` and that
started before the attention record's last write, BUG_099's ordering, so a
reused pid's environment is never taken for this host's. That costs a
session's attention-only host one ``ps`` (``process_facts``, cached) beyond its
name check, and only with a probe that reads environments. ``ps`` reads the
environment of the user's own processes without privilege, whole with
``-ww``; anything it cannot show (another user's process, a process gone
between the two reads) is unknown, never clean.

.. mermaid::

   flowchart TD
       H["live host, its own pid"] --> E{"ps -E: environment after the argv?"}
       E -- "cannot read" --> U["inherited_env null"]
       E -- "neither marker" --> C["inherited_env []"]
       E -- "CLAUDE_CODE_CHILD_SESSION and/or COLORTERM=truecolor" --> T{"parent chain: Terminal.app before any claude?"}
       T -- "cannot read" --> U
       T -- "another app, or a claude first" --> C
       T -- "yes" --> F["inherited_env: the names, sorted"]
       F --> S["signal inherited_env (info): the remedy"]

**The oracle never picks one silently.** When both sources name a pid and the
pids differ, or two records for one host are both alive, the row carries
``pid_source: conflict`` and ``pid_conflict``, each candidate with its own
liveness, and ``pid`` and ``alive`` stay ``null``. Unknown liveness is what no
signal reads as gone, so a conflict never raises ``host_gone`` and never offers
adopt. The TUI says ``pids disagree: attention 3 (gone), record 4 (alive)``.

A host row also keeps the record's ``launched_name`` (the session name it was
started with), ``cwd``, ``started`` (epoch seconds) and ``process_start`` (the
start token, so a later action can compare it again at the moment it acts),
and the verified process's ``tty``. The tty is what close host needs to find
the terminal tab the host runs in ("Close host" below). On an
``attention+ps`` row these come from ``ps`` and ``lsof`` instead (``launched_name``,
``started`` and ``status`` stay ``null``), and ``unverified`` names what could not
be read. It keeps the
record's ``status`` too, as written (Claude Code writes ``busy``, ``idle``,
``waiting``, or ``shell`` while its turn is idle and a background shell runs),
and only when the record joined the host: ``signals.turn_state`` decides what
it means ("Mid-turn" below, BUG_087, BUG_094). A conflict row has no
status, as it has no pid.

**Where an orphan was started** (IDEA_027 increment 6). An orphan host row's
view also carries ``rooted_here``: whether its ``cwd`` (the record's, or on an
``attention+ps`` row the one ``lsof`` read) is the project's
root, a folder under it, or one of its worktrees (the ones git lists and the
ones its session records name), compared by whole path components after
resolving; ``null`` without a ``cwd``. And once every project is read,
``scan.mark_claims`` names on each orphan row the project whose live session
lists the same host, as ``claimed_elsewhere``: a project's own live sessions
never leave an orphan, so such a host is one another project attributed to
it, BUG_040's case. ``assess`` calls it first. Both are evidence close host
needs, and neither changes a signal.

**When the records are not used.** They are Claude Code internals, not a
contract, so the reader (``claude_code_launch.read_host_processes``) is strict:
only ``<digits>.json`` is opened (the ``<pid>.<hash>.key`` files beside them never
are), a record must be a JSON object of at most 64 KiB whose ``pid`` is the
positive integer the file is named after, with a non-empty ``sessionId``, and a
``pidDomain`` other than this platform is skipped. Optional fields of the wrong
type read as ``null``. A missing directory, or files of which none parse, raise,
and so does a probe that cannot read them; the scan then uses no record, every
host's pid is the attention record's or ``null`` as before, and each project of
the document says so under ``host_processes``: ``{used, records, detail}``. ``oracle list`` prints ``host process records: not used: <why>`` under its table. An empty
directory is a source with nothing running. The records are read once per 60
seconds, like the other cached answers.

**A session's own values, masked** (BUG_093). A pipeline view carries
``last_scoped``, the session's newest test run that named selectors: its index
in the record (``run``), the pipeline's ``started``, its ``selectors``, whether the
run kept what its discovery found (``recorded``), whether the project's
``test_one`` command has a placeholder only ``discover`` supplies
(``needs_discovery``), and ``env``, each discovered value under the variable a
replay exports it as, ``INVARIANT_<KEY>`` (``scan.discovery_env``; a key that is
no variable name, or that would be ``INVARIANT_SESSION_ID``, is left out).
Each value the project's privacy patterns flag is masked with ``core/privacy``'s
own redaction, a finding's ``redacted`` (``scan.mask_value``: two characters kept,
the rest blocked out, as in ``A1████``), and named in ``masked``. Patterns that
cannot be built mask every value whole, so a broken setting never reads as
"nothing flagged". ``last_test`` is masked the same way: its ``discovered``
values, and each one where its resolved ``command`` spells it. So no flagged
value is in the scan's rows, the ``assess`` document or the watcher's snapshot
in the clear. ``scan.replay_values`` reads it back, for one recorded run only,
when a confirmed step runs ("Confirm-first actions" below).

**Last activity** is the latest of the host transcripts' last turns, the
attention records' modification times, the session's ``heartbeat`` and its
``plan_touched``. The times are read, never stored, so the "no stored clock
time" rule is untouched.

A transcript's last turn is the ``timestamp`` of its newest ``user`` or
``assistant`` entry, not the file's modification time (BUG_078). Claude Code
keeps appending bookkeeping lines (``last-prompt``, ``cost-state``,
``bridge-session``, ``system``, ``attachment``) to a conversation that has gone
quiet, so the mtime moved while the host did nothing: an orphan idle for ten
days read as active minutes ago, never left the 24-hour window, and ``stuck``
was judged against a clock that kept being reset. A ``user`` entry that only
carries a tool result counts as a turn, because a tool finishing is the host
working, and that is what ``stuck`` asks during a running turn. A subagent's
(``isSidechain``) entry counts too, and it is not in the main transcript: Claude
Code writes each subagent's entries to a file of its own,
``<host id>/subagents/agent-<agent id>.jsonl`` beside the host's transcript, so
the main transcript can stay quiet for as long as a subagent works (BUG_096).
``activity()`` credits each such file to the host whose directory holds it and
takes the latest of their last turns with the host's own. It reads one only
when the file's mtime is newer than the host's answer so far, because a file
cannot hold a turn later than its last write, so a finished subagent costs a
``stat`` per scan. The subagent files are never hosts or telemetry sources of
their own: ``paths()``, ``sessions()`` and ``find()`` list main transcripts only, and
a subagent directory with no main transcript beside it credits nobody.

The read is ``hosts/claude_code_telemetry.last_turn``, behind the probe's
``activity()``. It reads the transcript backwards from its end in 32 KiB
blocks, joins a line split across blocks, and stops at the first turn. It
skips the bytes after the last newline (a line still being written) and any
line that does not parse, and reads at most 2 MiB. **The mtime is the
fallback**, used only when no turn is found within that budget: a transcript
with no turn yet, or one whose tail is a single line longer than 2 MiB. A
file is only written after its last turn, so the fallback errs towards
"active", as the old formula always did. The reader also returns the turn's
role and its text (capped at 4000 characters), and takes a predicate, so
IDEA_029's Read half can ask it for the newest entry with text. That half
shipped as ADR-029's increment 1 (IDEA_060 took it in) through its own
reader, ``hosts/claude_code_talk``, on the same ``scan_tail``: the web server,
not the scan, asks for it, so no transcript text enters the ``assess``
document (ADR-009 D17; "Shipped: a window's last message" below).

**Host facts are injected** (ADR-007 D11). Which transcripts belong to a
project and whether a pid is ``claude`` are Claude Code knowledge. The scan
defines ``HostProbe`` (``transcripts(root)``, ``is_host_process(pid)``, and, since
IDEA_024, ``host_processes()`` and ``process_facts(pid)``, and since BUG_099
``process_cwd(pid)``), the CLI injects
``hosts/claude_code_launch.ClaudeCodeProbe``, and the oracle imports nothing
from ``hosts/``. IDEA_035 adds ``inherited_markers(pid)``, optional like the
others: without it no environment is read and no row is flagged. The start token in a record and in ``process_facts`` is one
UTC spelling, ``YYYY-MM-DDTHH:MM:SS``: ``procStart`` is UTC as Claude Code writes
it, and ``process_facts`` runs ``ps`` with ``TZ=UTC``. The oracle compares the two
for equality and never parses them.
Without a probe the scan still runs: no transcripts, no records, and a live
pid counts as the host. A probe without the two newer methods is a probe whose
records are not used; one without ``process_cwd`` leaves an attention-only
orphan ``unverified: cwd``, so it is never offered Close.

**A store's docs lock on a session row** (ADR-014 D5, increment 4b). In a
store project the scan reads the store's lease record once
(``docs_lock_views``) and puts each live session's place on its row as
``docs_lock``: every unit its lease holds, with ``since``, how long, the
queue as session ids, ``stale`` and why, and ``new`` when the session's
store leg has no open span for it, so a release passed it on unseen; and the
unit it queues for with its position and holder. Staleness reads the
holder's host from its attention sidecar (``lease_host_alive``, which the
CLI hands the session domain as well). A store that cannot be read leaves
every row without it. ``signals`` raises ``docs_granted`` and
``docs_queued`` from it; ``oracle list`` prints a ``docs`` line per unit; the
panel and page name it under "Technical details", with the chips in
:doc:`oracle-panel`'s wording.

**Cheap on the next pass.** ``ScanCache`` keeps the git-backed answers — each
project's worktree list and its transcript source, whose construction asks git
for the project's worktrees — and the process-name checks for 60 seconds. A
repeat scan re-reads only small JSON files, directory listings and the tails of
the transcripts that changed: the source remembers each transcript's last turn
against its size and mtime. Measured:
the suite's three projects and five sessions scan in about 0.1 s cold and
0.04 s warm, and this machine's two real projects in 0.11 s and 0.29 s each.

``oracle/signals``
~~~~~~~~~~~~~~~~~~

``classify(row, now, thresholds)`` is pure: the row and the clock it is handed,
nothing else. Alerts come first in the list it returns.

+----------------------+-------+--------------+------------------------+------------------------+
| Signal               | Alert | Severity     | Raised when            | Key discriminator      |
+======================+=======+==============+========================+========================+
| ``waiting``          | yes   | high; medium | the current host is    | ``<kind>@<since>``, or |
|                      |       | after 6 h    | waiting on the person  | ``input``              |
|                      |       |              | (``turn_state``        |                        |
|                      |       |              | ``waiting``): its      |                        |
|                      |       |              | ``waiting.kind`` is    |                        |
|                      |       |              | ``permission`` or      |                        |
|                      |       |              | ``question``, or       |                        |
|                      |       |              | Claude Code's own      |                        |
|                      |       |              | status says            |                        |
|                      |       |              | ``waiting`` (cause     |                        |
|                      |       |              | ``input`` when the     |                        |
|                      |       |              | hooks recorded no      |                        |
|                      |       |              | prompt; always high,   |                        |
|                      |       |              | it carries no time)    |                        |
+----------------------+-------+--------------+------------------------+------------------------+
| ``gate_blocked``     | yes   | medium       | ``blocked`` is set and | the sorted rule ids    |
|                      |       |              | ``turn`` is ``ended``  |                        |
|                      |       |              | (ADR-007 D8)           |                        |
+----------------------+-------+--------------+------------------------+------------------------+
| ``stuck``            | yes   | medium       | ``turn`` is            | ``turn_since``         |
|                      |       |              | ``running``, the host  |                        |
|                      |       |              | is mid-turn and        |                        |
|                      |       |              | working                |                        |
|                      |       |              | (``turn_state``        |                        |
|                      |       |              | ``busy``, "Mid-turn"   |                        |
|                      |       |              | below), and the last   |                        |
|                      |       |              | activity is older than |                        |
|                      |       |              | 20 minutes; or the     |                        |
|                      |       |              | host's turn ended with |                        |
|                      |       |              | background work still  |                        |
|                      |       |              | running                |                        |
|                      |       |              | (``turn_state``        |                        |
|                      |       |              | ``background``, cause  |                        |
|                      |       |              | ``background``,        |                        |
|                      |       |              | BUG_094) and the last  |                        |
|                      |       |              | activity is older than |                        |
|                      |       |              | 20 minutes             |                        |
+----------------------+-------+--------------+------------------------+------------------------+
| ``stalled``          | yes   | medium       | a ``session`` row      | when the quiet began   |
|                      |       |              | whose current host is  | (``last_activity``)    |
|                      |       |              | known alive, with no   |                        |
|                      |       |              | activity for the       |                        |
|                      |       |              | project's              |                        |
|                      |       |              | ``stall_after`` of     |                        |
|                      |       |              | ``oracle.chain`` (2 h),|                        |
|                      |       |              | not ``waiting``; cause |                        |
|                      |       |              | ``limit``, ``error``   |                        |
|                      |       |              | or ``unknown`` from    |                        |
|                      |       |              | the host's last turn   |                        |
|                      |       |              | (ADR-027 ruling 11,    |                        |
|                      |       |              | D26, D27)              |                        |
+----------------------+-------+--------------+------------------------+------------------------+
| ``finished``         | yes   | low          | the session is in its  | ``reviewed`` /         |
|                      |       |              | reviewed stage         | ``ready``              |
|                      |       |              | (``cause: reviewed``), |                        |
|                      |       |              | or in its working      |                        |
|                      |       |              | stage with a complete  |                        |
|                      |       |              | plan and a landed      |                        |
|                      |       |              | pipeline               |                        |
|                      |       |              | (``cause: ready``)     |                        |
+----------------------+-------+--------------+------------------------+------------------------+
| ``error``            | yes   | high         | the last test run      | ``tests:<run count>``, |
|                      |       |              | failed, was not read   | ``degraded@<since>``,  |
|                      |       |              | as red, and was not in | ``scan``               |
|                      |       |              | the red phase          |                        |
|                      |       |              | (``tests``); a hook    |                        |
|                      |       |              | recorded ``degraded``  |                        |
|                      |       |              | (``degraded``); the    |                        |
|                      |       |              | row is an error row    |                        |
|                      |       |              | (``scan``)             |                        |
+----------------------+-------+--------------+------------------------+------------------------+
| ``your_turn``        | no    | info         | ``waiting.kind`` is    | —                      |
|                      |       |              | ``idle``, the host is  |                        |
|                      |       |              | not gone, and it is    |                        |
|                      |       |              | not mid-turn           |                        |
|                      |       |              | (``turn_state``        |                        |
|                      |       |              | ``idle``): never while |                        |
|                      |       |              | background work it     |                        |
|                      |       |              | started runs on        |                        |
+----------------------+-------+--------------+------------------------+------------------------+
| ``host_gone``        | no    | info         | the recorded pid is    | —                      |
|                      |       |              | not alive, and neither |                        |
|                      |       |              | the binding nor the    |                        |
|                      |       |              | record says the host   |                        |
|                      |       |              | ended                  |                        |
+----------------------+-------+--------------+------------------------+------------------------+
| ``parked``           | no    | info         | the session's binding  | —                      |
|                      |       |              | is spent and its       |                        |
|                      |       |              | worktree is there      |                        |
+----------------------+-------+--------------+------------------------+------------------------+
| ``stranded``         | no    | info         | the session's recorded | —                      |
|                      |       |              | worktree is gone       |                        |
+----------------------+-------+--------------+------------------------+------------------------+
| ``teardown_pending`` | no    | info         | the session is in its  | —                      |
|                      |       |              | landed stage           |                        |
+----------------------+-------+--------------+------------------------+------------------------+
| ``docs_granted``     | yes   | low          | in a store project,    | ``<unit>@<since>`` per |
|                      |       |              | the session's lease    | new unit, joined       |
|                      |       |              | holds a docs-lock unit |                        |
|                      |       |              | with no open span on   |                        |
|                      |       |              | its store leg: a       |                        |
|                      |       |              | release passed it on   |                        |
|                      |       |              | since the session last |                        |
|                      |       |              | looked (ADR-014 D5,    |                        |
|                      |       |              | increment 4b)          |                        |
+----------------------+-------+--------------+------------------------+------------------------+
| ``docs_queued``      | no    | info         | the session queues for | —                      |
|                      |       |              | a docs-lock unit       |                        |
+----------------------+-------+--------------+------------------------+------------------------+
| ``orphan_host``      | no    | info         | a ``host`` row; its    | —                      |
|                      |       |              | ``cause`` is the       |                        |
|                      |       |              | class: ``gone``,       |                        |
|                      |       |              | ``unknown``,           |                        |
|                      |       |              | ``active``,            |                        |
|                      |       |              | ``leftover``,          |                        |
|                      |       |              | ``unmanaged``          |                        |
|                      |       |              | (``orphan_class``,     |                        |
|                      |       |              | IDEA_025)              |                        |
+----------------------+-------+--------------+------------------------+------------------------+
| ``inherited_env``    | no    | info         | the current host is    | the names, joined      |
|                      |       |              | alive and its          |                        |
|                      |       |              | ``inherited_env``      |                        |
|                      |       |              | names a marker it      |                        |
|                      |       |              | inherited from         |                        |
|                      |       |              | Terminal.app; the      |                        |
|                      |       |              | detail gives the       |                        |
|                      |       |              | remedy, "quit          |                        |
|                      |       |              | Terminal.app fully and |                        |
|                      |       |              | relaunch it from the   |                        |
|                      |       |              | Dock" (IDEA_035)       |                        |
+----------------------+-------+--------------+------------------------+------------------------+
| ``leaked_worktree``  | no    | info         | a ``worktree`` row     | —                      |
+----------------------+-------+--------------+------------------------+------------------------+

**Mid-turn** (BUG_087). Whether a host is mid-turn is one answer,
``turn_state(host, last_activity, now, thresholds)``: ``busy`` (working),
``waiting`` (on the person), ``background`` (its main turn ended, and work it
started in the background runs on, BUG_094) or ``idle`` (not mid-turn). The
orphan class, the ``waiting``, ``stuck`` and ``your_turn`` signals, the gate
cache's "Never while a turn runs" and the panel's Working group all read it,
so none can contradict another. ``mid_turn`` is "not ``idle``". ``assess`` writes
it on each row as ``turn_state``, the field the panel reads, and beside it
``background``: for a ``background`` row, what it waits on, ``{agents, commands}`` (``background_work``), and ``null`` for any other.

+----------------------------------+----------------------------------+
| Evidence, first that holds       | Answer                           |
+==================================+==================================+
| no current host, or it is known  | ``idle``                         |
| to be gone                       |                                  |
+----------------------------------+----------------------------------+
| alive, and Claude Code's         | ``background``: its turn is idle |
| per-process record joined it     | and a background shell runs      |
| with a ``status`` of ``shell``   |                                  |
+----------------------------------+----------------------------------+
| the same, with ``idle``, and an  | ``background``: Claude Code says |
| open ``agent`` in the ledger     | ``idle`` while a background      |
|                                  | agent runs                       |
+----------------------------------+----------------------------------+
| the same, with ``busy``,         | that status: the record outranks |
| ``idle`` or ``waiting``          | the attention record             |
+----------------------------------+----------------------------------+
| the attention record waits on a  | ``waiting``                      |
| ``permission`` or a ``question`` |                                  |
+----------------------------------+----------------------------------+
| a ``running`` turn with no       | ``busy``                         |
| ``idle`` prompt after it (an     |                                  |
| idle prompt is notified after    |                                  |
| the last prompt was submitted,   |                                  |
| so it is newer than the turn),   |                                  |
| heard from within                |                                  |
| ``orphan_idle_minutes`` (60), or |                                  |
| with no activity known           |                                  |
+----------------------------------+----------------------------------+
| an open ledger entry (the turn   | ``background``                   |
| ended, an ``idle`` prompt came   |                                  |
| after it, or a ``running`` turn  |                                  |
| went stale)                      |                                  |
+----------------------------------+----------------------------------+
| otherwise                        | ``idle``: a ``running`` turn     |
|                                  | quiet past the edge is stale,    |
|                                  | because a Stop that never        |
|                                  | arrived leaves ``running`` for   |
|                                  | ever                             |
+----------------------------------+----------------------------------+

**Background work** (BUG_094). The ledger on the attention record ("What
writes it" above) counts what the main turn left running. It lapses once
the host has been quiet for ``stuck_minutes`` (20), because Claude Code
absorbs a notification that arrives during a turn without a hook; no
activity known is no proof it lapsed. With BUG_096, a background agent's own
transcript is the host's activity, so a working agent keeps its entry open.
A verified Claude Code status answers for the commands, since it reads
``shell`` exactly while one runs: ``shell`` is at least one, any other status
none. The ledger's agents still count under an ``idle`` status.

.. mermaid::

   stateDiagram-v2
       [*] --> busy: a prompt, or a task-notification turn
       busy --> background: Stop, with a ledger entry open or Claude Code saying shell
       busy --> idle: Stop, nothing left running
       background --> busy: a task-notification turn closes its entries, or a prompt
       background --> idle: Claude Code says idle with no agent open, or the ledger lapses
       background --> stuck: shell, and quiet past stuck_minutes
       idle --> [*]: SessionEnd clears the ledger

``stuck`` on background work needs the ``shell`` status: the ledger on its own
lapses at the same 20 minutes, so a missed notification never raises an
alert. A background command the host verifies as running, with the host
quiet for 99 minutes, reads "background work, silent for 99 min". A
background shell meant to run for a long time, such as a development
server, reads ``stuck`` too once the host has been quiet that long; the alert
is keyed on ``turn_since``, so it notifies once per turn.

The record's status is used only when the host is alive by the record's own
check, so a reused or unverified pid never speaks for the host; an unknown
or missing status falls back to the attention record. Without a record a
``running`` turn reads ``stuck`` between 20 and 60 minutes of silence and then
stops counting; with one, ``busy`` stays ``stuck`` however long, and ``idle``
never is. Six Heap hosts idle 14 to 44 hours read ``orphan_host`` ``active``,
five of them "idle at the prompt" and one "stuck for 880 min", because the
Stop hook never wrote ``turn-ended`` for them while their records said ``idle``.

**Waiting on the person is never closeable.** A host whose record says
``waiting`` is ``active``, and raises the existing ``waiting`` alert rather than a
signal of its own: ``recommend``'s first row then offers nothing to run, the
panel puts it in Needs you with no button (ADR-009 D8), and the TUI's and
the page's ``refusal`` refuses a step on it. When the hooks recorded the
prompt, the alert keeps their kind and text; otherwise its cause is ``input``
and its discriminator ``input``. A record that says ``idle`` or ``busy`` silences
an attention prompt left behind by a missed ``PostToolUse`` or Stop.

Only the current host's record is read; the hosts a session had before are
history. Every signal carries ``key = project|subject|kind|discriminator``, where
the subject is the session id, ``host:<id>``, ``worktree:<path>`` or ``project``. The
discriminator changes exactly when the condition is a new occurrence — a new
prompt, a different set of refusing rules, another test run, a new turn — so
the watcher's ledger can alert once per rising edge.

A failed run in the red phase is the proof ``test.new_test_fails`` asks for, not
an error, which is why the red phase is excluded alongside ``read_as_red``.

``oracle/recommend``
~~~~~~~~~~~~~~~~~~~~

``recommend(row, signals, gates)`` names one next step: a label, the exact ``argv`` and
``cwd``, and a kind — ``run`` (an ``invariant`` command), ``terminal`` (one that
belongs in a new host session) or ``info`` (nothing to run). The first row of the
table that holds wins.

+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| Condition            | Kind     | Command                                                                      | Run in            |
+======================+==========+==============================================================================+===================+
| ``waiting``          | info     | none: answer it in its own terminal. The oracle never answers a prompt       | —                 |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| error row            | info     | none: what stops the read                                                    | —                 |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| leaked worktree      | info     | none: inspect it, remove it with git if spent                                | —                 |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| orphan host,         | run      | ``invariant oracle close-host <host> --pid N --start TOKEN [--tty ttysNNN]`` | project root      |
| ``leftover`` and     |          |                                                                              |                   |
| every close guard    |          |                                                                              |                   |
| holds                |          |                                                                              |                   |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| orphan host, alive,  | run      | ``invariant session adopt <host> --id <session>``                            | project root      |
| exactly one          |          |                                                                              |                   |
| adoptable session,   |          |                                                                              |                   |
| and no other live    |          |                                                                              |                   |
| orphan host here     |          |                                                                              |                   |
| could take it        |          |                                                                              |                   |
| (``adopt_rivals`` 0) |          |                                                                              |                   |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| orphan host,         | run      | ``invariant oracle close-host …``, as above                                  | project root      |
| ``unmanaged`` and    |          |                                                                              |                   |
| every close guard    |          |                                                                              |                   |
| holds                |          |                                                                              |                   |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| orphan host, alive,  | info     | none: "not closeable from here: ``<the guard>``", with ``reason`` naming it  | —                 |
| ``leftover`` or      |          | (BUG_099)                                                                    |                   |
| ``unmanaged``, a     |          |                                                                              |                   |
| close guard failing  |          |                                                                              |                   |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| orphan host,         | info     | none: start or resume a session from that host                               | —                 |
| otherwise            |          |                                                                              |                   |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| failed tests, a      | run      | ``invariant pipeline test --id <session> --selector S …``, each selector of  | worktree, else    |
| scoped run on record |          | the session's newest scoped run, with ``INVARIANT_<KEY>`` in ``env`` for     | root              |
|                      |          | each value that run's discovery resolved (BUG_093)                           |                   |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| failed tests, no     | info     | none: run its tests from the session's own window. The oracle never runs the | —                 |
| scoped run on        |          | whole suite (ADR-010)                                                        |                   |
| record, or one older |          |                                                                              |                   |
| than the record of   |          |                                                                              |                   |
| discovered values    |          |                                                                              |                   |
| while the project's  |          |                                                                              |                   |
| ``test_one`` needs   |          |                                                                              |                   |
| discovery            |          |                                                                              |                   |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| ``gate_blocked``     | run      | ``invariant pipeline gates --id <session>``                                  | worktree, else    |
|                      |          |                                                                              | root              |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| ``stuck``            | run      | ``invariant context --id <session>``                                         | worktree, else    |
|                      |          |                                                                              | root              |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| ``finished``,        | run      | ``invariant session merge --id <session>``                                   | project root (the |
| reviewed             |          |                                                                              | merge runs from   |
|                      |          |                                                                              | the main          |
|                      |          |                                                                              | checkout)         |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| ``finished``, ready  | run      | ``invariant session review --id <session>``                                  | worktree, else    |
|                      |          |                                                                              | root              |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| ``teardown_pending`` | run      | ``invariant session teardown --id <session>``                                | project root      |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| ``stranded``         | run      | ``invariant session reap --dry-run`` (look before reaping)                   | project root      |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| ``parked``           | terminal | ``invariant session resume --id <session>``                                  | its origin, else  |
|                      |          |                                                                              | root              |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| ``host_gone``        | terminal | ``invariant session resume --id <session> --force``                          | its origin, else  |
|                      |          |                                                                              | root              |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| ``error``, degraded  | run      | ``invariant doctor``                                                         | project root      |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+
| ``your_turn``        | info     | none                                                                         | —                 |
+----------------------+----------+------------------------------------------------------------------------------+-------------------+

``recommend`` never answers a prompt, and ADR-029 keeps that: the answers
the phone gives are typed through a path of their own (``oracle/talk``),
never offered as a step here (a question's since increment 5, "Shipped:
answering a question" below; a permission prompt's in increment 6).

The close guards are ADR-009 D7's and D15's: the host is alive by a
verified source (``pid_source`` ``record`` or ``both``, or ``attention+ps`` since
BUG_099), with a start token and no ``pid_conflict``; it is ``rooted_here``;
and no other project's live session claims it (``claimed_elsewhere``). A
gone, active or unknown orphan is never offered close, and an active one
includes a host Claude Code says is busy or waiting ("Mid-turn" above).

**Why a live leftover is not closeable** (BUG_099). ``recommend.close_blocker``
names the first guard a live ``leftover`` or ``unmanaged`` host fails, in the
order above, and the ``info`` step carries it as ``reason``, which the panel
words on the host's leftovers row:

+----------------------+-----------------------+-------------------------+
| ``reason``           | The guard             | From                    |
+======================+=======================+=========================+
| ``close_ps``         | ``ps`` describes the  | ``unverified: ps``, or  |
|                      | process               | an attention pid with   |
|                      |                       | nothing read            |
+----------------------+-----------------------+-------------------------+
| ``close_start``      | a start token         | ``unverified: start``,  |
|                      |                       | or a record with no     |
|                      |                       | ``procStart``           |
+----------------------+-----------------------+-------------------------+
| ``close_newer``      | the process started   | ``unverified: newer``   |
|                      | before the attention  |                         |
|                      | record's last write   |                         |
+----------------------+-----------------------+-------------------------+
| ``close_records``    | Claude Code's records | ``unverified: records`` |
|                      | were read             |                         |
+----------------------+-----------------------+-------------------------+
| ``close_other``      | no record names the   | ``unverified: other``   |
|                      | pid for another host  |                         |
+----------------------+-----------------------+-------------------------+
| ``close_cwd``        | a working directory   | ``unverified: cwd``, or |
|                      |                       | a record with no        |
|                      |                       | ``cwd``                 |
|                      |                       | (``rooted_here`` null)  |
+----------------------+-----------------------+-------------------------+
| ``close_not_rooted`` | the directory is in   | ``rooted_here`` false   |
|                      | this project          |                         |
+----------------------+-----------------------+-------------------------+
| ``close_claimed``    | no other project's    | ``claimed_elsewhere``   |
|                      | live session lists it |                         |
+----------------------+-----------------------+-------------------------+

A conflict, a gone or unknown host and an active one keep their own words.

**Attach only when the match is unique both ways.** ``assess`` calls
``scan.mark_rivals`` on each project after ``mark_claims``: an orphan host row
with exactly one adoptable session gets ``adopt_rivals``, the number of the
project's other orphan hosts known alive (``alive`` true) that list the same
session. Adopt is the step only when the host is alive, has exactly one
adoptable session, and has no rivals. Six idle hosts each told to adopt the
one session a project has were offered something none of them could take,
and it hid their Close: now each host that every close guard lets go is
offered Close, and one that fails a guard is offered nothing to run. A gone
host, or one whose liveness is unknown, is nobody's rival. The close guards
themselves are unchanged. A row carries one step; offering Close beside
Attach waits for the panel's second action (:doc:`oracle-panel.md </architecture/oracle-panel>`,
"As built in increment 6").

Every step that acts on a session names it with ``--id`` in its ``argv``
(IDEA_042); none carries the session in ``env``. Until IDEA_042 ``pipeline gates``
and ``pipeline test`` took no ``--id``, and those two steps pinned the session
through ``INVARIANT_SESSION_ID`` in ``env``. A step's ``env`` now carries only the
discovery values below, and the runner still drops the dashboard's own
``INVARIANT_SESSION_ID`` from the child, which also keeps an ambient variable
from contradicting the step's ``--id`` (a contradiction the CLI refuses).

**A re-run is the session's own** (BUG_093). A project whose discovery reads
a value the session's own shell exported (Heap's ``INVARIANT_DESTINATION_ID``,
which refuses to guess with several simulators booted) used to fail the
phone's re-run with exit 2, and with a value found it would have run the whole
suite. Each test run now records what its discovery resolved (state_model.md,
"Domain: pipeline"), and the step replays the session's newest scoped run: the session as ``--id``, its
selectors as ``--selector``, and each recorded value in ``env`` as
``INVARIANT_<KEY>``. That variable is an input to the project's own discovery
script, which still decides; core honours no ``INVARIANT_<NAME>`` override, so
a script that ignores it resolves as it always did. BUG_093 left the ``pipeline test`` CLI
unchanged. A value the project's privacy patterns flag is carried masked,
listed in ``masked``, and ``replay`` names the run its real value is read from:
``{project, root, session, started, run}``. With nothing to replay the step is
``info`` with a ``reason`` (``rerun_unscoped``, ``rerun_unrecorded``), which the panel
words.

.. mermaid::

   sequenceDiagram
       participant Own as the session's own shell
       participant P as domains/pipeline
       participant S as oracle/scan
       participant R as oracle/recommend
       participant A as oracle/actions
       Own->>P: pipeline test --selector S (INVARIANT_DESTINATION_ID exported)
       P->>P: command_for, then discovered_in: record {destination_id: value}
       S->>P: read the record (inert store)
       S->>S: last_scoped: selectors, env masked where flagged, run, started
       R->>R: argv pipeline test --id <session> --selector S, env INVARIANT_DESTINATION_ID (masked), replay
       A->>A: confirm shows the masked value, command_of keeps it with masked and replay
       A->>S: perform: replay_values(replay): the one run, its value masks as shown
       A->>A: the child's environment gets the real value, nothing else does

Adopt is offered only for a host known to be alive: a project that
just tore several sessions down has several transcript-only hosts, and each
would otherwise be told to adopt the same session. The suite parses every
``argv`` in the table with the real CLI parser.

``assess(snapshots, now, thresholds, gates)`` joins each row with its signals
and its recommendation, with the gate cache's answer for it under ``gates``
(``null`` when there is none), and with its current host's ``turn_state``. Each
project carries ``unread``, and the document ``oracle``, the read's own condition
("An older oracle" above). It is the ``invariant oracle list --json``
document, and the body of the watcher's snapshot. How the gate answer changes
a recommendation is in "Shipped: the gate cache" below.

``invariant oracle list``
~~~~~~~~~~~~~~~~~~~~~~~~~

A one-shot scan of every registered project, or of ``--project NAME``, printed
as a status table: a project line with its scan time, then one line per row
flagged ``ALERT``, ``note`` or ``ok`` with its signals, and a ``next`` line with the
recommended command and where to run it. No ``Context`` is built, so it runs
from any directory. See :doc:`cli.md </architecture/cli>`, "Oracle".

Shipped: the watcher and the notifier
-------------------------------------

``oracle/watcher``
~~~~~~~~~~~~~~~~~~

``Watcher(index, notifier, clock, interval, cache, thresholds)`` runs one tick
at a time. A tick does four things in order:

1. **Scan** every registered project with ``scan_all``, read-only as before.
2. **Assess** it with ``assess``, the same document ``oracle list --json`` prints.
3. **Write the snapshot**, ``<oracle home>/oracle/snapshot.json``. It is that
   document plus ``home``, ``version``, ``generated``, ``watcher_pid`` and
   ``interval``, written atomically, so a reader never sees half of one.
4. **De-duplicate and notify** through the ledger,
   ``<oracle home>/oracle/alerts.json``.
5. **The chain step**, unless ``--no-chain``: follow, decide and start the
   next session in each project ("The chain step" below). The tick's result
   carries what it did as ``chain``.

``run(once, max_ticks, on_tick)`` repeats the tick and sleeps so that ticks
start one ``interval`` apart. A tick that raises is logged to stderr, and the
loop goes on. ``watch --once`` calls one tick directly, so a failure there is the
command's error.

**It restarts itself when invariant changes under it** (BUG_089). Each tick's
scan compares the installed tree with the one the watcher started from ("An
older oracle" above). Once it has changed and then held still for 5 seconds
(``build.SETTLE_SECONDS``, so a checkout still writing files is not restarted
into), ``run`` returns between two ticks with ``restarting`` set, and ``oracle watch`` runs itself again through the ``_oracle_restart`` seam:
``build.reexec``, the same interpreter and arguments in the same process, so
launchd keeps the job and its pid. An exec that fails exits 75 instead, and
``KeepAlive`` starts the agent again. Nothing is lost: the snapshot and the
ledger are on disk and written whole inside a tick, and the gate cache is
evaluated again, as after any restart. A rule gap alone never restarts it:
the restarted process would carry the same registry, and an install that
disagrees with itself would restart for ever. ``--once`` never restarts. The
snapshot carries ``build``, the stamp the watcher started from.

**The ledger makes "once" true.** It maps each alert's signal key
(``project|subject|kind|discriminator``, see "oracle/signals") to ``{since, notified}``. Each tick applies these rules:

+----------------------------------+----------------------------------+
| Case                             | What happens                     |
+==================================+==================================+
| A key not in the ledger          | A rising edge. It is added,      |
|                                  | notified, and reported.          |
+----------------------------------+----------------------------------+
| A key already there,             | Not news. Nothing is sent.       |
| ``notified: true``               |                                  |
+----------------------------------+----------------------------------+
| A key already there,             | Owed. Either there was no        |
| ``notified: false``              | notifier (``--no-notify``) or    |
|                                  | delivery failed. The next tick   |
|                                  | that can deliver it does.        |
+----------------------------------+----------------------------------+
| A key the tick no longer sees    | Dropped, so the same condition   |
|                                  | coming back alerts again.        |
+----------------------------------+----------------------------------+
| A key of a project this tick     | Kept. "Cannot read" is not       |
| could not read                   | "cleared", and a broken manifest |
|                                  | must not re-alert everything in  |
|                                  | the project once it is fixed.    |
+----------------------------------+----------------------------------+
| The same condition, recurring as | Has a new discriminator, so it   |
| a new occurrence                 | is a new key and alerts again.   |
|                                  | Examples are a new prompt,       |
|                                  | another failed test run, or a    |
|                                  | different set of refusing rules. |
+----------------------------------+----------------------------------+

Only alerts notify. Markers never do. There is no repeat timer in v1. The
read-modify-write runs under the home's lock (``locks/alerts.lock``), so the
daemon and a ``watch --once`` cannot both announce one edge. After the first
failed delivery in a tick, the rest of that tick's edges are left owed instead
of being tried one by one.

**Both files are derived and safe to delete.** Deleting the snapshot costs
nothing, because the next tick writes it again. Deleting the ledger costs at
most one repeat of each live alert. A ledger that is not a JSON object, or
that has another ``version`` or shape, is replaced, not refused, because it is
the watcher's own derived state and not a record anybody else writes. The
formats are in :doc:`state_model.md </architecture/state_model>`, "User-level state".

**The latency budget.** A permission prompt is written to the sidecar by the
``Notification`` hook at once. The next tick sees it, so the time from prompt
to ping is at most one ``interval`` (5 s) plus one tick. Measured on this
machine: the suite's three projects with five sessions each, all waiting, took
0.10 s for a cold tick and 0.04 s warm, and the suite asserts under 2 s. This
repository's real state root took 0.09 s for a cold ``watch --once``. That is
well inside IDEA_012's 30-second acceptance, and inside the plan's "under 10 s"
with room to spare.

``oracle/notify``
~~~~~~~~~~~~~~~~~

``Notifier`` is a protocol with one method, ``notify(Notification)``. A
``Notification`` has a ``title``, a ``message``, an optional ``subtitle`` and an
optional ``sound``.

- ``FanOutNotifier`` (ADR-027 increment 4, :need:`DEC_ADR_027_D38`) is what
  the seam returns: the Mac banner first, then web push to every phone that
  turned it on ("Web push" below), each channel on its own, so one failing
  never stops or delays the other. It counts as delivered once any channel
  delivered, so the ledgers do not owe a notification the Mac showed; a
  channel that failed beside one that delivered is reported through its
  ``log`` (``oracle watch``'s stderr) and its ``last`` outcomes, and is not
  retried. A channel with nobody to send to raises ``NobodyToNotify``, which
  is neither a delivery nor a failure. Without ``cryptography`` it is the Mac
  alone.
- ``OsascriptNotifier`` runs ``osascript -e 'display notification … with title … subtitle … sound name …'``. The script is one argument, with no shell in
  between. A refusal or a missing ``osascript`` raises ``NotifyError``, which
  exits 1.
- ``RecordingNotifier`` keeps what it was asked to send. It is for tests, and
  its ``fail`` flag makes it refuse.

The watcher titles a notification ``invariant: <project>``. The subtitle is the
row's label, which is the label ``oracle list`` prints (``recommend.row_label``,
one spelling for both). The message is the signal's detail. A high or medium
alert plays ``Glass``, and a low one (finished) is a silent banner.

**The quoting is injected** (ADR-007 D13). Turning text into an AppleScript
string literal already has an owner: ``hosts/claude_code_launch.applescript_string``.
The oracle may not import ``hosts/``, so ``OsascriptNotifier`` takes the quoting
function as an argument. The CLI's ``_oracle_notifier`` seam passes in the
host's function. This is the ``HostProbe`` shape again. The suite replaces the
seam with a recorder for every test, and a ``conftest.py`` guard makes
``subprocess.Popen`` refuse ``osascript`` unless a test is marked
``real_osascript``. The one test that runs it evaluates a string literal and
posts nothing.

``invariant oracle watch`` and ``notify-test``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``watch`` runs the loop until it is interrupted. In text mode it prints only the
ticks that have news. With ``--json`` it prints one line per tick. ``--once`` runs
a single tick and prints its result. ``--no-notify`` sends nothing, and the edges
it sees stay owed to a watcher that does notify. ``notify-test`` posts one
notification through the same seam and prints each channel's outcome: the
Mac banner and each device with web push on, or why push is not available.
Any channel that failed exits 1, naming it, even when another delivered
(:need:`DEC_ADR_027_D43`). See :doc:`cli.md </architecture/cli>`, "Oracle".

Shipped: the gate cache
-----------------------

``oracle/gates``
~~~~~~~~~~~~~~~~

A row says a gate refused only when a refusal was actually met: ``blocked`` in
the attention sidecar (ADR-007 D8). It cannot say which gates are owed now, or
what each wants, and without that the best recommendation is "run ``pipeline gates``". ``GateCache`` supplies the detail by running
``PipelineDomain.evaluate_gates_by_destination``, the evaluation ``pipeline gates``
prints, and keeps the answer in memory.

+----------------------------------+----------------------------------------+
| Rule                             | How                                    |
+==================================+========================================+
| **Never a write**                | The domains are built on an            |
|                                  | ``InertStore`` over the row's state    |
|                                  | root. The manifest is the session      |
|                                  | worktree's own (the main checkout's if |
|                                  | the worktree has none), because the    |
|                                  | rules resolve the paths they compare   |
|                                  | against the manifest's root. The rules |
|                                  | run with ``cwd`` set to the worktree,  |
|                                  | so their git questions are asked       |
|                                  | there, and inside                      |
|                                  | ``core/git.without_optional_locks()``, |
|                                  | so every one runs with                 |
|                                  | ``GIT_OPTIONAL_LOCKS=0``: a plain      |
|                                  | ``git diff`` cannot refresh the index  |
|                                  | stat cache and take ``index.lock``     |
|                                  | under the host working there (ADR-007  |
|                                  | D16).                                  |
+----------------------------------+----------------------------------------+
| **Never while a turn runs**      | A host mid-turn is using that          |
|                                  | worktree, and its own git commands     |
|                                  | must not meet ours. Mid-turn is        |
|                                  | ``signals.turn_state``'s answer, the   |
|                                  | one the signals read ("Mid-turn"       |
|                                  | above): a host Claude Code says is     |
|                                  | busy or waiting, one waiting on a      |
|                                  | prompt, a ``running`` turn heard from  |
|                                  | within an hour, or a host whose turn   |
|                                  | ended with background work still       |
|                                  | running (``background``, BUG_094: a    |
|                                  | background reviewer may be committing  |
|                                  | there) is never evaluated. A session   |
|                                  | whose host is not mid-turn is          |
|                                  | evaluated at once when its ``turn`` is |
|                                  | ``ended``, and otherwise after 2       |
|                                  | minutes without activity: no turn      |
|                                  | known, a gone host, a record that says |
|                                  | ``idle``, or a ``running`` turn gone   |
|                                  | stale.                                 |
+----------------------------------+----------------------------------------+
| **Only when something moved**    | The fingerprint is the session's       |
|                                  | ``state`` and ``heartbeat``, the       |
|                                  | pipeline's ``state`` and transition    |
|                                  | count, and the pipeline record's       |
|                                  | ``st_mtime_ns``. Evidence is recorded  |
|                                  | without a transition, so the write     |
|                                  | time is what moves for a new test run  |
|                                  | or a doc read. The scan supplies all   |
|                                  | five (``PipelineView.transitions``,    |
|                                  | ``written_ns``), so fingerprinting     |
|                                  | costs no extra read.                   |
+----------------------------------+----------------------------------------+
| **Skipped when there is nothing  | A row that is not a session, has no    |
| to ask**                         | pipeline or a terminal one, is         |
|                                  | stranded, or whose worktree (else      |
|                                  | root) is not a directory.              |
+----------------------------------+----------------------------------------+
| **Bounded**                      | Each evaluation runs in a daemon       |
|                                  | thread, at most 2 at once across every |
|                                  | project, with a 10-second timeout. A   |
|                                  | worker that timed out keeps its slot   |
|                                  | until it really ends, so a hung        |
|                                  | ``git`` cannot multiply threads. A     |
|                                  | result later than the timeout is       |
|                                  | dropped.                               |
+----------------------------------+----------------------------------------+
| **A failure keeps the last       | A timeout or an error keeps the        |
| answer**                         | previous result, flagged ``timed_out`` |
|                                  | or with ``error``, and that            |
|                                  | fingerprint is not tried again. The    |
|                                  | next change of fingerprint is.         |
+----------------------------------+----------------------------------------+
| **In memory**                    | Nothing is persisted. The watcher's    |
|                                  | cache lives across its ticks; a        |
|                                  | restarted watcher evaluates again. The |
|                                  | answer reaches the snapshot only as    |
|                                  | part of the assessed document.         |
+----------------------------------+----------------------------------------+

``refresh(snapshots, now, wait)`` starts what is owed, waits up to ``wait``
seconds and returns a ``GateReport`` per ``(state root, session id)``: the gates
not met, once each, blocking first, each with its level, the first line of
its message, its first fix and every edge out of the state that owes it. A
report whose fingerprint has moved since is ``stale``. The answers of sessions
that are no longer live in a scanned project are forgotten.

**It feeds the recommendation, never an alert.** ``recommend(row, signals, gates)`` takes the report as an argument, so it stays pure, and ``classify``
never sees it: the signals, and so the ledger and the notifications, are the
same with or without it. The suite checks that.

+----------------------------------+-------------------------------------------------------+
| Row                              | What the report adds                                  |
+==================================+=======================================================+
| ``gate_blocked`` (or failed      | ``detail``:                                           |
| tests on a refused row)          | ``owed: <rule> (<level>, to <edges>): <message>; …``. |
|                                  | A stale report is prefixed                            |
|                                  | ``as of <pipeline state>,``. A report with nothing    |
|                                  | owed says the refusal may already be cleared. A       |
|                                  | report with only a timeout or an error adds nothing.  |
+----------------------------------+-------------------------------------------------------+
| Any other session row with a     | ``detail``:                                           |
| step                             | ``next owed: <blocking rules> (to <first edge>)``,    |
|                                  | from a current report only.                           |
+----------------------------------+-------------------------------------------------------+
| A session row with no step       | The same hint becomes its step, an ``info`` one with  |
|                                  | no command.                                           |
+----------------------------------+-------------------------------------------------------+

**Where it is wired.**

+-----------------------------------+----------------------------------+
| Caller                            | Wait                             |
+===================================+==================================+
| ``Watcher(gates=…, gate_wait=0)`` | The loop never waits: a tick     |
|                                   | starts what is owed and reports  |
|                                   | what has finished. An evaluation |
|                                   | that finishes between ticks is   |
|                                   | the next tick's.                 |
+-----------------------------------+----------------------------------+
| ``oracle watch --once``           | Waits up to the timeout, because |
|                                   | it is a one-shot like ``list``.  |
+-----------------------------------+----------------------------------+
| ``oracle list``                   | Evaluates the eligible sessions  |
|                                   | once and waits up to the         |
|                                   | timeout.                         |
+-----------------------------------+----------------------------------+

The CLI reaches the cache through the ``_oracle_gates`` seam, as it does the scan
cache. The text output prints the detail on its own line under ``next``. The
seam also hands the cache the host's instruction files for each manifest
(``evaluate_row``'s ``instruction_files_for``), because the landing's
``guidelines.generated_current`` reads them as the default of
``guidelines.instruction_files`` and the oracle imports no host (ADR-012 D3).
Without that the cache could pass a drifted ``CLAUDE.md`` block that ``pipeline gates`` reports.

**Measured** on this repository's real state root, against a scratch
``INVARIANT_HOME``, on 2026-10-22: one live session, evaluated in 0.02 s in its
worker. Watcher ticks with the gate cache took 0.11 s cold and about 0.03 s
warm, the same as without it, and a ``watch --once`` that waited for the gates
took 0.12 s. The suite stalls an evaluation and checks that the tick still
returns in under 2 s.

Shipped: the launchd agent
--------------------------

.. comp:: The watcher's LaunchAgent
   :id: COMP_AGENT_ORACLE_WATCH
   :uses: COMP_ORACLE_WATCHER

   The user LaunchAgent that keeps ``invariant oracle watch`` running on macOS,
   its plist under ``~/Library/LaunchAgents`` written by ``oracle/launchd``
   (ADR-007 D17).


``oracle/launchd``
~~~~~~~~~~~~~~~~~~

The watcher is useful only while it runs, so on macOS it is a user
LaunchAgent: ``~/Library/LaunchAgents/dev.invariant.oracle.plist``, loaded into
the person's GUI domain, ``gui/<uid>``. It is the first long-lived process this
library starts (ADR-007 D17).

``plist_for(python, home, interval, path, pin_home)`` builds the property list,
and ``plist_bytes`` writes it with ``plistlib``:

+--------------------------+-------------------------------------------------------------+------------------------------------+
| Key                      | Value                                                       | Why                                |
+==========================+=============================================================+====================================+
| ``Label``                | ``dev.invariant.oracle``                                    | One agent per user.                |
+--------------------------+-------------------------------------------------------------+------------------------------------+
| ``ProgramArguments``     | ``[<python>, -m, invariant, oracle, watch, --interval, N]`` | The absolute interpreter that ran  |
|                          |                                                             | ``install``, unresolved. A virtual |
|                          |                                                             | environment's ``python`` is a      |
|                          |                                                             | symlink and finds its environment  |
|                          |                                                             | from its own path, so resolving it |
|                          |                                                             | would lose the environment.        |
|                          |                                                             | ``-m invariant``, not the console  |
|                          |                                                             | script, so nothing depends on      |
|                          |                                                             | ``PATH``.                          |
+--------------------------+-------------------------------------------------------------+------------------------------------+
| ``RunAtLoad``,           | ``true``                                                    | Starts at login and is restarted   |
| ``KeepAlive``            |                                                             | if it exits.                       |
+--------------------------+-------------------------------------------------------------+------------------------------------+
| ``StandardOutPath``,     | ``<oracle home>/oracle/watcher.log``                        | Both streams. The watcher prints   |
| ``StandardErrorPath``    |                                                             | only ticks with news and failed    |
|                          |                                                             | ticks, so the log grows slowly.    |
|                          |                                                             | ``install`` creates the directory, |
|                          |                                                             | because launchd does not.          |
+--------------------------+-------------------------------------------------------------+------------------------------------+
| ``EnvironmentVariables`` | ``PATH``, and ``INVARIANT_HOME`` only if it was set         | launchd's ``PATH`` is minimal.     |
|                          |                                                             | This one is the directories        |
|                          |                                                             | ``git``, ``osascript`` and         |
|                          |                                                             | ``claude`` (the chain step opens   |
|                          |                                                             | it, ADR-027) are found in now,     |
|                          |                                                             | then                               |
|                          |                                                             | ``/usr/bin:/bin:/usr/sbin:/sbin``. |
|                          |                                                             | A program this ``PATH`` lacks is   |
|                          |                                                             | looked for in                      |
|                          |                                                             | ``/opt/homebrew/bin``,             |
|                          |                                                             | ``/usr/local/bin``,                |
|                          |                                                             | ``~/.local/bin`` and               |
|                          |                                                             | ``~/.claude/local``; one found     |
|                          |                                                             | nowhere is a warning naming it,    |
|                          |                                                             | never dropped in silence (BUG_155, |
|                          |                                                             | ADR-028). An unset                 |
|                          |                                                             | ``INVARIANT_HOME`` stays unset, so |
|                          |                                                             | it keeps meaning ``~/.invariant``. |
+--------------------------+-------------------------------------------------------------+------------------------------------+

``LaunchdInstaller(agents_dir, uid, run, sleep)`` owns the plist file and the
``launchctl`` calls. ``launchctl`` is reached only through ``run``, an injected
``(argv) -> (status, text)``; the CLI passes ``run_launchctl``, which runs
``/bin/launchctl``.

+-------------------------+--------------------------------------------+
| Operation               | Calls                                      |
+=========================+============================================+
| ``install(plist)``      | ``print gui/<uid>/dev.invariant.oracle``   |
|                         | to see whether it is loaded. If it is:     |
|                         | ``bootout`` that service, then ``print``   |
|                         | until it is gone (up to 5 s), because      |
|                         | ``bootout`` returns before the process has |
|                         | left and launchd refuses to bootstrap a    |
|                         | label still loaded. Then the plist is      |
|                         | written atomically and                     |
|                         | ``bootstrap gui/<uid> <plist>`` loads it.  |
|                         | A refusal is a ``LaunchdError`` (exit 1)   |
|                         | that names the plist.                      |
+-------------------------+--------------------------------------------+
| ``uninstall()``         | ``print``; ``bootout`` and wait, if        |
|                         | loaded; remove the plist, if present.      |
|                         | Nothing installed is nothing to do. A      |
|                         | ``bootout`` that fails because the agent   |
|                         | already left is not an error.              |
+-------------------------+--------------------------------------------+
| ``--dry-run`` of either | Only the ``print``, so the steps reported  |
|                         | are the ones a real run would make. No     |
|                         | file is written, no directory created,     |
|                         | nothing loaded or unloaded.                |
+-------------------------+--------------------------------------------+
| ``status()``            | Whether the plist exists, whether          |
|                         | ``print`` finds the agent, the interpreter |
|                         | it names and whether that still exists,    |
|                         | its interval, and ``missing_programs``:    |
|                         | the programs its label needs               |
|                         | (``AGENT_PROGRAMS``) that the plist's own  |
|                         | ``PATH`` cannot find, asked of             |
|                         | ``shutil.which`` as the agent would        |
|                         | (BUG_155).                                 |
+-------------------------+--------------------------------------------+
| ``interpreter_check()`` | ``doctor``'s row. Reads the plist only,    |
|                         | asks launchd nothing. ``None`` with no     |
|                         | plist.                                     |
+-------------------------+--------------------------------------------+
| ``programs_check()``    | ``doctor``'s programs row: the same read   |
|                         | as ``missing_programs``. ``None`` with no  |
|                         | plist or an unreadable one, which the      |
|                         | interpreter row reports.                   |
+-------------------------+--------------------------------------------+

``RecordingLaunchctl`` is a fake launchd for tests: it records every call,
keeps a loaded set, can refuse a verb, and can keep an agent "exiting" for a
number of ``print`` calls after its ``bootout``. ``recording_installer(dir)`` pairs
it with a temporary directory and a sleep that does not sleep.

``invariant oracle install``, ``uninstall`` and ``status``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``install [--interval 5] [--dry-run]`` writes and loads the agent; a second
``install`` replaces it cleanly. ``uninstall [--dry-run]`` unloads it and removes
the plist. ``status`` reports the agent and the snapshot: the snapshot's age is
its modification time (``watcher.snapshot_status``), and it is **stale** when it
is older than three of its own intervals, or missing. ``doctor`` adds one row
when the plist exists: ``oracle agent interpreter``, failing when the
interpreter has moved. ``install`` prints a note for each program it found only
in a well-known directory and a warning naming each it found nowhere, and
``status`` a ``programs`` line naming each the plist's ``PATH`` cannot find;
``doctor``'s ``oracle agent programs`` row warns of the same, and never fails
(BUG_155, ADR-028). See :doc:`cli.md </architecture/cli>`, "Oracle".

Both also say whether the processes run the installed invariant (BUG_089).
``status`` stamps the installed tree (``build.stamp``) and compares it with the
``build`` a fresh snapshot carries, and with the page's report: ``build current (<digest>)``, or ``OLDER than the installed invariant``, which a fresh
snapshot with no stamp is too, since every build that writes snapshots stamps
them, and so is one whose own document carries a skew. ``doctor`` adds ``oracle watcher build`` when the watcher's plist exists and a fresh snapshot is behind
it, and ``oracle web build`` beside the page's rows; each fails while the
process is older. A stale snapshot or a page whose process is gone says
nothing about a running build, so there is no line and no row.

The CLI reaches the installer through the ``_oracle_installer`` seam. The suite
replaces it for every test with a recording installer over a temporary
``LaunchAgents`` directory, and the ``conftest.py`` guard makes ``subprocess.Popen``
refuse ``launchctl`` outright, with no marker that admits it. Real ``launchctl``
never runs in the suite.

**The guard** raises ``RefusedProgram``, a ``BaseException``, so code under test
that catches ``Exception`` (the watcher's notify path does, on purpose) cannot
swallow it. It reads the program from an argument list, a bare string, bytes
or a path (``os.fspath``), an ``executable=`` override, and, best effort, a shell
command string: ``shell=True``, or ``sh``/``bash``/``zsh -c``, where each command's
first word after assignments and ``exec``/``env`` wrappers is checked, one level
of nested ``sh -c`` included.

Shipped: the dashboard
----------------------

``invariant oracle tui`` is a live terminal dashboard. It is a Textual app, and
Textual ships as the ``invariant[oracle]`` extra, so the core install does not
carry it (ADR-007 D19). The command imports ``oracle/tui`` inside itself and
nowhere else; without Textual it is a usage error naming the extra.

Three modules, split so that only the drawing needs Textual:

+---------------+----------------------------+--------------------------+
| Module        | Owns                       | Never does               |
+===============+============================+==========================+
| ``dashboard`` | ``load(home, now, scan)``: | Import Textual; run      |
|               | the watcher's snapshot     | anything                 |
|               | while it is fresh, else    |                          |
|               | the ``scan`` it is handed, |                          |
|               | behind a banner.           |                          |
|               | ``lines(document)``: each  |                          |
|               | row as a table line, each  |                          |
|               | line's tone, the status    |                          |
|               | flag ``oracle list``       |                          |
|               | prints too                 |                          |
+---------------+----------------------------+--------------------------+
| ``actions``   | ``Step``, a row's          | Import ``hosts/``; run a |
|               | recommendation as the      | step nobody confirmed;   |
|               | confirm modal shows it;    | answer a prompt          |
|               | ``confirm_lines``;         |                          |
|               | ``refusal``;               |                          |
|               | ``child_env``;             |                          |
|               | ``Actions``, the dispatch  |                          |
|               | on a confirmed step        |                          |
|               | through an injected runner |                          |
|               | and terminal launcher;     |                          |
|               | ``run_subprocess``, the    |                          |
|               | real runner;               |                          |
|               | ``RecordingRunner`` for    |                          |
|               | tests                      |                          |
+---------------+----------------------------+--------------------------+
| ``tui``       | ``OracleApp``, the table   | Decide anything the two  |
|               | and its refresh, and four  | modules above decide, or |
|               | modals: ``ConfirmScreen``, | what a start would be    |
|               | ``InfoScreen``,            |                          |
|               | ``OutputScreen`` and       |                          |
|               | ``StartScreen``, the ``n`` |                          |
|               | key's "New session"        |                          |
|               | confirm ("Start a          |                          |
|               | session", "From the        |                          |
|               | dashboard")                |                          |
+---------------+----------------------------+--------------------------+

**Where the rows come from.** ``load`` asks ``watcher.snapshot_status``, the rule
``oracle status`` reports. A snapshot that exists, is not older than three of
its own intervals, and parses as the ``assess`` document is shown as it is. One
that is missing, stale or unreadable makes the dashboard scan in-process,
through the same ``scan_all`` and ``assess`` path as ``oracle list``, and show a
banner: "watcher not running", why, and that ``oracle install`` keeps one
running. The in-process read asks the gate cache without waiting, so gate
detail appears from the next refresh on. Either way the table refreshes on
the snapshot's interval (5 s by default), in a worker thread, and keeps the
cursor on the row it was on.

**It always refreshes** (ADR-008 D6). The timer is armed when the app mounts,
at 5 s, before the first read, and re-armed when a snapshot names another
interval. A read that fails shows its error in the banner and leaves the
timer running, so a first read that fails is retried on the next tick. A line
under the banner says ``updated N s ago``, every second: the rows' age when
read (the snapshot's age, or 0 for an in-process scan) plus the time since.
It turns red with ``STALE`` once that is older than three intervals.
``dashboard.freshness`` words it, and the web page and ``oracle status`` use the
same rule. Once the app has begun shutting down, a freshness tick or a read's
result that arrives shows nothing: textual removes the widgets before it
stops the timers and cancels the read (BUG_134).

**The table.** Project by project: each live session, with its hosts nested
under it (current first, then earlier ones), then the orphan hosts, the
leaked worktrees and the error rows.

+--------------+-------------------------------------------------------+
| Column       | Shows                                                 |
+==============+=======================================================+
| project      | the project's name (blank on a nested host line)      |
+--------------+-------------------------------------------------------+
| session      | ``row_label``, the name ``oracle list`` and the       |
|              | notifications use                                     |
+--------------+-------------------------------------------------------+
| stage / lane | a session's stage and its pipeline, as                |
|              | ``working · tdd:green``;                              |
|              | ``orphan host · alive, pid N`` (its liveness as the   |
|              | status column words it), ``leaked worktree``,         |
|              | ``unreadable``; a nested host's ``current host`` or   |
|              | ``earlier host``                                      |
+--------------+-------------------------------------------------------+
| status       | ``ALERT``, ``note`` or ``ok``; a nested host's        |
|              | liveness, its pid (or the pids its sources disagree   |
|              | on) and what it waits on                              |
+--------------+-------------------------------------------------------+
| signals      | each signal and its detail: a high alert red, any     |
|              | other alert amber, a marker dim                       |
+--------------+-------------------------------------------------------+
| next         | the recommended command, or the step's label when     |
|              | there is nothing to run                               |
+--------------+-------------------------------------------------------+

A row with a high alert is drawn red throughout, and a host waiting on a
permission prompt or a question is a high alert. Measured with the suite's
three projects and five sessions, from a stale snapshot so it scans
in-process: the first render took 0.4 s, inside the 5-second acceptance.

Confirm-first actions
~~~~~~~~~~~~~~~~~~~~~

Enter (or ``a``) on a line acts on its row; a nested host line acts for its
session. Nothing runs from the key itself (ADR-007 D18):

+--------------+---------------------------+--------------------------------------------------------+
| Step         | The modal                 | On ``y`` or Run                                        |
+==============+===========================+========================================================+
| ``info``     | The label and detail. No  | nothing; any key closes it                             |
|              | Run button                |                                                        |
+--------------+---------------------------+--------------------------------------------------------+
| ``run``      | ``run <argv>``,           | the runner starts it: no shell, no stdin, a 600 s      |
|              | ``in <cwd>``, each        | limit. A masked variable's real value is joined first, |
|              | variable ``env`` adds (a  | into the child's environment only. The output and exit |
|              | masked one as shown,      | status are shown, and the table refreshes              |
|              | marked "masked; the       |                                                        |
|              | session's recorded value  |                                                        |
|              | is used when it runs"),   |                                                        |
|              | and a note that host      |                                                        |
|              | markers and               |                                                        |
|              | ``INVARIANT_SESSION_ID``  |                                                        |
|              | are removed. Cancel is    |                                                        |
|              | focused                   |                                                        |
+--------------+---------------------------+--------------------------------------------------------+
| ``terminal`` | The same, plus the exact  | a ``session handoff`` launch:                          |
|              | line a new Terminal       | ``claude --name <session> --remote-control <session>`` |
|              | window will run           | in the ``cwd``, whose first prompt asks it to run the  |
|              |                           | step's command. It counts once the successor is seen   |
|              |                           | alive. No note is recorded and no host is ended        |
+--------------+---------------------------+--------------------------------------------------------+
| refused      | Why it will not run       | nothing                                                |
+--------------+---------------------------+--------------------------------------------------------+

**A masked value is joined when it runs, and only then** (BUG_093, the user's
ruling: use it, but mask it). The real value is never in a step, a confirm,
a Copy line, the snapshot or the audit log. ``Actions.perform`` asks its
``reveal`` (by default ``actions.recorded_values``, which is
``scan.replay_values``) for the values behind the step's ``masked`` names, from
the run its ``replay`` names, and adds them to the child's environment. A run is
appended and never rewritten, and ``started`` tells one pipeline of a session
from the next, so the reference names one set of values for good; each must
still mask to what was shown. A pipeline that moved, a run that is gone, or a
value that no longer masks alike runs nothing, and the outcome says why.
``command_of`` keeps the masked value, the masked names and the reference, so
two runs whose values mask alike are still two commands: a confirm for one
does not run the other.

``refusal`` refuses a runnable step on a row whose host is waiting on a prompt:
no confirmed action answers one, and a waiting row recommends nothing to run in
the first place. As built the oracle answers no prompt at all; ADR-029
(proposed) plans answers from the phone on a separate path, and this refusal
stays as it is. It also refuses an ``argv`` that is not an ``invariant``
command, a ``cwd`` that does not exist, and a kind it does not know, because
the snapshot is a file and a file is not a reason to run a program.

``n`` opens the "New session" modal, a start in any registered project; its
rules are under "Start a session", "From the dashboard (increment 3)".

**The host facts are injected.** Which variables are host markers
(``without_host_markers``) and how a terminal is opened (``_handoff_launcher``,
``plan_launch`` and the successor watch) are ``hosts/claude_code_launch``'s. The
CLI's ``_oracle_actions`` hands them to ``Actions``, so the oracle still imports
nothing from ``hosts/``. The runner is reached through ``_oracle_runner``. The
suite replaces both seams for every test with recorders (``RecordingRunner``,
and an inert launcher), and the ``osascript`` guard stays the backstop.

**Setting it up.** See the installation guide's "The Session Oracle" section:
``pip install 'invariant[oracle]'`` (or add Textual to a uv tool install), then
``invariant oracle install`` and ``invariant oracle tui``.

Shipped: the web page (IDEA_023 increment 1)
--------------------------------------------

``invariant oracle web`` serves the dashboard to a phone over the person's
tailnet, with the TUI's confirm-first actions, each confirmed on the page
(increment 2), and ``oracle install --web`` keeps it served by a second
LaunchAgent (increment 3, "The page, always on" below). The decisions are
ADR-008's. The person-facing setup is the guide's
:doc:`The oracle on your phone </guide/oracle-web>`.

+----------------+-------------------------------------+--------------------------+
| Module         | Owns                                | Never does               |
+================+=====================================+==========================+
| ``web``        | Which addresses to bind             | Bind the wildcard; read  |
|                | (``discover_addresses``,            | anything for an unpaired |
|                | ``choose_bind``), the ``Host``      | request; decide what may |
|                | check, the page's view of the       | run; import ``hosts/``   |
|                | ``assess`` document (``view``,      | or Textual               |
|                | which is ``panel.view``, with the   |                          |
|                | device's recent jobs) and its       |                          |
|                | freshness fields, ``WebApp``        |                          |
|                | (request in, response out, no       |                          |
|                | socket: the routes, the token, the  |                          |
|                | ``Origin`` rules, the action body), |                          |
|                | ``WebServer`` (one                  |                          |
|                | ``ThreadingHTTPServer`` per         |                          |
|                | address, with its timeout and caps; |                          |
|                | with a ``Served``, TLS per          |                          |
|                | connection in that connection's     |                          |
|                | thread, and a ``307`` to the https  |                          |
|                | name for plain HTTP), the HTTPS     |                          |
|                | rules (only the certificate's name  |                          |
|                | as ``Host``, the ``https`` origin,  |                          |
|                | the ``Secure`` cookie), the         |                          |
|                | plain-HTTP warning, and             |                          |
|                | ``RecordingWeb`` for tests          |                          |
+----------------+-------------------------------------+--------------------------+
| ``panel``      | The control panel's view model      | Import ``actions``,      |
|                | (IDEA_027, ADR-009): the attention  | ``webactions`` or        |
|                | groups and their order, the summary | ``hosts/``; show an      |
|                | strip's counts, the leftovers, each | engine name, a path, an  |
|                | session's detail, and the one       | argv, a pid or a host id |
|                | wording table (``WORDS``, chips,    | outside a detail's       |
|                | sentences, lanes and phases,        | "Technical details";     |
|                | ``step_words``, ``confirm_words``,  | give a fact it does not  |
|                | ``result_words``, ``why_words``,    | know a phrase            |
|                | ``copy_line``). Pure: the           |                          |
|                | ``assess`` document in, words out   |                          |
+----------------+-------------------------------------+--------------------------+
| ``webactions`` | The remote confirm on top of        | Run a step the server    |
|                | ``actions``: ``RemoteActions`` (the | did not derive itself;   |
|                | confirm view, with the sheet's      | add a refusal of its     |
|                | words from ``panel``, the action,   | own; read anything a     |
|                | the job with its result's words,    | client sends but the row |
|                | and its polling), ``Confirms`` (the | key and the nonce;       |
|                | nonce book), ``AuditLog``           | import ``hosts/``; name  |
|                | (``web/actions.log``),              | a cause the output does  |
|                | ``find_row``; the failure summary   | not state                |
|                | (``evidence``, ``summarise``): a    |                          |
|                | failed job's invariant-written line |                          |
|                | and last meaningful line, worded by |                          |
|                | ``panel.why_words``                 |                          |
+----------------+-------------------------------------+--------------------------+
| ``webpage``    | The HTML: the self-contained        | Fetch anything from      |
|                | control panel with its token CSS    | another origin; build an |
|                | and its script (the overview,       | element from markup;     |
|                | detail and leftovers screens, the   | post anything but a row  |
|                | confirm and result bottom sheets,   | key and a nonce; hold a  |
|                | copy), ``WORDS`` embedded from      | word of its own          |
|                | ``panel``, and the "not paired",    |                          |
|                | pairing-form and message pages; the |                          |
|                | home-screen app's ``<head>``, its   |                          |
|                | manifest (``manifest``) and its     |                          |
|                | icons (``icon``, PNGs drawn from    |                          |
|                | the tokens)                         |                          |
+----------------+-------------------------------------+--------------------------+
| ``devices``    | ``<oracle home>/web/devices.json``: | Keep a token or a code   |
|                | one-time pairing codes and paired   | in the clear; let a      |
|                | devices' token hashes; issue,       | device in when the file  |
|                | redeem, verify, list, revoke        | cannot be read; create   |
|                |                                     | the file readable by     |
|                |                                     | anyone else              |
+----------------+-------------------------------------+--------------------------+
| ``webservice`` | ``WebService``, the loop behind     | Bind an address outside  |
|                | ``oracle web --wait``: wait for a   | Tailscale's ranges; exit |
|                | tailnet address, bind it, follow    | for a missing tailnet;   |
|                | it, retry a failed bind; keep the   | ask launchd anything;    |
|                | certificate due, swap a renewal     | cut off a running action |
|                | into the listener, listen again     | to restart               |
|                | over HTTPS or plain HTTP when that  |                          |
|                | changes; restart once invariant     |                          |
|                | changes under it, after the running |                          |
|                | actions finish (BUG_089); its       |                          |
|                | report, ``oracle/web.json``;        |                          |
|                | ``web_status``, ``cert_text`` and   |                          |
|                | their wording, which                |                          |
|                | ``oracle status`` and ``doctor``    |                          |
|                | read                                |                          |
+----------------+-------------------------------------+--------------------------+
| ``tls``        | The page's certificate (IDEA_026):  | Run ``tailscale`` except |
|                | ``issue``, Tailscale's CLI asked    | through its injected     |
|                | through stdout; ``split_pem`` and   | runner; write the key    |
|                | ``cert_info``, the chain, the key,  | anywhere but its         |
|                | the names and the expiry, read with | owner-only file; replace |
|                | the standard library;               | a good pair with one     |
|                | ``Certificates``, the kept          | that does not load       |
|                | ``web/tls/oracle.pem`` and when to  |                          |
|                | ask again; ``Served``, the          |                          |
|                | certificate and context a listener  |                          |
|                | serves, swapped together;           |                          |
|                | ``RecordingIssuer`` for tests       |                          |
+----------------+-------------------------------------+--------------------------+

Where it listens
~~~~~~~~~~~~~~~~

.. invariant: allow-privacy

Only on the Mac's Tailscale addresses: those in ``100.64.0.0/10`` or
``fd7a:115c:a1e0::/48``, as ``tailscale ip`` reports them, else as ``ifconfig``
lists them on a tunnel interface (``utun*``, ``tailscale*``). Both run through
``web.run_program``, which gives the program ``TERM=dumb`` when the environment
has no ``TERM`` (see "Over HTTPS"). Never the wildcard.
With none, ``oracle web`` refuses to start (exit 1). ``--allow-loopback`` serves
on ``127.0.0.1`` instead, only when no tailnet address exists. Over HTTPS, a
request whose ``Host`` is not the certificate's name gets a 421 naming the
https URL. Over plain HTTP, one whose ``Host`` is not a bound address, a
``*.ts.net`` name or, on loopback, ``localhost``, gets a 421 (ADR-008 D1, D2,
D22).

Who gets an answer
~~~~~~~~~~~~~~~~~~

+--------------------------------+----------------------+---------------------------------------------------------------------+
| Request                        | Unpaired             | Paired                                                              |
+================================+======================+=====================================================================+
| ``GET /pair?code=…``           | the pairing form,    | the same                                                            |
|                                | code filled in;      |                                                                     |
|                                | spends nothing       |                                                                     |
+--------------------------------+----------------------+---------------------------------------------------------------------+
| ``POST /pair`` (form, same     | redeems the code: a  | the same                                                            |
| origin)                        | 303 to ``/`` with    |                                                                     |
|                                | the cookie, else 403 |                                                                     |
+--------------------------------+----------------------+---------------------------------------------------------------------+
| ``GET /``                      | 401, a minimal "not  | the page                                                            |
|                                | paired" page         |                                                                     |
+--------------------------------+----------------------+---------------------------------------------------------------------+
| ``GET /api/state``             | 401, the same page   | ``{document, view, freshness, jobs}``; a read that raises is a 503  |
|                                |                      | with its error, and the next read runs                              |
+--------------------------------+----------------------+---------------------------------------------------------------------+
| ``GET /api/confirm?row=<key>`` | 401                  | what the confirm shows:                                             |
|                                |                      | ``{row, subject, kind, lines, words, refused, nonce, expires_in}``; |
|                                |                      | no ``row`` is a 400, a row not there a 404                          |
+--------------------------------+----------------------+---------------------------------------------------------------------+
| ``POST /api/act`` (JSON or     | 401                  | needs a same-origin ``Origin``: 202 with the job, else 403 (origin, |
| form: ``row``, ``nonce``)      |                      | nonce), 409 (refused, changed, busy), 503 (no read, no audit)       |
+--------------------------------+----------------------+---------------------------------------------------------------------+
| ``GET /api/job?id=<id>``       | 401                  | the job, to the device that started it; else 404                    |
+--------------------------------+----------------------+---------------------------------------------------------------------+
| ``GET /manifest.webmanifest``  | 401                  | the home-screen app's manifest, ``application/manifest+json``       |
|                                |                      | (IDEA_027 inc 4, ADR-009 D10); reads nothing                        |
+--------------------------------+----------------------+---------------------------------------------------------------------+
| ``GET /icon-180.png``,         | 401                  | the app's icon at that size, ``image/png``, drawn by                |
| ``/icon-192.png``,             |                      | ``webpage.icon``; reads nothing                                     |
| ``/icon-512.png``              |                      |                                                                     |
+--------------------------------+----------------------+---------------------------------------------------------------------+
| ``GET /sw.js``                 | 401                  | the service worker (ADR-027 inc 4), ``text/javascript``, under its  |
|                                |                      | own ``default-src 'none'; img-src 'self'``; while push is offered,  |
|                                |                      | else 404                                                            |
+--------------------------------+----------------------+---------------------------------------------------------------------+
| ``POST /api/push/subscribe``   | 401                  | needs a same-origin ``Origin``: keeps the browser's subscription    |
| (JSON)                         |                      | for this device, 200 ``{on}``; 403 (origin), 400 (not one the       |
|                                |                      | oracle keeps), 409 (push not offered now), 413, 503 (cannot write)  |
+--------------------------------+----------------------+---------------------------------------------------------------------+
| ``POST /api/push/unsubscribe`` | 401                  | needs a same-origin ``Origin``: drops ``endpoint``, or every one    |
| (JSON ``{endpoint}``)          |                      | of this device's; 200 ``{removed}``, over HTTP too                  |
+--------------------------------+----------------------+---------------------------------------------------------------------+
| anything else                  | 401                  | 404, or 405 for a method that is not ``GET``                        |
+--------------------------------+----------------------+---------------------------------------------------------------------+

The token is the ``invariant_oracle`` cookie (``HttpOnly; SameSite=Strict``, and
``Secure`` over HTTPS), or an ``Authorization: Bearer`` header. The token is checked before anything is
read, so an unpaired request costs no scan. Every response is ``no-store``,
``nosniff`` and ``Referrer-Policy: same-origin``: a referrer, and a ``?code=`` in
it, never leaves the origin, while the page's own form ``POST`` keeps its real
``Origin``. Under ``no-referrer`` a browser sent that ``POST`` with ``Origin: null``
and the pairing form refused itself (BUG_079, ADR-008 D5). The page's
Content-Security-Policy admits only its own nonce-tagged script and style,
``connect-src 'self'``, and, for the home-screen app, ``manifest-src 'self'``
and ``img-src 'self'`` (ADR-009 D10), and, for web push, ``worker-src 'self'``
(ADR-027 increment 4).

**The home-screen app** (IDEA_027 increment 4, ADR-009 D10). The panel's
``<head>`` links the manifest with ``crossorigin="use-credentials"``, so the
browser sends the device's cookie with that fetch, and ``/icon-180.png`` as
the ``apple-touch-icon``, and carries ``apple-mobile-web-app-capable``, a
``black-translucent`` status bar, the app's title and a ``theme-color``. The
manifest (``webpage.manifest``) names the app from ``panel.WORDS`` ("Session
Oracle", short name "Oracle"), opens ``/`` standalone within scope ``/``, takes
its background and theme colour from the ``bg`` token, and lists the 192- and
512-pixel icons. The icons are PNGs ``webpage.icon`` writes with ``zlib`` from the
tokens, the summary strip's four accents as a two-by-two grid on the page's
ground, inside a maskable icon's safe circle; nothing is fetched or shipped
as a file. All four routes sit behind the token like every other (ADR-008
D4), so an unpaired device gets the "not paired" page for them too. Every
page sets ``viewport-fit=cover`` and pads by ``env(safe-area-inset-*)``, and the
bottom sheet pads by the bottom and side insets. The "not paired" page links
``/pair``, so a home-screen app that does not share the browser's cookie can be
paired from inside itself. On an iPhone (2026-10-25) iOS sent the
cookie when it fetched the touch icon, and the home-screen app kept Safari's
pairing, so the token rule needs no exception (:doc:`oracle-panel.md </architecture/oracle-panel>`).

What the page shows
~~~~~~~~~~~~~~~~~~~

``document`` is ``oracle list --json``'s, read through ``dashboard.load`` (the
snapshot while fresh, else a scan, behind its banner). One read runs at a
time. ``view`` is ``panel.view`` of it, the control panel's view model
(IDEA_027 increment 3, ADR-009 D2–D5, D8): the summary strip's four tiles,
the groups in attention order (needs you, problems, working, idle) with
their cards, the leftovers card and screen, and each session's detail,
keyed by its row key. ``jobs`` are the device's last 20 actions, each with its
result in words. ``freshness`` is ``{source, age, interval, stale_after, stale, text, served_at, banner}``. The screens, components, tokens and every word are
specified in :doc:`oracle-panel.md </architecture/oracle-panel>`; this section says how they
are served.

.. mermaid::

   graph LR
       DOC["assess document"] --> PV["panel.view"]
       JOBS["device's jobs"] --> PV
       CHAINS["ChainReader.read: ledger, queues, usage hold (ADR-027 inc 3)"] --> PV
       PV --> STATE["/api/state: view"]
       WORDS["panel.WORDS"] --> PAGE["GET /: the page, WORDS embedded"]
       STATE --> SCRIPT["ORACLE.screen: overview, detail, leftovers or chain"]
       PAGE --> SCRIPT

**The page holds no vocabulary** (ADR-009 D3). The fixed strings are
``panel.WORDS``, embedded in the script as JSON when ``GET /`` is served (with
``<``, ``>`` and ``&`` escaped, so no word can end the script). Every other word,
the chips, sentences, button labels, ages and the leftovers' lines, arrives
in ``view``. The default view, the strip, the cards and the leftovers card,
holds no path, argv, pid, host id or engine state name, which a test checks
against the machine fixture. Those live in a detail's "Technical details".

**Drawing.** The script's ``ORACLE`` object owns the loop and every screen's
drawing: ``ORACLE.screen(view, ui, h, on, now)`` takes an element builder ``h``
and the actions ``on``, so the suite runs it, and the whole script against a
fake document, under ``node`` or macOS's ``jsc`` (``tests/test_oracle_webpage.py``).
The detail and leftovers screens are views inside the page, not routes, and
``history.pushState`` makes the phone's back gesture return to the overview at
the scroll position it left. A detail whose session has gone falls back to
the overview.

**Refresh.** The script fetches ``/api/state`` on a timer at the snapshot's
interval, with no meta refresh. After a failure it waits the interval
doubled per failure, up to 60 s, says so under the header, and never stops.
The freshness line says ``Live · updated N s ago`` and turns red, ``Stale · updated N s ago``, once the rows are older than ``stale_after``, three
intervals. A refresh redraws the screen the person is on and keeps the open
detail, every open disclosure and the scroll position. An open sheet is not
redrawn by a refresh.

Actions from the page
~~~~~~~~~~~~~~~~~~~~~

A card in needs you or problems whose step is ``run`` or ``terminal`` has a
primary button named from the wording table ("Re-run tests…", "Merge…",
"Resume…"), and so does the step in its detail. An ``info`` step, a row with no
step and a step the table does not know have none. A row waiting on a prompt
has no button at all: its card opens its detail (ADR-009 D8). ADR-029
(proposed, not built) plans an "Answer…" or "Reply…" button on such a row,
which opens the session's conversation rather than a step's confirm. The actions are
the TUI's, under the TUI's rules (ADR-007 D18, ADR-008 D8–D13):

+----------------------------------+----------------------------------+
| Step                             | What happens                     |
+==================================+==================================+
| press the button                 | ``GET /api/confirm``: the server |
|                                  | scans afresh, finds the row by   |
|                                  | its key, derives the step and    |
|                                  | answers ``refusal``'s reason, or |
|                                  | the ``info`` text, or the TUI's  |
|                                  | confirm lines and a nonce, with  |
|                                  | ``words``:                       |
|                                  | ``panel.confirm_words``, the     |
|                                  | sheet's title, sentence, deed,   |
|                                  | the command as one shell line,   |
|                                  | the place in plain words, the    |
|                                  | exact folder, each added         |
|                                  | variable and a terminal's launch |
|                                  | line (previewed once). The       |
|                                  | bottom sheet shows the question, |
|                                  | the command with Copy, the rest  |
|                                  | behind "Exact folder and         |
|                                  | environment", the deed over      |
|                                  | Cancel, which is focused, and    |
|                                  | the nonce counting down; at zero |
|                                  | the deed is disabled and "Look   |
|                                  | again" asks for a new confirm. A |
|                                  | destructive step                 |
|                                  | (``session teardown``) lists     |
|                                  | what it deletes, puts Cancel     |
|                                  | first and the command behind     |
|                                  | "Exact command". Nothing has run |
+----------------------------------+----------------------------------+
| press the deed ("Run tests",     | ``POST /api/act`` with           |
| "Merge", "Open window")          | ``{row, nonce}`` and nothing     |
|                                  | else. The nonce is spent; the    |
|                                  | server scans again, checks every |
|                                  | refusal again and that the step  |
|                                  | is the one confirmed             |
|                                  | (``command_of``, which for a     |
|                                  | masked value includes the        |
|                                  | recorded run it is read from),   |
|                                  | reserves the row, writes the     |
|                                  | audit start line, and answers    |
|                                  | 202 with a job. A thread runs    |
|                                  | ``Actions.perform``, which joins |
|                                  | a masked value's real value from |
|                                  | that run into the child's        |
|                                  | environment ("Confirm-first      |
|                                  | actions" above)                  |
+----------------------------------+----------------------------------+
| afterwards                       | the sheet polls ``/api/job``     |
|                                  | every second until the job is    |
|                                  | done, then shows its ``words``   |
|                                  | (``panel.result_words``: "Done   |
|                                  | in N s", "Failed, exit N",       |
|                                  | "Opened in a new window"), for a |
|                                  | failure a "Why" panel with       |
|                                  | ``words.why``, and the output's  |
|                                  | last 64 KiB, unchanged, behind a |
|                                  | closed "Show full output". The   |
|                                  | state loop keeps refreshing      |
|                                  | throughout, and ``/api/state``   |
|                                  | carries the device's last 20     |
|                                  | jobs, which a session's detail   |
|                                  | lists under "Recent", so a       |
|                                  | reload still shows the result    |
+----------------------------------+----------------------------------+

**A chain action** (ADR-027 increment 3) takes the same three steps. Its
button names a chain key, ``<project key>|chain:<action>[:<entry>]``, in
place of a row key; the server derives its ``invariant`` argv from a fresh
chain read (``oracle/chainview``) instead of a row's recommendation, and
keys its job by the project's chain, so one chain action runs per project at
a time. "The chain on the phone" below has the rest.

**Copy.** The detail's step and every confirm carry Copy, which puts
``panel.copy_line`` on the clipboard: ``shlex.join`` of the argv, its pinned
variables leading as ``NAME=value``. It runs nothing and spends no nonce. A
line with a masked variable is not the command that runs, so it is a shell
comment that says so, "# Not runnable as shown, NAME masked: …", and pasting
it runs nothing (BUG_093). Over
plain HTTP the clipboard is not available, so the command is shown and
selected instead (ADR-009 D5).

+----------------------------------+----------------------------------+
| Refused when                     | Answer                           |
+==================================+==================================+
| the row waits on a prompt, the   | the confirm shows the reason and |
| argv is not ``invariant``, the   | offers no deed; an action is a   |
| cwd does not exist, the kind is  | 409 with it                      |
| unknown                          |                                  |
+----------------------------------+----------------------------------+
| the nonce is unknown, spent,     | 403; it is spent either way      |
| expired, another device's or     |                                  |
| another row's                    |                                  |
+----------------------------------+----------------------------------+
| the step changed since the       | 409                              |
| confirm, the row is gone, the    |                                  |
| row's last action still runs,    |                                  |
| four already run                 |                                  |
+----------------------------------+----------------------------------+
| no ``Origin``, a foreign one     | 403, before the body is read     |
| (another host, port or scheme:   |                                  |
| an ``http://`` origin on the     |                                  |
| HTTPS page), or                  |                                  |
| ``Sec-Fetch-Site`` other than    |                                  |
| ``same-origin``                  |                                  |
+----------------------------------+----------------------------------+
| the audit start line cannot be   | 503; nothing ran                 |
| written                          |                                  |
+----------------------------------+----------------------------------+

**The audit log.** ``<oracle home>/web/actions.log``, owner-only from
creation. It gets a JSON line when an action starts and one when it ends:
when, which device, which row, the kind, the ``argv`` and ``cwd``, and at the end
the exit status, whether it succeeded and how long it took. It holds no
token, nonce, environment or output. It is a record, not derived state, so
it lives in ``web/``. The format is in :doc:`state_model.md </architecture/state_model>`,
"User-level state".

**What the server never does.** It does not run an ``argv``, ``cwd`` or
environment a client sent, and it does not trust the snapshot for an action.
It does not answer a permission prompt, and it does not run a step on a row
that waits on one. Both hold for the action routes as built. ADR-029
(proposed) plans talk routes that do answer prompts, under their own
fingerprint, receipt and confirm ("Planned: talking to sessions" below); the
action routes keep this rule.

**The listener.** Each connection reads with a 10-second socket timeout,
carries at most 40 headers (431 past that), and at most 32 are served at
once. More are closed unanswered. A malformed ``Content-Length`` is a 400
(ADR-008 D14).

Pairing
~~~~~~~

``oracle web pair`` issues a 10-character code that lives 5 minutes
(``--ttl``) and works once, and prints it as two groups of five,
``ABCDE-FGHJK`` (``devices.display_code``), in the text and in its link
(``/pair?code=ABCDE-FGHJK``; redeeming drops the dash). It prints the link: the https name once a valid
certificate is kept, else a plain-HTTP link per tailnet address. Ten wrong
codes in a row cancel every pending one. The pairing form's ``POST`` refuses a
foreign ``Origin`` and lets a missing one through: the code, not the cookie, is
its credential (ADR-008 D11). ``oracle web devices`` lists the
paired devices and ``oracle web revoke <id>`` forgets one. The store is
specified in :doc:`state_model.md </architecture/state_model>`, "User-level state".

The seams
~~~~~~~~~

The CLI reaches discovery through ``_oracle_web_addresses``, the server
through ``_oracle_web_server`` and the certificate through
``_oracle_web_certificates``, and the suite replaces all three for every test
(``RecordingWeb``, and a ``RecordingIssuer`` that refuses until a test hands it a
self-signed fixture from ``tests/fixtures/tls``). The server seam is
``WebServer``'s constructor, ``(app, addresses, port, *, served, request_timeout, max_connections)``: everything after the port is
keyword-only, the CLI passes ``served=`` by name, and ``RecordingWeb``'s factory
takes the same signature, which a test holds equal to the real one. Before
that, the CLI passed the certificate holder fourth, it landed in
``request_timeout``, and every HTTPS connection raised while the recorder
accepted the call (BUG_083). The one TLS test that opens a
socket serves a fixture on loopback. An action leaves through the TUI's own seams,
``_oracle_runner`` and ``_handoff_launcher``, which the suite replaces with
recorders, so no test runs a command or opens a window. The ``conftest.py`` program guard refuses ``tailscale``. Its
socket guard refuses any ``bind``, ``connect``, ``connect_ex`` or ``sendto`` past
loopback, and any reverse lookup, because macOS asks for Local Network
permission the first time a process reaches past loopback. The server's bind
skips the standard library's ``getfqdn`` for the same reason. A real serve on
the tailnet may need that permission granted to the Python interpreter that
serves it, in System Settings > Privacy & Security > Local Network (ADR-008
D7).

The page, always on (increment 3)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. comp:: The web page's LaunchAgent
   :id: COMP_AGENT_ORACLE_WEB
   :uses: COMP_ORACLE_WEBSERVICE

   The second LaunchAgent, running ``invariant oracle web --wait``, so the page
   is served whenever the Mac is up (ADR-008 D15).


``invariant oracle install --web [--port 7341]`` installs a second
LaunchAgent, ``dev.invariant.oracle.web``, whose plist runs ``oracle web --port N --wait``. The watcher's agent is untouched by it; ``oracle uninstall --web``
removes the page alone and ``oracle uninstall`` removes both (ADR-008 D15).

+--------------------------+---------------------------------------------------------------+-----------------------+
| Key                      | Value                                                         | Why                   |
+==========================+===============================================================+=======================+
| ``Label``                | ``dev.invariant.oracle.web``                                  | Its own agent: a      |
|                          |                                                               | listener that fails   |
|                          |                                                               | takes no notification |
|                          |                                                               | with it, and the page |
|                          |                                                               | stays opt-in.         |
+--------------------------+---------------------------------------------------------------+-----------------------+
| ``ProgramArguments``     | ``[<python>, -m, invariant, oracle, web, --port, N, --wait]`` | The watcher's rules   |
|                          |                                                               | for the interpreter.  |
|                          |                                                               | Never                 |
|                          |                                                               | ``--allow-loopback``. |
+--------------------------+---------------------------------------------------------------+-----------------------+
| ``RunAtLoad``,           | ``true``                                                      | As the watcher's.     |
| ``KeepAlive``            |                                                               | ``--wait`` means it   |
|                          |                                                               | never exits for a     |
|                          |                                                               | missing tailnet, so   |
|                          |                                                               | launchd has no crash  |
|                          |                                                               | to restart.           |
+--------------------------+---------------------------------------------------------------+-----------------------+
| ``StandardOutPath``,     | ``<oracle home>/oracle/web.log``                              | The loop's state      |
| ``StandardErrorPath``    |                                                               | changes only:         |
|                          |                                                               | waiting, serving, a   |
|                          |                                                               | changed address, a    |
|                          |                                                               | failed bind. Requests |
|                          |                                                               | are never logged,     |
|                          |                                                               | because a request     |
|                          |                                                               | line can carry a      |
|                          |                                                               | pairing code.         |
+--------------------------+---------------------------------------------------------------+-----------------------+
| ``EnvironmentVariables`` | ``PATH``, and ``INVARIANT_HOME`` only if set                  | ``PATH`` finds        |
|                          |                                                               | ``invariant`` (an     |
|                          |                                                               | action runs it),      |
|                          |                                                               | ``claude`` (a resume  |
|                          |                                                               | or a start from the   |
|                          |                                                               | page opens it,        |
|                          |                                                               | IDEA_030), git,       |
|                          |                                                               | osascript and         |
|                          |                                                               | ``tailscale``, then   |
|                          |                                                               | the interpreter's own |
|                          |                                                               | directory, then the   |
|                          |                                                               | system directories.   |
|                          |                                                               | Built as the          |
|                          |                                                               | watcher's, with the   |
|                          |                                                               | same well-known       |
|                          |                                                               | fallback and the same |
|                          |                                                               | warning for a program |
|                          |                                                               | found nowhere         |
|                          |                                                               | (ADR-028). No         |
|                          |                                                               | ``TERM``, which       |
|                          |                                                               | launchd does not set  |
|                          |                                                               | either:               |
|                          |                                                               | ``web.run_program``   |
|                          |                                                               | gives Tailscale's CLI |
|                          |                                                               | one (BUG_084, "Over   |
|                          |                                                               | HTTPS").              |
+--------------------------+---------------------------------------------------------------+-----------------------+

``LaunchdInstaller`` takes the label, so both agents are written, loaded,
replaced and checked by one code path. The CLI reaches the second through
``_oracle_web_installer``, which the suite replaces with a recording installer
over the watcher's temporary directory and the same recording launchctl.

**The loop** (``oracle/webservice.WebService``, ADR-008 D16):

.. mermaid::

   stateDiagram-v2
       [*] --> waiting: no Tailscale address
       [*] --> serving: addresses found, bound
       waiting --> waiting: still none, or the bind failed (look again in 10 s)
       waiting --> serving: addresses found, bound
       serving --> serving: same addresses (look again in 30 s)
       serving --> serving: addresses changed: close, bind the new ones
       serving --> waiting: addresses gone: close
       serving --> serving: certificate renewed (same name): swapped in, no rebind
       serving --> serving: first certificate, lost, or a new name: close, bind over https or http
       serving --> [*]: stopped (report removed)
       waiting --> [*]: stopped (report removed)

Discovery is the foreground's (``_oracle_web_addresses``), filtered to
Tailscale's ranges again inside the loop. Each listener gets a fresh ``WebApp``
bound to its addresses, for the ``Host`` check, and all of them share one
``RemoteActions``, so jobs survive a rebind. The pause is the
``_oracle_web_wait`` seam, which the suite replaces with a scripted recorder,
so no test sleeps.

**The report.** On each change of state the loop writes
``<oracle home>/oracle/web.json`` (the format is in
:doc:`state_model.md </architecture/state_model>`, "User-level state"). ``web_status`` reads it,
checks its pid with ``scan.pid_exists``, counts the paired devices and reads the
audit log's last line (``AuditLog.last``, the last 8 KiB only). ``oracle status``
prints that under the watcher's lines, and ``doctor`` adds ``oracle web interpreter`` and ``oracle web page`` when the web plist exists, asking launchd
nothing (ADR-008 D17).

**A restart when invariant changes** (BUG_089). The loop is handed the scan
cache's ``BuildWatch`` and the page's ``RemoteActions``. Each step first checks
the installed tree; while it is moving the loop looks again every 5 seconds
(``BUILD_SECONDS``) instead of 30. Once it has held still, the page drains:
``RemoteActions.close`` makes every new action and start a 409, "the oracle is
restarting to pick up an updated invariant; nothing ran", and the loop
restarts only when ``RemoteActions.running()`` is zero, so a confirmed action
already running is never cut off; each action has the runner's 600-second
limit, so the wait is bounded. Then ``run`` returns with ``restarting`` set, the
listener closes and the report is removed, and ``oracle web --wait`` runs
itself again through ``_oracle_restart``, as the watcher does. Paired devices,
the audit log and the certificate are files and survive it. A confirm not yet
acted on is lost with the process's nonces and is asked again ("Look again"),
and a job's result is the audit log's, as after any restart (D18). The
report carries ``build``, the stamp the page started from.

.. mermaid::

   stateDiagram-v2
       serving --> serving: tree unchanged (look again in 30 s)
       serving --> settling: tree changed (look again in 5 s)
       settling --> settling: still changing
       settling --> draining: held still 5 s, new actions refused
       draining --> draining: actions still running (look again in 5 s)
       draining --> [*]: none running, so close, remove the report, re-exec

**The certificate, inside the loop** (IDEA_026, ADR-008 D19–D23). With the
tailnet up, each look also calls ``Certificates.ensure``: it reads the kept
``web/tls/oracle.pem`` and asks Tailscale through the seam only when fewer than
30 days remain or there is none, at most hourly while the answer stays the
same (every 10 minutes with none). A renewal for the same name is swapped
into the running listener's ``Served``, so the next connection gets it with no
restart. A first certificate, a lost one (expired, renewal failing) or one
for a new name makes the loop listen again: over HTTPS on the same port,
whose plain-HTTP requests get only a ``307`` to the https name, or over plain
HTTP with the page's warning. The report gains ``scheme`` and ``certificate``,
which ``oracle status`` prints as ``certificate  <name> expires <when> (in N days)`` and ``doctor`` checks as ``oracle web certificate``.

**The page recovers.** The refresh loop never stops (D6), so a listener or
agent restart is recovered by itself within the backoff. A look at the page
(visible again, focus, restored, back online) cuts the backoff short and
reads at once, with no second read while one is in flight. A job the
restarted server no longer knows (``/api/job`` 404) is reported lost, with a
pointer to the audit log, instead of being polled forever (D18). A page open
over plain HTTP when the listener moves to HTTPS gets the redirect on its
next read, which it does not follow; it reloads, and the browser follows the
redirect to the https name (D23).

Over HTTPS (IDEA_026)
~~~~~~~~~~~~~~~~~~~~~

The page is served with the Let's Encrypt certificate Tailscale issues for the
Mac's MagicDNS name, once the person turns on HTTPS Certificates in the
Tailscale admin console (ADR-008 D19–D24).

Tailscale's CLI is asked through ``web.run_program``, the runner address
discovery uses too. The Mac App Store and standalone apps' binary starts its
GUI instead of answering when ``TERM`` is missing, and launchd sets none, so
the runner adds ``TERM=dumb`` to the environment it passes on when ``TERM`` is
missing or empty, and keeps one it was given. The plist does not carry it:
the runner is the one place every call passes, the agent's and a foreground
run's alike (BUG_084, ADR-008 D19).

.. mermaid::

   sequenceDiagram
       participant Loop as WebService (oracle web --wait)
       participant Certs as tls.Certificates
       participant TS as Tailscale CLI (via the seam)
       participant Srv as WebServer listener
       Loop->>Certs: ensure() each look while the tailnet is up
       Certs->>Certs: read web/tls/oracle.pem; due if < 30 days or none
       Note over Certs,TS: web.run_program, with TERM=dumb when the environment has none
       Certs->>TS: status --json (CertDomains, else Self.DNSName)
       Certs->>TS: cert --cert-file - --key-file - <name>
       TS-->>Certs: chain then key, on stdout
       Certs->>Certs: write candidate 0600, load it, rename over oracle.pem
       Certs-->>Loop: Certificate(name, not_after)
       alt same name, newer certificate
           Loop->>Srv: Served.renew(cert, fresh SSLContext): next connection
       else first, lost, or a new name
           Loop->>Srv: close; bind again over https, or http with a warning
       end
       Loop->>Loop: report scheme and certificate in oracle/web.json

+----------------------------------+--------------------------------------+
| Case                             | What is served                       |
+==================================+======================================+
| A valid certificate              | HTTPS on the port, at the            |
|                                  | certificate's name only. A           |
|                                  | plain-HTTP request gets ``307`` to   |
|                                  | ``https://<name>:<port>/`` and       |
|                                  | nothing else. The cookie is          |
|                                  | ``Secure``; the ``Origin`` must be   |
|                                  | ``https``                            |
+----------------------------------+--------------------------------------+
| None to be had                   | Plain HTTP, as before, with a red    |
|                                  | warning on the page and the pairing  |
|                                  | form, ``PLAIN HTTP`` on              |
|                                  | ``oracle web``'s output,             |
|                                  | ``certificate NONE`` in              |
|                                  | ``oracle status`` and a failing      |
|                                  | ``doctor`` row. Asked for again      |
|                                  | every 10 minutes                     |
+----------------------------------+--------------------------------------+
| A renewal fails                  | The kept certificate while it is     |
|                                  | valid, the failure in ``status`` and |
|                                  | the log; ``doctor`` fails 14 days    |
|                                  | before expiry                        |
+----------------------------------+--------------------------------------+
| Tailscale answers                | Treated as no certificate; the error |
| ``status --json`` with something | quotes the answer's first line       |
| that is not JSON                 | (``did not print JSON; it said: …``) |
+----------------------------------+--------------------------------------+
| ``--allow-loopback``             | Plain HTTP on loopback, no warning,  |
|                                  | nothing asked of Tailscale           |
+----------------------------------+--------------------------------------+

The control panel (IDEA_027)
----------------------------

The web page above is being redesigned as a mobile control panel. The
decisions are ADR-009's, and the design of record (screens, components,
tokens, wording, the increment plan) is
:doc:`oracle-panel.md </architecture/oracle-panel>`.

- **Increment 3 has shipped**: the plain-language panel. ``oracle/panel``
  groups the rows by attention, counts them for the summary strip, and words
  every label from one table; ``web.view`` is its view, ``webactions`` reads its
  confirm and result words, and ``webpage`` draws the overview, the session
  detail, the leftovers screen and the bottom sheets ("What the page shows"
  and "Actions from the page" above).
- **Increment 4 has shipped**: the home-screen app, a manifest and icons
  served under the same token and a CSP that adds only ``manifest-src 'self'``
  and ``img-src 'self'``, the iOS standalone tags, and safe-area padding
  ("Who gets an answer" above). Both iOS behaviours D10 named were
  verified on an iPhone on 2026-10-25 (:doc:`oracle-panel.md </architecture/oracle-panel>`).
- **Increment 5 has shipped**: a one-line reason for a failed action
  (ADR-009 D9). When a job ends, ``webactions.summarise`` reads its kept
  output for the line invariant's CLI writes for its own errors
  (``invariant: <message>``) and for the last meaningful line, skipping blank
  lines, traceback frames, progress bars and rules; ``panel.why_words`` words
  them. A cause is named only from invariant's own line ("invariant could
  not find a value for destination_id."); anything else reads "Failed, exit
  N. ``<last line>``". The job's dict gains only ``words.why``, and the output is
  unchanged behind "Show full output".
- **Increment 6 has shipped**: orphan classes and close host (IDEA_025,
  ADR-009 D15), the oracle's first destructive power. This amends ADR-007's
  read-only premise (ADR-009 D1): the scan, the watcher and the gate cache
  stay read-only, and only a confirmed ``invariant oracle close-host`` signals
  anything ("Close host" below).
- **IDEA_030 increment 2 has shipped**: "New session…" in the top bar and a
  shortcut per project open a confirm sheet that starts a new Claude window,
  Remote Control on, in a registered project's main checkout, through
  ``oracle start-session``'s own path; the start is a "Starting…" card under
  Working until a scan sees its window ("Start a session" below, ADR-009
  D16).
- **ADR-027 increment 3 has shipped**: the chain on the phone. Its cards
  join Needs you, each project's row gains a chain line that opens its Chain
  screen, and every chain action runs the CLI's own verb through the confirm
  and the action above ("The chain on the phone" below).
- **IDEA_060's increment 1 has shipped** (ADR-029, accepted): each live
  window's last message on its card, its leftover row and its detail,
  IDEA_029's Read half ("Shipped: a window's last message" below).
  Increments 2 to 7, the conversation, sending, interrupting and answering
  its questions and permission prompts, are planned ("Planned: talking to
  sessions" below).
- **Increment 7** adds the clean-up and unstick actions (IDEA_017). Each
  destructive action is confirmed, re-checks its precondition at confirm
  time, runs as an ``invariant`` command with no refusal override, and is
  never offered on a row BUG_040 (below) may have misattributed. Both facts
  they waited for have shipped: BUG_078 (true last activity) and IDEA_024
  (every live host's pid, its start token and its terminal, "Host liveness"
  above).

This section and the graph in "Where it sits" are updated as each increment
ships.

Close host (IDEA_027 increment 6)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The leftovers screen's "Close…" runs ``invariant oracle close-host <host-id> --pid N --start TOKEN [--tty ttysNNN]``, the recommendation of a closeable
orphan, as a ``run`` step in the project root: the server derives it from a
fresh scan for the confirm and again for the action, like every step, and
the client sends only the row key and the nonce. The decisions are ADR-009
D15's; the words are :doc:`oracle-panel.md </architecture/oracle-panel>`'s.

+------------------------------+--------------------------+----------------------+
| Module                       | Owns                     | Never does           |
+==============================+==========================+======================+
| ``closehost``                | ``CloseRequest``,        | Import ``hosts/``;   |
|                              | ``HostSeams``,           | send any signal but  |
|                              | ``close_host`` (the      | one SIGTERM to one   |
|                              | checks again, then       | positive pid; close  |
|                              | ``end_then_close_tab``), | a tab before Claude  |
|                              | ``end_then_close_tab``   | has exited           |
|                              | (the parent chain read,  |                      |
|                              | the SIGTERM and the      |                      |
|                              | wait, the tab and its    |                      |
|                              | settle; the one way      |                      |
|                              | invariant ends a Claude  |                      |
|                              | window, shared with the  |                      |
|                              | handoff's closer,        |                      |
|                              | BUG_100),                |                      |
|                              | ``binding_refusal``,     |                      |
|                              | ``attention_refusal``    |                      |
|                              | (the fresh scan's word   |                      |
|                              | for a pid no record      |                      |
|                              | names, BUG_099),         |                      |
|                              | ``send_sigterm``, the    |                      |
|                              | outcome lines and        |                      |
|                              | ``outcome_word`` (a line |                      |
|                              | back to its code,        |                      |
|                              | BUG_098)                 |                      |
+------------------------------+--------------------------+----------------------+
| ``webactions``               | Writes the close's       | Record the outcome   |
|                              | outcome code on the      | line or any other    |
|                              | audit log's done line    | output               |
|                              | (``tab``), through       |                      |
|                              | ``outcome_word``         |                      |
+------------------------------+--------------------------+----------------------+
| ``hosts/claude_code_launch`` | ``runs_in_terminal_app`` | Interpolate the tty  |
|                              | (Terminal.app in the     | into the script;     |
|                              | parent chain),           | start Terminal;      |
|                              | ``TerminalTabCloser``    | decide whether to    |
|                              | and                      | close                |
|                              | ``TERMINAL_TAB_SCRIPT``  |                      |
+------------------------------+--------------------------+----------------------+
| ``cli/host_close``           | ``claude_code_seams``    | Check a leftover     |
|                              | (the one join of Claude  | window (that is      |
|                              | Code's facts and effects | ``close_host``'s);   |
|                              | into ``HostSeams``),     | close a tab by any   |
|                              | ``end_handed_off_host``, | rule of its own      |
|                              | ``DetachedCloser`` and   |                      |
|                              | the detached process     |                      |
|                              | ``session handoff``      |                      |
|                              | starts (``main``)        |                      |
+------------------------------+--------------------------+----------------------+

.. mermaid::

   sequenceDiagram
       participant Page as Phone (leftovers)
       participant Srv as webactions
       participant Cmd as oracle close-host
       participant Seams as HostSeams (the CLI injects Claude Code's)
       Page->>Srv: GET /api/confirm?row=K|host:ID
       Srv-->>Page: destructive sheet: This ends, Checked just now, nonce
       Page->>Srv: POST /api/act {row, nonce}
       Srv->>Srv: fresh scan, same step? (else 409, nothing runs)
       Srv->>Cmd: run argv in the project root
       Cmd->>Seams: record for pid: same session id, same start?
       Note over Cmd: no record: a fresh scan still has the host's row at attention+ps, this pid, this start?
       Cmd->>Seams: kill 0; ps: claude, same start, same tty?
       Cmd->>Cmd: fresh scan of every project: no live session lists it?
       Note over Cmd: any mismatch: "nothing was done: …", exit 1
       Cmd->>Seams: Terminal.app in the parent chain?
       Cmd->>Seams: SIGTERM once; poll kill 0 up to 10 s
       Note over Cmd: still alive: "Claude did not exit … nothing else was done", exit 1
       loop while the tab answers busy, every 0.5 s for up to 5 s
           Cmd->>Seams: osascript with /dev/TTY as its argument: close the tab if only its shell is left
       end
       Cmd-->>Srv: last line: what happened, exit 0
       Srv->>Srv: audit done line: exit, ok, elapsed, tab (the outcome's code, never the line)
       Srv-->>Page: result sheet: What happened, or Why

+----------------------------------+----------------------------------+
| Check, at the moment it runs     | Refusal (nothing is signalled)   |
+==================================+==================================+
| ``--pid`` is a positive number,  | a process id must be a positive  |
| the host id a plain name         | number / is not a host id        |
+----------------------------------+----------------------------------+
| Claude Code's per-process        | the records cannot be read       |
| records can be read              |                                  |
+----------------------------------+----------------------------------+
| a record has this pid: its       | now belongs to another Claude    |
| ``sessionId`` is the host id,    | session / a newer process than   |
| its ``procStart`` the            | the one confirmed                |
| ``--start``                      |                                  |
+----------------------------------+----------------------------------+
| no record has this pid           | no record of process N, and the  |
| (BUG_099): a fresh ``scan_all``  | window's own record no longer    |
| and ``assess`` still show the    | verifies it as this Claude / …   |
| host's orphan row with this pid, | and nothing else names it any    |
| ``pid_source`` ``attention+ps``  | more                             |
| and ``process_start`` the        |                                  |
| ``--start``, every fact in "An   |                                  |
| attention pid no record          |                                  |
| describes" read again            |                                  |
+----------------------------------+----------------------------------+
| ``kill 0`` finds the pid         | process N has already exited     |
+----------------------------------+----------------------------------+
| ``ps`` describes it, as          | cannot describe / not Claude any |
| ``claude``, started at           | more / started at another time / |
| ``--start``, on ``--tty`` (or on | runs on another terminal         |
| none when no ``--tty``)          |                                  |
+----------------------------------+----------------------------------+
| no live session in any           | a live invariant session uses    |
| registered project lists the     | this Claude window now           |
| host (a fresh ``scan_all`` and   | (``<project>``, session          |
| ``assess``)                      | ``<id>``)                        |
+----------------------------------+----------------------------------+

**Ending Claude.** One SIGTERM, then ``kill 0`` every 0.25 s for up to 10 s.
A pid that is gone before the signal counts as ended. No SIGKILL, ever: a
Claude still running after the wait is the failure "Claude did not exit
within 10 s of being asked to; nothing else was done", and its window is not
touched.

**Closing the tab.** Only once Claude has exited, only with a tty, and only
when the parent chain read before the signal shows Terminal.app (Claude
under the tab's shell, under ``login``, under Terminal). ``osascript`` runs
``TERMINAL_TAB_SCRIPT`` with ``/dev/<tty>`` as its argument, without the host
markers, and with a 30-second timeout. The script returns at once when
Terminal is not running, so it never starts it. It finds the tab whose
``tty`` is the argument; a tab that is ``busy``, or whose ``processes`` hold
anything but a login shell, is left; the only tab of a window closes with
the window; one tab of several is closed alone, which Terminal may refuse.

**Letting the tab settle (BUG_098).** Claude's own children (``caffeinate``, a
language server) share its tty and can outlive it by a moment, so a tab
asked about the instant Claude has gone may still answer ``busy``. A ``busy``
answer is asked again every ``TAB_POLL_SECONDS`` (0.5 s) for up to
``TAB_SETTLE_SECONDS`` (5 s), and the tab closes as soon as only its shell is
left. Every other answer is final at once. Past the bound the last ``busy``
answer stands, naming what still runs. The script's own rule is unchanged:
nothing but a login shell may be left in a tab it closes. The worst case,
the 10 s wait, the 5 s settle and one 30-second ``osascript``, stays far
inside the 600-second limit on a page action's run (``RUN_TIMEOUT_SECONDS``).

+----------------------------------+----------------------------------+
| Answer                           | The last line                    |
+==================================+==================================+
| ``closed_tab`` /                 | Claude ended, and its Terminal   |
| ``closed_window``                | tab closed. / … window closed.   |
+----------------------------------+----------------------------------+
| no tty                           | Claude ended; the window was     |
|                                  | left open because it has no      |
|                                  | terminal (it runs over Remote    |
|                                  | Control or inside an editor).    |
+----------------------------------+----------------------------------+
| not Terminal / cannot tell       | … because it does not run in     |
|                                  | Terminal (another app, such as   |
|                                  | VS Code or iTerm, owns it). / …  |
|                                  | because invariant could not tell |
|                                  | which app owns it.               |
+----------------------------------+----------------------------------+
| ``busy <processes>``             | … because something else is      |
|                                  | running in its tab               |
|                                  | (``<processes>``).               |
+----------------------------------+----------------------------------+
| ``no_tab`` / ``not_running``     | Claude ended, and no Terminal    |
|                                  | tab is left on ``<tty>``: its    |
|                                  | window closed with it, or was    |
|                                  | already closed. (A window        |
|                                  | invariant launched ends with its |
|                                  | Claude, IDEA_035, so this is not |
|                                  | a window left open.)             |
+----------------------------------+----------------------------------+
| ``tab_not_closable``             | … because Terminal would not     |
|                                  | close one tab of several from a  |
|                                  | script (``<error>``).            |
+----------------------------------+----------------------------------+
| ``not_allowed`` (error -1743,    | Claude ended; macOS has not      |
| "Not authorized", or a prompt    | allowed the oracle to control    |
| nobody answered in 30 s)         | Terminal, so the window was left |
|                                  | open. Allow it in System         |
|                                  | Settings > Privacy & Security >  |
|                                  | Automation …                     |
+----------------------------------+----------------------------------+
| anything else                    | … because Terminal could not be  |
|                                  | asked (``<error>``).             |
+----------------------------------+----------------------------------+

**What the audit log keeps.** The page's done line for a close that ran
gains ``tab``, the code of its last line (``closehost.outcome_word``):
``closed_tab``, ``closed_window``, ``not_allowed``, ``no_tty``, ``not_terminal``,
``unknown_app``, ``busy``, ``no_tab`` (also for ``not_running``, which says the same
sentence), ``tab_not_closable`` or ``failed``. Only the fixed words before a
reason's detail are matched, so the processes, the tty and any error stay
out of the log (:doc:`state_model.md </architecture/state_model>`, "The web actions' audit
log"). A refused or failed close, and every other step, has no ``tab``.

**The seams.** The CLI reaches every host fact and effect through
``_oracle_host_seams``, a ``HostSeams`` of Claude Code's built by
``host_close.claude_code_seams``: ``ClaudeCodeProbe``'s
``host_processes`` and ``process_facts``, ``scan.pid_exists``,
``closehost.send_sigterm``, ``runs_in_terminal_app`` and
``TerminalTabCloser().close``. The binding check and, for a pid no record
names, the attention check read one fresh scan through ``_oracle_cache``,
made once per run. The suite replaces ``_oracle_host_seams`` for every test with
seams that know no process, so the command refuses before it signals
anything. Beside the ``osascript`` guard, the ``conftest.py`` guard refuses
``os.kill`` and ``os.killpg`` with any signal but 0 unless the pid is a child
the test itself spawned through ``subprocess.Popen``, and raises
``RefusedProgram``. The one test that sends a real SIGTERM sends it to a
``python -c "sleep"`` child of its own.

**The handoff closes its window the same way (BUG_100).** ``session handoff``
ends the host that ran it once its successor is alive (cli.md; ADR-005 D7).
Its closer is ``end_then_close_tab`` too, behind a check of its own: the
handoff names its own ``CLAUDE_PID``, so there is no record, binding or
confirm to check again, only the process. ``DetachedCloser`` starts
``python -m invariant.cli.host_close <pid> <delay>`` in a session of its own,
holding none of the command's streams, with the interpreter that runs
invariant. That process describes the pid with ``ps`` at once, waits the
delay, describes it again, and ends it only if it is still ``claude``,
started at the same moment, on the same terminal; anything else is left
alone with nothing signalled. From there every rule above holds unchanged:
the parent chain read before the signal, one SIGTERM and the 10 s wait,
the tab only with a tty, only under Terminal.app, only when nothing but its
shell is left, asked again while it settles. No ``osascript`` appears in any
shell text; the tab closer finds it on ``PATH``. The suite replaces
``_handoff_closer`` everywhere, and BUG_100's repro runs the real detached
process with ``ps``, ``osascript`` and its signals faked.

**With a window that ends with Claude (IDEA_035).** A window invariant
launched runs the launch shell in place of its own (the typed line is
``exec``-ed, "Start a session" below), so once the SIGTERM has ended Claude
the launch file exits without waiting (a status above 128 is an ending by
a signal) and the tab's process is gone. The closer then finds no tab on
the tty, the window having closed by the user's Terminal profile, or a
finished tab with nothing but a shell, which it closes. Both rules above
stand unchanged; the ``no_tab`` line now says the window closed with Claude
rather than that it was left open. Close host meets the same window the
same way.

.. mermaid::

   sequenceDiagram
       participant Cmd as session handoff
       participant Det as host_close (detached process)
       participant Seams as HostSeams (claude_code_seams)
       Cmd->>Seams: ps: CLAUDE_PID names claude?
       Note over Cmd: not claude: "leaving this session open", nothing scheduled
       Cmd->>Det: start: pid, CLOSE_DELAY_SECONDS (own session, no streams)
       Cmd-->>Cmd: output printed, command returns
       Det->>Seams: ps: is claude, start, tty
       Det->>Det: sleep the delay
       Det->>Seams: ps again: still claude, same start, same tty?
       Note over Det: any mismatch: left alone, nothing signalled
       Det->>Det: end_then_close_tab(pid, tty): parent chain, SIGTERM, wait, settled tab close

Start a session (IDEA_030)
--------------------------

``invariant oracle start-session <project> [--prompt TEXT] [--name LABEL] [--dry-run]`` opens a new macOS Terminal window running ``claude --name <name> --remote-control <name> [prompt]`` in a registered project's main checkout.
It is the core the panel's "New session" sheet and the TUI's ``n`` run
(IDEA_030 increments 2 and 3). The decisions are ADR-009 D16's.

+------------------------------+---------------------------------+----------------------+
| Module                       | Owns                            | Never does           |
+==============================+=================================+======================+
| ``startsession``             | ``session_name``                | Import ``hosts/``;   |
|                              | (``<project>-<label>``,         | launch anything;     |
|                              | slugged, ``-2``, ``-3`` past a  | block a start on a   |
|                              | name in use), ``default_label`` | fact                 |
|                              | (``MMDD-HHMM``, local),         |                      |
|                              | ``open_windows`` and            |                      |
|                              | ``start_facts`` (pure, over one |                      |
|                              | project of the ``assess``       |                      |
|                              | document, for the panel to      |                      |
|                              | reuse), ``checkout_changes``    |                      |
|                              | (one ``git status --porcelain`` |                      |
|                              | with optional locks off),       |                      |
|                              | ``fact_lines``                  |                      |
+------------------------------+---------------------------------+----------------------+
| ``hosts/claude_code_launch`` | ``plan_launch`` with an         | Decide the project,  |
|                              | optional prompt; the            | the name or whether  |
|                              | ``exec``-ed typed line and the  | to start             |
|                              | launch file that pauses on a    |                      |
|                              | failed Claude                   |                      |
|                              | (``launch_script``,             |                      |
|                              | ``LAUNCH_PAUSE``, IDEA_035);    |                      |
|                              | ``require_default_permissions`` |                      |
|                              | and ``PERMISSION_FLAGS``;       |                      |
|                              | ``LAUNCH_UNSET`` (the host      |                      |
|                              | markers and                     |                      |
|                              | ``INVARIANT_HANDOFF``);         |                      |
|                              | ``names_in_use``,               |                      |
|                              | ``command_names_in_use``        |                      |
+------------------------------+---------------------------------+----------------------+
| CLI                          | ``cmd_oracle_start_session``;   | Pass a permission    |
|                              | ``_open_claude_window``, the    | flag; accept a path  |
|                              | one way invariant opens a       | the index does not   |
|                              | Claude window                   | name                 |
|                              | (``session handoff``, the       |                      |
|                              | dashboard's terminal step and   |                      |
|                              | this command); the              |                      |
|                              | ``_oracle_names_in_use`` seam   |                      |
+------------------------------+---------------------------------+----------------------+

.. mermaid::

   sequenceDiagram
       participant Cmd as oracle start-session
       participant Index as oracle/index
       participant Start as oracle/startsession
       participant Host as hosts/claude_code_launch (injected)
       Cmd->>Index: match(project): key, root or name
       Note over Cmd: not registered, no manifest at its root, or host.kind not claude_code: refused, nothing launched
       Cmd->>Host: names in use (live records, ps)
       Cmd->>Start: session_name(project, label, taken)
       Cmd->>Start: start_facts(scan of that project, checkout_changes(root))
       Cmd->>Host: plan_launch(claude, name, prompt, root): launch file, typed line
       Note over Host: refused: a permission flag in argv, a prompt starting with '-'
       Cmd->>Host: _handoff_launcher().launch(plan); successor watch
       Host-->>Cmd: pid of the live claude, or a failure with the line to run by hand

**Which projects.** The selector is matched as ``projects remove`` matches
it: key, then root, then name. A selector that names no entry is refused
(exit 2), so a free path is never launched; a root that lost its manifest is
refused with a pointer to ``projects prune``, and so is a project whose
``host.kind`` is not ``claude_code``. The window starts in the entry's ``root``,
the main checkout's spelling of the project.

**The name.** ``--name`` is the label; the name is ``<project>-<label>``, each
part slugged as a session name is (lower case, runs of other characters
folded to ``-``), so it cannot start with ``-``. Without ``--name`` the label is
the local time, ``MMDD-HHMM``. A name held by a live Claude window gets ``-2``,
then ``-3``: the names in use are the ``name`` of each Claude Code per-process
record whose pid is alive, and every ``--name``/``-n`` and
``--remote-control``/``--rc`` value on a ``claude`` line of the process table. The
CLI reads them through ``_oracle_names_in_use``, which the suite replaces
everywhere.

**The prompt.** Optional; an empty one is none. It is the last argument,
quoted by ``shlex`` into the launch file the handoff writes (mode 0600, removed
as ``claude`` starts), never in the typed line. One that starts with ``-`` is
refused, because ``claude`` would read it as a flag; one that only mentions a
flag is data.

**The facts.** Printed before the launch and in the JSON, and neither
blocks it: ``open_windows``, the distinct hosts the scan knows to be alive in
the project (a session's current or earlier host, or an orphan; unknown
liveness does not count), and ``uncommitted``, the paths ``git status --porcelain`` lists in the main checkout (ignored files do not count), with
``dirty`` (``null`` when git cannot say). The checkout is read with
``GIT_OPTIONAL_LOCKS=0``.

**What it never does.** It passes no ``--dangerously-skip-permissions``,
``--allow-dangerously-skip-permissions`` or ``--permission-mode``, and
``LaunchPlan`` refuses one. It creates no invariant session and records no
note; the typed line unsets an inherited ``INVARIANT_HANDOFF``, so the new
window claims nothing. It ends no host.

**When the window counts.** As for a handoff: once the successor watch sees
a new ``claude`` carrying the name's flags still alive 3 s after it appeared,
within 15 s. Otherwise the command fails, removes the launch file, and
prints the line to run by hand.

**When the window ends** (IDEA_035). The typed line is ``cd <root> && exec env -u … /bin/sh <launch file>``: the window's shell is replaced, so when
Claude exits no prompt is left, and the window closes or shows itself
finished, by the user's Terminal profile. The launch file runs ``claude`` as
its child. When Claude fails (a non-zero exit up to 128) at a terminal, it
prints "claude exited with status N. Press Return to close this window." and
waits, so a start-up crash does not vanish with the window; an ending by a
signal, such as a close's SIGTERM, never waits. Every path exits 0, which a
default profile closes. A launch that fails before Claude starts is the same
failure as before: the watch sees no successor, and the by-hand line, which
is never ``exec``-ed, is printed to paste into a shell that is already up. The
same holds for the panel's start, the dashboard's ``n`` and its terminal
step, and ``session handoff``, which all open their window here. The JSON's ``name`` is what a later scan
matches the window by (the record's ``launched_name``), which the panel's
"Starting…" card uses; its ``label`` is the label the name was made from.

From the phone (increment 2)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The panel's "New session…" sheet runs the command's own two steps, not a copy
of them. The CLI splits ``start-session`` into ``_start_prepare`` (resolve the
project from the index, check its manifest and host kind, name the window,
read the facts, build the plan without staging it) and ``_start_launch``
(``_open_claude_window`` in the entry's root), and hands the page both as an
``oracle/startsession.Starter``. ``oracle/webactions`` imports neither the CLI
nor ``hosts/``.

.. mermaid::

   sequenceDiagram
       participant Page as Phone (New session sheet)
       participant Web as web
       participant Srv as webactions
       participant Start as Starter (the CLI's prepare and launch)
       participant Host as hosts/claude_code_launch (via the CLI)
       Page->>Web: GET /api/start/confirm?project=KEY&label=L&prompt=P
       Web->>Srv: confirm_start(device, KEY, L, P)
       Srv->>Srv: fresh read of the machine
       Srv->>Start: prepare(KEY, L or None, P or None, that project of the read)
       Note over Start: index.match(KEY), manifest, host.kind, name, facts, plan (not staged). A refusal is the command's own
       Start-->>Srv: name, label, argv, cwd, facts
       Srv-->>Page: the sheet's words, and a nonce bound to (device, KEY|start, label, name, prompt)
       Note over Page: every edit asks again after 400 ms, Start session waits for the answer
       Page->>Web: POST /api/start {project: KEY, nonce} (this page's Origin)
       Web->>Srv: start(device, KEY, nonce)
       Srv->>Srv: spend the nonce, fresh read, prepare again from the confirmed label and prompt
       Note over Srv: the name changed (another window took it), or any refusal: 409, nothing ran
       Srv->>Srv: reserve KEY|start (one per project), audit start line
       Srv->>Start: launch(prepared), in a thread
       Start->>Host: _open_claude_window(name, prompt, root): plan, launcher, successor watch
       Srv->>Srv: audit end line, the job's words (Why from invariant's own line)
       Page->>Web: GET /api/state (every refresh)
       Web->>Srv: observe(document): a live host launched as the name? mark it appeared

+---------------------------------+-------------------------------------------------------+
| Step                            | Rule                                                  |
+=================================+=======================================================+
| The project                     | Only a key the page got from the snapshot's project   |
|                                 | list is offered, and the server resolves whatever it  |
|                                 | is sent through ``ProjectIndex.match``, as the        |
|                                 | command does. A path that names no entry is refused   |
|                                 | with the command's own sentence; nothing is launched  |
|                                 | (ADR-009 D16)                                         |
+---------------------------------+-------------------------------------------------------+
| The label and the first message | Validated where the command validates them: a label   |
|                                 | with no letter or digit is refused by                 |
|                                 | ``session_name``, and a first message that starts     |
|                                 | with ``-`` by ``plan_launch``. A blank label takes    |
|                                 | the time, a blank message is none. A first message    |
|                                 | over 4000 characters (``webactions.MAX_PROMPT``) is   |
|                                 | refused before anything is asked, because it travels  |
|                                 | in the confirm's query string                         |
+---------------------------------+-------------------------------------------------------+
| The nonce                       | Bound to the device, the row ``<key>|start`` and the  |
|                                 | tuple ``("start", key, label, name, prompt)`` the     |
|                                 | server answered. The start carries only the project   |
|                                 | and the nonce; the label and prompt are the server's  |
|                                 | own copy. A row's nonce cannot start a session and a  |
|                                 | start's nonce cannot run a row's step: the rows       |
|                                 | differ                                                |
+---------------------------------+-------------------------------------------------------+
| The name at the start           | Prepared again, with fresh names in use. If it is no  |
|                                 | longer the name the sheet showed, the start is        |
|                                 | refused, 409, and the sheet is opened again; a window |
|                                 | is never started under a name the person did not see  |
+---------------------------------+-------------------------------------------------------+
| One at a time                   | The job is keyed ``<key>|start``, so a second start   |
|                                 | in the same project waits for the first launch (at    |
|                                 | most the successor watch's 15 s); starts in two       |
|                                 | projects run side by side, under ``MAX_RUNNING``      |
+---------------------------------+-------------------------------------------------------+
| The audit                       | The start and end lines of every action, with         |
|                                 | ``kind`` ``start``, the ``argv`` of the ``claude``    |
|                                 | line (the prompt included: it is what ran) and the    |
|                                 | ``cwd``. ``exit`` is ``null``                         |
+---------------------------------+-------------------------------------------------------+
| A launch that failed            | The ``InvariantError`` the launcher raised becomes    |
|                                 | the job's output as ``invariant: <message>``, so      |
|                                 | "Why" quotes invariant's own sentence (ADR-009 D9)    |
+---------------------------------+-------------------------------------------------------+
| The window appearing            | ``RemoteActions.observe``, on every ``/api/state``,   |
|                                 | marks a start once ``startsession.find_started``      |
|                                 | finds a host in its project that is alive and whose   |
|                                 | ``launched_name`` is the start's name. The panel      |
|                                 | draws the rest                                        |
|                                 | (:doc:`oracle-panel.md </architecture/oracle-panel>`, |
|                                 | "As built in IDEA_030 increment 2")                   |
+---------------------------------+-------------------------------------------------------+

The seams are the command's: ``_oracle_names_in_use``, ``_oracle_cache``,
``_handoff_launcher`` and ``_handoff_successor``. The suite replaces all four, so
no test opens a window, reads the process table or reads ``~/.claude``.

From the dashboard (increment 3)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``n`` in ``invariant oracle tui`` opens ``StartScreen``, the same confirm in a
Textual modal. ``cmd_oracle_tui`` hands ``OracleApp`` the Starter
``_oracle_starter`` builds for the page, so the modal reaches
``_start_prepare`` and ``_start_launch`` and nothing else; ``oracle/tui`` imports
neither the CLI nor ``hosts/``.

.. mermaid::

   sequenceDiagram
       participant Key as n (OracleApp)
       participant Modal as StartScreen
       participant Start as Starter (the CLI's prepare and launch)
       participant Host as hosts/claude_code_launch (via the CLI)
       Key->>Modal: choices(the shown read), the highlighted row's project_key
       loop every edit, after 0.3 s, in a worker thread
           Modal->>Start: prepare_in(the shown read, key, label or None, message or None)
           Start-->>Modal: name, facts, by_hand, cwd, or the command's refusal
           Note over Modal: an answer for fields that moved since is dropped. Start is enabled only on a current answer
       end
       Modal-->>Key: Start: (key, prepared). Cancel or Escape: nothing
       Key->>Key: a fresh read (the dashboard's loader), in a worker thread
       Key->>Start: again(that read, key, the resolved label, the shown name, the message)
       Note over Key: the name moved, or any refusal: did not start, nothing ran
       Key->>Start: launch(prepared)
       Start->>Host: _open_claude_window(name, prompt, root)
       Key->>Key: OutputScreen New session: started as NAME, or did not start and why. Then a refresh

+---------------+------------------------------------------------------+
| Step          | Rule                                                 |
+===============+======================================================+
| The key       | ``n``, named "new session" in the footer. It offers  |
|               | nothing when the app has no Starter, or the shown    |
|               | read lists no project                                |
+---------------+------------------------------------------------------+
| The project   | A ``Select`` of ``startsession.choices`` of the read |
|               | the table shows, every registered project by name;   |
|               | the highlighted line's ``project_key`` is            |
|               | preselected (a nested host line carries its          |
|               | session's), else the first. Whatever is chosen is    |
|               | resolved by ``ProjectIndex.match`` in ``prepare``,   |
|               | as the command resolves it                           |
+---------------+------------------------------------------------------+
| The fields    | An optional first message and a label; the first     |
|               | message has the focus, so the dashboard's            |
|               | single-letter keys type into it. A blank label takes |
|               | the time, a blank message is none, as on the page    |
+---------------+------------------------------------------------------+
| What it shows | The full name, the first message, ``fact_lines``     |
|               | (the open windows and the main checkout's            |
|               | uncommitted changes, as the command prints them),    |
|               | the exact ``by_hand`` command and its folder, and    |
|               | that Claude runs in its default permission mode. A   |
|               | refusal replaces them with "will not start:          |
|               | ``<why>``", the command's own sentence               |
+---------------+------------------------------------------------------+
| Asking again  | Each edit disables Start and asks again 0.3 s after  |
|               | the last keystroke (``tui.START_DEBOUNCE``), in a    |
|               | thread worker, because the names in use cost a       |
|               | process-table read. An answer for fields that have   |
|               | moved on since is dropped                            |
+---------------+------------------------------------------------------+
| Start         | ``Starter.again``, the page's own rule: prepare      |
|               | again from a fresh read with the label the modal     |
|               | resolved (so a default time label is not taken       |
|               | twice) and refuse if the name or the message         |
|               | differs. Then ``launch``, in a worker thread. No     |
|               | nonce: the modal and the button are one process on   |
|               | the Mac, not a request from another device           |
+---------------+------------------------------------------------------+
| The outcome   | An ``OutputScreen`` titled "New session": "done" and |
|               | "started as ``<name>``" with the launcher's lines,   |
|               | or "failed" and "did not start: ``<why>``",          |
|               | invariant's own sentence for a refusal or a window   |
|               | that did not open. Any other error is shown the same |
|               | way and the dashboard goes on. The table refreshes   |
|               | afterwards                                           |
+---------------+------------------------------------------------------+
| The audit     | None: the audit log is the page's record of what     |
|               | remote devices ran (ADR-008 D13). A start from the   |
|               | TUI is a person at the Mac, like ``start-session``   |
|               | in a terminal                                        |
+---------------+------------------------------------------------------+

The dashboard keeps refreshing under the modal: its timer belongs to the
app, and the modal's workers are in their own group. The tests drive the
modal through ``App.run_test()`` with a recording Starter, and through the
CLI's own Starter behind the suite's launcher and successor seams, so no
test opens a window.

What runs next (IDEA_057, ADR-027 increment 1)
-----------------------------------------------

``invariant oracle next <project> [--json]`` prints what the oracle would
start next in one registered project, and why. It starts nothing; the
watcher's chain step acts on the same verdict ("The chain step" below), and
``invariant oracle chain start`` starts it by hand. The rules are
:need:`DEC_ADR_027_D3` and :need:`DEC_ADR_027_D12`, with increment 2's caps,
resume and proposals (:need:`DEC_ADR_027_D16`, :need:`DEC_ADR_027_D17`,
:need:`DEC_ADR_027_D20`).

+------------------+-------------------------------------+---------------------------+
| Module           | Owns                                | Never does                |
+==================+=====================================+===========================+
| ``chain``        | ``decide`` (pure), ``rank``,        | Read a store or a clock;  |
|                  | ``in_hours``, ``ChainSettings``;    | start anything; import    |
|                  | ``ready_items`` and                 | ``hosts/``                |
|                  | ``live_sessions``, the inputs read  |                           |
+------------------+-------------------------------------+---------------------------+
| ``core/queue``   | The record the oracle reads         | Know what runs next       |
|                  | (``read_queue``, ``queue_path``)    |                           |
+------------------+-------------------------------------+---------------------------+
| CLI              | ``cmd_oracle_next``: the index      | Decide anything ``decide``|
|                  | match, one scan of the project, the | decides                   |
|                  | reads, the verdict printed          |                           |
+------------------+-------------------------------------+---------------------------+

.. mermaid::

   sequenceDiagram
       participant Cmd as oracle next
       participant Index as oracle/index
       participant Scan as oracle/scan
       participant Ledger as oracle/chainstep
       participant Chain as oracle/chain
       Cmd->>Index: match(project): key, root or name
       Note over Cmd: not registered, or no manifest at its root: refused (exit 2)
       Cmd->>Chain: read_inputs(root): the queue (core/queue), the ready items (domains/workitem), oracle.chain
       Cmd->>Scan: scan_all(select=key), assess: live sessions (with their latest report), windows
       Cmd->>Ledger: limits(home, key): the pause, the last resume, the day's starts, the live chains
       Cmd->>Chain: decide(queue, ready, oracle.chain, live, windows, now, pause, resume, caps)
       Chain-->>Cmd: start (auto or propose), wait, paused or idle, and the reasons
       Note over Cmd: printed; nothing is launched

**The order.** A pause first: the chain ledger's, then a live session whose
latest report is ``blocked`` or ``needs-user`` and was made after the last
resume. Then the candidate: the queue's first ``queued`` entry of no
``propose`` mode, else the backlog's first ready item no entry names (in any
state, :need:`DEC_ADR_027_D18`) and no live entry serves
(:need:`DEC_ADR_027_D48`), ranked by tier, priority and natural id;
queued entries of ``propose`` mode ride the verdict as ``proposals``. An
entry the backlog mirror wrote (``added_by: backlog``, increment 5) takes
``oracle.chain.backlog``'s mode, not ``oracle.chain.queue``'s: under
``propose`` the first one rides ``proposals`` and under ``off`` they are
passed over, and the hand-queued work behind them runs
(:need:`DEC_ADR_027_D49`). A candidate naming a bug not yet reproduced
starts reproduce-first (:need:`DEC_ADR_027_D50`). A setting of ``off`` for a
hand-queued head is ``idle``, and that head never falls through to the
backlog. Then one at a time: any live session, or any live
Claude window the scan knows of (``startsession.open_windows``), is ``wait``,
naming the candidate it holds back; a session that reported ``done`` waits for
its teardown. Then the caps, which hold an ``auto`` start as a ``wait`` naming
the ``limit``: the day's chained starts against ``oracle.chain.daily_cap``,
the machine's live chains against ``chain.machine_cap``. Last,
``oracle.chain.hours``: outside it an ``auto`` start is proposed instead.

+-------------+---------------------------------------------------------------+
| Verdict     | When                                                          |
+=============+===============================================================+
| ``start``   | a candidate, its setting ``auto`` or ``propose``, nothing     |
|             | live in the project                                           |
+-------------+---------------------------------------------------------------+
| ``wait``    | a candidate, and a live session, a window or a cap holds it   |
+-------------+---------------------------------------------------------------+
| ``paused``  | the ledger's pause, or a live session's stop report           |
+-------------+---------------------------------------------------------------+
| ``idle``    | nothing queued and nothing ready, or the candidate's setting  |
|             | is ``off``                                                    |
+-------------+---------------------------------------------------------------+

**What it reads.** Everything read-only: the queue from the main checkout (or
the store, D10), the backlog through ``domains/workitem`` with each type's
``ready`` states (the shipped machines mark ``proposed`` and ``accepted``
ideas and ``reproduced`` bugs) and ``reproduce`` states with the residue each
item owes (the shipped bug machine's ``filed``, increment 5),
``oracle.chain`` from the project's manifest,
and one scan of the project through ``_oracle_cache``, which the suite
replaces, so no test reads a process table. The scan's session row carries
the session's latest ``session report`` as ``report`` (:need:`DEC_ADR_027_D11`).
The chain ledger is read without its lock (``chainstep.limits``), and one
that cannot be read is reported as a pause, as the step treats it.

The chain step (IDEA_057, ADR-027 increment 2)
-----------------------------------------------

Every ``oracle watch`` tick, after the alerts, runs the chain step: it
follows the start it made in each project, decides what runs next and acts on
it. ``--no-chain`` opts a watcher out; each project's ``oracle.chain``
settings are the switch (:need:`DEC_ADR_027_D13`). A failed start is triaged,
not paused (:ref:`ruling 9 <adr-027-ruling-9>`).

+------------------+---------------------------------------+---------------------------+
| Module           | Owns                                  | Never does                |
+==================+=======================================+===========================+
| ``chainstep``    | ``ChainStep.step`` (follow, the       | Write a project's record; |
|                  | report pause, decide, reserve,        | open a window itself;     |
|                  | launch, triage), ``start_now``, the   | import ``hosts/``;        |
|                  | ledger ``oracle/chain.json``          | replace a ledger it       |
|                  | (``pause``, ``resume``, ``status``,   | cannot read               |
|                  | ``limits``), ``ChainHands``           |                           |
+------------------+---------------------------------------+---------------------------+
| ``chain``        | ``decide`` with the caps, the resume  | Read a clock; start       |
|                  | time and ``proposals``;               | anything                  |
|                  | ``read_inputs``, ``session_end``,     |                           |
|                  | ``render_prompt``, ``label_for``,     |                           |
|                  | ``by_hand``, ``on_failure``           |                           |
+------------------+---------------------------------------+---------------------------+
| CLI              | ``_chain_hands`` (a seam):            | Decide anything the step  |
|                  | ``start`` is ``_start_prepare`` and   | decides                   |
|                  | ``_start_launch``; ``begin``,         |                           |
|                  | ``mark``, ``fail`` are                |                           |
|                  | ``domains/queue``'s; ``oracle chain`` |                           |
|                  | status, pause, resume, start          |                           |
+------------------+---------------------------------------+---------------------------+

.. mermaid::

   sequenceDiagram
       participant W as watcher tick
       participant S as chainstep
       participant L as oracle/chain.json (locks/chain.lock)
       participant C as oracle/chain
       participant H as ChainHands (the CLI)
       W->>S: step(assess document, now), after the alerts
       S->>L: lock, read (unreadable: do nothing, say so once)
       loop each project the tick read
           S->>S: follow the current start: bind, done, or failed
           Note over S,H: failed: H.fail marks it, queues triage Qn at the head and one retry behind it; a failed triage pauses
           S->>S: a blocked or needs-user report after the last resume: pause
           Note over S: paused, a start to follow, a live session or window: nothing more
           S->>C: read_inputs(root), decide(..., caps, resumed)
           S->>H: start auto: begin(entry) marks it started; reserve the slot
           S->>S: propose, or a cap that holds: one notice
       end
       S->>L: write, unlock
       S->>H: start(key, label, prompt): start-session's launch, outside the lock
       S->>L: lock: running (name, pid), or failed and triaged
       S->>S: deliver the owed notices

**Following a start** (:need:`DEC_ADR_027_D15`). The start is bound to the
session row whose host was launched with its name, or has the pid the launch
returned. Once that session has ended, ``chain.session_end`` reads its record
through an inert store: torn down with no deliverable open is ``done``, and the
entry is marked so.

+------------------------------------------------+----------------------------------+
| A chained start fails when                     | Its reason on the entry          |
+================================================+==================================+
| the launch raises (no successor, one outside   | the launch failed (the launcher's|
| the main checkout, not macOS, no ``claude``)   | words stay in the ledger)        |
+------------------------------------------------+----------------------------------+
| the reservation's process is gone, or it is    | the launch was interrupted       |
| older than 120 s                               |                                  |
+------------------------------------------------+----------------------------------+
| no session is bound 10 minutes after the       | no invariant session was bound   |
| window appeared                                | 10 minutes after the window      |
|                                                | appeared                         |
+------------------------------------------------+----------------------------------+
| every host of the session (or the unbound      | the window closed without a      |
| window) is seen not alive, no other Claude     | teardown or a report             |
| window lives in the project, for 60 s; unknown |                                  |
| liveness never counts, a ``done`` report waits |                                  |
+------------------------------------------------+----------------------------------+
| the session ended torn down with a deliverable | the session was torn down with   |
| open, abandoned, or its record is gone         | deliverables open / abandoned /  |
|                                                | cannot be found                  |
+------------------------------------------------+----------------------------------+

**Ruling 9** (:need:`DEC_ADR_027_D18`, :need:`DEC_ADR_027_D23`). A failure
marks the entry ``failed`` and, through ``ChainHands.fail``, queues in one
locked edit a retry of the same work at the head (``retry_of``; none when the
failed entry was already a retry) and the triage ``triage Qn`` in front of it
(``triage_of``), whose brief is ``oracle.chain.triage_prompt``. The triage
session files and reproduces the bug and judges: a severe bug's fix goes on
the queue as a proposal (``queue add --propose``) and the retry comes off; an
unsafe project gets ``session report --outcome needs-user``, which pauses;
otherwise the retry runs. A triage that fails pauses the chain, and so does a
triage the recorder cannot queue.

**What stops the chain:** a ``blocked`` or ``needs-user`` report made after
the last resume (the pause outlives the session), a failed triage, ``oracle
chain pause``, and nothing left to start. ``oracle chain resume`` lifts a
pause and retries nothing (:need:`DEC_ADR_027_D17`).

**The limits** (:need:`DEC_ADR_027_D16`). A day's starts, counted by the
machine's local calendar day, against ``oracle.chain.daily_cap``; the
projects with a start to follow against the user config's
``chain.machine_cap``. Either turns an ``auto`` start into a ``wait``, said
once per cap and day. A proposal is never held, and neither is ``oracle chain
start``.

**Proposals.** A backlog item under ``oracle.chain.backlog: propose``, any
start outside ``oracle.chain.hours``, and every queued entry of its own
``propose`` mode (:need:`DEC_ADR_027_D20`) is said once, naming ``invariant
oracle chain start <project> [--entry Qn]``. The first entry of no mode behind
a proposed one runs meanwhile.

**The start.** The window is ``oracle start-session``'s own, named
``<project>-<ref>`` (the ref cut to 40 characters), its first prompt the
entry's brief or ``oracle.chain.prompt`` (:need:`DEC_ADR_027_D19`). A backlog
start is recorded as an entry of its own (``added_by: oracle``). The window
binds only once it has run ``invariant session start`` itself, so each shipped
default (the start, triage and reproduce prompts) opens by telling it to run
``invariant session start`` (``{ref}-{entry}``, ``triage-{entry}``,
``reproduce-{ref}-{entry}``), ``session isolate --execute`` and ``session plan
add`` before the work (BUG_158, :need:`DEC_ADR_027_D62`). A brief, or a
project's own template, must carry the same step to bind.

**The ledger** (:need:`DEC_ADR_027_D14`) is described in
:doc:`state_model.md </architecture/state_model>`, "The chain ledger". One
that cannot be read halts the chain until it is mended; it is never replaced.

**Notices** (:need:`DEC_ADR_027_D21`) go through the watcher's notifier and
stay owed in the ledger until one is delivered.

The seams are ``_chain_hands`` and, under it, start-session's:
``_oracle_names_in_use``, ``_handoff_launcher`` and ``_handoff_successor``,
which the suite replaces, so a chained start in a test opens no window and
fails, and is triaged.

Usage and stalls (IDEA_057, ADR-027 increment 2b)
-------------------------------------------------

Increment 2b adds two things the chain step acts on: the plan's weekly usage,
which holds it (:ref:`ruling 10 <adr-027-ruling-10>`), and the ``stalled``
signal, which nudges a chained session once (:ref:`rulings 11 <adr-027-ruling-11>`
and :ref:`12 <adr-027-ruling-12>`). A chained session the chain has finished
with no longer holds its project (:ref:`ruling 13 <adr-027-ruling-13>`).

+--------------------------------+---------------------------------------+---------------------------+
| Module                         | Owns                                  | Never does                |
+================================+=======================================+===========================+
| ``usage``                      | ``PlanUsage``, ``oracle/usage.json``  | Parse a host's output;    |
|                                | (``record_usage``, ``read_usage``),   | start or hold anything    |
|                                | ``weekly_max``, the pure ``hold``     |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``chainstep``                  | the hold once a tick and its notice,  | Type into or close a      |
|                                | ``greenlight``; ``current.stall``,    | window itself; pause on a |
|                                | the nudge outside the lock,           | stall                     |
|                                | ``stall_action``; ``released``        |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``signals`` / ``scan``         | ``stalled``; ``HostView.turn_end``,   | Read a clock or a store   |
|                                | ``Row.stall_after``                   | in ``classify``           |
+--------------------------------+---------------------------------------+---------------------------+
| ``hosts/claude_code_usage``    | the status line's JSON into the fact, | Import the oracle         |
|                                | its text, ``install_statusline``      |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``hosts/claude_code_launch``,  | ``TerminalTyper``; ``turn_ends``: a   | Type into a tab where     |
| ``claude_code_telemetry``      | failed request's code and its quota   | only a shell runs         |
|                                | window                                |                           |
+--------------------------------+---------------------------------------+---------------------------+

**The reading** (:need:`DEC_ADR_027_D24`). Claude Code writes no usage file.
``invariant hook statusline``, installed by the user with ``invariant hooks
statusline`` (which never replaces a ``statusLine`` already there), reads
the status line's ``rate_limits`` on stdin, records ``{weekly,
weekly_resets_at, five_hour, five_hour_resets_at, read_at, host}`` in
``<oracle home>/oracle/usage.json`` and prints ``week 41% · 5h 12%``.

.. mermaid::

   sequenceDiagram
       participant CC as Claude Code status line
       participant Cmd as invariant hook statusline
       participant Host as hosts/claude_code_usage
       participant U as oracle/usage
       participant S as chainstep (each tick)
       CC->>Cmd: JSON on stdin: rate_limits.seven_day, five_hour
       Cmd->>Host: reading_from_statusline(payload, now)
       Cmd->>U: record_usage(home, fact): oracle/usage.json
       Cmd-->>CC: "week 41% · 5h 12%"
       S->>U: read_usage (the injected seam), hold(reading, now, cap, greenlight)
       Note over S: over or unknown: no auto-start, no proposal, one notice

**The hold** (:need:`DEC_ADR_027_D25`). Judged once a tick for the whole
machine: a reading under 30 minutes old with a weekly figure at or over
``chain.weekly_usage_max`` (default 95, the user config) holds as ``over``;
a missing or stale one, or one without a weekly figure, holds as
``unknown`` until ``invariant oracle chain greenlight``, which a reading
newer than it spends. ``decide`` answers a ``wait`` naming ``weekly_usage``
for an auto-start and a proposal alike, after one at a time and before the
caps; ``oracle next`` and ``oracle chain status`` show it. A start the user
asks for is never held.

**Stalled** (:need:`DEC_ADR_027_D26`, :need:`DEC_ADR_027_D27`). The signal
table above. Its cause reads ``HostView.turn_end``, which the probe's
transcripts answer from the last turn: Claude Code writes a failed request
as an entry with ``isApiErrorMessage``, an ``error`` code and, for a usage
limit, ``quotaLimits`` (``rateLimitType``, ``resetsAt``). A hand-started
session's stall is the watcher's alert and nothing more.

**The nudge** (:need:`DEC_ADR_027_D28`). Only the session bound to a
project's chained start, with no report made:

.. mermaid::

   stateDiagram-v2
       [*] --> stalled: the scan reads stalled
       stalled --> waiting_reset: a usage limit, before its reset + 60 s
       waiting_reset --> nudged: the reset passed
       stalled --> nudged: typed "continue" into its tab
       stalled --> not_nudged: no tab, no typer, or typing failed (a notice)
       nudged --> [*]: activity again (the stall clears)
       nudged --> still_stalled: one fuse later, or a new stall within one fuse of the nudge
       still_stalled --> nudged: oracle chain continue
       still_stalled --> [*]: oracle chain retry or close (released)
       not_nudged --> [*]: oracle chain retry or close (released)

The nudge is reserved under the chain lock and typed outside it through
``ChainHands.nudge``: ``TerminalTyper`` finds the Terminal tab by the host's
tty, refuses one where only a shell runs, and sends ``continue`` and a lone
Return. A stall never pauses the chain.

**Continue, Retry, Close** (:need:`DEC_ADR_027_D29`). ``invariant oracle chain
continue|retry|close <project>`` need a recorded stall on the project's
chained start. Continue types once more and re-arms the fuse. Retry and
Close end the window through ``closehost.close_host`` (close-host's seams,
every fact checked again); Retry puts the same entry back at the head
(``domains/queue.requeue``, not ruling 9's retry), Close marks it failed with
no triage.

**Released** (:need:`DEC_ADR_027_D30`). Every failed chained start and every
Retry or Close adds the session to the ledger's ``released``; one at a time
skips those sessions in the step and in ``oracle next``, so the next work
starts beside the parked session, whose worktree is the user's to land or
abandon. A live Claude window still holds its project.
A chained session that landed is released too once the oracle closed its
window on its consent ("A landed window asks to close", ruling 21).

The chain on the phone (IDEA_057, ADR-027 increment 3)
------------------------------------------------------

The phone shows each project's chain and acts on it through the verbs
increments 2 and 2b built (:ref:`rulings 14 to 16 <adr-027-ruling-14>`,
:need:`DEC_ADR_027_D31` to :need:`DEC_ADR_027_D37`). Every action is
confirmed with its command, nonce and expiry; Retry and Close on the
destructive sheet. The screens and every word are
:doc:`oracle-panel.md </architecture/oracle-panel>`'s.

+------------------+---------------------------------------+---------------------------+
| Module           | Owns                                  | Never does                |
+==================+=======================================+===========================+
| ``chainview``    | ``ChainReader``: the ledger           | Write anything; run or    |
|                  | (``chainstep.read_ledger``), each     | launch anything; import   |
|                  | project's queue, the usage hold; each | ``hosts/``                |
|                  | project's status and offered actions; |                           |
|                  | the chain keys; ``derive``            |                           |
+------------------+---------------------------------------+---------------------------+
| ``panel``        | the chain's cards, the Projects row's | Decide what is offered    |
|                  | chain line, the Chain screen,         |                           |
|                  | ``chain_confirm_words``; a chain      |                           |
|                  | step's deed in its result             |                           |
+------------------+---------------------------------------+---------------------------+
| ``webactions``   | a chain key's confirm and action      | Run an argv the page sent |
|                  | (``_chain_confirm``, ``_chain_act``), |                           |
|                  | one per project at a time             |                           |
+------------------+---------------------------------------+---------------------------+
| ``web``          | the chain's read in ``/api/state``'s  | Add a route               |
|                  | view                                  |                           |
+------------------+---------------------------------------+---------------------------+
| CLI              | ``_oracle_chain_reader`` (the usage   | Decide anything the       |
|                  | seam and the cap); ``queue move``     | reader decides            |
|                  | and ``queue remove``                  |                           |
|                  | ``--if-queued-at``; ``oracle chain    |                           |
|                  | start --item``                        |                           |
+------------------+---------------------------------------+---------------------------+

.. mermaid::

   sequenceDiagram
       participant Page as Phone (Chain screen or a card)
       participant Srv as webactions
       participant View as chainview (ChainReader, derive)
       participant Cmd as invariant (queue / oracle chain)
       Page->>Srv: GET /api/confirm?row=KEY|chain:move_up:Q4
       Srv->>Srv: fresh scan
       Srv->>View: read(scan, only=KEY): ledger, queue, usage hold, offered
       Srv->>View: derive: offered now? the argv, its folder, the facts
       Note over Srv,View: not offered: the sheet says why, no deed
       Srv-->>Page: the sheet's words, a nonce bound to the command
       Page->>Srv: POST /api/act {row: KEY|chain:move_up:Q4, nonce}
       Srv->>View: fresh read and derive again
       Note over Srv: not offered any more, or another command: 409, nothing ran
       Srv->>Srv: reserve KEY|chain, audit start line
       Srv->>Cmd: queue move Q4 --to 3 --if-queued-at 4, in the project's root
       Note over Cmd: under the queue's lock: Q4 still queued at 4? else exit 2, nothing moved
       Cmd-->>Srv: exit and output
       Srv-->>Page: result sheet (Why from invariant's own line)

+-------------------------------+---------------------------------------------------+
| Action                        | Offered when, and what runs                       |
+===============================+===================================================+
| Move up, move down            | A queued entry that is not first (up) or last     |
|                               | (down) among the queued ones. ``queue move Qn     |
|                               | --to <the neighbour's position> --if-queued-at    |
|                               | <its own>``                                       |
+-------------------------------+---------------------------------------------------+
| Remove                        | Any queued entry. ``queue remove Qn               |
|                               | --if-queued-at <its position>``                   |
+-------------------------------+---------------------------------------------------+
| Start                         | No chained start to follow, and a queued          |
|                               | propose-mode entry or a proposal the step made.   |
|                               | ``oracle chain start <key> --entry Qn``, or       |
|                               | ``--item <id>`` for a backlog proposal            |
+-------------------------------+---------------------------------------------------+
| Pause, Resume                 | Pause while not paused, Resume while paused.      |
|                               | ``oracle chain pause <key>`` or ``oracle chain    |
|                               | resume <key>``                                    |
+-------------------------------+---------------------------------------------------+
| Continue, Retry, Close        | A stall recorded on the project's current chained |
|                               | start (D29). ``oracle chain continue <key>``,     |
|                               | ``retry`` or ``close``. A hand-started session's  |
|                               | stall offers none                                 |
+-------------------------------+---------------------------------------------------+
| Go ahead (the greenlight)     | The weekly hold is ``unknown`` (D25). ``oracle    |
|                               | chain greenlight``, in the oracle home            |
+-------------------------------+---------------------------------------------------+

**What it reads.** On every ``/api/state``, and again for each confirm and
action, the chain ledger without its lock, each registered project's queue
and the plan's last usage reading: the read is never older than the page.
A queue's path is found again once a minute. A ledger that cannot be read
offers nothing anywhere; a queue that cannot be read offers nothing in its
project; either is named under the Chain screen's Technical details.

**The seams** are the chain step's: ``_oracle_plan_usage`` (the suite
replaces it where a test needs a reading), and the page's runner,
``_oracle_runner``, so no test runs a chain verb from the page. The queue
and chain verbs a confirmed action runs are the CLI's own, with their own
refusals.

Web push (IDEA_057, ADR-027 increment 4)
----------------------------------------

Every oracle alert and every chain notice also reaches each phone that turned
push on, under the same rising-edge de-dup and owed-until-delivered ledgers
(:ref:`ruling 17 <adr-027-ruling-17>`, IDEA_013, :need:`DEC_ADR_027_D38` to
:need:`DEC_ADR_027_D43`). Push is one more notifier behind the seam: the
watcher and the chain step are unchanged, and push has no filter of its own.

+------------------+----------------------------------------+---------------------------+
| Module           | Owns                                   | Never does                |
+==================+========================================+===========================+
| ``push``         | ``PushBook`` (``web/push.json``: the   | Reach the network but     |
|                  | VAPID key pair, each paired device's   | through the injected      |
|                  | subscriptions), ``check_subscription``,| POST; send to an unpaired |
|                  | ``payload``, ``encrypt`` (RFC 8291),   | device; send more than    |
|                  | ``vapid_authorization`` (RFC 8292),    | the banner's text; import |
|                  | ``PushNotifier``, ``urllib_post``      | ``hosts/``                |
+------------------+----------------------------------------+---------------------------+
| ``notify``       | ``FanOutNotifier``, ``Outcome``,       | Decide what deserves a    |
|                  | ``NobodyToNotify``                     | notification              |
+------------------+----------------------------------------+---------------------------+
| ``web``          | ``/sw.js``, ``/api/push/subscribe``    | Offer push over plain     |
|                  | and ``unsubscribe``; this device's     | HTTP or without           |
|                  | push in ``/api/state``'s view          | ``cryptography``          |
+------------------+----------------------------------------+---------------------------+
| ``webpage``,     | the service worker; the Notifications  | Hold a word of its own    |
| ``panel``        | row and its words                      |                           |
+------------------+----------------------------------------+---------------------------+
| CLI              | ``_oracle_notifier`` (the fan-out),    | Send a push itself        |
|                  | ``_oracle_push_post`` (a seam);        |                           |
|                  | ``oracle web`` hands the page the      |                           |
|                  | book; ``web revoke`` drops a device's  |                           |
|                  | subscriptions; ``notify-test``         |                           |
+------------------+----------------------------------------+---------------------------+

.. mermaid::

   sequenceDiagram
       participant Phone as Phone (the page, then its service worker)
       participant Web as oracle web
       participant Book as web/push.json
       participant W as oracle watch (a tick)
       participant Fan as FanOutNotifier
       participant PS as push service (Apple, Google, Mozilla, Windows)
       Phone->>Web: GET /api/state: the VAPID key, made on first need
       Phone->>Phone: register /sw.js; Turn on: pushManager.subscribe (the browser asks)
       Phone->>Web: POST /api/push/subscribe {endpoint, keys}, same origin
       Web->>Book: check_subscription; keep it for this device
       W->>Fan: notify(title, subtitle, message): a rising edge or an owed notice
       Fan->>Fan: the Mac banner (osascript), first
       Fan->>Book: the subscriptions of devices still paired
       Fan->>PS: POST aes128gcm ciphertext, VAPID, TTL 12 h, 5 s timeout
       PS-->>Fan: 201; 404 or 410 (pruned); a failure (reported, the endpoint rests 60 s)
       PS->>Phone: the ciphertext, which the browser decrypts
       Phone->>Phone: showNotification(title, subtitle and message); a tap opens the panel

**What is sent** (:need:`DEC_ADR_027_D40`). The banner's own text, as JSON
``{title, subtitle, message, silent}``, cut to 120, 160 and 600 characters:
the title ``invariant: <project>``, the row's label or ``chain <action>``,
and the notice's one line. Never a brief, a prompt, a transcript or a path.
It is sealed for the browser's key and auth secret (RFC 8291, one
``aes128gcm`` record of 4096 bytes), so the push service relays ciphertext.
Each POST carries ``TTL: 43200`` (a phone that is off gets it within 12
hours), ``Urgency: high`` for an alert that sounds and ``normal`` for a silent
one, and a VAPID token (ES256, 12 hours) whose ``aud`` is the push service and
whose ``sub`` is the page's own https origin, the one the device subscribed
from.

**Where it is kept** (:need:`DEC_ADR_027_D39`). ``<oracle home>/web/push.json``,
beside ``devices.json``: a record, owner-only from creation, under
``locks/push.lock``, never in a manifest (:doc:`state_model.md
</architecture/state_model>`, "Web push"). The VAPID key is made the first
time the page offers push. A subscription belongs to the device whose token
posted it, at most four per device; ``oracle web revoke`` drops it, a send
skips any subscription whose device is no longer paired, and nothing is sent
when the paired devices cannot be read.

**When it is offered** (:need:`DEC_ADR_027_D41`). Only over HTTPS (a service
worker and the Push API need a secure context) and only where
``cryptography``, in the ``invariant[oracle]`` extra, is installed on the Mac.
Otherwise the page's Notifications row says why and offers nothing. An
endpoint is kept only when it is https on port 443 at one of the push services
browsers use (``push.apple.com``, ``fcm.googleapis.com``,
``push.services.mozilla.com``, ``notify.windows.com``, or a host below one),
so a paired device cannot point the oracle's POSTs elsewhere. Opting in is the
device's own setting, not an oracle action: it runs nothing on the Mac, and
the browser's own permission prompt is its confirm, so it has no confirm
sheet. On an iPhone, push works in the home-screen app (ADR-009 D10).

**When a channel fails** (:need:`DEC_ADR_027_D38`). The Mac banner goes first,
so push never delays it. A POST takes at most 5 s; an endpoint that could not
be reached, or answered 429 or a 5xx, rests 60 s, so a slow push service costs
one timeout per device a minute, not one per notification. A 404 or 410
removes the subscription. One device failing never stops another. The
fan-out counts a notification delivered once any channel delivered it: the
ledger does not owe it again, the failure is a line in ``oracle watch``'s
output (``oracle watch: push to iPhone could not notify: …``), and that
device's row reads "On, but the last one did not arrive". A notification no
channel delivered stays owed, as before.

**The seams.** ``_oracle_push_post`` is the POST; ``conftest.py`` replaces it
for every test with one that refuses, and the socket guard refuses anything
past loopback besides. The tests run the real cryptography: RFC 8291's
example reproduced byte for byte, a round trip through a browser key made in
the test, and the VAPID signature verified with the public key the page is
given. The page's script and the service worker run whole under ``node`` or
``jsc`` against fakes of the push API.

The backlog on the queue (IDEA_057, ADR-027 increment 5)
--------------------------------------------------------

Every open backlog item is on its project's queue, the chain works through it
on its own, and a filed bug is reproduced before anyone fixes it
(:ref:`rulings 18 to 20 <adr-027-ruling-18>`, :need:`DEC_ADR_027_D44` to
:need:`DEC_ADR_027_D52`). The shipped defaults are on for every project; a
project opts out in its manifest (``oracle.chain.mirror``,
``oracle.chain.backlog``, ``oracle.chain.reproduce_first``,
:doc:`manifest.md </architecture/manifest>`).

+------------------+---------------------------------------+---------------------------+
| Module           | Owns                                  | Never does                |
+==================+=======================================+===========================+
| ``domains/queue``| ``mirror`` (the reconcile), ``link``, | Read the oracle's ledger; |
|                  | ``add --serves``, ``after_reproduce`` | start anything            |
+------------------+---------------------------------------+---------------------------+
| ``core/queue``   | ``serves``, ``blind``, ``mirrored``,  | Know what is mirrored     |
|                  | ``BACKLOG``, ``rank_key``, the        |                           |
|                  | reproduction's outcome words          |                           |
+------------------+---------------------------------------+---------------------------+
| ``chain``        | mirrored entries under                | Write anything; parse an  |
|                  | ``oracle.chain.backlog``;             | agent's verdict words     |
|                  | ``reproducing``,                      |                           |
|                  | ``reproduce_outcome``, the reproduce  |                           |
|                  | prompt                                |                           |
+------------------+---------------------------------------+---------------------------+
| ``chainstep``    | the mirror once a minute on an idle   | Write the queue itself    |
|                  | tick; a reproduce start in the        |                           |
|                  | ledger; the verdict routed when its   |                           |
|                  | session lands                         |                           |
+------------------+---------------------------------------+---------------------------+
| CLI              | ``ChainHands.mirror`` and             | Decide what is mirrored   |
|                  | ``.reproduced`` over                  |                           |
|                  | ``domains/queue``; ``queue mirror``,  |                           |
|                  | ``queue link``, ``queue add           |                           |
|                  | --serves``                            |                           |
+------------------+---------------------------------------+---------------------------+

.. mermaid::

   sequenceDiagram
       participant W as watcher tick (chainstep)
       participant H as ChainHands (the CLI)
       participant Q as domains/queue
       participant B as main checkout's backlog (domains/workitem)
       participant C as oracle/chain
       W->>H: an idle project, once a minute: mirror(key)
       H->>Q: mirror()
       Q->>B: every item, its machine's mirror states, live sessions
       Note over Q: under the queue's lock: closed or served mirrored entries leave; new items go in by rank
       W->>C: read_inputs: queue, ready, unreproduced bugs; decide
       C-->>W: start Qn (auto; reproduce-first for a filed bug)
       W->>H: begin, start: the reproduce prompt
       Note over W: the session lands (torn down, plan complete)
       W->>C: reproduce_outcome(root, bug, residue at the start)
       W->>H: reproduced(key, Qn, red|closed|owed|blind)
       H->>Q: after_reproduce: red at the head, closed done, owed or a second blind a proposal, blind at the head

**The mirror** (:need:`DEC_ADR_027_D44` to :need:`DEC_ADR_027_D47`). One
idempotent reconcile over the backlog of the main checkout (or the store),
under the queue's lock, writing only when something changed. An item in a
state its machine marks ``mirror`` (the shipped bug machine ``filed`` and
``reproduced``; ideas ``proposed``, ``accepted`` and ``in_progress``), or one
``oracle.chain.mirror_states`` names, gets one entry ``added_by: backlog``,
before the first queued mirrored entry whose item ranks after it, else at the
end. It is skipped while any entry names it in any state, while a live entry
serves it, while a live session names it in its name or an open deliverable,
and once the mirror has added it in its current open spell (the queue's
``mirrored``), so an entry the user removed stays off. A queued mirrored
entry leaves when its item leaves the mirrored states or another live entry
serves it. The chain step has the hands run it for an idle project at most
once a minute; ``invariant queue mirror`` runs it by hand.

**Serves** (:need:`DEC_ADR_027_D48`). ``invariant queue add REF --serves
ITEM`` and ``invariant queue link ID ITEM`` (``--clear``) say which open item
an entry serves; the mirror, the fallback and ``oracle chain start --item``
pass over that item while the entry is queued or started.

**Backlog work starts on its own** (:need:`DEC_ADR_027_D49`). A mirrored entry
starts under ``oracle.chain.backlog``, ``auto`` by default, with every limit of
increment 2 and 2b: one at a time, the caps, the hours, the weekly hold.

**Reproduced first** (:need:`DEC_ADR_027_D50`, :need:`DEC_ADR_027_D51`). A
queued entry naming an item in a ``reproduce`` state (a filed bug) starts with
``oracle.chain.reproduce_prompt``, which names the bug-reproducer agent and
says to land the verdict on the item and fix nothing; the ledger's ``current``
records ``reproduce`` and the residue the bug owed. When the session lands,
the item says the verdict:

+--------------------------------------+------------+--------------------------------------+
| The bug, once the session landed     | Outcome    | The same entry                       |
+======================================+============+======================================+
| in a ``ready`` state (reproduced)    | ``red``    | back at the head, queued: the fix    |
|                                      |            | starts on this or the next tick      |
+--------------------------------------+------------+--------------------------------------+
| in no ``reproduce`` state, or gone   | ``closed`` | done                                 |
+--------------------------------------+------------+--------------------------------------+
| unreproduced, owing more residue     | ``owed``   | a propose entry where it stands,     |
| than at the start                    |            | for the user's live repro            |
+--------------------------------------+------------+--------------------------------------+
| unreproduced, nothing more owed      | ``blind``  | at the head for one more run; after  |
|                                      |            | a second, a propose entry            |
+--------------------------------------+------------+--------------------------------------+

A verdict that cannot be read marks the entry done. A reproduce session that
fails is ruling 9's, and nothing here pauses the chain.

**The seams** are the step's ``outcome`` (``chain.reproduce_outcome``, which
the suite replaces) and the hands' ``mirror`` and ``reproduced``, which
``_chain_hands`` builds over ``domains/queue``; no test writes the main
checkout's queue.

A landed window asks to close (IDEA_062, ADR-027 ruling 21)
------------------------------------------------------------

A chained session that lands (torn down with its plan complete) leaves its
Claude window open, and a live window holds its project under one at a time
(:need:`DEC_ADR_027_D5`). The chain step follows that window until it closes:
once it has been quiet it asks the session, in its own tab, whether the
window may close, and closes it when the session consents
(:ref:`ruling 21 <adr-027-ruling-21>`, :need:`DEC_ADR_027_D53` to
:need:`DEC_ADR_027_D61`). Consent is an engine record the session writes with
a command, never text read from its transcript. A window the user typed in
since the teardown is the user's, and is never asked or closed.

**Becoming the fallback** (:need:`DEC_ADR_031`, proposed). If ADR-031 is
approved and built, a chained session's final teardown ends its own window, and this ask
is the fallback for a landed chained window that did not end itself (no
closer on its host, the exit failed or was not scheduled, a session from
before the record). The user's window and one left open then stop holding
the project. Until then this section describes the oracle as built; see
"Planned: finished sessions end their own window" below.

+------------------+---------------------------------------+---------------------------+
| Module           | Owns                                  | Never does                |
+==================+=======================================+===========================+
| ``chainstep``    | ``landed`` from the tick a start is   | Type into or close a      |
|                  | done; the quiet check, the ask        | window itself; read       |
|                  | reserved and typed outside the lock,  | consent from a transcript;|
|                  | the close on consent, the notice;     | close the user's window   |
|                  | ``consent``, ``landed_action``;       |                           |
|                  | ``landings``                          |                           |
+------------------+---------------------------------------+---------------------------+
| ``chain``        | ``ask_line``, ``session_landed``      | Read a clock              |
|                  | (the teardown's heartbeat), the       |                           |
|                  | ``close_*`` settings                  |                           |
+------------------+---------------------------------------+---------------------------+
| ``attention``    | ``prompts`` and ``prompted``: the     | Count a background task's |
|                  | person's prompts                      | notice                    |
+------------------+---------------------------------------+---------------------------+
| ``chainview``,   | the landed window in the read, its    | Offer Close or Leave open |
| ``panel``        | offers, card, Now line and sheets     | before the notice         |
+------------------+---------------------------------------+---------------------------+
| CLI              | ``oracle chain consent``,             | Close a window by any     |
|                  | ``close-window``, ``leave-open``;     | rule of its own           |
|                  | ``_chain_landed_host`` (a fresh scan);|                           |
|                  | the typer and closer the hands carry  |                           |
+------------------+---------------------------------------+---------------------------+

.. mermaid::

   stateDiagram-v2
       [*] --> watching: the start is done (its session torn down, plan complete)
       watching --> asking: quiet close_ask_after minutes (idle turn, no activity)
       asking --> asked: typed into its tab
       asking --> notified: no tab, a shell-only tab, no typer, typing failed
       asked --> consented: consent --close
       asked --> watching: consent --not-yet (asked again close_ask_again minutes later)
       asked --> notified: no answer within close_answer_within, or the last "not yet"
       consented --> closing: its turn idle, or 60 s after the consent
       closing --> [*]: closed through close-host's path (released, the chain goes on)
       closing --> notified: the close failed
       notified --> consented: a late consent --close
       notified --> [*]: close-window (the user)
       notified --> left_open: leave-open (the user)
       watching --> users: a turn the user typed since the teardown
       asked --> users: more prompts than the oracle's asks
       watching --> notified: the attention record cannot be read
       left_open --> [*]: the window closed
       users --> [*]: the window closed

**Finding the window** (:need:`DEC_ADR_027_D53`). By its launched name or pid,
as a start is bound, on the orphan host row it becomes once its session is
torn down. Seen gone for 60 s it ends as ``gone``. While it lives it holds the
project as any live window does, so ``decide`` is unchanged; the settings of
the day (:need:`DEC_ADR_027_D60`, four ``oracle.chain.close_*`` numbers) are
read once, when it lands.

**The user's window** (:need:`DEC_ADR_027_D54`). The attention record counts
the person's prompts (``prompts``) and stamps the last (``prompted``). Before
the first ask, a prompt later than the session's teardown (its record's last
``heartbeat``) makes the window the user's; after it, more prompts than the
oracle's own asks do. A live session in the window is the user's too. One
notice says the window holds the project; it is never asked and never closed.
A record that cannot be read is never taken for quiet: the user is asked
instead, with the notice below.

**The ask** (:need:`DEC_ADR_027_D55`, :need:`DEC_ADR_027_D56`). Quiet is the
window's turn ``idle`` and its last activity ``close_ask_after`` minutes old.
The line, ``chain.ask_line``, is reserved under the chain lock and typed
outside it through ``ChainHands.nudge``, the "continue"'s typer: the tab by
its tty, never a tab where only a shell runs, then a lone Return.

**The answer** (:need:`DEC_ADR_027_D57`). ``invariant oracle chain consent
<project> --close | --not-yet --note TEXT``, run by the session, records it
under the chain lock; the note is vetted as a handoff note is. An answer from
another Claude (``CLAUDE_PID``) is refused, and so is one nobody asked for.
"Not yet" is asked again ``close_ask_again`` minutes later, once quiet,
``close_asks`` times in all.

**The close** (:need:`DEC_ADR_027_D58`). The command only records; the next
tick, once the answering turn is over or 60 s later, closes the window
through ``ChainHands.close`` (``closehost.close_host`` with close-host's
seams, every fact checked again) and releases the session. The next tick
after that starts the next work.

**The notice** (:need:`DEC_ADR_027_D59`). No answer within
``close_answer_within`` minutes, the last "not yet", an ask that could not be
typed, a record that cannot be read, or a close that failed: one notice, with
a sound, naming ``invariant oracle chain close-window <project>`` and
``leave-open <project>``, and no more asks. Close ends the window the same
way; Leave open keeps it, holding the project, never asked again. The phone
offers both on a card (:need:`DEC_ADR_027_D61`, :doc:`oracle-panel.md
</architecture/oracle-panel>`).

**The seams** are the hands' ``nudge`` and ``close`` (``_chain_typer``,
``_chain_closer``), the step's ``landed_at`` (``chain.session_landed``), and
the CLI's ``_chain_landed_host`` over ``_oracle_landed_rows``; the suite
replaces each, so no test types into a terminal or closes a window.

Planned: finished sessions end their own window (IDEA_064, ADR-031)
-------------------------------------------------------------------

**Planned, not built.** ADR-031 is proposed (2026-11-22) and awaits the
user's approval of the plan; none of its five increments is built or queued. This section says where each part will live and how it
joins what is built. Everything above describes the oracle as built.

A chained session's final teardown records its consent to close on the
session record and schedules its own exit; the closer ends the window once
the landing's own work is over (its turn, and the background task that
carried the teardown, which the host names), no other work is open and the
person's prompt count is unchanged, and the
chain step sees the window gone and starts the next entry on the next tick
(:need:`DEC_ADR_031_D1` to :need:`DEC_ADR_031_D6`). A landed chained window
the user keeps (typed into after the landing, or left open) stays open and no
longer holds its project (:need:`DEC_ADR_031_D7`, :need:`DEC_ADR_031_D9`);
one with other work still running holds it only until that work is over
(:need:`DEC_ADR_031_D8`). Ruling 21's ask, above, is the fallback.

+--------------------------------+---------------------------------------+---------------------------+
| Module (planned)               | Owns                                  | Never does                |
+================================+=======================================+===========================+
| ``domains/session``            | the ``landing`` record, written in    | Import ``oracle/`` or     |
|                                | the final teardown's save; its        | ``hosts/``; end a process |
|                                | ``outcome``, written by the closer    |                           |
+--------------------------------+---------------------------------------+---------------------------+
| CLI (``session teardown``,     | ``oracle.chain.end_window``, the      | Decide what runs next     |
| ``cli/host_close``)            | stamp read from the attention record, |                           |
|                                | the ``_landing_closer`` seam, the     |                           |
|                                | detached closer's wait, check and end |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``chainstep``, ``chain``       | the landing record on ``landed``,     | Import ``hosts/``; end a  |
|                                | ``ending`` and ``ongoing``,           | window on this path; read |
|                                | ``ended_itself``, the ledger's        | consent from a transcript |
|                                | ``kept``, which one at a time passes  |                           |
|                                | over                                  |                           |
+--------------------------------+---------------------------------------+---------------------------+

.. mermaid::

   sequenceDiagram
       participant R as compliance-reviewer (FINAL)
       participant T as session teardown (CLI)
       participant D as domains/session
       participant K as cli/host_close (detached)
       participant S as chainstep (watcher tick)
       R->>T: invariant session teardown, plan complete
       T->>D: torn_down, and the landing record (at, host, prompts, background, exit, wait)
       T->>K: start the closer (CLAUDE_PID through the host seam)
       T->>D: the host hook adds the carrier (main, or the background task that ran teardown)
       Note over K: waits for the landing's own work (its turn and its carrier task) to end, at most end_window_wait
       K->>K: prompts above the stamp is users, other work open is ongoing, unreadable is unread
       K->>D: outcome (ended, users, ongoing, unread or failed)
       S->>D: read the landing record (inert store)
       S->>S: window gone after ended: ended_itself, next start on this tick
       S->>S: users or left open go to kept and hold nothing, ongoing holds until idle
       S->>S: no exit or a failed one: ruling 21's ask, as above

The settings are ``oracle.chain.end_window`` (``chained``, ``all`` or
``off``) and ``oracle.chain.end_window_wait`` (minutes), per project
(:need:`DEC_ADR_031_D3`). Chain status counts every session started in the
project beside the chained ones (:need:`DEC_ADR_031_D12`). A session's
follow-up goes on the queue by rank, and ``session handoff`` is deprecated
(:need:`DEC_ADR_031_D10`, :need:`DEC_ADR_031_D11`).

Shipped: a window's last message (ADR-029 increment 1)
------------------------------------------------------

IDEA_029's Read half (:need:`DEC_ADR_009_D17`, :need:`DEC_ADR_029_D11`'s
excerpt). Each live Claude window's last message reaches its card, its row on
the Leftovers screen and its detail, on a paired device only; the screens and
words are :doc:`oracle-panel.md </architecture/oracle-panel>`'s "As built for
ADR-029 increment 1".

+--------------------------------+---------------------------------------+---------------------------+
| Module                         | Owns (increment 1)                    | Never does                |
+================================+=======================================+===========================+
| ``talk`` (new)                 | ``SessionTalk`` with ``conversation`` | Import ``hosts/``; write  |
|                                | only, ``TalkHost``, the shapes it     | a session's text          |
|                                | reads, ``last_message``               | anywhere                  |
|                                | (:need:`DEC_ADR_029_D14`)             |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``web``                        | ``excerpts``: on ``/api/state``, once | Read for an unpaired      |
|                                | the token verifies, each live window  | request; put the text in  |
|                                | read through the injected seam, one   | the ``assess`` document   |
|                                | failure costing only its own window   |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``panel``                      | ``excerpt_hosts``, which window each  | Read a transcript; word   |
|                                | row's text is read from; the card's,  | the text                  |
|                                | row's and detail's excerpt and fold   |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``webpage``                    | the inset blocks, "Show all"; the     | Render the text as markup |
|                                | text set with ``textContent``         |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``hosts/claude_code_talk``     | ``ClaudeCodeTalk.conversation``: the  | Import the oracle; write  |
| (new)                          | transcript read, fail closed, capped, | anything; read a shape it |
|                                | kept in memory per transcript         | does not know as anything |
|                                | (:need:`DEC_ADR_029_D15` to D17)      | but ``Unreadable``        |
+--------------------------------+---------------------------------------+---------------------------+
| ``hosts/claude_code_telemetry``| ``scan_tail`` and ``parse_timestamp``,| Stop at a shape it does   |
|                                | public now for the talk reader        | not know (its own readers |
|                                |                                       | skip)                     |
+--------------------------------+---------------------------------------+---------------------------+
| CLI                            | ``_oracle_talk``, handed to           | Decide anything ``talk``  |
|                                | ``WebApp`` by ``oracle web``          | decides                   |
+--------------------------------+---------------------------------------+---------------------------+

.. mermaid::

   sequenceDiagram
       participant Page as Phone (paired)
       participant Web as web
       participant Panel as panel
       participant Talk as talk.last_message
       participant Host as SessionTalk (ClaudeCodeTalk, injected)
       Page->>Web: GET /api/state (token checked first)
       Web->>Web: load: the assess document (snapshot or scan)
       Web->>Panel: excerpt_hosts(document): row key to live window
       loop each live window
           Web->>Host: conversation(TalkHost(id))
           Host->>Host: <id>.jsonl under the Claude home, read back at most 2 MiB, stop at an unknown shape
           Host-->>Web: entries (oldest first), unreadable or none
           Web->>Talk: last_message: Claude's newest text, the prompt before it
       end
       Web->>Panel: view(document, excerpts)
       Web-->>Page: {document (no text), view (cards, rows, details with the text)}, no-store

**Which windows.** ``panel.excerpt_hosts`` names, for each ``session`` and
``host`` row, its current window (else its first) while its liveness is
``True``; a gone window, one of unknown liveness, a worktree row and an error
row are not read. The reader finds ``<host id>.jsonl`` under any directory of
the Claude home's ``projects/``, so a window BUG_040 files under the wrong
project still shows its own text (:need:`DEC_ADR_029_D17`).

**What is read.** The newest entries with text, read backwards from the end
of the transcript under the tail reader's rules (the open last line left out,
at most 2 MiB): at most 512 KiB of text, each entry cut at 20 000
characters, ending once 40 entries and Claude's newest text are held. Since
increment 2 Claude's tool calls are entries too, one line each, and the read
goes on past a run of them to Claude's text (:need:`DEC_ADR_029_D19`).
Claude Code's own ``user`` entries, tool results, a subagent's entries,
bookkeeping and entries without text are skipped; any other shape stops the
read, and only what is newer stands (:need:`DEC_ADR_029_D15`,
:need:`DEC_ADR_029_D16`). ``last_message`` takes Claude's newest text and the
newest prompt before it (:need:`DEC_ADR_029_D14`).

**What is kept.** Nothing on disk, nothing in the document: the
``assess`` document, so the watcher's snapshot, ``oracle list`` and the TUI,
never carry it, and the response is ``no-store`` like every other. The reader
keeps each read in memory against the transcript's size and modification
time, for at most 64 transcripts, so a quiet window is not read again on the
next refresh. The page drops the text after 60 seconds in the background, as
built with increment 2 (below, :need:`DEC_ADR_029_D24`).

**The seam** is ``_oracle_talk``, read-only in this increment like
``_oracle_cache``: a test that serves the state from the app ``oracle web``
built replaces it with a recorder. The suite-wide recorder ADR-029 D4 names
comes with ``deliver``, in increment 3.

Shipped: a window's conversation (ADR-029 increment 2)
------------------------------------------------------

The live conversation view (:need:`DEC_ADR_029_D11`'s second bullet,
:need:`DEC_ADR_009_D17`). A paired device opens a live window's recent
conversation from its session's detail and watches it as it grows. Reading
only: nothing is sent, offered or answered, and no talk nonce is issued. The
screen and its words are :doc:`oracle-panel.md </architecture/oracle-panel>`'s
"As built for ADR-029 increment 2".

+--------------------------------+---------------------------------------+---------------------------+
| Module                         | Owns (increment 2)                    | Never does                |
+================================+=======================================+===========================+
| ``talk``                       | ``turns`` (the newest 40, capped      | Import ``hosts/``; write  |
|                                | again, a stop among them said),       | a session's text          |
|                                | ``read_turns`` (a read that raises is | anywhere; offer anything  |
|                                | unreadable), ``cursor``, ``offered``  | in this increment         |
|                                | (:need:`DEC_ADR_029_D21`,             |                           |
|                                | :need:`DEC_ADR_029_D22`)              |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``web``                        | ``GET /api/session``: the token first,| Read a window that is not |
|                                | the row's live window read through    | live; serve more than     |
|                                | the seam, ``unchanged`` on the same   | 512 KiB                   |
|                                | cursor, the oldest turns dropped to   |                           |
|                                | 512 KiB                               |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``panel``                      | ``conversation_head`` (kicker, title, | Read a transcript; word   |
|                                | chips); a detail's ``conversation``   | the text                  |
|                                | (:need:`DEC_ADR_029_D23`); the words  |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``webpage``                    | the Conversation screen, its 2-second | Render the text as        |
|                                | read, the bottom and the pill, the    | markup; keep any session  |
|                                | 60-second background drop of every    | text past a minute in the |
|                                | text (:need:`DEC_ADR_029_D24`)        | background                |
+--------------------------------+---------------------------------------+---------------------------+
| ``hosts/claude_code_talk``     | each tool call as one line            | Import the oracle; word a |
|                                | (``tool_line``,                       | tool call it cannot read  |
|                                | :need:`DEC_ADR_029_D20`); the read    | (it is ``Unreadable``)    |
|                                | that goes on to Claude's newest text  |                           |
|                                | (:need:`DEC_ADR_029_D19`)             |                           |
+--------------------------------+---------------------------------------+---------------------------+

.. mermaid::

   sequenceDiagram
       participant Page as Phone (Conversation, open and visible)
       participant Web as web
       participant Panel as panel
       participant Talk as talk
       participant Host as SessionTalk (ClaudeCodeTalk, injected)
       Page->>Web: GET /api/session?row=K[&after=C] (token checked first)
       Web->>Web: load: the assess document (snapshot or scan)
       Web->>Panel: conversation_head(document, K), excerpt_hosts(document)
       alt no such row
           Web-->>Page: 404
       else the row's window is not live
           Web-->>Page: live false, no turns, offered why gone
       else a live window
           Web->>Talk: read_turns(talk, TalkHost(id))
           Talk->>Host: conversation(TalkHost(id))
           Host-->>Talk: entries with tool lines (kept in memory per transcript), unreadable or none
           Talk-->>Web: the newest 40, capped, unreadable when the stop is among them
           Web->>Web: drop the oldest turns to 512 KiB, the cursor of what is served
           Web-->>Page: head, turns, unreadable, offered (all off, why), cursor, or unchanged when it equals C
       end
       Note over Page: again 2 s after each answer, and hidden 60 s every text is dropped

**The route.** ``GET /api/session?row=<key>[&after=<cursor>]``, behind the
token as every route is, checked before anything is read; an unpaired request
gets "not paired" and reads no transcript. It loads the document as
``/api/state`` does and reads only the row's live window, the one
``panel.excerpt_hosts`` names for the excerpt. The answer is ``no-store``,
at most 512 KiB as encoded, and holds ``row``, ``head`` (kicker, title,
chips), ``live``, ``turns`` (each ``role`` ``user``, ``assistant`` or
``tool``, ``text``, ``at``, ``cut``), ``unreadable``, ``offered`` (send,
answer and interrupt off, with ``why`` ``reading_only``, ``unreadable`` or
``gone``) and ``cursor``. Given the cursor of the same read as ``after``, it
is ``unchanged: true`` with no turns and no text (:need:`DEC_ADR_029_D21`).
A row the document does not have is 404; a load that fails is 503.

**Fail closed.** A stop the host met among the newest 40 is ``unreadable``,
and the page shows "Can't read this session here — use the Mac." above the
newer turns; a read that raises is unreadable with no turns; a window no
longer live reads nothing (:need:`DEC_ADR_029_D22`).

**What is kept.** As for the excerpt: nothing on disk, nothing in the
``assess`` document, the watcher's snapshot, ``oracle list`` or the TUI,
nothing in a log, a notification or a push. The server holds only the
request's read and the reader's in-memory tail; the page holds the last
answer while the screen is open and drops it, with every other text, after a
minute in the background (:need:`DEC_ADR_029_D24`).

Shipped: sending to a window (ADR-029 increment 3)
--------------------------------------------------

The oracle's second power beside the confirmed action
(:need:`DEC_ADR_029_D1`): a paired device sends a live Claude window a
message, which the oracle types into its Terminal tab. Messages only; the
interrupt, slash commands and the answers are increments 4 to 6. The screen
and its words are :doc:`oracle-panel.md </architecture/oracle-panel>`'s "As
built for ADR-029 increment 3".

+--------------------------------+---------------------------------------+---------------------------+
| Module                         | Owns (increment 3)                    | Never does                |
+================================+=======================================+===========================+
| ``talk``                       | ``Talker``: the one-tap nonce, the    | Import ``hosts/``; keep   |
|                                | Undo hold, the ``Fingerprint`` and    | the text past its typing; |
|                                | its check, readiness, the ring, the   | retry a send; type while  |
|                                | delivery loop, the quiet wait, the    | Claude waits on a prompt; |
|                                | receipt; ``typist`` (the per-tab file | log the text              |
|                                | lock); ``read_attention``;            |                           |
|                                | ``RecordingTalk``                     |                           |
|                                | (:need:`DEC_ADR_029_D25` to D33)      |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``web``                        | ``POST /api/talk``,                   | Take a talk post from     |
|                                | ``/api/talk/cancel``; ``/api/session``| another origin, or a body |
|                                | adds what is offered, this device's   | past 32 KiB               |
|                                | sends and the nonce; ``boot`` on      |                           |
|                                | ``/api/state``                        |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``panel``, ``webpage``         | "Reply…" on a card waiting for input; | Render the text as markup |
|                                | the composer, a send's line, Undo and |                           |
|                                | Cancel, the off lines; the words      |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``attention``                  | ``received: {kind, hash, at}``,       | Hold the text             |
|                                | written with the turn a prompt starts |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``chainstep``                  | the chain's nudge under the same      | Nudge a tab another       |
|                                | typist lock                           | typist holds              |
+--------------------------------+---------------------------------------+---------------------------+
| ``hosts/claude_code_talk``     | ``quiet`` (Claude Code's input box),  | Import the oracle; read a |
|                                | ``deliver``, ``digest``               | box it does not know as   |
|                                | (``prompt_digest``), ``refuses``      | anything but              |
|                                | (``MODE_PREFIXES``)                   | ``Unreadable``            |
+--------------------------------+---------------------------------------+---------------------------+
| ``hosts/claude_code_launch``   | ``TerminalReader``, the tab's visible | Put text or a tty inside  |
|                                | text; ``run_tab_script``, shared with | a script                  |
|                                | the typer and the closer              |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``hosts/claude_code``          | the prompt signal's ``received``      | Pass the prompt's text on |
|                                | digest                                |                           |
+--------------------------------+---------------------------------------+---------------------------+
| CLI                            | ``_oracle_talker`` (the audit log,    | Decide anything ``talk``  |
|                                | the lock, the alert, the device       | decides                   |
|                                | check); ``_apply_signal`` records the |                           |
|                                | receipt                               |                           |
+--------------------------------+---------------------------------------+---------------------------+

.. mermaid::

   sequenceDiagram
       participant Page as Phone (Conversation)
       participant Web as web
       participant Talk as talk.Talker
       participant Host as SessionTalk (ClaudeCodeTalk, injected)
       participant Hook as invariant hook (UserPromptSubmit)
       Page->>Web: GET /api/session?row=K: turns, offered, sends, nonce (bound to the fingerprint)
       Page->>Web: POST /api/talk {row, nonce, kind: message, text}
       Web->>Talk: post: spend the nonce, refuse what is not a message, hold
       Talk->>Talk: talk-held (no record, no send: 503)
       Web-->>Page: 202, the send: "Sending… · Undo" for 5 s
       Note over Talk: Undo in the window: cancelled, nothing typed
       Talk->>Talk: each second: due, or queued while the turn runs (ring of 8 per window, 64 in all)
       loop until quiet 3 s, refused when busy 60 s
           Talk->>Host: quiet(host): the input box's digest, empty or not
       end
       Talk->>Talk: take locks/type-TTY.lock
       Talk->>Talk: fresh scan: same window, process, terminal; ready (and the same turn, for a send read while ready)
       Talk->>Host: deliver(host, text): typed into the tab found by its tty
       Talk->>Talk: half a second, then release the lock
       Hook->>Hook: attention received {kind, hash, at}
       Talk->>Talk: received, new and within 5 s, with the digest: Delivered; else Not confirmed and one alert
       Page->>Web: GET /api/session again: the send's state

.. mermaid::

   stateDiagram-v2
       [*] --> held: one tap (refused at the tap: not a message, a mode, no tab)
       held --> cancelled: Undo
       held --> queued: the turn still runs, or the window has a send under way
       held --> due: the window ended, Claude waits for input
       queued --> due: the turn ended (one send per turn end)
       queued --> cancelled: Cancel
       queued --> dropped: the ring overflowed, the device was unpaired, or the server restarted
       due --> queued: the fresh scan finds the turn running
       due --> refused: busy past 60 s, the window changed, no tab, shell only, unreadable
       due --> typed: typed into the tab
       typed --> delivered: a new receipt carries the digest
       typed --> not_confirmed: no receipt in 5 s, or another hash (one alert)
       delivered --> [*]
       not_confirmed --> [*]
       refused --> [*]
       cancelled --> [*]
       dropped --> [*]

**The routes.** ``POST /api/talk`` takes ``{row, nonce, kind, text}`` as JSON
or a form, behind the token, from the page's own origin (``Origin`` naming
this server, ``Sec-Fetch-Site`` same-origin where sent), with a body of at
most 32 KiB, which only the talk routes take. It answers 202 with the send,
409 with one reason for a refusal, 403 for a nonce that is unknown, spent,
expired or another device's or row's, and 503 when the ``talk-held`` line
cannot be written. ``POST /api/talk/cancel`` takes ``{id}`` from the device
that sent it. ``GET /api/session`` gains ``offered`` (``send``, ``why``
``gone``, ``unreadable``, ``no_tab`` or ``no_claude``, and ``running``),
``sends`` (this device's to the row: ``id``, ``state``, ``reason``,
``what``, ``at``, ``length``, never the text), ``boot`` and ``nonce``, a new
one on every read while a send is offered (:need:`DEC_ADR_029_D32`).

**What may be sent.** A message of text and line breaks, at most 8000
characters. One opening with ``!``, ``#`` or ``&`` is a command to Claude
Code and is refused (:need:`DEC_ADR_029_D26`); one opening with ``/`` is a
slash command, typed since increment 4 unless it opens a menu (below). A window with no tty, in another terminal, or
whose tab holds only a shell, is not typed into.

**When it is typed.** Only while Claude waits for input
(:need:`DEC_ADR_029_D28`), after its input box has been unchanged and empty
for 3 seconds (:need:`DEC_ADR_029_D29`), and after a fresh scan finds the
same window, process and terminal. A send read while Claude waited must also
find the same waiting identity; one read mid-turn, or queued, waits for the
turn to end. The typist's lock, ``locks/type-<tty>.lock`` in the oracle
home, is held for the fresh scan, the typing and half a second after; the
chain's nudge takes it too.

**The receipt.** The hook path records ``received`` on the sidecar for every
prompt: the SHA-256 of the prompt, normalised (:need:`DEC_ADR_029_D27`). A
new one within 5 seconds carrying the message's digest is "Delivered";
anything else is "Not confirmed", with one alert through the watcher's
notifier (Mac banner and push) naming the project and the session. Nothing
is ever typed twice.

**What is written.** The talk audit lines in ``web/actions.log`` (``event``,
``send``, ``device``, ``device_name``, ``row``, ``host``, ``kind``,
``length``, ``hash`` of the text as typed, and a refusal's ``reason`` and
``what``), never the text; and ``received`` on the sidecar, written by the
hook. The server holds a send's text in memory until it is typed or gone; a
restart drops every waiting send, and the page shows it dropped from the
boot id.

**The seams.** ``_oracle_talk`` now types, so the suite installs
``oracle/talk.RecordingTalk`` for every test; ``_oracle_talker`` builds the
``Talker`` with the audit log, the typist's lock, the notifier and the device
store. The delivery loop is a thread of the web server that starts with the
first tap (:need:`DEC_ADR_029_D33`).

Shipped: stopping a turn and slash commands (ADR-029 increment 4)
-----------------------------------------------------------------

The interrupt (:need:`DEC_ADR_029_D34` to D36): a paired device stops a
running turn from the Conversation's Stop, which the oracle types into the
window's Terminal tab as Esc. Slash commands (:need:`DEC_ADR_029_D37` to D39,
ADR-029 ruling 15): a message opening with ``/`` is typed as a message,
except a command that opens a menu when given no arguments, which is refused
at the tap. The screen and its words are :doc:`oracle-panel.md
</architecture/oracle-panel>`'s "As built for ADR-029 increment 4".

+--------------------------------+---------------------------------------+---------------------------+
| Module                         | Owns (increment 4)                    | Never does                |
+================================+=======================================+===========================+
| ``talk``                       | the ``interrupt`` kind: offered while | Queue an interrupt; type  |
|                                | a turn runs with no prompt open, held | it into an ended turn, an |
|                                | for Undo, then the fresh check, one   | open prompt or text left  |
|                                | look, the key and the transcript's    | on the line; import       |
|                                | receipt (``Fingerprint.asking``); a   | ``hosts/``                |
|                                | message's receipt from the window's   |                           |
|                                | evidence where no hook answered;      |                           |
|                                | ``TalkHost.pid``                      |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``web``                        | the talk nonce whenever send or Stop  | Take a stop with text     |
|                                | is offered; ``kind: interrupt`` on    |                           |
|                                | ``POST /api/talk``                    |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``panel``, ``webpage``         | Stop beside Send, alone when send is  | Touch the draft           |
|                                | off; a stop's card and line; the      |                           |
|                                | words                                 |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``hosts/claude_code_talk``     | ``interrupt`` (the ``escape`` key),   | Import the oracle; read a |
|                                | ``receipt`` (``first_after``: the     | line it cannot read as    |
|                                | person's next entry, Claude Code's    | evidence                  |
|                                | interrupt marker its digest, a        |                           |
|                                | command's echo, the new session after |                           |
|                                | ``/clear``, the process ending for    |                           |
|                                | ``/exit``); ``MENU_COMMANDS``;        |                           |
|                                | ``command_text``, a command as the    |                           |
|                                | person's turn                         |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``hosts/claude_code_launch``   | ``TerminalTyper.key`` and             | Type a key it does not    |
|                                | ``TERMINAL_KEY_SCRIPT``: Esc as       | name; put a key's bytes   |
|                                | ``ESC [ 27 u``, built in the script   | in an argument            |
+--------------------------------+---------------------------------------+---------------------------+

.. mermaid::

   sequenceDiagram
       participant Page as Phone (Conversation, a turn running)
       participant Web as web
       participant Talk as talk.Talker
       participant Host as SessionTalk (ClaudeCodeTalk, injected)
       Page->>Web: GET /api/session: offered.interrupt, nonce (bound to the running turn)
       Page->>Web: POST /api/talk {row, nonce, kind: interrupt}
       Web->>Talk: post: spend the nonce, refuse an ended turn or an open prompt, hold
       Web-->>Page: 202: "Stop", "Sending… · Undo" for 5 s
       Talk->>Talk: due: typed at once, never queued
       Talk->>Talk: take locks/type-TTY.lock; fresh scan: same window, process, terminal, the same turn running, no prompt
       Talk->>Host: quiet(host): one look, the input line must be empty
       Talk->>Host: interrupt(host): the escape key, ESC [27u, into the tab found by its tty
       Talk->>Host: receipt(host, interrupt, since): the person's next entry in the transcript
       Talk->>Talk: Claude Code's interrupt marker: Delivered; a prompt or nothing in 5 s: Not confirmed and one alert

**When it is offered and typed.** While the turn runs in a Terminal tab
where Claude runs, with no question or permission prompt open; it stays when
the session cannot be read. It is typed at the end of the Undo window, never
queued and with no wait for quiet, after the fresh scan finds the same turn
still running and one look finds Claude's input line empty
(:need:`DEC_ADR_029_D35`). The key is Esc as Claude Code reads it, ``ESC [
27 u``, since Terminal ends everything it types with a Return
(:need:`DEC_ADR_029_D34`).

**The receipt.** No hook fires for an interrupt, so the evidence is the
transcript's first entry of the person's after the key: Claude Code's
"[Request interrupted by user]" marker is "Delivered", anything else or
nothing within 5 seconds "Not confirmed", with one alert
(:need:`DEC_ADR_029_D36`). What is written is the audit lines, of the kind
``interrupt`` with length 0; nothing else.

**Slash commands.** A message opening with ``/`` is typed as any message
(:need:`DEC_ADR_029_D37`). One that Claude Code opens a menu for when given
no arguments, on the host layer's list with the version it was seen on, is
refused at the tap, "This command opens a menu — use it on the Mac." (ADR-029
ruling 15). A custom command or skill fires ``UserPromptSubmit`` and is
confirmed by the hook's receipt; a built-in fires none, and while no hook
has answered the oracle asks the host for the window's evidence: the
command's echo, ``/compact``'s plain entry, the echo in the new session the
same process runs after ``/clear``, or the process ending after ``/exit``
(:need:`DEC_ADR_029_D38`). In the conversation a command is the person's
turn, ``/name args``, and its output is not shown (:need:`DEC_ADR_029_D39`).

Shipped: answering a question (ADR-029 increment 5)
---------------------------------------------------

A paired device answers the question a live Claude window waits on
(:need:`DEC_ADR_029_D8`, :need:`DEC_ADR_029_D40` to D46): a choice, several
for a multi-select, "Other" with its text, and several questions in one
send. The Needs you card waiting on a question offers "Answer…"
(:need:`DEC_ADR_029_D10`, :need:`DEC_ADR_029_D45`). Permission prompts are
increment 6's. The screen and its words are :doc:`oracle-panel.md
</architecture/oracle-panel>`'s "As built for ADR-029 increment 5".

+--------------------------------+---------------------------------------+---------------------------+
| Module                         | Owns (increment 5)                    | Never does                |
+================================+=======================================+===========================+
| ``talk``                       | the ``question`` kind:                | Import ``hosts/``; queue  |
|                                | ``read_question``, ``pending_view``,  | an answer; type one into  |
|                                | ``picks`` (each question's choices by | a question that changed;  |
|                                | index and an "Other"), the nonce      | log a label or an "Other" |
|                                | bound to the question read, the       | text                      |
|                                | fresh check and read before the host  |                           |
|                                | types, the receipt; ``offered``'s     |                           |
|                                | ``answer``; ``Look.shows``            |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``web``                        | ``pending`` on ``/api/session`` and   | Read a question for an    |
|                                | in its cursor; ``answers`` on         | unpaired request; put it  |
|                                | ``POST /api/talk``; ``prompts`` on    | in the ``assess``         |
|                                | ``/api/state``, each waiting          | document                  |
|                                | question's read for its card          |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``panel``, ``webpage``         | "Answer…" and the card's note; the    | Render the question as    |
|                                | question block, its toggles, "Other…" | markup                    |
|                                | and "Send answer"; the words          |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``attention``                  | ``received`` of the kind ``question`` | Hold the answers          |
|                                | with the write that clears            |                           |
|                                | ``waiting``                           |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``hosts/claude_code_talk``     | ``pending`` (``read_pending``,        | Import the oracle; type a |
|                                | ``question_call``),                   | step the tab did not      |
|                                | ``question_screen``, ``answer`` (one  | follow from; read a call  |
|                                | step a script, the tab read after     | or a view it does not     |
|                                | each), ``answer_digest``,             | know as anything but      |
|                                | ``answer_receipt``,                   | ``Unreadable``            |
|                                | ``answered_after``                    |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``hosts/claude_code_launch``   | ``TerminalTyper.keys`` and            | Put a move's bytes or the |
|                                | ``TERMINAL_KEYS_SCRIPT``: arrow keys  | text inside a script      |
|                                | (``MOVE_KEYS``) and a text, one       |                           |
|                                | ``do script``                         |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``hosts/claude_code``, CLI     | ``PostToolUse`` of                    | Pass the answers on       |
|                                | ``AskUserQuestion`` carries the       |                           |
|                                | answers' digest; ``_apply_signal``    |                           |
|                                | records it                            |                           |
+--------------------------------+---------------------------------------+---------------------------+

.. mermaid::

   sequenceDiagram
       participant Page as Phone (Conversation, a question waiting)
       participant Web as web
       participant Talk as talk.Talker
       participant Host as SessionTalk (ClaudeCodeTalk, injected)
       participant Hook as invariant hook (PostToolUse)
       Page->>Web: GET /api/session?row=K
       Web->>Host: pending(host): the open AskUserQuestion call in the transcript
       Web-->>Page: turns, pending (the question's words), offered.answer, nonce (bound to the question)
       Page->>Web: POST /api/talk {row, nonce, kind: question, answers: [{choices, other}]}
       Web->>Talk: post: spend the nonce, check the indexes against the read, hold
       Web-->>Page: 202: the "You" card, "Sending… · Undo" for 5 s
       Talk->>Talk: due: never queued
       loop until untouched and unchanged 3 s, refused when busy 60 s
           Talk->>Host: quiet(host): shows question, nothing chosen
       end
       Talk->>Talk: take locks/type-TTY.lock; fresh scan: same window, process, terminal, the same question since
       Talk->>Host: pending(host) again: the same call, the same words
       Talk->>Host: answer(host, question, picks)
       loop each step
           Host->>Host: one do script: arrow keys, "Other" text, Terminal's Return
           Host->>Host: read the tab until it shows the step's effect, else stop
       end
       Hook->>Hook: attention received {kind: question, hash, at}
       Talk->>Talk: the answers' digest, new and within 5 s: Delivered; else Not confirmed and one alert

**When it is offered and typed.** While the attention record says a question
waits, in a Terminal tab where Claude runs, when the session and the question
both read (:need:`DEC_ADR_029_D44`). An answer is held for its Undo window,
never queued, typed after the question on the tab has been untouched and
unchanged for 3 seconds, and only if the fresh scan finds the same question
waiting and a fresh read the same call; otherwise "Claude is no longer
waiting on that question.".

**What may be sent.** One entry per question, its choices by index and an
optional "Other" of one line; a single-select question takes one answer, a
multi-select one at least one. The indexes are checked against the question
the read served with the nonce (:need:`DEC_ADR_029_D42`).

**The keys** (:need:`DEC_ADR_029_D40`). Each step is one Terminal script of
arrow keys and, for "Other", its text, whose own Return picks, ticks, moves
on or submits, as Claude Code 2.1.330 was seen to take them; the tab is read
after every step and typing stops at one it did not follow.

**The receipt** (:need:`DEC_ADR_029_D43`). ``PostToolUse`` of
``AskUserQuestion`` records the hash of the call's id and its answers on the
sidecar, ``received`` of the kind ``question``; while none has come, the
transcript's tool result for the call is the window's evidence. What is
written is the audit lines, with each question's choice indexes (``choice``)
and never a label, and the receipt's hash.

**On the cards.** ``/api/state`` reads each waiting question: its first line
is the card's note and the page offers "Answer…"; one that cannot be read
says so (:need:`DEC_ADR_029_D45`).

Planned: talking to sessions (IDEA_060, ADR-029)
------------------------------------------------

**Planned, not built, after increments 1 to 5.** ADR-029 is accepted
(2026-11-20) and its increments 6 and 7 are queued. This section is the plan's
architecture for what is left: permission answers, the phone rules and
``doctor``'s drift row. Increments 1 to 5, the last message, the
conversation, sending a message, the interrupt and slash commands, and
answering a question, have shipped (above); every "never answers a
permission prompt" above describes the oracle as built.

The action path, ``refusal`` and ``recommend`` stay unchanged
(:need:`DEC_ADR_029_D1`).

+--------------------------------+---------------------------------------+---------------------------+
| Module (inc 6 and 7, planned)  | Owns                                  | Never does                |
+================================+=======================================+===========================+
| ``talk``                       | the ``permission`` kind: ``pending``  | Import ``hosts/``; answer |
|                                | for a prompt, the confirm nonce bound | a permission with no      |
|                                | to the choice, no Undo hold           | confirm sheet             |
+--------------------------------+---------------------------------------+---------------------------+
| ``web``                        | ``/api/talk/confirm``,                | Read anything for an      |
|                                | ``/api/rules``,                       | unpaired request          |
|                                | ``/api/rules/remove``                 |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``panel``, ``webpage``         | the permission block and its confirm  | Render the prompt as      |
|                                | sheet, the Phone rules screen, their  | markup                    |
|                                | words                                 |                           |
+--------------------------------+---------------------------------------+---------------------------+
| ``webactions``                 | the phone-rule removal's confirm      | Record a rule's text in   |
|                                |                                       | the log                   |
+--------------------------------+---------------------------------------+---------------------------+
| ``hosts/claude_code_talk``     | the permission prompt read from the   | Import the oracle; offer  |
|                                | tab and the pending tool call, its    | a choice it cannot name   |
|                                | keys, Claude Code's permission        | exactly                   |
|                                | settings                              |                           |
+--------------------------------+---------------------------------------+---------------------------+
| CLI                            | ``oracle web rules`` and ``rules      | Decide anything ``talk``  |
|                                | remove``; ``revoke``'s rule listing;  | decides                   |
|                                | ``doctor``'s ``oracle talk`` row      |                           |
+--------------------------------+---------------------------------------+---------------------------+

.. mermaid::

   graph TD
       CLI["cli: oracle web, oracle web rules, doctor"]
       subgraph oracle["oracle/"]
           WEB[web]
           PANEL[panel]
           WEBPAGE[webpage]
           WEBACT[webactions]
           TALK["talk (ADR-029; inc 1 to 5 built: conversation, last_message, turns, Talker, interrupt, question answers)"]
           SCAN[scan]
           ATTN[attention]
           HOME[home]
       end
       subgraph hosts["hosts/"]
           HTALK["claude_code_talk (inc 1 to 5 built: conversation, tool lines, quiet, deliver, digest, interrupt, receipt, pending question, answer)"]
           LAUNCH["claude_code_launch: TerminalTyper (keys), TerminalReader"]
           TELEM["claude_code_telemetry: tail reader"]
       end
       CLI --> WEB
       CLI -. "injects SessionTalk (_oracle_talk) from" .-> HTALK
       WEB -- "/api/session, /api/talk, /api/rules" --> TALK
       WEB -- "excerpts and waiting questions from the live read" --> PANEL
       TALK -- "fingerprint: a fresh scan" --> SCAN
       TALK -- "waiting, turn, received" --> ATTN
       TALK -- "locks/type-TTY.lock, web/phone-rules.json" --> HOME
       TALK -- "talk audit lines" --> WEBACT
       HTALK --> LAUNCH
       HTALK --> TELEM

The arrows from ``talk`` to ``hosts/`` do not exist: the CLI hands ``talk``
the ``SessionTalk`` it builds (:need:`DEC_ADR_029_D4`), as it hands the scan
its ``HostProbe`` (ADR-007 D11). The chain's nudge (ADR-027 D28) keeps its
own path through ``ChainHands`` and takes the same per-tab lock.

**A permission answer** (:need:`DEC_ADR_029_D2`, :need:`DEC_ADR_029_D9`)
joins the same path as the inputs built (above): refused unless its prompt
waits now, through the confirm sheet, with no hold.

**What is read, and what is kept** (ADR-009 D17, :need:`DEC_ADR_029_D11`).
Built in increments 1, 2 and 5 (above): the conversation and a waiting
question are read from the host's transcript when the person opens a session
or reloads, and every 2 seconds while it is open and visible. Neither enters
the ``assess`` document, so the watcher's snapshot, ``oracle list`` and the
TUI never carry it, and no oracle file, log, notification or push payload
holds it. A shape the host layer does not know turns send and answer off for
that session, and interrupt stays (built). Increment 6 reads a permission
prompt the same way.

**What is written.** Built: the talk audit lines in ``web/actions.log``
(device, row, host, kind, length, hash, a question's choice indexes; ADR-008
D13's log, :need:`DEC_ADR_029_D5`) and ``received`` on the host's attention
sidecar, written by the hook (ADR-007 D5), both in :doc:`state_model.md
</architecture/state_model>`. Planned: ``web/phone-rules.json``, a record of
every "allow always" given from the phone, with the exact rule the receipt
found in Claude Code's settings (:need:`DEC_ADR_029_D9`), which
``state_model.md`` gains with increment 6.

**Routes.** Every one behind the token, every ``POST`` with an action's
``Origin`` rules; a refusal is a 409 with one plain reason. ``GET
/api/session``, ``POST /api/talk`` and ``POST /api/talk/cancel`` are built
(increments 2 to 5: the conversation, the pending question, what is offered
and why not, this device's sends, the talk nonce, a message, a stop, an
answer and their Undo); the routes below are planned.

+------------------------------------------+------------------------------------------------------------+
| Request                                  | Paired                                                     |
+==========================================+============================================================+
| ``GET /api/talk/confirm?row=…&choice=…`` | a permission answer's sheet and its 120-second nonce       |
+------------------------------------------+------------------------------------------------------------+
| ``GET /api/rules``,                      | the phone-rule list; a removal, through a confirm sheet    |
| ``POST /api/rules/remove``               |                                                            |
+------------------------------------------+------------------------------------------------------------+

**Where it may type.** A live Claude window the fresh scan verifies, with a
pid, a start token and a tty, in a Terminal.app tab where Claude runs. A
shell-only tab, a gone or conflicting host, and a window over Remote Control
or in another terminal are not typed into; the last two can still be read. A
host BUG_040 attributes to the wrong project is still the same window, so
the fingerprint, which names the window and not the project, holds for it.

The increments, their order and what each waits for are ADR-029's
"Increments"; the screens and words are :doc:`oracle-panel.md
</architecture/oracle-panel>`'s "Planned: talking to sessions".

Known gap: a host rooted in another repository (BUG_040)
--------------------------------------------------------

A host session is attributed to the project its hooks resolve, and its
transcript to the project directory it was started in. A host started in one
repository that works on a session of another therefore has its attention
record and transcript in the wrong project: it shows up there as an orphan
``host`` row, and the session it serves shows no host. This is documented, not
fixed; BUG_040 owns it. Close host is never offered on such a row: a host
another project's live session lists is marked ``claimed_elsewhere``, one whose
record ``cwd`` is not in the project is not ``rooted_here``, and ``oracle close-host`` refuses a host any live session lists at the moment it runs.
