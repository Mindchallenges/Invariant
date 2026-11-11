"""The shipped agent bodies, onto the engine (ADR-012 D8, the A6 rulings 9 and 14).

A body speaks to the invariant CLI and to the ruleset, never to a copy of
either. So two things are pinned here. Every command a body names parses with
the real parser, so a renamed flag or subcommand cannot leave an instruction
nobody can run (ruling 9). And the design and review agents carry the literal
ruling-9 commands, in order, with a no-ruleset branch that is decided by what
the engine prints, never by the agent's judgement.
"""

from __future__ import annotations

import argparse
import re
import shlex

import pytest
import yaml

from invariant.core.agents import parse
from invariant.hosts import claude_code
from invariant.hosts.claude_code import render_agent

LIBRARY = claude_code.DATA_DIR / "agents"
NAMES = sorted(p.stem for p in LIBRARY.glob("*.md"))

#: What `guidelines show` prints, with or without `--phase`, where no ruleset
#: is configured. The bodies quote it, so the branch is the engine's answer.
NO_RULESET = "no guidelines ruleset configured; nothing to show"

#: A placeholder the parser reads as a number. Every other one is a string
#: wherever it lands, as in the rules' own fix scan (tests/test_cli.py).
NUMERIC_PLACEHOLDERS = {"<n>"}


def source_text(name):
    return (LIBRARY / f"{name}.md").read_text(encoding="utf-8")


def section(name, section_id):
    agent = parse(source_text(name), name)
    return next(s for s in agent.sections if s.id == section_id).text


def commands_in(text):
    """Every invariant command a body names, as written.

    Two forms: a code span opening on `invariant `, which may wrap across
    lines, and a line whose text starts with `invariant `, inside a fence or
    out of one. A prose line that happens to start with the word is read as a
    command too, so the bodies never wrap prose that way.
    """
    found = [" ".join(span.split()) for span in re.findall(r"`(invariant [^`]*)`", text)]
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("invariant "):
            found.append(stripped)
    return found


def tokens_of(command):
    """The parser's argv for a command as a body writes it."""
    command = command.split("#")[0]
    for placeholder in re.findall(r"<[^<>]*>", command):
        stand_in = "1" if placeholder in NUMERIC_PLACEHOLDERS else "X"
        command = command.replace(placeholder, stand_in)
    words = shlex.split(command)
    assert words[0] == "invariant", command
    # `...` is "and so on" after a repeatable argument, not an argument.
    return [word for word in words[1:] if word != "..."]


def readings_of(words):
    """Each argv an alternation spells: `new|list` is one command per name."""
    readings = [[]]
    for word in words:
        options = word.split("|") if "|" in word else [word]
        readings = [reading + [option] for reading in readings for option in options]
    return readings


