"""ADR-016 increment 4: adopting existing documentation, in place or by copy.

D7: `invariant docs adopt <source> --mode in_place|copy [--dry-run]` writes
the configuration. Markdown is adopted by copy only, into a migrating project
(ADR-018 D11), so the in-place proofs adopt a Sphinx tree. In place points `paths.docs` (and `records.docs`) at the
source, which must lie under the repo root or the store root, and sets
`docs.format` from the detected format, refusing one with no reader (ruling
5). Copy copies into the resolved docs location and never writes the
original; any format copies, and one with no reader is "not assessable"
(ruling 10). D8: the receipt, `.invariant-adoption.yaml`, the re-copy
refusal, and drift named by `doctor` and `docs assess` (ruling 3). D9: the
`docs.adopt` keys at every level but the pointer, `docs.adopt.source` refused
at the user level (ruling 11).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from invariant.cli.main import main
from invariant.core.errors import ManifestError, UsageError
from invariant.core.home import user_config_path
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.core.rules import RuleContext, get_rule
from invariant.core.stores import POINTER, read_pointer, write_repo_settings
from invariant.domains.docs import (
    RECEIPT,
    adoption_drift,
    assess,
    copy_adoption,
    plan_adoption,
)

DOC = (
    "# Toy\n\n## Layers\n\nThe alpha package does things.\n\n"
    "## Component responsibilities\n\nalpha owns the work.\n\n"
    "## Compliance checklist\n\n- none\n"
)
NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
#: DOC in rST: Markdown is adopted by copy only (ADR-018 D11), so in place
#: adopts a Sphinx tree.
RST_DOC = (
    "Toy\n===\n\nLayers\n------\n\nThe alpha package does things.\n\n"
    "Component responsibilities\n--------------------------\n\nalpha owns the work.\n\n"
    "Compliance checklist\n--------------------\n\n- none\n"
)


def rst_handbook(root: Path, name: str = "guide") -> Path:
    """A Sphinx-format handbook to adopt in place, two documents deep."""
    guide = root / name
    (guide / "deep").mkdir(parents=True)
    (guide / "overview.rst").write_text(RST_DOC, encoding="utf-8")
    (guide / "deep" / "notes.rst").write_text("Notes\n=====\n", encoding="utf-8")
    return guide


def git(root: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", *argv], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def tree(root: Path) -> dict[str, str]:
    """Every file under ``root`` and the SHA-256 of its bytes."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def run(capsys, *argv) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def run_json(capsys, *argv) -> tuple[int, dict]:
    capsys.readouterr()
    code = main([*argv, "--json"])
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else {}


def write_manifest(root: Path, body: dict) -> None:
    path = root / MANIFEST_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body), encoding="utf-8")


def read_manifest(root: Path) -> dict:
    return yaml.safe_load((root / MANIFEST_RELPATH).read_text(encoding="utf-8"))


BASE = {
    "version": 1,
    "project": {"name": "toy", "ecosystems": ["generic"]},
    "docs": {
        "assess": {"areas": {"from": "declared", "declared": [{"path": "alpha"}]}}
    },
}


