"""ADR-016 increment 1: the docs assessment, `docs assess` and the doctor row.

D1: three checks (sections, areas, links), each a setting, gaps grouped by
check, deterministic with a digest of the inputs. D2: source areas from the
adapter, from globs or from a declared list. D3: `invariant docs assess` and
the `docs assessment` doctor row through the rule `docs.assessment`. D9: the
`docs.assess` keys, refused by name when unknown, at every config level but
the pointer (ruling 11).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.adapters import get_adapter
from invariant.adapters.base import Adapter
from invariant.cli.main import main
from invariant.core.errors import ManifestError
from invariant.core.home import user_config_path
from invariant.core.manifest import DATA_DIR, MANIFEST_RELPATH, Level, Manifest
from invariant.core.rules import RuleContext, get_rule
from invariant.core.stores import read_pointer
from invariant.domains.docs import Area, assess

STARTER = (
    "# Toy\n\n## Layers\n\nThe alpha package does things.\n\n"
    "## Component responsibilities\n\nSee [the guide](guide.md) and "
    "[gone](missing.md#part), [web](https://example.invalid/x.md), "
    "[anchor](#layers).\n"
)


def git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


def write_manifest(root: Path, body: dict) -> None:
    path = root / MANIFEST_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body), encoding="utf-8")


@pytest.fixture
def toy(tmp_path, monkeypatch):
    """A project documenting half its areas: alpha named, beta not."""
    root = tmp_path / "toy"
    for area in ("alpha", "beta"):
        (root / "pkg" / area).mkdir(parents=True)
        (root / "pkg" / area / "mod.py").write_text("x = 1\n", encoding="utf-8")
    docs = root / "docs" / "architecture"
    docs.mkdir(parents=True)
    (docs / "overview.md").write_text(STARTER, encoding="utf-8")
    (docs / "guide.md").write_text("# Guide\n", encoding="utf-8")
    write_manifest(
        root,
        {
            "version": 1,
            "project": {"name": "toy", "ecosystems": ["python"]},
            "docs": {"assess": {"areas": {"from": "globs", "globs": ["pkg/*"]}}},
        },
    )
    git(root, "init", "-q", "-b", "master")
    monkeypatch.chdir(root)
    return root


def gaps(assessment, check: str) -> list[str]:
    return [g.subject for g in assessment.gaps if g.check == check]


# -- D9: the keys ------------------------------------------------------


class TestKeys:
    def test_defaults_are_the_starters_shape(self, tmp_path):
        m = Manifest.from_raw({}, root=tmp_path, path=tmp_path / "m.yaml", env={})
        assert m.get("docs.assess.checks") == ["sections", "areas", "links"]
        assert m.get("docs.assess.sections") == [
            "Layers",
            "Component responsibilities",
            "Compliance checklist",
        ]
        assert m.get("docs.assess.areas") == {
            "from": "adapter",
            "globs": [],
            "declared": [],
            "exclude": [],
        }
        assert m.rule_level("docs.assessment") is Level.WARN

    @pytest.mark.parametrize("profile", ["loose", "standard", "strict"])
    def test_the_rule_warns_in_every_profile(self, tmp_path, profile):
        raw = {"enforcement": {"profile": profile}}
        m = Manifest.from_raw(raw, root=tmp_path, path=tmp_path / "m.yaml", env={})
        assert m.rule_level("docs.assessment") is Level.WARN
        assert m.source_of("enforcement.rules.docs.assessment") == f"profile:{profile}"

    def test_the_rule_is_never_injected_into_a_pipeline(self):
        for path in (DATA_DIR / "machines").rglob("*.yaml"):
            assert "docs.assessment" not in path.read_text(encoding="utf-8"), path
        from invariant.core import machines

        assert "docs.assessment" not in Path(machines.__file__).read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        ("assess_block", "named"),
        [
            ({"checks": ["sections", "colour"]}, "colour"),
            ({"checks": "sections"}, "docs.assess.checks"),
            ({"checks": ["links", "links"]}, "links"),
            ({"sections": ["Layers", ""]}, "docs.assess.sections"),
            ({"sections": "Layers"}, "docs.assess.sections"),
            ({"depth": 3}, "docs.assess.depth"),
            ({"areas": {"from": "crystal"}}, "crystal"),
            ({"areas": {"colour": 1}}, "docs.assess.areas.colour"),
            ({"areas": {"globs": "pkg/*"}}, "docs.assess.areas.globs"),
            ({"areas": {"exclude": ["../out"]}}, "docs.assess.areas.exclude"),
            ({"areas": {"declared": [{"name": "x"}]}}, "docs.assess.areas.declared"),
            ({"areas": {"declared": ["pkg"]}}, "docs.assess.areas.declared"),
            (
                {"areas": {"declared": [{"path": "pkg", "colour": 1}]}},
                "colour",
            ),
            ({"areas": {"declared": [{"path": "pkg", "name": ""}]}}, "name"),
            ({"areas": "adapter"}, "docs.assess.areas"),
            ("on", "docs.assess"),
        ],
    )
    def test_an_unknown_or_malformed_value_is_refused_naming_it(
        self, tmp_path, assess_block, named
    ):
        raw = {"docs": {"assess": assess_block}}
        with pytest.raises(ManifestError) as exc:
            Manifest.from_raw(raw, root=tmp_path, path=tmp_path / "m.yaml", env={})
        assert named in str(exc.value)

    def test_an_empty_check_list_is_accepted(self, tmp_path):
        raw = {"docs": {"assess": {"checks": []}}}
        m = Manifest.from_raw(raw, root=tmp_path, path=tmp_path / "m.yaml", env={})
        assert m.get("docs.assess.checks") == []

    def test_a_user_default_resolves_with_its_label(self, tmp_path):
        path = user_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"defaults": {"docs": {"assess": {"checks": ["links"]}}}}),
            encoding="utf-8",
        )
        write_manifest(tmp_path, {"version": 1})
        m = Manifest.locate(tmp_path)
        assert m.get("docs.assess.checks") == ["links"]
        assert m.source_of("docs.assess.checks").startswith("user:")

    def test_the_store_level_resolves_and_the_repo_wins(self, tmp_path):
        below = [("store:docs-store", {"docs": {"assess": {"sections": ["Layers"]}}})]
        m = Manifest.from_raw(
            {"docs": {"assess": {"checks": ["sections"]}}},
            root=tmp_path,
            path=tmp_path / ".claude" / "invariant.yaml",
            env={},
            below=below,
        )
        assert m.get("docs.assess.sections") == ["Layers"]
        assert m.source_of("docs.assess.sections") == "store:docs-store"
        assert m.source_of("docs.assess.checks") == "repo:.claude/invariant.yaml"

    def test_the_pointer_refuses_them(self, tmp_path):
        (tmp_path / ".invariant.yaml").write_text(
            yaml.safe_dump({"version": 1, "docs": {"assess": {"checks": []}}}),
            encoding="utf-8",
        )
        with pytest.raises(ManifestError, match="'docs'"):
            read_pointer(tmp_path)


# -- D1: the three checks ----------------------------------------------


class TestAssessment:
    def test_gaps_by_check(self, toy):
        a = assess(Manifest.locate(toy))
        assert a.checks == ("sections", "areas", "links")
        assert gaps(a, "sections") == ["Compliance checklist"]
        assert gaps(a, "areas") == ["pkg/beta"]
        assert gaps(a, "links") == ["overview.md:9"]
        link = next(g for g in a.gaps if g.check == "links")
        assert "missing.md" in link.detail
        assert a.areas == (Area("alpha", "pkg/alpha"), Area("beta", "pkg/beta"))
        assert a.documents == 2

    def test_two_runs_over_an_unchanged_tree_agree_byte_for_byte(self, toy):
        first = assess(Manifest.locate(toy))
        second = assess(Manifest.locate(toy))
        assert first.render() == second.render()
        assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(
            second.to_dict(), sort_keys=True
        )
        assert len(first.digest) == 12

    def test_the_list_loses_an_area_when_its_doc_is_added(self, toy):
        before = assess(Manifest.locate(toy))
        (toy / "docs" / "architecture" / "beta.md").write_text(
            "# The beta area\n\nbeta owns the second half.\n", encoding="utf-8"
        )
        after = assess(Manifest.locate(toy))
        assert gaps(after, "areas") == []
        assert len(after.gaps) == len(before.gaps) - 1
        assert after.digest != before.digest

    def test_an_area_counts_only_as_a_whole_word(self, toy):
        (toy / "docs" / "architecture" / "x.md").write_text(
            "alphabet betamax _beta\n", encoding="utf-8"
        )
        assert gaps(assess(Manifest.locate(toy)), "areas") == ["pkg/beta"]

    def test_headings_match_after_normalisation_across_docs(self, toy):
        (toy / "docs" / "architecture" / "rules.md").write_text(
            "Compliance   CHECKLIST\n===\n", encoding="utf-8"
        )
        assert gaps(assess(Manifest.locate(toy)), "sections") == []

    def test_a_check_set_off_is_absent_and_says_so(self, toy):
        write_manifest(
            toy,
            {"docs": {"assess": {"checks": ["sections"], "areas": {"from": "globs"}}}},
        )
        a = assess(Manifest.locate(toy))
        assert a.checks == ("sections",)
        assert a.off == ("areas", "links")
        assert {g.check for g in a.gaps} == {"sections"}
        text = a.render()
        assert "areas: off" in text
        assert "links: off" in text
        assert set(a.to_dict()["checks"]) == {"sections"}

    def test_every_check_off_leaves_nothing_to_report(self, toy):
        write_manifest(toy, {"docs": {"assess": {"checks": []}}})
        a = assess(Manifest.locate(toy))
        assert a.checks == () and a.gaps == ()
        assert "sections: off" in a.render()

    def test_a_format_with_no_reader_is_not_assessable(self, toy):
        write_manifest(
            toy,
            {
                "docs": {
                    "format": "sphinx",
                    "assess": {"areas": {"from": "globs", "globs": ["pkg/*"]}},
                }
            },
        )
        (toy / "docs" / "architecture" / "index.rst").write_text(
            "beta\n====\n", encoding="utf-8"
        )
        a = assess(Manifest.locate(toy))
        for check in ("sections", "links"):
            (gap,) = [g for g in a.gaps if g.check == check]
            assert gap.subject == "not assessable"
            assert gap.detail == "no reader for sphinx"
        # The areas check still reads every file's text.
        assert gaps(a, "areas") == []

    def test_a_missing_docs_location_owes_every_section(self, toy):
        write_manifest(
            toy,
            {
                "paths": {"docs": "nowhere"},
                "docs": {"assess": {"areas": {"from": "globs", "globs": ["pkg/*"]}}},
            },
        )
        a = assess(Manifest.locate(toy))
        assert a.documents == 0
        assert len(gaps(a, "sections")) == 3
        assert gaps(a, "areas") == ["pkg/alpha", "pkg/beta"]
        assert "absent" in a.render()

    def test_a_relative_link_to_an_existing_file_or_dir_passes(self, toy):
        (toy / "docs" / "architecture" / "overview.md").write_text(
            "[g](guide.md) [up](../architecture/) [abs](/etc/passwd) "
            "[mail](mailto:x@example.invalid) [sp](my%20guide.md)\n",
            encoding="utf-8",
        )
        (toy / "docs" / "architecture" / "my guide.md").write_text("x\n", encoding="utf-8")
        assert gaps(assess(Manifest.locate(toy)), "links") == []

    def test_files_in_hidden_directories_are_not_docs(self, toy):
        hidden = toy / "docs" / "architecture" / ".drafts"
        hidden.mkdir()
        (hidden / "beta.md").write_text("beta\n", encoding="utf-8")
        assert gaps(assess(Manifest.locate(toy)), "areas") == ["pkg/beta"]

    def test_render_and_json_carry_the_summary_and_digest(self, toy):
        a = assess(Manifest.locate(toy))
        text = a.render()
        assert text.splitlines()[0].startswith("docs assessment: docs/architecture")
        assert "3 gap(s): sections 1, areas 1, links 1" in text
        assert f"digest {a.digest}" in text
        data = a.to_dict()
        assert data["gaps"] == 3
        assert data["checks"]["areas"] == [
            {"subject": "pkg/beta", "detail": "beta is named in no doc"}
        ]
        assert data["areas"] == [
            {"name": "alpha", "path": "pkg/alpha"},
            {"name": "beta", "path": "pkg/beta"},
        ]


# -- D2: where areas come from -----------------------------------------


class TestAreaSources:
    def test_globs_match_directories_only(self, toy):
        (toy / "pkg" / "notes.txt").write_text("x\n", encoding="utf-8")
        write_manifest(toy, {"docs": {"assess": {"areas": {"from": "globs", "globs": ["pkg/*"]}}}})
        assert [x.path for x in assess(Manifest.locate(toy)).areas] == [
            "pkg/alpha",
            "pkg/beta",
        ]

    def test_declared_areas_may_name_themselves(self, toy):
        write_manifest(
            toy,
            {
                "docs": {
                    "assess": {
                        "areas": {
                            "from": "declared",
                            "declared": [
                                {"path": "pkg/beta", "name": "Toy"},
                                {"path": "pkg/alpha"},
                            ],
                        }
                    }
                }
            },
        )
        a = assess(Manifest.locate(toy))
        assert a.areas == (Area("alpha", "pkg/alpha"), Area("Toy", "pkg/beta"))
        assert gaps(a, "areas") == []

    def test_exclude_holds_areas_out(self, toy):
        write_manifest(
            toy,
            {
                "docs": {
                    "assess": {
                        "areas": {"from": "globs", "globs": ["pkg/*"], "exclude": ["pkg/b*"]}
                    }
                }
            },
        )
        a = assess(Manifest.locate(toy))
        assert [x.path for x in a.areas] == ["pkg/alpha"]
        assert gaps(a, "areas") == []

    def test_the_adapter_names_the_python_subpackages(self, toy):
        for area in ("alpha", "beta"):
            (toy / "pkg" / area / "__init__.py").write_text("", encoding="utf-8")
        (toy / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (toy / "pkg" / "data").mkdir()
        write_manifest(toy, {"project": {"ecosystems": ["python"]}})
        a = assess(Manifest.locate(toy))
        assert [x.path for x in a.areas] == ["pkg/alpha", "pkg/beta"]

    def test_an_adapter_that_cannot_tell_is_a_gap(self, toy):
        import shutil

        shutil.rmtree(toy / ".git")
        write_manifest(toy, {"project": {"ecosystems": ["generic"]}})
        a = assess(Manifest.locate(toy))
        (gap,) = [g for g in a.gaps if g.check == "areas"]
        assert gap.subject == "generic"
        assert "cannot be derived" in gap.detail
        assert a.areas == ()

    def test_the_adapter_falls_back_to_tracked_top_level_dirs(self, toy):
        write_manifest(toy, {"project": {"ecosystems": ["generic"]}})
        git(toy, "add", "-A")
        a = assess(Manifest.locate(toy))
        assert [x.path for x in a.areas] == ["docs", "pkg"]


class TestAdapterHook:
    def test_base_reads_one_level_below_the_measured_scope(self, tmp_path):
        class Scoped(Adapter):
            def measured_scope(self, root):
                return [root / "src"]

        for name in ("one", "two", ".hidden", "empty"):
            (tmp_path / "src" / name).mkdir(parents=True)
        for name in ("one", "two", ".hidden"):
            (tmp_path / "src" / name / "f.txt").write_text("x", encoding="utf-8")
        assert Scoped().source_areas(tmp_path) == [
            tmp_path / "src" / "one",
            tmp_path / "src" / "two",
        ]

    def test_base_without_scope_or_tracked_files_cannot_tell(self, tmp_path):
        assert Adapter().source_areas(tmp_path) is None

    def test_base_uses_tracked_files_it_accepts(self, tmp_path):
        tracked = [Path("a/x.py"), Path("b/y.txt"), Path(".claude/z.py"), Path("top.py")]
        assert get_adapter("python").source_areas(tmp_path, tracked) == [tmp_path / "a"]

    def test_python_reads_the_measured_package(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[tool.coverage.run]\nsource = ["src/lib"]\n', encoding="utf-8"
        )
        for sub in ("core", "cli"):
            (tmp_path / "src" / "lib" / sub).mkdir(parents=True)
            (tmp_path / "src" / "lib" / sub / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "src" / "lib" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "src" / "lib" / "data").mkdir()
        assert get_adapter("python").source_areas(tmp_path) == [
            tmp_path / "src" / "lib" / "cli",
            tmp_path / "src" / "lib" / "core",
        ]

    def test_python_without_subpackages_falls_back_to_the_base(self, tmp_path):
        assert get_adapter("python").source_areas(tmp_path) is None


# -- D3: the rule, the command and the doctor row ----------------------


def run(*argv: str) -> int:
    return main(list(argv))


class TestRuleAndCommand:
    def test_the_rule_fails_on_gaps_and_names_them(self, toy):
        verdict = get_rule("docs.assessment").fn(
            RuleContext(manifest=Manifest.locate(toy), cwd=toy)
        )
        assert not verdict.ok
        assert "3 gap(s)" in verdict.message
        assert "areas: pkg/beta" in verdict.detail
        assert verdict.fixes == ("invariant docs assess",)

    def test_the_rule_passes_clean_docs(self, toy):
        write_manifest(toy, {"docs": {"assess": {"checks": ["areas"], "areas": {"from": "declared", "declared": [{"path": "pkg/alpha"}]}}}})
        verdict = get_rule("docs.assessment").fn(
            RuleContext(manifest=Manifest.locate(toy), cwd=toy)
        )
        assert verdict.ok and not verdict.skipped

    def test_the_rule_is_na_with_every_check_off(self, toy):
        write_manifest(toy, {"docs": {"assess": {"checks": []}}})
        verdict = get_rule("docs.assessment").fn(
            RuleContext(manifest=Manifest.locate(toy), cwd=toy)
        )
        assert verdict.skipped

    def test_docs_assess_prints_the_gaps_and_exits_zero(self, toy, capsys):
        assert run("docs", "assess") == 0
        text = capsys.readouterr().out
        assert "pkg/beta" in text and "Compliance checklist" in text
        assert run("docs", "assess", "--json") == 0
        data = json.loads(capsys.readouterr().out)
        assert data["gaps"] == 3

    def test_docs_assess_exits_two_on_a_configuration_it_cannot_load(self, toy, capsys):
        write_manifest(toy, {"docs": {"assess": {"checks": ["colour"]}}})
        assert run("docs", "assess") == 2
        assert "colour" in capsys.readouterr().err

    def doctor_rows(self, capsys) -> tuple[int, dict]:
        code = run("doctor", "--json")
        rows = {c["check"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
        return code, rows

    def test_doctor_row_warns_and_never_sets_the_exit_code(self, toy, capsys):
        _code, rows = self.doctor_rows(capsys)
        row = rows["docs assessment"]
        assert row["ok"] is True and row["warn"] is True
        assert "pkg/beta" in row["detail"]
        write_manifest(
            toy,
            {
                "docs": {"assess": {"checks": []}},
            },
        )
        _code_off, rows_off = self.doctor_rows(capsys)
        assert rows_off["docs assessment"]["warn"] is False
        assert _code == _code_off

    def test_block_makes_the_row_fail(self, toy, capsys):
        write_manifest(
            toy,
            {
                "docs": {"assess": {"areas": {"from": "globs", "globs": ["pkg/*"]}}},
                "enforcement": {"rules": {"docs.assessment": "block"}},
            },
        )
        code, rows = self.doctor_rows(capsys)
        assert code == 1
        assert rows["docs assessment"]["ok"] is False

    def test_off_removes_the_row(self, toy, capsys):
        write_manifest(
            toy, {"enforcement": {"rules": {"docs.assessment": "off"}}}
        )
        _code, rows = self.doctor_rows(capsys)
        assert "docs assessment" not in rows
        assert "architecture docs exist" in rows
