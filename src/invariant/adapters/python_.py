"""Python adapter."""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from pathlib import Path

import tomllib

from .base import Adapter, Hazard, TestResult


class PythonAdapter(Adapter):
    id = "python"

    manifest_defaults = {
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

    def source_files(self, changed: list[Path]) -> list[Path]:
        return [p for p in changed if p.suffix == ".py"]

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
