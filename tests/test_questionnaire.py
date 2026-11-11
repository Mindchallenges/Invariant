"""ADR-017 increment 1: the catalogue, `resolve`, and init's configuration report.

D1: the shipped catalogue is data, `data/questionnaire/catalogue.yaml`, sections
of questions over manifest keys, checked as it loads. D2: `core/questionnaire`
resolves every question to its default (the value the configuration resolves
to now), that value's provenance label as `config show` prints it, its current
value at the target level, the levels allowed to hold its key and its choices.
D9: a non-interactive `init` adds, after its report, each core question's
resolved value and provenance and the line naming `invariant configure`, and
under `--json` a `configuration` field. The files it writes do not change.

INVARIANT_HOME is the suite's temporary home (conftest), so the user level here
is synthetic.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.cli import prompts
from invariant.cli.main import main
from invariant.core import machines, questionnaire
from invariant.core.errors import ManifestError
from invariant.core.home import PROJECT_SPECIFIC, user_config_path
from invariant.core.manifest import (
    DEFAULTS,
    MANIFEST_RELPATH,
    RECORD_LOCATIONS,
    Manifest,
    profile_names,
)
from invariant.core.privacy import PATTERNS

ORIGIN = "https://host.example/team/widgets.git"
IDENTITY = "host.example/team/widgets"

#: What init writes today for a python repository named toy, byte for byte.
TODAYS_MANIFEST = (
    "version: 1\n"
    "\n"
    "project:\n"
    "  name: toy\n"
    "  ecosystems: [python]\n"
    "\n"
    "enforcement:\n"
    "  profile: standard\n"
    "\n"
    "# Everything else has a default. 'invariant config show --provenance'\n"
    "# prints the resolved configuration and where each value came from.\n"
)

#: What init writes for the same repository, which has no docs: the docs
#: starter, and the project enforced on it (ADR-018 D10, D11), in the new
#: project's invariant-docs/, both keys written explicitly (ADR-020 D8).
STARTER_MANIFEST = TODAYS_MANIFEST.replace(
    "\n# Everything else",
    "\npaths:\n  docs: invariant-docs/architecture\n"
    "\ndocs:\n  format: sphinx\n  needs:\n    state: enforced\n    srcdir: invariant-docs\n"
    "\nitems:\n  backend: sphinx_needs\n  path: invariant-docs/backlog.rst\n"
    "\nguidelines:\n  ruleset: invariant-docs/guidelines/ruleset.yaml\n"
    "\n# Everything else",
)

CORE_KEYS = [
    "project.ecosystems",
    "commands.test",
    "commands.test_one",
    "enforcement.profile",
    "methodology.pipeline",
    "methodology.gates.coverage_min",
    "paths.docs",
    "docs.onboarding",
]


def git(root: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", *argv], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def tree(root: Path) -> dict[str, str]:
    """Every file under ``root``, outside .git, and the SHA-256 of its bytes."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def run(capsys, *argv) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def write_user(body: dict) -> Path:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def make_repo(path: Path, origin: str | None = ORIGIN) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.invalid")
    git(path, "config", "user.name", "T")
    (path / "pyproject.toml").write_text("[project]\nname = 'toy'\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "init")
    if origin:
        git(path, "remote", "add", "origin", origin)
    return path


@pytest.fixture
def toy(tmp_path, monkeypatch):
    """A python repository with no manifest."""
    root = make_repo(tmp_path / "toy", origin=None)
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def no_terminal(monkeypatch):
    """Fail the test if init asks anything: these runs are non-interactive."""
    monkeypatch.setattr(prompts, "interactive", lambda args: False)


@pytest.fixture
def store_project(tmp_path):
    """A repo linked from the user level to a store that sets the floor."""
    repo = make_repo(tmp_path / "widgets")
    store = tmp_path / "stores" / "widgets"
    store.mkdir(parents=True)
    (store / "invariant.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "project": {"name": "widgets", "ecosystems": ["python"]},
                "methodology": {"gates": {"coverage_min": 70}},
                "store": {"repos": {IDENTITY: {"methodology": {"pipeline": "chore"}}}},
            }
        ),
        encoding="utf-8",
    )
    write_user({"version": 1, "repos": {IDENTITY: {"store": str(store)}}})
    return repo, store


