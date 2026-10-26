"""IDEA_032 increment 2 — whole-suite and whole-scheme evidence move to a cadence.

ADR-010. A project can put `coverage.meets_floor` and `test.all_pass` on a
sweep cadence (`methodology.gates.schedule`). Per change, a scheduled floor is
advisory for every touched file outside `schedule.held_paths`, a scheduled
`test.all_pass` still fails on a red scoped run, the manifest refuses a cadence
that still demands whole-run evidence per change, and `sweep.no_full_run`
catches a full run on a change at the CLI and at the hook. The sweep itself,
its committed records and `sweep.fresh` are increment 3.
"""

import json
import subprocess

import pytest
import test_cli
import test_rules
import yaml
from test_cli import payload, run
from test_rules import bash_payload, only, write_manifest

from invariant.adapters import get_adapter
from invariant.core.errors import ManifestError, UsageError
from invariant.core.manifest import MANIFEST_RELPATH, Level, Manifest
from invariant.core.rules import (
    NORMALISED_PAYLOAD_KEYS,
    evaluate,
    evaluate_event,
    get_rule,
)

# The shared fixtures, by name, so a test asks for them as it would there.
project = test_cli.project
repo = test_rules.repo
ctx_factory = test_rules.ctx_factory

CADENCE_BOTH = {
    "cadence_days": 7,
    "gates": ["coverage.meets_floor", "test.all_pass"],
}


def manifest_at(tmp_path, gates=None, rules=None, commands=None, ecosystems=None, env=None):
    body = {"version": 1, "project": {"name": "demo", "ecosystems": ecosystems or ["python"]}}
    if gates is not None:
        body["methodology"] = {"gates": gates}
    if rules is not None:
        body["enforcement"] = {"rules": rules}
    if commands is not None:
        body["commands"] = commands
    return Manifest.from_raw(
        body, root=tmp_path, path=tmp_path / MANIFEST_RELPATH, env=env or {}
    )


# -- the manifest ------------------------------------------------------------


class TestTheScheduleKeys:
    def test_the_defaults_change_nothing(self, tmp_path):
        m = manifest_at(tmp_path, ecosystems=["generic"])
        assert m.get("methodology.gates.schedule") == {
            "cadence_days": None,
            "grace_days": 3,
            "gates": [],
            "held_paths": [],
        }
        assert m.get("commands.coverage_one") is None
        assert m.scheduled_gates() == frozenset()

    def test_a_cadence_names_the_gates_it_moves(self, tmp_path):
        m = manifest_at(
            tmp_path, gates={"schedule": {"cadence_days": 7, "gates": ["test.all_pass"]}}
        )
        assert m.scheduled_gates() == frozenset({"test.all_pass"})

    def test_both_gates_can_move(self, tmp_path):
        m = manifest_at(tmp_path, gates={"schedule": CADENCE_BOTH})
        assert m.scheduled_gates() == frozenset({"coverage.meets_floor", "test.all_pass"})

    def test_the_adapters_supply_a_scoped_coverage_command(self, tmp_path):
        assert "{selector}" in manifest_at(tmp_path).get("commands.coverage_one")
        swift = manifest_at(tmp_path, ecosystems=["swift"]).get("commands.coverage_one")
        assert "-only-testing:{selector}" in swift
        assert "-enableCodeCoverage YES" in swift
        node = manifest_at(tmp_path, ecosystems=["node"]).get("commands.coverage_one")
        assert "--coverage" in node and "{selector}" in node


