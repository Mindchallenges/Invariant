"""Python adapter."""

from __future__ import annotations

import re
from pathlib import Path

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
        },
        "docs": {"format": "markdown", "diagrams": "mermaid"},
    }

    def detect(self, root: Path) -> bool:
        return (root / "pyproject.toml").is_file() or (root / "setup.py").is_file()

    def source_files(self, changed: list[Path]) -> list[Path]:
        return [p for p in changed if p.suffix == ".py"]

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
        note = ""
        if code == 5 or re.search(r"collected 0 items", combined):
            note = "zero tests were collected — the selector matched nothing"
        return TestResult(
            passed=code == 0,
            exit_code=code,
            total=(passed or 0) + (failed or 0) or None,
            failed=failed,
            hazards=live,
            note=note,
        )

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
            re.M,
        ):
            out[match.group("file")] = float(match.group("pct"))
        return out


def _first_int(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None
