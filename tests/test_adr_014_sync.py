"""ADR-014 increment 5a: the store's sync (D7).

A store kept in step with its remote: `sync.*` read at the store's level,
with the user's defaults below it; a pull at session start, resume and `docs
lock` whose four states each say what they did (behind fast-forwards, ahead
carries on, offline warns and records `store_pull: stale`, diverged stops
with nothing registered); `store pull --replay`, which replays unpublished
commits on top of the remote unless the same file changed on both sides; and
the first clone of a store, which is explicit and shows the commands the store
declares before anything can run them (ruling 18).

Every remote is a local bare repository, and offline is a remote path that
does not exist: nothing here reaches a network. The home is the suite's
scratch one.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.cli.main import main
from invariant.core import git as gitq
from invariant.core.errors import ManifestError
from invariant.core.home import invariant_home, user_config_path
from invariant.core.manifest import Manifest
from invariant.domains.session import SessionDomain

WIDGETS = "host.example/team/widgets"
GADGETS = "host.example/team/gadgets"


class _Lazy:
    """``core/storesync``, imported when first used, so a build without it
    fails each test that needs it rather than the whole module."""

    def __getattr__(self, name):
        import importlib

        return getattr(importlib.import_module("invariant.core.storesync"), name)


storesync = _Lazy()


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


def identify(repo: Path) -> None:
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "T")


def new_repo(root: Path, origin: str) -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    identify(root)
    (root / "conf.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    git(root, "remote", "add", "origin", origin)
    return root


def edit(repo: Path, rel: str, text: str, message: str = "edit", push: bool = False) -> None:
    if push:
        git(repo, "pull", "-q", "--ff-only")
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    if push:
        git(repo, "push", "-q", "origin", "main")


def set_store(store: Path, update: dict, push: bool = True) -> None:
    body = yaml.safe_load((store / "invariant.yaml").read_text(encoding="utf-8"))
    body.update(update)
    (store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    git(store, "add", "-A")
    git(store, "commit", "-qm", "settings")
    if push:
        git(store, "push", "-q", "origin", "main")


class Synced:
    widgets: Path
    store: Path
    remote: Path
    colleague: Path


@pytest.fixture
def synced(tmp_path, monkeypatch, capsys) -> Synced:
    """widgets linked to a store published to a bare remote, and a colleague's
    clone of that remote, all in step."""
    widgets = new_repo(tmp_path / "widgets", f"https://{WIDGETS}.git")
    monkeypatch.chdir(widgets)
    code, text = run(capsys, "store", "init")
    assert code == 0, text
    store = invariant_home() / "stores" / "widgets"
    identify(store)
    body = yaml.safe_load((store / "invariant.yaml").read_text(encoding="utf-8"))
    body["store"]["repos"][WIDGETS] = {"records": {"docs": "store"}}
    (store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    for rel in ("docs/architecture/overview.rst", "docs/architecture/cli.rst"):
        (store / rel).parent.mkdir(parents=True, exist_ok=True)
        (store / rel).write_text(f"{rel}\n", encoding="utf-8")
    git(store, "add", "-A")
    git(store, "commit", "-qm", "the store")
    git(store, "branch", "-M", "main")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(store, "remote", "add", "origin", str(remote))
    git(store, "push", "-q", "-u", "origin", "main")
    colleague = tmp_path / "colleague"
    git(tmp_path, "clone", "-q", str(remote), str(colleague))
    identify(colleague)
    t = Synced()
    t.widgets, t.store, t.remote, t.colleague = widgets, store, remote, colleague
    return t


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def sessions(repo: Path) -> list:
    return SessionDomain(Manifest.locate(repo)).list()


# -- the settings ------------------------------------------------------------


def test_a_store_project_resolves_the_ruled_defaults(synced):
    manifest = Manifest.locate(synced.widgets)
    assert manifest.get("sync") == {
        "pull_at_start": True,
        "push_after_landing": False,
        "offline": "warn",
        "diverged": "stop",
        "timeout_seconds": 20,
    }
    assert manifest.source_of("sync.pull_at_start") == "default"
    settings = storesync.settings(manifest)
    assert settings.pull_at_start and settings.offline == "warn"


def test_an_in_repo_project_carries_no_sync_block(tmp_path):
    repo = new_repo(tmp_path / "plain", f"https://{GADGETS}.git")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "invariant.yaml").write_text("version: 1\n", encoding="utf-8")
    manifest = Manifest.locate(repo)
    assert manifest.get("sync") is None
    assert not any(key.startswith("sync.") for key, _v, _l in manifest.iter_provenance())


def test_an_in_repo_project_setting_sync_is_refused(tmp_path):
    repo = new_repo(tmp_path / "plain", f"https://{GADGETS}.git")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "invariant.yaml").write_text(
        "version: 1\nsync: {offline: stop}\n", encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="sync is a store's"):
        Manifest.locate(repo)


def test_the_store_level_wins_over_a_user_default(synced, capsys):
    user_config_path().write_text(
        yaml.safe_dump(
            {
                **yaml.safe_load(user_config_path().read_text(encoding="utf-8")),
                "defaults": {"sync": {"offline": "stop", "timeout_seconds": 5}},
            }
        ),
        encoding="utf-8",
    )
    set_store(synced.store, {"sync": {"offline": "warn"}})
    manifest = Manifest.locate(synced.widgets)
    assert manifest.get("sync.offline") == "warn"
    assert manifest.source_of("sync.offline").startswith("store:")
    assert manifest.get("sync.timeout_seconds") == 5
    assert manifest.source_of("sync.timeout_seconds").startswith("user:")


@pytest.mark.parametrize(
    ("sync", "match"),
    [
        ({"offline": "ignore"}, "sync.offline"),
        ({"diverged": "merge"}, "sync.diverged"),
        ({"timeout_seconds": 0}, "sync.timeout_seconds"),
        ({"timeout_seconds": True}, "sync.timeout_seconds"),
        ({"pull_at_start": "yes"}, "sync.pull_at_start"),
        ({"pull": True}, "sync.pull"),
        ("on", "sync must be a mapping"),
    ],
)
def test_a_bad_setting_is_refused_at_load(synced, sync, match):
    set_store(synced.store, {"sync": sync}, push=False)
    with pytest.raises(ManifestError, match=match):
        Manifest.locate(synced.widgets)


# -- the four states at session start ---------------------------------------


def test_behind_fast_forwards_at_session_start(synced, capsys):
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    code, text = run(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 0, text
    assert text.splitlines()[0].startswith("session s0000001 (")
    assert "behind its remote by 1 commit; fast-forwarded" in text
    assert head(synced.store) == head(synced.colleague)
    assert sessions(synced.widgets)[0].store_pull == "current"


def test_ahead_carries_on(synced, capsys):
    edit(synced.store, "docs/architecture/cli.rst", "mine\n")
    before = head(synced.store)
    code, text = run(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 0, text
    assert "ahead of its remote by 1 unpublished commit; carrying on" in text
    assert head(synced.store) == before


def test_in_step_says_so(synced, capsys):
    code, data = run_json(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 0
    assert data["store_pull"]["state"] == "current"


def test_offline_warns_and_records_a_stale_pull(synced, capsys, tmp_path):
    git(synced.store, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    code, text = run(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 0, text
    assert "could not reach its remote" in text
    assert "carrying on with the local copy" in text
    assert sessions(synced.widgets)[0].store_pull == "stale"
    code, text = run(capsys, "session", "status", "--id", "s0000001")
    assert "store pull  stale" in text
    assert "invariant store pull" in text


def test_offline_stops_when_the_store_says_so(synced, capsys, tmp_path):
    set_store(synced.store, {"sync": {"offline": "stop"}})
    git(synced.store, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    code, text = run(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 2, text
    assert "sync.offline: stop" in text
    assert sessions(synced.widgets) == []


def test_diverged_stops_registering_nothing(synced, capsys):
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    edit(synced.store, "docs/architecture/overview.rst", "mine\n")
    before = head(synced.store)
    code, text = run(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 2, text
    assert "diverged" in text
    assert "invariant store pull --replay" in text
    assert "session s0000001 (" not in text
    assert sessions(synced.widgets) == []
    assert head(synced.store) == before


def test_pull_at_start_off_fetches_nothing(synced, capsys):
    set_store(synced.store, {"sync": {"pull_at_start": False}})
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    before = head(synced.store)
    code, data = run_json(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 0
    assert data["store_pull"] is None
    assert head(synced.store) == before


def test_a_store_with_no_remote_pulls_nothing(synced, capsys):
    git(synced.store, "remote", "remove", "origin")
    code, data = run_json(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 0
    assert data["store_pull"]["state"] == "no_remote"


def test_behind_over_an_uncommitted_change_it_would_move_stops(synced, capsys):
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    (synced.store / "docs/architecture/cli.rst").write_text("dirty\n", encoding="utf-8")
    before = head(synced.store)
    code, text = run(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 2, text
    assert "docs/architecture/cli.rst" in text
    assert "uncommitted" in text
    assert head(synced.store) == before
    assert sessions(synced.widgets) == []


def test_an_in_repo_project_starts_as_before(tmp_path, monkeypatch, capsys):
    repo = new_repo(tmp_path / "plain", f"https://{GADGETS}.git")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "invariant.yaml").write_text("version: 1\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    code, data = run_json(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 0
    assert data["store_pull"] is None
    assert "store_pull" not in data["session"] or data["session"]["store_pull"] is None


# -- resume and docs lock pull too ------------------------------------------


def test_resume_pulls_first_and_a_divergence_refuses_it(synced, capsys):
    code, text = run(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 0, text
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    code, text = run(capsys, "session", "resume", "s0000001", "--force")
    assert code == 0, text
    assert "fast-forwarded" in text
    assert head(synced.store) == head(synced.colleague)
    edit(synced.colleague, "docs/architecture/cli.rst", "again\n", push=True)
    edit(synced.store, "docs/architecture/overview.rst", "mine\n")
    code, text = run(capsys, "session", "resume", "s0000001", "--force")
    assert code == 2, text
    assert "diverged" in text
    assert "session s0000001 (" not in text


def test_docs_lock_pulls_first(synced, capsys, tmp_path):
    code, text = run(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 0, text
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    code, text = run(capsys, "docs", "lock", "docs/architecture/cli.rst", "--id", "s0000001")
    assert code == 0, text
    assert "fast-forwarded" in text
    assert "granted" in text
    git(synced.store, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    code, text = run(capsys, "docs", "lock", "docs/architecture/overview.rst", "--id", "s0000001")
    assert code == 0, text
    assert "could not reach its remote" in text
    assert sessions(synced.widgets)[0].store_pull == "stale"


def test_docs_lock_refuses_on_divergence_taking_nothing(synced, capsys):
    code, text = run(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 0, text
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    edit(synced.store, "docs/architecture/overview.rst", "mine\n")
    code, text = run(capsys, "docs", "lock", "docs/architecture/cli.rst", "--id", "s0000001")
    assert code == 2, text
    assert "diverged" in text
    code, data = run_json(capsys, "docs", "lock", "--id", "s0000001")
    assert data["held"] == []


# -- store pull and --replay -------------------------------------------------


def test_store_pull_clears_a_stale_record(synced, capsys, tmp_path):
    url = git(synced.store, "remote", "get-url", "origin")
    git(synced.store, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    code, text = run(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 0, text
    code, text = run(capsys, "store", "pull", "--id", "s0000001")
    assert code == 1, text
    assert "could not reach its remote" in text
    git(synced.store, "remote", "set-url", "origin", url)
    code, text = run(capsys, "store", "pull", "--id", "s0000001")
    assert code == 0, text
    assert "in step with its remote" in text
    assert sessions(synced.widgets)[0].store_pull == "current"


def test_store_pull_without_replay_names_it_on_divergence(synced, capsys):
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    edit(synced.store, "docs/architecture/overview.rst", "mine\n")
    code, text = run(capsys, "store", "pull")
    assert code == 1, text
    assert "invariant store pull --replay" in text


def test_replay_puts_unpublished_commits_on_top(synced, capsys):
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    edit(synced.store, "docs/architecture/overview.rst", "mine\n", message="mine")
    theirs = head(synced.colleague)
    code, text = run(capsys, "store", "pull", "--replay")
    assert code == 0, text
    assert "replayed 1 unpublished commit" in text
    assert git(synced.store, "rev-parse", "HEAD~1") == theirs
    assert git(synced.store, "log", "-1", "--format=%s") == "mine"
    assert (synced.store / "docs/architecture/cli.rst").read_text() == "theirs\n"
    assert (synced.store / "docs/architecture/overview.rst").read_text() == "mine\n"


def test_replay_stops_on_the_same_file_showing_both_diffs(synced, capsys):
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    edit(synced.store, "docs/architecture/cli.rst", "mine\n")
    before = head(synced.store)
    code, text = run(capsys, "store", "pull", "--replay")
    assert code == 1, text
    assert "changed on both sides" in text
    assert "docs/architecture/cli.rst" in text
    assert "+mine" in text and "+theirs" in text
    assert head(synced.store) == before
    code, data = run_json(capsys, "store", "pull", "--replay")
    assert data["state"] == "overlap"
    assert [o["path"] for o in data["overlaps"]] == ["docs/architecture/cli.rst"]


def test_replay_is_refused_while_a_unit_is_held(synced, capsys):
    code, text = run(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 0, text
    code, text = run(capsys, "docs", "lock", "docs/architecture/cli.rst", "--id", "s0000001")
    assert code == 0, text
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    edit(synced.store, "docs/architecture/overview.rst", "mine\n")
    before = head(synced.store)
    code, text = run(capsys, "store", "pull", "--replay")
    assert code == 1, text
    assert "file:docs/architecture/cli.rst" in text
    assert "s0000001" in text
    assert head(synced.store) == before


def test_replay_when_merely_behind_fast_forwards(synced, capsys):
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    code, text = run(capsys, "store", "pull", "--replay")
    assert code == 0, text
    assert "fast-forwarded" in text


def test_store_pull_outside_a_store_project_is_refused(tmp_path, monkeypatch, capsys):
    repo = new_repo(tmp_path / "plain", f"https://{GADGETS}.git")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "invariant.yaml").write_text("version: 1\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    code, text = run(capsys, "store", "pull")
    assert code == 2
    assert "in-repo" in text or "no store" in text


# -- the same-file check, one function ---------------------------------------


def test_the_same_file_check_names_only_files_changed_on_both_sides(synced):
    base = head(synced.store)
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    edit(synced.store, "docs/architecture/cli.rst", "mine\n")
    edit(synced.store, "docs/architecture/overview.rst", "only mine\n")
    git(synced.store, "fetch", "-q", "origin")
    overlaps = storesync.same_file_check(synced.store, base, "HEAD", "origin/main")
    assert [o.path for o in overlaps] == ["docs/architecture/cli.rst"]
    assert "+mine" in overlaps[0].ours and "+theirs" in overlaps[0].theirs
    assert storesync.same_file_check(synced.store, "nope", "HEAD", "origin/main") is None


# -- the first clone is explicit (ruling 18) ---------------------------------


def published_store(tmp_path: Path, origin: str = WIDGETS) -> Path:
    """A store published to a bare remote and on no machine's user level."""
    work = tmp_path / "publisher"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    identify(work)
    body = {
        "version": 1,
        "project": {"name": "widgets", "ecosystems": ["python", "node"]},
        "commands": {"test": "pytest -q", "node": {"test": "npm test"}},
        "methodology": {"extra_machines": ["lanes/review.yaml"]},
        "store": {
            "repos": {
                origin: {"commands": {"lint": "ruff check ."}},
                GADGETS: {},
            }
        },
    }
    (work / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "the store")
    remote = tmp_path / "published.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(work, "push", "-q", str(remote), "main")
    return remote


