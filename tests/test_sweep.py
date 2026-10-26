"""IDEA_032 increment 3 — `core/sweep`, the sweep's pure half (ADR-010 D4, D7, D9).

The record type, reading committed records from a git ref, the diff against
the last sweep (new, still broken, recovered, floor drops), the settling of
new reds by their isolated re-runs (flaky, the cap), the dedup against open
items, and the age arithmetic `sweep.fresh` will read in increment 4. The
floor policy a drop is judged by (non_source, coverage_exclude, the baseline
ratchet) is `core.rules.sweep_below_floor`, tested here beside its reader.
"""

import json
import subprocess
from datetime import date

import pytest

from invariant.core import sweep
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.core.rules import sweep_below_floor

HEAD_A = "a" * 40
HEAD_B = "b" * 40


def record(**overrides) -> sweep.SweepRecord:
    values = {
        "seq": 1,
        "ran_on": "2026-10-19",
        "branch": "main",
        "head": HEAD_A,
        "previous": None,
        "command": "coverage",
        "executed": 10,
        "failed": 0,
        "passed": True,
        "failing": [],
        "flaky": [],
        "files": {"src/a.py": 90.0},
        "floor": 85.0,
        "hazards": [],
        "filed": [],
        "still_broken": [],
        "recovered": [],
    }
    values.update(overrides)
    return sweep.SweepRecord(**values)


# -- keys --------------------------------------------------------------------


class TestKeys:
    def test_each_finding_has_one_spelling(self):
        assert sweep.test_key("t.py::x") == "test:t.py::x"
        assert sweep.flaky_key("t.py::x") == "flaky:t.py::x"
        assert sweep.floor_key("src/a.py") == "floor:src/a.py"
        assert sweep.SUITE_RED == "suite:red"

    def test_a_test_key_and_its_flaky_form_claim_each_other(self):
        assert sweep.claiming_keys("test:t.py::x") == ("test:t.py::x", "flaky:t.py::x")
        assert sweep.claiming_keys("flaky:t.py::x") == ("test:t.py::x", "flaky:t.py::x")
        assert sweep.claiming_keys("floor:src/a.py") == ("floor:src/a.py",)
        assert sweep.claiming_keys("suite:red") == ("suite:red",)

    def test_the_test_id_is_read_back_off_a_test_key_only(self):
        assert sweep.test_id_of("test:t.py::x") == "t.py::x"
        assert sweep.test_id_of("flaky:t.py::x") == "t.py::x"
        assert sweep.test_id_of("floor:src/a.py") is None


# -- the record --------------------------------------------------------------


class TestTheRecord:
    def test_it_round_trips_through_json(self):
        original = record(failing=["t.py::x"], filed=[{"key": "test:t.py::x", "item": "BUG_001"}])
        again = sweep.SweepRecord.from_dict(json.loads(original.to_json()))
        assert again == original
        assert json.loads(original.to_json())["version"] == sweep.RECORD_VERSION

    def test_its_file_name_is_the_bare_date_and_twelve_hex_digits(self):
        assert record().name == f"2026-10-19-{'a' * 12}.json"

    def test_it_carries_no_clock_time_and_no_resolved_command(self):
        text = record().to_json()
        assert "T00" not in text and ":00" not in text
        assert json.loads(text)["command"] == "coverage"

    def test_failing_may_be_null_when_identities_are_unreadable(self):
        again = sweep.SweepRecord.from_dict(record(failing=None).to_dict())
        assert again.failing is None

    @pytest.mark.parametrize(
        "damage",
        [
            {"version": 2},
            {"seq": "one"},
            {"head": None},
            {"files": ["src/a.py"]},
            {"failing": "t.py::x"},
            {"command": "pytest -q"},
        ],
    )
    def test_a_record_of_the_wrong_shape_is_refused(self, damage):
        raw = record().to_dict()
        raw.update(damage)
        with pytest.raises(sweep.SweepRecordError):
            sweep.SweepRecord.from_dict(raw)

    def test_a_missing_field_is_refused(self):
        raw = record().to_dict()
        del raw["branch"]
        with pytest.raises(sweep.SweepRecordError):
            sweep.SweepRecord.from_dict(raw)

    def test_a_document_that_is_not_a_mapping_is_refused(self):
        with pytest.raises(sweep.SweepRecordError):
            sweep.SweepRecord.from_dict([])


# -- reading committed records ----------------------------------------------


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def commit_record(root, rec, directory="docs/sweeps"):
    target = root / directory / rec.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rec.to_json(), encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", f"record {rec.seq}")


