"""IDEA_052 — a plain transition can carry the commit it rests on.

``--evidence`` was built as one half of IDEA_019's ruled pair, so the ordinary
``reproduced -> fixed`` edge could not name the commit that fixed the bug
without a ruling nobody made. The commit now stands alone: resolved before
anything is written, recorded on the row, read back by every backend. Only a
ruling still walks a missing edge (DEC_ADR_022_D1).
"""

import subprocess

import pytest
from test_bug_065_repro import BACKLOG
from test_cli import (
    out,
    payload,
    project,  # noqa: F401 -- fixture
    run,
)
from test_domains import git_project

from invariant.core.errors import TransitionError, UsageError
from invariant.core.items import HISTORY_MARKER
from invariant.core.manifest import Manifest
from invariant.domains.workitem import WorkItemDomain

RULING = "maintainer, 2026-11-15"

BACKENDS = {
    "markdown": None,
    "json": {"version": 1, "items": {"backend": "json", "path": "backlog.json"}},
    "sphinx_needs": {
        "version": 1,
        "items": {"backend": "sphinx_needs", "path": "backlog.rst"},
    },
}


def head(root) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def store_bytes(m) -> dict:
    """Every byte of every items store the project has, keyed by path."""
    found = {}
    for path in sorted(m.root.rglob("*")):
        if path.is_file() and ".git" not in path.parts and (
            path.suffix in {".md", ".json", ".rst"}
        ):
            found[str(path.relative_to(m.root))] = path.read_bytes()
    return found


def reproduced_bug(tmp_path, backend: str):
    """A project on ``backend`` holding one bug already in ``reproduced``."""
    m = git_project(tmp_path / "proj", BACKENDS[backend])
    if backend == "sphinx_needs":
        (m.root / "backlog.rst").write_text(BACKLOG, encoding="utf-8")
        bug_id = "BUG_WRAPPED"
    else:
        bug_id = WorkItemDomain(m).new("bug", "A defect").id
    WorkItemDomain(m).transition(bug_id, "reproduced", cwd=m.root)
    return m, bug_id


@pytest.mark.parametrize("backend", sorted(BACKENDS))
class TestACommitWithoutARuling:
    def test_reproduced_to_fixed_records_the_resolved_commit(self, tmp_path, backend):
        m, bug_id = reproduced_bug(tmp_path, backend)
        sha = head(m.root)

        item, _ = WorkItemDomain(m).transition(
            bug_id, "fixed", evidence=sha[:8], reason="green since the fix", cwd=m.root
        )

        assert item.status == "fixed"
        # A fresh domain reads the store back: the row survives the backend.
        stored = WorkItemDomain(Manifest.locate(m.root)).get(bug_id)
        assert stored.status == "fixed"
        assert [(h["from"], h["to"]) for h in stored.history] == [
            ("filed", "reproduced"),
            ("reproduced", "fixed"),
        ]
        row = stored.history[-1]
        assert row["evidence"] == sha
        assert "ruling" not in row and "intermediate" not in row
        assert "evidence" not in stored.history[0]

    def test_a_commit_that_does_not_resolve_writes_nothing(self, tmp_path, backend):
        m, bug_id = reproduced_bug(tmp_path, backend)
        before = store_bytes(m)

        with pytest.raises(UsageError, match="names no commit"):
            WorkItemDomain(m).transition(
                bug_id, "fixed", evidence="deadbeefdeadbeef", cwd=m.root
            )

        assert store_bytes(m) == before
        assert WorkItemDomain(m).get(bug_id).status == "reproduced"

    def test_filed_to_fixed_on_a_commit_alone_is_refused_naming_the_ruled_route(
        self, tmp_path, backend
    ):
        m = git_project(tmp_path / "proj", BACKENDS[backend])
        if backend == "sphinx_needs":
            (m.root / "backlog.rst").write_text(BACKLOG, encoding="utf-8")
            bug_id = "BUG_WRAPPED"
        else:
            bug_id = WorkItemDomain(m).new("bug", "A defect").id
        before = store_bytes(m)

        with pytest.raises(TransitionError) as exc:
            WorkItemDomain(m).transition(bug_id, "fixed", evidence="HEAD", cwd=m.root)

        message = str(exc.value)
        assert "--ruling" in message and "reproduced" in message
        assert store_bytes(m) == before

        # With a ruling the route is walked as IDEA_019 built it.
        item, _ = WorkItemDomain(m).transition(
            bug_id, "fixed", ruling=RULING, evidence="HEAD", cwd=m.root
        )
        assert item.status == "fixed"
        history = WorkItemDomain(Manifest.locate(m.root)).get(bug_id).history
        assert [h.get("intermediate", False) for h in history] == [True, False]
        assert all(h["ruling"] == RULING for h in history)


class TestTheSphinxNeedsHistoryMarker:
    def test_the_commit_is_written_in_the_history_block_and_read_back(self, tmp_path):
        m, bug_id = reproduced_bug(tmp_path, "sphinx_needs")
        sha = head(m.root)

        WorkItemDomain(m).transition(bug_id, "fixed", evidence="HEAD", cwd=m.root)

        text = (m.root / "backlog.rst").read_text(encoding="utf-8")
        block = text.split(HISTORY_MARKER, 1)[1].split("Closed bugs", 1)[0]
        assert f"evidence: {sha}" in block
        assert "ruling" not in block
        assert text.count(HISTORY_MARKER) == 1
        row = WorkItemDomain(Manifest.locate(m.root)).get(bug_id).history[-1]
        assert row == {
            "from": "reproduced",
            "to": "fixed",
            "reason": None,
            "forced": False,
            "evidence": sha,
        }


class TestAnIdeaCarriesItsCommitToo:
    def test_any_machine_edge_takes_a_commit(self, tmp_path):
        m = git_project(tmp_path / "proj")
        domain = WorkItemDomain(m)
        idea = domain.new("idea", "A want")
        domain.transition(idea.id, "accepted", evidence="HEAD", cwd=m.root)
        assert domain.get(idea.id).history[0]["evidence"] == head(m.root)


class TestTheCommitFromTheCli:
    def setup(self):
        run("init", "--host-install", "--ecosystem", "python")
        run("item", "new", "bug", "--title", "A defect")
        run("item", "transition", "BUG_001", "reproduced")

    def test_show_lists_the_resolved_commit_on_the_row(self, project, capsys):  # noqa: F811
        self.setup()
        sha = head(project)
        capsys.readouterr()

        assert run("item", "transition", "BUG_001", "fixed", "--evidence", sha[:9]) == 0
        assert "BUG_001 -> fixed" in out(capsys)

        run("item", "show", "BUG_001")
        shown = out(capsys)
        assert f"reproduced -> fixed  (evidence {sha[:12]})" in shown
        assert "ruled by" not in shown

        run("--json", "item", "show", "BUG_001")
        assert payload(capsys)["history"][-1]["evidence"] == sha

    def test_a_commit_that_does_not_resolve_is_refused(self, project, capsys):  # noqa: F811
        self.setup()
        capsys.readouterr()
        assert run("item", "transition", "BUG_001", "fixed", "--evidence", "nope") != 0
        assert "names no commit" in capsys.readouterr().err

    def test_a_ruling_without_its_commit_is_still_refused(self, project, capsys):  # noqa: F811
        self.setup()
        capsys.readouterr()
        assert run("item", "transition", "BUG_001", "fixed", "--ruling", RULING) == 2
        assert "--evidence" in capsys.readouterr().err
