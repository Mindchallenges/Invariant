"""ADR-026 — the gate receipt is the pipeline's coverage record (BUG_126).

``invariant pipeline coverage`` signs the tree it measured, before the run and
after it (``core/git.tree_signature``: the commit, every uncommitted change and
every untracked file git does not ignore). ``invariant pipeline receipt`` is the
check: it matches when a coverage run is on record, the tree held still for the
run, the tree now is that tree, the suite passed and ``coverage.meets_floor``
passes. Nothing is written by an agent, so no host permission set can refuse it,
and a new file is hashed by its content like any other.
"""

from __future__ import annotations

import json
import subprocess

import pytest
import yaml
from test_cli import payload, run

from invariant.core import git
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain

#: Every ``*.py`` file in the tree is reported at the percentage the
#: ``COVERAGE_PCT`` file holds (100 when it is absent), so a test can put a
#: touched file under the floor without a real coverage tool.
FAKE_COVERAGE = """#!/bin/sh
echo '3 passed in 0.01s'
pct=100
[ -f COVERAGE_PCT ] && pct=$(cat COVERAGE_PCT)
for f in *.py; do
  [ -f "$f" ] && echo "$f      10      0   $pct%"
done
exit 0
"""


def git_in(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    git_in(root, "init", "-q", "-b", "master")
    git_in(root, "config", "user.email", "t@example.invalid")
    git_in(root, "config", "user.name", "T")
    (root / ".gitignore").write_text(".build/\nCOVERAGE_PCT\n", encoding="utf-8")
    (root / "fake-cov.sh").write_text(FAKE_COVERAGE, encoding="utf-8")
    (root / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    (root / "gone.py").write_text("z = 1\n", encoding="utf-8")
    body = {
        "version": 1,
        "project": {"name": "demo", "ecosystems": ["python"]},
        "commands": {
            "test": "sh fake-cov.sh",
            "coverage": "sh fake-cov.sh",
            "coverage_one": "sh fake-cov.sh {selector}",
        },
        "methodology": {"gates": {"coverage_min": 85}},
    }
    manifest = root / MANIFEST_RELPATH
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(yaml.safe_dump(body), encoding="utf-8")
    git_in(root, "add", "-A")
    git_in(root, "commit", "-qm", "base")
    monkeypatch.chdir(root)
    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
    assert run("session", "start", "x") == 0
    assert run("pipeline", "start", "--task", "t") == 0
    return root


def coverage(capsys, *selectors) -> None:
    argv = ["pipeline", "coverage"]
    for selector in selectors:
        argv += ["--selector", selector]
    assert run(*argv) == 0
    capsys.readouterr()


def receipt(capsys) -> tuple[int, dict]:
    code = run("pipeline", "receipt", "--json")
    return code, payload(capsys)


def reasons(doc) -> str:
    return "\n".join(doc["reasons"])


class TestAMatch:
    def test_a_mixed_change_matches_while_the_tree_holds(self, repo, capsys):
        (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        (repo / "new.py").write_text("y = 1\n", encoding="utf-8")
        coverage(capsys)
        code, doc = receipt(capsys)
        assert code == 0, reasons(doc)
        assert doc["match"] is True
        assert doc["reasons"] == []
        assert doc["coverage"]["new.py"] == 100
        assert doc["commit"] == git.resolve_commit(repo, "HEAD")

    def test_an_all_new_change_matches(self, repo, capsys):
        (repo / "new.py").write_text("y = 1\n", encoding="utf-8")
        coverage(capsys)
        code, doc = receipt(capsys)
        assert code == 0, reasons(doc)

    def test_a_deleted_file_is_part_of_the_tree_it_signs(self, repo, capsys):
        (repo / "gone.py").unlink()
        (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        coverage(capsys)
        assert receipt(capsys)[0] == 0

    def test_an_ignored_file_does_not_move_the_tree(self, repo, capsys):
        (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        coverage(capsys)
        (repo / ".build" / "gate").mkdir(parents=True)
        (repo / ".build" / "gate" / "cache").write_text("warm\n", encoding="utf-8")
        assert receipt(capsys)[0] == 0

    def test_a_scoped_run_is_a_receipt_too(self, repo, capsys):
        (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        coverage(capsys, "tests/test_tracked.py")
        code, doc = receipt(capsys)
        assert code == 0, reasons(doc)
        assert doc["scope"] == "scoped"

    def test_the_text_says_match_and_names_the_numbers(self, repo, capsys):
        (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        coverage(capsys)
        assert run("pipeline", "receipt") == 0
        text = capsys.readouterr().out
        assert text.startswith("receipt: MATCH")
        assert "tracked.py  100%" in text
        assert "coverage.meets_floor" in text

    def test_the_check_reads_the_tree_the_run_measured(self, repo, capsys, tmp_path):
        """A reviewer whose shell stands elsewhere still compares the tree the
        architect's run measured, which the record names."""
        (repo / "new.py").write_text("y = 1\n", encoding="utf-8")
        coverage(capsys)
        sid = json.loads(_status(capsys))["session"]["id"]
        manifest = Manifest.locate(repo)
        session = SessionDomain(manifest).require(sid).to_dict()
        doc = PipelineDomain(manifest).gate_receipt(sid, session=session, cwd=tmp_path)
        assert doc["match"] is True, doc["reasons"]
        assert doc["root"] == str(repo)


def _status(capsys) -> str:
    assert run("session", "status", "--json") == 0
    return capsys.readouterr().out


class TestNoMatch:
    def test_an_edit_to_a_new_file_after_the_run_moves_the_tree(self, repo, capsys):
        (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        (repo / "new.py").write_text("y = 1\n", encoding="utf-8")
        coverage(capsys)
        (repo / "new.py").write_text("y = 'edited after the gate'\n", encoding="utf-8")
        code, doc = receipt(capsys)
        assert code == 1
        assert doc["match"] is False
        assert "has moved since the coverage run" in reasons(doc)

    def test_a_new_file_added_after_the_run_moves_the_tree(self, repo, capsys):
        (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        coverage(capsys)
        (repo / "late.py").write_text("w = 1\n", encoding="utf-8")
        assert receipt(capsys)[0] == 1

    def test_a_deletion_after_the_run_moves_the_tree(self, repo, capsys):
        (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        coverage(capsys)
        (repo / "gone.py").unlink()
        assert receipt(capsys)[0] == 1

    def test_a_commit_after_the_run_moves_the_tree(self, repo, capsys):
        (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        coverage(capsys)
        git_in(repo, "commit", "-qam", "wip")
        code, doc = receipt(capsys)
        assert code == 1
        assert "commit" in reasons(doc)

    def test_no_coverage_run_on_record(self, repo, capsys):
        code, doc = receipt(capsys)
        assert code == 1
        assert "no coverage run is recorded" in reasons(doc)

    def test_no_pipeline_running(self, repo, capsys):
        sid = json.loads(_status(capsys))["session"]["id"]
        domain = PipelineDomain(Manifest.locate(repo))
        domain.store.write(domain.relpath(sid), {})
        code, doc = receipt(capsys)
        assert code == 1
        assert "no pipeline is running" in reasons(doc)

    def test_a_red_suite_vouches_for_nothing(self, repo, capsys):
        script = repo / "fake-cov.sh"
        script.write_text(
            FAKE_COVERAGE.replace("3 passed in 0.01s", "1 failed, 2 passed in 0.01s").replace(
                "exit 0", "exit 1"
            ),
            encoding="utf-8",
        )
        git_in(repo, "commit", "-qam", "red suite")
        (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        run("pipeline", "coverage")
        capsys.readouterr()
        code, doc = receipt(capsys)
        assert code == 1
        assert "did not pass" in reasons(doc)

    def test_a_file_under_the_floor_vouches_for_nothing(self, repo, capsys):
        (repo / "COVERAGE_PCT").write_text("50", encoding="utf-8")
        (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        coverage(capsys)
        code, doc = receipt(capsys)
        assert code == 1
        assert "coverage.meets_floor" in reasons(doc)

    def test_a_tree_that_moved_during_the_run(self, repo, capsys):
        script = repo / "fake-cov.sh"
        script.write_text(
            FAKE_COVERAGE.replace("exit 0", "echo out > run-output.txt\nexit 0"),
            encoding="utf-8",
        )
        git_in(repo, "commit", "-qam", "writes its own output")
        (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        coverage(capsys)
        code, doc = receipt(capsys)
        assert code == 1
        assert "moved during the coverage run" in reasons(doc)

    def test_a_record_with_no_tree_signature(self, repo, capsys):
        (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        coverage(capsys)
        sid = json.loads(_status(capsys))["session"]["id"]
        domain = PipelineDomain(Manifest.locate(repo))
        raw = domain.store.read(domain.relpath(sid), {})
        del raw["current"]["evidence"]["coverage_run"]["tree"]
        domain.store.write(domain.relpath(sid), raw)
        code, doc = receipt(capsys)
        assert code == 1
        assert "carries no tree signature" in reasons(doc)

    def test_git_cannot_name_the_tree_now(self, repo, capsys, monkeypatch):
        (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        coverage(capsys)
        monkeypatch.setattr(git, "tree_signature", lambda cwd: None)
        code, doc = receipt(capsys)
        assert code == 1
        assert "git cannot say which tree" in reasons(doc)

    def test_the_text_says_no_match_and_why(self, repo, capsys):
        assert run("pipeline", "receipt") == 1
        text = capsys.readouterr().out
        assert text.startswith("receipt: NO MATCH")
        assert "no coverage run is recorded" in text


class TestTheRunSignsItsTree:
    def test_the_coverage_record_carries_the_signature(self, repo, capsys):
        (repo / "new.py").write_text("y = 1\n", encoding="utf-8")
        coverage(capsys)
        assert run("pipeline", "status", "--json") == 0
        tree = json.loads(capsys.readouterr().out)["evidence"]["coverage_run"]["tree"]
        commit, digest = git.tree_signature(repo)
        assert tree == {
            "root": str(repo),
            "commit": commit,
            "before": digest,
            "commit_after": commit,
            "after": digest,
        }

    def test_outside_a_repository_the_record_carries_none(self, repo, capsys, monkeypatch):
        monkeypatch.setattr(git, "tree_signature", lambda cwd: None)
        coverage(capsys)
        assert run("pipeline", "status", "--json") == 0
        assert json.loads(capsys.readouterr().out)["evidence"]["coverage_run"]["tree"] is None


def _moves(root, change) -> bool:
    """Whether ``change`` moves the signature of the tree at ``root``."""
    before = git.tree_signature(root)
    assert before is not None
    change()
    return git.tree_signature(root) != before


def _symlink_or_skip(target, link):
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("this file system cannot hold a symlink")


class TestTheSignatureHashesWhatGitHides:
    """A receipt never vouches for content it did not hash (BUG_126's
    expected). ``git diff HEAD`` and a by-content hash of each untracked file
    leave several kinds of change unseen, each found probing the receipt at
    review: an untracked file's mode, an untracked symlink's target, a
    submodule's or an embedded repository's contents, and a tracked file git is
    told not to look at. Each must move the signature. An ignored file and a
    change only to the index do not: the one is outside the tree by
    ``.gitignore``, and the other leaves the files on disk as they were."""

    def test_an_untracked_file_made_executable(self, repo):
        new = repo / "new.py"
        new.write_text("y = 1\n", encoding="utf-8")
        assert _moves(repo, lambda: new.chmod(0o755))

    def test_an_untracked_symlink_retargeted_at_equal_content(self, repo):
        (repo / "a.txt").write_text("same\n", encoding="utf-8")
        (repo / "b.txt").write_text("same\n", encoding="utf-8")
        link = repo / "link"
        _symlink_or_skip("a.txt", link)

        def retarget():
            link.unlink()
            link.symlink_to("b.txt")

        assert _moves(repo, retarget)

    def test_an_untracked_symlink_to_a_directory_retargeted(self, repo):
        for name, body in (("x", "1"), ("y", "2")):
            (repo / name).mkdir()
            (repo / name / "f.py").write_text(body, encoding="utf-8")
        link = repo / "link"
        _symlink_or_skip("x", link)

        def retarget():
            link.unlink()
            link.symlink_to("y")

        assert _moves(repo, retarget)

    def test_a_second_edit_inside_a_dirty_submodule(self, repo, tmp_path):
        upstream = tmp_path / "upstream"
        upstream.mkdir()
        git_in(upstream, "init", "-q")
        git_in(upstream, "config", "user.email", "t@example.invalid")
        git_in(upstream, "config", "user.name", "T")
        (upstream / "lib.py").write_text("v = 0\n", encoding="utf-8")
        git_in(upstream, "add", "-A")
        git_in(upstream, "commit", "-qm", "lib")
        git_in(
            repo, "-c", "protocol.file.allow=always",
            "submodule", "add", "-q", str(upstream), "sub",
        )
        git_in(repo, "commit", "-qm", "submodule")
        (repo / "sub" / "lib.py").write_text("v = 1\n", encoding="utf-8")
        assert _moves(
            repo, lambda: (repo / "sub" / "lib.py").write_text("v = 2\n", encoding="utf-8")
        )
        assert _moves(
            repo, lambda: (repo / "sub" / "new.py").write_text("n = 1\n", encoding="utf-8")
        )

    def test_an_edit_inside_an_embedded_repository(self, repo):
        inner = repo / "inner"
        inner.mkdir()
        git_in(inner, "init", "-q")
        (inner / "f.py").write_text("1\n", encoding="utf-8")
        assert _moves(repo, lambda: (inner / "f.py").write_text("2\n", encoding="utf-8"))

    @pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
    def test_an_edit_to_a_file_git_is_told_not_to_look_at(self, repo, flag):
        git_in(repo, "update-index", flag, "tracked.py")
        assert _moves(
            repo, lambda: (repo / "tracked.py").write_text("x = 9\n", encoding="utf-8")
        )

    def test_an_ignored_file_and_the_index_alone_do_not_move_it(self, repo):
        (repo / ".build").mkdir()
        assert not _moves(
            repo, lambda: (repo / ".build" / "cache").write_text("warm\n", encoding="utf-8")
        )
        (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        assert not _moves(repo, lambda: git_in(repo, "add", "tracked.py"))

    def test_an_ordinary_untracked_file_signs_as_it_did(self, repo):
        """Records taken before the hardening still match a tree of plain
        files: the digest of a plain untracked file is unchanged."""
        import hashlib

        git_in(repo, "rm", "-q", "--cached", "gone.py")
        git_in(repo, "commit", "-qm", "untrack")
        (repo / "gone.py").write_text("z = 1\n", encoding="utf-8")
        digest = hashlib.sha256(b"")
        digest.update(b"\0untracked\0gone.py\0")
        digest.update(hashlib.sha256(b"z = 1\n").digest())
        assert git.tree_signature(repo)[1] == digest.hexdigest()

    def test_the_receipt_refuses_a_retargeted_symlink(self, repo, capsys):
        (repo / "a.txt").write_text("same\n", encoding="utf-8")
        (repo / "b.txt").write_text("same\n", encoding="utf-8")
        link = repo / "link"
        _symlink_or_skip("a.txt", link)
        coverage(capsys)
        assert receipt(capsys)[0] == 0
        link.unlink()
        link.symlink_to("b.txt")
        code, doc = receipt(capsys)
        assert code == 1
        assert "has moved since the coverage run" in reasons(doc)
