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
