"""ADR-013 — a manifest declaring several ecosystems runs each through its own.

The primary ecosystem keeps the flat ``commands.*`` keys and the whole-suite
run; every further one keeps its commands under ``commands.<ecosystem>``. A
scoped run goes to the ecosystem whose adapter owns its selectors, that
ecosystem's command runs it and that ecosystem's adapter reads it. A run whose
selectors span ecosystems is refused. A single-ecosystem manifest resolves and
runs exactly as it did before.
"""

import subprocess

import pytest
from test_domains import git_project
from test_rules import (  # noqa: F401
    bash_payload,
    ctx_factory,
    only,
    repo,
    write_manifest,
)

from invariant import adapters
from invariant.adapters import get_adapter
from invariant.adapters.node import NodeAdapter
from invariant.adapters.python_ import PythonAdapter
from invariant.core.errors import ManifestError, UsageError
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.core.rules import evaluate
from invariant.domains import pipeline as pipeline_module
from invariant.domains.pipeline import PipelineDomain, ecosystem_of
from invariant.domains.session import SessionDomain
from invariant.domains.sweep import SweepDomain

PY = PythonAdapter.manifest_defaults["commands"]
NODE = NodeAdapter.manifest_defaults["commands"]


def resolve(tmp_path, ecosystems, **raw):
    body = {"project": {"ecosystems": ecosystems}, **raw}
    return Manifest.from_raw(body, root=tmp_path, path=tmp_path / MANIFEST_RELPATH, env={})


# -- the manifest ------------------------------------------------------------


class TestResolution:
    def test_a_single_ecosystem_resolves_as_it_always_did(self, tmp_path):
        m = resolve(tmp_path, ["python"])
        for name, command in PY.items():
            assert m.get(f"commands.{name}") == command
        assert m.declared_ecosystems == ["python"]
        assert m.primary_ecosystem == "python"
        assert "python" not in (m.get("commands") or {})

    def test_the_primary_owns_every_flat_command(self, tmp_path):
        m = resolve(tmp_path, ["python", "node"])
        for name, command in PY.items():
            assert m.get(f"commands.{name}") == command, name
        # python leaves build unset; node's build must not fill it.
        assert m.get("commands.build") is None

    def test_a_further_ecosystem_keeps_its_commands_under_its_own_key(self, tmp_path):
        m = resolve(tmp_path, ["python", "node"])
        assert m.get("commands.node") == NODE
        assert m.source_of("commands.node.test_one") == "adapter:node"
        assert m.source_of("commands.test_one") == "adapter:python"

    def test_an_explicit_per_ecosystem_command_overrides_only_that_key(self, tmp_path):
        m = resolve(
            tmp_path,
            ["python", "node"],
            commands={"test": "uv run pytest", "node": {"test_one": "npx vitest run {selector}"}},
        )
        assert m.get("commands.node.test_one") == "npx vitest run {selector}"
        assert m.source_of("commands.node.test_one") == "repo:.claude/invariant.yaml"
        assert m.get("commands.node.test") == NODE["test"]
        assert m.get("commands.test") == "uv run pytest"

    def test_a_repeated_ecosystem_is_declared_once(self, tmp_path):
        m = resolve(tmp_path, ["python", "python", "node", "node"])
        assert m.declared_ecosystems == ["python", "node"]
        assert m.get("commands.test") == PY["test"]

    def test_a_further_adapter_fills_only_what_no_adapter_set(self, tmp_path, monkeypatch):
        fake = {
            "python": {"docs": {"format": "markdown"}},
            "other": {
                "docs": {"format": "sphinx", "index": "docs/index.rst"},
                "commands": {"test": "other-test"},
            },
        }
        monkeypatch.setattr(adapters, "defaults_for", lambda eco: dict(fake.get(eco, {})))
        m = resolve(tmp_path, ["python", "other"])
        assert m.get("docs.format") == "markdown"
        assert m.source_of("docs.format") == "adapter:python"
        assert m.get("docs.index") == "docs/index.rst"
        assert m.source_of("docs.index") == "adapter:other"
        assert m.get("commands.other.test") == "other-test"
        assert m.get("commands.test") is None


class TestCommandLookup:
    def test_the_primary_is_the_flat_key_however_it_is_named(self, tmp_path):
        m = resolve(tmp_path, ["python", "node"])
        assert m.command_key("test") == "commands.test"
        assert m.command_key("test", "python") == "commands.test"
        assert m.command_key("test", "node") == "commands.node.test"

    def test_command_for_resolves_the_named_ecosystems_template(self, tmp_path):
        m = resolve(tmp_path, ["python", "node"])
        assert m.command_for("test_one", "node", selector="a.test.ts") == "npm test -- a.test.ts"
        assert m.command_for("test_one", selector="t.py") == "pytest t.py"

    def test_a_missing_further_command_names_its_own_key(self, tmp_path):
        m = resolve(tmp_path, ["python", "generic"])
        assert m.command_template("test_one", "generic") is None
        with pytest.raises(ManifestError, match=r"commands\.generic\.test_one"):
            m.command_for("test_one", "generic", selector="x")


