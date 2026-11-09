"""ADR-018 D12 (increment 4): the guideline rules rendered as rule needs.

`invariant guidelines render` writes a third generated file in a project
whose docs are a sphinx-needs project: `<srcdir>/rules.rst`, one need of the
`rule` role's type per rule, id `<prefix><RULE_ID_UPPERCASED>` with every
non-alphanumeric character as `_`. The file carries the managed marker, and
`guidelines.generated_current` reads it as it reads the checklist. Two rule
ids that would give one need id are refused at ruleset load. Need ids in a
rule's `links` and `amended[].adr` render as `decided_by`; a path link stays
text. Under the mandate (`docs.needs.state: enforced`) the checklist is rST
in the docs project, so both rendered files sit where the starter's toctree
names them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from test_cli import payload, run
from test_guidelines import RULESET, mutated, problems_of
from test_guidelines_domain import RULESET_REL, write

from invariant.core import guidelines
from invariant.core.guidelines import parse
from invariant.core.rules import RenderRefused, RuleContext, all_rules, evaluate
from invariant.domains.guidelines import GuidelinesDomain

REGISTERED = frozenset(spec.id for spec in all_rules())
RULES_REL = "docs/rules.rst"

#: A ruleset whose first rule links a decision need and a path, and whose
#: amendment cites another decision need.
LINKED = RULESET.replace(
    "links: [docs/architecture/overview.md]",
    "links: [DEC_ADR_012, docs/architecture/overview.md]",
).replace("adr: docs/adr/ADR-012.md", "adr: DEC_ADR_012_D3")


def docs_project(root: Path) -> None:
    """A sphinx-needs docs project at docs/, its architecture pages below it."""
    write(root, "docs/conf.py", "extensions = ['sphinx_needs']\n")
    (root / "docs" / "architecture").mkdir(parents=True, exist_ok=True)


def body(*, fmt="sphinx", state="migrating", needs=None, guidelines_cfg=None, **extra):
    data = {
        "version": 1,
        "project": {"name": "demo", "ecosystems": ["python"]},
        "guidelines": {"ruleset": RULESET_REL, **(guidelines_cfg or {})},
        "docs": {"format": fmt, "needs": {"state": state, **(needs or {})}},
        **extra,
    }
    if state == "enforced":
        data["items"] = {"backend": "sphinx_needs", "path": "docs/backlog.rst"}
    return data


def context(make_manifest, root, **kwargs) -> RuleContext:
    return RuleContext(manifest=make_manifest(body(**kwargs)), cwd=root)


def domain(make_manifest, root, **kwargs) -> GuidelinesDomain:
    return GuidelinesDomain(context(make_manifest, root, **kwargs))


@pytest.fixture
def tree(tmp_path):
    write(tmp_path, RULESET_REL, LINKED)
    docs_project(tmp_path)
    return tmp_path


def ruleset(text=LINKED):
    return parse(text, registered=REGISTERED)


# -- the need id -------------------------------------------------------------------


class TestNeedId:
    def test_the_rule_id_uppercased_with_every_other_character_as_underscore(self):
        assert guidelines.rule_need_id("layers.only-the-cli-imports-the-oracle") == (
            "RULE_LAYERS_ONLY_THE_CLI_IMPORTS_THE_ORACLE"
        )

    def test_the_prefix_is_the_rule_types(self):
        assert guidelines.rule_need_id("di.x", "GL_") == "GL_DI_X"

    def test_two_ids_that_collide_are_refused_at_load(self):
        def edit(raw):
            twin = dict(raw["rules"][1])
            twin["id"] = "tests-coverage-floor"
            raw["rules"].append(twin)

        problems = problems_of(mutated(edit))
        assert any(
            "tests-coverage-floor" in p
            and "tests.coverage-floor" in p
            and "RULE_TESTS_COVERAGE_FLOOR" in p
            for p in problems
        ), problems

    def test_ids_that_differ_only_in_case_collide_too(self):
        def edit(raw):
            twin = dict(raw["rules"][1])
            twin["id"] = "Tests.Coverage-Floor"
            raw["rules"].append(twin)

        assert any("collide" in p for p in problems_of(mutated(edit)))

    def test_a_need_id_is_accepted_as_an_amendment_adr(self):
        rule = ruleset().rules[0]
        assert rule.amended[0].adr == "DEC_ADR_012_D3"
        assert rule.links == ("DEC_ADR_012", "docs/architecture/overview.md")


# -- the pure renderer ----------------------------------------------------------------


class TestRenderRuleNeeds:
    def render(self, rs=None, **kwargs):
        return guidelines.render_rule_needs(
            ruleset() if rs is None else rs, source=RULESET_REL, **kwargs
        )

    def test_the_marker_holds_the_digest_of_the_body(self):
        text = self.render()
        digest, rest = guidelines.split_managed(text, "sphinx")
        assert digest == guidelines.digest_of(rest.encode("utf-8"))

    def test_one_rule_need_per_rule(self):
        text = self.render()
        assert text.count(".. rule:: ") == 2
        assert ".. rule:: Views never touch concrete services or singletons" in text
        assert "   :id: RULE_DI_VIEWS_NO_CONCRETE_SERVICES" in text
        assert "   :id: RULE_TESTS_COVERAGE_FLOOR" in text

    def test_need_ids_in_links_and_amended_render_as_decided_by(self):
        text = self.render()
        assert "   :decided_by: DEC_ADR_012, DEC_ADR_012_D3" in text
        assert text.count(":decided_by:") == 1

    def test_a_path_link_stays_text(self):
        text = self.render()
        assert "``docs/architecture/overview.md``" in text
        assert "DEC_ADR_012``" not in text

    def test_the_statement_is_the_need_content(self):
        text = self.render()
        assert "   A view never reaches a concrete service or a shared singleton." in text
        assert "   - Rule: ``di.views-no-concrete-services``" in text

    def test_the_directive_and_prefix_are_the_projects(self):
        text = self.render(directive="guide", prefix="GL_")
        assert ".. guide:: " in text and "   :id: GL_DI_VIEWS_NO_CONCRETE_SERVICES" in text

    def test_no_ruleset_renders_a_page_with_no_rules(self):
        text = guidelines.render_rule_needs(None, source=None)
        assert text.startswith(".. invariant-managed: ")
        assert ".. rule::" not in text
        assert "guidelines.ruleset" in text

    def test_no_ruleset_renders_a_checklist_page_too(self):
        text = guidelines.render_checklist(None, "sphinx", source=None)
        digest, rest = guidelines.split_managed(text, "sphinx")
        assert digest == guidelines.digest_of(rest.encode("utf-8"))
        assert "guidelines.ruleset" in text


# -- the render writes it ---------------------------------------------------------------


class TestRenderWritesRulesRst:
    def test_a_sphinx_needs_project_gets_rules_rst_in_its_source_directory(
        self, make_manifest, tree
    ):
        result = domain(make_manifest, tree).render()
        assert result.rules_relpath == RULES_REL
        assert result.rules_status == "new"
        text = (tree / RULES_REL).read_text(encoding="utf-8")
        assert text == guidelines.render_rule_needs(ruleset(), source=RULESET_REL)

    def test_a_second_render_is_current(self, make_manifest, tree):
        domain(make_manifest, tree).render()
        assert domain(make_manifest, tree).render().rules_status == "current"

    def test_dry_run_writes_nothing(self, make_manifest, tree):
        result = domain(make_manifest, tree).render(dry_run=True)
        assert result.rules_status == "new"
        assert not (tree / RULES_REL).exists()

    def test_a_markdown_project_gets_no_rules_page(self, make_manifest, tree):
        result = domain(make_manifest, tree, fmt="markdown").render()
        assert result.rules_relpath is None and result.rules_status is None
        assert not (tree / RULES_REL).exists()

    def test_a_sphinx_project_without_a_docs_project_gets_none(self, make_manifest, tmp_path):
        write(tmp_path, RULESET_REL, LINKED)
        result = domain(make_manifest, tmp_path).render()
        assert result.rules_relpath is None

    def test_the_rule_type_comes_from_docs_needs_types(self, make_manifest, tree):
        types = [
            {"directive": "req", "prefix": "REQ_", "role": "requirement"},
            {"directive": "dec", "prefix": "DEC_", "role": "decision"},
            {"directive": "comp", "prefix": "COMP_", "role": "element"},
            {"directive": "ui", "prefix": "UI_", "role": "ui"},
            {"directive": "guide", "prefix": "GL_", "role": "rule"},
            {"directive": "idea", "prefix": "IDEA_", "role": "idea"},
            {"directive": "bug", "prefix": "BUG_", "role": "bug"},
        ]
        domain(make_manifest, tree, needs={"types": types}).render()
        text = (tree / RULES_REL).read_text(encoding="utf-8")
        assert ".. guide:: " in text and ":id: GL_TESTS_COVERAGE_FLOOR" in text

    def test_an_existing_rules_rst_render_did_not_write_is_refused(self, make_manifest, tree):
        write(tree, RULES_REL, "Our rules\n=========\n")
        with pytest.raises(RenderRefused) as caught:
            domain(make_manifest, tree).render()
        assert "no invariant-managed marker" in str(caught.value)
        assert (tree / RULES_REL).read_text(encoding="utf-8") == "Our rules\n=========\n"
        assert not (tree / "docs/guidelines/checklist.rst").exists()

    def test_an_exempt_rules_page_is_refused(self, make_manifest, tree):
        dom = domain(make_manifest, tree, session={"exempt_paths": [RULES_REL]})
        with pytest.raises(RenderRefused) as caught:
            dom.render(dry_run=True)
        assert "exempt from session isolation" in str(caught.value)

    def test_a_privacy_finding_in_the_rules_page_refuses_the_render(self, make_manifest, tree):
        write(tree, RULESET_REL, LINKED.replace(
            "A view never reaches", "Ask TICKET-4242 first. A view never reaches"
        ))
        allow = [{"paths": ["docs/guidelines/checklist.rst"], "reason": "r"}]
        dom = domain(
            make_manifest, tree, privacy={"extra": ["TICKET-[0-9]+"], "allow": allow}
        )
        with pytest.raises(RenderRefused) as caught:
            dom.render()
        assert f"{RULES_REL}:" in str(caught.value) and "privacy" in str(caught.value)
        assert not (tree / RULES_REL).exists()

    def test_a_hand_edit_is_named_and_replaced(self, make_manifest, tree):
        domain(make_manifest, tree).render()
        path = tree / RULES_REL
        path.write_text(path.read_text(encoding="utf-8") + "\nA hand edit.\n", encoding="utf-8")
        result = domain(make_manifest, tree).render()
        assert result.rules_status == "edited"
        assert "A hand edit." not in path.read_text(encoding="utf-8")


class TestUnderTheMandate:
    """Enforced: the checklist is rST in the docs project, beside rules.rst."""

    def test_the_checklist_is_rendered_in_rst_in_the_source_directory(
        self, make_manifest, tree
    ):
        result = domain(make_manifest, tree, state="enforced").render()
        assert result.relpath == "docs/checklist.rst"
        text = (tree / "docs/checklist.rst").read_text(encoding="utf-8")
        assert text.startswith(".. invariant-managed: ")
        assert not any(tree.rglob("checklist.md"))
        assert result.rules_relpath == RULES_REL

    def test_a_markdown_checklist_is_refused_at_load(self, make_manifest, tree):
        from invariant.core.errors import ManifestError

        with pytest.raises(ManifestError):
            make_manifest(body(state="enforced", fmt="markdown"))

    def test_a_configured_checklist_must_be_rst(self, make_manifest, tree):
        dom = domain(
            make_manifest, tree, state="enforced",
            guidelines_cfg={"checklist": "docs/checklist.md"},
        )
        with pytest.raises(RenderRefused) as caught:
            dom.render()
        assert ".rst" in str(caught.value)


# -- the drift rule ---------------------------------------------------------------------


class TestGeneratedCurrent:
    def current(self, make_manifest, root, **kwargs):
        (result,) = evaluate(["guidelines.generated_current"], context(make_manifest, root, **kwargs))
        return result.verdict

    def test_rules_rst_never_rendered_is_missing(self, make_manifest, tree):
        result = self.current(make_manifest, tree)
        assert not result.ok
        assert f"{RULES_REL} is missing" in result.message

    def test_a_render_is_current(self, make_manifest, tree):
        domain(make_manifest, tree).render()
        result = self.current(make_manifest, tree)
        assert result.ok, result.message
        assert "2 generated output(s) current" in result.message

    def test_a_hand_edit_of_rules_rst_is_caught(self, make_manifest, tree):
        domain(make_manifest, tree).render()
        path = tree / RULES_REL
        path.write_text(path.read_text(encoding="utf-8").replace("A view", "Any view"),
                        encoding="utf-8")
        result = self.current(make_manifest, tree)
        assert not result.ok
        assert f"{RULES_REL} was edited by hand" in result.message
        assert any("move the edit into the ruleset" in fix for fix in result.fixes)

    def test_a_changed_ruleset_makes_it_stale(self, make_manifest, tree):
        domain(make_manifest, tree).render()
        write(tree, RULESET_REL, LINKED.replace("severity: warn", "severity: blocker"))
        result = self.current(make_manifest, tree)
        assert f"{RULES_REL} is stale" in result.message


# -- the command ---------------------------------------------------------------------------


class TestCommand:
    @pytest.fixture(autouse=True)
    def _inside(self, project, monkeypatch):
        monkeypatch.chdir(project)

    def test_render_json_carries_the_rules_page(self, project, capsys):
        write(project, RULESET_REL, LINKED)
        docs_project(project)
        manifest = project / ".claude" / "invariant.yaml"
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        data["guidelines"] = {"ruleset": RULESET_REL}
        data["project"]["ecosystems"] = ["python"]
        data.setdefault("docs", {})["format"] = "sphinx"
        manifest.write_text(yaml.safe_dump(data), encoding="utf-8")
        assert run("--json", "guidelines", "render") == 0
        rendered = payload(capsys)
        assert rendered["rules"] == {"path": RULES_REL, "status": "new", "written": True}
        assert run("guidelines", "render", "--dry-run") == 0
        from test_cli import out

        assert f"would write {RULES_REL}" in out(capsys)
