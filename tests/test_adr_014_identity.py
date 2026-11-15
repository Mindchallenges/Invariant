"""ADR-014 D4: a repository's identity, read without a subprocess.

The identity is the key a store is found by: the normalised origin URL first,
so a clone on another machine finds the same store, else a generated
``invariant.id`` in the repository's local git config. Every host and path in
this file is synthetic.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from invariant.core.errors import StateError
from invariant.core.identity import (
    LOCAL_ID_KEY,
    Identity,
    common_dir_of,
    new_local_id,
    normalise_origin,
    read_git_config,
    repo_identity,
    write_local_id,
)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "Widgets Repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


class TestNormaliseOrigin:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/Example/Widgets.git",
            "git@github.com:Example/Widgets.git",
            "ssh://git@github.com:22/Example/Widgets/",
            "https://someone@GitHub.com/Example/Widgets",
            "https://github.com/Example/Widgets.git/",
        ],
    )
    def test_the_spellings_of_one_origin_reduce_to_one_identity(self, url):
        assert normalise_origin(url) == "github.com/example/widgets"

    @pytest.mark.parametrize("host", ["gitlab.com", "bitbucket.org"])
    def test_the_other_case_insensitive_hosts_lowercase_the_path(self, host):
        assert normalise_origin(f"https://{host}/Team/Repo.git") == f"{host}/team/repo"

    def test_any_other_host_keeps_the_path_case(self):
        assert (
            normalise_origin("https://Git.Example.org/Team/Repo.git")
            == "git.example.org/Team/Repo"
        )

    def test_a_port_is_dropped(self):
        assert (
            normalise_origin("ssh://git@host.example:2222/a/b.git") == "host.example/a/b"
        )

    def test_scp_form_without_a_user(self):
        assert normalise_origin("host.example:team/repo") == "host.example/team/repo"

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "   ",
            "/srv/git/repo.git",
            "./relative/repo",
            "../sibling.git",
            "file:///srv/git/repo.git",
            "https://host.example/",
            "https://host.example/.git",
            None,
        ],
    )
    def test_an_origin_naming_no_host_and_path_has_no_identity(self, url):
        """A local path names a place on one machine, so it is no identity."""
        assert normalise_origin(url) is None


class TestReadGitConfig:
    def test_sections_subsections_quotes_and_comments(self, tmp_path):
        config = tmp_path / "config"
        config.write_text(
            "# a comment\n"
            "[core]\n"
            "\tbare = false\n"
            "[remote \"origin\"]\n"
            "\turl = \"https://host.example/a/b.git\" ; trailing\n"
            "\tfetch = +refs/heads/*:refs/remotes/origin/*\n"
            "[Invariant]\n"
            "\tID = local:x-0123abcd\n"
            "[branch.Main]\n"
            "\tflag\n"
            "not a line git would write\n"
            "\tescaped = \"a\\\"b\\\\c\\td\\ne\"\n",
            encoding="utf-8",
        )
        parsed = read_git_config(config)
        assert parsed[("remote", "origin")]["url"] == "https://host.example/a/b.git"
        assert parsed[("invariant", None)]["id"] == "local:x-0123abcd"
        assert parsed[("branch", "main")]["flag"] == "true"
        assert parsed[("branch", "main")]["escaped"] == 'a"b\\c\td\ne'

    def test_the_last_value_wins(self, tmp_path):
        config = tmp_path / "config"
        config.write_text("[a]\n k = 1\n[a]\n k = 2\n", encoding="utf-8")
        assert read_git_config(config)[("a", None)]["k"] == "2"

    def test_an_unreadable_config_reads_as_empty(self, tmp_path):
        assert read_git_config(tmp_path / "absent") == {}


class TestCommonDir:
    def test_the_main_checkout(self, repo):
        assert common_dir_of(repo) == (repo / ".git").resolve()

    def test_a_linked_worktree_reaches_the_shared_directory(self, repo, tmp_path):
        worktree = tmp_path / "wt"
        git(repo, "worktree", "add", "-q", str(worktree), "-b", "side")
        assert common_dir_of(worktree) == (repo / ".git").resolve()

    def test_a_directory_that_is_no_git_top_level(self, tmp_path):
        assert common_dir_of(tmp_path) is None

    def test_a_dot_git_file_naming_nothing(self, tmp_path):
        (tmp_path / ".git").write_text("not a gitdir line\n", encoding="utf-8")
        assert common_dir_of(tmp_path) is None

    def test_a_relative_gitdir_without_a_commondir(self, tmp_path):
        """A separated git directory (`git init --separate-git-dir`)."""
        real = tmp_path / "store.git"
        real.mkdir()
        top = tmp_path / "top"
        top.mkdir()
        (top / ".git").write_text("gitdir: ../store.git\n", encoding="utf-8")
        assert common_dir_of(top) == real.resolve()


class TestRepoIdentity:
    def test_no_remote_and_no_id_has_no_identity(self, repo):
        assert repo_identity(repo) is None

    def test_the_origin_is_the_key(self, repo):
        git(repo, "remote", "add", "origin", "git@github.com:Example/Widgets.git")
        found = repo_identity(repo)
        assert found == Identity(
            key="github.com/example/widgets",
            origin="github.com/example/widgets",
            local_id=None,
        )
        assert found.source == "origin"

    def test_the_local_id_is_the_key_with_no_remote(self, repo):
        git(repo, "config", "--local", LOCAL_ID_KEY, "local:widgets-0123abcd")
        found = repo_identity(repo)
        assert found.key == "local:widgets-0123abcd"
        assert found.source == "local"

    def test_the_origin_wins_over_the_local_id(self, repo):
        git(repo, "remote", "add", "origin", "https://host.example/a/b.git")
        git(repo, "config", "--local", LOCAL_ID_KEY, "local:widgets-0123abcd")
        found = repo_identity(repo)
        assert found.key == "host.example/a/b"
        assert found.local_id == "local:widgets-0123abcd"

    def test_a_remote_naming_a_local_path_falls_back_to_the_id(self, repo, tmp_path):
        git(repo, "remote", "add", "origin", str(tmp_path / "elsewhere.git"))
        assert repo_identity(repo) is None

    def test_a_linked_worktree_has_its_repository_identity(self, repo, tmp_path):
        git(repo, "remote", "add", "origin", "https://host.example/a/b.git")
        worktree = tmp_path / "wt"
        git(repo, "worktree", "add", "-q", str(worktree), "-b", "side")
        assert repo_identity(worktree).key == "host.example/a/b"

    def test_it_is_read_without_a_subprocess(self, repo, monkeypatch):
        git(repo, "remote", "add", "origin", "https://host.example/a/b.git")

        def refuse(*args, **kwargs):
            raise AssertionError("identity must be read without a subprocess")

        monkeypatch.setattr(subprocess, "run", refuse)
        monkeypatch.setattr(subprocess, "Popen", refuse)
        assert repo_identity(repo).key == "host.example/a/b"

    def test_outside_a_repository(self, tmp_path):
        assert repo_identity(tmp_path) is None


class TestLocalId:
    def test_the_shape(self):
        assert re.fullmatch(r"local:widgets-repo-[0-9a-f]{8}", new_local_id("Widgets Repo"))

    def test_a_name_with_nothing_to_slug(self):
        assert re.fullmatch(r"local:repo-[0-9a-f]{8}", new_local_id("!!!"))

    def test_two_ids_differ(self):
        assert new_local_id("x") != new_local_id("x")

    def test_written_into_the_local_config_and_read_back(self, repo):
        value = new_local_id(repo.name)
        write_local_id(repo, value)
        assert repo_identity(repo).key == value
        assert git(repo, "status", "--porcelain") == ""

    def test_a_write_git_refuses_is_an_error(self, tmp_path):
        with pytest.raises(StateError, match="invariant.id"):
            write_local_id(tmp_path, "local:x-00000000")