class TestValidation:
    @pytest.mark.parametrize(
        ("ecosystems", "commands", "match"),
        [
            (["python", "node"], {"node": "npm test"}, r"commands\.node must be a mapping"),
            (["python", "node"], {"python": {"test": "x"}}, r"primary ecosystem"),
            (["python"], {"node": {"test": "x"}}, r"does not declare \(python\)"),
            (["python", "node"], {"node": {"tset": "x"}}, r"commands\.node\.tset is not a command"),
            (["python", "node"], {"node": {"test": 5}}, r"must be a command string or null"),
        ],
    )
    def test_a_block_that_would_do_nothing_is_refused(self, tmp_path, ecosystems, commands, match):
        with pytest.raises(ManifestError, match=match):
            resolve(tmp_path, ecosystems, commands=commands)

    def test_a_null_further_command_is_accepted(self, tmp_path):
        m = resolve(tmp_path, ["python", "node"], commands={"node": {"build": None}})
        assert m.get("commands.node.build") is None


# -- the adapters' ownership answer -----------------------------------------


class TestOwnsSelector:
    @pytest.mark.parametrize(
        ("eco", "selector", "owned"),
        [
            ("python", "tests/test_x.py", True),
            ("python", "tests/test_x.py::TestA::test_b", True),
            ("python", "src/pkg/mod.py", False),
            ("python", "web/src/render.test.ts", False),
            ("python", "-k engine", False),
            ("node", "web/src/render.test.ts", True),
            ("node", "web/__tests__/a.js", True),
            ("node", "web/src/render.ts", False),
            ("node", "tests/test_x.py", False),
            ("generic", "tests/test_x.py", False),
        ],
    )
    def test_ownership_is_claimed_only_on_positive_evidence(self, eco, selector, owned):
        assert get_adapter(eco).owns_selector(selector) is owned


# -- routing ------------------------------------------------------------------


class TestEcosystemOf:
    def test_a_single_ecosystem_never_routes(self, tmp_path):
        m = resolve(tmp_path, ["python"])
        assert ecosystem_of(m, ["web/src/render.test.ts"]) == "python"

    def test_no_selector_is_the_primarys_whole_suite(self, tmp_path):
        assert ecosystem_of(resolve(tmp_path, ["python", "node"]), []) == "python"

    def test_a_selector_goes_to_the_ecosystem_that_owns_it(self, tmp_path):
        m = resolve(tmp_path, ["python", "node"])
        assert ecosystem_of(m, ["web/a.test.ts", "web/b.spec.ts"]) == "node"
        assert ecosystem_of(m, ["tests/test_x.py::test_y"]) == "python"

    def test_an_unclaimed_selector_is_the_primarys(self, tmp_path):
        assert ecosystem_of(resolve(tmp_path, ["node", "python"]), ["-k engine"]) == "node"

    def test_selectors_spanning_ecosystems_are_refused_naming_each(self, tmp_path):
        m = resolve(tmp_path, ["python", "node"])
        with pytest.raises(UsageError) as exc:
            ecosystem_of(m, ["tests/test_x.py", "web/a.test.ts"])
        message = str(exc.value)
        assert "python: tests/test_x.py" in message
        assert "node: web/a.test.ts" in message
        assert "Nothing was run" in message

    def test_a_selector_two_adapters_claim_is_refused(self, tmp_path):
        m = resolve(tmp_path, ["swift", "swift_xcode"])
        with pytest.raises(UsageError, match="more than one declared ecosystem"):
            ecosystem_of(m, ["AppTests/LoginTests.swift"])


TWO = {"version": 1, "project": {"name": "demo", "ecosystems": ["python", "node"]}}


def started(m):
    session = SessionDomain(m).start("x")
    pipelines = PipelineDomain(m)
    pipelines.start(session.id)
    return session, pipelines


def fake_runner(monkeypatch, stdout, code=0):
    ran: list[str] = []

    def fake_run(command, root):
        ran.append(command)
        return subprocess.CompletedProcess(command, code, stdout=stdout, stderr="")

    monkeypatch.setattr(pipeline_module, "run_command", fake_run)
    return ran