class TestTheScheduleIsValidatedAtLoad:
    """D3: a cadence that still demands whole-run evidence per change is refused."""

    def refused(self, tmp_path, **kwargs) -> str:
        with pytest.raises(ManifestError) as caught:
            manifest_at(tmp_path, **kwargs)
        return str(caught.value)

    def test_a_cadence_scheduling_nothing_is_refused(self, tmp_path):
        text = self.refused(tmp_path, gates={"schedule": {"cadence_days": 7, "gates": []}})
        assert "methodology.gates.schedule.gates" in text

    def test_an_unschedulable_gate_is_refused_with_the_nearest_id(self, tmp_path):
        text = self.refused(
            tmp_path,
            gates={"schedule": {"cadence_days": 7, "gates": ["test.all_passes"]}},
        )
        assert "'test.all_passes'" in text
        assert "did you mean 'test.all_pass'" in text

    def test_a_gate_that_is_no_rule_at_all_is_refused_without_a_guess(self, tmp_path):
        text = self.refused(
            tmp_path,
            gates={"schedule": {"cadence_days": 7, "gates": ["privacy.clean"]}},
        )
        assert "'privacy.clean'" in text
        assert "coverage.meets_floor" in text and "test.all_pass" in text

    @pytest.mark.parametrize("level", ["block", "ask"])
    def test_an_unscheduled_floor_that_stops_work_is_refused(self, tmp_path, level):
        text = self.refused(
            tmp_path,
            gates={"schedule": {"cadence_days": 7, "gates": ["test.all_pass"]}},
            rules={"coverage.meets_floor": level},
        )
        assert "coverage.meets_floor" in text
        assert "schedule" in text and "warn" in text

    def test_an_unscheduled_advisory_floor_is_allowed(self, tmp_path):
        m = manifest_at(
            tmp_path,
            gates={"schedule": {"cadence_days": 7, "gates": ["test.all_pass"]}},
            rules={"coverage.meets_floor": "warn"},
        )
        assert m.scheduled_gates() == frozenset({"test.all_pass"})

    def test_the_level_is_read_resolved_from_the_profile(self, tmp_path):
        body = {
            "version": 1,
            "project": {"name": "demo"},
            "methodology": {
                "gates": {"schedule": {"cadence_days": 7, "gates": ["test.all_pass"]}}
            },
            "enforcement": {"profile": "strict"},
        }
        with pytest.raises(ManifestError, match="coverage.meets_floor"):
            Manifest.from_raw(body, root=tmp_path, path=tmp_path / MANIFEST_RELPATH, env={})

    @pytest.mark.parametrize("scope", ["all", "affected"])
    def test_a_scheduled_floor_over_every_row_is_refused(self, tmp_path, scope):
        text = self.refused(
            tmp_path, gates={"coverage_scope": scope, "schedule": CADENCE_BOTH}
        )
        assert "coverage_scope" in text and scope in text

    def test_a_scheduled_floor_beside_advisory_paths_is_refused(self, tmp_path):
        text = self.refused(
            tmp_path,
            gates={"coverage_advisory_paths": ["web/**"], "schedule": CADENCE_BOTH},
        )
        assert "coverage_advisory_paths" in text

    @pytest.mark.parametrize("cadence", [0, -1, "7", True, 1.5])
    def test_a_cadence_that_is_no_positive_integer_is_refused(self, tmp_path, cadence):
        text = self.refused(
            tmp_path,
            gates={"schedule": {"cadence_days": cadence, "gates": ["test.all_pass"]}},
        )
        assert "cadence_days" in text

    @pytest.mark.parametrize("grace", [-1, "3", None, False])
    def test_a_grace_that_is_no_non_negative_integer_is_refused(self, tmp_path, grace):
        text = self.refused(
            tmp_path,
            gates={
                "schedule": {
                    "cadence_days": 7,
                    "grace_days": grace,
                    "gates": ["test.all_pass"],
                }
            },
        )
        assert "grace_days" in text

    def test_a_zero_grace_is_allowed(self, tmp_path):
        m = manifest_at(
            tmp_path,
            gates={
                "schedule": {"cadence_days": 7, "grace_days": 0, "gates": ["test.all_pass"]}
            },
        )
        assert m.get("methodology.gates.schedule.grace_days") == 0

    def test_a_schedule_that_is_no_mapping_is_refused(self, tmp_path):
        assert "methodology.gates.schedule" in self.refused(
            tmp_path, gates={"schedule": ["test.all_pass"]}
        )

    def test_gates_that_are_no_list_are_refused(self, tmp_path):
        assert "schedule.gates" in self.refused(
            tmp_path, gates={"schedule": {"cadence_days": 7, "gates": "test.all_pass"}}
        )

    @pytest.mark.parametrize("held", ["web/**", [1], [None]])
    def test_held_paths_must_be_a_list_of_globs(self, tmp_path, held):
        text = self.refused(
            tmp_path,
            gates={"schedule": {**CADENCE_BOTH, "held_paths": held}},
        )
        assert "held_paths" in text

    def test_held_paths_with_no_cadence_are_refused(self, tmp_path):
        text = self.refused(tmp_path, gates={"schedule": {"held_paths": ["core/**"]}})
        assert "held_paths" in text and "cadence_days" in text

    def test_gates_with_no_cadence_are_refused(self, tmp_path):
        text = self.refused(tmp_path, gates={"schedule": {"gates": ["test.all_pass"]}})
        assert "cadence_days" in text

    def test_disabling_the_floor_by_environment_satisfies_the_check(self, tmp_path):
        m = manifest_at(
            tmp_path,
            gates={"schedule": {"cadence_days": 7, "gates": ["test.all_pass"]}},
            rules={"coverage.meets_floor": "block"},
            env={"INVARIANT_DISABLE": "coverage.meets_floor"},
        )
        assert m.rule_level("coverage.meets_floor") is Level.OFF


# -- coverage.meets_floor on a cadence --------------------------------------


LIVE = "def f():\n    return g()\n"


