"""This repository's own guidelines ruleset (IDEA_031 A3 increment 9).

ADR-012 D4 names this repository as the first user of the `boundary` kind:
its layering, checked until now by hand-written import scans in the suite, is
written as two ruleset entries and held by `guidelines.mechanical_clean` at
the landing. The ruling took two rules that together cover four of the five
scans:

- `layers.only-the-cli-imports-the-oracle`: nothing under `core/`,
  `domains/`, `adapters/` or `hosts/` imports the oracle (the scans in
  `test_oracle_index.py` and `test_oracle_attention.py`);
- `layers.oracle-never-imports-hosts`: nothing under `oracle/` imports a
  host (the scans in `test_oracle_scan.py` and `test_oracle_watcher.py`).

The fifth, "only the dashboard module imports Textual", is about a
third-party package, which is outside the kind (DN-A2).

The ruleset's first `always` rule came later (2026-11-08):
`conduct.interview-when-input-is-needed`, a warn-severity judgement rule over
the agent and instruction files, carried by the `guidelines/index` block in
`CLAUDE.md` (ADR-012, the A5 ruling D11 revisited).

ADR-032 increment 1b (DEC_ADR_032_D12, DEC_ADR_032_I1B) merged the default
set in: version 2, the four-layer map by package and the three direction
rules derived from it, the five SOLID rules, the pattern rule,
`docs.design-before-code` and the catalogue. The inward imports the map found
are baselined, so the tree passes and a new one, such as a `core` module
importing `cli`, is a finding.
"""

from __future__ import annotations

import copy as deep
from pathlib import Path

import pytest
import yaml
from test_guidelines_domain import write

from invariant.core import guidelines
from invariant.core.manifest import Manifest
from invariant.core.rules import RuleContext, load_ruleset
from invariant.core.rules import evaluate as evaluate_rules
from invariant.domains import docs as docs_domain

REPO = Path(__file__).resolve().parent.parent
RULESET_REL = "docs/guidelines/ruleset.yaml"
LAYERS = ("core", "domains", "adapters", "hosts")

ORACLE_RULE = "layers.only-the-cli-imports-the-oracle"
HOSTS_RULE = "layers.oracle-never-imports-hosts"
CONDUCT_RULE = "conduct.interview-when-input-is-needed"
#: The docs mandate's two judgement rules (ADR-018 D8, D9), which the
#: migration wrote into this ruleset (increment 7).
DOCS_RULES = ("docs.items-carry-no-design", "docs.decisions-are-declared")
#: The default set's rules the merge added (ADR-032 increment 1b).
SOLID = (
    "solid.single-responsibility", "solid.open-closed", "solid.liskov-substitution",
    "solid.interface-segregation", "solid.dependency-inversion",
)
COPIED = (*SOLID, "patterns.named-or-declined", "docs.design-before-code")
#: The direction rules the layer map derives (ADR-032 D2).
ENTITIES_RULE = "layers.entities-depends-inward"
USE_CASES_RULE = "layers.use-cases-depends-inward"
DERIVED = (ENTITIES_RULE, USE_CASES_RULE, "layers.interface-adapters-depends-inward")
CATALOGUE_REL = "docs/guidelines/patterns.yaml"
BASELINE_REL = "docs/guidelines/baseline.txt"
SHIPPED_CATALOGUE = (
    Path(guidelines.__file__).resolve().parent.parent / "data" / "scaffold" / "guidelines"
    / "patterns.yaml"
)


def repo_context(files=None) -> RuleContext:
    session = None if files is None else {"files": list(files), "base": ""}
    return RuleContext(manifest=Manifest.locate(REPO), cwd=REPO, session=session)


def verdict_of(rule_id: str, ctx: RuleContext):
    (result,) = evaluate_rules([rule_id], ctx)
    return result.verdict


def every_source_file() -> list[str]:
    package = REPO / "src" / "invariant"
    return sorted(path.relative_to(REPO).as_posix() for path in package.rglob("*.py"))


