"""ADR-032 increment 1b: invariant adopts the default guidelines.

The defaults are merged into a project's ruleset, never retyped
(DEC_ADR_032_I1B): ``core/guidelines.merge_defaults`` adds, as text, what the
project's ruleset lacks of the default set (the version, the catalogue key,
the absent sections, the layer block with its paths filled from a map, the
absent rules), keeps every byte the project wrote, stamps the added origins,
and refuses a result that does not read back as the project's ruleset plus
those additions. ``GuidelinesDomain.merge_defaults`` validates and writes it
and copies the catalogue beside it; ``domains/docs.default_ruleset`` is the
starter's one fill of the shipped template.

The merge's result on this repository, and the increment's deliverable test
there, are ``test_dogfood_ruleset.py``'s.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from test_guidelines_domain import RULESET_REL, write

from invariant.core import guidelines
from invariant.core.guidelines import RulesetError, merge_defaults
from invariant.core.rules import RuleContext, all_rules, evaluate
from invariant.domains import docs as docs_domain
from invariant.domains.guidelines import DefaultsRefused, GuidelinesDomain

REGISTERED = frozenset(spec.id for spec in all_rules())
REPO = Path(__file__).resolve().parent.parent
REPO_RULESET = REPO / "docs" / "guidelines" / "ruleset.yaml"
REPO_CATALOGUE = REPO / "docs" / "guidelines" / "patterns.yaml"
SCAFFOLD = Path(guidelines.__file__).resolve().parent.parent / "data" / "scaffold"
SHIPPED_CATALOGUE = SCAFFOLD / "guidelines" / "patterns.yaml"
CATALOGUE_REL = "docs/guidelines/patterns.yaml"
SOLID = (
    "solid.single-responsibility", "solid.open-closed", "solid.liskov-substitution",
    "solid.interface-segregation", "solid.dependency-inversion",
)
PLACEHOLDER = guidelines.ORIGIN_PLACEHOLDER

PROJECT = """\
# The project's own header comment.
version: 1
sections:
  - id: layers
    title: Layering
  - id: docs
    title: Design records
rules:
  - id: layers.app-never-imports-tools
    section: layers
    title: App never imports tools
    statement: >
      Nothing in app imports tools; a folded statement the project wrote
      across two lines.
    severity: blocker
    phases: [review]
    applies_to: ["app/*.py"]
    check:
      kind: boundary
      engine: imports
      forbid: ["tools/*"]
  # A comment the project keeps between its rules.
  - id: docs.kept
    section: docs
    title: Kept as written
    statement: The project's own wording.
    severity: warn
    phases: [review]
    applies_to: ["**"]
    check:
      kind: judgement
      evidence: citation
      scope: diff
"""

DEFAULTS = f"""\
# The defaults' header, which is not copied.
version: 2
catalogue: patterns.yaml
sections:
  - id: layers
    title: Layers
  - id: solid
    title: SOLID principles
  - id: docs
    title: Design records
layers:
  # A comment inside the block travels with it.
  owner: user
  origin: {{set: defaults, version: 1, digest: "{PLACEHOLDER}"}}
  map:
    - id: inner
      title: Inner
      paths: []
    - id: outer
      title: Outer
      paths: []
rules:
  - id: solid.one
    section: solid
    title: One
    statement: |
      First paragraph.

      Fix: the second.
    severity: blocker
    owner: user
    phases: [design, review, landing]
    applies_to:
      - "**/*.py"
    check:
      kind: judgement
      evidence: citation
      scope: diff
    origin: {{set: defaults, version: 1, digest: "{PLACEHOLDER}"}}
  - id: docs.kept
    section: docs
    title: The default wording, which must not replace the project's
    statement: Shipped.
    severity: warn
    phases: [review]
    applies_to: ["**"]
    check:
      kind: judgement
      evidence: citation
      scope: diff
    origin: {{set: defaults, version: 1, digest: "{PLACEHOLDER}"}}