def pointed_repo(tmp_path: Path, store: str) -> Path:
    repo = new_repo(tmp_path / "widgets", f"https://{WIDGETS}.git")
    (repo / ".invariant.yaml").write_text(
        yaml.safe_dump({"store": store}), encoding="utf-8"
    )
    return repo


def test_a_pointer_to_an_absent_store_stops_session_start_with_the_clone_command(
    tmp_path, monkeypatch, capsys
):
    remote = published_store(tmp_path)
    url = f"file://{remote}"
    repo = pointed_repo(tmp_path, url)
    monkeypatch.chdir(repo)
    assert not user_config_path().exists()
    code, text = run(capsys, "session", "start", "work", "--id", "s0000001")
    assert code != 0
    assert f"invariant store clone {url}" in text
    assert not user_config_path().exists()


def test_a_pointer_to_an_absent_local_path_names_the_clone_command_too(
    tmp_path, monkeypatch, capsys
):
    repo = pointed_repo(tmp_path, str(tmp_path / "nowhere"))
    monkeypatch.chdir(repo)
    code, text = run(capsys, "session", "start", "work", "--id", "s0000001")
    assert code != 0
    assert "invariant store clone <remote> --path" in text


def test_store_clone_shows_the_declared_commands_and_registers_on_yes(
    tmp_path, monkeypatch, capsys
):
    remote = published_store(tmp_path)
    url = f"file://{remote}"
    repo = pointed_repo(tmp_path, url)
    monkeypatch.chdir(tmp_path)
    code, text = run(capsys, "store", "clone", url, "--yes")
    assert code == 0, text
    target = invariant_home() / "stores" / "published"
    assert "commands.test = pytest -q  (store:published)" in text
    assert "commands.node.test = npm test  (store:published)" in text
    assert f"commands.lint = ruff check .  (store:published#{WIDGETS})" in text
    assert "lanes/review.yaml" in text
    assert WIDGETS in text and GADGETS in text
    assert (target / "invariant.yaml").is_file()
    user = yaml.safe_load(user_config_path().read_text(encoding="utf-8"))
    assert set(user["repos"]) == {WIDGETS, GADGETS}
    assert not [p for p in (invariant_home() / "stores").iterdir() if p.name.startswith(".")]
    monkeypatch.chdir(repo)
    manifest = Manifest.locate(repo)
    assert manifest.store is not None and manifest.store.root == target.resolve()
    assert manifest.store.via == "pointer"
    code, text = run(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 0, text


def test_a_declined_clone_registers_nothing(tmp_path, monkeypatch, capsys):
    remote = published_store(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    code, text = run(capsys, "store", "clone", f"file://{remote}")
    assert code == 1, text
    assert "commands.test = pytest -q" in text
    assert "declined" in text
    assert not user_config_path().exists()
    stores = invariant_home() / "stores"
    assert not stores.exists() or list(stores.iterdir()) == []


def test_a_clone_answered_no_at_the_terminal_registers_nothing(
    tmp_path, monkeypatch, capsys
):
    remote = published_store(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("invariant.cli.main._stdin_is_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    code, text = run(capsys, "store", "clone", f"file://{remote}")
    assert code == 1, text
    assert not user_config_path().exists()


def test_a_clone_answered_yes_at_the_terminal_registers(tmp_path, monkeypatch, capsys):
    remote = published_store(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("invariant.cli.main._stdin_is_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    target = tmp_path / "here"
    code, text = run(capsys, "store", "clone", f"file://{remote}", "--path", str(target))
    assert code == 0, text
    user = yaml.safe_load(user_config_path().read_text(encoding="utf-8"))
    assert Path(user["repos"][WIDGETS]["store"]).expanduser().resolve() == target.resolve()


def test_a_clone_of_no_store_is_refused_and_cleaned_up(tmp_path, monkeypatch, capsys):
    work = tmp_path / "plain"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    identify(work)
    (work / "README").write_text("x\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "x")
    monkeypatch.chdir(tmp_path)
    code, text = run(capsys, "store", "clone", f"file://{work}", "--yes")
    assert code == 2, text
    assert "no store" in text
    stores = invariant_home() / "stores"
    assert not stores.exists() or list(stores.iterdir()) == []


def test_a_clone_that_cannot_reach_the_remote_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code, text = run(capsys, "store", "clone", f"file://{tmp_path}/gone.git", "--yes")
    assert code == 2, text
    assert "could not clone" in text
    assert not user_config_path().exists()


def test_a_clone_whose_repo_is_linked_elsewhere_is_refused(tmp_path, monkeypatch, capsys):
    remote = published_store(tmp_path)
    other = tmp_path / "other-store"
    other.mkdir()
    user_config_path().parent.mkdir(parents=True, exist_ok=True)
    user_config_path().write_text(
        yaml.safe_dump({"version": 1, "repos": {GADGETS: {"store": str(other)}}}),
        encoding="utf-8",
    )
    before = user_config_path().read_bytes()
    monkeypatch.chdir(tmp_path)
    code, text = run(capsys, "store", "clone", f"file://{remote}", "--yes")
    assert code == 2, text
    assert GADGETS in text and "already linked" in text
    assert user_config_path().read_bytes() == before
    assert not (invariant_home() / "stores" / "published").exists()


def test_a_clone_onto_a_taken_name_falls_back_or_is_refused(tmp_path, monkeypatch, capsys):
    remote = published_store(tmp_path)
    taken = invariant_home() / "stores" / "published"
    taken.mkdir(parents=True)
    (taken / "x").write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, text = run(capsys, "store", "clone", f"file://{remote}", "--yes")
    assert code == 2, text
    assert "--path" in text
    code, text = run(capsys, "store", "clone", f"file://{remote}", "--yes", "--path", str(taken))
    assert code == 2, text
    assert "not empty" in text


def test_the_clone_name_follows_store_naming():
    assert storesync.clone_name("https://host.example/team/specs.git", "repo") == "specs"
    assert (
        storesync.clone_name("https://host.example/team/specs.git", "url")
        == "host.example_team_specs"
    )
    assert storesync.clone_name("file:///srv/stores/specs.git", "url") == "specs"


# -- core/git's new writes ----------------------------------------------------


def test_fetch_reports_an_unreachable_remote(tmp_path):
    repo = new_repo(tmp_path / "r", str(tmp_path / "gone.git"))
    ok, said = gitq.fetch(repo, "origin", timeout=10)
    assert not ok and said


# -- the pull's other edges ---------------------------------------------------


def test_a_remote_with_no_copy_of_the_branch_carries_on(synced, tmp_path):
    empty = tmp_path / "empty.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(empty))
    git(synced.store, "remote", "add", "mirror", str(empty))
    git(synced.store, "config", "branch.main.remote", "mirror")
    out = storesync.pull(synced.store, storesync.SyncSettings())
    assert out.state == "ahead" and not out.stops
    assert out.lines() == ["store widgets: its remote has no copy of this branch yet; carrying on"]
    assert out.upstream == "mirror/main"


def test_a_store_on_a_detached_head_follows_origin(synced):
    git(synced.store, "checkout", "-q", "--detach")
    remote, upstream = storesync._tracking(synced.store)
    assert remote == "origin" and upstream is None


def test_a_remote_not_named_origin_is_followed(synced):
    git(synced.store, "remote", "rename", "origin", "hub")
    git(synced.store, "config", "--unset", "branch.main.remote")
    git(synced.store, "config", "--unset", "branch.main.merge")
    remote, upstream = storesync._tracking(synced.store)
    assert (remote, upstream) == ("hub", "refs/remotes/hub/main")


def test_replay_over_uncommitted_changes_is_blocked(synced):
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    edit(synced.store, "docs/architecture/overview.rst", "mine\n")
    (synced.store / "docs/architecture/overview.rst").write_text("dirty\n", encoding="utf-8")
    out = storesync.pull(synced.store, storesync.SyncSettings(), explicit=True, replay=True)
    assert out.state == "blocked" and out.stops
    assert out.paths == ["docs/architecture/overview.rst"]
    assert "uncommitted" in out.lines()[0]


def test_a_failed_replay_is_undone(synced, monkeypatch):
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    edit(synced.store, "docs/architecture/overview.rst", "mine\n")
    before = head(synced.store)
    monkeypatch.setattr(storesync.git, "rebase", lambda root, onto: (False, "stopped"))
    out = storesync.pull(synced.store, storesync.SyncSettings(), explicit=True, replay=True)
    assert out.state == "blocked" and "undone: stopped" in out.detail
    assert head(synced.store) == before


def test_a_failed_fast_forward_is_blocked(synced, monkeypatch):
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    monkeypatch.setattr(storesync.git, "fast_forward", lambda root, rev: (False, "refused"))
    out = storesync.pull(synced.store, storesync.SyncSettings())
    assert out.state == "blocked" and "refused" in out.detail


@pytest.mark.parametrize("broken", ["ahead_behind", "diff_names", "merge_base"])
def test_git_that_cannot_answer_blocks(synced, monkeypatch, broken):
    edit(synced.colleague, "docs/architecture/cli.rst", "theirs\n", push=True)
    edit(synced.store, "docs/architecture/overview.rst", "mine\n")
    monkeypatch.setattr(storesync.git, broken, lambda *a, **k: None)
    out = storesync.pull(synced.store, storesync.SyncSettings(), explicit=True, replay=True)
    assert out.state == "blocked" and out.stops
    assert "git could not" in out.detail


def test_offline_with_a_silent_git_says_no_answer(synced, monkeypatch):
    monkeypatch.setattr(storesync.git, "fetch", lambda root, remote, timeout: (False, ""))
    out = storesync.pull(synced.store, storesync.SyncSettings())
    assert out.state == "offline" and out.detail == "no answer"
    assert out.record == "stale"


def test_the_off_state_reads_as_such():
    out = storesync.pull(Path("/nonexistent"), storesync.SyncSettings(pull_at_start=False))
    assert out.state == "off" and out.record is None
    assert "pull_at_start: false" in out.lines()[0]


def test_the_lines_of_each_state(synced):
    pull = storesync.Pull
    assert "1 unpublished commit" in pull("ahead", "s", ahead=1).lines()[0]
    assert "2 commits" in pull("behind", "s", behind=2).lines()[0]
    assert "replayed 2 unpublished commits" in pull("replayed", "s", ahead=2, behind=1).lines()[0]
    assert pull("blocked", "s", detail="why", paths=["a"]).lines() == [
        "store s: not pulled: why",
        "  a",
    ]
    assert not pull("current", "s", before="a", after="a").moved
    assert pull("behind", "s", before="a", after="b").moved


def test_declared_reads_lanes_given_as_one_path_and_skips_empty_commands(tmp_path):
    root = tmp_path / "s"
    root.mkdir()
    (root / "invariant.yaml").write_text(
        yaml.safe_dump(
            {
                "commands": "not a mapping",
                "methodology": {"extra_machines": "lanes/one.yaml"},
                "store": {"repos": {WIDGETS: {"commands": {"test": None, "lint": "x"}}}},
            }
        ),
        encoding="utf-8",
    )
    found = storesync.declared(root, "s")
    assert found.commands == [("commands.lint", "x", f"store:s#{WIDGETS}")]
    assert found.lanes == [("lanes/one.yaml", "store:s")]


def test_the_clone_name_of_an_scp_remote():
    assert storesync.clone_name("git@host.example:team/specs.git", "repo") == "specs"
    assert storesync.clone_name("git@host.example:team/specs.git", "url") == "host.example_team_specs"


def test_the_git_writes_report_what_git_said(tmp_path):
    repo = new_repo(tmp_path / "r", str(tmp_path / "gone.git"))
    assert gitq.config_value(repo, "remote.origin.url") == str(tmp_path / "gone.git")
    assert gitq.config_value(repo, "no.such") is None
    assert gitq.remotes(repo) == ["origin"]
    assert gitq.ahead_behind(repo, "nope") is None
    moved, said = gitq.fast_forward(repo, "nope")
    assert not moved and said
    done, said = gitq.rebase(repo, "nope")
    assert not done and said
    cloned, said = gitq.clone(str(tmp_path / "gone.git"), tmp_path / "c", timeout=10)
    assert not cloned and said
    assert gitq.diff_text(repo, "nope", "HEAD", "conf.py") is None


def test_a_remote_that_does_not_answer_in_time_counts_as_unreachable(tmp_path, monkeypatch):
    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(gitq.subprocess, "run", slow)
    ok, said = gitq.fetch(tmp_path, "origin", timeout=1)
    assert not ok and "within 1s" in said
