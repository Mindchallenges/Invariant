.. _adr-025:

ADR-025 — A subagent's shell call is placed where its commands run
==================================================================

.. dec:: A subagent's shell call is placed where its commands run
   :id: DEC_ADR_025
   :status: accepted
   :affects: COMP_HOSTS_CLAUDE_CODE, COMP_CORE_SHELL, COMP_CORE_RULES

   - **Status:** Accepted (2026-11-17). BUG_125 left what counts as proof of
     place for a shell call to ``feature-architect``; this record is that
     choice, built in the same change.
   - **Date:** 2026-11-17
   - **Supersedes:** nothing. Extends "Attribution" in
     ``docs/architecture/state_model.rst``: "a proof of identity is not a
     proof of place, and an actor row needs both" (BUG_028).

Context
-------

An actor row claims that a subagent worked in a pipeline, so for the host
route it needs the call to be placed inside the session's worktree. A call
was placed by the path it names (a write's destination, a read's file) and
otherwise by the hook's working directory. A subagent's shell starts at the
host's working directory on every call, the shared checkout, so one that
works only through the shell spells every call ``cd <worktree> && ...``, and
the hook never sees it anywhere but the shared checkout. On Dang (session
``a8958b87``) a ``compliance-reviewer`` and a ``feature-architect`` worked
that way and wrote no actor row, so ``agents.review_agent_ran`` read "none"
while the reviewer was reviewing (BUG_125).

The other side has to hold as well. BUG_028's curator, filing a bug in the
shared checkout under the host bound to an isolated session, must not be
banked on that session, and a command that only names a worktree path (in an
``echo``, as a ``grep`` argument) did no work there.

.. dec:: A shell call's place is where its commands run: a top-level cd, or git -C
   :id: DEC_ADR_025_D1
   :status: accepted
   :part_of: DEC_ADR_025
   :affects: COMP_HOSTS_CLAUDE_CODE, COMP_CORE_SHELL

   - **Read.** A shell call is placed by the directory each of its commands
     runs in, starting from the hook's working directory: a ``cd <dir>`` or
     ``pushd <dir>`` at the top level, in sequence (``&&`` or ``;`` on both
     sides, or the command's start or end), moves every command after it, and
     ``git -C <dir>`` moves that one git invocation. A path a command only
     names moves nothing. A newline ends a command, as in bash. A ``cd`` is
     logical (``..`` comes off the path as written, bash's ``cd -L``), and
     ``git -C`` physical (a ``chdir``, so ``..`` after a symlink is resolved
     as the kernel does). A directory that does not exist after the call was
     never entered (``cd <typo>; cmd`` ran ``cmd`` where the shell stood), so
     such a call is placed nowhere.
   - **Placed only when whole.** The call is placed in a session's worktree
     only when every command of it runs inside that one session's worktree.
     A command that runs nothing (a bare ``cd``) places nothing.
   - **Cannot tell, then nowhere.** A directory change bash might not keep (in
     a pipe, in the background, behind ``||``, in a subshell, after a
     reserved word or ``command``), one with options or an expanded target
     (``~``, ``$VAR``, a substitution, a glob, ``-``), ``popd``, a git
     invocation naming ``--git-dir`` or ``--work-tree``, and unbalanced quotes
     place the call nowhere. A change inside a nested shell string
     (``bash -c '...'``, ``eval``) is not read, and the call stands where the
     outer command stands, as it did before.
   - **Unchanged.** A call naming a destination is placed by it. Any other
     pathless call stands where the hook does. Only the host route asks for
     place; a session with no worktree passes on the binding alone. The
     location still wins over the binding for an actor (the second-track
     inversion), so a ``cd`` into a second track's worktree records there.
   - **Tightened.** A shell call that leaves the worktree (``cd <shared
     checkout> && ...`` from a hook standing in the worktree) is no longer
     placed in it.
   - **Weighed against:** any worktree path the command names (an ``echo``
     would count); the first directory only (``cd <wt> && ... && cd <shared>
     && invariant item new`` would count); following ``env -C``, ``make -C``
     and the like (no caller needs them, and each is a parser to keep).

.. dec:: One shell reader, lifted into core/shell
   :id: DEC_ADR_025_D2
   :status: accepted
   :part_of: DEC_ADR_025
   :affects: COMP_CORE_SHELL, COMP_CORE_RULES, COMP_HOSTS_CLAUDE_CODE

   - The lexer BUG_132 built for ``privacy.clean``'s commit route, and the
     reading of git's own options (``-C`` cumulative, the subcommand where
     they stop, ``--git-dir`` and ``--work-tree`` held), move from
     ``core/rules`` to ``core/shell`` as ``lex`` and ``git_options``.
     ``working_directories`` (D1) is built on them there.
   - ``core/rules`` and ``hosts/claude_code`` both import ``core/shell``, so the
     commit route and the actor placement cannot disagree about how bash
     splits a command. ``core/shell`` imports nothing of the library.
   - **Weighed against:** a public reader in ``core/rules`` that the host
     imports (``CC --> RULES`` exists, but a rule registry is the wrong owner
     of a lexer); a second parser in the host (two readers of one command
     line drift, which is what BUG_027 and BUG_132 were).
