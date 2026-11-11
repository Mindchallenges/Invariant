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
