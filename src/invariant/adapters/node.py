"""Node / TypeScript adapter."""

from __future__ import annotations

import re
from pathlib import Path

from .base import Adapter, Hazard, TestResult


class NodeAdapter(Adapter):
    id = "node"

    manifest_defaults = {
        "commands": {
            "build": "npm run build",
            "test": "npm test",
            "test_one": "npm test -- {selector}",
            "lint": "npm run lint",
            "coverage": "npm test -- --coverage",
            "coverage_one": "npm test -- --coverage {selector}",
        },
        "docs": {"format": "markdown", "diagrams": "mermaid"},
    }

    def detect(self, root: Path) -> bool:
        return (root / "package.json").is_file()

    # `test_scope_of` is deliberately the base answer. Everything after the
    # script is passed to whichever runner the project's `test` script names,
    # and what a `--` argument narrows is that runner's business, so only
    # nothing-at-all is claimed as the whole suite (ADR-010 D5).

    def source_files(self, changed: list[Path]) -> list[Path]:
        return [p for p in changed if p.suffix in {".ts", ".tsx", ".js", ".jsx"}]

    def is_test_file(self, path: Path) -> bool | None:
        """The conventions jest, vitest and node:test all key on.

        ``.test.`` / ``.spec.`` infixes and a ``__tests__`` directory. Anchored
        on the dot so ``latest.ts`` is not claimed as a test — see the base
        docstring on why that direction is the dangerous one.
        """
        if path.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
            return None
        if "__tests__" in path.parts[:-1]:
            return True
        return path.stem.endswith((".test", ".spec"))

    def hazards(self) -> list[Hazard]:
        return [
            Hazard(
                id="npm.missing_script_exits_nonzero_for_the_wrong_reason",
                summary="a missing script fails the same way a test failure does",
                detail=(
                    "'npm test' with no test script defined fails, which reads as "
                    "a red suite rather than as an absent one."
                ),
                detect=r"Missing script:",
            ),
            Hazard(
                id="jest.pattern_matched_no_tests",
                summary="a test-name pattern matching nothing can still exit zero",
                detail=(
                    "Depending on configuration a filter that matches no test "
                    "reports success. Check the executed count, not the exit code."
                ),
                detect=r"No tests found|Tests:\s+0 total",
            ),
        ]

    def parse_test_output(self, stdout: str, stderr: str, code: int) -> TestResult:
        combined = f"{stdout}\n{stderr}"
        live = [h for h in self.hazards() if h.matches(combined)]
        total = _first_int(r"Tests:.*?(\d+) total", combined)
        failed = _first_int(r"Tests:.*?(\d+) failed", combined)
        note = "" if total else "no executed-test count found in the output"
        return TestResult(
            passed=code == 0,
            exit_code=code,
            total=total,
            failed=failed,
            hazards=live,
            note=note,
        )


def _first_int(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text, re.S)
    return int(match.group(1)) if match else None