def in_repo(root: Path, body: str = "version: 1\n") -> Manifest:
    (root / MANIFEST_RELPATH).parent.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_RELPATH).write_text(body, encoding="utf-8")
    return Manifest.locate(root)


def by_id(questions) -> dict[str, questionnaire.Question]:
    return {q.id: q for q in questions}


# -- D1: the shipped catalogue -------------------------------------------


class TestTheShippedCatalogue:
    def test_it_loads_with_the_sections_of_d1_in_order(self):
        cat = questionnaire.load_catalogue()
        assert cat.core == ("ecosystems", "profile", "lane", "coverage", "docs")
        assert cat.advanced == ("agents", "guidelines", "records", "privacy", "session")

    def test_every_plain_question_is_over_a_key_the_manifest_has(self):
        cat = questionnaire.load_catalogue()
        for section in cat.sections.values():
            for entry in section.questions:
                if entry.kind == "step":
                    assert entry.key is None
                    assert entry.id in questionnaire.STEPS
                else:
                    assert entry.key == entry.id
                    node = DEFAULTS
                    for part in entry.key.split("."):
                        node = node[part]

    def test_the_core_sections_hold_the_questions_of_d1(self):
        cat = questionnaire.load_catalogue()
        ids = {sid: [q.id for q in cat.sections[sid].questions] for sid in cat.core}
        assert ids == {
            "ecosystems": ["project.ecosystems", "commands"],
            "profile": ["enforcement.profile"],
            "lane": ["methodology.pipeline"],
            "coverage": ["methodology.gates.coverage_min"],
            "docs": ["docs"],
        }

    def test_ordered_is_core_then_advanced(self):
        cat = questionnaire.load_catalogue()
        assert [(tier, s.id) for tier, s in cat.ordered()] == [
            *(("core", sid) for sid in cat.core),
            *(("advanced", sid) for sid in cat.advanced),
        ]


def write_catalogue(tmp_path: Path, body) -> Path:
    path = tmp_path / "catalogue.yaml"
    path.write_text(body if isinstance(body, str) else yaml.safe_dump(body), encoding="utf-8")
    return path


def minimal(**overrides) -> dict:
    body = {
        "version": 1,
        "core": ["coverage"],
        "advanced": [],
        "sections": {
            "coverage": {
                "title": "Coverage",
                "questions": [
                    {"id": "methodology.gates.coverage_min", "kind": "percent", "prompt": "Floor?"}
                ],
            }
        },
    }
    body.update(overrides)
    return body


def question(**fields) -> dict:
    return {"id": "methodology.gates.coverage_min", "kind": "percent", "prompt": "Floor?", **fields}


def with_question(**fields) -> dict:
    return minimal(
        sections={"coverage": {"title": "Coverage", "questions": [question(**fields)]}}
    )