def cadence_ctx(ctx_factory, files, coverage=None, run=None, schedule=None, gates=None):
    body_gates = {"coverage_min": 90, "coverage_scope": "touched"}
    body_gates["schedule"] = schedule if schedule is not None else CADENCE_BOTH
    body_gates.update(gates or {})
    write_manifest(
        ctx_factory.main,
        {
            "version": 1,
            "project": {"name": "demo", "ecosystems": ["python"]},
            "methodology": {"gates": body_gates},
            "commands": {"test": "uv run pytest", "coverage": "uv run pytest --cov"},
        },
    )
    measured = {}
    for rel, pct in (coverage or {}).items():
        path = ctx_factory.wt / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(LIVE, encoding="utf-8")
        if pct is not None:
            measured[str(path)] = pct
    for rel in files:
        path = ctx_factory.wt / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(LIVE, encoding="utf-8")
    evidence = {}
    if measured:
        evidence["coverage"] = measured
    if run is not None:
        evidence["coverage_run"] = run
    return ctx_factory(
        cwd=ctx_factory.main,
        session={"id": "s1", "files": files, "worktree": str(ctx_factory.wt), "base": ""},
        pipeline={"evidence": evidence},
    )


def floor(ctx):
    return only(evaluate(["coverage.meets_floor"], ctx)).verdict


class TestAScheduledFloorIsAdvisoryPerChange:
    SHARED = "src/app/mod.py"
    CORE = "core/engine.py"

    def test_a_change_with_no_coverage_run_passes_and_says_why(self, ctx_factory):
        verdict = floor(cadence_ctx(ctx_factory, [self.SHARED]))
        assert verdict.ok and not verdict.skipped, verdict.message
        assert "sweep cadence" in verdict.message
        assert "every 7 day(s)" in verdict.message
        assert f"{self.SHARED} not measured" in verdict.detail

    def test_a_scoped_figure_below_the_floor_is_reported_not_held(self, ctx_factory):
        verdict = floor(
            cadence_ctx(
                ctx_factory,
                [self.SHARED],
                coverage={self.SHARED: 65.0},
                run={"scope": "scoped", "selectors": ["tests/test_mod.py"]},
            )
        )
        assert verdict.ok, verdict.message
        assert f"{self.SHARED} 65%, below the 90% floor" in verdict.detail
        assert "methodology.gates.schedule" in verdict.detail

    def test_a_file_with_no_row_is_named_and_does_not_fail(self, ctx_factory):
        verdict = floor(
            cadence_ctx(
                ctx_factory,
                [self.SHARED],
                coverage={"src/other.py": 95.0},
                run={"scope": "scoped"},
            )
        )
        assert verdict.ok, verdict.message
        assert f"{self.SHARED} not measured, no coverage row" in verdict.detail

    def test_a_non_source_claim_still_applies_first(self, ctx_factory):
        verdict = floor(cadence_ctx(ctx_factory, ["docs/notes.md"]))
        assert verdict.ok and verdict.skipped, verdict.message
        assert "docs/notes.md" not in verdict.message + verdict.detail

    def test_a_held_path_list_over_an_unestablished_change_still_refuses(
        self, ctx_factory
    ):
        ctx = cadence_ctx(
            ctx_factory, [], schedule={**CADENCE_BOTH, "held_paths": ["core/**"]}
        )
        ctx = type(ctx)(
            manifest=ctx.manifest,
            cwd=ctx_factory.wt,
            session={**ctx.session, "base": "no-such-branch"},
            pipeline=ctx.pipeline,
        )
        verdict = floor(ctx)
        assert not verdict.ok

    def test_a_held_path_on_a_scoped_run_owes_a_full_run(self, ctx_factory):
        verdict = floor(
            cadence_ctx(
                ctx_factory,
                [self.CORE, self.SHARED],
                coverage={self.CORE: 99.0},
                run={"scope": "scoped", "selectors": ["tests/test_engine.py"]},
                schedule={**CADENCE_BOTH, "held_paths": ["core/**"]},
            )
        )
        assert not verdict.ok
        assert "held_paths" in verdict.message
        assert self.CORE in verdict.detail
        assert any(fix.startswith("invariant pipeline coverage") for fix in verdict.fixes)
        assert "--selector" not in verdict.fixes[0]

    def test_a_held_path_measured_by_a_full_run_is_held_to_the_floor(self, ctx_factory):
        verdict = floor(
            cadence_ctx(
                ctx_factory,
                [self.CORE, self.SHARED],
                coverage={self.CORE: 95.0},
                run={"scope": "full", "selectors": []},
                schedule={**CADENCE_BOTH, "held_paths": ["core/**"]},
            )
        )
        assert verdict.ok, verdict.message
        assert "1 file(s) at or above 90%" in verdict.message
        assert "1 more advisory on the sweep cadence" in verdict.message
        assert self.SHARED in verdict.detail

    def test_a_held_path_below_the_floor_on_a_full_run_fails(self, ctx_factory):
        verdict = floor(
            cadence_ctx(
                ctx_factory,
                [self.CORE],
                coverage={self.CORE: 50.0},
                run={"scope": "full"},
                schedule={**CADENCE_BOTH, "held_paths": ["core/**"]},
            )
        )
        assert not verdict.ok
        assert "below the 90% floor" in verdict.message

    def test_a_held_path_with_no_coverage_recorded_fails(self, ctx_factory):
        verdict = floor(
            cadence_ctx(
                ctx_factory,
                [self.CORE],
                schedule={**CADENCE_BOTH, "held_paths": ["core/**"]},
            )
        )
        assert not verdict.ok
        assert "no coverage was recorded" in verdict.message

    def test_a_run_record_without_a_scope_is_not_proof_of_a_full_run(self, ctx_factory):
        """A record written before runs carried a scope cannot vouch for one."""
        verdict = floor(
            cadence_ctx(
                ctx_factory,
                [self.CORE],
                coverage={self.CORE: 95.0},
                run={"command": "older build"},
                schedule={**CADENCE_BOTH, "held_paths": ["core/**"]},
            )
        )
        assert not verdict.ok
        assert "held_paths" in verdict.message

    def test_an_unscheduled_floor_is_unchanged(self, ctx_factory):
        verdict = floor(
            cadence_ctx(
                ctx_factory,
                [self.SHARED],
                coverage={self.SHARED: 65.0},
                schedule={"cadence_days": 7, "gates": ["test.all_pass"]},
                gates={},
            )
        )
        assert not verdict.ok
        assert "below the 90% floor" in verdict.message


