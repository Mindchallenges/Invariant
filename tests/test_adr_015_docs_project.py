"""ADR-015 increment 2: the shipped `docs-project` lane and doctor's command
rows per lane (D2, rulings 5 and 6).

The lane lands a documentation repository's change on the sphinx adapter's
strict test and a privacy scan, with no build step. Doctor's command rows
follow the lanes a project's sessions can run: a command is required where a
selectable lane carries a gate that reads its run, and each row names that
lane and gate.
"""

import json
import os

import pytest
import yaml
from test_adapters_sphinx import GREEN_OUT, LINKCHECK_RED_ERR, LINKCHECK_RED_OUT, tree
from test_domains import git_project
from test_rules import ctx_factory, repo, write_manifest  # noqa: F401

from invariant.adapters import defaults_for
from invariant.cli.main import command_rows, main
from invariant.core.machines import (
    command_needs,
    command_of_gate,
    load_machine,
    load_pipeline_machine,
    pipeline_ids,
    shipped_ids,
)
from invariant.core.rules import evaluate
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain

LANE = "docs-project"


def gates_of(machine) -> set[str]:
    return set(machine.gate_ids())


# -- the lane ------------------------------------------------------------------------


class TestTheLane:
    def test_it_is_shipped_and_selectable(self, make_manifest):
        assert LANE in shipped_ids()
        assert load_machine(LANE).kind == "pipeline"
        assert LANE in pipeline_ids(make_manifest({}))

    def test_its_states_are_drafting_reviewing_and_the_landing(self):
        machine = load_machine(LANE)
        assert machine.initial == "drafting"
        assert set(machine.terminal) == {"committed", "abandoned"}
        assert machine.states_with_role("design") == ("drafting",)
        assert machine.states_with_role("review") == ("reviewing",)
        assert machine.states_with_role("landing") == ("committed",)
        assert machine.states_with_role("abandon") == ("abandoned",)
        assert machine.can("drafting", "reviewing")
        assert machine.can("reviewing", "committed")
        assert machine.can("reviewing", "drafting")

    def test_the_landing_runs_the_test_and_the_privacy_scan(self):
        """Ruling 5's gates, and nothing that runs a build (ruling 6)."""
        committed = load_machine(LANE).state("committed")
        assert set(committed.entry_gates) == {"test.all_pass", "privacy.clean"}

    def test_it_owes_neither_a_red_test_nor_coverage(self, make_manifest):
        gates = gates_of(load_pipeline_machine(make_manifest({}), LANE))
        assert "test.new_test_fails" not in gates
        assert "coverage.meets_floor" not in gates

    def test_the_injected_gates_attach_by_role(self, make_manifest):
        machine = load_pipeline_machine(make_manifest({}), LANE)
        assert "docs.read_architecture" in machine.state("drafting").exit_gates
        landing = set(machine.state("committed").entry_gates)
        assert {
            "docs.updated",
            "agents.design_agent_ran",
            "agents.review_agent_ran",
            "test.all_pass",
            "privacy.clean",
        } <= landing

    def test_reading_back_and_going_back_are_ungated(self, make_manifest):
        machine = load_pipeline_machine(make_manifest({}), LANE)
        domain = PipelineDomain(make_manifest({}))
        assert domain.gates_for("drafting", "reviewing", machine) == [
            "docs.read_architecture",
            "docs.ahead_of_code",
        ]
        assert domain.gates_for("reviewing", "drafting", machine) == []


# -- which commands the lanes need ---------------------------------------------------------