"""

MAP = {"inner": ["app/**"], "outer": ["tools/**", "cli/**"]}


def merged(project=PROJECT, defaults=DEFAULTS, layers=MAP):
    return merge_defaults(project, defaults, layers=layers)


class TestMergeDefaults:
    def test_it_adds_what_the_project_lacks(self):
        result = merged()
        assert result.version == 2
        assert result.catalogue == "patterns.yaml"
        assert result.sections == ("solid",)
        assert result.layers is True
        assert result.rules == ("solid.one",)
        assert result.kept == ("docs.kept",)
        data = yaml.safe_load(result.text)
        assert data["version"] == 2 and data["catalogue"] == "patterns.yaml"
        assert [s["id"] for s in data["sections"]] == ["layers", "docs", "solid"]
        assert [r["id"] for r in data["rules"]] == [
            "layers.app-never-imports-tools", "docs.kept", "solid.one",
        ]

    def test_every_line_the_project_wrote_is_kept_in_order(self):
        lines = merged().text.splitlines()
        theirs = [line for line in PROJECT.splitlines() if not line.startswith("version:")]
        found = iter(lines)
        assert all(line in found for line in theirs)
        assert lines[0] == "# The project's own header comment."
        assert "# The defaults' header, which is not copied." not in lines

    def test_a_present_rule_is_never_rewritten(self):
        data = yaml.safe_load(merged().text)
        kept = next(r for r in data["rules"] if r["id"] == "docs.kept")
        assert kept["title"] == "Kept as written" and "origin" not in kept
        assert next(s for s in data["sections"] if s["id"] == "layers")["title"] == "Layering"

    def test_the_layer_paths_are_filled_and_the_block_precedes_the_rules(self):
        text = merged().text
        data = yaml.safe_load(text)
        assert [layer["paths"] for layer in data["layers"]["map"]] == [
            ["app/**"], ["tools/**", "cli/**"],
        ]
        assert text.index("\nlayers:\n") < text.index("\nrules:\n")
        assert "  # A comment inside the block travels with it.\n" in text
        assert text.index("catalogue: patterns.yaml\n") == text.index("version: 2\n") + len(
            "version: 2\n"
        )

    def test_the_added_origins_are_the_digests_of_the_project_copy(self):
        data = yaml.safe_load(merged().text)
        added = next(r for r in data["rules"] if r["id"] == "solid.one")
        assert added["origin"]["digest"] == guidelines.origin_digest(added)
        assert data["layers"]["origin"]["digest"] == guidelines.origin_digest(data["layers"])
        assert PLACEHOLDER not in merged().text

    def test_the_result_loads_and_derives_the_layer_rule(self):
        ruleset = guidelines.parse(
            merged().text, registered=REGISTERED, location=RULESET_REL,
            read=lambda rel: SHIPPED_CATALOGUE.read_bytes() if rel == CATALOGUE_REL else None,
        )
        derived = ruleset.by_id("layers.inner-depends-inward")
        assert derived.check.forbid == ("tools/**", "cli/**")
        assert ruleset.by_id("solid.one").statement.startswith("First paragraph.")

    def test_without_a_map_the_layers_stay_empty(self):
        data = yaml.safe_load(merged(layers=None).text)
        assert [layer["paths"] for layer in data["layers"]["map"]] == [[], []]

    def test_merging_again_changes_nothing(self):
        once = merged().text
        again = merge_defaults(once, DEFAULTS, layers=None)
        assert again.text == once
        assert (again.version, again.catalogue, again.sections, again.layers, again.rules) == (
            None, None, (), False, (),
        )
        assert again.kept == ("solid.one", "docs.kept")

    def test_a_differently_indented_project_is_reindented(self):
        project = (
            "version: 1\nsections:\n    -   id: docs\n        title: Docs\n"
            "rules:\n    -   id: docs.kept\n        section: docs\n        title: T\n"
            "        statement: S.\n        severity: warn\n        phases: [review]\n"
            "        applies_to: ['**']\n        check:\n            kind: judgement\n"
            "            evidence: citation\n            scope: diff\n"
        )
        data = yaml.safe_load(merged(project=project).text)
        assert [r["id"] for r in data["rules"]] == ["docs.kept", "solid.one"]
        assert [s["id"] for s in data["sections"]] == ["docs", "layers", "solid"]

    def test_a_map_given_where_the_project_has_one_is_refused(self):
        with pytest.raises(RulesetError, match="already maps its layers"):
            merge_defaults(merged().text, DEFAULTS, layers=MAP)

    def test_a_layer_the_defaults_do_not_name_is_refused(self):
        with pytest.raises(RulesetError, match="'middle'"):
            merged(layers={"middle": ["x/**"]})

    @pytest.mark.parametrize(
        ("project", "words"),
        [
            ("- a list\n", "a mapping"),
            ("version: 9\nsections: []\nrules: []\n", "version 9"),
            ("version: [\n", "not valid YAML"),
        ],
    )
    def test_a_project_ruleset_it_cannot_read_is_refused(self, project, words):
        with pytest.raises(RulesetError, match=words):
            merged(project=project)

    def test_defaults_it_cannot_read_are_refused(self):
        with pytest.raises(RulesetError, match="defaults"):
            merged(defaults="- not a mapping\n")

    def test_a_flow_style_block_that_cannot_be_appended_to_is_refused(self):
        project = PROJECT.replace("rules:\n", "rules: []\nold_rules:\n", 1)
        with pytest.raises(RulesetError):
            merged(project=project)

    def test_a_project_with_no_sections_block_is_refused(self):
        with pytest.raises(RulesetError, match="sections"):
            merged(project="version: 1\nrules: []\n")

    def test_an_indentless_project_list_is_dedented_to_it(self):
        project = PROJECT.replace("\n  - id: layers\n    title: Layering\n  - id: docs\n    title: Design records\n",
                                  "\n- id: layers\n  title: Layering\n- id: docs\n  title: Design records\n")
        text = merged(project=project).text
        assert "\n- id: solid\n  title: SOLID principles\n" in text
        assert [s["id"] for s in yaml.safe_load(text)["sections"]] == ["layers", "docs", "solid"]

    def test_a_project_with_no_rules_block_is_refused(self):
        project = "version: 1\nsections:\n  - id: docs\n    title: D\n"
        with pytest.raises(RulesetError, match="no rules block"):
            merged(project=project)

    def test_a_list_that_is_not_of_mappings_is_refused(self):
        with pytest.raises(RulesetError, match="sections must be a list of mappings"):
            merged(project="version: 1\nsections: {a: 1}\nrules: []\n")

    def test_defaults_of_a_version_this_build_does_not_read_are_refused(self):
        with pytest.raises(RulesetError, match="the defaults are version 7"):
            merged(defaults=DEFAULTS.replace("version: 2\n", "version: 7\n", 1))

    def test_a_layer_whose_paths_are_not_empty_in_the_defaults_is_refused(self):
        defaults = DEFAULTS.replace("      title: Inner\n      paths: []", '      title: Inner\n      paths: ["x/**"]')
        with pytest.raises(RulesetError, match="'inner' has no empty paths"):
            merged(defaults=defaults)

    def test_a_project_text_with_no_final_newline_is_merged(self):
        result = merged(project=PROJECT.rstrip("\n"))
        assert result.rules == ("solid.one",) and result.text.endswith("\n")

    def test_the_layer_block_goes_above_the_comment_that_heads_the_rules(self):
        project = PROJECT.replace("rules:\n", "\n# The rules.\nrules:\n", 1)
        text = merged(project=project).text
        assert text.index("\nlayers:\n") < text.index("# The rules.\nrules:\n")

    def test_a_value_the_merge_would_change_is_refused(self):
        """A kept block scalar's trailing lines are loose to the merge, so the
        rules appended before them would change the project's value."""
        project = PROJECT + "    rationale: |+\n      kept\n\n"
        with pytest.raises(RulesetError, match="does not read back"):
            merged(project=project)

    def test_a_result_that_does_not_read_back_is_refused(self):
        with pytest.raises(RulesetError, match="does not read back"):
            merged(project=PROJECT + "...\n")


