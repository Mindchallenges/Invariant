"""Dart and Flutter adapters (ADR-019).

One ecosystem, two runners. A pure Dart package runs `dart test`, and a
package depending on the Flutter SDK runs `flutter test`. Both print the same
`package:test` reporter and both write the same lcov report, so everything
past the default commands is shared: :class:`FlutterAdapter` differs from
:class:`DartAdapter` only in what it runs and in how it is detected.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

from .base import Adapter, Hazard, TestResult

#: `docs` defaults shared by both runners, as every shipped adapter gives.
_DOCS = {"format": "markdown", "diagrams": "mermaid"}

#: Directories the Dart toolchain treats as tests rather than as the package:
#: `test/` is what `dart test` and `flutter test` run, `integration_test/` and
#: `test_driver/` are Flutter's device-test homes.
_TEST_DIRS = frozenset({"test", "integration_test", "test_driver"})


class DartAdapter(Adapter):
    """A pure Dart package: `dart test`, `dart analyze`."""

    id = "dart"

    # The coverage run writes its lcov report inside the per-run
    # {result_bundle} directory, which the pipeline clears before every run,
    # and the report leg reads it from there. A fixed `coverage/lcov.info`
    # outlives a run that failed before writing it, and the report leg would
    # then publish the previous run's numbers as this one's.
    manifest_defaults: ClassVar[dict[str, Any]] = {
        "commands": {
            "build": None,
            "test": "dart test",
            "test_one": "dart test {selector}",
            "lint": "dart analyze",
            "coverage": "dart test --coverage-path={result_bundle}/lcov.info",
            "coverage_one": "dart test --coverage-path={result_bundle}/lcov.info {selector}",
            "coverage_report": "cat {result_bundle}/lcov.info",
        },
        "docs": _DOCS,
    }

    def detect(self, root: Path) -> bool:
        return (root / "pubspec.yaml").is_file() and not _uses_flutter(root)

    def source_files(self, changed: list[Path]) -> list[Path]:
        """Every `.dart` file, generated ones included.

        A `*.g.dart` or `*.freezed.dart` file is still code the reporter
        measures when a test loads it, so it is not set aside here. A
        generator that marks its output `// coverage:ignore-file` (freezed
        does) gets no row, and :meth:`carries_executable_code` explains that
        absence. Any other generated file is the project's to exempt, through
        `methodology.gates.coverage_exclude`, where the exemption is named.
        """
        return [p for p in changed if p.suffix == ".dart"]

    def is_test_file(self, path: Path) -> bool | None:
        """The layout `dart test` and `flutter test` themselves enforce.

        A file under `test/`, `integration_test/` or `test_driver/` is a test
        or its support code, and a `*_test.dart` file is what the runner
        collects. Everything under `lib/` is the package's library, whatever
        it is called, because that is what other code imports: the first of
        `lib` or a test directory along the path decides. Matching "test"
        anywhere would claim `lib/src/latest.dart`.
        """
        if path.suffix != ".dart":
            return None
        for part in path.parts[:-1]:
            if part == "lib":
                return False
            if part in _TEST_DIRS:
                return True
        return path.name.endswith("_test.dart")

    def join_selectors(self, selectors: Sequence[str]) -> str | None:
        """`dart test` and `flutter test` take test paths positionally."""
        return " ".join(selectors)

    def carries_executable_code(
        self, path: Path, relative: Path | None = None
    ) -> bool | None:
        """``False`` for a `.dart` file no lcov record can exist for.

        Two shapes, both observed against `dart test --coverage-path`:

        - a file marked `// coverage:ignore-file`, which the reporter drops;
        - a file holding only directives (`library`, `import`, `export`,
          `part`), typedefs, plain enums, and classes or mixins whose members
          are all abstract signatures. None of them compiles to a line the VM
          can hit, so the report has no record for them, and a barrel file or
          a port interface would otherwise hold a floor no test can meet.

        Anything else, and any file the reader cannot finish, is ``None``.
        The answer is read from the contents only, so ``relative`` is unused.
        """
        if path.suffix != ".dart":
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            return None
        read = _dart_code(text)
        if read is None:
            return None
        code, ignored = read
        if ignored or _declarations_are_inert(code):
            return False
        return None

    def hazards(self) -> list[Hazard]:
        return [
            Hazard(
                id="dart_test.no_tests_ran",
                summary="a path or name filter matching nothing runs no test",
                detail=(
                    "`dart test` and `flutter test` print 'No tests ran.' and "
                    "exit 79 when a selector or --name matches nothing; across "
                    "several packages one such run hides inside a green total."
                ),
                detect=r"(?m)^No tests ran\.",
            ),
        ]

    def read_run(self, stdout: str, stderr: str, code: int, bundle=None) -> TestResult:
        """The console is the only record a Dart run writes.

        The {result_bundle} directory holds the lcov report the coverage
        command wrote there, not test counts. Handing it to the base reader
        would note "a result bundle was written but could not be read" on
        every coverage run, which is false.
        """
        return self.parse_test_output(stdout, stderr, code)

    def parse_test_output(self, stdout: str, stderr: str, code: int) -> TestResult:
        """The reporter's closing line, `+P ~S -F: <verdict>`, per run.

        `+` counts passed tests, `~` skipped, `-` failed. One line closes each
        run, so output holding several runs (a script testing every package
        of a workspace) is summed over those lines. A progress line carries
        the same counts but is not read, because it is not a total. Executed
        tests are passed plus failed: a skipped test ran nothing.
        """
        text = _ANSI.sub("", f"{stdout}\n{stderr}").replace("\r", "\n")
        live = [h for h in self.hazards() if h.matches(text)]
        closing = list(_SUMMARY.finditer(text))
        no_tests = _NO_TESTS.search(text) is not None
        total = failed = skipped = None
        note = ""
        if closing:
            passed = sum(int(m.group("passed")) for m in closing)
            failed = sum(int(m.group("failed") or 0) for m in closing)
            skipped = sum(int(m.group("skipped") or 0) for m in closing)
            total = passed + failed
        elif no_tests:
            total = 0
        else:
            note = "no dart test summary line found in the output"
        if no_tests:
            note = "a run printed 'No tests ran.': its selector or name filter matched nothing"
        return TestResult(
            passed=code == 0,
            exit_code=code,
            total=total,
            failed=failed,
            failed_tests=failed,
            skipped=skipped,
            hazards=live,
            note=note,
        )

    def parse_coverage(self, raw: str) -> dict[str, float]:
        """Each `.dart` file's line coverage, from lcov records or summary rows.

        lcov is what the Dart toolchain writes (`SF:` ... `LF:` ... `LH:` ...
        `end_of_record`), so a project needs no script of its own to be
        measured: the default report leg prints the file. A record is read
        only when it is complete, names a `.dart` file, and counts at least
        one line with no more lines hit than found. A record with no lines
        found is the reporter saying there was nothing to measure; that is
        not a percentage, and :meth:`carries_executable_code` answers for it.

        A summary row, `<path>  <cells>  <pct>%`, is read the way the python
        adapter reads coverage.py's table: anchored on the `.dart` name at the
        start of a line, then any number of integer cells on the SAME line,
        then the first percentage. That keeps a test's progress line or a
        stack frame, which can name a `.dart` file and a percentage, from
        becoming a measurement, and it keeps the row from reaching onto the
        next line for its numbers.

        A file read twice is kept at its lower figure. Merging two reports is
        the reporter's job, and the higher figure is the one that can pass
        code nobody measured.
        """
        out: dict[str, float] = {}

        def keep(name: str, pct: float) -> None:
            out[name] = min(pct, out.get(name, pct))

        for name, pct in _lcov_records(raw):
            keep(name, pct)
        for match in _COVERAGE_ROW.finditer(raw):
            pct = float(match.group("pct"))
            if pct <= 100:
                keep(match.group("file"), pct)
        return out


class FlutterAdapter(DartAdapter):
    """A package depending on the Flutter SDK: `flutter test`, `flutter analyze`."""

    id = "flutter"

    manifest_defaults: ClassVar[dict[str, Any]] = {
        "commands": {
            "build": None,
            "test": "flutter test",
            "test_one": "flutter test {selector}",
            "lint": "flutter analyze",
            "coverage": "flutter test --coverage --coverage-path={result_bundle}/lcov.info",
            "coverage_one": (
                "flutter test --coverage --coverage-path={result_bundle}/lcov.info {selector}"
            ),
            "coverage_report": "cat {result_bundle}/lcov.info",
        },
        "docs": _DOCS,
    }

    def detect(self, root: Path) -> bool:
        return (root / "pubspec.yaml").is_file() and _uses_flutter(root)


#: A dependency on the Flutter SDK, `sdk: flutter`, under any dependency key.
_FLUTTER_SDK = re.compile(
    r"""^[ \t]+sdk:[ \t]*["']?flutter["']?[ \t]*(?:#.*)?$""", re.MULTILINE
)


