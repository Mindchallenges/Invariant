.. _adr-024:

ADR-024 — A host declares its own commit attribution trailer, which privacy.clean admits only in a message's trailer block
==========================================================================================================================

.. dec:: A host declares its own commit attribution trailer, which privacy.clean admits only in a message's trailer block
   :id: DEC_ADR_024
   :status: accepted
   :affects: COMP_CORE_PRIVACY, COMP_CORE_RULES, COMP_HOSTS_CLAUDE_CODE, COMP_CLI_MAIN

   - **Status:** Accepted (2026-11-16). BUG_127 left where the allowance
     lives to ``feature-architect`` (the host adapter, the default allow list,
     or a commit-message reader that knows about trailers); this record is
     that choice, built in the same change.
   - **Date:** 2026-11-16
   - **Supersedes:** nothing. Extends ``core/privacy`` (``docs/architecture/overview.rst``,
     ``core/privacy``) and the commit-message half of ``privacy.clean``
     (``docs/architecture/manifest.rst``, "Privacy scope" and "Privacy allow").

Context
-------

Claude Code asks an agent to end every commit message with two trailers:
``Co-Authored-By`` with a no-reply address, and ``Claude-Session`` with a link
to the host session the commit was made in. With ``commit_messages`` in
``privacy.scope`` and ``links`` on, both of them defaults, ``privacy.clean``
refused every commit carrying the second: ``links`` reads any URI naming a
network host as a leak, and nothing knew the host's own trailer (BUG_127).
This repository has committed with ``Co-Authored-By`` alone since 2026-09-06
for that reason, and BUG_043's residue left a fix direction unchosen because
of it.

A ``privacy.allow`` value was the workaround, and the wrong reach: a value
applies to every text the gate reads, documents and memories included, while
the session link is legitimate in exactly one place. The link names a session
of the host's own service and no user content, but the same characters
written in prose, or the host's token over somebody else's link, are what the
pattern exists to catch.

Three placements were offered. The default allow list puts one vendor's link
into the engine's defaults and reaches every document. The host adapter alone
cannot reach the rule: nothing below the CLI imports a host. A reader that
knows about trailers can tell the one place apart, but must not know whose
trailer it is.

