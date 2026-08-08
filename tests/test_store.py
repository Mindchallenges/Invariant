import fcntl
import json
import threading

import pytest

from invariant.core.errors import LockTimeout, StateError
from invariant.core.store import JsonStore


@pytest.fixture
def store(tmp_path):
    s = JsonStore(tmp_path / "state")
    s.ensure_root()
    return s


class TestReadWrite:
    def test_roundtrip(self, store):
        store.write("sessions/abc.json", {"state": "isolated"})
        assert store.read("sessions/abc.json") == {"state": "isolated"}

    def test_missing_returns_default(self, store):
        assert store.read("sessions/nope.json", default={"x": 1}) == {"x": 1}
        assert store.read("sessions/nope.json") is None

    def test_write_creates_parent_directories(self, store):
        store.write("deep/nested/file.json", [1, 2])
        assert store.read("deep/nested/file.json") == [1, 2]

    def test_corrupt_json_names_the_repair_command(self, store):
        path = store.root / "sessions" / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(StateError, match="invariant state repair"):
            store.read("sessions/bad.json")

    def test_write_leaves_no_temp_files_behind(self, store):
        store.write("sessions/abc.json", {"a": 1})
        leftovers = list((store.root / "sessions").glob(".*tmp"))
        assert leftovers == []

    def test_written_json_is_human_readable(self, store):
        # The recovery path for a wedged store is a human opening the file, so
        # indentation and stable key order are a contract, not a nicety.
        store.write("sessions/abc.json", {"b": 1, "a": 2})
        raw = (store.root / "sessions" / "abc.json").read_text(encoding="utf-8")
        assert raw.startswith("{\n")
        assert raw.index('"a"') < raw.index('"b"')
        assert raw.endswith("\n")

    def test_existing_file_survives_a_failed_write(self, store, monkeypatch):
        store.write("sessions/abc.json", {"good": True})

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("os.replace", boom)
        with pytest.raises(StateError, match="cannot write"):
            store.write("sessions/abc.json", {"good": False})
        assert store.read("sessions/abc.json") == {"good": True}


class TestPathSafety:
    def test_escaping_the_root_is_refused(self, store):
        with pytest.raises(StateError, match="escapes the state root"):
            store.read("../../secrets.json")

    def test_escaping_via_write_is_refused(self, store):
        with pytest.raises(StateError, match="escapes the state root"):
            store.write("../outside.json", {})


class TestListAndDelete:
    def test_list_ids_is_sorted_and_stem_only(self, store):
        for name in ("b", "a", "c"):
            store.write(f"sessions/{name}.json", {})
        assert store.list_ids("sessions") == ["a", "b", "c"]

    def test_list_ids_of_missing_directory(self, store):
        assert store.list_ids("nothing") == []

    def test_delete(self, store):
        store.write("sessions/a.json", {})
        assert store.delete("sessions/a.json") is True
        assert store.delete("sessions/a.json") is False


class TestLocking:
    def test_mutation_is_written_back(self, store):
        with store.locked("registry.json", default={"sessions": {}}) as data:
            data["sessions"]["s1"] = {"state": "working"}
        assert store.read("registry.json")["sessions"]["s1"]["state"] == "working"

    def test_exception_leaves_the_previous_document_intact(self, store):
        store.write("registry.json", {"sessions": {"s1": {}}})
        with pytest.raises(ValueError):
            with store.locked("registry.json") as data:
                data["sessions"]["s2"] = {}
                raise ValueError("mid-mutation failure")
        # A half-applied registry is worse than no change at all.
        assert store.read("registry.json") == {"sessions": {"s1": {}}}

    def test_a_held_lock_times_out_rather_than_waiting_forever(self, store):
        lock_path = store.root / "locks" / "registry.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        blocker = open(lock_path, "a+")
        fcntl.flock(blocker.fileno(), fcntl.LOCK_EX)
        try:
            with pytest.raises(LockTimeout, match="state repair"):
                with store.locked("registry.json", timeout=0.15):
                    pass
        finally:
            fcntl.flock(blocker.fileno(), fcntl.LOCK_UN)
            blocker.close()

    def test_concurrent_writers_do_not_lose_updates(self, store):
        store.write("registry.json", {"n": 0})
        errors = []

        def bump():
            try:
                for _ in range(20):
                    with store.locked("registry.json") as data:
                        data["n"] += 1
            except Exception as exc:  # pragma: no cover - surfaced via assert
                errors.append(exc)

        threads = [threading.Thread(target=bump) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # Without the lock held across read-modify-write this loses increments.
        assert store.read("registry.json")["n"] == 80

    def test_lock_is_released_after_use(self, store):
        with store.locked("registry.json", default={}):
            pass
        with store.locked("registry.json", default={}) as data:
            data["second"] = True
        assert store.read("registry.json") == {"second": True}


def test_ensure_root_is_idempotent(tmp_path):
    store = JsonStore(tmp_path / "state")
    store.ensure_root()
    store.ensure_root()
    assert (store.root / "sessions").is_dir()


def test_ensure_root_reports_a_real_failure(tmp_path):
    blocker = tmp_path / "state"
    blocker.write_text("I am a file, not a directory", encoding="utf-8")
    with pytest.raises(StateError, match="cannot create state root"):
        JsonStore(blocker).ensure_root()


def test_json_is_valid_on_disk(store):
    store.write("sessions/a.json", {"k": "v"})
    json.loads((store.root / "sessions" / "a.json").read_text(encoding="utf-8"))
