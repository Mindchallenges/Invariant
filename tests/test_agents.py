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


# -- overlays (ADR-012 D8, the A6 rulings 4 and 5, A6 increment 3) ----------

#: GOOD with the separator the shipped sources carry between sections.
RULED = (
    "---\n"
    "name: thing\n"
    "description: Does a thing.\n"
    "capabilities: [read]\n"
    "---\n"
    "<!-- invariant:section intro -->\n"
    "Intro.\n"
    "\n"
    "---\n"
    "\n"
    "<!-- invariant:section phase-1 -->\n"
    "## Phase 1\n"
    "\n"
    "Read.\n"
    "\n"
    "---\n"
    "\n"
    "<!-- invariant:section phase-2 -->\n"
    "## Phase 2\n"
    "\n"
    "Plan.\n"
)


def overlay(*operations, version=1):
    import yaml

    return yaml.safe_dump({"version": version, "operations": list(operations)})


def merged(text, *operations):
    return agents.merge(parse(text, "thing"), overlay(*operations))


def refusals(text, overlay_text):
    with pytest.raises(agents.OverlayError) as exc:
        agents.merge(parse(text, "thing"), overlay_text)
    return exc.value.problems


class TestAnOverlayMerges:
    def test_no_overlay_is_the_source_unchanged(self):
        source = parse(RULED, "thing")
        assert agents.merge(source, None) is source

    def test_append_adds_to_the_sections_end_before_its_separator(self):
        body = merged(RULED, {"append": "phase-1", "text": "Also this.\n"}).body()
        assert "Read.\n\nAlso this.\n\n---\n\n## Phase 2" in body

    def test_append_to_the_last_section_keeps_the_final_newline(self):
        body = merged(RULED, {"append": "phase-2", "text": "And this."}).body()
        assert body.endswith("Plan.\n\nAnd this.\n")

    def test_replace_swaps_the_content_and_keeps_the_separator(self):
        body = merged(RULED, {"replace": "phase-1", "text": "## Phase 1\n\nOurs.\n"}).body()
        assert "Intro.\n\n---\n\n## Phase 1\n\nOurs.\n\n---\n\n## Phase 2" in body
        assert "Read." not in body

    def test_insert_after_adds_a_new_anchored_section(self):
        agent = merged(
            RULED, {"insert_after": "phase-1", "id": "house-rules", "text": "## House\n\nRules."}
        )
        assert agent.section_ids() == ("intro", "phase-1", "house-rules", "phase-2")
        assert "Read.\n\n---\n\n## House\n\nRules.\n\n---\n\n## Phase 2" in agent.body()

    def test_insert_after_the_last_section(self):
        agent = merged(RULED, {"insert_after": "phase-2", "id": "tail", "text": "Last."})
        assert agent.section_ids()[-1] == "tail"
        assert agent.body().endswith("Plan.\n\nLast.\n")

    def test_an_inserted_section_can_be_targeted_by_a_later_operation(self):
        agent = merged(
            RULED,
            {"insert_after": "intro", "id": "mine", "text": "Mine."},
            {"append": "mine", "text": "More."},
        )
        assert "Mine.\n\nMore.\n" in agent.body()

    def test_description_append(self):
        agent = merged(RULED, {"description_append": "Our house rules apply."})
        assert agent.description == "Does a thing. Our house rules apply."

    def test_operations_run_in_order(self):
        agent = merged(
            RULED,
            {"replace": "phase-2", "text": "First."},
            {"append": "phase-2", "text": "Second."},
        )
        assert agent.body().endswith("First.\n\nSecond.\n")

    def test_nothing_else_changes(self):
        source = parse(RULED, "thing")
        agent = agents.merge(source, overlay({"append": "intro", "text": "x"}))
        assert (agent.name, agent.capabilities) == (source.name, source.capabilities)
        assert agent.preamble_lines == source.preamble_lines


