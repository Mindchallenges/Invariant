from pathlib import Path

import pytest

from invariant.adapters import available, defaults_for, detect, get_adapter
from invariant.adapters.base import Adapter, Hazard, TestResult


class TestRegistry:
    def test_generic_is_always_available(self):
        assert "generic" in available()

    def test_unknown_ecosystem_falls_back_to_generic(self):
        assert get_adapter("cobol").id == "generic"

    def test_swift_aliases(self):
        assert get_adapter("swift").id == get_adapter("swift_xcode").id == "swift_xcode"

    def test_defaults_for_unknown_is_empty(self):
        assert defaults_for("cobol") == {}


class TestDetection:
    def test_python_project(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        assert detect(tmp_path) == ["python"]

    def test_node_project(self, tmp_path):
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        assert detect(tmp_path) == ["node"]

    def test_xcode_project(self, tmp_path):
        (tmp_path / "App.xcworkspace").mkdir()
        assert detect(tmp_path) == ["swift_xcode"]

    def test_multiple_ecosystems_keep_a_stable_order(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        assert detect(tmp_path) == ["python", "node"]

    def test_nothing_recognised(self, tmp_path):
        assert detect(tmp_path) == ["generic"]


class TestTrustworthiness:
    def test_a_clean_pass_is_trustworthy(self):
        assert TestResult(passed=True, exit_code=0).trustworthy

    def test_a_pass_with_a_live_hazard_is_not(self):
        hazard = Hazard(id="h", summary="s", detail="d")
        assert not TestResult(passed=True, exit_code=0, hazards=[hazard]).trustworthy

    def test_a_failure_is_never_trustworthy(self):
        assert not TestResult(passed=False, exit_code=1).trustworthy


class TestGenericAdapter:
    def test_passes_through_exit_code(self):
        result = Adapter().parse_test_output("", "", 0)
        assert result.passed and result.hazards == []

    def test_parses_nothing(self):
        assert Adapter().parse_coverage("anything") == {}

    def test_does_not_narrow_the_changed_set(self):
        files = [Path("a.txt"), Path("b.swift")]
        assert Adapter().source_files(files) == files

    def test_cannot_tell_a_test_file_from_a_source_file(self):
        """Knowing a test file by sight is ecosystem knowledge, so the base
        adapter answers `None` — "cannot tell" — exactly as it does for
        `carries_executable_code`."""
        assert Adapter().is_test_file(Path("tests/test_thing.py")) is None


class TestTestFileRecognition:
    """BUG_019 — a touched test file was demanded a coverage row that the
    configured reporter is defined never to produce.

    The two wrong answers are not symmetrical, and the dangerous one is
    calling a production file a test: it would set that file aside and pass
    unmeasured code silently. So every pattern here is anchored on a naming
    convention the ecosystem actually enforces, never on the substring
    "test" appearing somewhere in a path.
    """

    def test_python_knows_its_own_conventions(self):
        adapter = get_adapter("python")
        for path in ("tests/test_rules.py", "test_rules.py", "rules_test.py",
                     "tests/conftest.py", "src/pkg/tests/test_a.py"):
            assert adapter.is_test_file(Path(path)) is True, path

    def test_python_does_not_claim_production_files(self):
        adapter = get_adapter("python")
        for path in ("src/invariant/core/rules.py", "src/contest.py",
                     "src/latest.py", "src/testing_utils.py", "protest.py"):
            assert adapter.is_test_file(Path(path)) is False, path

    def test_node_knows_its_own_conventions(self):
        adapter = get_adapter("node")
        for path in ("src/a.test.ts", "src/a.spec.tsx", "__tests__/a.ts"):
            assert adapter.is_test_file(Path(path)) is True, path
        assert adapter.is_test_file(Path("src/latest.ts")) is False

    def test_swift_knows_its_own_conventions(self):
        adapter = get_adapter("swift_xcode")
        assert adapter.is_test_file(Path("AppTests/ThingTests.swift")) is True
        assert adapter.is_test_file(Path("App/Thing.swift")) is False

    def test_an_adapter_answers_none_outside_its_own_extensions(self):
        """Adapters stack, so one that does not own a file must not answer for
        it — otherwise a mixed-ecosystem project gets the first adapter's
        opinion about the second's files."""
        assert get_adapter("python").is_test_file(Path("AppTests/ThingTests.swift")) is None
        assert get_adapter("swift_xcode").is_test_file(Path("tests/test_a.py")) is None
        assert get_adapter("node").is_test_file(Path("tests/test_a.py")) is None


class TestSwiftAdapter:
    def setup_method(self):
        self.adapter = get_adapter("swift_xcode")

    def test_narrows_to_swift_sources(self):
        files = [Path("A.swift"), Path("readme.md"), Path("B.swift")]
        assert self.adapter.source_files(files) == [Path("A.swift"), Path("B.swift")]

    def test_name_destination_hazard_fires(self):
        out = "xcodebuild test -destination 'platform=iOS Simulator, name=iPhone 16'"
        result = self.adapter.parse_test_output(out, "", 0)
        ids = {h.id for h in result.hazards}
        assert "xcode.destination_by_name_resolves_latest" in ids
        assert not result.trustworthy

    def test_zero_executed_tests_is_flagged_despite_exit_zero(self):
        # The failure mode this exists for: a filter that matches nothing
        # produces a clean exit and an empty run.
        result = self.adapter.parse_test_output("Executed 0 tests, with 0 failures", "", 0)
        assert result.passed
        assert "zero tests" in result.note
        assert not result.trustworthy

    def test_summaries_without_an_all_tests_line_take_the_last(self):
        """This test used to assert 12 + 30 = 42, which pinned the defect.

        Real xcodebuild always prints an "All tests" line that already totals
        every bundle, so a log without one is degraded output. Taking the last
        summary under-reports where summing over-reported, and under-reporting
        is the safe direction: a count inflated past what ran reads later as
        coverage that never existed.
        """
        out = "Executed 12 tests, with 0 failures\nExecuted 30 tests, with 2 failures"
        result = self.adapter.parse_test_output(out, "", 1)
        assert result.total == 30
        assert result.failed == 2

    def test_a_real_green_run_is_trustworthy(self):
        result = self.adapter.parse_test_output("Executed 42 tests, with 0 failures", "", 0)
        assert result.trustworthy
        assert result.note == ""

    def test_coverage_parsing(self):
        raw = "  Sources/App/Store.swift   87.5%\n  Sources/App/View.swift   91%"
        assert self.adapter.parse_coverage(raw) == {
            "Sources/App/Store.swift": 87.5,
            "Sources/App/View.swift": 91.0,
        }

    def test_every_hazard_has_a_summary_and_detail(self):
        for hazard in self.adapter.hazards():
            assert hazard.summary and hazard.detail
            assert hazard.id.startswith("xcode.")


class TestPythonAdapter:
    def setup_method(self):
        self.adapter = get_adapter("python")

    def test_zero_collected_is_flagged(self):
        result = self.adapter.parse_test_output("collected 0 items", "", 0)
        assert result.passed and not result.trustworthy
        assert "matched nothing" in result.note

    def test_collection_error_hazard(self):
        result = self.adapter.parse_test_output("ERROR collecting tests/x.py", "", 1)
        assert any(
            h.id == "pytest.import_error_masquerades_as_collection_error"
            for h in result.hazards
        )

    def test_counts(self):
        result = self.adapter.parse_test_output("3 failed, 7 passed in 1.2s", "", 1)
        assert result.total == 10 and result.failed == 3

    def test_coverage_parsing(self):
        raw = "src/app/core.py      120     12    90%"
        assert self.adapter.parse_coverage(raw) == {"src/app/core.py": 90.0}

    def test_coverage_parsing_branch_table(self):
        """Repro for BUG_011 — see docs/backlog/BUG_011.md.

        `[tool.coverage.run] branch = true` makes coverage.py emit
        `Name Stmts Miss Branch BrPart Cover Missing` — four numeric columns
        before the percentage — instead of the two the parser's regex
        requires. This is the real header/row shape `pytest --cov
        --cov-report=term-missing` prints in this repository, which has had
        `branch = true` since its first commit.
        """
        raw = (
            "Name                     Stmts   Miss Branch BrPart  Cover   Missing\n"
            "----------------------------------------------------------------------\n"
            "src/app/core.py            120     12     18      3    90%   45-50\n"
        )
        assert self.adapter.parse_coverage(raw) == {"src/app/core.py": 90.0}

    def test_a_whole_branch_table_is_read_row_by_row(self):
        """The full artefact, not one hand-made row.

        The `Missing` column carries line ranges and branch arrows, the rule
        line is punctuation, and the last row is a TOTAL with no file name.
        None of them is a measurement, and reading any of them as one puts a
        number nobody measured into the durable pipeline record.
        """
        raw = (
            "Name                                 Stmts   Miss Branch BrPart  Cover   Missing\n"
            "------------------------------------------------------------------------------\n"
            "src/invariant/adapters/python_.py       40      2     12      1    94%   55-56, 61->64\n"
            "src/invariant/core/rules.py            820     31    240     18    95%   102, 330->exit\n"
            "tests/conftest.py                       12      0      0      0   100%\n"
            "------------------------------------------------------------------------------\n"
            "TOTAL                                  872     33    252     19    95%\n"
        )
        assert self.adapter.parse_coverage(raw) == {
            "src/invariant/adapters/python_.py": 94.0,
            "src/invariant/core/rules.py": 95.0,
            "tests/conftest.py": 100.0,
        }

    def test_a_fractional_percentage_keeps_its_fraction(self):
        """`[tool.coverage.report] precision` adds decimals to every row.

        Truncating to the integer part reads a file at 84.6% as 84 and blocks
        against a floor it did not miss — the same class of coupling as the
        column count, in the other direction.
        """
        raw = "src/app/core.py    120   12   18   3   84.62%   45-50\n"
        assert self.adapter.parse_coverage(raw) == {"src/app/core.py": 84.62}

    def test_a_row_with_no_percentage_borrows_none_from_the_next(self):
        """A name and a percentage on different lines are not a measurement.

        The pattern may not cross a newline, so a row coverage.py did not
        finish cannot silently acquire the following row's number — which
        would be an invented figure attributed to a real file.
        """
        raw = "src/app/core.py\nsrc/app/other.py   10   0   2   0   77%\n"
        assert self.adapter.parse_coverage(raw) == {"src/app/other.py": 77.0}

    def test_a_verbose_progress_line_is_not_a_measurement(self):
        """`pytest -v` prints a percentage after a file name on every line.

        It is progress through the run, not coverage of the file, and the
        coverage command's raw output contains both. Reading it would put a
        number nobody measured into the pipeline record, and it reads HIGH as
        often as low — the direction `Hazard.can_overstate` says must never be
        tolerated. A table row is a name followed by numeric cells; this is a
        name followed by a test id.
        """
        raw = (
            "tests/test_app.py::test_one PASSED                       [ 50%]\n"
            "tests/test_app.py::test_two PASSED                       [100%]\n"
        )
        assert self.adapter.parse_coverage(raw) == {}

    def test_prose_mentioning_a_module_is_not_a_measurement(self):
        raw = "collected 4 items\nERROR collecting tests/test_app.py\n"
        assert self.adapter.parse_coverage(raw) == {}


class TestPythonMeasuredScope:
    """BUG_019 — the fact the coverage rule had no way to learn.

    `source_files` counts every `.py` as source, so a session touching its own
    tests puts them in the touched set; `[tool.coverage.run] source` means the
    reporter is DEFINED never to measure them. The gate then demands a row that
    no amount of work can produce. The rule keeps the policy, so what is added
    here is only the fact: which paths this project's reporter is scoped to.

    Every "cannot tell" answers `None`, which relaxes nothing — the same
    obligation `carries_executable_code` carries, for the same reason: calling
    a measured file unmeasured passes unmeasured code silently.
    """

    def setup_method(self):
        self.adapter = get_adapter("python")

    def project(self, tmp_path, body, dirs=("src/app",)):
        for rel in dirs:
            (tmp_path / rel).mkdir(parents=True, exist_ok=True)
        (tmp_path / "pyproject.toml").write_text(body, encoding="utf-8")
        return tmp_path

    def test_a_configured_source_directory_is_the_scope(self, tmp_path):
        root = self.project(
            tmp_path, '[tool.coverage.run]\nsource = ["src/app"]\n'
        )
        assert self.adapter.measured_scope(root) == [root / "src/app"]

    def test_several_configured_directories_are_all_in_scope(self, tmp_path):
        root = self.project(
            tmp_path,
            '[tool.coverage.run]\nsource = ["src/app", "src/lib"]\n',
            dirs=("src/app", "src/lib"),
        )
        assert self.adapter.measured_scope(root) == [
            root / "src/app",
            root / "src/lib",
        ]

    def test_no_pyproject_means_cannot_tell(self, tmp_path):
        assert self.adapter.measured_scope(tmp_path) is None

    def test_no_coverage_config_means_cannot_tell(self, tmp_path):
        root = self.project(tmp_path, '[project]\nname = "x"\n')
        assert self.adapter.measured_scope(root) is None

    def test_an_unparseable_pyproject_means_cannot_tell(self, tmp_path):
        root = self.project(tmp_path, "this is not toml [[[\n")
        assert self.adapter.measured_scope(root) is None

    def test_a_bare_package_name_means_cannot_tell(self, tmp_path):
        """`source = ["app"]` may name an installed package rather than a
        directory in this checkout. Resolving it would be a guess, and the
        wrong guess narrows the scope and excuses a real file."""
        root = self.project(tmp_path, '[tool.coverage.run]\nsource = ["app"]\n')
        assert self.adapter.measured_scope(root) is None

    def test_source_pkgs_alongside_source_means_cannot_tell(self, tmp_path):
        """`source_pkgs` WIDENS the real scope beyond the directories named in
        `source`. Reporting only the latter would understate it, and an
        understated scope is the direction that excuses an unmeasured file."""
        root = self.project(
            tmp_path,
            '[tool.coverage.run]\nsource = ["src/app"]\nsource_pkgs = ["other"]\n',
        )
        assert self.adapter.measured_scope(root) is None

    def test_the_base_adapter_cannot_tell(self, tmp_path):
        assert get_adapter("generic").measured_scope(tmp_path) is None


class TestNodeAdapter:
    def setup_method(self):
        self.adapter = get_adapter("node")

    def test_missing_script_hazard(self):
        result = self.adapter.parse_test_output("", "npm ERR! Missing script: test", 1)
        assert any(h.id.startswith("npm.") for h in result.hazards)

    def test_no_tests_found_hazard(self):
        result = self.adapter.parse_test_output("No tests found", "", 0)
        assert result.passed and not result.trustworthy

    def test_counts(self):
        result = self.adapter.parse_test_output("Tests:  2 failed, 8 total", "", 1)
        assert result.total == 8


def test_every_shipped_adapter_declares_unique_hazard_ids():
    seen: set[str] = set()
    for name in available():
        for hazard in get_adapter(name).hazards():
            assert hazard.id not in seen or name in {"swift", "swift_xcode"}
            seen.add(hazard.id)



class TestXcodebuildCountsAreNotSummed:
    """xcodebuild prints one summary per nesting level for a single run.

    Summing them multiplied the real figures by the nesting depth, and the
    result lands in the durable pipeline record. An inflated count on a green
    phase is indistinguishable afterwards from a run that genuinely executed
    that many tests, which is the exact shape of lie this project's discipline
    exists to catch.
    """

    ADAPTER = get_adapter("swift")

    REAL_RUN = """
Test Suite 'UnifiedDrainFailureVerdictReproTests' failed at 2026-08-09.
	 Executed 6 tests, with 4 failures (0 unexpected) in 6.524 (6.528) seconds
Test Suite 'HeapTests.xctest' failed at 2026-08-09.
	 Executed 6 tests, with 4 failures (0 unexpected) in 6.524 (6.529) seconds
Test Suite 'All tests' failed at 2026-08-09.
	 Executed 6 tests, with 4 failures (0 unexpected) in 6.524 (6.530) seconds
"""

    def test_the_reported_run_gives_its_true_numbers(self):
        """The case as observed: 6 tests, reported as 18 executed / 12 failed."""
        result = self.ADAPTER.parse_test_output(self.REAL_RUN, "", 65)
        assert result.total == 6
        assert result.failed == 4

    def test_a_single_summary_is_read_directly(self):
        text = "\t Executed 3 tests, with 0 failures (0 unexpected) in 1.0 (1.1) seconds\n"
        result = self.ADAPTER.parse_test_output(text, "", 0)
        assert result.total == 3
        assert result.failed == 0

    def test_the_outermost_total_wins_over_per_target_lines(self):
        """Several test targets: 'All tests' already totals them.

        This is why "take the last match" alone would not do — per-target
        summaries genuinely should add up, and the outermost line is where
        xcodebuild has already added them.
        """
        text = (
            "Test Suite 'AlphaTests.xctest' passed at 2026-08-09.\n"
            "\t Executed 4 tests, with 0 failures (0 unexpected) in 1.0 (1.1) seconds\n"
            "Test Suite 'BetaTests.xctest' passed at 2026-08-09.\n"
            "\t Executed 7 tests, with 0 failures (0 unexpected) in 2.0 (2.1) seconds\n"
            "Test Suite 'All tests' passed at 2026-08-09.\n"
            "\t Executed 11 tests, with 0 failures (0 unexpected) in 3.0 (3.1) seconds\n"
        )
        result = self.ADAPTER.parse_test_output(text, "", 0)
        assert result.total == 11

    def test_the_last_summary_is_the_fallback_without_an_all_tests_line(self):
        text = (
            "Test Suite 'OneTests' passed.\n"
            "\t Executed 2 tests, with 0 failures (0 unexpected) in 1.0 (1.1) seconds\n"
            "Test Suite 'OneTests.xctest' passed.\n"
            "\t Executed 2 tests, with 0 failures (0 unexpected) in 1.0 (1.2) seconds\n"
        )
        assert self.ADAPTER.parse_test_output(text, "", 0).total == 2

    def test_two_aggregates_are_summed_even_when_it_might_be_a_rerun(self):
        """Two "All tests" blocks are ambiguous, and the tie goes to summing.

        They mean parallel clones (which must be summed) or a rerun in one log
        (which arguably should not be). The readings fail in opposite
        directions, and only one of those directions is safe:

        * Not summing clones under-reports catastrophically — a ~13,900-test
          run read as 34 executed, 0 failed, and a rule called it a trustworthy
          green. Silent, and it opens the gate.
        * Summing a rerun over-reports and carries the earlier failures
          forward, so a suite that went red then green reads red. Visible,
          annoying, and it makes someone look at a suite that needed a retry.

        This test previously asserted the opposite and had to be changed;
        keeping it would have pinned the unsafe direction.
        """
        text = (
            "Test Suite 'All tests' failed.\n"
            "\t Executed 6 tests, with 4 failures (0 unexpected) in 6.5 (6.5) seconds\n"
            "Test Suite 'All tests' passed.\n"
            "\t Executed 6 tests, with 0 failures (0 unexpected) in 6.4 (6.4) seconds\n"
        )
        result = self.ADAPTER.parse_test_output(text, "", 0)
        assert (result.total, result.failed) == (12, 4)

    def test_parallel_clones_are_summed(self):
        """The reported case: one aggregate per clone, last one tiny."""
        text = "".join(
            f"Test Suite 'All tests' failed.\n"
            f"\t Executed {n} tests, with {f} failures (0 unexpected) in 1.0 (1.0) seconds\n"
            for n, f in [(6000, 300), (7000, 300), (34, 0)]
        )
        result = self.ADAPTER.parse_test_output(text, "", 65)
        assert result.total == 13034
        assert result.failed == 600
        assert not result.trustworthy

    def test_no_summary_at_all_reports_zero_and_is_not_trusted(self):
        """Zero executed on a clean exit stays the untrustworthy case."""
        result = self.ADAPTER.parse_test_output("nothing here", "", 0)
        assert result.total == 0
        assert not result.trustworthy



class TestSwiftCoverageCommand:
    """The slot existed and was empty, so the gate reading it could never pass."""

    ADAPTER = get_adapter("swift")

    def test_a_coverage_command_is_supplied(self):
        assert self.ADAPTER.default_commands().get("coverage")

    def test_it_produces_a_result_bundle_before_reading_one(self):
        commands = self.ADAPTER.default_commands()
        assert "-enableCodeCoverage YES" in commands["coverage"]
        assert "xccov" in commands["coverage_report"]

    def test_the_bundle_path_is_a_placeholder_not_a_fixed_path(self):
        """A fixed path would be shared state between concurrent sessions."""
        commands = self.ADAPTER.default_commands()
        assert "{result_bundle}" in commands["coverage"]
        assert "{result_bundle}" in commands["coverage_report"]

    def test_its_output_is_what_parse_coverage_already_reads(self):
        raw = (
            "    /Users/x/Heap/Frameworks/Intake/IntakeDrainDisposition.swift"
            "    100.00% (55/55)\n"
            "    /Users/x/Heap/Frameworks/Intake/FileBytesIngester.swift"
            "     56.25% (56/99)\n"
        )
        parsed = self.ADAPTER.parse_coverage(raw)
        assert parsed == {
            "/Users/x/Heap/Frameworks/Intake/IntakeDrainDisposition.swift": 100.0,
            "/Users/x/Heap/Frameworks/Intake/FileBytesIngester.swift": 56.25,
        }



class TestCoverageDoesNotRequireAGreenSuite:
    """`&&` made the report conditional on a full-suite pass.

    That silently reimposed `test.all_pass` as a block on any project that had
    deliberately downgraded it — and a project whose main branch is not green
    is precisely the one that downgraded it. The result was that such a project
    could never obtain coverage at all, through a door nobody had opened on
    purpose.

    `;` cured that and introduced its own lie, because the shell then reports
    the READER's status as the run's. Neither operator can express two
    independent results, so the two legs are two commands.
    """

    ADAPTER = get_adapter("swift")

    def test_the_report_step_is_not_chained_at_all(self):
        command = self.ADAPTER.default_commands()["coverage"]
        assert "&&" not in command
        assert ";" not in command
        assert "xccov" not in command

    def test_the_report_is_its_own_command(self):
        commands = self.ADAPTER.default_commands()
        assert commands.get("coverage_report")
        assert "xccov" in commands["coverage_report"]



class TestHazardDistortionDirection:
    def test_an_unclassified_hazard_is_assumed_dangerous(self):
        """The default has to be the safe one.

        A hazard added without thought would otherwise inherit "annotate" and
        go quiet, which is the direction that passes uncovered work.
        """
        assert Hazard(id="h", summary="s", detail="d").may_overstate

    def test_an_understating_hazard_says_so(self):
        assert not Hazard(
            id="h", summary="s", detail="d", can_overstate=False
        ).may_overstate

    def test_every_shipped_swift_hazard_is_classified_deliberately(self):
        """Unclassified is allowed, but only where it is the honest answer.

        `destination_by_name` stays unclassified because resolving to another
        runtime means the measurement describes something else, and "something
        else" has no direction — so it takes the safe default.
        """
        by_id = {h.id: h for h in get_adapter("swift").hazards()}
        assert by_id["xcode.exit65_drops_suites"].can_overstate is False
        assert by_id["xcode.only_testing_takes_target_class"].can_overstate is False
        assert by_id["xcode.destination_by_name_resolves_latest"].can_overstate is None


class TestFailingCaseCount:
    """Failing ASSERTIONS and failing TESTS are different numbers."""

    ADAPTER = get_adapter("swift")

    RUN = """
Test Case '-[HeapTests.DrainTests testThrowsOnEmpty]' failed (0.01 seconds).
Test Case '-[HeapTests.DrainTests testVerdictShape]' failed (0.02 seconds).
Test Case '-[HeapTests.DrainTests testVerdictShape]' failed (0.02 seconds).
Test Suite 'All tests' failed at 2026-08-09.
\t Executed 14 tests, with 6 failures (0 unexpected) in 1.0 (1.1) seconds
"""

    def test_distinct_failing_cases_are_counted(self):
        result = self.ADAPTER.parse_test_output(self.RUN, "", 65)
        assert result.failed == 6        # assertions, xcodebuild's own count
        assert result.failed_tests == 2  # distinct cases, deduplicated

    def test_the_assertion_count_is_left_in_its_own_units(self):
        """Converting it would invent a number xcodebuild never reported."""
        assert self.ADAPTER.parse_test_output(self.RUN, "", 65).failed == 6

    def test_a_log_naming_no_cases_reports_none_not_zero(self):
        text = "\t Executed 14 tests, with 6 failures (0 unexpected) in 1.0 (1.1) s\n"
        assert self.ADAPTER.parse_test_output(text, "", 65).failed_tests is None

    def test_a_green_run_names_no_failing_cases(self):
        text = "\t Executed 14 tests, with 0 failures (0 unexpected) in 1.0 (1.1) s\n"
        assert self.ADAPTER.parse_test_output(text, "", 0).failed_tests is None


class TestResultBundleIsPreferredOverConsole:
    """The console is a rendering of a run; the bundle is the run's own record.

    Scraping the rendering was wrong twice in one day, in both directions —
    over-counting by nesting depth, then under-counting by 400x because
    parallel clones each print their own aggregate. The bundle reports test
    CASES and does not change shape with parallelism.
    """

    ADAPTER = get_adapter("swift")

    CONSOLE = (
        "Test Suite 'All tests' failed at 2026-08-09.\n"
        "\t Executed 34 tests, with 0 failures (0 unexpected) in 1.0 (1.0) seconds\n"
    )

    class FakeAdapter:
        pass

    def adapter_returning(self, counts):
        from invariant.adapters.swift_xcode import SwiftXcodeAdapter

        class Stub(SwiftXcodeAdapter):
            def result_bundle_counts(self, path):
                return counts

        return Stub()

    def test_bundle_counts_win_over_the_console(self):
        adapter = self.adapter_returning(
            {"total": 13950, "failed": 625, "skipped": 2, "result": "Failed",
             "device": "Heap-wt-mine (ABC)"}
        )
        result = adapter.read_run(self.CONSOLE, "", 65, bundle="/some/path")
        assert result.total == 13950
        assert result.failed == 625
        assert result.failed_tests == 625  # test CASES, the unit a reader expects
        assert result.skipped == 2

    def test_the_source_is_recorded_not_assumed(self):
        adapter = self.adapter_returning({"total": 10, "failed": 0, "result": "Passed"})
        assert adapter.read_run(self.CONSOLE, "", 0, bundle="/p").source == "result_bundle"

    def test_the_device_is_carried_out_of_the_bundle(self):
        """Two sessions ran on a peer's simulator with no way to tell."""
        adapter = self.adapter_returning(
            {"total": 10, "failed": 0, "result": "Passed", "device": "Heap-wt-mine (ABC)"}
        )
        assert adapter.read_run(self.CONSOLE, "", 0, bundle="/p").device == "Heap-wt-mine (ABC)"

    def test_an_unreadable_bundle_falls_back_to_the_console_and_says_so(self):
        """The fallback must be visible, not silent."""
        adapter = self.adapter_returning(None)
        result = adapter.read_run(self.CONSOLE, "", 65, bundle="/missing")
        assert result.total == 34
        assert result.source == "console"

    def test_no_bundle_at_all_is_the_console_path(self):
        result = self.ADAPTER.read_run(self.CONSOLE, "", 65, bundle=None)
        assert result.source == "console"

    def test_a_bundle_saying_failed_overrides_a_zero_exit(self):
        """Exit zero with a failed bundle is a contradiction, resolved toward red."""
        adapter = self.adapter_returning(
            {"total": 100, "failed": 3, "result": "Failed"}
        )
        result = adapter.read_run(self.CONSOLE, "", 0, bundle="/p")
        assert not result.passed
        assert "exited zero" in result.note

    def test_the_exit_code_still_decides_a_build_failure(self):
        """A build that died before any test ran writes no usable bundle."""
        adapter = self.adapter_returning(None)
        assert not adapter.read_run("error: build failed", "", 65, bundle="/p").passed


class TestBundleReadFailureIsLoud:
    """A reader that silently falls back is a reader that is not there.

    This one WAS inert for a day: `xcrun` resolves through `xcode-select`, and
    on a machine pointing at CommandLineTools it cannot find xcresulttool at
    all — so every run fell back to the console parser the bundle read was
    built to replace, with nothing saying so.
    """

    def adapter_returning(self, counts):
        from invariant.adapters.swift_xcode import SwiftXcodeAdapter

        class Stub(SwiftXcodeAdapter):
            def result_bundle_counts(self, path):
                return counts

        return Stub()

    def test_an_existing_but_unreadable_bundle_says_so(self, tmp_path):
        bundle = tmp_path / "run.xcresult"
        bundle.mkdir()
        result = self.adapter_returning(None).read_run(
            "\t Executed 34 tests, with 0 failures (0 unexpected) in 1.0 (1.0) s\n",
            "",
            65,
            bundle=bundle,
        )
        assert "could not be read" in result.note
        assert result.source == "console"

    def test_an_absent_bundle_is_not_reported_as_unreadable(self, tmp_path):
        """No bundle and an unreadable bundle are different facts."""
        result = self.adapter_returning(None).read_run(
            "\t Executed 34 tests, with 0 failures (0 unexpected) in 1.0 (1.0) s\n",
            "",
            65,
            bundle=tmp_path / "never-written.xcresult",
        )
        assert "could not be read" not in (result.note or "")

    def test_a_readable_bundle_carries_no_such_note(self, tmp_path):
        bundle = tmp_path / "run.xcresult"
        bundle.mkdir()
        result = self.adapter_returning(
            {"total": 13950, "failed": 625, "result": "Failed"}
        ).read_run("", "", 65, bundle=bundle)
        assert "could not be read" not in (result.note or "")
        assert result.total == 13950


class TestDeveloperDirFallback:
    def test_a_developer_dir_is_locatable_on_this_platform(self):
        """Guards the retry path that made the reader work here at all."""
        from invariant.adapters.swift_xcode import _newest_developer_dir

        found = _newest_developer_dir()
        assert found is None or found.name == "Developer"


class TestAdapterSelfCheck:
    """Reachable FROM HERE, not "installed somewhere".

    A reader shipped as ground truth was inert on the machine it was written
    for, because the tool it calls could not be found the way the caller
    resolves it — and the check that passed had been run with a variable
    exported by hand. A verification inherits its environment, and the
    environment is part of what is being asserted.
    """

    def test_the_generic_adapter_claims_nothing(self):
        """An adapter with no tooling has no reachability to assert."""
        assert get_adapter("generic").self_check() == []

    def test_swift_reports_every_tool_the_commands_invoke(self):
        labels = [row[0] for row in get_adapter("swift").self_check()]
        assert any("xcodebuild" in label for label in labels)
        assert any("xcresulttool" in label for label in labels)
        assert any("xccov" in label for label in labels)

    def test_the_tools_checked_are_the_tools_the_commands_run(self):
        """Every tool in every command slot, not the ones on the obvious slot.

        xccov is invoked by `coverage_report` and by nothing else, and it was
        unchecked while `doctor` reported a healthy toolchain — so the coverage
        gate's binary could be missing with no instrument saying so. Where
        xcode-select answers CommandLineTools there is no xccov and xcrun exits
        72, which reads exactly like "the bundle produced no per-file
        measurements", and it was misdiagnosed twice.

        Deriving the expected set from the command templates is what keeps the
        checked tools and the run tools from drifting apart again — including
        when a command is split into a new slot, as this one was.
        """
        adapter = get_adapter("swift")
        commands = " ".join(adapter.manifest_defaults["commands"].values())
        # Labels read "xcode: <tool> reachable".
        checked = {label.split()[-2] for label, _, _ in adapter.self_check()}

        for tool in ("xcodebuild", "xccov"):
            assert tool in commands, f"{tool} is no longer invoked; update this test"
            assert tool in checked, f"{tool} is run but never checked for reachability"

    def test_a_fallback_resolution_is_reported_as_such(self, monkeypatch):
        """Working via fallback is not the same as working, and says so.

        It means every caller that does NOT share this process's fallback sees
        something different — which is exactly how the inert reader hid.
        """
        import invariant.adapters.swift_xcode as swift

        monkeypatch.delenv("DEVELOPER_DIR", raising=False)
        monkeypatch.setattr(
            swift, "_tool_is_reachable", lambda tool, dd: dd is not None
        )
        monkeypatch.setattr(swift, "_newest_developer_dir", lambda: Path("/X/Developer"))

        rows = get_adapter("swift").self_check()
        assert all(ok for _, ok, _ in rows)
        assert all("NOT via xcrun" in detail for _, _, detail in rows)
        assert all("DEVELOPER_DIR" in detail for _, _, detail in rows)

    def test_an_unreachable_tool_fails_the_check(self, monkeypatch):
        import invariant.adapters.swift_xcode as swift

        monkeypatch.delenv("DEVELOPER_DIR", raising=False)
        monkeypatch.setattr(swift, "_tool_is_reachable", lambda tool, dd: False)
        monkeypatch.setattr(swift, "_newest_developer_dir", lambda: None)

        rows = get_adapter("swift").self_check()
        assert rows and not any(ok for _, ok, _ in rows)
        assert all("scraped" in detail or "unavailable" in detail for _, _, detail in rows)

    def test_a_clean_resolution_is_not_annotated(self, monkeypatch):
        import invariant.adapters.swift_xcode as swift

        monkeypatch.setattr(swift, "_tool_is_reachable", lambda tool, dd: dd is None)
        rows = get_adapter("swift").self_check()
        assert all(ok and detail == "via xcrun" for _, ok, detail in rows)


class TestCarriesExecutableCode:
    """Explaining an ABSENT coverage row, not predicting a present one.

    A coverage reporter emits nothing at all for a file with no executable
    lines — a bare protocol, an enum of cases, a typealias. That absence is
    indistinguishable, to the gate, from a file that was never measured, and
    the two need opposite handling: the first is vacuously fine, the second
    is exactly what a coverage floor exists to catch.

    So this answers one narrow question — "is this absence explained by the
    file having no executable lines?" — and the safe direction is asymmetric.
    Saying "has code" about an inert file costs a spurious block, which is
    visible and sends someone to look. Saying "no code" about a live file
    passes unmeasured code silently. Every uncertain case must therefore
    answer True or None, never False.
    """

    def setup_method(self):
        self.adapter = get_adapter("swift_xcode")

    def write(self, tmp_path, name, body):
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        return path

    # -- the shapes that genuinely cannot be measured -------------------

    def test_a_bare_protocol_carries_no_executable_code(self, tmp_path):
        path = self.write(
            tmp_path,
            "IntakeDrainRunner.swift",
            """
            import Foundation

            protocol IntakeDrainRunner: AnyObject {
                func pumpNow(batchLimit: Int) async
                func pauseForLibrarySwitchAndAwaitIdle() async
                var currentPauseReason: IntakePauseReason? { get async }
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is False

    def test_a_stored_property_only_struct_carries_no_executable_code(self, tmp_path):
        """A value type of declarations. Swift permits no statement in a type
        body, so there is nothing here for a coverage run to report — and the
        file that exposed this blocked a change whose only edit to it was a
        comment."""
        path = self.write(
            tmp_path,
            "WatchedOriginRefreshOutcome.swift",
            """
            import Foundation

            struct WatchedOriginRefreshOutcome: Equatable, Sendable {
                let originID: Int
                let username: String
                let platform: String
                let attemptedAt: Date
                let success: Bool
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is False

    def test_a_struct_wrapping_a_bare_enum_is_inert(self, tmp_path):
        """The nested declaration is a member, not a body."""
        path = self.write(
            tmp_path,
            "WatchedOrigin.swift",
            """
            struct WatchedOrigin: Identifiable, Hashable {
                let id: Int
                var cadenceMinutes: Int
                var lastChecked: Date?
                let action: Action

                enum Action: String, Codable, Hashable {
                    case review
                    case autoDownload
                }
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is False

    def test_a_stored_property_only_class_is_inert(self, tmp_path):
        path = self.write(
            tmp_path,
            "Handle.swift",
            """
            final class Handle: NSObject {
                let libraryID: UUID
                var lastOpened: Date?
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is False

    def test_an_enum_of_bare_cases_carries_no_executable_code(self, tmp_path):
        path = self.write(
            tmp_path,
            "IntakePauseReason.swift",
            """
            import Foundation

            enum IntakePauseReason: String, Equatable, Sendable {
                case librarySwitch
                case userPaused
                case cellular
            }

            typealias PhotosImportPauseReason = IntakePauseReason
            """,
        )
        assert self.adapter.carries_executable_code(path) is False

    def test_a_raw_value_enum_is_still_inert(self, tmp_path):
        """Raw values are compile-time constants, not executable lines."""
        path = self.write(
            tmp_path,
            "Kind.swift",
            """
            enum Kind: String {
                case photo = "photo"
                case video = "video"
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is False

    def test_a_comment_mentioning_func_does_not_count(self, tmp_path):
        path = self.write(
            tmp_path,
            "Seam.swift",
            """
            //  The drain's `func pumpNow()` body { lives elsewhere }.
            protocol Seam {
                func pumpNow() async
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is False

    # -- everything else must NOT be called inert -----------------------

    def test_a_function_body_carries_executable_code(self, tmp_path):
        path = self.write(
            tmp_path,
            "Runner.swift",
            """
            struct Runner {
                func pumpNow() { print("go") }
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is True

    def test_a_class_initialiser_body_carries_executable_code(self, tmp_path):
        """Skipping a type's HEADER must not skip its members' bodies."""
        path = self.write(
            tmp_path,
            "Registry.swift",
            """
            final class Registry {
                let root: URL

                init(root: URL) {
                    self.root = root
                }
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is True

    def test_a_deinit_body_carries_executable_code(self, tmp_path):
        """`deinit` is the member a class has and a struct cannot."""
        path = self.write(
            tmp_path,
            "Handle.swift",
            """
            final class Handle {
                var raw: OpaquePointer?

                deinit { sqlite3_close(raw) }
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is True

    def test_an_actor_method_body_carries_executable_code(self, tmp_path):
        path = self.write(
            tmp_path,
            "DrainActor.swift",
            """
            actor DrainActor {
                var claimed: Int

                func claim() { claimed += 1 }
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is True

    def test_a_computed_property_with_statements_carries_code(self, tmp_path):
        """`{ get }` is a requirement; `{ 1 }` is an implementation."""
        path = self.write(
            tmp_path,
            "Sizes.swift",
            """
            enum Sizes {
                var thumbnail: Int { 44 }
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is True

    def test_a_stored_property_initialiser_carries_code(self, tmp_path):
        path = self.write(
            tmp_path,
            "Defaults.swift",
            """
            struct Defaults {
                var limit: Int = 200
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is True

    def test_a_property_observer_carries_code(self, tmp_path):
        path = self.write(
            tmp_path,
            "Observed.swift",
            """
            struct Observed {
                var count: Int {
                    didSet { recompute() }
                }
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is True

    def test_a_protocol_extension_with_a_default_body_carries_code(self, tmp_path):
        """The shape most likely to be mistaken for a bare protocol."""
        path = self.write(
            tmp_path,
            "SeamDefaults.swift",
            """
            protocol Seam {
                func pumpNow() async
            }

            extension Seam {
                func pumpNow() async { await drain() }
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is True

    def test_a_conditional_compilation_block_is_not_claimed_inert(self, tmp_path):
        """Two versions of the file exist; this reads only one of them."""
        path = self.write(
            tmp_path,
            "Forwarder.swift",
            """
            #if DEBUG
            protocol Seam { func a() }
            #else
            protocol Seam { func b() }
            #endif
            """,
        )
        assert self.adapter.carries_executable_code(path) is not False

    def test_a_nested_block_comment_hides_what_looks_like_code(self, tmp_path):
        """Swift nests `/* */`, so the first `*/` does not end the comment."""
        path = self.write(
            tmp_path,
            "Commented.swift",
            """
            /* the old shape:
               /* func pumpNow() { drain() } */
               kept for reference */
            protocol Seam {
                func pumpNow() async
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is False

    def test_a_multiline_string_is_not_read_as_code(self, tmp_path):
        """Braces and slashes inside a `\"\"\"` literal are text, not syntax."""
        path = self.write(
            tmp_path,
            "Fixture.swift",
            '''
            enum Fixture: String {
                case payload = """
                    { "note": "// not a comment", "closing": "}" }
                    """
            }
            ''',
        )
        assert self.adapter.carries_executable_code(path) is False

    def test_a_marker_type_with_an_empty_body_is_inert(self, tmp_path):
        """`{}` is a block, and there is nothing inside it to run."""
        path = self.write(
            tmp_path,
            "Marker.swift",
            """
            protocol Marker {}

            struct IntakeToken: Marker {}
            """,
        )
        assert self.adapter.carries_executable_code(path) is False

    # -- a type-level constant is a declaration, not a statement --------

    def test_an_extension_of_constant_notification_names_is_inert(self, tmp_path):
        """The shape that exposed this: seven bare protocols and two extensions
        holding nothing but `static let` constants. Confirmed absent from every
        target of the consuming project's coverage report three ways — the
        engine's own recorded evidence, a direct `xccov --json` extraction over
        nine targets, and a grep of the raw 23 MB report — while sibling files
        in the same directory are present and numbered."""
        path = self.write(
            tmp_path,
            "LibrarySwapPorts.swift",
            """
            import Foundation

            protocol LibrarySwitchableMediaStore: AnyObject {
                func prepareForLibrarySwitch() async
                func adoptLibrary(at root: URL) async throws
            }

            protocol LibrarySwitchableEncryptor: AnyObject {
                func rekeyForLibrary(_ libraryID: UUID) throws
            }

            extension Notification.Name {
                static let activeLibraryDidChange = Notification.Name("activeLibraryDidChange")
                static let libraryRegistryDidChange = Notification.Name("libraryRegistryDidChange")
                static let blobReadDidFail404 = Notification.Name("blobReadDidFail404")
            }

            extension Notification {
                static let blobRelativePathKey = "blobRelativePath"
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is False

    def test_one_constant_static_let_in_an_extension_is_inert(self, tmp_path):
        """The defect isolated to a single line. `extension` is deliberately
        not an inert BLOCK HEADER — its residue can be a member list only a
        body explains — so this member has to be recognised as a declaration
        on its own terms rather than by where it sits."""
        path = self.write(
            tmp_path,
            "Names.swift",
            'extension Notification.Name { static let x = Notification.Name("abc") }\n',
        )
        assert self.adapter.carries_executable_code(path) is False

    def test_a_static_string_constant_is_inert(self, tmp_path):
        """A bare literal, with no constructor call to recognise. The real file
        carries one of these next to its `Notification.Name` constants, so a
        rule that took only the call shape would still have blocked it."""
        path = self.write(
            tmp_path,
            "Keys.swift",
            """
            enum PayloadKeys {
                static let blobRelativePath = "blobRelativePath"
                static let attempt = 0
                static let verbose = false
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is False

    # -- one bit away from each of those, and each must still block -----

    def test_a_static_let_from_a_call_carries_code(self, tmp_path):
        """The one-bit control. Identical to the inert extension above except
        for one right-hand side, which is a call rather than a constant — if
        this ever answers False the relaxation has stopped being narrow."""
        path = self.write(
            tmp_path,
            "Names.swift",
            """
            extension Notification.Name {
                static let activeLibraryDidChange = Notification.Name("activeLibraryDidChange")
                static let derived = computeChannelName()
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is True

    def test_a_static_let_derived_from_another_symbol_carries_code(self, tmp_path):
        """No literal anywhere in the initialiser: reading another declaration
        is work, and the reader has no way to know how much."""
        path = self.write(
            tmp_path,
            "Names.swift",
            """
            extension Notification.Name {
                static let mirrored = Notification.Name(Channels.active)
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is True

    def test_a_zero_argument_constructor_is_not_a_constant(self, tmp_path):
        """`Foo()` is the shape a custom `init` doing real work wears, and a
        singleton is exactly where it appears. Excluded deliberately: the
        argument list is the only evidence available here that the call is
        naming a value rather than building one."""
        path = self.write(
            tmp_path,
            "Registry.swift",
            """
            enum Services {
                static let registry = Registry()
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is not False

    def test_arithmetic_in_a_static_initialiser_carries_code(self, tmp_path):
        """Constant-foldable to a compiler, not to this reader. Anything it
        cannot read as one literal keeps blocking."""
        path = self.write(
            tmp_path,
            "Limits.swift",
            """
            enum Limits {
                static let window = 60 * 60
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is True

    def test_an_instance_stored_property_constant_still_carries_code(self, tmp_path):
        """The asymmetry that keeps this narrow. An instance initialiser is
        emitted into every `init` the type has, so it is a line a coverage run
        really does report — unlike a type-level binding, which is a lazily
        initialised global. Only the second is relaxed, and `static` is what
        tells them apart."""
        path = self.write(
            tmp_path,
            "Defaults.swift",
            """
            struct Defaults {
                let name = "inbox"
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is True

    def test_a_constant_beside_a_body_still_carries_code(self, tmp_path):
        """Dropping the constant from a block's residue must not drop the
        block: the function next to it is its own block with its own header."""
        path = self.write(
            tmp_path,
            "Channels.swift",
            """
            extension Notification.Name {
                static let active = Notification.Name("active")

                static func named(_ raw: String) -> Notification.Name {
                    Notification.Name(raw)
                }
            }
            """,
        )
        assert self.adapter.carries_executable_code(path) is True

    def test_a_file_scope_constant_is_not_claimed_inert(self, tmp_path):
        """A global at file scope is a real initialiser rather than a type
        member, and this reader has no evidence about what a coverage run does
        with it. Unrelaxed on purpose."""
        path = self.write(
            tmp_path,
            "Globals.swift",
            'let defaultChannel = "inbox"\n',
        )
        assert self.adapter.carries_executable_code(path) is not False

    def test_an_unrecognised_top_level_shape_is_unknown_not_inert(self, tmp_path):
        """A residue this reader cannot classify answers None, never False.

        Top-level statements are the case that made this matter: a `main.swift`
        runs its own body, and a reader that shrugged "no blocks, therefore no
        code" would excuse the file from the floor entirely.
        """
        path = self.write(tmp_path, "main.swift", 'ingest(from: "inbox")\n')
        assert self.adapter.carries_executable_code(path) is None

    def test_an_unreadable_file_is_unknown_rather_than_inert(self, tmp_path):
        assert self.adapter.carries_executable_code(tmp_path / "gone.swift") is None

    def test_a_non_swift_file_is_not_this_adapters_question(self, tmp_path):
        """Unless it is a resource type Xcode is known never to compile.

        A markdown file is not one: `.md` is not an Xcode file kind at all,
        and `core/rules` already holds documentation suffixes out of the
        changed-source set one layer up. Answering here would be this adapter
        claiming a fact that is neither its ecosystem's nor needed.
        """
        path = self.write(tmp_path, "notes.md", "# not swift")
        assert self.adapter.carries_executable_code(path) is None

    # -- resource file kinds no Xcode target ever compiles ----------------
    #
    # The same question as a bare protocol's, asked about the file's KIND
    # rather than its contents: `xccov` measures compiled Swift/ObjC/C, so a
    # property list can never earn a row no matter what is written in it, and
    # a floor demanding one is a gate no test can clear.

    @pytest.mark.parametrize(
        "name",
        [
            "Info.plist",
            "Heap.entitlements",
            "Localizable.strings",
            "Plurals.stringsdict",
            "Localizable.xcstrings",
            "Main.storyboard",
            "LaunchScreen.xib",
            "Release.xcconfig",
            "Assets.xcassets",
        ],
    )
    def test_an_xcode_resource_kind_carries_no_executable_code(self, tmp_path, name):
        path = self.write(tmp_path, name, "<resource/>")
        assert self.adapter.carries_executable_code(path) is False

    @pytest.mark.parametrize(
        "relpath",
        [
            "Assets.xcassets/AppIcon.appiconset/icon-1024.png",
            "en.lproj/InfoPlist.strings",
            "Heap.docc/Overview.md",
            "Heap.xcodeproj/project.pbxproj",
            "Heap.xcworkspace/contents.xcworkspacedata",
            "Model.xcdatamodeld/Model.xcdatamodel/contents",
        ],
    )
    def test_a_file_inside_a_resource_bundle_directory_is_inert(
        self, tmp_path, relpath
    ):
        """The useful half in practice. A touched path rarely names the
        catalog; it names a payload file several components inside one, whose
        own suffix (`.png`, or none at all) says nothing. The ancestor
        directory is what carries the fact.
        """
        path = tmp_path / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("payload", encoding="utf-8")
        assert self.adapter.carries_executable_code(path) is False

    # -- everything else must NOT be called inert ------------------------
    #
    # `False` is the dangerous answer here, so each of these asserts the
    # contract exactly: a compiled language `xccov` really does measure, and
    # an extension this adapter has no opinion about, both stay `None`.

    @pytest.mark.parametrize(
        "name",
        [
            "Ingester.m",
            "Bridge.mm",
            "shim.c",
            "vendor.cc",
            "vendor.cpp",
            "vendor.cxx",
            "Ingester.h",
            "vendor.hpp",
            "vendor.hh",
            "Blur.metal",
        ],
    )
    def test_a_compiled_source_kind_is_never_called_inert(self, tmp_path, name):
        """`xccov` measures Objective-C, C and C++ as readily as Swift.

        Calling one of these inert is precisely the direction the base
        docstring forbids — it would pass unmeasured compiled code in
        silence. This adapter reads no C, so the honest answer is `None`,
        and the assertion pins that rather than merely "not False".
        """
        path = self.write(tmp_path, name, "int main(void) { return 0; }\n")
        assert self.adapter.carries_executable_code(path) is None

    def test_a_source_file_inside_a_bundle_directory_is_still_not_inert(
        self, tmp_path
    ):
        """The ancestor rule must not reach a file kind that is compiled.

        A directory suffix is weaker evidence than a file's own kind, so the
        two are not allowed to compose in the relaxing direction.
        """
        path = tmp_path / "Heap.docc" / "Snippet.m"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("int main(void) { return 0; }\n", encoding="utf-8")
        assert self.adapter.carries_executable_code(path) is None

    def test_an_unknown_extension_is_still_unknown(self, tmp_path):
        """Not a denylist. An extension absent from the allowlist is a file
        this adapter has no fact about, which is `None` — inverting that into
        "anything I do not recognise is inert" is the silent-pass failure.
        """
        path = self.write(tmp_path, "rules.mystery", "who knows")
        assert self.adapter.carries_executable_code(path) is None

    def test_a_swift_file_still_reads_its_contents(self, tmp_path):
        """The allowlist sits beside the reader, never in front of it."""
        path = self.write(tmp_path, "Work.swift", "struct A { func f() { g() } }\n")
        assert self.adapter.carries_executable_code(path) is True

    def test_the_generic_adapter_never_claims_to_know(self, tmp_path):
        path = tmp_path / "a.swift"
        path.write_text("protocol P {}", encoding="utf-8")
        assert Adapter().carries_executable_code(path) is None


class TestResultBundleCounts:
    """The run's own record of itself, and every way reading it can fail.

    This reader exists because the console parser counts assertions where a
    reader wants CASES. Every failure below returns None rather than a
    number, so the caller falls back to the parser it was built to replace —
    a missing count is recoverable, a wrong one is not.
    """

    def fake_run(self, monkeypatch, *responses):
        """Answer each `subprocess.run` with the next scripted response.

        A response is an exception to raise, or (returncode, stdout).
        """
        import subprocess

        class Completed:
            def __init__(self, returncode, stdout):
                self.returncode, self.stdout, self.stderr = returncode, stdout, ""

        pending = list(responses)
        calls: list[dict] = []

        def run(argv, **kwargs):
            calls.append({"argv": argv, "env": kwargs.get("env") or {}})
            response = pending.pop(0) if pending else (1, "")
            if isinstance(response, Exception):
                raise response
            return Completed(*response)

        monkeypatch.setattr(subprocess, "run", run)
        return calls

    def bundle(self, tmp_path):
        path = tmp_path / "run.xcresult"
        path.mkdir()
        return path

    def adapter(self, monkeypatch, developer_dir=None):
        import invariant.adapters.swift_xcode as swift

        monkeypatch.delenv("DEVELOPER_DIR", raising=False)
        monkeypatch.setattr(swift, "_newest_developer_dir", lambda: developer_dir)
        return get_adapter("swift")

    PAYLOAD = (
        '{"totalTestCount": 42, "failedTests": 2, "skippedTests": 1, '
        '"result": "Failure", "devicesAndConfigurations": '
        '[{"device": {"deviceName": "iPhone 16", "deviceId": "ABC-123"}}]}'
    )

    def test_the_counts_are_read_in_test_cases(self, tmp_path, monkeypatch):
        self.fake_run(monkeypatch, (0, self.PAYLOAD))
        counts = self.adapter(monkeypatch).result_bundle_counts(self.bundle(tmp_path))
        assert counts == {
            "total": 42,
            "failed": 2,
            "skipped": 1,
            "result": "Failure",
            "device": "iPhone 16 (ABC-123)",
        }

    def test_a_bundle_that_is_not_there_is_not_asked_about(self, tmp_path, monkeypatch):
        calls = self.fake_run(monkeypatch, (0, self.PAYLOAD))
        adapter = self.adapter(monkeypatch)
        assert adapter.result_bundle_counts(tmp_path / "absent.xcresult") is None
        assert adapter.result_bundle_counts(None) is None
        assert calls == []

    def test_an_unreachable_tool_is_retried_against_an_installed_xcode(
        self, tmp_path, monkeypatch
    ):
        """The fallback that made this reader work at all.

        A machine whose xcode-select points at CommandLineTools cannot find
        xcresulttool through xcrun, and the reader fell silently back to the
        console parser on every run.
        """
        calls = self.fake_run(monkeypatch, (72, ""), (0, self.PAYLOAD))
        adapter = self.adapter(monkeypatch, developer_dir=Path("/X/Developer"))
        counts = adapter.result_bundle_counts(self.bundle(tmp_path))
        assert counts["total"] == 42
        assert calls[0]["env"].get("DEVELOPER_DIR") is None
        assert calls[1]["env"]["DEVELOPER_DIR"] == "/X/Developer"

    def test_a_tool_that_cannot_be_run_at_all_yields_no_counts(
        self, tmp_path, monkeypatch
    ):
        self.fake_run(monkeypatch, OSError("no xcrun"), OSError("no xcrun"))
        adapter = self.adapter(monkeypatch, developer_dir=Path("/X/Developer"))
        assert adapter.result_bundle_counts(self.bundle(tmp_path)) is None

    def test_output_that_is_not_json_yields_no_counts(self, tmp_path, monkeypatch):
        self.fake_run(monkeypatch, (0, "xcresulttool: unrecognised subcommand"))
        adapter = self.adapter(monkeypatch)
        assert adapter.result_bundle_counts(self.bundle(tmp_path)) is None

    def test_json_without_a_total_is_refused_rather_than_read_as_zero(
        self, tmp_path, monkeypatch
    ):
        """A shape this reader does not recognise is not a run of no tests."""
        self.fake_run(monkeypatch, (0, '{"result": "Success"}'))
        adapter = self.adapter(monkeypatch)
        assert adapter.result_bundle_counts(self.bundle(tmp_path)) is None

    def test_a_run_with_no_device_recorded_still_reports_its_counts(
        self, tmp_path, monkeypatch
    ):
        self.fake_run(
            monkeypatch,
            (0, '{"totalTestCount": 3, "devicesAndConfigurations": [{"device": {}}]}'),
        )
        counts = self.adapter(monkeypatch).result_bundle_counts(self.bundle(tmp_path))
        assert counts["total"] == 3 and counts["device"] is None