class TestCommandNeeds:
    def test_the_gates_that_read_a_run(self):
        assert command_of_gate("test.all_pass") == "test"
        assert command_of_gate("test.new_test_fails") == "test"
        assert command_of_gate("coverage.meets_floor") == "coverage"
        assert command_of_gate("privacy.clean") is None
        assert command_of_gate("docs.builds_strict") is None

    def test_every_selectable_lane_is_asked_the_configured_one_first(self, make_manifest):
        needs = command_needs(make_manifest({"methodology": {"pipeline": "chore"}}))
        lanes = [lane for lane, _gates in needs["test"]]
        assert lanes[0] == "chore"
        assert set(lanes) == {"chore", "tdd", "bugfix", "spec-first", LANE}
        assert dict(needs["test"])["tdd"] == ["test.all_pass", "test.new_test_fails"]
        assert dict(needs["test"])[LANE] == ["test.all_pass"]
        assert needs["coverage"] == [
            ("spec-first", ["coverage.meets_floor"]),
            ("tdd", ["coverage.meets_floor"]),
        ]

    def test_the_coverage_lanes_are_the_ones_carrying_the_floor(self, make_manifest):
        manifest = make_manifest({})
        expected = [
            lane
            for lane in pipeline_ids(manifest)
            if "coverage.meets_floor" in load_pipeline_machine(manifest, lane).gate_ids()
        ]
        found = [lane for lane, _gates in command_needs(manifest)["coverage"]]
        assert sorted(found) == sorted(expected) and "tdd" in found

    def test_a_gate_switched_off_requires_nothing(self, make_manifest):
        manifest = make_manifest(
            {"enforcement": {"rules": {"test.new_test_fails": "off"}}}
        )
        assert dict(command_needs(manifest)["test"])["tdd"] == ["test.all_pass"]

    def test_with_every_test_gate_off_nothing_needs_the_test(self, make_manifest):
        manifest = make_manifest(
            {"enforcement": {"rules": {"test.new_test_fails": "off", "test.all_pass": "off"}}}
        )
        assert command_needs(manifest)["test"] == []

    def test_no_floor_requires_no_coverage(self, make_manifest):
        manifest = make_manifest({"methodology": {"gates": {"coverage_min": 0}}})
        assert command_needs(manifest)["coverage"] == []

    def test_a_project_lane_counts(self, make_manifest, tmp_path):
        (tmp_path / "lanes").mkdir()
        (tmp_path / "lanes" / "site.yaml").write_text(
            yaml.safe_dump(
                {
                    "id": "site",
                    "kind": "pipeline",
                    "initial": "writing",
                    "terminal": ["landed"],
                    "states": {
                        "writing": {"next": ["landed"]},
                        "landed": {"roles": ["landing"], "entry_gates": ["test.all_pass"]},
                    },
                }
            ),
            encoding="utf-8",
        )
        manifest = make_manifest({"methodology": {"extra_machines": ["lanes"]}})
        assert dict(command_needs(manifest)["test"])["site"] == ["test.all_pass"]


# -- doctor's rows ---------------------------------------------------------------------------


def rows(manifest) -> dict[str, tuple[bool, str]]:
    return {label: (ok, detail) for label, ok, detail in command_rows(manifest)}


