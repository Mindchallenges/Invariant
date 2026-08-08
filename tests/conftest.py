"""Shared fixtures.

Every fixture value here is synthetic. Nothing in this suite may carry a real
person's identifier, which is the same rule the library's own privacy gate
enforces on its consumers.
"""

from pathlib import Path

import pytest
import yaml

from invariant.core.manifest import MANIFEST_RELPATH, Manifest


def write_manifest(root: Path, body: dict) -> Path:
    path = root / MANIFEST_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path):
    """A minimal project directory with a manifest."""
    write_manifest(tmp_path, {"version": 1, "project": {"name": "demo"}})
    (tmp_path / "docs" / "architecture").mkdir(parents=True)
    (tmp_path / ".claude" / "memory").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def manifest(project):
    return Manifest.load(project)


@pytest.fixture
def make_manifest(tmp_path):
    def build(body: dict, root: Path | None = None) -> Manifest:
        root = root or tmp_path
        write_manifest(root, body)
        return Manifest.load(root)

    return build