class TestThisRepositorysRuleset:
    def test_the_manifest_names_it(self):
        assert Manifest.locate(REPO).get("guidelines.ruleset") == RULESET_REL

    def test_it_passes_ruleset_valid(self):
        verdict = verdict_of("guidelines.ruleset_valid", repo_context())
        assert verdict.ok and not verdict.skipped, verdict.message

    def test_it_holds_the_layering_conduct_and_docs_rules(self):
        ruleset, failure = load_ruleset(repo_context())
        assert failure is None
        ids = [rule.id for rule in ruleset.rules]
        assert ids == [ORACLE_RULE, HOSTS_RULE, CONDUCT_RULE, *DOCS_RULES, *COPIED, *DERIVED]

    def test_the_layering_rules_are_blocking_import_boundaries(self):
        ruleset, _ = load_ruleset(repo_context())
        layering = [rule for rule in ruleset.rules if rule.section == "layers" and not rule.layer]
        assert [rule.id for rule in layering] == [ORACLE_RULE, HOSTS_RULE]
        for rule in layering:
            assert rule.severity == "blocker"
            assert (rule.check.kind, rule.check.engine) == ("boundary", "imports")
            assert not rule.check.only

    def test_the_oracle_rule_covers_every_layer_below_the_cli(self):
        ruleset, _ = load_ruleset(repo_context())
        rule = ruleset.by_id(ORACLE_RULE)
        assert sorted(rule.applies_to) == sorted(
            f"src/invariant/{layer}/*.py" for layer in LAYERS
        )
        assert list(rule.check.forbid) == ["src/invariant/oracle/*"]

    def test_the_hosts_rule_covers_the_oracle(self):
        ruleset, _ = load_ruleset(repo_context())
        rule = ruleset.by_id(HOSTS_RULE)
        assert list(rule.applies_to) == ["src/invariant/oracle/*.py"]
        assert list(rule.check.forbid) == ["src/invariant/hosts/*"]

    def test_the_conduct_rule_is_an_always_warn_judgement_over_instruction_files(self):
        ruleset, _ = load_ruleset(repo_context())
        rule = ruleset.by_id(CONDUCT_RULE)
        assert (rule.section, rule.severity, rule.phases) == ("conduct", "warn", ("always",))
        check = rule.check
        assert (check.kind, check.evidence, check.verdict_scope) == ("judgement", "citation", "diff")
        assert [na.id for na in rule.check.not_applicable_when] == ["asking-unchanged"]
        assert "CLAUDE.md" in rule.applies_to
        assert ".claude/agents/*.md" in rule.applies_to
        assert not any(glob.endswith(".py") for glob in rule.applies_to)

    def test_the_instruction_file_carries_the_always_rule(self):
        """An always rule with no `guidelines/index` block is the A5.4 gap."""
        verdict = verdict_of("guidelines.generated_current", repo_context())
        assert verdict.ok and not verdict.skipped, verdict.message
        text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
        assert "<!-- invariant:begin guidelines/index" in text
        assert f"`{CONDUCT_RULE}` (always)" in text

    def test_no_hand_written_boundary_glob_uses_a_double_star(self):
        """`**/` needs a directory, so it would miss each layer's top-level
        modules. The layer map's `<package>/**` globs are the derived rules',
        which reach top-level modules (`TestASeededImportIsFound`)."""
        ruleset, _ = load_ruleset(repo_context())
        for rule in ruleset.rules:
            if rule.check.kind != "boundary" or rule.layer:
                continue
            assert not any("**" in glob for glob in (*rule.applies_to, *rule.check.forbid))

    def test_the_baseline_holds_only_derived_layer_findings(self):
        """The inward imports the map found when it was adopted (DEC_ADR_032_I1B);
        the hand-written bans had none, and still have none."""
        assert Manifest.locate(REPO).get("guidelines.baseline") == BASELINE_REL
        entries = guidelines.parse_baseline((REPO / BASELINE_REL).read_text(encoding="utf-8"))
        assert entries
        assert {entry.rule for entry in entries} <= set(DERIVED)