class TestReadingCommittedRecords:
    def test_no_directory_is_no_records(self, repo):
        assert sweep.read_records(repo, "HEAD", "docs/sweeps") == ([], [])

    def test_the_latest_is_the_highest_seq_on_the_branch(self, repo):
        commit_record(repo, record(seq=1, head=HEAD_A))
        commit_record(repo, record(seq=2, head=HEAD_B, ran_on="2026-10-26"))
        commit_record(repo, record(seq=9, head="c" * 40, branch="other"))
        records, unreadable = sweep.read_records(repo, "HEAD", "docs/sweeps")
        assert unreadable == []
        assert sweep.latest(records, "main").seq == 2
        assert sweep.latest(records, "other").seq == 9
        assert sweep.latest(records, "nowhere") is None

    def test_an_uncommitted_record_is_never_a_baseline(self, repo):
        (repo / "docs" / "sweeps").mkdir(parents=True)
        (repo / "docs" / "sweeps" / record().name).write_text(record().to_json(), encoding="utf-8")
        assert sweep.read_records(repo, "HEAD", "docs/sweeps") == ([], [])

    def test_an_unreadable_record_is_named_and_skipped(self, repo):
        commit_record(repo, record(seq=1))
        (repo / "docs" / "sweeps" / "2026-10-20-broken.json").write_text("{nope", encoding="utf-8")
        (repo / "docs" / "sweeps" / "notes.txt").write_text("not a record", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "broken")
        records, unreadable = sweep.read_records(repo, "HEAD", "docs/sweeps")
        assert [r.seq for r in records] == [1]
        assert unreadable == ["docs/sweeps/2026-10-20-broken.json"]

    def test_a_ref_git_cannot_read_is_no_answer(self, repo):
        assert sweep.read_records(repo, "no-such-ref", "docs/sweeps") is None

    def test_the_directory_is_read_as_declared(self, repo):
        commit_record(repo, record(seq=4), directory="evidence/sweeps")
        records, _ = sweep.read_records(repo, "HEAD", "./evidence/sweeps/")
        assert [r.seq for r in records] == [4]


# -- the diff ----------------------------------------------------------------


class TestRedKeys:
    def test_a_green_run_has_no_red_keys(self):
        assert sweep.red_keys(True, []) == set()
        assert sweep.red_keys(True, None) == set()

    def test_a_red_run_names_each_failing_test(self):
        assert sweep.red_keys(False, ["a", "b"]) == {"test:a", "test:b"}

    def test_a_red_run_with_unreadable_identities_is_one_suite_key(self):
        assert sweep.red_keys(False, None) == {"suite:red"}
        assert sweep.red_keys(False, []) == {"suite:red"}


class TestTheDiff:
    def test_with_no_previous_sweep_everything_is_baseline(self):
        d = sweep.diff(None, passed=False, failing=["t::x"], below_now={"src/a.py": 50.0}, below_before=None)
        assert d.baseline
        assert d.new == [] and d.still_broken == [] and d.recovered == []
        assert d.current == ["floor:src/a.py", "test:t::x"]

    def test_a_new_red_and_a_floor_drop_are_new(self):
        prev = record(passed=True, failing=[])
        d = sweep.diff(prev, passed=False, failing=["t::x"], below_now={"src/a.py": 50.0}, below_before={})
        assert not d.baseline
        assert d.new == ["floor:src/a.py", "test:t::x"]
        assert d.new_test_ids == ["t::x"]
        assert d.floor_drops == {"src/a.py": 50.0}

    def test_a_red_already_red_last_sweep_is_still_broken(self):
        prev = record(passed=False, failing=["t::x"])
        d = sweep.diff(prev, passed=False, failing=["t::x", "t::y"], below_now={}, below_before={})
        assert d.new == ["test:t::y"]
        assert d.still_broken == ["test:t::x"]

    def test_a_file_below_at_both_sweeps_is_still_broken_not_a_drop(self):
        prev = record()
        d = sweep.diff(prev, passed=True, failing=[], below_now={"src/a.py": 70.0}, below_before={"src/a.py": 72.0})
        assert d.floor_drops == {}
        assert d.still_broken == ["floor:src/a.py"]

    def test_what_was_red_and_is_not_now_has_recovered(self):
        prev = record(passed=False, failing=["t::x"])
        d = sweep.diff(prev, passed=True, failing=[], below_now={}, below_before={"src/b.py": 10.0})
        assert d.recovered == ["floor:src/b.py", "test:t::x"]

    def test_a_floor_with_no_previous_figures_is_not_a_drop(self):
        prev = record(command="test", files={})
        d = sweep.diff(prev, passed=True, failing=[], below_now={"src/a.py": 50.0}, below_before=None)
        assert d.new == []
        assert d.unbaselined == ["floor:src/a.py"]

    def test_a_suite_red_is_diffed_like_any_key(self):
        prev = record(passed=False, failing=None)
        d = sweep.diff(prev, passed=False, failing=None, below_now={}, below_before={})
        assert d.still_broken == ["suite:red"]
        d = sweep.diff(record(), passed=False, failing=None, below_now={}, below_before={})
        assert d.new == ["suite:red"]


