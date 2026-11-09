"""The sphinx adapter (ADR-015 D1, pulled forward by ADR-018 increment 2).

The console samples below are verbatim from `sphinx-build` (Sphinx 9.1.0,
sphinx-needs 8.1.1) run on a throwaway tree, with the absolute path to the
tree replaced by ``/work/toy``, so the parser is held to what the tool prints.

One test drives a real `sphinx-build` end to end. It runs only where
``INVARIANT_TEST_SPHINX_BUILD`` names one, so the suite never depends on Sphinx
being installed.
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml
from test_domains import git_project
from test_rules import ctx_factory, only, repo, write_manifest  # noqa: F401

from invariant.adapters import available, defaults_for, detect, get_adapter
from invariant.adapters.base import Adapter, TestResult
from invariant.adapters.sphinx import (
    DEFAULT_DOCS,
    SphinxAdapter,
    conf_facts,
    diagnostics,
    docs_project_srcdir,
)
from invariant.core.manifest import DEFAULTS
from invariant.core.rules import evaluate
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain

SPHINX = get_adapter("sphinx")

#: `sphinx-build -E -W --keep-going -n -b linkcheck`: two reference warnings
#: and one link that could not be reached.
LINKCHECK_RED_OUT = """\
Running Sphinx v9.1.0
loading translations [en]... done
making output directory... done
building [mo]: targets for 0 po files that are out of date
writing output...
building [linkcheck]: targets for 2 source files that are out of date
updating environment: [new config] 2 added, 0 changed, 0 removed
reading sources... [ 50%] index
reading sources... [100%] other

looking for now-outdated files... none found
pickling environment... done
checking consistency... done
preparing documents... done
copying assets...
copying assets: done
writing output... [ 50%] index
writing output... [100%] other

(           index: line   10) broken    http://localhost:1/nothing - HTTPConnectionPool(host='localhost', port=1): Max retries exceeded with url: /nothing (Caused by NewConnectionError("HTTPConnection(host='localhost', port=1): Failed to establish a new connection: [Errno 61] Connection refused"))
build finished with problems, 2 warnings (with warnings treated as errors).
"""
LINKCHECK_RED_ERR = """\
/work/toy/docs/index.rst:4: WARNING: unknown document: 'missing' [ref.doc]
/work/toy/docs/index.rst:4: WARNING: undefined label: 'nolabel' [ref.ref]
"""

#: The needs builder over a tree with a docutils error and a dangling need link.
NEEDS_RED_ERR = """\
/work/toy/docs/index.rst:12: ERROR: Unknown directive type "foo".

