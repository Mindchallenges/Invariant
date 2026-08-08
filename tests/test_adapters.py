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

    def test_executed_counts_are_summed_across_bundles(self):
        out = "Executed 12 tests, with 0 failures\nExecuted 30 tests, with 2 failures"
        result = self.adapter.parse_test_output(out, "", 1)
        assert result.total == 42
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
