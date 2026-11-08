"""ADR-016 increment 3: the incremental rule, `docs.area_documented` (D6).

Armed only by `docs.onboarding: incremental`. It maps each source file of the
session's changed set to its source area (D2) and asks the live assessment of
the session's tree whether that area is named; each unnamed, touched area is
owed. It rides the landing state's entry beside `docs.updated`, injected only
when incremental is chosen, and warns in `loose` and `standard` and blocks in
`strict` (D9, ruling 1).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.core import machines
from invariant.core.machines import DOCS_FIRST_GATES, load_pipeline_machine
from invariant.core.manifest import MANIFEST_RELPATH, Level, Manifest
from invariant.core.rules import RuleContext, evaluate
from invariant.domains.pipeline import PipelineDomain

RULE = "docs.area_documented"

OVERVIEW = "# Toy\n\n## Layers\n\nThe alpha package does things.\n"


def git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


def write_manifest(root: Path, docs: dict | None = None, **extra) -> None:
    body = {
        "version": 1,
        "project": {"name": "toy", "ecosystems": ["python"]},
        "docs": {
            "onboarding": "incremental",
            "assess": {"areas": {"from": "globs", "globs": ["pkg/*"]}},
            **(docs or {}),
        },
        **extra,
    }
    path = root / MANIFEST_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body), encoding="utf-8")


def write(root: Path, rel: str, text: str = "x = 1\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(root: Path, message: str = "change") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)


@pytest.fixture
def toy(tmp_path):
    """A main checkout naming `alpha` but not `beta`, and a session worktree."""
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q", "-b", "master")
    git(main, "config", "user.email", "test@example.invalid")
    git(main, "config", "user.name", "Test")
    for area in ("alpha", "beta"):
        write(main, f"pkg/{area}/mod.py")
    write(main, "docs/architecture/overview.md", OVERVIEW)
    write_manifest(main)
    commit(main, "init")
    return main


def worktree(main: Path, name: str) -> Path:
    wt = main.parent / name
    git(main, "worktree", "add", "-q", str(wt), "-b", f"session/{name}")
    return wt


def ctx(main: Path, wt: Path, *, pipeline: dict | None = None, files=()) -> RuleContext:
    """The context a landing sees: the manifest from the main checkout, the
    session standing in its own worktree."""
    session = {
        "worktree": str(wt),
        "base": "master",
        "branch": f"session/{wt.name}",
        "files": list(files),
    }
    return RuleContext(
        manifest=Manifest.locate(main), cwd=wt, session=session, pipeline=pipeline
    )


def verdict(context: RuleContext):
    (result,) = evaluate([RULE], context)
    return result


def landing_gates(manifest: Manifest, methodology: str = "tdd") -> list[str]:
    machine = load_pipeline_machine(manifest, methodology)
    (landing,) = machine.states_with_role("landing")
    (review,) = machine.states_with_role("review")
    return PipelineDomain.gates_for(None, review, landing, machine)


# -- the proof (the increment's "proven by") ------------------------------


class TestProof:
    @pytest.mark.parametrize(
        ("profile", "level", "blocks"),
        [
            ("loose", Level.WARN, False),
            ("standard", Level.WARN, False),
            ("strict", Level.BLOCK, True),
        ],
    )
    def test_a_session_touching_an_unnamed_area_is_owed_at_the_landing(
        self, toy, profile, level, blocks
    ):
        write_manifest(toy, enforcement={"profile": profile})
        wt = worktree(toy, "one")
        write(wt, "pkg/beta/mod.py", "x = 2\n")
        commit(wt)

        context = ctx(toy, wt)
        gates = landing_gates(context.manifest)
        assert RULE in gates
        (result,) = [r for r in evaluate(gates, context) if r.rule_id == RULE]
        assert result.level is level
        assert not result.verdict.ok and not result.verdict.skipped
        assert result.blocks is blocks
        assert "pkg/beta" in result.verdict.message
        assert "docs/architecture/beta.md" in " ".join(result.verdict.fixes)

    def test_a_second_session_on_the_now_named_area_is_not_asked(self, toy):
        write_manifest(toy, enforcement={"profile": "strict"})
        commit(toy, "strict")
        first = worktree(toy, "one")
        write(first, "pkg/beta/mod.py", "x = 2\n")
        write(first, "docs/architecture/beta.md", "# Beta\n\nThe beta package.\n")
        commit(first)
        assert verdict(ctx(toy, first)).verdict.ok
        git(toy, "merge", "-q", "--ff-only", "session/one")

        second = worktree(toy, "two")
        write(second, "pkg/beta/mod.py", "x = 3\n")
        commit(second)
        result = verdict(ctx(toy, second))
        assert result.verdict.ok and not result.verdict.skipped
        assert not result.blocks
        assert "beta" in result.verdict.message

    def test_a_docs_lane_session_is_na(self, toy):
        wt = worktree(toy, "one")
        write(wt, "docs/architecture/overview.md", OVERVIEW + "\nMore.\n")
        commit(wt)
        result = verdict(ctx(toy, wt, pipeline={"methodology": "docs"}))
        assert result.verdict.skipped
        assert "documentation pipeline" in result.verdict.message

    def test_a_change_with_no_source_is_na(self, toy):
        wt = worktree(toy, "one")
        write(wt, "docs/architecture/overview.md", OVERVIEW + "\nMore.\n")
        write(wt, "tests/test_beta.py", "def test(): pass\n")
        commit(wt)
        result = verdict(ctx(toy, wt))
        assert result.verdict.skipped
        assert "no source" in result.verdict.message


# -- arming and levels ----------------------------------------------------


class TestArming:
    @pytest.mark.parametrize("mode", ["off", "all_at_once"])
    def test_another_onboarding_mode_is_na_and_not_injected(self, toy, mode):
        write_manifest(toy, docs={"onboarding": mode})
        wt = worktree(toy, "one")
        write(wt, "pkg/beta/mod.py", "x = 2\n")
        commit(wt)
        context = ctx(toy, wt)
        result = verdict(context)
        assert result.verdict.skipped
        assert f"docs.onboarding is {mode}" in result.verdict.message
        assert RULE not in landing_gates(context.manifest)

    def test_a_project_setting_nothing_gets_no_new_gate(self, tmp_path):
        m = Manifest.from_raw({}, root=tmp_path, path=tmp_path / "m.yaml", env={})
        assert m.get("docs.onboarding") == "off"
        for methodology in ("tdd", "bugfix", "spec-first", "chore", "docs"):
            assert RULE not in landing_gates(m, methodology), methodology

    @pytest.mark.parametrize("methodology", ["tdd", "bugfix", "spec-first", "chore", "docs"])
    def test_incremental_rides_every_landing_beside_the_update_gate(
        self, toy, methodology
    ):
        machine = load_pipeline_machine(Manifest.locate(toy), methodology)
        (landing,) = machine.states_with_role("landing")
        entry = list(machine.state(landing).entry_gates)
        assert entry.index(RULE) > entry.index("docs.updated")
        assert getattr(machines, "DOCS_ONBOARDING_GATES", None) == {
            "landing": {"entry": [RULE]}
        }
        assert RULE not in DOCS_FIRST_GATES["landing"]["entry"]

    @pytest.mark.parametrize(
        ("profile", "level"),
        [("loose", Level.WARN), ("standard", Level.WARN), ("strict", Level.BLOCK)],
    )
    def test_profile_levels(self, tmp_path, profile, level):
        raw = {"enforcement": {"profile": profile}}
        m = Manifest.from_raw(raw, root=tmp_path, path=tmp_path / "m.yaml", env={})
        assert m.rule_level(RULE) is level
        assert m.source_of(f"enforcement.rules.{RULE}") == f"profile:{profile}"

    @pytest.mark.parametrize(
        ("profile", "override", "blocks"),
        [("standard", "block", True), ("strict", "warn", False)],
    )
    def test_enforcement_rules_override_the_profile(
        self, toy, profile, override, blocks
    ):
        write_manifest(
            toy, enforcement={"profile": profile, "rules": {RULE: override}}
        )
        wt = worktree(toy, "one")
        write(wt, "pkg/beta/mod.py", "x = 2\n")
        commit(wt)
        result = verdict(ctx(toy, wt))
        assert result.level is Level(override)
        assert result.blocks is blocks


# -- what the rule reads --------------------------------------------------


class TestReading:
    def test_a_named_area_is_clean(self, toy):
        wt = worktree(toy, "one")
        write(wt, "pkg/alpha/mod.py", "x = 2\n")
        commit(wt)
        result = verdict(ctx(toy, wt))
        assert result.verdict.ok and not result.verdict.skipped
        assert "pkg/alpha" in result.verdict.message

    def test_a_doc_written_in_the_same_session_settles_it(self, toy):
        """The live assessment of the SESSION's tree, not the main checkout's."""
        wt = worktree(toy, "one")
        write(wt, "pkg/beta/mod.py", "x = 2\n")
        write(wt, "docs/architecture/beta.md", "# Beta\n\nThe beta package.\n")
        commit(wt)
        assert verdict(ctx(toy, wt)).verdict.ok
        assert not (toy / "docs/architecture/beta.md").exists()

    def test_work_in_progress_counts(self, toy):
        wt = worktree(toy, "one")
        write(wt, "pkg/beta/mod.py", "x = 2\n")
        result = verdict(ctx(toy, wt))
        assert not result.verdict.ok
        assert "pkg/beta" in result.verdict.message

    def test_each_owed_area_is_named_once(self, toy):
        write(toy, "pkg/gamma/mod.py")
        commit(toy, "gamma")
        wt = worktree(toy, "one")
        write(wt, "pkg/beta/mod.py", "x = 2\n")
        write(wt, "pkg/beta/other.py")
        write(wt, "pkg/gamma/mod.py", "x = 2\n")
        commit(wt)
        result = verdict(ctx(toy, wt))
        assert "2 touched source area(s)" in result.verdict.message
        assert result.verdict.detail.count("pkg/beta") == 1
        assert "pkg/gamma" in result.verdict.detail
        assert "gamma.md" in " ".join(result.verdict.fixes)

    def test_a_file_in_no_area_is_not_asked(self, toy):
        wt = worktree(toy, "one")
        write(wt, "scripts/tool.py")
        commit(wt)
        result = verdict(ctx(toy, wt))
        assert result.verdict.ok and not result.verdict.skipped
        assert "no touched source file lies in a source area" in result.verdict.message

    def test_an_excluded_area_is_not_asked(self, toy):
        write_manifest(
            toy,
            docs={
                "assess": {
                    "areas": {"from": "globs", "globs": ["pkg/*"], "exclude": ["pkg/beta"]}
                }
            },
        )
        wt = worktree(toy, "one")
        write(wt, "pkg/beta/mod.py", "x = 2\n")
        commit(wt)
        assert verdict(ctx(toy, wt)).verdict.ok

    def test_the_most_specific_declared_area_owns_a_file(self, toy):
        write_manifest(
            toy,
            docs={
                "assess": {
                    "areas": {
                        "from": "declared",
                        "declared": [
                            {"path": "pkg"},
                            {"path": "pkg/beta", "name": "betamax"},
                        ],
                    }
                }
            },
        )
        write(toy, "docs/architecture/overview.md", OVERVIEW + "\nThe pkg root.\n")
        commit(toy, "declared")
        wt = worktree(toy, "one")
        write(wt, "pkg/beta/mod.py", "x = 2\n")
        commit(wt)
        result = verdict(ctx(toy, wt))
        assert not result.verdict.ok
        assert "betamax" in result.verdict.detail
        assert "betamax.md" in " ".join(result.verdict.fixes)

    def test_the_areas_check_turned_off_is_na(self, toy):
        write_manifest(toy, docs={"assess": {"checks": ["sections"]}})
        wt = worktree(toy, "one")
        write(wt, "pkg/beta/mod.py", "x = 2\n")
        commit(wt)
        result = verdict(ctx(toy, wt))
        assert result.verdict.skipped
        assert "areas check is off" in result.verdict.message

    def test_areas_that_cannot_be_derived_fail_rather_than_skip(
        self, toy, monkeypatch
    ):
        from invariant.adapters import get_adapter

        write_manifest(
            toy,
            docs={"assess": {"areas": {"from": "adapter"}}},
            project={"name": "toy", "ecosystems": ["generic"]},
        )
        monkeypatch.setattr(
            type(get_adapter("generic")), "source_areas", lambda self, root, tracked: None
        )
        wt = worktree(toy, "one")
        write(wt, "pkg/beta/mod.py", "x = 2\n")
        commit(wt)
        result = verdict(ctx(toy, wt))
        assert not result.verdict.ok and not result.verdict.skipped
        assert "cannot be derived" in result.verdict.message
        assert "generic" in result.verdict.detail

    def test_an_underivable_ecosystem_does_not_hold_a_mapped_file(
        self, toy, monkeypatch
    ):
        from invariant.adapters import get_adapter

        write_manifest(
            toy,
            docs={"assess": {"areas": {"from": "adapter"}}},
            project={"name": "toy", "ecosystems": ["python", "generic"]},
        )
        monkeypatch.setattr(
            type(get_adapter("generic")), "source_areas", lambda self, root, tracked: None
        )
        monkeypatch.setattr(
            type(get_adapter("python")),
            "source_areas",
            lambda self, root, tracked: [root / "pkg" / "alpha"],
        )
        wt = worktree(toy, "one")
        write(wt, "pkg/alpha/mod.py", "x = 2\n")
        commit(wt)
        assert verdict(ctx(toy, wt)).verdict.ok

    def test_an_unestablished_changed_set_fails(self, toy):
        context = RuleContext(
            manifest=Manifest.locate(toy),
            cwd=toy,
            session={"base": "no-such-ref", "files": []},
        )
        result = verdict(context)
        assert not result.verdict.ok and not result.verdict.skipped
        assert "could not be established" in result.verdict.message

    def test_nothing_changed_is_na(self, toy):
        wt = worktree(toy, "one")
        result = verdict(ctx(toy, wt))
        assert result.verdict.skipped
        assert "nothing changed" in result.verdict.message

    def test_store_docs_are_read_from_the_store(self, toy, tmp_path):
        """With `records.docs: store`, `paths.docs` resolves into the store,
        whatever tree the session stands in (per-leg reading is ADR-014
        increment 4)."""
        store_docs = tmp_path / "store" / "docs" / "architecture"
        write(store_docs.parent.parent, "docs/architecture/overview.md", "beta\n")
        wt = worktree(toy, "one")
        write(wt, "pkg/beta/mod.py", "x = 2\n")
        commit(wt)
        manifest = Manifest.locate(toy)
        manifest.data["paths"]["docs"] = str(store_docs)
        context = RuleContext(manifest=manifest, cwd=wt, session=ctx(toy, wt).session)
        assert verdict(context).verdict.ok