def _uses_flutter(root: Path) -> bool:
    try:
        text = (root / "pubspec.yaml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return _FLUTTER_SDK.search(text) is not None


#: Terminal colour codes, which a reporter writing to a terminal emits.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

#: The line closing one run of the `package:test` reporter.
_SUMMARY = re.compile(
    r"^(?:[\d:]+ )?\+(?P<passed>\d+)(?: ~(?P<skipped>\d+))?(?: -(?P<failed>\d+))?: "
    r"(?:All tests passed!|Some tests failed\.|All tests skipped\.)",
    re.MULTILINE,
)

_NO_TESTS = re.compile(r"^No tests ran\.", re.MULTILINE)

#: A summary row: the name, integer cells on the same line, the percentage.
_COVERAGE_ROW = re.compile(
    r"^[ \t]*(?P<file>[\w./+-]+\.dart)(?:[ \t]+\d+)*[ \t]+(?P<pct>\d+(?:\.\d+)?)%",
    re.MULTILINE,
)


def _lcov_records(raw: str) -> list[tuple[str, float]]:
    """``(file, percent)`` for every complete, consistent `.dart` lcov record."""
    found: list[tuple[str, float]] = []
    name: str | None = None
    counts: dict[str, int | None] = {}
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("SF:"):
            name, counts = line[3:].strip(), {}
        elif line[:3] in ("LF:", "LH:") and name is not None:
            counts[line[:2]] = _count(line[3:])
        elif line == "end_of_record" and name is not None:
            lines, hit = counts.get("LF"), counts.get("LH")
            if name.endswith(".dart") and lines and hit is not None and hit <= lines:
                found.append((name, 100.0 * hit / lines))
            name = None
    return found


def _count(value: str) -> int | None:
    value = value.strip()
    return int(value) if value.isdigit() else None


# -- "does this file have anything to measure" -------------------------
#
# Only asked to explain an ABSENT row, so a mistake toward "inert" passes
# unmeasured code. The reader therefore claims inert only for shapes it has
# fully understood, and any doubt (an unfinished comment or string, a string
# with `${`, a brace it cannot pair, a member it does not recognise) is
# "cannot tell".

#: `// coverage:ignore-file`, as `package:coverage` honours it.
_IGNORE_FILE = re.compile(r"^//\s*coverage:ignore-file\b")


def _dart_code(text: str) -> tuple[str, bool] | None:
    """The text with comments removed and every string reduced to `''`.

    Returns the reduced text and whether a line comment says
    `coverage:ignore-file`, or ``None`` where the reader cannot finish: an
    unclosed comment or string, a newline inside a single-line string, or an
    interpolation `${`, which can hold quotes of its own.
    """
    out: list[str] = []
    ignored = False
    i, n = 0, len(text)
    while i < n:
        two = text[i : i + 2]
        if two == "//":
            end = text.find("\n", i)
            end = n if end == -1 else end
            ignored = ignored or _IGNORE_FILE.match(text[i:end]) is not None
            out.append(" ")
            i = end
        elif two == "/*":
            i = _block_comment_end(text, i)
            if i < 0:
                return None
            out.append(" ")
        elif text[i] in "'\"" or (
            text[i] == "r"
            and i + 1 < n
            and text[i + 1] in "'\""
            and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_$"))
        ):
            i = _string_end(text, i)
            if i < 0:
                return None
            out.append("''")
        else:
            out.append(text[i])
            i += 1
    return "".join(out), ignored


def _block_comment_end(text: str, i: int) -> int:
    """Past a block comment opening at ``i``; Dart's nest. ``-1``: unclosed."""
    depth = 0
    while i < len(text):
        two = text[i : i + 2]
        if two == "/*":
            depth += 1
            i += 2
        elif two == "*/":
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    return -1


def _string_end(text: str, i: int) -> int:
    """Past a string literal starting at ``i``. ``-1``: unread."""
    raw = text[i] == "r"
    i += raw
    quote = text[i] * 3 if text[i : i + 3] == text[i] * 3 else text[i]
    i += len(quote)
    while i < len(text):
        if text.startswith(quote, i):
            return i + len(quote)
        char = text[i]
        if char == "\n" and len(quote) == 1:
            return -1
        if not raw and char == "\\":
            i += 2
            continue
        if not raw and text.startswith("${", i):
            return -1
        i += 1
    return -1


def _declarations_are_inert(code: str) -> bool:
    """Is every top-level declaration one that compiles to no hittable line?"""
    i, n = 0, len(code)
    while i < n:
        if code[i].isspace():
            i += 1
            continue
        stop = _next_of(code, i, ";{}")
        if stop < 0 or code[stop] == "}":
            return False
        head = _without_annotations(code[i:stop].strip())
        if head is None:
            return False
        if code[stop] == ";":
            if not _inert_statement(head):
                return False
            i = stop + 1
            continue
        close = code.find("}", stop)
        if close < 0 or "{" in code[stop + 1 : close]:
            return False
        if not _inert_block(head, code[stop + 1 : close]):
            return False
        i = close + 1
    return True


def _next_of(code: str, start: int, chars: str) -> int:
    for index in range(start, len(code)):
        if code[index] in chars:
            return index
    return -1


_ANNOTATION = re.compile(r"^@[\w.]+\s*")


def _without_annotations(text: str) -> str | None:
    """``text`` past its leading annotations; ``None`` if one takes arguments."""
    while text.startswith("@"):
        match = _ANNOTATION.match(text)
        if match is None or text[match.end() :].startswith("("):
            return None
        text = text[match.end() :]
    return text


_DIRECTIVE = re.compile(
    r"^(?:library(?:\s+[\w.]+)?|(?:import|export|part)\s*''.*|part\s+of\s+(?:''|[\w.]+))$",
    re.DOTALL,
)


def _inert_statement(text: str) -> bool:
    """A directive, or a typedef (which never holds a function body)."""
    if _DIRECTIVE.match(text):
        return True
    return text.startswith("typedef") and text[7:8].isspace() and "=>" not in text


_CLASS_HEAD = re.compile(
    r"^(?:(?:abstract|base|interface|final|sealed|mixin)\s+)*(?:class|mixin)\s+(?P<name>\w+)"
    r"(?P<rest>[^(=]*)$",
    re.DOTALL,
)
_ENUM = re.compile(r"^enum\s+\w+$")
_ENUM_VALUES = re.compile(r"^\s*(?:\w+\s*(?:,\s*\w+\s*)*,?)?\s*$")


def _inert_block(head: str, body: str) -> bool:
    """A plain enum, or a class or mixin of abstract member signatures."""
    if _ENUM.match(head):
        return _ENUM_VALUES.match(body) is not None
    match = _CLASS_HEAD.match(head)
    if match is None:
        return False
    members = body.split(";")
    if members[-1].strip():
        return False
    return all(_abstract_member(m.strip(), match.group("name")) for m in members[:-1])


_NOT_ABSTRACT = re.compile(
    r"^(?:static|factory|const|external|late|var|final|covariant)\b"
)
_GETTER = re.compile(r"\bget\s+\w+$")


def _abstract_member(text: str, owner: str) -> bool:
    """A member signature with no body, initialiser or constructor."""
    member = _without_annotations(text)
    if not member or "=" in member or _NOT_ABSTRACT.match(member):
        return False
    if re.match(rf"^{re.escape(owner)}\s*[.(]", member):
        return False
    if member.startswith("abstract") and member[8:9].isspace():
        return True
    if "(" in member:
        return member.endswith(")")
    return _GETTER.search(member) is not None