# -- test.all_pass on a cadence ---------------------------------------------


def suite_ctx(ctx_factory, runs, schedule=None, files=None, base=""):
    write_manifest(
        ctx_factory.main,
        {
            "version": 1,
            "project": {"name": "demo", "ecosystems": ["python"]},
            "methodology": {
                "gates": {
                    "schedule": schedule
                    if schedule is not None
                    else {"cadence_days": 7, "gates": ["test.all_pass"]}
                }
            },
            "enforcement": {"rules": {"coverage.meets_floor": "warn"}},
        },
    )
    for rel in files or []:
        path = ctx_factory.wt / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(LIVE, encoding="utf-8")
    session = None
    if files is not None:
        session = {"id": "s1", "files": files, "worktree": str(ctx_factory.wt), "base": base}
    return ctx_factory(
        cwd=ctx_factory.wt, session=session, pipeline={"evidence": {"test_runs": runs}}
    )


def suite(ctx):
    return only(evaluate(["test.all_pass"], ctx)).verdict


SCOPED_GREEN = {"passed": True, "executed": 4, "scope": "scoped", "selectors": ["t.py"]}
FULL_GREEN = {"passed": True, "executed": 400, "scope": "full", "selectors": []}


class TestAScheduledSuiteGateJudgesTheScopedRun:
    def test_a_scoped_green_passes_and_names_the_cadence(self, ctx_factory):
        verdict = suite(suite_ctx(ctx_factory, [SCOPED_GREEN]))
        assert verdict.ok
        assert "affected suites are green" in verdict.message
        assert "sweep cadence" in verdict.message

    def test_a_red_scoped_run_still_fails(self, ctx_factory):
        red = {**SCOPED_GREEN, "passed": False, "failed": 1}
        verdict = suite(suite_ctx(ctx_factory, [red]))
        assert not verdict.ok
        assert "(1 failing)" in verdict.message

    def test_a_scoped_run_that_executed_nothing_still_fails(self, ctx_factory):
        verdict = suite(suite_ctx(ctx_factory, [{**SCOPED_GREEN, "executed": 0}]))
        assert not verdict.ok and "zero tests" in verdict.message

    def test_no_run_names_the_scoped_recorder(self, ctx_factory):
        verdict = suite(suite_ctx(ctx_factory, []))
        assert not verdict.ok
        assert any("--selector" in fix for fix in verdict.fixes)
        assert "sweep cadence" in verdict.detail

    def test_a_full_green_reads_as_before(self, ctx_factory):
        verdict = suite(suite_ctx(ctx_factory, [FULL_GREEN]))
        assert verdict.ok
        assert verdict.message == "suite is green and the pass is trustworthy"

    def test_an_unscheduled_gate_is_unchanged(self, ctx_factory):
        verdict = suite(
            suite_ctx(
                ctx_factory,
                [SCOPED_GREEN],
                schedule={"cadence_days": None, "gates": []},
            )
        )
        assert verdict.message == "suite is green and the pass is trustworthy"
        no_run = suite(suite_ctx(ctx_factory, [], schedule={"cadence_days": None, "gates": []}))
        assert no_run.fixes == ("invariant pipeline test",)

    def test_a_held_path_on_a_scoped_green_owes_a_whole_suite_run(self, ctx_factory):
        verdict = suite(
            suite_ctx(
                ctx_factory,
                [SCOPED_GREEN],
                schedule={
                    "cadence_days": 7,
                    "gates": ["test.all_pass"],
                    "held_paths": ["core/**"],
                },
                files=["core/engine.py"],
            )
        )
        assert not verdict.ok
        assert "held_paths" in verdict.message
        assert "core/engine.py" in verdict.detail
        assert verdict.fixes[0].startswith("invariant pipeline test")
        assert "--selector" not in verdict.fixes[0]

    def test_a_held_path_on_a_full_green_passes(self, ctx_factory):
        verdict = suite(
            suite_ctx(
                ctx_factory,
                [FULL_GREEN],
                schedule={
                    "cadence_days": 7,
                    "gates": ["test.all_pass"],
                    "held_paths": ["core/**"],
                },
                files=["core/engine.py"],
            )
        )
        assert verdict.ok, verdict.message

    def test_a_change_not_touching_a_held_path_lands_on_its_scoped_green(
        self, ctx_factory
    ):
        verdict = suite(
            suite_ctx(
                ctx_factory,
                [SCOPED_GREEN],
                schedule={
                    "cadence_days": 7,
                    "gates": ["test.all_pass"],
                    "held_paths": ["core/**"],
                },
                files=["src/other.py"],
            )
        )
        assert verdict.ok, verdict.message
        assert "affected suites are green" in verdict.message

    def test_held_paths_with_an_unestablished_change_refuse(self, ctx_factory):
        verdict = suite(
            suite_ctx(
                ctx_factory,
                [SCOPED_GREEN],
                schedule={
                    "cadence_days": 7,
                    "gates": ["test.all_pass"],
                    "held_paths": ["core/**"],
                },
                files=[],
                base="no-such-branch",
            )
        )
        assert not verdict.ok
        assert "could not be established" in verdict.message