# -- the defaults' one fill ----------------------------------------------------------------------


class TestDefaultRuleset:
    def test_it_is_the_template_filled_and_unstamped(self, make_manifest):
        manifest = make_manifest({"version": 1, "project": {"name": "d", "ecosystems": ["python"]}})
        text = docs_domain.default_ruleset(manifest, "docs/backlog.rst")
        assert "{{" not in text and PLACEHOLDER in text
        data = yaml.safe_load(text)
        assert data["rules"][0]["applies_to"] == ["**/*.py"]
        items = next(r for r in data["rules"] if r["id"] == "docs.items-carry-no-design")
        assert items["applies_to"] == ["docs/backlog.rst"]


# -- the writer ----------------------------------------------------------------------------------


V1 = PROJECT.replace("docs.kept", "docs.own")


def defaults_text(make_manifest) -> str:
    manifest = make_manifest({"version": 1, "project": {"name": "d", "ecosystems": ["python"]}})
    return docs_domain.default_ruleset(manifest, "docs/backlog.rst")


@pytest.fixture
def tree(tmp_path):
    write(tmp_path, RULESET_REL, V1)
    return tmp_path


def domain(make_manifest, root, *, admit=None, ruleset=RULESET_REL) -> GuidelinesDomain:
    body = {"version": 1, "project": {"name": "d", "ecosystems": ["python"]}}
    if ruleset is not None:
        body["guidelines"] = {"ruleset": ruleset}
    return GuidelinesDomain(
        RuleContext(manifest=make_manifest(body), cwd=root), admit=admit,
    )


