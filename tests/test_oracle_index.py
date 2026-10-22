"""The oracle's user-level state: its home directory and the project index.

The index is the first state this library keeps OUTSIDE any project, so two
things are held here beyond the behaviour itself: no test may reach the
developer's real home (the autouse ``INVARIANT_HOME`` fixture in conftest), and
the two automatic registration routes — ``init`` and the SessionStart hook —
can never change what those commands report.
"""

import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from invariant.cli.main import main
from invariant.core.errors import StateError, UsageError
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.oracle import index as index_module
from invariant.oracle.home import HOME_ENV, oracle_home
from invariant.oracle.index import INDEX_FILE, ProjectIndex

# -- fixtures ------------------------------------------------------------


def git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


def write_manifest(root: Path, name: str) -> None:
    path = root / MANIFEST_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"version": 1, "project": {"name": name}}), encoding="utf-8"
    )


@pytest.fixture
def repo(tmp_path):
    """A committed git repository carrying a manifest at its root."""
    root = tmp_path / "proj"
    root.mkdir()
    git(root, "init", "-q", "-b", "master")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    write_manifest(root, "demo")
    (root / "docs" / "architecture").mkdir(parents=True)
    (root / "docs" / "architecture" / "overview.md").write_text("# O\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root.resolve()


@pytest.fixture
def worktree(repo, tmp_path):
    path = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "side", str(path))
    return path.resolve()


@pytest.fixture
def home():
    return oracle_home()


@pytest.fixture
def index(home):
    return ProjectIndex(home)


def stored(home: Path) -> dict:
    return json.loads((home / INDEX_FILE).read_text(encoding="utf-8"))


def run(*argv) -> int:
    return main(list(argv))


def last_json(capsys) -> dict:
    text = capsys.readouterr().out
    lines = text.splitlines()
    for start in range(len(lines) - 1, -1, -1):
        if lines[start].startswith("{"):
            return json.loads("\n".join(lines[start:]))
    raise AssertionError(f"no JSON document in output:\n{text}")


def hook(event: str, body: dict) -> int:
    stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps(body))
    try:
        return main(["hook", event])
    finally:
        sys.stdin = stdin


# -- home ----------------------------------------------------------------