class TestACatalogueIsCheckedAsItLoads:
    def test_a_minimal_catalogue_loads(self, tmp_path):
        cat = questionnaire.load_catalogue(write_catalogue(tmp_path, minimal()))
        assert cat.core == ("coverage",)
        assert cat.sections["coverage"].title == "Coverage"

    @pytest.mark.parametrize(
        "body, says",
        [
            ("- a list\n", "must contain a mapping"),
            (": : not yaml\n  - [", "not valid YAML"),
            (minimal(version=2), "version 2"),
            (minimal(extra=1), "'extra'"),
            (minimal(core="coverage"), "core must be a list"),
            (minimal(core=["nowhere"]), "'nowhere'"),
            (minimal(core=["coverage"], advanced=["coverage"]), "both core and advanced"),
            (minimal(core=["coverage", "coverage"]), "twice"),
            (minimal(sections=[]), "sections must be a mapping"),
            (minimal(sections={"coverage": []}), "section 'coverage' must be a mapping"),
            (
                minimal(sections={"coverage": {"title": "C", "questions": [], "x": 1}}),
                "'x'",
            ),
            (minimal(sections={"coverage": {"title": "", "questions": [question()]}}), "title"),
            (minimal(sections={"coverage": {"title": "C", "questions": []}}), "questions"),
            (minimal(sections={"coverage": {"title": "C", "questions": ["q"]}}), "mapping"),
            (with_question(kind="slider"), "'slider'"),
            (with_question(prompt=""), "prompt"),
            (with_question(id=""), "id"),
            (with_question(colour="red"), "'colour'"),
            (with_question(id="methodology.gates.nothing"), "not a manifest key"),
            (with_question(id="methodology.gates"), "not a manifest key"),
            (with_question(key="methodology.pipeline"), "its id is its key"),
            (with_question(choices=[1, 2]), "takes no choices"),
            (with_question(choices_from="profiles"), "takes no choices"),
            (with_question(placeholders=["selector"]), "placeholders"),
            (
                with_question(kind="choice", choices=["a"], choices_from="profiles"),
                "both choices and choices_from",
            ),
            (with_question(kind="choice", choices_from="moods"), "'moods'"),
            (with_question(kind="choice", choices="a"), "choices must be a list"),
            (with_question(id="wizard", kind="step"), "no step named 'wizard'"),
            (with_question(id="docs", kind="step", key="paths.docs"), "a step writes no key"),
            (with_question(id="docs", kind="step", choices=["a"]), "a step takes no choices"),
        ],
    )
    def test_a_malformed_entry_is_refused_naming_it(self, tmp_path, body, says):
        path = write_catalogue(tmp_path, body)
        with pytest.raises(ManifestError) as caught:
            questionnaire.load_catalogue(path)
        assert says in str(caught.value)
        assert str(path) in str(caught.value)

    def test_a_question_id_used_twice_is_refused(self, tmp_path):
        body = minimal(
            core=["coverage", "again"],
            sections={
                "coverage": {"title": "C", "questions": [question()]},
                "again": {"title": "A", "questions": [question()]},
            },
        )
        with pytest.raises(ManifestError, match="twice"):
            questionnaire.load_catalogue(write_catalogue(tmp_path, body))

    def test_a_section_in_neither_list_loads_and_is_not_ordered(self, tmp_path):
        body = minimal(
            sections={
                "coverage": {"title": "C", "questions": [question()]},
                "spare": {
                    "title": "S",
                    "questions": [{"id": "session.push", "kind": "bool", "prompt": "Push?"}],
                },
            }
        )
        cat = questionnaire.load_catalogue(write_catalogue(tmp_path, body))
        assert "spare" in cat.sections
        assert [s.id for _tier, s in cat.ordered()] == ["coverage"]

    def test_fixed_choices_and_placeholders_load(self, tmp_path):
        body = minimal(
            sections={
                "coverage": {
                    "title": "C",
                    "questions": [
                        {
                            "id": "methodology.pipeline",
                            "kind": "choice",
                            "prompt": "Lane?",
                            "choices": ["tdd", "chore"],
                        },
                        {
                            "id": "commands.test_one",
                            "kind": "command",
                            "prompt": "Test one?",
                            "placeholders": ["selector"],
                        },
                    ],
                }
            }
        )
        cat = questionnaire.load_catalogue(write_catalogue(tmp_path, body))
        lane, test_one = cat.sections["coverage"].questions
        assert lane.choices == ("tdd", "chore")
        assert test_one.placeholders == ("selector",)


# -- D2: resolve ------------------------------------------------------------


