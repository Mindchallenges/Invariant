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
| 4c        | D9: ``_changed_set`` per leg,   | A ``conf.py`` change held  |
|           | ``docs.updated`` pairing a span | by ``docs.updated`` until  |
|           | with its read,                  | a doc is committed under a |
|           | ``docs.diagrams_current`` and   | span that also holds the   |
|           | privacy per leg, the manifest's | read. A grown store        |
|           | location a property, the shrink | baseline refused.          |
|           | gate reading the store's base,  |                            |
|           | ``store.docs_lock_released`` on |                            |
|           | design states, both guidelines  |                            |
|           | refusals lifted, the store      |                            |
|           | starter writing a ruleset, the  |                            |
|           | library feature-architect's doc |                            |
|           | update moved to the end of      |                            |
|           | design under a store docs lock, |                            |
|           | invariant's copy re-rendered.   |                            |
+-----------+---------------------------------+----------------------------+
| 5         | D6 and D7: release with fetch   | A forced repo-leg failure  |
|           | and the same-file check, the    | finished by resume. A      |
|           | landing record, half-landed     | same-file conflict at      |
|           | resume, sync settings,          | release shown, with the    |
|           | ``store pull --replay``, the    | units kept. Offline,       |
|           | explicit ``store clone``.       | behind, ahead and          |
|           |                                 | diverged, each with its    |
|           |                                 | message. On a fresh        |
|           |                                 | ``INVARIANT_HOME``, a      |
|           |                                 | pointer to an absent store |
|           |                                 | stopping session start     |
|           |                                 | with the clone command;    |
|           |                                 | ``store clone`` showing    |
|           |                                 | the declared commands, and |
|           |                                 | a declined clone           |
|           |                                 | registering nothing.       |
+-----------+---------------------------------+----------------------------+
| 6         | D8: push scan, store git hooks, | A home path refused at     |
|           | store-aware routes, the         | push from a host, from a   |
|           | no-remote relaxation.           | shell and from inside the  |
|           |                                 | store.                     |
+-----------+---------------------------------+----------------------------+
| 7         | D12: ``store migrate`` both     | A round trip on a copy of  |
|           | ways.                           | Heap's docs, with path     |
|           |                                 | history intact.            |
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