def subcommands(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def positional_choices(parser):
    """The choices of each leading positional, in order, until one has none."""
    found = []
    for action in parser._actions:
        if action.option_strings or isinstance(action, argparse._SubParsersAction):
            continue
        if not action.choices:
            break
        found.append(set(action.choices))
    return found


def names_a_command(parser, words):
    """True when the words only name a command, with no argument of their own.

    `invariant item transition` or `invariant item new idea` in prose names
    the command; it is not an invocation, so it does not carry the arguments a
    run needs. Each word must be a subcommand on one path through the parser,
    or one of the fixed choices of the positional that comes next. So the walk
    still proves every word exists, which is what a rename breaks.
    """
    words = list(words)
    while words and words[0] in subcommands(parser):
        parser = subcommands(parser)[words.pop(0)]
    for choices in positional_choices(parser):
        if not words:
            break
        if words[0] not in choices:
            return False
        words.pop(0)
    return not words


def problem_with(parser, command):
    """None when the command parses or names a command; else what failed."""
    for words in readings_of(tokens_of(command)):
        if words and names_a_command(parser, words):
            continue
        try:
            parser.parse_args(words)
        except SystemExit:
            return command
    return None


@pytest.fixture(scope="module")
def parser():
    from invariant.cli.main import build_parser

    return build_parser()


class TestTheScan:
    def test_it_reads_spans_wrapped_lines_and_fences(self):
        text = (
            "Run `invariant pipeline gates` first, then `invariant item\n"
            "show BUG_<id>`.\n\n```\n   invariant config show   # the paths\n```\n"
        )
        assert commands_in(text) == [
            "invariant pipeline gates",
            "invariant item show BUG_<id>",
            "invariant config show   # the paths",
        ]

    def test_placeholders_become_arguments(self):
        assert tokens_of('invariant session plan done <n>') == ["session", "plan", "done", "1"]
        assert tokens_of('invariant item show BUG_<id>') == ["item", "show", "BUG_X"]
        assert tokens_of(
            "invariant pipeline test --selector <the touched files' tests>"
        ) == ["pipeline", "test", "--selector", "X"]

    @pytest.mark.parametrize(
        "command",
        [
            "invariant guidelines show --phase design --touchd",
            "invariant guidelines verdict <rule-id> --status maybe",
            "invariant guideline show",
            "invariant item transition BUG_<id>",
            "invariant of the whole lifecycle",
            "invariant session start repro-<bug_slug> --task <bug_slug>",
            "invariant item new|lsit",
            "invariant item new ideas",
        ],
    )
    def test_a_dead_instruction_is_caught(self, parser, command):
        assert problem_with(parser, command) == command

    @pytest.mark.parametrize(
        "command",
        [
            "invariant item transition",
            "invariant session plan",
            "invariant check",
            "invariant item new idea",
            "invariant session files add",
            "invariant item new|list|show",
            "invariant item new idea --title \"<title>\" --field priority=<p> ...",
        ],
    )
    def test_a_command_named_in_prose_passes(self, parser, command):
        assert problem_with(parser, command) is None


class TestEveryShippedCommandParses:
    @pytest.mark.parametrize("name", NAMES)
    def test_every_command_a_body_names_parses(self, parser, name):
        named = commands_in(source_text(name))
        assert named, name
        dead = [c for c in named if problem_with(parser, c) is not None]
        assert dead == []


#: The review phase's commands, in the order ruling 9 gives them.
REVIEW_COMMANDS = (
    "invariant guidelines show --phase review --touched",
    "invariant check guidelines.mechanical_clean",
    "invariant guidelines verdicts show",
    "invariant guidelines verdict <rule-id> --status pass --cite <path> <first-line> <last-line>",
)


class TestTheBodiesAskTheEngine:
    def test_the_architect_reads_the_design_guidelines_before_planning(self):
        agent = parse(source_text("feature-architect"), "feature-architect")
        ids = agent.section_ids()
        assert ids.index("phase-1c") < ids.index("phase-2")
        text = section("feature-architect", "phase-1c")
        assert "invariant guidelines show --phase design --touched" in commands_in(text)
        assert NO_RULESET in text

    def test_the_architects_boy_scout_phase_applies_what_it_read(self):
        text = section("feature-architect", "phase-4")
        assert "Phase 1c" in text
        assert "paths.docs" in text

    def test_under_a_store_docs_lock_the_architects_doc_update_ends_design(self):
        # ADR-014 D5, increment 4c-1: conditional on a store docs lock only.
        text = section("feature-architect", "phase-7")
        assert "records.docs: store" in text
        named = commands_in(text)
        assert "invariant docs unlock" in named
        assert "store.docs_lock_released" in text
        assert "stays after the code" in text
        assert "Phase 7 comes first" in section("feature-architect", "phase-5")

    def test_the_reviewer_runs_the_four_commands_in_order(self):
        named = commands_in(section("compliance-reviewer", "phase-2"))
        positions = [named.index(command) for command in REVIEW_COMMANDS]
        assert positions == sorted(positions)

    def test_the_reviewer_submits_one_verdict_per_rule_not_a_file(self):
        named = commands_in(section("compliance-reviewer", "phase-2"))
        assert not any("verdicts submit" in command for command in named)
        statuses = {
            tokens_of(c)[tokens_of(c).index("--status") + 1]
            for c in named
            if c.startswith("invariant guidelines verdict ")
        }
        assert statuses == {"pass", "fail", "na"}

    def test_the_reviewer_falls_back_on_the_engines_no_ruleset_line(self):
        text = section("compliance-reviewer", "phase-2")
        assert NO_RULESET in text
        assert "paths.docs" in text

    def test_the_report_pastes_the_engines_standings(self):
        text = section("compliance-reviewer", "phase-9")
        assert "GUIDELINES" in text
        assert "invariant guidelines verdicts show" in commands_in(text)

    def test_a_refused_verdict_is_answered_again_after_a_fix(self):
        assert "stale" in section("compliance-reviewer", "phase-4")


# -- the engine prints what the bodies quote ----------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    from invariant.cli.main import main

    root = tmp_path / "proj"
    (root / "invariant-docs" / "architecture").mkdir(parents=True)
    monkeypatch.chdir(root)
    assert main(["init", "--host-install", "--name", "demo"]) == 0
    return root


RULESET = """\
version: 1
sections:
  - id: layers
    title: Layers
rules:
  - id: layers.example
    section: layers
    title: An example rule
    statement: Something the review judges.
    severity: warn
    phases: [design, review]
    applies_to: ["src/**"]
    check: {kind: judgement, evidence: citation, scope: diff}
"""


def configure_ruleset(project):
    path = project / "docs" / "guidelines" / "ruleset.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(RULESET, encoding="utf-8")
    manifest = project / ".claude" / "invariant.yaml"
    body = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    body.setdefault("guidelines", {})["ruleset"] = "docs/guidelines/ruleset.yaml"
    manifest.write_text(yaml.safe_dump(body), encoding="utf-8")


class TestTheBranchIsTheEnginesOutput:
    @pytest.mark.parametrize("phase", ["design", "review"])
    def test_with_no_ruleset_show_prints_the_quoted_line(self, project, capsys, phase):
        from invariant.cli.main import main

        capsys.readouterr()
        assert main(["guidelines", "show", "--phase", phase]) == 0
        assert capsys.readouterr().out.strip() == NO_RULESET

    def test_with_a_ruleset_show_prints_rules_not_the_line(self, project, capsys):
        from invariant.cli.main import main

        configure_ruleset(project)
        capsys.readouterr()
        assert main(["guidelines", "show", "--phase", "review"]) == 0
        out = capsys.readouterr().out
        assert "layers.example" in out
        assert NO_RULESET not in out


class TestTheRenderDoesNotDependOnTheRuleset:
    """The commands are plain text in the source; nothing is computed at render."""

    @pytest.mark.parametrize("name", ["feature-architect", "compliance-reviewer"])
    def test_agents_show_is_the_same_bytes_with_and_without_a_ruleset(
        self, project, capsys, name
    ):
        from invariant.cli.main import main

        capsys.readouterr()
        assert main(["agents", "show", name]) == 0
        without = capsys.readouterr().out
        configure_ruleset(project)
        assert main(["agents", "show", name]) == 0
        with_one = capsys.readouterr().out
        assert with_one == without == render_agent(parse(source_text(name), name))
