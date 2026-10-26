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


def _tool_is_reachable(tool: str, developer_dir) -> bool:
    import subprocess

    env = dict(os.environ)
    if developer_dir:
        env["DEVELOPER_DIR"] = str(developer_dir)
    try:
        proc = subprocess.run(
            ["xcrun", "--find", tool],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


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
            # TWO SLOTS, not one command with a `;` in it. xccov reads a result
            # bundle, so the run has to produce one first; {result_bundle} is
            # supplied by the caller, never written into a manifest, because it
            # is a per-run path rather than configuration.
            #
            # These were one chained command until it was shown to lie in both
            # directions, because a shell reports the LAST command's status:
            #
            #   - a red suite followed by a clean read exits 0, and the run was
            #     recorded as a passing suite with a measurement beside it;
            #   - a green suite followed by a failed read exits non-zero, and a
            #     perfectly good suite was recorded as failed. That one is not
            #     hypothetical: `xcrun` answers 72 for "utility not found", and
            #     because `DEVELOPER_DIR=x cmd` is a PER-COMMAND assignment it
            #     never reached the leg behind the semicolon, so every project
            #     repairing its toolchain that way repaired only the first half.
            #
            # Chaining with `&&` would have fixed neither: it makes the report
            # conditional on a full-suite pass, silently reimposing
            # test.all_pass as a block on any project that deliberately
            # downgraded it — and a project whose main branch is not green is
            # precisely the one that downgraded it.
            #
            # Split, each leg's status means one thing. "The suite failed" and
            # "the measurement could not be read" stay different claims, and
            # whether the measurement can be trusted is still decided in
            # run_coverage from whether the suite RAN, not whether it passed.
            "coverage": (
                "xcodebuild test -workspace {workspace} -scheme {scheme} "
                "-destination 'platform=iOS Simulator,id={destination_id}' "
                "-enableCodeCoverage YES -resultBundlePath {result_bundle}"
            ),
            "coverage_report": "xcrun xccov view --report {result_bundle}",
            # The scoped sibling of `coverage`, as `test_one` is of `test`
            # (ADR-010). Under a sweep cadence a change's coverage comes from
            # its affected suites; the figure is a lower bound and is reported,
            # not held to the floor.
            "coverage_one": (
                "xcodebuild test -workspace {workspace} -scheme {scheme} "
                "-destination 'platform=iOS Simulator,id={destination_id}' "
                "-enableCodeCoverage YES -resultBundlePath {result_bundle} "
                "-only-testing:{selector}"
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

    # `join_selectors` is deliberately NOT overridden. xcodebuild takes a
    # repeated `-only-testing:` FLAG rather than a list of values, and the flag
    # lives in `commands.test_one` — a template a project may have rewritten.
    # Producing `A -only-testing:B` would be correct against the shipped
    # default and silently wrong against any other, which is the same class of
    # defect as the discarded selector this answers (BUG_031). The base
    # `None` makes the CLI refuse and say so, and one selector per invocation
    # still works.

    def test_scope_of(self, extra_tokens) -> str | None:
        """``-only-testing:`` narrows; ``-skip-testing:`` and anything else is doubt.

        xcodebuild's own selection flags are the only ones this adapter can
        read. ``-skip-testing:`` removes tests from a run that may otherwise be
        whole, which is neither answer, and any other token may be a setting
        that changes what runs (ADR-010 D5).
        """
        tokens = list(extra_tokens)
        if not tokens:
            return "full"
        if any(t.startswith("-skip-testing:") for t in tokens):
            return None
        if any(t.startswith("-only-testing:") for t in tokens):
            return "scoped"
        return None

    def is_test_file(self, path: Path) -> bool | None:
        """The convention `swift test` and Xcode both key on.

        A ``*Tests.swift`` file, or anything under a ``*Tests`` target
        directory — the layout `swift package init` generates and Xcode's test
        target default. Anchored on the suffix rather than on the word
        appearing anywhere, so ``TestHarness.swift`` shipped in the app target
        is not claimed.
        """
        if path.suffix != ".swift":
            return None
        if any(part.endswith("Tests") for part in path.parts[:-1]):
            return True
        return path.stem.endswith("Tests")

    def carries_executable_code(
        self, path: Path, relative: Path | None = None
    ) -> bool | None:
        """A Swift file by its contents; every other kind by whether Xcode
        compiles it at all.

        Two questions with one answer type, and — the reason this method takes
        two spellings — two different paths. `.swift` goes to the reader, which
        OPENS the file and therefore needs ``path`` resolvable. Everything else
        goes to :func:`_xcode_resource_kind`, whose ancestor rule is a
        statement about the project's own layout and therefore needs
        ``relative``. Swapping one for the other would have broken whichever
        half it was not chosen for, which is why the argument is split rather
        than replaced.

        `_xcode_resource_kind` answers `False` only for the kinds this
        ecosystem is certain never to compile and `None` for the rest, so a
        non-Swift file this adapter has no fact about relaxes nothing.
        """
        if path.suffix != ".swift":
            return _xcode_resource_kind(path, relative)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            return None
        return _swift_carries_executable_code(text)

    def executable_text(self, source: str) -> str | None:
        return _swift_executable_text(source)

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

    def self_check(self) -> list[tuple[str, bool, str]]:
        """Can this process reach the Xcode tools, without help from a shell?

        Deliberately run with the environment as-is. Exporting DEVELOPER_DIR to
        make the check pass would reproduce the exact mistake this exists to
        catch: proving a tool reachable under conditions its caller never has.
        """
        results: list[tuple[str, bool, str]] = []
        fallback = _newest_developer_dir()

        # ``xccov`` belongs here as much as the other two, and leaving it out
        # let a real outage go unreported. It is invoked by ``coverage_report``
        # and by nothing else, so a probe that looked only at the commands
        # everyone thinks about missed it entirely: where xcode-select answers
        # CommandLineTools there is no xccov, xcrun exits 72, and that reads
        # exactly like "the bundle produced no per-file measurements" — which
        # is how it was misdiagnosed twice.
        #
        # Probing only the obvious tools reported a healthy toolchain while
        # the binary the coverage gate actually needs was missing.
        for tool in ("xcodebuild", "xcresulttool", "xccov"):
            direct = _tool_is_reachable(tool, None)
            if direct:
                results.append((f"xcode: {tool} reachable", True, "via xcrun"))
                continue
            if fallback and _tool_is_reachable(tool, fallback):
                results.append(
                    (
                        f"xcode: {tool} reachable",
                        True,
                        f"NOT via xcrun — falling back to {fallback}. "
                        f"xcode-select points somewhere without it; export "
                        f"DEVELOPER_DIR to make every caller agree.",
                    )
                )
                continue
            results.append(
                (
                    f"xcode: {tool} reachable",
                    False,
                    "not found via xcrun and no installed Xcode has it; "
                    "counts and coverage will be unavailable or scraped",
                )
            )
        return results

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
        """Every measured source row `xccov` reported, whatever language it is.

        The pattern used to end in a literal `\\.swift`, which was the whole of
        BUG_041: an Objective-C file the tool genuinely measured, and genuinely
        had a percentage for, was dropped on the way in. It then reached
        `coverage.meets_floor` looking exactly like a file no run had touched,
        the rule correctly refused to relax over a compiled kind, and the gate
        blocked permanently — a floor no test the author could write would ever
        clear. Every layer below behaved correctly on a false picture supplied
        here.

        The suffix set is :data:`_COMPILED_SUFFIXES` rather than a second list
        written beside it, so the reviewed table cannot drift from its copy.
        Note the asymmetry runs the OPPOSITE way from the relaxation question
        that table usually serves: a suffix listed here that `xccov` never
        emits a row for simply matches nothing, while one omitted drops a real
        measurement. So the whole table is used, `.metal` and `.s` included,
        rather than a cautious subset.

        Still an allowlist, and that is what keeps the widening honest — the
        report's target lines (`Heap.app  78.00%`) and any resource kind carry
        the same shape and must not become coverage rows.
        """
        out: dict[str, float] = {}
        for match in _COVERAGE_ROW.finditer(raw):
            out[match.group("file")] = float(match.group("pct"))
        return out


# -- "does this file have anything to measure" -------------------------
#
# Only ever consulted to explain a coverage row that is ABSENT, so the whole
# job is deciding between "nothing to measure" and "not measured". It reads
# structure, not semantics: every brace block in the file is classified, and
# the file is called inert only when every one of them is a shape that cannot
# contain a statement. Anything unrecognised answers "has code" or "cannot
# tell", never "inert" — see ``Adapter.carries_executable_code`` for why those
# two mistakes are not interchangeable.
#
# The reader above answers about a Swift file's CONTENTS. The four tables
# below answer a coarser question the same gate needs — whether the file is a
# kind an Xcode target compiles at all — because `xccov` reports on compiled
# Swift/ObjC/C and nothing else. A property list can never earn a row however
# it is written, so a floor demanding one is a gate no test can clear, which
# is the family this adapter has already been wrong about twice (BUG_025,
# BUG_034) and the shape this library treats as an engine defect rather than
# as a reason to force anything.
#
# Three properties keep the relaxation as narrow as the reader's:
#
#   - It is an ALLOWLIST. An extension, or an exact file name, absent from it
#     answers `None`, not `False`. "Not a source kind I recognise" is a
#     guess, and a guess in the relaxing direction passes unmeasured code in
#     silence — the one mistake `Adapter.carries_executable_code`'s docstring
#     forbids outright.
#   - Compiled kinds are named explicitly and checked FIRST, so no future
#     entry and no directory rule can reach one of them. `.m`, `.mm`, `.c`,
#     `.cc`, `.cpp`, `.cxx`, `.h`, `.hpp`, `.hh` are measured by `xccov` as
#     readily as Swift is; `.metal` and `.s` are not, and are listed here
#     anyway, because the cost of being wrong about them runs the wrong way.
#   - Documentation and configuration suffixes — `.md`, `.json`, `.yaml`,
#     `.toml` — are deliberately ABSENT. `core/rules` already holds those out
#     of the changed-source set for every ecosystem, and a second copy of that
#     judgement here is the duplication that drifts.

#: Kinds a coverage report can name, so this adapter may never call one inert.
#: Checked before every other table, including the directory one: a file's own
#: kind is stronger evidence than the folder it happens to sit in.
_COMPILED_SUFFIXES = frozenset(
    {
        ".swift",
        ".m",
        ".mm",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".h",
        ".hpp",
        ".hh",
        # The C++ spellings the table missed. Same claim as `.h`/`.hpp`/`.hh`
        # one line up — a header or inline file can carry code `xccov`
        # attributes to it — in extensions clang recognises and this list did
        # not. The omission was not neutral: an unlisted compiled kind falls
        # past this check and becomes reachable by the directory rule below,
        # which is the only way BUG_042's wrong relaxation could happen at all.
        # This table is checked FIRST and answers `None`, so an entry here can
        # never relax anything; over-inclusion costs a spurious block.
        ".hxx",
        ".ipp",
        ".inl",
        ".cp",
        ".c++",
        ".metal",
        ".s",
    }
)

#: Xcode resource kinds. Each is consumed by a resource step — `plutil`,
#: `ibtool`, `actool`, the strings compilers — that produces a bundle payload
#: rather than an instrumented binary, so none can appear in a coverage report
#: under any build configuration. `.xcconfig` is build SETTINGS, read before
#: compilation and never a compilation input itself.
_RESOURCE_SUFFIXES = frozenset(
    {
        ".plist",
        ".entitlements",
        ".strings",
        ".stringsdict",
        ".xcstrings",
        ".storyboard",
        ".xib",
        ".xcassets",
        ".xcconfig",
    }
)

#: Directory bundles whose entire contents are resources or project metadata.
#: The useful half in practice: a touched path rarely names the catalog, it
#: names a payload several components inside one — `Assets.xcassets/AppIcon.
#: appiconset/icon.png`, or an `.xcdatamodel/contents` with no suffix at all —
#: and that payload's own name carries no fact. Each entry is here because
#: nothing inside it is ever a compilation input: asset catalogs and
#: localisation directories hold resources, `.docc` holds documentation,
#: `.xcodeproj` and `.xcworkspace` hold project metadata, and an
#: `.xcdatamodeld` holds model versions compiled by `momc` into a bundle
#: resource. `.bundle` and `.framework` are deliberately absent — a vendored
#: bundle may hold anything, and "vendored" is exactly the guess this table
#: must not make.
_RESOURCE_BUNDLE_DIR_SUFFIXES = (
    ".xcassets",
    ".lproj",
    ".docc",
    ".xcodeproj",
    ".xcworkspace",
    ".xcdatamodeld",
    ".xcdatamodel",
)

#: Dependency-manager manifests and lockfiles, matched on the file's own EXACT
#: name, because the kind lives in the name rather than in a suffix: `Podfile`
#: has none and `Podfile.lock`'s `.lock` says nothing on its own (BUG_085).
#: Each is read by a tool that resolves dependencies BEFORE a build — CocoaPods
#: and the Bundler that pins it, Carthage, SwiftPM's resolver — and none is an
#: input to any Xcode target, so `xccov` can never emit a row for one.
#:
#: What the manifests resolve is a different matter and is deliberately NOT
#: reached: `Pods/` and `Carthage/Checkouts/` hold vendored ObjC/C/Swift that
#: `xccov` measures (BUG_041), and only these exact names relax, never a
#: directory. `Package.swift` is absent too — it is Swift and keeps the reader's
#: content path, which is why this table is consulted only for non-`.swift`
#: paths. Fastlane's `Fastfile` and its kin are Ruby automation rather than
#: dependency manifests, and stay out until something states that fact for them.
_DEPENDENCY_MANIFEST_NAMES = frozenset(
    {
        "Podfile",
        "Podfile.lock",
        "Gemfile",
        "Gemfile.lock",
        "Cartfile",
        "Cartfile.private",
        "Cartfile.resolved",
        "Package.resolved",
    }
)


#: One `xccov view --report` row for a file it measured:
#: ``    <path>    <pct>% (<covered>/<total>)``. Longest suffix first, so
#: `Engine.swift` cannot be truncated to a `.s` match by backtracking.
_COVERAGE_ROW = re.compile(
    r"^\s*(?P<file>[\w./+-]+(?:"
    + "|".join(
        re.escape(s) for s in sorted(_COMPILED_SUFFIXES, key=len, reverse=True)
    )
    + r"))\s+(?P<pct>\d+(?:\.\d+)?)%",
    re.MULTILINE,
)


def _xcode_resource_kind(path: Path, relative: Path | None) -> bool | None:
    """``False`` for a path no Xcode target compiles, ``None`` if unsure.

    Never returns ``True``: "this kind is compiled" is not the same claim as
    "this file has executable lines in it", and the Swift reader is what
    answers the second. Returning ``True`` here would let a `.swift` file skip
    that reader entirely.

    The SUFFIX and NAME questions are answered from ``path``, which carries
    the file's own kind wherever it is spelled. The ANCESTOR question is answered from
    ``relative`` and from nothing else: `parts[:-1]` on an absolute path walks
    out of the project and into the machine's layout, so a checkout under a
    directory named `*.docc` would relax the coverage gate for everything
    inside it (BUG_042). Without a project-relative spelling the ancestor rule
    abstains — `None` relaxes nothing, which is the direction the base
    contract requires when an adapter cannot tell.
    """
    suffix = path.suffix.lower()
    if suffix in _COMPILED_SUFFIXES:
        return None
    if suffix in _RESOURCE_SUFFIXES:
        return False
    if path.name in _DEPENDENCY_MANIFEST_NAMES:
        return False
    if relative is not None and any(
        part.lower().endswith(_RESOURCE_BUNDLE_DIR_SUFFIXES)
        for part in relative.parts[:-1]
    ):
        return False
    return None

#: Everything legal inside a protocol's accessor requirement. A block holding
#: only these is `{ get }`, a promise about a member; a block holding anything
#: else is `{ 44 }`, an implementation of one.
_ACCESSOR_ONLY = {"get", "set", "async", "throws", "mutating", "nonmutating"}

#: Block headers that cannot themselves introduce executable lines. Their
#: MEMBERS still get classified — a `func` body nested in a protocol extension
#: is its own block and is judged on its own header.
#:
#: `struct`, `class` and `actor` are here for the same reason `protocol` and
#: `enum` are, and leaving them out was a defect rather than a caution: Swift
#: permits no statement in a type body, so a type whose own residue is stored
#: properties and nested type declarations has nothing a coverage run could
#: report, and the two files that exposed this (a value type of six `let`/`var`
#: declarations, and one wrapping a bare enum) really do produce no row in any
#: lane. Skipping the header is not skipping the body: every `func`, `init`,
#: `deinit`, `subscript`, computed property and property observer inside is its
#: own block with its own header and is still judged on it, and a stored
#: property with an initialiser expression is caught file-wide by
#: `_INITIALISED_BINDING` before any block is classified — unless the binding
#: is a type-level constant, which `_is_constant_declaration` sets aside for
#: the reason recorded there. `extension` is deliberately NOT here — its
#: residue can be a member list that only a body could explain, which is why
#: a constant member of one has to be recognised as a declaration on its own
#: terms rather than by the block it sits in.
_INERT_BLOCK_HEADER = re.compile(r"\b(protocol|enum|struct|class|actor)\b")

#: A binding with an initialiser expression: `var limit: Int = 200`. It has no
#: brace, so no block classifies it, and the expression is an executable line.
#: `typealias X = Y` and `case a = "a"` are deliberately not matched.
_INITIALISED_BINDING = re.compile(r"\b(?:var|let)\b[^\n=]*=(?!=)")

#: A value with no work behind it. The string alternative is `""` rather than
#: anything between quotes because `_strip_comments_and_strings` has already
#: blanked the CONTENTS by the time this reads them, leaving the quotes as the
#: shape to match.
_LITERAL = (
    r"(?:\"\"|-?(?:0[xXbBoO])?\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][-+]?\d+)?"
    r"|true|false|nil)"
)

#: `Notification.Name("activeLibraryDidChange")` — a dotted path of CAPITALISED
#: components applied to literals. Every component capitalised is what says the
#: callee is a type name, so the call is naming a value rather than reaching
#: through a lowercase member into whatever that member does.
#:
#: At least one argument is REQUIRED, which excludes `Registry()`. A custom
#: `init` can do real work, the empty argument list is the shape a singleton
#: wears, and the argument list is the only evidence available here — so the
#: zero-argument case keeps blocking.
_CONSTANT_CALL = (
    r"[A-Z]\w*(?:\.[A-Z]\w*)*\("
    rf"\s*(?:\w+\s*:\s*)?{_LITERAL}(?:\s*,\s*(?:\w+\s*:\s*)?{_LITERAL})*\s*"
    r"\)"
)

#: The whole right-hand side, and nothing else. Arithmetic a compiler would
#: fold (`60 * 60`) is deliberately not here: this reader cannot fold, and
#: what it cannot read as one value it must keep blocking on.
_CONSTANT_EXPRESSION = re.compile(rf"^(?:{_LITERAL}|{_CONSTANT_CALL})$")

#: A TYPE-LEVEL binding with an initialiser: `static let x = …`, whatever
#: attributes and access modifiers precede it. `static` is load-bearing rather
#: than incidental — see `_is_constant_declaration`.
_STATIC_BINDING = re.compile(
    r"^[^=\n]*\bstatic\b[^=\n]*\b(?:let|var)\b[^=\n]*=(?!=)(?P<rhs>[^\n]*)$"
)


def _statements(text: str) -> list[str]:
    """`text` cut at every boundary a statement cannot cross.

    A newline, a `;`, and either brace. Cutting at braces is what lets a
    one-line member — `extension N { static let x = N("a") }` — be read as the
    declaration it is instead of as a line with a stray `}` on the end.
    """
    return re.split(r"[\n;{}]", text)


def _is_constant_declaration(fragment: str) -> bool:
    """Is this a type-level binding to a value, rather than to work?

    Two conditions, and the second is the one that keeps this narrow.

    **`static`.** A type-level or global binding is emitted as a lazily
    initialised global, and the observed fact this exists to explain is that a
    file whose only members are such bindings gets NO coverage row at all —
    checked three ways against a consuming project's report, including a grep
    of the raw JSON, while sibling files in the same directory are present and
    numbered. An INSTANCE stored property is the opposite case: its initialiser
    is emitted into every `init` the type has, so it is a line a coverage run
    really does report, and `var limit: Int = 200` must keep answering True.
    `static` is the only thing in the text that tells the two apart.

    **A constant right-hand side.** A literal, or a call on a capitalised
    dotted path with at least one literal argument. Anything else — a call to
    a lowercase function, a value read from another symbol, arithmetic, a
    closure, a multi-line expression this cut into fragments — is not
    recognised, and an unrecognised initialiser goes on meaning "carries code".

    The asymmetry is the whole design: calling a live file inert passes
    unmeasured code silently, so every case this cannot read must fall through
    to blocking.
    """
    match = _STATIC_BINDING.match(fragment)
    if match is None:
        return False
    return bool(_CONSTANT_EXPRESSION.match(match.group("rhs").strip()))


#: Top-level lines that may remain once every block has been lifted out. A
#: file whose residue is only these is declarations and nothing else.
_TOP_LEVEL_RESIDUE = re.compile(
    r"^\s*(?:@[\w.]+\s*(?:\([^)]*\))?\s*)*"
    r"(?:import|typealias|protocol|enum|struct|class|actor|extension|"
    r"final|public|internal|private|fileprivate|open|indirect)\b"
)


def _strip_comments_and_strings(text: str) -> str:
    """Blank out anything whose contents are not code.

    A `//` inside a string literal and a `"` inside a comment each break the
    naive version of the other, so both are consumed in ONE left-to-right
    pass — whichever opens first wins, which is what the compiler does.
    Newlines are preserved so line-oriented checks still see the shape.

    A string's CONTENTS go and its QUOTES stay, which is what "blank" means
    here and what the sibling reducer's docstring has always said this one
    did. Dropping the quotes too erased the difference between a literal and
    nothing at all: `Notification.Name("a")` came out as `Notification.Name()`,
    indistinguishable from a bare `Registry()` call, and an initialiser with a
    string on the right came out with an empty right-hand side. Both are
    distinctions a reader downstream has to be able to make.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        two = text[i : i + 2]
        if two == "//":
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif two == "/*":
            depth, i = 1, i + 2  # Swift nests block comments
            while i < n and depth:
                if text[i : i + 2] == "/*":
                    depth, i = depth + 1, i + 2
                elif text[i : i + 2] == "*/":
                    depth, i = depth - 1, i + 2
                else:
                    if text[i] == "\n":
                        out.append("\n")
                    i += 1
        elif ch == '"':
            if text[i : i + 3] == '"""':
                j = text.find('"""', i + 3)
                chunk = text[i : n if j < 0 else j + 3]
                out.append('""' + "\n" * chunk.count("\n"))
                i = n if j < 0 else j + 3
            else:
                i += 1
                while i < n and text[i] != '"':
                    i += 2 if text[i] == "\\" else 1
                i += 1
                out.append('""')
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _swift_executable_text(source: str) -> str | None:
    """`source` with comments and layout removed and code left intact.

    Deliberately NOT `_strip_comments_and_strings`. That one blanks a string
    literal's CONTENTS, which is right for asking "is there a statement here"
    and catastrophic for asking "did the code change": `"a"` and `"b"` reduce
    to the same text, so a real edit would read as cosmetic — the one mistake
    this answer must never make. Literals are copied verbatim.

    Whitespace is normalised because re-indenting is not a code change and a
    reader who reflows a signature has changed nothing a coverage run can
    see. Line structure is not preserved for the same reason: the comparison
    is over what the file says, not over where it says it.

    ``None`` on any input this cannot read to the end — an unterminated
    literal or block comment means the reduction is missing text, and a
    truncated reduction compares equal too easily.
    """
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        two = source[i : i + 2]
        if two == "//":
            j = source.find("\n", i)
            i = n if j < 0 else j
        elif two == "/*":
            depth, i = 1, i + 2  # Swift nests block comments
            while i < n and depth:
                if source[i : i + 2] == "/*":
                    depth, i = depth + 1, i + 2
                elif source[i : i + 2] == "*/":
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                return None  # ran off the end inside a comment
        elif ch == '"':
            if source[i : i + 3] == '"""':
                j = source.find('"""', i + 3)
                if j < 0:
                    return None
                out.append(source[i : j + 3])
                i = j + 3
            else:
                start = i
                i += 1
                while i < n and source[i] != '"':
                    i += 2 if source[i] == "\\" else 1
                if i >= n:
                    return None
                i += 1
                out.append(source[start:i])
        else:
            out.append(ch)
            i += 1
    return " ".join("".join(out).split())


def _brace_blocks(text: str) -> list[tuple[str, str]]:
    """Every `{...}` as (header, own content), innermost blocks removed.

    The header is the text back to the previous statement boundary, which is
    what says whether the block is a protocol body, an accessor requirement
    or a function implementation.
    """
    blocks: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    for i, ch in enumerate(text):
        if ch == "{":
            boundary = max(text.rfind(c, 0, i) for c in "{};\n")
            stack.append((i, text[boundary + 1 : i]))
        elif ch == "}" and stack:
            start, header = stack.pop()
            blocks.append((header, text[start + 1 : i]))
    return blocks


def _without_nested_blocks(content: str) -> str:
    out, depth = [], 0
    for ch in content:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _swift_carries_executable_code(source: str) -> bool | None:
    text = _strip_comments_and_strings(source)

    # Two versions of the file exist and this reads neither of them whole.
    # Answering from one branch would be a claim about code that may not be
    # the code that compiled.
    if re.search(r"^\s*#(if|elseif|else)\b", text, re.MULTILINE):
        return None

    # An initialised binding has no brace, so no block classifies it and the
    # expression is an executable line — with one exception, which is why this
    # is a scan rather than the single search it used to be: a type-level
    # constant declares a value and earns no row. It falls through to the block
    # classification below, where its own residue is dropped, so it neither
    # proves code here nor smuggles anything past the checks there.
    for fragment in _statements(text):
        if _INITIALISED_BINDING.search(fragment) and not _is_constant_declaration(
            fragment
        ):
            return True

    for header, content in _brace_blocks(text):
        if _INERT_BLOCK_HEADER.search(header):
            continue
        # Constant declarations are dropped from a block's OWN residue for the
        # reason the file-wide scan lets them past: they are members, not
        # statements. Nothing else is dropped, so a body sitting next to one is
        # still its own block and still judged on its own header.
        own = "\n".join(
            fragment
            for fragment in _statements(_without_nested_blocks(content))
            if not _is_constant_declaration(fragment)
        ).strip()
        if not own:
            continue  # `{}` — a marker type's body holds nothing to run
        # An accessor requirement NAMES its accessors. Requiring a keyword
        # rather than accepting "no identifiers" is what tells `{ get }` from
        # `{ 44 }`, whose implicit return has no identifier in it at all and
        # which an emptiness test therefore waves through as a requirement.
        names = set(re.findall(r"[A-Za-z_]\w*", own))
        if names and names <= _ACCESSOR_ONLY:
            continue
        return True

    residue = _without_nested_blocks(text)
    for line in residue.splitlines():
        if line.strip() and not _TOP_LEVEL_RESIDUE.match(line):
            return None

    return False


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
