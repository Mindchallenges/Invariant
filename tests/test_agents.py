"""The host-neutral agent source (ADR-012 D8, A6 increment 2): ``core/agents``.

The parser is pure and host-agnostic. It refuses a source whole and names every
problem, as the ruleset loader does, so a source with three faults is fixed in
one pass rather than three.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from invariant.core import agents
from invariant.core.agents import AgentSourceError, parse
from invariant.core.errors import InvariantError

GOOD = (
    "---\n"
    "name: thing\n"
    "description: Does a thing. Use when: X\n"
    "capabilities: [read, edit, shell]\n"
    "---\n"
    "Before any anchor.\n"
    "<!-- invariant:section first -->\n"
    "## First\n"
    "\n"
    "One.\n"
    "<!-- invariant:section second -->\n"
    "## Second\n"
    "Two.\n"
)


def problems(text, expected_name=None):
    with pytest.raises(AgentSourceError) as exc:
        parse(text, expected_name)
    return exc.value.problems


def source(front, body="Body.\n"):
    return "---\n" + front + "---\n" + body


class TestTheModel:
    def test_the_frontmatter_is_read(self):
        agent = parse(GOOD, "thing")
        assert agent.name == "thing"
        assert agent.description == "Does a thing. Use when: X"
        assert agent.capabilities == ("read", "edit", "shell")

    def test_sections_run_from_anchor_to_anchor(self):
        agent = parse(GOOD)
        assert agent.section_ids() == ("first", "second")
        assert agent.sections[0].text == "## First\n\nOne."
        assert agent.sections[1].text == "## Second\nTwo.\n"

    def test_the_text_before_the_first_anchor_is_the_preamble(self):
        assert parse(GOOD).preamble == "Before any anchor."

    def test_the_body_is_the_source_body_with_every_anchor_line_removed(self):
        assert parse(GOOD).body() == (
            "Before any anchor.\n## First\n\nOne.\n## Second\nTwo.\n"
        )

    def test_a_body_with_no_anchor_is_all_preamble(self):
        agent = parse(source("name: a\ndescription: d\ncapabilities: [shell]\n"))
        assert agent.sections == ()
        assert agent.body() == "Body.\n"

    def test_an_anchor_on_the_first_body_line_leaves_an_empty_preamble(self):
        text = source(
            "name: a\ndescription: d\ncapabilities: [read]\n",
            "<!-- invariant:section intro -->\n\nHello.\n",
        )
        agent = parse(text)
        assert agent.preamble == ""
        assert agent.body() == "\nHello.\n"

    def test_every_capability_is_accepted(self):
        text = source("name: a\ndescription: d\ncapabilities: [read, edit, shell, dispatch]\n")
        assert parse(text).capabilities == agents.CAPABILITIES

    def test_blank_frontmatter_lines_are_ignored(self):
        text = source("name: a\n\ndescription: d\ncapabilities: [read]\n")
        assert parse(text).name == "a"


class TestEveryProblemIsNamed:
    def test_the_refusal_is_an_invariant_error_naming_the_count(self):
        with pytest.raises(InvariantError) as exc:
            parse("no frontmatter\n")
        assert "1 problem(s)" in str(exc.value)

    def test_no_frontmatter_on_line_one(self):
        assert problems("\n---\nname: a\n---\n") == ("no frontmatter on line 1",)

    def test_an_unclosed_frontmatter(self):
        assert problems("---\nname: a\n") == ("no frontmatter on line 1",)

    def test_each_missing_key_is_named(self):
        found = problems(source("name: a\n"))
        assert "no description" in found
        assert "no capabilities" in found

    def test_a_missing_name(self):
        assert "no name" in problems(source("description: d\ncapabilities: [read]\n"))

    def test_every_problem_is_named_in_one_refusal(self):
        found = problems(
            source(
                "name: a\nmodel: sonnet\ntools: Bash\ncapabilities: [fly]\n",
                "<!-- invariant:section Bad Id -->\n",
            )
        )
        assert len(found) == 5, found

    @pytest.mark.parametrize(
        ("key", "said"),
        [
            ("tier", "A7"),
            ("model", "never names a model"),
            ("tools", "capabilities"),
            ("roles", "agents.roles"),
        ],
    )
    def test_a_refused_key_is_refused_by_name(self, key, said):
        found = problems(
            source(f"name: a\ndescription: d\ncapabilities: [read]\n{key}: x\n")
        )
        assert len(found) == 1
        assert found[0].startswith(f"{key}:")
        assert said in found[0]

    def test_an_unknown_key(self):
        found = problems(source("name: a\ndescription: d\ncapabilities: [read]\ncolour: red\n"))
        assert found == ("colour: not a key of a neutral agent source",)

    def test_a_repeated_key(self):
        found = problems(source("name: a\nname: b\ndescription: d\ncapabilities: [read]\n"))
        assert found == ("name: declared twice",)

    def test_a_line_that_is_not_a_key(self):
        found = problems(source("name: a\ndescription: d\n  continued\ncapabilities: [read]\n"))
        assert found == ("line 4: not a 'key: value' line",)

    @pytest.mark.parametrize("value", ["read, edit", "[read", "read]", ""])
    def test_capabilities_must_be_a_bracketed_list(self, value):
        found = problems(source(f"name: a\ndescription: d\ncapabilities: {value}\n"))
        assert len(found) == 1
        assert "capabilities" in found[0]

    def test_an_empty_capability_list(self):
        found = problems(source("name: a\ndescription: d\ncapabilities: []\n"))
        assert found == ("capabilities: none declared",)

    def test_an_unknown_capability(self):
        found = problems(source("name: a\ndescription: d\ncapabilities: [read, fly]\n"))
        assert found == (
            "capabilities: 'fly' is not one of read, edit, shell, dispatch",
        )

    def test_a_repeated_capability(self):
        found = problems(source("name: a\ndescription: d\ncapabilities: [read, read]\n"))
        assert found == ("capabilities: 'read' listed twice",)

    def test_a_name_that_is_not_the_file(self):
        found = problems(source("name: a\ndescription: d\ncapabilities: [read]\n"), "b")
        assert found == ("name: declares 'a' but the source is named 'b'",)

    def test_a_repeated_anchor(self):
        found = problems(
            source(
                "name: a\ndescription: d\ncapabilities: [read]\n",
                "<!-- invariant:section x -->\n<!-- invariant:section x -->\n",
            )
        )
        assert found == ("line 7: section 'x' is anchored twice",)

    @pytest.mark.parametrize(
        "line",
        [
            "<!-- invariant:section -->",
            "<!-- invariant:section Bad -->",
            "<!-- invariant:section a b -->",
            "text <!-- invariant:section a -->",
            "<!-- invariant:section a --> text",
        ],
    )
    def test_a_malformed_anchor(self, line):
        found = problems(
            source("name: a\ndescription: d\ncapabilities: [read]\n", line + "\n")
        )
        assert found == (f"line 6: a malformed section anchor: {line.strip()!r}",)


class TestCoreAgentsIsHostAgnostic:
    """D8: the neutral model lives in ``core/``, so it cannot know a host."""

    def test_it_imports_no_host(self):
        path = Path(agents.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.append(("." * node.level) + (node.module or ""))
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
        assert imported, "the scan found no import at all; it is not reading the module"
        assert not [name for name in imported if "hosts" in name], imported

    def test_it_imports_nothing_that_does_io(self):
        tree = ast.parse(Path(agents.__file__).read_text(encoding="utf-8"))
        names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not names & {"os", "subprocess", "pathlib", "Path", "open"}