class TestHome:
    def test_the_suite_never_reaches_the_real_home(self, tmp_path_factory):
        """The autouse fixture points every test at a temporary directory."""
        home = oracle_home()
        assert tmp_path_factory.getbasetemp() in home.parents
        assert home != Path.home() / ".invariant"

    def test_the_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv(HOME_ENV, str(tmp_path / "elsewhere"))
        assert oracle_home() == tmp_path / "elsewhere"

    def test_unset_falls_back_to_the_user_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv(HOME_ENV, raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert oracle_home() == tmp_path / ".invariant"

    def test_an_empty_override_is_unset(self, monkeypatch, tmp_path):
        monkeypatch.setenv(HOME_ENV, "")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert oracle_home() == tmp_path / ".invariant"

    def test_an_explicit_environment_is_read(self, tmp_path):
        assert oracle_home({HOME_ENV: str(tmp_path / "x")}) == tmp_path / "x"


# -- the index -----------------------------------------------------------


class TestRegistration:
    def test_an_absent_index_lists_nothing_and_creates_nothing(self, index, home):
        assert index.list() == []
        assert not home.exists()

    def test_register_records_the_state_root_as_the_key(self, index, home, repo):
        manifest = Manifest.load(repo)
        entry, added = index.register(manifest, via="manual")
        assert added is True
        assert entry.key == str(manifest.state_root)
        assert entry.root == str(repo)
        assert entry.name == "demo"
        assert entry.via == "manual"
        document = stored(home)
        assert document["version"] == 1
        assert document["projects"] == [entry.to_dict()]

    def test_an_entry_stores_no_clock_time(self, index, home, repo):
        index.register(Manifest.load(repo), via="init")
        (row,) = stored(home)["projects"]
        assert set(row) == {"key", "root", "name", "via"}

    def test_two_worktrees_collapse_to_one_entry(self, index, repo, worktree):
        first, added_first = index.register(Manifest.load(repo), via="init")
        second, added_second = index.register(
            Manifest.load(worktree), via="session-start"
        )
        assert (added_first, added_second) == (True, False)
        assert second == first
        assert len(index.list()) == 1

    def test_a_worktree_registering_first_records_the_main_checkout(
        self, index, repo, worktree
    ):
        entry, _ = index.register(Manifest.load(worktree), via="session-start")
        assert entry.root == str(repo)
        assert entry.via == "session-start"

    def test_several_manifests_sharing_one_git_dir_collapse(self, index, repo):
        sub = repo / "packages" / "lib"
        write_manifest(sub, "lib")
        index.register(Manifest.load(repo), via="init")
        _, added = index.register(Manifest.load(sub), via="init")
        assert added is False
        assert [e.name for e in index.list()] == ["demo"]

    def test_a_subproject_in_a_worktree_records_its_main_checkout_spelling(
        self, index, repo, tmp_path
    ):
        sub = repo / "packages" / "lib"
        write_manifest(sub, "lib")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "sub")
        wt = tmp_path / "wt2"
        git(repo, "worktree", "add", "-q", "-b", "side2", str(wt))
        entry, _ = index.register(Manifest.load(wt / "packages" / "lib"), via="manual")
        assert entry.root == str(sub)

    def test_a_project_outside_git_keys_on_its_own_state_root(self, index, tmp_path):
        root = tmp_path / "plain"
        write_manifest(root, "plain")
        entry, _ = index.register(Manifest.load(root), via="manual")
        assert entry.key == str(root.resolve() / ".claude" / "invariant" / "state")
        assert entry.root == str(root.resolve())

    def test_a_present_project_is_answered_without_the_lock(
        self, index, repo, monkeypatch
    ):
        """The SessionStart path reads first; a known project takes no lock."""
        index.register(Manifest.load(repo), via="init")

        def refuse(*_a, **_k):
            raise AssertionError("the lock was taken for a known project")

        monkeypatch.setattr(index._store, "locked", refuse)
        _, added = index.register(Manifest.load(repo), via="session-start")
        assert added is False

    def test_a_race_lost_inside_the_lock_adds_nothing(self, index, home, repo):
        """Two hosts both read 'missing'; the second to lock finds it present."""
        manifest = Manifest.load(repo)
        other = ProjectIndex(home)
        real_read = index._read_entries
        calls = {"n": 0}

        def stale_read():
            calls["n"] += 1
            if calls["n"] == 1:
                other.register(manifest, via="init")
                return []
            return real_read()

        index._read_entries = stale_read
        _, added = index.register(manifest, via="session-start")
        assert added is False
        assert len(index.list()) == 1
        assert index.list()[0].via == "init"

    def test_an_unknown_route_is_refused(self, index, repo):
        with pytest.raises(UsageError):
            index.register(Manifest.load(repo), via="cron")

    def test_a_manifest_without_a_name_uses_the_directory(self, index, tmp_path):
        root = tmp_path / "nameless"
        (root / MANIFEST_RELPATH).parent.mkdir(parents=True)
        (root / MANIFEST_RELPATH).write_text("version: 1\n", encoding="utf-8")
        entry, _ = index.register(Manifest.load(root), via="manual")
        assert entry.name == "nameless"

    def test_get_finds_by_key(self, index, repo):
        entry, _ = index.register(Manifest.load(repo), via="init")
        assert index.get(entry.key) == entry
        assert index.get("/nowhere") is None