class TestAnOverlayIsRefusedWithAFix:
    def test_the_refusal_is_an_invariant_error_naming_each_problem_and_fix(self):
        with pytest.raises(agents.OverlayError) as exc:
            agents.merge(parse(RULED, "thing"), overlay({"append": "nope", "text": "x"}))
        assert isinstance(exc.value, InvariantError)
        assert "fix:" in str(exc.value)
        assert all(p.fix for p in exc.value.problems)

    def test_not_yaml(self):
        (found,) = refusals(RULED, "version: [1\n")
        assert "not valid YAML" in found.problem

    def test_not_a_mapping(self):
        (found,) = refusals(RULED, "- append: intro\n")
        assert "mapping" in found.problem

    def test_no_version(self):
        found = refusals(RULED, "operations: [{append: intro, text: x}]\n")
        assert any("no version" in p.problem for p in found)

    def test_an_unknown_version(self):
        (found,) = refusals(RULED, overlay({"append": "intro", "text": "x"}, version=2))
        assert "version 2" in found.problem and "version 1" in found.fix

    def test_no_operations(self):
        (found,) = refusals(RULED, "version: 1\noperations: []\n")
        assert "no operations" in found.problem

    def test_an_unknown_top_level_key(self):
        text = "version: 1\ntitle: other\noperations: [{append: intro, text: x}]\n"
        (found,) = refusals(RULED, text)
        assert "'title'" in found.problem and "only 'version' and 'operations'" in found.fix

    @pytest.mark.parametrize("key", ["capabilities", "model", "tools", "tier", "name"])
    def test_capabilities_and_model_are_refused_by_name(self, key):
        text = f"version: 1\n{key}: x\noperations: [{{append: intro, text: x}}]\n"
        (found,) = refusals(RULED, text)
        assert repr(key) in found.problem and "ruling 5" in found.problem

    @pytest.mark.parametrize("key", ["capabilities", "model"])
    def test_an_operation_cannot_change_them_either(self, key):
        (found,) = refusals(RULED, overlay({key: "x"}))
        assert "ruling 5" in found.problem

    def test_an_unknown_operation(self):
        (found,) = refusals(RULED, overlay({"prepend": "intro", "text": "x"}))
        assert "operation 1" in found.problem and "'prepend'" in found.problem
        assert "append" in found.fix

    def test_two_operations_in_one_entry(self):
        (found,) = refusals(RULED, overlay({"append": "intro", "replace": "intro", "text": "x"}))
        assert "more than one operation" in found.problem

    def test_an_operation_that_is_not_a_mapping(self):
        (found,) = refusals(RULED, "version: 1\noperations: [append]\n")
        assert "operation 1" in found.problem

    def test_an_unknown_key_on_an_operation(self):
        (found,) = refusals(RULED, overlay({"append": "intro", "text": "x", "after": "y"}))
        assert "'after'" in found.problem

    def test_a_missing_text(self):
        (found,) = refusals(RULED, overlay({"append": "intro"}))
        assert "no text" in found.problem

    def test_text_that_is_not_a_string(self):
        (found,) = refusals(RULED, overlay({"append": "intro", "text": 3}))
        assert "text" in found.problem

    def test_text_carrying_an_anchor(self):
        text = "<!-- invariant:section sneaky -->\nx"
        (found,) = refusals(RULED, overlay({"append": "intro", "text": text}))
        assert "anchor" in found.problem and "insert_after" in found.fix

    def test_an_anchor_that_is_not_a_string(self):
        (found,) = refusals(RULED, overlay({"append": 3, "text": "x"}))
        assert "anchor" in found.problem

    def test_a_missing_anchor_names_the_nearest_real_one(self):
        (found,) = refusals(RULED, overlay({"append": "phase_1", "text": "x"}))
        assert "'phase_1'" in found.problem
        assert "'phase-1'" in found.fix

    def test_a_missing_anchor_far_from_any_names_the_list(self):
        (found,) = refusals(RULED, overlay({"replace": "zzzzzz", "text": "x"}))
        assert "intro, phase-1, phase-2" in found.fix

    def test_an_inserted_id_that_collides(self):
        (found,) = refusals(
            RULED, overlay({"insert_after": "intro", "id": "phase-2", "text": "x"})
        )
        assert "'phase-2'" in found.problem and "already" in found.problem

    def test_an_inserted_id_that_collides_with_an_earlier_insert(self):
        (found,) = refusals(
            RULED,
            overlay(
                {"insert_after": "intro", "id": "mine", "text": "x"},
                {"insert_after": "phase-1", "id": "mine", "text": "y"},
            ),
        )
        assert "operation 2" in found.problem

    def test_an_insert_with_no_id(self):
        (found,) = refusals(RULED, overlay({"insert_after": "intro", "text": "x"}))
        assert "no id" in found.problem

    @pytest.mark.parametrize("bad", ["Has Caps", "-lead", "", 3])
    def test_a_malformed_inserted_id(self, bad):
        (found,) = refusals(RULED, overlay({"insert_after": "intro", "id": bad, "text": "x"}))
        assert "id" in found.problem

    @pytest.mark.parametrize("bad", ["", "two\nlines", 3])
    def test_a_description_append_must_be_one_line_of_text(self, bad):
        (found,) = refusals(RULED, overlay({"description_append": bad}))
        assert "description_append" in found.problem

    def test_a_description_append_takes_no_other_key(self):
        (found,) = refusals(RULED, overlay({"description_append": "x", "text": "y"}))
        assert "'text'" in found.problem

    def test_every_problem_is_named_in_one_refusal(self):
        found = refusals(
            RULED,
            overlay(
                {"append": "nope", "text": "x"},
                {"prepend": "intro"},
                {"insert_after": "intro", "id": "phase-1", "text": "y"},
            ),
        )
        import re

        assert [re.match(r"operation (\d+)", p.problem).group(1) for p in found] == [
            "1",
            "2",
            "3",
        ]
