from pathlib import Path

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

    def test_a_rerun_in_one_log_takes_the_later_total(self):
        text = (
            "Test Suite 'All tests' failed.\n"
            "\t Executed 6 tests, with 4 failures (0 unexpected) in 6.5 (6.5) seconds\n"
            "Test Suite 'All tests' passed.\n"
            "\t Executed 6 tests, with 0 failures (0 unexpected) in 6.4 (6.4) seconds\n"
        )
        result = self.ADAPTER.parse_test_output(text, "", 0)
        assert (result.total, result.failed) == (6, 0)

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
        command = self.ADAPTER.default_commands()["coverage"]
        assert "-enableCodeCoverage YES" in command
        assert command.index("-resultBundlePath") < command.index("xccov")

    def test_the_bundle_path_is_a_placeholder_not_a_fixed_path(self):
        """A fixed path would be shared state between concurrent sessions."""
        assert "{result_bundle}" in self.ADAPTER.default_commands()["coverage"]

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
    """

    ADAPTER = get_adapter("swift")

    def test_the_report_step_is_not_chained_on_success(self):
        command = self.ADAPTER.default_commands()["coverage"]
        assert "&& xcrun xccov" not in command
        assert "; xcrun xccov" in command

    def test_the_report_still_runs_after_the_test_step(self):
        command = self.ADAPTER.default_commands()["coverage"]
        assert command.index("-resultBundlePath") < command.index("xccov")
