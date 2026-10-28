"""The ruleset pinned on the pipeline at start (IDEA_031 A2 increment 6).

ADR-012 D3 ("The ruleset is pinned per pipeline") and ruling DN-D. At
`pipeline start`, with `guidelines.ruleset` configured, the record carries the
ruleset's path and content hash, read through `load_ruleset` from the tree in
hand, as the methodology is carried (AUD-02, AUD-03). A ruleset that does not
load is recorded with the reason and the pipeline still starts; the landing
refuses it through `guidelines.ruleset_valid`. With no ruleset configured
nothing is pinned and nothing changes. The comparison of a verdict's hash with
the pin is A4's (DN-D), so no gate here reads the digest.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from conftest import write_manifest
from test_cli import commit_all, out, payload, run
from test_domains import git_project
from test_guidelines import RULESET
from test_guidelines_domain import RULESET_REL, write
from test_guidelines_kinds import REGISTRY, REGISTRY_REL
from test_guidelines_kinds import RULESET as WITH_REGISTRY

from invariant.core import guidelines, rules
from invariant.core.machines import load_pipeline_machine
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.core.rules import RuleContext, load_ruleset
from invariant.domains.pipeline import Pipeline, PipelineDomain
from invariant.domains.session import SessionDomain

VALID = "guidelines.ruleset_valid"
DRIFT = "guidelines.generated_current"
#: The ruleset's pattern rule reads code, which needs a masking adapter.
CONFIGURED = {
    "version": 1,
    "project": {"name": "demo", "ecosystems": ["python"]},
    "guidelines": {"ruleset": RULESET_REL},
}


def digest(text: str) -> str:
    return guidelines.digest_of(text.encode("utf-8"))


def started(m: Manifest, **kwargs) -> tuple[str, Pipeline]:
    session = SessionDomain(m).start("pin")
    pipeline = PipelineDomain(m).start(session.id, task="t", cwd=m.root, **kwargs)
    return session.id, pipeline


def set_manifest(m: Manifest, edit) -> Manifest:
    path = m.root / MANIFEST_RELPATH
    body = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(body)
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return Manifest.load(m.root)


def landing_entry(machine) -> tuple[str, ...]:
    (landing,) = machine.states_with_role("landing")
    return tuple(machine.state(landing).entry_gates)


@pytest.fixture
def m(tmp_path) -> Manifest:
    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, RULESET)
    return git_project(root, CONFIGURED)


class TestWhatIsPinned:
    def test_a_loaded_ruleset_pins_its_path_and_digest(self, m):
        _, pipeline = started(m)
        assert pipeline.ruleset == {"path": RULESET_REL, "digest": digest(RULESET), "error": None}

    def test_the_pin_survives_a_reload(self, m):
        sid, pipeline = started(m)
        fresh = PipelineDomain(Manifest.load(m.root)).current(sid)
        assert fresh.ruleset == pipeline.ruleset

    def test_no_ruleset_pins_nothing(self, tmp_path):
        m = git_project(tmp_path / "bare")
        sid, pipeline = started(m)
        assert pipeline.ruleset is None
        assert PipelineDomain(m).current(sid).to_dict()["ruleset"] is None

    def test_the_digest_is_the_one_load_ruleset_reads(self, m):
        """One owner of "what the ruleset is": the pin and the gate agree."""
        _, pipeline = started(m)
        ctx = RuleContext(manifest=m, cwd=m.root)
        assert rules.ruleset_pin(ctx) == pipeline.ruleset
        ruleset, failure = load_ruleset(ctx)
        assert failure is None and pipeline.ruleset["digest"] == ruleset.digest

    def test_it_is_read_from_the_session_worktree(self, m, tmp_path):
        """The tree in hand: a session's worktree beats the checkout called from."""
        worktree = tmp_path / "wt"
        edited = RULESET.replace("title: Testing", "title: Tests")
        write(worktree, RULESET_REL, edited)
        _, pipeline = started(m, session={"worktree": str(worktree)})
        assert pipeline.ruleset["digest"] == digest(edited)

    def test_a_registry_is_not_part_of_the_pin(self, tmp_path):
        """A registry is read as it stands after the change (ADR-012, A2
        increment 2), so editing it mid-pipeline is no ruleset change."""
        root = tmp_path / "proj"
        root.mkdir()
        write(root, RULESET_REL, WITH_REGISTRY)
        write(root, REGISTRY_REL, REGISTRY)
        m = git_project(root, CONFIGURED)
        _, pipeline = started(m)
        assert pipeline.ruleset["digest"] == digest(WITH_REGISTRY)
        assert pipeline.ruleset["error"] is None