# -- sweep.no_full_run ------------------------------------------------------


def detect_ctx(ctx_factory, command=None, extra=None, schedule=None, files=None, session=True):
    write_manifest(
        ctx_factory.main,
        {
            "version": 1,
            "project": {"name": "demo", "ecosystems": ["python"]},
            "methodology": {
                "gates": {"schedule": schedule if schedule is not None else CADENCE_BOTH}
            },
            "commands": {
                "test": "uv run pytest",
                "coverage": "uv run pytest --cov --cov-report=term-missing",
            },
        },
    )
    for rel in files or []:
        path = ctx_factory.wt / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(LIVE, encoding="utf-8")
    return ctx_factory(
        cwd=ctx_factory.main,
        session=(
            {"id": "s1", "files": files or [], "worktree": str(ctx_factory.wt), "base": ""}
            if session
            else None
        ),
        pipeline={"evidence": {}},
        payload=bash_payload(command) if command is not None else {},
        extra=extra or {},
    )


def detect(ctx):
    return only(evaluate(["sweep.no_full_run"], ctx))


class TestTheRuleIsRegisteredAndGraded:
    def test_it_fires_at_pre_tool_use(self):
        assert "pre-tool-use" in get_rule("sweep.no_full_run").events

    def test_the_shell_command_is_part_of_the_payload_contract(self):
        assert "tool_input.command" in NORMALISED_PAYLOAD_KEYS

    @pytest.mark.parametrize(
        ("profile", "level"), [("loose", "warn"), ("standard", "warn"), ("strict", "block")]
    )
    def test_its_level_by_profile(self, tmp_path, profile, level):
        body = {"version": 1, "project": {"name": "demo"}, "enforcement": {"profile": profile}}
        m = Manifest.from_raw(body, root=tmp_path, path=tmp_path / MANIFEST_RELPATH, env={})
        assert m.rule_level("sweep.no_full_run").value == level


