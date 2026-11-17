.. _adr-026:

ADR-026 — The gate receipt is the pipeline's coverage record
============================================================

.. dec:: The gate receipt is the pipeline's coverage record
   :id: DEC_ADR_026
   :status: accepted
   :affects: COMP_DOMAINS_PIPELINE, COMP_CLI_MAIN, COMP_DATA_AGENTS

   - **Status:** Accepted (2026-11-17). BUG_126 left the receipt's shape to
     ``feature-architect`` ("whether the receipt moves into an engine command
     ... is a decision for feature-architect"); this record is that choice,
     built in the same change.
   - **Date:** 2026-11-17
   - **Supersedes:** nothing. The receipt was a shell recipe in the shipped
     agent text (``feature-architect`` "Gate receipt", ``compliance-reviewer``
     Phase 5a) with no decision record; BUG_033 fixed its quoting.

Context
-------

``compliance-reviewer`` skips a cold rebuild and a second coverage pass when
``feature-architect``'s coverage gate passed on the very tree it is reviewing.
The proof was a receipt the architect wrote with a shell recipe:
``git diff --no-color HEAD -- <session files> | shasum -a 256`` and the
commit, into ``.build/gate/gate-receipt.txt``; the reviewer recomputed the
hash and compared. It almost never worked (BUG_126):

1. The write was the agent's own, so it went through the host's file-write
   permissions. On Dang, under the host's default permission set, it was
   refused, so the reviewer found no receipt and always ran the full pass.
2. ``git diff HEAD`` does not see an untracked file. A change made only of new
   files hashed empty, and BUG_033's guard refused to write it. A change
   mixing modified and new files hashed only the modified ones, so an edit to
   a new file after the gate left the receipt matching: the fast path fired on
   a moved tree, the one case the receipt exists to detect. The repro proved
   both (``tests/test_bug_126_repro.py``).

``invariant pipeline coverage`` already records every coverage run in the
pipeline's evidence (``coverage_run``, ``coverage``), and the ADR-021 steps
already sign a tree by content (``core/git.tree_signature``).

.. dec:: The coverage run signs the tree it measured; nothing else is written
   :id: DEC_ADR_026_D1
   :status: accepted
   :part_of: DEC_ADR_026
   :affects: COMP_DOMAINS_PIPELINE, COMP_CORE_GIT

   - ``run_coverage`` takes ``core/git.tree_signature`` of the tree it runs in
     before the run and after it, and keeps both in the run's record,
     ``coverage_run.tree``: the tree's root, the commit and digest before the
     run, the commit and digest after it. ``None`` when git cannot name the
     tree.
   - The signature is ADR-021 D3's: the commit, ``git diff HEAD`` (staged and
     unstaged, binary included) and every untracked file git does not ignore,
     by path and content. A new file, a modified file and a deleted file all
     count, with no file set to declare and no index to touch.
   - The signature hashes what those two reads leave out too, found probing
     the receipt at review: an untracked file's executable bit, an untracked
     symlink's target (not the file it points at), the contents of a
     submodule or an embedded repository (their own signature, recursively),
     and a tracked file git is told not to look at (``--assume-unchanged``,
     ``--skip-worktree``), hashed from disk. A plain untracked file hashes as
     before, so a step run recorded earlier still matches. A directory git
     lists that is no repository cannot be signed, and the signature is then
     ``None``, never a partial digest.
   - **Out of the signature on purpose:** a file ``.gitignore`` names. It is
     the environment the tree runs in (a virtual environment, a build
     directory, the coverage tool's own output), like the interpreter, and
     the signature could not hold still across a run that writes one. A suite
     that reads an ignored input the change depends on puts that input
     outside what a receipt vouches for; track it to bring it in. A change to
     the index alone moves nothing either: the files on disk are the same.
   - That record is the receipt. No agent writes one, so no host permission set
     has anything to refuse, and nothing is kept under ``.build/``. The write
     is the engine's own, into the pipeline record in the state root, under
     the record's lock with the rest of ``coverage_run``.
   - **Weighed against:** fixing the recipe alone (hash each session file's
     content with ``git hash-object``): it covers new files but leaves half (1),
     the write the host refuses, and keeps a recipe each agent must type the
     same way, which is how BUG_033 happened. A separate ``gate receipt write``
     command: the engine already records the run, so a second write could only
     disagree with it. ``git add -N`` so ``git diff`` sees new files: refused,
     it changes the index of a shared worktree, and every later diff, commit
     and gate reads it.

.. dec:: invariant pipeline receipt is the check, and it asks five things
   :id: DEC_ADR_026_D2
   :status: accepted
   :part_of: DEC_ADR_026
   :affects: COMP_DOMAINS_PIPELINE, COMP_CLI_MAIN

   - ``invariant pipeline receipt`` reads the pipeline's coverage record and
     writes nothing. It matches, exits 0 and prints the run's command, scope,
     suite counts and per-file coverage when all of these hold:

     1. a coverage run is on record and it signed its tree;
     2. the tree held still for the whole run (the signature before is the
        signature after);
     3. the tree now has the same commit and the same digest;
     4. the run's suite passed;
     5. ``coverage.meets_floor`` passes now, asked as a transition asks it,
        acknowledgements applied. A receipt is "the architect's coverage gate
        passed", not only "a run happened".

     Otherwise it exits 1 and names every condition that failed.
   - The tree compared, and the place the floor is asked from, is the root the
     record names, so a reviewer whose shell stands somewhere else still reads
     the tree the run measured.
   - **Stricter than ADR-021 D3 on purpose.** A step's run counts if the tree
     matches its signature before or after it, because a step may write its own
     output. A receipt vouches for coverage numbers, and a tree that changed
     during a run may not be the tree the run measured, so it needs both
     signatures equal. A coverage tool's output belongs in ``.gitignore``,
     which takes it out of the signature.
   - **Weighed against:** a declared session file set (what the requester
     suggested): an undeclared file the suite reads, a ``conftest.py`` or a
     fixture, would move unseen, and the receipt must never vouch for content
     it did not hash. The whole tree costs a fast path when an unrelated file
     moves, which is the safe direction: the reviewer runs the full pass.

.. dec:: The agents ask the engine; the shell recipe retires
   :id: DEC_ADR_026_D3
   :status: accepted
   :part_of: DEC_ADR_026
   :affects: COMP_DATA_AGENTS

   - ``feature-architect`` makes its recorded coverage run its last act on the
     tree, after every edit including the Phase 7 doc changes, and confirms the
     receipt with ``invariant pipeline receipt``. A NO MATCH there is a finding
     to fix before the handoff.
   - ``compliance-reviewer``'s Phase 5a is ``invariant pipeline receipt``: exit
     0 is the fast path (an incremental smoke build, the touched tests once, the
     receipt's coverage numbers), exit 1 the full build and coverage pass.
   - The recipe, the receipt file, the ``SESSION_FILES`` array and the
     empty-hash guard leave both agents. The shared gate build directory stays:
     it is what makes the reviewer's build incremental, and nothing in it is the
     receipt any more.

Consequences
------------

- BUG_126's half (1), the host refusing the receipt write, is moot: there is
  no agent write to refuse. ``invariant`` itself must still be runnable, as
  every gated step already requires.
- BUG_033's defect class, a shell hash that lands on the empty string, cannot
  recur: no shell computes the hash.
- A record from before this change carries no signature, and the check says
  so: run coverage again. No migration is owed, since the record lives for one
  pipeline.
