"""IDEA_019 — a ruled closure for a bug that was already fixed.

`bug-reproducer`'s ALREADY-FIXED verdict names a fixing commit and a test that
goes red with that commit reverted. The engine's only route from `filed` used
to be `not_reproducible`, which records the wrong meaning. A ruled transition
records the verdict as what it is: a reproduction on the pre-fix tree, then the
fix, in one command and one write, each row carrying who ruled and on which
commit. `fixed` stays reachable only through `reproduced`.
"""

import difflib
import json
import subprocess

import pytest
from test_bug_065_repro import BACKLOG, ENTRY_STATUS_LINE
from test_cli import (
    out,
    payload,
    project,  # noqa: F401 -- fixture
    run,
)
from test_domains import git_project

from invariant.core import git
from invariant.core.errors import TransitionError, UsageError
from invariant.core.rules import RuleContext, evaluate
from invariant.domains.workitem import WorkItemDomain

RULING = "maintainer, 2026-10-22"


def head(root) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def m(tmp_path):
    return git_project(tmp_path / "proj")


class TestResolveCommit:
    def test_a_commit_resolves_to_its_full_hash(self, m):
        sha = head(m.root)
        assert git.resolve_commit(m.root, sha[:7]) == sha
        assert git.resolve_commit(m.root, "HEAD") == sha

    def test_a_name_that_is_no_commit_resolves_to_nothing(self, m):
        assert git.resolve_commit(m.root, "deadbeefdeadbeef") is None
        assert git.resolve_commit(m.root, "") is None

    def test_an_option_shaped_name_is_never_passed_to_git(self, m):
        assert git.resolve_commit(m.root, "--all") is None

    def test_outside_a_repository_nothing_resolves(self, tmp_path):
        assert git.resolve_commit(tmp_path, "HEAD") is None