class TestTheDocument:
    def test_keys_this_build_does_not_know_survive_a_write(self, index, home, repo):
        home.mkdir(parents=True)
        (home / INDEX_FILE).write_text(
            json.dumps(
                {
                    "version": 1,
                    "later": {"x": 1},
                    "projects": [
                        {"key": "/k", "root": "/r", "name": "n", "via": "manual",
                         "extra": True}
                    ],
                }
            ),
            encoding="utf-8",
        )
        index.register(Manifest.load(repo), via="init")
        document = stored(home)
        assert document["later"] == {"x": 1}
        assert document["projects"][0]["extra"] is True

    def test_a_newer_version_is_refused_not_rewritten(self, index, home, repo):
        home.mkdir(parents=True)
        body = json.dumps({"version": 2, "projects": []})
        (home / INDEX_FILE).write_text(body, encoding="utf-8")
        with pytest.raises(StateError, match="version 2"):
            index.list()
        with pytest.raises(StateError):
            index.register(Manifest.load(repo), via="init")
        assert (home / INDEX_FILE).read_text(encoding="utf-8") == body

    def test_malformed_json_is_a_state_error(self, index, home):
        home.mkdir(parents=True)
        (home / INDEX_FILE).write_text("{nope", encoding="utf-8")
        with pytest.raises(StateError):
            index.list()

    @pytest.mark.parametrize(
        "document",
        [[], {"version": 1, "projects": {}}, {"version": 1, "projects": [{"root": "/r"}]}],
    )
    def test_a_wrong_shape_is_a_state_error(self, index, home, document):
        home.mkdir(parents=True)
        (home / INDEX_FILE).write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(StateError):
            index.list()

    def test_the_home_holds_only_the_index_and_its_lock(self, index, home, repo):
        index.register(Manifest.load(repo), via="init")
        assert sorted(p.name for p in home.iterdir()) == sorted([INDEX_FILE, "locks"])


class TestRemoveAndPrune:
    @pytest.fixture
    def two(self, index, repo, tmp_path):
        other = tmp_path / "other"
        write_manifest(other, "other")
        a, _ = index.register(Manifest.load(repo), via="init")
        b, _ = index.register(Manifest.load(other), via="manual")
        return a, b

    def test_remove_by_key(self, index, two):
        a, b = two
        assert index.remove(a.key) == a
        assert index.list() == [b]

    def test_remove_by_root(self, index, two):
        a, b = two
        assert index.remove(b.root) == b
        assert index.list() == [a]

    def test_remove_by_a_relative_root(self, index, two, monkeypatch):
        _, b = two
        monkeypatch.chdir(Path(b.root).parent)
        assert index.remove("./other") == b

    def test_remove_by_name(self, index, two):
        a, b = two
        assert index.remove("demo") == a
        assert index.list() == [b]

    def test_remove_matching_nothing_is_refused(self, index, two):
        with pytest.raises(UsageError, match="demo"):
            index.remove("absent")
        assert len(index.list()) == 2

    def test_remove_on_an_empty_index_is_refused(self, index):
        with pytest.raises(UsageError, match="no projects"):
            index.remove("absent")

    def test_an_ambiguous_name_is_refused_with_every_key(self, index, two, tmp_path):
        a, _ = two
        twin = tmp_path / "twin"
        write_manifest(twin, "demo")
        c, _ = index.register(Manifest.load(twin), via="manual")
        with pytest.raises(UsageError) as caught:
            index.remove("demo")
        assert a.key in str(caught.value) and c.key in str(caught.value)
        assert len(index.list()) == 3

    def test_prune_drops_roots_without_a_manifest(self, index, two):
        a, b = two
        (Path(b.root) / MANIFEST_RELPATH).unlink()
        assert index.prune() == [b]
        assert index.list() == [a]

    def test_prune_dry_run_changes_nothing(self, index, home, two):
        _, b = two
        (Path(b.root) / MANIFEST_RELPATH).unlink()
        before = (home / INDEX_FILE).read_bytes()
        assert index.prune(dry_run=True) == [b]
        assert (home / INDEX_FILE).read_bytes() == before

    def test_prune_with_nothing_to_drop_takes_no_lock(self, index, two, monkeypatch):
        monkeypatch.setattr(
            index._store, "locked", lambda *a, **k: pytest.fail("locked")
        )
        assert index.prune() == []


# -- CLI -----------------------------------------------------------------


@pytest.fixture
def elsewhere(tmp_path, monkeypatch):
    """A working directory governed by no manifest."""
    path = tmp_path / "elsewhere"
    path.mkdir()
    monkeypatch.chdir(path)
    return path


