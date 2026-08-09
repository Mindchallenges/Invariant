"""Swift / Xcode adapter.

This adapter exists first because the project this library was extracted from
needs it, and because its ecosystem supplies the clearest examples of what
``hazards()`` is for: several of its most common commands report success in
ways that do not mean what they appear to mean.

Every hazard below is recorded from an observed failure, not imagined.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .base import Adapter, Hazard, TestResult


def _newest_developer_dir() -> Path | None:
    """The newest installed Xcode's Developer directory, or ``None``.

    Only consulted when ``xcrun`` cannot find the tool, which means
    ``xcode-select`` is pointing somewhere without it. Deliberately not written
    into any config: a toolchain path is machine state, and machine state in a
    committed file is both stale and somebody's disk layout.
    """
    candidates = sorted(Path("/Applications").glob("Xcode*.app/Contents/Developer"))
    return candidates[-1] if candidates else None


class SwiftXcodeAdapter(Adapter):
    id = "swift_xcode"

    manifest_defaults = {
        "commands": {
            "build": "xcodebuild build -workspace {workspace} -scheme {scheme}",
            # -resultBundlePath is not optional decoration. The bundle is the
            # run's own machine-readable record, and it is the only source that
            # reports test CASES rather than a console layout that changes with
            # parallelism. Console parsing stays as the fallback.
            "test": (
                "xcodebuild test -workspace {workspace} -scheme {scheme} "
                "-destination 'platform=iOS Simulator,id={destination_id}' "
                "-resultBundlePath {result_bundle}"
            ),
            # Two commands, which is presumably why this slot stayed empty
            # while the single-command ecosystems got one. xccov reads a result
            # bundle, so the run has to produce one first; {result_bundle} is
            # supplied by the caller, never written into a manifest, because it
            # is a per-run path rather than configuration.
            #
            # Separated by `;` and NOT `&&`. Chaining on success makes the
            # report conditional on a full-suite pass, which silently reimposes
            # test.all_pass as a block on any project that deliberately
            # downgraded it — and a project whose main branch is not green is
            # precisely the one that downgraded it. "The suite failed" and "the
            # measurement is untrustworthy" are different claims. Whether the
            # measurement can be trusted is decided in run_coverage, from
            # whether the suite RAN, not from whether it passed.
            "coverage": (
                "xcodebuild test -workspace {workspace} -scheme {scheme} "
                "-destination 'platform=iOS Simulator,id={destination_id}' "
                "-enableCodeCoverage YES -resultBundlePath {result_bundle} "
                "; xcrun xccov view --report {result_bundle}"
            ),
            "test_one": (
                "xcodebuild test -workspace {workspace} -scheme {scheme} "
                "-destination 'platform=iOS Simulator,id={destination_id}' "
                "-resultBundlePath {result_bundle} "
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
                # Dropped suites mean files never exercised, so coverage
                # comes out LOW. It can block work that was fine; it cannot
                # pass work that was not covered.
                can_overstate=False,
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
                # Left unclassified deliberately. Resolving to a different
                # runtime means the measurement describes something other
                # than the thing under test, and "different" has no
                # direction — so it takes the safe default and blocks.
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
                # A filter matching nothing runs nothing, so coverage is
                # empty or low — never inflated.
                can_overstate=False,
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
                can_overstate=False,  # about symbol search, not measurement
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
                can_overstate=False,  # exhausted descriptors fail suites, lowering coverage
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
                can_overstate=False,  # reds snapshots; coverage is still measured
            ),
        ]

    def parse_test_output(self, stdout: str, stderr: str, code: int) -> TestResult:
        combined = f"{stdout}\n{stderr}"
        live = [h for h in self.hazards() if h.matches(combined)]

        executed, failed = _parse_counts(combined)
        failed_tests = _count_failing_cases(combined)

        # A filtered run that executed nothing exits zero. Treat that as
        # untrustworthy rather than as a pass.
        note = ""
        if code == 0 and executed == 0:
            note = (
                "the run exited clean having executed zero tests — check the "
                "-only-testing: selector"
            )
        elif code != 0 and failed == 0:
            # The parse and the exit status disagree, and the exit status is
            # ground truth: a non-zero exit means something failed, so a
            # parsed count of zero failures did not come from a run with no
            # failures — it came from a parse that did not find them.
            #
            # `failed` becomes None rather than 0. A zero derived from a failed
            # parse is indistinguishable downstream from a real zero, and it
            # renders as green. Absence of evidence is not evidence of absence,
            # and this is where the difference gets recorded.
            failed = None
            note = (
                f"exit {code} says the run failed, but no failure count could "
                f"be parsed from its output — the count is unknown, not zero"
            )

        return TestResult(
            failed_tests=failed_tests,
            passed=code == 0,
            exit_code=code,
            total=executed,
            failed=failed,
            hazards=live,
            note=note,
        )

    def result_bundle_counts(self, path) -> dict | None:
        """Counts from the .xcresult bundle — the run's own record of itself.

        Every number here is in the unit a reader expects: ``totalTestCount``
        and ``failedTests`` are test CASES, not assertions, and they do not
        depend on how xcodebuild chose to lay out its console summaries. The
        console parser stays as a fallback, because a bundle is only written
        when ``-resultBundlePath`` was passed and ``xcresulttool`` is reachable
        — on a machine whose ``xcode-select`` points at CommandLineTools it is
        not, without DEVELOPER_DIR.
        """
        import json
        import subprocess

        if not path or not Path(path).exists():
            return None

        argv = [
            "xcrun", "xcresulttool", "get", "test-results", "summary",
            "--path", str(path),
        ]

        # `xcrun` resolves tools through `xcode-select`, and a machine whose
        # xcode-select points at CommandLineTools cannot find xcresulttool at
        # all. That is not hypothetical: it is the state of the machine this
        # was written on, and it made this whole reader inert — falling back to
        # the console parser it was built to replace, silently, on every run.
        # So a failed lookup is retried against an actual Xcode.
        attempts = [None]
        if not os.environ.get("DEVELOPER_DIR"):
            attempts.append(_newest_developer_dir())

        data = None
        for developer_dir in attempts:
            env = dict(os.environ)
            if developer_dir:
                env["DEVELOPER_DIR"] = str(developer_dir)
            try:
                proc = subprocess.run(
                    argv, capture_output=True, text=True, timeout=120, env=env
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if proc.returncode != 0:
                continue
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                continue
            break

        if data is None:
            return None
        if not isinstance(data, dict) or data.get("totalTestCount") is None:
            return None

        device = None
        for entry in data.get("devicesAndConfigurations") or []:
            name = (entry.get("device") or {}).get("deviceName")
            identifier = (entry.get("device") or {}).get("deviceId")
            if name or identifier:
                device = f"{name or '?'} ({identifier or '?'})"
                break

        return {
            "total": data.get("totalTestCount"),
            "failed": data.get("failedTests"),
            "skipped": data.get("skippedTests"),
            "result": data.get("result"),
            "device": device,
        }

    def parse_coverage(self, raw: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for match in re.finditer(
            r"^\s*(?P<file>[\w./+-]+\.swift)\s+(?P<pct>\d+(?:\.\d+)?)%", raw, re.M
        ):
            out[match.group("file")] = float(match.group("pct"))
        return out


#: A failing test CASE. xcodebuild names each one it runs, so counting the
#: distinct identifiers that failed gives the unit a reader expects, alongside
#: the assertion count the summary line reports.
_FAILING_CASE = re.compile(r"Test Case '([^']+)' failed")


def _count_failing_cases(text: str) -> int | None:
    """Distinct failing test cases, or ``None`` if none were named.

    ``None`` rather than 0: a log that never names a case has not told us there
    were no failing cases, and a zero here would contradict a non-zero
    assertion count for no reason a reader could resolve.
    """
    names = {m.group(1) for m in _FAILING_CASE.finditer(text)}
    return len(names) or None


#: One xcodebuild summary line, at any nesting level.
_SUMMARY = re.compile(r"Executed (\d+) tests?, with (\d+) failures?")

#: The OUTERMOST summary — the one under "Test Suite 'All tests'".
_ALL_TESTS = re.compile(
    r"Test Suite '?All tests'?[^\n]*\n\s*Executed (\d+) tests?, with (\d+) failures?"
)


def _parse_counts(text: str) -> tuple[int, int]:
    """``(executed, failed)`` from xcodebuild's summary lines.

    xcodebuild prints one summary per nesting level for a single run — the test
    class, then the bundle, then "All tests" — so **summing** the matches
    multiplies the true figures by the nesting depth. A six-test suite was
    recorded as ``executed=18``, and that number lands in the durable pipeline
    record: a count inflated in the direction of more coverage than there was,
    on a green phase, is indistinguishable afterwards from a run that really
    did execute eighteen tests.

    Taking the last match alone is not enough either, and taking the last
    "All tests" line was itself a bug: under PARALLEL testing xcodebuild runs
    clones and prints one "All tests" aggregate PER CLONE, so the last one is
    a single clone's tally. A ~13,900-test run reported as 34 executed and 0
    failed, and a rule downstream called that a trustworthy green.

    Summing the "All tests" aggregates is right for both shapes at once. A
    single serial run prints exactly one of them — the nested class and bundle
    summaries are not "All tests" lines, which is what fixed the original
    over-count — and a parallel run prints one per clone, which genuinely do
    add up.

    ``failed`` is xcodebuild's failure count, which counts failing assertions
    rather than failing test methods; three failing tests raising four
    assertions between them reports four. That is xcodebuild's own accounting
    and is left as it is.
    """
    aggregates = list(_ALL_TESTS.finditer(text))
    if aggregates:
        return (
            sum(int(m.group(1)) for m in aggregates),
            sum(int(m.group(2)) for m in aggregates),
        )

    matches = list(_SUMMARY.finditer(text))
    if not matches:
        return 0, 0
    return int(matches[-1].group(1)), int(matches[-1].group(2))
