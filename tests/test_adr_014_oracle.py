"""ADR-014 D10: the oracle sees a store project as a project.

A store project has no .claude/invariant.yaml at its root, so the five checks
that tested for that file would have pruned it and shown it as an ALERT. They
ask Manifest.locate instead. The index key, the state root, is unchanged.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.core.home import user_config_path
from invariant.core.manifest import Manifest
from invariant.oracle import scan as scan_module
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectEntry, ProjectIndex, _canonical_root

IDENTITY = "host.example/team/widgets"


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def store_project(tmp_path) -> Path:
    repo = tmp_path / "widgets"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "T")
    (repo / "conf.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    git(repo, "remote", "add", "origin", f"https://{IDENTITY}.git")
    store = tmp_path / "store"
    store.mkdir()
    (store / "invariant.yaml").write_text(
        yaml.safe_dump(
            {"project": {"name": "widgets-docs"}, "store": {"repos": {IDENTITY: {}}}}
        ),
        encoding="utf-8",
    )
    user_config_path().parent.mkdir(parents=True, exist_ok=True)
    user_config_path().write_text(
        yaml.safe_dump({"repos": {IDENTITY: {"store": str(store)}}}), encoding="utf-8"
    )
    return repo


class TestTheIndex:
    def test_a_store_project_has_its_manifest(self, store_project):
        entry = ProjectEntry(key="k", root=str(store_project), name="w", via="manual")
        assert entry.has_manifest

    def test_a_root_with_no_project_has_none(self, tmp_path):
        assert not ProjectEntry(key="k", root=str(tmp_path), name="x", via="m").has_manifest
        assert not ProjectEntry(key="k", root="", name="x", via="m").has_manifest

    def test_prune_keeps_a_store_project(self, store_project):
        index = ProjectIndex(oracle_home())
        entry, added = index.register(Manifest.locate(store_project), via="manual")
        assert added
        assert entry.name == "widgets-docs"
        assert entry.key == str((store_project / ".git" / "invariant" / "state").resolve())
        assert index.prune(dry_run=True) == []

    def test_a_store_project_with_no_name_is_named_after_its_root(self, store_project):
        store = Path(
            yaml.safe_load(user_config_path().read_text(encoding="utf-8"))["repos"][IDENTITY][
                "store"
            ]
        )
        (store / "invariant.yaml").write_text(
            yaml.safe_dump({"store": {"repos": {IDENTITY: {}}}}), encoding="utf-8"
        )
        entry, _ = ProjectIndex(oracle_home()).register(
            Manifest.locate(store_project), via="manual"
        )
        assert entry.name == "widgets"

    def test_a_worktree_root_is_recorded_as_the_main_checkout(self, store_project, tmp_path):
        worktree = tmp_path / "wt"
        git(store_project, "worktree", "add", "-q", str(worktree), "-b", "side")
        assert _canonical_root(worktree) == store_project.resolve()


class TestTheScan:
    def test_a_store_project_is_read_not_refused(self, store_project):
        entry = ProjectEntry(key="k", root=str(store_project), name="w", via="manual")
        reader = scan_module._ProjectReader(entry, 0.0, scan_module.ScanCache())
        assert reader.manifest.store is not None

    def test_a_root_that_lost_its_project_is_refused(self, tmp_path):
        entry = ProjectEntry(key="k", root=str(tmp_path), name="w", via="manual")
        with pytest.raises(scan_module._NoManifest):
            scan_module._ProjectReader(entry, 0.0, scan_module.ScanCache())
