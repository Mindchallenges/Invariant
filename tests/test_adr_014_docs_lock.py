"""ADR-014 increment 4a: the store's docs lease and its CLI (D5).

Two repositories share one store, and each runs a session. The lease record
lives in the store's git directory; units are declared areas or single files;
a session waits only for a unit whose key sorts after every unit it holds;
the queue is per unit and first in, first out; a stale lease is taken over
only with a reason; a late unit that is busy backs the session off in order
(ruling 21). Enforcement at the host and the gates read per leg are 4b and 4c.
The home is the suite's scratch one; nothing reaches the real ~/.invariant.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest
import yaml

from invariant.cli.main import main
from invariant.core import doclock
from invariant.core.doclock import DocsLock, DocsLockConfig, parse_docs_lock, unit_of
from invariant.core.errors import ManifestError, StateError, UsageError
from invariant.core.home import invariant_home
from invariant.core.manifest import Manifest
from invariant.domains.session import SessionDomain

WIDGETS = "host.example/team/widgets"
GADGETS = "host.example/team/gadgets"
AREAS = {
    "architecture": ["docs/architecture/**"],
    "guidelines": ["docs/guidelines/**"],
}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def run(capsys, *argv) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def run_json(capsys, *argv) -> tuple[int, dict]:
    code, text = run(capsys, *argv, "--json")
    try:
        return code, json.loads(text)
    except json.JSONDecodeError:
        raise AssertionError(text) from None


def new_repo(root: Path, origin: str) -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    (root / "conf.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    git(root, "remote", "add", "origin", origin)
    return root


def write_store_config(store: Path, docs_lock: dict | None) -> None:
    body = yaml.safe_load((store / "invariant.yaml").read_text(encoding="utf-8"))
    if docs_lock is None:
        body["store"].pop("docs_lock", None)
    else:
        body["store"]["docs_lock"] = docs_lock
    (store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


def commit_store(store: Path, message: str = "store") -> None:
    git(store, "add", "-A")
    git(store, "commit", "-qm", message, "--allow-empty")


@pytest.fixture
def two(tmp_path, monkeypatch, capsys):
    """Two repositories, widgets and gadgets, linked to one store, each with a
    session: ``aaaa1111`` in widgets and ``bbbb2222`` in gadgets."""
    widgets = new_repo(tmp_path / "widgets", f"https://{WIDGETS}.git")
    gadgets = new_repo(tmp_path / "gadgets", f"https://{GADGETS}.git")
    monkeypatch.chdir(widgets)
    code, text = run(capsys, "store", "init")
    assert code == 0, text
    store = invariant_home() / "stores" / "widgets"
    body = yaml.safe_load((store / "invariant.yaml").read_text(encoding="utf-8"))
    body["store"]["repos"][GADGETS] = {}
    body["store"]["repos"][WIDGETS] = {"records": {"docs": "store"}}
    body["store"]["repos"][GADGETS] = {"records": {"docs": "store"}}
    (store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    write_store_config(store, {"areas": AREAS})
    for rel in (
        "docs/architecture/overview.rst",
        "docs/architecture/cli.rst",
        "docs/guidelines/ruleset.yaml",
        "docs/notes.rst",
    ):
        (store / rel).parent.mkdir(parents=True, exist_ok=True)
        (store / rel).write_text(f"{rel}\n", encoding="utf-8")
    git(store, "config", "user.email", "t@example.invalid")
    git(store, "config", "user.name", "T")
    commit_store(store, "the store's docs")
    monkeypatch.chdir(gadgets)
    code, text = run(capsys, "store", "link", str(store))
    assert code == 0, text
    for repo, sid in ((widgets, "aaaa1111"), (gadgets, "bbbb2222")):
        monkeypatch.chdir(repo)
        code, text = run(capsys, "session", "start", f"design-{sid}", "--id", sid)
        assert code == 0, text

    class Two:
        pass

    t = Two()
    t.widgets, t.gadgets, t.store = widgets, gadgets, store

    def at(repo: Path) -> None:
        monkeypatch.chdir(repo)

    t.at = at
    return t


def lease_record(store: Path) -> dict:
    return json.loads((store / ".git" / "invariant" / "locks" / "docs.json").read_text())


def session_record(repo: Path, sid: str) -> dict:
    return SessionDomain(Manifest.locate(repo)).require(sid).to_dict()


def spans(repo: Path, sid: str) -> list[dict]:
    legs = session_record(repo, sid).get("legs") or []
    store_legs = [leg for leg in legs if leg.get("kind") == "store"]
    return store_legs[0]["lock_spans"] if store_legs else []


# -- the configuration ------------------------------------------------------


class TestConfiguration:
    def test_an_overlapping_area_declaration_is_refused_at_load(self, two):
        write_store_config(
            two.store,
            {"areas": {"docs": ["docs/**"], "architecture": ["docs/architecture/**"]}},
        )
        with pytest.raises(ManifestError) as caught:
            Manifest.locate(two.widgets)
        assert "docs" in str(caught.value)
        assert "architecture" in str(caught.value)
        assert "overlap" in str(caught.value)

    def test_the_cli_refuses_at_load_too(self, two, capsys):
        write_store_config(
            two.store, {"areas": {"a": ["docs/architecture/**"], "b": ["docs/architecture/cli.rst"]}}
        )
        two.at(two.widgets)
        code, text = run(capsys, "config", "show")
        assert code != 0
        assert "overlap" in text

    @pytest.mark.parametrize(
        "block, said",
        [
            ({"areas": []}, "areas"),
            ({"areas": {"a": "docs/**"}}, "list"),
            ({"areas": {"a": []}}, "pattern"),
            ({"areas": {"a": ["/abs/**"]}}, "store-relative"),
            ({"areas": {"a": ["../up/**"]}}, "store-relative"),
            ({"areas": {"a:b": ["x/**"]}}, "name"),
            ({"lease_minutes": 0}, "lease_minutes"),
            ({"lease_minutes": "30"}, "lease_minutes"),
            ({"wait": 3}, "wait"),
            ("yes", "mapping"),
        ],
    )
    def test_a_malformed_block_is_refused_naming_it(self, block, said, tmp_path):
        with pytest.raises(ManifestError) as caught:
            parse_docs_lock(block, tmp_path / "invariant.yaml")
        assert said in str(caught.value)

    def test_defaults(self, tmp_path):
        config = parse_docs_lock(None, tmp_path / "invariant.yaml")
        assert config.areas == {}
        assert config.lease_minutes == 30

    def test_units_are_areas_else_single_files(self):
        config = DocsLockConfig(areas={"architecture": ("docs/architecture/**",)})
        assert unit_of("docs/architecture/overview.rst", config) == "area:architecture"
        assert unit_of("docs/architecture/new/page.rst", config) == "area:architecture"
        assert unit_of("docs/notes.rst", config) == "file:docs/notes.rst"
        assert unit_of("docs/notes.rst", DocsLockConfig()) == "file:docs/notes.rst"

    def test_a_path_two_areas_match_is_refused_naming_both(self):
        config = DocsLockConfig(areas={"a": ("docs/*.md",), "b": ("docs/x*",)})
        with pytest.raises(ManifestError) as caught:
            unit_of("docs/xy.md", config)
        assert "'a'" in str(caught.value) and "'b'" in str(caught.value)

    def test_the_store_key_is_read(self, two):
        manifest = Manifest.locate(two.widgets)
        config = doclock.config_of(manifest)
        assert set(config.areas) == {"architecture", "guidelines"}


# -- the lease, by command ---------------------------------------------------


class TestSameArea:
    def test_the_second_is_queued_until_the_first_releases_then_granted(self, two, capsys):
        two.at(two.widgets)
        code, out = run_json(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        assert code == 0
        assert out["granted"] == ["area:architecture"]
        two.at(two.gadgets)
        code, out = run_json(capsys, "docs", "lock", "architecture", "--id", "bbbb2222")
        assert code == 0
        assert out["granted"] == []
        assert out["queued"] == {
            "area:architecture": {"position": 1, "holder": "aaaa1111"}
        }
        code, text = run(capsys, "docs", "lock", "architecture", "--id", "bbbb2222")
        assert "queued" in text and "aaaa1111" in text and "position 1" in text

        two.at(two.widgets)
        code, out = run_json(capsys, "docs", "unlock", "--id", "aaaa1111")
        assert code == 0
        assert out["released"] == ["area:architecture"]
        assert out["handed"] == {"area:architecture": "bbbb2222"}
        record = lease_record(two.store)
        bee = record["units"]["area:architecture"]["holder"]
        assert record["leases"][bee]["session"] == "bbbb2222"

        two.at(two.gadgets)
        code, out = run_json(capsys, "docs", "lock", "architecture", "--id", "bbbb2222")
        assert out["held"] == ["area:architecture"]
        assert [s["unit"] for s in spans(two.gadgets, "bbbb2222")] == ["area:architecture"]
        assert spans(two.gadgets, "bbbb2222")[0]["acquired_at_sha"]

    def test_the_lease_names_session_identity_and_host(self, two, capsys):
        two.at(two.widgets)
        run(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        lease = next(iter(lease_record(two.store)["leases"].values()))
        assert lease["session"] == "aaaa1111"
        assert lease["identity"] == WIDGETS
        assert "host" in lease
        assert lease["state_root"] == str(Manifest.locate(two.widgets).state_root)

    def test_nothing_is_written_into_either_repository(self, two, capsys):
        two.at(two.widgets)
        run(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        assert git(two.widgets, "status", "--porcelain") == ""
        assert git(two.store, "status", "--porcelain") == ""


class TestDifferentAreas:
    def test_both_granted_at_once(self, two, capsys):
        two.at(two.widgets)
        _, a = run_json(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        two.at(two.gadgets)
        _, b = run_json(capsys, "docs", "lock", "guidelines", "--id", "bbbb2222")
        assert a["granted"] == ["area:architecture"]
        assert b["granted"] == ["area:guidelines"]
        assert b["queued"] == {}

    def test_a_file_outside_every_area_is_its_own_unit(self, two, capsys):
        two.at(two.widgets)
        _, a = run_json(capsys, "docs", "lock", "docs/notes.rst", "--id", "aaaa1111")
        assert a["granted"] == ["file:docs/notes.rst"]
        two.at(two.gadgets)
        _, b = run_json(
            capsys, "docs", "lock", str(two.store / "docs" / "notes.rst"), "--id", "bbbb2222"
        )
        assert b["queued"]["file:docs/notes.rst"]["holder"] == "aaaa1111"

    def test_with_no_area_declared_every_file_is_its_own_unit(self, two, capsys):
        write_store_config(two.store, None)
        two.at(two.widgets)
        _, a = run_json(
            capsys, "docs", "lock", "docs/architecture/overview.rst", "--id", "aaaa1111"
        )
        two.at(two.gadgets)
        _, b = run_json(capsys, "docs", "lock", "docs/architecture/cli.rst", "--id", "bbbb2222")
        assert a["granted"] == ["file:docs/architecture/overview.rst"]
        assert b["granted"] == ["file:docs/architecture/cli.rst"]


class TestOrder:
    def test_up_front_units_are_taken_in_key_order(self, two, capsys):
        two.at(two.gadgets)
        run(capsys, "docs", "lock", "architecture", "--id", "bbbb2222")
        two.at(two.widgets)
        _, out = run_json(
            capsys, "docs", "lock", "guidelines", "docs/notes.rst", "architecture",
            "--id", "aaaa1111",
        )
        # area:architecture < area:guidelines < file:docs/notes.rst: queued at
        # the first busy one, and nothing after it is taken.
        assert out["granted"] == []
        assert list(out["queued"]) == ["area:architecture"]
        assert out["pending"] == ["area:guidelines", "file:docs/notes.rst"]
        two.at(two.gadgets)
        run(capsys, "docs", "unlock", "--id", "bbbb2222")
        two.at(two.widgets)
        _, out = run_json(capsys, "docs", "lock", "--id", "aaaa1111")
        assert out["held"] == [
            "area:architecture", "area:guidelines", "file:docs/notes.rst"
        ]

    def test_opposite_orders_both_complete_with_no_deadlock(self, two, capsys):
        two.at(two.widgets)
        run(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        two.at(two.gadgets)
        run(capsys, "docs", "lock", "guidelines", "--id", "bbbb2222")
        # A wants guidelines next: it sorts after what A holds, so A waits.
        two.at(two.widgets)
        _, a = run_json(capsys, "docs", "lock", "guidelines", "--id", "aaaa1111")
        assert a["queued"] == {"area:guidelines": {"position": 1, "holder": "bbbb2222"}}
        # B wants architecture: it sorts BEFORE guidelines, which B holds, and
        # it is busy, so B backs off (ruling 21): it releases guidelines,
        # which passes to A, and queues for architecture then guidelines.
        two.at(two.gadgets)
        _, b = run_json(capsys, "docs", "lock", "architecture", "--id", "bbbb2222")
        assert b["backed_off"] == ["area:guidelines"]
        assert b["handed"] == {"area:guidelines": "aaaa1111"}
        assert list(b["queued"]) == ["area:architecture"]
        assert b["pending"] == ["area:guidelines"]
        # A now holds both, finishes and releases; B gets both.
        two.at(two.widgets)
        _, a = run_json(capsys, "docs", "lock", "--id", "aaaa1111")
        assert a["held"] == ["area:architecture", "area:guidelines"]
        run(capsys, "docs", "unlock", "--id", "aaaa1111")
        two.at(two.gadgets)
        _, b = run_json(capsys, "docs", "lock", "--id", "bbbb2222")
        assert b["held"] == ["area:architecture", "area:guidelines"]
        run(capsys, "docs", "unlock", "--id", "bbbb2222")
        assert lease_record(two.store)["units"] == {}
        backed = [s for s in spans(two.gadgets, "bbbb2222") if s.get("backed_off")]
        assert [s["unit"] for s in backed] == ["area:guidelines"]

    def test_a_late_free_unit_is_granted_without_waiting(self, two, capsys):
        two.at(two.widgets)
        run(capsys, "docs", "lock", "guidelines", "--id", "aaaa1111")
        _, out = run_json(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        assert out["granted"] == ["area:architecture"]
        assert out["backed_off"] == []

    def test_the_back_off_refuses_while_a_unit_it_would_release_is_dirty(self, two, capsys):
        two.at(two.widgets)
        run(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        two.at(two.gadgets)
        run(capsys, "docs", "lock", "guidelines", "--id", "bbbb2222")
        (two.store / "docs" / "guidelines" / "ruleset.yaml").write_text("edited\n")
        code, text = run(capsys, "docs", "lock", "architecture", "--id", "bbbb2222")
        assert code != 0
        assert "docs/guidelines/ruleset.yaml" in text
        assert "uncommitted" in text
        record = lease_record(two.store)
        assert record["leases"][record["units"]["area:guidelines"]["holder"]]["session"] == "bbbb2222"


class TestRelease:
    def test_unlock_refuses_a_unit_with_uncommitted_changes(self, two, capsys):
        two.at(two.widgets)
        run(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        (two.store / "docs" / "architecture" / "new.rst").write_text("new\n")
        code, text = run(capsys, "docs", "unlock", "--id", "aaaa1111")
        assert code != 0
        assert "docs/architecture/new.rst" in text
        commit_store(two.store, "the update")
        code, _ = run_json(capsys, "docs", "unlock", "--id", "aaaa1111")
        assert code == 0
        (span,) = spans(two.widgets, "aaaa1111")
        assert span["released_at_sha"] == git(two.store, "rev-parse", "HEAD")
        assert span["changed"] == ["docs/architecture/new.rst"]

    def test_a_release_with_no_change_records_that_nothing_was_written(self, two, capsys):
        two.at(two.widgets)
        run(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        run(capsys, "docs", "unlock", "architecture", "--id", "aaaa1111")
        (span,) = spans(two.widgets, "aaaa1111")
        assert span["changed"] == []
        assert span["released_at_sha"] == span["acquired_at_sha"]

    def test_unlock_of_a_unit_not_held_is_refused(self, two, capsys):
        two.at(two.widgets)
        code, text = run(capsys, "docs", "unlock", "guidelines", "--id", "aaaa1111")
        assert code != 0
        assert "area:guidelines" in text

    def test_unlock_drops_a_queued_want(self, two, capsys):
        two.at(two.widgets)
        run(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        two.at(two.gadgets)
        run(capsys, "docs", "lock", "architecture", "--id", "bbbb2222")
        code, _ = run_json(capsys, "docs", "unlock", "architecture", "--id", "bbbb2222")
        assert code == 0
        assert lease_record(two.store)["units"]["area:architecture"]["queue"] == []

    @pytest.mark.parametrize("how", ["teardown", "abandon", "merged"])
    def test_the_lifecycle_frees_every_unit_and_hands_it_on(self, two, capsys, how):
        two.at(two.widgets)
        run(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        two.at(two.gadgets)
        run(capsys, "docs", "lock", "architecture", "--id", "bbbb2222")
        domain = SessionDomain(Manifest.locate(two.widgets))
        session = domain.require("aaaa1111")
        domain.release_docs_lock(session)
        domain._save(session)
        record = lease_record(two.store)
        holder = record["units"]["area:architecture"]["holder"]
        assert record["leases"][holder]["session"] == "bbbb2222"
        assert all(s.get("released_at") for s in spans(two.widgets, "aaaa1111"))

    def test_abandoning_through_the_cli_releases(self, two, capsys):
        two.at(two.widgets)
        run(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        code, text = run(
            capsys, "session", "transition", "abandoned", "--reason", "dropped",
            "--id", "aaaa1111",
        )
        assert code == 0, text
        assert lease_record(two.store)["units"] == {}
        assert spans(two.widgets, "aaaa1111")[0]["released_at"]


class TestTakeover:
    def _age(self, store: Path, sid: str, minutes: float) -> None:
        path = store / ".git" / "invariant" / "locks" / "docs.json"
        record = json.loads(path.read_text())
        for lease in record["leases"].values():
            if lease["session"] == sid:
                lease["heartbeat"] = time.time() - minutes * 60
        path.write_text(json.dumps(record))

    def test_a_live_lease_is_not_taken_over(self, two, capsys):
        two.at(two.widgets)
        run(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        two.at(two.gadgets)
        code, text = run(
            capsys, "docs", "lock", "--take-over", "architecture", "--reason", "gone",
            "--id", "bbbb2222",
        )
        assert code != 0
        assert "not stale" in text

    def test_a_stale_lease_needs_a_reason(self, two, capsys):
        two.at(two.widgets)
        run(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        self._age(two.store, "aaaa1111", 31)
        two.at(two.gadgets)
        code, text = run(
            capsys, "docs", "lock", "--take-over", "architecture", "--id", "bbbb2222"
        )
        assert code != 0
        assert "--reason" in text
        code, out = run_json(
            capsys, "docs", "lock", "--take-over", "architecture",
            "--reason", "the holder's host crashed", "--id", "bbbb2222",
        )
        assert code == 0, out
        assert out["held"] == ["area:architecture"]
        theirs = session_record(two.widgets, "aaaa1111")["overrides"]
        ours = session_record(two.gadgets, "bbbb2222")["overrides"]
        assert any(o.get("reason") == "the holder's host crashed" for o in theirs)
        assert any(o.get("reason") == "the holder's host crashed" for o in ours)
        assert any(o.get("taken_by") == "bbbb2222" for o in theirs)
        assert spans(two.widgets, "aaaa1111")[0]["taken_over"] is True

    def test_a_dead_host_makes_a_lease_stale(self, two, tmp_path):
        manifest = Manifest.locate(two.widgets)
        lock = DocsLock(manifest, alive=lambda lease: False)
        holder = doclock.Holder("aaaa1111", WIDGETS, "h-1", "/nowhere")
        lock.acquire(holder, ["area:architecture"])
        lease = lock.lease_of("aaaa1111")
        assert "not running" in (lock.staleness(lease) or "")
        alive = DocsLock(manifest, alive=lambda lease: True)
        assert alive.staleness(alive.lease_of("aaaa1111")) is None
        unknown = DocsLock(manifest, alive=lambda lease: None)
        assert unknown.staleness(unknown.lease_of("aaaa1111")) is None

    def test_taking_over_a_free_unit_is_refused(self, two, capsys):
        two.at(two.gadgets)
        code, text = run(
            capsys, "docs", "lock", "--take-over", "architecture", "--reason", "x",
            "--id", "bbbb2222",
        )
        assert code != 0
        assert "free" in text


class TestWaiting:
    def test_wait_blocks_until_granted(self, two, capsys):
        two.at(two.widgets)
        run(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        manifest = Manifest.locate(two.gadgets)
        lock = DocsLock(manifest)
        holder = doclock.Holder("bbbb2222", GADGETS, None, str(manifest.state_root))
        lock.acquire(holder, ["area:architecture"])
        result: dict = {}

        def waiter() -> None:
            result["outcome"] = lock.wait("bbbb2222", timeout=20)

        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(0.3)
        assert thread.is_alive()
        DocsLock(Manifest.locate(two.widgets)).release("aaaa1111", None)
        thread.join(10)
        assert not thread.is_alive()
        assert result["outcome"].waiting is None

    def test_wait_times_out_still_queued(self, two, capsys):
        two.at(two.widgets)
        run(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        two.at(two.gadgets)
        code, text = run(
            capsys, "docs", "lock", "architecture", "--wait", "--timeout", "0.2",
            "--id", "bbbb2222",
        )
        assert code != 0
        assert "still queued" in text


class TestRefusals:
    def test_a_memory_path_is_outside_the_lock(self, two, capsys):
        body = yaml.safe_load((two.store / "invariant.yaml").read_text(encoding="utf-8"))
        body["store"]["repos"][WIDGETS]["records"]["memory"] = "store"
        body["store"]["repos"][WIDGETS]["paths"] = {"memory": "memory"}
        (two.store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
        two.at(two.widgets)
        code, text = run(capsys, "docs", "lock", "memory/note.md", "--id", "aaaa1111")
        assert code != 0
        assert "ruling 20" in text

    def test_a_path_outside_the_store_is_refused(self, two, capsys, tmp_path):
        two.at(two.widgets)
        code, text = run(capsys, "docs", "lock", str(tmp_path / "x.rst"), "--id", "aaaa1111")
        assert code != 0
        assert "outside the store" in text

    def test_an_unknown_area_key_is_refused(self, two, capsys):
        two.at(two.widgets)
        code, text = run(capsys, "docs", "lock", "area:nope", "--id", "aaaa1111")
        assert code != 0
        assert "nope" in text

    def test_no_session_is_refused(self, two, capsys):
        two.at(two.widgets)
        code, _ = run(capsys, "docs", "lock", "architecture", "--id", "ffffffff")
        assert code != 0


class TestVisible:
    def test_session_status_and_list_show_holder_age_and_queue(self, two, capsys):
        two.at(two.widgets)
        run(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        two.at(two.gadgets)
        run(capsys, "docs", "lock", "architecture", "--id", "bbbb2222")
        code, text = run(capsys, "session", "status", "--id", "bbbb2222")
        assert code == 0
        assert "area:architecture" in text
        assert "aaaa1111" in text
        assert "queue" in text and "bbbb2222" in text
        code, text = run(capsys, "session", "list")
        assert "area:architecture" in text and "held by aaaa1111" in text

    def test_an_in_repo_session_list_is_unchanged(self, tmp_path, monkeypatch, capsys):
        root = new_repo(tmp_path / "plain", "https://host.example/team/plain.git")
        (root / ".claude").mkdir()
        (root / ".claude" / "invariant.yaml").write_text("version: 1\n", encoding="utf-8")
        monkeypatch.chdir(root)
        run(capsys, "session", "start", "plain", "--id", "cccc3333")
        code, text = run(capsys, "session", "list")
        assert "docs lock" not in text
        code, text = run(capsys, "docs", "lock", "docs/x.rst", "--id", "cccc3333")
        assert code != 0
        assert "store" in text


class TestHeartbeat:
    def test_commands_refresh_at_most_once_a_minute(self, two):
        manifest = Manifest.locate(two.widgets)
        now = [1000.0]
        lock = DocsLock(manifest, clock=lambda: now[0])
        holder = doclock.Holder("aaaa1111", WIDGETS, None, str(manifest.state_root))
        lock.acquire(holder, ["area:architecture"])
        assert lock.lease_of("aaaa1111")["heartbeat"] == 1000.0
        now[0] = 1030.0
        assert lock.touch("aaaa1111") is False
        now[0] = 1061.0
        assert lock.touch("aaaa1111") is True
        assert lock.lease_of("aaaa1111")["heartbeat"] == 1061.0
        assert lock.touch("nobody") is False

    def test_a_lease_past_its_minutes_is_stale(self, two):
        manifest = Manifest.locate(two.widgets)
        now = [1000.0]
        lock = DocsLock(manifest, clock=lambda: now[0])
        holder = doclock.Holder("aaaa1111", WIDGETS, None, str(manifest.state_root))
        lock.acquire(holder, ["area:architecture"])
        now[0] = 1000.0 + 31 * 60
        assert "heartbeat" in (lock.staleness(lock.lease_of("aaaa1111")) or "")


def test_the_lease_record_is_untracked(two, capsys):
    two.at(two.widgets)
    run(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
    assert (two.store / ".git" / "invariant" / "locks" / "docs.json").is_file()
    assert git(two.store, "status", "--porcelain") == ""


def test_errors_are_the_engine_s(two):
    manifest = Manifest.locate(two.widgets)
    lock = DocsLock(manifest)
    with pytest.raises(UsageError):
        lock.release("nobody", ["area:architecture"])
    assert issubclass(StateError, Exception)


class TestCliPieces:
    def test_the_host_probe_reads_the_attention_sidecar(self, tmp_path):
        import os

        from invariant.cli.main import _lease_host_alive
        from invariant.core.store import JsonStore

        root = tmp_path / "state"
        JsonStore(root).write("attention/host-1.json", {"host_session_id": "host-1", "pid": os.getpid()})
        JsonStore(root).write("attention/host-2.json", {"host_session_id": "host-2", "pid": 999999})
        JsonStore(root).write("attention/host-3.json", {"host_session_id": "host-3"})
        lease = {"state_root": str(root)}
        assert _lease_host_alive({**lease, "host": "host-1"}) is True
        assert _lease_host_alive({**lease, "host": "host-2"}) is False
        assert _lease_host_alive({**lease, "host": "host-3"}) is None
        assert _lease_host_alive({**lease, "host": "host-4"}) is None
        assert _lease_host_alive({**lease, "host": None}) is None
        assert _lease_host_alive({**lease, "host": "../bad id"}) is None

    def test_held_for_reads_as_a_duration(self):
        from invariant.cli.main import _held_for

        assert _held_for(5) == "5s"
        assert _held_for(125) == "2m"
        assert _held_for(3 * 3600 + 7 * 60) == "3h07m"

    def test_no_session_here_is_refused_naming_the_way_on(self, two, capsys, monkeypatch):
        monkeypatch.setenv("INVARIANT_SESSION_ID", "")
        two.at(two.store)
        code, _ = run(capsys, "docs", "lock", "architecture")
        assert code != 0

    def test_take_over_names_one_unit(self, two, capsys):
        two.at(two.gadgets)
        code, text = run(
            capsys, "docs", "lock", "guidelines", "--take-over", "architecture",
            "--reason", "x", "--id", "bbbb2222",
        )
        assert code != 0
        assert "one unit" in text

    def test_an_idle_store_says_no_unit_is_held(self, two, capsys):
        two.at(two.widgets)
        _, text = run(capsys, "session", "list")
        assert "no unit held" in text

    def test_an_in_repo_session_domain_has_no_units(self, tmp_path, monkeypatch, capsys):
        root = new_repo(tmp_path / "plain", "https://host.example/team/plain.git")
        (root / ".claude").mkdir()
        (root / ".claude" / "invariant.yaml").write_text("version: 1\n", encoding="utf-8")
        monkeypatch.chdir(root)
        run(capsys, "session", "start", "plain", "--id", "cccc3333")
        domain = SessionDomain(Manifest.locate(root))
        session = domain.require("cccc3333")
        assert domain.release_docs_lock(session) is None
        assert domain.docs_units() == []
        assert session.legs == []

    def test_a_takeover_from_a_session_whose_record_is_gone_still_lands(self, two, capsys):
        two.at(two.widgets)
        run(capsys, "docs", "lock", "architecture", "--id", "aaaa1111")
        TestTakeover._age(None, two.store, "aaaa1111", 31)
        state = Manifest.locate(two.widgets).state_root
        (state / "sessions" / "aaaa1111.json").unlink()
        two.at(two.gadgets)
        code, out = run_json(
            capsys, "docs", "lock", "--take-over", "architecture", "--reason", "gone",
            "--id", "bbbb2222",
        )
        assert code == 0
        assert out["held"] == ["area:architecture"]