class TestSettlingNewReds:
    def test_red_again_is_a_new_red_and_green_alone_is_flaky(self):
        prev = record()
        d = sweep.diff(prev, passed=False, failing=["t::x", "t::y"], below_now={}, below_before={})
        settled = sweep.settle(d, {"t::x": True, "t::y": False})
        assert settled.to_file == ["flaky:t::y", "test:t::x"]
        assert settled.flaky == ["t::y"]
        assert settled.confirmed == ["t::x"]
        assert not settled.over_cap

    def test_beyond_the_cap_one_suite_red_replaces_every_test_key(self):
        ids = [f"t::{n}" for n in range(sweep.RERUN_CAP + 1)]
        d = sweep.diff(record(), passed=False, failing=ids, below_now={"src/a.py": 1.0}, below_before={})
        assert sweep.needs_reruns(d) is False
        settled = sweep.settle(d, None)
        assert settled.over_cap
        assert settled.to_file == ["floor:src/a.py", "suite:red"]
        assert settled.flaky == []

    def test_at_the_cap_every_new_red_is_re_run(self):
        ids = [f"t::{n}" for n in range(sweep.RERUN_CAP)]
        d = sweep.diff(record(), passed=False, failing=ids, below_now={}, below_before={})
        assert sweep.needs_reruns(d) is True

    def test_a_re_run_that_was_never_made_files_nothing_as_flaky(self):
        d = sweep.diff(record(), passed=False, failing=["t::x"], below_now={}, below_before={})
        assert sweep.settle(d, {}).to_file == ["test:t::x"]

    def test_a_baseline_files_nothing(self):
        d = sweep.diff(None, passed=False, failing=["t::x"], below_now={}, below_before=None)
        assert sweep.settle(d, None).to_file == []


class TestDedup:
    def test_an_open_item_holding_the_key_claims_it(self):
        open_items = [("test:t::x", "BUG_001", "filed")]
        file, held = sweep.dedup(["test:t::x", "floor:a.py"], open_items)
        assert file == ["floor:a.py"]
        assert held == [{"key": "test:t::x", "item": "BUG_001", "status": "filed", "regression": False}]

    def test_a_flaky_item_claims_the_test_key_and_the_reverse(self):
        file, held = sweep.dedup(["test:t::x"], [("flaky:t::x", "BUG_081", "filed")])
        assert file == [] and held[0]["item"] == "BUG_081"
        file, held = sweep.dedup(["flaky:t::x"], [("test:t::x", "BUG_002", "reproduced")])
        assert file == [] and held[0]["item"] == "BUG_002"

    @pytest.mark.parametrize("status", ["fixed", "verified"])
    def test_an_item_called_fixed_that_is_still_red_is_a_regression(self, status):
        _, held = sweep.dedup(["test:t::x"], [("test:t::x", "BUG_003", status)])
        assert held[0]["regression"] is True

    def test_a_key_nobody_holds_is_filed(self):
        assert sweep.dedup(["suite:red"], []) == (["suite:red"], [])


# -- age arithmetic (read by sweep.fresh in increment 4) ---------------------


class TestAge:
    def test_age_is_whole_days_since_the_bare_date(self):
        assert sweep.age_days("2026-10-19", date(2026, 10, 26)) == 7
        assert sweep.age_days("2026-10-26", date(2026, 10, 26)) == 0

    def test_an_unreadable_date_has_no_age(self):
        assert sweep.age_days("yesterday", date(2026, 10, 26)) is None

    def test_overdue_is_past_cadence_plus_grace(self):
        assert sweep.overdue(10, cadence_days=7, grace_days=3) is False
        assert sweep.overdue(11, cadence_days=7, grace_days=3) is True
        assert sweep.overdue(None, cadence_days=7, grace_days=3) is True
        assert sweep.overdue(400, cadence_days=None, grace_days=3) is False


# -- the floor policy a drop is judged by -------------------------------------


def manifest(tmp_path, **gates):
    body = {"version": 1, "project": {"name": "demo", "ecosystems": ["python"]}}
    body["methodology"] = {"gates": {"coverage_min": 85, **gates}}
    return Manifest.from_raw(body, root=tmp_path, path=tmp_path / MANIFEST_RELPATH, env={})