class TestFullRunDetection:
    def test_without_a_cadence_it_does_not_apply(self, ctx_factory):
        result = detect(
            detect_ctx(
                ctx_factory, command="uv run pytest", schedule={"cadence_days": None, "gates": []}
            )
        )
        assert result.verdict.skipped
        assert "no sweep cadence" in result.verdict.message

    def test_the_cli_whole_suite_run_is_caught(self, ctx_factory):
        result = detect(detect_ctx(ctx_factory, extra={"full_run": "test"}))
        assert not result.verdict.ok
        assert "whole-suite" in result.verdict.message
        assert any("--selector" in fix for fix in result.verdict.fixes)
        assert any("invariant override sweep.no_full_run" in fix for fix in result.verdict.fixes)

    def test_the_cli_whole_scheme_run_is_caught(self, ctx_factory):
        result = detect(detect_ctx(ctx_factory, extra={"full_run": "coverage"}))
        assert not result.verdict.ok
        assert "whole-scheme" in result.verdict.message
        assert any("pipeline coverage --selector" in fix for fix in result.verdict.fixes)

    def test_a_coverage_run_is_a_suite_run_too(self, ctx_factory):
        result = detect(
            detect_ctx(
                ctx_factory,
                extra={"full_run": "coverage"},
                schedule={"cadence_days": 7, "gates": ["test.all_pass"]},
            )
        )
        assert not result.verdict.ok

    def test_a_whole_suite_run_is_free_when_only_the_floor_is_scheduled(self, ctx_factory):
        result = detect(
            detect_ctx(
                ctx_factory,
                extra={"full_run": "test"},
                schedule={"cadence_days": 7, "gates": ["coverage.meets_floor"]},
            )
        )
        assert result.verdict.skipped

    @pytest.mark.parametrize(
        "command",
        [
            "uv run pytest",
            "uv run pytest -q",
            "uv run pytest -x --tb=short -p no:cacheprovider",
            "cd sub && FOO=1 uv run pytest -q",
            "uv run pytest --cov --cov-report=term-missing",
            "echo start; uv run pytest | tail -3",
        ],
    )
    def test_a_full_run_through_the_shell_is_caught(self, ctx_factory, command):
        result = detect(detect_ctx(ctx_factory, command=command))
        assert not result.verdict.ok, command

    @pytest.mark.parametrize(
        "command",
        [
            "uv run pytest tests/test_x.py",
            "uv run pytest -k engine",
            "uv run pytest --lf",
            "uv run pytest --cov tests/test_x.py",
            "uv run pytest --maxfail=2",
            "python -m pytest",
            "git status",
            "uv run ruff check .",
            "uv run pytest 'unbalanced",
        ],
    )
    def test_a_scoped_or_doubtful_shell_call_gets_no_verdict(self, ctx_factory, command):
        result = detect(detect_ctx(ctx_factory, command=command))
        assert result.verdict.ok, command
        assert result.verdict.skipped, command

    def test_a_call_that_is_no_shell_command_gets_no_verdict(self, ctx_factory):
        ctx = detect_ctx(ctx_factory)
        assert detect(ctx).verdict.skipped

    def test_a_held_path_is_the_run_a_change_owes(self, ctx_factory):
        result = detect(
            detect_ctx(
                ctx_factory,
                command="uv run pytest",
                schedule={**CADENCE_BOTH, "held_paths": ["core/**"]},
                files=["core/engine.py"],
            )
        )
        assert result.verdict.ok and not result.verdict.skipped
        assert "held_paths" in result.verdict.message
        assert "core/engine.py" in result.verdict.detail

    def test_without_a_session_no_held_path_can_exempt_it(self, ctx_factory):
        result = detect(
            detect_ctx(
                ctx_factory,
                command="uv run pytest",
                schedule={**CADENCE_BOTH, "held_paths": ["core/**"]},
                session=False,
            )
        )
        assert not result.verdict.ok

    def test_it_reaches_the_hook_surface(self, ctx_factory):
        verdicts = evaluate_event("pre-tool-use", detect_ctx(ctx_factory, command="uv run pytest"))
        assert any(v.rule_id == "sweep.no_full_run" and v.warns for v in verdicts)

    def test_a_placeholder_in_the_template_matches_any_one_token(self, ctx_factory):
        write_manifest(
            ctx_factory.main,
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["swift"]},
                "methodology": {"gates": {"schedule": CADENCE_BOTH}},
                "enforcement": {"rules": {"coverage.meets_floor": "warn"}},
                "commands": {
                    "test": "xcodebuild test -scheme {scheme} -resultBundlePath {result_bundle}",
                    "coverage": "xcodebuild test -scheme {scheme} -enableCodeCoverage YES",
                },
            },
        )
        ctx = ctx_factory(
            cwd=ctx_factory.main,
            payload=bash_payload("xcodebuild test -scheme App -resultBundlePath /tmp/b.xcresult"),
        )
        assert not detect(ctx).verdict.ok
        scoped = ctx_factory(
            cwd=ctx_factory.main,
            payload=bash_payload(
                "xcodebuild test -scheme App -resultBundlePath /tmp/b -only-testing:AppTests/A"
            ),
        )
        assert detect(scoped).verdict.skipped


# -- the adapters' scope answer ---------------------------------------------


class TestTestScopeOf:
    def test_the_generic_answer_is_full_only_for_nothing_extra(self):
        adapter = get_adapter("generic")
        assert adapter.test_scope_of([]) == "full"
        assert adapter.test_scope_of(["x"]) is None

    @pytest.mark.parametrize(
        "extra",
        [
            [],
            ["-q"],
            ["-x", "-s", "-v", "-vv", "-qq"],
            ["--tb=short", "--no-header"],
            ["-p", "no:cacheprovider"],
            ["--cov", "--cov=src", "--cov-report=term-missing"],
        ],
    )
    def test_python_full(self, extra):
        assert get_adapter("python").test_scope_of(extra) == "full"

    @pytest.mark.parametrize(
        "extra",
        [
            ["tests/test_x.py"],
            ["tests/test_x.py::TestA::test_b"],
            ["-k", "engine"],
            ["-m", "slow"],
            ["--lf"],
            ["--ff"],
            ["--deselect", "tests/x.py::t"],
            ["--deselect=tests/x.py::t"],
            ["-q", "tests", "--weird"],
        ],
    )
    def test_python_scoped(self, extra):
        assert get_adapter("python").test_scope_of(extra) == "scoped"

    @pytest.mark.parametrize(
        "extra", [["--maxfail=2"], ["-n", "4"], ["-p"], ["--tb"], ["-rA"]]
    )
    def test_python_doubt(self, extra):
        assert get_adapter("python").test_scope_of(extra) is None

    def test_swift(self):
        adapter = get_adapter("swift")
        assert adapter.test_scope_of([]) == "full"
        assert adapter.test_scope_of(["-only-testing:AppTests/A"]) == "scoped"
        assert adapter.test_scope_of(["-only-testing:A", "-skip-testing:B"]) is None
        assert adapter.test_scope_of(["-quiet"]) is None

    def test_node(self):
        adapter = get_adapter("node")
        assert adapter.test_scope_of([]) == "full"
        assert adapter.test_scope_of(["--", "src/a.test.ts"]) is None


