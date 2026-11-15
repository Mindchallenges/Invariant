"""Import references, the ``imports`` engine's question (IDEA_031 A3 increment 2).

ADR-012 D4 and the check-kind amendment: a ``boundary`` rule asks which
project files a changed file references. The adapter that owns the file
answers ``references(path, relative, root)`` with one ``Reference`` per
import: the target as a project-relative file, the line, and the line's text,
which a finding's fingerprint is taken from (D6). An import that resolves
outside the project is outside the kind and is not reported. ``None`` means
the file cannot be answered for, and a boundary rule then fails rather than
passes.

Python resolves through ``ast`` (DN-14). Relative imports resolve by the
file's package. Absolute imports resolve against the roots the tree implies:
the first ancestor with no ``__init__.py``, then the project root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from invariant.adapters import get_adapter
from invariant.adapters.base import Adapter, Reference
from invariant.core.rules import _offered_engines, _references

PY = get_adapter("python")


def write(root: Path, relative: str, text: str = "") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def package(root: Path, *dirs: str) -> None:
    """Each directory a regular package, with an empty ``__init__.py``."""
    for directory in dirs:
        write(root, f"{directory}/__init__.py")


def refs(root: Path, relative: str) -> list[Reference] | None:
    return PY.references(root / relative, Path(relative), root)


def targets(root: Path, relative: str) -> list[str]:
    answer = refs(root, relative)
    assert answer is not None
    return [ref.target for ref in answer]


@pytest.fixture
def src(tmp_path: Path) -> Path:
    """A src layout: ``src`` has no ``__init__.py``, so it is the import root."""
    package(tmp_path, "src/app", "src/app/core", "src/app/ui")
    write(tmp_path, "src/app/core/store.py", "X = 1\n")
    write(tmp_path, "src/app/core/rules.py", "Y = 2\n")
    write(tmp_path, "src/app/ui/view.py", "Z = 3\n")
    return tmp_path


# -- the seam ------------------------------------------------------------------------


class TestTheSeam:
    def test_the_base_answers_none(self, tmp_path):
        """None is unanswerable: a boundary rule then fails, never passes."""
        path = write(tmp_path, "a.py", "import os\n")
        assert Adapter().references(path, Path("a.py"), tmp_path) is None

    @pytest.mark.parametrize("ecosystem", ["swift_xcode", "node", "generic"])
    def test_only_python_answers(self, tmp_path, ecosystem):
        """Swift boundaries need symbols (ruling 3); node and generic gain
        nothing (DN-9)."""
        path = write(tmp_path, "a.py", "import b\n")
        write(tmp_path, "b.py")
        adapter = get_adapter(ecosystem)
        assert "imports" not in adapter.guideline_engines
        assert adapter.references(path, Path("a.py"), tmp_path) is None

    def test_python_offers_imports_and_mask(self):
        assert PY.guideline_engines == frozenset({"mask", "imports"})

    def test_a_file_outside_python_answers_none(self, tmp_path):
        path = write(tmp_path, "a.swift", "import b\n")
        write(tmp_path, "b.py")
        assert PY.references(path, Path("a.swift"), tmp_path) is None

    def test_an_unreadable_file_answers_none(self, tmp_path):
        assert PY.references(tmp_path / "gone.py", Path("gone.py"), tmp_path) is None

    def test_a_file_that_is_not_utf8_answers_none(self, tmp_path):
        path = tmp_path / "a.py"
        path.write_bytes(b"x = '\xff'\n")
        assert PY.references(path, Path("a.py"), tmp_path) is None

    def test_a_syntax_error_answers_none(self, tmp_path):
        write(tmp_path, "b.py")
        path = write(tmp_path, "a.py", "import b\ndef broken(:\n")
        assert PY.references(path, Path("a.py"), tmp_path) is None

    def test_a_reference_carries_target_line_and_text(self, src):
        write(src, "src/app/ui/screen.py", '"""Doc."""\n\nfrom app.core import store  # db\n')
        assert refs(src, "src/app/ui/screen.py") == [
            Reference(
                target="src/app/core/store.py",
                line=3,
                text="from app.core import store  # db",
            )
        ]

    def test_no_imports_is_an_empty_answer_not_none(self, tmp_path):
        write(tmp_path, "a.py", "x = 1\n")
        assert refs(tmp_path, "a.py") == []


# -- relative imports resolve by the file's package ---------------------------------------


class TestRelative:
    def test_a_sibling_module(self, src):
        write(src, "src/app/core/engine.py", "from .store import X\n")
        assert targets(src, "src/app/core/engine.py") == ["src/app/core/store.py"]

    def test_from_dot_import_a_submodule(self, src):
        write(src, "src/app/core/engine.py", "from . import rules, store\n")
        assert targets(src, "src/app/core/engine.py") == [
            "src/app/core/rules.py",
            "src/app/core/store.py",
        ]

    def test_from_dot_import_a_name_is_the_package_init(self, src):
        write(src, "src/app/core/engine.py", "from . import helper\n")
        assert targets(src, "src/app/core/engine.py") == ["src/app/core/__init__.py"]

    def test_two_dots_climb_one_package(self, src):
        write(src, "src/app/core/engine.py", "from ..ui.view import Z\n")
        assert targets(src, "src/app/core/engine.py") == ["src/app/ui/view.py"]

    def test_a_package_init_resolves_relative_to_its_own_package(self, src):
        write(src, "src/app/core/__init__.py", "from .rules import Y\n")
        assert targets(src, "src/app/core/__init__.py") == ["src/app/core/rules.py"]

    def test_a_relative_module_that_is_not_there_is_unanswerable(self, src):
        write(src, "src/app/core/engine.py", "from .missing import X\n")
        assert refs(src, "src/app/core/engine.py") is None

    def test_climbing_out_of_the_project_is_unanswerable(self, tmp_path):
        write(tmp_path, "a.py", "from .. import x\n")
        assert refs(tmp_path, "a.py") is None


# -- absolute imports resolve against the implied roots --------------------------------------


class TestAbsolute:
    def test_src_layout_resolves_against_the_first_ancestor_without_init(self, src):
        write(src, "src/app/ui/screen.py", "import app.core.store\n")
        assert targets(src, "src/app/ui/screen.py") == ["src/app/core/store.py"]

    def test_the_project_root_is_a_root_too(self, tmp_path):
        package(tmp_path, "lib")
        write(tmp_path, "lib/util.py")
        write(tmp_path, "scripts/run.py", "import lib.util\n")
        assert targets(tmp_path, "scripts/run.py") == ["lib/util.py"]

    def test_a_top_level_script_imports_its_neighbour(self, tmp_path):
        write(tmp_path, "tools/helper.py")
        write(tmp_path, "tools/run.py", "import helper\n")
        assert targets(tmp_path, "tools/run.py") == ["tools/helper.py"]

    def test_import_a_package_is_its_init(self, src):
        write(src, "src/app/ui/screen.py", "import app.core\n")
        assert targets(src, "src/app/ui/screen.py") == ["src/app/core/__init__.py"]

    def test_from_a_package_import_a_submodule(self, src):
        write(src, "src/app/ui/screen.py", "from app.core import rules\n")
        assert targets(src, "src/app/ui/screen.py") == ["src/app/core/rules.py"]

    def test_from_a_package_import_a_name_is_its_init(self, src):
        write(src, "src/app/ui/screen.py", "from app.core import Thing\n")
        assert targets(src, "src/app/ui/screen.py") == ["src/app/core/__init__.py"]

    def test_from_a_module_import_a_name_is_the_module(self, src):
        write(src, "src/app/ui/screen.py", "from app.core.store import X\n")
        assert targets(src, "src/app/ui/screen.py") == ["src/app/core/store.py"]

    def test_star_import_is_the_module(self, src):
        write(src, "src/app/ui/screen.py", "from app.core.store import *\n")
        assert targets(src, "src/app/ui/screen.py") == ["src/app/core/store.py"]

    def test_a_subpackage_named_in_from_is_its_init(self, src):
        write(src, "src/app/ui/screen.py", "from app import core\n")
        assert targets(src, "src/app/ui/screen.py") == ["src/app/core/__init__.py"]


# -- what is outside the kind, and what cannot be answered -----------------------------------


class TestOutsideAndUnanswerable:
    def test_an_import_outside_the_project_is_not_reported(self, src):
        write(
            src,
            "src/app/ui/screen.py",
            "from __future__ import annotations\nimport os\nimport yaml.constructor\n"
            "from collections import abc\nimport app.core.store\n",
        )
        answer = refs(src, "src/app/ui/screen.py")
        assert answer == [
            Reference("src/app/core/store.py", 5, "import app.core.store"),
        ]

    def test_a_project_name_that_does_not_resolve_is_unanswerable(self, src):
        """The first segment is a project package, so the import is the
        project's, and a name that resolves to nothing cannot be judged."""
        write(src, "src/app/ui/screen.py", "import app.core.missing\n")
        assert refs(src, "src/app/ui/screen.py") is None

    def test_a_name_past_a_module_is_unanswerable(self, src):
        write(src, "src/app/ui/screen.py", "import app.core.store.deeper\n")
        assert refs(src, "src/app/ui/screen.py") is None

    def test_from_a_missing_project_module_is_unanswerable(self, src):
        write(src, "src/app/ui/screen.py", "from app.nothing import x\n")
        assert refs(src, "src/app/ui/screen.py") is None

    def test_a_namespace_directory_counts_as_a_project_package(self, tmp_path):
        """A directory holding Python with no ``__init__.py`` is a namespace
        package, still the project's: a name under it that does not resolve
        is unanswerable, never third-party."""
        write(tmp_path, "ns/mod.py")
        write(tmp_path, "a.py", "import ns.mod\nimport ns.gone\n")
        assert refs(tmp_path, "a.py") is None

    def test_a_namespace_submodule_resolves(self, tmp_path):
        write(tmp_path, "ns/mod.py")
        write(tmp_path, "a.py", "import ns.mod\nfrom ns import mod\n")
        assert targets(tmp_path, "a.py") == ["ns/mod.py", "ns/mod.py"]

    def test_a_name_from_a_namespace_package_is_unanswerable(self, tmp_path):
        """No ``__init__.py`` holds the name, so there is no file to name."""
        write(tmp_path, "ns/mod.py")
        write(tmp_path, "a.py", "from ns import thing\n")
        assert refs(tmp_path, "a.py") is None

    def test_a_directory_without_python_is_not_a_project_package(self, tmp_path):
        (tmp_path / "docs").mkdir()
        write(tmp_path, "docs/readme.md", "x\n")
        write(tmp_path, "a.py", "import docs.thing\n")
        assert refs(tmp_path, "a.py") == []


