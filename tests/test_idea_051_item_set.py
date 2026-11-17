"""IDEA_051 — ``item set`` changes an existing item's fields, in its history.

A field fixed at ``item new`` could only change by a hand edit of the store,
which a project allowing backlog writes only through ``invariant item`` cannot
make. ``item set`` sets, adds to, removes from and drops fields on every
backend, records each change as a history row with no ``from`` and ``to``,
refuses what ``item new`` refuses and the item's own keys, and writes only
the items store (DEC_ADR_023_D1 to D3).
"""

import subprocess
from datetime import date, datetime

import pytest
from test_adr_018_decisions import (
    build,
    green_with,
    toy,  # noqa: F401 -- fixture
    worktree,
)
from test_bug_065_repro import BACKLOG
from test_cli import (
    out,
    payload,
    project,  # noqa: F401 -- fixture
    run,
)
from test_domains import git_project

from invariant.core.errors import GateFailure, UsageError
from invariant.core.items import HISTORY_MARKER
from invariant.core.manifest import Manifest
from invariant.core.rules import RuleContext, evaluate, is_exempt_path
from invariant.domains.workitem import WorkItemDomain

DAY = date(2026, 11, 16)

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


def a_bug(tmp_path, backend: str):
    """A project on ``backend`` holding one filed bug with severity and affects."""
    m = git_project(tmp_path / "proj", BACKENDS[backend])
    bug = WorkItemDomain(m).new(
        "bug",
        "A defect",
        fields={"severity": "low", "affects": "COMP_A, COMP_B"},
        body="The body.",
    )
    return m, bug.id


def fresh(m, item_id):
    """The item as a new domain reads it back from the store."""
    return WorkItemDomain(Manifest.locate(m.root)).get(item_id)


def set_fields(m, item_id, *changes, reason="because", **kwargs):
    return WorkItemDomain(m).set_fields(
        item_id, list(changes), reason=reason, cwd=m.root, today=DAY, **kwargs
    )