LAYER_MAP = {
    "entities": ["app/**"], "use-cases": ["usecases/**"],
    "interface-adapters": ["adapters/**"], "frameworks-drivers": ["tools/**"],
}


class TestTheWriter:
    def test_it_writes_the_merge_and_copies_the_catalogue(self, make_manifest, tree):
        catalogue = SHIPPED_CATALOGUE.read_text(encoding="utf-8")
        result = domain(make_manifest, tree).merge_defaults(
            defaults_text(make_manifest), catalogue=catalogue, layers=LAYER_MAP,
        )
        assert result.written and result.relpath == RULESET_REL
        assert result.catalogue_relpath == CATALOGUE_REL and result.catalogue_written
        assert set(SOLID) <= set(result.merge.rules)
        assert (tree / CATALOGUE_REL).read_text(encoding="utf-8") == catalogue
        ctx = RuleContext(manifest=domain(make_manifest, tree).manifest, cwd=tree)
        (valid,) = evaluate(["guidelines.ruleset_valid"], ctx)
        assert valid.verdict.ok, valid.verdict.message

    def test_a_dry_run_writes_nothing(self, make_manifest, tree):
        result = domain(make_manifest, tree).merge_defaults(
            defaults_text(make_manifest), catalogue="version: 1\npatterns: []\n",
            dry_run=True,
        )
        assert not result.written and not result.catalogue_written
        assert (tree / RULESET_REL).read_text(encoding="utf-8") == V1
        assert not (tree / CATALOGUE_REL).exists()

    def test_a_catalogue_the_project_has_is_never_rewritten(self, make_manifest, tree):
        own = SHIPPED_CATALOGUE.read_text(encoding="utf-8") + "# the project's own\n"
        write(tree, CATALOGUE_REL, own)
        result = domain(make_manifest, tree).merge_defaults(
            defaults_text(make_manifest), catalogue="version: 1\npatterns: []\n",
        )
        assert result.written and not result.catalogue_written
        assert (tree / CATALOGUE_REL).read_text(encoding="utf-8") == own

    def test_every_path_is_admitted_before_the_first_write(self, make_manifest, tree):
        seen = []

        def refuse(paths):
            seen.extend(paths)
            raise DefaultsRefused("locked", "take the lock")

        with pytest.raises(DefaultsRefused, match="locked"):
            domain(make_manifest, tree, admit=refuse).merge_defaults(
                defaults_text(make_manifest), catalogue="version: 1\npatterns: []\n",
            )
        assert [p.relative_to(tree).as_posix() for p in seen] == [RULESET_REL, CATALOGUE_REL]
        assert (tree / RULESET_REL).read_text(encoding="utf-8") == V1

    def test_no_ruleset_configured_is_refused(self, make_manifest, tree):
        with pytest.raises(DefaultsRefused, match="guidelines.ruleset"):
            domain(make_manifest, tree, ruleset=None).merge_defaults(
                defaults_text(make_manifest), catalogue=None,
            )

    def test_a_missing_ruleset_is_refused(self, make_manifest, tmp_path):
        with pytest.raises(DefaultsRefused, match="does not exist"):
            domain(make_manifest, tmp_path).merge_defaults(
                defaults_text(make_manifest), catalogue=None,
            )

    def test_a_ruleset_outside_the_project_is_refused(self, make_manifest, tree):
        with pytest.raises(DefaultsRefused, match="names nothing inside"):
            domain(make_manifest, tree, ruleset="/elsewhere/ruleset.yaml").merge_defaults(
                defaults_text(make_manifest), catalogue=None,
            )

    def test_a_named_catalogue_nobody_supplies_is_refused_with_nothing_written(
        self, make_manifest, tree
    ):
        with pytest.raises(DefaultsRefused, match="does not validate"):
            domain(make_manifest, tree).merge_defaults(defaults_text(make_manifest), catalogue=None)
        assert (tree / RULESET_REL).read_text(encoding="utf-8") == V1

    def test_a_merge_refusal_is_a_defaults_refusal(self, make_manifest, tree):
        with pytest.raises(DefaultsRefused, match="'nowhere'"):
            domain(make_manifest, tree).merge_defaults(
                defaults_text(make_manifest), catalogue=None, layers={"nowhere": ["x/**"]},
            )
