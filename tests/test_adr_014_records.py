"""ADR-014 D1: each record type has a location, `repo` or `store`.

Every key defaults to `repo`, which is today's layout exactly. A path key
under a `store` record is store-relative, joined onto the store root at load,
and refused when it climbs out with `..` or is absolute. The guidelines
records are refused under `store` until D9 is built (increment 4), and
`items.exempt: false` with the backlog in a store is refused (D10).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from invariant.core.errors import ManifestError
from invariant.core.manifest import (
    DEFAULTS,
    RECORD_PATH_KEYS,
    Manifest,
    store_record_paths,
)


def resolve(raw: dict, tmp_path: Path, store_root: Path | None = None) -> Manifest:
    return Manifest.from_raw(
        raw, root=tmp_path, path=tmp_path / "m.yaml", env={}, store_root=store_root
    )


class TestDefaults:
    def test_every_record_is_in_the_repo_by_default(self):
        assert DEFAULTS["records"] == {
            "docs": "repo",
            "backlog": "repo",
            "guidelines": "repo",
            "memory": "repo",
            "reports": "repo",
            "sweeps": "repo",
            "hooks": "repo",
            "agents": "repo",
        }

    def test_the_default_layout_is_unchanged(self, tmp_path):
        m = resolve({}, tmp_path)
        assert m.get("paths.docs") == "docs/architecture"
        assert m.get("items.path") == "docs/backlog"
        assert m.path_for("docs") == (tmp_path / "docs/architecture").resolve()
        assert m.store_root is None

    def test_an_explicit_repo_record_changes_nothing(self, tmp_path):
        m = resolve({"records": {"docs": "repo"}}, tmp_path)
        assert m.get("paths.docs") == "docs/architecture"
        assert m.source_of("records.docs") == "repo:m.yaml"


class TestValidation:
    @pytest.mark.parametrize(
        ("records", "needle"),
        [
            (["docs"], "records must be a mapping"),
            ({"manifest": "store"}, "records.manifest"),
            ({"docs": "elsewhere"}, "records.docs"),
            ({"docs": True}, "records.docs"),
            ({"hooks": "store"}, "records.hooks"),
        ],
    )
    def test_a_malformed_block_is_refused(self, tmp_path, records, needle):
        with pytest.raises(ManifestError, match=needle):
            resolve({"records": records}, tmp_path)

    def test_the_manifest_itself_is_not_a_record_and_says_why(self, tmp_path):
        with pytest.raises(ManifestError, match="config levels"):
            resolve({"records": {"manifest": "store"}}, tmp_path)

    @pytest.mark.parametrize("key", ["hooks", "agents"])
    def test_user_level_wiring_is_retired(self, tmp_path, key):
        """ADR-020 D9 replaced D11's user-level wiring before it was built."""
        with pytest.raises(ManifestError, match="retired"):
            resolve({"records": {key: "user"}}, tmp_path)

    def test_the_guidelines_records_are_refused_under_a_store(self, tmp_path):
        """D9: until the gates read each record's own tree, a store ruleset
        or baseline would make the shrink gate silently inert."""
        with pytest.raises(ManifestError, match="increment 4"):
            resolve({"records": {"guidelines": "store"}}, tmp_path, tmp_path / "store")

    def test_a_store_record_with_no_store_is_refused(self, tmp_path):
        with pytest.raises(ManifestError, match="no store"):
            resolve({"records": {"docs": "store"}}, tmp_path)

    def test_an_unexempt_backlog_in_a_store_is_refused_with_the_reason(self, tmp_path):
        with pytest.raises(ManifestError, match="items.exempt") as caught:
            resolve(
                {"records": {"backlog": "store"}, "items": {"exempt": False}},
                tmp_path,
                tmp_path / "store",
            )
        assert "main checkout" in str(caught.value)

    def test_an_exempt_backlog_in_a_store_loads(self, tmp_path):
        resolve({"records": {"backlog": "store"}}, tmp_path, tmp_path / "store")

    @pytest.mark.parametrize(
        ("raw", "key"),
        [
            ({"records": {"docs": "store"}, "paths": {"docs": "../docs"}}, "paths.docs"),
            ({"records": {"docs": "store"}, "paths": {"docs": "a/../../b"}}, "paths.docs"),
            ({"records": {"docs": "store"}, "paths": {"docs": "/abs/docs"}}, "paths.docs"),
            (
                {"records": {"backlog": "store"}, "items": {"closed_path": "x/../y"}},
                "items.closed_path",
            ),
            (
                {"records": {"memory": "store"}, "paths": {"memory_index": "/M.md"}},
                "paths.memory_index",
            ),
        ],
    )
    def test_a_store_path_must_be_store_relative(self, tmp_path, raw, key):
        with pytest.raises(ManifestError, match=key) as caught:
            resolve(raw, tmp_path, tmp_path / "store")
        assert "store-relative" in str(caught.value)

    def test_a_repo_record_keeps_its_freedom(self, tmp_path):
        """Only a store path is held to store-relative: in the repo an
        absolute or climbing path means what it always meant."""
        m = resolve({"paths": {"docs": "../shared/docs"}}, tmp_path)
        assert m.get("paths.docs") == "../shared/docs"


