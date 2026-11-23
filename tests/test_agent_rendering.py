"""The shipped neutral agents and the Claude Code renderer (ADR-012 D8, A6.2).

The golden half pins the migration: a shipped source rendered with no overlay
is the text A6.1 installed, byte for byte, with only the differences the A6
rulings asked for. Those are declared here, one by one, so a body change cannot
ride in under the format change unseen: the host-neutral rewrites of A6.2, and
the bodies moved onto the engine in A6.5, and the model prose deleted in A7.2.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from invariant.core.agents import parse
from invariant.hosts import assets, claude_code
from invariant.hosts.claude_code import (
    HOST_VOCABULARY,
    ClaudeCodeHost,
    render_agent,
)

FIXTURES = Path(__file__).parent / "fixtures" / "agents_a6_1"
LIBRARY = claude_code.DATA_DIR / "agents"
NAMES = sorted(p.stem for p in LIBRARY.glob("*.md"))

#: The two sentences in backlog-curator that named the host's question tool,
#: rewritten host-neutrally (A6 ruling 12). The meaning is kept: the parent
#: relays the interview questions to the user verbatim.
NEUTRALISED = {
    "backlog-curator": (
        (
            (
                "verbatim — using `AskUserQuestion` when the answers map cleanly to "
                "choices, otherwise as a plain numbered list."
            ),
            (
                "verbatim — as a multiple-choice question when the answers map "
                "cleanly to choices and your host offers one, otherwise as a plain "
                "numbered list."
            ),
        ),
        (
            (
                "  • Use AskUserQuestion when the answer maps cleanly to 2–4 choices\n"
                "    (severity, priority, item_type, in/out scope toggle)."
            ),
            (
                "  • Ask it as a multiple-choice question, where your host offers one,\n"
                "    when the answer maps cleanly to 2–4 choices (severity, priority,\n"
                "    item_type, in/out scope toggle)."
            ),
        ),
    ),
}

#: A6.5: the bodies onto the engine (ADR-012 D8, the A6 rulings 9 and 14).
#: The design agent reads `guidelines show --phase design --touched` before it
#: plans (a new section, `phase-1c`) and applies those rules at Boy-Scout time;
#: the review agent runs the review commands in order, submits one verdict per
#: owed rule, and pastes the engine's standings into its report. Both fall back
#: to the checklist on the engine's no-ruleset line. Two lines in
#: `bug-reproducer` were dead or misread by the command scan
#: (tests/test_agent_bodies.py): `session start` never took `--task`, and a
#: prose line opened on the word "invariant". Each pair is (A6.1's text, the
#: new text), applied after NEUTRALISED.
A6_5 = {
    "bug-reproducer": (
        (
            (
                "invariant session start repro-<bug_slug> --task <bug_slug>"
            ),
            (
                "invariant session start repro-<bug_slug>"
            ),
        ),
        (
            (
                "`fixed` is reachable **only from `reproduced`**. That is the flagship\n"
                "invariant of the whole lifecycle — a fix for an unreproduced defect proves"
            ),
            (
                "`fixed` is reachable **only from `reproduced`**. That is the\n"
                "flagship invariant of the whole lifecycle — a fix for an unreproduced defect proves"
            ),
        ),
    ),
    "compliance-reviewer": (
        (
            (
                "guidelines (the canonical compliance checklist in the architecture docs "
                "plus the engine's rules), resolves"
            ),
            (
                "guidelines (the engine's guidelines ruleset, or the canonical compliance "
                "checklist in the architecture docs where a project has none, plus the "
                "engine's rules), resolves"
            ),
        ),
        (
            (
                "## Phase 2 — Review (the canonical checklist)"
            ),
            (
                "## Phase 2 — Review (the guidelines through the engine, then the checklist)"
            ),
        ),
        (
            (
                "**Ruleset — single source of truth: the project's canonical compliance checklist in the architecture docs at `paths.docs`.**\n"
                "Read that doc now; it is the canonical checklist, shared with"
            ),
            (
                "**The guidelines come from the engine.** Run these in order, from the\n"
                "session's worktree:\n"
                "\n"
                "1. `invariant guidelines show --phase review --touched` prints, in full, every\n"
                "   review, landing and always rule the session's declared and changed files\n"
                "   bring into play. Read each one. What it prints decides the route, not your\n"
                "   judgement:\n"
                "   - **It prints rules** (a header naming the ruleset and its digest, then each\n"
                "     rule): the project has a guidelines ruleset. Continue with steps 2 to 4.\n"
                "     The engine answers for the guideline rules, and they are never\n"
                "     hand-marked.\n"
                "   - **It prints `no guidelines ruleset configured; nothing to show`:** the\n"
                "     project has no ruleset. Skip steps 2 to 4 and review against the\n"
                "     checklist below, marking every rule by hand.\n"
                "   - **It exits non-zero:** that is a refusal, not the no-ruleset case, so do\n"
                "     not fall back. A ruleset that does not load is a `[BLOCKER]`, and\n"
                "     `invariant check guidelines.ruleset_valid` names its problems.\n"
                "2. `invariant check guidelines.mechanical_clean` evaluates the pattern,\n"
                "   boundary and cochange rules over the change and names each finding by rule,\n"
                "   path and line. Each new finding is a `[BLOCKER]` or a `[WARN]` by its rule's\n"
                "   severity, resolved in Phase 3 like any other.\n"
                "3. `invariant guidelines verdicts show` lists the judgement rules the change\n"
                "   owes a verdict, each led by its standing: `current`, `stale`, `missing`, or\n"
                "   `engine` (the engine answered it, and it owes nothing).\n"
                "4. Judge each owed rule standing `missing` or `stale` against the diff, and\n"
                "   submit one verdict per rule, each with its own command:\n"
                "\n"
                "   ```\n"
                "   invariant guidelines verdict <rule-id> --status pass --cite <path> <first-line> <last-line>\n"
                "   invariant guidelines verdict <rule-id> --status fail --cite <path> <first-line> <last-line>\n"
                "   invariant guidelines verdict <rule-id> --status na --na-condition <condition-id>\n"
                "   ```\n"
                "\n"
                "   A citation names lines in a touched file, and for a rule whose scope is the\n"
                "   diff, lines the diff adds or changes. Where the rule accepts a test as\n"
                "   evidence, `--test <selector>` names one a recorded run in this pipeline\n"
                "   included. `na` takes only a condition the rule declares; there is no\n"
                "   free-text N/A. A refused verdict names the predicate that failed and its\n"
                "   fix: fix the evidence and submit again. A `fail` at blocker severity is a\n"
                "   `[BLOCKER]` for Phase 3, and once it is fixed the rule is submitted again.\n"
                "   Then run `invariant guidelines verdicts show` once more. What it prints is\n"
                "   what the Phase 9 report carries, pasted as printed.\n"
                "\n"
                "**The rest of the review is by hand.** `invariant rules list` shows the\n"
                "engine-enforced rules and each one's resolved level; `invariant check`\n"
                "evaluates them on demand. With no ruleset, the single source of truth is the\n"
                "project's canonical compliance checklist in the architecture docs at\n"
                "`paths.docs`. Read that doc now; it is the canonical checklist, shared with"
            ),
        ),
        (
            (
                "doc existed. `invariant rules list` shows the engine-enforced rules and each\n"
                "one's resolved level; `invariant check` evaluates them on demand."
            ),
            (
                "doc existed. With a ruleset, whatever that checklist holds that the ruleset\n"
                "does not carry is still reviewed here."
            ),
        ),
        (
            (
                "Evaluate **every** rule against the SESSION FILE SET and mark each one\n"
                "`[BLOCKER]`, `[WARN]`, `[OK]`, or `[N/A]`, using the severity the checklist and\n"
                "the enforcement profile assign. Coverage below the configured floor and\n"
                "unresolvable judgment calls are `[BLOCKER]`s — they cannot be silenced by\n"
                "re-review. The Phase 9 report must list each rule line individually (see its\n"
                'output contract); do not collapse to "all rules passed".'
            ),
            (
                "Evaluate **every** rule reviewed by hand against the SESSION FILE SET and mark\n"
                "each one `[BLOCKER]`, `[WARN]`, `[OK]`, or `[N/A]`, using the severity the\n"
                "checklist and the enforcement profile assign. Coverage below the configured\n"
                "floor and unresolvable judgment calls are `[BLOCKER]`s — they cannot be\n"
                "silenced by re-review. The Phase 9 report must list each rule line\n"
                'individually (see its output contract); do not collapse to "all rules passed".'
            ),
        ),
        (
            (
                "Re-run Phase 2 on the now-edited working tree. Any new violations introduced by Phase 3 fixes themselves must be resolved (loop back to Phase 3, up to 2 passes). If after 2 passes there are still `[BLOCKER]`s, bail.\n"
            ),
            (
                "Re-run Phase 2 on the now-edited working tree. With a ruleset, a Phase 3 fix that edited a cited file leaves that verdict `stale` in `invariant guidelines verdicts show`, and the rule is submitted again on the new lines. Any new violations introduced by Phase 3 fixes themselves must be resolved (loop back to Phase 3, up to 2 passes). If after 2 passes there are still `[BLOCKER]`s, bail.\n"
            ),
        ),
        (
            (
                '- Then the full Phase-2 review report: every rule you evaluated with `[BLOCKER]`, `[WARN]`, `[OK]`, `[N/A]`, or `[NEEDS-USER]` and a one-line finding (with file:line) for each non-`[OK]`/`[N/A]`. Do NOT collapse to "all rules passed" — list each rule line so the user can see what was checked.'
            ),
            (
                "- Then the `GUIDELINES` block: with a ruleset, the output of `invariant check guidelines.mechanical_clean` and of Phase 2's last `invariant guidelines verdicts show`, each pasted as printed and never re-marked by hand; with none, the no-ruleset line.\n"
                '- Then the full Phase-2 review report: every rule you evaluated by hand with `[BLOCKER]`, `[WARN]`, `[OK]`, `[N/A]`, or `[NEEDS-USER]` and a one-line finding (with file:line) for each non-`[OK]`/`[N/A]`. Do NOT collapse to "all rules passed" — list each rule line so the user can see what was checked.'
            ),
        ),
        (
            (
                "REVIEW (every checklist rule)\n"
                "  [BLOCKER] coverage   71% on BulkImportViewModel.<ext> — floor is <coverage_min>%"
            ),
            (
                "GUIDELINES (the engine's output, pasted as printed)\n"
                "  mechanical — invariant check guidelines.mechanical_clean:\n"
                "    <its output, as printed>\n"
                "  verdicts — invariant guidelines verdicts show:\n"
                "    <its output, as printed>\n"
                "  (with no ruleset: no guidelines ruleset configured; nothing to show)\n"
                "\n"
                "REVIEW (every rule reviewed by hand)\n"
                "  [BLOCKER] coverage   71% on BulkImportViewModel.<ext> — floor is <coverage_min>%"
            ),
        ),
        (
            (
                "REVIEW (every checklist rule)\n"
                "  [BLOCKER] migrations.safe-pattern   in-place schema alteration — migrations/M042.<ext>:14"
            ),
            (
                "GUIDELINES\n"
                "  <as above, or SKIPPED (bailed at Phase <N>)>\n"
                "\n"
                "REVIEW (every rule reviewed by hand)\n"
                "  [BLOCKER] migrations.safe-pattern   in-place schema alteration — migrations/M042.<ext>:14"
            ),
        ),
    ),
    "feature-architect": (
        (
            (
                "compliance checklist),"
            ),
            (
                "guidelines: the engine's ruleset, or the compliance checklist where there is none),"
            ),
        ),
        (
            (
                "\n"
                "---\n"
                "\n"
                "## Phase 2 — Plan against the diagrams"
            ),
            (
                "\n"
                "### Phase 1c — Read the design-phase guidelines (before planning)\n"
                "\n"
                "Before Phase 2, ask the engine which guidelines this change is held to while it\n"
                "is designed and built:\n"
                "\n"
                "```bash\n"
                "invariant guidelines show --phase design --touched\n"
                "```\n"
                "\n"
                "It prints, in full, the design and always rules that the session's declared\n"
                "and changed files bring into play; with no such files yet, every rule of those\n"
                "phases. Read each one. They are what Phase 2 plans against and what Phase 4\n"
                "applies to every touched file. What the command prints decides the route, not\n"
                "your judgement:\n"
                "\n"
                "- **It prints rules** (a header naming the ruleset and its digest, then each\n"
                "  rule): the project has a guidelines ruleset, and those rules are the\n"
                "  guidelines. Phase 4 applies them; do not read a second copy of them.\n"
                "- **It prints `no guidelines ruleset configured; nothing to show`:** the\n"
                "  project has no ruleset. Phase 4 falls back to the canonical compliance\n"
                "  checklist in the architecture docs at `paths.docs`.\n"
                "- **It exits non-zero:** that is a refusal, not the no-ruleset case, so do not\n"
                "  fall back. Report what it names. A ruleset that does not load is\n"
                "  `invariant check guidelines.ruleset_valid`'s finding to fix first.\n"
                "\n"
                "No gate records this read and none is owed: it is how the design gets its\n"
                "rules, and the review answers for them.\n"
                "\n"
                "---\n"
                "\n"
                "## Phase 2 — Plan against the diagrams"
            ),
        ),
        (
            (
                "compliance checklist."
            ),
            (
                "guidelines (the ruleset below)."
            ),
        ),
        (
            (
                "**Ruleset — single source of truth: the project's canonical compliance checklist in the architecture docs at `paths.docs`.**\n"
                "Read that doc now; it is the canonical checklist shared with `compliance-reviewer` — do **not** keep a private copy here (two inline copies drifted apart before the shared doc existed; that drift is why the single source exists). `invariant rules list` shows the engine-enforced subset and each rule's resolved level. At Boy-Scout time enforce the build-time subset on every touched file: clean architecture & dependency injection, migrations & data safety, the project's documented store/scope invariants, UI rules, testing rules, ecosystem gotchas, and code quality. The coverage floor is handled in Phase 6."
            ),
            (
                "**Ruleset — single source of truth: the rules Phase 1c printed; where it printed the no-ruleset line, the project's canonical compliance checklist in the architecture docs at `paths.docs`.**\n"
                "With a ruleset, apply every rule Phase 1c printed to every touched file, and for a touched file that read could not see yet, ask for its rules with `invariant guidelines show --phase design --files <path>...`. With no ruleset, read the checklist doc now; it is the canonical checklist shared with `compliance-reviewer` — do **not** keep a private copy here (two inline copies drifted apart before the shared doc existed; that drift is why the single source exists). `invariant rules list` shows the engine-enforced subset and each rule's resolved level. At Boy-Scout time enforce the build-time subset on every touched file: clean architecture & dependency injection, migrations & data safety, the project's documented store/scope invariants, UI rules, testing rules, ecosystem gotchas, and code quality. The coverage floor is handled in Phase 6."
            ),
        ),
    ),
}

#: A7.2: the model prose is deleted from two descriptions (the A7 ruling 5). A
#: tier says this now, and the source's `tier:` line never reaches the render.
A7_2 = {
    "bug-reproducer": (
        (
            (
                " MODEL: hunting a repro is exploratory; run on the session model unless "
                "the entry already names an exact in-target assertion."
            ),
            "",
        ),
    ),
    "feature-architect": (
        (
            (
                " MODEL: no hardcoded assignment — the parent scores the task's complexity "
                "per the host-injected rubric and passes `model` at invocation (session "
                "model for anything with design content; a smaller model only when every "
                "low-complexity signal holds)."
            ),
            "",
        ),
    ),
}


#: ADR-018 increment 5: the design agent's design phase ends by declaring its
#: decisions, and its report gains the `DECISIONS:` block (D9).
ADR_018_5 = {
    "feature-architect": (
        (
            (
                "8. **Docs to update** — the exact architecture-doc files under `paths.docs` and which diagram blocks change.\n"
            ),
            (
                "8. **Docs to update** — the exact architecture-doc files under `paths.docs` and which diagram blocks change.\n"
                "9. **Decisions** — end the design phase by declaring every decision it made on the session: `invariant session decisions add <DEC_id>` for one already a need (`invariant docs decision new \"<title>\"` mints a record page), or `invariant session decisions add --draft \"<one line>\"` for one not yet written as a need. A draft holds the landing (`docs.decisions_recorded`) until `invariant session decisions resolve <n> <DEC_id>` names its need.\n"
            ),
        ),
        (
            (
                "    - <paths.docs>/views doc (add DetailsSheet to view graph)\n\n"
                "BOY-SCOUT REFACTORS"
            ),
            (
                "    - <paths.docs>/views doc (add DetailsSheet to view graph)\n\n"
                "DECISIONS: (exactly what `invariant session decisions add` recorded)\n"
                "  - DEC_ADR_<nnn>_D<k>   <one line>\n"
                "  - draft 2              <one line>  (resolve before landing)\n"
                "  (or \"none\")\n\n"
                "BOY-SCOUT REFACTORS"
            ),
        ),
    ),
}

#: ADR-014 increment 4c-1: under a store docs lock, the design agent's doc
#: update moves from after the code to the end of design (D5), conditional on
#: the docs being kept in a store, so an in-repo project's Phase 7 is as it was.
ADR_014_4C1 = {
    "feature-architect": (
        (
            "the diagram edit lands in Phase 7).",
            (
                "the diagram edit lands in Phase 7, or at the end of design under a "
                "store docs lock — see Phase 7)."
            ),
        ),
        (
            "Failing test first, then the code, then verify green.\n",
            (
            "Failing test first, then the code, then verify green.\n\n"
            "**Under a store docs lock, Phase 7 comes first.** If the project keeps its "
            "docs in a store (`invariant config show` gives `records.docs: store`), do "
            "Phase 7 now, before the first advance out of design — see \"Under a store "
            "docs lock\" there.\n"
            ),
        ),
        (
            "skip this phase but say so explicitly in the report.\n",
            (
            "skip this phase but say so explicitly in the report.\n\n"
            "**Under a store docs lock, this phase ends design.** When the project keeps "
            "its docs in a store (`invariant config show` gives `records.docs: store`), "
            "reading them took their units of the store's docs lock, and the read, the "
            "design and the doc update are one cycle under that lock, so no other session "
            "designs against docs you are about to change. The doc update therefore moves "
            "from after the code to the end of design, after Phase 4 and before Phase 5's "
            "first advance:\n\n"
            "1. Write the doc changes Phase 2 section 8 names, in the store's main "
            "checkout, under the units you hold. A doc you did not read needs `invariant "
            "docs lock <area or path>` and a read first.\n"
            "2. Commit them in the store (`git -C <store root> commit`): `docs.updated` "
            "counts a store doc only when it is committed under a span of its unit that "
            "also holds your read of it, or is new in that span.\n"
            "3. Release: `invariant docs unlock`. It refuses a unit with uncommitted "
            "changes. `store.docs_lock_released` holds the move out of design until every "
            "unit is released.\n\n"
            "If you bail at Phase 3, release the units with no change. If the code later "
            "shows the design was wrong, lock the unit again, read it again and update it "
            "under the new span. In a project whose docs are in its repository, none of "
            "this applies, and this phase stays after the code.\n"
            ),
        ),
    ),
}

#: IDEA_052: a plain transition names its commit with --evidence alone
#: (DEC_ADR_022_D1), so the routing table and MARK ... FIXED pass it.
IDEA_052 = {
    "backlog-curator": (
        (
            (
                "  fixed`, prepend `**Fixed <date>** (commit <hash>).` to the body. Note the\n"
                "  lifecycle makes `fixed` reachable only from `reproduced` — a fix for an\n"
                "  unreproduced defect proves nothing, and the engine will reject the edge from\n"
                "  `filed`. If it rejects, report it: the bug needs `bug-reproducer` first, or\n"
                "  an explicit user override."
            ),
            (
                "  fixed --evidence <commit-hash>`, prepend `**Fixed <date>** (commit <hash>).`\n"
                "  to the body. `--evidence` records the commit on the history row, resolved\n"
                "  to its full hash; a hash that names no commit in the repository is refused\n"
                "  and nothing is written, so report it rather than dropping the flag. Note the\n"
                "  lifecycle makes `fixed` reachable only from `reproduced` — a fix for an\n"
                "  unreproduced defect proves nothing, and the engine will reject the edge from\n"
                "  `filed`, with or without `--evidence`. If it rejects, report it: the bug\n"
                "  needs `bug-reproducer` first, or an explicit user override."
            ),
        ),
    ),
    "bug-reproducer": (
        (
            "which the report names — `fixed` from `reproduced`;",
            "which the report names — `fixed --evidence <hash>` from `reproduced`;",
        ),
        (
            (
                "fixed --reason \"green since <hash>\"` | The reproduction is on record; "
                "the edge exists |"
            ),
            (
                "fixed --evidence <hash> --reason \"green since <hash>\"` | The "
                "reproduction is on record; the edge exists. `--evidence` records the "
                "fixing commit on the row, checked to resolve; no ruling is "
                "needed on an edge the machine has |"
            ),
        ),
        (
            (
                "refused, and its refusal names the ruled route; `--ruling` without\n"
                "`--evidence`, or the reverse, is refused too."
            ),
            (
                "refused, and its refusal names the ruled route; so is one with `--evidence`\n"
                "and no `--ruling`, because a commit alone records provenance and buys no\n"
                "route. `--ruling` without `--evidence` is refused too."
            ),
        ),
    ),
}

#: IDEA_051: field changes go through `item set` (DEC_ADR_023), so the curator
#: offers a shortcut for them and its operating rule names the command.
IDEA_051 = {
    "backlog-curator": (
        (
            "(drop / promote / mark done / mark fixed)",
            "(drop / promote / mark done / mark fixed / change a field)",
        ),
        (
            "  Normally written by `bug-reproducer`; accept it from the user too.\n",
            (
                "  Normally written by `bug-reproducer`; accept it from the user too.\n"
                "- `SET <ID> <field> — <reason>` (re-prioritise, re-batch, link a decision or\n"
                "  an element) → `invariant item set <ID>` with `--field <k>=<v>` to replace a\n"
                "  value, `--add <k>=<v>` or `--remove <k>=<v>` to edit a list field such as\n"
                "  `affects` or `decided_by`, or `--unset <k>` to drop one, always with\n"
                "  `--reason \"<why>\"`. Each change is recorded in the entry's history with the\n"
                "  date. `id` and `status` are refused (a status goes through `item\n"
                "  transition`), and so is a value naming a decision no recorded docs build\n"
                "  holds; nothing is written, so report the refusal. Re-prioritising and\n"
                "  re-batching still need the user's explicit signal (operating rules).\n"
            ),
        ),
        (
            (
                "- **Status transitions go through `invariant item transition`, always.** The\n"
                "  lifecycle FSM is the authority on legal edges; a rejected transition is a\n"
                "  finding to report, never an obstacle to hand-edit around.\n"
            ),
            (
                "- **Status transitions go through `invariant item transition`, and field\n"
                "  changes through `invariant item set`, always.** The lifecycle FSM is the\n"
                "  authority on legal edges; a rejected transition or field change is a\n"
                "  finding to report, never an obstacle to hand-edit around.\n"
            ),
        ),
    ),
}

#: BUG_126 (DEC_ADR_026): the gate receipt is the pipeline's coverage record,
#: so the shell recipe and the receipt file under .build/gate are gone: the
#: architect's last act is `invariant pipeline coverage`, and the reviewer's
#: fast path asks `invariant pipeline receipt`.
BUG_126 = {
    "feature-architect": (
        (
            (
                '### Gate receipt (lets compliance-reviewer skip a redundant rebuild)\n'
                '\n'
                'You and `compliance-reviewer` share one gate build directory (e.g. `.build/gate`\n'
                'under the repo — keep it out of version control), so its build is incremental on\n'
                'top of yours instead of a cold rebuild. Once coverage passes, write a receipt\n'
                'recording exactly what you gated so the reviewer can detect whether anything\n'
                'changed after you (e.g. the main agent edited a file post-handoff) and reuse\n'
                'your green build+coverage when nothing did.\n'
                '\n'
                'Build the file set as a shell ARRAY and expand it quoted. This is not a style\n'
                'preference: **zsh does not word-split an unquoted parameter**, so\n'
                '`-- $SESSION_FILES` hands git ONE bogus pathspec, the diff comes back empty,\n'
                'and `diff_sha` becomes the SHA-256 of the empty string whatever you changed.\n'
                '`git diff` exits 0 on a pathspec that matches nothing and prints no\n'
                'diagnostic, so nothing anywhere signals the failure. An array is correct in\n'
                'both bash and zsh and does not depend on knowing which shell the host runs.\n'
                '\n'
                '```bash\n'
                'SESSION_FILES=( <one touched source/test/doc file per element> )\n'
                'DIFF_SHA=$(git diff --no-color HEAD -- "${SESSION_FILES[@]}" | shasum -a 256 | cut -d\' \' -f1)\n'
                '\n'
                '# A receipt over a non-empty file set with a zero-line diff is suspicious on\n'
                '# its face. This constant is `printf "" | shasum -a 256` — the hash of nothing.\n'
                'EMPTY_SHA=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n'
                'if [ "$DIFF_SHA" = "$EMPTY_SHA" ] && [ ${#SESSION_FILES[@]} -gt 0 ]; then\n'
                '  echo "NOT writing a receipt: the diff over ${#SESSION_FILES[@]} changed file(s) hashed empty"\n'
                'else\n'
                '  {\n'
                '    echo "head $(git rev-parse HEAD)"\n'
                '    echo "diff_sha $DIFF_SHA"\n'
                '    echo "coverage <File1>=<pct> <File2>=<pct> ..."\n'
                '  } > .build/gate/gate-receipt.txt\n'
                'fi\n'
                '```\n'
                '\n'
                '`diff_sha` is the signature compliance-reviewer compares against. If the tree it\n'
                'sees is byte-identical (same `head` + same `diff_sha`), it trusts these coverage\n'
                'numbers and runs only an incremental smoke build. If it differs, the receipt is\n'
                'ignored and the reviewer runs its full build+test+coverage. The receipt is\n'
                "advisory — never a substitute for the reviewer's own gates when anything moved.\n"
                '\n'
                'Never write a receipt you could not compute. A receipt whose signature is the\n'
                'empty-string hash matches every other broken receipt, so the fast path fires on\n'
                'a tree that has moved — which is the one case the receipt exists to detect.\n'
            ),
            (
                '### Gate receipt (lets compliance-reviewer skip a redundant rebuild)\n'
                '\n'
                'You and `compliance-reviewer` share one gate build directory (e.g. `.build/gate`\n'
                'under the repo — keep it out of version control), so its build is incremental on\n'
                'top of yours instead of a cold rebuild. What lets it also skip re-running your\n'
                "tests and coverage is the receipt, and the receipt is the engine's record of\n"
                'your coverage run: there is nothing for you to write.\n'
                '\n'
                '`invariant pipeline coverage` signs the tree it measures, before the run and\n'
                'after it: the commit, every uncommitted change and every untracked file git\n'
                'does not ignore, by content, so new, modified and deleted files all count.\n'
                '`invariant pipeline receipt` checks that record against the tree as it is now.\n'
                'It matches only when the coverage run is on record, the tree held still for the\n'
                'whole run, the tree has not moved since, the suite passed and\n'
                '`coverage.meets_floor` passes. It names each condition that does not hold.\n'
                '\n'
                'So make the recorded coverage run your last act on the tree. Every edit comes\n'
                'first, the Phase 7 doc changes and any `invariant docs decision new` page\n'
                'included; an edit after the run moves the tree and costs the reviewer its full\n'
                'run. Then confirm:\n'
                '\n'
                '```bash\n'
                'invariant pipeline coverage\n'
                'invariant pipeline receipt\n'
                '```\n'
                '\n'
                'Under a sweep cadence (Phase 5), the run is the scoped one, `invariant pipeline\n'
                "coverage --selector <the touched files' tests>`; the receipt reads whichever\n"
                'run is on record. A NO MATCH here is a finding: fix what it names (an edit\n'
                'after the run means running coverage again) rather than handing off a receipt\n'
                'the reviewer will refuse. Never stage, intent-to-add or otherwise touch the\n'
                'index to make a file count: an untracked file is already signed by its content.\n'
            ),
        ),
        (
            # The architect's Phase 7 sends it back to the receipt step: the
            # recorded coverage run follows the doc edits, never precedes them.
            ('If you bail at Phase 3, release the units with no change. If the code later shows the design was wrong, lock the unit again, read it again and update it under the new span. In a project whose docs are in its repository, none of this applies, and this phase stays after the code.\n'),
            ('If you bail at Phase 3, release the units with no change. If the code later shows the design was wrong, lock the unit again, read it again and update it under the new span. In a project whose docs are in its repository, none of this applies, and this phase stays after the code.\n\n**Then the recorded coverage run, again if this phase edited the tree.** The\nGate receipt step of Phase 6 is your last act on the tree, so an edit made here\nafter your Phase 6 run (a doc, a diagram, an `invariant docs decision new` page)\nmeans running `invariant pipeline coverage` and then\n`invariant pipeline receipt` now. Hand off only on a MATCH: a run taken before\nthese edits no longer matches the tree, and the reviewer pays a full run for it.\n'),
        ),
    ),
    "compliance-reviewer": (
        (
            (
                '### 5a — Gate-receipt fast path (skip a redundant cold rebuild)\n'
                '\n'
                '`feature-architect` shares your gate build directory (e.g. `.build/gate`) and,\n'
                'when its coverage gate passed, left a receipt at `.build/gate/gate-receipt.txt`\n'
                'recording the `head` SHA, the `diff_sha` of the working tree it gated, and\n'
                'per-file coverage. If the tree you see is byte-identical, its green build +\n'
                'coverage still hold and re-deriving them from scratch is pure duplication.\n'
                'Check:\n'
                '\n'
                'Use the same construction the architect writes with — a shell ARRAY, expanded\n'
                'quoted. An unquoted `-- $SESSION_FILES` is not word-split by zsh, so git gets\n'
                'one bogus pathspec, the diff comes back empty and exits 0, and the hash is the\n'
                'SHA-256 of the empty string. Both sides computing it that way made\n'
                '`WANT_DIFF == GOT_DIFF` hold unconditionally, so this fast path fired on every\n'
                'tree, moved or not.\n'
                '\n'
                '```bash\n'
                'RECEIPT=.build/gate/gate-receipt.txt\n'
                '# `printf "" | shasum -a 256` — the hash of nothing. A receipt carrying it was\n'
                '# computed over no diff at all and vouches for nothing.\n'
                'EMPTY_SHA=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n'
                'if [ -f "$RECEIPT" ]; then\n'
                '  SESSION_FILES=( <one element per file in YOUR session file set> )\n'
                '  WANT_HEAD=$(git rev-parse HEAD)\n'
                '  WANT_DIFF=$(git diff --no-color HEAD -- "${SESSION_FILES[@]}" | shasum -a 256 | cut -d\' \' -f1)\n'
                '  GOT_HEAD=$(awk \'$1=="head"{print $2}\' "$RECEIPT")\n'
                '  GOT_DIFF=$(awk \'$1=="diff_sha"{print $2}\' "$RECEIPT")\n'
                '  # Either side landing on the empty hash means the signature was not computed.\n'
                '  # Ignore the receipt and run the full gates.\n'
                '  if [ "$WANT_DIFF" = "$EMPTY_SHA" ] || [ "$GOT_DIFF" = "$EMPTY_SHA" ]; then\n'
                '    GOT_DIFF=ignored\n'
                '  fi\n'
                'fi\n'
                '```\n'
                '\n'
                '- **Match** (`WANT_HEAD == GOT_HEAD` AND `WANT_DIFF == GOT_DIFF`, computed over\n'
                '  your SESSION FILE SET): the architect already gated this exact tree. Run an\n'
                '  **incremental** build against the shared gate directory as a smoke check\n'
                "  (warm — near-no-op), then in Phase 6 reuse the receipt's coverage numbers\n"
                '  instead of re-running the full coverage pass. Note `[fast-path: reused\n'
                "  architect gate <head>]` in the report's GATES block.\n"
                '- **Mismatch or no receipt** (the main agent edited a file after the architect,\n'
                '  the architect bailed, or there was no architect run): ignore the receipt and\n'
                '  run the **full** build + Phase 6 coverage below. This is the default — when in\n'
                '  doubt, do the full run. The receipt is advisory and never replaces your gate\n'
                '  when anything moved.\n'
                '\n'
            ),
            (
                '### 5a — Gate-receipt fast path (skip a redundant cold rebuild)\n'
                '\n'
                '`feature-architect` shares your gate build directory (e.g. `.build/gate`), and\n'
                'its recorded coverage run is the receipt: `invariant pipeline coverage` signed\n'
                'the tree it measured, the commit, every uncommitted change and every untracked\n'
                'file git does not ignore, by content. If the tree you see is that tree, its\n'
                'green build and coverage still hold and re-deriving them from scratch is pure\n'
                'duplication. Ask the engine:\n'
                '\n'
                '```bash\n'
                'invariant pipeline receipt\n'
                '```\n'
                '\n'
                "It exits 0 and prints `receipt: MATCH` with the run's command, scope and\n"
                'per-file coverage only when a coverage run is on record, the tree held still\n'
                'for the whole run, the tree has not moved since (committed or not, new files\n'
                'included), the suite passed and `coverage.meets_floor` passes now. Otherwise it\n'
                'exits 1, prints `receipt: NO MATCH` and names each condition that failed. It\n'
                'writes nothing, so it needs no permission beyond running `invariant`.\n'
                '\n'
                '- **Match** (exit 0): the architect already gated this exact tree. Run an\n'
                '  **incremental** build against the shared gate directory as a smoke check\n'
                "  (warm — near-no-op), then in Phase 6 reuse the receipt's coverage numbers\n"
                '  instead of re-running the full coverage pass. Note `[fast-path: reused\n'
                "  architect gate <commit>]` in the report's GATES block.\n"
                '- **No match** (exit 1: the main agent or your Phase 3 edited a file after the\n'
                "  architect's run, the architect bailed, or there was no architect run): run\n"
                '  the **full** build + Phase 6 coverage below. This is the default — when in\n'
                '  doubt, do the full run. The receipt is advisory and never replaces your gate\n'
                '  when anything moved.\n'
                '\n'
            ),
        ),
        (
            (
                '**Fast path (Phase 5a matched):** the architect already ran tests + coverage on\n'
                'this exact tree. Re-run only the touched-file test classes once as a smoke check\n'
                '(a warm build cache makes this cheap), and take per-file coverage from the gate\n'
                'receipt rather than re-deriving it. If that smoke run shows any failure, fall\n'
                'back to the full run below. Otherwise skip straight to the doc rebuild /\n'
                "Phase 7 with the receipt's coverage numbers in the report."
            ),
            (
                '**Fast path (Phase 5a matched):** the architect already ran tests + coverage on\n'
                'this exact tree. Re-run only the touched-file test classes once as a smoke check\n'
                '(a warm build cache makes this cheap), and take per-file coverage from\n'
                '`invariant pipeline receipt` rather than re-deriving it. If that smoke run shows\n'
                'any failure, fall back to the full run below. Otherwise skip straight to the doc\n'
                "rebuild / Phase 7 with the receipt's coverage numbers in the report."
            ),
        ),
    ),
}

#: Scoped runs per change (DEC_ADR_010_D8, D10): every recorded run selects the
#: change's affected tests, the touched files' tests and those of the modules
#: that import a touched module, with or without a sweep cadence. A full run
#: is owed only for a touched held path; the whole suite runs on a schedule
#: outside working hours, and a project with no cadence is told to set one.
#: The reviewer's "the full suite is CI's job" rule goes: no project is
#: assumed to have a CI.
SCOPED_RUNS = {
    "feature-architect": (
        (
            (
                "| `red` | `invariant pipeline test --phase red` (add `--selector <the new test>` "
                "to keep it fast) | The failing run `test.new_test_fails` demands |\n"
                "| `green` | `invariant pipeline test --phase green` | The passing run "
                "`test.all_pass` demands |\n"
                "| `refactor` | `invariant pipeline test --phase refactor` | That the cleanup "
                "kept the suite green |\n"
                "| `reviewing` | `invariant pipeline test` then `invariant pipeline coverage` | "
                "The final green run, and the per-file numbers `coverage.meets_floor` reads |\n"
            ),
            (
                "| `red` | `invariant pipeline test --phase red --selector <the new test>` | "
                "The failing run `test.new_test_fails` demands |\n"
                "| `green` | `invariant pipeline test --phase green --selector <the affected "
                "tests>` | The passing run `test.all_pass` demands |\n"
                "| `refactor` | `invariant pipeline test --phase refactor --selector <the "
                "affected tests>` | That the cleanup kept the affected tests green |\n"
                "| `reviewing` | `invariant pipeline test --selector <the affected tests>` then "
                "`invariant pipeline coverage --selector <the affected tests>`, or the bare "
                "forms where a held path owes a full run (below) | The final green run, and "
                "the per-file numbers `coverage.meets_floor` reads |\n"
            ),
        ),
        (
            (
                "**Under a sweep cadence, every run in that table is scoped.** When `invariant\n"
                "config show` reports `methodology.gates.schedule.cadence_days` set, the\n"
                "project has moved whole-suite and whole-scheme evidence to a scheduled sweep\n"
                "(`invariant sweep run`), and a full run on a change is exactly what\n"
                "`sweep.no_full_run` reports (or refuses, at `block`). Record every phase with\n"
                "`invariant pipeline test --selector <the touched files' tests>` and coverage\n"
                "with `invariant pipeline coverage --selector <the same>` — never the bare\n"
                "forms. A scheduled `coverage.meets_floor` reads those scoped figures as\n"
                "advisory (a lower bound, shown beside the last sweep's figure for the file),\n"
                "and a scheduled `test.all_pass` still fails on a red scoped run. The one\n"
                "exception is a touched path under `methodology.gates.schedule.held_paths`: it\n"
                "owes a FULL run before merge, so record the bare `invariant pipeline test` /\n"
                "`invariant pipeline coverage` for it, which `sweep.no_full_run` then answers\n"
                "clean. A `sweep.fresh` warning beside a gate says the last sweep is late; it\n"
                "never blocks, and running the sweep is the project's job, not this change's.\n"
            ),
            (
                "**Every run in that table is scoped to the change, with or without a sweep\n"
                "cadence.** The affected tests are the test file paired with every touched\n"
                "file, every touched test file, and the tests of every module that imports a\n"
                "touched module (its reverse dependencies, read from the dependency diagrams\n"
                "in the architecture docs, not from a search of the code). Pass each as a\n"
                "`--selector`. Never run the whole suite on a change: it runs on a schedule\n"
                "outside working hours, through `invariant sweep run` on the project's cadence\n"
                "(`methodology.gates.schedule.cadence_days`).\n"
                "\n"
                "The one exception is a touched path under\n"
                "`methodology.gates.schedule.held_paths`. The project holds those paths back\n"
                "from its cadence because a change to one can alter what every test sees, so\n"
                "it owes a full run, the bare `invariant pipeline test` and `invariant\n"
                "pipeline coverage`, once, on the final tree: a scheduled `test.all_pass`\n"
                "fails on a scoped green and `coverage.meets_floor` on a scoped coverage run\n"
                "for it, and `sweep.no_full_run` answers that full run clean.\n"
                "\n"
                "If `invariant config show` reports `cadence_days` null, nothing runs the\n"
                "whole suite. Still run scoped only, and say so in the report: recommend\n"
                "setting `methodology.gates.schedule.cadence_days` so a sweep scheduled for\n"
                "the evening holds the gates. Do not run the whole suite in its place.\n"
                "\n"
                "Under a cadence, any other full run on a change is exactly what\n"
                "`sweep.no_full_run` reports (or refuses, at `block`). A scheduled\n"
                "`coverage.meets_floor` reads the scoped figures as advisory (a lower bound,\n"
                "shown beside the last sweep's figure for the file), and a scheduled\n"
                "`test.all_pass` still fails on a red scoped run. A `sweep.fresh` warning\n"
                "beside a gate says the last sweep is late; it never blocks, and running the\n"
                "sweep is the project's job, not this change's.\n"
            ),
        ),
        (
            (
                "Under a sweep cadence (Phase 5), the run is the scoped one, `invariant pipeline\n"
                "coverage --selector <the touched files' tests>`; the receipt reads whichever\n"
                "run is on record."
            ),
            (
                "The coverage run takes `--selector <the affected tests>` as every run in\n"
                "Phase 5 does, and is bare as above only where a held path owes a full run\n"
                "(Phase 5); the receipt reads whichever run is on record."
            ),
        ),
    ),
    "compliance-reviewer": (
        (
            (
                "or the equivalent multi-selection form) — never the bare full-suite invocation."
            ),
            (
                "or the equivalent multi-selection form), recorded with `invariant pipeline\n"
                "test --selector <affected tests>` and `invariant pipeline coverage --selector\n"
                "<affected tests>` — the bare full-suite forms only where a held path owes a\n"
                "full run (below)."
            ),
        ),
        (
            (
                "**Scope — affected set, never the full suite locally.** The full suite is the "
                "job of CI, which runs the entire scheme + the coverage-regression budget on "
                "every push to the main branch and on PRs. Re-running it here just duplicates "
                "that long run, so DO NOT. Instead run, via test selection:\n"
                "\n"
                "1. The test class for **every touched source file** (the paired test file per "
                "project convention).\n"
                "2. The test classes that **exercise the changed component** — its reverse "
                "dependencies per the dependency graph in the architecture docs (the components "
                "whose diagrams have an arrow into a touched file). Read the graph; do not "
                "file-search for callers.\n"
                "\n"
                "If a touched file genuinely has cross-cutting reverse deps spanning most of the "
                "suite (rare — e.g., the dependency container or the central persistence store), "
                "run that file's direct test class + the handful of integration classes named "
                "in its doc section, and note in the report that the full regression net is "
                "delegated to CI on push. Never expand to the bare full-suite run as the local "
                "gate.\n"
            ),
            (
                "**Scope — the affected set, never the full suite on a change.** The whole "
                "suite runs on a schedule outside working hours, through `invariant sweep run` "
                "on the project's cadence (`methodology.gates.schedule.cadence_days`), with or "
                "without a cadence set. Select, and pass each as a `--selector`:\n"
                "\n"
                "1. The tests of **every touched file** (the paired test file per project "
                "convention), and every touched test file.\n"
                "2. The tests of **every module that imports a touched module** — its reverse "
                "dependencies per the dependency graph in the architecture docs (the components "
                "whose diagrams have an arrow into a touched file). Read the graph; do not "
                "file-search for callers.\n"
                "\n"
                "If a touched file genuinely has cross-cutting reverse deps spanning most of the "
                "suite (rare — e.g., the dependency container or the central persistence store), "
                "run that file's direct tests + the handful of integration tests named in its "
                "doc section, and note in the report that the rest of the regression net is the "
                "scheduled sweep's.\n"
                "\n"
                "A full run, the bare `invariant pipeline test` and `invariant pipeline "
                "coverage` once on the final tree, is owed only for a touched path under "
                "`methodology.gates.schedule.held_paths` (below); a Phase 5a receipt whose run "
                "was that full run already carries it. If `cadence_days` is null, nothing runs "
                "the whole suite: still run scoped only, say so in the report, and recommend "
                "setting `cadence_days` so a sweep scheduled for the evening holds the gates. "
                "Do not run the whole suite in its place.\n"
            ),
        ),
        (
            (
                "Record this phase's evidence scoped, with `invariant pipeline test --selector "
                "<affected tests>` and `invariant pipeline coverage --selector <affected tests>` "
                "— never the bare forms."
            ),
            (
                "Record this phase's evidence scoped, with `invariant pipeline test --selector "
                "<affected tests>` and `invariant pipeline coverage --selector <affected tests>` "
                "— never the bare forms, except for a held path."
            ),
        ),
    ),
}

#: The sections each shipped agent offers an overlay, in order. A rename here
#: breaks every overlay that names the old id, so it is pinned.
ANCHORS = {
    "backlog-curator": (
        "intro",
        "phase-0",
        "phase-1",
        "phase-2",
        "phase-3",
        "phase-4",
        "phase-5",
        "phase-6",
        "phase-7",
        "phase-8",
        "operating-rules",
        "edge-cases",
    ),
    "bug-reproducer": (
        "intro",
        "outcomes",
        "phase-0",
        "phase-1",
        "phase-2",
        "phase-3",
        "phase-4",
        "phase-5",
        "phase-6",
        "phase-7",
        "phase-8",
        "report-format",
        "operating-constraints",
    ),
    "compliance-reviewer": (
        "intro",
        "phase-0",
        "phase-0b",
        "phase-1",
        "phase-2",
        "phase-3",
        "phase-4",
        "phase-5",
        "phase-6",
        "phase-7",
        "phase-8",
        "phase-8b",
        "phase-8e",
        "phase-8c",
        "phase-8d",
        "phase-9",
        "operating-constraints",
    ),
    "feature-architect": (
        "intro",
        "phase-1",
        "phase-1b",
        "phase-1c",
        "phase-2",
        "phase-3",
        "phase-4",
        "phase-5",
        "phase-6",
        "phase-7",
        "phase-8",
        "phase-9",
        "operating-constraints",
    ),
}


def one_digest_copy(text):
    """A copy as A6.1 or A6.2 installed it: a one-digest marker holding the
    digest of the very text written. A6.3 writes three, and must still read
    these with no history (the A6 ruling 1)."""
    from invariant.hosts import assets

    front, body = assets.split_frontmatter(text)
    marker = f"{assets.MARKER_PREFIX} {assets.digest_of(text)}"
    return "\n".join([assets.FENCE, marker, *front, assets.FENCE, body])



#: ADR-027 increment 2 (DEC_ADR_027_D22): a session queues its successor and the
#: oracle starts it. The parent names it under THEN: and reports a stop; the
#: reviewer queues it before the teardown and commits the repository queue in
#: the memory/config sync (ruling 7, D8; the user's answers of 2026-11-18).
QUEUE_SUCCESSOR = {
    "compliance-reviewer": (
        (
            (
                'Do NOT re-invoke this subagent to "finish the merge", and do not merge or '
                "tear down by hand."
            ),
            (
                'Do NOT re-invoke this subagent to "finish the merge", and do not merge or '
                "tear down by hand. (5) Name the work that should run after this session under "
                "a `THEN:` header (`THEN: <ref> | <brief>`, with `in <project>` when it is "
                "another project's); in FINAL mode the subagent puts it on the queue with "
                "`invariant queue add` before the teardown, and the oracle starts it. Never "
                "start the next session yourself. (6) When you stop for the user, run "
                "`invariant session report --outcome needs-user --note \"<why>\"`; when the "
                "session ends unable to land, `--outcome blocked`. Either pauses the project's "
                "chain until the user resumes it. The subagent never reports."
            ),
        ),
        (
            (
                "the memory index (`paths.memory_index`), and the host config directory "
                "(agents, hooks, settings, the manifest)."
            ),
            (
                "the memory index (`paths.memory_index`), the host config directory (agents, "
                "hooks, settings, the manifest), and the queue file (`paths.queue`) when "
                "`invariant config show` reports `records.queue = 'repo'`."
            ),
        ),
        (
            (
                "Commit with title style `Memory: <slug> — <hook>` or `Config: <area> — "
                "<change>`, same trailer convention as Phase 8."
            ),
            (
                "Commit with title style `Memory: <slug> — <hook>`, `Config: <area> — "
                "<change>` or `Queue: <change>`, same trailer convention as Phase 8."
            ),
        ),
        (
            "A scope-contamination bail aborts only the memory commit, never the merge.\n",
            (
                "A scope-contamination bail aborts only the memory commit, never the merge.\n"
                "\n"
                "**The successor (FINAL mode only, after the merge and before Phase 8d).** When "
                "the invocation names one under `THEN:`, queue it: `invariant queue add <ref> "
                "--brief \"<brief>\"`, adding `--project <project>` when it names another "
                "project. Queue it before the teardown, so the oracle never sees the project "
                "idle in between. Never run `invariant oracle start-session` or `invariant "
                "session handoff` for it: the oracle starts queued work. A refusal (already "
                "queued, a closed item, a privacy finding) is `[WARN] successor not queued — "
                "<reason>` and does not stop the teardown. With no `THEN:`, queue nothing. "
                "Record it as `SUCCESSOR` under `PLAN` in the Phase 9 report: the entry id, "
                "`none named`, or the refusal.\n"
            ),
        ),
    ),
}


#: ADR-032 increment 6 (DEC_ADR_032_I6): every queue edit renders the plan page
#: beside the queue, so the reviewer's memory/config sync commits the two
#: together.
ADR_032_I6 = {
    "compliance-reviewer": (
        (
            (
                "and the queue file (`paths.queue`) when `invariant config show` reports "
                "`records.queue = 'repo'`."
            ),
            (
                "and the queue file (`paths.queue`) with its plan page (`plan.rst` in the "
                "docs project's source directory, which every queue edit renders again) "
                "when `invariant config show` reports `records.queue = 'repo'`."
            ),
        ),
    ),
}


#: ADR-032 increment 4 (DEC_ADR_032_D10, I4): the design agent gives each
#: component its layer and each arrow its direction, writes a SOLID block naming
#: the five principles, names each new abstraction's pattern, and edits the
#: design documents before leaving design; its Boy-Scout pass applies the five
#: principles. The review agent answers each `solid.*` rule by name, the layer
#: findings, the pattern rule and both docs-first checks. Both propose every
#: exception on a user-owned rule with the `guidelines except` line.
ADR_032_I4 = {
    "compliance-reviewer": (
        (
            (
                '   - **It prints rules** (a header naming the ruleset and its digest, then each\n'
                '     rule): the project has a guidelines ruleset. Continue with steps 2 to 4.\n'
                '     The engine answers for the guideline rules, and they are never\n'
            ),
            (
                '   - **It prints rules** (a header naming the ruleset and its digest, then each\n'
                '     rule): the project has a guidelines ruleset. Continue with steps 2 to 5.\n'
                '     The engine answers for the guideline rules, and they are never\n'
            ),
        ),
        (
            (
                '   - **It prints `no guidelines ruleset configured; nothing to show`:** the\n'
                '     project has no ruleset. Skip steps 2 to 4 and review against the\n'
                '     checklist below, marking every rule by hand.\n'
                '   - **It exits non-zero:** that is a refusal, not the no-ruleset case, so do\n'
            ),
            (
                '   - **It prints `no guidelines ruleset configured; nothing to show`:** the\n'
                '     project has no ruleset. Skip steps 2 to 5 and review against the\n'
                '     checklist below, marking every rule by hand, the five SOLID principles\n'
                '     included (see "With no ruleset" in step 5).\n'
                '   - **It exits non-zero:** that is a refusal, not the no-ruleset case, so do\n'
            ),
        ),
        (
            (
                '   what the Phase 9 report carries, pasted as printed.\n'
                '\n'
            ),
            (
                '   what the Phase 9 report carries, pasted as printed.\n'
                '5. **The default guidelines, answered by name.** Before leaving the review,\n'
                '   confirm each of these is answered, and say so in the report:\n'
                '   - **SOLID, one cited verdict per principle:** `solid.single-responsibility`\n'
                '     (single responsibility), `solid.open-closed` (open/closed),\n'
                '     `solid.liskov-substitution` (Liskov substitution),\n'
                '     `solid.interface-segregation` (interface segregation) and\n'
                '     `solid.dependency-inversion` (dependency inversion), each judged against\n'
                "     the diff and the architect's SOLID block, each cited to the lines it\n"
                '     rests on. A `solid.*` rule the ruleset does not carry (the user retired\n'
                '     or paused it, or it was never adopted) is reported as not in the\n'
                "     ruleset and owes nothing: the user's ruleset decides.\n"
                '   - **The layers:** every finding of the derived\n'
                '     `layers.<layer id>-depends-inward` rules out of step 2, by rule, path\n'
                "     and line. Each is a `[BLOCKER]` at the map's severity; a baselined one\n"
                '     is not new. A changed file in no layer is named as unmapped, never a\n'
                '     finding.\n'
                '   - **Patterns:** the `patterns.named-or-declined` verdict, citing the\n'
                "     design's naming of each new abstraction's catalogue pattern\n"
                '     (`patterns.rst#<id>`) or its sentence on why none fits; where the\n'
                '     ruleset names no catalogue, the design says `no catalogue configured`.\n'
                '   - **Docs first:** the `docs.design-before-code` verdict (the design\n'
                '     documents describe the change and were written before it), and the\n'
                "     engine's own record of when they changed:\n"
                '\n'
                '     ```\n'
                '     invariant check docs.ahead_of_code\n'
                '     ```\n'
                '\n'
                '     Paste what it prints. A `fail` (the design documents had not changed\n'
                '     when the build began) is a `[BLOCKER]` you never force past and cannot\n'
                "     repair by editing docs now: report it `[NEEDS-USER]` with the gate's own\n"
                '     fix lines. Its N/A (no design state in the methodology, no snapshot) is\n'
                "     the engine's, pasted as printed.\n"
                '\n'
                '   **A rule the user owns is never N/A and never waived.** A rule marked\n'
                '   `owner: user` (`Owner: user` in `guidelines show`) is answered `pass` or\n'
                "   `fail`, or N/A only where the engine's own mechanical condition answers\n"
                '   it; never submit `--status na` on one (the engine refuses it). A finding\n'
                '   you would otherwise waive is never answered N/A, never downgraded and\n'
                '   never left out of the report. There are three routes past it, and only\n'
                '   the first is yours:\n'
                '\n'
                '   1. **Fix the code** (Phase 3).\n'
                '   2. **A user-ruled exception.** Put it under `PROPOSED EXCEPTIONS` in the\n'
                '      report: the rule, the path, the finding, why an exception is right\n'
                '      here, and the exact line for the user to run, with `--by` left for the\n'
                '      user:\n'
                '\n'
                '      ```\n'
                '      invariant guidelines except <rule-id> <path> [--fingerprint <fp>] --by <who> --reason <why>\n'
                '      ```\n'
                '\n'
                '   3. **The user changes the rule**, where the rule itself looks wrong for\n'
                '      this project rather than for this one path. Propose it the same way:\n'
                '\n'
                '      ```\n'
                '      invariant guidelines loosen <rule-id> --to warn|off --by <who> --reason <why>\n'
                '      invariant guidelines retire <rule-id> --by <who> --reason <why>\n'
                '      ```\n'
                '\n'
                '   Never run any of the three commands yourself: they ask for the user at a\n'
                '   terminal. Until the user rules, the finding stays a `[BLOCKER]` at its\n'
                '   severity and the run bails `[NEEDS-USER]` (Phase 3).\n'
                '\n'
                '   **With no ruleset**, nothing can be excepted. Review the five principles\n'
                '   by hand as checklist lines, named one by one, at the severity the\n'
                '   checklist gives them or `[WARN]` where it names none; a finding you\n'
                '   cannot fix is `[NEEDS-USER]`, and the report names\n'
                '   `invariant guidelines adopt` as how the project gets the default rules.\n'
                '\n'
            ),
        ),
        (
            (
                '- Touched file in a scoped-refactor registry entry — report the recommended consolidation from the registry doc and stop; do not unify the family at commit time.\n'
                "- Any `[BLOCKER]` you don't know how to fix safely\n"
            ),
            (
                '- Touched file in a scoped-refactor registry entry — report the recommended consolidation from the registry doc and stop; do not unify the family at commit time.\n'
                '- A finding on an `owner: user` rule that you cannot fix: list it under `PROPOSED EXCEPTIONS` with its `invariant guidelines except` line (Phase 2, step 5), never as `[N/A]`\n'
                "- Any `[BLOCKER]` you don't know how to fix safely\n"
            ),
        ),
        (
            (
                "- Then the `GUIDELINES` block: with a ruleset, the output of `invariant check guidelines.mechanical_clean` and of Phase 2's last `invariant guidelines verdicts show`, each pasted as printed and never re-marked by hand; with none, the no-ruleset line.\n"
                '- Then the full Phase-2 review report: every rule you evaluated by hand with `[BLOCKER]`, `[WARN]`, `[OK]`, `[N/A]`, or `[NEEDS-USER]` and a one-line finding (with file:line) for each non-`[OK]`/`[N/A]`. Do NOT collapse to "all rules passed" — list each rule line so the user can see what was checked.\n'
            ),
            (
                "- Then the `GUIDELINES` block: with a ruleset, the output of `invariant check guidelines.mechanical_clean` and of Phase 2's last `invariant guidelines verdicts show`, each pasted as printed and never re-marked by hand; with none, the no-ruleset line.\n"
                '- Then the `DEFAULT GUIDELINES` block (Phase 2, step 5: each `solid.*` rule by name, the layer findings, the pattern rule and both docs-first checks) and the `PROPOSED EXCEPTIONS` block, each finding with its `invariant guidelines except` line, or "none".\n'
                '- Then the full Phase-2 review report: every rule you evaluated by hand with `[BLOCKER]`, `[WARN]`, `[OK]`, `[N/A]`, or `[NEEDS-USER]` and a one-line finding (with file:line) for each non-`[OK]`/`[N/A]`. Do NOT collapse to "all rules passed" — list each rule line so the user can see what was checked.\n'
            ),
        ),
        (
            (
                '  (with no ruleset: no guidelines ruleset configured; nothing to show)\n'
                '\n'
            ),
            (
                '  (with no ruleset: no guidelines ruleset configured; nothing to show)\n'
                '\n'
                'DEFAULT GUIDELINES (Phase 2, step 5)\n'
                '  solid.single-responsibility   pass   src/foo/Bar.<ext>:12-40\n'
                '  solid.open-closed             pass   src/foo/Bar.<ext>:44-61\n'
                '  solid.liskov-substitution     pass   src/foo/Bar.<ext>:12-18\n'
                '  solid.interface-segregation   pass   src/foo/Bar.<ext>:5-9\n'
                '  solid.dependency-inversion    pass   src/foo/Bar.<ext>:20-24\n'
                '  layers.<layer id>-depends-inward   no new finding\n'
                '  patterns.named-or-declined    pass   <the design doc>:<lines>\n'
                '  docs.design-before-code       pass   <the design doc>:<lines>\n'
                '  docs.ahead_of_code            <as printed by invariant check docs.ahead_of_code>\n'
                '\n'
                "PROPOSED EXCEPTIONS (the user's to run; never applied by this agent)\n"
                '  (or "none")\n'
                '\n'
            ),
        ),
    ),
    "feature-architect": (
        (
            (
                '1b. **Backlog siblings** — the Phase 1b scan result: every open work item touching the same files/component/surface, each marked `[COMBINED]` / `[DEFERRED — reason]` / `[UNRELATED — reason]`, or "none found".\n'
                '2. **Affected components** — list each component the change touches, derived from the dependency diagrams (not file search). Mark each `[NEW]`, `[MODIFIED]`, or `[READ-ONLY-DEPENDENCY]`.\n'
                '   - **Responsibility owner (single source of truth):** for every behavior the task needs, name the component that already *owns* that responsibility in the docs. If an owner exists → reuse / extend it, never fork a parallel implementation. If no owner exists → declare the new owner and document it (a behavior with no documented home is itself a finding). Judge by responsibility + invariants, not surface similarity — similar-looking code answering to a *different* caller stays separate.\n'
            ),
            (
                '1b. **Backlog siblings** — the Phase 1b scan result: every open work item touching the same files/component/surface, each marked `[COMBINED]` / `[DEFERRED — reason]` / `[UNRELATED — reason]`, or "none found".\n'
                '2. **Affected components** — list each component the change touches, derived from the dependency diagrams (not file search). Mark each `[NEW]`, `[MODIFIED]`, or `[READ-ONLY-DEPENDENCY]`, and give each its **layer**.\n'
                "   - **Layer:** the layer titles are the project's, from its ruleset's layer map (by default, innermost first: Entities, Use Cases, Interface Adapters, Frameworks & Drivers). Phase 1c printed one derived rule per mapped layer, `layers.<layer id>-depends-inward`, naming its layer and its paths; `invariant guidelines show --phase design --files <path>...` places a file you are not sure of. A component no derived rule covers is written `unmapped`, and a project with no layer map, or an empty one, is said to have none: never invent a layer. Placing an unmapped folder is the project's, so name it in the report rather than choosing for it.\n"
                '   - **Responsibility owner (single source of truth):** for every behavior the task needs, name the component that already *owns* that responsibility in the docs. If an owner exists → reuse / extend it, never fork a parallel implementation. If no owner exists → declare the new owner and document it (a behavior with no documented home is itself a finding). Judge by responsibility + invariants, not surface similarity — similar-looking code answering to a *different* caller stays separate.\n'
            ),
        ),
        (
            (
                '   - **Responsibility owner (single source of truth):** for every behavior the task needs, name the component that already *owns* that responsibility in the docs. If an owner exists → reuse / extend it, never fork a parallel implementation. If no owner exists → declare the new owner and document it (a behavior with no documented home is itself a finding). Judge by responsibility + invariants, not surface similarity — similar-looking code answering to a *different* caller stays separate.\n'
                '3. **Dependency arrows added / removed / changed** — what the updated diagram will look like.\n'
                '4. **Data flow** — if the change alters an end-to-end flow, sketch the new flow (textual sequence is fine; the diagram edit lands in Phase 7, or at the end of design under a store docs lock — see Phase 7).\n'
                "5. **Schema / model deltas** — any persistent-schema change. If yes, you must use the project's documented safe-migration pattern (see Phase 4 and BAIL gate B4) — never a destructive in-place alteration.\n"
            ),
            (
                '   - **Responsibility owner (single source of truth):** for every behavior the task needs, name the component that already *owns* that responsibility in the docs. If an owner exists → reuse / extend it, never fork a parallel implementation. If no owner exists → declare the new owner and document it (a behavior with no documented home is itself a finding). Judge by responsibility + invariants, not surface similarity — similar-looking code answering to a *different* caller stays separate.\n'
                '3. **Dependency arrows added / removed / changed** — what the updated diagram will look like. Show each new or changed arrow **pointing inward**: from its source\'s layer to the same layer or an inner one, never outward (the direction `layers.<layer id>-depends-inward` enforces). An arrow that would point outward is redesigned (define the shape the inner side needs as an interface there, implement it outward, inject it), or carried as a proposed exception (Phase 4, "Propose, never waive"); it is never written down as a note. Where the project has no layer map, say so and judge direction against the overview\'s layer diagram, if it draws one.\n'
                '3b. **SOLID** — a block naming the five principles one by one, each with how this design meets it, citing the component or seam it relies on:\n'
                '   - **Single responsibility** (`solid.single-responsibility`): each unit the change adds or edits answers to one owner, named in the docs.\n'
                '   - **Open/closed** (`solid.open-closed`): new behaviour arrives as a new implementation, data entry or strategy behind an existing seam, not as a new branch on a kind inside working code.\n'
                "   - **Liskov substitution** (`solid.liskov-substitution`): every implementation honours its interface's contract, and no caller checks which one it holds.\n"
                "   - **Interface segregation** (`solid.interface-segregation`): an interface added or widened is shaped by its callers' roles; nothing is forced to stub what it cannot honour.\n"
                '   - **Dependency inversion** (`solid.dependency-inversion`): policy depends on abstractions it owns, details are injected at the composition root, nothing high-level constructs or looks up a concrete detail.\n'
                '\n'
                "   A principle the change does not exercise says so in one line, with the reason. A principle the design cannot meet is a finding: redesign, or propose the exception (Phase 4). Where the ruleset does not carry a `solid.*` rule (the user retired or paused it, or it was never adopted), name the principle as not in the ruleset; it owes no verdict, and the user's ruleset decides.\n"
                '3c. **Patterns** (`patterns.named-or-declined`) — for each new abstraction (an interface, protocol, base class, or a type that mediates between two others), the catalogue pattern it applies, by its id on the catalogue page (`patterns.rst#<id>`), or one sentence on why none fits. Where the ruleset names no catalogue, write `no catalogue configured` and still say in one sentence what the abstraction is for. No new abstraction: say "none".\n'
                '4. **Data flow** — if the change alters an end-to-end flow, sketch the new flow (textual sequence is fine; the diagram edit is a design document, so it lands with item 8, before the code).\n'
                "5. **Schema / model deltas** — any persistent-schema change. If yes, you must use the project's documented safe-migration pattern (see Phase 4 and BAIL gate B4) — never a destructive in-place alteration.\n"
            ),
        ),
        (
            (
                "7. **Test plan** — for each touched source file, list the test file the project's conventions pair with it, the test cases you will add, and how they get the file to the configured coverage floor.\n"
                '8. **Docs to update** — the exact architecture-doc files under `paths.docs` and which diagram blocks change.\n'
                '9. **Decisions** — end the design phase by declaring every decision it made on the session: `invariant session decisions add <DEC_id>` for one already a need (`invariant docs decision new "<title>"` mints a record page), or `invariant session decisions add --draft "<one line>"` for one not yet written as a need. A draft holds the landing (`docs.decisions_recorded`) until `invariant session decisions resolve <n> <DEC_id>` names its need.\n'
            ),
            (
                "7. **Test plan** — for each touched source file, list the test file the project's conventions pair with it, the test cases you will add, and how they get the file to the configured coverage floor.\n"
                "8. **Design docs, updated before the code** — the exact architecture-doc files under `paths.docs` and decision records that change, and which diagram blocks and component responsibilities. Write these edits at the end of design, **before the first advance out of the design state** (`invariant pipeline advance <first build state>`): that advance records which design documents differ, and `docs.ahead_of_code` fails a structural change at review whose design documents had not changed by then. The advance prints what it recorded; a notice that none changed means write them now. `docs.design-before-code` is the review's judgement that they describe the change. A methodology with no design state takes no snapshot, and the gate says so itself. Phase 7 then reconciles the docs with what was built.\n"
                '9. **Decisions** — end the design phase by declaring every decision it made on the session: `invariant session decisions add <DEC_id>` for one already a need (`invariant docs decision new "<title>"` mints a record page), or `invariant session decisions add --draft "<one line>"` for one not yet written as a need. A draft holds the landing (`docs.decisions_recorded`) until `invariant session decisions resolve <n> <DEC_id>` names its need.\n'
            ),
        ),
        (
            (
                "With a ruleset, apply every rule Phase 1c printed to every touched file, and for a touched file that read could not see yet, ask for its rules with `invariant guidelines show --phase design --files <path>...`. With no ruleset, read the checklist doc now; it is the canonical checklist shared with `compliance-reviewer` — do **not** keep a private copy here (two inline copies drifted apart before the shared doc existed; that drift is why the single source exists). `invariant rules list` shows the engine-enforced subset and each rule's resolved level. At Boy-Scout time enforce the build-time subset on every touched file: clean architecture & dependency injection, migrations & data safety, the project's documented store/scope invariants, UI rules, testing rules, ecosystem gotchas, and code quality. The coverage floor is handled in Phase 6.\n"
                '\n'
            ),
            (
                "With a ruleset, apply every rule Phase 1c printed to every touched file, and for a touched file that read could not see yet, ask for its rules with `invariant guidelines show --phase design --files <path>...`. With no ruleset, read the checklist doc now; it is the canonical checklist shared with `compliance-reviewer` — do **not** keep a private copy here (two inline copies drifted apart before the shared doc existed; that drift is why the single source exists). `invariant rules list` shows the engine-enforced subset and each rule's resolved level. At Boy-Scout time enforce the build-time subset on every touched file: clean architecture & dependency injection, migrations & data safety, the project's documented store/scope invariants, UI rules, testing rules, ecosystem gotchas, and code quality. The coverage floor is handled in Phase 6.\n"
                '\n'
                "**The five SOLID principles, on every touched file.** Beside the rules Phase 1c printed, judge each touched file against single responsibility, open/closed, Liskov substitution, interface segregation and dependency inversion, by name, as Phase 2's SOLID block judged the design, and against the derived `layers.<layer id>-depends-inward` rules for its layer. A pre-existing violation in a touched file is a REFACTOR-TODO like any other; one that would take a refactor beyond the file is a B3 bail. With no ruleset, they are judged by hand as checklist lines.\n"
                '\n'
                '### Propose, never waive\n'
                '\n'
                "Some rules are the user's alone to set aside: `guidelines show` marks them `Owner: user` (the five `solid.*` rules, the derived layer rules, `patterns.named-or-declined` and `docs.design-before-code` in the default set). A finding on one of them that you would otherwise waive is never answered N/A, never downgraded and never left out of the report. There are three routes past it, and only the first is yours:\n"
                '\n'
                '1. **Fix the code.**\n'
                '2. **A user-ruled exception.** Put it under `PROPOSED EXCEPTIONS` in the report: the rule, the path, the finding, why an exception is right here, and the exact line for the user to run, with `--by` left for the user:\n'
                '\n'
                '   ```\n'
                '   invariant guidelines except <rule-id> <path> [--fingerprint <fp>] --by <who> --reason <why>\n'
                '   ```\n'
                '\n'
                '3. **The user changes the rule**, where the rule itself looks wrong for this project rather than for this one path. Propose it the same way, naming the line, and leave it to the user:\n'
                '\n'
                '   ```\n'
                '   invariant guidelines loosen <rule-id> --to warn|off --by <who> --reason <why>\n'
                '   invariant guidelines retire <rule-id> --by <who> --reason <why>\n'
                '   ```\n'
                '\n'
                'Never run any of the three commands yourself: they ask for the user at a terminal. Where the project has no ruleset there is nothing to except; a finding you cannot fix is `[NEEDS-USER]`, and the report names `invariant guidelines adopt` as how the project gets the default rules.\n'
                '\n'
            ),
        ),
        (
            (
                '\n'
                'For every structural change identified in Phase 2 sections 3 / 4 / 5, update the corresponding doc under `paths.docs`:\n'
                '\n'
            ),
            (
                '\n'
                'The design-document edits were written at the end of design (Phase 2 item 8), before the first advance out of the design state. This phase reconciles them with what was built: where the code moved from the design, update the docs to match. For every structural change identified in Phase 2 sections 3 / 4 / 5, the corresponding doc under `paths.docs` must cover:\n'
                '\n'
            ),
        ),
        (
            (
                '    (or "none found")\n'
                '  Affected components:\n'
                '    - DetailsSheet                          [NEW]\n'
                '    - SettingsScreen                        [MODIFIED]\n'
                '    - ManagementViewModel                   [MODIFIED]\n'
                '    - Registry                              [READ-ONLY-DEPENDENCY]\n'
                '  Dependency arrows:\n'
                '    + SettingsScreen → DetailsSheet (new presentation)\n'
                '  Data flow:\n'
            ),
            (
                '    (or "none found")\n'
                '  Affected components (layer):\n'
                '    - DetailsSheet                          [NEW]                   Interface Adapters\n'
                '    - SettingsScreen                        [MODIFIED]              Interface Adapters\n'
                '    - ManagementViewModel                   [MODIFIED]              Use Cases\n'
                '    - Registry                              [READ-ONLY-DEPENDENCY]  Entities\n'
                '  Dependency arrows (each inward):\n'
                '    + SettingsScreen → DetailsSheet (new presentation; same layer)\n'
                '  SOLID:\n'
                '    Single responsibility   DetailsSheet only presents; ManagementViewModel owns selection\n'
                '    Open/closed             new sheet added behind the existing presentation seam\n'
                '    Liskov substitution     no new implementation of an existing interface\n'
                '    Interface segregation   DetailsSheet depends on the read role only\n'
                '    Dependency inversion    the view model receives its store through injection\n'
                '  Patterns:\n'
                '    - DetailsSheetPresenting   patterns.rst#gof.strategy  (or: why none fits)\n'
                '  Data flow:\n'
            ),
        ),
        (
            (
                '    - tests/ManagementViewModelTests.<ext>          +N tests → 91%\n'
                '  Docs to update:\n'
                '    - <paths.docs>/views doc (add DetailsSheet to view graph)\n'
            ),
            (
                '    - tests/ManagementViewModelTests.<ext>          +N tests → 91%\n'
                '  Design docs (edited before the first advance out of design):\n'
                '    - <paths.docs>/views doc (add DetailsSheet to view graph)\n'
            ),
        ),
        (
            (
                '  - (each entry: file, checklist rule, what changed)\n'
                '\n'
            ),
            (
                '  - (each entry: file, checklist rule, what changed)\n'
                '\n'
                "PROPOSED EXCEPTIONS (the user's to run; never applied by this agent)\n"
                '  - solid.open-closed  src/legacy/Router.<ext>: <the finding>; <why an exception is right here>\n'
                '      invariant guidelines except solid.open-closed src/legacy/Router.<ext> --by <who> --reason "<why>"\n'
                '  (or "none")\n'
                '\n'
            ),
        ),
    ),
}


def source_text(name):
    return (LIBRARY / f"{name}.md").read_text(encoding="utf-8")


def a6_1_text(name):
    return (FIXTURES / f"{name}.md").read_text(encoding="utf-8")


def expected_render(name):
    """What A6.1 installed, minus its model line, with the declared rewrites."""
    lines = a6_1_text(name).split("\n")
    close = lines.index("---", 1)
    front = [line for line in lines[1:close] if not line.startswith("model:")]
    text = "\n".join([lines[0], *front, *lines[close:]])
    for old, new in (
        *NEUTRALISED.get(name, ()),
        *A6_5.get(name, ()),
        *A7_2.get(name, ()),
        *ADR_018_5.get(name, ()),
        *ADR_014_4C1.get(name, ()),
        *IDEA_052.get(name, ()),
        *IDEA_051.get(name, ()),
        *BUG_126.get(name, ()),
        *SCOPED_RUNS.get(name, ()),
        *QUEUE_SUCCESSOR.get(name, ()),
        *ADR_032_I4.get(name, ()),
        *ADR_032_I6.get(name, ()),
    ):
        assert text.count(old) == 1, f"{name}: the rewrite no longer matches {old!r}"
        text = text.replace(old, new)
    return text


def rendered(name):
    return render_agent(parse(source_text(name), name))


def test_the_library_ships_the_four_agents():
    assert NAMES == sorted(ANCHORS)


class TestTheGolden:
    @pytest.mark.parametrize("name", NAMES)
    def test_a_source_renders_to_what_a6_1_installed(self, name):
        assert rendered(name) == expected_render(name)

    def test_only_the_reviewer_carried_a_model_line(self):
        carried = [n for n in NAMES if "\nmodel:" in a6_1_text(n).split("\n---\n")[0]]
        assert carried == ["compliance-reviewer"]

    @pytest.mark.parametrize("name", NAMES)
    def test_no_model_line_is_written_without_a_map(self, name):
        front = assets.frontmatter(rendered(name))
        assert "model" not in front

    @pytest.mark.parametrize("name", NAMES)
    def test_no_anchor_survives_rendering(self, name):
        assert "invariant:section" not in rendered(name)


class TestTheSources:
    @pytest.mark.parametrize("name", NAMES)
    def test_each_source_declares_its_anchor_set(self, name):
        assert parse(source_text(name), name).section_ids() == ANCHORS[name]

    @pytest.mark.parametrize("name", NAMES)
    def test_every_anchor_sits_on_a_heading(self, name):
        """An anchor names the section that follows it, so it is placed just
        above that section's heading; the intro anchor opens the body."""
        lines = source_text(name).split("\n")
        for index, line in enumerate(lines):
            if line.startswith("<!-- invariant:section ") and not line.endswith(" intro -->"):
                assert lines[index + 1].startswith("##"), (name, index + 1)

    @pytest.mark.parametrize("name", NAMES)
    def test_each_source_declares_its_capabilities(self, name):
        assert parse(source_text(name), name).capabilities == ("read", "edit", "shell")

    @pytest.mark.parametrize("name", NAMES)
    def test_the_host_reader_still_reads_the_description_as_one_line(self, name):
        """The parent reads the description off the rendered file through the
        host's line reader, so it must stay a single line."""
        front = assets.frontmatter(rendered(name))
        assert front["description"] == parse(source_text(name), name).description


