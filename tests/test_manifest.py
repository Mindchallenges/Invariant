from typing import ClassVar

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
        # The primary wins every key it names; a further ecosystem's commands
        # live under their own key (ADR-013 D4). Later-wins here was BUG_097.
        assert m.source_of("commands.test") == "adapter:python"
        assert m.source_of("commands.node.test") == "adapter:node"

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
        with pytest.raises(ManifestError, match="block, ask, warn, off"):
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

    def test_coverage_advisory_paths_defaults_to_empty(self, minimal):
        """The key is known, so `config show --provenance` lists it."""
        assert minimal.get("methodology.gates.coverage_advisory_paths") == []
        keys = {key for key, *_ in minimal.iter_provenance()}
        assert "methodology.gates.coverage_advisory_paths" in keys

    @pytest.mark.parametrize(
        "key", ["coverage_advisory_paths", "coverage_exclude", "non_source"]
    )
    def test_a_glob_list_given_as_a_string_is_refused(self, tmp_path, key):
        """A bare string iterates as characters, and one of them is `*`: the
        whole tree would match. A mistyped relaxation is refused at load."""
        write_manifest(tmp_path, {"methodology": {"gates": {key: "web/**"}}})
        with pytest.raises(ManifestError, match=f"methodology.gates.{key}"):
            Manifest.load(tmp_path)

    def test_a_glob_list_given_as_null_reads_as_empty(self, tmp_path):
        """A YAML `key:` with no value is null; every reader treats it as the
        empty list, so load accepts it rather than refusing a non-relaxation."""
        write_manifest(
            tmp_path, {"methodology": {"gates": {"coverage_advisory_paths": None}}}
        )
        assert not Manifest.load(tmp_path).get(
            "methodology.gates.coverage_advisory_paths"
        )

    def test_a_glob_list_entry_must_be_a_string(self, tmp_path):
        write_manifest(
            tmp_path,
            {"methodology": {"gates": {"coverage_advisory_paths": [["web/**"]]}}},
        )
        with pytest.raises(ManifestError, match="coverage_advisory_paths"):
            Manifest.load(tmp_path)

    def test_unknown_methodology_is_a_validation_error(self, tmp_path):
        """AUD-08: a mistyped `methodology.pipeline` used to surface only when
        a machine load happened to run — and several gates skipped, silently
        and non-blocking, exactly then. The manifest is where a configuration
        error belongs, loudly, at load."""
        write_manifest(tmp_path, {"methodology": {"pipeline": "tdd-typo"}})
        with pytest.raises(ManifestError, match="methodology.pipeline"):
            Manifest.load(tmp_path)

    def test_a_project_local_methodology_validates(self, tmp_path):
        (tmp_path / "machines").mkdir()
        (tmp_path / "machines" / "local.yaml").write_text(
            "id: local\nkind: pipeline\ninitial: a\nterminal: [b]\n"
            "states:\n  a:\n    next: [b]\n  b: {}\n",
            encoding="utf-8",
        )
        write_manifest(
            tmp_path,
            {"methodology": {"pipeline": "local", "extra_machines": ["machines"]}},
        )
        assert Manifest.load(tmp_path).get("methodology.pipeline") == "local"


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

    def test_a_run_reports_only_what_its_discovery_resolved(self, tmp_path):
        # BUG_093: a run records its discovered values so a replay can hand
        # them back; vars, extras and placeholders of other commands are not.
        write_manifest(
            tmp_path,
            {"commands": {"test": "run {device} {scheme} {bundle}", "build": "b {other}"},
             "vars": {"scheme": "App"},
             "discover": {"device": "printf SIM-A", "other": "printf O", "scheme": "printf S"}},
        )
        m = Manifest.load(tmp_path)
        assert m.discovered_in("test", bundle="/b") == {}
        m.command_for("test", bundle="/b")
        assert m.discovered_in("test", bundle="/b") == {"device": "SIM-A"}
        assert m.discovered_in("build") == {}
        assert m.discovered_in("lint") == {}

    def test_what_only_discovery_can_supply(self, tmp_path):
        write_manifest(
            tmp_path,
            {"commands": {"test_one": "run {device} {scheme} {selector}"},
             "vars": {"scheme": "App"},
             "discover": {"device": "printf SIM-A", "scheme": "printf S"}},
        )
        m = Manifest.load(tmp_path)
        assert m.discovers_for("test_one") == ["device"]
        assert m.discovers_for("lint") == []

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


