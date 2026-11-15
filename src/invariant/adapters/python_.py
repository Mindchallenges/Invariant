"""Python adapter."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, ClassVar

import tomllib

from .base import Adapter, Hazard, Reference, TestResult, masked


class PythonAdapter(Adapter):
    id = "python"

    manifest_defaults: ClassVar[dict[str, Any]] = {
        "commands": {
            "build": None,
            "test": "pytest",
            "test_one": "pytest {selector}",
            "lint": "ruff check .",
            "coverage": "pytest --cov --cov-report=term-missing",
            "coverage_one": "pytest --cov --cov-report=term-missing {selector}",
        },
        "docs": {"format": "markdown", "diagrams": "mermaid"},
    }

    def detect(self, root: Path) -> bool:
        return (root / "pyproject.toml").is_file() or (root / "setup.py").is_file()

    guideline_engines: ClassVar[frozenset[str]] = frozenset({"mask", "imports"})

    def source_files(self, changed: list[Path]) -> list[Path]:
        return [p for p in changed if p.suffix == ".py"]

    def lexical_mask(self, relative: Path, text: str, keep: str) -> str | None:
        """Through ``tokenize``, so the regions are Python's own.

        An f-string or a t-string is masked whole as a string, replacement
        fields included, on every interpreter from 3.10 (DN-10). ``None`` for
        a file that is not ``.py`` and for text that does not tokenize.
        """
        if Path(relative).suffix != ".py":
            return None
        tokens = _python_tokens(text)
        if tokens is None:
            return None
        return masked(text, _python_regions(text, tokens), keep)

    def references(self, path: Path, relative: Path, root: Path) -> list[Reference] | None:
        """Every project file the module imports, read through ``ast`` (DN-14).

        Every import statement counts, wherever it sits: at module level, in
        a function, under ``if TYPE_CHECKING:`` or in a ``try``. A relative
        import resolves by the file's package. An absolute one resolves
        against the roots the tree implies, the first ancestor of the file
        with no ``__init__.py`` and then the project root, and is the
        project's when its first segment names a project package there. A
        project import that does not resolve to a file makes the whole file
        unanswerable, and so do a file that is not ``.py``, one that cannot be
        read as UTF-8, and a syntax error.
        """
        relative = Path(relative)
        if relative.suffix != ".py":
            return None
        try:
            text = Path(path).read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
            return None
        return _python_references(tree, text, relative.parent.parts, Path(root))

    def executable_text(self, relative: Path, source: str) -> str | None:
        """Python's tokens with comments, blank lines and layout removed (DN-10).

        Docstrings are kept: a docstring is a string a coverage run executes
        as an expression, and keeping it reduces too little, never too much.
        ``None`` for a file that is not ``.py`` and for text that does not
        tokenize, so a truncated reduction never compares equal.
        """
        if Path(relative).suffix != ".py":
            return None
        tokens = _python_tokens(source)
        if tokens is None:
            return None
        return _python_executable_text(source, tokens)

    def owns_selector(self, selector: str) -> bool:
        """A pytest node id names its file before the first ``::``."""
        return super().owns_selector(selector.split("::", 1)[0])

    def join_selectors(self, selectors) -> str | None:
        """pytest takes node ids positionally, so several are just several.

        This is the ecosystem fact the base class declines to guess at: the
        selectors go where the command's arguments go, with no repeated flag
        and no separator of its own.
        """
        return " ".join(selectors)

    def test_scope_of(self, extra_tokens: Sequence[str]) -> str | None:
        """pytest's whole-suite and narrowing arguments, and nothing guessed.

        A positional is a path or node id, and ``-k``, ``-m``, ``--lf``,
        ``--ff`` and ``--deselect`` select a subset: any of them is
        ``"scoped"``. Only options known not to narrow the collected set —
        verbosity, ``-x``, ``-s``, traceback style, ``-p <plugin>``, the
        ``--cov`` family, ``--no-header`` — leave a run ``"full"``. Any other
        option is ``None``: pytest plugins add options freely, and a guess
        that one does not narrow is how a legitimate scoped run gets refused
        (ADR-010 D5).
        """
        tokens = list(extra_tokens)
        doubt = False
        # A bare word straight after an option this reader does not know may
        # be that option's VALUE (`-n 4`) rather than a path, so it proves
        # nothing either way.
        after_unknown = False
        index = 0
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if not token.startswith("-"):
                if after_unknown:
                    after_unknown = False
                    continue
                return "scoped"
            after_unknown = False
            name = token.split("=", 1)[0]
            if name in _PYTEST_NARROWING:
                return "scoped"
            if token in _PYTEST_TAKES_VALUE:
                if index >= len(tokens):
                    doubt = True
                index += 1
                continue
            if _pytest_keeps_the_whole_suite(token):
                continue
            doubt = True
            after_unknown = "=" not in token
        return None if doubt else "full"

    def failing_test_ids(self, stdout: str, stderr: str, bundle=None) -> list[str]:
        """pytest's short test summary: ``FAILED <nodeid>`` and ``ERROR <nodeid>``.

        pytest prints the summary by default, one line per failing or erroring
        node id, with `` - <message>`` after it when there is one. A node id is
        exactly what ``pytest {selector}`` takes back. An ``ERROR`` line naming
        only a file is a collection error, and the file is its selector. Only
        lines OPENING with the word count, so ``-v``'s ``<nodeid> FAILED``
        progress lines are not read twice. Order kept, duplicates dropped.
        """
        found: dict[str, None] = {}
        for match in _SUMMARY_LINE.finditer(f"{stdout}\n{stderr}"):
            found.setdefault(match.group("nodeid").strip(), None)
        return list(found)

    def measured_scope(self, root: Path) -> list[Path] | None:
        """``[tool.coverage.run] source``, resolved to directories.

        Only ``pyproject.toml`` is read. coverage.py also accepts ``.coveragerc``
        and ``setup.cfg``, and a project using one of those gets ``None`` —
        "cannot tell" — which relaxes nothing and leaves the pre-existing
        behaviour exactly as it was. Guessing at a config file this adapter has
        not read would be the understating direction, which is the one that
        excuses a real file.

        Every bail below is that same choice. A ``source`` entry that is not a
        directory in this checkout may be an installed package name — coverage
        accepts both spellings — and resolving it would be a guess. A
        ``source_pkgs`` alongside WIDENS the true scope past the directories
        named here, so reporting only these would understate it.
        """
        config = root / "pyproject.toml"
        try:
            data = tomllib.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # ValueError covers tomllib.TOMLDecodeError. An unreadable or
            # unparseable config is a thing not known, not a scope of nothing.
            return None
        run = data.get("tool", {}).get("coverage", {}).get("run", {})
        if not isinstance(run, dict) or run.get("source_pkgs"):
            return None
        declared = run.get("source")
        if not isinstance(declared, list) or not declared:
            return None
        resolved: list[Path] = []
        for entry in declared:
            candidate = root / str(entry)
            if not candidate.is_dir():
                return None
            resolved.append(candidate)
        return resolved

    def source_areas(
        self, root: Path, tracked: Sequence[Path] | None = None
    ) -> list[Path] | None:
        """The subpackages of each top package (ADR-016 D2).

        The top packages are the measured scope's roots that are packages,
        else the packages directly under ``src/`` and the project root. A
        subpackage is a directory holding ``__init__.py``, so package data
        and caches are not areas. With none found, the base answer.
        """
        scope = self.measured_scope(root) or []
        tops = [base for base in scope if (base / "__init__.py").is_file()]
        if not tops:
            tops = [
                child
                for base in (root / "src", root)
                if base.is_dir()
                for child in sorted(base.iterdir())
                if (child / "__init__.py").is_file()
            ]
        areas = [
            child
            for top in tops
            for child in sorted(top.iterdir())
            if (child / "__init__.py").is_file()
        ]
        return areas or super().source_areas(root, tracked)

    def is_test_file(self, path: Path) -> bool | None:
        """pytest's own discovery conventions, and nothing looser.

        ``test_*.py`` / ``*_test.py`` are what pytest collects by default, a
        ``tests`` or ``test`` directory component is the near-universal layout,
        and ``conftest.py`` is pytest's own fixture module. Matching on the
        substring "test" anywhere would claim ``latest.py``, ``contest.py`` and
        ``testing_utils.py`` — production files that would then be set aside
        unmeasured, which is the failure direction the base docstring forbids.
        """
        if path.suffix != ".py":
            return None
        if any(part in {"tests", "test"} for part in path.parts[:-1]):
            return True
        name = path.name
        return name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py")

    def hazards(self) -> list[Hazard]:
        return [
            Hazard(
                id="pytest.no_tests_collected_exits_five",
                summary="a selector that matches nothing exits 5, not 1",
                detail=(
                    "A path or -k filter that collects zero tests is not a "
                    "failure and is easy to read as 'nothing broke'. A green run "
                    "is only meaningful alongside a non-zero collected count."
                ),
                detect=r"no tests ran|collected 0 items",
            ),
            Hazard(
                id="pytest.import_error_masquerades_as_collection_error",
                summary="a collection error can hide an entire suite",
                detail=(
                    "One unimportable module removes every test in it from the "
                    "run while the remaining suites still report green."
                ),
                detect=r"ERROR collecting|ImportError while importing test module",
            ),
        ]

    def parse_test_output(self, stdout: str, stderr: str, code: int) -> TestResult:
        combined = f"{stdout}\n{stderr}"
        live = [h for h in self.hazards() if h.matches(combined)]
        passed = _first_int(r"(\d+) passed", combined)
        failed = _first_int(r"(\d+) failed", combined)
        # Kept apart from the two above by the pattern itself: in "1 xfailed"
        # the "x" sits between the count and the word, so `(\d+) failed`
        # cannot read an expected failure as a failure.
        xfailed = _first_int(r"(\d+) xfailed", combined)
        xpassed = _first_int(r"(\d+) xpassed", combined)
        note = ""
        # An expected failure and an unexpected pass both ran their body.
        total = (passed or 0) + (failed or 0) + (xfailed or 0) + (xpassed or 0) or None
        if code == 5 or re.search(r"collected 0 items|no tests ran", combined):
            note = "zero tests were collected — the selector matched nothing"
            # Zero is what pytest reported, not a count this parser missed:
            # every reader that refuses a run that executed nothing asks
            # `total == 0`, and `None` ("cannot count") slipped past them.
            total = total or 0
        return TestResult(
            passed=code == 0,
            exit_code=code,
            total=total,
            failed=failed,
            expected_failures=xfailed,
            hazards=live,
            note=note,
        )

    def expected_failures_are_strict(
        self, root: Path, selectors: Sequence[str]
    ) -> bool | None:
        """Read from the selected files, because pytest's output cannot say.

        A strict and a non-strict ``xfail`` that fail print the same ``x``, the
        same ``XFAIL`` summary line and the same ``N xfailed``; strictness is
        visible in the output only when the test PASSES. So the answer comes
        from the source, and only one form counts: a ``mark.xfail(...)`` call
        with a literal ``strict=True``. Anything else that could produce an
        expected failure in the selected files — ``strict=False``, no keyword
        (the ini ``xfail_strict`` default is not read), a non-literal value, an
        uncalled mark, the imperative ``pytest.xfail()`` — answers ``False``,
        because one loose marker beside a strict one leaves the reader unable
        to say which of them produced the count.

        ``None`` for everything this reading cannot see: no selector, a
        selector that is not a ``.py`` file or a node id in one (a directory,
        a ``-k`` filter), a file that cannot be read or parsed, and selected
        files carrying no expected-failure marker at all — the count then came
        from somewhere else, a ``conftest.py`` hook, say.
        """
        selected = [s for s in selectors if s]
        if not selected:
            return None
        found = False
        for selector in selected:
            path = Path(selector.split("::", 1)[0])
            if not path.is_absolute():
                path = root / path
            if path.suffix != ".py" or not path.is_file():
                return None
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, ValueError):
                return None
            verdict = _xfail_markers_are_strict(tree)
            if verdict is False:
                return False
            found = found or verdict is True
        return True if found else None

    def parse_coverage(self, raw: str) -> dict[str, float]:
        """Each ``.py`` row's percentage, without counting columns.

        The previous form required exactly two numeric columns between the
        name and the percentage, which is coverage.py's NON-branch table
        (``Name Stmts Miss Cover Missing``). ``[tool.coverage.run] branch =
        true`` emits four (``Name Stmts Miss Branch BrPart Cover Missing``),
        so on any branch-measuring project nothing matched, ``{}`` came back,
        and the coverage gate reported "no coverage was recorded" forever —
        a measurement that could never be taken reading as one nobody had
        taken yet.

        Widening the count to "two or four" would only postpone the next
        break, because the column layout is owned by coverage.py and not by
        this adapter. So the anchor is the ``.py`` extension and the answer
        is the FIRST percentage on that line, whatever sits between them —
        the same shape ``swift_xcode.parse_coverage`` uses, and the reason a
        table gaining or losing columns cannot break that one.

        What sits between is any number of whitespace-separated integer cells,
        zero included — which is a description of a table ROW, not a count of
        its columns. Accepting *anything* between the name and the first ``%``
        would have been simpler and is wrong in the more dangerous direction:
        ``pytest -v`` prints ``tests/test_app.py::test_x PASSED  [ 50%]`` for
        every test, and a parser that reads a progress percentage as a
        measurement writes a number nobody measured into the durable pipeline
        record. Per ``Hazard.can_overstate``, a reading bent HIGH passes
        unmeasured code silently, so it is the one the pattern must refuse.
        """
        out: dict[str, float] = {}
        for match in re.finditer(
            r"^\s*(?P<file>[\w./+-]+\.py)(?:\s+\d+)*\s+(?P<pct>\d+(?:\.\d+)?)%",
            raw,
            re.MULTILINE,
        ):
            out[match.group("file")] = float(match.group("pct"))
        return out


#: One line of pytest's short test summary, its node id up to the message.
#: ``ERROR collecting <path>`` is the heading of a collection error's report,
#: not a summary line; its summary line names the path on its own.
_SUMMARY_LINE = re.compile(
    r"^(?:FAILED|ERROR) (?!collecting )(?P<nodeid>\S.*?)(?: - .*)?$", re.MULTILINE
)

#: Options that select a subset of the collected tests.
_PYTEST_NARROWING = frozenset({"-k", "-m", "--lf", "--last-failed", "--ff", "--deselect"})

#: Options known not to narrow that take their value as the NEXT token.
_PYTEST_TAKES_VALUE = frozenset({"-p"})


def _pytest_keeps_the_whole_suite(token: str) -> bool:
    """A pytest option known to leave the collected set untouched."""
    if re.fullmatch(r"-[qvxs]+", token):
        return True
    if token.startswith(("--tb=", "-p")) and token not in ("--tb=", "-p"):
        return True
    if token == "--cov" or token.startswith(("--cov=", "--cov-")):
        return True
    return token in {"--no-header", "--quiet", "--verbose", "--exitfirst"}


def _xfail_markers_are_strict(tree: ast.AST) -> bool | None:
    """``True`` all strict, ``False`` any not, ``None`` none present."""
    calls = {id(node.func): node for node in ast.walk(tree) if isinstance(node, ast.Call)}
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "xfail":
            # `from pytest import xfail` — the imperative form, never strict.
            return False
        if not (isinstance(node, ast.Attribute) and node.attr == "xfail"):
            continue
        found = True
        owner = node.value
        is_mark = (isinstance(owner, ast.Attribute) and owner.attr == "mark") or (
            isinstance(owner, ast.Name) and owner.id == "mark"
        )
        call = calls.get(id(node))
        if not is_mark or call is None or not _literal_true(call, "strict"):
            return False
    return True if found else None


def _literal_true(call: ast.Call, keyword: str) -> bool:
    return any(
        kw.arg == keyword
        and isinstance(kw.value, ast.Constant)
        and kw.value.value is True
        for kw in call.keywords
    )


def _first_int(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


# -- the lexer the mask and the reducer share (IDEA_031 A3.1, DN-10) ----------

#: Tokens that open and close an f-string or a t-string on the interpreters
#: that split one into parts (f-strings from 3.12, t-strings from 3.14).
#: Missing names are simply absent, so an older interpreter matches nothing.
_LITERAL_OPENERS = frozenset(
    getattr(tokenize, name) for name in ("FSTRING_START", "TSTRING_START")
    if hasattr(tokenize, name)
)
_LITERAL_CLOSERS = frozenset(
    getattr(tokenize, name) for name in ("FSTRING_END", "TSTRING_END")
    if hasattr(tokenize, name)
)

#: The prefixes a t-string can carry. Before 3.14 the tokenizer reads
#: ``t"..."`` as a name glued to a string.
_TSTRING_PREFIXES = frozenset({"t", "rt", "tr"})

_REGION_OF = {tokenize.STRING: "strings", tokenize.COMMENT: "comments"}

#: Tokens whose own text is layout: the structure they mark is kept, the
#: whitespace or line end that spells it is not.
_LAYOUT = frozenset({tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE, tokenize.ENDMARKER})


def _python_tokens(text: str) -> list[tokenize.TokenInfo] | None:
    """Every token of ``text``, or ``None`` when it does not tokenize to the end."""
    try:
        return list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return None


def _lexemes(
    text: str, tokens: Sequence[tokenize.TokenInfo]
) -> Iterator[tuple[int, int, int, str]]:
    """``(type, start, end, string)`` per lexeme, with offsets into ``text``.

    One lexeme per token, except that every f-string and t-string is ONE
    ``STRING`` lexeme spelled as its source. The interpreters disagree about
    them: 3.10 and 3.11 read an f-string as one string token, 3.12 splits it
    into parts and reads the replacement fields as code, and a t-string is a
    name glued to a string before 3.14 and split into parts from it. Merging
    gives every interpreter the same lexemes, so a verdict does not depend on
    which one ran (DN-10).
    """
    starts = [0, *(i + 1 for i, ch in enumerate(text) if ch == "\n")]

    def at(position: tuple[int, int]) -> int:
        row, col = position
        return min(starts[row - 1] + col, len(text)) if row <= len(starts) else len(text)

    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type in _LITERAL_OPENERS:
            close, depth = index, 0
            while True:
                kind = tokens[close].type
                depth += (kind in _LITERAL_OPENERS) - (kind in _LITERAL_CLOSERS)
                if not depth:
                    break
                close += 1
            start, end = at(token.start), at(tokens[close].end)
            yield tokenize.STRING, start, end, text[start:end]
            index = close + 1
            continue
        glued = tokens[index + 1] if index + 1 < len(tokens) else None
        if (
            token.type == tokenize.NAME
            and token.string.lower() in _TSTRING_PREFIXES
            and glued is not None
            and glued.type == tokenize.STRING
            and glued.start == token.end
        ):
            start, end = at(token.start), at(glued.end)
            yield tokenize.STRING, start, end, text[start:end]
            index += 2
            continue
        yield token.type, at(token.start), at(token.end), token.string
        index += 1


def _python_regions(
    text: str, tokens: Sequence[tokenize.TokenInfo]
) -> list[tuple[str, int, int]]:
    """``text`` cut into code, comment and string regions that cover it."""
    regions: list[tuple[str, int, int]] = []
    position = 0
    for kind, start, end, _ in _lexemes(text, tokens):
        region = _REGION_OF.get(kind)
        if region is None:
            continue
        if start > position:
            regions.append(("code", position, start))
        regions.append((region, start, end))
        position = end
    if position < len(text):
        regions.append(("code", position, len(text)))
    return regions


def _python_executable_text(text: str, tokens: Sequence[tokenize.TokenInfo]) -> str:
    """One line per lexeme a coverage run could count: its type and its text.

    Comments and blank lines go, and so does every byte of whitespace between
    tokens, because ``tokenize`` does not keep it. Indentation is kept as the
    ``INDENT`` and ``DEDENT`` structure it spells, so re-indenting a block
    whole is no change and moving a statement out of its block is one.
    Docstrings are string tokens and stay, which reduces too little: a
    docstring-only edit is held to the floor, the safe direction.
    """
    lines = []
    for kind, _, _, string in _lexemes(text, tokens):
        if kind in (tokenize.COMMENT, tokenize.NL):
            continue
        spelled = "" if kind in _LAYOUT else string
        lines.append(f"{tokenize.tok_name[kind]} {spelled!r}")
    return "\n".join(lines)


# -- import references, the imports engine (IDEA_031 A3.2, DN-14) ------------------

#: A project-relative location, as path parts: ``()`` is the project root.
_Parts = tuple[str, ...]

#: The line breaks ``ast`` counts lines by.
_LINE_BREAK = re.compile(r"\r\n|\r|\n")


def _python_references(
    tree: ast.AST, text: str, folder: _Parts, root: Path
) -> list[Reference] | None:
    """One :class:`Reference` per project file each import names, in order.

    ``None`` as soon as one project import cannot be resolved: a file whose
    references are known in part would let a boundary rule pass on the part
    it was shown.
    """
    lines = _LINE_BREAK.split(text)
    roots = _import_roots(root, folder)
    statements = sorted(
        (n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))),
        key=lambda n: (n.lineno, n.col_offset),
    )
    found: list[Reference] = []
    for node in statements:
        if isinstance(node, ast.Import):
            resolved = _resolve_import(node, root, roots)
        else:
            resolved = _resolve_from(node, root, folder, roots)
        if resolved is None:
            return None
        line = lines[node.lineno - 1]
        for target in dict.fromkeys(resolved):
            found.append(Reference("/".join(target), node.lineno, line))
    return found


def _import_roots(root: Path, folder: _Parts) -> list[_Parts]:
    """The roots an absolute import resolves against, in the order tried.

    The first ancestor of the file with no ``__init__.py`` is where its
    top-level package sits, as a src layout's ``src`` is; the project root
    follows it.
    """
    parts = folder
    while parts and (root.joinpath(*parts) / "__init__.py").is_file():
        parts = parts[:-1]
    return [parts, ()] if parts else [()]


def _owns(root: Path, base: _Parts, name: str) -> bool:
    """Is ``name`` a project module or package directly under ``base``?

    A regular package, a module, or a directory holding Python directly, a
    namespace package. A directory with no Python in it is not one, so an
    import that merely shares its name stays third-party.
    """
    folder = root.joinpath(*base, name)
    if (folder / "__init__.py").is_file() or root.joinpath(*base, f"{name}.py").is_file():
        return True
    return folder.is_dir() and any(folder.glob("*.py"))


def _walk(
    root: Path, base: _Parts, segments: list[str]
) -> tuple[_Parts | None, _Parts | None] | None:
    """Resolve dotted ``segments`` under the package directory ``base``.

    The answer is ``(file, package)``: the file the name loads (a module, a
    package's ``__init__.py``, or ``None`` for a namespace package) and the
    directory a submodule of it would sit in (``None`` for a module). ``None``
    when a segment names nothing.
    """
    init = base + ("__init__.py",)
    file: _Parts | None = init if root.joinpath(*init).is_file() else None
    package: _Parts | None = base
    for segment in segments:
        if package is None:
            return None
        here = package + (segment,)
        if root.joinpath(*here, "__init__.py").is_file():
            file, package = here + ("__init__.py",), here
        elif root.joinpath(*package, f"{segment}.py").is_file():
            file, package = package + (f"{segment}.py",), None
        elif root.joinpath(*here).is_dir():
            file, package = None, here
        else:
            return None
    return file, package


def _root_owning(root: Path, roots: list[_Parts], name: str) -> _Parts | None:
    """The first root where ``name`` is a project package, or ``None``."""
    return next((base for base in roots if _owns(root, base, name)), None)


def _resolve_import(node: ast.Import, root: Path, roots: list[_Parts]) -> list[_Parts] | None:
    """``import a.b.c``: the file of the module named in full, per alias."""
    targets: list[_Parts] = []
    for alias in node.names:
        segments = alias.name.split(".")
        base = _root_owning(root, roots, segments[0])
        if base is None:
            continue
        module = _walk(root, base, segments)
        if module is None or module[0] is None:
            return None
        targets.append(module[0])
    return targets


def _resolve_from(
    node: ast.ImportFrom, root: Path, folder: _Parts, roots: list[_Parts]
) -> list[_Parts] | None:
    """``from m import n``: a submodule ``n``'s file, else ``m``'s own file."""
    segments = node.module.split(".") if node.module else []
    if node.level:
        base = folder
        for _ in range(node.level - 1):
            if not base:
                return None
            base = base[:-1]
    else:
        owner = _root_owning(root, roots, segments[0])
        if owner is None:
            return []
        base = owner
    module = _walk(root, base, segments)
    if module is None:
        return None
    file, package = module
    targets: list[_Parts] = []
    for alias in node.names:
        target = None if package is None else _submodule(root, package, alias.name)
        target = target or file
        if target is None:
            return None
        targets.append(target)
    return targets


def _submodule(root: Path, package: _Parts, name: str) -> _Parts | None:
    """The file of submodule ``name`` of ``package``, or ``None``."""
    if root.joinpath(*package, name, "__init__.py").is_file():
        return package + (name, "__init__.py")
    if root.joinpath(*package, f"{name}.py").is_file():
        return package + (f"{name}.py",)
    return None
