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


@pytest.fixture(autouse=True)
def _no_real_oracle_home(monkeypatch, tmp_path_factory):
    """No test may write the developer's real ``~/.invariant``.

    ``init`` and the SessionStart hook register the project in the oracle's
    user-level index, so without this every test that initialises a project
    would add a temporary directory to the index on the machine running the
    suite. The variable is set, not deleted, because the fallback is the real
    home; it is exported, so a hook shim a test runs as a subprocess inherits
    it too. The directory itself is not created: a test that asserts nothing
    was written can check that it does not exist.
    """
    home = tmp_path_factory.mktemp("oracle") / "home"
    monkeypatch.setenv("INVARIANT_HOME", str(home))
    return home


class InertCloser:
    """A host closer that signals nothing and records what it was asked."""

    def __init__(self, name: str | None = "claude") -> None:
        self.name = name
        self.asked: list[int] = []
        self.closed: list[tuple[int, int]] = []

    def process_name(self, pid: int) -> str | None:
        self.asked.append(pid)
        return self.name

    def close(self, pid: int, delay: int) -> None:
        self.closed.append((pid, delay))


@pytest.fixture(autouse=True)
def _no_real_host_close(monkeypatch):
    """No test anywhere may end the Claude Code session it runs inside.

    A successful `session handoff` schedules a SIGTERM to `CLAUDE_PID`
    (IDEA_009), and this suite runs inside a Claude Code session that exports
    it — so a test reaching the command with the real closer in place would end
    the host running the suite. The closer is replaced suite-wide, not only in
    the handoff tests, and the ambient pid is removed: a test that wants one
    sets it.
    """
    monkeypatch.delenv("CLAUDE_PID", raising=False)
    inert = InertCloser()
    monkeypatch.setattr(
        "invariant.cli.main._handoff_closer", lambda: inert, raising=False
    )
    return inert


@pytest.fixture(autouse=True)
def _no_real_process_walk(monkeypatch):
    """No hook in the suite reads the real process table for its host pid.

    A hook records its host's pid in the attention sidecar, from ``CLAUDE_PID``
    or, without it, by walking the parent chain with ``ps``. The closer fixture
    above removes ``CLAUDE_PID``, so every hook a test runs would otherwise
    walk — slower, and answering with whatever ``claude`` happens to be running
    the suite. The walk finds nothing here; a test about the walk supplies a
    chain of its own.
    """
    monkeypatch.setattr(
        "invariant.cli.main._attention_process_lookup",
        lambda: (lambda pid: None),
        raising=False,
    )


class InertWatch:
    """A successor watch that reads no process table.

    It answers what a stub launcher makes true: nothing was started, so there
    is no successor, and a handoff that reaches it fails as a real one with no
    successor would. A test whose subject is the launch that worked sets
    ``pid`` to the successor it pretends was found.
    """

    def __init__(self, pid: int | None = None) -> None:
        self.pid = pid
        self.watched: list[tuple[str, set[int]]] = []

    def running(self, name: str) -> set[int]:
        return set()

    def wait_for(self, name: str, exclude: set[int]) -> int | None:
        self.watched.append((name, set(exclude)))
        return self.pid


@pytest.fixture(autouse=True)
def _no_real_successor_watch(monkeypatch):
    """No test waits on the real process table for a successor (BUG_075).

    The real watch polls ``ps`` for up to ``SUCCESSOR_TIMEOUT_SECONDS``, and no
    test starts a successor, so it would only ever time out. Replaced
    suite-wide beside the closer.
    """
    inert = InertWatch()
    monkeypatch.setattr(
        "invariant.cli.main._handoff_successor", lambda: inert, raising=False
    )
    return inert


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
