"""ADR-018 D11 (increment 4): `init` writes the Sphinx starter.

Where `paths.docs` resolves to nothing, `init`, interactive or not, writes a
sphinx-needs docs project in the directory above it (the source directory,
`invariant-docs/` for a new project's `invariant-docs/architecture`, ADR-020
D8): `conf.py`, `index.rst`, a fully
pinned `requirements.txt`, a `.gitignore` holding `.venv`, the architecture
overview with one element need per source area and the three sections the
assessment requires, the requirements, decisions and ui pages, the two
generated guidelines pages, and `backlog.rst` as the items store. The manifest
says `docs.needs.state: enforced`. Only an interactive `init` creates the
docs environment; `--dry-run` and a run with no terminal name the same files,
create nothing, and name `invariant docs venv`.

The assessment answers `areas` from element needs where the docs project has
sphinx-needs; Markdown is adopted by copy only, into a migrating project.

The real build is proven behind ``INVARIANT_TEST_SPHINX_BUILD``: the starter's
own pinned environment is created by `init` with uv, and `invariant docs
build` runs it strictly.
"""

from __future__ import annotations

import ast
import json
import os
import re
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from test_docs_onboarding import Scripted

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.docformat import RstReader
from invariant.core.manifest import DATA_DIR, Manifest
from invariant.domains import docs as docs_domain

#: Every file the default starter writes, relative to the project root: the
#: guidelines ruleset with the mandate's two judgement rules since increment 5,
#: at ADR-012 D3's default path, and the docs project in invariant-docs/
#: (ADR-020 D8).
STARTER = sorted(
    [
        "invariant-docs/.gitignore",
        "invariant-docs/architecture/overview.rst",
        "invariant-docs/backlog.rst",
        "invariant-docs/checklist.rst",
        "invariant-docs/conf.py",
        "invariant-docs/decisions/index.rst",
        "docs/guidelines/ruleset.yaml",
        "invariant-docs/index.rst",
        "invariant-docs/requirements.rst",
        "invariant-docs/requirements.txt",
        "invariant-docs/rules.rst",
        "invariant-docs/ui.rst",
    ]
)

#: The link options the starter declares: dang's set, plus D7's two.
LINKS = {"satisfies", "implements", "uses", "decided_by", "affects", "part_of", "amends", "supersedes"}


def git(root: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", *argv], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


@pytest.fixture
def toy_python(tmp_path, monkeypatch):
    """A Python package with two subpackages, alpha and beta, and no docs."""
    root = repo(
        tmp_path / "toy",
        {
            "pyproject.toml": '[project]\nname = "toy"\nversion = "0"\n',
            "pkg/__init__.py": "",
            "pkg/alpha/__init__.py": "x = 1\n",
            "pkg/beta/__init__.py": "y = 2\n",
        },
    )
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def toy_flutter(tmp_path, monkeypatch):
    """A Flutter app tree with Dart sources under lib/ and no docs."""
    root = repo(
        tmp_path / "app",
        {
            "pubspec.yaml": "name: app\ndependencies:\n  flutter:\n    sdk: flutter\n",
            "lib/main.dart": "void main() {}\n",
            "lib/screens/home.dart": "class Home {}\n",
            "test/widget_test.dart": "void main() {}\n",
        },
    )
    monkeypatch.chdir(root)
    return root


def run(capsys, *argv: str) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


#: The starter's source directory for a new project (ADR-020 D8).
SRC = "invariant-docs"


def written(root: Path) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix()
        for folder in (root / SRC, root / "docs")
        for p in folder.rglob("*")
        if p.is_file() and ".venv" not in p.relative_to(root).parts
    )


def conf_namespace(root: Path) -> dict:
    """``conf.py`` executed as Sphinx executes it, ``__file__`` set."""
    return runpy.run_path(str(root / SRC / "conf.py"))


# -- non-interactive init -------------------------------------------------------------