class TestCommandRows:
    def test_an_unset_build_passes_required_by_nothing(self, make_manifest):
        ok, detail = rows(make_manifest({}))["commands.build"]
        assert ok
        assert detail == "unset; required by nothing: no gate of any lane runs it"

    def test_a_set_build_is_reported_not_required(self, make_manifest):
        ok, detail = rows(make_manifest({"commands": {"build": "make"}}))["commands.build"]
        assert ok and detail.startswith("make; required by nothing")

    def test_a_set_test_names_each_lane_and_gate(self, make_manifest):
        ok, detail = rows(make_manifest({"commands": {"test": "uv run pytest"}}))["commands.test"]
        assert ok
        assert detail.startswith(
            "uv run pytest; required by tdd (methodology.pipeline): "
            "test.all_pass, test.new_test_fails; "
        )
        assert f"{LANE}: test.all_pass" in detail

    def test_an_unset_test_fails_naming_the_gates(self, make_manifest):
        manifest = make_manifest({"methodology": {"pipeline": LANE}})
        ok, detail = rows(manifest)["commands.test"]
        assert not ok
        assert detail.startswith(f"unset; required by {LANE} (methodology.pipeline): test.all_pass; ")
        assert detail.endswith("those gates would have nothing to run. Set commands.test")

    def test_coverage_is_required_where_an_adapter_measures(self, make_manifest):
        manifest = make_manifest({"project": {"ecosystems": ["python"]}})
        manifest.data["commands"]["coverage"] = None
        ok, detail = rows(manifest)["commands.coverage"]
        assert not ok
        assert "tdd (methodology.pipeline): coverage.meets_floor" in detail

    def test_a_set_coverage_passes_naming_the_lanes(self, make_manifest):
        manifest = make_manifest({"project": {"ecosystems": ["python"]}})
        ok, detail = rows(manifest)["commands.coverage"]
        assert ok and "required by tdd (methodology.pipeline): coverage.meets_floor" in detail

    def test_coverage_is_not_required_where_no_adapter_measures(self, make_manifest):
        manifest = make_manifest(
            {"project": {"ecosystems": ["sphinx"]}, "methodology": {"pipeline": LANE}}
        )
        ok, detail = rows(manifest)["commands.coverage"]
        assert ok
        assert detail.startswith(
            "unset; not required: every declared adapter declares coverage not applicable (sphinx)"
        )

    def test_nothing_carrying_the_floor_requires_no_coverage(self, make_manifest):
        manifest = make_manifest({"methodology": {"gates": {"coverage_min": 0}}})
        ok, detail = rows(manifest)["commands.coverage"]
        assert ok
        assert detail.endswith("required by nothing: no lane carries a gate that reads its run")

    def test_lanes_that_cannot_be_read_fail_one_row(self, make_manifest, tmp_path):
        (tmp_path / "lanes").mkdir()
        (tmp_path / "lanes" / "broken.yaml").write_text(
            yaml.safe_dump(
                {
                    "id": "broken",
                    "kind": "pipeline",
                    "initial": "a",
                    "terminal": ["b"],
                    "states": {"a": {"roles": ["review"], "next": ["b"]}, "b": {}},
                }
            ),
            encoding="utf-8",
        )
        manifest = make_manifest({"methodology": {"extra_machines": ["lanes"]}})
        ((label, ok, detail),) = command_rows(manifest)
        assert (label, ok) == ("commands", False)
        assert detail.startswith("the lanes could not be read: ")
        assert "'broken'" in detail


class TestDoctor:
    def doctor(self, root, monkeypatch, capsys) -> dict[str, dict]:
        monkeypatch.chdir(root)
        main(["doctor", "--json"])
        return {row["check"]: row for row in json.loads(capsys.readouterr().out)["checks"]}

    def test_this_repository_shape_passes_with_build_unset(self, tmp_path, monkeypatch, capsys):
        """The increment's acceptance, on a manifest shaped like invariant's
        own: python, tdd, no build command."""
        root = tmp_path / "proj"
        git_project(
            root,
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["python"]},
                "commands": {"build": None},
            },
        )
        found = self.doctor(root, monkeypatch, capsys)
        for label in ("commands.build", "commands.test", "commands.coverage"):
            assert found[label]["ok"], found[label]
        assert "commands.build configured" not in found
        assert "required by tdd (methodology.pipeline)" in found["commands.test"]["detail"]

    def test_a_documentation_repository_passes_with_test_alone(
        self, tmp_path, monkeypatch, capsys
    ):
        root = tmp_path / "proj"
        git_project(
            root,
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["sphinx"]},
                "methodology": {"pipeline": LANE},
                "vars": {"srcdir": "manual"},
            },
        )
        found = self.doctor(root, monkeypatch, capsys)
        for label in ("commands.build", "commands.test", "commands.coverage"):
            assert found[label]["ok"], found[label]
        assert found["coverage"]["detail"].startswith("not applicable (sphinx)")


# -- through the lane, on a toy documentation repository --------------------------------------


FAKE = """\
printf '%s' "$SPHINX_OUT"
printf '%s' "$SPHINX_ERR" >&2
exit "$SPHINX_EXIT"
"""


def documentation_repository(tmp_path, test_command):
    root = tmp_path / "proj"
    manifest = git_project(
        root,
        {
            "version": 1,
            "project": {"name": "demo", "ecosystems": ["sphinx"]},
            "methodology": {"pipeline": LANE},
            "vars": {"srcdir": "manual"},
            "commands": {"test": test_command},
        },
    )
    tree(
        root,
        {
            "fake-sphinx.sh": FAKE,
            "manual/conf.py": "project = 'toy'\n",
            "manual/index.rst": "Toy\n===\n\nSee :doc:`other` and :doc:`missing`.\n\n"
            ".. toctree::\n\n   other\n",
            "manual/other.rst": "Other\n=====\n",
        },
    )
    session = SessionDomain(manifest).start("x")
    pipelines = PipelineDomain(manifest)
    pipelines.start(session.id, task="t")
    return manifest, pipelines, session, root