def vocabulary_hits(text, vocabulary):
    """Where a neutral source speaks a host's own words.

    A term with an inner capital (``AskUserQuestion``) is no English word, so it
    is a hit anywhere. A tool named by an English word (``Read``, ``Task``) is a
    hit only where it is used as a tool: in a code span, before "tool", or in a
    ``tools:`` list. "Read the docs FIRST" is prose, not the host's vocabulary.
    """
    hits = []
    for number, line in enumerate(text.split("\n"), 1):
        for term in vocabulary:
            if term[1:] != term[1:].lower():
                pattern = rf"\b{term}\b"
            else:
                pattern = rf"`{term}`|\b{term} tools?\b|^tools:.*\b{term}\b"
            if re.search(pattern, line):
                hits.append((number, term))
    return hits


class TestTheHostVocabularyLint:
    def test_the_adapter_supplies_its_tool_names_and_its_question_tool(self):
        assert {"Read", "Edit", "Write", "Bash", "Grep", "Glob", "Agent"} <= set(HOST_VOCABULARY)
        assert "AskUserQuestion" in HOST_VOCABULARY

    @pytest.mark.parametrize(
        "line",
        [
            "use AskUserQuestion here",
            "open it with the `Read` tool",
            "the Bash tool runs it",
            "tools: Bash, Read",
        ],
    )
    def test_the_lint_goes_red_on_host_vocabulary(self, line):
        assert vocabulary_hits(line, HOST_VOCABULARY)

    @pytest.mark.parametrize(
        "line", ["## Phase 1 — Read the docs FIRST", "1. **Task restated** — one paragraph"]
    )
    def test_the_lint_passes_prose(self, line):
        assert vocabulary_hits(line, HOST_VOCABULARY) == []

    @pytest.mark.parametrize("name", NAMES)
    def test_no_neutral_source_speaks_the_hosts_vocabulary(self, name):
        assert vocabulary_hits(source_text(name), HOST_VOCABULARY) == []