@pytest.mark.parametrize("backend", sorted(BACKENDS))
class TestEveryBackend:
    def test_setting_a_field_changes_that_field_and_records_a_row(self, tmp_path, backend):
        m, bug_id = a_bug(tmp_path, backend)
        before = fresh(m, bug_id)

        item, rows = set_fields(m, bug_id, ("set", "severity", "high"), reason="user ruling")

        stored = fresh(m, bug_id)
        assert stored.fields == {**before.fields, "severity": "high"}
        assert (stored.title, stored.status, stored.body, stored.residue) == (
            before.title,
            before.status,
            before.body,
            before.residue,
        )
        row = {
            "field": "severity",
            "was": "low",
            "now": "high",
            "reason": "user ruling",
            "date": "2026-11-16",
        }
        assert rows == [row]
        assert stored.history == [row]
        assert item.history == stored.history

    def test_add_appends_to_the_list_and_creates_an_absent_field(self, tmp_path, backend):
        m, bug_id = a_bug(tmp_path, backend)

        _, rows = set_fields(
            m,
            bug_id,
            ("add", "affects", "COMP_C"),
            ("add", "affects", "COMP_A"),
            ("add", "batch", "items"),
        )

        stored = fresh(m, bug_id)
        assert stored.fields["affects"] == "COMP_A, COMP_B, COMP_C"
        assert stored.fields["batch"] == "items"
        assert [(r["field"], r["was"], r["now"]) for r in rows] == [
            ("affects", "COMP_A, COMP_B", "COMP_A, COMP_B, COMP_C"),
            ("batch", None, "items"),
        ]

    def test_remove_drops_a_value_and_the_field_once_empty(self, tmp_path, backend):
        m, bug_id = a_bug(tmp_path, backend)

        set_fields(m, bug_id, ("remove", "affects", "COMP_A"))
        assert fresh(m, bug_id).fields["affects"] == "COMP_B"

        _, rows = set_fields(m, bug_id, ("remove", "affects", "COMP_B"))
        assert "affects" not in fresh(m, bug_id).fields
        assert rows[0]["now"] is None

    def test_unset_drops_the_field(self, tmp_path, backend):
        m, bug_id = a_bug(tmp_path, backend)

        _, rows = set_fields(m, bug_id, ("unset", "severity", None))

        assert "severity" not in fresh(m, bug_id).fields
        assert (rows[0]["was"], rows[0]["now"]) == ("low", None)

    def test_a_change_that_changes_nothing_writes_nothing(self, tmp_path, backend):
        m, bug_id = a_bug(tmp_path, backend)
        before = store_bytes(m)

        item, rows = set_fields(
            m, bug_id, ("set", "severity", "low"), ("add", "affects", "COMP_B")
        )

        assert rows == []
        assert item.history == []
        assert store_bytes(m) == before

    @pytest.mark.parametrize(
        ("changes", "named"),
        [
            ([("set", "status", "fixed")], "item transition"),
            ([("set", "id", "BUG_999")], "id"),
            ([("set", "title", "x")], "title"),
            ([("set", "type", "idea")], "type"),
            ([("add", "residue", "x")], "item residue"),
            ([("unset", "history", None)], "history"),
            ([("set", "bad key", "x")], "bad key"),
            ([("set", "severity", "")], "--unset"),
            ([("set", "severity", "two\nlines")], "line break"),
            ([("remove", "affects", "COMP_Z")], "COMP_Z"),
            ([("unset", "batch", None)], "batch"),
            ([("set", "severity", "a"), ("unset", "severity", None)], "severity"),
            ([("set", "severity", "a"), ("set", "severity", "b")], "severity"),
            ([("add", "affects", "COMP_C"), ("remove", "affects", "COMP_A")], "affects"),
            ([("move", "severity", "x")], "move"),
            ([], "nothing to change"),
        ],
    )
    def test_a_refused_change_writes_nothing(self, tmp_path, backend, changes, named):
        m, bug_id = a_bug(tmp_path, backend)
        before = store_bytes(m)

        with pytest.raises(UsageError) as exc:
            set_fields(m, bug_id, *changes)

        assert named in str(exc.value)
        assert store_bytes(m) == before

    def test_a_reason_is_required(self, tmp_path, backend):
        m, bug_id = a_bug(tmp_path, backend)
        before = store_bytes(m)

        with pytest.raises(UsageError, match="--reason"):
            set_fields(m, bug_id, ("set", "severity", "high"), reason="  ")

        assert store_bytes(m) == before

    def test_the_commit_is_resolved_and_recorded(self, tmp_path, backend):
        m, bug_id = a_bug(tmp_path, backend)
        sha = head(m.root)

        set_fields(m, bug_id, ("set", "severity", "high"), evidence=sha[:8])

        assert fresh(m, bug_id).history[-1]["evidence"] == sha

    def test_a_commit_that_does_not_resolve_writes_nothing(self, tmp_path, backend):
        m, bug_id = a_bug(tmp_path, backend)
        before = store_bytes(m)

        with pytest.raises(UsageError, match="names no commit"):
            set_fields(m, bug_id, ("set", "severity", "high"), evidence="deadbeefdead")

        assert store_bytes(m) == before

    def test_a_closed_item_s_fields_may_change(self, tmp_path, backend):
        m = git_project(tmp_path / "proj", BACKENDS[backend])
        domain = WorkItemDomain(m)
        idea = domain.new("idea", "A want", fields={"priority": "low"})
        for status in ("accepted", "in_progress", "done"):
            domain.transition(idea.id, status, cwd=m.root)

        set_fields(m, idea.id, ("set", "priority", "high"))

        stored = fresh(m, idea.id)
        assert (stored.status, stored.fields["priority"]) == ("done", "high")

    def test_the_history_rule_does_not_read_a_field_row_as_a_status_move(
        self, tmp_path, backend
    ):
        m, bug_id = a_bug(tmp_path, backend)
        domain = WorkItemDomain(m)
        set_fields(m, bug_id, ("set", "severity", "high"))
        domain.transition(bug_id, "reproduced", cwd=m.root)
        set_fields(m, bug_id, ("add", "affects", "COMP_C"))
        domain.transition(bug_id, "fixed", cwd=m.root)

        (result,) = evaluate(
            ["item.history_matches_status"], RuleContext(manifest=m, cwd=m.root)
        )

        assert result.verdict.ok, result.render()
        assert [bool(r.get("field")) for r in fresh(m, bug_id).history] == [
            True,
            False,
            True,
            False,
        ]

    def test_a_status_with_only_field_rows_still_reads_as_unrecorded(
        self, tmp_path, backend
    ):
        m, bug_id = a_bug(tmp_path, backend)
        domain = WorkItemDomain(m)
        item = domain.get(bug_id)
        item.status = "fixed"  # a hand-set status, as an unmanaged backlog has
        domain.backend.save(item)
        set_fields(m, bug_id, ("set", "severity", "high"))

        (result,) = evaluate(
            ["item.history_matches_status"], RuleContext(manifest=m, cwd=m.root)
        )

        assert not result.verdict.ok
        assert f"{bug_id}: status 'fixed' with no recorded transition" in (
            result.verdict.detail
        )

    def test_only_the_items_store_is_written_and_it_is_exempt(self, tmp_path, backend):
        m, bug_id = a_bug(tmp_path, backend)
        subprocess.run(["git", "add", "-A"], cwd=m.root, check=True)
        subprocess.run(["git", "commit", "-qm", "filed"], cwd=m.root, check=True)

        set_fields(m, bug_id, ("set", "severity", "high"))

        changed = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=m.root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        assert len(changed) == 1
        written = m.root / changed[0][3:]
        assert is_exempt_path(m, m.root, written)


