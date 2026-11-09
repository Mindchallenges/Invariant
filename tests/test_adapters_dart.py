"""The dart and flutter adapters (ADR-019).

The console and lcov samples below are verbatim from `dart test` (Dart SDK
3.13) run on a throwaway package, so the parser is held to what the tool
prints rather than to what its documentation says it prints.
"""

from pathlib import Path

import pytest
from test_domains import git_project

from invariant.adapters import available, defaults_for, detect, get_adapter
from invariant.core.errors import UsageError
from invariant.domains.pipeline import PipelineDomain, _refuse_chained_coverage
from invariant.domains.session import SessionDomain

DART = get_adapter("dart")
FLUTTER = get_adapter("flutter")

#: `dart test` over two files: one pass, one failure, one skip.
RED_RUN = """\
00:00 +0: loading test/b_test.dart
00:00 +0: test/b_test.dart: ok
00:00 +1: test/a_test.dart: adds
00:00 +2: test/a_test.dart: fails
00:00 +2 -1: test/a_test.dart: fails [E]
  Expected: <4>
    Actual: <3>

  package:matcher        expect
  test/a_test.dart 5:23  main.<fn>

00:00 +2 -1: test/a_test.dart: skipped
  Skip: later
00:00 +2 ~1 -1: Some tests failed.

Failing tests:
  test/a_test.dart: fails
"""

GREEN_RUN = """\
00:00 +0: loading test/b_test.dart
00:00 +0: ok
00:00 +1: All tests passed!
"""

NO_TESTS = """\
00:00 +0: loading test/b_test.dart
No tests ran.
No tests match regular expression "nomatch".
"""

ALL_SKIPPED = """\
00:00 +0: loading test/c_test.dart
00:00 +0: sub
00:00 +0 ~1: All tests skipped.
"""

LCOV = """\
SF:/work/app/packages/core/lib/core.dart
DA:1,2
DA:2,0
DA:3,0
LF:3
LH:1
end_of_record
SF:lib/main.dart
DA:3,1
LF:4
LH:4
end_of_record
"""


class TestRegistry:
    def test_both_ids_are_registered(self):
        assert {"dart", "flutter"} <= set(available())
        assert DART.id == "dart"
        assert FLUTTER.id == "flutter"

    def test_dart_defaults_run_dart_test(self):
        commands = defaults_for("dart")["commands"]
        assert commands["test"] == "dart test"
        assert commands["test_one"] == "dart test {selector}"
        assert commands["lint"] == "dart analyze"
        assert "--coverage-path={result_bundle}/lcov.info" in commands["coverage"]
        assert "{selector}" in commands["coverage_one"]
        assert commands["coverage_report"] == "cat {result_bundle}/lcov.info"

    def test_flutter_defaults_run_flutter_test(self):
        commands = defaults_for("flutter")["commands"]
        assert commands["test"] == "flutter test"
        assert commands["test_one"] == "flutter test {selector}"
        assert commands["lint"] == "flutter analyze"
        assert commands["coverage"].startswith("flutter test --coverage ")
        assert commands["coverage_report"] == "cat {result_bundle}/lcov.info"

    @pytest.mark.parametrize("ecosystem", ["dart", "flutter"])
    def test_the_report_leg_is_not_chained_into_the_run(self, ecosystem):
        commands = defaults_for(ecosystem)["commands"]
        bundle = "/state/coverage/s.coverage.xcresult"
        for key in ("coverage", "coverage_one"):
            run = commands[key].replace("{result_bundle}", bundle)
            report = commands["coverage_report"].replace("{result_bundle}", bundle)
            _refuse_chained_coverage(run, report, bundle)  # does not raise

    def test_default_commands_reads_the_defaults(self):
        assert DART.default_commands()["test"] == "dart test"


