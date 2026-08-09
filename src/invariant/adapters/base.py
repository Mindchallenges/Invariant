"""The adapter interface.

An adapter carries ecosystem knowledge the manifest should not have to spell
out: how to build, how to run one test, how to read a coverage number, and —
most usefully — which of that ecosystem's success signals are unreliable.

``hazards()`` is the part that earns the abstraction. A hazard is not a rule.
Rules gate transitions; hazards annotate a result so a green is not mistaken
for proof. When ``parse_test_output`` reports ``passed`` alongside a hazard,
the library is saying: this looks green, and here is the specific reason it
might not be.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Hazard:
    """A way this ecosystem's success signal can lie."""

    id: str
    summary: str
    detail: str
    detect: str | None = None  # optional regex over combined output

    #: Can this hazard make a *measurement* read BETTER than reality?
    #:
    #: The two directions are not equally dangerous, so they must not be
    #: handled alike. A measurement bent low blocks work that was fine: it is
    #: visible, it is annoying, and it is self-correcting, because being
    #: blocked is what sends someone to look. A measurement bent high passes
    #: work that was never covered, silently, and nobody goes to look — which
    #: is the whole problem. So the first may be annotated and recorded; the
    #: second has to block.
    #:
    #: ``None`` means "not examined yet" and is treated as True. The default
    #: for an unclassified hazard must be the safe one, or a hazard added
    #: without thought inherits "annotate" and goes quiet.
    can_overstate: bool | None = None

    @property
    def may_overstate(self) -> bool:
        return self.can_overstate is not False

    def matches(self, text: str) -> bool:
        return bool(self.detect) and re.search(self.detect, text) is not None


@dataclass
class TestResult:
    # Its name matches pytest's collection pattern; it is a result type, not a
    # test case.
    __test__ = False

    passed: bool
    exit_code: int
    #: Test CASES executed.
    total: int | None = None
    #: Failures as the ecosystem counts them. For xcodebuild that is failing
    #: ASSERTIONS, not failing test cases — one test tripping four asserts
    #: reports four. Kept in the ecosystem's own units because converting it
    #: would be inventing a number the tool did not report.
    failed: int | None = None
    #: Failing test CASES, when the adapter can distinguish them. This exists
    #: because `total` and `failed` are counted in different units, and a line
    #: reading "executed=14 failed=6" invites the reader to look for six failing
    #: tests among fourteen — there were two.
    failed_tests: int | None = None
    #: Where the counts came from: ``"console"`` (scraped from output) or
    #: ``"result_bundle"`` (read from the run's own machine-readable record).
    #: Recorded rather than assumed, so a silent fall back to scraping is
    #: visible in the evidence instead of looking like a bundle read.
    source: str = "console"
    #: The device the run actually executed on, when the source can say. Two
    #: sessions ran on a peer's simulator without either being able to tell.
    device: str | None = None
    skipped: int | None = None
    hazards: list[Hazard] = field(default_factory=list)
    note: str = ""

    @property
    def trustworthy(self) -> bool:
        """Narrower than ``passed``, on purpose.

        A pass with a live hazard is not a trustworthy pass. Neither is a pass
        that executed nothing: a filter matching no tests exits clean, and
        "the suite is green" and "the suite did not run" are the same exit
        status. Treating zero-executed as trustworthy is how a run that proved
        nothing gets recorded as proof.

        ``total is None`` is not zero. It means this adapter cannot count, not
        that nothing ran; holding a missing adapter feature against a real run
        would leave the generic adapter unable to report a trustworthy pass at
        all.
        """
        return self.passed and not self.hazards and self.total != 0


class Adapter:
    """Base adapter. ``generic`` is this class, unmodified."""

    def result_bundle_counts(self, path) -> dict | None:
        """Authoritative counts from a machine-readable run artifact.

        ``None`` means this ecosystem has no such artifact, or it could not be
        read. Console output is a *rendering* of a run; where the run also
        writes a structured record, that record is the fact and the text is a
        summary of it — and scraping a summary has now been wrong twice in one
        day, in both directions.
        """
        return None

    def read_run(self, stdout: str, stderr: str, code: int, bundle=None) -> "TestResult":
        """The run's outcome, preferring the structured record over the text.

        The exit status still decides ``passed``, because a build that failed
        before any test ran produces no bundle at all — the bundle can only
        speak about tests that happened.
        """
        result = self.parse_test_output(stdout, stderr, code)
        counts = self.result_bundle_counts(bundle) if bundle else None
        if not counts:
            # A bundle that exists but cannot be read is a different situation
            # from no bundle at all, and it is the dangerous one: the run wrote
            # its record and something stopped us reading it, so the counts
            # come from the parser this was meant to replace. Silence there is
            # how a "ground truth" reader stays inert for a day.
            if bundle is not None and Path(str(bundle)).exists():
                note = result.note or ""
                extra = (
                    "a result bundle was written but could not be read; counts "
                    "below are scraped from console output"
                )
                return replace(result, note=f"{note}; {extra}" if note else extra)
            return result

        note = result.note
        if code == 0 and str(counts.get("result", "")).lower() == "failed":
            note = (
                "the process exited zero but its result bundle records the run "
                "as failed — trusting the bundle"
            )
        return replace(
            result,
            total=counts.get("total", result.total),
            failed=counts.get("failed", result.failed),
            failed_tests=counts.get("failed", result.failed_tests),
            skipped=counts.get("skipped"),
            device=counts.get("device"),
            source="result_bundle",
            passed=result.passed and str(counts.get("result", "")).lower() != "failed",
            note=note,
        )

    id = "generic"
    manifest_defaults: dict[str, Any] = {}

    def detect(self, root: Path) -> bool:
        return False

    def default_commands(self) -> dict[str, str | None]:
        return dict(self.manifest_defaults.get("commands", {}))

    def parse_test_output(self, stdout: str, stderr: str, code: int) -> TestResult:
        combined = f"{stdout}\n{stderr}"
        live = [h for h in self.hazards() if h.matches(combined)]
        return TestResult(passed=code == 0, exit_code=code, hazards=live)

    def parse_coverage(self, raw: str) -> dict[str, float]:
        return {}

    def source_files(self, changed: list[Path]) -> list[Path]:
        """Narrow a changed-file list to this ecosystem's source files."""
        return list(changed)

    def hazards(self) -> list[Hazard]:
        return []