# -- the pipeline domain ----------------------------------------------------


FAKE_SUITE = "#!/bin/sh\necho \"args: $*\"\necho '3 passed in 0.01s'\n"
FAKE_COVERAGE = (
    "#!/bin/sh\necho \"args: $*\"\necho '3 passed in 0.01s'\n"
    "echo 'app.py      10      0   100%'\n"
)


def domain_project(tmp_path, commands, schedule=None):
    from test_domains import git_project

    from invariant.domains.pipeline import PipelineDomain
    from invariant.domains.session import SessionDomain

    root = tmp_path / "proj"
    git_project(root)
    suite = root / "fake-suite.sh"
    suite.write_text(FAKE_SUITE, encoding="utf-8")
    cov = root / "fake-cov.sh"
    cov.write_text(FAKE_COVERAGE, encoding="utf-8")
    resolved = {
        key: value.replace("SUITE", f"sh {suite}").replace("COV", f"sh {cov}")
        if value
        else value
        for key, value in commands.items()
    }
    body = {
        "version": 1,
        "project": {"name": "demo", "ecosystems": ["python"]},
        "commands": resolved,
    }
    if schedule:
        body["methodology"] = {"gates": {"schedule": schedule}}
        body["enforcement"] = {"rules": {"coverage.meets_floor": "warn"}}
    (root / MANIFEST_RELPATH).write_text(yaml.safe_dump(body), encoding="utf-8")
    fresh = Manifest.load(root)
    session = SessionDomain(fresh).start("x")
    pipelines = PipelineDomain(fresh)
    pipelines.start(session.id, task="t")
    return pipelines, session, root


class TestRunsSayWhichTheyWere:
    def test_a_full_test_run_records_its_scope(self, tmp_path):
        pipelines, session, root = domain_project(
            tmp_path, {"test": "SUITE", "test_one": "SUITE {selector}"}
        )
        record = pipelines.run_tests(session.id, cwd=root)
        assert record["scope"] == "full"
        assert record["selectors"] == []

    def test_a_scoped_test_run_records_its_selectors(self, tmp_path):
        pipelines, session, root = domain_project(
            tmp_path, {"test": "SUITE", "test_one": "SUITE {selector}"}
        )
        record = pipelines.run_tests(session.id, selectors=["tests/a.py", "tests/b.py"], cwd=root)
        assert record["scope"] == "scoped"
        assert record["selectors"] == ["tests/a.py", "tests/b.py"]

    def test_a_scoped_coverage_run_uses_coverage_one(self, tmp_path):
        pipelines, session, root = domain_project(
            tmp_path, {"coverage": "COV --all", "coverage_one": "COV {selector}"}
        )
        result = pipelines.run_coverage(session.id, cwd=root, selectors=["tests/a.py"])
        assert result["command"].endswith("tests/a.py")
        assert result["scope"] == "scoped"
        run_record = pipelines.current(session.id).evidence["coverage_run"]
        assert run_record["scope"] == "scoped"
        assert run_record["selectors"] == ["tests/a.py"]
        last = pipelines.current(session.id).evidence["test_runs"][-1]
        assert last["scope"] == "scoped"

    def test_a_full_coverage_run_records_its_scope(self, tmp_path):
        pipelines, session, root = domain_project(tmp_path, {"coverage": "COV --all"})
        pipelines.run_coverage(session.id, cwd=root)
        run_record = pipelines.current(session.id).evidence["coverage_run"]
        assert run_record["scope"] == "full"
        assert run_record["selectors"] == []

    def test_a_scoped_coverage_run_with_no_template_is_refused(self, tmp_path):
        pipelines, session, root = domain_project(
            tmp_path, {"coverage": "COV --all", "coverage_one": None}
        )
        with pytest.raises(UsageError, match="commands.coverage_one"):
            pipelines.run_coverage(session.id, cwd=root, selectors=["tests/a.py"])
        assert "coverage_run" not in pipelines.current(session.id).evidence

    def test_several_coverage_selectors_the_adapter_cannot_join_are_refused(self, tmp_path):
        pipelines, session, root = domain_project(
            tmp_path, {"coverage": "COV --all", "coverage_one": "COV {selector}"}
        )
        pipelines.manifest.data["project"]["ecosystems"] = ["generic"]
        with pytest.raises(UsageError, match="one selector at a time"):
            pipelines.run_coverage(session.id, cwd=root, selectors=["a", "b"])