class TestNonInteractiveInit:
    def test_it_writes_the_starter_and_no_environment(self, toy_python, capsys):
        code, text = run(capsys, "init", "--ecosystem", "python", "--name", "toy")
        assert code == 0, text
        assert written(toy_python) == STARTER
        assert not (toy_python / SRC / ".venv").exists()
        assert "invariant docs venv" in text

    def test_the_manifest_is_enforced_on_the_starter(self, toy_python, capsys):
        run(capsys, "init", "--ecosystem", "python", "--name", "toy")
        m = Manifest.locate(toy_python)
        assert m.get("docs.needs.state") == "enforced"
        assert m.get("docs.format") == "sphinx"
        assert m.get("items.backend") == "sphinx_needs"
        assert m.get("items.path") == "invariant-docs/backlog.rst"
        assert m.get("paths.docs") == "invariant-docs/architecture"

    def test_dry_run_names_the_same_files_and_writes_nothing(self, toy_python, capsys):
        code, text = run(capsys, "--json", "init", "--ecosystem", "python", "--dry-run")
        assert code == 0, text
        actions = json.loads(text)["actions"]
        named = sorted(
            path for path in STARTER if any(f"write {path}" in action for action in actions)
        )
        assert named == STARTER
        assert not (toy_python / SRC).exists() and not (toy_python / "docs").exists()
        assert any("invariant docs venv" in action for action in actions)
        code, plain = run(capsys, "init", "--ecosystem", "python", "--dry-run")
        assert "invariant docs venv" in plain

    def test_dry_run_and_no_terminal_name_what_is_written(self, toy_python, capsys):
        _code, text = run(capsys, "--json", "init", "--ecosystem", "python", "--dry-run")
        dry = json.loads(text)["actions"]
        run(capsys, "init", "--ecosystem", "python")
        for path in written(toy_python):
            assert any(f"write {path}" in action for action in dry), path

    def test_json_output_names_the_environment_command(self, toy_python, capsys):
        _code, text = run(capsys, "--json", "init", "--ecosystem", "python")
        data = json.loads(text)
        assert data["docs_venv"] == {
            "created": False,
            "srcdir": SRC,
            "command": "invariant docs venv",
        }

    def test_the_assessment_after_init_finds_no_gap(self, toy_python, capsys):
        _code, text = run(capsys, "--json", "init", "--ecosystem", "python")
        assessment = json.loads(text)["docs_assessment"]
        assert assessment["format"] == "sphinx"
        assert all(not gaps for gaps in assessment["checks"].values()), assessment

    def test_an_item_lands_in_the_backlog_page(self, toy_python, capsys):
        run(capsys, "init", "--ecosystem", "python")
        code, text = run(
            capsys, "item", "new", "bug", "--title", "It breaks", "--field", "severity=high"
        )
        assert code == 0, text
        backlog = (toy_python / SRC / "backlog.rst").read_text(encoding="utf-8")
        assert ".. bug:: It breaks" in backlog
        assert "severity" in conf_namespace(toy_python)["needs_fields"]