.. foo:: [docutils]
/work/toy/docs/index.rst:8: WARNING: Need 'COMP_A' has unknown outgoing link 'COMP_MISSING' in field 'links' [needs.link_outgoing]
"""

GREEN_OUT = """\
Running Sphinx v9.1.0
building [linkcheck]: targets for 2 source files that are out of date
updating environment: [new config] 2 added, 0 changed, 0 removed
reading sources... [ 50%] index
reading sources... [100%] other
build succeeded.
"""

#: The html builder without -W: the same warnings, and an exit status of 0.
NO_W_OUT = """\
updating environment: [new config] 2 added, 0 changed, 0 removed
build succeeded, 2 warnings.
"""

#: A second build without -E into the same output directory.
INCREMENTAL_OUT = """\
Running Sphinx v9.1.0
loading pickled environment... done
updating environment: 0 added, 0 changed, 0 removed
build succeeded.
"""

STARTER_CONF = """\
project = "toy"
extensions = ["sphinx_needs", "sphinxcontrib.mermaid"]
needs_id_required = True
"""


def tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# -- registration and defaults ---------------------------------------------------


class TestRegistration:
    def test_sphinx_and_its_docs_alias_are_registered(self):
        assert "sphinx" in available() and "docs" in available()
        assert isinstance(get_adapter("sphinx"), SphinxAdapter)
        assert isinstance(get_adapter("docs"), SphinxAdapter)

    def test_the_default_commands_are_strict(self):
        commands = defaults_for("sphinx")["commands"]
        for key in ("build", "test", "test_one"):
            assert "-E -W --keep-going -n" in commands[key], key
        assert "-b html" in commands["build"]
        assert "-b linkcheck" in commands["test"]
        assert commands["test_one"].endswith(" {selector}")
        assert commands["coverage"] is None and commands["lint"] is None

    def test_output_is_written_under_the_per_run_bundle_never_the_tree(self):
        for key, command in defaults_for("sphinx")["commands"].items():
            if command:
                assert "{srcdir} {result_bundle}/" in command, key

    def test_a_failed_link_is_retried_once_before_it_counts(self):
        assert "-D linkcheck_retries=2" in defaults_for("sphinx")["commands"]["test"]

    def test_the_docs_default_is_the_manifests(self):
        """Detection reads the default `paths.docs` (ADR-018 D3) without importing
        the manifest; this holds the two spellings together."""
        assert DEFAULT_DOCS == DEFAULTS["paths"]["docs"]

    def test_coverage_is_declared_not_applicable_with_a_reason(self):
        assert SphinxAdapter.coverage_applicable is False
        assert "no measurable lines" in SphinxAdapter.coverage_reason
        assert Adapter.coverage_applicable is True
        assert Adapter().claims(Path("a.py"), Path("/nowhere")) is False


# -- detection (ADR-015 D1, ADR-018 D3) ----------------------------------------------


class TestDetection:
    @pytest.mark.parametrize(
        "conf", ["conf.py", "doc/conf.py", "source/conf.py", "docs/source/conf.py"]
    )
    def test_a_conf_py_at_a_known_home_is_sphinx(self, tmp_path, conf):
        tree(tmp_path, {conf: "project = 'x'\n"})
        assert SPHINX.detect(tmp_path)
        assert SPHINX.source_dir(tmp_path) == (tmp_path / conf).parent
        assert detect(tmp_path) == ["sphinx"]

    def test_no_conf_py_is_not_sphinx(self, tmp_path):
        tree(tmp_path, {"docs/index.rst": "x\n", "lib/conf.py": "x = 1\n"})
        assert not SPHINX.detect(tmp_path)
        assert SPHINX.source_dir(tmp_path) is None

    def test_a_starter_shaped_docs_project_is_not_an_ecosystem(self, tmp_path):
        """ADR-018 row 2's proof: a Python project whose only Sphinx tree is its
        docs project (D3's default srcdir, `docs/`, above `docs/architecture`)."""
        tree(
            tmp_path,
            {
                "pyproject.toml": "[project]\nname = 'toy'\n",
                "src/toy/__init__.py": "",
                "docs/conf.py": STARTER_CONF,
                "docs/index.rst": "Toy\n===\n",
                "docs/architecture/overview.rst": "Overview\n========\n",
                "docs/requirements.txt": "sphinx==9.1.0\n",
            },
        )
        assert docs_project_srcdir(tmp_path) == tmp_path / "docs"
        assert not SPHINX.detect(tmp_path)
        assert detect(tmp_path) == ["python"]

    def test_the_docs_project_is_the_nearest_conf_py_at_or_above_paths_docs(self, tmp_path):
        tree(tmp_path, {"docs/architecture/conf.py": "", "docs/conf.py": ""})
        assert docs_project_srcdir(tmp_path) == tmp_path / "docs" / "architecture"
        assert docs_project_srcdir(tmp_path, "elsewhere/arch") is None

    def test_the_search_never_climbs_above_the_root(self, tmp_path):
        (tmp_path / "conf.py").write_text("", encoding="utf-8")
        inner = tree(tmp_path / "proj", {"README": "x\n"})
        assert docs_project_srcdir(inner) is None

    def test_the_root_is_never_the_docs_project(self, tmp_path):
        """A root `conf.py` is a documentation repository's own content."""
        tree(tmp_path, {"conf.py": ""})
        assert docs_project_srcdir(tmp_path) is None
        assert docs_project_srcdir(tmp_path, ".") is None

    def test_content_beside_a_docs_project_is_sphinx(self, tmp_path):
        """A documentation repository: its own content under `source/`, and the
        docs project at `docs/` skipped."""
        tree(tmp_path, {"docs/conf.py": STARTER_CONF, "source/conf.py": ""})
        assert SPHINX.detect(tmp_path)
        assert SPHINX.source_dir(tmp_path) == tmp_path / "source"

    def test_two_content_trees_are_asked_about_not_guessed(self, tmp_path):
        tree(tmp_path, {"conf.py": "", "source/conf.py": ""})
        assert SPHINX.detect(tmp_path)
        assert SPHINX.source_dir(tmp_path) is None
        assert SPHINX.source_dirs(tmp_path) == [tmp_path, tmp_path / "source"]

    def test_a_declared_project_whose_only_tree_is_at_docs_still_builds_it(self, tmp_path):
        """Declared `sphinx` by hand (`init --ecosystem sphinx`): with nothing
        but the skipped tree, that tree is the one the project builds."""
        tree(tmp_path, {"docs/conf.py": ""})
        assert not SPHINX.detect(tmp_path)
        assert SPHINX.source_dir(tmp_path) == tmp_path / "docs"


# -- reading conf.py --------------------------------------------------------------------


class TestConfFacts:
    def test_extensions_are_read_from_every_literal_form(self, tmp_path):
        conf = tree(
            tmp_path,
            {
                "conf.py": (
                    "extensions = ['a']\n"
                    "extensions += ['b']\n"
                    "extensions.append('sphinx_needs')\n"
                    "extensions.extend(('c',))\n"
                )
            },
        ) / "conf.py"
        assert conf_facts(conf).extensions == ("a", "b", "sphinx_needs", "c")

    def test_extensions_built_at_run_time_cannot_be_told(self, tmp_path):
        conf = tree(tmp_path, {"conf.py": "extensions = load()\n"}) / "conf.py"
        assert conf_facts(conf).extensions is None

    def test_an_unparseable_or_absent_conf_tells_nothing(self, tmp_path):
        conf = tree(tmp_path, {"conf.py": "extensions = [\n"}) / "conf.py"
        assert conf_facts(conf).extensions is None
        assert conf_facts(conf).narrowing == ()
        assert conf_facts(tmp_path / "absent.py").extensions is None

    def test_no_extensions_at_all_is_an_empty_answer(self, tmp_path):
        conf = tree(tmp_path, {"conf.py": "project = 'x'\n"}) / "conf.py"
        assert conf_facts(conf).extensions == ()

    def test_each_narrowing_setting_is_named_with_its_count(self, tmp_path):
        conf = tree(
            tmp_path,
            {
                "conf.py": (
                    "linkcheck_ignore = [r'http://a', r'http://b']\n"
                    "linkcheck_anchors = False\n"
                    "nitpick_ignore = [('py:class', 'X')]\n"
                    "nitpick_ignore_regex = make()\n"
                    "suppress_warnings = ['ref.ref']\n"
                )
            },
        ) / "conf.py"
        assert conf_facts(conf).narrowing == (
            "linkcheck_ignore (2 patterns)",
            "linkcheck_anchors = False",
            "nitpick_ignore (1 pattern)",
            "nitpick_ignore_regex (not a literal; count unknown)",
            "suppress_warnings (1 pattern)",
        )

    def test_settings_that_narrow_nothing_are_not_named(self, tmp_path):
        conf = tree(
            tmp_path,
            {"conf.py": "linkcheck_ignore = []\nlinkcheck_anchors = True\n"},
        ) / "conf.py"
        assert conf_facts(conf).narrowing == ()


# -- reading a run -----------------------------------------------------------------------


class TestReadingARun:
    def test_every_warning_error_and_broken_link_is_a_failure(self):
        result = SPHINX.parse_test_output(LINKCHECK_RED_OUT, LINKCHECK_RED_ERR, 1)
        assert not result.passed
        assert (result.total, result.failed, result.failed_tests) == (2, 3, 3)
        assert "/work/toy/docs/index.rst:4 [ref.doc] unknown document: 'missing'" in result.note
        assert "broken link" in result.note

    def test_a_diagnostic_is_named_by_document_line_and_type(self):
        found = diagnostics(NEEDS_RED_ERR)
        assert [(d.document, d.line, d.level, d.type) for d in found] == [
            ("/work/toy/docs/index.rst", 12, "ERROR", "docutils"),
            ("/work/toy/docs/index.rst", 8, "WARNING", "needs.link_outgoing"),
        ]
        assert found[0].message == 'Unknown directive type "foo".'

    def test_a_diagnostic_without_a_line_or_a_location_is_still_one(self):
        found = diagnostics(
            "/work/toy/docs/x.rst: WARNING: document isn't included in any toctree"
            " [toc.not_included]\n"
            "WARNING: html_static_path entry '_static' does not exist\n"
            "/work/toy/docs/y.rst:3: CRITICAL: Title level inconsistent:\n"
        )
        assert [(d.document, d.line, d.level, d.type) for d in found] == [
            ("/work/toy/docs/x.rst", None, "WARNING", "toc.not_included"),
            (None, None, "WARNING", None),
            ("/work/toy/docs/y.rst", 3, "CRITICAL", None),
        ]
        assert found[0].render() == (
            "/work/toy/docs/x.rst [toc.not_included] document isn't included in any toctree"
        )
        assert found[1].render() == "html_static_path entry '_static' does not exist"

    def test_the_two_steps_of_a_test_are_counted_together(self):
        result = SPHINX.parse_test_output(
            LINKCHECK_RED_OUT + GREEN_OUT, LINKCHECK_RED_ERR + NEEDS_RED_ERR, 1
        )
        assert (result.total, result.failed) == (4, 5)
        assert result.note.count(";") >= 3

    def test_a_long_list_of_failures_is_cut_short_and_counted(self):
        err = "".join(f"/d/i.rst:{n}: WARNING: w{n} [ref.ref]\n" for n in range(1, 13))
        result = SPHINX.parse_test_output("", err, 1)
        assert result.failed == 12
        assert "and 4 more" in result.note

    def test_a_clean_run_passes_and_counts_its_documents(self):
        result = SPHINX.parse_test_output(GREEN_OUT, "", 0)
        assert result.passed and result.trustworthy
        assert (result.total, result.failed) == (2, 0)
        assert result.note == ""

    def test_a_run_that_read_no_environment_count_cannot_count(self):
        result = SPHINX.parse_test_output("build succeeded.\n", "", 0)
        assert result.total is None and result.passed

    def test_warnings_fail_the_run_even_when_it_exited_zero(self):
        result = SPHINX.parse_test_output(NO_W_OUT, LINKCHECK_RED_ERR, 0)
        assert not result.passed
        assert result.failed == 2

    def test_a_failed_exit_with_nothing_parsed_still_fails(self):
        result = SPHINX.parse_test_output("Configuration error:\n", "", 2)
        assert not result.passed and result.failed == 0


class TestHazards:
    def by_id(self, result: TestResult) -> dict:
        return {h.id: h for h in result.hazards}

    def test_a_build_without_W_is_named(self):
        hazards = self.by_id(SPHINX.parse_test_output(NO_W_OUT, LINKCHECK_RED_ERR, 0))
        assert "sphinx.warnings_not_errors" in hazards
        assert hazards["sphinx.warnings_not_errors"].may_overstate

    def test_an_incremental_build_without_E_is_named(self):
        result = SPHINX.parse_test_output(INCREMENTAL_OUT, "", 0)
        assert "sphinx.incremental" in self.by_id(result)
        assert not result.trustworthy

    def test_a_network_failure_in_linkcheck_can_only_understate(self):
        hazards = self.by_id(SPHINX.parse_test_output(LINKCHECK_RED_OUT, "", 1))
        assert hazards["sphinx.linkcheck_network"].can_overstate is False

    def test_a_broken_link_that_answered_is_no_network_failure(self):
        out = "(index: line    3) broken    https://example.org/x - 404 Client Error: Not Found\n"
        result = SPHINX.parse_test_output(out, "", 1)
        assert result.failed == 1
        assert "sphinx.linkcheck_network" not in self.by_id(result)

    def test_hazard_ids_are_unique_and_classified(self):
        hazards = SPHINX.hazards()
        assert len({h.id for h in hazards}) == len(hazards)
        assert all(h.can_overstate is not None for h in hazards)


class TestAnnotate:
    def test_narrowing_settings_are_noted_not_hazards(self, tmp_path):
        tree(tmp_path, {"docs/source/conf.py": "linkcheck_ignore = ['x']\n"})
        result = SPHINX.annotate(TestResult(passed=True, exit_code=0, total=1), tmp_path)
        assert result.hazards == []
        assert result.note == (
            "conf.py narrows what passes (the project's stated choice): "
            "linkcheck_ignore (1 pattern)"
        )

    def test_the_tree_path_is_spelled_relative_to_the_project(self, tmp_path):
        tree(tmp_path, {"conf.py": ""})
        note = f"2 failure(s): {tmp_path}/index.rst:4 [ref.doc] x"
        result = SPHINX.annotate(TestResult(passed=False, exit_code=1, note=note), tmp_path)
        assert result.note == "2 failure(s): index.rst:4 [ref.doc] x"

    def test_several_content_trees_are_each_named(self, tmp_path):
        tree(
            tmp_path,
            {"conf.py": "nitpick_ignore = [1]\n", "source/conf.py": "suppress_warnings = [1]\n"},
        )
        note = SPHINX.annotate(TestResult(passed=True, exit_code=0), tmp_path).note
        assert "conf.py: nitpick_ignore (1 pattern)" in note
        assert "source/conf.py: suppress_warnings (1 pattern)" in note

    def test_nothing_to_say_leaves_the_result_alone(self, tmp_path):
        result = TestResult(passed=True, exit_code=0)
        assert SPHINX.annotate(result, tmp_path) is result

    def test_the_base_adapter_annotates_nothing(self, tmp_path):
        result = TestResult(passed=True, exit_code=0)
        assert Adapter().annotate(result, tmp_path) is result


# -- selectors and scope --------------------------------------------------------------------


class TestSelectors:
    @pytest.mark.parametrize("selector", ["docs/index.rst", "source/guide.md"])
    def test_a_document_is_this_ecosystems(self, selector):
        assert SPHINX.owns_selector(selector)

    @pytest.mark.parametrize("selector", ["tests/test_x.py", "docs/conf.py", "x.txt"])
    def test_anything_else_is_not_claimed(self, selector):
        assert not SPHINX.owns_selector(selector)

    def test_several_documents_are_appended_together(self):
        assert SPHINX.join_selectors(["a.rst", "b.rst"]) == "a.rst b.rst"

    def test_the_scope_of_a_run(self):
        assert SPHINX.test_scope_of([]) == "full"
        assert SPHINX.test_scope_of(["docs/a.rst", "docs/b.rst"]) == "scoped"
        assert SPHINX.test_scope_of(["-q"]) is None

    def test_no_document_is_a_test_or_inert_by_this_adapter(self, tmp_path):
        assert SPHINX.is_test_file(Path("docs/index.rst")) is None
        assert SPHINX.carries_executable_code(tmp_path / "conf.py") is None


class TestClaims:
    def test_everything_under_a_source_directory_is_claimed(self, tmp_path):
        tree(tmp_path, {"docs/conf.py": "", "src/app.py": ""})
        for rel in ("docs/conf.py", "docs/_ext/role.py", "docs/_static/x.css", "docs/a.rst"):
            assert SPHINX.claims(Path(rel), tmp_path), rel
        assert not SPHINX.claims(Path("src/app.py"), tmp_path)

    def test_a_root_source_directory_claims_only_what_sphinx_owns_there(self, tmp_path):
        """With `conf.py` at the root, claiming everything would set a stacked
        Python package aside from its floor (ADR-013), the relaxing direction."""
        tree(tmp_path, {"conf.py": ""})
        for rel in ("conf.py", "index.rst", "guide.md", "_templates/page.html", "_static/a.css"):
            assert SPHINX.claims(Path(rel), tmp_path), rel
        for rel in ("src/app.py", "setup.py", "_ext/role.py"):
            assert not SPHINX.claims(Path(rel), tmp_path), rel

    def test_no_source_directory_claims_nothing(self, tmp_path):
        assert not SPHINX.claims(Path("docs/conf.py"), tmp_path)


# -- init's settings ---------------------------------------------------------------------


class TestInitSettings:
    def test_the_source_directory_is_written_as_a_var(self, tmp_path):
        tree(tmp_path, {"source/conf.py": "extensions = []\n"})
        settings, notes = SPHINX.init_settings(tmp_path)
        assert settings == {"vars": {"srcdir": "source"}}
        assert notes == []

    def test_sphinx_needs_adds_the_needs_builder_to_the_test(self, tmp_path):
        tree(tmp_path, {"conf.py": STARTER_CONF})
        settings, _notes = SPHINX.init_settings(tmp_path)
        assert settings["vars"] == {"srcdir": "."}
        test = settings["commands"]["test"]
        assert test.startswith(defaults_for("sphinx")["commands"]["test"] + " && ")
        assert test.endswith("-b needs {srcdir} {result_bundle}/needs")
        assert settings["commands"]["test_one"].count("{selector}") == 2

    def test_unreadable_extensions_are_said(self, tmp_path):
        tree(tmp_path, {"doc/conf.py": "extensions = load()\n"})
        settings, notes = SPHINX.init_settings(tmp_path)
        assert settings == {"vars": {"srcdir": "doc"}}
        assert "could not read" in notes[0] and "needs" in notes[0]

    def test_two_trees_write_nothing_and_ask(self, tmp_path):
        tree(tmp_path, {"doc/conf.py": "", "source/conf.py": ""})
        settings, notes = SPHINX.init_settings(tmp_path)
        assert settings == {}
        assert "doc, source" in notes[0] and "vars.srcdir" in notes[0]

    def test_no_tree_writes_nothing_and_says_so(self, tmp_path):
        settings, notes = SPHINX.init_settings(tmp_path)
        assert settings == {}
        assert "no conf.py" in notes[0]

    def test_the_base_adapter_has_nothing_to_write(self, tmp_path):
        assert Adapter().init_settings(tmp_path) == ({}, [])


class TestInit:
    def test_init_fills_in_the_source_directory(self, tmp_path, monkeypatch, capsys):
        from invariant.cli.main import main

        root = tree(tmp_path / "proj", {"source/conf.py": STARTER_CONF})
        subprocess.run(["git", "init", "-q", "-b", "master"], cwd=root, check=True)
        monkeypatch.setenv("INVARIANT_HOME", str(tmp_path / "home"))
        monkeypatch.chdir(root)
        assert main(["init", "--root", str(root)]) == 0
        body = yaml.safe_load((root / ".claude" / "invariant.yaml").read_text())
        assert body["project"]["ecosystems"] == ["sphinx"]
        assert body["vars"] == {"srcdir": "source"}
        assert "-b needs" in body["commands"]["test"]

    def test_a_secondary_sphinx_keeps_its_commands_under_its_own_key(self, tmp_path):
        from invariant.cli.main import _init_ecosystem_settings

        root = tree(tmp_path, {"pyproject.toml": "", "conf.py": STARTER_CONF})
        settings, notes = _init_ecosystem_settings(root, ["python", "sphinx"])
        assert settings["vars"] == {"srcdir": "."}
        assert "-b needs" in settings["commands"]["sphinx"]["test"]
        assert notes == []

    def test_an_ambiguous_tree_is_named_in_the_report(self, tmp_path, monkeypatch, capsys):
        from invariant.cli.main import main

        root = tree(tmp_path / "proj", {"doc/conf.py": "", "source/conf.py": ""})
        subprocess.run(["git", "init", "-q", "-b", "master"], cwd=root, check=True)
        monkeypatch.setenv("INVARIANT_HOME", str(tmp_path / "home"))
        assert main(["init", "--root", str(root), "--dry-run"]) == 0
        assert "set vars.srcdir" in capsys.readouterr().out


# -- coverage.meets_floor answers N/A by name ----------------------------------------------


def sphinx_ctx(ctx_factory, ecosystems, files, coverage=None):  # noqa: F811
    write_manifest(
        ctx_factory.main,
        {
            "version": 1,
            "project": {"name": "demo", "ecosystems": ecosystems},
            "methodology": {"gates": {"coverage_min": 90, "coverage_scope": "touched"}},
        },
    )
    tree(ctx_factory.wt, {"docs/conf.py": "", "docs/index.rst": "x\n", "src/app.py": ""})
    return ctx_factory(
        cwd=ctx_factory.main,
        session={"id": "s1", "files": files, "worktree": str(ctx_factory.wt)},
        pipeline={"evidence": {"coverage": coverage or {}}},
    )


class TestCoverageNotApplicable:
    def test_a_docs_change_is_not_applicable_naming_the_adapter(self, ctx_factory):  # noqa: F811
        ctx = sphinx_ctx(ctx_factory, ["sphinx"], ["docs/conf.py", "docs/index.rst"])
        verdict = only(evaluate(["coverage.meets_floor"], ctx)).verdict
        assert verdict.skipped
        assert verdict.message == (
            "the sphinx adapter declares coverage not applicable, because a "
            "documentation build executes no measurable lines"
        )
        assert verdict.detail == "docs/conf.py, docs/index.rst"

    def test_a_stacked_python_file_is_still_held(self, ctx_factory):  # noqa: F811
        ctx = sphinx_ctx(
            ctx_factory,
            ["python", "sphinx"],
            ["docs/conf.py", "src/app.py"],
            coverage={str(ctx_factory.wt / "src/app.py"): 40.0},
        )
        verdict = only(evaluate(["coverage.meets_floor"], ctx)).verdict
        assert not verdict.ok
        assert "src/app.py" in verdict.detail
        assert "docs/conf.py (coverage not applicable: sphinx)" in verdict.detail

    def test_without_sphinx_declared_the_docs_project_is_unchanged(self, ctx_factory):  # noqa: F811
        ctx = sphinx_ctx(ctx_factory, ["python"], ["docs/conf.py"])
        verdict = only(evaluate(["coverage.meets_floor"], ctx)).verdict
        assert not verdict.ok
        assert verdict.message == "no coverage was recorded"


class TestDoctorRow:
    def test_doctor_says_coverage_is_not_applicable(self, tmp_path, monkeypatch, capsys):
        from invariant.cli.main import main

        root = tmp_path / "proj"
        git_project(
            root,
            {"version": 1, "project": {"name": "demo", "ecosystems": ["sphinx"]}},
        )
        monkeypatch.chdir(root)
        main(["doctor"])
        out = capsys.readouterr().out
        assert "coverage" in out and "not applicable (sphinx)" in out


# -- through the pipeline ----------------------------------------------------------------


class TestThroughThePipeline:
    """A stand-in `sphinx-build` prints a recorded run; the pipeline records it."""

    FAKE = """\
printf '%s' "$SPHINX_OUT"
printf '%s' "$SPHINX_ERR" >&2
exit "$SPHINX_EXIT"
"""

    def project(self, tmp_path):
        root = tmp_path / "proj"
        manifest = git_project(
            root,
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["sphinx"]},
                "vars": {"srcdir": "docs"},
                "commands": {"test": "sh fake-sphinx.sh {srcdir} {result_bundle}/linkcheck"},
            },
        )
        tree(root, {"fake-sphinx.sh": self.FAKE, "docs/conf.py": "linkcheck_anchors = False\n"})
        session = SessionDomain(manifest).start("x")
        pipelines = PipelineDomain(manifest)
        pipelines.start(session.id, task="t")
        return pipelines, session, root

    def test_a_red_run_is_recorded_by_document_and_annotated(self, tmp_path, monkeypatch):
        pipelines, session, root = self.project(tmp_path)
        monkeypatch.setenv("SPHINX_OUT", LINKCHECK_RED_OUT)
        monkeypatch.setenv("SPHINX_ERR", LINKCHECK_RED_ERR.replace("/work/toy", str(root)))
        monkeypatch.setenv("SPHINX_EXIT", "1")
        record = pipelines.run_tests(session.id, cwd=root)
        assert (record["passed"], record["executed"], record["failed"]) == (False, 2, 3)
        assert "docs/index.rst:4 [ref.doc]" in record["note"]
        assert str(root) not in record["note"]
        assert "linkcheck_anchors = False" in record["note"]

    def test_a_green_run_is_trustworthy(self, tmp_path, monkeypatch):
        pipelines, session, root = self.project(tmp_path)
        monkeypatch.setenv("SPHINX_OUT", GREEN_OUT)
        monkeypatch.setenv("SPHINX_ERR", "")
        monkeypatch.setenv("SPHINX_EXIT", "0")
        record = pipelines.run_tests(session.id, cwd=root)
        assert record["passed"] and record["executed"] == 2 and record["hazards"] == []


