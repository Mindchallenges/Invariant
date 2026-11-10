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
from typing import Any, ClassVar


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
    #: Tests that failed under an expected-failure marker, as the ecosystem
    #: reports them (pytest's ``N xfailed``). Not part of ``failed``: such a
    #: run exits clean, and outside the red phase that clean exit is the
    #: truth. ``None`` means not reported or not parsed, not zero.
    expected_failures: int | None = None
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


@dataclass(frozen=True)
class Reference:
    """One project file a source file references (ADR-012 D4, ``imports``).

    ``target`` is the referenced file, project-relative in POSIX spelling, so a
    ``boundary`` rule's globs match it as they match every other declared path.
    ``line`` is the 1-based line the reference starts on, and ``text`` is that
    line without its line end: a finding's fingerprint is taken from it (D6),
    so moving the import does not make the finding new and editing it does.
    """

    target: str
    line: int
    text: str


class Adapter:
    """Base adapter. ``generic`` is this class, unmodified."""

    def __init__(self, *, docs_srcdir: Path | None = None) -> None:
        #: The source directory of invariant's own docs project, as the engine
        #: resolved it (``docs.needs.srcdir``, ADR-020 D8), or ``None`` when
        #: there is none. It is never the project's own content. An adapter
        #: imports nothing from the engine, so the value is handed in.
        self.docs_srcdir = docs_srcdir

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

    def read_run(self, stdout: str, stderr: str, code: int, bundle=None) -> TestResult:
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
    manifest_defaults: ClassVar[dict[str, Any]] = {}

    #: Whether a coverage floor means anything for the files this adapter
    #: :meth:`claims` (ADR-015 D1). ``False`` comes with
    #: :attr:`coverage_reason`, and ``coverage.meets_floor`` answers N/A for
    #: those files naming this adapter and the reason: a stated skip, never a
    #: silent one. Every other adapter's files are held as before.
    coverage_applicable: ClassVar[bool] = True
    coverage_reason: ClassVar[str] = ""

    def claims(self, relative: Path, root: Path) -> bool:
        """Is the project file ``relative`` (under ``root``) this ecosystem's own?

        Asked only of an adapter whose coverage is not applicable, to decide
        which touched files that N/A covers. A claim sets a file aside from
        the floor, which is the relaxing direction, so it is made only on
        positive evidence. The base claims nothing.
        """
        return False

    def annotate(self, result: TestResult, root: Path) -> TestResult:
        """``result`` with what only the project's own files can add to it.

        Asked after a run is read, with the project root the run's adapter
        cannot otherwise see: a setting in the project's configuration that
        narrows what the run refuses is the project's stated choice, noted on
        the result rather than raised as a hazard (ADR-015 D1). Anything
        that can make the result lie belongs in :meth:`hazards` instead. The
        base adds nothing and returns ``result`` itself.
        """
        return result

    def init_settings(self, root: Path) -> tuple[dict[str, Any], list[str]]:
        """Settings ``init`` writes for this ecosystem in the project at ``root``.

        For what the adapter's static defaults cannot know, such as where a
        project keeps its tree. ``commands`` are named bare; ``init`` puts them
        under this ecosystem's key. The notes are printed in ``init``'s report,
        each naming a setting it could not decide and the operator should set.
        The base writes nothing.
        """
        return {}, []

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

    def owns_selector(self, selector: str) -> bool:
        """Does this test selector name something in this ecosystem? (ADR-013 D2)

        Asked only where a manifest declares more than one ecosystem, to route
        a scoped run to the ecosystem whose command and adapter it belongs to.
        Answered from the adapter's own test-file convention
        (:meth:`is_test_file`), read off the selector as a path, so the run
        path and coverage classification cannot disagree about whose a test
        is. Not from :meth:`source_files`: the base answer there keeps every
        path, and a generic adapter claiming every selector would make every
        run ambiguous. ``False`` claims nothing: a selector no adapter claims
        runs as the primary's, so only positive evidence moves a run.
        """
        return self.is_test_file(Path(selector)) is True

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

    def test_scope_of(self, extra_tokens: Sequence[str]) -> str | None:
        """Is a run of the configured suite with these extra tokens the WHOLE suite?

        Asked by ``sweep.no_full_run`` at the hook (ADR-010 D5), about a shell
        call whose leading tokens are the project's own ``commands.test`` or
        ``commands.coverage`` template; ``extra_tokens`` is what followed.
        ``"full"`` claims the call runs everything the template runs,
        ``"scoped"`` claims it runs a subset, and ``None`` is "cannot tell".

        Only ``"full"`` makes the rule fire, so the two wrong answers are not
        symmetrical: a false ``"full"`` refuses a legitimate scoped run, which
        is visible and one override away, while a missed full run costs time
        and passes nothing it should not. So ``"full"`` is claimed only on
        positive evidence. The base answer knows no runner: nothing extra is
        the template itself, and anything else is doubt.
        """
        return "full" if not list(extra_tokens) else None

    def failing_test_ids(self, stdout: str, stderr: str, bundle=None) -> list[str] | None:
        """Which tests a run failed, each named so ``commands.test_one`` can re-run it.

        Asked by the sweep (ADR-010 D5), which re-runs each new red alone and
        files it under this identity, so an identity must be a selector the
        ecosystem's scoped command accepts, not a display name. ``bundle`` is
        the run's result bundle, where the ecosystem writes one.

        ``None`` is "cannot read identities here", and the sweep then files one
        ``suite:red`` key for the run instead of a key per test. An empty list
        is "this output names no failing test", which a red run turns into the
        same ``suite:red``. The base answer knows no runner: ``None``.
        """
        return None

    def source_files(self, changed: list[Path]) -> list[Path]:
        """Narrow a changed-file list to this ecosystem's source files."""
        return list(changed)

    def carries_executable_code(
        self, path: Path, relative: Path | None = None
    ) -> bool | None:
        """Does this file contribute lines a coverage tool could measure?

        Asked only to explain an ABSENT coverage row. Reporters emit nothing
        for a file with no executable lines — a bare protocol, an enum of
        cases, a typealias — and that silence is indistinguishable from a
        file the run never measured. The two need opposite handling: the
        first is vacuously fine, the second is the thing a coverage floor
        exists to catch.

        **Two spellings, and which one answers which question.** ``path`` is
        resolvable: an implementation that needs the file's CONTENTS opens it.
        ``relative`` is the same file spelled against the project root, and is
        the only spelling a DIRECTORY convention may be read from — an absolute
        path carries components above the project that the project does not
        own, so a checkout sitting inside a directory named `*.docc` would
        otherwise decide the answer for every file underneath it (BUG_042).
        ``relative`` is ``None`` when the caller could not supply one, and an
        implementation whose answer depends on the directory convention must
        then answer ``None``: the fail-safe direction relaxes nothing, and
        reading the absolute spelling instead is the defect itself.

        This obligation used to live only as a comment at ONE call site in
        another layer, protecting :meth:`is_test_file` and not this method. A
        guarantee written where the implementer cannot see it is one the next
        ancestor-scanning rule inherits by luck.

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

    #: The guideline engines this adapter answers for (ADR-012 D4), a subset
    #: of ``mask`` (:meth:`lexical_mask`) and ``imports`` (the import graph a
    #: ``boundary`` rule reads). ``guidelines.ruleset_valid`` refuses a rule
    #: that needs an engine no configured adapter offers, so a rule is never
    #: evaluated over raw text in place of the masked text it asked for. The
    #: base offers none.
    guideline_engines: ClassVar[frozenset[str]] = frozenset()

    def lexical_mask(self, relative: Path, text: str, keep: str) -> str | None:
        """``text`` with every region outside ``keep`` blanked in place.

        ``keep`` is one of :data:`LEXICAL_REGIONS`: ``code``, ``comments`` or
        ``strings``. The answer has the same lines and columns as ``text``, so
        a pattern rule's match is reported where it is in the file (ADR-012
        D4). ``relative`` is the project-relative path, which says whose
        language the file is written in.

        ``None`` for a file outside this adapter's ecosystem, and for one it
        cannot read to the end. Adapters stack and the first answer wins, so
        an answer for a file in another language is the defect BUG_103 was:
        one lexer reading another's quotes and comments. ``None`` relaxes
        nothing. A rule that needs a mask and gets none cannot be answered for
        that file, which is the opposite of matching the raw text, a silent
        widening of what the rule reads. The base answers ``None``.
        """
        return None

    def references(self, path: Path, relative: Path, root: Path) -> list[Reference] | None:
        """Every project file ``path`` imports, one :class:`Reference` each.

        The ``imports`` engine's question (ADR-012 D4, the check-kind
        amendment): a ``boundary`` rule reads which project files a changed
        file references. ``path`` is the file to read, ``relative`` its
        project-relative path, which says whose language it is and where it
        sits in its packages, and ``root`` the project tree imports resolve
        against.

        An import that resolves outside the project, a third-party or
        standard-library module, is outside the kind and is not reported
        (DN-A2). An empty list is a file that imports nothing of the
        project's. ``None`` is a file this adapter cannot answer for: one
        outside its ecosystem, one it cannot read or parse, or one whose
        project import does not resolve. A boundary rule then fails as
        unanswerable, never passes, for the reason a pattern rule without a
        mask does. The base answers ``None``; an adapter that answers lists
        ``imports`` in :attr:`guideline_engines`.
        """
        return None

    def executable_text(self, relative: Path, source: str) -> str | None:
        """This source with everything a coverage tool ignores removed.

        ``relative`` is the file's project-relative path. An adapter answers
        only for a file of its own ecosystem and ``None`` for every other:
        adapters stack and the first answer wins, and a Swift reducer reading
        a Python or TypeScript file took ``'http://a'`` for a string opener
        followed by a comment, so a real edit compared equal (BUG_103).

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

    def source_areas(
        self, root: Path, tracked: Sequence[Path] | None = None
    ) -> list[Path] | None:
        """The project's source areas, each a directory an architecture doc
        should name (ADR-016 D2).

        The base answer is the directories one level below each
        :meth:`measured_scope` root that hold a file :meth:`source_files`
        accepts. With no scope, it is the top-level directories holding such
        a file among ``tracked``, the project-relative files git tracks,
        which the caller hands in because an adapter asks git nothing. A
        directory whose name starts with a dot is never an area.

        Returns absolute directories, sorted, or ``None`` for "cannot tell",
        which the assessment reports as a gap rather than passing empty.
        """
        scope = self.measured_scope(root)
        if scope is not None:
            return [
                child
                for base in scope
                for child in sorted(base.iterdir() if base.is_dir() else ())
                if child.is_dir()
                and not child.name.startswith(".")
                and self.source_files(
                    sorted(p for p in child.rglob("*") if p.is_file())
                )
            ]
        if tracked is None:
            return None
        tops = {
            path.parts[0]
            for path in self.source_files([Path(p) for p in tracked])
            if len(path.parts) > 1 and not path.parts[0].startswith(".")
        }
        return [root / name for name in sorted(tops)]

    def is_test_file(self, path: Path) -> bool | None:
        """Is this file part of the test suite rather than the code under test?

        ``path`` is the PROJECT-RELATIVE spelling. This question is answered
        from the directory convention, and only the project's own layout
        carries that fact — see :meth:`carries_executable_code` for the
        spelling rule and for what happened when a sibling rule was written
        without it.

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

    def expected_failures_are_strict(
        self, root: Path, selectors: Sequence[str]
    ) -> bool | None:
        """Is every expected-failure marker the selected tests carry STRICT?

        Asked by the red-phase recorder about a run that exited clean with
        expected failures on it (BUG_066). A strict marker fails the suite when
        the expected failure stops occurring, so a strict expected failure that
        fails is the committed form of a red test, and the recorder may count
        it as one. A non-strict marker is a tolerance — a known-broken test
        allowed to stay broken — and is not proof of anything.

        ``True`` lets the recorder read the run as red; ``False`` and ``None``
        ("cannot tell") both leave it recorded as it exited. The wrong answers
        are not symmetrical: calling a loose marker strict manufactures proof,
        which is the direction that must never happen; calling a strict marker
        unknown costs the reproducer one unwrapped red run.

        The generic adapter knows no test framework and answers ``None``. So
        do ``node`` and ``swift_xcode``, deliberately: Jest's ``test.failing``
        and XCTest's ``XCTExpectFailure`` have strict semantics of their own,
        but neither adapter parses them, and an answer would be a guess. In
        those ecosystems record the red run with the wrapper off, then wrap.
        """
        return None

    def hazards(self) -> list[Hazard]:
        return []


#: The regions a lexical mask can keep (ADR-012 D4). A pattern rule's fourth
#: scope, ``any``, reads the text unmasked and never asks for one.
LEXICAL_REGIONS = ("code", "comments", "strings")


def masked(text: str, regions: Sequence[tuple[str, int, int]], keep: str) -> str:
    """``text`` with every region not of kind ``keep`` blanked in place.

    ``regions`` are ``(kind, start, end)`` character offsets that cover the
    text. Blanking replaces each character with a space and leaves line ends
    alone, so the result has the same lines and the same columns: a match in
    the mask is a match at the same place in the file.
    """
    if keep not in LEXICAL_REGIONS:
        raise ValueError(
            f"a lexical mask keeps one of {', '.join(LEXICAL_REGIONS)}, not {keep!r}; "
            "a rule reading any region reads the text unmasked"
        )
    out: list[str] = []
    for kind, start, end in regions:
        chunk = text[start:end]
        out.append(chunk if kind == keep else _LINE_BODY.sub(" ", chunk))
    return "".join(out)


#: Every character a blanked region replaces: anything but a line end.
_LINE_BODY = re.compile(r"[^\r\n]")