class TestResolveInAnInRepoProject:
    def test_each_default_and_provenance_is_what_config_show_prints(self, toy):
        manifest = in_repo(toy, TODAYS_MANIFEST)
        questions = questionnaire.resolve(questionnaire.load_catalogue(), manifest)
        shown = {key: (value, source) for key, value, source in manifest.iter_provenance()}
        keyed = [q for q in questions if q.key is not None]
        assert keyed
        for q in keyed:
            if q.key in shown:
                assert (q.default, q.provenance) == shown[q.key], q.id

    def test_every_section_core_then_advanced(self, toy):
        manifest = in_repo(toy, TODAYS_MANIFEST)
        questions = questionnaire.resolve(questionnaire.load_catalogue(), manifest)
        sections = []
        for q in questions:
            if q.section not in sections:
                sections.append(q.section)
        assert sections == list(questionnaire.load_catalogue().core) + list(
            questionnaire.load_catalogue().advanced
        )

    def test_the_commands_step_asks_every_command_of_the_primary(self, toy):
        manifest = in_repo(toy, TODAYS_MANIFEST)
        q = by_id(questionnaire.resolve(questionnaire.load_catalogue(), manifest))
        assert q["commands:test"].key == "commands.test"
        assert q["commands:test"].kind == "command"
        assert q["commands:test"].default == "pytest"
        assert q["commands:test"].provenance == "adapter:python"
        assert q["commands:test_one"].placeholders == ("selector",)
        assert q["commands:coverage_one"].placeholders == ("selector",)
        assert q["commands:lint"].placeholders == ()
        assert q["commands:build"].default is None

    def test_a_further_ecosystem_has_its_own_command_keys(self, toy):
        manifest = in_repo(toy, "version: 1\nproject: {ecosystems: [python, node]}\n")
        q = by_id(questionnaire.resolve(questionnaire.load_catalogue(), manifest))
        assert q["commands:node.test"].key == "commands.node.test"
        assert q["commands:node.test"].provenance == "adapter:node"
        assert q["commands:test"].provenance == "adapter:python"

    def test_the_docs_step_is_location_then_onboarding(self, toy):
        manifest = in_repo(toy, TODAYS_MANIFEST)
        q = by_id(questionnaire.resolve(questionnaire.load_catalogue(), manifest))
        assert q["docs:location"].key == "paths.docs"
        assert q["docs:location"].kind == "path"
        assert q["docs:location"].default == "docs/architecture"
        assert q["docs:onboarding"].key == "docs.onboarding"
        assert q["docs:onboarding"].choices == ("off", "incremental", "all_at_once")
        assert q["docs:onboarding"].default == "off"
        assert q["docs:onboarding"].provenance == "default"

    def test_the_store_step_names_no_store_in_an_in_repo_project(self, toy):
        manifest = in_repo(toy)
        q = by_id(questionnaire.resolve(questionnaire.load_catalogue(), manifest))
        assert q["store:location"].key is None
        assert q["store:location"].default is None
        assert q["store:location"].allowed == ()

    def test_choices_are_filled_from_their_sources(self, toy):
        manifest = in_repo(toy)
        q = by_id(questionnaire.resolve(questionnaire.load_catalogue(), manifest))
        assert q["enforcement.profile"].choices == tuple(profile_names())
        assert set(q["enforcement.profile"].choices) >= {"loose", "standard", "strict"}
        assert q["methodology.pipeline"].choices == tuple(machines.pipeline_ids(manifest))
        assert "python" in q["project.ecosystems"].choices
        assert "feature-architect" in q["agents.library"].choices
        assert q["privacy.patterns"].choices == tuple(PATTERNS)
        assert q["records.docs"].choices == RECORD_LOCATIONS["docs"]
        assert q["records.hooks"].choices == RECORD_LOCATIONS["hooks"]
        assert q["methodology.gates.coverage_min"].choices is None

    def test_the_levels_allowed_in_an_in_repo_project(self, toy):
        manifest = in_repo(toy)
        q = by_id(questionnaire.resolve(questionnaire.load_catalogue(), manifest))
        assert q["methodology.gates.coverage_min"].allowed == ("user", "repo")
        assert q["project.ecosystems"].allowed == ("repo",)
        assert q["records.docs"].allowed == ("repo",)
        assert q["commands:test"].allowed == ("user", "repo")

    def test_current_is_the_target_levels_own_value(self, toy):
        write_user({"version": 1, "defaults": {"methodology": {"gates": {"coverage_min": 80}}}})
        manifest = in_repo(toy, TODAYS_MANIFEST)
        cat = questionnaire.load_catalogue()
        repo = by_id(questionnaire.resolve(cat, manifest))
        assert repo["enforcement.profile"].current == "standard"
        assert repo["enforcement.profile"].set_at_target
        assert repo["methodology.gates.coverage_min"].current is None
        assert not repo["methodology.gates.coverage_min"].set_at_target
        user = by_id(questionnaire.resolve(cat, manifest, target="user"))
        assert user["methodology.gates.coverage_min"].current == 80
        assert user["methodology.gates.coverage_min"].set_at_target
        assert not user["enforcement.profile"].set_at_target

    def test_a_target_level_that_sets_nothing_has_no_current_value(self, toy):
        manifest = in_repo(toy, TODAYS_MANIFEST)
        user = by_id(questionnaire.resolve(questionnaire.load_catalogue(), manifest, "user"))
        assert not any(q.set_at_target for q in user.values())

    def test_a_user_default_is_shown_with_its_user_label(self, toy):
        write_user({"version": 1, "defaults": {"methodology": {"gates": {"coverage_min": 80}}}})
        manifest = in_repo(toy, TODAYS_MANIFEST)
        q = by_id(questionnaire.resolve(questionnaire.load_catalogue(), manifest))
        floor = q["methodology.gates.coverage_min"]
        assert floor.default == 80
        assert floor.provenance.startswith("user:")
        assert floor.provenance == manifest.source_of("methodology.gates.coverage_min")

    def test_a_store_target_is_refused_outside_a_store_project(self, toy):
        manifest = in_repo(toy)
        with pytest.raises(ManifestError) as caught:
            questionnaire.resolve(questionnaire.load_catalogue(), manifest, target="store")
        assert "user, repo" in str(caught.value)

    def test_an_unknown_target_is_refused(self, toy):
        manifest = in_repo(toy)
        with pytest.raises(ManifestError, match="'galaxy'"):
            questionnaire.resolve(questionnaire.load_catalogue(), manifest, target="galaxy")

    def test_named_sections_only(self, toy):
        manifest = in_repo(toy)
        questions = questionnaire.resolve(
            questionnaire.load_catalogue(), manifest, sections=["lane", "coverage"]
        )
        assert [q.id for q in questions] == [
            "methodology.pipeline",
            "methodology.gates.coverage_min",
        ]

    def test_an_unknown_section_is_refused_naming_the_ones_it_holds(self, toy):
        manifest = in_repo(toy)
        with pytest.raises(ManifestError) as caught:
            questionnaire.resolve(questionnaire.load_catalogue(), manifest, sections=["nope"])
        assert "'nope'" in str(caught.value)
        assert "coverage" in str(caught.value)

    def test_to_dict_carries_every_field(self, toy):
        manifest = in_repo(toy, TODAYS_MANIFEST)
        q = by_id(questionnaire.resolve(questionnaire.load_catalogue(), manifest))
        assert q["enforcement.profile"].to_dict() == {
            "id": "enforcement.profile",
            "section": "profile",
            "key": "enforcement.profile",
            "kind": "choice",
            "prompt": "Which enforcement profile?",
            "choices": list(profile_names()),
            "placeholders": [],
            "default": "standard",
            "provenance": "repo:.claude/invariant.yaml",
            "current": "standard",
            "set_at_target": True,
            "allowed": ["user", "repo"],
        }