class TestTheSphinxNeedsBackend:
    def project(self, tmp_path):
        m = git_project(tmp_path / "proj", BACKENDS["sphinx_needs"])
        path = m.root / "backlog.rst"
        path.write_text(BACKLOG, encoding="utf-8")
        return m, path

    def test_adding_to_affects_leaves_every_other_byte(self, tmp_path):
        m, path = self.project(tmp_path)
        before = path.read_text(encoding="utf-8").splitlines(keepends=True)

        set_fields(m, "BUG_WRAPPED", ("add", "affects", "COMP_CORE_ITEMS"))

        after = path.read_text(encoding="utf-8").splitlines(keepends=True)
        # The wrapped area and its role line stay, the option lands after the
        # last option, and the history block follows the body.
        at = before.index("          :doc:`architecture/overview` for the owner\n") + 1
        assert after[:at] == before[:at]
        assert after[at] == "   :affects: COMP_CORE_ITEMS\n"
        body_end = before.index("   The body of the entry under test.\n") + 1
        assert after[at + 1 : body_end + 1] == before[at:body_end]
        tail = before[body_end:]
        assert after[-len(tail) :] == tail
        block = after[body_end + 1 : -len(tail)]
        assert block[:2] == ["\n", f"   {HISTORY_MARKER}\n"]
        assert "      - field: affects\n" in block

    def test_setting_a_neighbour_leaves_the_wrapped_entry_alone(self, tmp_path):
        m, path = self.project(tmp_path)
        before = path.read_text(encoding="utf-8")

        set_fields(m, "BUG_NEIGHBOUR", ("set", "severity", "high"))

        after = path.read_text(encoding="utf-8")
        assert after.split(".. bug:: Wrapped")[1].split("Closed bugs")[1] == (
            before.split(".. bug:: Wrapped")[1].split("Closed bugs")[1]
        )
        assert "   :severity: high\n" in after.split(".. bug:: Wrapped")[0]
        assert after.split(".. bug:: Wrapped")[1].split("Closed bugs")[0] == (
            before.split(".. bug:: Wrapped")[1].split("Closed bugs")[0]
        )

    def test_unset_removes_the_option_line_alone(self, tmp_path):
        m, path = self.project(tmp_path)

        set_fields(m, "BUG_NEIGHBOUR", ("unset", "severity", None))

        after = path.read_text(encoding="utf-8")
        assert ":severity: low" not in after
        assert "severity" not in fresh(m, "BUG_NEIGHBOUR").fields
        assert ":severity: high" in after  # the wrapped entry's own

    @pytest.mark.parametrize("key", ["tags", "hide", "delete", "links"])
    def test_a_need_directive_option_is_refused(self, tmp_path, key):
        m, path = self.project(tmp_path)
        before = path.read_bytes()

        with pytest.raises(UsageError, match=key):
            set_fields(m, "BUG_NEIGHBOUR", ("set", key, "x"))

        assert path.read_bytes() == before


