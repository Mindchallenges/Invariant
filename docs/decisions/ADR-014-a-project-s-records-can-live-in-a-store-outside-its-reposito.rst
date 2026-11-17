.. _adr-014:

ADR-014 — A project's records can live in a store outside its repository
========================================================================

.. dec:: A project's records can live in a store outside its repository
   :id: DEC_ADR_014
   :status: accepted
   :affects: COMP_CORE_STORES, COMP_CORE_HOME, COMP_CORE_MANIFEST

   - **Status:** Accepted (2026-11-05). Twenty-two rulings were made by the
     user on 2026-11-05 and are recorded below as decided. Each decision gives
     the design that carries out its ruling. Any choice beyond a ruling is
     marked *recommended*. No question is open. Amended at acceptance by the
     user's ruling the same day: the store example's ``publish`` key, which no
     decision defined, is dropped (pushing is ``sync.push_after_landing``, D7),
     and D7's ``pull_at_start`` cites ruling 7. A further amendment, ruled 2026-11-06,
     widens D9's interim refusal to ``guidelines.baseline`` (D9), and reads
     ``store_naming`` from the user level only (D2): a store's own setting has
     nothing to read it, since the store does not exist when its name is chosen.
   - **Amended 2026-11-10 by** :need:`ADR-020 <DEC_ADR_020>`, ruled by the
     user. The store becomes ``init``'s default home (ADR-020 D1), which
     amends ruling 1's "In-repo stays the default" as init's default only;
     the load-time defaults do not move. Ruling 17's user-level wiring is
     replaced: nothing is installed at user level, ``init`` asks per project,
     and a store project is wired locally (ADR-020 D9). D1's ``hooks`` and
     ``agents`` locations, D11 and increment 3 below are amended in place to
     that design, and D12 gains the configuration's move (ADR-020 D3).
   - **Date:** 2026-11-05
   - **Supersedes:** nothing. Amends ``docs/architecture/overview.md``
     ("Where things live at rest"), ``docs/architecture/manifest.md`` ("Resolution
     order", "Declared stores are writable stores"), ``docs/architecture/state_model.md``
     ("Attribution", the session record), ``docs/architecture/oracle.md``
     (``ProjectIndex``), ``docs/architecture/cli.md`` (session merge and teardown) and
     ``docs/guide/installation.md`` ("Several projects, one machine"). It extends
     ADR-011 to more than one git repository per session. The gates a
     documentation repository needs, its adapter, deriving commands from CI, and
     heavy suites are covered by :need:`ADR-015 <DEC_ADR_015>`.

Context
-------

Adopting invariant commits its files to the repository it governs. The
manifest and host assets go under ``.claude/``, and the docs and backlog go
under the configured paths. Four documentation repositories (Sphinx,
sphinx-needs) must keep their content their own, so none of that may land in
them. IDEA_040 records the requirement, the two layouts already tested and
rejected, and the open questions this record settles.

Rulings (user, 2026-11-05)
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. _adr-014-ruling-1:

1. **What moves.** Each record type has its own location setting, so a
   project can move all of its records or only some. The types are the
   manifest, the architecture docs, the backlog, the guidelines, memory,
   reports, sweeps, hooks and agents. In-repo stays the default.

.. _adr-014-ruling-2:

2. **Repo-to-store mapping.** Any mapping is allowed. A store can serve one
   repo or many, and a repo names the store it uses.

.. _adr-014-ruling-3:

3. **Store location and type.** A store can be at any path, and
   ``~/.invariant`` is the default. Git is the only sync mechanism, and a
   remote is optional. There is no pluggable backend.

.. _adr-014-ruling-4:

4. **Config levels.** User defaults in ``~/.invariant`` are overridden by the
   store, and the store is overridden by an optional pointer in the repo.
   Provenance shows where each value came from. The repo level is never
   required.

.. _adr-014-ruling-5:

5. **Repo identity.** The normalised origin URL comes first, so a clone on
   another machine finds the same store. A repo with no remote falls back to
   a generated ID in its local git config, which never touches tracked files.

.. _adr-014-ruling-6:

6. **Cross-repo sessions.** Multi-repo sessions are designed now, so that
   nothing blocks them, and built later. The first build is one repo per
   session plus its store.

.. _adr-014-ruling-7:

7. **Sync.** It is configurable, with safe defaults. Pull at session start is
   on, push after landing is off, an offline pull warns and continues, and a
   diverged store stops the command.

.. _adr-014-ruling-8:

8. **Landing.** The store lands first, then the repo. If the repo merge
   fails, the session records the half-landed state and resuming finishes
   it. There is no rollback.

.. _adr-014-ruling-9:

9. **Team use.** Before landing, invariant pulls the store and replays the
   session's store changes on top. If the same file changed on both sides, it
   stops and shows the conflict. It never resolves a conflict by itself.

.. _adr-014-ruling-10:

10. **Privacy.** Every store commit is scanned before it can be pushed, with
    the store's own allow list and scope. A store with no remote can relax
    this in config.

.. _adr-014-ruling-11:

11. **Migration.** One command moves a project's records from the repo to a
    store, or back, and keeps their history. invariant and Heap stay as they
    are unless that command is run.

.. _adr-014-ruling-12:

12. **Hosts.** The design is host-neutral. The store and the lookup know
    nothing about hosts, and each host adapter decides how to wire itself from
    outside the repo. Claude Code is built first.

.. _adr-014-ruling-13:

13. **Docs isolation (ruled after the first twelve, same day).** A session
    takes an exclusive lock on the store's docs for the whole
    read-design-write cycle. It takes the lock when it starts reading the
    documentation and releases it only after it has finished updating them.
    Other sessions wait for the release, then read, update and unlock in turn.
    This replaces a store worktree per session.

.. _adr-014-ruling-14:

14. **The default store name (same day).** A store's folder is named after
    the repo by default. A naming setting, at user or store level, selects the
    URL form instead: the protocol is dropped and slashes become underscores.
    If the repo-name default collides with an existing store, the second store
    falls back to the URL form automatically, and invariant reports that it
    did. A store is matched by repo identity (D4), never by its folder name.

.. _adr-014-ruling-15:

15. **The scope of privacy (same day).** The server, organisation, username
    and repo name are not private: a developer's identity is well known.
    Privacy is about the user's private content, such as sample data taken
    during development or the contents of user-reported bugs, which must not
    end up in code or records. Store names and URLs therefore need no
    allow-listing and no placeholder. The store's push-time scan (ruling 10)
    stays.

.. _adr-014-ruling-16:

16. **The docs lock is per area (the draft's Q6, same day).** The draft's
    lock design is accepted, with one change: the lock is taken per area,
    not per store.

    - Areas are declared in the store's config as path patterns. A doc
      outside every declared area is locked on its own, per file.
    - A session locks the areas it reads.
    - A session that needs several areas takes them in one fixed global
      order, so that two sessions can never deadlock.
    - Unchanged from the draft: a 30-minute lease with a heartbeat; a
      takeover only with a recorded reason; a visible first-in, first-out
      queue per area, with no spinning; reads refused without the lock; read
      and write bound to one lease; code work in parallel after release; the
      lock machine-local in v1. The store sync's conflict stop (ruling 9) is
      the cross-machine guard until a later version.
    - The rule underneath is ruling 13: reading, designing and writing the
      docs is atomic, so agents always work from current docs.

.. _adr-014-ruling-17:

17. **Host wiring at user level (the draft's Q3 and Q4, same day).
    Replaced 2026-11-10 by ADR-020 ruling 8:** nothing is installed at user
    level, and D11 is amended to a per-project install. The ruling as given:
    ``invariant host install --scope user`` may edit the user's Claude Code settings (hooks
    and ``permissions.additionalDirectories``) and may install guarded
    user-level agents. ``invariant host uninstall --scope user`` removes exactly
    what was added, and nothing else. An in-repo project (invariant, Heap)
    must never fire twice.

.. _adr-014-ruling-18:

18. **The first store clone is explicit (the draft's Q5, same day).** The
    first clone of a store on a machine is always an explicit command, and it
    shows the commands the store declares before anything can run them. Later
    pulls at session start are automatic (ruling 7).

.. _adr-014-ruling-19:

19. **User defaults reach in-repo projects (the draft's Q1, same day).**
    User-level defaults apply to in-repo projects too, below the repo's own
    settings. ``config show`` and ``doctor`` name every value that came from the
    user level.

.. _adr-014-ruling-20:

20. **Memory under a store (the draft's Q2, same day).** Memory under a store
    is exempt from isolation and from the docs lock.

.. _adr-014-ruling-21:

21. **A unit discovered mid-design (raised by ruling 16, same day).** Try
    without waiting, else back off in order: a late unit is granted at once
    if it is free; if it is busy, the session releases only the held units
    that sort after it, queues for the wanted unit and then the released ones,
    and reads them again once regained. The back-off refuses while a unit it
    would release has uncommitted changes. Designed under "Late units and
    pulled commands" below.

.. _adr-014-ruling-22:

22. **A pulled store that changes a command (raised by ruling 18, same
    day).** The pull stays automatic and the session proceeds; session start
    names every changed command, old and new, in its output and context.

What was run to write this
~~~~~~~~~~~~~~~~~~~~~~~~~~

Every claim below is labelled in one of three ways. **Run** means it was
exercised on 2026-11-05 in throwaway repositories in a scratch directory,
with ``INVARIANT_HOME`` pointed at that directory so the real index was never
written. **Read** means it was read in the code at a3171c7. **Reasoned**
means it was neither run nor read.

- **A store spike.** The engine at this commit, unmodified, with two
  functions replaced from outside: ``Manifest.find_root`` and ``Manifest.load``.
  The manifest was read from a separate git repository, and the store-owned
  path keys were rewritten to absolute paths at load. Run in the spike: ``config show``, ``doctor``, session start and isolate, the read-first gate, ``item new``,
  a landing in the docs lane, merge and teardown. Afterwards the repository's
  history held only the project's own commit, its tree held only its own
  files, and no worktree remained.
- **Git probes.** These covered root commits under moves, worktrees, shallow
  clones and merged histories. They checked whether a local config value
  survives a move, is shared by worktrees and is copied by a clone. They
  covered origin URL spellings and pull states (behind, diverged, unreachable).
  They tested a fast-forward over a moved base, store git hooks firing from a
  linked worktree, and a path-filtered history export.
- **Host probes.** Claude Code 2.1.291 in print mode, with hooks from
  ``--settings``, from project settings, from a user config directory and from
  ``--plugin-dir``.

Decisions
---------

.. dec:: Each record type has a location: repo or store
   :id: DEC_ADR_014_D1
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_CORE_RULES, COMP_CMD_ITEMS

   The manifest gains a ``records`` block. A path key keeps its meaning, a path
   relative to a root, and the record's location chooses that root:

   .. code:: yaml

      records:              # every key defaults to repo: today's layout exactly
        docs: store         # paths.docs, read against the store root
        backlog: store      # items.path, items.closed_path
        guidelines: store   # guidelines.ruleset, .baseline, .checklist, agents.overlays
        memory: store       # paths.memory, paths.memory_index
        reports: store      # paths.reports
        sweeps: store       # paths.sweeps
        hooks: local        # repo | local: where the host adapter wires hooks (D11)
        agents: local       # repo | local: where it installs agents (D11)

   ``user`` for ``hooks`` and ``agents`` is retired by ADR-020 D9 (amended
   2026-11-10): ``local`` is the project's own ``.claude/``, uncommitted and
   hidden through ``.git/info/exclude``.

   - **The manifest is not a key.** Its location is answered by the config
     levels (D3): a project "moves its manifest" by having no repo level.
   - **Path keys under a ``store`` record are store-relative, with no ``..`` and
     never absolute.** The store can be shared, so an absolute path would carry a
     home directory and would break on another machine. At load they are joined
     onto the store root (``Manifest.path_for`` already resolves absolute paths;
     run). ``methodology.extra_machines`` and ``methodology.gates.coverage_baseline``
     follow ``guidelines``, because they are policy files too.
   - **Everything else stays the repo's.** That covers ``paths.root``,
     ``paths.state``, ``session.*`` and the commands. Commands run in the session's
     repo worktree.
   - **The state root does not move.** It stays the repo's git common directory
     (``Manifest.state_root``, unchanged). Runtime state stays machine-local and
     per repo, and the oracle's index key is unchanged (D10).

   Run in the spike: moving ``docs`` and ``backlog`` with only this rewrite was
   enough for the session flow listed above. ``item new`` wrote into the store and
   left the repository clean.

.. dec:: Stores, and how a repo finds one
   :id: DEC_ADR_014_D2
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_MANIFEST_FILE

   **A store is a git repository at any path** (ruling 3). Its root holds
   ``invariant.yaml``, the store level of the config (D3), and the records placed
   there. Its manifest declares the repos it serves:

   .. code:: yaml

      # <store>/invariant.yaml
      version: 1
      store:
        repos:                           # one or many (ruling 2)
          github.com/mindchallenges/invariant: {}   # an identity (D4); per-repo overrides inside
          local:inv-3fa91c0e: {name: scratch-notes}

   The default parent is ``~/.invariant/stores/``, which ``$INVARIANT_HOME`` moves
   (ADR-007). The store path can be any path (ruling 3). The folder name follows
   ruling 14:

   .. code:: yaml

      store_naming: repo        # repo (default) | url; user level (D3; ruled 2026-11-06)

   - ``repo``: the repo name, the last segment of the normalised origin (D4), or
     the directory name for a repo with no remote. For this repository that is
     ``invariant``.
   - ``url``: the normalised origin with slashes turned into underscores, such as
     ``github.com_mindchallenges_invariant``.
   - **On a collision** under ``repo``, when a store of that name already exists
     for a different identity, the new store takes its ``url`` name instead.
     ``store init`` and ``store link`` print that they did, naming both stores. A
     repo with no remote has no URL form, so its collision falls back to its
     local ID (``local:<slug>-<8 hex>``, with ``:`` written as ``_``).
   - **The folder name is never the key.** The lookup goes from identity to path
     through the user level and the store's ``store.repos`` list (below), so
     renaming or moving a store folder means updating the path, nothing else.
     The ``<name>`` in a provenance label is the folder name, shown for reading.

   **A repo names its store** (ruling 2), and the first level that answers wins:

   1. The repo pointer, ``.invariant.yaml`` at the repo root (D3), with
      ``store: <path or remote>``. It is optional.
   2. The user level, ``~/.invariant/config.yaml``, keyed on the repo's identity:
      ``repos: {<identity>: {store: ~/.invariant/stores/specs}}``.
      ``invariant store link`` writes this entry.
   3. Neither: there is no store, and every record is in-repo.

   A store a repo names must list that repo's identity under ``store.repos``.
   Otherwise the lookup refuses with both names, so a wrong link cannot make one
   project's gates read another project's docs.

   **The walk.** ``Manifest.find_root`` and ``Manifest.load`` become
   ``Manifest.locate(start)``. It walks up from ``start``, and at each level it asks
   two questions:

   1. Is there a ``.claude/invariant.yaml`` here? If so, this is an in-repo
      project, exactly as today. This question comes first, so invariant and
      Heap resolve byte for byte as before.
   2. Is this level a git top level whose identity has a store (above), or which
      carries a repo pointer? If so, the project root is here.

   The nearest answer wins. A repo nested inside an in-repo project finds its own
   store first, before the parent's manifest. That is the failure IDEA_040
   recorded for the shared-parent layout.

.. dec:: Three config levels, each with its provenance
   :id: DEC_ADR_014_D3
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_MANIFEST_FILE

   The ruled order, later wins (ruling 4), replaces layer 5 of "Resolution order":

   +-------+------------------------------+-----------------------------------+
   | Layer | Source                       | Provenance label                  |
   +=======+==============================+===================================+
   | 1–4   | library defaults, adapters,  | ``default``, ``adapter:<eco>``,   |
   |       | profile (unchanged)          | ``profile:<name>``                |
   +-------+------------------------------+-----------------------------------+
   | 5a    | user defaults:               | ``user:~/.invariant/config.yaml`` |
   |       | ``~/.invariant/config.yaml`` |                                   |
   |       | ``defaults:``                |                                   |
   +-------+------------------------------+-----------------------------------+
   | 5b    | the store:                   | ``store:<name>``,                 |
   |       | ``<store>/invariant.yaml``,  | ``store:<name>#<identity>``       |
   |       | then its                     |                                   |
   |       | ``store.repos.<identity>``   |                                   |
   |       | block                        |                                   |
   +-------+------------------------------+-----------------------------------+
   | 5c    | the repo:                    | ``repo:<file>``                   |
   |       | ``.claude/invariant.yaml``   |                                   |
   |       | (in-repo) or                 |                                   |
   |       | ``.invariant.yaml`` (the     |                                   |
   |       | pointer)                     |                                   |
   +-------+------------------------------+-----------------------------------+
   | 6     | ``INVARIANT_*`` environment  | ``env``                           |
   |       | (unchanged)                  |                                   |
   +-------+------------------------------+-----------------------------------+

   - **The repo level is never required.** A store project with no pointer
     resolves from 5a and 5b alone. The user level may hold no project-specific
     keys except ``repos:`` (D2), so a project's policy can never be set by one
     operator's home directory alone.
   - **An in-repo project gets 5a too** (ruling 19). It sits below the repo's
     own settings, so a key the repo sets always wins. That is the one change an
     in-repo project can see, and only if the user writes defaults. ``config show``
     labels every value from 5a with ``user:~/.invariant/config.yaml``, and
     ``doctor`` carries one row listing every such value with its key, so an
     operator's home directory never changes a resolved value unannounced. With
     no user defaults written, nothing changes and the row says so.
   - **The pointer is minimal by design.** Its keys are ``store:`` and the
     ``records`` block. A pointer that sets policy keys is refused, so the policy of
     a store project lives in its store and is reviewed there.
   - ``invariant config show`` prints the label for every value. ``--provenance``
     already exists, and the labels gain the paths.

.. dec:: Repo identity: the normalised origin URL, else a local config ID
   :id: DEC_ADR_014_D4
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_CORE_STORES, COMP_CORE_HOME, COMP_CORE_MANIFEST

   As ruled (5):

   - **Origin first.** ``remote.origin.url`` is normalised. The scheme, the
     userinfo, the port, a trailing ``.git`` and a trailing ``/`` are dropped, and
     the host is lowercased. Run: four spellings of this repository's origin
     (the HTTPS form with ``.git``, the scp-like SSH form, the ``ssh://`` form with a
     port and a trailing slash, and an HTTPS form with userinfo and a capitalised
     host) all reduced to ``github.com/mindchallenges/invariant`` once the path was
     lowercased. They are described rather than quoted because the ``links`` and
     ``emails`` patterns flag them; under ruling 15 that is an engine question,
     recorded in the session report, not a privacy concern.
     *Recommended:* lowercase the path too for the hosts known to treat paths
     case-insensitively (GitHub, GitLab, Bitbucket), and keep the case
     elsewhere.
   - **No remote: ``invariant.id`` in the local git config.** ``store link`` writes
     ``local:<slug>-<8 hex>``. Run: one value in ``.git/config`` is seen by every
     linked worktree, survives ``mv``, is not copied by ``git clone``, and leaves
     ``git status`` empty.
   - **The ID is written even when there is a remote** (*recommended*). Lookup
     stays origin-first, as ruled. If the origin key misses but the ID matches a
     store entry, a remote was added or renamed. ``doctor`` says so and offers
     ``store link --update``. It never re-keys by itself.
   - **Consequences of the ruling, stated.** Two clones of one repo on one
     machine share a store, and that is the intent. A fork has a different origin,
     so it gets its own store unless it is linked to one. The root commit was
     rejected as a key (run: a ``--depth 1`` clone reports a different root, and a
     merge of unrelated histories has two).
   - **Cost.** One read of ``.git/config``, found through the worktree's ``commondir``
     with no subprocess (D11), and one lookup in the user config.

.. dec:: The store's docs are locked per area for the whole read-design-write cycle
   :id: DEC_ADR_014_D5
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_CORE_STORE

   **Ruled (13 and 16, 2026-11-05).** A session takes an exclusive lock on the
   docs it reads when it starts reading them. It releases the lock only after it
   has finished updating them. Other sessions that need the same docs wait for
   the release, then read, update and unlock in turn. The user's reason: agents
   read the docs, design, write the docs and only then proceed, and that cycle
   must be atomic, so that no agent ever designs against docs that another
   session is about to change ("read rumors"). Ruling 16 makes the lock per
   area, so two sessions working on unrelated parts of one store's docs do not
   wait for each other. This replaces the per-session store worktree of the
   earlier draft. There is no store worktree and no store branch. A session reads
   and writes the docs in the store's main checkout while it holds their lock.

   **Sessions are still a list of legs** (ruling 6), so multi-repo sessions are
   not blocked. The store leg has no worktree. It records the spans during which
   the session held each unit of the lock:

   .. code:: yaml

      legs:
        - {kind: store, root: <store main checkout>,
           lock_spans: [{lease: <id>, unit: area:architecture,
                         acquired_at_sha: ..., released_at_sha: ...}]}
        - {kind: repo, identity: <identity>, root: ..., worktree: ..., branch: session/<id>, base: main}
      landing: {store: null, repo: null}   # each set to a landed sha (D6)

   - The session lives in the registry of its first repo leg, under that repo's
     state root, as today. Later repo legs refer to their own repos and are
     never written, which keeps one owner per session. The existing fields
     ``worktree``, ``branch`` and ``base`` read the first repo leg, so records written
     before this change still load. v1 refuses a session with more than one repo
     leg.

   **What the lock covers.** The store's policy records: the architecture docs,
   and also the ruleset, the baseline, the overlays and the lane files, because
   each is something an agent designs against.

   - **Memory is outside the lock** (ruling 20), and outside isolation too
     (D10). It is written and committed in the store's main checkout at any time.
   - **The other data records are outside it as well** (*recommended*). The
     backlog, reports and sweeps are written and committed in the store's main
     checkout at any time, as an in-repo backlog is today (BUG_008). Run: ``item new`` from inside the session worktree wrote the store and left the repo
     clean, which avoids BUG_032's shape by construction.

   **Areas** (ruling 16). The store's config declares them as path patterns,
   relative to the store root:

   .. code:: yaml

      # <store>/invariant.yaml
      store:
        docs_lock:
          areas:                     # name -> path patterns; default: none declared
            architecture: [docs/architecture/**]
            guidelines: [docs/guidelines/**, methodology/*.yaml]
          lease_minutes: 30          # ruled default (16)

   - **The unit of the lock** is a declared area (``area:<name>``), or, for a
     policy file outside every declared area, that one file (``file:<path>``).
     With no areas declared, which is the default, every policy file is its own
     unit. That keeps the ruled atomicity for every file a session reads, and
     lets sessions that read different files run side by side.
   - **An area covers files that do not exist yet.** A file created inside an
     area is under that area's lock. A new file outside every area is its own
     unit, taken when it is first written. A project whose sessions add docs
     often declares areas for that reason: a per-file lock cannot stop another
     session adding a page next to the ones a design read.
   - **Areas may not overlap** (*recommended*). A path matched by two areas'
     patterns is refused when the store's config loads, naming both areas, so
     every path has exactly one unit.

   **The global order** (ruling 16). Every unit has a key, ``area:<name>`` or
   ``file:<store-relative path>``, and keys are ordered as plain strings. A session
   may wait for a unit only if its key sorts after every unit it already holds.
   Two sessions can then never each hold what the other waits for, so the queues
   can never deadlock. v1 has one store per session. When sessions span several
   stores (ruling 6, later), the key gains the store's name as its first part, so
   the order stays total.

   **Taking units** (*recommended*):

   - **Up front, in order.** The design phase begins by naming the units it
     expects to read, which an agent already decides when it maps its task to
     docs. ``invariant docs lock <area or path>...`` sorts them and takes them in
     order. Free units are granted at once. At the first busy one the session is
     queued, and the rest follow when that one is granted.
   - **On read.** A session locks the units it reads (ruling 16). The
     pre-tool-use rule below allows a read of a unit the session holds. A read of
     a free unit with an empty queue, whose key sorts after every unit the
     session holds, is granted in the same hook call: a lease write of a few
     milliseconds, with no waiting. Any other read is refused, naming the unit,
     its holder, the session's position if it is queued, and the command to run.
   - **A unit discovered mid-design** that sorts before one the session holds
     is ruling 21, designed under "Late units and pulled commands" below.

   **The rest of the draft, as ruled (16):**

   - **A lease with a heartbeat.** A session has one lease, which holds all its
     units. The lease record names the holder session, its repo identity and its
     host. The holding session's hook calls refresh the heartbeat, at most once
     a minute, and so do its CLI commands. Its units are freed automatically when
     the holder lands, tears down or is abandoned. The lease becomes *stale* when
     the heartbeat is older than ``store.docs_lock.lease_minutes`` (30 by default),
     or when the holder's host process, read from the attention sidecar's ``pid``,
     is dead. A stale lease is never freed silently. ``invariant docs lock --take-over <unit> --reason '<why>'`` takes one unit over and records the
     reason in both sessions' overrides, as ``invariant override`` does.
   - **Waiting is visible and does not spin.** Each unit keeps its own
     first-in, first-out queue. ``invariant docs lock`` either grants or queues and
     returns at once, naming each position and holder. It never loops. On
     release, ownership passes to the head of the unit's queue inside the same
     locked write, so no race decides who is next. The new holder is told
     through the oracle's alert, ``session status`` and its next SessionStart or
     turn context. ``--wait`` blocks on an OS file lock, with no polling, up to a
     timeout. ``session status``, ``session list`` and the oracle's list, panel and
     page show, per unit, the holder, how long it has held it, and the queue.
   - **Reads and writes require the lock.** A new rule, ``store.docs_lock_held``
     (pre-tool-use, block), denies a Read, Edit or Write of a store policy path
     whose unit the session does not hold, after the on-read grant above has had
     its chance. Reads are included, because a read without the lock is exactly
     the stale design the ruling forbids. A call that resolves to no session is
     ungoverned, as now.
   - **One lease, read and write together.** Each ``docs_read`` evidence row and
     each store doc commit carries the lease id and the unit.
     ``docs.read_architecture`` accepts only reads made under a lease the session
     holds or held. ``docs.updated`` accepts a store doc change in a unit only if
     it was committed under a span of that unit which also holds a recorded read
     in the same unit, or the file did not exist when the unit was taken. So a
     gate can check that the read happened under the same lock as the write. A
     session that must change a unit again after releasing it takes it again,
     and reads again under the new span before it writes.
   - **Code runs in parallel.** The lock covers the docs only. Once the docs
     are written and the units released, the session's code work in its own repo
     worktree runs alongside other sessions' doc cycles.
   - **Releasing.** ``invariant docs unlock [<unit>...]`` releases the named
     units, or all of them, in any order: releasing never waits, so it cannot
     deadlock. It refuses a unit whose paths carry uncommitted changes. The
     session commits its doc update on the store's main line first, which is what
     makes the next holder read the latest docs. Releasing with no doc change is
     allowed, and the span records that nothing was written. In a lane with a
     design state, an exit gate on that state, ``store.docs_lock_released``, holds
     the move to implementation until every unit is released. The
     feature-architect's doc update therefore moves from after the code (its
     Phase 7) to the end of design.
   - **Machine-local in v1.** The store sync's conflict stop (ruling 9, D6) is
     the cross-machine guard until a later version.

   **What ``core/store`` offers, and what it lacks.** ``JsonStore.locked`` (``fcntl.flock``
   on a lock file, then an atomic temp-file write) fits the lease record's
   read-modify-write exactly, and the record lives in the store's own git
   directory (``<store>/.git/invariant/locks/docs.json``, untracked). It is in the
   store, not in a repo's state root, because sessions of several repos share it.
   The primitive itself is not the docs lock:

   - It is held for one ``with`` block in one process. A session's lock lasts
     across many short CLI and hook processes, so it has to be a record, not a
     held ``flock``.
   - It waits by polling every 50 ms with a non-blocking ``flock``, for up to 10 s
     (``LOCK_POLL_SECONDS``, ``LOCK_TIMEOUT_SECONDS``). That suits a critical section
     of a few milliseconds and is wrong for a wait of minutes. The new
     ``--wait`` blocks instead.
   - It is not re-entrant, and it has no owner, lease, heartbeat, unit, queue
     or takeover. All of these are added in the lease record, which holds every
     unit's holder and queue in one file, not in ``JsonStore``.
   - It is machine-local. Two operators on two machines who share a store
     through a remote do not exclude each other.

   **Costs, stated honestly:**

   - **Long design phases serialise sessions on the same docs**, across every
     repo the store serves. Per-area units (ruling 16) narrow this to sessions
     that read the same area or file, but a session that holds a unit through a
     long interview holds up everyone queued for that unit. A broad area, or a
     doc every design reads (an overview), still makes a queue.
   - **A back-off costs a re-read.** A session that has to give units back to
     respect the order (ruling 21) reads them again after it gets them back.
   - **The docs land before the code is reviewed.** The doc update is committed
     on the store's main line at release, before the repo change has passed its
     own review. That relaxes ADR-012 D10 ("policy is reviewed on a branch") for
     store policy. The review gates still see the change, because the docs leg's
     changed set is the commits inside the session's lock spans, but they see it
     after it is already on the base.
   - **An abandoned session leaves its docs behind.** The docs then describe
     code that never landed. With no rollback (ruling 8), the abandon names the
     spans' commits and the operator decides.
   - **Across machines, the lock does not exclude.** ``docs lock`` fetches the
     store first (D7), so the read is the latest as of that moment. A unit
     granted on read inside a hook is not fetched first, because a hook never
     waits on the network (D7); its freshness is that of the session start's
     pull. A colleague's
     concurrent edit on another machine is caught at the next push or pull by
     ruling 9's same-file check, not prevented. A lease kept on the remote (a ref
     pushed with ``--force-with-lease``) could extend the lock across machines
     later. It is not designed here.

   **Attribution (ADR-011, extended).** No store worktree means that a store doc
   path names no session by itself. A read or edit of one is attributed by the
   ordinary order (host binding, then the working directory) and is then
   checked against the holder of the path's unit. Run: a read of the store's main copy was
   accepted from a host rooted in the session worktree. With two sessions live
   and a host in the repo's main checkout, the read was refused and the refusal
   announced ("resolved by 'ambiguous'"). That is BUG_028's rule, and holding the
   lock does not turn a guess into a proof. ``docs_roots`` includes the store's
   main checkout. ``_foreign_project`` reaches a store path's repo through the
   store's ``store.repos`` and the user config. A host rooted in a store with no
   project is ungoverned, and ``doctor`` says so. D8's git hooks still screen its
   commits.

.. dec:: Landing: the store's docs land at release, the repo at merge; a half-landed state is normal and resumable
   :id: DEC_ADR_014_D6
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_CORE_STORES, COMP_CORE_HOME, COMP_CORE_MANIFEST

   Ruling 8 (store first, then the repo) now holds by construction. The
   session's doc update reaches the store's main line when its units are released
   (D5), which is always before the repo's merge.

   1. **At release (the store leg).** If the store has a remote and pull is on
      (D7), it fetches. If the remote moved since the lock was taken, the files
      changed on each side since the span's start are compared. If any file
      changed on both sides, the release stops, shows each such file with both
      diffs, and keeps the units (ruling 9). *This is stricter than git*, which
      would merge separate hunks of one file. The ruling says the same file, and
      that is what is checked. Otherwise the session's span commits are replayed
      on top. ``landing.store`` is set to the released sha.
   2. **At ``session merge``.** The gates run over every leg's changed set (D9
      and ADR-015), then the repo leg runs exactly as today: ``switch``, ``merge --ff-only`` and the containment check. ``landing.repo`` is set to the merged
      sha. Run: once an item commit lands on the store's main line, a
      fast-forward of an older store line is refused and a rebase succeeds. That
      is why the release replays instead of fast-forwarding.
   3. The session goes to ``merged`` once every leg has landed.

   +----------------------+----------------------+----------------------+
   | Outcome              | Recorded             | What happens next    |
   +======================+======================+======================+
   | Release stops on a   | nothing landed; the  | The conflict is      |
   | shared file          | units are still held | shown. The operator  |
   |                      |                      | resolves it in the   |
   |                      |                      | store's main         |
   |                      |                      | checkout and         |
   |                      |                      | releases again.      |
   +----------------------+----------------------+----------------------+
   | Docs released, repo  | ``landing.store``    | This is the normal   |
   | not yet merged       | set                  | state during         |
   |                      |                      | implementation.      |
   |                      |                      | ``session status``   |
   |                      |                      | shows **docs landed, |
   |                      |                      | code pending**.      |
   +----------------------+----------------------+----------------------+
   | Repo merge fails     | ``landing.repo``     | The session is       |
   |                      | null                 | **half-landed**,     |
   |                      |                      | naming both.         |
   |                      |                      | ``session merge`` or |
   |                      |                      | ``session resume``   |
   |                      |                      | finishes it. Git     |
   |                      |                      | containment is       |
   |                      |                      | checked against the  |
   |                      |                      | record, and a        |
   |                      |                      | mismatch stops with  |
   |                      |                      | both values. No      |
   |                      |                      | rollback (ruling 8). |
   +----------------------+----------------------+----------------------+
   | Both landed          | ``merged``           | As now. Then the     |
   |                      |                      | store push, if       |
   |                      |                      | configured (D7).     |
   +----------------------+----------------------+----------------------+

   A half-landed session is held out of ``abandoned`` unless forced. A forced
   abandon names the store commits that stay landed. Teardown removes the repo
   worktree and branch as today and releases every unit the session still holds.

   *Evidence:* reasoned, except the repo leg (run end to end) and the
   fast-forward behaviour (run).

.. dec:: Sync is configurable, with the ruled defaults
   :id: DEC_ADR_014_D7
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_CORE_RULES, COMP_CMD_SETUP, COMP_CMD_SESSION

   .. code:: yaml

      sync:                    # store level, overridable per user (D3 5a)
        pull_at_start: true    # session start, resume and `docs lock` fetch first (ruling 7)
        push_after_landing: false
        offline: warn          # warn | stop: what a failed or timed-out fetch does
        diverged: stop         # stop: the only value in v1 (ruling 9: never auto-resolve)
        timeout_seconds: 20

   - **The fetch** runs with ``GIT_TERMINAL_PROMPT=0`` and the time limit, so a
     credential prompt or a hung remote counts as offline, not as a hang
     (reasoned). It never runs in a hook, because a hook has 15 seconds and must
     never wait on the network.
   - **The states**, run with plain git. *Behind* fast-forwards. *Ahead* carries
     on. *Offline* warns on the command's output, and the session records
     ``store_pull: stale`` so ``session status`` shows it. *Diverged* stops: no
     session is registered and no branch moves (``pull --ff-only`` refused, local
     history unchanged).
   - **A consequence to watch.** With push off by default, a store shared by
     several people will be diverged whenever anyone else pushed. Stopping is
     the ruled answer. The way out is ``invariant store pull --replay``, run
     explicitly. It replays local unpublished commits onto the remote with the
     same same-file check as D6, and stops on overlap.
   - **Push after landing**, when on, pushes the store's base branch after D8's
     scan. It is not part of the landing. If the push fails, the session is still
     ``merged`` and the output names the failed push, so ``store push`` can retry it.
     ``session.push`` stays the repo's own switch.
   - **The first clone is explicit** (ruling 18). On a machine with no copy of
     a store, nothing clones it implicitly: not session start, not a pointer,
     not the user config. ``invariant store clone <remote>`` clones it into a
     staging directory and prints every command the store declares (each
     ``commands.*`` key, per ecosystem, with its provenance label, and the lanes
     named in ``methodology.extra_machines``) together with the repos it serves.
     Only after the operator confirms does it move the store into place and
     register those repos in the user config. Declined, the staging copy is
     removed and nothing is registered. A store's manifest names commands that
     the gates run, which is why its first arrival is shown before it is
     trusted. A repo whose pointer names a store not yet on the machine stops at
     session start with the ``store clone`` command to run.
   - **Later pulls are automatic** (rulings 7 and 18). Once registered, the
     store is pulled at session start, resume and ``docs lock`` as ``sync`` says,
     and the origin identity (D4) finds it from any clone of its repos. A pull
     that changes a declared command is ruling 22, designed under "Late units
     and pulled commands" below.

.. dec:: Privacy: every store commit is scanned before it can leave
   :id: DEC_ADR_014_D8
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_CORE_STORES, COMP_CORE_HOME, COMP_CORE_MANIFEST

   Run: today none of the three privacy routes reaches a store. An edit payload
   carrying a home path, written into a store doc, passed. A commit made with
   ``git -C <store>`` was treated as foreign, so its staged content was not
   screened. At the review boundary the gate reported ``[skip:BLOCK] no path ... inside privacy scope``.

   1. **The ruled guarantee lives at the push.** ``store push``, push after landing
      and the store's ``pre-push`` git hook each scan every outgoing commit: its
      content and its message. They use the **store's** ``privacy`` block, its
      ``allow`` and ``scope``, read at that commit. A finding refuses the push. The
      first push after linking a remote scans the whole unpublished history.
   2. **Earlier screens.** ``pre-commit`` and ``commit-msg`` hooks in the store run
      the same scan at commit time. Run: hooks in the store's common directory
      fire from a linked worktree. The store's local config sets
      ``core.hooksPath``, so a global hooks path cannot skip them (reasoned). The
      engine's edit and review routes spell store paths against the store, so
      the existing gate covers a store edit in the turn it happens. ``--no-verify``
      skips the commit hooks. The push scan in item 1 is the one that cannot be
      skipped through invariant's own commands.
   3. **Relaxation (ruling 10).** ``privacy.store.scan: false`` is honoured only
      while the store has no remote, and ``doctor`` names it as an active
      exception. When a remote is added, the setting is ignored, the next push
      scans the whole history, and ``doctor`` turns the row into a failure until
      the key is removed.
   4. **Default scope in a store is ``all``** (*recommended*): the store is
      invariant's own record, and it is the part that can be shared. The repo's
      content is scanned only as far as the scope entries in its own levels name.

.. dec:: Gates read the tree each record lives in
   :id: DEC_ADR_014_D9
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_MANIFEST_FILE

   Run in the spike, before any change:

   - ``docs.updated`` blocked a ``docs/conf.py`` change ("source changed but no
     architecture doc did") after the store's docs were updated and committed.
   - ``guidelines.ruleset_valid`` refused a store ruleset as "outside every
     checkout".
   - With only the manifest moved and the baseline left in the repo, the shrink
     gate found no manifest at the merge base. It concluded that "the base branch
     has no baseline configured, so every entry is admitted". A hand-added entry
     passed. Moving the lookup alone therefore makes that gate silently inert.

   The decision:

   - ``_changed_set(ctx, leg)`` takes the leg. For the store leg it is the commits
     inside the session's lock spans (D5): from each span's start sha to its
     release sha, plus any uncommitted change in a unit while it is held.
     ``docs.updated`` takes "source changed" from the repo legs and "docs changed"
     from the store leg. It accepts a change in a unit only if it was committed
     under a span of that unit which also holds a recorded read in it (D5). It blocks until the store carries the
     update, and clears once it does. ``docs.diagrams_current`` reads the doc leg. Coverage,
     tests and the touched-file guideline selection read the repo legs. The
     privacy review boundary reads all legs.
   - The manifest's own location in its tree is a property of the loaded
     manifest, not ``MANIFEST_RELPATH``. The base readers (the shrink gate) read it
     at the sha where the session's first lock span began. In a store project, no manifest at
     the base is a failure, never "no baseline configured".
   - Until this decision is built, the guidelines records are refused under
     ``store``, so the inert gate above cannot ship (increment order below).
   - **Ruled 2026-11-06, found while building increment 2.** Refusing the
     guidelines records under ``store`` is not enough on its own: a project whose
     manifest is not in its repository (a store project, or one defined by a
     pointer alone) with ``records.guidelines: repo`` and ``guidelines.baseline``
     set leaves the shrink gate's base reader with no ``.claude/invariant.yaml``
     at the merge base, so it admits every entry. Such a project is therefore
     refused at load if it sets ``guidelines.baseline``, naming increment 4,
     which lifts this refusal with the guidelines one.

.. dec:: Declared stores, exemptions and the oracle
   :id: DEC_ADR_014_D10
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_ORACLE_GATES, COMP_ORACLE_SCAN, COMP_HOSTS_CLAUDE_CODE, COMP_CLI_CONFORMANCE, COMP_CLI_MAIN

   - "Declared stores are writable stores" holds. The write scope is still "the
     state root and the stores the manifest declares", and declared stores resolve
     where their record lives. Under ``store``, the data records (``backlog``,
     ``memory``, ``reports``, ``sweeps``) are written in the store's main checkout and
     are exempt from isolation. For ``memory`` that is ruled (20): it is data, a
     store has no ``.claude/*`` pattern to rely on, and it is outside the docs
     lock as well (D5). BUG_072's in-repo ruling is not reopened, because it
     governs memory in a repo, not in a store. Policy records are never exempt
     (ADR-012 D10).
   - ``items.exempt: false`` with ``records.backlog: store`` is refused in v1, with
     the reason, rather than silently routed.
   - **The oracle.** The index key, ``Manifest.state_root``, is unchanged. Five
     places test ``root / MANIFEST_RELPATH`` to decide whether a project still
     exists: ``ProjectEntry.has_manifest``, ``_canonical_root``, ``oracle/scan`` (twice)
     and ``oracle/gates``. Run: ``oracle projects prune --dry-run`` would drop a store
     project, and ``oracle list`` showed it as an ALERT. All five ask
     ``Manifest.locate`` instead. The other ``MANIFEST_RELPATH`` readers (``cli/main``
     init, doctor and uninstall, ``cli/conformance``, ``hosts/claude_code`` uninstall)
     follow the loaded manifest's location (read).

.. dec:: Hosts: a neutral seam; Claude Code wires each project locally, only when asked
   :id: DEC_ADR_014_D11
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_CORE_MANIFEST, COMP_MANIFEST_FILE, COMP_HOSTS_CLAUDE_CODE

   **Amended 2026-11-10 by** :need:`DEC_ADR_020_D9` **(ADR-020 ruling 8).**
   The user-level design this decision first held (ruling 17: a user-level
   shim in the user's Claude Code settings, guarded user-level agents, a
   linked-identity index) is replaced. Nothing is installed at user level.
   ``init`` asks per project whether to install hooks and agents, default
   no, and a store project is wired inside its own directory. The design
   below is the amended one.

   ``core/manifest`` and the store know nothing about hosts (ruling 12). The host
   adapter interface gains ``wiring_plan(location)``, with location ``repo`` or
   ``local``, read from ``records.hooks`` and ``records.agents``. Each adapter
   decides what each location means for its host. Only ``hosts/`` writes host
   files, and it reaches the engine only through the CLI, the rule BUG_010
   recorded.

   **Claude Code, first:**

   - **``repo``** is today's committed wiring: ``.claude/settings.json``,
     ``.claude/hooks/`` and ``.claude/agents/``. In-repo projects keep it.
   - **``local``** wires the same project directory with nothing committed:
     hook entries in ``.claude/settings.local.json`` (Claude Code's personal,
     per-project settings file), shims in ``.claude/hooks/``, agents in
     ``.claude/agents/``, each path added to ``.git/info/exclude``. That file
     is in the git common directory: every worktree of the clone shares it,
     a clone never copies it, and it is not tracked. The shared
     ``.gitignore`` is never edited. The store's main checkout is added to
     ``permissions.additionalDirectories`` in ``settings.local.json``.
   - **One firing.** A project is wired in one place, its own ``.claude/``,
     so no check against double firing is needed.
   - **Worktrees.** ``session isolate`` renders the local wiring into a new
     session worktree when the main checkout has it installed, through the
     same install plan; the exclude lines already cover it.
   - **Instructions.** Under ``records.agents: local`` the managed
     guidelines block is not written into a tracked ``CLAUDE.md``. The
     ``always`` rules reach the agent through the hook context.
   - **Install and uninstall are exact inverses.** The install writes a
     receipt, ``<state root>/host/claude_code/installed.json``, listing each
     settings entry it added verbatim, each exclude line it added and each
     agent file with its hash. Uninstall removes exactly the receipt's
     entries and lines, leaving every other setting and exclude line as the
     user has it. An agent file whose hash no longer matches was edited by
     the user, so it is left in place and named. A second install adds
     nothing already in the receipt. ``doctor`` reports a receipt entry
     missing from the files, and a ``local`` path missing from
     ``.git/info/exclude``.
   - **The linked-identity index** (``~/.invariant/linked-repos.txt``)
     existed for the user-level shim and has no reader under this design;
     increment 3 removes it.

   Run on 2026-11-05 for the replaced design and still true: Claude Code
   deduplicates identical hook commands only, and ``CLAUDE_PROJECT_DIR`` names
   the worktree inside one. The local design has not been run.

.. dec:: Migration: one command each way, history kept
   :id: DEC_ADR_014_D12
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_MANIFEST_FILE

   ``invariant store migrate --to store [--records docs,backlog,...]`` and
   ``--to repo`` (ruling 11). It runs as its own session lane. Both legs are
   reviewed and landed through D6, so the result is gated like any change.

   - **To a store.** The selected paths' history is exported with ``git fast-export <base> -- <paths>`` into the store, keeping the same relative
     paths. Run: only the commits that touch those paths come across, carrying
     only those paths' blobs (none of the code), and ``git log -- <path>`` in the
     store shows the original commits. The ``records`` keys are written to the store
     level (D3). The repo leg deletes the moved files and drops the repo level's
     moved keys, or the whole ``.claude/invariant.yaml`` when every record moved
     and the user chose no pointer.
   - **Back to the repo.** The same export, from the store into the repo.
   - **What it does not do.** It does not rewrite the repo's existing history:
     the moved files stay in its past commits, and scrubbing them would be a
     history rewrite, which is out of scope. The exported commit messages are
     scanned by D8 before the store's first push.
   - Without the command, nothing changes for invariant or Heap: their records
     default to ``repo``.

Increments
----------

Each increment leaves in-repo projects unchanged, and proves that with the
existing suite. Increment 1 is this record. ADR-015's increments interleave
after increment 2.

+-----------+---------------------------------+----------------------------+
| Increment | Lands                           | Proven by                  |
+===========+=================================+============================+
| 1         | This ADR and ADR-015, with the  | Docs only.                 |
|           | rulings.                        |                            |
+-----------+---------------------------------+----------------------------+
| 2         | D1 to D4: ``records``,          | The user's step 2: one of  |
|           | ``Manifest.locate``, identity,  | the four repos, linked     |
|           | the three levels with           | from the home side,        |
|           | provenance, user defaults below | running a docs-lane        |
|           | an in-repo project's settings   | session from start to      |
|           | (ruling 19),                    | merge, with the repo       |
|           | ``store init / link / status``, | afterwards holding only    |
|           | the oracle's five checks (D10). | its own commit (the spike  |
|           | Guidelines records refused      | walked this on a toy       |
|           | under ``store`` (D9).           | tree). In this repository, |
|           |                                 | a user default shown by    |
|           |                                 | ``config show`` with its   |
|           |                                 | ``user:`` label and listed |
|           |                                 | by ``doctor``, and a key   |
|           |                                 | the repo sets winning over |
|           |                                 | it.                        |
+-----------+---------------------------------+----------------------------+
| 3         | D11 for Claude Code, as amended | In a store project with    |
|           | by ADR-020 D9 (2026-11-10):     | ``local`` wiring:          |
|           | ``records.hooks`` / ``agents``  | ``git status`` empty after |
|           | ``local`` (``user`` retired),   | install; a host in the     |
|           | ``.claude/settings.local.json``,| main checkout and one in a |
|           | shims and agents under          | session worktree each      |
|           | ``.claude/``, the               | firing once. Uninstall     |
|           | ``.git/info/exclude`` lines,    | leaving                    |
|           | permissions, the install        | ``settings.local.json``    |
|           | receipt, isolate rendering into | and ``.git/info/exclude``  |
|           | worktrees, doctor rows;         | byte for byte as before,   |
|           | ``linked-repos.txt`` removed.   | and an agent file edited   |
|           |                                 | after install left in      |
|           |                                 | place and named. A scratch |
|           |                                 | ``HOME`` with no setting   |
|           |                                 | written. invariant and     |
|           |                                 | Heap unchanged.            |
+-----------+---------------------------------+----------------------------+
| 4         | Split on 2026-11-11             | The union of the three     |
|           | (DEC_ADR_014_I4A) into 4a, 4b   | rows below.                |
|           | and 4c, built in that order.    |                            |
|           | Together: D5 (legs, areas and   |                            |
|           | per-file units, the global      |                            |
|           | order, the lease, per-unit      |                            |
|           | queues, takeover and rules, the |                            |
|           | mid-design back-off of ruling   |                            |
|           | 21, attribution) and D9 (gates  |                            |
|           | per leg, reads and writes under |                            |
|           | one span, store bases). Memory  |                            |
|           | outside the lock (ruling 20).   |                            |
|           | The guidelines refusal lifted.  |                            |
+-----------+---------------------------------+----------------------------+
| 4a        | The lease and its CLI, no hooks | Two sessions on one store  |
|           | and no gates:                   | reading the same area: the |
|           | ``store.docs_lock`` read,       | second queued until the    |
|           | overlapping areas refused at    | first releases, then       |
|           | load, unit keys and the global  | granted. Reading different |
|           | order, the lease record, grant, | areas, both granted at     |
|           | queue and hand-over in one      | once. Opposite-order       |
|           | locked write, heartbeat and     | requests both completing   |
|           | staleness (time and a dead      | with no deadlock. An       |
|           | host), ``--take-over            | overlapping area           |
|           | --reason``, ``docs unlock``     | declaration refused at     |
|           | refusing uncommitted changes,   | load. A stale lease taken  |
|           | ruling 21's back-off,           | over only with a reason.   |
|           | ``--wait``; the session's store |                            |
|           | leg and its spans, units        |                            |
|           | released at landing, teardown   |                            |
|           | and abandon, units in ``session |                            |
|           | status`` and ``list``.          |                            |
+-----------+---------------------------------+----------------------------+
| 4b        | Enforcement and attribution:    | The second session denied  |
|           | ``store.docs_lock_held`` with   | a doc read until the first |
|           | the on-read grant, memory       | releases, then granted. A  |
|           | outside the lock, ``docs_read`` | memory write by a session  |
|           | rows carrying lease and unit,   | holding no unit, accepted. |
|           | ``docs.read_architecture``      |                            |
|           | taking reads under a lease,     |                            |
|           | ``docs_roots`` and              |                            |
|           | ``_foreign_project`` for store  |                            |
|           | paths, the hook heartbeat, the  |                            |
|           | new holder told (SessionStart,  |                            |
|           | turn context, the oracle's      |                            |
|           | alert), the oracle's list,      |                            |
|           | panel and page.                 |                            |
+-----------+---------------------------------+----------------------------+
| 4c        | Split on 2026-11-11             | The union of the two rows  |
|           | (DEC_ADR_014_I4C1) into 4c-1    | below.                     |
|           | and 4c-2, built in that order.  |                            |
|           | Together: D9 (gates per leg,    |                            |
|           | reads and writes under one      |                            |
|           | span, store bases),             |                            |
|           | ``store.docs_lock_released`` on |                            |
|           | design states, both guidelines  |                            |
|           | refusals lifted, the store      |                            |
|           | starter writing a ruleset, the  |                            |
|           | library feature-architect's doc |                            |
|           | update moved to the end of      |                            |
|           | design under a store docs lock, |                            |
|           | invariant's copy re-rendered.   |                            |
+-----------+---------------------------------+----------------------------+
| 4c-1      | Gates per leg and the lock      | A ``conf.py`` change held  |
|           | released: ``_changed_set`` per  | by ``docs.updated`` until  |
|           | leg, ``docs.updated`` pairing a | a doc is committed under a |
|           | span with its read,             | span that also holds the   |
|           | ``docs.diagrams_current``       | read. invariant and Heap   |
|           | reading the doc leg, the        | unchanged.                 |
|           | privacy review boundary reading |                            |
|           | every leg,                      |                            |
|           | ``store.docs_lock_released`` on |                            |
|           | every design state of a store   |                            |
|           | project, the library            |                            |
|           | feature-architect's doc update  |                            |
|           | moved to the end of design      |                            |
|           | under a store docs lock,        |                            |
|           | invariant's copy re-rendered.   |                            |
+-----------+---------------------------------+----------------------------+
| 4c-2      | Guidelines in a store: the      | A grown store baseline     |
|           | manifest's location a property, | refused. invariant's       |
|           | the guidelines readers' policy  | ruleset pin unchanged.     |
|           | tree split from the change's    |                            |
|           | tree, the shrink gate reading   |                            |
|           | the store's base, both          |                            |
|           | guidelines refusals lifted, the |                            |
|           | store starter writing a ruleset |                            |
|           | at the docs project's           |                            |
|           | guidelines folder (ADR-020      |                            |
|           | D10). Decided with the split,   |                            |
|           | built here: a session that held |                            |
|           | no span compares against the    |                            |
|           | store's current HEAD; CLI       |                            |
|           | writes into store policy take   |                            |
|           | 4b's on-access grant, or refuse |                            |
|           | naming ``docs lock``.           |                            |
+-----------+---------------------------------+----------------------------+
| 5         | D6 and D7: release with fetch   | A forced repo-leg failure  |
|           | and the same-file check, the    | finished by resume. A      |
|           | landing record, half-landed     | same-file conflict at      |
|           | resume, sync settings,          | release shown, with the    |
|           | ``store pull --replay``, the    | units kept. Offline,       |
|           | explicit ``store clone``. Split | behind, ahead and          |
|           | on 2026-11-15                   | diverged, each with its    |
|           | (DEC_ADR_014_I5A) into 5a and   | message. On a fresh        |
|           | 5b, built in that order.        | ``INVARIANT_HOME``, a      |
|           |                                 | pointer to an absent store |
|           |                                 | stopping session start     |
|           |                                 | with the clone command;    |
|           |                                 | ``store clone`` showing    |
|           |                                 | the declared commands, and |
|           |                                 | a declined clone           |
|           |                                 | registering nothing.       |
+-----------+---------------------------------+----------------------------+
| 5a        | D7, sync: the ``sync``          | Offline, behind, ahead and |
|           | settings, ``core/storesync``    | diverged, each with its    |
|           | with ``core/git``'s fetch as a  | message. On a fresh        |
|           | named write, the four states at | ``INVARIANT_HOME``, a      |
|           | session start, resume and       | pointer to an absent store |
|           | ``docs lock``, the same-file    | stopping session start     |
|           | check as one function, ``store  | with the clone command;    |
|           | pull --replay``, ``store        | ``store clone`` showing    |
|           | clone`` and the absent-store    | the declared commands, and |
|           | stop.                           | a declined clone           |
|           |                                 | registering nothing.       |
|           |                                 | invariant and Heap         |
|           |                                 | unchanged.                 |
+-----------+---------------------------------+----------------------------+
| 5b        | D6, landing: the release        | A forced repo-leg failure  |
|           | fetching first and stopping on  | finished by resume. A      |
|           | 5a's same-file check, the span  | same-file conflict at      |
|           | commits replayed on top, the    | release shown, with the    |
|           | landing record, half-landed     | units kept. A command a    |
|           | resume and abandon, and ruling  | pull changed, named at     |
|           | 22: session start naming every  | session start, old and     |
|           | ``commands.*`` value a pull     | new. invariant and Heap    |
|           | changed, old and new.           | unchanged.                 |
+-----------+---------------------------------+----------------------------+
| 6         | D8: push scan, store git hooks, | A home path refused at     |
|           | store-aware routes, the         | push from a host, from a   |
|           | no-remote relaxation.           | shell and from inside the  |
|           |                                 | store.                     |
+-----------+---------------------------------+----------------------------+
| 7a        | D12 with ADR-020 D3, split      | A toy in-repo project      |
|           | (decided 2026-11-15 by the      | moved to a store: ``config |
|           | parent session,                 | show`` the same, labelled  |
|           | :need:`DEC_ADR_014_I7A`):       | ``store:``; path history   |
|           | ``store migrate --to store``,   | intact;                    |
|           | the shared machinery            | ``invariant-docs/`` moved  |
|           | (``core/storemigrate``: the     | with no path rewritten;    |
|           | plan, the history both ways,    | wiring committed to local; |
|           | the either-direction refusals,  | ``git status`` clean in    |
|           | the marker), the                | both homes; each refusal   |
|           | command-started ``chore``-lane  | exit 2 with nothing        |
|           | session, the committed wiring   | written. invariant and     |
|           | made local after the merge, and | Heap unchanged.            |
|           | ``--to store``'s own refusals.  |                            |
+-----------+---------------------------------+----------------------------+
| 7b        | ``--to repo``: the keys         | The round trip ADR-020's   |
|           | materialised, the store's entry | table lists, ``--to        |
|           | and records removed, the        | store`` then ``--to        |
|           | user-level link removed, local  | repo``, labelled           |
|           | wiring made committed, the      | ``store:`` then ``repo:``. |
|           | half-landed main checkout       | invariant and Heap         |
|           | refusing and finished by        | unchanged.                 |
|           | resume, its refusals with the   |                            |
|           | root ``conf.py`` one; ADR-020   |                            |
|           | increment 2 recorded.           |                            |
+-----------+---------------------------------+----------------------------+
| Later     | More than one repo leg per      | Not built here. D5's list  |
|           | session (ruling 6).             | and D6's per-leg landing   |
|           |                                 | are the seams.             |
+-----------+---------------------------------+----------------------------+

.. mermaid::

   graph TD
       CLI[cli] --> LOC["core/manifest: locate, levels, records"]
       LOC --> GIT[core/git]
       LOC --> USERCFG[("~/.invariant/config.yaml: user level, repo -> store")]
       LOC --> STORECFG[("store/invariant.yaml: store level")]
       CLI --> SESSION["domains/session: legs, docs lock units and lease, landing record"]
       SESSION --> STORELOCK[("store/.git/invariant/locks/docs.json")]
       SESSION --> GIT
       RULES["core/rules: changed set per leg"] --> LOC
       RULES --> GIT
       ORACLE["oracle/index, scan, gates"] --> LOC
       HOSTS["hosts/claude_code: wiring_plan(repo or local)"] --> CLI
       STORE[("store: a git repository")] -. git hooks call .-> CLI

Alternatives rejected
---------------------

+----------------------------------+----------------------------------+
| Alternative                      | Why not                          |
+==================================+==================================+
| In-repo ``.invariant/`` per repo | Run in IDEA_040: the files still |
|                                  | land in the repo.                |
+----------------------------------+----------------------------------+
| A shared parent directory        | Run in IDEA_040: no repository   |
|                                  | at the parent, so no sessions.   |
+----------------------------------+----------------------------------+
| Root commit, or path, as the     | D4: shallow clones and merged    |
| identity                         | histories (run), and moves.      |
+----------------------------------+----------------------------------+
| A store worktree and branch per  | Replaced by ruling 13: parallel  |
| session (this record's earlier   | doc branches let one session     |
| draft)                           | design against docs another is   |
|                                  | changing. Its costs, policy      |
|                                  | landing before review and        |
|                                  | serialised design, are stated in |
|                                  | D5.                              |
+----------------------------------+----------------------------------+
| One docs lock per store (the     | Replaced by ruling 16: sessions  |
| next draft)                      | on unrelated areas of one store  |
|                                  | would wait for each other.       |
+----------------------------------+----------------------------------+
| Waiting for units in any order   | Two sessions could each hold     |
|                                  | what the other waits for. Ruling |
|                                  | 16 fixes one global order.       |
+----------------------------------+----------------------------------+
| Cloning a store implicitly, from | Ruled out (18): a store names    |
| a pointer or at session start    | commands the gates run, so its   |
|                                  | first arrival is shown first.    |
+----------------------------------+----------------------------------+
| A pluggable sync backend         | Ruled out (3).                   |
+----------------------------------+----------------------------------+
| Calling ``invariant hook``       | Run: degraded output in every    |
| directly from user settings      | unlinked repo, and about 0.24 s  |
|                                  | per call.                        |
+----------------------------------+----------------------------------+
| Agents scoped to a launch only   | D11: a plain ``claude`` would    |
|                                  | have the gates and no agents.    |
+----------------------------------+----------------------------------+
| User-level hooks, shim and       | Replaced 2026-11-10 by ADR-020   |
| agents (this record's D11 as     | ruling 8: never install          |
| first accepted)                  | globally; the user decides per   |
|                                  | project.                         |
+----------------------------------+----------------------------------+
| Pull in the SessionStart hook    | Its 15 seconds must never wait   |
|                                  | on the network.                  |
+----------------------------------+----------------------------------+
| Repo leg first at landing;       | Ruled out (8).                   |
| rollback of a landed store       |                                  |
+----------------------------------+----------------------------------+
| Merging hunks of a file changed  | Ruled out (9).                   |
| on both sides                    |                                  |
+----------------------------------+----------------------------------+

Consequences
------------

- An in-repo project resolves exactly as before, except for user defaults the
  user chooses to write, which sit below its own settings and are named by
  ``config show`` and ``doctor`` (D3, ruling 19).
- A store project's repo commits nothing of invariant's, or only the pointer
  if one is chosen. Its git directory carries the identity ID, the runtime
  state and, when the user installed the wiring, the exclude lines; none is
  tracked.
- Amended 2026-11-10 (ADR-020 D9): no Claude Code session outside a wired
  project sees anything of invariant's. A project the user chose to wire
  carries its hooks and agents in its own ``.claude/``, and uninstall takes
  exactly those back out. (As first accepted, every session on the machine
  ran a user-level shell walk per hook event.)
- Sessions on one store take turns at each area of the docs: one reads,
  designs and writes an area at a time, sessions on different areas run side
  by side, and their code work runs in parallel afterwards. A session
  is routinely half-landed (docs landed, code pending), in a recorded,
  resumable state.
- What leaves the machine from a store passes one scan, whatever route made
  the commit.

Late units and pulled commands (rulings 21 and 22)
--------------------------------------------------

The draft's six questions were ruled on 2026-11-05 (rulings 16 to 20). The
two questions those rulings raised were ruled the same day (rulings 21 and
22). Their designs follow. No question is open.

- **Ruling 21 (D5). A unit discovered mid-design.** A session that
  holds some units finds, mid-design, that it must read another, whose key
  sorts before one it holds. Waiting for it would break the global order.
  Ruled: declare up front, try a late unit without waiting, and back off in
  order when it is busy.

  1. The normal path is up front: the design phase names its units and
     ``docs lock`` takes them in order (D5).
  2. A late unit that sorts after every held unit is queued for as usual.
  3. A late unit that sorts before a held one is tried without waiting. It is
     granted if it is free with an empty queue: a grant that never waits adds
     no edge to any wait-for cycle, so it cannot deadlock.
  4. If it is busy, the engine backs off for the session. It releases every
     held unit that sorts after the wanted one, voids the reads made under
     those spans, and queues the session for the wanted unit followed by the
     released ones, in order. The session re-reads each released unit after
     it gets it back, as the one-lease rule already requires. The back-off
     refuses while a unit it would release carries uncommitted changes, and
     names them. The session first commits that unit's finished update,
     which releases it normally, or discards its edit.

  Rejected: releasing everything and starting over (it throws away units
  that are not in the way); and declaring up front only, with no late units
  (a design cannot always know which docs it will need).

- **Ruling 22 (D7). A pulled store that changes a command.** The
  first clone shows the store's commands, but a later automatic pull can
  change one, and the gates would then run a command this operator has not
  seen. Ruled: the pull stays automatic, as ruled, and session start
  names every ``commands.*`` value the pull changed, old and new, in its output
  and context. It does not block, because the change reached the store's main
  line through another session's gates. This matches ADR-015 ruling 2, under which
  a CI edit never silently changes a gate.

As built: increment 3 (2026-11-11)
----------------------------------

D11 for Claude Code, as :need:`DEC_ADR_020_D9` amends it, as the architecture
docs describe it (``manifest.rst``, "Records and their locations";
``cli.rst``, ``init``, ``hooks install``, ``uninstall``, ``doctor``, ``store
link`` and ``session isolate``; ``overview.rst``, ``hosts/claude_code_wiring``
and "Where things live at rest"; ``state_model.rst``, "Storage layout"). Where
the build settles what the text left open:

.. dec:: ADR-014 increment 3 as built: local wiring, its receipt, and init's question
   :id: DEC_ADR_014_I3
   :status: accepted
   :part_of: DEC_ADR_014
   :amends: DEC_ADR_020
   :affects: COMP_HOSTS_CLAUDE_CODE, COMP_HOSTS_CLAUDE_CODE_WIRING, COMP_CORE_MANIFEST, COMP_CORE_STORES, COMP_CMD_SETUP, COMP_CMD_SESSION, COMP_CORE_RULES

   - **One module says where.** ``hosts/claude_code_wiring`` holds
     ``wiring_plan(root, location, store_root)``, the receipt, and the edits
     to the settings file and to ``.git/info/exclude``.
     ``ClaudeCodeHost.wiring_plan(location)`` is the adapter's half of the
     neutral seam: it reads ``records.hooks`` and ``records.agents`` and hands
     the store's main checkout in, so ``core/manifest`` still knows nothing of
     hosts. The two records are read separately, so a project may wire its
     hooks ``local`` and keep committed agents, or the reverse.
   - **``user`` is refused naming its fix.** ``records.hooks: user`` and
     ``records.agents: user`` fail at load with "user is retired (ADR-020 D9)"
     and both locations that remain, ``local`` and ``repo``.
   - **A receipt for ``local`` only.** ``repo`` wiring is tracked, and git is
     its record, so a ``repo`` install writes no receipt and uninstall keeps
     recognising its entries by the shim path, as before. invariant and Heap
     are therefore unchanged.
   - **Byte for byte, by comparison rather than by copy.** The receipt keeps
     the settings file's and the exclude file's text from before invariant
     first touched them. Uninstall removes exactly the receipt's entries,
     lines and files; when what remains equals that earlier content, the
     earlier bytes are written back, and when the file did not exist and
     nothing else is left, it is removed. A key or line the user added since
     survives, and the file is then written in its normal form. A second
     install adds nothing already present and writes nothing.
   - **What stays, stays hidden.** An agent edited since install is left,
     named by uninstall, and keeps its exclude line, so ``git status`` does
     not start showing it. A ``settings.local.json`` invariant created that
     still holds the user's own keys stays, with its line. Both stay on the
     receipt, so a later uninstall, once the user has removed the file, takes
     the line too.
   - **Exclude lines are anchored paths**, one per file (``/.claude/settings.local.json``,
     ``/.claude/hooks/invariant-<event>.sh``, ``/.claude/agents/<name>.md``),
     so a line covers the main checkout and every session worktree alike, and
     never a file the user has of their own elsewhere under ``.claude/``.
   - **Rendering into a worktree** is the CLI's step after ``session isolate
     --execute`` creates the worktree, since only ``hosts/`` writes host files
     and ``domains/session`` imports no host. It runs only when the main
     checkout's receipt holds an install, writes through the same install
     plan, and records nothing. The rendered files are excluded, so
     ``git worktree remove`` (teardown) counts the worktree clean.
   - **Not installed is not broken.** ``doctor``'s ``host wiring`` row passes,
     naming ``invariant hooks install`` and ``invariant agents install``, when
     neither hooks nor agents are installed; the shim rows then pass too, and
     ``agents loadable by host`` does not list absent library agents. Once any
     library agent is installed, an absent one is a half install and fails as
     before. ``agents.rendered_current`` and the ``library agents current``
     row are unchanged: with nothing installed they warn, naming each absent
     agent and ``invariant agents install``, and never block, so a project
     that chose not to wire is reminded at each landing and is never stopped.
   - **No managed block under ``agents: local``.** The CLI's
     ``host_instruction_files`` answers no default file, so ``guidelines
     render`` writes no block into a tracked ``CLAUDE.md``, and ``doctor`` and
     ``guidelines.generated_current`` raise no always-layer gap for it; the
     turn index carries the always rules. A project that names files in
     ``guidelines.instruction_files`` still gets them.
   - **init's question lands here, brought forward from ADR-020's switch.**
     The user's direction for this increment: "We should not install hooks
     globally to take over everything in the system so user should tell us if
     invariant should install the hooks and each project." So ``init`` asks
     ``host:install`` last, default no, and a non-interactive ``init``
     installs nothing and names the two commands; ``--host-install`` answers
     yes. It is asked by ``init`` alone, not from the questionnaire's
     catalogue, because it writes no configuration: ``hooks install`` and
     ``agents install`` are its route afterwards. Yes installs where the
     project as written says. This amends :need:`DEC_ADR_020`'s increments,
     which listed the question with the switch; the switch keeps the rest of
     D9's question as it stands (the home section and its order).
   - **The index is gone.** ``store link`` and ``store link --update`` no longer
     write ``~/.invariant/linked-repos.txt``, and nothing reads it. A copy an
     earlier build wrote is inert and may be deleted.

The proof is ``tests/test_adr_014_local_wiring.py``, on a toy repository with
a scratch ``HOME`` seeded with a user-level Claude Code settings file and the
suite's scratch ``INVARIANT_HOME``. In a store project with ``hooks`` and
``agents`` ``local``: ``git status --untracked-files=all`` is empty after
``hooks install`` and ``agents install``; ``settings.local.json`` holds one
entry per event and the store's main checkout; a second install changes no
byte; uninstall gives back a hand-formatted ``settings.local.json`` and the
exclude file byte for byte, and removes what it created with its
directories; an agent edited after install is left and named; a setting the
user added after install survives; ``doctor`` passes local wiring, passes
not-installed naming the commands, and fails a missing exclude line and a
missing settings entry; ``session isolate --execute`` renders the wiring into
the worktree with ``git status`` empty there, and the worktree stays
removable; each shim answers in both checkouts as the host would call it;
the scratch ``HOME`` is unchanged throughout; and ``init`` installs nothing
without ``--host-install`` or a yes. An in-repo project's ``repo`` wiring is
written as before, with no receipt and no exclude line.

**Owed, live.** No test can start Claude Code. The leg that remains is a real
host: in a store project wired ``local``, ``claude`` started in the main
checkout and one started in a session worktree each fire every hook once,
read the agents, and are granted the store's checkout, while a ``claude``
started outside the project sees nothing of invariant's. Until that runs,
whether Claude Code also reads the main checkout's ``settings.local.json``
from a worktree nested under it (``session.worktree_dir`` inside the
repository) is reasoned, not run.

As built: increment 4a (2026-11-11)
-----------------------------------

The lease and its CLI, as the architecture docs describe them
(``manifest.rst``, "Records and their locations"; ``cli.rst``, ``docs lock``,
``docs unlock``, ``session status`` and ``session list``; ``overview.rst``,
``core/doclock`` and ``domains/session``; ``state_model.rst``, the session's
``legs`` and "Storage layout"). Where the build settles what the text left
open:

.. dec:: ADR-014 increment 4 is split; 4a as built: the lease, its units and its CLI
   :id: DEC_ADR_014_I4A
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_CORE_DOCLOCK, COMP_CORE_STORES, COMP_DOMAINS_SESSION, COMP_CORE_GIT

   - **The split.** Increment 4 was too large for one reviewable change, so
     it is built as 4a, 4b and 4c, each proving its slice of the row (the
     increments table above). Decided 2026-11-11 from the user's standing
     rulings (generic and configurable, no surprises): 4a the lease and its
     CLI, 4b enforcement and attribution, 4c D9's gates.
   - **The feature-architect's doc update** moves to the end of design in the
     library agent's text, conditional on a store docs lock, and invariant's
     own rendered copy is re-rendered in 4c, with the
     ``store.docs_lock_released`` gate that makes it binding. Nothing in 4a
     changes an agent, so invariant's and Heap's copies stay current.
   - **A store doc commit names no lease.** Who wrote it is derived from the
     span's sha range and its unit's paths: units never overlap and only the
     holder writes a unit, so the commits between a span's two shas touching
     its paths are the holder's. No trailer is written or read, so a commit
     made with plain ``git -C <store>`` attributes the same way. A span's
     ``changed`` is that reading, taken at release.
   - **The repository leg is the record's own fields.** ``legs`` holds the
     store leg only; ``worktree``, ``branch`` and ``base`` stay where they are
     and are the one repository leg, so no record is rewritten and every
     reader of them is unchanged. A second repository leg (ruling 6) is the
     later increment that moves them.
   - **The lease record is the truth for open spans.** A grant made by the
     session's own command opens its span at once; a unit passed to it by
     another session's release opens its span from the record's ``since``
     and ``sha`` the next time the session's own command reads the record,
     because one session never writes another's record in ordinary use (the
     state model's single-writer rule). The one exception is a takeover,
     which writes the previous holder's record (its span closed as
     ``taken_over``, an override naming the reason and who took it) in
     whichever repository's state root it lives: that lease is stale by
     definition, so its owner is not writing at the same time.
   - **A lease ends when it holds and wants nothing**, and the session's next
     ``docs lock`` opens a new one; each span names the lease it ran under.
   - **The lifecycle never refuses.** Landing, teardown and abandoning free
     every unit the session holds or waits for, uncommitted changes or not:
     the holder is going, and a unit held by a finished session would only
     wait to go stale. The closed span names the unit's dirty paths
     (``uncommitted``), so the next holder's reviewer can see them.
     ``docs unlock`` and the back-off do refuse, naming the paths.
   - **``--wait`` blocks on a named pipe, not a file lock.** D5 said "an OS
     file lock", but no process holds a lock for the length of a lease: the
     lease is a record, written by short processes. So the waiter opens its
     lease's pipe in the store's git directory, re-reads the record, and
     blocks in ``select`` until a hand-over writes one byte, after the record
     is written, or the timeout ends. It never polls, which is what the ruling
     asks.
   - **Overlap is refused twice.** At load, a pattern of one area that matches
     another's pattern read as a path (``docs/**`` beside
     ``docs/architecture/**``). Patterns that overlap with neither matching
     the other's text (``docs/*.md`` beside ``docs/x*``) cannot be found
     without listing paths at every load, so a path both match is refused,
     naming both areas, when it is first resolved to a unit.
   - **Data records take no unit.** A path under ``memory``, the backlog,
     reports or sweeps kept in the store is refused by ``docs lock``, naming
     ruling 20; those are written at any time. The read and write side of
     ruling 20 (a memory write by a session holding no unit, accepted) is
     4b's, where the host first enforces the lock.
   - **The heartbeat in 4a** is written by ``docs lock`` and ``docs unlock``,
     at most once a minute. The hooks' heartbeat is 4b's, with the rest of
     the host side. A host's liveness is read from the attention sidecar's
     ``pid`` in the holder's own state root, by the CLI, which hands
     ``domains/session`` a callable: neither ``core`` nor ``domains`` imports
     the oracle. An unknown answer never makes a lease stale.
   - **No fetch yet.** ``docs lock`` does not pull the store first: sync is
     D7, increment 5.
   - **Exit codes.** A refusal is 2, as every usage refusal; ``--wait`` timing
     out is 1 and keeps the place in the queue.

The proof is ``tests/test_adr_014_docs_lock.py``. Two toy repositories, each
with an origin, are linked to one store with two areas declared, and each
runs a session. The same area: the second session is queued at position 1
naming the holder, and is granted when the first releases, the hand-over
recorded in the lease and as a span on its store leg. Different areas: both
granted at once. Opposite orders: each holds one area and wants the other;
the one whose late unit sorts first backs off, its unit passing to the other,
which finishes and releases; the first then holds both, and the lease record
ends empty. An overlapping declaration is refused at load, and by ``config
show``. A live lease is not taken over; a stale one (a heartbeat 31 minutes
old) is refused without ``--reason`` and taken with one, the reason in both
sessions' overrides and the holder's span closed as ``taken_over``; a dead
host makes a lease stale and an unknown one does not. Also: units taken in
key order, stopping at the first busy one; a late free unit granted without
waiting; a back-off and an unlock each refused over an uncommitted path, and
the span's ``changed`` after a commit; ``--wait`` blocking until granted,
and timing out still queued; the lifecycle releasing and handing on; memory
refused as a unit; ``session status`` and ``session list`` showing holder,
age and queue; an in-repo project unchanged, its ``docs lock`` refused and
its record carrying no leg.

**Owed.** The proof's "denied a doc read until the first releases" and "a
memory write ... accepted" are 4b's, where the host enforces the lock; the
``conf.py`` and grown-baseline proofs are 4c's.

As built: increment 4b (2026-11-11)
-----------------------------------

Enforcement and attribution at the host, as the architecture docs describe
them (``state_model.rst``, "Attribution" and the session domain's rules;
``cli.rst``, ``docs lock``, ``invariant hook``, ``invariant context`` and
``oracle list``; ``overview.rst``, ``core/doclock``, ``core/rules``,
``domains/session``, ``oracle/scan``, ``oracle/signals`` and
``hosts/claude_code``; ``manifest.rst``, "Declared stores are writable
stores"; ``oracle.rst`` and ``oracle-panel.rst``, the signals and their
words). Where the build settles what the text left open:

.. dec:: ADR-014 increment 4b as built: the lock enforced and attributed at the host
   :id: DEC_ADR_014_I4B
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_CORE_DOCLOCK, COMP_CORE_RULES, COMP_CORE_STORES, COMP_DOMAINS_SESSION, COMP_HOSTS_CLAUDE_CODE, COMP_CLI_MAIN, COMP_ORACLE_SCAN, COMP_ORACLE_SIGNALS, COMP_ORACLE_PANEL

   - **What the lock governs.** A store policy path: one under a policy
     record kept in the store (``docs``, which includes ``docs.needs.srcdir``,
     and ``guidelines``, overlays included), or one a declared area matches,
     so an area may reach files no record names. The data records kept there
     (memory, the backlog, reports, sweeps) are outside it, which is ruling
     20 at the host: ``store.docs_lock_held`` answers N/A for them, naming
     the ruling. The store's own ``invariant.yaml`` and its git directory are
     not governed: neither is something an agent designs against, and the
     engine reads the configuration through its own code, not a tool call.
     ``core/doclock.classify`` is that one reading, asked by the rule and by
     the grant alike.
   - **The on-access grant covers reads and edits.** D5 spoke of reads; a
     write takes the same grant, which is what makes "a new file outside every
     area is its own unit, taken when it is first written" hold. It is made
     by the session domain before the pre-tool-use rules run, since a rule
     only reads. A unit is granted when nobody holds it and nobody queues for
     it, and when the session's lease waits for no unit sorting before it: a
     grant never waits, but holding a unit that sorts after one the lease
     waits for would break the order every wait relies on. The hook never
     queues; a refusal names ``docs lock``, which queues or backs off as
     ruling 21 says.
   - **Granted only on proof.** The grant writes the lease on the session's
     behalf, so it is made only for a session the call proves (explicit,
     host, target, prompt, cwd). A session the call only guesses (``origin``)
     is still checked by the rule against the holder, and is refused when it
     does not hold the unit, which is D5's "holding the lock does not turn a
     guess into a proof" read from the other side. A call that resolves to no
     session is ungoverned, as at every pre-tool-use rule.
   - **A read carries its lease.** A store policy path's ``docs_read`` row is
     ``{"path", "lease", "unit"}``; every other row stays the path, so an
     in-repo project's evidence, and every reader of it, is unchanged. For
     docs kept in a store, ``docs.read_architecture`` counts a row only when
     its lease is one of the session's spans' leases (held or held before),
     and its failure counts the reads it set aside.
   - **Another project's store is unplaced, not routed.** D5 had
     ``_foreign_project`` reach a store path's repository through
     ``store.repos`` and the user config. Neither names a checkout: both map
     an identity to a store. The lease record names the unit's holder and
     its state root, but taking the holder as the call's session would be the
     lock standing in for attribution, which D5 forbids. So a path in the
     host project's own store is home, and one in another project's store
     (``core/stores.store_root_of``, asked when no project claims the path) is
     unplaced with reason ``store``, recorded nowhere, and a Read of one says
     so, which is ADR-011's answer for any path no live worktree contains.
   - **The hooks' heartbeat** is written by ``invariant hook`` for every event
     but SessionStart, through the session domain, at most once a minute, for
     the session the host resolves to. SessionStart writes nothing, as it
     never has.
   - **The new holder is told, read only.** A unit the lease holds with no
     open span on the store leg was passed on by a release since the session
     last looked. The turn context (SessionStart and every prompt) says so
     until the session's next store call or ``docs`` command opens the span;
     the oracle raises ``docs_granted``, an alert in the "Needs you" group,
     once per hand-over. Nothing on those paths writes the session record.
   - **The oracle's views** read the lease record once per store project per
     scan (``oracle/scan.docs_lock_views``): each session row carries its
     held units (age, queue, staleness, unseen) and the unit it queues for;
     ``oracle list`` prints a ``docs`` line for each, the TUI the signals,
     and the panel and page a chip for each signal and the units under
     "Technical details". ``lease_host_alive``, the holder's liveness from
     its attention sidecar, moved from the CLI into ``oracle/scan``, which
     owns the sidecar; the CLI still hands it to the session domain.
   - **Attribution of a store commit** is 4a's: derived from a span's sha
     range and its unit's paths, with no commit trailer written or read.
     Nothing in 4b writes a commit.
   - **A hook pays for the lock only where it governs.** Hooks fire on every
     tool call, so the rule and the session domain classify the path against
     the store's ``read_config`` first and build a ``DocsLock`` only for a
     governed path; a memory, config or outside path opens no lease record.
     The lock finds the store's git directory from disk
     (``identity.common_dir_of``), asking git only when that cannot answer,
     so the heartbeat every hook writes spawns no subprocess.
   - **In-repo projects are unchanged.** The grant, the heartbeat and the
     turn context's lines cost one attribute read; ``store.docs_lock_held``
     answers N/A; ``docs_roots`` and the docs gate read as before. The rule
     blocks in every profile, as the isolation rules do.

The proof is ``tests/test_adr_014_docs_lock_host.py``, on 4a's two toy
repositories linked to one store, with memory kept in the store and each
session bound to its own host, driving ``invariant hook`` as Claude Code
would. The first session's Read of an architecture doc takes
``area:architecture`` in the hook call and opens its span; the second
session's Read is refused naming the unit, the holder and the command, and
takes nothing; queued, it is refused naming its position; when the first
releases, the second's next prompt and SessionStart say the unit was passed
to it, its Read is allowed and recorded with the lease and the unit, and
``docs.read_architecture`` passes; once its span is open the notice turns
to "holding". A Write to memory by a session holding no unit is accepted,
and the rule names ruling 20. Also: an edit of a held unit passes and of a
busy one is refused; a new file outside every area is taken when first
written; the store's ``invariant.yaml`` and git directory are not governed;
an unresolved call is ungoverned; a stale holder is named with the takeover;
a grant never puts a unit after one the lease waits for, and grants one
before it; a guessed session is refused and granted nothing; an overlap only
a path shows is refused naming the areas; reads without a lease, or none,
fail the docs gate, and a lease held before still counts after release; the
lease row is written once; the hooks refresh a stale-looking heartbeat and
SessionStart does not; a Read of another project's store from an in-repo
project is recorded nowhere and said; the oracle's rows, list lines, signals,
chip and technical lines; an in-repo project's rule N/A and its
``docs_roots`` unchanged.

As built: increment 4c-1 (2026-11-11)
-------------------------------------

D9's gates per leg and D5's release before implementation, as the
architecture docs describe them (``state_model.rst``, the gates table and
"Domain: pipeline"; ``overview.rst``, ``core/rules`` and ``core/machines``;
``manifest.rst``, "Records and their locations"). Where the build settles
what the text left open:

.. dec:: ADR-014 increment 4c is split; 4c-1 as built: the gates read each leg, and the lock is released before implementation
   :id: DEC_ADR_014_I4C1
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_CORE_RULES, COMP_CORE_DOCLOCK, COMP_DOMAINS_SESSION

   - **The split.** Increment 4c was too large for one reviewable change,
     so it is built as 4c-1 and 4c-2 (the increments table above). Decided
     2026-11-11 by the parent session, on the basis of the 4a, 4b and 4c
     split: 4c-1 the gates per leg and the lock released, 4c-2 the
     guidelines in a store. Two choices 4c-2 makes were decided with the
     split and are built there: a session that held no span compares the
     baseline against the store's current HEAD, since the lock gates every
     write it could have made; and a CLI write into store policy
     (``guidelines render``, ``guidelines baseline --prune``, the starter)
     takes 4b's on-access grant, or refuses naming ``docs lock``.
   - **The store leg is read from the spans.** ``_changed_set(ctx, leg)``
     takes the leg, the repository leg by default, so every reader that
     names none (coverage, tests, guideline selection, the stale-entry
     check) reads exactly what it read before. The store leg is, for each
     span on the session's store leg, the store's diff from its
     ``acquired_at_sha`` to its ``released_at_sha`` (to the store's HEAD
     while it is open), kept to the span's unit, plus a held unit's
     uncommitted paths. Its paths are absolute, in the store's main
     checkout. A span git cannot read, or one naming no sha, makes the leg
     unanswerable, never empty; a session with no span has an empty leg,
     which is an answer.
   - **A store doc counts when it is committed under a span with its
     read.** Under ``records.docs: store``, ``docs.updated`` takes "source
     changed" from the repository leg, as before, and counts a doc of the
     store leg when it was committed under a span of its unit whose lease
     and unit a ``docs_read`` row names, or when it was no file at the
     span's ``acquired_at_sha``. An uncommitted doc never counts: the gate
     blocks until the store carries the update. One counted doc clears it,
     as one doc alongside does in-repo; a doc set aside is named in the
     detail either way. A ``docs_read`` row carries no time, so a unit taken
     again under the same live lease pairs with a read made under either
     span: the lease and the unit are what the record holds.
   - **The doc leg for diagrams.** ``docs.diagrams_current`` reads the store
     leg when the docs are kept in a store, and the repository leg
     otherwise.
   - **Privacy reads every leg.** At the review boundary,
     ``privacy.clean`` scans the store leg's content from the store's main
     checkout, where it already is, beside the repository leg's. Scope is
     spelled store-relative there: ``all``, and each keyword whose path
     resolves into the store (``docs``, ``memory``, ``reports``). A literal
     prefix is spelled against the repository and reaches no store path;
     the store's own ``privacy`` block is D8's, increment 6.
   - **``store.docs_lock_released``** is an exit gate on every state with
     the ``design`` role, injected by ``core/machines`` only in a store
     project (``STORE_LOCK_GATES``), as ``sweep.fresh`` is only where it can
     apply, so an in-repo project's machines are unchanged. It blocks in
     every profile. It fails while the session's lease holds a unit, is
     queued for one or still wants one, and names ``invariant docs unlock``;
     since ``docs unlock`` refuses a unit with uncommitted changes, passing
     it means the doc update is on the store's main line.
   - **The agent.** The library feature-architect's Phase 7 says that,
     under a store docs lock, the doc update ends design (written, committed
     in the store, released) before Phase 5's first advance, and Phase 5
     points there. In a project whose docs are in its repository nothing
     moves. invariant's installed copy was re-rendered by ``invariant agents
     install``. Heap's copy is behind the library until Heap runs its own
     ``agents install``; ``agents.rendered_current`` says so there, as a
     warning.

The proof is ``tests/test_adr_014_per_leg_gates.py``, on 4b's two toy
repositories linked to one store, each session bound to its own host. A
``conf.py`` change is held by ``docs.updated``; a doc read under the lock and
written but not committed still holds it, named as uncommitted; committed in
the store under the span holding the read, it clears, and stays clear after
the release. A doc committed under a span with no read is set aside, naming
it, until the read is made under the same span; one committed after the
release counts for nothing; a file new in the span needs no read. Also: the
store leg's paths, open and committed, and the repository leg untouched by
them; an empty leg without a span; an unreadable span making the leg
unanswerable and the gate fail; ``docs.diagrams_current`` failing a doc
without a diagram and passing one with; the review boundary refusing a home
path committed to a store doc, naming a clean one as scanned, and not
scanning a store path outside scope; ``store.docs_lock_released`` failing a
held unit and a queued session, naming ``docs unlock``, and passing with no
lease; the gate on the design state of ``tdd``, ``bugfix``, ``spec-first`` and
``docs`` in a store project and nowhere on ``chore``; and an in-repo
project's machines without it, the rule N/A and the store leg empty.

As built: increment 4c-2 (2026-11-14)
-------------------------------------

D9's guidelines half, as the architecture docs describe it (``manifest.rst``,
"Records and their locations" and "Finding the project"; ``overview.rst``,
``core/manifest``, ``core/rules``, ``domains/session`` and
``domains/guidelines``; ``state_model.rst``, "Domain: pipeline"; ``cli.rst``,
``init``, ``guidelines render``, ``guidelines baseline`` and ``check``). Where
the build settles what the text left open:

.. dec:: ADR-014 increment 4c-2 as built: the guidelines read from their own tree, and the shrink gate's base in the store
   :id: DEC_ADR_014_I4C2
   :status: accepted
   :part_of: DEC_ADR_014
   :amends: DEC_ADR_020_D10
   :affects: COMP_CORE_MANIFEST, COMP_CORE_RULES, COMP_DOMAINS_GUIDELINES, COMP_DOMAINS_SESSION, COMP_DOMAINS_DOCS, COMP_HOSTS_CLAUDE_CODE, COMP_CMD_SETUP, COMP_CMD_GUIDELINES, COMP_CLI_ONBOARDING

   - **The manifest's location is a property.** ``Manifest.location`` is a
     ``ManifestLocation``: the checkout the project's own level lives in, its
     path there and its kind. ``repo`` is ``.claude/invariant.yaml`` in the
     repository; ``store`` is the store's ``invariant.yaml`` in the store's
     main checkout, whatever pointer named the store, since a pointer may set
     no policy key; ``pointer`` is ``.invariant.yaml`` for a project defined
     by its pointer alone. ``ManifestLocation.level`` reads what that file
     sets for this repository: for a store, its top level overlaid by its
     ``store.repos.<key>`` block, as D3's level 5b resolves. The base readers
     ask it rather than ``MANIFEST_RELPATH``: the shrink gate,
     ``docs.mandate_kept`` (which, in a store project, now compares the
     store's level at the store's base with its main checkout), the
     ``docs.needs`` change ``docs.builds_strict`` looks for (the repository's
     manifest only), ``onboarding.carries_manifest``, ``docs assess --plan``'s
     refusal and ``doctor``'s ``config home`` row. ``manifest_tracking`` takes
     the location's path.
   - **Two trees.** The policy tree is where the guidelines records are read
     and rendered: the store's main checkout under ``records.guidelines:
     store``, else the tree in hand (``guidelines_tree``, with
     ``policy_relpath`` spelling a record's path there). The change tree is
     the tree in hand (``change_tree``), whose files every guideline is
     evaluated over. The ruleset and its registries, the pin, the baseline,
     the checklist and the rules page are the policy tree's; the findings,
     the advisories at the edit, the verdict facts, the touched files and the
     baseline's ``--record`` and ``--prune`` scans are the change tree's; the
     instruction files are the repository's, and are compared with the
     ruleset or the checklist only when those are in the same tree. A path
     in the project's store is never exempt from review, since the docs lock
     governs it, unless it lies under a data record kept there. The overlays
     follow the guidelines record in the host, read store-relative from the
     store's main checkout.
   - **The shrink gate's base.** The base's manifest and the guidelines
     records kept in the store are read in the store at the
     ``acquired_at_sha`` of the session's earliest docs-lock span, the store
     as the session found it before it wrote anything under the lock. A
     session that held no span compares against the store's HEAD, as decided
     with the split (:need:`DEC_ADR_014_I4C1`). Guidelines records kept in the
     repository are still read at the merge base, even when the manifest is
     the store's. Renames stay the repository's, since an entry names a code
     path. A store manifest missing at that commit fails the rule, naming the
     commit; it is never "no baseline configured". A span naming no sha, and
     a store HEAD git cannot read, fail too. The base's paths are read
     against the record's location as the manifest stands now; a record that
     moved between the base and now is ``store migrate``'s (increment 7).
   - **CLI writes into store policy.** ``SessionDomain.docs_write`` takes the
     on-access grant (``grant_free``) for each unit a write is about to touch,
     in key order, for the session the call resolves to, and records the
     spans, as decided with the split. A unit it cannot have refuses the whole
     write with exit 2 and nothing written, naming the unit, its holder and
     ``invariant docs lock``. A session the call only guesses is granted
     nothing and must already hold the unit, as at the host (4b). With no
     session, which is ``init``'s case and a shell outside every session,
     there is no lease to write under: the write goes ahead only into units
     nobody holds or queues for, so it can never change docs another session
     is designing against. ``domains/guidelines`` receives the check as an
     ``admit`` callable from the CLI and imports no other domain for it.
     ``--dry-run`` takes no unit.
   - **The refusals.** ``records.guidelines: store`` loads, and a store
     project may set ``guidelines.baseline``. A project defined by a pointer
     alone, with no store, may still not: its level may set no policy key,
     so the baseline could only come from the user level, which no base
     holds.
   - **The store starter.** ``init`` writes the starter's ruleset in the
     store, at ``ruleset_location(<srcdir>)`` under the store root, and the
     repository's block in the store gains ``records.guidelines: store`` and
     ``guidelines.ruleset``. This amends :need:`DEC_ADR_020_D10`'s "a store
     project still gets no ruleset". The starter's paths are checked against
     the store's lock first, with no session: a held or queued unit leaves the
     starter unwritten, with a note naming the holder.
   - **Unchanged.** An in-repo project's location is ``repo`` and both of its
     trees are the tree in hand, so invariant and Heap resolve, render and
     gate exactly as before; invariant's pin stays ``02ed45e4db9d``.

The proof is ``tests/test_adr_014_guidelines_store.py``, on 4b's two toy
repositories linked to one store, widgets keeping its ruleset and baseline in
the store. A baseline raised under the lock and committed in the store is
refused, naming the span's commit; one pruned by ``guidelines baseline
--prune``, which takes the unit on access, is accepted as shrinking. Also: a
session with no span compared against the store's HEAD; growth committed
before the first span read as the base; the first span's commit kept as the
base after a unit is taken again; a store manifest absent at the base
failing; a repository rename followed; the ruleset read from the store and
the pin store-relative; the change scanned in the repository with the store
baseline holding its finding; ``guidelines render`` writing the checklist into
the store under a grant, then current; an edit advised against the store
ruleset; a write into a unit another session holds refused naming ``docs
lock``, a dry run taking no unit, a write with no session admitted only into a
free unit, and a repository path not governed; ``Manifest.location`` for a
store and an in-repo project; ``init`` writing a store project's ruleset in
the store, where ``guidelines.ruleset_valid`` reads it; and this repository's
pin unchanged. ``tests/test_adr_014_locate.py`` keeps the pointer-only
refusal.

As built: increment 5a (2026-11-15)
-----------------------------------

D7's sync, as the architecture docs describe it (``manifest.rst``, "A
store's sync" and "Finding the project"; ``cli.rst``, ``store pull``,
``store clone``, ``session start``, ``session resume``, ``session status``
and ``docs lock``; ``overview.rst``, ``core/storesync``, ``core/git``,
``core/stores`` and ``domains/session``; ``state_model.rst``, the record's
``store_pull`` and "User-level state"). Where the build settles what the text
left open:

.. dec:: ADR-014 increment 5 is split; 5a as built: the store's sync, its pull, its replay and its first clone
   :id: DEC_ADR_014_I5A
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_CORE_STORESYNC, COMP_CORE_GIT, COMP_CORE_STORES, COMP_CORE_MANIFEST, COMP_DOMAINS_SESSION, COMP_CMD_SESSION, COMP_CLI_MAIN

   - **The split.** Increment 5 was about twice the size of each 4x slice,
     so it is built as 5a and 5b (the increments table above). Decided
     2026-11-15 by the parent session, choosing the sync-first order: 5a is
     D7, sync; 5b is D6, landing. D6's release fetches, reads the store's
     state and stops on "the same same-file check" D7 names, so 5a builds
     each once and 5b adds no git machinery.
   - **Sync has its own owner.** ``core/storesync`` holds the settings, the
     pull and its states, the replay, the same-file check and the clone's
     review, because ``core/stores`` never clones, pulls or pushes a store.
     ``core/git`` gains ``fetch``, ``clone``, ``fast_forward`` and
     ``rebase`` as named writes, as ``commit_only`` is one. The calls that
     reach a remote run with ``GIT_TERMINAL_PROMPT=0``, a batch-mode ssh and
     the time limit, so a prompt or a hung remote reads as unreachable. Only
     the CLI calls it: a hook never pulls.
   - **The settings resolve in D3's order, in a store project only.** The
     user's defaults sit below the store's level, as every 5a value does, so
     "overridable per user" means a machine's default that the store did not
     set. The ruled defaults are layered, labelled ``default``, only when the
     project has a store. An in-repo project carries no ``sync`` block, and
     its own manifest setting one is refused, since it has no store to pull.
   - **``push_after_landing: true`` is refused** at load until increment 6.
     D7 pushes "after D8's scan", and D8 is increment 6: a push with nothing
     screening it cannot ship, the same reasoning as D9's interim refusal.
   - **The states.** Behind fast-forwards; ahead carries on; offline warns
     and the session records ``store_pull: stale``, which ``session status``
     shows, or stops under ``sync.offline: stop``; diverged stops. A stop at
     session start registers no session, at resume moves no binding, and at
     ``docs lock`` takes no unit; exit 2 in each. Two cases the text did not
     name: a remote with no copy of the branch yet carries on as ahead, and
     a store with no remote pulls nothing and says so.
   - **Behind over an uncommitted change it would move stops.** When the
     incoming commits touch a path that has uncommitted changes in the
     store's main checkout, the fast-forward is not attempted, and the
     command stops naming the paths. Ruling 9 says never resolve by
     yourself, and a holder's half-written edit is not invariant's to
     overwrite.
   - **``store pull``** pulls now, whatever ``pull_at_start`` says, and
     exits 1 when it could not. **``--replay``** replays the unpublished
     commits on top of the remote. It stops, moving nothing, when a file
     changed on both sides since the merge base, printing both diffs. It
     also stops when the main checkout has uncommitted changes, and **while
     any unit of the store's docs lock is held**, naming each with its
     session: a replay rewrites the commits a span's shas name (D9's store
     leg), so a held unit's span would read another machine's commits as its
     own.
   - **A pointer naming a remote** is found through the user level's entry
     for the repository's identity, which ``store clone`` registers. With
     none, the lookup stops naming ``invariant store clone <remote>``. A
     store a path names and that is absent names the command with
     ``--path``. Nothing clones implicitly (ruling 18).
   - **``store clone``** stages the clone beside its target and shows each
     ``commands.*`` key with its provenance label, the lanes and the
     repositories. It asks at a terminal, or takes ``--yes``. A run with no
     terminal and no ``--yes`` is declined: the staging copy is removed and
     nothing is registered, because confirming by default would make ruling
     18's review a formality. Registration is all or nothing, and an identity
     already linked to another store refuses the clone before anything moves.
     The folder follows ``store_naming``; a remote naming no host (a local
     path, ``file://``) has no URL form and takes the repo form.
   - **Not built here.** Ruling 22, a pulled store that changes a declared
     command named at session start, is not in 5a's row. It is owed in 5b
     (its row above, ruled 2026-11-15), and the pull's ``before`` and
     ``after`` shas are the seam it reads.
   - **Unchanged.** An in-repo project resolves, starts, resumes and locks
     exactly as before: no ``sync`` block, no pull, and ``store_pull`` null on
     its records.

The proof is ``tests/test_adr_014_sync.py``. It uses a toy repository linked
to a store published to a local bare remote, and a colleague's clone of that
remote; offline is a remote path that does not exist, so nothing reaches a
network. At session start: behind fast-forwards and says so, ahead carries
on, in step says so, offline warns and records ``store_pull: stale``, which
``session status`` names, and offline under ``sync.offline: stop`` stops
registering nothing. Diverged stops, naming ``store pull --replay``, with no
session registered and no branch moved. Also: pull at start off fetches
nothing; a store with no remote pulls nothing; behind over an uncommitted
change on an incoming path stops, naming it; resume and ``docs lock`` pull
first, and a divergence refuses each; ``store pull`` clears a stale record;
``--replay`` puts an unpublished commit on top of the remote, stops on the
same file showing both diffs, and is refused while a unit is held; the
same-file check names only files changed on both sides. On a fresh
``INVARIANT_HOME``, a pointer to an absent remote store stops session start
with ``invariant store clone <remote>``, and a pointer to an absent path names
the command with ``--path``. ``store clone --yes`` shows each declared
command with its label, the lane and the repositories, registers them, and
the pointer then resolves and a session starts. A declined clone, with no
terminal or answered no, registers nothing and leaves no staging copy. A
clone of no store, of an unreachable remote, or of one whose repository is
linked elsewhere is refused, and so is a taken name. The settings: the ruled
defaults in a store project, none in an in-repo one, an in-repo manifest
setting ``sync`` refused, the store's level winning over a user default, and
each bad value refused. ``tests/test_adr_014_locate.py`` now expects the clone
command where it expected increment 5's refusal.

As built: increment 5b (2026-11-15)
-----------------------------------

D6's landing and ruling 22, as the architecture docs describe them
(``cli.rst``, ``docs unlock``, ``session start``, ``session status``,
``session transition``, ``session merge``, ``session resume`` and
``session teardown``; ``state_model.rst``, the record's ``landing`` and
"Domain: session"; ``overview.rst``, ``domains/session``, ``core/git``,
``core/doclock`` and ``core/storesync``). It adds no git machinery: the
release is 5a's pull with ``--replay``. Where the build settles what the
text left open:

.. dec:: ADR-014 increment 5b as built: the docs land at release, the repo at merge, and a half-landed session is finished or held
   :id: DEC_ADR_014_I5B
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_DOMAINS_SESSION, COMP_CMD_SESSION, COMP_CORE_GIT, COMP_CORE_DOCLOCK, COMP_CORE_STORESYNC, COMP_CLI_MAIN

   - **The release is 5a's replay.** ``docs unlock`` first asks which held
     units it would release and refuses a dirty one before anything reaches
     the remote (``DocsLock.releasable``). When it releases any, it pulls
     the store as ``store pull --replay`` does: with pull on and a remote, a
     fetch; behind fast-forwards; past a remote that moved, the unpublished
     commits are replayed on top, unless a file changed on both sides since
     the merge base, which stops the release with both diffs and every unit
     still held (exit 2). The merge base is at or before the span's start,
     so the check is at least as strict as D6's "since the span's start".
     Offline warns and releases (``store_pull: stale``), or stops under
     ``sync.offline: stop``. Releasing only a unit the session queues for
     pulls nothing.
   - **The release replays over units other sessions hold.** A release
     never waits on another session (D5), and refusing it while another
     session holds a unit would let two sessions each block the other's
     release. ``store pull --replay``, which an operator runs by choice,
     keeps 5a's refusal. A held unit's span then also covers the remote's
     commits in its unit, which ruling 9's check found to touch no file
     changed here. The replay rewrites the holder's unpublished commits,
     its span's ``acquired_at_sha`` among them; the old commits stay in the
     store's object database, so its span, its store leg and its base
     still read, and its own release lands its own commit. A held unit's
     uncommitted edit stops the release instead, since the replay needs a
     clean main checkout.
   - **The landing record** is ``landing: {store, repo, failed}``, kept in a
     store project only; an in-repo session's stays null, and its merge,
     failed or not, records nothing new. ``store`` is the store's sha at the
     release that left the session holding no unit, cleared when it takes a
     unit again (D5's re-read rule makes those docs unlanded until the next
     release). ``repo`` is the base's sha after the merge. ``failed`` is
     ``{at, detail, head, base}`` of the last repo merge that failed,
     cleared when the merge lands.
   - **Half-landed is "docs landed, code pending"**, as the Consequences
     section reads it: ``session status`` says *docs landed, code pending*
     while the store leg has landed and the repo has not, and *half-landed*,
     naming the store's sha and git's reason, once a repo merge failed. A
     store session with no docs landed whose merge failed is named as such,
     not as half-landed.
   - **``session merge`` lands the store first.** Units still held at the
     merge are released through the same release, and a stop there merges
     nothing. A repo merge that fails is recorded before the command exits,
     naming ``session merge`` and ``session resume``.
   - **Finishing.** ``session merge``, or ``session resume`` after the
     binding has moved, finishes a session with a failed merge. The record
     is checked against git first: the store must still contain
     ``landing.store`` (``git.is_ancestor``), else the finish stops with the
     recorded sha and the store's HEAD, and ``session merge --force``
     finishes it anyway with the override recorded (rule
     ``session.landing``). A branch the base already contains, merged by
     hand, is recorded without merging again. Resume prints its binding line
     first in every case, and exits 1 when the finish stops, the session
     still half-landed.
   - **Abandon is held while the docs landed and the code did not**, both
     states above. ``session transition abandoned`` is refused (exit 2)
     naming the store commits that would stay; ``--force --reason``
     abandons it, recording the commits in an override and printing them.
     ``session reap`` and ``session repair`` skip such a session, since they
     abandon without being asked. Nothing is rolled back (ruling 8).
   - **Ruling 22 compares the resolved values.** Session start and resume
     read every ``commands.*`` value the project resolves before the pull
     and again after a pull that moved the store, and name each that
     differs, old and new, in the output and as ``commands_changed`` under
     ``--json``. A store value that the repository's own level overrides
     changes nothing that runs and is not named. Nothing blocks. The turn
     context is not extended: the command's output is what the agent that
     ran it reads.
   - **Unchanged.** An in-repo project starts, releases nothing, merges,
     fails a merge, resumes and abandons exactly as before, and its records
     carry ``landing: null``.

The proof is ``tests/test_adr_014_landing.py``, on a toy repository linked to
a store published to a local bare remote, with a colleague's clone. A release
over a remote that moved replays the span's commit on top and records
``landing.store``; one over the same file stops showing both diffs, the unit
kept and nothing landed; a release with nothing written fast-forwards and
lands. Also: offline warns and releases, or stops under ``sync.offline:
stop``; pull off fetches nothing; no remote releases as before; a dirty unit
is refused before any fetch; another session's held unit does not stop the
replay; a queued want pulls nothing; a unit taken again clears the landing;
one of two units released lands nothing. A merge records both legs; a merge
forced to fail by a file in its way leaves the session half-landed, which
``session status`` names; ``session resume`` finishes it (the forced repo-leg
failure of the increment's row), and so does ``session merge``; a finish that
fails again exits 1 still half-landed; a store whose landed commit was
rewritten stops the finish with both shas until ``merge --force``; a branch
merged by hand is recorded without merging again; units still held land the
store first at merge, and a conflict there merges nothing. Abandon is refused
naming the store commits and forced naming them again; reap and repair hold
the session. Session start names a command the colleague's pushed change set,
old and new, and resume does too; a pull that changes no command names none;
an in-repo start names none and its failed merge records nothing.

As built: increment 6 (2026-11-15)
----------------------------------

D8's privacy screen, as the architecture docs describe it (``manifest.rst``,
"A store's privacy screen" and "A store's sync"; ``cli.rst``, ``store push``,
``store hook``, ``store init``, ``store link``, ``session start``, ``session
merge`` and ``doctor``; ``overview.rst``, ``core/storescan``,
``core/storesync`` and ``core/git``). It lifts 5a's interim refusal of
``sync.push_after_landing: true`` (:need:`DEC_ADR_014_I5A`). Where the build
settles what the text left open:

.. dec:: ADR-014 increment 6 as built: every store commit screened at the push, the commit and the edit
   :id: DEC_ADR_014_I6
   :status: accepted
   :part_of: DEC_ADR_014
   :affects: COMP_CORE_STORESCAN, COMP_CORE_STORESYNC, COMP_CORE_GIT, COMP_CORE_MANIFEST, COMP_CORE_RULES, COMP_CMD_SETUP, COMP_CMD_SESSION, COMP_CLI_MAIN

   - **The screen has its own owner.** ``core/storescan`` reads the store's
     block and scans; ``core/storesync`` pushes, and pushes only what the
     screen passed. The screen reaches no network, so the git hooks may call
     it; ``storesync`` still never runs in a hook. ``core/git`` gains
     ``push`` as a named write, called only after the screen.
   - **The store's block is its top level.** The screen reads ``privacy`` at
     the top of the store's ``invariant.yaml`` as the judged commit holds it
     (the index, for the commit hooks; the working tree, at the edit and the
     review). ``scope`` defaults to ``all``; a literal entry is a
     store-relative prefix, and ``docs``, ``memory`` and ``reports`` resolve
     through the store-relative ``paths.*`` of its top level and its
     repository blocks. Content is narrowed by the scope; **a commit's
     message is always read**, since D8 names content and message both.
     ``privacy.enabled`` does not turn the store's screen off: ruling 10's
     key is the one relaxation. A block that does not load at a commit, and
     any answer git cannot give, refuses.
   - **``privacy.store`` is the store's top level's alone.** Set at any other
     level (a repository's block in the store, an in-repo manifest, the
     user level) it is refused at load, naming the level, as is a value
     that is not ``{scan: <bool>}``.
   - **The relaxation's reach.** ``privacy.store.scan: false``, honoured only
     while ``git remote`` lists none, turns off the commit hooks and
     ``privacy.clean``'s reading of store paths (edit, commit, review), the
     verdict saying so. A push always scans: a push names a destination even
     with no remote configured. While the key is present a push scans the
     whole history, not just the unpublished commits (D8 item 3), and
     ``doctor``'s ``store privacy`` row warns while it is honoured and fails
     once a remote exists, until the key is removed.
   - **What a push publishes.** ``store push`` and push after landing push
     ``HEAD`` to the branch it tracks, the commits being those the remote's
     tracking refs do not reach, so the first push after linking a remote
     reads the whole unpublished history. The ``pre-push`` hook reads git's
     ref lines: a named remote leaves out what its tracking refs reach, a
     push to a URL only the remote's sha; a deletion publishes nothing.
   - **The hooks.** ``pre-commit``, ``commit-msg`` and ``pre-push`` are written
     to ``.git/invariant/hooks`` in the store's common directory, and its
     local ``core.hooksPath`` names that directory, so a global hooks path
     cannot skip them and a linked worktree fires them. Each runs the build
     that installed it (the interpreter and the package's directory), else
     an ``invariant`` on ``PATH``, else refuses: the screen fails closed.
     Then it runs the store's own hook of the same name in ``.git/hooks``, so
     setting ``core.hooksPath`` costs a store none of its hooks. A local
     ``core.hooksPath`` already naming somewhere else is never overwritten;
     the install says so and ``doctor`` fails. They are installed or repaired
     by ``store init``, ``store link`` (also when already linked), ``store
     clone``, ``store push`` and ``session start`` in a store project.
     ``--no-verify`` skips the commit hooks; every push still screens.
   - **The routes.** ``privacy.clean`` spells a path in the project's store,
     or in any store a path lies in (``store_root_of``), against that store
     and reads it with that store's block: an edit into a store doc, a
     commit made in a store (no longer foreign), its message, and the store
     leg at the review boundary. The repository's own scope no longer
     decides which store paths are read.
   - **``store push`` from inside the store.** Run with no repository around
     it, the store's own level gives the ``sync`` settings. Exit 0 when
     pushed or in step; 1 with no remote, refused, or a push git did not
     make; 2 in an in-repo project.
   - **Push after landing** runs at the end of every ``session merge`` that
     reaches ``merged``, the forced one included, since the screen is what
     guards it. It is not part of the landing: refused or failed, the
     session stays ``merged``, the merge exits 0, and the output names
     ``invariant store push``.
   - **Unchanged.** An in-repo project has no store: no hooks, no push, no
     ``store privacy`` row, and ``privacy.clean`` reads its paths exactly as
     before.

The proof is ``tests/test_adr_014_push_scan.py``, on a toy repository linked
to a store published to a local bare remote. A home path committed past the
commit hooks is refused at push from a host (``invariant store push`` from the
repository, naming the commit with the text redacted, and push after landing,
the session staying ``merged`` and the output naming ``store push``), from a
shell (a plain ``git push`` in the store, through its ``pre-push`` hook) and
from inside the store (``invariant store push`` run there); a home path in a
message is refused too, and a clean push publishes. Also: the first push
after linking a remote reading the whole unpublished history; ``pre-commit``
and ``commit-msg`` refusing a home path, from a linked worktree too and under
a global hooks path; the store's own hook still running; a missing hook
repaired by session start and by ``store link``; a local hooks path elsewhere
named, not overwritten; no invariant reachable failing closed; the default
scope ``all``, a narrower scope leaving a path unread; an allow entry counted
from its own commit and not for an earlier one; a block that does not load at
a commit refusing; the relaxation honoured without a remote, named by
``doctor``, ignored once a remote exists with ``doctor`` failing, and a push
while it is present reading the whole history; ``privacy.clean`` reading an
edit, a commit and its message in the store with the store's block; and each
bad ``privacy.store`` refused at load. ``tests/test_adr_014_per_leg_gates.py``
now reads the store leg with the store's scope, and
``tests/test_adr_014_sync.py`` no longer expects 5a's refusal.

As built: increment 7a (2026-11-15)
-----------------------------------

D12's ``--to store`` with ADR-020 D3, as the architecture docs describe it
(``cli.rst``, ``store migrate``; ``manifest.rst``, "Records and their
locations" and "Finding the project"; ``overview.rst``, ``core/storemigrate``,
``cli/migrate``, ``core/git``, ``core/storesync``, ``domains/session`` and
``hosts/claude_code``; ``state_model.rst``, the record's ``migrate``). Where the
build settles what the text left open:

.. dec:: ADR-014 increment 7 is split; 7a as built: store migrate --to store, as a session the command starts
   :id: DEC_ADR_014_I7A
   :status: accepted
   :part_of: DEC_ADR_014
   :amends: DEC_ADR_014_D12
   :affects: COMP_CORE_STOREMIGRATE, COMP_CLI_MIGRATE, COMP_CORE_GIT, COMP_CORE_STORESYNC, COMP_CORE_MANIFEST, COMP_DOMAINS_SESSION, COMP_HOSTS_CLAUDE_CODE, COMP_CMD_SETUP

   - **The split.** Increment 7, with ADR-020 increment 2, was the largest
     remaining increment, so it is built as 7a and 7b (the increments table
     above). Decided 2026-11-15 by the parent session, on this session's
     proposal: 7a is the shared machinery and ``--to store``, with the
     refusals that hold in either direction; 7b is ``--to repo``, the
     half-landed main checkout finished by resume, the refusals of that
     direction (the root ``conf.py`` one among them) and ADR-020 increment 2's
     record. Until then ``--to repo`` and ``--records`` are refused, exit 2.
   - **The lane is a session the command starts.** ``store migrate`` starts
     its own session, parked as ``docs assess --plan`` parks its plan (no host
     bound, its output never printing ``session <id> (``), isolates it, and
     starts a ``chore`` pipeline on it: a move has no design content. The
     repository leg is one commit on its branch, reviewed and landed by
     ``session merge`` like any change; ``session resume <name>`` picks it up.
     The worktree resolves as a store project, so ``docs.builds_strict`` and
     ``docs.needs_traceable`` read the session's store leg
     (:need:`DEC_ADR_014_B123`).
   - **The store leg lands first, at once** (ruling 8). The block is written
     in the store's ``invariant.yaml``; a new store is created and committed
     straight away, since a store with no commit gives the docs lock no sha
     to open a span at. The moved paths' history is carried with ``git
     fast-export <HEAD> -- <roots>``, the stream re-aimed at a temporary ref
     on the store's HEAD, every ``data <n>`` length honoured so a blob that
     reads ``reset ...`` stays a blob, imported, fast-forwarded onto and the
     ref deleted. It is written under the session's docs-lock grants, read as
     the session's worktree now resolves (a store project), and the units are
     released at once, which sets ``landing.store``; a move that wrote no
     policy path takes no unit, and ``landing.store`` is the store's HEAD.
   - **The link is written at once.** The in-repo manifest is found first
     until the merge (D2), so nothing resolves differently in the main
     checkout, while the session's worktree, which no longer carries it,
     resolves the store.
   - **Wiring converts after the repository leg lands, never before.**
     Before the merge the committed ``.claude/settings.json`` and a new
     ``settings.local.json`` would both fire. The repository leg removes the
     committed wiring (each shim, this library's settings entries, the
     settings file when nothing else is left in it, and each library agent as
     installed; an agent edited since is the project's work, stays committed
     and is named); once ``session merge`` or ``session resume`` lands it,
     ``cli/migrate.finish`` installs the same kinds as ``local``.
   - **A migrate session keeps its landing record** and a ``migrate`` field,
     whichever home the tree in hand resolves to: the main checkout reads
     ``.claude/invariant.yaml`` until the merge, so the landing record, kept
     for a store project only since 5b, would otherwise never say the move
     is half-landed. A failed repository merge is recorded and finished by
     ``session merge`` or ``session resume``; abandon is held while the store
     leg has landed, as for any session.
   - **A state-root marker names the moving session.** ``<state
     root>/migrate.json`` is written with the ``migrate`` field and removed
     when the move finishes or is abandoned. ``Manifest.locate``, refusing a
     store that does not list the repository, adds the session and the
     commands that finish it; ``core/manifest`` imports nothing of a domain
     for it.
   - **What moves.** Every data and policy record becomes ``store`` and keeps
     its path keys' spelling, now store-relative; the docs project's folder
     moves whole. A record whose path key is absolute or climbs out of the
     repository stays ``repo``, named, since a store path must be
     store-relative and nothing of it is in the repository.
     ``paths.memory_index`` is a name inside ``paths.memory``. ``hooks`` and
     ``agents`` become ``local`` (ADR-020 D1). The block is every key
     ``.claude/invariant.yaml`` sets but ``version``, checked to resolve from
     the store before anything is written. Ignored files under a moved folder
     stay in the working tree and are named.
   - **The refusals beyond D3's list** (exit 2, nothing written): a target
     path already in the store (never written over, never merged, as D3
     says of ``--to repo``); a store ``invariant.yaml`` holding comments a
     rewrite would drop, or with uncommitted changes; the repository linked
     to another store; a named folder that is no store and not empty; a
     detached head; a run from a linked worktree. A store behind or diverged
     is read by fetching and comparing (``storesync.standing``), and nothing
     is pulled; offline warns, or refuses under ``sync.offline: stop``.
   - **Unchanged.** Without the command, nothing changes for an in-repo
     project or a store project: no ``migrate`` field, no marker, and the
     landing record kept for a store project only, as before.

The proof is ``tests/test_adr_014_migrate.py``, on a toy in-repo project
written by ``invariant init --host-install`` with its wiring committed and a
docs history with a side branch merged in. ``store migrate --to store``
creates the store with the same relative paths and the same file bytes, ``git
log -- <path>`` showing the original commits, the merged side note among
them; links the repository at once while the main checkout still reads
``repo:``; parks and isolates the session with ``docs_landed`` and the
marker; and commits the repository leg, after which the worktree resolves
``store:``. After ``session merge`` the main checkout resolves the same
values, labelled ``store:``; the wiring is local, in
``.claude/settings.local.json`` and ``.git/info/exclude``; and ``git status``
is clean in the repository and in the store. Also: a named existing store
keeps its other repository and its own level; a failed repository merge is
half-landed and finished by ``session resume``; a project with no wiring
gets none; an edited agent stays committed and named; an absolute memory
path keeps ``memory`` in the repository; a folder named with ``--store`` and
absent becomes the store; a failure part way names what was done. Each
refusal exits 2 with the repository, the user level, the stores, the
sessions and the marker as they were: below the top level, outside a project,
from a linked worktree, a store project, the manifest changed or not
committed, a detached head, a moved path or the committed wiring changed, a
store already listing the repository (named and default), the repository
linked elsewhere, a target path in the store, a commented or uncommitted
store file, a folder that is no store, a ``docs/`` docs project, a store
behind and then diverged, a unit another session holds, and ``--to repo`` and
``--records``. The stream rewriter keeps a blob that begins with ``reset`` and
refuses a stream cut short; the transfer carries a file that begins with
``reset`` with its two commits, leaves nothing outside its paths, and moves
nothing when a file is in its way. The marker names its session in the
refusal and is cleared by an abandon.

As built: the BUG_123 fix (2026-11-15)
--------------------------------------

D9's store leg for the docs project's two gates, as the architecture docs
describe it (``state_model.rst``, the docs project's strict build and the
two legs; ``overview.rst``, ``core/needs`` and ``core/rules``; ``cli.rst``,
``store migrate``). Increment 4c-1 moved ``docs.updated`` and
``docs.diagrams_current`` onto the doc leg; ``docs.builds_strict`` and
``docs.needs_traceable``, written for ADR-018 increment 3 before that leg
existed, still refused every store-project session with a non-empty
repository changed set as "not established". Where the fix settles what the
text left open:

.. dec:: The docs project's gates read the doc leg, and the build record stays in the project's state root
   :id: DEC_ADR_014_B123
   :status: accepted
   :part_of: DEC_ADR_014
   :amends: DEC_ADR_014_I7A
   :affects: COMP_CORE_RULES, COMP_CORE_NEEDS, COMP_DOMAINS_DOCS, COMP_CLI_MIGRATE

   - **The doc leg, picked as ``docs.diagrams_current`` picks it.** Under
     ``records.docs: store`` both gates read the store leg: the commits
     inside the session's docs-lock spans, kept to each span's unit, and a
     held unit's uncommitted paths. An empty store leg is N/A, and so is one
     that touches neither the docs project's source directory nor
     ``docs.needs``, so a code-only session advances without ``--force``.
     Only a store leg git cannot read fails, naming why, with the verdict
     ``docs.updated`` gives the same leg. An in-repo project reads the
     repository leg exactly as before.
   - **The source directory is found in the store's main checkout.**
     ``core/needs.srcdir_of`` reads ``docs.needs.srcdir``, or its default
     rule against ``paths.docs``, against the store root whichever tree is
     asked (``docs_store_root``), since a store has no worktree per session
     (D5). Before, a store project with no ``docs.needs.srcdir`` had no docs
     project at all, so ``invariant docs build`` refused there too.
   - **The record for a store tree's digest is the project's.** ``invariant
     docs build`` run from the repository writes the record under the
     project's state root, the repository's git common directory, and both
     gates, ``docs.decisions_recorded`` and ``items.decisions_linked`` read
     it there. Not the store's: a digest names the source tree's bytes, so
     where the record is kept changes nothing it proves, and keeping it with
     the project needs no write from a repository into the store's git
     directory, which every repository linked to the store shares. The cost
     is that two repositories sharing one docs project each build it once.
   - **Diagnostics are spelled from the store.** A store docs project's
     build names each file from the store's checkout (``docs/...:4``), never
     the machine's path to it.
   - **Retired with it.** ``store migrate``'s advice to pass the two gates
     with ``pipeline advance committed --force --reason``, and the known
     limit :need:`DEC_ADR_014_I7A` and ``cli.rst`` recorded: the migrate
     session's moved docs project is judged against its own build.

The proof is ``tests/test_bug_123_docs_build_store_leg.py``, on 4b's two toy
repositories linked to one store. The reproducer's test, unchanged: a
code-only session with an empty, readable store leg is N/A for both gates.
Then, with a docs project on sphinx in the store: the source directory
resolves in the store from either tree, from ``paths.docs`` alone too; a doc
committed under the lock with no build recorded fails both gates naming
``invariant docs build``; a green build, recorded in the widgets
repository's state root and not the store's, passes both; a red one fails
``docs.builds_strict`` naming the file and line spelled from the store, and
``docs.needs_traceable`` for want of a green build; a record for the tree
before a later commit does not count; and an unreadable span fails both
naming why. Where ``INVARIANT_TEST_SPHINX_BUILD`` names a ``sphinx-build``
with sphinx-needs, a real strict build of the store's docs project passes
and then fails on a broken reference. ``tests/test_adr_014_migrate.py`` now
asserts the override advice is gone.

As built: increment 7b (2026-11-16)
-----------------------------------

D12's ``--to repo`` with ADR-020 D3, as the architecture docs describe it
(``cli.rst``, ``store migrate``, ``session merge`` and ``session resume``;
``manifest.rst``, "Records and their locations" and "Finding the project";
``overview.rst``, ``core/storemigrate``, ``cli/migrate``, ``core/stores``,
``core/git``, ``adapters/sphinx`` and ``hosts/claude_code``;
``state_model.rst``, the record's ``migrate``). It records ADR-020's
increment 2 with 7a (that record's "As built: increment 2"). Where the build
settles what the text left open:

.. dec:: ADR-014 increment 7b as built: store migrate --to repo, finished from the main checkout
   :id: DEC_ADR_014_I7B
   :status: accepted
   :part_of: DEC_ADR_014
   :amends: DEC_ADR_014_I7A
   :affects: COMP_CORE_STOREMIGRATE, COMP_CLI_MIGRATE, COMP_CORE_STORES, COMP_CORE_GIT, COMP_ADAPTERS_SPHINX, COMP_HOSTS_CLAUDE_CODE, COMP_DOMAINS_SESSION, COMP_CLI_MAIN

   - **The same lane, the other way.** ``--to repo`` starts its own parked
     ``chore``-lane session, isolates it and starts its pipeline, as
     ``--to store`` does (:need:`DEC_ADR_014_I7A`). The repository leg is
     laid first on the session's branch, while the store still holds the
     records: their history carried from the store with ``git
     fast-export`` (7a's ``transfer``, the original commits, the same
     relative paths), ``.claude/invariant.yaml`` written, a committed
     pointer removed and the wiring in its committed form, then one commit.
     The store leg follows and lands at once (ruling 8): this repository's
     block and its records' tracked files removed and committed in the
     store, under the session's docs-lock grants taken as the main checkout
     still resolves it, then released, which sets ``landing.store``.
   - **The keys materialised.** ``.claude/invariant.yaml`` is the store's own
     level overlaid by this repository's block, as ``Manifest.location``'s
     ``level`` reads it, less ``store``, ``records`` (each record is then
     ``repo``, the load-time default) and the keys an in-repo manifest
     refuses at load: ``sync`` (5a) and ``privacy.store`` (6). Those stay
     behind and are named (``dropped``), rather than writing a manifest that
     cannot load. The output names each key copied from the store's own
     level and not set by the block (``from_store_level``). The user level's
     ``defaults`` are the operator's and are not copied. The manifest is
     checked to load in the repository before anything is written.
   - **The store's own level is never changed.** Only this repository's
     block leaves ``store.repos``, which may be left empty: a store serving
     no repository is kept and the output says so (``serves_none``). Only
     tracked files leave the store; an ignored one under a moved folder
     stays in its working tree and is named (``left``), as 7a leaves them in
     the repository.
   - **Mid-move, the main checkout finds neither home** (ADR-020 D3): the
     store no longer lists the repository, and the manifest lands with the
     merge. Every command there refuses, naming the session and its
     worktree, which the marker now carries. The two that finish the move,
     ``session merge`` and ``session resume``, run there all the same: they
     read the project from the worktree the marker names, and the merge
     runs from the main checkout as ``session.ff_merge_target`` requires.
     Nothing else resolves through the marker, so the refusal stays the
     rule. A failed repository merge is half-landed, recorded on the landing
     record as 7a's is, and finished by either command.
   - **Wiring becomes committed through the same install plan.** In the
     session's worktree, whose manifest now wires ``repo``, ``hooks
     install`` and ``agents install``'s own plan writes
     ``.claude/settings.json``, the shims and the agents
     (``ClaudeCodeHost.commit_local``), committed with ``git add --force``,
     since the main checkout's ``.git/info/exclude`` still lists them. A
     fast-forward replaces an ignored file at a path the branch adds (run:
     git 2.50 overwrote one without a word), so a file edited in the main
     checkout since the local install is committed as edited, and named
     (``edited``), never lost. Once the repository leg lands,
     ``retire_local`` removes the local install's settings entries and
     granted directories from ``.claude/settings.local.json`` (the file too
     when invariant created it and nothing else is left), its exclude lines
     and its receipt; the files stay, tracked. A project with no local
     install gets no wiring.
   - **The link is removed after the merge, never before.** ``unlink_repo``
     removes the user level's entry for the store's key and the
     repository's identity, only where it names that store, every other key
     kept and an emptied ``repos`` dropped. ``invariant.id`` stays in the
     local git config (D4 writes it whatever the home).
   - **The refusals beyond D3's list** (exit 2, nothing written): ``--store``
     (it names a store ``--to store`` moves into); a project its pointer
     alone defines; an uncommitted pointer, which the in-repo manifest
     would leave stale; a store ``invariant.yaml`` holding comments the
     rewrite would drop, or with uncommitted changes; a wiring path in the
     main checkout that git neither tracks nor ignores, where the merge
     would stop; and a record another repository of the store also names,
     whose files moving out would take that repository's records too. D3's
     root ``conf.py`` refusal reads every ``conf.py`` at or above the docs
     project's folder, the root's among them, through the ``sphinx``
     adapter's ``conf_excludes`` (literal ``exclude_patterns``, read with
     Sphinx's globbing), handed in by the CLI; one it cannot read refuses
     too, naming the line to add. D8's content-folder refusal holds in this
     direction as well. A target path already in the repository is any
     file, tracked, untracked or ignored, under a root the move adds, and
     ``.claude/invariant.yaml``.
   - **``--records`` is not 7b's.** Neither this record's 7b row nor
     ADR-020's increment 2 row names it; ADR-020 D3 keeps it "for moving
     single records within a store project", and no increment builds that.
     It is refused in either direction, exit 2, naming that, and the
     increments table carries no row for it yet.
   - **Unchanged.** Without the command, an in-repo project and a store
     project resolve, merge and resume exactly as before: the marker is
     read only when a ``--to repo`` move names a worktree and the main
     checkout does not resolve, and ``commit_only`` adds with force only
     when asked.

The proof is ``tests/test_adr_014_migrate_to_repo.py``, on 7a's toy in-repo
project written by ``invariant init --host-install`` with its wiring
committed. The round trip: ``store migrate --to store``, landed, resolves the
same values labelled ``store:`` with the wiring local; ``store migrate --to
repo`` removes the block and the records from the store (its own level
unchanged, kept though it serves no repository), leaves the main checkout
refusing and naming the session and its worktree, and lays the records with
the same bytes at the same paths and their history, the materialised
manifest and the committed wiring on the session's branch, where the
worktree resolves ``repo:``; merged from the main checkout, the project
resolves the same values as before the round trip, labelled ``repo:``, the
wiring committed with no settings entry, exclude line or receipt left, the
user level's link and the marker gone, and ``git status`` clean in the
repository and the store. Also: a failed repository merge half-landed and
finished by ``session resume`` run in the main checkout; an edited local
agent committed as edited; a project with no wiring given none; a shared
store keeping its other repository and its own level, a key copied from that
level named; the pointer removed with the move; ``sync`` and
``privacy.store`` left behind and named; a failure part way naming what was
done; an abandon clearing the marker. Each refusal exits 2 with the
repository, the user level, the stores, the sessions and the marker as they
were: an in-repo project, outside a project, below the top level, from a
linked worktree, a detached head, a target path in the repository, a wiring
path git would not overwrite, an uncommitted store path, a commented and then
an uncommitted store file, a store behind its remote, a unit another session
holds, a record another repository shares, a root ``conf.py`` that does not
exclude the docs project (and one that does, accepted) or cannot be read to
tell, a ``docs/`` docs project, ``--store``, a project its pointer alone
defines, an uncommitted pointer, and ``--records`` in either direction. ``conf_excludes``,
``unlink_repo`` and the marker's note are tested on their own.
``tests/test_adr_014_migrate.py`` now expects ``--to repo`` in an in-repo
project to refuse as one.