class TestProjectsCommands:
    def test_list_is_empty_from_a_directory_with_no_manifest(
        self, elsewhere, capsys, home
    ):
        assert run("oracle", "projects", "list", "--json") == 0
        assert last_json(capsys) == {"home": str(home), "projects": []}

    def test_list_text_when_empty(self, elsewhere, capsys):
        assert run("oracle", "projects", "list") == 0
        assert "no projects registered" in capsys.readouterr().out

    def test_add_then_list(self, elsewhere, capsys, repo):
        assert run("oracle", "projects", "add", str(repo), "--json") == 0
        added = last_json(capsys)
        assert added["added"] is True
        assert added["project"]["root"] == str(repo)
        assert added["project"]["via"] == "manual"

        assert run("oracle", "projects", "list", "--json") == 0
        assert last_json(capsys)["projects"] == [added["project"]]

        assert run("oracle", "projects", "list") == 0
        text = capsys.readouterr().out
        assert "demo" in text and str(repo) in text and "manual" in text

    def test_add_defaults_to_the_current_directory(self, repo, monkeypatch, capsys):
        monkeypatch.chdir(repo / "docs")
        assert run("oracle", "projects", "add") == 0
        assert "registered demo" in capsys.readouterr().out

    def test_add_twice_is_idempotent(self, elsewhere, capsys, repo):
        run("oracle", "projects", "add", str(repo))
        capsys.readouterr()
        assert run("oracle", "projects", "add", str(repo), "--json") == 0
        assert last_json(capsys)["added"] is False
        assert run("oracle", "projects", "add", str(repo)) == 0
        assert "already registered" in capsys.readouterr().out

    def test_add_where_there_is_no_manifest_is_a_usage_error(
        self, elsewhere, capsys
    ):
        assert run("oracle", "projects", "add", str(elsewhere)) == 2

    def test_remove(self, elsewhere, capsys, repo):
        run("oracle", "projects", "add", str(repo))
        capsys.readouterr()
        assert run("oracle", "projects", "remove", "demo", "--json") == 0
        assert last_json(capsys)["removed"]["root"] == str(repo)
        run("oracle", "projects", "add", str(repo))
        capsys.readouterr()
        assert run("oracle", "projects", "remove", str(repo)) == 0
        assert "removed demo" in capsys.readouterr().out

    def test_remove_nothing_is_a_usage_error(self, elsewhere, capsys):
        assert run("oracle", "projects", "remove", "nothing") == 2

    def test_prune(self, elsewhere, capsys, repo, tmp_path):
        gone = tmp_path / "gone"
        write_manifest(gone, "gone")
        run("oracle", "projects", "add", str(repo))
        run("oracle", "projects", "add", str(gone))
        (gone / MANIFEST_RELPATH).unlink()
        capsys.readouterr()

        assert run("oracle", "projects", "prune", "--dry-run", "--json") == 0
        dry = last_json(capsys)
        assert dry["dry_run"] is True
        assert [p["name"] for p in dry["pruned"]] == ["gone"]

        assert run("oracle", "projects", "prune", "--dry-run") == 0
        assert "would prune gone" in capsys.readouterr().out

        assert run("oracle", "projects", "prune") == 0
        assert "pruned gone" in capsys.readouterr().out
        assert run("oracle", "projects", "prune") == 0
        assert "nothing to prune" in capsys.readouterr().out

    def test_a_broken_index_is_a_state_error(self, elsewhere, home, capsys):
        home.mkdir(parents=True)
        (home / INDEX_FILE).write_text("{", encoding="utf-8")
        assert run("oracle", "projects", "list") == 3


