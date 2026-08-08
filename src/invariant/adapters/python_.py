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
        out: dict[str, float] = {}
        for match in re.finditer(
            r"^(?P<file>[\w./+-]+\.py)\s+\d+\s+\d+\s+(?P<pct>\d+)%", raw, re.M
        ):
            out[match.group("file")] = float(match.group("pct"))
        return out


def _first_int(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None