class TestTheStartersFiles:
    @pytest.fixture
    def root(self, toy_python, capsys):
        run(capsys, "init", "--ecosystem", "python", "--name", "toy")
        return toy_python

    def test_conf_py_declares_the_baseline(self, root):
        conf = conf_namespace(root)
        assert conf["extensions"] == ["sphinx_needs", "sphinxcontrib.mermaid"]
        assert {(t["directive"], t["prefix"]) for t in conf["needs_types"]} == {
            ("req", "REQ_"), ("dec", "DEC_"), ("comp", "COMP_"), ("ui", "UI_"),
            ("rule", "RULE_"), ("idea", "IDEA_"), ("bug", "BUG_"),
        }
        assert set(conf["needs_links"]) == LINKS
        assert conf["needs_id_required"] is True
        assert conf["needs_build_json"] is True
        regex = re.compile(conf["needs_id_regex"])
        assert regex.match("DEC_ADR_018_D3") and regex.match("RULE_A_B")
        assert not regex.match("dec_lower") and not regex.match("NOPREFIX")
        assert ".venv" in conf["exclude_patterns"]
        assert "design_link" in conf["needs_fields"]

    def test_conf_py_uses_no_deprecated_key_of_the_pinned_version(self, root):
        text = (root / SRC / "conf.py").read_text(encoding="utf-8")
        assert "needs_extra_links" not in text
        assert "needs_extra_options" not in text
        assert "needs_statuses" not in text  # D6 item 4: items carry their own

    def test_conf_py_names_the_project(self, root):
        assert conf_namespace(root)["project"] == "toy"

    def test_requirements_pin_every_package(self, root):
        lines = [
            line.strip()
            for line in (root / SRC / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert lines and all(re.fullmatch(r"[A-Za-z0-9_.\-]+==[0-9][\w.]*", line) for line in lines)
        names = {line.split("==")[0].lower() for line in lines}
        assert {"sphinx", "sphinx-needs", "sphinxcontrib-mermaid"} <= names

    def test_the_pins_are_the_shipped_set(self, root):
        shipped = DATA_DIR / "scaffold" / "sphinx" / "requirements.txt"
        assert (root / SRC / "requirements.txt").read_bytes() == shipped.read_bytes()

    def test_gitignore_holds_the_environment(self, root):
        lines = (root / SRC / ".gitignore").read_text(encoding="utf-8").splitlines()
        assert ".venv/" in lines

    def test_the_overview_has_one_element_need_per_area(self, root):
        text = (root / SRC / "architecture" / "overview.rst").read_text(encoding="utf-8")
        assert ".. comp:: alpha" in text and "   :id: COMP_ALPHA" in text
        assert ".. comp:: beta" in text and "   :id: COMP_BETA" in text
        assert "``pkg/alpha``" in text
        headings = [h.text for h in RstReader().headings(text)]
        for section in ("Layers", "Component responsibilities", "Compliance checklist"):
            assert section in headings
        assert ".. mermaid::" in text

    def test_the_index_toctree_names_every_page(self, root):
        links = {link.target for link in RstReader().links(
            (root / SRC / "index.rst").read_text(encoding="utf-8")
        )}
        assert links == {
            "architecture/overview", "requirements", "decisions/index", "ui",
            "rules", "checklist", "backlog",
        }

    def test_the_generated_pages_carry_the_managed_marker(self, root):
        for name in ("rules.rst", "checklist.rst"):
            assert (root / SRC / name).read_text(encoding="utf-8").startswith(
                ".. invariant-managed: "
            )

    def test_every_file_passes_the_privacy_scan(self, root, capsys):
        for rel in STARTER:
            code, text = run(capsys, "privacy", "scan", str(root / rel))
            assert code == 0, (rel, text)


class TestFlutter:
    def test_a_flutter_tree_gets_an_element_per_area(self, toy_flutter, capsys):
        code, text = run(capsys, "init", "--name", "app")
        assert code == 0, text
        assert Manifest.locate(toy_flutter).get("project.ecosystems") == ["flutter"]
        assert written(toy_flutter) == STARTER
        overview = (toy_flutter / SRC / "architecture" / "overview.rst").read_text(
            encoding="utf-8"
        )
        assert "   :id: COMP_LIB" in overview


class TestWhereTheStarterIsNotWritten:
    def test_existing_docs_are_left_alone_and_the_project_migrates(self, toy_python, capsys):
        overview = toy_python / SRC / "architecture" / "overview.md"
        overview.parent.mkdir(parents=True)
        overview.write_text("# Ours\n", encoding="utf-8")
        run(capsys, "init", "--ecosystem", "python")
        assert written(toy_python) == [f"{SRC}/architecture/overview.md"]
        m = Manifest.locate(toy_python)
        assert m.get("docs.needs.state") == "migrating"
        assert m.get("docs.format") == "markdown"

    def test_a_conf_py_already_in_the_source_directory_is_never_overwritten(
        self, toy_python, capsys
    ):
        conf = toy_python / SRC / "conf.py"
        conf.parent.mkdir()
        conf.write_text("project = 'theirs'\n", encoding="utf-8")
        code, text = run(capsys, "init", "--ecosystem", "python")
        assert code == 0, text
        assert conf.read_text(encoding="utf-8") == "project = 'theirs'\n"
        assert written(toy_python) == [f"{SRC}/conf.py"]
        assert f"{SRC}/conf.py" in text and "starter" in text
        assert Manifest.locate(toy_python).get("docs.needs.state") == "migrating"

    def test_the_markdown_starter_is_retired(self, toy_python, capsys):
        run(capsys, "init", "--ecosystem", "python")
        assert not any((toy_python / SRC).rglob("*.md"))
        assert not (DATA_DIR / "scaffold" / "architecture-overview.md").exists()


# -- interactive init creates the environment ---------------------------------------


class FakeUv:
    """``uv`` as `docs venv` runs it, recorded; the environment is faked."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv, cwd):
        self.calls.append(list(argv))
        if argv[1] == "venv":
            bin_dir = Path(argv[2]) / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "python").write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")


class TestInteractiveInit:
    def test_it_creates_the_environment(self, toy_python, capsys, monkeypatch):
        monkeypatch.setattr(cli_main, "_init_prompter", lambda args: Scripted("not_now"))
        fake = FakeUv()
        monkeypatch.setattr(docs_domain, "_run", fake)
        monkeypatch.setattr(docs_domain, "_uv", lambda: "/bin/uv")
        code, text = run(capsys, "init", "--ecosystem", "python")
        assert code == 0, text
        venv = toy_python / SRC / ".venv"
        assert fake.calls[0] == ["/bin/uv", "venv", str(venv)]
        assert fake.calls[1][:3] == ["/bin/uv", "pip", "sync"]
        assert docs_domain.venv_state(toy_python / SRC).in_sync
        assert "created the docs environment" in text

    def test_a_failed_environment_is_reported_not_a_failed_init(
        self, toy_python, capsys, monkeypatch
    ):
        monkeypatch.setattr(cli_main, "_init_prompter", lambda args: Scripted("not_now"))
        monkeypatch.setattr(docs_domain, "_uv", lambda: None)
        code, text = run(capsys, "init", "--ecosystem", "python")
        assert code == 0, text
        assert written(toy_python) == STARTER
        assert "uv is not on PATH" in text and "invariant docs venv" in text

    def test_non_interactive_init_runs_no_uv(self, toy_python, capsys, monkeypatch):
        fake = FakeUv()
        monkeypatch.setattr(docs_domain, "_run", fake)
        run(capsys, "init", "--ecosystem", "python")
        assert fake.calls == []


class TestTheDocsStepAsksNoFormat:
    def test_no_question_reads_docs_format(self):
        catalogue = DATA_DIR / "questionnaire" / "catalogue.yaml"
        assert "docs.format" not in catalogue.read_text(encoding="utf-8")


# -- the assessment's areas by element needs ----------------------------------------------


def needs_project(root: Path, overview: str, *, extensions=("sphinx_needs",)) -> Manifest:
    (root / "docs" / "architecture").mkdir(parents=True)
    (root / "docs" / "conf.py").write_text(f"extensions = {list(extensions)!r}\n", encoding="utf-8")
    (root / "docs" / "architecture" / "overview.rst").write_text(overview, encoding="utf-8")
    for area in ("alpha", "beta"):
        (root / area).mkdir()
        (root / area / "m.py").write_text("x = 1\n", encoding="utf-8")
    manifest = root / ".claude" / "invariant.yaml"
    manifest.parent.mkdir()
    manifest.write_text(
        yaml.safe_dump({
            "version": 1,
            "project": {"name": "t", "ecosystems": ["generic"]},
            "docs": {"format": "sphinx", "assess": {
                "checks": ["areas"],
                "areas": {"from": "declared", "declared": [
                    {"name": "alpha", "path": "alpha"}, {"name": "beta", "path": "beta"},
                ]},
            }},
        }),
        encoding="utf-8",
    )
    return Manifest.locate(root)


OVERVIEW = """\
Overview
========

The beta package is mentioned here in prose only.

.. comp:: alpha
   :id: COMP_ALPHA

   Owns the work.
"""


class TestAreasByElementNeeds:
    def unnamed(self, manifest):
        return sorted(docs_domain.assess(manifest).unnamed())

    def test_prose_does_not_name_an_area_where_sphinx_needs_is_present(self, tmp_path):
        assert self.unnamed(needs_project(tmp_path, OVERVIEW)) == ["beta"]

    def test_an_element_needs_content_names_it(self, tmp_path):
        text = OVERVIEW + "\n.. comp:: Storage\n   :id: COMP_STORAGE\n\n   The beta area.\n"
        assert self.unnamed(needs_project(tmp_path, text)) == []

    def test_a_need_of_another_role_does_not(self, tmp_path):
        text = OVERVIEW + "\n.. dec:: Use beta\n   :id: DEC_BETA\n"
        assert self.unnamed(needs_project(tmp_path, text)) == ["beta"]

    def test_the_whole_word_rule_holds(self, tmp_path):
        text = OVERVIEW + "\n.. comp:: alphabet and betamax\n   :id: COMP_X\n"
        assert self.unnamed(needs_project(tmp_path, text)) == ["beta"]

    def test_without_sphinx_needs_the_text_names_it(self, tmp_path):
        manifest = needs_project(tmp_path, OVERVIEW, extensions=())
        assert self.unnamed(manifest) == []


# -- adoption of Markdown ---------------------------------------------------------------


class TestMarkdownAdoption:
    @pytest.fixture
    def handbook(self, toy_python, capsys):
        docs = toy_python / SRC / "architecture"
        docs.mkdir(parents=True)
        (docs / "overview.md").write_text("# Ours\n", encoding="utf-8")
        run(capsys, "init", "--ecosystem", "python")
        (toy_python / "handbook").mkdir()
        (toy_python / "handbook" / "guide.md").write_text("# Guide\n", encoding="utf-8")
        return toy_python

    def test_in_place_is_refused_naming_copy(self, handbook, capsys):
        code, text = run(capsys, "docs", "adopt", "handbook", "--mode", "in_place")
        assert code == 2
        assert "--mode copy" in text and "ADR-018" in text

    def test_copy_into_a_migrating_project_is_accepted(self, handbook, capsys):
        code, text = run(capsys, "docs", "adopt", "handbook", "--mode", "copy", "--dry-run")
        assert code == 0, text

    def test_copy_into_an_enforced_project_is_refused(self, toy_python, capsys):
        run(capsys, "init", "--ecosystem", "python")
        (toy_python / "handbook").mkdir()
        (toy_python / "handbook" / "guide.md").write_text("# Guide\n", encoding="utf-8")
        code, text = run(capsys, "docs", "adopt", "handbook", "--mode", "copy", "--dry-run")
        assert code == 2
        assert "enforced" in text and "migrating" in text


# -- the real build -------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("INVARIANT_TEST_SPHINX_BUILD") or shutil.which("uv") is None,
    reason="set INVARIANT_TEST_SPHINX_BUILD (and have uv) to create the starter's "
    "pinned environment and build it strictly",
)
class TestTheStarterBuildsStrictly:
    """Interactive `init` creates the pinned environment; `docs build` is green."""

    def init_and_build(self, root, capsys, monkeypatch, *argv):
        monkeypatch.setattr(cli_main, "_init_prompter", lambda args: Scripted("not_now"))
        monkeypatch.setattr(docs_domain, "_uv", lambda: shutil.which("uv"))
        code, text = run(capsys, "init", *argv)
        assert code == 0, text
        assert "created the docs environment" in text, text
        assert docs_domain.venv_state(root / SRC).in_sync
        code, text = run(capsys, "--json", "docs", "build")
        record = json.loads(text)
        assert code == 0 and record["passed"], record
        assert record["diagnostics"] == []
        return record

    def test_a_toy_python_project(self, toy_python, capsys, monkeypatch):
        self.init_and_build(toy_python, capsys, monkeypatch, "--ecosystem", "python")

    def test_a_toy_flutter_tree(self, toy_flutter, capsys, monkeypatch):
        self.init_and_build(toy_flutter, capsys, monkeypatch)

    def test_the_rendered_guidelines_pages_build_too(self, toy_python, capsys, monkeypatch):
        self.init_and_build(toy_python, capsys, monkeypatch, "--ecosystem", "python")
        ruleset = Path(__file__).resolve().parent.parent / "docs" / "guidelines" / "ruleset.yaml"
        # The starter wrote one with the mandate's two rules; this
        # repository's replaces it, links to Markdown files included.
        target = toy_python / "docs" / "guidelines" / "ruleset.yaml"
        shutil.copy(ruleset, target)
        manifest = toy_python / ".claude" / "invariant.yaml"
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        data["guidelines"] = {"ruleset": "docs/guidelines/ruleset.yaml"}
        manifest.write_text(yaml.safe_dump(data), encoding="utf-8")
        code, text = run(capsys, "guidelines", "render")
        assert code == 0, text
        rules = (toy_python / SRC / "rules.rst").read_text(encoding="utf-8")
        assert ":id: RULE_LAYERS_ONLY_THE_CLI_IMPORTS_THE_ORACLE" in rules
        # A path link renders as text; a need id as decided_by (D12), so the
        # decisions it names must be needs of the project for the build to pass.
        assert ":decided_by: DEC_ADR_007_D6" in rules
        index = toy_python / SRC / "decisions" / "index.rst"
        index.write_text(
            index.read_text(encoding="utf-8")
            + "".join(
                f"\n.. dec:: ADR-007 {d}\n   :id: DEC_ADR_007_{d}\n   :status: accepted\n"
                for d in ("D6", "D11")
            ),
            encoding="utf-8",
        )
        code, text = run(capsys, "--json", "docs", "build")
        record = json.loads(text)
        assert code == 0 and record["passed"], record


def test_the_conf_py_template_parses():
    """A template edit that breaks Python fails here, not in a project's build."""
    template = DATA_DIR / "scaffold" / "sphinx" / "conf.py"
    ast.parse(
        template.read_text(encoding="utf-8")
        .replace("{{types}}", "[]")
        .replace("{{project}}", '"x"')
        .replace("{{item_files}}", "[]")
    )


class TestForceKeepsTheMandate:
    def test_a_forced_reinit_keeps_the_project_enforced(self, toy_python, capsys):
        run(capsys, "init", "--ecosystem", "python")
        before = written(toy_python)
        code, text = run(capsys, "init", "--force", "--ecosystem", "python")
        assert code == 0, text
        m = Manifest.locate(toy_python)
        assert m.get("docs.needs.state") == "enforced"
        assert m.get("items.backend") == "sphinx_needs"
        assert m.get("items.path") == "invariant-docs/backlog.rst"
        assert m.get("paths.docs") == "invariant-docs/architecture"
        assert m.get("guidelines.ruleset") == "docs/guidelines/ruleset.yaml"
        assert written(toy_python) == before

    def test_a_migrating_project_stays_as_it_was(self, toy_python, capsys):
        (toy_python / SRC / "architecture").mkdir(parents=True)
        run(capsys, "init", "--ecosystem", "python")
        run(capsys, "init", "--force", "--ecosystem", "python")
        assert Manifest.locate(toy_python).get("docs.needs.state") == "migrating"


# -- plan_starter, directly ----------------------------------------------------------


def starter_manifest(root: Path, **docs) -> Manifest:
    body = {"version": 1, "project": {"name": "t", "ecosystems": ["generic"]}}
    if docs:
        body["docs"] = docs
    return Manifest.from_raw(body, root=root, path=root / "m.yaml", env={})


def files_of(starter) -> dict[str, str]:
    return dict(starter.files)


class TestPlanStarter:
    def test_paths_docs_with_no_directory_above_it_is_refused(self, tmp_path):
        with pytest.raises(docs_domain.StarterNotWritten) as caught:
            docs_domain.plan_starter(
                starter_manifest(tmp_path), project="t", base=tmp_path, docs="architecture"
            )
        assert "root is never the docs project" in str(caught.value)

    def test_a_file_the_starter_writes_is_never_written_over(self, tmp_path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "index.rst").write_text("Ours\n====\n", encoding="utf-8")
        with pytest.raises(docs_domain.StarterNotWritten) as caught:
            docs_domain.plan_starter(
                starter_manifest(tmp_path), project="t", base=tmp_path,
                docs="docs/architecture",
            )
        assert "docs/index.rst already exists" in str(caught.value)

    def test_no_area_gives_a_diagram_of_the_project_and_no_element(self, tmp_path):
        manifest = starter_manifest(
            tmp_path, assess={"areas": {"from": "declared", "declared": []}}
        )
        starter = docs_domain.plan_starter(
            manifest, project="t", base=tmp_path, docs="docs/architecture"
        )
        overview = files_of(starter)["architecture/overview.rst"]
        assert 'PROJECT["t"]' in overview
        assert ".. comp::" not in overview
        assert "No source area was found" in overview

    def test_two_areas_of_one_name_take_their_paths(self, tmp_path):
        declared = [{"path": "a/core"}, {"path": "b/core"}]
        manifest = starter_manifest(
            tmp_path, assess={"areas": {"from": "declared", "declared": declared}}
        )
        starter = docs_domain.plan_starter(
            manifest, project="t", base=tmp_path, docs="docs/architecture"
        )
        overview = files_of(starter)["architecture/overview.rst"]
        assert ":id: COMP_A_CORE" in overview and ":id: COMP_B_CORE" in overview

    def test_a_further_required_section_is_written(self, tmp_path):
        manifest = starter_manifest(
            tmp_path,
            assess={
                "sections": ["Layers", "Data flows"],
                "areas": {"from": "declared", "declared": []},
            },
        )
        starter = docs_domain.plan_starter(
            manifest, project="t", base=tmp_path, docs="docs/architecture"
        )
        headings = [h.text for h in RstReader().headings(
            files_of(starter)["architecture/overview.rst"]
        )]
        assert headings.count("Data flows") == 1 and headings.count("Layers") == 1

    def test_an_extension_type_is_declared_too(self, tmp_path):
        types = [
            {"directive": "req", "prefix": "REQ_", "role": "requirement"},
            {"directive": "dec", "prefix": "DEC_", "role": "decision"},
            {"directive": "comp", "prefix": "COMP_", "role": "element"},
            {"directive": "ui", "prefix": "UI_", "role": "ui"},
            {"directive": "rule", "prefix": "RULE_", "role": "rule"},
            {"directive": "idea", "prefix": "IDEA_", "role": "idea"},
            {"directive": "bug", "prefix": "BUG_", "role": "bug"},
            {"directive": "instr", "prefix": "INSTR_"},
        ]
        starter = docs_domain.plan_starter(
            starter_manifest(tmp_path, needs={"types": types}),
            project="t", base=tmp_path, docs="docs/architecture",
        )
        conf = files_of(starter)["conf.py"]
        assert '"directive": "instr", "title": "Instr", "prefix": "INSTR_"' in conf

    def test_every_role_has_a_type(self):
        from invariant.core import needs

        with pytest.raises(ValueError, match="'rule'"):
            needs.type_of_role(needs.default_types()[:4], "rule")
