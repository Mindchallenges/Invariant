.. _adr-020:

ADR-020 — The store is init's default home; committing the configuration is opt-in
==================================================================================

.. dec:: The store is init's default home; committing the configuration is opt-in
   :id: DEC_ADR_020
   :status: accepted
   :amends: DEC_ADR_012, DEC_ADR_014, DEC_ADR_016, DEC_ADR_017, DEC_ADR_018
   :affects: COMP_CMD_SETUP, COMP_CORE_STORES, COMP_CORE_QUESTIONNAIRE, COMP_HOSTS_CLAUDE_CODE

   - **Status:** Accepted (2026-11-10) by the user's direction. Rulings 1 to
     4 were given by the user on 2026-11-10. Rulings 5 to 8 were given the
     same day, in the user's review of this record's first draft, by
     interview. Ruling 9 was given on 2026-11-11, after increment 1 was
     built, and D10 carries it out. All are recorded below as decided. Each decision gives the
     design that carries out its rulings. Any choice beyond a ruling is
     marked *recommended*. No question is open.
   - **Date:** 2026-11-10
   - **Supersedes:** nothing as a record. For Claude Code it replaces the
     user-level wiring of ADR-014 D11 (its ruling 17) with a per-project
     install (D9 here); ADR-014 D11 and its increment 3 are amended in place
     and say so. Amends
     :need:`ADR-014 <DEC_ADR_014>`
     (ruling 1's "In-repo stays the default", as init's default only; D1's
     ``hooks`` and ``agents`` locations, ``user`` retired for ``local``; D11
     and increment 3; D12, the configuration's move; ruling 11 and
     IDEA_040's carve-out stand as invariant's and Heap's own choice),
     :need:`ADR-016 <DEC_ADR_016>`
     (D4: the docs location's default follows the home; ruling H's "today's
     files" as to where they are written),
     :need:`ADR-017 <DEC_ADR_017>`
     (ruling 3's level default, ruling 5's "today's files", D1's core
     sections, D3's order, D4's offered levels, D8's flags, D9's report) and
     :need:`ADR-018 <DEC_ADR_018>`
     (D3's "Default: in the repository", its ruling 17, and D3's and D11's
     starter source directory, ``docs/`` for a new project becoming
     ``invariant-docs/``; D13's in-repo folder takes the same name, and its
     exclusion requirement is unchanged) and
     :need:`ADR-012 <DEC_ADR_012>`
     (ruling 1's ``docs/guidelines/ruleset.yaml``, for new projects only,
     and the privacy-scope reason given for it; D3 and D13 say so in place;
     D10 here). Amends
     ``docs/architecture/overview.rst`` ("Where things live at rest"),
     ``docs/architecture/manifest.rst`` ("Records and their locations") and
     ``docs/architecture/cli.rst`` (``init``, ``doctor``, ``store``). Folds
     IDEA_048 into IDEA_040, and removes the second cause of
     :need:`BUG_118 <BUG_118>`.

Context
-------

ADR-014 made a store possible. A store is a separate git repository,
outside the project's repository. It holds the project's configuration and
any of its records, and it is found through the user level by the
repository's identity. ADR-014 kept the in-repo layout as the default
(ruling 1). So ``init`` still commits ``.claude/invariant.yaml``, the host
assets and the docs project into the repository it governs. ADR-014
increment 2 is built, and increments 3 to 7 are open.

Two backlog items asked for less in the repository. IDEA_040 is ADR-014's
own item: documentation repositories must keep their content their own.
IDEA_048 asked for a lighter mode, an in-repo ``.claude/`` kept out of git.
A session worktree cannot see that, because a worktree holds only committed
files.

The user weighed a store-only design and rejected it. With the
configuration in a store only, a configuration change never appears in a
code diff. A check that compares the base's configuration with the tip's
(``docs.mandate_kept``) then has nothing to compare. A fresh clone and CI
would run unconfigured, and the configuration would not be versioned with
each commit. So the store becomes the default and the repository stays
available.

The first draft raised three further questions, which the user then ruled
on. The first was where invariant's own docs project goes in a repository
whose content already uses ``docs/``. The second was whether ``init`` may
wire hooks and agents for the whole user, as ADR-014 D11 had designed. The
third was what sharing takes with it.

Rulings (user, 2026-11-10)
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. _adr-020-ruling-1:

1. **The default.** The store becomes ``init``'s default home for a
   project's invariant configuration (the manifest) and its records. A
   project's repository need not carry ``.claude/invariant.yaml``.

.. _adr-020-ruling-2:

2. **Sharing is opt-in.** Committing the configuration into the repository
   is an opt-in, for users who want to share it with teammates, with CI and
   in pull-request review. The in-repo layout stays fully supported, and
   invariant and Heap keep it.

.. _adr-020-ruling-3:

3. **IDEA_048 is folded into IDEA_040.** A machine-local, git-ignored
   ``.claude/`` is not built separately. The store covers "keep it out of
   git".

.. _adr-020-ruling-4:

4. **Default, not only.** This is the parent session's reasoning, accepted
   by the user. Store-only would lose configuration changes from code diffs
   (the base-versus-tip check of ``docs.mandate_kept``). It would leave
   fresh clones and CI unconfigured, and it would lose per-commit
   configuration.

Rulings at review (user, 2026-11-10, by interview):

.. _adr-020-ruling-5:

5. **Sharing is everything.** Committing the configuration moves the
   configuration, the docs, the backlog and the memory to the repository.
   The configuration has one home at a time.

.. _adr-020-ruling-6:

6. **invariant's own docs folder.** For new projects, invariant's
   sphinx-needs docs project lives in a folder only invariant owns,
   ``invariant-docs/`` at the repository root. The same relative name is
   used inside the store, so ``store migrate`` is a plain move. It never
   uses ``docs/``, ``doc/``, ``source/`` or ``docs/source``. A content
   repository whose ``conf.py`` is at its root must exclude
   ``invariant-docs``. invariant never edits that ``conf.py``. ``doctor``
   names the line to add, and until the line is added, that repository's
   docs stay in the store. invariant itself keeps ``docs/``.

.. _adr-020-ruling-7:

7. **The store default holds for a non-interactive init too.**

.. _adr-020-ruling-8:

8. **No global install.** In the user's words: "we should not install
   hooks globally to take over everything in the system ... user should
   tell us if invariant should install the hooks in each project". ``init``
   asks, per project, whether to install invariant's hooks and agents, and
   a non-interactive ``init`` installs nothing and names the command to run
   later. A store project gets its hooks in ``.claude/settings.local.json``
   and its agents in ``.claude/agents/``, both hidden from git through
   ``.git/info/exclude``. No commit, no shared ``.gitignore`` edit, and
   nothing outside the project is touched. A shared in-repo project keeps
   today's committed ``.claude/settings.json`` and ``.claude/agents/``. This
   replaces ADR-014 D11's user-level design for Claude Code.

Ruling at build (user, 2026-11-11):

.. _adr-020-ruling-9:

9. **The guidelines ruleset follows the docs project.** Increment 1's build
   found that the starter still wrote ``docs/guidelines/ruleset.yaml``, so
   every new project gained a ``docs/`` folder, and in a documentation
   repository the file landed inside the content's own ``docs/`` tree. A
   new project's ruleset lives at
   ``<docs.needs.srcdir>/guidelines/ruleset.yaml``, which is
   ``invariant-docs/guidelines/ruleset.yaml`` for a fresh ``init``. This
   amends ADR-012 ruling 1 for new projects only. Existing projects,
   invariant among them, keep ``docs/guidelines/ruleset.yaml`` byte for
   byte.

What was read to write this
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Labels follow ADR-014. **Read** means read in the docs and items at
5c91b07. That covers ADR-014 in full, ADR-016, ADR-017, ADR-018 D3 to D13,
``overview.rst``, ``manifest.rst``, ``cli.rst``, ``state_model.rst``
("Attribution"), IDEA_040, IDEA_048 and BUG_118. Nothing was run. Every
other claim is **reasoned**.

- ``Manifest.locate`` first asks whether a level holds
  ``.claude/invariant.yaml``. An in-repo project does no store lookup, so a
  ``store`` record in an in-repo manifest is refused at load as having no
  store (``manifest.rst``, "Records and their locations"; read). A
  project's configuration therefore already has exactly one home: the
  repository, or a store.
- ``records.*`` and ``paths.docs`` have library defaults at load (``repo``
  and ``docs/architecture``; read). ``init`` writes ``docs.needs.srcdir``
  explicitly (``cli.rst``, ``init``; read).
- ``init``'s docs step can already make a fresh project a store project,
  and then it writes no ``.claude/invariant.yaml`` (ADR-016 D4, built;
  read).
- The store is offered only inside a git repository, at its top level
  (``cli.rst``, ``init``; read).
- The sphinx adapter's detection skips a ``conf.py`` at the location
  invariant reserves for its own docs project, ``docs/``. So a content tree
  at ``docs/`` is never detected as ``sphinx``. That is BUG_118's second
  suspected cause (read in the item; not yet reproduced).
- ``.git/info/exclude`` lives in the git common directory. Every linked
  worktree of a clone shares it, ``git clone`` does not copy it, and it is
  not tracked (git's documented behaviour; reasoned, not run here).

Decisions
---------

.. dec:: A fresh init puts the configuration and every record in a store
   :id: DEC_ADR_020_D1
   :status: accepted
   :part_of: DEC_ADR_020
   :amends: DEC_ADR_014, DEC_ADR_018_D3
   :affects: COMP_CMD_SETUP, COMP_CORE_STORES

   Rulings :ref:`1 <adr-020-ruling-1>`, :ref:`4 <adr-020-ruling-4>` and
   :ref:`7 <adr-020-ruling-7>`. Where the store is available, a fresh
   ``init`` makes a **store project**. This is ADR-014's layout, chosen by
   default:

   - **The store.** ``init`` runs ADR-014's ``store init`` at the default
     path (``~/.invariant/stores/<name>``, named by ruling 14 there). Where
     the chosen path already holds a store, it runs ``store link`` instead.
     The store is created or linked first among the writes, as ADR-017 D3
     already orders it.
   - **The configuration** goes where a store project's configuration goes
     today (``cli.rst``, ``init``). ``project`` and ``enforcement.profile``
     go at the level of a store ``init`` created. The rest goes in the
     store's ``store.repos.<identity>`` block.
   - **The records.** The block sets ``records`` explicitly: ``docs``,
     ``backlog``, ``guidelines``, ``memory``, ``reports`` and ``sweeps`` to
     ``store``, and ``hooks`` and ``agents`` to ``local`` (D9). The docs
     starter is written in the store at ``invariant-docs/`` (D8). This
     amends ADR-018 D3's "Default: in the repository" (its ruling 17) for a
     fresh ``init``.
   - **Nothing committed, and nothing in the shared ``.gitignore``.** No
     ``.claude/invariant.yaml``, no pointer (``init`` never writes one,
     ADR-017 D4) and no ``.gitignore`` line: the state root is the git
     common directory. The repository's local git config gains
     ``invariant.id`` (ADR-014 D4), which is not tracked. Files appear in
     the working tree only when the user opts in to the host install (D9):
     ``.claude/settings.local.json``, ``.claude/hooks/`` and
     ``.claude/agents/``, each hidden through ``.git/info/exclude``.
   - **Not available, said so.** Outside a git repository, or below its top
     level, there is no store, because ``store init`` refuses there. The home
     is then ``repo``, and ``init``'s report gives the reason on its ``home``
     row (D4). It is never a silent fallback.

   This changes what ``init`` writes, not what a manifest means. Every
   load-time default stays as it is (D2).

.. dec:: Existing projects are unchanged, and no migration is forced
   :id: DEC_ADR_020_D2
   :status: accepted
   :part_of: DEC_ADR_020
   :affects: COMP_MANIFEST_FILE, COMP_CORE_MANIFEST

   Ruling :ref:`2 <adr-020-ruling-2>`.

   - **The load-time defaults do not move.** An absent ``records`` key still
     means ``repo``. ``paths.docs`` still defaults to ``docs/architecture``.
     ``Manifest.locate`` still asks first for ``.claude/invariant.yaml``. An
     in-repo project resolves byte for byte as before. Moving a schema
     default would silently change what every existing manifest means.
   - **No command moves a project's home by itself.** ``init --force`` on an
     in-repo project rewrites ``.claude/invariant.yaml`` in place. On a store
     project it rewrites the project's block in the store. ``invariant
     configure`` writes the levels the project already has. Only ``store
     migrate`` (D3) moves the configuration.
   - **Existing wiring stays.** An installed in-repo project keeps its
     committed ``.claude/settings.json``, shims and agents. ``hooks install``
     and ``agents install`` keep rewriting them there.
   - **invariant and Heap** keep the in-repo layout and their docs folders,
     ``docs/`` for invariant, whose content is not Sphinx. That layout is
     their choice to share (ruling 2). ADR-014 ruling 11 and IDEA_040's
     carve-out stand on those terms: the two projects stay as they are
     unless ``store migrate`` is run.

.. dec:: One home at a time; store migrate moves everything to share it, or back
   :id: DEC_ADR_020_D3
   :status: accepted
   :part_of: DEC_ADR_020
   :amends: DEC_ADR_014_D12
   :affects: COMP_CMD_SETUP, COMP_CORE_STORES, COMP_CORE_HOME, COMP_HOSTS_CLAUDE_CODE

   Rulings :ref:`2 <adr-020-ruling-2>`, :ref:`4 <adr-020-ruling-4>` and
   :ref:`5 <adr-020-ruling-5>`.

   **One home.** The configuration lives in the repository only while every
   record does, and that is already built: an in-repo manifest cannot name
   a store. Sharing the configuration is therefore the in-repo layout,
   everything moved (ruling 5). The route between the two homes is ADR-014
   D12's command. D12 is the one owner of moving a project's files between
   the repository and a store with their history, so no second command is
   added.

   ::

      invariant store migrate --to repo
      invariant store migrate --to store [--store <path>]

   Both run as their own session lane and land through ADR-014 D6, so the
   move is reviewed like any change. A move into the repository arrives as a
   diff that adds ``.claude/invariant.yaml``, which is the review ruling 4
   asks for. D12's ``--records`` subset stays for moving single records
   within a store project. With ``--to repo`` and a subset, the
   configuration stays in the store. ``--to store`` always moves the
   configuration, because an in-repo manifest cannot name a store.

   Because D8 gives the docs project the same relative folder in both
   homes, the docs move is a plain move with no path rewritten.

   **Moving to the repository (sharing):**

   - ``.claude/invariant.yaml`` is written with every key that the store's
     own level and this repository's block set, the block winning, less
     ``store`` and ``records``. ``config show`` resolves the same values
     before and after. Only the labels change, from ``store:`` to ``repo:``.
     The output names each key copied from the store's own level.
   - Every record is exported with its history (D12).
   - The store leg removes this repository's ``store.repos`` entry and its
     records. The store's own level is never changed, because other
     repositories may read it. A store left serving no repository is kept,
     and the output says so.
   - **Host wiring** that is installed locally (D9) moves to the committed
     in-repo form: ``.claude/settings.json``, ``.claude/hooks/`` and
     ``.claude/agents/`` in the repository leg. The local entries and their
     ``.git/info/exclude`` lines are removed after that leg lands. A
     project with no install gets none.
   - After the repository leg lands, the user level's ``repos.<identity>``
     entry is removed, which is ``store link``'s inverse.
   - ADR-014 ruling 8 lands the store first. If the repository merge then
     fails, the move is half-landed: the main checkout finds neither home
     and refuses, naming the session, until ``session merge`` or ``session
     resume`` finishes it (ADR-014 D6). The session's worktree still
     resolves.

   **Moving to a store:**

   - The target is the store ``--store`` names, or a new one at the default
     path (ADR-014 D2). The store leg writes this repository's block, with
     the manifest's keys and ``records``, and writes the records with their
     history.
   - The repository leg deletes ``.claude/invariant.yaml`` and the moved
     records. Committed host wiring becomes local wiring (D9), and the user
     level is linked (``store link``). Until the repository leg lands, the
     in-repo manifest is found first, as ADR-014 D2 orders it, so nothing
     resolves differently.
   - **Not a refusal, said loudly** (*recommended*): when the repository has
     a remote, the output says that after the merge every other clone,
     teammates' and CI's, finds no configuration until it links the store
     (``store clone``, ADR-014 D7). That is what sharing gave.

   **Refused**, with exit 2, naming the reason and writing nothing:

   - **Either direction:**

     - outside a project, or below the repository's top level;
     - while the store is behind its remote or has diverged from it
       (ADR-014 D7), naming ``store pull``;
     - while a path the move would change carries uncommitted changes, or
       another session holds its docs-lock unit (ADR-014 D5), naming each.

   - **``--to repo``:**

     - in an in-repo project ("the configuration is already in the
       repository");
     - when any target path already exists in the repository, such as
       ``.claude/invariant.yaml`` or ``invariant-docs/``, naming each. It
       never merges and never overwrites.
     - in a documentation repository whose own ``conf.py`` would read
       ``invariant-docs/`` and does not exclude it (D8), naming the line to
       add (ruling 6).

   - **``--to store``:**

     - in a store project ("the configuration is already in store
       <name>");
     - when ``.claude/invariant.yaml`` is not committed at HEAD, or has
       uncommitted changes, because the move takes the committed
       configuration;
     - when the target store already lists this repository's identity,
       which would make a second home, naming that store.

.. dec:: init asks for the home first; the store is the default in every mode
   :id: DEC_ADR_020_D4
   :status: accepted
   :part_of: DEC_ADR_020
   :amends: DEC_ADR_017_D1, DEC_ADR_017_D3, DEC_ADR_017_D4, DEC_ADR_017_D8, DEC_ADR_017_D9, DEC_ADR_016_D4
   :affects: COMP_CORE_QUESTIONNAIRE, COMP_DATA_QUESTIONNAIRE, COMP_CLI_CONFIGURE, COMP_CLI_ONBOARDING, COMP_CMD_SETUP

   Rulings :ref:`1 <adr-020-ruling-1>`, :ref:`2 <adr-020-ruling-2>` and
   :ref:`7 <adr-020-ruling-7>`.

   **The question.** A new core section, ``home``, is the first section of
   the shipped catalogue. Its step question is ``home:location``:

   ::

      Where does this project's invariant configuration live?
        store  outside the repository, in a store on this machine;
               nothing of invariant's is committed           (default)
        repo   committed in the repository: shared with teammates,
               CI and pull-request review

   - Choosing ``store`` then asks the ``store`` step for the path. That step
     is the one ADR-017 D1 already places under ``records``: one placement,
     one store per project (ADR-017 D3). The default path is ADR-014 D2's,
     and a path that holds a store links it.
   - ``store`` is not offered where there is no store (D1). ``repo`` is not
     offered in a documentation repository whose ``conf.py`` would read
     ``invariant-docs/`` without excluding it (D8). Each question says why,
     as ADR-016 D4's store offer already does.
   - ``home`` is a step and not a key. Where the configuration lives is
     answered by the levels (ADR-014 D1), never by a key.

   **The order** (amends ADR-017 D3) is the home, then the target level,
   then the other core sections as before, then the host install question
   (D9). Under ``store``, the level question offers ``user``, ``store`` and
   ``store-repo``, with ``store-repo`` as the default, which ADR-017 ruling
   10 already makes a store project's default. This amends ADR-017 ruling
   3's "default repo" and ``cli.rst``'s "a fresh project has no store yet"
   for a fresh store project.

   **What follows from the home:**

   - The ``docs`` step writes the starter at ``invariant-docs/`` in the home
     (D8). Its location offer (ADR-016 D4.1) defaults to the home. In the
     store, an existing repository directory is never touched. Bringing a
     project's existing documentation in is adoption (ADR-016 D7).
   - The advanced ``records`` section's defaults follow the home. Under
     ``store`` they are D1's, and each may be set to ``repo``. Under
     ``repo`` only ``repo`` is offered for the data and policy records,
     because an in-repo manifest cannot name a store.

   **Not interactive** (ruling 7; amends ADR-016 ruling H and ADR-017 ruling
   5 as to where the files go). An ``init`` with no terminal and no flags
   asks nothing and offers nothing, as before, and takes the default home.
   Where the store is available, it writes D1's store project rather than
   ``.claude/invariant.yaml``. A script that needs the in-repo layout says
   so with ``--home repo``.

   **Flags** (amends ADR-017 D8, *recommended*): ``--home store|repo`` and
   ``--store <path>`` answer ``home:location`` and the ``store`` step, as
   ``--profile`` answers its question. They are checked like typed answers,
   and a refused one exits 2 before anything is written. ``--set
   home:location=repo`` gives the same answer. ``--home`` naming a home
   that is not offered is refused, naming why.

   **The report** (amends ADR-017 D9). The configuration list opens with a
   ``home`` row and then a line naming the other route:

   ::

      configuration (core questions):
        home                store:widgets (~/.invariant/stores/widgets)  default
        ...
      to commit the configuration instead: invariant init --home repo,
      or later: invariant store migrate --to repo

   Under ``--json``, a ``home`` field holds ``{"location", "store",
   "reason"}``. ``reason`` is non-null only when one home was not offered.
   ``--dry-run`` names the store it would create or link, and lists the
   files it would write there.

.. dec:: doctor names the home, two homes, and a missing exclusion
   :id: DEC_ADR_020_D5
   :status: accepted
   :part_of: DEC_ADR_020
   :affects: COMP_CMD_SETUP

   A new ``config home`` row (*recommended*). It informs, and warns only in
   the cases below. It never fails, so ``doctor``'s exit code is unchanged:

   - **In-repo, committed:** ``repo: .claude/invariant.yaml, committed
     (shared through the repository)``.
   - **A store project:** ``store <name> at <path>``. The row adds that
     nothing of invariant's is committed in the repository, that a fresh
     clone or CI is unconfigured until it links the store (``store
     clone``), and that ``invariant store migrate --to repo`` is the way to
     share. With no store remote, it adds "this machine only".
   - **Warns, in-repo but not committed:** ``.claude/invariant.yaml`` is
     untracked or git-ignored. No session worktree will hold it, so
     sessions find no project. The row names ``invariant store migrate --to
     store`` as the way to keep the configuration out of git (D6).
   - **Warns, two homes:** an in-repo manifest beside a user-level link, or
     beside a store listing this repository's identity. The row names both,
     and the one ``Manifest.locate`` uses (the in-repo manifest, ADR-014
     D2). It names a half-landed ``store migrate`` session when one is the
     cause, and otherwise the way to retire the other home.

   A ``docs folder`` row, present only in a documentation repository whose
   own ``conf.py`` would read ``invariant-docs/`` (D8). It passes when that
   ``conf.py`` excludes the folder. Otherwise it warns, naming the
   ``conf.py``, the exact ``exclude_patterns`` line to add and that the docs
   stay in the store until then (ruling 6).

   The host wiring rows are D9's.

.. dec:: A git-ignored .claude/ is not a mode; the store keeps the configuration out of git
   :id: DEC_ADR_020_D6
   :status: accepted
   :part_of: DEC_ADR_020
   :affects: COMP_CORE_MANIFEST, COMP_CMD_SETUP

   Ruling :ref:`3 <adr-020-ruling-3>`. IDEA_048's machine-local mode for
   the configuration is not built. It would have had to copy or link the
   main checkout's manifest into every session worktree. Every reader that
   takes the manifest from the base branch would have had to accept a local
   copy. And it would have added a third configuration home beside the
   repository and a store.

   A store project already commits nothing of invariant's, and its
   configuration is reviewed in the store (ADR-014 D3, D9). IDEA_048's user
   chooses the store home, which is now the default. An in-repo manifest
   kept out of git is not supported, and D5's row says so and names the
   move.

   Only host wiring is ever local (D9). Wiring is rendered from the
   configuration, so a copy of it cannot drift the way a copied manifest
   would.

.. dec:: The switch lands only after ADR-014's increments 3 to 7
   :id: DEC_ADR_020_D7
   :status: accepted
   :part_of: DEC_ADR_020
   :affects: COMP_CMD_SETUP, COMP_CORE_STORES, COMP_HOSTS_CLAUDE_CODE

   A default is what every new project gets without being asked, so it must
   be no less safe than the in-repo layout it replaces, and it must be
   reversible. Each increment below is a prerequisite, re-derived with
   increment 3 as D9 redesigns it:

   - **Increment 3 (ADR-014 D11 as D9 amends it: per-project local
     wiring).** Without it, a store project can be wired only by committing
     ``.claude/settings.json``, which breaks D1's "nothing committed". The
     only other option would leave the project with no hooks and no agents.
     Until it is built, ``records.hooks: local`` and ``records.agents:
     local`` are refused at load, so D1's project could not be written.
   - **Increment 4 (D5, D9: the docs lock, gates per leg, guidelines in a
     store).** Until it is built, ``records.guidelines: store`` is refused,
     and so is ``guidelines.baseline`` in a project whose manifest is not in
     its repository. So D1 could not put the guidelines in the store.
     ``docs.builds_strict`` and ``docs.updated`` answer a store docs project
     "changed set not established", which is a failure. Every enforced store
     project would be unable to land a docs change (ADR-018 D5).
   - **Increment 5 (D6, D7: landing, sync, ``store clone``).** In a store
     project a session is routinely half-landed, with the docs landed and the
     code pending (ADR-014 D5's costs). The landing record and resume are D6.
     A configuration kept off the repository also needs the sanctioned route
     to a second machine, to CI and to a teammate's clone. That route is
     ``store clone``, which shows the store's commands before anything runs
     them (ADR-014 ruling 18). Without it, the default would strand the
     configuration on one machine.
   - **Increment 6 (D8, the store's privacy scan).** Under today's default,
     ``privacy.clean`` screens every record at edit, at commit and at the
     review boundary. ADR-014 D8 ran all three routes against a store, and
     none reached it. Making the store the default before D8 is built would
     silently drop that screen for every new project.
   - **Increment 7 (D12, ``store migrate``), with D3 here.** The opt-in of
     ruling 2 must be reachable after ``init``, not only during it. A project
     that took the default must be able to share later, and to come back.

   D8's docs folder does not depend on any of these, and lands first (see
   Increments).

.. dec:: invariant's docs project lives in invariant-docs/, a folder only invariant owns
   :id: DEC_ADR_020_D8
   :status: accepted
   :part_of: DEC_ADR_020
   :amends: DEC_ADR_018_D3, DEC_ADR_018_D11, DEC_ADR_018_D13
   :affects: COMP_CMD_SETUP, COMP_CLI_ONBOARDING, COMP_ADAPTERS_SPHINX

   Ruling :ref:`6 <adr-020-ruling-6>`.

   - **The starter's source directory.** For a new project, in either home,
     ``init`` writes the docs starter (ADR-018 D11) at ``invariant-docs/``,
     at the repository root or at the store root, with the same relative
     name in both. It writes ``docs.needs.srcdir: invariant-docs`` and
     ``paths.docs: invariant-docs/architecture`` explicitly, so the
     load-time defaults do not move (D2). This amends ADR-018 D3's
     ``docs/`` and D11's "the one above ``paths.docs``" for a new project.
     D13's in-repo folder, ``docs.needs.home: in_repo``, takes the same name
     by default.
   - **Never a content folder.** ``docs/``, ``doc/``, ``source/`` and
     ``docs/source`` are never used for invariant's docs project in a new
     project. A ``docs.needs.srcdir`` naming one of them is refused at
     ``init`` and at ``store migrate``, naming ``invariant-docs``
     (*recommended*: refused at those writes only, not at load, so that
     invariant itself, whose ``docs/`` holds its own docs project, still
     loads, D2).
   - **A shared documentation repository no longer collides** with its
     content's ``docs/``. The content keeps ``docs/``, ``doc/`` or
     ``source/``, and invariant's project sits beside it.
   - **BUG_118's second cause goes away.** The sphinx adapter skips the
     docs project's source directory when it detects a content tree. That
     directory is now read from the resolved ``docs.needs.srcdir``, never a
     fixed ``docs/`` (*recommended*). Because invariant no longer claims
     ``docs/`` in a new project, a content tree at ``docs/conf.py`` is
     detected as ``sphinx``. The first cause, the detection order against a
     Sphinx-only ``pyproject.toml``, is untouched and stays BUG_118's to fix
     (:need:`BUG_118 <BUG_118>`).
   - **A content ``conf.py`` at the repository root** reads every folder
     below it, ``invariant-docs/`` included. There, the content's
     ``conf.py`` must list ``invariant-docs`` in its ``exclude_patterns``.
     This is ADR-018 D13's existing requirement. invariant never edits that
     file: ``doctor`` names the line to add (D5). Until the line is there,
     the repository's docs stay in the store. ``init`` does not offer
     ``repo``, and ``store migrate --to repo`` refuses (D3, D4). A
     ``conf.py`` under ``docs/`` reads only ``docs/``, so it needs nothing.
   - **invariant itself keeps ``docs/``.** Its content is not Sphinx, so
     nothing collides, and existing projects are unchanged (D2).

.. dec:: Hooks and agents are installed only when the user says so, per project, and locally in a store project
   :id: DEC_ADR_020_D9
   :status: accepted
   :part_of: DEC_ADR_020
   :amends: DEC_ADR_014_D1, DEC_ADR_014_D11
   :affects: COMP_HOSTS_CLAUDE_CODE, COMP_HOSTS_ASSETS, COMP_CMD_SETUP, COMP_MANIFEST_FILE, COMP_CMD_SESSION

   Ruling :ref:`8 <adr-020-ruling-8>`. Nothing invariant installs reaches
   beyond one project. No user-level hook, shim, agent or settings entry is
   written, so ADR-014 D11's user-level design for Claude Code, and its
   ruling 17, are replaced. ADR-014 D11 is amended in place.

   **The question.** ``init`` asks ``host:install``: "Install invariant's
   hooks and agents in this project?" Its default is **no** in every mode
   and in both homes (*recommended*). The ruling says the user decides per
   project, and a default of yes would install without the user saying so
   whenever Enter is pressed. A non-interactive ``init`` installs nothing,
   and its report names the commands to run later, ``invariant hooks
   install`` and ``invariant agents install``. ``--host-install`` answers
   yes from a script.

   **A ``records`` location for "local to this clone".** ``records.hooks``
   and ``records.agents`` take ``repo`` or ``local``. ``user`` is retired:
   it was refused at load until increment 3 and never built, so no manifest
   holds it. This amends ADR-014 D1. The load-time default stays ``repo``
   (D2).

   - ``repo`` keeps today's form, committed: ``.claude/settings.json``,
     ``.claude/hooks/`` and ``.claude/agents/``. A shared in-repo project
     keeps it.
   - ``local``, which D1 writes for a store project, uses the same project
     directory with nothing committed. Hook entries go in
     ``.claude/settings.local.json``, Claude Code's personal, per-project
     settings file. The shims go in ``.claude/hooks/`` and the agents in
     ``.claude/agents/``. Each path is added to ``.git/info/exclude``, which
     is clone-local, shared by the clone's worktrees, never copied by a
     clone and not tracked. The shared ``.gitignore`` is never edited. The
     store's main checkout is added to that file's
     ``permissions.additionalDirectories``.

   **What else follows** (*recommended*):

   - **One firing.** A project is wired in exactly one place, its own
     ``.claude/``. The user-level shim's in-repo check, which existed only
     to prevent a second firing, has nothing left to do.
   - **Worktrees.** A session worktree holds only committed files, so
     ``session isolate`` renders the local wiring into the new worktree
     when the main checkout has it installed. It renders, not copies,
     through the same install plan, because the wiring is generated from
     the configuration. The exclude lines already cover it, because
     ``.git/info/exclude`` is shared.
   - **Exact inverses.** The install writes a receipt in the state root,
     ``<state root>/host/claude_code/installed.json``. It lists each
     settings entry it added, each exclude line it added and each agent
     file with its hash. ``invariant uninstall`` removes exactly those
     entries and lines and leaves every other setting and exclude line as
     the user has it. An agent file whose hash no longer matches was edited
     by the user, so it is left in place and named.
   - **Instructions.** Under ``local``, the managed guidelines block is not
     written into a tracked ``CLAUDE.md``. The ``always`` rules reach the
     agent through the hook context (ADR-014 D11, kept).
   - **The linked-identity index loses its reader.**
     ``~/.invariant/linked-repos.txt`` existed for the user-level shim, and
     increment 3 removes it. ``store status --quiet`` stays a plain status
     query.
   - **doctor rows.** A ``host wiring`` row names where the project is
     wired (``repo``, ``local`` or not installed) and the commands to
     install. It never fails when nothing is installed, because that is the
     user's choice. It fails on a receipt entry missing from the files, and
     on a ``local`` path missing from ``.git/info/exclude``. The second case
     would show invariant's files to ``git status``.
   - **Moves.** ``store migrate`` converts between ``local`` and ``repo``
     wiring when the wiring is installed (D3).

.. dec:: A new project's guidelines ruleset follows its docs project
   :id: DEC_ADR_020_D10
   :status: accepted
   :part_of: DEC_ADR_020
   :amends: DEC_ADR_012_D3, DEC_ADR_012_D13, DEC_ADR_020_I1
   :affects: COMP_CMD_SETUP, COMP_DOMAINS_DOCS, COMP_CORE_GUIDELINES, COMP_CORE_NEEDS, COMP_CORE_RULES

   Ruling :ref:`9 <adr-020-ruling-9>`. Built with this record's increment 1
   follow-up (2026-11-11).

   - **The place.** A ruleset's documented place is inside the docs
     project, ``<docs project>/guidelines/ruleset.yaml``
     (``core/guidelines.ruleset_location``). The docs project's folder is
     ``core/needs.docs_folder``: the resolved ``docs.needs.srcdir``, else
     the folder above ``paths.docs`` (ADR-018 D11's rule for the starter).
     So a new project's is ``invariant-docs/guidelines/ruleset.yaml``, a
     docs step that chose ``design/arch`` gives
     ``design/guidelines/ruleset.yaml``, and a docs project at ``docs/``
     gives ADR-012 ruling 1's path exactly, which is why no existing project
     has anything to move.
   - **One resolution for every reader: the key.** As D8 did for
     ``docs.needs.srcdir`` and ``paths.docs``, ``init`` writes
     ``guidelines.ruleset`` explicitly, in the starter's settings beside
     them. Every reader (``guidelines.ruleset_valid`` and the other
     ``guidelines.*`` gates, the pipeline's pin, ``guidelines render``,
     ``show`` and ``select``, the managed block in ``CLAUDE.md``, the turn
     index and ``doctor``) reads that key and nothing else, as it already
     did. No reader derives a location: the key's load-time default stays
     null, meaning no guidelines (ADR-012 D13), so no manifest changes
     meaning (D2). It stays configurable the way it always was: a
     configured ``guidelines.ruleset`` is the project's, is never rewritten,
     and wins over the place.
   - **The writer.** The starter (``domains/docs``) writes the ruleset at
     ``ruleset_location(<srcdir>)``, the source directory it is writing,
     and never over a file already there (the note names it and the two
     rules to add by hand, as before). A store project still gets no
     ruleset, because ``records.guidelines: store`` is refused until ADR-014
     increment 4.
   - **The fixes.** The guidelines gates that offer a path in a fix
     (``guidelines.ruleset`` and ``guidelines.baseline`` outside the
     project, a baseline not configured) name it through the same two
     functions (``core/rules.guidelines_example``), so a new project is
     never told to create ``docs/guidelines/``. A registry's example in the
     ruleset parser, which has no manifest, names
     ``<docs project>/guidelines/``.
   - **Existing projects.** ``init --force`` keeps an existing manifest's
     ``guidelines.ruleset`` (ADR-018 D10's kept keys). One that sets none
     and whose docs project is ``docs/`` gets ``docs/guidelines/
     ruleset.yaml``. invariant's own manifest names
     ``docs/guidelines/ruleset.yaml`` and resolves it unchanged, with the
     same pin (``02ed45e4db9d`` at this record).
   - **Privacy scope.** ADR-012 ruling 1 gave "inside the ``docs/`` privacy
     scope" as a reason for ``docs/guidelines/``. As built, the ``docs``
     scope keyword reaches ``paths.docs`` alone (``docs/architecture``), so
     the ruleset file was inside the privacy scope only where
     ``privacy.scope`` names its folder literally, as invariant's names
     ``docs/``. The move neither gains nor loses that: the default scope
     reaches neither ``docs/guidelines/`` nor
     ``invariant-docs/guidelines/``. What protects the ruleset's text is
     that every output rendered from it (the checklist, the rules page and
     the instruction-file block) is privacy-scanned at render whatever
     ``privacy.scope`` says (ADR-012 D3). A project that wants the file
     itself screened names its docs project folder in ``privacy.scope``.
     ADR-012 D3 is amended in place to say so.
   - **Isolation.** ``invariant-docs/guidelines/`` is outside
     ``session.exempt_paths``' default ``.claude/*``, so ADR-012 D3's
     reason, that a ruleset edit never lands on the base branch unreviewed,
     holds unchanged.

   This amends :need:`DEC_ADR_020_I1`'s "Unchanged here" bullet.

   Proven by ``tests/test_adr_020_ruleset_location.py``: a fresh ``init``
   on a toy repository writes no ``docs/`` folder and the ruleset at
   ``invariant-docs/guidelines/ruleset.yaml``, named in the manifest, which
   the gate, the pin, ``guidelines show``, the rules page and ``doctor`` all
   read; a ``docs/conf.py`` content repository's ``docs/`` tree is byte for
   byte as it was; a chosen docs folder carries the ruleset; an existing
   file is never written over; ``init --force`` over a manifest with docs at
   ``docs/`` writes ``docs/guidelines/ruleset.yaml``; the fixes name the
   docs project's folder; and this repository resolves
   ``docs/guidelines/ruleset.yaml`` with its pin ``02ed45e4db9d``
   unchanged. The starter still builds strictly
   (``tests/test_adr_018_starter.py``, behind
   ``INVARIANT_TEST_SPHINX_BUILD``).

Increments
----------

Each increment leaves every existing project, in-repo or store, resolving
and wired exactly as before, and proves it with the existing suite.
ADR-014's increments keep their own scope, and increment 3 takes D9's
design. This record adds to increment 7, and adds the switch after it.

.. list-table::
   :header-rows: 1
   :widths: 12 44 44

   * - Order
     - Lands
     - Proven by
   * - Record
     - This ADR, accepted, and ADR-014 D11 and its increment 3 row amended to
       D9. The architecture docs note the unbuilt decisions.
     - Docs only: a green ``invariant docs build``.
   * - ADR-020 inc 1 (the docs folder)
     - D8: the starter at ``invariant-docs/`` with ``docs.needs.srcdir`` and
       ``paths.docs`` written explicitly, the reserved names refused at
       ``init``, and the sphinx adapter skipping the resolved source
       directory. It depends on nothing below and may land any time after
       the record.
     - ``init`` on a toy repository writes ``invariant-docs/``, which builds
       strictly. A toy content tree at ``docs/conf.py`` is detected as
       ``sphinx`` (BUG_118's case b). This repository resolves and builds
       ``docs/`` exactly as before.
   * - ADR-020 inc 1, follow-up (the ruleset)
     - D10: the starter's ruleset at ``<srcdir>/guidelines/ruleset.yaml``,
       ``guidelines.ruleset`` written explicitly, and the gates' fixes
       naming the docs project's folder.
     - A fresh ``init`` writes no ``docs/`` folder. A ``docs/conf.py``
       content repository's ``docs/`` is untouched. This repository resolves
       ``docs/guidelines/ruleset.yaml`` with the same pin.
   * - ADR-014 inc 3
     - ADR-014 D11 as amended by D9: ``records.hooks`` and
       ``records.agents`` ``local``, ``.claude/settings.local.json``,
       shims and agents under ``.claude/``, the ``.git/info/exclude``
       lines, the receipt, isolate rendering into worktrees, the doctor
       rows, ``user`` retired and ``linked-repos.txt`` removed. With it,
       brought forward from the switch at the user's direction
       (:need:`DEC_ADR_014_I3`): ``init``'s ``host:install`` question,
       default no, and ``--host-install``.
     - The amended row in ADR-014, and a non-interactive ``init`` that
       installs nothing and names the commands.
   * - ADR-014 inc 4
     - ADR-014 D5 and D9: the docs lock, gates per leg, and the guidelines
       refusal lifted.
     - ADR-014's own proof.
   * - ADR-014 inc 5
     - ADR-014 D6 and D7: release, the landing record, half-landed resume,
       sync and ``store clone``.
     - ADR-014's own proof.
   * - ADR-014 inc 6
     - ADR-014 D8: the push scan, store git hooks and store-aware routes.
     - ADR-014's own proof.
   * - ADR-014 inc 7, with ADR-020 inc 2
     - ADR-014 D12 (``store migrate`` both ways), with D3 here:
       everything moves together, the configuration's keys are
       materialised from the store's level and block, the user-level link
       is removed or written, the wiring converts between ``local`` and
       ``repo``, and the refusals.
     - On a copy of a toy in-repo project: ``store migrate --to store``,
       then ``--to repo``. ``config show`` gives the same values, labelled
       ``store:`` and then ``repo:``. Path history is intact both ways, and
       ``invariant-docs/`` is moved with no path rewritten. Installed wiring
       goes from committed to local and back, and ``git status`` is clean
       in the store home. Each of D3's refusals exits 2 with nothing
       written, the root-``conf.py`` refusal included. A failed repository
       merge on ``--to repo`` is half-landed and finished by ``session
       resume``.
   * - ADR-020 inc 3 (the switch)
     - D1, D4, D5 and D9's question: the ``home`` section and
       ``home:location``, ``--home`` and ``--store``, the store default in
       every mode, the level and docs defaults following the home, the
       ``home`` report row and field, and ``doctor``'s ``config home`` and
       ``docs folder`` rows. (``host:install`` and ``--host-install`` landed
       with ADR-014 increment 3.)
     - On a toy git repository with a scratch ``INVARIANT_HOME`` and a
       scratch ``HOME``:

       - ``init`` with no terminal leaves ``git status`` empty, writes no
         ``.claude/``, and leaves the scratch ``HOME`` without any Claude
         Code setting. It creates and links a store, sets ``records`` as D1
         says, and builds the starter strictly at ``invariant-docs/`` in the
         store. Its report names the install commands.
       - With ``--host-install``, the local files exist, ``git status`` is
         still empty, and the hooks fire once.
       - A docs-lane session runs from start to merge, and the repository
         holds only its own commit.
       - ``init --home repo`` writes the in-repo layout.
       - Outside a git repository, ``init`` takes ``repo`` and names the
         reason.
       - A root-``conf.py`` content repository is not offered ``repo``,
         and ``doctor`` names the exclude line.
       - On this repository, ``config show`` and ``doctor`` are unchanged
         except for the informational rows.

.. mermaid::

   graph TD
       INIT["cli: init (home step first, host:install last)"] --> Q["core/questionnaire: home section, home:location, host:install"]
       INIT --> STORES["core/stores: store init / link (ADR-014 D2)"]
       INIT -. "only when the user says yes" .-> HOST["hosts/claude_code: wiring_plan(repo or local)"]
       HOST --> LOCAL[(".claude/settings.local.json, hooks/, agents/; .git/info/exclude")]
       ISOLATE["domains/session: isolate"] -. "renders local wiring into the worktree" .-> HOST
       MIGRATE["cli: store migrate --to repo / --to store (ADR-014 D12)"] --> STORES
       MIGRATE --> HOME["core/home: user-level link written or removed"]
       MIGRATE --> HOST
       DOCTOR["cli: doctor: config home, docs folder, host wiring"] --> LOC["core/manifest: locate (unchanged)"]
       SPHINX["adapters/sphinx: detect skips the resolved docs.needs.srcdir"] --> LOC

Alternatives rejected
---------------------

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Alternative
     - Why not
   * - The store as the only home
     - Ruling 4: configuration changes would leave code diffs, fresh clones
       and CI would run unconfigured, and per-commit configuration would be
       lost.
   * - Moving the load-time defaults (``records`` to ``store``,
       ``paths.docs`` to ``invariant-docs/``)
     - It would change what every existing manifest means. Ruling 2 keeps the
       in-repo layout fully supported (D2).
   * - Sharing the configuration alone, with the records left in a store
     - Ruling 5: sharing is everything, one home at a time.
   * - A new ``store share`` / ``store unshare`` pair
     - ADR-014 D12 already owns moving a project's files between the
       repository and a store with their history. A second command would
       fork that owner (D3).
   * - A git-ignored ``.claude/`` for the configuration (IDEA_048)
     - Ruling 3: the store covers it (D6).
   * - Keeping in-repo as the non-interactive default
     - Ruling 7.
   * - User-level hooks, shim and agents (ADR-014 D11 as accepted)
     - Ruling 8: never install globally. The user decides per project.
   * - Installing hooks and agents by default, or without asking
     - Ruling 8.
   * - Hiding local wiring through the shared ``.gitignore``
     - Ruling 8: it would be a committed edit to the user's repository.
       ``.git/info/exclude`` is clone-local.
   * - Keeping ``docs/`` for invariant's docs project in new projects
     - Ruling 6: it collides with a documentation repository's content
       and causes BUG_118's second cause.
   * - Editing a content repository's ``conf.py`` to exclude
       ``invariant-docs``
     - Ruling 6: invariant never edits it, and ``doctor`` names the line.
   * - Switching the default before ADR-014's increments 3 to 7
     - D7: there would be no wiring without a commit, a docs gate that
       cannot pass, no landing record, no route to a second machine, no
       privacy screen and no way back.

Consequences
------------

- A new project commits nothing of invariant's and leaves the shared
  ``.gitignore`` alone. Its configuration and records are reviewed and
  versioned in the store.
- Nothing invariant does reaches outside the project, apart from the store
  and the user level's ``repos:`` link (ADR-014 D2). A machine gains no
  global hook, shim or agent, and an unrelated repository never sees
  invariant.
- A project the user does not choose to wire has no hooks and no agents.
  Its gates still answer through the CLI, but nothing enforces them in the
  host. ``init``'s report and ``doctor`` name the commands that wire it.
- In a store project, a configuration change is not in the repository's
  pull requests, and a fresh clone or CI is unconfigured until it links the
  store. Ruling 4 names these costs, and they are why sharing stays one
  command away (D3) and is named by ``init``'s report and by ``doctor``.
- A documentation repository whose content ``conf.py`` is at the root
  shares its configuration only after its maintainers add one
  ``exclude_patterns`` line.
- invariant and Heap are unchanged. Their in-repo layout is their choice to
  share, and invariant keeps ``docs/``.
- IDEA_048 closes, merged into IDEA_040. BUG_118 keeps only its first
  cause once increment 1 lands.

As built: increment 1 (2026-11-10)
----------------------------------

D8, as the architecture docs describe it (``cli.rst``, ``init``;
``manifest.rst``, ``docs.needs`` and the sphinx adapter's detection;
``overview.rst``, ``core/needs``, ``adapters/sphinx`` and "Where things live
at rest"). Where the build settles what the text left open:

.. dec:: ADR-020 increment 1 as built: how the adapter learns the docs project, and where a new project begins
   :id: DEC_ADR_020_I1
   :status: accepted
   :part_of: DEC_ADR_020
   :affects: COMP_ADAPTERS_BASE, COMP_ADAPTERS_SPHINX, COMP_CORE_NEEDS, COMP_CMD_SETUP, COMP_CLI_ONBOARDING, COMP_DOMAINS_DOCS, COMP_DOMAINS_PIPELINE, COMP_DOMAINS_SWEEP

   - **The adapter is told, when it is made.** An adapter imports nothing
     from the engine, so the resolved ``docs.needs.srcdir`` is handed in:
     every adapter takes ``docs_srcdir`` (``Adapter(docs_srcdir=...)``,
     ``adapters.get_adapter`` and ``adapters.detect``), and the ``sphinx``
     adapter skips that directory, compared resolved. An adapter told
     nothing skips nothing, never a fixed ``docs/``. ``init`` tells it a new
     project's ``invariant-docs/``, and, under ``--force``, what the existing
     manifest resolves (``srcdir_of``; the default rule over the default
     ``paths.docs`` where that manifest does not load). A pipeline's test run
     and the sweep tell it ``srcdir_of``, so the narrowing notes of
     ``annotate`` never read the docs project as content.
   - **The default rule moves to the key it defaults.** ADR-018 D3's rule,
     the nearest ``conf.py`` at or above ``paths.docs`` strictly below the
     root, is ``core/needs.default_srcdir``. It was the adapter's only while
     detection read it against the default ``paths.docs``. The
     ``CNEEDS --> SPHINX`` arrow and the adapter's ``DEFAULT_DOCS`` are gone;
     ``init`` (``CLI``), ``domains/pipeline`` and ``domains/sweep`` reach
     ``core/needs`` for ``srcdir_of`` instead.
   - **The refused folders are the adapter's content homes.** A content
     folder is where the ``sphinx`` adapter looks for a documentation
     repository's ``conf.py`` below the root (``HOMES``): ``docs``,
     ``docs/source``, ``doc``, ``doc/source`` and ``source``. That is D8's
     four and ``doc/source``, refused for the same reason, and one list, so
     the refusal and the detection cannot drift apart
     (``domains/docs.refused_srcdir``). It is checked where ``init`` takes a
     docs location, the docs step's ``path`` and store answers, which are
     asked again naming ``invariant-docs`` (as a path climbing out with
     ``..`` already is). A non-interactive ``init`` takes no location, so
     that offer is the one route. ``store migrate``'s refusal is with its
     own increment (ADR-014 increment 7).
   - **A new project is one with no** ``.claude/invariant.yaml``. Only it gets
     ``invariant-docs/architecture`` and the refusal. ``init --force`` keeps
     the existing manifest's ``paths.docs`` (D2: it rewrites the manifest in
     place), so a project ``init`` wrote keeps ``invariant-docs/`` and one
     with no ``paths.docs``, invariant among them, keeps ``docs/``.
   - **``paths.docs`` is written wherever it is not the load-time default**,
     so a new project's manifest sets it even where the folder already
     exists and no starter is written, and ``init``'s configuration report
     reads it from ``repo:``, not ``default``. In a store project it is in the
     store's block, as before.
   - **Unchanged here:** the guidelines ruleset the starter writes stays at
     ADR-012 D3's default path, ``docs/guidelines/ruleset.yaml`` (that
     record's ruling 1), so a new project still gains a ``docs/guidelines/``
     folder beside ``invariant-docs/``. D8 does not move it. **Amended
     2026-11-11 by** :need:`DEC_ADR_020_D10` **(ruling 9):** a new
     project's ruleset follows its docs project, at
     ``invariant-docs/guidelines/ruleset.yaml``, and a fresh ``init`` writes
     no ``docs/`` folder.

The proofs: ``init`` on a toy Python repository writes the starter in
``invariant-docs/``, with both keys in the manifest, and interactive
``init`` creates its pinned environment and ``invariant docs build`` is
green on it, strictly (behind ``INVARIANT_TEST_SPHINX_BUILD``); a toy content
tree at ``docs/conf.py`` is detected as ``sphinx`` and ``init`` puts its docs
project beside it, ``vars.srcdir: docs``; a starter-shaped ``docs/`` told to
the adapter is not an ecosystem; each content folder is asked again at the
offer of a new project and accepted for an existing one; ``init --force``
over an existing manifest keeps ``docs/``; and this repository resolves
``docs/`` as its source directory, is not detected as ``sphinx`` and builds
``docs/`` strictly as before.