class TestAnUnloadableRuleset:
    """DN-D: recorded at start, and the pipeline still starts."""

    def test_an_invalid_ruleset_is_recorded_with_the_reason(self, m):
        write(m.root, RULESET_REL, "version: 9\n")
        _, pipeline = started(m)
        assert pipeline.state == "designing"
        assert pipeline.ruleset["path"] == RULESET_REL
        assert pipeline.ruleset["digest"] is None
        assert "does not validate" in pipeline.ruleset["error"]
        assert "version" in pipeline.ruleset["error"]

    def test_a_missing_ruleset_is_recorded(self, m):
        (m.root / RULESET_REL).unlink()
        _, pipeline = started(m)
        assert pipeline.ruleset["digest"] is None
        assert "does not exist" in pipeline.ruleset["error"]

    def test_a_broken_registry_makes_it_unloadable(self, tmp_path):
        """The pin reads what `load_ruleset` reads, registries included."""
        root = tmp_path / "proj"
        root.mkdir()
        write(root, RULESET_REL, WITH_REGISTRY)
        m = git_project(root, CONFIGURED)
        _, pipeline = started(m)
        assert pipeline.ruleset["digest"] is None
        assert REGISTRY_REL in pipeline.ruleset["error"]

    def test_a_path_outside_the_project_is_recorded_as_given(self, tmp_path):
        outside = "../elsewhere/ruleset.yaml"
        m = git_project(
            tmp_path / "proj",
            {"version": 1, "project": {"name": "demo"}, "guidelines": {"ruleset": outside}},
        )
        _, pipeline = started(m)
        assert pipeline.ruleset["path"] == outside
        assert "names nothing inside the project" in pipeline.ruleset["error"]

    def advance_to_review(self, m) -> tuple[PipelineDomain, str]:
        session = SessionDomain(m).start("land")
        pipelines = PipelineDomain(m)
        pipelines.start(session.id, methodology="chore", task="t", cwd=m.root)
        pipelines.advance(session.id, "reviewing", force=True, reason="test", cwd=m.root)
        return pipelines, session.id

    def verdict(self, pipelines, sid, m, rule):
        verdicts = pipelines.evaluate_gates(sid, target="committed", cwd=m.root)
        return next(v for v in verdicts if v.rule_id == rule)

    def test_it_fails_at_landing_through_ruleset_valid(self, m):
        write(m.root, RULESET_REL, "version: 9\n")
        pipelines, sid = self.advance_to_review(m)
        result = self.verdict(pipelines, sid, m, VALID)
        assert result.blocks and "does not validate" in result.verdict.message

    def test_a_ruleset_fixed_in_the_change_lands(self, m):
        """`ruleset_valid` judges the ruleset the change lands, which is how
        the ruleset lane lands the repair (D10). The pin keeps the record."""
        write(m.root, RULESET_REL, "version: 9\n")
        pipelines, sid = self.advance_to_review(m)
        write(m.root, RULESET_REL, RULESET)
        assert self.verdict(pipelines, sid, m, VALID).verdict.ok
        assert pipelines.current(sid).ruleset["digest"] is None


class TestAChangeMidPipeline:
    def test_editing_the_ruleset_does_not_move_the_pin(self, m):
        sid, pipeline = started(m)
        write(m.root, RULESET_REL, RULESET.replace("title: Testing", "title: Tests"))
        assert PipelineDomain(Manifest.load(m.root)).current(sid).ruleset == pipeline.ruleset

    def test_clearing_the_manifest_key_does_not_drop_the_landing_gates(self, m):
        """AUD-03's shape: one exempt manifest line must not retune the gates."""
        sid, _ = started(m)
        fresh_manifest = set_manifest(m, lambda body: body.pop("guidelines"))
        fresh = PipelineDomain(fresh_manifest)
        assert VALID not in fresh.machine.gate_ids()
        entry = landing_entry(fresh.machine_for(fresh.current(sid)))
        assert VALID in entry and DRIFT in entry

    def test_with_nothing_pinned_the_manifest_decides_as_before(self, tmp_path):
        """No ruleset at start: configuring one later injects as it always has."""
        m = git_project(tmp_path / "bare")
        sid, _ = started(m)
        fresh_manifest = set_manifest(
            m, lambda body: body.update(guidelines={"ruleset": RULESET_REL})
        )
        fresh = PipelineDomain(fresh_manifest)
        assert VALID in landing_entry(fresh.machine_for(fresh.current(sid)))

    def test_a_pinned_methodology_and_ruleset_resolve_together(self, m):
        sid, _ = started(m, methodology="chore")
        fresh = PipelineDomain(set_manifest(m, lambda body: body.pop("guidelines")))
        machine = fresh.machine_for(fresh.current(sid))
        assert machine.id == "chore" and VALID in landing_entry(machine)


