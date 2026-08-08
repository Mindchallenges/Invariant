"""Swift / Xcode adapter.

This adapter exists first because the project this library was extracted from
needs it, and because its ecosystem supplies the clearest examples of what
``hazards()`` is for: several of its most common commands report success in
ways that do not mean what they appear to mean.

Every hazard below is recorded from an observed failure, not imagined.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import Adapter, Hazard, TestResult


class SwiftXcodeAdapter(Adapter):
    id = "swift_xcode"

    manifest_defaults = {
        "commands": {
            "build": "xcodebuild build -workspace {workspace} -scheme {scheme}",
            "test": (
                "xcodebuild test -workspace {workspace} -scheme {scheme} "
                "-destination 'platform=iOS Simulator,id={destination_id}'"
            ),
            "test_one": (
                "xcodebuild test -workspace {workspace} -scheme {scheme} "
                "-destination 'platform=iOS Simulator,id={destination_id}' "
                "-only-testing:{selector}"
            ),
        },
        "docs": {"format": "sphinx", "diagrams": "plantuml"},
    }

    def detect(self, root: Path) -> bool:
        return any(root.glob("*.xcworkspace")) or any(root.glob("*.xcodeproj"))

    def source_files(self, changed: list[Path]) -> list[Path]:
        return [p for p in changed if p.suffix == ".swift"]

    def hazards(self) -> list[Hazard]:
        return [
            Hazard(
                id="xcode.exit65_drops_suites",
                summary="exit 65 can mean whole suites never ran",
                detail=(
                    "An exit status of 65 is reported for a failed test run and "
                    "for a run that silently dropped test bundles. Count the "
                    "suites that actually executed against the suites expected "
                    "before reading the result as a test failure."
                ),
                detect=r"\*\* TEST FAILED \*\*|Exit status: 65",
            ),
            Hazard(
                id="xcode.destination_by_name_resolves_latest",
                summary="a name= destination resolves the OS to 'latest'",
                detail=(
                    "Passing a destination by device name lets the OS resolve to "
                    "'latest', which may be a runtime where that device does not "
                    "exist. Always pass a concrete device identifier, discovered "
                    "at runtime rather than written down."
                ),
                detect=r"platform=iOS Simulator,\s*name=",
            ),
            Hazard(
                id="xcode.only_testing_takes_target_class",
                summary="-only-testing: takes Target/Class, never a folder path",
                detail=(
                    "A folder-shaped filter matches nothing and the run reports "
                    "success having executed zero tests. A green from a filtered "
                    "run is only meaningful alongside a non-zero executed count."
                ),
                detect=r"-only-testing:[^\s']*/[^\s']*\.(swift|m)\b|Executed 0 tests",
            ),
            Hazard(
                id="xcode.debug_device_binary_is_a_stub",
                summary="a debug device build's main binary carries no symbols",
                detail=(
                    "Symbol lookups against the main executable return empty for "
                    "every query, which reads identically to 'the symbol is "
                    "absent'. Search the companion debug dylib instead before "
                    "concluding a symbol did not ship."
                ),
            ),
            Hazard(
                id="xcode.fd_exhaustion_tail_failures",
                summary="late-alphabet suites can fail from descriptor exhaustion",
                detail=(
                    "A long run can exhaust file descriptors, so failures "
                    "clustered in the tail of an alphabetical suite order are "
                    "suspect. Re-run those suites in isolation before attributing "
                    "the failure to shared state between tests."
                ),
            ),
            Hazard(
                id="xcode.rotated_simulator_reds_snapshots",
                summary="a rotated simulator reds every snapshot test",
                detail=(
                    "A simulator left in landscape fails every snapshot "
                    "comparison by a fixed offset. 'Fails only after other "
                    "suites' is a symptom of simulator orientation far more often "
                    "than of shared state."
                ),
            ),
        ]

    def parse_test_output(self, stdout: str, stderr: str, code: int) -> TestResult:
        combined = f"{stdout}\n{stderr}"
        live = [h for h in self.hazards() if h.matches(combined)]

        executed = _sum_executed(combined)
        failed = _sum_failures(combined)

        # A filtered run that executed nothing exits zero. Treat that as
        # untrustworthy rather than as a pass.
        note = ""
        if code == 0 and executed == 0:
            note = (
                "the run exited clean having executed zero tests — check the "
                "-only-testing: selector"
            )

        return TestResult(
            passed=code == 0,
            exit_code=code,
            total=executed,
            failed=failed,
            hazards=live,
            note=note,
        )

    def parse_coverage(self, raw: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for match in re.finditer(
            r"^\s*(?P<file>[\w./+-]+\.swift)\s+(?P<pct>\d+(?:\.\d+)?)%", raw, re.M
        ):
            out[match.group("file")] = float(match.group("pct"))
        return out


def _sum_executed(text: str) -> int:
    return sum(
        int(m.group(1)) for m in re.finditer(r"Executed (\d+) tests?", text)
    )


def _sum_failures(text: str) -> int:
    return sum(
        int(m.group(1)) for m in re.finditer(r"with (\d+) failures?", text)
    )
