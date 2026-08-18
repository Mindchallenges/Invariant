"""Shared fixtures.

Every fixture value here is synthetic. Nothing in this suite may carry a real
person's identifier, which is the same rule the library's own privacy gate
enforces on its consumers.
"""

from pathlib import Path

import pytest
import yaml

from invariant.core.manifest import MANIFEST_RELPATH, Manifest


@pytest.fixture(autouse=True)
def _no_ambient_host_project_dir(monkeypatch):
    """Hooks resolve their manifest from CLAUDE_PROJECT_DIR when set (AUD-04).

    This suite itself runs inside an agent host, so the variable is set — and
    points at the library's own repository. Left in place it would make every
    hook test resolve the real project's manifest instead of the synthetic one
    the test built, which is the exact cross-project misattribution the
    resolution order exists to prevent. Tests that need the variable set it
    explicitly.
    """
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


@pytest.fixture(autouse=True)
def _no_ambient_session_id(monkeypatch):
    """This suite creates sessions, and `INVARIANT_SESSION_ID` names one.

    `SessionDomain.start()` resolves the id to register as
    `session_id or os.environ["INVARIANT_SESSION_ID"]`, so with the variable
    set every `.start()` that does not pass an id explicitly resolves to the
    real, live session instead of the derived fallback — and collides with it.
    Measured: 25 false failures in `test_domains.py` against an identical bare
    run that was fully green (BUG_035).

    `domains/pipeline` now strips it from the environment it spawns the suite
    with, which fixes the route through `invariant pipeline test`. This is the
    other route: a bare `uv run pytest` typed in a shell that has it exported.
    Tests that need the variable set it explicitly.
    """
    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)


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
