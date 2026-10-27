"""IDEA_032 increment 3 — `domains/sweep`: the sweep run (ADR-010 D4, D7, D9).

A real repository and a real backlog; the suite itself is a stub runner
answering the configured commands with pytest-shaped output, so every
assertion is about what the sweep does with a run, not about pytest.
"""

import json
import subprocess
from datetime import date

import pytest

from invariant.core.errors import StateError, UsageError
from invariant.core.items import MarkdownBackend
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.core.store import JsonStore
from invariant.domains.sweep import SweepDomain

TODAY = date(2026, 10, 26)
FULL = "pytest -q"
COVER = "pytest -q --cov"


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "docs" / "backlog").mkdir(parents=True)
    (root / "docs" / "backlog" / ".keep").write_text("", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def manifest(root, gates=("coverage.meets_floor", "test.all_pass"), **extra):
    body = {
        "version": 1,
        "project": {"name": "demo", "ecosystems": ["python"]},
        "commands": {"test": FULL, "test_one": "pytest -q {selector}", "coverage": COVER},
        "items": {"backend": "markdown", "path": "docs/backlog"},
        "methodology": {
            "gates": {
                "coverage_min": 85,
                "schedule": {"cadence_days": 7, "gates": list(gates)},
            }
        },
    }
    for dotted, value in extra.items():
        node = body
        *parents, leaf = dotted.split("__")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return Manifest.from_raw(body, root=root, path=root / MANIFEST_RELPATH, env={})


class Suite:
    """Answers the configured commands like pytest would.

    ``failing`` fails in the whole-suite run; ``green_alone`` passes when re-run
    on its own; ``coverage`` is the per-file table the coverage run prints.
    """

    def __init__(self, failing=(), green_alone=(), coverage=None, executed=10, extra=""):
        self.failing = list(failing)
        self.green_alone = set(green_alone)
        self.coverage = {"src/a.py": 90.0} if coverage is None else coverage
        self.executed = executed
        self.extra = extra
        self.calls: list[str] = []

    def __call__(self, command, root):
        self.calls.append(command)
        if command in (FULL, COVER):
            lines = [f"FAILED {t} - AssertionError" for t in self.failing]
            if self.executed:
                passed = self.executed - len(self.failing)
                tally = f"{len(self.failing)} failed, " if self.failing else ""
                lines.append(f"{tally}{passed} passed in 0.1s")
            else:
                lines.append("no tests ran in 0.01s")
            if command == COVER:
                lines += [f"{path}   10   1   {pct:g}%" for path, pct in self.coverage.items()]
            lines.append(self.extra)
            code = 1 if self.failing else (5 if not self.executed else 0)
            return subprocess.CompletedProcess(command, code, "\n".join(lines), "")
        test = next(t for t in self.failing if t in command)
        if test in self.green_alone:
            return subprocess.CompletedProcess(command, 0, "1 passed in 0.01s", "")
        return subprocess.CompletedProcess(command, 1, f"FAILED {test}\n1 failed in 0.01s", "")

    def reruns(self):
        return [c for c in self.calls if c not in (FULL, COVER)]


def domain(root, suite, m=None, today=TODAY):
    m = m or manifest(root)
    return SweepDomain(m, JsonStore(m.state_root), runner=suite, today=today)


def items(root):
    return {i.id: i for i in MarkdownBackend(root / "docs" / "backlog").load_all()}


def commit_change(root, name="change"):
    (root / "src" / f"{name}.py").write_text("y = 2\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", name)


# -- preconditions (ruling 11) ----------------------------------------------


class TestWhereItMayRun:
    def test_it_refuses_inside_a_linked_worktree(self, proj, tmp_path):
        wt = tmp_path / "wt"
        git(proj, "worktree", "add", "-q", str(wt), "-b", "session/x")
        with pytest.raises(UsageError, match="main checkout"):
            domain(wt, Suite()).run()

    def test_it_refuses_on_a_detached_head(self, proj):
        git(proj, "checkout", "-q", "--detach")
        with pytest.raises(UsageError, match="detached"):
            domain(proj, Suite()).run()

    def test_it_refuses_a_tracked_change_and_names_it(self, proj):
        (proj / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
        suite = Suite()
        with pytest.raises(UsageError, match="src/a.py"):
            domain(proj, suite).run()
        assert suite.calls == [], "refused before anything ran"

    def test_an_untracked_file_does_not_count(self, proj):
        (proj / "scratch.txt").write_text("notes\n", encoding="utf-8")
        report = domain(proj, Suite()).run()
        assert report.committed

    def test_it_refuses_outside_a_repository(self, tmp_path):
        bare = tmp_path / "bare"
        bare.mkdir()
        m = manifest(bare, paths__state=".state")
        with pytest.raises(UsageError, match="repository"):
            SweepDomain(m, JsonStore(m.state_root), runner=Suite(), today=TODAY).run()

    def test_a_second_sweep_cannot_run_while_one_holds_the_lock(self, proj):
        d = domain(proj, Suite())
        with d.store.locked("sweep", timeout=0), pytest.raises(StateError, match="another sweep"):
            d.run()


# -- the first sweep: baseline only (ruling 7) --------------------------------


class TestTheFirstSweep:
    def test_it_files_nothing_and_commits_its_record(self, proj):
        head = git(proj, "rev-parse", "HEAD")
        report = domain(proj, Suite(failing=["tests/t.py::test_x"], coverage={"src/a.py": 50.0})).run()
        assert report.baseline
        assert report.filed == []
        assert items(proj) == {}
        rec = report.record
        assert rec.seq == 1 and rec.previous is None and rec.head == head
        assert rec.failing == ["tests/t.py::test_x"]
        assert rec.files == {"src/a.py": 50.0}
        assert report.current == ["floor:src/a.py", "test:tests/t.py::test_x"]
        assert git(proj, "log", "-1", "--format=%s") == f"Sweep: 2026-10-26 {head[:12]} (0 filed)"
        committed = git(proj, "show", "--name-only", "--format=", "HEAD").splitlines()
        assert committed == [f"docs/sweeps/2026-10-26-{head[:12]}.json"]
        assert git(proj, "status", "--porcelain") == ""

    def test_no_reruns_are_made_for_a_baseline(self, proj):
        suite = Suite(failing=["tests/t.py::test_x"])
        domain(proj, suite).run()
        assert suite.reruns() == []

    def test_the_record_holds_relative_paths_and_a_command_key_only(self, proj):
        absolute = str(proj / "src" / "a.py")
        report = domain(proj, Suite(coverage={absolute: 90.0, "/elsewhere/b.py": 10.0})).run()
        raw = json.loads((proj / report.record_path).read_text(encoding="utf-8"))
        assert raw["files"] == {"src/a.py": 90.0}
        assert raw["command"] == "coverage"
        assert str(proj) not in json.dumps(raw)
        assert report.outside_project == 1


# -- the commit range: one new red and one floor drop, then still broken -------


class TestTheCommitRange:
    def test_two_findings_filed_once_each_with_the_range(self, proj):
        first = domain(proj, Suite()).run()
        commit_change(proj)
        head = git(proj, "rev-parse", "HEAD")

        suite = Suite(failing=["tests/t.py::test_x"], coverage={"src/a.py": 60.0})
        second = domain(proj, suite).run()
        assert sorted(f["key"] for f in second.filed) == [
            "floor:src/a.py",
            "test:tests/t.py::test_x",
        ]
        filed = items(proj)
        assert len(filed) == 2
        for item in filed.values():
            assert item.type == "bug" and item.status == "filed"
            assert item.fields["sweep_range"] == f"{first.record.head}..{head}"
        floor = next(i for i in filed.values() if i.fields["sweep_key"] == "floor:src/a.py")
        assert "60%" in floor.body and "85%" in floor.body
        assert "1 failing" in floor.body, "a drop measured on a red run says so"
        assert second.record.seq == 2 and second.record.previous == first.record.head
        assert second.record.filed == second.filed_refs
        message = git(proj, "log", "-1", "--format=%s")
        assert message.endswith("(2 filed)")
        shown = git(proj, "show", "--name-only", "--format=", "HEAD").splitlines()
        assert len(shown) == 3, "the record and the two items, nothing else"
        assert suite.reruns() == ["pytest -q tests/t.py::test_x"]

        commit_change(proj, "later")
        third = domain(proj, Suite(failing=["tests/t.py::test_x"], coverage={"src/a.py": 60.0})).run()
        assert third.filed == []
        assert sorted(s["key"] for s in third.still_broken) == [
            "floor:src/a.py",
            "test:tests/t.py::test_x",
        ]
        assert len(items(proj)) == 2

    def test_what_recovers_is_reported_and_never_closed(self, proj):
        domain(proj, Suite()).run()
        domain(proj, Suite(failing=["tests/t.py::test_x"])).run()
        report = domain(proj, Suite()).run()
        assert report.recovered == ["test:tests/t.py::test_x"]
        (only,) = items(proj).values()
        assert only.status == "filed"

    def test_a_selector_that_needs_quoting_is_quoted(self, proj):
        domain(proj, Suite()).run()
        suite = Suite(failing=["tests/t.py::test_p[a b]"])
        domain(proj, suite).run()
        assert suite.reruns() == ["pytest -q 'tests/t.py::test_p[a b]'"]


# -- flaky (ruling 8, D7) -----------------------------------------------------


class TestFlaky:
    def test_green_alone_is_filed_once_as_flaky(self, proj):
        domain(proj, Suite()).run()
        flake = "tests/t.py::test_f"
        report = domain(proj, Suite(failing=[flake], green_alone=[flake])).run()
        assert [f["key"] for f in report.filed] == [f"flaky:{flake}"]
        assert report.record.flaky == [flake]
        assert report.record.failing == []
        again = domain(proj, Suite(failing=[flake], green_alone=[flake])).run()
        assert again.filed == []
        assert [s["key"] for s in again.still_broken] == [f"flaky:{flake}"]
        assert len(items(proj)) == 1

    def test_an_item_filed_by_hand_is_claimed_by_its_sweep_key(self, proj):
        domain(proj, Suite()).run()
        flake = "tests/t.py::test_f"
        (proj / "docs" / "backlog" / "BUG_081.md").write_text(
            "---\nid: BUG_081\ntype: bug\ntitle: flake\nstatus: filed\nresidue: []\n"
            f"history: []\nsweep_key: 'flaky:{flake}'\n---\n\nby hand\n",
            encoding="utf-8",
        )
        git(proj, "add", "-A")
        git(proj, "commit", "-qm", "claim")
        report = domain(proj, Suite(failing=[flake])).run()
        assert report.filed == []
        assert report.still_broken == [
            {"key": f"test:{flake}", "item": "BUG_081", "status": "filed", "regression": False}
        ]

    def test_a_re_run_that_executes_nothing_is_not_proof_of_green(self, proj):
        domain(proj, Suite()).run()

        class Empty(Suite):
            def __call__(self, command, root):
                if command in (FULL, COVER):
                    return super().__call__(command, root)
                self.calls.append(command)
                return subprocess.CompletedProcess(command, 5, "no tests ran in 0.01s", "")

        report = domain(proj, Empty(failing=["tests/t.py::test_x"])).run()
        assert [f["key"] for f in report.filed] == ["test:tests/t.py::test_x"]


class TestTheCap:
    def test_beyond_twenty_new_reds_one_suite_red_item_and_no_reruns(self, proj):
        domain(proj, Suite()).run()
        many = [f"tests/t.py::test_{n}" for n in range(21)]
        suite = Suite(failing=many, executed=30)
        report = domain(proj, suite).run()
        assert suite.reruns() == []
        assert report.over_cap
        assert [f["key"] for f in report.filed] == ["suite:red"]
        (item,) = items(proj).values()
        assert "21" in item.body and "systemic" in item.body


class TestUnreadableIdentities:
    def test_a_red_run_naming_no_test_is_one_suite_red(self, proj):
        domain(proj, Suite()).run()

        class Anonymous(Suite):
            def __call__(self, command, root):
                self.calls.append(command)
                return subprocess.CompletedProcess(
                    command, 1, "1 failed, 9 passed in 0.1s\nsrc/a.py 10 1 90%", ""
                )

        report = domain(proj, Anonymous()).run()
        assert [f["key"] for f in report.filed] == ["suite:red"]


class TestRegression:
    def test_an_item_called_fixed_that_is_still_red_is_named(self, proj):
        domain(proj, Suite()).run()
        domain(proj, Suite(failing=["tests/t.py::test_x"])).run()
        (bug,) = items(proj).values()
        path = proj / "docs" / "backlog" / f"{bug.id}.md"
        path.write_text(path.read_text(encoding="utf-8").replace("status: filed", "status: fixed"), encoding="utf-8")
        git(proj, "add", "-A")
        git(proj, "commit", "-qm", "called fixed")
        report = domain(proj, Suite(failing=["tests/t.py::test_x"])).run()
        assert report.regressions == [f"test:tests/t.py::test_x ({bug.id}, fixed)"]


# -- what records nothing ---------------------------------------------------


class TestWhatRecordsNothing:
    def test_dry_run_writes_files_and_commits_nothing(self, proj):
        domain(proj, Suite()).run()
        before = git(proj, "rev-parse", "HEAD")
        report = domain(proj, Suite(failing=["tests/t.py::test_x"])).run(dry_run=True)
        assert report.dry_run
        assert [f["key"] for f in report.filed] == ["test:tests/t.py::test_x"]
        assert report.filed[0]["item"] is None
        assert items(proj) == {}
        assert git(proj, "rev-parse", "HEAD") == before
        assert git(proj, "status", "--porcelain") == ""
        assert not report.committed

    def test_a_run_that_executed_nothing_records_nothing(self, proj):
        with pytest.raises(UsageError, match="no tests"):
            domain(proj, Suite(executed=0)).run()
        assert not (proj / "docs" / "sweeps").exists()

    def test_a_coverage_run_with_no_rows_records_nothing(self, proj):
        with pytest.raises(UsageError, match="no per-file"):
            domain(proj, Suite(coverage={})).run()
        assert not (proj / "docs" / "sweeps").exists()

    def test_a_test_only_sweep_runs_the_test_command(self, proj):
        m = manifest(proj, gates=("test.all_pass",))
        suite = Suite()
        report = domain(proj, suite, m=m).run()
        assert suite.calls == [FULL]
        assert report.record.command == "test" and report.record.files == {}


# -- privacy (D9) -----------------------------------------------------------


class TestPrivacy:
    def test_a_finding_refuses_the_commit_and_leaves_the_record(self, proj):
        before = git(proj, "rev-parse", "HEAD")
        report = domain(proj, Suite(failing=["tests/t.py::test_mail_someone@corp.test"])).run()
        assert not report.committed
        assert report.privacy and "emails" in report.privacy[0]
        assert "someone@corp.test" not in " ".join(report.privacy)
        assert git(proj, "rev-parse", "HEAD") == before
        assert (proj / report.record_path).is_file(), "left uncommitted, and untracked"
        again = domain(proj, Suite()).run()
        assert again.record.seq == 1, "an uncommitted record is never a baseline"


class TestHazards:
    def test_a_hazard_on_the_run_is_recorded(self, proj):
        report = domain(proj, Suite(extra="ERROR collecting tests/t.py")).run()
        assert report.record.hazards == ["pytest.import_error_masquerades_as_collection_error"]


# -- status and show --------------------------------------------------------


class TestStatusAndShow:
    def test_status_with_no_sweep(self, proj):
        status = domain(proj, Suite()).status()
        assert status["last"] is None
        assert status["overdue"] is True
        assert status["cadence_days"] == 7 and status["grace_days"] == 3

    def test_status_names_the_last_sweep_and_its_age(self, proj):
        domain(proj, Suite(), today=date(2026, 10, 15)).run()
        status = domain(proj, Suite()).status()
        assert status["last"]["seq"] == 1
        assert status["age_days"] == 11
        assert status["overdue"] is True

    def test_show_reads_the_latest_or_a_named_record(self, proj):
        first = domain(proj, Suite()).run()
        commit_change(proj)
        second = domain(proj, Suite()).run()
        d = domain(proj, Suite())
        assert d.show(None).seq == 2
        assert d.show("1").head == first.record.head
        assert d.show(second.record.name).seq == 2
        assert d.show(first.record.head[:8]).seq == 1
        with pytest.raises(UsageError, match="no sweep record"):
            d.show("99")

    def test_show_with_no_record(self, proj):
        with pytest.raises(UsageError, match="no sweep"):
            domain(proj, Suite()).show(None)


class TestTheDeclaredStore:
    def test_paths_sweeps_defaults_to_docs_sweeps(self, proj):
        assert manifest(proj).get("paths.sweeps") == "docs/sweeps"

    def test_a_declared_store_elsewhere_is_honoured(self, proj):
        m = manifest(proj, paths__sweeps="evidence/sweeps")
        report = domain(proj, Suite(), m=m).run()
        assert report.record_path.startswith("evidence/sweeps/")


class TestAStagedFileIsATrackedChange:
    def test_it_is_refused_and_named(self, proj):
        (proj / "notes.txt").write_text("mine\n", encoding="utf-8")
        git(proj, "add", "notes.txt")
        with pytest.raises(UsageError, match="notes.txt"):
            domain(proj, Suite()).run()


def test_a_failed_commit_is_reported_not_raised(proj, monkeypatch):
    from invariant.core import git as core_git

    monkeypatch.setattr(core_git, "commit_only", lambda cwd, paths, message: (False, "hook said no"))
    report = domain(proj, Suite()).run()
    assert not report.committed
    assert "hook said no" in report.commit_error



class TestRefusedBeforeRunning:
    def test_no_work_item_backend(self, proj):
        suite = Suite()
        with pytest.raises(UsageError, match="items.backend"):
            domain(proj, suite, m=manifest(proj, items__backend="none")).run()
        assert suite.calls == []

    def test_an_unreadable_coverage_baseline(self, proj):
        suite = Suite()
        m = manifest(proj, methodology__gates__coverage_baseline="missing.txt")
        with pytest.raises(UsageError, match="baseline"):
            domain(proj, suite, m=m).run()
        assert suite.calls == []

    def test_git_unable_to_say_whether_the_tree_is_clean(self, proj, monkeypatch):
        from invariant.core import git as core_git

        monkeypatch.setattr(core_git, "tracked_changes", lambda cwd: None)
        with pytest.raises(UsageError, match="clean"):
            domain(proj, Suite()).run()

    def test_git_unable_to_read_head(self, proj, monkeypatch):
        from invariant.core import sweep as core_sweep

        monkeypatch.setattr(core_sweep, "read_records", lambda *a: None)
        with pytest.raises(UsageError, match="could not read"):
            domain(proj, Suite()).status()


class TestNoScopedCommand:
    def test_a_red_that_cannot_be_re_run_is_kept_red_and_noted(self, proj):
        m = manifest(proj, commands__test_one=None)
        domain(proj, Suite(), m=m).run()
        report = domain(proj, Suite(failing=["tests/t.py::test_x"]), m=m).run()
        assert [f["key"] for f in report.filed] == ["test:tests/t.py::test_x"]
        assert any("could not be re-run alone" in n for n in report.notes)


class TestNoPreviousFigures:
    def test_a_coverage_sweep_after_a_test_sweep_files_no_drop(self, proj):
        domain(proj, Suite(), m=manifest(proj, gates=("test.all_pass",))).run()
        report = domain(proj, Suite(coverage={"src/a.py": 10.0})).run()
        assert report.filed == []
        assert report.unbaselined == ["floor:src/a.py"]
        assert any("measured no coverage" in n for n in report.notes)


class TestPrivacyOff:
    def test_a_project_with_privacy_off_is_not_scanned(self, proj):
        m = manifest(proj, privacy__enabled=False)
        report = domain(proj, Suite(failing=["tests/t.py::test_x_someone@corp.test"]), m=m).run()
        assert report.committed and report.privacy == []


class TestTheItemBody:
    def test_a_hazard_on_the_run_is_named_in_what_is_filed(self, proj):
        domain(proj, Suite()).run()
        domain(proj, Suite(failing=["tests/t.py::test_x"], extra="ERROR collecting tests/t.py")).run()
        (item,) = [i for i in items(proj).values() if "test_x" in i.fields["sweep_key"]]
        assert "pytest.import_error_masquerades_as_collection_error" in item.body


# Repro for BUG_090 — see the work-item entry.
# Fails while the defect is live. When the fix lands, delete the
# expected-failure wrapper; the assertion must then pass unchanged.
@pytest.mark.xfail(
    strict=True,
    reason="BUG_090 is live — delete this marker with the fix",
)
def test_BUG_090_a_sweep_commits_on_a_project_with_device_ids_enabled(proj):
    from invariant.core.privacy import DEFAULT_PATTERNS

    m = manifest(proj, privacy__patterns=[*DEFAULT_PATTERNS, "device_ids"])

    # The baseline sweep: the record alone must commit.
    first = domain(proj, Suite(), m=m).run()
    assert first.committed and first.privacy == [], (
        f"baseline sweep NOT committed: {first.commit_error}; findings: {first.privacy}"
    )

    # A later sweep that files a bug: the record and the filed item must commit.
    commit_change(proj)
    second = domain(proj, Suite(failing=["tests/t.py::test_x"]), m=m).run()
    assert [f["key"] for f in second.filed] == ["test:tests/t.py::test_x"]
    assert second.committed and second.privacy == [], (
        f"filing sweep NOT committed: {second.commit_error}; findings: {second.privacy}"
    )