class TestTheGatesItemNewApplies:
    def test_a_decision_no_build_holds_is_refused_and_nothing_written(
        self, toy, monkeypatch  # noqa: F811
    ):
        wt, fake = worktree(toy)
        green_with(fake, "DEC_ADR_001")
        build(wt)
        monkeypatch.chdir(wt)
        m = Manifest.locate(wt)
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "Broken")
        before = (wt / "docs/backlog.rst").read_bytes()

        with pytest.raises(GateFailure) as raised:
            domain.set_fields(
                bug.id, [("add", "decided_by", "DEC_ADR_404")], reason="linked", cwd=wt
            )

        assert "DEC_ADR_404" in raised.value.verdicts[0].verdict.message
        assert (wt / "docs/backlog.rst").read_bytes() == before

        domain.set_fields(
            bug.id, [("add", "decided_by", "DEC_ADR_001")], reason="linked", cwd=wt
        )
        assert domain.get(bug.id).fields["decided_by"] == "DEC_ADR_001"


class TestTheCommand:
    def setup(self):
        run("init", "--host-install", "--ecosystem", "python")
        run("item", "new", "bug", "--title", "A defect", "--field", "severity=low")

    def test_set_then_show_lists_the_change_with_its_date_and_reason(
        self, project, capsys  # noqa: F811
    ):
        self.setup()
        capsys.readouterr()

        code = run(
            "item", "set", "BUG_001", "--field", "severity=high",
            "--add", "affects=COMP_A", "--reason", "user ruling",
        )
        assert code == 0
        text = out(capsys)
        assert "BUG_001  severity: low -> high" in text
        assert "BUG_001  affects: (unset) -> COMP_A" in text

        run("item", "show", "BUG_001")
        shown = out(capsys)
        today = datetime.now().astimezone().date().isoformat()
        assert f"  history  severity: low -> high  (user ruling; {today})" in shown
        assert f"affects: (unset) -> COMP_A  (user ruling; {today})" in shown

    def test_json_names_the_rows_written(self, project, capsys):  # noqa: F811
        self.setup()
        sha = head(project)
        capsys.readouterr()

        run(
            "--json", "item", "set", "BUG_001", "--unset", "severity",
            "--reason", "r", "--evidence", sha[:9],
        )

        doc = payload(capsys)
        assert [r["field"] for r in doc["changed"]] == ["severity"]
        assert doc["changed"][0]["evidence"] == sha
        assert "severity" not in doc["fields"]

        run("item", "show", "BUG_001")
        today = datetime.now().astimezone().date().isoformat()
        assert f"severity: low -> (unset)  (r; {today}; evidence {sha[:12]})" in out(capsys)

    def test_nothing_changed_says_so(self, project, capsys):  # noqa: F811
        self.setup()
        capsys.readouterr()

        assert run("item", "set", "BUG_001", "--field", "severity=low", "--reason", "r") == 0
        assert "BUG_001: nothing changed" in out(capsys)

    def test_status_is_refused_naming_transition(self, project, capsys):  # noqa: F811
        self.setup()
        capsys.readouterr()

        code = run("item", "set", "BUG_001", "--field", "status=fixed", "--reason", "r")

        assert code == 2
        assert "item transition" in capsys.readouterr().err

    def test_a_pair_without_equals_is_refused(self, project, capsys):  # noqa: F811
        self.setup()
        capsys.readouterr()

        assert run("item", "set", "BUG_001", "--add", "affects", "--reason", "r") == 2
        assert "not key=value" in capsys.readouterr().err

    def test_an_unlinked_decision_exits_1(self, toy, monkeypatch, capsys):  # noqa: F811
        wt, _ = worktree(toy)
        monkeypatch.chdir(wt)
        WorkItemDomain(Manifest.locate(wt)).new("bug", "Broken")
        capsys.readouterr()

        code = run(
            "item", "set", "BUG_001", "--add", "decided_by=DEC_ADR_404", "--reason", "r"
        )

        captured = capsys.readouterr()
        assert code == 1
        assert "DEC_ADR_404" in captured.out + captured.err