@pytest.fixture
def toy(tmp_path, monkeypatch):
    """An in-repo project with one area, alpha, and a handbook to adopt."""
    root = tmp_path / "toy"
    (root / "alpha").mkdir(parents=True)
    (root / "alpha" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    handbook = root / "handbook"
    (handbook / "deep").mkdir(parents=True)
    (handbook / "overview.md").write_text(DOC, encoding="utf-8")
    (handbook / "deep" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    (handbook / ".hidden").write_text("not a doc\n", encoding="utf-8")
    write_manifest(root, BASE)
    git(root, "init", "-q", "-b", "main")
    monkeypatch.chdir(root)
    return root


def doctor_rows(capsys) -> dict:
    _code, data = run_json(capsys, "doctor")
    return {row["check"]: row for row in data["checks"]}


def load(tmp_path, raw: dict, **kw) -> Manifest:
    return Manifest.from_raw(raw, root=tmp_path, path=tmp_path / "m.yaml", env={}, **kw)


# -- D9: the keys ------------------------------------------------------


class TestAdoptKeys:
    def test_the_defaults_adopt_nothing(self, tmp_path):
        m = load(tmp_path, {})
        assert m.get("docs.adopt") == {"mode": None, "source": None}

    def test_copy_loads_with_a_relative_or_absolute_source(self, tmp_path):
        rel = load(tmp_path, {"docs": {"adopt": {"mode": "copy", "source": "handbook"}}})
        assert rel.get("docs.adopt.mode") == "copy"
        outside = str(tmp_path.parent / "elsewhere")
        absolute = load(tmp_path, {"docs": {"adopt": {"mode": "copy", "source": outside}}})
        assert absolute.get("docs.adopt.source") == outside

    def test_in_place_loads_when_paths_docs_is_the_source(self, tmp_path):
        raw = {
            "paths": {"docs": "handbook"},
            "docs": {"adopt": {"mode": "in_place", "source": "handbook/"}},
        }
        assert load(tmp_path, raw).get("docs.adopt.mode") == "in_place"

    def test_explicit_nulls_load(self, tmp_path):
        m = load(tmp_path, {"docs": {"adopt": {"mode": None, "source": None}}})
        assert m.get("docs.adopt.mode") is None

    @pytest.mark.parametrize(
        ("adopt", "named"),
        [
            ({"mode": "mirror", "source": "h"}, "mirror"),
            ({"mode": True, "source": "h"}, "docs.adopt.mode"),
            ({"mode": "copy", "source": "h", "colour": 1}, "docs.adopt.colour"),
            ({"mode": "copy"}, "docs.adopt.source"),
            ({"source": "handbook"}, "docs.adopt.mode"),
            ({"mode": "copy", "source": ""}, "docs.adopt.source"),
            ({"mode": "copy", "source": 3}, "docs.adopt.source"),
            ({"mode": "copy", "source": "a\nb"}, "docs.adopt.source"),
            ({"mode": "copy", "source": "../up"}, "docs.adopt.source"),
            ({"mode": "in_place", "source": "/abs/handbook"}, "absolute"),
            ({"mode": "in_place", "source": "../handbook"}, "docs.adopt.source"),
            ({"mode": "in_place", "source": "elsewhere"}, "paths.docs"),
        ],
    )
    def test_a_bad_adopt_block_is_refused_naming_it(self, tmp_path, adopt, named):
        with pytest.raises(ManifestError) as exc:
            load(tmp_path, {"docs": {"adopt": adopt}})
        assert named in str(exc.value)

    def test_adopt_must_be_a_mapping(self, tmp_path):
        with pytest.raises(ManifestError, match="docs.adopt must be a mapping"):
            load(tmp_path, {"docs": {"adopt": "copy"}})

    def test_the_mode_resolves_from_the_user_level(self, toy):
        user_config_path().parent.mkdir(parents=True, exist_ok=True)
        user_config_path().write_text(
            "version: 1\ndefaults:\n  docs:\n    adopt:\n      mode: copy\n",
            encoding="utf-8",
        )
        write_manifest(toy, {**BASE, "docs": {"adopt": {"source": "handbook"}}})
        m = Manifest.locate(toy)
        assert m.get("docs.adopt.mode") == "copy"
        assert m.source_of("docs.adopt.mode").startswith("user:")
        assert m.source_of("docs.adopt.source") == f"repo:{MANIFEST_RELPATH}"

    def test_the_source_is_refused_at_the_user_level(self, toy):
        user_config_path().parent.mkdir(parents=True, exist_ok=True)
        user_config_path().write_text(
            "version: 1\ndefaults:\n  docs:\n    adopt:\n"
            "      mode: copy\n      source: handbook\n",
            encoding="utf-8",
        )
        with pytest.raises(ManifestError) as exc:
            Manifest.locate(toy)
        assert "docs.adopt.source" in str(exc.value)
        assert "user level" in str(exc.value)

    def test_the_store_level_resolves_with_its_label(self, tmp_path):
        below = [("store:s#k", {"docs": {"adopt": {"mode": "copy", "source": "h"}}})]
        m = load(tmp_path, {}, below=below)
        assert m.source_of("docs.adopt.source") == "store:s#k"

    def test_the_pointer_refuses_them(self, tmp_path):
        (tmp_path / POINTER).write_text(
            "version: 1\ndocs:\n  adopt:\n    mode: copy\n", encoding="utf-8"
        )
        with pytest.raises(ManifestError, match="'docs'"):
            read_pointer(tmp_path)


# -- write_repo_settings: the in-repo manifest write -------------------


class TestWriteRepoSettings:
    def test_a_file_without_comments_is_rewritten_merged(self, tmp_path):
        path = tmp_path / "m.yaml"
        path.write_text("version: 1\ndocs:\n  onboarding: incremental\n", encoding="utf-8")
        result = write_repo_settings(path, {"docs": {"adopt": {"mode": "copy"}}})
        assert result.written
        assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
            "version": 1,
            "docs": {"onboarding": "incremental", "adopt": {"mode": "copy"}},
        }

    def test_a_commented_file_gains_a_block_it_does_not_hold(self, tmp_path):
        path = tmp_path / "m.yaml"
        text = "version: 1\n# a comment the operator wrote\n"
        path.write_text(text, encoding="utf-8")
        result = write_repo_settings(path, {"docs": {"format": "markdown"}})
        assert result.written
        written = path.read_text(encoding="utf-8")
        assert written.startswith(text)
        assert yaml.safe_load(written) == {"version": 1, "docs": {"format": "markdown"}}

    def test_a_commented_file_already_holding_the_key_is_not_written(self, tmp_path):
        path = tmp_path / "m.yaml"
        text = "version: 1\n# keep me\ndocs:\n  onboarding: incremental\n"
        path.write_text(text, encoding="utf-8")
        result = write_repo_settings(path, {"docs": {"adopt": {"mode": "copy"}}})
        assert not result.written
        assert path.read_text(encoding="utf-8") == text
        assert "comment" in result.reason
        assert yaml.safe_load(result.by_hand) == {"docs": {"adopt": {"mode": "copy"}}}

    def test_a_manifest_that_is_not_a_mapping_is_refused(self, tmp_path):
        path = tmp_path / "m.yaml"
        path.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(ManifestError, match="must contain a mapping"):
            write_repo_settings(path, {"docs": {"format": "markdown"}})
        assert path.read_text(encoding="utf-8") == "- a\n- b\n"

    def test_a_commented_flow_mapping_is_not_appended_to(self, tmp_path):
        path = tmp_path / "m.yaml"
        text = "{version: 1, project: {name: t}}  # flow style\n"
        path.write_text(text, encoding="utf-8")
        result = write_repo_settings(path, {"docs": {"format": "markdown"}})
        assert not result.written
        assert path.read_text(encoding="utf-8") == text
        assert "read back" in result.reason

    def test_a_file_without_a_trailing_newline_still_appends_cleanly(self, tmp_path):
        path = tmp_path / "m.yaml"
        path.write_text("version: 1 # inline", encoding="utf-8")
        assert write_repo_settings(path, {"paths": {"docs": "h"}}).written
        assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
            "version": 1,
            "paths": {"docs": "h"},
        }


