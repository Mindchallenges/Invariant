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
from collections.abc import Sequence
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

    def self_check(self) -> list[tuple[str, bool, str]]:
        """Is the tooling this adapter depends on reachable FROM HERE?

        Not "is it installed" — reachable in the environment this process has,
        which is the environment the agent's shell has, which is frequently not
        the one a human verifying by hand has. A reader was shipped as ground
        truth and was inert on the machine it was written for, because the tool
        it calls could not be found the way the caller resolves it; the check
        that passed had been run with a variable exported by hand.

        A verification inherits its environment, and the environment is part of
        what is being asserted.
        """
        return []

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

    def join_selectors(self, selectors: Sequence[str]) -> str | None:
        """How this ecosystem spells SEVERAL selectors in one `{selector}` slot.

        ``None`` means it cannot, and the caller must REFUSE rather than run a
        narrower scope than was asked for. A repeated ``--selector`` used to
        keep only the last one, so the run reported a healthy green for a scope
        nobody asked for and nothing was printed about the discarded flag
        (BUG_031). Of the three possible behaviours — run both, refuse, discard
        one — the discard is the only one that leaves the caller believing
        something was covered.

        The base answer is deliberately narrow. ``commands.test_one`` is a
        template a project may have overridden, and whether several selectors
        are a space-separated list of positional arguments or a repeated FLAG
        is a fact about the tool the template invokes. Guessing wrong builds a
        well-formed command against the wrong scope, which is the defect this
        exists to close, one layer along.
        """
        selectors = list(selectors)
        return selectors[0] if len(selectors) == 1 else None

    def source_files(self, changed: list[Path]) -> list[Path]:
        """Narrow a changed-file list to this ecosystem's source files."""
        return list(changed)

    def carries_executable_code(self, path: Path) -> bool | None:
        """Does this file contribute lines a coverage tool could measure?

        Asked only to explain an ABSENT coverage row. Reporters emit nothing
        for a file with no executable lines — a bare protocol, an enum of
        cases, a typealias — and that silence is indistinguishable from a
        file the run never measured. The two need opposite handling: the
        first is vacuously fine, the second is the thing a coverage floor
        exists to catch.

        ``None`` means "cannot tell", and carries the same obligation as
        :func:`git.branch_files`' ``None`` — a caller deciding whether to
        RELAX must treat it as "do not relax". The generic adapter knows no
        language, so it always answers ``None``.

        The two wrong answers are not symmetrical, and an implementation
        must prefer the first: claiming an inert file has code costs a
        spurious block, which is visible and sends someone to look; claiming
        a live file is inert passes unmeasured code silently.
        """
        return None

    def executable_text(self, source: str) -> str | None:
        """This source with everything a coverage tool ignores removed.

        The third fact a coverage floor needs and cannot derive. Comparing
        two revisions of a file through this answers a question the raw diff
        cannot: did this change alter any line a coverage run could measure?
        A change that only rewrote comments moved no number and can move
        none, so holding it to the floor asks for work that cannot affect
        the thing being demanded — the shape that turns a gate into
        something people force past.

        Returns the reduced text, or ``None`` for "cannot tell". ``None``
        carries the same obligation as :meth:`carries_executable_code`'s and
        :meth:`measured_scope`'s: a caller deciding whether to RELAX must
        treat it as "do not relax". The generic adapter knows no comment
        syntax, so it always answers ``None``.

        The asymmetry points the same way as :meth:`measured_scope`'s,
        because this answer too is used to SHRINK what is demanded: reducing
        too little costs a spurious block, reducing too much calls a real
        code change cosmetic and passes it unmeasured. An implementation
        that cannot parse the file must answer ``None`` rather than its best
        guess — and must never strip anything a coverage tool would count,
        which is why a string literal is reduced but not removed.
        """
        return None

    def measured_scope(self, root: Path) -> list[Path] | None:
        """Which directories this project's coverage reporter is scoped to.

        The second fact a coverage floor needs and cannot derive. A file may
        have no row for a reason that has nothing to do with the file: the
        reporter was CONFIGURED never to look at it. A Python project setting
        ``[tool.coverage.run] source`` to its package directory can never
        produce a row for a test file, so demanding one is a gate no work can
        clear — which is how an enforced gate becomes a gate people route
        around with ``--force`` (BUG_019).

        Returns absolute directories, or ``None`` for "cannot tell". ``None``
        carries the same obligation as :meth:`carries_executable_code`'s: a
        caller deciding whether to RELAX must treat it as "do not relax".

        The two wrong answers are again asymmetrical, and the asymmetry points
        the other way here, because this answer is used to SHRINK what is
        demanded. An overstated scope costs a spurious block; an understated
        one excuses a file the reporter really does measure, and passes
        unmeasured code silently. So an implementation that cannot resolve
        every configured entry must answer ``None`` rather than the part of
        the scope it managed to work out.
        """
        return None

    def is_test_file(self, path: Path) -> bool | None:
        """Is this file part of the test suite rather than the code under test?

        Asked for the same reason as :meth:`carries_executable_code` and
        answered on the same terms — to explain an ABSENT coverage row. A test
        file is full of executable lines, so that method cannot answer this
        question honestly; what makes a test file different is that it is what
        *measures*, not what is measured. :meth:`measured_scope` answers the
        configured half of the question — where the reporter was told to look —
        and this method answers the conventional half for projects whose
        configuration the adapter cannot read: the usual coverage
        configuration (``source = ["src/..."]`` and its equivalents) is
        defined never to produce a row for a test file. Demanding a row anyway
        is a gate no amount of work can clear.

        ``None`` means "cannot tell" and relaxes nothing, exactly as there. An
        adapter must answer ``None`` for a file outside its own ecosystem
        rather than guess, because adapters stack and the first real answer
        wins.

        The two wrong answers are not symmetrical and an implementation must
        prefer the second: calling a test file production costs a spurious
        block, which is visible; calling a production file a test sets it
        aside and passes unmeasured code silently. So implementations anchor
        on naming conventions the ecosystem actually enforces, never on the
        substring "test" appearing somewhere in a path.
        """
        return None

    def hazards(self) -> list[Hazard]:
        return []
