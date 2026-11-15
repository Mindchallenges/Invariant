"""ADR-020 D10: a new project's guidelines ruleset follows its docs project.

The user ruled on 2026-11-11 (ADR-020 ruling 9) that a NEW project's ruleset
lives at ``<docs.needs.srcdir>/guidelines/ruleset.yaml``, so a fresh ``init``
writes ``invariant-docs/guidelines/ruleset.yaml`` and no ``docs/`` folder at
all. This amends ADR-012 ruling 1 for new projects only. Every reader reads
the location from ``guidelines.ruleset``, which ``init`` writes explicitly,
and whose load-time default stays null (no guidelines). Existing projects,
invariant among them, keep ``docs/guidelines/ruleset.yaml`` byte for byte.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from test_adr_018_starter import repo, run
from test_docs_onboarding import Scripted

from invariant.cli import main as cli_main
from invariant.core import guidelines as guidelines_reader
from invariant.core import needs
from invariant.core.manifest import DEFAULTS, MANIFEST_RELPATH, Manifest
from invariant.core.rules import RuleContext, evaluate, ruleset_pin

REPOSITORY = Path(__file__).resolve().parent.parent

#: Where a fresh init puts the ruleset: inside its docs project.
NEW_RULESET = "invariant-docs/guidelines/ruleset.yaml"

#: A documentation repository whose own content is a Sphinx project at docs/.
CONTENT = {
    "docs/conf.py": "project = 'handbook'\nextensions = []\n",
    "docs/index.rst": "Handbook\n========\n",
    "docs/guide/start.rst": "Start\n=====\n",
}


def manifest_text(root: Path) -> dict:
    return yaml.safe_load((root / MANIFEST_RELPATH).read_text(encoding="utf-8"))


def files_under(folder: Path) -> dict[str, bytes]:
    return {
        p.relative_to(folder).as_posix(): p.read_bytes()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def toy(tmp_path, monkeypatch):
    root = repo(tmp_path / "toy", {"pyproject.toml": '[project]\nname = "toy"\n'})
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def handbook(tmp_path, monkeypatch):
    root = repo(tmp_path / "handbook", CONTENT)
    monkeypatch.chdir(root)
    return root


# -- the rule ---------------------------------------------------------------------


class TestTheLocationRule:
    def test_it_follows_the_docs_project(self):
        assert guidelines_reader.ruleset_location("invariant-docs") == NEW_RULESET
        assert guidelines_reader.ruleset_location("design") == "design/guidelines/ruleset.yaml"

    def test_a_docs_project_at_docs_gives_adr_012s_path(self):
        """The rule is ADR-012 ruling 1's path wherever the docs project is
        docs/, which is why an existing project needs nothing moved."""
        assert guidelines_reader.ruleset_location("docs") == "docs/guidelines/ruleset.yaml"

    def test_other_guidelines_files_sit_beside_it(self):
        assert (
            guidelines_reader.ruleset_location("invariant-docs", "baseline.txt")
            == "invariant-docs/guidelines/baseline.txt"
        )

    def test_the_load_time_default_stays_null(self):
        """No reader derives a location: null still means no guidelines."""
        assert DEFAULTS["guidelines"]["ruleset"] is None


# -- a fresh init -----------------------------------------------------------------


class TestAFreshInit:
    def test_it_writes_no_docs_folder_at_all(self, toy, capsys):
        code, text = run(capsys, "init", "--host-install", "--ecosystem", "python", "--name", "toy")
        assert code == 0, text
        assert not (toy / "docs").exists()
        assert (toy / NEW_RULESET).is_file()

    def test_the_location_is_written_explicitly(self, toy, capsys):
        run(capsys, "init", "--host-install", "--ecosystem", "python")
        assert manifest_text(toy)["guidelines"]["ruleset"] == NEW_RULESET
        assert Manifest.locate(toy).get("guidelines.ruleset") == NEW_RULESET

    def test_every_reader_resolves_it(self, toy, capsys):
        """The gate, the pin and the rendered pages read the one key."""
        run(capsys, "init", "--host-install", "--ecosystem", "python")
        ctx = RuleContext(manifest=Manifest.locate(toy), cwd=toy)
        (result,) = evaluate(["guidelines.ruleset_valid"], ctx)
        assert result.verdict.ok, result.verdict.message
        pin = ruleset_pin(ctx)
        assert pin["path"] == NEW_RULESET and pin["error"] is None
        code, text = run(capsys, "guidelines", "show")
        assert code == 0, text
        assert NEW_RULESET in text
        page = (toy / "invariant-docs" / "rules.rst").read_text(encoding="utf-8")
        assert NEW_RULESET in page

    def test_doctor_reads_it(self, toy, capsys):
        run(capsys, "init", "--host-install", "--ecosystem", "python")
        _code, text = run(capsys, "--json", "doctor")
        rows = {row["check"]: row for row in json.loads(text)["checks"]}
        assert rows["guidelines ruleset"]["ok"], rows["guidelines ruleset"]

    def test_dry_run_names_it(self, toy, capsys):
        code, text = run(capsys, "--json", "init", "--host-install", "--ecosystem", "python", "--dry-run")
        assert code == 0, text
        actions = json.loads(text)["actions"]
        assert any(f"write {NEW_RULESET}" in action for action in actions)
        assert not any("docs/guidelines" in action.replace(NEW_RULESET, "") for action in actions)

    def test_a_chosen_docs_folder_carries_the_ruleset(self, toy, capsys, monkeypatch):
        """The ruleset follows whatever source directory the docs step chose."""
        (toy / "invariant-docs" / "architecture").mkdir(parents=True)
        monkeypatch.setattr(
            cli_main, "_init_prompter", lambda args: Scripted("path", "design/arch", "not_now")
        )
        code, text = run(capsys, "init", "--host-install", "--ecosystem", "python")
        assert code == 0, text
        assert (toy / "design" / "guidelines" / "ruleset.yaml").is_file()
        assert manifest_text(toy)["guidelines"]["ruleset"] == "design/guidelines/ruleset.yaml"
        assert not (toy / "docs").exists()

    def test_a_ruleset_already_there_is_never_overwritten(self, toy, capsys):
        target = toy / NEW_RULESET
        target.parent.mkdir(parents=True)
        target.write_text("ours\n", encoding="utf-8")
        code, text = run(capsys, "init", "--host-install", "--ecosystem", "python")
        assert code == 0, text
        assert target.read_text(encoding="utf-8") == "ours\n"
        assert f"{NEW_RULESET} exists and guidelines.ruleset is not set" in text
        assert "guidelines" not in manifest_text(toy)


class TestADocumentationRepository:
    def test_its_docs_tree_is_left_untouched(self, handbook, capsys):
        before = files_under(handbook / "docs")
        code, text = run(capsys, "--json", "init", "--host-install", "--name", "handbook")
        assert code == 0, text
        assert files_under(handbook / "docs") == before
        assert not (handbook / "docs" / "guidelines").exists()

    def test_its_ruleset_sits_beside_the_content(self, handbook, capsys):
        run(capsys, "init", "--host-install", "--name", "handbook")
        assert (handbook / NEW_RULESET).is_file()
        assert Manifest.locate(handbook).get("guidelines.ruleset") == NEW_RULESET


# -- existing projects ------------------------------------------------------------


class TestAnExistingProject:
    def test_a_forced_reinit_with_docs_writes_adr_012s_path(self, toy, capsys):
        """An existing manifest's docs project is docs/, so the starter's
        ruleset is docs/guidelines/ruleset.yaml, as it always was."""
        manifest = toy / MANIFEST_RELPATH
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "version: 1\nproject:\n  name: toy\n  ecosystems: [python]\n", encoding="utf-8"
        )
        code, text = run(capsys, "init", "--host-install", "--force", "--ecosystem", "python")
        assert code == 0, text
        assert (toy / "docs" / "guidelines" / "ruleset.yaml").is_file()
        assert manifest_text(toy)["guidelines"]["ruleset"] == "docs/guidelines/ruleset.yaml"
        assert not (toy / "invariant-docs").exists()

    def test_a_configured_ruleset_is_kept_where_it_is(self, toy, capsys):
        run(capsys, "init", "--host-install", "--ecosystem", "python")
        data = manifest_text(toy)
        data["guidelines"] = {"ruleset": "docs/guidelines/ruleset.yaml"}
        (toy / MANIFEST_RELPATH).write_text(yaml.safe_dump(data), encoding="utf-8")
        code, text = run(capsys, "init", "--host-install", "--force", "--ecosystem", "python")
        assert code == 0, text
        assert manifest_text(toy)["guidelines"]["ruleset"] == "docs/guidelines/ruleset.yaml"


# -- the fixes the gates offer ----------------------------------------------------


class TestTheFixesNameTheDocsProject:
    def test_an_outside_ruleset_is_offered_the_docs_projects_path(self, toy, capsys):
        run(capsys, "init", "--host-install", "--ecosystem", "python")
        data = manifest_text(toy)
        data["guidelines"] = {"ruleset": "../elsewhere/ruleset.yaml"}
        (toy / MANIFEST_RELPATH).write_text(yaml.safe_dump(data), encoding="utf-8")
        ctx = RuleContext(manifest=Manifest.locate(toy), cwd=toy)
        (result,) = evaluate(["guidelines.ruleset_valid"], ctx)
        assert not result.verdict.ok
        assert any(NEW_RULESET in fix for fix in result.verdict.fixes)
        assert not any("docs/guidelines" in fix.replace(NEW_RULESET, "") for fix in result.verdict.fixes)

    def test_an_outside_baseline_is_offered_one_beside_it(self, toy, capsys):
        run(capsys, "init", "--host-install", "--ecosystem", "python")
        data = manifest_text(toy)
        data["guidelines"]["baseline"] = "../elsewhere/baseline.txt"
        (toy / MANIFEST_RELPATH).write_text(yaml.safe_dump(data), encoding="utf-8")
        ctx = RuleContext(manifest=Manifest.locate(toy), cwd=toy)
        (result,) = evaluate(["guidelines.baseline_only_shrinks"], ctx)
        assert any(
            "invariant-docs/guidelines/baseline.txt" in fix for fix in result.verdict.fixes
        ), result.verdict

    def test_without_a_docs_project_the_folder_above_paths_docs(self, tmp_path):
        """No resolved source directory: the starter's own rule, the folder
        above paths.docs, names the place."""
        m = Manifest.from_raw(
            {"version": 1, "project": {"name": "t", "ecosystems": ["generic"]}},
            root=tmp_path, path=tmp_path / "m.yaml", env={},
        )
        assert needs.docs_folder(m) == "docs"

    @pytest.mark.parametrize("docs", ["architecture", "/elsewhere/architecture"])
    def test_with_nothing_above_paths_docs_the_default_folder(self, tmp_path, docs):
        """A paths.docs at the root, or outside the project (a store's), has
        no folder above it here: the default paths.docs' folder names it."""
        m = Manifest.from_raw(
            {
                "version": 1,
                "project": {"name": "t", "ecosystems": ["generic"]},
                "paths": {"docs": docs},
            },
            root=tmp_path, path=tmp_path / "m.yaml", env={},
        )
        assert needs.docs_folder(m) == "docs"

    def test_a_configured_srcdir_wins(self, tmp_path):
        m = Manifest.from_raw(
            {
                "version": 1,
                "project": {"name": "t", "ecosystems": ["generic"]},
                "paths": {"docs": "notes/arch"},
                "docs": {"needs": {"srcdir": "handbook"}},
            },
            root=tmp_path, path=tmp_path / "m.yaml", env={},
        )
        assert needs.docs_folder(m) == "handbook"


# -- this repository --------------------------------------------------------------


class TestThisRepository:
    def test_it_resolves_its_ruleset_unchanged(self):
        m = Manifest.locate(REPOSITORY)
        assert m.get("guidelines.ruleset") == "docs/guidelines/ruleset.yaml"
        assert needs.docs_folder(m) == "docs"
        assert guidelines_reader.ruleset_location(needs.docs_folder(m)) == (
            "docs/guidelines/ruleset.yaml"
        )

    def test_its_pin_is_unchanged(self):
        m = Manifest.locate(REPOSITORY)
        pin = ruleset_pin(RuleContext(manifest=m, cwd=REPOSITORY))
        assert pin["path"] == "docs/guidelines/ruleset.yaml"
        assert pin["error"] is None
        assert pin["digest"] == "02ed45e4db9d"

    def test_its_docs_project_is_still_docs(self):
        m = Manifest.locate(REPOSITORY)
        assert needs.srcdir_of(m) == (REPOSITORY / "docs").resolve()