# -- D7, D8: copy ------------------------------------------------------


class TestCopy:
    def test_copy_leaves_the_original_untouched_and_the_gate_reads_the_copy(
        self, toy, capsys
    ):
        before = tree(toy / "handbook")
        code, out = run(capsys, "docs", "adopt", "handbook", "--mode", "copy")
        assert code == 0, out
        assert tree(toy / "handbook") == before
        copy = toy / "docs" / "architecture"
        assert (copy / "overview.md").read_text(encoding="utf-8") == DOC
        assert (copy / "deep" / "notes.md").is_file()
        assert not (copy / ".hidden").exists()
        m = Manifest.locate(toy)
        assert m.get("docs.adopt") == {"mode": "copy", "source": "handbook"}
        assert m.get("docs.format") == "markdown"
        assert m.path_for("docs") == copy.resolve()
        assessment = assess(m)
        assert assessment.documents == 2
        assert assessment.location == "docs/architecture"

        def gate(read: Path):
            pipeline = {"evidence": {"docs_read": [str(read)]}}
            return get_rule("docs.read_architecture").fn(
                RuleContext(manifest=m, cwd=toy, pipeline=pipeline)
            )

        assert gate(copy / "overview.md").ok
        assert not gate(toy / "handbook" / "overview.md").ok

    def test_the_receipt_records_source_time_and_both_hashes(self, toy):
        m = Manifest.locate(toy)
        plan = plan_adoption(m, "handbook", "copy", cwd=toy)
        copy_adoption(plan, now=NOW)
        receipt = yaml.safe_load(
            (toy / "docs" / "architecture" / RECEIPT).read_text(encoding="utf-8")
        )
        digest = hashlib.sha256(DOC.encode("utf-8")).hexdigest()
        assert receipt["version"] == 1
        assert receipt["source"] == "handbook"
        assert receipt["copied_at"] == "2026-11-08T12:00:00Z"
        assert receipt["files"]["overview.md"] == {"original": digest, "copy": digest}
        assert sorted(receipt["files"]) == ["deep/notes.md", "overview.md"]

    def test_the_receipt_is_held_out_of_the_assessment(self, toy, capsys):
        assert run(capsys, "docs", "adopt", "handbook", "--mode", "copy")[0] == 0
        first = assess(Manifest.locate(toy))
        receipt = toy / "docs" / "architecture" / RECEIPT
        receipt.write_text(receipt.read_text(encoding="utf-8") + "# beta\n", encoding="utf-8")
        again = assess(Manifest.locate(toy))
        assert again.documents == first.documents
        assert again.digest == first.digest

    def test_editing_the_original_is_named_by_doctor_and_docs_assess(self, toy, capsys):
        assert run(capsys, "docs", "adopt", "handbook", "--mode", "copy")[0] == 0
        copy = toy / "docs" / "architecture"
        copied = tree(copy)
        assert doctor_rows(capsys)["docs adoption"]["warn"] is False
        (toy / "handbook" / "overview.md").write_text(DOC + "\nMore.\n", encoding="utf-8")
        (toy / "handbook" / "added.md").write_text("# Added\n", encoding="utf-8")
        (toy / "handbook" / "deep" / "notes.md").unlink()

        row = doctor_rows(capsys)["docs adoption"]
        assert row["ok"] is True and row["warn"] is True
        for named in ("overview.md", "added.md", "deep/notes.md"):
            assert named in row["detail"]

        code, text = run(capsys, "docs", "assess")
        assert code == 0
        assert "adoption drift" in text
        assert "changed: overview.md" in text
        assert "added: added.md" in text
        assert "removed: deep/notes.md" in text
        _code, data = run_json(capsys, "docs", "assess")
        assert data["adoption"]["changed"] == ["overview.md"]
        assert data["adoption"]["added"] == ["added.md"]
        assert data["adoption"]["removed"] == ["deep/notes.md"]
        assert tree(copy) == copied

    def test_an_unchanged_original_reports_no_drift(self, toy, capsys):
        assert run(capsys, "docs", "adopt", "handbook", "--mode", "copy")[0] == 0
        row = doctor_rows(capsys)["docs adoption"]
        assert row["ok"] is True and row["warn"] is False
        assert "unchanged" in row["detail"]
        code, text = run(capsys, "docs", "assess")
        assert code == 0
        assert "adoption drift: none" in text

    def test_recopy_refuses_an_edited_copy_unless_forced(self, toy, capsys):
        assert run(capsys, "docs", "adopt", "handbook", "--mode", "copy")[0] == 0
        copy = toy / "docs" / "architecture" / "overview.md"
        copy.write_text("# Mine now\n", encoding="utf-8")
        (toy / "handbook" / "overview.md").write_text(DOC + "\nNew.\n", encoding="utf-8")
        code, out = run(capsys, "docs", "adopt", "handbook", "--mode", "copy")
        assert code == 2
        assert "overview.md" in out and "--force" in out
        assert copy.read_text(encoding="utf-8") == "# Mine now\n"
        code, out = run(capsys, "docs", "adopt", "handbook", "--mode", "copy", "--force")
        assert code == 0, out
        assert copy.read_text(encoding="utf-8") == DOC + "\nNew.\n"
        assert adoption_drift(Manifest.locate(toy)).clean

    def test_recopy_of_an_unedited_copy_needs_no_force(self, toy, capsys):
        assert run(capsys, "docs", "adopt", "handbook", "--mode", "copy")[0] == 0
        (toy / "handbook" / "overview.md").write_text(DOC + "\nNew.\n", encoding="utf-8")
        (toy / "handbook" / "deep" / "notes.md").unlink()
        code, data = run_json(capsys, "docs", "adopt", "handbook", "--mode", "copy")
        assert code == 0
        assert data["kept"] == ["deep/notes.md"]
        assert (toy / "docs" / "architecture" / "deep" / "notes.md").is_file()
        assert adoption_drift(Manifest.locate(toy)).clean

    def test_a_first_copy_over_different_files_is_refused(self, toy, capsys):
        docs = toy / "docs" / "architecture"
        docs.mkdir(parents=True)
        (docs / "overview.md").write_text("# Ours\n", encoding="utf-8")
        (docs / "deep").mkdir()
        (docs / "deep" / "notes.md").write_text("# Notes\n", encoding="utf-8")
        code, out = run(capsys, "docs", "adopt", "handbook", "--mode", "copy")
        assert code == 2
        assert "overview.md" in out and "deep/notes.md" not in out
        assert (docs / "overview.md").read_text(encoding="utf-8") == "# Ours\n"

    def test_dry_run_writes_nothing(self, toy, capsys):
        manifest_before = (toy / MANIFEST_RELPATH).read_text(encoding="utf-8")
        code, data = run_json(
            capsys, "docs", "adopt", "handbook", "--mode", "copy", "--dry-run"
        )
        assert code == 0
        assert data["dry_run"] is True
        assert data["files"] == ["deep/notes.md", "overview.md"]
        assert not (toy / "docs" / "architecture").exists()
        assert (toy / MANIFEST_RELPATH).read_text(encoding="utf-8") == manifest_before
        code, text = run(capsys, "docs", "adopt", "handbook", "--mode", "copy", "--dry-run")
        assert "would" in text

    def test_an_unreadable_format_copies_and_says_not_assessable(self, toy, capsys):
        manual = toy / "manual"
        manual.mkdir()
        (manual / "index.adoc").write_text("= Toy\n\nalpha\n", encoding="utf-8")
        code, out = run(capsys, "docs", "adopt", "manual", "--mode", "copy")
        assert code == 0, out
        assert "not assessable" in out and "asciidoc" in out
        m = Manifest.locate(toy)
        assert m.get("docs.format") == "asciidoc"
        assessment = assess(m)
        assert [g.subject for g in assessment.by_check("sections")] == ["not assessable"]
        assert [g.subject for g in assessment.by_check("links")] == ["not assessable"]
        assert assessment.by_check("areas") == []

    def test_a_source_outside_both_roots_is_recorded_as_given(self, toy, tmp_path, capsys):
        outside = tmp_path / "shared-handbook"
        outside.mkdir()
        (outside / "overview.md").write_text(DOC, encoding="utf-8")
        assert run(capsys, "docs", "adopt", str(outside), "--mode", "copy")[0] == 0
        m = Manifest.locate(toy)
        assert m.get("docs.adopt.source") == str(outside.resolve())
        assert adoption_drift(m).reachable
        (outside / "overview.md").unlink()
        outside.rmdir()
        drift = adoption_drift(Manifest.locate(toy))
        assert not drift.reachable and not drift.clean
        row = doctor_rows(capsys)["docs adoption"]
        assert row["warn"] is True
        assert "original not reachable" in row["detail"]
        assert "adoption drift: original not reachable" in run(capsys, "docs", "assess")[1]

    def test_a_missing_receipt_is_named(self, toy, capsys):
        assert run(capsys, "docs", "adopt", "handbook", "--mode", "copy")[0] == 0
        (toy / "docs" / "architecture" / RECEIPT).unlink()
        row = doctor_rows(capsys)["docs adoption"]
        assert row["warn"] is True and RECEIPT in row["detail"]
        assert f"adoption drift: no receipt at docs/architecture/{RECEIPT}" in run(
            capsys, "docs", "assess"
        )[1]

    def test_an_unreadable_receipt_is_named(self, toy, capsys):
        assert run(capsys, "docs", "adopt", "handbook", "--mode", "copy")[0] == 0
        (toy / "docs" / "architecture" / RECEIPT).write_text("- [", encoding="utf-8")
        drift = adoption_drift(Manifest.locate(toy))
        assert not drift.clean and "cannot be read" in drift.problem
        code, out = run(capsys, "docs", "adopt", "handbook", "--mode", "copy")
        assert code == 2 and "cannot be read" in out

    @pytest.mark.parametrize(
        ("body", "problem"),
        [
            ("[1, 2]\n", "mapping"),
            ("version: 2\nsource: h\ncopied_at: t\nfiles: {}\n", "version"),
            ("version: 1\nsource: h\ncopied_at: t\nfiles: [a]\n", "files"),
            (
                "version: 1\nsource: h\ncopied_at: t\nfiles:\n  a.md: {original: x}\n",
                "a.md",
            ),
            ("version: 1\nsource: ''\ncopied_at: t\nfiles: {}\n", "source"),
        ],
    )
    def test_a_malformed_receipt_names_what_is_wrong(self, toy, capsys, body, problem):
        assert run(capsys, "docs", "adopt", "handbook", "--mode", "copy")[0] == 0
        (toy / "docs" / "architecture" / RECEIPT).write_text(body, encoding="utf-8")
        drift = adoption_drift(Manifest.locate(toy))
        assert problem in drift.problem

    @pytest.mark.parametrize(
        ("source", "said"),
        [
            ("nowhere", "not a directory"),
            ("alpha/mod.py", "not a directory"),
            ("empty", "holds no file"),
            ("docs", "inside"),
            ("docs/architecture/sub", "inside"),
        ],
    )
    def test_a_source_it_cannot_copy_is_refused(self, toy, capsys, source, said):
        (toy / "empty").mkdir()
        (toy / "docs" / "architecture" / "sub").mkdir(parents=True)
        (toy / "docs" / "architecture" / "sub" / "x.md").write_text("# X\n", encoding="utf-8")
        code, out = run(capsys, "docs", "adopt", source, "--mode", "copy")
        assert code == 2
        assert said in out

    def test_a_project_setting_no_adopt_key_behaves_as_today(self, toy, capsys):
        assert "docs adoption" not in doctor_rows(capsys)
        _code, data = run_json(capsys, "docs", "assess")
        assert "adoption" not in data
        _code, text = run(capsys, "docs", "assess")
        assert "adoption" not in text
        assert adoption_drift(Manifest.locate(toy)) is None


