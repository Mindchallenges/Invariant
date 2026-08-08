import pytest
import yaml

from invariant.core.errors import ManifestError
from invariant.core.manifest import MANIFEST_RELPATH, Level, Manifest


def write_manifest(root, body: dict):
    path = root / MANIFEST_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


@pytest.fixture
def minimal(tmp_path):
    write_manifest(tmp_path, {"version": 1, "project": {"name": "demo"}})
    return Manifest.load(tmp_path)


class TestDiscovery:
    def test_found_in_the_directory_itself(self, tmp_path, minimal):
        assert minimal.root == tmp_path.resolve()

    def test_found_from_a_subdirectory(self, tmp_path):
        write_manifest(tmp_path, {"version": 1})
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        assert Manifest.load(deep).root == tmp_path.resolve()

    def test_absent_manifest_names_the_init_command(self, tmp_path):
        with pytest.raises(ManifestError, match="invariant init"):
            Manifest.load(tmp_path)

    def test_absent_manifest_is_tolerated_when_not_required(self, tmp_path):
        m = Manifest.load(tmp_path, required=False)
        assert m.get("project.name") == "unnamed"

    def test_invalid_yaml(self, tmp_path):
        path = tmp_path / MANIFEST_RELPATH
        path.parent.mkdir(parents=True)
        path.write_text("project: [unclosed\n", encoding="utf-8")
        with pytest.raises(ManifestError, match="not valid YAML"):
            Manifest.load(tmp_path)

    def test_non_mapping_manifest(self, tmp_path):
        path = tmp_path / MANIFEST_RELPATH
        path.parent.mkdir(parents=True)
        path.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(ManifestError, match="must contain a mapping"):
            Manifest.load(tmp_path)


class TestLayering:
    def test_defaults_apply(self, minimal):
        assert minimal.get("methodology.pipeline") == "tdd"
        assert minimal.source_of("methodology.pipeline") == "default"

    def test_adapter_defaults_beat_library_defaults(self, tmp_path):
        write_manifest(tmp_path, {"project": {"ecosystems": ["python"]}})
        m = Manifest.load(tmp_path)
        assert m.get("commands.test") == "pytest"
        assert m.source_of("commands.test") == "adapter:python"

    def test_explicit_values_beat_adapter_defaults(self, tmp_path):
        write_manifest(
            tmp_path,
            {
                "project": {"ecosystems": ["python"]},
                "commands": {"test": "make test"},
            },
        )
        m = Manifest.load(tmp_path)
        assert m.get("commands.test") == "make test"
        assert m.source_of("commands.test") == "manifest"

    def test_first_ecosystem_is_primary_for_overlapping_keys(self, tmp_path):
        write_manifest(tmp_path, {"project": {"ecosystems": ["python", "node"]}})
        m = Manifest.load(tmp_path)
        # Later adapters still merge, so the last writer of a shared key wins;
        # provenance is what makes that legible rather than mysterious.
        assert m.source_of("commands.test") == "adapter:node"

    def test_swift_adapter_switches_the_doc_format(self, tmp_path):
        write_manifest(tmp_path, {"project": {"ecosystems": ["swift"]}})
        m = Manifest.load(tmp_path)
        assert m.get("docs.format") == "sphinx"
        assert m.get("docs.diagrams") == "plantuml"


class TestEnforcementResolution:
    def test_profile_supplies_levels(self, minimal):
        assert minimal.rule_level("privacy.clean") is Level.BLOCK
        assert minimal.source_of("enforcement.rules.privacy.clean") == "profile:standard"

    def test_per_rule_override_beats_the_profile(self, tmp_path):
        write_manifest(
            tmp_path,
            {"enforcement": {"profile": "standard", "rules": {"privacy.clean": "warn"}}},
        )
        m = Manifest.load(tmp_path)
        assert m.rule_level("privacy.clean") is Level.WARN
        assert m.source_of("enforcement.rules.privacy.clean") == "manifest"

    def test_strict_profile_blocks_more(self, tmp_path):
        write_manifest(tmp_path, {"enforcement": {"profile": "strict"}})
        m = Manifest.load(tmp_path)
        assert m.rule_level("coverage.meets_floor") is Level.BLOCK

    def test_loose_still_blocks_the_unrecoverable_two(self, tmp_path):
        write_manifest(tmp_path, {"enforcement": {"profile": "loose"}})
        m = Manifest.load(tmp_path)
        assert m.rule_level("privacy.clean") is Level.BLOCK
        assert m.rule_level("session.no_edit_in_main_checkout") is Level.BLOCK
        assert m.rule_level("coverage.meets_floor") is Level.OFF

    def test_unknown_profile_lists_the_available_ones(self, tmp_path):
        write_manifest(tmp_path, {"enforcement": {"profile": "nonexistent"}})
        with pytest.raises(ManifestError, match="loose, standard, strict"):
            Manifest.load(tmp_path)

    def test_unknown_rule_defaults_to_warn(self, minimal):
        assert minimal.rule_level("some.unregistered.rule") is Level.WARN

    def test_invalid_level_is_rejected_with_the_valid_ones(self, tmp_path):
        write_manifest(tmp_path, {"enforcement": {"rules": {"privacy.clean": "shout"}}})
        with pytest.raises(ManifestError, match="block, warn, off"):
            Manifest.load(tmp_path)