class TestDeclaredPathsHaveOneSpelling:
    """`project_relative` — the one normaliser every matched declaration uses.

    A project-relative target never starts with ``./``, never ends with ``/``
    and carries no empty or ``..`` segment, so a declaration compared as
    written matched nothing whenever it was spelled any of those ways, and the
    exemption it declared was off without a word (BUG_071).
    """

    @pytest.mark.parametrize(
        "written",
        [
            "docs/backlog",
            "./docs/backlog",
            "docs/backlog/",
            "./docs/backlog/",
            "docs//backlog",
            "docs/./backlog",
            "docs/x/../backlog",
            " docs/backlog ",
        ],
    )
    def test_equivalent_spellings_reduce_to_one(self, written):
        from invariant.core.manifest import project_relative

        assert project_relative(written) == "docs/backlog"

    @pytest.mark.parametrize(
        ("written", "canonical"),
        [
            ("./.claude/**", ".claude/**"),
            (".claude/*", ".claude/*"),
            ("design//**/", "design/**"),
            ("MEMORY.md", "MEMORY.md"),
        ],
    )
    def test_globs_are_normalised_lexically(self, written, canonical):
        from invariant.core.manifest import project_relative

        assert project_relative(written) == canonical

    @pytest.mark.parametrize("written", ["../outside", "..", "docs/../../outside"])
    def test_a_relative_path_that_climbs_out_names_nothing_inside(self, written):
        from invariant.core.manifest import project_relative

        assert project_relative(written) is None

    @pytest.mark.parametrize("written", [None, "", "   "])
    def test_an_empty_declaration_names_nothing(self, written):
        from invariant.core.manifest import project_relative

        assert project_relative(written) is None

    @pytest.mark.parametrize("written", [".", "./", "docs/.."])
    def test_the_root_itself_stays_the_root(self, written):
        """Never widened to "everything": a caller building match patterns
        drops it, so it stays as inert as it always was."""
        from invariant.core.manifest import project_relative

        assert project_relative(written) == "."

    def test_an_absolute_path_inside_a_base_is_spelled_against_it(self, tmp_path):
        from invariant.core.manifest import project_relative

        written = str(tmp_path / "docs" / "backlog") + "/"
        assert project_relative(written, [tmp_path]) == "docs/backlog"

    def test_an_absolute_path_is_tried_against_every_base(self, tmp_path):
        from invariant.core.manifest import project_relative

        main, other = tmp_path / "main", tmp_path / "other"
        written = str(main / "docs" / "backlog")
        assert project_relative(written, [None, other, main]) == "docs/backlog"

    def test_an_absolute_path_through_a_symlinked_base_is_recognised(self, tmp_path):
        from invariant.core.manifest import project_relative

        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        assert project_relative(str(real / "docs"), [link]) == "docs"
        assert project_relative(str(link / "docs"), [link]) == "docs"

    def test_the_absolute_root_is_the_root(self, tmp_path):
        from invariant.core.manifest import project_relative

        assert project_relative("/" + str(tmp_path), [tmp_path]) == "."

    def test_an_absolute_path_outside_every_base_names_nothing(self, tmp_path):
        from invariant.core.manifest import project_relative

        assert project_relative(str(tmp_path.parent / "elsewhere"), [tmp_path]) is None
        assert project_relative(str(tmp_path / "docs"), []) is None

    def test_a_sibling_sharing_the_bases_name_prefix_is_outside(self, tmp_path):
        from invariant.core.manifest import project_relative

        base = tmp_path / "proj"
        assert project_relative(str(tmp_path / "proj-other" / "docs"), [base]) is None

    def test_a_climb_back_out_of_an_absolute_base_is_outside(self, tmp_path):
        from invariant.core.manifest import project_relative

        base = tmp_path / "proj"
        assert project_relative(f"{base}/docs/../../escape", [base]) is None


class TestSweepFreshIsCappedAtWarn:
    """ADR-010 D3 item 5, ruling 10: `sweep.fresh` only ever warns.

    A stale sweep is evidence the project has not refreshed, never a reason
    to stop a change; the engine holds that ruling at load rather than in
    prose, so a manifest raising the rule's level is refused, naming it.
    """

    CADENCE: ClassVar[dict] = {"schedule": {"cadence_days": 7, "gates": ["coverage.meets_floor"]}}

    @pytest.mark.parametrize("level", ["block", "ask"])
    def test_a_level_that_stops_work_is_refused(self, tmp_path, level):
        write_manifest(
            tmp_path,
            {"methodology": {"gates": self.CADENCE}, "enforcement": {"rules": {"sweep.fresh": level}}},
        )
        with pytest.raises(ManifestError) as caught:
            Manifest.load(tmp_path)
        text = str(caught.value)
        assert "sweep.fresh" in text
        assert level in text
        assert "warn" in text

    def test_it_is_refused_without_a_cadence_too(self, tmp_path):
        """The cap is a property of the rule, not of the cadence: a level
        nothing reads today would block the day a cadence is set."""
        write_manifest(tmp_path, {"enforcement": {"rules": {"sweep.fresh": "block"}}})
        with pytest.raises(ManifestError, match="sweep.fresh"):
            Manifest.load(tmp_path)

    @pytest.mark.parametrize("level", ["warn", "off"])
    def test_warn_and_off_load(self, tmp_path, level):
        write_manifest(
            tmp_path,
            {"methodology": {"gates": self.CADENCE}, "enforcement": {"rules": {"sweep.fresh": level}}},
        )
        assert Manifest.load(tmp_path).rule_level("sweep.fresh") is Level.parse(level)

    @pytest.mark.parametrize("profile", ["loose", "standard", "strict"])
    def test_every_shipped_profile_loads_it_at_warn(self, tmp_path, profile):
        from invariant.core.manifest import DATA_DIR

        raw = yaml.safe_load((DATA_DIR / "profiles" / f"{profile}.yaml").read_text())
        assert raw["rules"]["sweep.fresh"] == "warn"
        write_manifest(tmp_path, {"enforcement": {"profile": profile}})
        assert Manifest.load(tmp_path).rule_level("sweep.fresh") is Level.WARN