class TestResolveInAStoreProject:
    def test_the_targets_and_the_default(self, store_project):
        repo, _store = store_project
        manifest = Manifest.locate(repo)
        assert questionnaire.targets(manifest) == ("user", "store", "store-repo", "repo")
        assert questionnaire.default_target(manifest) == "store-repo"

    def test_a_store_value_is_shown_with_its_store_label(self, store_project):
        repo, _store = store_project
        manifest = Manifest.locate(repo)
        q = by_id(questionnaire.resolve(questionnaire.load_catalogue(), manifest))
        assert q["methodology.gates.coverage_min"].default == 70
        assert q["methodology.gates.coverage_min"].provenance == "store:widgets"
        assert q["methodology.pipeline"].default == "chore"
        assert q["methodology.pipeline"].provenance == f"store:widgets#{IDENTITY}"

    def test_current_at_each_store_target(self, store_project):
        repo, _store = store_project
        manifest = Manifest.locate(repo)
        cat = questionnaire.load_catalogue()
        top = by_id(questionnaire.resolve(cat, manifest, target="store"))
        block = by_id(questionnaire.resolve(cat, manifest, target="store-repo"))
        assert top["methodology.gates.coverage_min"].current == 70
        assert not top["methodology.pipeline"].set_at_target
        assert block["methodology.pipeline"].current == "chore"
        assert not block["methodology.gates.coverage_min"].set_at_target

    def test_the_levels_allowed_in_a_store_project(self, store_project):
        repo, _store = store_project
        manifest = Manifest.locate(repo)
        q = by_id(questionnaire.resolve(questionnaire.load_catalogue(), manifest))
        assert q["methodology.gates.coverage_min"].allowed == ("user", "store", "store-repo")
        assert q["project.ecosystems"].allowed == ("store", "store-repo")
        assert q["records.docs"].allowed == ("store", "store-repo", "repo")

    def test_the_store_step_names_the_store(self, store_project):
        repo, store = store_project
        manifest = Manifest.locate(repo)
        q = by_id(questionnaire.resolve(questionnaire.load_catalogue(), manifest))
        assert q["store:location"].default == str(store)
        assert q["store:location"].provenance == "store:widgets"


