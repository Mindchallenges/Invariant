"""ADR-014 D3, level 5a: the user's defaults, below a project's own settings.

Ruling 19: user-level defaults apply to in-repo projects too, below the
repo's own settings, and `config show` and `doctor` name every value that
came from the user level. With none written, nothing resolves differently.
The suite's INVARIANT_HOME is a temporary directory (conftest), so nothing
here reads or writes the real one.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.cli.main import main
from invariant.core.errors import ManifestError, UnknownRuleError
from invariant.core.home import (
    USER_CONFIG,
    UserConfig,
    display_path,
    invariant_home,
    load_user_config,
    user_config_path,
)
from invariant.core.manifest import MANIFEST_RELPATH, Level, Manifest
from invariant.oracle.home import oracle_home


def write_user_config(body) -> Path:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = body if isinstance(body, str) else yaml.safe_dump(body)
    path.write_text(text, encoding="utf-8")
    return path


def write_manifest(root: Path, body: dict) -> Path:
    path = root / MANIFEST_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


class TestHome:
    def test_the_variable_names_the_home(self, tmp_path):
        assert invariant_home({"INVARIANT_HOME": str(tmp_path)}) == tmp_path

    def test_unset_or_empty_is_the_home_directory(self):
        for env in ({}, {"INVARIANT_HOME": ""}):
            assert invariant_home(env) == Path.home() / ".invariant"

    def test_the_oracle_reads_the_same_home(self):
        assert oracle_home() == invariant_home()

    def test_the_user_config_lives_in_the_home(self):
        assert user_config_path() == invariant_home() / USER_CONFIG

    def test_a_path_under_the_home_directory_is_shown_with_a_tilde(self):
        assert display_path(Path.home() / ".invariant" / "config.yaml") == (
            "~/.invariant/config.yaml"
        )

    def test_any_other_path_is_shown_as_it_is(self, tmp_path):
        assert display_path(tmp_path / "x") == str(tmp_path / "x")


class TestLoadUserConfig:
    def test_no_file_is_an_empty_level(self):
        found = load_user_config()
        assert found == UserConfig(path=user_config_path())
        assert found.defaults == {}
        assert found.repos == {}
        assert found.store_naming == "repo"

    def test_the_label_names_the_file(self):
        assert load_user_config().label == f"user:{display_path(user_config_path())}"

    def test_a_home_that_is_no_directory_is_an_empty_level(self):
        """The file cannot exist, so there is no user level; an unusable home
        must never change what a project resolves (the oracle's own rule)."""
        home = invariant_home()
        home.parent.mkdir(parents=True, exist_ok=True)
        home.write_text("x", encoding="utf-8")
        assert load_user_config().defaults == {}

    def test_a_config_that_exists_and_cannot_be_read_is_refused(self):
        user_config_path().mkdir(parents=True)
        with pytest.raises(ManifestError, match="cannot be read"):
            load_user_config()

    def test_an_empty_file_is_an_empty_level(self):
        write_user_config("")
        assert load_user_config().defaults == {}

    def test_the_three_sections_are_read(self):
        write_user_config(
            {
                "version": 1,
                "store_naming": "url",
                "defaults": {"methodology": {"gates": {"coverage_min": 70}}},
                "repos": {"host.example/a/b": {"store": "~/stores/b"}},
            }
        )
        found = load_user_config()
        assert found.store_naming == "url"
        assert found.defaults == {"methodology": {"gates": {"coverage_min": 70}}}
        assert found.repos == {"host.example/a/b": {"store": "~/stores/b"}}

    @pytest.mark.parametrize(
        ("body", "needle"),
        [
            ("defaults: [unclosed\n", "not valid YAML"),
            ("- a\n", "must contain a mapping"),
            ({"version": 2}, "version 2"),
            ({"projects": {}}, "'projects'"),
            ({"defaults": ["x"]}, "defaults must be a mapping"),
            ({"defaults": {"project": {"name": "x"}}}, "project-specific"),
            ({"defaults": {"records": {"docs": "store"}}}, "project-specific"),
            ({"repos": ["x"]}, "repos must be a mapping"),
            ({"repos": {"host.example/a/b": "path"}}, "must be a mapping"),
            ({"repos": {"host.example/a/b": {"store": ""}}}, "store must be a path"),
            ({"repos": {"host.example/a/b": {}}}, "store must be a path"),
            ({"repos": {"host.example/a/b": {"store": "x", "docs": "y"}}}, "'docs'"),
            ({"repos": {"": {"store": "x"}}}, "an identity"),
            ({"store_naming": "folder"}, "store_naming"),
        ],
    )
    def test_a_malformed_user_level_is_refused_naming_the_file(self, body, needle):
        write_user_config(body)
        with pytest.raises(ManifestError, match=needle) as caught:
            load_user_config()
        assert str(user_config_path()) in str(caught.value) or "~/" in str(caught.value)


class TestUserDefaultsReachAnInRepoProject:
    def test_none_written_resolves_exactly_as_before(self, tmp_path):
        write_manifest(tmp_path, {"version": 1, "project": {"name": "demo"}})
        without = Manifest.locate(tmp_path)
        write_user_config({"version": 1})
        empty = Manifest.locate(tmp_path)
        assert empty.data == without.data
        assert empty.provenance == without.provenance

    def test_a_user_default_applies_with_its_label(self, tmp_path):
        write_manifest(tmp_path, {"version": 1})
        write_user_config({"defaults": {"methodology": {"gates": {"coverage_min": 70}}}})
        m = Manifest.locate(tmp_path)
        assert m.get("methodology.gates.coverage_min") == 70
        assert m.source_of("methodology.gates.coverage_min") == load_user_config().label

    def test_a_key_the_repo_sets_wins_over_it(self, tmp_path):
        write_manifest(tmp_path, {"methodology": {"gates": {"coverage_min": 85}}})
        write_user_config({"defaults": {"methodology": {"gates": {"coverage_min": 70}}}})
        m = Manifest.locate(tmp_path)
        assert m.get("methodology.gates.coverage_min") == 85
        assert m.source_of("methodology.gates.coverage_min") == "repo:.claude/invariant.yaml"

    def test_a_user_profile_selects_the_profile_below_the_repo(self, tmp_path):
        write_manifest(tmp_path, {"version": 1})
        write_user_config({"defaults": {"enforcement": {"profile": "strict"}}})
        m = Manifest.locate(tmp_path)
        assert m.get("enforcement.profile") == "strict"
        assert m.source_of("enforcement.rules.privacy.clean") == "profile:strict"

    def test_the_repo_profile_wins_over_the_user_profile(self, tmp_path):
        write_manifest(tmp_path, {"enforcement": {"profile": "loose"}})
        write_user_config({"defaults": {"enforcement": {"profile": "strict"}}})
        assert Manifest.locate(tmp_path).get("enforcement.profile") == "loose"

    def test_a_user_rule_level_sits_above_the_profile(self, tmp_path):
        write_manifest(tmp_path, {"version": 1})
        write_user_config(
            {"defaults": {"enforcement": {"rules": {"memory.index_current": "off"}}}}
        )
        m = Manifest.locate(tmp_path)
        assert m.rule_level("memory.index_current") is Level.OFF
        assert m.source_of("enforcement.rules.memory.index_current").startswith("user:")

    def test_they_reach_a_directory_with_no_manifest_too(self, tmp_path):
        write_user_config({"defaults": {"methodology": {"gates": {"coverage_min": 70}}}})
        m = Manifest.locate(tmp_path, required=False)
        assert m.get("methodology.gates.coverage_min") == 70

    def test_an_invalid_user_default_is_refused_naming_the_user_level(self, tmp_path):
        write_manifest(tmp_path, {"version": 1})
        write_user_config({"defaults": {"items": {"backend": "spreadsheet"}}})
        with pytest.raises(ManifestError, match="spreadsheet") as caught:
            Manifest.locate(tmp_path)
        assert "items.backend" in str(caught.value)
        assert load_user_config().label in str(caught.value)

    def test_an_unknown_rule_in_the_user_level_stays_an_unknown_rule(self, tmp_path):
        """The oracle tells an older build from a typo by this type (BUG_089)."""
        write_manifest(tmp_path, {"version": 1})
        write_user_config(
            {"defaults": {"enforcement": {"rules": {"privacy.klean": "off"}}}}
        )
        with pytest.raises(UnknownRuleError) as caught:
            Manifest.locate(tmp_path)
        assert caught.value.rule_id == "privacy.klean"
        assert load_user_config().label in str(caught.value)

    def test_an_invalid_repo_value_names_no_user_level_when_none_contributed(
        self, tmp_path
    ):
        write_manifest(tmp_path, {"items": {"backend": "spreadsheet"}})
        with pytest.raises(ManifestError) as caught:
            Manifest.locate(tmp_path)
        assert "user:" not in str(caught.value)

    def test_a_user_level_values_lists_each_key_it_set(self, tmp_path):
        write_manifest(tmp_path, {"methodology": {"gates": {"coverage_min": 85}}})
        write_user_config(
            {
                "defaults": {
                    "methodology": {"gates": {"coverage_min": 70}},
                    "memory": {"index_generated": False},
                }
            }
        )
        m = Manifest.locate(tmp_path)
        assert m.user_level_values() == [
            ("memory.index_generated", False, load_user_config().label)
        ]


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "app.py").write_text("print(1)\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    monkeypatch.chdir(root)
    assert main(["init", "--host-install", "--ecosystem", "python"]) == 0
    return root


def doctor_row(capsys, label: str) -> str:
    capsys.readouterr()
    main(["doctor"])
    lines = capsys.readouterr().out.splitlines()
    return next(line for line in lines if label in line)


class TestTheUserLevelIsNamed:
    def test_config_show_labels_a_user_default(self, project, capsys):
        write_user_config({"defaults": {"methodology": {"gates": {"coverage_min": 70}}}})
        capsys.readouterr()
        main(["config", "show", "--provenance"])
        text = capsys.readouterr().out
        row = next(ln for ln in text.splitlines() if "coverage_min" in ln)
        assert "70" in row
        assert f"[{load_user_config().label}]" in row

    def test_doctor_lists_every_user_level_value(self, project, capsys):
        write_user_config(
            {
                "defaults": {
                    "methodology": {"gates": {"coverage_min": 70}},
                    "memory": {"index_generated": False},
                }
            }
        )
        row = doctor_row(capsys, "user defaults")
        assert "methodology.gates.coverage_min = 70" in row
        assert "memory.index_generated = False" in row
        assert load_user_config().label.removeprefix("user:") in row

    def test_doctor_says_when_there_is_none(self, project, capsys):
        row = doctor_row(capsys, "user defaults")
        assert "none" in row

    def test_doctor_names_a_user_default_the_repo_overrides_nowhere(
        self, project, capsys
    ):
        manifest = project / MANIFEST_RELPATH
        body = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        body.setdefault("methodology", {}).setdefault("gates", {})["coverage_min"] = 88
        manifest.write_text(yaml.safe_dump(body), encoding="utf-8")
        write_user_config({"defaults": {"methodology": {"gates": {"coverage_min": 70}}}})
        row = doctor_row(capsys, "user defaults")
        assert "coverage_min" not in row
        assert "none" in row

    def test_a_broken_user_level_fails_doctor_naming_the_file(self, project, capsys):
        write_user_config({"store_naming": "folder"})
        capsys.readouterr()
        assert main(["doctor"]) == 1
        text = capsys.readouterr().out
        assert "store_naming" in text

    def test_the_suite_never_reaches_the_real_home(self):
        assert os.environ["INVARIANT_HOME"] != str(Path.home() / ".invariant")