class TestTheRuledClosure:
    def test_a_filed_bug_reaches_fixed_through_a_recorded_reproduction(self, m):
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect")
        sha = head(m.root)

        item, _ = domain.transition(
            bug.id, "fixed", ruling=RULING, evidence=sha[:8], cwd=m.root
        )

        assert item.status == "fixed"
        stored = domain.get(bug.id)
        assert [(h["from"], h["to"]) for h in stored.history] == [
            ("filed", "reproduced"),
            ("reproduced", "fixed"),
        ]
        for row in stored.history:
            assert row["ruling"] == RULING
            assert row["evidence"] == sha
            assert row["forced"] is False
        # The reproduction row says it was recorded on the ruling, not walked.
        assert stored.history[0]["intermediate"] is True
        assert "intermediate" not in stored.history[1]

    def test_the_ruled_history_is_explained_by_the_machine(self, m):
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect")
        domain.transition(bug.id, "fixed", ruling=RULING, evidence="HEAD", cwd=m.root)
        [verdict] = evaluate(
            ["item.history_matches_status"], RuleContext(manifest=m, cwd=m.root)
        )
        assert verdict.verdict.ok and not verdict.verdict.skipped, verdict.verdict.message

    def test_without_a_ruling_filed_to_fixed_stays_illegal_and_names_the_route(self, m):
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect")
        with pytest.raises(TransitionError, match="--ruling") as exc:
            domain.transition(bug.id, "fixed", cwd=m.root)
        assert "reproduced" in str(exc.value)
        assert domain.get(bug.id).status == "filed"

    def test_a_ruling_without_evidence_is_refused_and_says_so(self, m):
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect")
        with pytest.raises(UsageError, match="--evidence"):
            domain.transition(bug.id, "fixed", ruling=RULING, cwd=m.root)
        assert domain.get(bug.id).history == []

    def test_evidence_without_a_ruling_is_refused_and_says_so(self, m):
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect")
        with pytest.raises(UsageError, match="--ruling"):
            domain.transition(bug.id, "fixed", ruling="   ", evidence="HEAD", cwd=m.root)

    def test_evidence_that_names_no_commit_is_refused(self, m):
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect")
        with pytest.raises(UsageError, match="names no commit"):
            domain.transition(
                bug.id, "fixed", ruling=RULING, evidence="deadbeefdead", cwd=m.root
            )
        assert domain.get(bug.id).status == "filed"

    def test_a_ruling_on_a_direct_edge_annotates_it_without_a_detour(self, m):
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect")
        domain.transition(bug.id, "reproduced", cwd=m.root)
        domain.transition(bug.id, "fixed", ruling=RULING, evidence="HEAD", cwd=m.root)
        history = domain.get(bug.id).history
        assert [(h["from"], h["to"]) for h in history] == [
            ("filed", "reproduced"),
            ("reproduced", "fixed"),
        ]
        assert "ruling" not in history[0]
        assert history[1]["ruling"] == RULING and "intermediate" not in history[1]

    def test_a_ruling_reaches_no_further_than_one_recorded_step(self, m):
        """`filed -> verified` would need two unwalked states; the ruling covers one."""
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect")
        with pytest.raises(TransitionError):
            domain.transition(
                bug.id, "verified", ruling=RULING, evidence="HEAD", cwd=m.root
            )

    def test_a_blocking_gate_on_either_step_refuses_the_whole_command(
        self, tmp_path, monkeypatch
    ):
        """One write or none: a refused second step leaves no reproduction row."""
        from invariant.core import rules
        from invariant.core.errors import GateFailure

        m = git_project(tmp_path / "proj")
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect")
        real = rules.evaluate

        def blocking_on_fixed(gate_ids, ctx):
            verdicts = real(gate_ids, ctx)
            if ctx.extra.get("target_status") == "fixed":
                for v in verdicts:
                    if v.rule_id == "item.bug_reproduced_before_fix":
                        object.__setattr__(
                            v, "verdict", rules.Verdict.fail("refused for the test")
                        )
            return verdicts

        monkeypatch.setattr("invariant.domains.workitem.evaluate", blocking_on_fixed)
        with pytest.raises(GateFailure):
            domain.transition(bug.id, "fixed", ruling=RULING, evidence="HEAD", cwd=m.root)
        stored = domain.get(bug.id)
        assert stored.status == "filed" and stored.history == []

    def test_force_without_a_reason_is_still_refused_on_a_ruled_walk(
        self, tmp_path, monkeypatch
    ):
        from invariant.core import rules

        m = git_project(tmp_path / "proj")
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect")
        real = rules.evaluate

        def always_blocking(gate_ids, ctx):
            verdicts = real(gate_ids, ctx)
            for v in verdicts:
                object.__setattr__(v, "verdict", rules.Verdict.fail("refused"))
            return verdicts

        monkeypatch.setattr("invariant.domains.workitem.evaluate", always_blocking)
        with pytest.raises(UsageError, match="--reason"):
            domain.transition(
                bug.id, "fixed", ruling=RULING, evidence="HEAD", force=True, cwd=m.root
            )
        item, _ = domain.transition(
            bug.id, "fixed", ruling=RULING, evidence="HEAD", force=True,
            reason="gate under test", cwd=m.root,
        )
        assert item.status == "fixed"
        assert [h["forced"] for h in domain.get(bug.id).history] == [False, True]


