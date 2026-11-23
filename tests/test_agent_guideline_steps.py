"""The design and review agents name the default guidelines (ADR-032 D10, I4).

The rules reach every agent through the ruleset; these steps make the two
roles that answer for them say so. feature-architect's design gives each
component its layer and each arrow its direction, writes a SOLID block that
names the five principles one by one, names each new abstraction's pattern,
and edits the design documents before it leaves the design state; its
Boy-Scout pass applies the five principles. compliance-reviewer's review
carries a verdict on each ``solid.*`` rule by id, the derived layer rules,
the pattern rule and both docs-first checks. Neither waives a finding on a
rule only the user may except: each proposes the exception with the exact
command for the user to run.
"""

from __future__ import annotations

import re

import pytest

from invariant.core.agents import parse
from invariant.hosts import claude_code
from invariant.hosts.claude_code import render_agent

LIBRARY = claude_code.DATA_DIR / "agents"
BOTH = ("feature-architect", "compliance-reviewer")

PRINCIPLES = (
    "single responsibility",
    "open/closed",
    "Liskov substitution",
    "interface segregation",
    "dependency inversion",
)
SOLID_RULES = (
    "solid.single-responsibility",
    "solid.open-closed",
    "solid.liskov-substitution",
    "solid.interface-segregation",
    "solid.dependency-inversion",
)
DEFAULT_LAYERS = ("Entities", "Use Cases", "Interface Adapters", "Frameworks & Drivers")

EXCEPT_LINE = (
    "invariant guidelines except <rule-id> <path> [--fingerprint <fp>] "
    "--by <who> --reason <why>"
)
LOOSEN_LINE = "invariant guidelines loosen <rule-id> --to warn|off --by <who> --reason <why>"
RETIRE_LINE = "invariant guidelines retire <rule-id> --by <who> --reason <why>"

#: Host and model names the sources never carry (DEC_ADR_032_D10).
HOST_OR_MODEL = re.compile(r"claude|anthropic|opus|sonnet|haiku|gpt|gemini", re.IGNORECASE)


def agent(name):
    return parse((LIBRARY / f"{name}.md").read_text(encoding="utf-8"), name)


def section(name, section_id):
    return " ".join(
        next(s for s in agent(name).sections if s.id == section_id).text.split()
    )


def rendered(name):
    return " ".join(render_agent(agent(name)).split())


class TestTheArchitectsDesign:
    def test_it_names_the_five_principles_one_by_one(self):
        text = section("feature-architect", "phase-2").lower()
        assert "solid" in text
        for principle in PRINCIPLES:
            assert principle.lower() in text, principle

    def test_each_component_gets_its_layer_and_each_arrow_points_inward(self):
        text = section("feature-architect", "phase-2")
        for title in DEFAULT_LAYERS:
            assert title in text, title
        assert "layers.<layer id>-depends-inward" in text
        assert "inward" in text
        assert "unmapped" in text

    def test_each_new_abstraction_names_its_pattern_or_why_none_fits(self):
        text = section("feature-architect", "phase-2")
        assert "patterns.named-or-declined" in text
        assert "patterns.rst" in text
        assert "why none fits" in text
        assert "no catalogue configured" in text

    def test_the_design_documents_are_edited_before_leaving_design(self):
        text = section("feature-architect", "phase-2")
        assert "docs.ahead_of_code" in text
        assert "docs.design-before-code" in text
        assert "before the first advance out of the design state" in text

    def test_the_boy_scout_pass_applies_the_five_principles(self):
        text = section("feature-architect", "phase-4").lower()
        for principle in PRINCIPLES:
            assert principle.lower() in text, principle


class TestTheReviewersReview:
    def test_it_answers_each_solid_rule_by_its_id(self):
        text = section("compliance-reviewer", "phase-2")
        for rule in SOLID_RULES:
            assert rule in text, rule

    def test_it_names_the_layer_pattern_and_docs_first_rules(self):
        text = section("compliance-reviewer", "phase-2")
        assert "layers.<layer id>-depends-inward" in text
        assert "patterns.named-or-declined" in text
        assert "docs.design-before-code" in text
        assert "invariant check docs.ahead_of_code" in text

    def test_it_never_answers_a_user_owned_rule_na(self):
        text = section("compliance-reviewer", "phase-2")
        assert "owner: user" in text
        assert "never N/A" in text


class TestProposeNeverWaive:
    @pytest.mark.parametrize("name", BOTH)
    def test_the_rendered_agent_carries_the_exception_line(self, name):
        text = rendered(name)
        assert EXCEPT_LINE in text
        assert "PROPOSED EXCEPTIONS" in text

    @pytest.mark.parametrize("name", BOTH)
    def test_changing_the_rule_is_named_as_the_users(self, name):
        text = rendered(name)
        assert LOOSEN_LINE in text
        assert RETIRE_LINE in text

    @pytest.mark.parametrize("name", BOTH)
    def test_waiving_is_refused_in_so_many_words(self, name):
        text = rendered(name)
        assert "never answered N/A, never downgraded" in text


class TestBothRenderedAgents:
    @pytest.mark.parametrize("name", BOTH)
    def test_each_carries_every_principle_layer_pattern_and_docs_ahead_step(self, name):
        text = rendered(name)
        for principle in PRINCIPLES:
            assert principle.lower() in text.lower(), principle
        assert "layers.<layer id>-depends-inward" in text
        assert "patterns.named-or-declined" in text
        assert "docs.ahead_of_code" in text

    @pytest.mark.parametrize("name", BOTH)
    def test_the_sources_name_no_host_and_no_model(self, name):
        source = (LIBRARY / f"{name}.md").read_text(encoding="utf-8")
        assert HOST_OR_MODEL.findall(source) == []