class TestTheDefaultSet:
    """ADR-032 increment 1b: the default set, merged into this ruleset."""

    def test_it_is_version_2_and_names_the_catalogue(self):
        ruleset, _ = load_ruleset(repo_context())
        raw = yaml.safe_load((REPO / RULESET_REL).read_text(encoding="utf-8"))
        assert (raw["version"], raw["catalogue"]) == (2, "patterns.yaml")
        assert ruleset.catalogue.path == CATALOGUE_REL
        assert len(ruleset.catalogue.patterns) == 32
        assert (REPO / CATALOGUE_REL).read_bytes() == SHIPPED_CATALOGUE.read_bytes()

    def test_the_solid_rules_are_user_owned_blockers(self):
        ruleset, _ = load_ruleset(repo_context())
        for rule_id in SOLID:
            rule = ruleset.by_id(rule_id)
            assert (rule.section, rule.severity, rule.owner) == ("solid", "blocker", "user")
        for rule_id in ("patterns.named-or-declined", "docs.design-before-code"):
            assert (ruleset.by_id(rule_id).severity, ruleset.by_id(rule_id).owner) == (
                "warn", "user",
            )

    def test_each_copy_is_the_shipped_rule_with_its_origin(self, make_manifest):
        manifest = make_manifest({"version": 1, "project": {"name": "d", "ecosystems": ["python"]}})
        shipped = {
            rule["id"]: rule
            for rule in yaml.safe_load(docs_domain.default_ruleset(manifest, "x.rst"))["rules"]
        }
        ours = {
            rule["id"]: rule
            for rule in yaml.safe_load((REPO / RULESET_REL).read_text(encoding="utf-8"))["rules"]
        }
        for rule_id in COPIED:
            copied = deep.deepcopy(ours[rule_id])
            origin = copied.pop("origin")
            assert origin == {
                "set": "defaults", "version": 1,
                "digest": guidelines.origin_digest(ours[rule_id]),
            }
            shipped[rule_id].pop("origin")
            assert copied == shipped[rule_id], rule_id

    def test_the_layer_map_is_by_package(self):
        ruleset, _ = load_ruleset(repo_context())
        assert [(layer.title, layer.paths) for layer in ruleset.layers.layers] == [
            ("Entities", ("src/invariant/core/**",)),
            ("Use Cases", ("src/invariant/domains/**",)),
            ("Interface Adapters", ("src/invariant/adapters/**", "src/invariant/hosts/**")),
            ("Frameworks & Drivers", ("src/invariant/oracle/**", "src/invariant/cli/**")),
        ]
        assert ruleset.layers.owner == "user"

    def test_each_inner_layer_forbids_every_outer_one(self):
        ruleset, _ = load_ruleset(repo_context())
        assert {rule.id: rule.check.forbid for rule in ruleset.rules if rule.layer} == {
            ENTITIES_RULE: (
                "src/invariant/domains/**", "src/invariant/adapters/**",
                "src/invariant/hosts/**", "src/invariant/oracle/**", "src/invariant/cli/**",
            ),
            USE_CASES_RULE: (
                "src/invariant/adapters/**", "src/invariant/hosts/**",
                "src/invariant/oracle/**", "src/invariant/cli/**",
            ),
            "layers.interface-adapters-depends-inward": (
                "src/invariant/oracle/**", "src/invariant/cli/**",
            ),
        }

    def test_the_review_phase_prints_the_five_solid_rules_by_name(self):
        ruleset, _ = load_ruleset(repo_context())
        chosen = guidelines.select(ruleset, ("review",), ())
        text = guidelines.render_selected(
            ruleset, chosen, source=RULESET_REL, phases=guidelines.phases_for(("review",)),
            files=(),
        )
        for name in ("Single responsibility", "Open/closed", "Liskov substitution",
                     "Interface segregation", "Dependency inversion"):
            assert name in text
        assert all(f"### {rule_id}:" in text for rule_id in SOLID)


class TestTheTreeIsClean:
    def test_every_source_module_has_no_finding(self):
        files = every_source_file()
        verdict = verdict_of("guidelines.mechanical_clean", repo_context(files))
        assert verdict.ok and not verdict.skipped, verdict.message
        assert "5 mechanical guideline(s)" in verdict.message


# -- a fixture copy with a seeded import --------------------------------------