@pytest.mark.skipif(
    not os.environ.get("INVARIANT_TEST_SPHINX_BUILD"),
    reason="set INVARIANT_TEST_SPHINX_BUILD to a sphinx-build to run a real build",
)
class TestARealBuild:
    """ADR-015 increment 2's proof on a toy tree: a broken reference fails the
    test, the fix passes it."""

    def test_a_broken_reference_fails_and_its_fix_passes(self, tmp_path):
        sphinx_build = os.environ["INVARIANT_TEST_SPHINX_BUILD"]
        root = tmp_path / "proj"
        test = defaults_for("sphinx")["commands"]["test"].replace(
            "sphinx-build", sphinx_build
        )
        manifest = git_project(
            root,
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["sphinx"]},
                "vars": {"srcdir": "manual"},
                "commands": {"test": test},
            },
        )
        tree(
            root,
            {
                "manual/conf.py": "project = 'toy'\n",
                "manual/index.rst": "Toy\n===\n\nSee :doc:`other` and :doc:`missing`.\n\n"
                ".. toctree::\n\n   other\n",
                "manual/other.rst": "Other\n=====\n",
            },
        )
        session = SessionDomain(manifest).start("x")
        pipelines = PipelineDomain(manifest)
        pipelines.start(session.id, task="t")
        red = pipelines.run_tests(session.id, cwd=root)
        assert not red["passed"] and red["failed"] == 1
        assert "manual/index.rst:4 [ref.doc] unknown document: 'missing'" in red["note"]
        (root / "manual" / "index.rst").write_text(
            "Toy\n===\n\nSee :doc:`other`.\n\n.. toctree::\n\n   other\n", encoding="utf-8"
        )
        green = pipelines.run_tests(session.id, cwd=root)
        assert green["passed"] and green["executed"] == 2 and green["hazards"] == []
        assert not list(root.glob("manual/_build"))