def landing_verdicts(pipelines, session, root):
    found = pipelines.evaluate_gates(session.id, "committed", cwd=root)
    return {e.rule_id: e.verdict for e in found}


class TestThroughTheLane:
    def test_the_lane_is_pinned_and_lands_on_the_test(self, tmp_path, monkeypatch):
        _manifest, pipelines, session, root = documentation_repository(
            tmp_path, "sh fake-sphinx.sh {srcdir}"
        )
        assert pipelines.require(session.id).methodology == LANE
        p = pipelines.require(session.id)
        p.state = "reviewing"
        pipelines._save(session.id, p)

        monkeypatch.setenv("SPHINX_OUT", LINKCHECK_RED_OUT)
        monkeypatch.setenv("SPHINX_ERR", LINKCHECK_RED_ERR.replace("/work/toy", str(root)))
        monkeypatch.setenv("SPHINX_EXIT", "1")
        pipelines.run_tests(session.id, cwd=root)
        red = landing_verdicts(pipelines, session, root)
        assert not red["test.all_pass"].ok
        assert "coverage.meets_floor" not in red

        monkeypatch.setenv("SPHINX_OUT", GREEN_OUT)
        monkeypatch.setenv("SPHINX_ERR", "")
        monkeypatch.setenv("SPHINX_EXIT", "0")
        pipelines.run_tests(session.id, cwd=root)
        green = landing_verdicts(pipelines, session, root)
        assert green["test.all_pass"].ok and not green["test.all_pass"].skipped

    def test_coverage_reads_not_applicable_naming_the_adapter(self, ctx_factory):  # noqa: F811
        """Asked directly, since the lane does not carry it: N/A, naming the
        adapter, for a change made only of the repository's documents."""
        root = ctx_factory.wt
        tree(root, {"source/conf.py": "", "source/index.rst": "x\n"})
        write_manifest(
            ctx_factory.main,
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["sphinx"]},
                "vars": {"srcdir": "source"},
            },
        )
        ctx = ctx_factory(
            cwd=ctx_factory.main,
            session={
                "id": "s1",
                "files": ["source/conf.py", "source/index.rst"],
                "worktree": str(root),
            },
            pipeline={"evidence": {"coverage": {}}},
        )
        (verdict,) = [e.verdict for e in evaluate(["coverage.meets_floor"], ctx)]
        assert verdict.skipped
        assert verdict.message.startswith("the sphinx adapter declares coverage not applicable")
        assert verdict.detail == "source/conf.py, source/index.rst"


@pytest.mark.skipif(
    not os.environ.get("INVARIANT_TEST_SPHINX_BUILD"),
    reason="set INVARIANT_TEST_SPHINX_BUILD to a sphinx-build to run a real build",
)
class TestARealBuildThroughTheLane:
    def test_a_broken_reference_fails_the_landing_and_its_fix_passes(self, tmp_path):
        sphinx_build = os.environ["INVARIANT_TEST_SPHINX_BUILD"]
        test = defaults_for("sphinx")["commands"]["test"].replace("sphinx-build", sphinx_build)
        _manifest, pipelines, session, root = documentation_repository(tmp_path, test)
        p = pipelines.require(session.id)
        p.state = "reviewing"
        pipelines._save(session.id, p)
        pipelines.run_tests(session.id, cwd=root)
        assert not landing_verdicts(pipelines, session, root)["test.all_pass"].ok
        (root / "manual" / "index.rst").write_text(
            "Toy\n===\n\nSee :doc:`other`.\n\n.. toctree::\n\n   other\n", encoding="utf-8"
        )
        pipelines.run_tests(session.id, cwd=root)
        assert landing_verdicts(pipelines, session, root)["test.all_pass"].ok