# -- every import counts ---------------------------------------------------------------------


class TestEveryImportCounts:
    def test_function_level_type_checking_and_try_imports(self, src):
        write(
            src,
            "src/app/ui/screen.py",
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from app.core.store import X\n"
            "try:\n"
            "    import app.core.rules\n"
            "except ImportError:\n"
            "    pass\n"
            "def late():\n"
            "    from app.ui import view\n"
            "    return view\n",
        )
        assert refs(src, "src/app/ui/screen.py") == [
            Reference("src/app/core/store.py", 3, "    from app.core.store import X"),
            Reference("src/app/core/rules.py", 5, "    import app.core.rules"),
            Reference("src/app/ui/view.py", 9, "    from app.ui import view"),
        ]

    def test_a_multiline_import_reports_its_first_line(self, src):
        write(src, "src/app/ui/screen.py", "from app.core import (\n    rules,\n    store,\n)\n")
        answer = refs(src, "src/app/ui/screen.py")
        assert answer == [
            Reference("src/app/core/rules.py", 1, "from app.core import ("),
            Reference("src/app/core/store.py", 1, "from app.core import ("),
        ]

    def test_one_statement_naming_a_file_twice_reports_it_once(self, src):
        write(src, "src/app/ui/screen.py", "from app.core import A, B\n")
        assert targets(src, "src/app/ui/screen.py") == ["src/app/core/__init__.py"]


# -- stacking --------------------------------------------------------------------------------


class TestStacking:
    def test_the_first_adapter_with_an_answer_wins(self, src):
        write(src, "src/app/ui/screen.py", "import app.core.store\n")
        stack = [get_adapter("swift_xcode"), get_adapter("python")]
        path = src / "src/app/ui/screen.py"
        answer = _references(stack, path, Path("src/app/ui/screen.py"), src)
        assert answer is not None
        assert [ref.target for ref in answer] == ["src/app/core/store.py"]

    def test_no_adapter_answering_is_none(self, tmp_path):
        path = write(tmp_path, "a.py", "import b\n")
        stack = [get_adapter("swift_xcode"), get_adapter("node")]
        assert _references(stack, path, Path("a.py"), tmp_path) is None

    def test_the_offered_engines_include_imports_with_python(self):
        stack = [get_adapter("node"), get_adapter("python")]
        assert _offered_engines(stack) == frozenset({"mask", "imports"})