.. dec:: The host declares the trailer, core reads trailer blocks, the CLI joins them
   :id: DEC_ADR_024_D1
   :status: accepted
   :part_of: DEC_ADR_024
   :affects: COMP_CORE_PRIVACY, COMP_CORE_RULES, COMP_HOSTS_CLAUDE_CODE, COMP_CLI_MAIN

   - **``core/privacy`` owns the shape and the reading, and no host.**
     ``AttributionTrailer`` is a declaration: a trailer token and the whole
     shape of its value, a compiled pattern. ``PrivacyScanner.scan_message``
     scans a commit message as ``scan_text`` scans a path-less text, except
     for the lines of its trailer block that a declaration given to it
     admits. No host's token or link is spelled anywhere in ``core/``.
   - **The host adapter declares its own.** ``hosts/claude_code`` names
     ``ATTRIBUTION_TRAILERS``: ``Claude-Session`` with an https link to a
     ``claude.ai/code/session_`` path and nothing after the session id. A
     second host declares its trailers the same way, with no change to the
     engine.
   - **The CLI joins them,** as it does the instruction files (ADR-012 D3):
     ``host_attribution_trailers(manifest)`` answers the adapter's
     declarations for ``host.kind``, and ``Context.rule_context`` hands them
     down as ``RuleContext.host_attribution_trailers``. A host kind with no
     adapter declares none. The pipeline domain and the oracle's gate cache
     are not handed them: a transition gives ``privacy.clean`` no commit
     message to read.
   - **Weighed against:** a default ``privacy.allow`` value (vendor knowledge in
     the engine's defaults, and a reach over every document and memory); a
     ``privacy`` manifest key listing trailers (the default would still have
     to name the vendor in ``core/manifest``, and no project asked to
     configure one); a host-side rewrite of the message before the rule
     reads it (the rule would screen something other than what lands).

.. dec:: The allowance is one line in the trailer block, under the token, in the declared shape
   :id: DEC_ADR_024_D2
   :status: accepted
   :part_of: DEC_ADR_024
   :affects: COMP_CORE_PRIVACY, COMP_CORE_RULES

   - **The trailer block** is the message's last paragraph after any trailing
     blank lines, when it is not the first paragraph (a subject is never
     trailers) and every line of it is ``Token: value``, the token hard
     against its colon. A paragraph holding one line of anything else, a
     continuation line included, is no block at all, and nor is a paragraph
     with more text after it. That is narrower than git, which accepts
     configured separators, continuation lines and a block that is only a
     quarter trailers; the difference falls on the noisy side.
   - **A line is admitted** when its token is a declared token spelled
     exactly, and its value matches the declared pattern whole. Git compares
     trailer tokens without case; the gate does not, because a host writes
     its token one way and another spelling is not what it wrote. An admitted
     line is skipped whole; every other line of the message, the block's
     other trailers included, is read as before.
   - **The lines are git's.** A message is cut into lines at a newline, a
     carriage return before one dropped, and the ``-F`` file is read as bytes
     so nothing translates a lone carriage return. A message holding a line
     break git does not honour (a lone carriage return, a vertical tab, a form
     feed, the Unicode line and paragraph separators) admits nothing, because
     the host's trailer could look whole on a line that, in the history,
     carries more after it.
   - **Only the repository commit route reads it.** The host's link in the
     message body, after the block, under another token, or with anything
     after the session id is still a ``links`` finding, and so is a forged
     link under the host's token. Documents, memories, reports, staged files
     and ``invariant privacy scan`` read the same text as before, wherever it
     sits in them. A commit made in a store is screened by the store's block
     and its ``commit-msg`` hook, which this change leaves as they were:
     BUG_133.

.. dec:: BUG_043's direction 4 is no longer blocked by privacy, and stays unchosen
   :id: DEC_ADR_024_D3
   :status: accepted
   :part_of: DEC_ADR_024
   :affects: COMP_CORE_PRIVACY, COMP_DATA_AGENTS

   - BUG_043's residue records the same conflict BUG_127 reports: making the
     compliance reviewer's trailer unconditional (its direction 4) was not
     chosen because ``privacy.clean`` refused the ``Claude-Session`` trailer.
     That conflict ends with D1 and D2 for a repository commit, so the
     residue says so.
   - Whether to take direction 4 is not settled here. It changes the commit
     convention of every project that runs the reviewer, which is the user's
     call, and BUG_043's own fix (directions 1 to 3) does not depend on it.

.. dec:: The commit route reads every message source git writes from, or refuses the commit
   :id: DEC_ADR_024_D4
   :status: accepted
   :part_of: DEC_ADR_024
   :affects: COMP_CORE_RULES

   - **Context.** BUG_132: the commit route read the first ``-m`` and
     returned, so a second ``-m``, which git writes as its own paragraph,
     reached history unscanned under a verdict that called the message
     scanned. D2 reads the trailer block as the message's last paragraph,
     so the reader has to build the message git builds. Settled 2026-11-16,
     with each case below checked against git 2.50.
   - **Read.** Every ``-m`` and ``--message``, in every spelling git accepts
     (a value attached, ``-mfix``, inside a cluster, ``-am``, and an
     abbreviated long option, ``--mess=``), joined as git joins them: each
     value a paragraph, so the last ``-m`` is the last paragraph and the
     trailer block D2 reads. The ``-F`` file git reads, the last one named.
     Text the command names that git does not write is read too, each as a
     message of its own, on the noisy side: a value a ``--no-message``
     dropped, any other ``-F`` file (each of which must be readable). The
     trailer block is still read from what git writes. Each ``--trailer``
     value, read as a line of its own and never
     admitted under D2, on the noisy side. The commit's words run from
     ``commit`` to the operator that ends the shell command, and stop at
     ``--``. Words are read as bash hands them to git: quotes removed,
     ``$'...'`` decoded, a backslash-newline joined away; an operator
     character inside quotes is a word's (``-m ';'``), and a ``$(...)`` or
     backquoted substitution is one literal piece of its word whose
     parentheses end nothing. A ``git`` behind ``(`` or ``;`` with no space
     is a commit as surely as one behind a space.
   - **Refused, as an unreadable message is** (``-F -``, AUD-09): a message
     git opens an editor for (no ``-m``, ``-F`` or ``-C``, or ``-c``,
     ``--amend``, ``--squash`` and ``--fixup=amend:`` or ``reword:`` alone,
     or ``-e``; ``--no-edit`` turns it off and the last of the two wins), and
     a ``-t`` template git writes unedited. The remedy is ``-m``, and where
     git has a message to copy, ``--no-edit``.
   - **Refused, as a directory the hook cannot follow:** a relative ``-F``
     path after a ``cd`` or ``pushd`` earlier in the same command. The shell
     has not changed directory when the hook runs, so which file git reads
     cannot be told. The remedy is an absolute path.
   - **Refused, as git refuses them:** ``-m`` with ``-F``, ``-C`` or ``-c``;
     two of ``-C``, ``-c``, ``-F`` and ``--fixup``; ``--squash`` with
     ``--fixup``; ``--fixup=amend:`` or ``reword:`` with ``-m``. git makes no
     commit, and which message was meant cannot be told.
   - **Not read:** text git copies from a commit the repository already
     holds: ``-C``, the message ``--amend --no-edit`` keeps, the subject
     ``--fixup`` and ``--squash`` prefix, and what ``--fixup=amend:`` or
     ``reword:`` copies under ``--no-edit``; and what a merge or a pick
     leaves for ``--no-edit``. It puts nothing into history that is not
     there already. The ``-m`` text beside it is read. Nothing is demanded of
     a status run (``--dry-run``, ``--short``, ``--porcelain``, ``--long``,
     ``-h``), nor of a word ``commit`` that is not git's subcommand
     (``git log --grep commit``), though the messages after it are read.
   - **Out of scope:** what git takes from the configuration or the
     repository's hooks (``-s``, ``commit.template``, a
     ``prepare-commit-msg`` hook), and the identity in the commit's header
     (``--author``), which are the user's own and not content; a message in
     a shell expansion (``-m "$MSG"``, ``-m "$(...)"``), read as its literal
     text, and a second ``git commit`` in one command, each filed as its own
     defect; and every command other than ``git commit`` that writes a
     message (``git merge -m``, ``git tag -m``).
   - **Weighed against:** reading ``-C`` and ``--amend``'s message through
     git (it would refuse an amend over a commit made before the gate, for
     text history already holds); scanning an editor's seed and passing (the
     verdict would vouch for text the editor replaces); scanning both sides
     of a combination git refuses (no commit follows, so the scan answers a
     question nobody asked).
