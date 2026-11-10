"""ADR-020 D8 (increment 1): invariant's docs project lives in invariant-docs/.

A new project's docs starter is written at ``invariant-docs/``, a folder only
invariant owns, at the repository root or at the store root under the same
relative name, and ``init`` writes ``docs.needs.srcdir`` and ``paths.docs``
explicitly, so the load-time defaults do not move (D2). The content folders a
documentation repository uses (``docs/``, ``doc/``, ``source/`` and their
``source/`` subfolders) are refused for it at ``init``, never at load:
invariant itself keeps ``docs/``. The sphinx adapter skips the docs project's
RESOLVED source directory, never a fixed ``docs/``, so a content tree at
``docs/conf.py`` is detected as ``sphinx`` (BUG_118, case b).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from test_adapters_sphinx import STARTER_CONF
from test_adr_018_starter import repo, run
from test_docs_onboarding import Scripted

from invariant import adapters
from invariant.adapters.base import Adapter, TestResult
from invariant.adapters.sphinx import SphinxAdapter
from invariant.cli import main as cli_main
from invariant.cli import onboarding
from invariant.core import needs
from invariant.core.manifest import DEFAULTS, MANIFEST_RELPATH, Manifest
from invariant.domains import docs as docs_domain

REPOSITORY = Path(__file__).resolve().parent.parent

#: A documentation repository whose own content is a Sphinx project at docs/.
CONTENT = {
    "docs/conf.py": "project = 'handbook'\nextensions = []\n",
    "docs/index.rst": "Handbook\n========\n",
}


def manifest_text(root: Path) -> dict:
    return yaml.safe_load((root / MANIFEST_RELPATH).read_text(encoding="utf-8"))


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


# -- the starter's folder ---------------------------------------------------------


class TestANewProjectsDocsFolder:
    def test_init_writes_the_starter_at_invariant_docs(self, toy, capsys):
        code, text = run(capsys, "init", "--ecosystem", "python", "--name", "toy")
        assert code == 0, text
        assert (toy / "invariant-docs" / "conf.py").is_file()
        assert (toy / "invariant-docs" / "architecture" / "overview.rst").is_file()
        assert not (toy / "docs" / "conf.py").exists()

    def test_the_two_keys_are_written_explicitly(self, toy, capsys):
        run(capsys, "init", "--ecosystem", "python")
        written = manifest_text(toy)
        assert written["paths"]["docs"] == "invariant-docs/architecture"
        assert written["docs"]["needs"]["srcdir"] == "invariant-docs"
        m = Manifest.locate(toy)
        assert m.get("items.path") == "invariant-docs/backlog.rst"
        assert needs.srcdir_of(m) == (toy / "invariant-docs").resolve()

    def test_the_load_time_defaults_do_not_move(self, tmp_path):
        assert DEFAULTS["paths"]["docs"] == "docs/architecture"
        bare = Manifest.from_raw(
            {"version": 1, "project": {"name": "t", "ecosystems": ["generic"]}},
            root=tmp_path, path=tmp_path / "m.yaml", env={},
        )
        assert bare.get("paths.docs") == "docs/architecture"
        assert bare.get("docs.needs.srcdir") is None

    def test_dry_run_names_the_folder(self, toy, capsys):
        code, text = run(capsys, "--json", "init", "--ecosystem", "python", "--dry-run")
        assert code == 0, text
        data = json.loads(text)
        assert data["docs_venv"]["srcdir"] == "invariant-docs"
        assert any("write invariant-docs/conf.py" in action for action in data["actions"])
        assert not (toy / "invariant-docs").exists()

    def test_the_configuration_report_reads_the_written_location(self, toy, capsys):
        code, text = run(capsys, "--json", "init", "--ecosystem", "python")
        assert code == 0, text
        rows = {row["key"]: row for row in json.loads(text)["configuration"]}
        assert rows["paths.docs"]["value"] == "invariant-docs/architecture"
        assert rows["paths.docs"]["provenance"] == f"repo:{MANIFEST_RELPATH}"

    def test_an_existing_folder_is_reused_and_still_named(self, toy, capsys):
        (toy / "invariant-docs" / "architecture").mkdir(parents=True)
        run(capsys, "init", "--ecosystem", "python")
        assert manifest_text(toy)["paths"]["docs"] == "invariant-docs/architecture"
        assert not (toy / "invariant-docs" / "conf.py").exists()

    def test_a_store_takes_the_same_relative_name(self, toy, capsys, monkeypatch):
        """The offer arises where the folder exists; the store's starter goes
        under the store root by the same relative path."""
        (toy / "invariant-docs" / "architecture").mkdir(parents=True)
        monkeypatch.setattr(
            cli_main, "_init_prompter", lambda args: Scripted("store", "", "", "not_now")
        )
        code, text = run(capsys, "init", "--ecosystem", "python")
        assert code == 0, text
        m = Manifest.locate(toy)
        assert m.store is not None
        assert m.path_for("docs") == m.store.root / "invariant-docs" / "architecture"
        assert (m.store.root / "invariant-docs" / "conf.py").is_file()
        assert needs.srcdir_of(m) == (m.store.root / "invariant-docs").resolve()


class TestAnExistingProjectIsUnchanged:
    def test_a_forced_reinit_keeps_docs(self, toy, capsys):
        """D2: init --force rewrites the manifest in place; the docs location
        an existing project resolves stays where it was."""
        manifest = toy / MANIFEST_RELPATH
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "version: 1\nproject:\n  name: toy\n  ecosystems: [python]\n", encoding="utf-8"
        )
        code, text = run(capsys, "init", "--force", "--ecosystem", "python")
        assert code == 0, text
        assert (toy / "docs" / "conf.py").is_file()
        assert not (toy / "invariant-docs").exists()
        assert "paths" not in manifest_text(toy)

    def test_its_docs_project_at_docs_is_not_an_ecosystem(self, toy, capsys):
        """A project whose docs project is docs/ (D3's default rule) keeps it
        out of detection when re-initialised: the RESOLVED srcdir is skipped."""
        manifest = toy / MANIFEST_RELPATH
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "version: 1\nproject:\n  name: toy\n  ecosystems: [python]\n", encoding="utf-8"
        )
        (toy / "docs" / "architecture").mkdir(parents=True)
        (toy / "docs" / "conf.py").write_text(STARTER_CONF, encoding="utf-8")
        code, text = run(capsys, "--json", "init", "--force")
        assert code == 0, text
        assert json.loads(text)["ecosystems"] == ["python"]


    def test_a_manifest_that_does_not_load_keeps_the_default(self, toy, capsys):
        """`init --force` over a manifest it cannot read writes the default
        location again, as it always has, and never invariant-docs/."""
        manifest = toy / MANIFEST_RELPATH
        manifest.parent.mkdir(parents=True)
        manifest.write_text("version: 1\nproject: [\n", encoding="utf-8")
        code, text = run(capsys, "init", "--force", "--ecosystem", "python")
        assert code == 0, text
        assert (toy / "docs" / "conf.py").is_file()
        assert not (toy / "invariant-docs").exists()


# -- the refused folders ----------------------------------------------------------


class TestTheContentFoldersAreRefused:
    @pytest.mark.parametrize(
        ("docs", "refused"),
        [
            ("docs/architecture", "docs"),
            ("doc/architecture", "doc"),
            ("source/architecture", "source"),
            ("docs/source/architecture", "docs/source"),
            ("doc/source/architecture", "doc/source"),
        ],
    )
    def test_each_is_named(self, docs, refused):
        assert docs_domain.refused_srcdir(docs) == refused

    @pytest.mark.parametrize(
        "docs", ["invariant-docs/architecture", "design/arch", "docs", "docs/a/b"]
    )
    def test_others_are_not(self, docs):
        assert docs_domain.refused_srcdir(docs) is None

    def test_the_offer_asks_again_naming_invariant_docs(self, tmp_path):
        (tmp_path / "invariant-docs" / "architecture").mkdir(parents=True)
        scripted = Scripted("path", "docs/architecture", "design/arch")
        choice = onboarding.offer_location(
            scripted, tmp_path, "invariant-docs/architecture", None, new_project=True
        )
        assert choice == onboarding.DocsChoice("path", "design/arch")
        refusal = [said for said in scripted.said if "docs/architecture" in said]
        assert refusal and "invariant-docs" in refusal[0]

    def test_a_store_path_is_refused_too(self, tmp_path):
        (tmp_path / "invariant-docs" / "architecture").mkdir(parents=True)
        scripted = Scripted("store", "", "source/arch", "notes/arch")
        choice = onboarding.offer_location(
            scripted, tmp_path, "invariant-docs/architecture", None, new_project=True
        )
        assert choice == onboarding.DocsChoice("store", "notes/arch", "")

    def test_an_existing_project_is_not_refused(self, tmp_path):
        """Refused at init for a new project only, never at load (D2)."""
        (tmp_path / "docs" / "architecture").mkdir(parents=True)
        scripted = Scripted("path", "doc/architecture")
        choice = onboarding.offer_location(scripted, tmp_path, "docs/architecture", None)
        assert choice == onboarding.DocsChoice("path", "doc/architecture")


# -- detection skips the resolved source directory (BUG_118, case b) --------------


class TestDetection:
    def test_a_content_tree_at_docs_is_sphinx(self, tmp_path):
        repo(tmp_path / "h", CONTENT)
        assert SphinxAdapter().detect(tmp_path / "h")
        assert adapters.detect(tmp_path / "h") == ["sphinx"]

    def test_the_told_source_directory_is_skipped(self, tmp_path):
        root = repo(tmp_path / "h", {"docs/conf.py": STARTER_CONF})
        assert not SphinxAdapter(docs_srcdir=root / "docs").detect(root)
        assert adapters.detect(root, docs_srcdir=root / "docs") == ["generic"]

    def test_a_relative_spelling_is_the_same_directory(self, tmp_path, monkeypatch):
        root = repo(tmp_path / "h", {"docs/conf.py": STARTER_CONF})
        monkeypatch.chdir(root)
        assert not SphinxAdapter(docs_srcdir=Path("docs")).detect(Path("."))

    def test_init_detects_it_and_writes_its_docs_project_beside_it(self, handbook, capsys):
        code, text = run(capsys, "--json", "init", "--name", "handbook")
        assert code == 0, text
        assert json.loads(text)["ecosystems"] == ["sphinx"]
        m = Manifest.locate(handbook)
        assert m.get("vars.srcdir") == "docs"
        assert (handbook / "invariant-docs" / "conf.py").is_file()
        assert (handbook / "docs" / "conf.py").read_text(encoding="utf-8") == CONTENT["docs/conf.py"]

    def test_init_settings_skip_the_told_directory(self, tmp_path):
        root = repo(tmp_path / "h", {"docs/conf.py": STARTER_CONF, "source/conf.py": ""})
        settings, notes = SphinxAdapter(docs_srcdir=root / "docs").init_settings(root)
        assert settings["vars"] == {"srcdir": "source"} and notes == []

    def test_annotate_reads_only_the_content(self, tmp_path):
        root = repo(
            tmp_path / "h",
            {
                "docs/conf.py": "suppress_warnings = ['a']\n",
                "source/conf.py": "nitpick_ignore = ['b', 'c']\n",
            },
        )
        result = TestResult(passed=True, exit_code=0, total=1, failed=0)
        told = SphinxAdapter(docs_srcdir=root / "docs").annotate(result, root)
        assert "nitpick_ignore (2 patterns)" in told.note
        assert "suppress_warnings" not in told.note

    def test_every_adapter_takes_the_value(self, tmp_path):
        assert Adapter().docs_srcdir is None
        assert adapters.get_adapter("python", docs_srcdir=tmp_path).docs_srcdir == tmp_path


class TestTheDefaultRuleIsTheEngines:
    """D3's default rule now lives with the key it defaults (core/needs)."""

    def test_the_nearest_conf_py_at_or_above_paths_docs(self, tmp_path):
        (tmp_path / "docs" / "architecture").mkdir(parents=True)
        (tmp_path / "docs" / "conf.py").write_text("", encoding="utf-8")
        assert needs.default_srcdir(tmp_path, "docs/architecture") == tmp_path / "docs"
        assert needs.default_srcdir(tmp_path, "elsewhere/arch") is None

    def test_never_the_root(self, tmp_path):
        (tmp_path / "conf.py").write_text("", encoding="utf-8")
        assert needs.default_srcdir(tmp_path, "docs/architecture") is None
        assert needs.default_srcdir(tmp_path, ".") is None


# -- this repository --------------------------------------------------------------


class TestThisRepository:
    def test_it_resolves_docs_as_before(self):
        m = Manifest.locate(REPOSITORY)
        assert m.get("paths.docs") == "docs/architecture"
        assert m.get("docs.needs.srcdir") is None
        assert needs.srcdir_of(m) == (REPOSITORY / "docs").resolve()

    def test_its_docs_project_is_not_an_ecosystem(self):
        m = Manifest.locate(REPOSITORY)
        assert "sphinx" not in adapters.detect(REPOSITORY, docs_srcdir=needs.srcdir_of(m))