class TestRunTests:
    def test_the_record_names_the_ecosystem_that_ran(self, tmp_path, monkeypatch):
        m = git_project(tmp_path / "proj", TWO)
        ran = fake_runner(monkeypatch, "Tests:       1 failed, 1 total\n", code=1)
        session, pipelines = started(m)
        record = pipelines.run_tests(session.id, phase="red", selectors=["web/a.test.ts"])
        assert ran == ["npm test -- web/a.test.ts"]
        assert record["ecosystem"] == "node"
        assert record["failed"] == 1

    def test_a_primary_selector_runs_the_primarys_command(self, tmp_path, monkeypatch):
        m = git_project(tmp_path / "proj", TWO)
        ran = fake_runner(monkeypatch, "1 passed in 0.01s\n")
        session, pipelines = started(m)
        record = pipelines.run_tests(session.id, selectors=["tests/test_x.py"])
        assert ran == ["pytest tests/test_x.py"]
        assert record["ecosystem"] == "python"

    def test_a_spanning_run_records_nothing(self, tmp_path, monkeypatch):
        m = git_project(tmp_path / "proj", TWO)
        ran = fake_runner(monkeypatch, "")
        session, pipelines = started(m)
        with pytest.raises(UsageError):
            pipelines.run_tests(session.id, selectors=["tests/test_x.py", "web/a.test.ts"])
        assert ran == []
        assert not pipelines.current(session.id).evidence.get("test_runs")


class TestRunCoverage:
    def test_a_further_ecosystem_runs_its_own_coverage_command(self, tmp_path, monkeypatch):
        m = git_project(tmp_path / "proj", TWO)
        ran = fake_runner(monkeypatch, "Tests:       2 passed, 2 total\n")
        monkeypatch.setattr(
            NodeAdapter, "parse_coverage", lambda self, raw: {"web/src/render.ts": 95.0}
        )
        session, pipelines = started(m)
        result = pipelines.run_coverage(session.id, selectors=["web/a.test.ts"])
        assert ran == ["npm test -- --coverage web/a.test.ts"]
        run = pipelines.current(session.id).evidence["coverage_run"]
        assert run["ecosystem"] == "node"
        assert result["coverage"] == {"web/src/render.ts": 95.0}

    def test_node_coverage_is_still_unreadable_until_bug_018(self, tmp_path, monkeypatch):
        m = git_project(tmp_path / "proj", TWO)
        fake_runner(monkeypatch, "Tests:       2 passed, 2 total\n")
        session, pipelines = started(m)
        with pytest.raises(UsageError, match="no per-file measurements"):
            pipelines.run_coverage(session.id, selectors=["web/a.test.ts"])

    def test_a_further_ecosystems_report_leg_is_its_own(self, tmp_path, monkeypatch):
        body = {**TWO, "commands": {"node": {"coverage_report": "report-node"}}}
        m = git_project(tmp_path / "proj", body)
        ran = fake_runner(monkeypatch, "Tests:       2 passed, 2 total\n")
        monkeypatch.setattr(NodeAdapter, "parse_coverage", lambda self, raw: {"web/a.ts": 91.0})
        session, pipelines = started(m)
        pipelines.run_coverage(session.id, selectors=["web/a.test.ts"])
        assert ran == ["npm test -- --coverage web/a.test.ts", "report-node"]

    def test_a_missing_further_coverage_one_names_its_key(self, tmp_path, monkeypatch):
        body = {**TWO, "commands": {"node": {"coverage_one": None}}}
        m = git_project(tmp_path / "proj", body)
        ran = fake_runner(monkeypatch, "")
        session, pipelines = started(m)
        with pytest.raises(UsageError, match=r"commands\.node\.coverage_one"):
            pipelines.run_coverage(session.id, selectors=["web/a.test.ts"])
        assert ran == []


# -- the sweep and the full-run detector --------------------------------------


class TestTheSweepHoldsThePrimary:
    def test_its_adapter_is_the_primarys(self, tmp_path):
        m = git_project(tmp_path / "proj", TWO)
        sweep = SweepDomain(m, store=None)
        assert isinstance(sweep.adapter, PythonAdapter)


CADENCE = {"cadence_days": 7, "gates": ["coverage.meets_floor", "test.all_pass"]}


def detect(ctx_factory, command, ecosystems=("python", "node")):  # noqa: F811
    write_manifest(
        ctx_factory.main,
        {
            "version": 1,
            "project": {"name": "demo", "ecosystems": list(ecosystems)},
            "methodology": {"gates": {"schedule": CADENCE}},
            "enforcement": {"rules": {"coverage.meets_floor": "warn"}},
        },
    )
    ctx = ctx_factory(cwd=ctx_factory.main, payload=bash_payload(command))
    return only(evaluate(["sweep.no_full_run"], ctx))


class TestFullRunPerEcosystem:
    @pytest.mark.parametrize("command", ["pytest", "npm test", "npm test -- --coverage"])
    def test_each_ecosystems_whole_suite_is_a_full_run(self, ctx_factory, command):  # noqa: F811
        assert not detect(ctx_factory, command).verdict.ok, command

    @pytest.mark.parametrize("command", ["pytest tests/test_x.py", "npm test -- web/a.test.ts"])
    def test_each_ecosystems_scoped_run_gets_no_verdict(self, ctx_factory, command):  # noqa: F811
        assert detect(ctx_factory, command).verdict.skipped, command

    def test_a_single_ecosystem_still_reads_only_its_own(self, ctx_factory):  # noqa: F811
        assert detect(ctx_factory, "npm test", ecosystems=("python",)).verdict.skipped