class TestTheFloorPolicy:
    def test_below_the_floor_is_below(self, tmp_path):
        below, error = sweep_below_floor(manifest(tmp_path), {"src/a.py": 80.0, "src/b.py": 90.0})
        assert error is None
        assert below == {"src/a.py": 80.0}

    def test_non_source_and_coverage_exclude_are_never_held(self, tmp_path):
        m = manifest(tmp_path, non_source=["design/**"], coverage_exclude=["scripts/*"])
        below, _ = sweep_below_floor(m, {"design/x.py": 1.0, "scripts/y.py": 2.0, "src/a.py": 3.0})
        assert below == {"src/a.py": 3.0}

    def test_the_baseline_ratchet_holds_a_recorded_shortfall_out(self, tmp_path):
        (tmp_path / "baseline.txt").write_text("src/a.py 70\nsrc/b.py 70\n", encoding="utf-8")
        m = manifest(tmp_path, coverage_baseline="baseline.txt")
        below, _ = sweep_below_floor(m, {"src/a.py": 71.0, "src/b.py": 60.0, "src/c.py": 50.0})
        assert below == {"src/b.py": 60.0, "src/c.py": 50.0}

    def test_an_unreadable_baseline_is_an_error_not_a_smaller_record(self, tmp_path):
        (tmp_path / "baseline.txt").write_text("src/a.py seventy\n", encoding="utf-8")
        m = manifest(tmp_path, coverage_baseline="baseline.txt")
        below, error = sweep_below_floor(m, {"src/a.py": 71.0})
        assert below is None
        assert "baseline" in error


class TestAFigureThatIsNotANumber:
    def test_is_refused(self):
        raw = record().to_dict()
        raw["files"] = {"src/a.py": "ninety"}
        with pytest.raises(sweep.SweepRecordError, match="not a number"):
            sweep.SweepRecord.from_dict(raw)


class TestTheGitReadsTheSweepUses:
    """`core/git`'s new questions, and the one write named as one."""

    def test_outside_a_repository_nothing_is_known(self, tmp_path):
        from invariant.core import git as core_git

        assert core_git.tracked_changes(tmp_path) is None
        assert core_git.status_paths(tmp_path, ".") is None
        assert core_git.tree_paths(tmp_path, "HEAD", "docs") is None

    def test_a_ref_spelled_as_an_option_is_never_handed_to_git(self, repo):
        from invariant.core import git as core_git

        assert core_git.tree_paths(repo, "--all", "docs") is None
        assert core_git.tree_paths(repo, "", "docs") is None

    def test_tracked_changes_keep_the_first_column(self, repo):
        from invariant.core import git as core_git

        (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
        (repo / "new.txt").write_text("untracked\n", encoding="utf-8")
        assert core_git.tracked_changes(repo) == ["app.py"]

    def test_commit_only_commits_nothing_for_no_paths(self, repo):
        from invariant.core import git as core_git

        assert core_git.commit_only(repo, [], "m") == (False, "nothing to commit")

    def test_commit_only_reports_a_path_git_cannot_add(self, repo):
        from invariant.core import git as core_git

        ok, output = core_git.commit_only(repo, ["no/such/file"], "m")
        assert not ok and output


class TestTheLastSweepOnABranch:
    """`last_on` and the manifest readers `sweep.fresh` and `sweep status` share."""

    def test_the_latest_on_the_branch_is_read_at_its_tip(self, repo):
        commit_record(repo, record(seq=1))
        commit_record(repo, record(seq=2, head=HEAD_B, ran_on="2026-10-20"))
        last = sweep.last_on(repo, "main", "docs/sweeps")
        assert last.readable and last.branch == "main"
        assert last.record.seq == 2

    def test_no_branch_reads_nothing_and_says_so(self, repo):
        last = sweep.last_on(repo, None, "docs/sweeps")
        assert last == sweep.LastSweep(None, None, readable=False)

    def test_a_branch_git_cannot_read_is_unreadable(self, repo):
        last = sweep.last_on(repo, "nope", "docs/sweeps")
        assert not last.readable and last.branch == "nope" and last.record is None

    def test_no_record_is_readable_and_empty(self, repo):
        last = sweep.last_on(repo, "main", "docs/sweeps")
        assert last.readable and last.record is None

    def test_the_manifest_readers(self, tmp_path):
        m = Manifest.from_raw(
            {
                "version": 1,
                "project": {"name": "demo"},
                "paths": {"sweeps": "./records/sweeps/"},
                "methodology": {
                    "gates": {"schedule": {"cadence_days": 7, "gates": ["test.all_pass"]}}
                },
            },
            root=tmp_path,
            path=tmp_path / MANIFEST_RELPATH,
            env={},
        )
        assert sweep.directory_of(m) == "records/sweeps"
        assert sweep.cadence_of(m) == (7, sweep.DEFAULT_GRACE_DAYS)

    def test_the_defaults(self, tmp_path):
        m = Manifest.from_raw(
            {"version": 1, "project": {"name": "demo"}},
            root=tmp_path,
            path=tmp_path / MANIFEST_RELPATH,
            env={},
        )
        assert sweep.directory_of(m) == "docs/sweeps"
        assert sweep.cadence_of(m) == (None, 3)

    def test_today_is_a_bare_date(self):
        assert isinstance(sweep.utc_today(), date)