class TestOlderRecords:
    def test_a_record_without_the_field_loads(self):
        record = Pipeline.from_dict({"session_id": "s", "methodology": "tdd", "state": "red"})
        assert record.ruleset is None

    def test_a_record_without_the_field_follows_the_manifest(self, m):
        sid, _ = started(m)
        pipelines = PipelineDomain(m)
        raw = pipelines.store.read(pipelines.relpath(sid), {})
        raw["current"].pop("ruleset")
        pipelines.store.write(pipelines.relpath(sid), raw)
        pipeline = pipelines.current(sid)
        assert pipeline.ruleset is None
        assert VALID in landing_entry(pipelines.machine_for(pipeline))
        bare = PipelineDomain(set_manifest(m, lambda body: body.pop("guidelines")))
        assert VALID not in landing_entry(bare.machine_for(bare.current(sid)))


class TestTheMachineLoader:
    def test_guidelines_true_injects_without_a_configured_ruleset(self, make_manifest):
        manifest = make_manifest({"version": 1})
        assert VALID not in load_pipeline_machine(manifest).gate_ids()
        assert VALID in landing_entry(load_pipeline_machine(manifest, guidelines=True))

    def test_guidelines_none_follows_the_manifest(self, make_manifest):
        manifest = make_manifest({"version": 1, "guidelines": {"ruleset": RULESET_REL}})
        assert VALID in landing_entry(load_pipeline_machine(manifest, guidelines=None))


class TestTheCommands:
    @pytest.fixture
    def repo(self, tmp_path, monkeypatch) -> Path:
        """A git repository with no manifest yet, for `init`, run from inside."""
        root = tmp_path / "repo"
        git_project(root)
        (root / MANIFEST_RELPATH).unlink()
        commit_all(root, "no manifest yet")
        monkeypatch.chdir(root)
        return root

    def setup(self, project: Path, ruleset: str | None = RULESET) -> None:
        run("init", "--ecosystem", "python")
        if ruleset is not None:
            write_manifest(project, {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["python"]},
                "guidelines": {"ruleset": RULESET_REL},
            })
            write(project, RULESET_REL, ruleset)
        commit_all(project, "adopt invariant")
        run("session", "start", "x")
        run("session", "isolate", "--execute")

    def test_start_and_status_name_the_pin(self, repo, capsys):
        self.setup(repo)
        capsys.readouterr()
        run("pipeline", "start", "--task", "t")
        assert f"ruleset {RULESET_REL} pinned at {digest(RULESET)}" in out(capsys)
        run("pipeline", "status")
        assert f"ruleset  {RULESET_REL} @ {digest(RULESET)}" in out(capsys)

    def test_status_json_carries_the_pin(self, repo, capsys):
        self.setup(repo)
        run("pipeline", "start", "--task", "t")
        capsys.readouterr()
        run("--json", "pipeline", "status")
        assert payload(capsys)["ruleset"] == {
            "path": RULESET_REL, "digest": digest(RULESET), "error": None,
        }

    def test_an_unloadable_ruleset_is_named_and_the_start_goes_through(self, repo, capsys):
        self.setup(repo, ruleset="version: 9\n")
        capsys.readouterr()
        assert run("pipeline", "start", "--task", "t") == 0
        text = out(capsys)
        assert f"ruleset {RULESET_REL} could not be loaded" in text
        assert VALID in text
        run("pipeline", "status")
        assert "could not be loaded at start" in out(capsys)

    def test_no_ruleset_says_nothing(self, repo, capsys):
        self.setup(repo, ruleset=None)
        capsys.readouterr()
        run("pipeline", "start", "--task", "t")
        run("pipeline", "status")
        assert "ruleset" not in out(capsys)
        run("--json", "pipeline", "status")
        assert payload(capsys)["ruleset"] is None
