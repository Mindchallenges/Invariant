"""ADR-027 D2 and D10: the queue, a record of what runs next in a project.

Three layers, each where it lives: the record's format and the pure edits on
it (``core/queue``), the operations that check a ``ref`` against the backlog
and vet a ``brief`` (``domains/queue``), and ``invariant queue`` joining them
(``cli/main``). The location key is ``core/manifest``'s. Every project here is
a scratch git repository under ``tmp_path``; the oracle home is the suite's
scratch one (``tests/conftest.py``).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from invariant.cli.main import main
from invariant.core import queue as queue_record
from invariant.core.errors import GateFailure, ManifestError, StateError, UsageError
from invariant.core.manifest import DEFAULTS, RECORD_PATH_KEYS, Manifest
from invariant.core.store import JsonStore
from invariant.domains.queue import QueueDomain

# -- the record ---------------------------------------------------------------


class TestTheRecord:
    def test_an_absent_file_is_an_empty_queue(self, tmp_path):
        got = queue_record.read_queue(tmp_path / "queue.json")
        assert got.entries == [] and got.next == 1

    def test_ids_are_minted_and_never_reused(self):
        q = queue_record.Queue()
        first = q.add("IDEA_1", added_by="user")
        second = q.add("IDEA_2", added_by="user")
        q.remove(second.id)
        third = q.add("IDEA_3", added_by="user")
        assert [first.id, second.id, third.id] == ["Q1", "Q2", "Q3"]
        assert [e.id for e in q.entries] == ["Q1", "Q3"]

    def test_add_at_a_position_counts_from_one(self):
        q = queue_record.Queue()
        q.add("a", added_by="user")
        q.add("b", added_by="user")
        q.add("c", added_by="user", at=1)
        assert [e.ref for e in q.entries] == ["c", "a", "b"]
        q.add("d", added_by="user", at=4)
        assert [e.ref for e in q.entries][-1] == "d"

    @pytest.mark.parametrize("at", [0, 3, -1])
    def test_a_position_outside_the_list_is_refused(self, at):
        q = queue_record.Queue()
        q.add("a", added_by="user")
        with pytest.raises(UsageError, match="position"):
            q.add("b", added_by="user", at=at)

    def test_move_puts_an_entry_at_a_position(self):
        q = queue_record.Queue()
        for ref in "abc":
            q.add(ref, added_by="user")
        q.move("Q3", 1)
        assert [e.ref for e in q.entries] == ["c", "a", "b"]
        q.move("Q3", 3)
        assert [e.ref for e in q.entries] == ["a", "b", "c"]
        with pytest.raises(UsageError, match="position"):
            q.move("Q1", 4)

    def test_an_unknown_id_is_refused_naming_the_ids(self):
        q = queue_record.Queue()
        q.add("a", added_by="user")
        with pytest.raises(UsageError, match="Q1"):
            q.remove("Q9")
        with pytest.raises(UsageError, match="no entry"):
            q.move("Q9", 1)

    def test_head_is_the_first_queued_entry(self):
        q = queue_record.Queue()
        q.add("a", added_by="user")
        q.add("b", added_by="user")
        q.entries[0].state = "started"
        q.entries[0].session = "s1"
        assert q.head().ref == "b"
        assert q.holding("a").id == "Q1"
        q.entries[0].state = "done"
        assert q.holding("a") is None
        assert queue_record.Queue().head() is None

    def test_it_round_trips_and_keeps_keys_it_does_not_know(self, tmp_path):
        path = tmp_path / "docs" / "queue.json"
        path.parent.mkdir()
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "next": 3,
                    "later": {"x": 1},
                    "entries": [
                        {"id": "Q1", "ref": "IDEA_1", "added_by": "user",
                         "state": "failed", "reason": "launch failed", "why": "kept"},
                        {"id": "Q2", "ref": "a label", "added_by": "abc",
                         "state": "started", "session": "abc"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        q = queue_record.read_queue(path)
        assert q.entries[0].reason == "launch failed"
        queue_record.write_queue(path, q)
        again = json.loads(path.read_text(encoding="utf-8"))
        assert again["later"] == {"x": 1}
        assert again["entries"][0]["why"] == "kept"
        assert "brief" not in again["entries"][0]
        assert again["entries"][1]["session"] == "abc"
        assert again["next"] == 3

    def test_no_clock_time_is_written(self, tmp_path):
        path = tmp_path / "queue.json"
        q = queue_record.Queue()
        q.add("IDEA_1", added_by="user", brief="do it")
        queue_record.write_queue(path, q)
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert set(raw) == {"version", "next", "entries"}
        assert set(raw["entries"][0]) == {"id", "ref", "added_by", "state", "brief"}

    @pytest.mark.parametrize(
        ("doc", "needle"),
        [
            ([], "not a queue"),
            ({"version": 2, "entries": []}, "version"),
            ({"version": 1, "entries": {}}, "entries"),
            ({"version": 1, "entries": [{"ref": "x"}]}, "id"),
            ({"version": 1, "entries": [{"id": "Q1", "ref": "x", "state": "odd"}]}, "state"),
            ({"version": 1, "next": "x", "entries": []}, "next"),
            ({"version": 1, "entries": [{"id": "Q1", "ref": ""}]}, "ref"),
            ({"version": 1, "entries": ["Q1"]}, "entry"),
        ],
    )
    def test_a_malformed_file_is_refused_naming_what(self, tmp_path, doc, needle):
        path = tmp_path / "queue.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(StateError, match=needle):
            queue_record.read_queue(path)

    def test_a_file_that_is_not_json_is_refused(self, tmp_path):
        path = tmp_path / "queue.json"
        path.write_text("{", encoding="utf-8")
        with pytest.raises(StateError, match="queue"):
            queue_record.read_queue(path)

    def test_next_never_falls_behind_an_id_on_file(self, tmp_path):
        path = tmp_path / "queue.json"
        path.write_text(
            json.dumps({"version": 1, "next": 1, "entries": [
                {"id": "Q7", "ref": "x", "added_by": "user", "state": "queued"}]}),
            encoding="utf-8",
        )
        q = queue_record.read_queue(path)
        assert q.add("y", added_by="user").id == "Q8"


# -- where it lives -----------------------------------------------------------


def resolve(raw: dict, tmp_path: Path, store_root: Path | None = None) -> Manifest:
    return Manifest.from_raw(
        raw, root=tmp_path, path=tmp_path / "m.yaml", env={}, store_root=store_root
    )


class TestItsLocation:
    def test_the_defaults_carry_no_queue_record_of_their_own(self):
        assert "queue" not in DEFAULTS["records"]
        assert DEFAULTS["paths"]["queue"] == "docs/queue.json"
        assert RECORD_PATH_KEYS["queue"] == ("paths.queue",)

    def test_unset_it_follows_the_backlog(self, tmp_path):
        m = resolve({}, tmp_path)
        assert m.get("records.queue") == "repo"
        assert "records.backlog" in m.source_of("records.queue")

    def test_unset_it_follows_a_backlog_in_the_store(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        m = resolve({"records": {"backlog": "store"}}, tmp_path, store_root=store)
        assert m.get("records.queue") == "store"
        assert m.get("paths.queue") == str(store / "docs/queue.json")

    def test_set_it_is_its_own(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        m = resolve(
            {"records": {"backlog": "store", "queue": "repo"}}, tmp_path, store_root=store
        )
        assert m.get("records.queue") == "repo"
        assert m.get("paths.queue") == "docs/queue.json"
        assert m.source_of("records.queue") == "repo:m.yaml"

    def test_a_bad_location_is_refused(self, tmp_path):
        with pytest.raises(ManifestError, match="records.queue"):
            resolve({"records": {"queue": "local"}}, tmp_path)

    def test_a_store_queue_path_may_not_climb_out(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        with pytest.raises(ManifestError, match="paths.queue"):
            resolve(
                {"records": {"queue": "store"}, "paths": {"queue": "../q.json"}},
                tmp_path, store_root=store,
            )

    def test_a_store_queue_path_is_used_as_joined(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        m = resolve({"records": {"queue": "store"}}, tmp_path, store_root=store)
        assert queue_record.queue_path(m) == store / "docs/queue.json"


# -- the operations -----------------------------------------------------------


def _git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "master")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    monkeypatch.chdir(root)
    monkeypatch.delenv("INVARIANT_HOST_SESSION_ID", raising=False)
    assert main(["init", "--home", "repo", "--name", "demo"]) == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "manifest")
    return root


def _domain(root: Path) -> QueueDomain:
    manifest = Manifest.locate(root)
    return QueueDomain(manifest, JsonStore(manifest.state_root))


def _file(root: Path, item_type: str, title: str) -> str:
    assert main(["item", "new", item_type, "--title", title, "--json"]) == 0
    return _domain(root).items.list()[-1].id


class TestTheDomain:
    def test_a_free_label_is_accepted_as_is(self, project):
        entry = _domain(project).add("ADR-027 inc 2", brief="build the chain step")
        assert (entry.id, entry.ref, entry.state, entry.added_by) == (
            "Q1", "ADR-027 inc 2", "queued", "user")
        on_disk = json.loads((project / "docs/queue.json").read_text(encoding="utf-8"))
        assert on_disk["entries"][0]["brief"] == "build the chain step"

    def test_a_work_item_ref_must_exist(self, project):
        with pytest.raises(UsageError, match="IDEA_404"):
            _domain(project).add("IDEA_404")
        assert not (project / "docs/queue.json").exists()

    def test_an_open_work_item_is_accepted(self, project):
        item = _file(project, "idea", "Something to do")
        assert _domain(project).add(item).ref == item

    def test_a_closed_work_item_is_refused(self, project):
        item = _file(project, "idea", "Something dropped")
        assert main(["item", "transition", item, "dropped", "--reason", "no"]) == 0
        with pytest.raises(UsageError, match="dropped"):
            _domain(project).add(item)

    def test_a_ref_already_queued_is_refused(self, project):
        domain = _domain(project)
        domain.add("label")
        with pytest.raises(UsageError, match="Q1"):
            domain.add("label")

    @pytest.mark.parametrize("brief", ["two\nlines", "x" * 2001, "   "])
    def test_a_brief_out_of_bounds_is_refused(self, project, brief):
        with pytest.raises(UsageError):
            _domain(project).add("label", brief=brief)

    def test_a_brief_with_a_privacy_finding_is_refused_and_redacted(self, project):
        with pytest.raises(GateFailure) as caught:
            _domain(project).add("label", brief="mail someone@example.org")
        assert "someone@example.org" not in str(caught.value)
        assert not (project / "docs/queue.json").exists()

    def test_a_ref_is_vetted_too(self, project):
        with pytest.raises(GateFailure):
            _domain(project).add("ask someone@example.org")
        with pytest.raises(UsageError):
            _domain(project).add("x" * 201)

    def test_remove_and_move(self, project):
        domain = _domain(project)
        domain.add("a")
        domain.add("b")
        domain.move("Q2", 1)
        assert [e.ref for e in domain.list().entries] == ["b", "a"]
        assert domain.remove("Q2").ref == "b"
        assert [e.id for e in domain.list().entries] == ["Q1"]

    def test_a_worktree_writes_the_main_checkout_queue(self, project, tmp_path):
        worktree = tmp_path / "wt"
        _git(project, "worktree", "add", "-q", str(worktree), "-b", "side")
        manifest = Manifest.locate(worktree)
        QueueDomain(manifest, JsonStore(manifest.state_root)).add("from the side")
        assert (project / "docs/queue.json").is_file()
        assert not (worktree / "docs/queue.json").exists()


# -- the commands ---------------------------------------------------------------


def _out(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


class TestTheCommands:
    def test_add_list_move_remove(self, project, capsys):
        assert main(["queue", "add", "first", "--json"]) == 0
        assert _out(capsys)["entry"]["id"] == "Q1"
        assert main(["queue", "add", "second", "--brief", "go", "--at", "1"]) == 0
        text = capsys.readouterr().out
        assert "Q2" in text and "second" in text
        assert main(["queue", "list", "--json"]) == 0
        listed = _out(capsys)
        assert [e["ref"] for e in listed["entries"]] == ["second", "first"]
        assert listed["path"].endswith("docs/queue.json")
        assert main(["queue", "move", "Q1", "--to", "1", "--json"]) == 0
        assert [e["id"] for e in _out(capsys)["entries"]] == ["Q1", "Q2"]
        assert main(["queue", "remove", "Q2", "--json"]) == 0
        assert _out(capsys)["removed"]["ref"] == "second"
        assert main(["queue", "list"]) == 0
        assert "first" in capsys.readouterr().out

    def test_an_empty_list_says_so(self, project, capsys):
        assert main(["queue", "list"]) == 0
        assert "empty" in capsys.readouterr().out

    def test_a_refusal_writes_nothing(self, project, capsys):
        assert main(["queue", "add", "BUG_999"]) == 2
        assert "BUG_999" in capsys.readouterr().err
        assert not (project / "docs/queue.json").exists()

    def test_a_session_adding_is_named_as_the_adder(self, project, capsys):
        assert main(["session", "start", "work", "--json"]) == 0
        session = _out(capsys)["session"]["id"]
        assert main(["queue", "add", "next thing", "--id", session, "--json"]) == 0
        assert _out(capsys)["entry"]["added_by"] == session

    def test_without_a_session_the_user_adds(self, project, capsys):
        assert main(["queue", "add", "next thing", "--json"]) == 0
        assert _out(capsys)["entry"]["added_by"] == "user"
