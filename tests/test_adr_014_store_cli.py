"""ADR-014 D2 and D4: `invariant store init`, `store link` and `store status`.

A store is created and linked from the home side: the repo itself gains no
tracked file. Link writes the user level's `repos:` entry and the repo's
`invariant.id` (even when it has a remote); the linked-identity index the
user-level shim of D11 was to read is gone with that shim (ADR-020 D9).
Naming follows ruling 14. The
home is the suite's temporary one; nothing reaches the real ~/.invariant.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.cli.main import main
from invariant.core.home import invariant_home, user_config_path
from invariant.core.identity import repo_identity
from invariant.core.manifest import Manifest

ORIGIN = "https://host.example/team/widgets.git"
IDENTITY = "host.example/team/widgets"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "widgets"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    (root / "conf.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    git(root, "remote", "add", "origin", ORIGIN)
    monkeypatch.chdir(root)
    return root


def run(capsys, *argv) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def user_config() -> dict:
    return yaml.safe_load(user_config_path().read_text(encoding="utf-8")) or {}


def no_index() -> bool:
    """The linked-identity index of ADR-014 D11's user-level shim is gone
    (ADR-020 D9, increment 3): nothing reads it, so nothing writes it."""
    return not (invariant_home() / "linked-repos.txt").exists()


def stores() -> Path:
    return invariant_home() / "stores"


def existing_store(path: Path, identity: str) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    (path / "invariant.yaml").write_text(
        yaml.safe_dump({"version": 1, "store": {"repos": {identity: {}}}}), encoding="utf-8"
    )
    return path


class TestStoreInit:
    def test_creates_links_and_leaves_the_repo_clean(self, repo, capsys):
        code, text = run(capsys, "store", "init")
        assert code == 0, text
        store = stores() / "widgets"
        assert (store / ".git").is_dir()
        body = yaml.safe_load((store / "invariant.yaml").read_text(encoding="utf-8"))
        assert body["store"]["repos"] == {IDENTITY: {}}
        assert body["project"]["name"] == "widgets"
        assert str(store) in text
        assert Path(user_config()["repos"][IDENTITY]["store"]).expanduser() == store
        found = repo_identity(repo)
        assert found.key == IDENTITY
        assert found.local_id.startswith("local:widgets-")
        assert no_index()
        assert git(repo, "status", "--porcelain") == ""
        m = Manifest.locate(repo)
        assert m.store.root == store
        assert m.source_of("project.name") == "store:widgets"

    def test_a_repo_with_no_remote_is_named_after_its_directory(self, repo, capsys):
        git(repo, "remote", "remove", "origin")
        code, text = run(capsys, "store", "init")
        assert code == 0, text
        found = repo_identity(repo)
        assert found.source == "local"
        assert (stores() / "widgets" / "invariant.yaml").is_file()
        assert found.key in user_config()["repos"]

    def test_a_collision_falls_back_to_the_url_name_and_says_so(self, repo, capsys):
        existing_store(stores() / "widgets", "host.example/other/widgets")
        code, text = run(capsys, "store", "init")
        assert code == 0, text
        fallback = stores() / "host.example_team_widgets"
        assert (fallback / "invariant.yaml").is_file()
        assert str(stores() / "widgets") in text
        assert str(fallback) in text
        assert "already" in text

    def test_a_collision_with_no_remote_falls_back_to_the_local_id(self, repo, capsys):
        git(repo, "remote", "remove", "origin")
        (stores() / "widgets").mkdir(parents=True)
        code, text = run(capsys, "store", "init")
        assert code == 0, text
        local = repo_identity(repo).local_id
        assert (stores() / local.replace(":", "_") / "invariant.yaml").is_file()

    def test_url_naming_from_the_user_level(self, repo, capsys):
        user_config_path().parent.mkdir(parents=True, exist_ok=True)
        user_config_path().write_text("store_naming: url\n", encoding="utf-8")
        code, text = run(capsys, "store", "init")
        assert code == 0, text
        assert (stores() / "host.example_team_widgets" / "invariant.yaml").is_file()
        assert user_config()["store_naming"] == "url"

    def test_both_names_taken_is_refused(self, repo, capsys):
        (stores() / "widgets").mkdir(parents=True)
        (stores() / "host.example_team_widgets").mkdir(parents=True)
        code, text = run(capsys, "store", "init")
        assert code != 0
        assert "host.example_team_widgets" in text

    def test_an_explicit_path(self, repo, capsys, tmp_path):
        target = tmp_path / "elsewhere" / "specs"
        code, text = run(capsys, "store", "init", str(target))
        assert code == 0, text
        assert (target / "invariant.yaml").is_file()
        assert Manifest.locate(repo).store.name == "specs"

    def test_an_explicit_path_already_serving_this_repo_is_refused(
        self, repo, capsys, tmp_path
    ):
        existing_store(tmp_path / "s", IDENTITY)
        code, text = run(capsys, "store", "init", str(tmp_path / "s"))
        assert code != 0
        assert "store link" in text

    def test_an_explicit_path_holding_anything_else_is_refused(self, repo, capsys, tmp_path):
        (tmp_path / "s").mkdir()
        (tmp_path / "s" / "notes.txt").write_text("mine\n", encoding="utf-8")
        code, text = run(capsys, "store", "init", str(tmp_path / "s"))
        assert code != 0
        assert "not empty" in text

    def test_an_existing_store_for_this_repo_is_refused_naming_link(self, repo, capsys):
        existing_store(stores() / "widgets", IDENTITY)
        code, text = run(capsys, "store", "init")
        assert code != 0
        assert "store link" in text

    def test_an_in_repo_project_is_refused(self, repo, capsys):
        (repo / ".claude").mkdir()
        (repo / ".claude" / "invariant.yaml").write_text("version: 1\n", encoding="utf-8")
        code, text = run(capsys, "store", "init")
        assert code != 0
        assert "in-repo" in text

    def test_outside_a_repository_is_refused(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        code, text = run(capsys, "store", "init")
        assert code != 0
        assert "git" in text


class TestStoreLink:
    def test_links_an_existing_store_and_writes_the_id_beside_the_remote(
        self, repo, capsys, tmp_path
    ):
        store = existing_store(tmp_path / "s", IDENTITY)
        code, text = run(capsys, "store", "link", str(store))
        assert code == 0, text
        assert Path(user_config()["repos"][IDENTITY]["store"]).expanduser() == store
        found = repo_identity(repo)
        assert found.origin == IDENTITY
        assert found.local_id is not None
        assert no_index()
        assert Manifest.locate(repo).store.root == store

    def test_linking_again_is_idempotent_and_keeps_the_id(self, repo, capsys, tmp_path):
        store = existing_store(tmp_path / "s", IDENTITY)
        run(capsys, "store", "link", str(store))
        first = repo_identity(repo).local_id
        code, _ = run(capsys, "store", "link", str(store))
        assert code == 0
        assert repo_identity(repo).local_id == first
        assert no_index()

    def test_other_user_level_keys_are_kept(self, repo, capsys, tmp_path):
        user_config_path().parent.mkdir(parents=True, exist_ok=True)
        user_config_path().write_text(
            "defaults:\n  session:\n    plan_stale_days: 9\n", encoding="utf-8"
        )
        store = existing_store(tmp_path / "s", IDENTITY)
        assert run(capsys, "store", "link", str(store))[0] == 0
        assert user_config()["defaults"] == {"session": {"plan_stale_days": 9}}

    def test_a_store_not_listing_the_repo_is_refused_with_the_line_to_add(
        self, repo, capsys, tmp_path
    ):
        store = existing_store(tmp_path / "s", "host.example/team/other")
        code, text = run(capsys, "store", "link", str(store))
        assert code != 0
        assert IDENTITY in text
        assert str(store) in text
        assert not user_config_path().exists()

    def test_a_path_that_is_no_store_is_refused(self, repo, capsys, tmp_path):
        code, text = run(capsys, "store", "link", str(tmp_path / "nothing"))
        assert code != 0
        assert "invariant.yaml" in text

    def test_a_repo_linked_elsewhere_is_refused(self, repo, capsys, tmp_path):
        first = existing_store(tmp_path / "a", IDENTITY)
        second = existing_store(tmp_path / "b", IDENTITY)
        run(capsys, "store", "link", str(first))
        code, text = run(capsys, "store", "link", str(second))
        assert code != 0
        assert str(first) in text

    def test_update_rekeys_a_local_link_to_the_new_origin(self, repo, capsys, tmp_path):
        git(repo, "remote", "remove", "origin")
        code, _ = run(capsys, "store", "init", str(tmp_path / "s"))
        assert code == 0
        local = repo_identity(repo).local_id
        git(repo, "remote", "add", "origin", ORIGIN)
        assert Manifest.locate(repo).store.mismatch
        code, text = run(capsys, "doctor")
        row = next(line for line in text.splitlines() if "repo identity" in line)
        assert "store link --update" in row
        # The store must list the new identity before the link can move.
        code, text = run(capsys, "store", "link", "--update")
        assert code != 0
        assert IDENTITY in text
        body = yaml.safe_load((tmp_path / "s" / "invariant.yaml").read_text(encoding="utf-8"))
        body["store"]["repos"][IDENTITY] = {}
        (tmp_path / "s" / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
        code, text = run(capsys, "store", "link", "--update")
        assert code == 0, text
        assert IDENTITY in user_config()["repos"]
        assert local not in user_config()["repos"]
        assert not Manifest.locate(repo).store.mismatch

    def test_update_with_nothing_to_move_is_refused(self, repo, capsys, tmp_path):
        store = existing_store(tmp_path / "s", IDENTITY)
        run(capsys, "store", "link", str(store))
        code, text = run(capsys, "store", "link", "--update")
        assert code != 0
        assert "nothing to update" in text

    def test_a_path_is_required_without_update(self, repo, capsys):
        code, text = run(capsys, "store", "link")
        assert code != 0
        assert "path" in text.lower()


class TestStoreStatus:
    def test_a_linked_repo(self, repo, capsys):
        run(capsys, "store", "init")
        code, text = run(capsys, "store", "status")
        assert code == 0
        assert IDENTITY in text
        assert "origin" in text
        assert str(stores() / "widgets") in text
        code, text = run(capsys, "store", "status", "--json")
        payload = json.loads(text[text.index("{"):])
        assert payload["linked"] is True
        assert payload["identity"] == IDENTITY

    def test_quiet_passes_silently_in_a_linked_repo(self, repo, capsys):
        run(capsys, "store", "init")
        assert run(capsys, "store", "status", "--quiet") == (0, "")

    def test_an_unlinked_repo_fails(self, repo, capsys):
        code, text = run(capsys, "store", "status")
        assert code == 1
        assert "not linked" in text
        assert run(capsys, "store", "status", "--quiet") == (1, "")

    def test_an_in_repo_project_fails(self, repo, capsys):
        (repo / ".claude").mkdir()
        (repo / ".claude" / "invariant.yaml").write_text("version: 1\n", encoding="utf-8")
        code, text = run(capsys, "store", "status")
        assert code == 1
        assert "in-repo" in text

    def test_a_refused_link_fails_quietly_and_names_why_aloud(self, repo, capsys, tmp_path):
        user_config_path().parent.mkdir(parents=True, exist_ok=True)
        user_config_path().write_text(
            yaml.safe_dump({"repos": {IDENTITY: {"store": str(tmp_path / "gone")}}}),
            encoding="utf-8",
        )
        assert run(capsys, "store", "status", "--quiet") == (1, "")
        code, text = run(capsys, "store", "status")
        assert code == 1
        assert "does not exist" in text


class TestDoctorAndConfigShow:
    def test_doctor_names_the_identity_and_store(self, repo, capsys):
        run(capsys, "store", "init")
        _code, text = run(capsys, "doctor")
        row = next(line for line in text.splitlines() if "repo identity" in line)
        assert IDENTITY in row
        assert "widgets" in row

    def test_config_show_labels_the_store_level(self, repo, capsys):
        run(capsys, "store", "init")
        _code, text = run(capsys, "config", "show", "--provenance")
        row = next(line for line in text.splitlines() if "project.name" in line)
        assert "[store:widgets]" in row