class TestAllowedLevels:
    def test_without_a_key_none(self):
        assert questionnaire.allowed_levels(None, store_project=False) == ()

    @pytest.mark.parametrize("block", PROJECT_SPECIFIC)
    def test_the_user_level_refuses_what_it_refuses_at_load(self, block):
        assert "user" not in questionnaire.allowed_levels(f"{block}.x", store_project=False)

    @pytest.mark.parametrize("key", questionnaire.USER_REFUSED_KEYS)
    def test_each_key_named_refused_at_the_user_level_is_refused_at_load(self, toy, key):
        *parents, leaf = key.split(".")
        defaults: dict = {}
        node = defaults
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = "elsewhere"
        defaults["docs"]["adopt"]["mode"] = "in_place"
        write_user({"version": 1, "defaults": defaults})
        with pytest.raises(ManifestError, match="user level"):
            in_repo(toy)
        assert questionnaire.allowed_levels(key, store_project=False) == ("repo",)


# -- the owners resolve reads -----------------------------------------------


class TestTheChoiceSources:
    def test_profile_names_are_the_shipped_profiles(self):
        assert profile_names() == ["loose", "standard", "strict"]

    def test_an_unknown_profile_still_names_them(self, toy):
        with pytest.raises(ManifestError) as caught:
            in_repo(toy, "enforcement: {profile: gentle}\n")
        assert "loose, standard, strict" in str(caught.value)

    def test_pipeline_ids_are_the_pipeline_machines_only(self, toy):
        manifest = in_repo(toy)
        ids = machines.pipeline_ids(manifest)
        assert "tdd" in ids and "chore" in ids and "docs" in ids
        assert "session" not in ids and "bug" not in ids
        assert ids == sorted(ids)

    def test_pipeline_ids_add_the_extra_machines(self, toy):
        extra = toy / "machines"
        extra.mkdir()
        tdd = (machines.MACHINE_DIR / "tdd.yaml").read_text(encoding="utf-8")
        (extra / "house.yaml").write_text(tdd.replace("id: tdd", "id: house"), encoding="utf-8")
        (toy / "solo.yaml").write_text(tdd.replace("id: tdd", "id: solo"), encoding="utf-8")
        session = (machines.MACHINE_DIR / "session.yaml").read_text(encoding="utf-8")
        (extra / "flow.yaml").write_text(session, encoding="utf-8")
        manifest = in_repo(
            toy, "methodology: {extra_machines: [machines, solo.yaml, gone.yaml]}\n"
        )
        ids = machines.pipeline_ids(manifest)
        assert "house" in ids and "solo" in ids
        assert "flow" not in ids and "gone" not in ids
        assert ids.count("tdd") == 1

    def test_pipeline_ids_without_a_manifest_are_the_shipped_ones(self):
        assert "tdd" in machines.pipeline_ids(None)

    def test_the_manifest_keeps_its_levels_lowest_first(self, store_project):
        repo, _store = store_project
        manifest = Manifest.locate(repo)
        labels = [label for label, _level in manifest.levels]
        assert labels == ["store:widgets", f"store:widgets#{IDENTITY}", "repo:.invariant.yaml"]


# -- D9: the non-interactive report ------------------------------------------


def configuration(payload: dict) -> dict[str, dict]:
    return {row["key"]: row for row in payload["configuration"]}