class TestInitRegisters:
    def test_init_registers_the_project(self, repo, monkeypatch, capsys, home):
        (repo / MANIFEST_RELPATH).unlink()
        monkeypatch.chdir(repo)
        assert run("init", "--ecosystem", "python", "--json") == 0
        result = last_json(capsys)
        (row,) = stored(home)["projects"]
        assert row["via"] == "init"
        assert row["root"] == str(repo)
        assert result["oracle"] == {"added": True, "key": row["key"]}

    def test_init_dry_run_registers_nothing_and_says_it_would(
        self, repo, monkeypatch, capsys, home
    ):
        (repo / MANIFEST_RELPATH).unlink()
        monkeypatch.chdir(repo)
        assert run("init", "--dry-run") == 0
        assert "oracle index" in capsys.readouterr().out
        assert not home.exists()

    def test_init_still_succeeds_when_the_index_cannot_be_written(
        self, repo, monkeypatch, capsys, home
    ):
        (repo / MANIFEST_RELPATH).unlink()
        home.parent.mkdir(parents=True, exist_ok=True)
        home.write_text("a file where a directory belongs", encoding="utf-8")
        monkeypatch.chdir(repo)
        assert run("init", "--ecosystem", "python") == 0
        out = capsys.readouterr().out
        assert "initialised" in out
        assert "Not registered in the oracle index" in out
        assert (repo / MANIFEST_RELPATH).is_file()

    def test_init_failure_to_register_is_reported_in_json(
        self, repo, monkeypatch, capsys, home
    ):
        (repo / MANIFEST_RELPATH).unlink()
        home.parent.mkdir(parents=True, exist_ok=True)
        home.write_text("x", encoding="utf-8")
        monkeypatch.chdir(repo)
        assert run("init", "--ecosystem", "python", "--json") == 0
        assert "error" in last_json(capsys)["oracle"]


class TestSessionStartRegisters:
    HOST = "host-oracle-0000"

    @pytest.fixture
    def initialised(self, repo, monkeypatch, capsys, home):
        monkeypatch.chdir(repo)
        assert run("init", "--ecosystem", "python", "--force") == 0
        (home / INDEX_FILE).unlink()
        capsys.readouterr()
        return repo

    def test_a_first_session_start_registers(self, initialised, capsys, home):
        assert hook("session-start", {"session_id": self.HOST}) == 0
        (row,) = stored(home)["projects"]
        assert row["via"] == "session-start"

    def test_the_project_dir_the_host_names_is_the_one_registered(
        self, initialised, capsys, home, monkeypatch, elsewhere
    ):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(initialised))
        assert hook("session-start", {"session_id": self.HOST}) == 0
        assert stored(home)["projects"][0]["root"] == str(initialised)

    def test_the_probe_registers_nothing(self, initialised, capsys, home):
        from invariant.hosts.claude_code import PROBE_KEY

        assert hook("session-start", {PROBE_KEY: True}) == 0
        assert not (home / INDEX_FILE).exists()

    def test_other_events_register_nothing(self, initialised, capsys, home):
        assert hook("user-prompt-submit", {"session_id": self.HOST}) == 0
        assert not (home / INDEX_FILE).exists()

    def test_a_registration_failure_never_changes_the_response(
        self, initialised, capsys, home, monkeypatch
    ):
        assert hook("session-start", {"session_id": self.HOST}) == 0
        healthy = capsys.readouterr().out
        (home / INDEX_FILE).unlink()

        def explode(*_a, **_k):
            raise RuntimeError("index on fire")

        monkeypatch.setattr(index_module.ProjectIndex, "register", explode)
        assert hook("session-start", {"session_id": self.HOST}) == 0
        broken = capsys.readouterr().out
        assert broken == healthy
        assert "invariantDegraded" not in broken

    def test_an_unwritable_home_never_changes_the_response(
        self, initialised, capsys, home
    ):
        assert hook("session-start", {"session_id": self.HOST}) == 0
        healthy = capsys.readouterr().out
        shutil.rmtree(home)
        home.write_text("x", encoding="utf-8")
        assert hook("session-start", {"session_id": self.HOST}) == 0
        assert capsys.readouterr().out == healthy

    def test_a_known_project_writes_nothing(self, initialised, capsys, home):
        hook("session-start", {"session_id": self.HOST})
        before = (home / INDEX_FILE).stat().st_mtime_ns
        hook("session-start", {"session_id": self.HOST})
        assert (home / INDEX_FILE).stat().st_mtime_ns == before


class TestLayering:
    def test_nothing_in_core_or_domains_imports_the_oracle(self):
        src = Path(__file__).resolve().parent.parent / "src" / "invariant"
        offenders = [
            str(path.relative_to(src))
            for layer in ("core", "domains", "adapters")
            for path in (src / layer).rglob("*.py")
            if _imports_oracle(path)
        ]
        assert offenders == []


def _imports_oracle(path: Path) -> bool:
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "oracle" in node.module:
            return True
        if isinstance(node, ast.Import) and any("oracle" in a.name for a in node.names):
            return True
    return False
