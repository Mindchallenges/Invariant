.. _adr-028:

ADR-028 — An oracle agent's PATH finds every program it needs, or install and status say which it cannot
========================================================================================================

.. dec:: An oracle agent's PATH finds every program it needs, or install and status say which it cannot
   :id: DEC_ADR_028
   :status: accepted
   :affects: COMP_ORACLE_LAUNCHD, COMP_CMD_ORACLE

   - **Status:** Accepted (2026-11-20). BUG_155's fix direction left the
     details to ``feature-architect`` ("decide the details yourself, state
     them"); this record is that choice, built in the same change.
   - **Date:** 2026-11-20
   - **Supersedes:** nothing. It amends how ADR-007 D17's explicit ``PATH`` is
     built; the plist's shape is unchanged.

Context
-------

Both LaunchAgents carry a ``PATH`` baked at install time, because launchd's
own is minimal (ADR-007 D17). ``oracle/launchd.search_path`` built it from
``shutil.which`` on the installing process's ``PATH`` and dropped a program
that did not resolve without a word. Two defects met there (BUG_155):

1. The watcher's program list was ``git`` and ``osascript``, but the watcher
   runs the chain step (ADR-027), which opens ``claude``. Its plist could never
   find ``claude``, however it was installed.
2. The web agent's list has ``claude``, but an install from a process where it
   did not resolve (a daemon running the ``REMEDIES`` command, whose own
   ``PATH`` already lacked it) lost it silently, and every later install from
   that daemon kept it lost.

Start session on the phone and the chain both failed with "no claude on PATH
to start", and nothing had said why.

.. dec:: The watcher needs claude
   :id: DEC_ADR_028_D1
   :status: accepted
   :part_of: DEC_ADR_028
   :affects: COMP_ORACLE_LAUNCHD

   - ``NEEDED_PROGRAMS``, the watcher's list, is ``git``, ``osascript`` and
     ``claude``: the chain step it runs opens ``claude`` through
     ``claude_code_launch.resolve_claude``, which is ``shutil.which`` on the
     watcher's own ``PATH``.
   - ``AGENT_PROGRAMS`` maps each label to its list, so the installer and the
     status check read one table.

.. dec:: A program the installing process cannot find is looked for in well-known directories
   :id: DEC_ADR_028_D2
   :status: accepted
   :part_of: DEC_ADR_028
   :affects: COMP_ORACLE_LAUNCHD

   - ``search_path`` asks ``which`` first, then, for a program it cannot find,
     ``well_known_dirs()``: ``/opt/homebrew/bin``, ``/usr/local/bin``,
     ``~/.local/bin`` and ``~/.claude/local`` (Homebrew on Apple silicon and
     Intel, and the directories Claude Code's own installers use). The first
     hit's directory joins the ``PATH`` in the program's place in the list.
   - The installing process's own ``PATH`` still comes first, so a person's
     choice of a program wins over a well-known copy. The fallback only adds
     what would otherwise be lost, which breaks the self-perpetuating loss: a
     daemon running the remedy finds ``claude`` where it is usually installed.
   - Rejected: a fixed ``PATH`` of well-known directories for every agent. It
     would ignore where the person actually installed a program.

.. dec:: A program found nowhere is a warning at install, not a refusal
   :id: DEC_ADR_028_D3
   :status: accepted
   :part_of: DEC_ADR_028
   :affects: COMP_ORACLE_LAUNCHD, COMP_CMD_ORACLE

   - ``search_path`` returns an ``AgentPath``: the ``PATH``, the programs
     found only in a well-known directory (``fallback``) and those found
     nowhere (``missing``). ``oracle install`` (and ``--web``, and either's
     ``--dry-run``) prints a ``note`` for each fallback and one ``warning``
     naming every missing program and the remedy; the JSON carries
     ``"programs"``.
   - The install still succeeds and exits 0. The watcher without ``claude``
     still scans and notifies, and the page without ``tailscale`` still waits
     for it; refusing would take those away to protect one feature, and a
     machine with no ``claude`` at all could not install the watcher.

.. dec:: oracle status and doctor check each agent's baked PATH
   :id: DEC_ADR_028_D4
   :status: accepted
   :part_of: DEC_ADR_028
   :affects: COMP_ORACLE_LAUNCHD, COMP_CMD_ORACLE

   - ``LaunchdInstaller`` reads the plist's ``EnvironmentVariables.PATH`` and
     asks ``shutil.which(program, path=...)`` for each program its label
     needs, as the agent itself would. ``AgentStatus.missing_programs`` holds
     the answer; a plist with no ``PATH`` is read as launchd's system
     directories.
   - ``oracle status`` prints a ``programs`` (``web programs``) line only when
     one is missing, naming it and the remedy. ``doctor`` adds ``oracle agent
     programs`` and ``oracle web programs`` rows for each installed plist,
     which **warn** and never fail, for D3's reason. Neither asks launchd
     anything; an unreadable plist gets no programs row, because the
     interpreter row already reports it.
