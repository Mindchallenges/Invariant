"""The git queries the cochange kind needs (IDEA_031 A3 increment 4).

ADR-012 D12: `core/rules` makes every git call the guidelines family needs, and
`core/findings` stays pure. Four thin queries serve the `cochange` kind:

- `merge_base`, the commit the branch left its base at;
- `exists_at`, which keeps "the path was absent at that revision" apart from
  "the revision could not be read", the two answers `file_at` merges;
- `blob_at`, a path's exact bytes at a revision, unstripped;
- `changed_lines`, the lines added from the merge base to the working tree,
  each file keyed by its path, untracked files counted whole.

Every one answers `None` when git cannot, and `None` relaxes nothing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from invariant.core import git


def sh(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    sh(root, "init", "-q", "-b", "master")
    sh(root, "config", "user.email", "tests")
    sh(root, "config", "user.name", "T")
    write(root, "app.py", "a = 1\nb = 2\nc = 3\n")
    write(root, "gone.py", "x = 1\n")
    write(root, "docs/notes.md", "one\n")
    sh(root, "add", "-A")
    sh(root, "commit", "-qm", "base")
    sh(root, "checkout", "-q", "-b", "work")
    return root


# -- merge_base ---------------------------------------------------------------------


class TestMergeBase:
    def test_it_is_the_commit_the_branch_left_its_base_at(self, repo):
        base = sh(repo, "rev-parse", "master")
        write(repo, "app.py", "a = 1\n")
        sh(repo, "commit", "-qam", "on the branch")
        sh(repo, "checkout", "-q", "master")
        write(repo, "docs/notes.md", "two\n")
        sh(repo, "commit", "-qam", "the base moved on")
        sh(repo, "checkout", "-q", "work")
        assert git.merge_base(repo, "master") == base

    @pytest.mark.parametrize("base", ["", "no-such-branch", "-p"])
    def test_an_unreadable_base_is_none(self, repo, base):
        assert git.merge_base(repo, base) is None

    def test_outside_a_repository_it_is_none(self, tmp_path):
        assert git.merge_base(tmp_path, "master") is None


# -- exists_at ----------------------------------------------------------------------


class TestExistsAt:
    def test_a_path_committed_at_the_revision_exists(self, repo):
        assert git.exists_at(repo, "master", "app.py") is True
        assert git.exists_at(repo, "master", "docs/notes.md") is True

    def test_a_path_absent_at_the_revision_is_false_not_none(self, repo):
        assert git.exists_at(repo, "master", "new.py") is False

    def test_a_directory_is_not_a_file(self, repo):
        assert git.exists_at(repo, "master", "docs") is False

    def test_a_glob_is_read_literally(self, repo):
        assert git.exists_at(repo, "master", "*.py") is False

    def test_a_path_added_later_did_not_exist_at_the_base(self, repo):
        write(repo, "new.py", "n = 1\n")
        sh(repo, "add", "new.py")
        sh(repo, "commit", "-qm", "new")
        assert git.exists_at(repo, "master", "new.py") is False
        assert git.exists_at(repo, "HEAD", "new.py") is True

    @pytest.mark.parametrize("rev", ["", "no-such-ref", "-p"])
    def test_an_unreadable_revision_is_none(self, repo, rev):
        assert git.exists_at(repo, rev, "app.py") is None

    def test_outside_a_repository_it_is_none(self, tmp_path):
        assert git.exists_at(tmp_path, "HEAD", "app.py") is None


# -- blob_at ------------------------------------------------------------------------


class TestBlobAt:
    def test_it_is_the_exact_bytes_unstripped(self, repo):
        write(repo, "app.py", "\n  a = 1\n\n")
        sh(repo, "commit", "-qam", "layout")
        assert git.blob_at(repo, "HEAD", "app.py") == b"\n  a = 1\n\n"

    def test_an_absent_path_is_none(self, repo):
        assert git.blob_at(repo, "master", "new.py") is None

    @pytest.mark.parametrize("rev", ["", "-p"])
    def test_an_unreadable_revision_is_none(self, repo, rev):
        assert git.blob_at(repo, rev, "app.py") is None


# -- changed_lines ------------------------------------------------------------------


class TestChangedLines:
    def test_a_branch_with_no_change_has_no_lines(self, repo):
        assert git.changed_lines(repo, "master") == {}

    def test_committed_staged_and_unstaged_additions_are_counted(self, repo):
        write(repo, "app.py", "a = 1\nb = 20\nc = 3\nd = 4\n")
        sh(repo, "commit", "-qam", "edit")
        write(repo, "app.py", "zero = 0\na = 1\nb = 20\nc = 3\nd = 4\n")
        lines = git.changed_lines(repo, "master")
        assert set(lines) == {"app.py"}
        assert lines["app.py"].lines == frozenset({1, 3, 5})
        assert not lines["app.py"].whole

    def test_line_numbers_are_the_working_tree_s(self, repo):
        write(repo, "app.py", "new\na = 1\nb = 2\nc = 3\nalso\n")
        added = git.changed_lines(repo, "master")["app.py"]
        assert 1 in added and 5 in added
        assert 2 not in added and 4 not in added

    def test_a_deletion_only_file_is_changed_with_no_added_line(self, repo):
        write(repo, "app.py", "a = 1\nc = 3\n")
        lines = git.changed_lines(repo, "master")
        assert lines["app.py"].lines == frozenset()

    def test_a_deleted_file_is_changed_with_no_added_line(self, repo):
        (repo / "gone.py").unlink()
        lines = git.changed_lines(repo, "master")
        assert "gone.py" in lines
        assert lines["gone.py"].lines == frozenset()

    def test_a_new_tracked_file_has_every_line_added(self, repo):
        write(repo, "new.py", "n = 1\nm = 2\n")
        sh(repo, "add", "new.py")
        assert git.changed_lines(repo, "master")["new.py"].lines == frozenset({1, 2})

    def test_an_untracked_file_is_counted_whole(self, repo):
        write(repo, "tests/test_app.py", "def test(): pass\n")
        added = git.changed_lines(repo, "master")["tests/test_app.py"]
        assert added.whole
        assert 1 in added and 4000 in added

    def test_an_ignored_file_is_not_counted(self, repo):
        write(repo, ".gitignore", "build/\n")
        write(repo, "build/out.py", "x = 1\n")
        lines = git.changed_lines(repo, "master")
        assert "build/out.py" not in lines
        assert ".gitignore" in lines

    def test_a_rename_counts_every_line_of_the_new_path(self, repo):
        sh(repo, "mv", "app.py", "renamed.py")
        lines = git.changed_lines(repo, "master")
        assert lines["renamed.py"].lines == frozenset({1, 2, 3})
        assert lines["app.py"].lines == frozenset()

    def test_a_binary_file_is_changed_with_no_line(self, repo):
        (repo / "blob.bin").write_bytes(b"\x00\x01\x02")
        sh(repo, "add", "blob.bin")
        lines = git.changed_lines(repo, "master")
        assert lines["blob.bin"].lines == frozenset()

    def test_an_added_line_that_looks_like_a_header_is_content(self, repo):
        write(repo, "app.py", "a = 1\nb = 2\nc = 3\n++ b/elsewhere.py\n")
        lines = git.changed_lines(repo, "master")
        assert set(lines) == {"app.py"}
        assert lines["app.py"].lines == frozenset({4})

    @pytest.mark.parametrize("name", ['we"ird.py', "back\\slash.py", "tab\there.py", "é.py"])
    def test_a_path_git_quotes_is_read_back_unquoted(self, repo, name):
        write(repo, name, "q = 1\n")
        sh(repo, "add", "--", name)
        assert git.changed_lines(repo, "master")[name].lines == frozenset({1})

    def test_a_change_made_on_the_base_after_the_branch_left_is_not_counted(self, repo):
        sh(repo, "checkout", "-q", "master")
        write(repo, "docs/notes.md", "one\ntwo\n")
        sh(repo, "commit", "-qam", "the base moved on")
        sh(repo, "checkout", "-q", "work")
        assert git.changed_lines(repo, "master") == {}

    def test_it_answers_from_a_subdirectory_with_top_level_paths(self, repo):
        write(repo, "docs/notes.md", "one\ntwo\n")
        write(repo, "docs/extra.md", "x\n")
        lines = git.changed_lines(repo / "docs", "master")
        assert set(lines) == {"docs/notes.md", "docs/extra.md"}

    @pytest.mark.parametrize("base", ["", "no-such-branch", "-p"])
    def test_an_unreadable_base_is_none(self, repo, base):
        assert git.changed_lines(repo, base) is None

    def test_outside_a_repository_it_is_none(self, tmp_path):
        assert git.changed_lines(tmp_path, "master") is None


class TestAddedLines:
    def test_membership_reads_the_lines_or_the_whole_file(self):
        assert 2 in git.AddedLines(lines=frozenset({2}))
        assert 3 not in git.AddedLines(lines=frozenset({2}))
        assert 3 in git.AddedLines(whole=True)
        assert 1 not in git.AddedLines()


class TestThePatchReader:
    """The zero-context patch is read by header state, and a header this
    reader cannot spell answers ``None`` rather than lines under a wrong name."""

    def test_hunks_are_filed_under_their_header(self):
        patch = (
            b"diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
            b"@@ -1 +1,2 @@\n-old\n+new\n+more\n"
            b"diff --git a/y.py b/y.py\n--- a/y.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n"
        )
        assert git._added_by_patch(patch) == {"x.py": {1, 2}}

    def test_a_header_with_no_b_prefix_is_unreadable(self):
        assert git._added_by_patch(b"diff --git a/x b/x\n+++ x.py\n@@ -0,0 +1 @@\n+a\n") is None

    @pytest.mark.parametrize(
        ("quoted", "plain"),
        [
            ('"b/we\\"ird.py"', "b/we\"ird.py"),
            ('"b/tab\\there.py"', "b/tab\there.py"),
            ('"b/\\303\\251.py"', "b/é.py"),
            ('"b/back\\\\slash.py"', "b/back\\slash.py"),
        ],
    )
    def test_git_quoting_is_undone(self, quoted, plain):
        assert git._unquote(quoted) == plain

    @pytest.mark.parametrize("bad", ['"', '"b/open', '"b/\\q.py"', '"b/\\12.py"'])
    def test_quoting_it_cannot_read_is_none(self, bad):
        assert git._unquote(bad) is None

    def test_a_quoted_header_it_cannot_read_is_unreadable(self):
        assert git._patch_path(b'"b/\\q.py"') is False
        assert git._patch_path(b'"b/ok.py"') == "ok.py"
        assert git._patch_path(b"/dev/null") is None


class TestAFailingLeg:
    """Each git call ``changed_lines`` makes must answer, or it answers
    ``None``: a partial answer would read as fewer changed files."""

    @pytest.mark.parametrize("leg", ["--name-only", "-U0", "ls-files"])
    def test_any_leg_failing_is_none(self, repo, monkeypatch, leg):
        real = git._run_bytes

        def failing(args, cwd):
            return (1, b"") if leg in args else real(args, cwd)

        monkeypatch.setattr(git, "_run_bytes", failing)
        assert git.changed_lines(repo, "master") is None

    def test_an_unreadable_patch_header_is_none(self, repo, monkeypatch):
        write(repo, "app.py", "a = 1\nb = 2\nc = 3\nd = 4\n")
        real = git._run_bytes

        def odd(args, cwd):
            if "-U0" in args:
                return 0, b"diff --git a/app.py b/app.py\n+++ app.py\n@@ -3,0 +4 @@\n+d = 4\n"
            return real(args, cwd)

        monkeypatch.setattr(git, "_run_bytes", odd)
        assert git.changed_lines(repo, "master") is None

    def test_exists_at_a_failing_listing_is_none(self, repo, monkeypatch):
        monkeypatch.setattr(git, "_run_bytes", lambda args, cwd: (1, b""))
        assert git.exists_at(repo, "master", "app.py") is None

    def test_merge_base_with_no_common_history_is_none(self, repo):
        sh(repo, "checkout", "-q", "--orphan", "island")
        sh(repo, "commit", "-qm", "alone")
        assert git.merge_base(repo, "master") is None
