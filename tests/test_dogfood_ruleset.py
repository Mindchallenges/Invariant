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
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_guidelines_domain import write

from invariant.core.manifest import Manifest
from invariant.core.rules import RuleContext, load_ruleset
from invariant.core.rules import evaluate as evaluate_rules

REPO = Path(__file__).resolve().parent.parent
RULESET_REL = "docs/guidelines/ruleset.yaml"
LAYERS = ("core", "domains", "adapters", "hosts")

ORACLE_RULE = "layers.only-the-cli-imports-the-oracle"
HOSTS_RULE = "layers.oracle-never-imports-hosts"


def repo_context(files=None) -> RuleContext:
    session = None if files is None else {"files": list(files), "base": ""}
    return RuleContext(manifest=Manifest.load(REPO), cwd=REPO, session=session)


def verdict_of(rule_id: str, ctx: RuleContext):
    (result,) = evaluate_rules([rule_id], ctx)
    return result.verdict


def every_source_file() -> list[str]:
    package = REPO / "src" / "invariant"
    return sorted(path.relative_to(REPO).as_posix() for path in package.rglob("*.py"))


class TestThisRepositorysRuleset:
    def test_the_manifest_names_it(self):
        assert Manifest.load(REPO).get("guidelines.ruleset") == RULESET_REL

    def test_it_passes_ruleset_valid(self):
        verdict = verdict_of("guidelines.ruleset_valid", repo_context())
        assert verdict.ok and not verdict.skipped, verdict.message

    def test_it_holds_exactly_the_two_layering_rules(self):
        ruleset, failure = load_ruleset(repo_context())
        assert failure is None
        assert [rule.id for rule in ruleset.rules] == [ORACLE_RULE, HOSTS_RULE]
        for rule in ruleset.rules:
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

    def test_no_glob_uses_a_double_star(self):
        """`**/` needs a directory, so it would miss each layer's top-level modules."""
        ruleset, _ = load_ruleset(repo_context())
        for rule in ruleset.rules:
            assert not any("**" in glob for glob in (*rule.applies_to, *rule.check.forbid))

    def test_no_baseline_is_configured(self):
        """No finding exists to record, so every finding is new (DN-1)."""
        assert Manifest.load(REPO).get("guidelines.baseline") is None


class TestTheTreeIsClean:
    def test_every_source_module_has_no_finding(self):
        files = every_source_file()
        verdict = verdict_of("guidelines.mechanical_clean", repo_context(files))
        assert verdict.ok and not verdict.skipped, verdict.message
        assert verdict.findings == ()
        assert "2 mechanical guideline(s)" in verdict.message


# -- a fixture copy with a seeded import --------------------------------------


@pytest.fixture
def copy(tmp_path):
    """The ruleset and the manifest's ecosystems, over a small src layout."""
    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, (REPO / RULESET_REL).read_text(encoding="utf-8"))
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