class TestDetection:
    def test_a_pure_dart_package(self, tmp_path):
        (tmp_path / "pubspec.yaml").write_text("name: pkg\n", encoding="utf-8")
        assert detect(tmp_path) == ["dart"]

    def test_a_flutter_app(self, tmp_path):
        (tmp_path / "pubspec.yaml").write_text(
            "name: app\ndependencies:\n  flutter:\n    sdk: flutter\n", encoding="utf-8"
        )
        assert detect(tmp_path) == ["flutter"]

    def test_a_flutter_sdk_dev_dependency_is_flutter(self, tmp_path):
        (tmp_path / "pubspec.yaml").write_text(
            "name: pkg\ndev_dependencies:\n  flutter_test:\n    sdk: flutter # tests\n",
            encoding="utf-8",
        )
        assert FLUTTER.detect(tmp_path)
        assert not DART.detect(tmp_path)

    def test_the_word_flutter_elsewhere_is_not_the_sdk(self, tmp_path):
        (tmp_path / "pubspec.yaml").write_text(
            "name: pkg\ndescription: not a flutter app; sdk: flutter is absent\n",
            encoding="utf-8",
        )
        assert detect(tmp_path) == ["dart"]

    def test_no_pubspec_is_neither(self, tmp_path):
        assert not DART.detect(tmp_path)
        assert not FLUTTER.detect(tmp_path)

    def test_an_unreadable_pubspec_is_not_flutter(self, tmp_path):
        (tmp_path / "pubspec.yaml").mkdir()
        assert not FLUTTER.detect(tmp_path)
        assert not DART.detect(tmp_path)

    def test_an_undecodable_pubspec_is_not_flutter(self, tmp_path):
        (tmp_path / "pubspec.yaml").write_bytes(b"name: \xff\xfe\n")
        assert not FLUTTER.detect(tmp_path)
        assert DART.detect(tmp_path)

    def test_alongside_another_ecosystem(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        (tmp_path / "pubspec.yaml").write_text("name: pkg\n", encoding="utf-8")
        assert detect(tmp_path) == ["python", "dart"]


class TestClassification:
    def test_source_files_are_dart_files(self):
        changed = [
            Path("lib/a.dart"),
            Path("lib/a.g.dart"),
            Path("README.md"),
            Path("a.py"),
        ]
        assert DART.source_files(changed) == [Path("lib/a.dart"), Path("lib/a.g.dart")]

    @pytest.mark.parametrize(
        "path",
        [
            "test/widget_test.dart",
            "test/helpers/fakes.dart",
            "packages/core/test/core_test.dart",
            "packages/core/test/support/builders.dart",
            "integration_test/app_test.dart",
            "test_driver/integration_test.dart",
            "tool/smoke_test.dart",
        ],
    )
    def test_test_files(self, path):
        assert DART.is_test_file(Path(path)) is True

    @pytest.mark.parametrize(
        "path",
        [
            "lib/main.dart",
            "lib/src/latest.dart",
            "lib/src/contest.dart",
            "lib/src/test/fixtures.dart",
            "lib/src/testing_test.dart",
            "packages/core/lib/test/thing.dart",
            "bin/main.dart",
        ],
    )
    def test_production_files(self, path):
        assert DART.is_test_file(Path(path)) is False

    def test_another_language_is_not_answered(self):
        assert DART.is_test_file(Path("test/test_app.py")) is None

    def test_owns_a_test_file_selector(self):
        assert DART.owns_selector("packages/core/test/core_test.dart")
        assert not DART.owns_selector("packages/core")
        assert not DART.owns_selector("lib/main.dart")

    def test_several_selectors_are_positional(self):
        assert DART.join_selectors(["test/a_test.dart", "test/b_test.dart"]) == (
            "test/a_test.dart test/b_test.dart"
        )


class TestCoverageRows:
    """The `<path>  <hit> <found>  <pct>%` table a summarising script prints."""

    def test_the_script_rows(self):
        raw = (
            "== tests with coverage: packages/dang_core\n"
            "00:01 +1: All tests passed!\n"
            "packages/dang_core/lib/dang_core.dart  1 1  100.0%\n"
            "lib/main.dart  17 19  89.5%\n"
        )
        assert DART.parse_coverage(raw) == {
            "packages/dang_core/lib/dang_core.dart": 100.0,
            "lib/main.dart": 89.5,
        }

    def test_a_row_with_no_count_cells(self):
        assert DART.parse_coverage("lib/a.dart 75%\n") == {"lib/a.dart": 75.0}

    def test_only_dart_rows(self):
        raw = "lib/a.py  1 2  50.0%\nTOTAL  3 4  75.0%\napp.dart.bak  1 1  100%\n"
        assert DART.parse_coverage(raw) == {}

    def test_a_progress_line_is_not_a_measurement(self):
        raw = (
            "00:00 +1: test/a_test.dart: grows 50%\n"
            "  test/a_test.dart 5:23  main.<fn>\n"
            "test/a_test.dart: reaches 90%\n"
        )
        assert DART.parse_coverage(raw) == {}

    def test_a_row_over_a_hundred_percent_is_refused(self):
        assert DART.parse_coverage("lib/a.dart  3 2  150.0%\n") == {}

    def test_a_row_does_not_reach_across_lines(self):
        assert DART.parse_coverage("lib/a.dart\n12 50.0%\n") == {}


class TestCoverageLcov:
    """Dart's own report, `SF:` / `LF:` / `LH:` / `end_of_record`."""

    def test_records_become_line_percentages(self):
        measured = DART.parse_coverage(LCOV)
        assert measured == {
            "/work/app/packages/core/lib/core.dart": pytest.approx(100 / 3),
            "lib/main.dart": 100.0,
        }

    def test_a_record_with_no_lines_found_is_not_a_measurement(self):
        raw = "SF:lib/a.dart\nLF:0\nLH:0\nend_of_record\n"
        assert DART.parse_coverage(raw) == {}

    def test_a_record_missing_a_count_is_not_a_measurement(self):
        assert DART.parse_coverage("SF:lib/a.dart\nLF:4\nend_of_record\n") == {}
        assert DART.parse_coverage("SF:lib/a.dart\nLH:4\nend_of_record\n") == {}

    def test_more_lines_hit_than_found_is_refused(self):
        raw = "SF:lib/a.dart\nLF:2\nLH:3\nend_of_record\n"
        assert DART.parse_coverage(raw) == {}

    def test_a_record_cut_off_before_its_end_is_not_read(self):
        assert DART.parse_coverage("SF:lib/a.dart\nLF:2\nLH:2\n") == {}

    def test_a_non_dart_source_is_not_read(self):
        raw = "SF:lib/a.js\nLF:2\nLH:2\nend_of_record\n"
        assert DART.parse_coverage(raw) == {}

    def test_a_malformed_count_is_not_read(self):
        raw = "SF:lib/a.dart\nLF:two\nLH:2\nend_of_record\n"
        assert DART.parse_coverage(raw) == {}

    def test_counts_do_not_leak_into_the_next_record(self):
        raw = "SF:lib/a.dart\nLF:2\nLH:2\nend_of_record\nSF:lib/b.dart\nend_of_record\n"
        assert DART.parse_coverage(raw) == {"lib/a.dart": 100.0}

    def test_a_file_reported_twice_reads_at_its_lower_figure(self):
        raw = (
            "SF:lib/a.dart\nLF:4\nLH:4\nend_of_record\n"
            "SF:lib/a.dart\nLF:4\nLH:1\nend_of_record\n"
            "lib/a.dart  2 4  50.0%\n"
        )
        assert DART.parse_coverage(raw) == {"lib/a.dart": 25.0}

    def test_crlf_lcov_reads(self):
        raw = "SF:lib/a.dart\r\nLF:2\r\nLH:1\r\nend_of_record\r\n"
        assert DART.parse_coverage(raw) == {"lib/a.dart": 50.0}


class TestTestOutput:
    def test_a_red_run(self):
        result = DART.parse_test_output(RED_RUN, "", 1)
        assert not result.passed
        assert result.total == 3
        assert result.failed == 1
        assert result.failed_tests == 1
        assert result.skipped == 1
        assert result.note == ""

    def test_a_green_run(self):
        result = DART.parse_test_output(GREEN_RUN, "", 0)
        assert result.passed and result.trustworthy
        assert (result.total, result.failed, result.skipped) == (1, 0, 0)

    def test_several_packages_are_summed(self):
        result = DART.parse_test_output(GREEN_RUN + RED_RUN, "", 1)
        assert result.total == 4
        assert result.failed == 1

    def test_no_tests_ran(self):
        result = DART.parse_test_output(NO_TESTS, "", 79)
        assert result.total == 0
        assert "matched nothing" in result.note
        assert [h.id for h in result.hazards] == ["dart_test.no_tests_ran"]
        assert not result.trustworthy

    def test_every_test_skipped_executed_nothing(self):
        result = DART.parse_test_output(ALL_SKIPPED, "", 0)
        assert result.total == 0
        assert result.skipped == 1
        assert not result.trustworthy

    def test_terminal_overwrites_and_colour_are_read_through(self):
        text = "\x1b[32m00:00 +0: x\r00:01 +5\x1b[0m: All tests passed!\n"
        assert DART.parse_test_output(text, "", 0).total == 5

    def test_no_summary_line_is_not_a_count(self):
        result = DART.parse_test_output("Compilation failed", "", 1)
        assert result.total is None
        assert result.note == "no dart test summary line found in the output"

    def test_the_summary_on_stderr_is_read(self):
        assert DART.parse_test_output("", GREEN_RUN, 0).total == 1

    def test_a_bundle_directory_is_not_a_result_bundle(self, tmp_path):
        """The bundle holds the lcov file only; it carries no test counts."""
        (tmp_path / "lcov.info").write_text("SF:lib/a.dart\n", encoding="utf-8")
        result = DART.read_run(GREEN_RUN, "", 0, bundle=tmp_path)
        assert result.total == 1
        assert result.source == "console"
        assert "result bundle" not in result.note


class TestExecutableCode:
    """Explains an ABSENT row: `dart test` writes no lcov record for these."""

    def write(self, tmp_path, text, name="a.dart"):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    @pytest.mark.parametrize(
        "text",
        [
            "library;\n",
            "// A barrel.\nexport 'src/a.dart';\nexport 'src/b.dart' show B, C;\n",
            "/* outer /* nested */ still */\nimport 'dart:io' as io;\npart 'a.g.dart';\n",
            "part of 'parent.dart';\n",
            "import 'a.dart' if (dart.library.io) 'b.dart';\n",
            r"export r'raw\path.dart';" + "\n",
            "export '''triple.dart''';\n",
            "export 'it\\'s.dart';\n",
            "@JS\nlibrary;\n",
            (
                "/// Doc.\nabstract interface class Port {\n  int get x;\n  void f(int a);\n"
                "  abstract final int y;\n  @override\n  String toString();\n}\n"
            ),
            "sealed class Shape {}\nfinal class Sq extends Shape {}\n",
            "mixin class M implements Comparable<M> {\n  set v(int value);\n}\n",
            "enum Colour { red, green, }\n",
            "typedef Cb = void Function(int);\n",
            "// coverage:ignore-file\nint mul(int a, int b) => a * b;\n",
            "//coverage:ignore-file\nclass Generated { void go() { print(1); } }\n",
            "",
        ],
    )
    def test_inert(self, tmp_path, text):
        assert DART.carries_executable_code(self.write(tmp_path, text)) is False

    @pytest.mark.parametrize(
        "text",
        [
            "int add(int a, int b) => a + b;\n",
            "void main() {}\n",
            "final x = 1;\n",
            "abstract class W {\n  const W();\n  void f();\n}\n",
            "abstract class W {\n  W.named();\n}\n",
            "abstract class W {\n  factory W() => _W();\n}\n",
            "abstract class W {\n  static int n();\n}\n",
            "abstract class W {\n  void f({int a = 1});\n}\n",
            "abstract class W {\n  void f() {}\n}\n",
            "abstract class W {\n  int x;\n}\n",
            "abstract class W {\n  @Deprecated('no')\n  void f();\n}\n",
            "class W {\n  int x;\n}\n",
            "extension E on int {\n  int get twice => this * 2;\n}\n",
            "enum E { a(1); const E(this.v); final int v; }\n",
            "export 'a.dart'\n",
            "import 'a.dart'; void main() {}\n",
            "import 'a.dart' // trailing\n;\nint f() => 1;\n",
            "class W {\n",
            "class W }\n",
            "export 'unterminated.dart;\n",
            "export \"${'interp'}.dart\";\n",
            "/* never closed\nexport 'a.dart';\n",
            "export 'a.dart'; // coverage:ignore-line\nint f() => 1;\n",
            "const s = '// coverage:ignore-file';\nint f() => 1;\n",
            "class W {}\nint f() => 1;\n",
            "abstract class W {\n  void f()\n}\n",
            "@JS('x')\nlibrary;\n",
            "export '''never closed.dart\n",
        ],
    )
    def test_not_claimed_inert(self, tmp_path, text):
        assert DART.carries_executable_code(self.write(tmp_path, text)) is None

    def test_another_language_is_not_answered(self, tmp_path):
        assert (
            DART.carries_executable_code(self.write(tmp_path, "x = 1\n", "a.py"))
            is None
        )

    def test_an_unreadable_file_is_not_answered(self, tmp_path):
        assert DART.carries_executable_code(tmp_path / "missing.dart") is None
        path = tmp_path / "bin.dart"
        path.write_bytes(b"\xff\xfe\x00")
        assert DART.carries_executable_code(path) is None


def test_the_family_shares_its_hazards():
    assert [h.id for h in DART.hazards()] == [h.id for h in FLUTTER.hazards()]


class TestThroughThePipeline:
    """The default report leg reads the lcov file the run wrote into its bundle."""

    #: Stands in for `dart test --coverage-path=<bundle>/lcov.info`: prints the
    #: reporter's closing line, and writes lcov only when told to.
    FAKE_DART = """\
echo "00:00 +2: All tests passed!"
if [ "$2" = write ]; then
  mkdir -p "$1"
  printf 'SF:%s/lib/a.dart\\nLF:4\\nLH:3\\nend_of_record\\n' "$PWD" > "$1/lcov.info"
fi
"""

    def project(self, tmp_path, mode):
        root = tmp_path / "proj"
        manifest = git_project(
            root,
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["dart"]},
                "commands": {"coverage": f"sh fake-dart.sh {{result_bundle}} {mode}"},
            },
        )
        (root / "fake-dart.sh").write_text(self.FAKE_DART, encoding="utf-8")
        session = SessionDomain(manifest).start("x")
        pipelines = PipelineDomain(manifest)
        pipelines.start(session.id, task="t")
        return pipelines, session, root

    def test_the_lcov_report_becomes_the_measurement(self, tmp_path):
        pipelines, session, root = self.project(tmp_path, "write")
        result = pipelines.run_coverage(session.id, cwd=root)
        assert result["report_command"].startswith("cat ")
        assert result["coverage"] == {f"{root}/lib/a.dart": 75.0}
        run = pipelines.current(session.id).evidence["coverage_run"]
        assert (run["executed"], run["failed"], run["suite_passed"]) == (2, 0, True)

    def test_a_run_that_wrote_no_report_is_not_given_the_last_one(self, tmp_path):
        pipelines, session, root = self.project(tmp_path, "write")
        pipelines.run_coverage(session.id, cwd=root)
        (root / "fake-dart.sh").write_text(
            self.FAKE_DART.replace("= write ]", "= never ]"), encoding="utf-8"
        )
        with pytest.raises(UsageError, match="no per-file measurements"):
            pipelines.run_coverage(session.id, cwd=root)
