"""ADR-014 D2 and D3: Manifest.locate, stores, the pointer and the levels.

`locate` walks up from where it starts. At each level an in-repo manifest
answers first, exactly as before; otherwise a git top level whose identity
has a store (the pointer, then the user level's `repos:`), or which carries a
pointer, is the project root. The nearest answer wins. A store must list the
repo's identity under `store.repos`. Every host and path here is synthetic,
and INVARIANT_HOME is the suite's temporary home (conftest).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.core.errors import ManifestError
from invariant.core.home import load_user_config, user_config_path
from invariant.core.manifest import MANIFEST_RELPATH, Manifest

ORIGIN = "https://host.example/team/widgets.git"
IDENTITY = "host.example/team/widgets"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(path: Path, origin: str | None = ORIGIN) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.invalid")
    git(path, "config", "user.name", "T")
    (path / "conf.py").write_text("x = 1\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "init")
    if origin:
        git(path, "remote", "add", "origin", origin)
    return path


def make_store(path: Path, repos: dict | None = None, **body) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    data = {"version": 1, **body}
    data.setdefault("store", {})["repos"] = repos if repos is not None else {IDENTITY: {}}
    (path / "invariant.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def link_in_user_config(identity: str, store: Path, **extra) -> None:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"version": 1, "repos": {identity: {"store": str(store)}}, **extra}
    path.write_text(yaml.safe_dump(body), encoding="utf-8")


def write_pointer(repo: Path, body: dict) -> Path:
    path = repo / ".invariant.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


@pytest.fixture
def linked(tmp_path):
    """A repo with an origin, linked from the user level to a store."""
    repo = make_repo(tmp_path / "widgets")
    store = make_store(
        tmp_path / "stores" / "widgets",
        project={"name": "widgets", "ecosystems": ["python"]},
        records={"docs": "store", "backlog": "store"},
    )
    link_in_user_config(IDENTITY, store)
    return repo, store


class TestInRepo:
    def test_found_from_a_subdirectory_with_its_label(self, tmp_path):
        (tmp_path / MANIFEST_RELPATH).parent.mkdir(parents=True)
        (tmp_path / MANIFEST_RELPATH).write_text(
            "project: {name: demo}\n", encoding="utf-8"
        )
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        m = Manifest.locate(deep)
        assert m.root == tmp_path.resolve()
        assert m.located
        assert m.store is None
        assert m.source_of("project.name") == "repo:.claude/invariant.yaml"

    def test_nothing_found_names_both_ways_in(self, tmp_path):
        with pytest.raises(ManifestError) as caught:
            Manifest.locate(tmp_path)
        assert "invariant init" in str(caught.value)
        assert "invariant store link" in str(caught.value)

    def test_nothing_found_and_not_required(self, tmp_path):
        m = Manifest.locate(tmp_path, required=False)
        assert not m.located
        assert m.root == tmp_path.resolve()

    def test_a_plain_git_repo_inside_a_project_is_walked_past(self, tmp_path):
        (tmp_path / MANIFEST_RELPATH).parent.mkdir(parents=True)
        (tmp_path / MANIFEST_RELPATH).write_text("version: 1\n", encoding="utf-8")
        inner = make_repo(tmp_path / "vendor" / "lib", origin=None)
        assert Manifest.locate(inner).root == tmp_path.resolve()

    def test_an_in_repo_manifest_wins_over_a_link(self, linked):
        repo, _store = linked
        (repo / MANIFEST_RELPATH).parent.mkdir(parents=True)
        (repo / MANIFEST_RELPATH).write_text("version: 1\n", encoding="utf-8")
        m = Manifest.locate(repo)
        assert m.store is None
        assert m.path == repo / MANIFEST_RELPATH


class TestALinkedStore:
    def test_the_project_root_is_the_repo_and_the_values_come_from_the_store(
        self, linked
    ):
        repo, store = linked
        m = Manifest.locate(repo / ".")
        assert m.root == repo.resolve()
        assert m.path == store / "invariant.yaml"
        assert m.store.root == store
        assert m.store.name == "widgets"
        assert m.store.key == IDENTITY
        assert m.store.via == "user"
        assert m.store_root == store
        assert m.get("project.name") == "widgets"
        assert m.source_of("project.name") == "store:widgets"
        assert m.source_of("commands.test") == "adapter:python"
        assert m.get("paths.docs") == str(store / "docs/architecture")
        assert m.get("items.path") == str(store / "docs/backlog")
        assert m.get("paths.reports") == "docs/reports"

    def test_the_state_root_stays_the_repos(self, linked):
        repo, _store = linked
        assert Manifest.locate(repo).state_root == (repo / ".git" / "invariant" / "state").resolve()

    def test_found_from_a_subdirectory_and_a_linked_worktree(self, linked, tmp_path):
        repo, store = linked
        (repo / "sub").mkdir()
        assert Manifest.locate(repo / "sub").root == repo.resolve()
        worktree = tmp_path / "wt"
        git(repo, "worktree", "add", "-q", str(worktree), "-b", "side")
        m = Manifest.locate(worktree)
        assert m.root == worktree.resolve()
        assert m.store.root == store

    def test_the_per_repo_block_is_its_own_level(self, tmp_path):
        repo = make_repo(tmp_path / "widgets")
        store = make_store(
            tmp_path / "s",
            repos={IDENTITY: {"methodology": {"gates": {"coverage_min": 60}}}},
            methodology={"gates": {"coverage_min": 80}},
        )
        link_in_user_config(IDENTITY, store)
        m = Manifest.locate(repo)
        assert m.get("methodology.gates.coverage_min") == 60
        assert m.source_of("methodology.gates.coverage_min") == f"store:s#{IDENTITY}"

    def test_user_defaults_sit_below_the_store(self, tmp_path):
        repo = make_repo(tmp_path / "widgets")
        store = make_store(tmp_path / "s", methodology={"gates": {"coverage_min": 80}})
        link_in_user_config(
            IDENTITY,
            store,
            defaults={
                "methodology": {"gates": {"coverage_min": 70}},
                "session": {"plan_stale_days": 9},
            },
        )
        m = Manifest.locate(repo)
        assert m.get("methodology.gates.coverage_min") == 80
        assert m.get("session.plan_stale_days") == 9
        assert m.source_of("session.plan_stale_days") == load_user_config().label

    def test_a_store_not_listing_the_repo_is_refused_naming_both(self, tmp_path):
        repo = make_repo(tmp_path / "widgets")
        store = make_store(tmp_path / "s", repos={"host.example/team/other": {}})
        link_in_user_config(IDENTITY, store)
        with pytest.raises(ManifestError) as caught:
            Manifest.locate(repo)
        text = str(caught.value)
        assert IDENTITY in text
        assert str(store) in text
        assert "store.repos" in text
        assert caught.value.project_root == repo.resolve()

    def test_a_linked_store_that_is_gone_is_refused(self, tmp_path):
        repo = make_repo(tmp_path / "widgets")
        link_in_user_config(IDENTITY, tmp_path / "nowhere")
        with pytest.raises(ManifestError, match="does not exist"):
            Manifest.locate(repo)

    def test_a_store_with_no_manifest_is_refused(self, tmp_path):
        repo = make_repo(tmp_path / "widgets")
        (tmp_path / "s").mkdir()
        link_in_user_config(IDENTITY, tmp_path / "s")
        with pytest.raises(ManifestError, match="invariant.yaml"):
            Manifest.locate(repo)

    def test_a_link_spelled_with_a_tilde_is_expanded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = make_repo(tmp_path / "widgets")
        store = make_store(tmp_path / "stores" / "w")
        path = user_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"repos": {IDENTITY: {"store": "~/stores/w"}}}), encoding="utf-8"
        )
        assert Manifest.locate(repo).store.root == store

    @pytest.mark.parametrize(
        ("body", "needle"),
        [
            ({"store_naming": "url"}, "store_naming"),
            ({"store": {"repos": {IDENTITY: {}}, "publish": True}}, "store.publish"),
            ({"store": {"repos": [IDENTITY]}}, "store.repos"),
            ({"store": {"repos": {IDENTITY: "x"}}}, IDENTITY),
            ({"store": "x"}, "store must be a mapping"),
        ],
    )
    def test_a_malformed_store_level_is_refused(self, tmp_path, body, needle):
        repo = make_repo(tmp_path / "widgets")
        (tmp_path / "s").mkdir()
        (tmp_path / "s" / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
        link_in_user_config(IDENTITY, tmp_path / "s")
        with pytest.raises(ManifestError, match=needle):
            Manifest.locate(repo)

    def test_a_store_level_that_is_no_mapping_is_refused(self, tmp_path):
        repo = make_repo(tmp_path / "widgets")
        (tmp_path / "s").mkdir()
        (tmp_path / "s" / "invariant.yaml").write_text("- a\n", encoding="utf-8")
        link_in_user_config(IDENTITY, tmp_path / "s")
        with pytest.raises(ManifestError, match="mapping"):
            Manifest.locate(repo)

    def test_a_baseline_in_a_store_project_is_refused_until_d9(self, tmp_path):
        """The shrink gate reads the base's manifest from the repo; in a store
        project there is none, and the gate would admit every entry."""
        repo = make_repo(tmp_path / "widgets")
        store = make_store(tmp_path / "s", guidelines={"baseline": "g/baseline.txt"})
        link_in_user_config(IDENTITY, store)
        with pytest.raises(ManifestError, match="increment 4"):
            Manifest.locate(repo)

    def test_the_nearest_answer_wins(self, linked):
        """A linked repo nested inside an in-repo project finds its store."""
        repo, store = linked
        outer = repo.parent
        (outer / MANIFEST_RELPATH).parent.mkdir(parents=True)
        (outer / MANIFEST_RELPATH).write_text("version: 1\n", encoding="utf-8")
        assert Manifest.locate(repo).store.root == store
        assert Manifest.locate(outer).store is None


class TestTheLocalIdFallback:
    def test_a_repo_linked_by_its_local_id_resolves_after_a_remote_is_added(
        self, tmp_path
    ):
        repo = make_repo(tmp_path / "widgets", origin=None)
        git(repo, "config", "--local", "invariant.id", "local:widgets-0123abcd")
        store = make_store(tmp_path / "s", repos={"local:widgets-0123abcd": {}})
        link_in_user_config("local:widgets-0123abcd", store)
        assert Manifest.locate(repo).store.mismatch is False
        git(repo, "remote", "add", "origin", ORIGIN)
        m = Manifest.locate(repo)
        assert m.store.root == store
        assert m.store.key == "local:widgets-0123abcd"
        assert m.store.mismatch is True


class TestThePointer:
    def test_a_pointer_names_its_store_relative_to_the_repo(self, tmp_path):
        repo = make_repo(tmp_path / "widgets")
        store = make_store(tmp_path / "s")
        write_pointer(repo, {"version": 1, "store": "../s", "records": {"docs": "store"}})
        m = Manifest.locate(repo)
        assert m.store.root == store.resolve()
        assert m.store.via == "pointer"
        assert m.path == repo / ".invariant.yaml"
        assert m.source_of("records.docs") == "repo:.invariant.yaml"

    def test_a_store_listing_the_repo_by_its_local_id_is_read_through_a_pointer(
        self, tmp_path
    ):
        repo = make_repo(tmp_path / "widgets")
        git(repo, "config", "--local", "invariant.id", "local:widgets-0123abcd")
        store = make_store(tmp_path / "s", repos={"local:widgets-0123abcd": {}})
        write_pointer(repo, {"store": "../s"})
        m = Manifest.locate(repo)
        assert m.store.key == "local:widgets-0123abcd"
        assert m.store.root == store.resolve()

    def test_an_empty_pointer_is_a_pointer(self, tmp_path):
        repo = make_repo(tmp_path / "widgets", origin=None)
        (repo / ".invariant.yaml").write_text("", encoding="utf-8")
        assert Manifest.locate(repo).located

    def test_an_empty_store_lists_nothing(self, tmp_path):
        repo = make_repo(tmp_path / "widgets")
        (tmp_path / "s").mkdir()
        (tmp_path / "s" / "invariant.yaml").write_text("", encoding="utf-8")
        write_pointer(repo, {"store": "../s"})
        with pytest.raises(ManifestError, match="store.repos"):
            Manifest.locate(repo)

    def test_the_pointer_wins_over_the_user_level(self, linked, tmp_path):
        repo, _store = linked
        other = make_store(tmp_path / "other")
        write_pointer(repo, {"store": str(other)})
        assert Manifest.locate(repo).store.root == other

    def test_a_pointer_with_no_store_uses_the_link(self, linked):
        repo, store = linked
        write_pointer(repo, {"records": {"reports": "store"}})
        m = Manifest.locate(repo)
        assert m.store.root == store
        assert m.store.via == "user"
        assert m.get("paths.reports") == str(store / "docs/reports")

    def test_a_pointer_alone_makes_a_project(self, tmp_path):
        repo = make_repo(tmp_path / "widgets", origin=None)
        write_pointer(repo, {"version": 1})
        m = Manifest.locate(repo)
        assert m.located
        assert m.root == repo.resolve()
        assert m.store is None

    @pytest.mark.parametrize("key", ["enforcement", "commands", "project", "privacy"])
    def test_a_pointer_carrying_policy_is_refused(self, tmp_path, key):
        repo = make_repo(tmp_path / "widgets")
        write_pointer(repo, {key: {}})
        with pytest.raises(ManifestError, match=f"'{key}'") as caught:
            Manifest.locate(repo)
        assert "pointer" in str(caught.value)

    def test_a_pointer_naming_a_remote_is_refused_until_clone_exists(self, tmp_path):
        repo = make_repo(tmp_path / "widgets")
        write_pointer(repo, {"store": "git@host.example:team/store.git"})
        with pytest.raises(ManifestError, match="increment 5"):
            Manifest.locate(repo)

    def test_a_pointer_naming_a_store_with_no_identity_is_refused(self, tmp_path):
        repo = make_repo(tmp_path / "widgets", origin=None)
        make_store(tmp_path / "s")
        write_pointer(repo, {"store": "../s"})
        with pytest.raises(ManifestError, match="store link"):
            Manifest.locate(repo)

    @pytest.mark.parametrize(
        ("text", "needle"),
        [("store: [x\n", "not valid YAML"), ("- a\n", "mapping"), ("store: ''\n", "store")],
    )
    def test_a_malformed_pointer_is_refused(self, tmp_path, text, needle):
        repo = make_repo(tmp_path / "widgets")
        (repo / ".invariant.yaml").write_text(text, encoding="utf-8")
        with pytest.raises(ManifestError, match=needle):
            Manifest.locate(repo)


class TestQuestionsAskedOfLocate:
    def test_is_project_root(self, linked, tmp_path):
        repo, _store = linked
        assert Manifest.is_project_root(repo)
        assert not Manifest.is_project_root(repo / "missing")
        (repo / "sub").mkdir()
        assert not Manifest.is_project_root(repo / "sub")
        in_repo = tmp_path / "plain"
        (in_repo / ".claude").mkdir(parents=True)
        (in_repo / MANIFEST_RELPATH).write_text("{{ not yaml", encoding="utf-8")
        assert Manifest.is_project_root(in_repo)

    def test_a_project_whose_store_refuses_is_still_a_project(self, tmp_path):
        repo = make_repo(tmp_path / "widgets")
        link_in_user_config(IDENTITY, tmp_path / "nowhere")
        assert Manifest.is_project_root(repo)

    def test_set_by_project(self, linked):
        repo, _store = linked
        m = Manifest.locate(repo)
        assert m.set_by_project("project.name")
        assert not m.set_by_project("session.plan_stale_days")

    def test_a_refusal_names_the_file_it_read(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        (tmp_path / MANIFEST_RELPATH).write_text("items: {backend: x}\n", encoding="utf-8")
        with pytest.raises(ManifestError) as caught:
            Manifest.locate(tmp_path)
        assert caught.value.project_root == tmp_path.resolve()
        assert caught.value.manifest_path == tmp_path.resolve() / MANIFEST_RELPATH

    def test_a_from_raw_level_is_labelled_by_its_file(self, tmp_path):
        m = Manifest.from_raw({"project": {"name": "x"}}, root=tmp_path, path=tmp_path / "m.yaml")
        assert m.source_of("project.name") == "repo:m.yaml"
        assert m.set_by_project("project.name")
