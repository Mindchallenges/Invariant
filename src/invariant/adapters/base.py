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
from dataclasses import dataclass, field
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
    total: int | None = None
    failed: int | None = None
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