class TestATransitionNeverRewritesTheBody:
    """The guarantee a ruled closure leans on, pinned on every backend."""

    BODY = (
        "**Symptom.** A paragraph that wraps\n"
        "over two lines.\n"
        "\n"
        "* a list item\n"
        "* another, with `code`\n"
        "\n"
        "    an indented block\n"
        "\n"
        "Closing prose."
    )

    def test_markdown_body_bytes_survive(self, m):
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect", body=self.BODY)
        path = m.root / "docs" / "backlog" / f"{bug.id}.md"
        before = path.read_text(encoding="utf-8").split("\n---\n", 1)[1]

        domain.transition(bug.id, "fixed", ruling=RULING, evidence="HEAD", cwd=m.root)

        after = path.read_text(encoding="utf-8").split("\n---\n", 1)[1]
        assert after == before
        assert domain.get(bug.id).body == self.BODY

    def test_json_body_survives(self, tmp_path):
        m = git_project(
            tmp_path / "proj",
            {"version": 1, "items": {"backend": "json", "path": "backlog.json"}},
        )
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect", body=self.BODY)

        domain.transition(bug.id, "fixed", ruling=RULING, evidence="HEAD", cwd=m.root)

        raw = json.loads((m.root / "backlog.json").read_text(encoding="utf-8"))
        assert raw["items"][0]["body"] == self.BODY
        assert raw["items"][0]["history"][0]["ruling"] == RULING

    def test_sphinx_needs_changes_only_the_status_line(self, tmp_path):
        """BUG_065 pinned on the ruled path: a wrapped option, a leading role and
        the blank line before the next heading all survive byte for byte."""
        m = git_project(
            tmp_path / "proj",
            {"version": 1, "items": {"backend": "sphinx_needs", "path": "backlog.rst"}},
        )
        path = m.root / "backlog.rst"
        path.write_text(BACKLOG, encoding="utf-8")
        before = path.read_text(encoding="utf-8").splitlines()

        WorkItemDomain(m).transition(
            "BUG_WRAPPED", "fixed", ruling=RULING, evidence="HEAD", cwd=m.root
        )

        after = path.read_text(encoding="utf-8").splitlines()
        diff = list(difflib.unified_diff(before, after, lineterm="", n=0))
        removed = [
            line[1:] for line in diff if line.startswith("-") and not line.startswith("---")
        ]
        added = [
            line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")
        ]
        assert removed == [ENTRY_STATUS_LINE], "\n".join(diff)
        assert added == ["   :status: fixed"], "\n".join(diff)


class TestTheRuledClosureFromTheCli:
    def setup(self):
        run("init", "--ecosystem", "python")
        run("item", "new", "bug", "--title", "A defect")

    def test_one_command_records_the_ruling(self, project, capsys):  # noqa: F811
        self.setup()
        sha = head(project)
        assert run(
            "item", "transition", "BUG_001", "fixed",
            "--ruling", RULING, "--evidence", sha[:9],
        ) == 0
        text = out(capsys)
        assert "BUG_001 -> fixed" in text and "through reproduced" in text

        run("--json", "item", "show", "BUG_001")
        shown = payload(capsys)
        assert shown["status"] == "fixed"
        assert shown["history"][0]["evidence"] == sha

    def test_show_tells_a_ruled_close_from_a_failed_reproduction(
        self, project, capsys  # noqa: F811
    ):
        self.setup()
        run("item", "new", "bug", "--title", "Another")
        run("item", "transition", "BUG_001", "fixed", "--ruling", RULING,
            "--evidence", "HEAD")
        run("item", "transition", "BUG_002", "not_reproducible", "--reason",
            "did not fire")
        capsys.readouterr()

        run("item", "show", "BUG_001")
        ruled = out(capsys)
        assert "filed -> reproduced" in ruled and "reproduced -> fixed" in ruled
        assert f"ruled by {RULING}" in ruled
        assert "recorded on the ruling" in ruled

        run("item", "show", "BUG_002")
        failed = out(capsys)
        assert "filed -> not_reproducible" in failed and "did not fire" in failed
        assert "ruled by" not in failed

    def test_a_missing_half_is_refused(self, project, capsys):  # noqa: F811
        self.setup()
        assert run("item", "transition", "BUG_001", "fixed", "--ruling", RULING) == 2
        assert "--evidence" in capsys.readouterr().err

    def test_without_flags_the_refusal_names_the_ruled_route(
        self, project, capsys  # noqa: F811
    ):
        self.setup()
        assert run("item", "transition", "BUG_001", "fixed") != 0
        assert "--ruling" in capsys.readouterr().err