class TestTheReport:
    def test_core_report_lists_the_d9_keys(self, toy):
        manifest = in_repo(toy, TODAYS_MANIFEST)
        rows = questionnaire.core_report(questionnaire.load_catalogue(), manifest)
        assert [q.key for q in rows] == CORE_KEYS

    def test_core_report_lists_test_and_test_one_of_each_further_ecosystem(self, toy):
        manifest = in_repo(toy, "version: 1\nproject: {ecosystems: [python, node]}\n")
        rows = questionnaire.core_report(questionnaire.load_catalogue(), manifest)
        keys = [q.key for q in rows]
        assert keys[1:5] == [
            "commands.test",
            "commands.test_one",
            "commands.node.test",
            "commands.node.test_one",
        ]

    def test_render_aligns_each_row_and_ends_naming_configure(self, toy):
        manifest = in_repo(toy, TODAYS_MANIFEST)
        rows = questionnaire.core_report(questionnaire.load_catalogue(), manifest)
        lines = questionnaire.render_report(rows).splitlines()
        assert lines[0] == "configuration (core questions):"
        assert lines[-1] == "run 'invariant configure' to change them"
        assert lines[1].split() == ["project.ecosystems", "[python]", "repo:.claude/invariant.yaml"]
        assert lines[3].split() == ["commands.test_one", "pytest", "{selector}", "adapter:python"]
        assert lines[6].split() == ["methodology.gates.coverage_min", "90", "default"]
        starts = {line.index(line.split()[1]) for line in lines[1:-1]}
        assert len(starts) == 1

    @pytest.mark.parametrize(
        "value, shown",
        [(None, "none"), (True, "true"), (False, "false"), ([], "[]"), (["a", "b"], "[a, b]"), (7, "7")],
    )
    def test_values_are_shown_plainly(self, value, shown):
        assert questionnaire.shown(value) == shown

    def test_report_dict_is_id_key_value_provenance(self, toy):
        manifest = in_repo(toy, TODAYS_MANIFEST)
        rows = questionnaire.core_report(questionnaire.load_catalogue(), manifest)
        assert rows[0].report_dict() == {
            "id": "project.ecosystems",
            "key": "project.ecosystems",
            "value": ["python"],
            "provenance": "repo:.claude/invariant.yaml",
        }