class TestTheAgentsKeys:
    """`agents.library`, `agents.local` and `agents.overlays` are real keys
    the renderer reads (ADR-012 D8, D13, the A6 rulings 6 and 11)."""

    def load(self, tmp_path, agents):
        write_manifest(tmp_path, {"agents": agents})
        return Manifest.load(tmp_path)

    def refused(self, tmp_path, agents):
        with pytest.raises(ManifestError) as caught:
            self.load(tmp_path, agents)
        return str(caught.value)

    def test_the_defaults(self, minimal):
        assert minimal.get("agents.overlays") == {}
        assert minimal.get("agents.local") == []
        assert "feature-architect" in minimal.get("agents.library")

    def test_an_overlay_naming_a_library_agent_loads(self, tmp_path):
        m = self.load(tmp_path, {"overlays": {"feature-architect": "docs/agents/fa.yaml"}})
        assert m.get("agents.overlays") == {"feature-architect": "docs/agents/fa.yaml"}
        assert m.source_of("agents.overlays.feature-architect") == "manifest"

    def test_there_is_no_implicit_overlay_location(self, tmp_path):
        """docs/agents/<name>.yaml is the convention, never a default (ruling 6)."""
        (tmp_path / "docs" / "agents").mkdir(parents=True)
        (tmp_path / "docs" / "agents" / "feature-architect.yaml").write_text("version: 1\n")
        assert self.load(tmp_path, {}).get("agents.overlays") == {}

    def test_agents_is_a_mapping(self, tmp_path):
        write_manifest(tmp_path, {"agents": ["feature-architect"]})
        with pytest.raises(ManifestError, match="agents must be a mapping"):
            Manifest.load(tmp_path)

    def test_an_unknown_agents_key(self, tmp_path):
        text = self.refused(tmp_path, {"overlay": {}})
        assert "agents.overlay " in text and "agents.overlays" in text

    def test_tiers_waits_for_a7(self, tmp_path):
        assert "A7" in self.refused(tmp_path, {"tiers": {}})

    @pytest.mark.parametrize("key", ["library", "local"])
    @pytest.mark.parametrize("value", ["feature-architect", [""], [3], None])
    def test_library_and_local_are_lists_of_names(self, tmp_path, key, value):
        assert f"agents.{key}" in self.refused(tmp_path, {key: value})

    @pytest.mark.parametrize("key", ["library", "local"])
    def test_a_name_listed_twice(self, tmp_path, key):
        names = ["feature-architect", "feature-architect"] if key == "library" else ["a", "a"]
        assert "twice" in self.refused(tmp_path, {key: names})

    def test_a_local_name_may_not_be_a_library_name(self, tmp_path):
        text = self.refused(tmp_path, {"local": ["feature-architect"]})
        assert "'feature-architect'" in text and "agents.library" in text

    def test_a_local_name_the_library_ships_but_the_project_left_out_loads(self, tmp_path):
        m = self.load(
            tmp_path, {"library": ["feature-architect"], "local": ["bug-reproducer"]}
        )
        assert m.get("agents.local") == ["bug-reproducer"]

    @pytest.mark.parametrize("value", [["docs/agents/fa.yaml"], "docs/agents/fa.yaml", None])
    def test_overlays_is_a_mapping(self, tmp_path, value):
        assert "agents.overlays" in self.refused(tmp_path, {"overlays": value})

    @pytest.mark.parametrize("path", ["", 3, None, ["a"]])
    def test_an_overlay_path_is_a_path(self, tmp_path, path):
        text = self.refused(tmp_path, {"overlays": {"feature-architect": path}})
        assert "agents.overlays.feature-architect" in text

    def test_an_overlay_must_name_a_library_agent(self, tmp_path):
        text = self.refused(tmp_path, {"overlays": {"nobody": "docs/agents/x.yaml"}})
        assert "'nobody'" in text and "agents.library" in text

    def test_an_overlay_on_a_local_agent_is_refused_with_its_own_fix(self, tmp_path):
        text = self.refused(
            tmp_path,
            {
                "library": ["feature-architect"],
                "local": ["mine"],
                "overlays": {"mine": "docs/agents/mine.yaml"},
            },
        )
        assert "project-local" in text and "edit it directly" in text