class TestTheChildEnvironment:
    def test_host_identity_never_reaches_a_recorded_suite(self, monkeypatch):
        from invariant.domains.pipeline import CHILD_ENV_STRIPPED, child_env

        for name in ("INVARIANT_SESSION_ID", "INVARIANT_HOST_SESSION_ID", "INVARIANT_HANDOFF"):
            monkeypatch.setenv(name, "x")
            assert name in CHILD_ENV_STRIPPED
        env = child_env()
        assert not {"INVARIANT_SESSION_ID", "INVARIANT_HOST_SESSION_ID", "INVARIANT_HANDOFF"} & set(env)

    def test_the_seam_runs_a_command_with_that_environment(self, tmp_path, monkeypatch):
        from invariant.domains.pipeline import run_command

        monkeypatch.setenv("INVARIANT_HANDOFF", "token")
        proc = run_command('echo "[${INVARIANT_HANDOFF:-unset}]"', tmp_path)
        assert proc.stdout.strip() == "[unset]"


# -- the CLI surface --------------------------------------------------------


def cli_project(project, capsys, level, schedule=None):
    assert run("init", "--ecosystem", "python") == 0
    suite = project / "fake-suite.sh"
    suite.write_text(FAKE_SUITE, encoding="utf-8")
    cov = project / "fake-cov.sh"
    cov.write_text(FAKE_COVERAGE, encoding="utf-8")
    path = project / MANIFEST_RELPATH
    body = yaml.safe_load(path.read_text(encoding="utf-8"))
    body.setdefault("commands", {}).update(
        {
            "test": f"sh {suite}",
            "test_one": f"sh {suite} {{selector}}",
            "coverage": f"sh {cov}",
            "coverage_one": f"sh {cov} {{selector}}",
        }
    )
    body.setdefault("methodology", {}).setdefault("gates", {})["schedule"] = (
        schedule or CADENCE_BOTH
    )
    rules = body.setdefault("enforcement", {}).setdefault("rules", {})
    rules["sweep.no_full_run"] = level
    rules["coverage.meets_floor"] = "warn"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "invariant"], cwd=project, check=True)
    assert run("session", "start", "x", "--json") == 0
    sid = payload(capsys)["session"]["id"]
    assert run("pipeline", "start", "--task", "t") == 0
    capsys.readouterr()
    return sid


def recorded_runs(capsys) -> list:
    assert run("pipeline", "status", "--json") == 0
    return json.loads(capsys.readouterr().out)["evidence"].get("test_runs", [])


class TestTheCliSurface:
    def test_a_blocked_whole_suite_run_runs_and_records_nothing(self, project, capsys):
        cli_project(project, capsys, "block")
        assert run("pipeline", "test") == 1
        err = capsys.readouterr().err
        assert "sweep.no_full_run" in err and "whole-suite" in err
        assert recorded_runs(capsys) == []

    def test_a_blocked_whole_scheme_run_runs_and_records_nothing(self, project, capsys):
        cli_project(project, capsys, "block")
        assert run("pipeline", "coverage") == 1
        assert "whole-scheme" in capsys.readouterr().err
        assert recorded_runs(capsys) == []

    def test_a_scoped_run_is_never_asked(self, project, capsys):
        cli_project(project, capsys, "block")
        assert run("pipeline", "test", "--selector", "tests/a.py") == 0
        assert run("pipeline", "coverage", "--selector", "tests/a.py") == 0
        captured = capsys.readouterr()
        assert "sweep.no_full_run" not in captured.err
        assert "tests/a.py" in captured.out
        runs = recorded_runs(capsys)
        assert [r["scope"] for r in runs] == ["scoped", "scoped"]

    def test_a_warned_full_run_runs_and_says_so(self, project, capsys):
        cli_project(project, capsys, "warn")
        assert run("pipeline", "test") == 0
        captured = capsys.readouterr()
        assert "[warn] sweep.no_full_run" in captured.err
        assert "passed" in captured.out
        assert [r["scope"] for r in recorded_runs(capsys)] == ["full"]

    def test_an_override_grant_passes_one_full_run_and_is_recorded(self, project, capsys):
        sid = cli_project(project, capsys, "block")
        assert run("override", "sweep.no_full_run", "--reason", "release check") == 0
        capsys.readouterr()
        assert run("pipeline", "test") == 0
        assert "[override] sweep.no_full_run" in capsys.readouterr().err
        assert run("session", "status", "--json") == 0
        doc = json.loads(capsys.readouterr().out)["session"]
        assert doc["id"] == sid
        spent = [o for o in doc["overrides"] if o.get("rule") == "sweep.no_full_run"]
        assert spent and spent[0]["surface"] == "cli"
        assert run("pipeline", "test") == 1

    def test_ask_stops_the_cli_too(self, project, capsys):
        cli_project(project, capsys, "ask")
        assert run("pipeline", "test") == 1

    def test_no_cadence_no_question(self, project, capsys):
        cli_project(project, capsys, "block", schedule={"cadence_days": None, "gates": []})
        assert run("pipeline", "test") == 0
        assert "sweep.no_full_run" not in capsys.readouterr().err

    def test_the_selector_flag_parses_repeatedly(self):
        from invariant.cli.main import build_parser

        args = build_parser().parse_args(
            ["pipeline", "coverage", "--selector", "a", "--selector", "b"]
        )
        assert args.selector == ["a", "b"]