@pytest.mark.usefixtures("no_terminal")
class TestNonInteractiveInit:
    def test_writes_todays_manifest_and_adds_the_core_list_after_the_report(
        self, toy, capsys
    ):
        code, text = run(capsys, "init", "--name", "toy")
        assert code == 0, text
        assert (toy / MANIFEST_RELPATH).read_text(encoding="utf-8") == STARTER_MANIFEST
        assessment = text.index("docs assessment:")
        listed = text.index("configuration (core questions):")
        assert assessment < listed
        assert text.rstrip().endswith("run 'invariant configure' to change them")
        for key in CORE_KEYS:
            assert key in text[listed:]

    def test_the_files_are_todays_whichever_way_the_report_is_given(
        self, tmp_path, capsys, monkeypatch
    ):
        """Text, --json, and a report that could not resolve: one tree, today's."""
        trees = []
        for name, argv in (
            ("text", ["init", "--name", "toy"]),
            ("json", ["--json", "init", "--name", "toy"]),
            ("broken", ["init", "--name", "toy"]),
        ):
            root = make_repo(tmp_path / name, origin=None)
            monkeypatch.chdir(root)
            if name == "broken":
                monkeypatch.setattr(
                    "invariant.core.questionnaire.core_report",
                    lambda *a, **k: (_ for _ in ()).throw(ManifestError("no")),
                )
            code, text = run(capsys, *argv)
            assert code == 0, text
            assert (root / MANIFEST_RELPATH).read_text(encoding="utf-8") == STARTER_MANIFEST
            assert (root / ".gitignore").read_text(encoding="utf-8") == (
                "\n# invariant runtime state: machine-local and session-scoped\n"
                ".claude/invariant/state/\n"
            )
            trees.append(tree(root))
        assert trees[0] == trees[1] == trees[2]

    def test_json_carries_the_configuration_field(self, toy, capsys):
        code, text = run(capsys, "--json", "init", "--name", "toy")
        assert code == 0, text
        payload = json.loads(text)
        assert [row["key"] for row in payload["configuration"]] == CORE_KEYS
        assert configuration(payload)["paths.docs"] == {
            "id": "docs:location",
            "key": "paths.docs",
            "value": "invariant-docs/architecture",
            "provenance": f"repo:{MANIFEST_RELPATH}",
        }
        assert (toy / MANIFEST_RELPATH).read_text(encoding="utf-8") == STARTER_MANIFEST

    def test_the_field_matches_config_show_for_every_listed_key(self, toy, capsys):
        write_user({"version": 1, "defaults": {"methodology": {"gates": {"coverage_min": 80}}}})
        code, text = run(capsys, "--json", "init", "--name", "toy")
        assert code == 0, text
        listed = configuration(json.loads(text))
        code, text = run(capsys, "--json", "config", "show")
        assert code == 0, text
        shown = json.loads(text)
        config, provenance = shown["config"], shown["provenance"]
        for key, row in listed.items():
            node = config
            for part in key.split("."):
                node = node[part]
            assert row["value"] == node, key
            assert row["provenance"] == provenance.get(key, "default"), key
        assert listed["methodology.gates.coverage_min"]["value"] == 80
        assert listed["methodology.gates.coverage_min"]["provenance"].startswith("user:")

    def test_a_user_level_floor_is_shown_with_its_user_label_in_text(self, toy, capsys):
        write_user({"version": 1, "defaults": {"methodology": {"gates": {"coverage_min": 85}}}})
        code, text = run(capsys, "init", "--name", "toy")
        assert code == 0, text
        row = next(
            line for line in text.splitlines() if line.strip().startswith("methodology.gates.coverage_min")
        )
        assert row.split()[1] == "85"
        assert row.split()[2].startswith("user:")

    def test_dry_run_writes_nothing_and_lists_what_the_manifest_would_resolve(
        self, toy, capsys
    ):
        before = tree(toy)
        code, text = run(capsys, "init", "--name", "toy", "--profile", "strict", "--dry-run")
        assert code == 0, text
        assert tree(toy) == before
        row = next(line for line in text.splitlines() if line.strip().startswith("enforcement.profile"))
        assert row.split()[1:] == ["strict", "repo:.claude/invariant.yaml"]
        code, text = run(capsys, "--json", "init", "--name", "toy", "--dry-run")
        assert code == 0, text
        assert [row["key"] for row in json.loads(text)["configuration"]] == CORE_KEYS
        assert tree(toy) == before

    def test_a_configuration_that_cannot_resolve_is_reported_not_raised(
        self, toy, capsys, monkeypatch
    ):
        def broken(path=None):
            raise ManifestError("the catalogue cannot be read")

        monkeypatch.setattr("invariant.core.questionnaire.load_catalogue", broken)
        code, text = run(capsys, "init", "--name", "toy", "--dry-run")
        assert code == 0, text
        assert "configuration not resolved: the catalogue cannot be read" in text
        code, text = run(capsys, "--json", "init", "--name", "toy")
        assert code == 0, text
        assert "cannot be read" in json.loads(text)["configuration"]["error"]

    def test_a_manifest_that_cannot_load_is_reported_by_both(self, toy, capsys, monkeypatch):
        def broken(*args, **kwargs):
            raise ManifestError("the manifest cannot be read")

        monkeypatch.setattr("invariant.core.manifest.Manifest.locate", broken)
        code, text = run(capsys, "--json", "init", "--name", "toy")
        assert code == 0, text
        payload = json.loads(text)
        assert "cannot be read" in payload["configuration"]["error"]
        assert "cannot be read" in payload["docs_assessment"]["error"]


class TestInteractiveInitIsUnchanged:
    def test_no_configuration_list_on_a_terminal(self, toy, capsys, monkeypatch):
        class Accepting:
            def say(self, text):
                pass

            def choose(self, question, options, default):
                return default

            def ask(self, question, default=""):
                return default

        monkeypatch.setattr("invariant.cli.main._init_prompter", lambda args: Accepting())
        code, text = run(capsys, "init", "--name", "toy")
        assert code == 0, text
        assert "configuration (core questions):" not in text
        assert (toy / MANIFEST_RELPATH).read_text(encoding="utf-8") == STARTER_MANIFEST
