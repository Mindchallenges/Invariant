"""ADR-020 D11: a manifest kept out of git through .git/info/exclude.

The user's unblock ruling (2026-11-12) amends D6 for the manifest alone. An
in-repo ``.claude/invariant.yaml`` that git ignores is a supported, local
configuration home: ``session isolate`` copies it into each new worktree, the
shared exclude keeps every worktree's copy out of ``git status``, ``doctor``
recognises it, and the onboarding plan accepts it. A committed manifest
behaves as before.

Every test runs with a scratch ``HOME`` and the suite's scratch
``INVARIANT_HOME``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from invariant.cli import onboarding
from invariant.cli.main import main
from invariant.core import git as core_git
from invariant.core.manifest import MANIFEST_RELPATH, Manifest, manifest_tracking

MANIFEST = "version: 1\nproject:\n  name: widgets\n"
EXCLUDE_LINE = ".claude/invariant.yaml"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def run(capsys, *argv) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def porcelain(cwd: Path) -> str:
    return git(cwd, "status", "--porcelain", "--untracked-files=all")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
    root = tmp_path / "widgets"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    (root / ".claude").mkdir()
    (root / MANIFEST_RELPATH).write_text(MANIFEST, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def keep_local(root: Path) -> None:
    with (root / ".git" / "info" / "exclude").open("a", encoding="utf-8") as fh:
        fh.write(EXCLUDE_LINE + "\n")


def commit_manifest(root: Path) -> None:
    git(root, "add", str(MANIFEST_RELPATH))
    git(root, "commit", "-qm", "manifest")


def doctor_rows(capsys) -> dict[str, dict]:
    capsys.readouterr()
    main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    return {row["check"]: row for row in payload["checks"]}


class TestTracking:
    def test_committed(self, repo):
        commit_manifest(repo)
        assert manifest_tracking(repo) == "committed"

    def test_local(self, repo):
        keep_local(repo)
        assert manifest_tracking(repo) == "local"

    def test_uncommitted(self, repo):
        assert manifest_tracking(repo) == "uncommitted"

    def test_none_without_a_manifest(self, repo):
        (repo / MANIFEST_RELPATH).unlink()
        assert manifest_tracking(repo) is None

    def test_none_outside_git(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        (tmp_path / MANIFEST_RELPATH).write_text(MANIFEST, encoding="utf-8")
        assert manifest_tracking(tmp_path) is None

    def test_is_ignored_answers_none_where_git_cannot(self, tmp_path):
        assert core_git.is_ignored(tmp_path, "x") is None


class TestSessionLifecycle:
    def test_a_local_manifest_runs_a_session_from_start_to_merge(self, repo, capsys, monkeypatch):
        keep_local(repo)
        assert porcelain(repo) == ""

        code, text = run(capsys, "session", "start", "local", "--id", "abc12345")
        assert code == 0, text
        code, text = run(capsys, "session", "isolate", "--id", "abc12345", "--execute")
        assert code == 0, text
        assert "copied this clone's local .claude/invariant.yaml" in text
        worktree = repo.parent / "widgets-wt-abc12345"
        copy = worktree / MANIFEST_RELPATH
        assert copy.read_text(encoding="utf-8") == MANIFEST
        assert porcelain(worktree) == ""
        assert porcelain(repo) == ""

        # A gate answers in the worktree: the project is found there.
        monkeypatch.chdir(worktree)
        monkeypatch.setenv("INVARIANT_SESSION_ID", "abc12345")
        assert Manifest.locate(worktree).root == worktree
        code, text = run(capsys, "pipeline", "start", "--task", "local manifest")
        assert code == 0, text
        code, text = run(capsys, "pipeline", "gates")
        assert "no .claude/invariant.yaml" not in text
        assert "docs.read_architecture" in text

        (worktree / "app.py").write_text("x = 2\n", encoding="utf-8")
        git(worktree, "commit", "-qam", "change")
        assert porcelain(worktree) == ""

        monkeypatch.chdir(repo)
        monkeypatch.delenv("INVARIANT_SESSION_ID")
        for state in ("working", "reviewed"):
            code, text = run(
                capsys, "session", "transition", state, "--id", "abc12345",
                "--force", "--reason", "test fixture",
            )
            assert code == 0, text
        code, text = run(capsys, "session", "merge", "--id", "abc12345")
        assert code == 0, text
        assert git(repo, "show", "HEAD:app.py") == "x = 2"
        assert git(repo, "ls-files", str(MANIFEST_RELPATH)) == ""
        assert porcelain(repo) == ""
        assert (repo / MANIFEST_RELPATH).read_text(encoding="utf-8") == MANIFEST

    def test_isolate_never_overwrites_a_committed_manifest(self, repo, capsys):
        commit_manifest(repo)
        before = git(repo, "rev-parse", "HEAD")
        run(capsys, "session", "start", "shared", "--id", "abc12345")
        code, text = run(capsys, "session", "isolate", "--id", "abc12345", "--execute")
        assert code == 0, text
        assert "copied" not in text
        worktree = repo.parent / "widgets-wt-abc12345"
        assert git(worktree, "ls-files", str(MANIFEST_RELPATH)) == str(MANIFEST_RELPATH)
        assert porcelain(worktree) == ""
        assert git(repo, "rev-parse", "HEAD") == before

    def test_isolate_copies_nothing_from_an_uncommitted_manifest(self, repo, capsys):
        run(capsys, "session", "start", "loose", "--id", "abc12345")
        code, text = run(
            capsys, "session", "isolate", "--id", "abc12345", "--execute", "--json"
        )
        assert code == 0, text
        assert not (repo.parent / "widgets-wt-abc12345" / MANIFEST_RELPATH).exists()


class TestDoctor:
    def test_committed_is_shared(self, repo, capsys):
        commit_manifest(repo)
        row = doctor_rows(capsys)["config home"]
        assert row["ok"] and not row["warn"] and "committed" in row["detail"]

    def test_local_is_recognised(self, repo, capsys):
        keep_local(repo)
        row = doctor_rows(capsys)["config home"]
        assert row["ok"] and not row["warn"] and "local" in row["detail"]
        assert "session isolate copies it" in row["detail"]

    def test_uncommitted_warns_and_names_both_routes(self, repo, capsys):
        row = doctor_rows(capsys)["config home"]
        assert row["ok"] and row["warn"]
        assert "git add" in row["detail"] and ".git/info/exclude" in row["detail"]


class TestOnboarding:
    def test_a_local_manifest_carries_the_plan(self, repo):
        keep_local(repo)
        assert onboarding.carries_manifest(Manifest.locate(repo))

    def test_an_uncommitted_manifest_still_does_not(self, repo):
        assert not onboarding.carries_manifest(Manifest.locate(repo))