class TestTheRenderer:
    def agent(self, capabilities):
        return parse(
            "---\nname: a\ndescription: d\n"
            f"capabilities: [{', '.join(capabilities)}]\n---\nBody.\n"
        )

    @pytest.mark.parametrize(
        ("capabilities", "tools"),
        [
            (["read"], "Read, Grep, Glob"),
            (["edit"], "Edit, Write"),
            (["shell"], "Bash"),
            (["dispatch"], "Agent"),
            (["dispatch", "shell", "read", "edit"], "Bash, Read, Edit, Write, Grep, Glob, Agent"),
        ],
    )
    def test_capabilities_map_to_the_hosts_tools_in_one_order(self, capabilities, tools):
        text = render_agent(self.agent(capabilities))
        assert text == f"---\nname: a\ndescription: d\ntools: {tools}\n---\nBody.\n"


# -- the install plan renders through the renderer -------------------------


@pytest.fixture
def host(tmp_path):
    return ClaudeCodeHost(tmp_path)


def target(host, name):
    return host.claude_dir / "agents" / f"{name}.md"


class TestThePlanRenders:
    @pytest.mark.parametrize("name", NAMES)
    def test_an_install_writes_the_rendered_text_under_its_marker(self, host, name):
        host.install_assets(only="agent")
        installed = target(host, name).read_text(encoding="utf-8")
        assert assets.reconstruct_source(installed) == rendered(name)
        assert assets.marker_digest(installed) == assets.digest_of(rendered(name))

    def test_a_fresh_install_is_current(self, host):
        host.install_assets(only="agent")
        assert {s.state for s in host.plan_assets(only="agent")} == {assets.CURRENT}

    def test_a_broken_neutral_source_is_refused_naming_its_problems(
        self, host, tmp_path, monkeypatch
    ):
        data = tmp_path / "data"
        (data / "agents").mkdir(parents=True)
        (data / "agents" / "odd.md").write_text(
            "---\nname: odd\ndescription: d\nmodel: sonnet\n---\nB.\n", encoding="utf-8"
        )
        monkeypatch.setattr(claude_code, "DATA_DIR", data)
        with pytest.raises(Exception) as exc:
            host.install_assets(only="agent")
        assert "odd" in str(exc.value)
        assert "no capabilities" in str(exc.value)
        assert "never names a model" in str(exc.value)
        assert not target(host, "odd").exists()


