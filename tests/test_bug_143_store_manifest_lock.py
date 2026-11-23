# ruff: noqa: F811 - the fixtures imported below are named as parameters
"""BUG_143's fix (DEC_ADR_014_B143): the store's invariant.yaml is under the docs lock.

Since increment 4c-2 the store's ``invariant.yaml`` holds policy (the
guidelines' ``baseline`` and ``ruleset`` in a repository's block), so a
host's Read or edit of it takes its unit, ``file:invariant.yaml`` (or the
declared area matching it), like any store policy file. The repro is
``tests/test_bug_143_repro.py``; here, what the lock must not stop:

- ``rules loosen`` at a terminal writes the file while a session holds its
  unit; the holder's ``docs unlock`` is then refused until the user commits
  the ruling, as for any doc edited by hand in the store;
- ``store migrate`` in either direction writes the file while another
  session holds its unit: the move is under its own session's grants for
  the records it moves, and the store's manifest is not one of them.
"""

from __future__ import annotations

import yaml
from test_adr_014_docs_lock import lease_record, run, two  # noqa: F401
from test_adr_014_docs_lock_host import HOST_A, denied, hosts  # noqa: F401
from test_adr_014_migrate import TOY, commit_all, git, migrate
from test_adr_014_migrate_to_repo import (  # noqa: F401
    _git_identity,
    stored,
    to_repo,
    toy,
)
from test_adr_021_rulings import terminal  # noqa: F401

from invariant.core.manifest import Manifest

UNIT = "file:invariant.yaml"


def holder_of(store, unit: str = UNIT) -> str | None:
    record = lease_record(store)
    lease = (record["units"].get(unit) or {}).get("holder")
    return (record["leases"].get(lease or "") or {}).get("session")


class TestRulesLoosen:
    def test_it_writes_while_a_session_holds_the_unit_and_the_release_waits_for_the_commit(
        self, hosts, capsys, terminal
    ):
        manifest = hosts.store / "invariant.yaml"
        assert denied(hosts.read(hosts.widgets, HOST_A, manifest)) is None
        assert holder_of(hosts.store) == "aaaa1111"

        terminal("y")
        hosts.at(hosts.gadgets)
        code, text = run(
            capsys, "rules", "loosen", "test.all_pass", "--to", "warn",
            "--by", "owner", "--reason", "the device suite is flaky",
        )
        assert code == 0, text
        body = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        block = body["store"]["repos"]["host.example/team/gadgets"]["enforcement"]
        assert block["rules"]["test.all_pass"] == "warn"
        assert holder_of(hosts.store) == "aaaa1111"

        # The ruling is left uncommitted: the holder's release waits for it.
        hosts.at(hosts.widgets)
        code, text = run(capsys, "docs", "unlock", "--id", "aaaa1111")
        assert code != 0, text
        assert "uncommitted" in text and "invariant.yaml" in text
        assert holder_of(hosts.store) == "aaaa1111"

        git(hosts.store, "add", "invariant.yaml")
        git(hosts.store, "commit", "-qm", "the user's ruling")
        code, text = run(capsys, "docs", "unlock", "--id", "aaaa1111")
        assert code == 0, text
        assert holder_of(hosts.store) is None


class TestStoreMigrate:
    def test_to_store_writes_the_file_another_session_holds(
        self, toy, tmp_path, monkeypatch, capsys
    ):
        from test_adr_014_migrate import new_repo

        widgets = new_repo(tmp_path / "widgets", "https://host.example/team/widgets.git")
        store = tmp_path / "shared"
        store.mkdir()
        git(store, "init", "-q", "-b", "main")
        (store / "invariant.yaml").write_text(
            yaml.safe_dump(
                {"version": 1, "store": {"repos": {"host.example/team/widgets": {}}}}
            ),
            encoding="utf-8",
        )
        commit_all(store, "the store")
        monkeypatch.chdir(widgets)
        code, text = run(capsys, "store", "link", str(store))
        assert code == 0, text
        code, text = run(capsys, "session", "start", "design", "--id", "w0000001")
        assert code == 0, text
        code, text = run(capsys, "docs", "lock", "invariant.yaml", "--id", "w0000001")
        assert code == 0, text
        assert holder_of(store) == "w0000001"

        monkeypatch.chdir(toy)
        code, out = migrate(capsys, "--store", str(store))
        assert code == 0, out
        body = yaml.safe_load((store / "invariant.yaml").read_text(encoding="utf-8"))
        assert set(body["store"]["repos"]) == {"host.example/team/widgets", TOY}
        assert git(store, "status", "--porcelain") == ""
        assert holder_of(store) == "w0000001"

    def test_to_repo_writes_the_file_another_session_holds(self, stored, capsys):
        _toy, store = stored
        code, text = run(capsys, "session", "start", "design", "--id", "w0000001")
        assert code == 0, text
        code, text = run(capsys, "docs", "lock", "invariant.yaml", "--id", "w0000001")
        assert code == 0, text
        assert holder_of(store) == "w0000001"

        code, out = to_repo(capsys)
        assert code == 0, out
        body = yaml.safe_load((store / "invariant.yaml").read_text(encoding="utf-8"))
        assert TOY not in (body["store"].get("repos") or {})
        assert git(store, "status", "--porcelain") == ""
        assert holder_of(store) == "w0000001"


def test_a_declared_area_takes_the_store_file_into_its_unit(hosts):
    body = yaml.safe_load((hosts.store / "invariant.yaml").read_text(encoding="utf-8"))
    body["store"]["docs_lock"]["areas"]["config"] = ["invariant.yaml"]
    (hosts.store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    commit_all(hosts.store, "the config area")
    assert Manifest.locate(hosts.widgets).store is not None
    assert denied(hosts.read(hosts.widgets, HOST_A, hosts.store / "invariant.yaml")) is None
    assert "area:config" in lease_record(hosts.store)["units"]
    assert UNIT not in lease_record(hosts.store)["units"]