# -- D7: in place ------------------------------------------------------


class TestInPlace:
    def test_in_place_points_the_gate_and_agents_at_the_original(self, toy, capsys):
        rst_handbook(toy)
        before = tree(toy / "guide")
        code, out = run(capsys, "docs", "adopt", "guide", "--mode", "in_place")
        assert code == 0, out
        assert tree(toy / "guide") == before
        assert not (toy / "guide" / RECEIPT).exists()
        written = read_manifest(toy)
        assert written["paths"] == {"docs": "guide"}
        assert written["docs"]["adopt"] == {"mode": "in_place", "source": "guide"}
        assert written["docs"]["format"] == "sphinx"
        assert "records" not in written
        m = Manifest.locate(toy)
        assert m.path_for("docs") == (toy / "guide").resolve()
        pipeline = {"evidence": {"docs_read": [str(toy / "guide" / "overview.rst")]}}
        verdict = get_rule("docs.read_architecture").fn(
            RuleContext(manifest=m, cwd=toy, pipeline=pipeline)
        )
        assert verdict.ok
        # An agent updating "the architecture docs" writes where paths.docs
        # resolves, which is now the original.
        (m.path_for("docs") / "services.rst").write_text("Services\n========\n", encoding="utf-8")
        assert (toy / "guide" / "services.rst").is_file()
        assert assess(Manifest.locate(toy)).documents == 3
        assert adoption_drift(m) is None
        assert "docs adoption" not in doctor_rows(capsys)

    def test_a_source_outside_the_roots_is_refused_offering_copy(self, toy, tmp_path, capsys):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (outside / "a.md").write_text("# A\n", encoding="utf-8")
        code, out = run(capsys, "docs", "adopt", str(outside), "--mode", "in_place")
        assert code == 2
        assert "absolute" in out and "--mode copy" in out

    def test_the_root_itself_is_refused(self, toy, capsys):
        code, out = run(capsys, "docs", "adopt", ".", "--mode", "in_place")
        assert code == 2
        assert "root itself" in out

    def test_an_unreadable_format_is_refused_in_place_and_accepted_by_copy(
        self, toy, capsys
    ):
        manual = toy / "manual"
        manual.mkdir()
        (manual / "index.adoc").write_text("= Toy\n", encoding="utf-8")
        before = (toy / MANIFEST_RELPATH).read_text(encoding="utf-8")
        code, out = run(capsys, "docs", "adopt", "manual", "--mode", "in_place")
        assert code == 2
        assert "asciidoc" in out and "no reader" in out and "--mode copy" in out
        assert (toy / MANIFEST_RELPATH).read_text(encoding="utf-8") == before
        code, out = run(capsys, "docs", "adopt", "manual", "--mode", "copy")
        assert code == 0
        assert "not assessable" in out

    def test_a_commented_manifest_holding_docs_gets_the_block_by_hand(self, toy, capsys):
        rst_handbook(toy)
        text = "version: 1\n# ours\ndocs:\n  onboarding: incremental\n"
        (toy / MANIFEST_RELPATH).write_text(text, encoding="utf-8")
        code, data = run_json(capsys, "docs", "adopt", "guide", "--mode", "in_place")
        assert code == 0
        assert (toy / MANIFEST_RELPATH).read_text(encoding="utf-8") == text
        held = data["settings_by_hand"]
        assert held["path"].endswith("invariant.yaml")
        assert yaml.safe_load(held["yaml"])["paths"] == {"docs": "guide"}
        code, out = run(capsys, "docs", "adopt", "guide", "--mode", "in_place")
        assert "by hand" in out

    def test_a_dry_run_says_what_it_would_write(self, toy, capsys):
        rst_handbook(toy)
        code, out = run(capsys, "docs", "adopt", "guide", "--mode", "in_place", "--dry-run")
        assert code == 0
        assert "paths.docs: guide" in out
        assert "paths" not in read_manifest(toy)

    def test_markdown_is_refused_in_place_naming_copy(self, toy, capsys):
        """ADR-018 D11: Markdown is adopted by copy only, into a migrating project."""
        before = (toy / MANIFEST_RELPATH).read_text(encoding="utf-8")
        code, out = run(capsys, "docs", "adopt", "handbook", "--mode", "in_place")
        assert code == 2
        assert "Markdown" in out and "--mode copy" in out and "ADR-018 D11" in out
        assert (toy / MANIFEST_RELPATH).read_text(encoding="utf-8") == before