@pytest.fixture
def copy(tmp_path):
    """The ruleset and the manifest's ecosystems, over a small src layout."""
    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, (REPO / RULESET_REL).read_text(encoding="utf-8"))
    write(root, CATALOGUE_REL, (REPO / CATALOGUE_REL).read_text(encoding="utf-8"))
    write(root, "src/invariant/__init__.py", "")
    for package in (*LAYERS, "oracle", "cli", "core/nested"):
        write(root, f"src/invariant/{package}/__init__.py", "")
    write(root, "src/invariant/oracle/scan.py", "X = 1\n")
    write(root, "src/invariant/hosts/claude_code.py", "Y = 2\n")
    return root


def copy_context(make_manifest, root, files) -> RuleContext:
    body = {
        "version": 1,
        "project": {"name": "copy", "ecosystems": ["python"]},
        "guidelines": {"ruleset": RULESET_REL},
    }
    return RuleContext(
        manifest=make_manifest(body, root), cwd=root, session={"files": files, "base": ""}
    )


class TestASeededImportIsFound:
    @pytest.mark.parametrize("layer", LAYERS)
    def test_a_layer_below_the_cli_importing_the_oracle(self, make_manifest, copy, layer):
        rel = f"src/invariant/{layer}/m.py"
        write(copy, rel, "x = 1\nfrom invariant.oracle import scan\n")
        verdict = verdict_of("guidelines.mechanical_clean", copy_context(make_manifest, copy, [rel]))
        assert not verdict.ok
        assert f"{ORACLE_RULE} {rel}:2 -> src/invariant/oracle/scan.py" in verdict.message

    def test_a_nested_core_module_is_covered(self, make_manifest, copy):
        rel = "src/invariant/core/nested/deep.py"
        write(copy, rel, "import invariant.oracle.scan\n")
        verdict = verdict_of("guidelines.mechanical_clean", copy_context(make_manifest, copy, [rel]))
        assert not verdict.ok
        assert f"{ORACLE_RULE} {rel}:1" in verdict.message

    def test_the_oracle_importing_a_host(self, make_manifest, copy):
        rel = "src/invariant/oracle/watcher.py"
        write(copy, rel, "from invariant.hosts import claude_code\n")
        verdict = verdict_of("guidelines.mechanical_clean", copy_context(make_manifest, copy, [rel]))
        assert not verdict.ok
        assert f"{HOSTS_RULE} {rel}:1 -> src/invariant/hosts/claude_code.py" in verdict.message

    def test_the_cli_importing_the_oracle_is_no_finding(self, make_manifest, copy):
        rel = "src/invariant/cli/main.py"
        write(copy, rel, "from invariant.oracle import scan\n")
        verdict = verdict_of("guidelines.mechanical_clean", copy_context(make_manifest, copy, [rel]))
        assert verdict.ok and verdict.findings == ()

    def test_a_core_module_importing_the_cli_is_a_new_finding(self, make_manifest, copy):
        """The increment's deliverable: Entities depends inward."""
        write(copy, "src/invariant/cli/main.py", "X = 1\n")
        rel = "src/invariant/core/m.py"
        write(copy, rel, "from invariant.cli import main\n")
        verdict = verdict_of("guidelines.mechanical_clean", copy_context(make_manifest, copy, [rel]))
        assert not verdict.ok
        assert f"{ENTITIES_RULE} {rel}:1 -> src/invariant/cli/main.py" in verdict.message

    def test_a_domain_importing_an_adapter_is_a_new_finding(self, make_manifest, copy):
        write(copy, "src/invariant/adapters/python.py", "X = 1\n")
        rel = "src/invariant/domains/m.py"
        write(copy, rel, "from invariant.adapters import python\n")
        verdict = verdict_of("guidelines.mechanical_clean", copy_context(make_manifest, copy, [rel]))
        assert not verdict.ok
        assert f"{USE_CASES_RULE} {rel}:1 -> src/invariant/adapters/python.py" in verdict.message

    def test_a_core_module_importing_core_is_no_finding(self, make_manifest, copy):
        write(copy, "src/invariant/core/other.py", "X = 1\n")
        rel = "src/invariant/core/m.py"
        write(copy, rel, "from invariant.core import other\n")
        verdict = verdict_of("guidelines.mechanical_clean", copy_context(make_manifest, copy, [rel]))
        assert verdict.ok and verdict.findings == (), verdict.message