class TestACopyFromBeforeA6_2:
    """The A6.1 classifier still tells an older release from an edit."""

    def install_a6_1(self, host, name, extra=""):
        old = a6_1_text(name)
        path = target(host, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(one_digest_copy(old) + extra, encoding="utf-8")

    def states(self, host):
        return {s.identity: s.state for s in host.plan_assets(only="agent")}

    def test_an_untouched_copy_reads_as_stale_where_the_text_changed(self, host):
        """Since A6.5 every shipped body differs from A6.1's (NEUTRALISED, A6_5)."""
        for name in NAMES:
            self.install_a6_1(host, name)
        assert self.states(host) == dict.fromkeys(NAMES, assets.OLDER)

    def test_an_untouched_copy_is_rewritten_unasked(self, host):
        for name in NAMES:
            self.install_a6_1(host, name)
        result = host.install_assets(only="agent")
        assert result.skipped == []
        assert self.states(host) == dict.fromkeys(NAMES, assets.CURRENT)

    @pytest.mark.parametrize("name", NAMES)
    def test_an_edited_copy_reads_as_edited(self, host, name):
        self.install_a6_1(host, name, extra="\nmine\n")
        assert self.states(host)[name] == assets.LOCAL
        result = host.install_assets(only="agent")
        assert (f"agent:{name}", assets.LOCAL) in result.skipped
        assert target(host, name).read_text(encoding="utf-8").endswith("\nmine\n")


# -- invariant agents show ---------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    from invariant.cli.main import main

    root = tmp_path / "proj"
    (root / "docs" / "architecture").mkdir(parents=True)
    monkeypatch.chdir(root)
    assert main(["init", "--host-install", "--name", "demo"]) == 0
    return root


def files_under(root):
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


class TestAgentsShow:
    def test_it_prints_the_rendered_text_and_writes_nothing(self, project, capsys):
        from invariant.cli.main import main

        before = files_under(project)
        capsys.readouterr()
        assert main(["agents", "show", "compliance-reviewer"]) == 0
        out = capsys.readouterr().out
        assert out == rendered("compliance-reviewer")
        assert files_under(project) == before

    def test_json_carries_the_text(self, project, capsys):
        from invariant.cli.main import main

        capsys.readouterr()
        assert main(["agents", "show", "feature-architect", "--json"]) == 0
        body = json.loads(capsys.readouterr().out)
        assert body == {"agent": "feature-architect", "text": rendered("feature-architect")}

    def test_sections_lists_the_anchor_ids(self, project, capsys):
        from invariant.cli.main import main

        capsys.readouterr()
        assert main(["agents", "show", "bug-reproducer", "--sections"]) == 0
        assert capsys.readouterr().out.split() == list(ANCHORS["bug-reproducer"])

        assert main(["agents", "show", "bug-reproducer", "--sections", "--json"]) == 0
        body = json.loads(capsys.readouterr().out)
        assert body == {"agent": "bug-reproducer", "sections": list(ANCHORS["bug-reproducer"])}

    def test_an_unknown_agent_is_refused_naming_the_shipped_ones(self, project, capsys):
        from invariant.cli.main import main

        capsys.readouterr()
        assert main(["agents", "show", "nobody"]) == 2
        err = capsys.readouterr().err
        assert "'nobody'" in err
        assert "feature-architect" in err

    def test_a_host_kind_with_no_adapter_is_refused(self, project, capsys):
        import yaml

        from invariant.cli.main import main

        path = project / ".claude" / "invariant.yaml"
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
        body["host"] = {"kind": "some-other-host"}
        path.write_text(yaml.safe_dump(body), encoding="utf-8")
        capsys.readouterr()
        assert main(["agents", "show", "feature-architect"]) == 2
        assert "'some-other-host'" in capsys.readouterr().err