class TestStorePathsAreJoinedOntoTheStore:
    def test_each_data_record_is_read_against_the_store(self, tmp_path):
        store = tmp_path / "store"
        m = resolve(
            {
                "records": {
                    "docs": "store",
                    "backlog": "store",
                    "memory": "store",
                    "reports": "store",
                    "sweeps": "store",
                },
                "items": {"closed_path": "docs/closed"},
            },
            tmp_path,
            store,
        )
        assert m.store_root == store
        assert m.get("paths.docs") == str(store / "docs/architecture")
        assert m.path_for("docs") == (store / "docs/architecture").resolve()
        assert m.get("items.path") == str(store / "docs/backlog")
        assert m.get("items.closed_path") == str(store / "docs/closed")
        assert m.get("paths.memory") == str(store / ".claude/memory")
        assert m.get("paths.memory_index") == str(store / "MEMORY.md")
        assert m.get("paths.reports") == str(store / "docs/reports")
        assert m.get("paths.sweeps") == str(store / "docs/sweeps")

    def test_a_null_path_stays_null(self, tmp_path):
        m = resolve({"records": {"backlog": "store"}}, tmp_path, tmp_path / "store")
        assert m.get("items.closed_path") is None

    def test_a_repo_record_stays_in_the_repo_beside_a_store(self, tmp_path):
        m = resolve({"records": {"docs": "store"}}, tmp_path, tmp_path / "store")
        assert m.get("items.path") == "docs/backlog"

    def test_the_provenance_of_a_joined_path_is_unchanged(self, tmp_path):
        m = resolve(
            {"records": {"docs": "store"}, "paths": {"docs": "docs/spec"}},
            tmp_path,
            tmp_path / "store",
        )
        assert m.source_of("paths.docs") == "repo:m.yaml"


class TestTheRecordPathTable:
    def test_every_record_with_paths_is_listed(self):
        assert set(RECORD_PATH_KEYS) == {
            "docs", "backlog", "guidelines", "memory", "reports", "sweeps",
        }

    def test_the_policy_files_follow_the_guidelines(self):
        """D1: extra_machines and coverage_baseline are policy files too."""
        assert "methodology.extra_machines" in RECORD_PATH_KEYS["guidelines"]
        assert "methodology.gates.coverage_baseline" in RECORD_PATH_KEYS["guidelines"]
        assert "agents.overlays" in RECORD_PATH_KEYS["guidelines"]

    def test_a_path_a_list_and_a_mapping_are_each_joined(self, tmp_path):
        data = {
            "guidelines": {"ruleset": "g/r.yaml", "baseline": None},
            "methodology": {"extra_machines": ["m/a.yaml"], "gates": {}},
            "agents": {"overlays": {"feature-architect": "o/f.yaml"}},
        }
        joined = store_record_paths(data, "guidelines", tmp_path)
        assert joined == {
            "guidelines.ruleset": str(tmp_path / "g/r.yaml"),
            "methodology.extra_machines": [str(tmp_path / "m/a.yaml")],
            "agents.overlays": {"feature-architect": str(tmp_path / "o/f.yaml")},
        }

    def test_a_climbing_entry_in_a_list_is_refused(self, tmp_path):
        data = {"methodology": {"extra_machines": ["../m.yaml"]}}
        with pytest.raises(ManifestError, match="methodology.extra_machines"):
            store_record_paths(data, "guidelines", tmp_path)