class TestStoreProject:
    @pytest.fixture
    def stored(self, tmp_path, monkeypatch, capsys):
        root = tmp_path / "toy"
        (root / "alpha").mkdir(parents=True)
        (root / "alpha" / "mod.py").write_text("x = 1\n", encoding="utf-8")
        (root / "handbook").mkdir()
        (root / "handbook" / "overview.md").write_text(DOC, encoding="utf-8")
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.email", "t@example.invalid")
        git(root, "config", "user.name", "T")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        monkeypatch.chdir(root)
        assert run(capsys, "store", "init")[0] == 0
        return root

    def store_block(self, m: Manifest) -> dict:
        body = yaml.safe_load((m.store.root / "invariant.yaml").read_text(encoding="utf-8"))
        return body["store"]["repos"][m.store.key]

    def test_in_place_under_the_store_points_records_docs_at_the_store(
        self, stored, capsys
    ):
        m = Manifest.locate(stored)
        arch = m.store.root / "arch"
        arch.mkdir()
        (arch / "overview.rst").write_text(RST_DOC, encoding="utf-8")
        code, out = run(capsys, "docs", "adopt", str(arch), "--mode", "in_place")
        assert code == 0, out
        block = self.store_block(m)
        assert block["records"] == {"docs": "store"}
        assert block["paths"] == {"docs": "arch"}
        assert block["docs"]["adopt"] == {"mode": "in_place", "source": "arch"}
        again = Manifest.locate(stored)
        assert again.path_for("docs") == arch.resolve()
        assert assess(again).documents == 1

    def test_in_place_under_the_repo_keeps_records_docs_in_the_repo(self, stored, capsys):
        rst_handbook(stored)
        assert run(capsys, "docs", "adopt", "guide", "--mode", "in_place")[0] == 0
        m = Manifest.locate(stored)
        assert self.store_block(m)["records"] == {"docs": "repo"}
        assert m.path_for("docs") == (stored / "guide").resolve()

    def test_copy_lands_in_the_store_docs_location(self, stored, capsys):
        m = Manifest.locate(stored)
        from invariant.core.stores import write_store_settings

        write_store_settings(
            m.store.root,
            m.store.key,
            repo={"records": {"docs": "store"}, "paths": {"docs": "arch"}},
        )
        before = tree(stored / "handbook")
        assert run(capsys, "docs", "adopt", "handbook", "--mode", "copy")[0] == 0
        m = Manifest.locate(stored)
        assert (m.store.root / "arch" / "overview.md").is_file()
        assert (m.store.root / "arch" / RECEIPT).is_file()
        assert tree(stored / "handbook") == before
        # The source lies under the repo, not under the store the docs record
        # names, so it is recorded as given.
        assert m.get("docs.adopt.source") == str((stored / "handbook").resolve())
        assert adoption_drift(m).clean


    def test_a_key_the_pointer_sets_is_refused_before_anything_is_written(
        self, stored, capsys
    ):
        (stored / POINTER).write_text(
            "version: 1\nrecords:\n  docs: repo\n", encoding="utf-8"
        )
        m = Manifest.locate(stored)
        arch = m.store.root / "arch"
        arch.mkdir()
        (arch / "overview.rst").write_text(RST_DOC, encoding="utf-8")
        before = (m.store.root / "invariant.yaml").read_text(encoding="utf-8")
        code, out = run(capsys, "docs", "adopt", str(arch), "--mode", "in_place")
        assert code == 2
        assert "records.docs" in out and POINTER in out
        assert (m.store.root / "invariant.yaml").read_text(encoding="utf-8") == before

    def test_a_dry_run_names_the_store_file(self, stored, capsys):
        rst_handbook(stored)
        m = Manifest.locate(stored)
        code, out = run(
            capsys, "docs", "adopt", "guide", "--mode", "in_place", "--dry-run"
        )
        assert code == 0
        assert str(m.store.root / "invariant.yaml") in out

    def test_a_commented_store_file_gets_the_block_by_hand(self, stored, capsys):
        m = Manifest.locate(stored)
        path = m.store.root / "invariant.yaml"
        path.write_text("# ours\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
        rst_handbook(stored)
        code, data = run_json(capsys, "docs", "adopt", "guide", "--mode", "in_place")
        assert code == 0
        assert data["written"] is None
        assert "paths" in data["settings_by_hand"]["yaml"]


class TestPlanAdoption:
    def test_the_plan_names_the_settings_without_writing(self, toy):
        m = Manifest.locate(toy)
        plan = plan_adoption(m, "handbook", "copy", cwd=toy)
        assert plan.settings == {
            "docs": {"format": "markdown", "adopt": {"mode": "copy", "source": "handbook"}}
        }
        assert plan.files == ("deep/notes.md", "overview.md")
        assert plan.assessable
        assert not (toy / "docs" / "architecture").exists()

    def test_an_unknown_mode_is_refused(self, toy):
        with pytest.raises(UsageError, match="mirror"):
            plan_adoption(Manifest.locate(toy), "handbook", "mirror", cwd=toy)


class TestASphinxTreeInPlace:
    """ADR-016 increment 4's deferred proof: a Sphinx tree adopted in place
    and assessed through the rST reader (ADR-018 increment 2)."""

    def test_it_is_adopted_and_assessed(self, toy, capsys):
        manual = toy / "manual"
        manual.mkdir()
        (manual / "conf.py").write_text("project = 'toy'\n", encoding="utf-8")
        (manual / "index.rst").write_text(
            "Toy\n===\n\nLayers\n------\n\nalpha. See :doc:`gone`.\n",
            encoding="utf-8",
        )
        code, out = run(capsys, "docs", "adopt", "manual", "--mode", "in_place")
        assert code == 0, out
        assert "no reader" not in out
        m = Manifest.locate(toy)
        assert m.get("docs.format") == "sphinx"
        assert m.get("paths.docs") == "manual"
        assessment = assess(m)
        assert [g.subject for g in assessment.by_check("sections")] == [
            "Component responsibilities",
            "Compliance checklist",
        ]
        assert [g.render() for g in assessment.by_check("links")] == [
            "index.rst:7: gone does not exist"
        ]