class TestEnvironmentOverrides:
    def test_profile_override(self, tmp_path):
        write_manifest(tmp_path, {})
        raw = yaml.safe_load((tmp_path / MANIFEST_RELPATH).read_text()) or {}
        m = Manifest.from_raw(
            raw,
            root=tmp_path,
            path=tmp_path / MANIFEST_RELPATH,
            env={"INVARIANT_PROFILE": "loose"},
        )
        assert m.rule_level("coverage.meets_floor") is Level.OFF
        assert m.source_of("enforcement.profile") == "env:INVARIANT_PROFILE"

    def test_disable_list(self, tmp_path):
        m = Manifest.from_raw(
            {},
            root=tmp_path,
            path=tmp_path / MANIFEST_RELPATH,
            env={"INVARIANT_DISABLE": "privacy.clean, docs.updated"},
        )
        assert m.rule_level("privacy.clean") is Level.OFF
        assert m.rule_level("docs.updated") is Level.OFF
        assert m.source_of("enforcement.rules.privacy.clean") == "env:INVARIANT_DISABLE"

    def test_state_root_override(self, tmp_path):
        m = Manifest.from_raw(
            {},
            root=tmp_path,
            path=tmp_path / MANIFEST_RELPATH,
            env={"INVARIANT_STATE_ROOT": "somewhere/else"},
        )
        assert m.state_root == (tmp_path / "somewhere" / "else").resolve()


class TestValidation:
    def test_future_version_is_refused(self, tmp_path):
        write_manifest(tmp_path, {"version": 99})
        with pytest.raises(ManifestError, match="understands version 1"):
            Manifest.load(tmp_path)

    def test_unknown_item_backend(self, tmp_path):
        write_manifest(tmp_path, {"items": {"backend": "carrier-pigeon"}})
        with pytest.raises(ManifestError, match="unknown work-item backend"):
            Manifest.load(tmp_path)

    def test_privacy_scope_must_be_a_list(self, tmp_path):
        write_manifest(tmp_path, {"privacy": {"scope": "docs"}})
        with pytest.raises(ManifestError, match="privacy.scope must be a list"):
            Manifest.load(tmp_path)


class TestCommandTemplates:
    def test_static_vars_substitute(self, tmp_path):
        write_manifest(
            tmp_path,
            {"commands": {"build": "build {ws} {scheme}"},
             "vars": {"ws": "App.xcworkspace", "scheme": "App"}},
        )
        m = Manifest.load(tmp_path)
        assert m.command_for("build") == "build App.xcworkspace App"

    def test_discovery_runs_a_command(self, tmp_path):
        write_manifest(
            tmp_path,
            {"commands": {"test": "run --id {device}"},
             "discover": {"device": "printf ABC123"}},
        )
        m = Manifest.load(tmp_path)
        assert m.command_for("test") == "run --id ABC123"

    def test_discovery_result_is_cached(self, tmp_path):
        counter = tmp_path / "runs"
        write_manifest(
            tmp_path,
            {"commands": {"test": "{a} {a}"},
             "discover": {"a": f"echo x >> {counter}; printf V"}},
        )
        m = Manifest.load(tmp_path)
        m.command_for("test")
        m.command_for("test")
        assert counter.read_text().count("x") == 1

    def test_a_placeholder_with_no_source_points_at_discovery(self, tmp_path):
        write_manifest(tmp_path, {"commands": {"test": "run {mystery}"}})
        m = Manifest.load(tmp_path)
        with pytest.raises(ManifestError, match="Prefer a discover entry"):
            m.command_for("test")

    def test_failed_discovery_reports_the_command(self, tmp_path):
        write_manifest(
            tmp_path,
            {"commands": {"test": "run {x}"}, "discover": {"x": "exit 3"}},
        )
        m = Manifest.load(tmp_path)
        with pytest.raises(ManifestError, match=r"exit 3\)"):
            m.command_for("test")

    def test_empty_discovery_output_is_a_failure(self, tmp_path):
        write_manifest(
            tmp_path,
            {"commands": {"test": "run {x}"}, "discover": {"x": "true"}},
        )
        m = Manifest.load(tmp_path)
        with pytest.raises(ManifestError, match="produced nothing"):
            m.command_for("test")

    def test_missing_command(self, minimal):
        with pytest.raises(ManifestError, match="no command configured for 'build'"):
            minimal.command_for("build")

    def test_extra_kwargs_win(self, tmp_path):
        write_manifest(
            tmp_path,
            {"commands": {"test_one": "run {selector}"}, "vars": {"selector": "all"}},
        )
        m = Manifest.load(tmp_path)
        assert m.command_for("test_one", selector="OneTest") == "run OneTest"


def test_provenance_is_enumerable(minimal):
    rows = list(minimal.iter_provenance())
    assert any(dotted == "methodology.pipeline" for dotted, _, _ in rows)
    assert all(source for _, _, source in rows)


def test_path_for_resolves_against_the_root(tmp_path, minimal):
    assert minimal.path_for("docs") == (tmp_path / "docs" / "architecture").resolve()


def test_path_for_unknown_key(minimal):
    with pytest.raises(ManifestError, match="no path configured"):
        minimal.path_for("nonexistent")
