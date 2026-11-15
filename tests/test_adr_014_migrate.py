"""ADR-014 increment 7a: ``store migrate --to store`` (D12, with ADR-020 D3).

A toy in-repo project, written by ``invariant init --host-install`` with its
wiring committed, moves its configuration and every record into a store. The
move runs as its own chore-lane session the command starts: the store leg (the
repository's block and the records with their history) lands at once, under
the session's docs-lock grants, and the repo leg (the manifest, the records
and the committed wiring removed) is a commit on the session's branch that
lands at ``session merge``. Until the merge the main checkout keeps reading
``.claude/invariant.yaml``; after it the configuration resolves from the
store, labelled ``store:``, and the wiring is local and out of ``git status``.

Every refusal exits 2 with nothing written. Remotes are local bare
repositories or a URL nothing fetches; the home is the suite's scratch one.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from test_adr_014_per_leg_gates import gate
from test_adr_018_docs_build import Fake

from invariant.cli.main import main
from invariant.core import git as gitq
from invariant.core import needs, storemigrate
from invariant.core.errors import ManifestError, UsageError
from invariant.core.home import invariant_home, user_config_path
from invariant.core.identity import repo_identity
from invariant.core.manifest import Manifest
from invariant.domains.session import SessionDomain

TOY = "host.example/team/toy"
DOC = "invariant-docs/architecture/overview.rst"


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


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    """Commits the command makes in a store it creates need an author."""
    for key, value in (
        ("GIT_AUTHOR_NAME", "T"),
        ("GIT_AUTHOR_EMAIL", "t@example.invalid"),
        ("GIT_COMMITTER_NAME", "T"),
        ("GIT_COMMITTER_EMAIL", "t@example.invalid"),
    ):
        monkeypatch.setenv(key, value)


def new_repo(root: Path, origin: str | None = f"https://{TOY}.git") -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    (root / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "toy"\n', encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    if origin:
        git(root, "remote", "add", "origin", origin)
    return root


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)


@pytest.fixture
def toy(tmp_path, monkeypatch, capsys) -> Path:
    """A toy in-repo project: init with its wiring committed, then a docs
    history with a side branch merged into it."""
    repo = new_repo(tmp_path / "toy")
    monkeypatch.chdir(repo)
    code, text = run(capsys, "init", "--host-install")
    assert code == 0, text
    commit_all(repo, "invariant init")
    git(repo, "switch", "-qc", "side")
    (repo / DOC).write_text((repo / DOC).read_text() + "\nA side note.\n", encoding="utf-8")
    commit_all(repo, "docs: a side note")
    git(repo, "switch", "-q", "main")
    (repo / "app.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    commit_all(repo, "code: two")
    git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge side")
    (repo / "invariant-docs/architecture/data.rst").write_text("Data\n====\n", encoding="utf-8")
    commit_all(repo, "docs: the data page")
    return repo


def snapshot(repo: Path) -> dict:
    """Everything a refusal must leave as it was."""
    user = user_config_path()
    stores = invariant_home() / "stores"
    state = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    sessions = state / "invariant" / "state" / "sessions"
    return {
        "head": git(repo, "rev-parse", "HEAD"),
        "status": git(repo, "status", "--porcelain"),
        "branches": git(repo, "branch", "--list"),
        "user": user.read_text() if user.is_file() else None,
        "stores": sorted(p.name for p in stores.iterdir()) if stores.is_dir() else [],
        "sessions": sorted(p.name for p in sessions.iterdir()) if sessions.is_dir() else [],
        "marker": (state / "invariant" / "state" / storemigrate.MARKER).exists(),
        "local_id": git_config(repo, "invariant.id"),
    }


def git_config(repo: Path, key: str) -> str | None:
    proc = subprocess.run(
        ["git", "config", "--local", key], cwd=repo, capture_output=True, text=True,
        check=False,
    )
    return proc.stdout.strip() or None


def resolved(cwd: Path) -> dict[str, tuple[object, str]]:
    manifest = Manifest.locate(cwd)
    return {key: (value, source) for key, value, source in manifest.iter_provenance()}


def comparable(values: dict, root: Path) -> dict:
    """Values with ``records.*`` left out, and the ``sync`` defaults a store
    project alone layers (ADR-014 I5A), and a path joined onto ``root``
    spelled relative to it, so the two homes compare."""
    out = {}
    for key, (value, source) in values.items():
        if key.startswith("records.") or (key.startswith("sync.") and source == "default"):
            continue
        if isinstance(value, str) and value.startswith(str(root) + "/"):
            value = value[len(str(root)) + 1:]
        out[key] = value
    return out


def doc_log(repo: Path, rel: str = DOC) -> list[str]:
    return git(repo, "log", "--format=%s", "--", rel).splitlines()


def migrate(capsys, *extra) -> tuple[int, dict]:
    return run_json(capsys, "store", "migrate", "--to", "store", *extra)


def walk(capsys, sid: str) -> None:
    """Walk the session and its chore pipeline to the merge, as a review
    would; forced here, since this suite reviews nothing."""
    for state in ("working", "reviewed"):
        code, text = run(
            capsys, "session", "transition", state, "--id", sid,
            "--force", "--reason", "test fixture",
        )
        assert code == 0, text
    for state in ("reviewing", "committed"):
        code, text = run(
            capsys, "pipeline", "advance", state, "--id", sid,
            "--force", "--reason", "test fixture",
        )
        assert code == 0, text


def land(capsys, sid: str) -> str:
    walk(capsys, sid)
    code, text = run(capsys, "session", "merge", "--id", sid)
    assert code == 0, text
    return text


# -- the proof ----------------------------------------------------------------


def test_to_store_moves_everything_with_history_and_lands_at_merge(toy, capsys):
    before = resolved(toy)
    assert before["paths.docs"][1] == "repo:.claude/invariant.yaml"
    history = doc_log(toy)
    data_history = doc_log(toy, "invariant-docs/architecture/data.rst")
    files = sorted(git(toy, "ls-files", "invariant-docs").splitlines())

    code, out = migrate(capsys)
    assert code == 0, out
    store = Path(out["store"])
    assert store == (invariant_home() / "stores" / "toy").resolve()
    assert out["created"] is True
    sid = out["session"]["id"]

    # The store leg: the same relative paths, nothing rewritten, history kept.
    assert sorted(git(store, "ls-files", "invariant-docs").splitlines()) == files
    for rel in files:
        assert (store / rel).read_bytes() == (toy / rel).read_bytes()
    assert doc_log(store) == history
    assert "docs: a side note" in history
    assert doc_log(store, "invariant-docs/architecture/data.rst") == data_history
    assert git(store, "status", "--porcelain") == ""
    body = yaml.safe_load((store / "invariant.yaml").read_text())
    block = body["store"]["repos"][TOY]
    assert block["records"] == {
        "docs": "store", "backlog": "store", "guidelines": "store", "memory": "store",
        "reports": "store", "sweeps": "store", "hooks": "local", "agents": "local",
    }
    assert block["paths"]["docs"] == "invariant-docs/architecture"
    assert "version" not in block

    # The link is written at once; the in-repo manifest still wins here.
    user = yaml.safe_load(user_config_path().read_text())
    assert Path(user["repos"][TOY]["store"]).expanduser().resolve() == store
    assert resolved(toy)["paths.docs"][1] == "repo:.claude/invariant.yaml"

    # The session: parked, isolated, its docs landed, its migrate field and marker.
    domain = SessionDomain(Manifest.locate(toy))
    session = domain.require(sid)
    assert session.state == "isolated"
    assert session.host_session_id is None
    assert session.migrate["to"] == "store"
    assert session.migrate["wiring"] == {"hooks": True, "agents": True}
    assert session.landing["store"] == git(store, "rev-parse", "HEAD")
    assert session.landing_state == "docs_landed"
    assert storemigrate.read_marker(domain.manifest.state_root)["session"] == sid

    # The repo leg, on the session's branch: the manifest, the records and the
    # committed wiring removed, the code untouched.
    worktree = Path(session.worktree)
    assert not (worktree / ".claude/invariant.yaml").exists()
    assert not (worktree / "invariant-docs").exists()
    assert not (worktree / ".claude/settings.json").exists()
    assert not (worktree / ".claude/hooks").exists()
    assert not list((worktree / ".claude/agents").glob("*.md")) if (
        worktree / ".claude/agents"
    ).exists() else True
    assert (worktree / "app.py").read_text() == (toy / "app.py").read_text()
    assert git(worktree, "status", "--porcelain") == ""
    assert resolved(worktree)["paths.docs"][1] == f"store:toy#{TOY}"

    text = land(capsys, sid)
    assert "local" in text

    # After the merge: one home, the store, with the same values.
    assert not (toy / ".claude/invariant.yaml").exists()
    after = resolved(toy)
    assert comparable(after, store) == comparable(before, toy)
    for key in ("paths.docs", "project.name", "enforcement.profile", "items.path"):
        assert after[key][1].startswith("store:"), (key, after[key])
    # The wiring is local now: excluded, and git status is clean in both homes.
    assert (toy / ".claude/settings.local.json").is_file()
    assert (toy / ".claude/hooks/invariant-pre-tool-use.sh").is_file()
    assert (toy / ".claude/agents/feature-architect.md").is_file()
    assert not (toy / ".claude/settings.json").exists()
    exclude = (Path(git(toy, "rev-parse", "--git-common-dir")) / "info" / "exclude")
    if not exclude.is_absolute():
        exclude = toy / exclude
    assert "/.claude/settings.local.json" in exclude.read_text()
    assert git(toy, "status", "--porcelain") == ""
    assert git(store, "status", "--porcelain") == ""
    finished = SessionDomain(Manifest.locate(toy)).require(sid)
    assert finished.state == "merged"
    assert finished.migrate["finished"] is not None
    assert finished.landing["repo"] == git(toy, "rev-parse", "HEAD")
    assert storemigrate.read_marker(Manifest.locate(toy).state_root) is None
    code, text = run(capsys, "config", "show", "--provenance")
    assert code == 0, text
    assert f"store:toy#{TOY}" in text


def test_to_store_into_a_named_existing_store_keeps_its_other_repository(
    toy, tmp_path, capsys
):
    store = tmp_path / "shared"
    store.mkdir()
    git(store, "init", "-q", "-b", "main")
    (store / "invariant.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "project": {"name": "shared"},
                "store": {"repos": {"host.example/team/other": {}}},
            }
        ),
        encoding="utf-8",
    )
    (store / "notes").mkdir(exist_ok=True)
    (store / "notes/other.rst").write_text("other\n", encoding="utf-8")
    commit_all(store, "the shared store")
    head = git(store, "rev-parse", "HEAD")

    code, out = migrate(capsys, "--store", str(store))
    assert code == 0, out
    assert out["created"] is False
    body = yaml.safe_load((store / "invariant.yaml").read_text())
    assert set(body["store"]["repos"]) == {"host.example/team/other", TOY}
    assert body["project"] == {"name": "shared"}
    assert git(store, "status", "--porcelain") == ""
    assert gitq.is_ancestor(store, head, "HEAD")
    assert doc_log(store)[-1] == "invariant init"


def test_a_failed_repo_merge_is_recorded_and_resume_finishes_the_move(toy, capsys, monkeypatch):
    code, out = migrate(capsys)
    assert code == 0, out
    sid, name = out["session"]["id"], out["session"]["name"]
    walk(capsys, sid)
    # An uncommitted edit on a path the merge deletes stops git's merge.
    (toy / DOC).write_text("mine\n", encoding="utf-8")
    code, text = run(capsys, "session", "merge", "--id", sid)
    assert code == 2, text
    assert "half-landed" in text
    session = SessionDomain(Manifest.locate(toy)).require(sid)
    assert session.landing["failed"] is not None
    assert session.landing_state == "half_landed"
    assert (toy / ".claude/invariant.yaml").is_file()

    git(toy, "checkout", "--", DOC)
    code, text = run(capsys, "session", "resume", name, "--force")
    assert code == 0, text
    assert "finished the half-landed merge" in text
    assert not (toy / ".claude/invariant.yaml").exists()
    finished = SessionDomain(Manifest.locate(toy)).require(sid)
    assert finished.migrate["finished"] is not None
    assert git(toy, "status", "--porcelain") == ""


def test_a_project_with_no_wiring_installed_gets_none(tmp_path, monkeypatch, capsys):
    repo = new_repo(tmp_path / "bare")
    monkeypatch.chdir(repo)
    code, text = run(capsys, "init")
    assert code == 0, text
    commit_all(repo, "invariant init")
    code, out = migrate(capsys)
    assert code == 0, out
    assert out["wiring"] == {"hooks": False, "agents": False}
    land(capsys, out["session"]["id"])
    assert not (repo / ".claude/settings.local.json").exists()
    assert not (repo / ".claude/hooks").exists()
    assert git(repo, "status", "--porcelain") == ""


def test_an_edited_committed_agent_stays_and_is_named(toy, capsys):
    agent = toy / ".claude/agents/backlog-curator.md"
    agent.write_text(agent.read_text() + "\nHouse rule.\n", encoding="utf-8")
    commit_all(toy, "our curator")
    code, out = migrate(capsys)
    assert code == 0, out
    assert out["kept_agents"] == [".claude/agents/backlog-curator.md"]
    worktree = Path(out["worktree"])
    assert (worktree / ".claude/agents/backlog-curator.md").is_file()
    assert not (worktree / ".claude/agents/feature-architect.md").exists()


def test_a_record_with_an_absolute_path_stays_in_the_repository(toy, tmp_path, capsys):
    manifest = toy / ".claude/invariant.yaml"
    body = yaml.safe_load(manifest.read_text())
    body.setdefault("paths", {})["memory"] = str(tmp_path / "elsewhere")
    manifest.write_text(yaml.safe_dump(body), encoding="utf-8")
    commit_all(toy, "memory elsewhere")
    code, out = migrate(capsys)
    assert code == 0, out
    assert out["records"]["memory"] == "repo"
    assert "memory" in out["kept"]


# -- the refusals: exit 2, nothing written ------------------------------------


def refused(capsys, repo: Path, *extra) -> str:
    before = snapshot(repo)
    code, text = run(capsys, "store", "migrate", "--to", "store", *extra)
    assert code == 2, text
    assert "nothing was written" in text, text
    assert snapshot(repo) == before
    return text


def test_refused_below_the_top_level(toy, monkeypatch, capsys):
    (toy / "pkg").mkdir()
    monkeypatch.chdir(toy / "pkg")
    text = refused(capsys, toy)
    assert "top level" in text


def test_refused_outside_a_project(tmp_path, monkeypatch, capsys):
    repo = new_repo(tmp_path / "plain")
    monkeypatch.chdir(repo)
    text = refused(capsys, repo)
    assert "no project" in text


def test_refused_from_a_session_worktree(toy, tmp_path, monkeypatch, capsys):
    worktree = tmp_path / "wt"
    git(toy, "worktree", "add", "-q", str(worktree), "-b", "elsewhere")
    monkeypatch.chdir(worktree)
    text = refused(capsys, toy)
    assert "main checkout" in text


def test_refused_in_a_store_project(tmp_path, monkeypatch, capsys):
    repo = new_repo(tmp_path / "linked", "https://host.example/team/linked.git")
    monkeypatch.chdir(repo)
    code, text = run(capsys, "store", "init")
    assert code == 0, text
    text = refused(capsys, repo)
    assert "already in store linked" in text


def test_refused_with_the_manifest_changed_or_not_committed(toy, tmp_path, monkeypatch, capsys):
    manifest = toy / ".claude/invariant.yaml"
    manifest.write_text(manifest.read_text() + "\n# mine\n", encoding="utf-8")
    text = refused(capsys, toy)
    assert ".claude/invariant.yaml" in text and "uncommitted" in text

    fresh = new_repo(tmp_path / "fresh", "https://host.example/team/fresh.git")
    monkeypatch.chdir(fresh)
    code, out = run(capsys, "init")
    assert code == 0, out
    text = refused(capsys, fresh)
    assert "not committed" in text


def test_refused_over_an_uncommitted_change_on_a_path_it_moves(toy, capsys):
    (toy / "invariant-docs/index.rst").write_text("changed\n", encoding="utf-8")
    (toy / "invariant-docs/architecture/draft.rst").write_text("new\n", encoding="utf-8")
    text = refused(capsys, toy)
    assert "invariant-docs/index.rst" in text
    assert "invariant-docs/architecture/draft.rst" in text


def test_refused_over_an_uncommitted_change_to_the_committed_wiring(toy, capsys):
    settings = toy / ".claude/settings.json"
    settings.write_text(settings.read_text().replace("{", "{ ", 1), encoding="utf-8")
    text = refused(capsys, toy)
    assert ".claude/settings.json" in text


def test_refused_into_a_store_that_already_lists_the_repository(toy, tmp_path, capsys):
    store = tmp_path / "taken"
    store.mkdir()
    git(store, "init", "-q", "-b", "main")
    (store / "invariant.yaml").write_text(
        yaml.safe_dump({"version": 1, "store": {"repos": {TOY: {}}}}), encoding="utf-8"
    )
    commit_all(store, "taken")
    text = refused(capsys, toy, "--store", str(store))
    assert "already lists" in text and str(store) in text


def test_refused_when_the_default_store_already_lists_the_repository(toy, capsys):
    store = invariant_home() / "stores" / "toy"
    store.mkdir(parents=True)
    git(store, "init", "-q", "-b", "main")
    (store / "invariant.yaml").write_text(
        yaml.safe_dump({"version": 1, "store": {"repos": {TOY: {}}}}), encoding="utf-8"
    )
    commit_all(store, "taken")
    text = refused(capsys, toy)
    assert "already lists" in text


def test_refused_over_a_target_path_already_in_the_store(toy, tmp_path, capsys):
    store = tmp_path / "busy"
    store.mkdir()
    git(store, "init", "-q", "-b", "main")
    (store / "invariant.yaml").write_text(
        yaml.safe_dump({"version": 1, "store": {"repos": {"host.example/x/y": {}}}}),
        encoding="utf-8",
    )
    (store / "invariant-docs").mkdir()
    (store / "invariant-docs/index.rst").write_text("theirs\n", encoding="utf-8")
    commit_all(store, "busy")
    text = refused(capsys, toy, "--store", str(store))
    assert "invariant-docs/index.rst" in text and "never" in text


def test_refused_into_a_store_whose_file_holds_comments(toy, tmp_path, capsys):
    store = tmp_path / "commented"
    store.mkdir()
    git(store, "init", "-q", "-b", "main")
    (store / "invariant.yaml").write_text(
        "# ours\nversion: 1\nstore:\n  repos:\n    host.example/x/y: {}\n", encoding="utf-8"
    )
    commit_all(store, "commented")
    text = refused(capsys, toy, "--store", str(store))
    assert "comment" in text


def test_refused_into_a_path_that_is_no_store(toy, tmp_path, capsys):
    target = tmp_path / "junk"
    target.mkdir()
    (target / "file.txt").write_text("x\n", encoding="utf-8")
    text = refused(capsys, toy, "--store", str(target))
    assert "no store" in text


def test_refused_when_the_docs_project_is_a_content_folder(tmp_path, monkeypatch, capsys):
    repo = new_repo(tmp_path / "old", "https://host.example/team/old.git")
    (repo / ".claude").mkdir()
    (repo / ".claude/invariant.yaml").write_text(
        yaml.safe_dump({"version": 1, "project": {"name": "old"}}), encoding="utf-8"
    )
    (repo / "docs/architecture").mkdir(parents=True)
    (repo / "docs/conf.py").write_text("project = 'old'\n", encoding="utf-8")
    (repo / "docs/architecture/overview.rst").write_text("Overview\n", encoding="utf-8")
    commit_all(repo, "old layout")
    monkeypatch.chdir(repo)
    text = refused(capsys, repo)
    assert "docs" in text and "invariant-docs" in text


def test_refused_while_the_store_is_behind_its_remote(toy, tmp_path, capsys):
    store = tmp_path / "synced"
    store.mkdir()
    git(store, "init", "-q", "-b", "main")
    (store / "invariant.yaml").write_text(
        yaml.safe_dump({"version": 1, "store": {"repos": {"host.example/x/y": {}}}}),
        encoding="utf-8",
    )
    commit_all(store, "the store")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(store, "remote", "add", "origin", str(remote))
    git(store, "push", "-q", "-u", "origin", "main")
    colleague = tmp_path / "colleague"
    git(tmp_path, "clone", "-q", str(remote), str(colleague))
    (colleague / "note.rst").write_text("theirs\n", encoding="utf-8")
    commit_all(colleague, "theirs")
    git(colleague, "push", "-q", "origin", "main")
    text = refused(capsys, toy, "--store", str(store))
    assert "behind" in text and "invariant store pull" in text

    (store / "mine.rst").write_text("mine\n", encoding="utf-8")
    commit_all(store, "mine")
    text = refused(capsys, toy, "--store", str(store))
    assert "diverged" in text and "invariant store pull" in text


def test_refused_while_another_session_holds_a_unit_it_would_write(
    toy, tmp_path, monkeypatch, capsys
):
    widgets = new_repo(tmp_path / "widgets", "https://host.example/team/widgets.git")
    store = tmp_path / "locked"
    store.mkdir()
    git(store, "init", "-q", "-b", "main")
    (store / "invariant.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "store": {
                    "repos": {
                        "host.example/team/widgets": {
                            "records": {"docs": "store"},
                            "paths": {"docs": "invariant-docs/architecture"},
                        }
                    },
                    "docs_lock": {"areas": {"arch": ["invariant-docs/**"]}},
                },
            }
        ),
        encoding="utf-8",
    )
    commit_all(store, "the store")
    monkeypatch.chdir(widgets)
    code, text = run(capsys, "store", "link", str(store))
    assert code == 0, text
    code, text = run(capsys, "session", "start", "design", "--id", "w0000001")
    assert code == 0, text
    code, text = run(capsys, "docs", "lock", "arch", "--id", "w0000001")
    assert code == 0, text
    monkeypatch.chdir(toy)
    text = refused(capsys, toy, "--store", str(store))
    assert "area:arch" in text and "w0000001" in text


def test_to_repo_and_a_records_subset_are_not_built_here(toy, capsys):
    before = snapshot(toy)
    code, text = run(capsys, "store", "migrate", "--to", "repo")
    assert code == 2, text
    assert "7b" in text
    code, text = run(capsys, "store", "migrate", "--to", "store", "--records", "docs")
    assert code == 2, text
    assert "--to store moves everything" in text
    assert snapshot(toy) == before


# -- the stream and the transfer ----------------------------------------------


def test_the_stream_rewriter_honours_data_lengths():
    payload = b"reset refs/heads/main\nfrom :1\ncommit refs/heads/main\n"
    stream = (
        b"blob\nmark :1\ndata " + str(len(payload)).encode() + b"\n" + payload + b"\n"
        b"reset refs/heads/main\n"
        b"commit refs/heads/main\nmark :2\nauthor T <t@x> 1 +0000\n"
        b"committer T <t@x> 1 +0000\ndata 3\nmsg\nM 100644 :1 a.txt\n\n"
        b"reset refs/heads/other\nfrom :2\n\n"
        b"commit refs/heads/main\nmark :3\nauthor T <t@x> 2 +0000\n"
        b"committer T <t@x> 2 +0000\ndata 4\nmsg2\nfrom :2\n"
        b"M 100644 inline b.txt\ndata 6\nreset\n\n"
    )
    out = storemigrate.retarget(stream, "refs/invariant/migrate", "abc123")
    assert out.startswith(b"reset refs/invariant/migrate\nfrom abc123\n\n")
    assert payload in out
    assert b"data 6\nreset\n" in out
    assert out.count(b"commit refs/invariant/migrate\n") == 2
    assert b"commit refs/heads/main" not in out.replace(payload, b"")
    assert b"refs/heads/other" not in out
    rootless = storemigrate.retarget(stream, "refs/x", None)
    assert rootless.startswith(b"blob\n")


def test_the_stream_rewriter_refuses_a_truncated_stream():
    with pytest.raises(UsageError):
        storemigrate.retarget(b"blob\nmark :1\ndata 10\nshort", "refs/x", None)


def test_transfer_keeps_a_blob_that_begins_with_reset(tmp_path):
    source = new_repo(tmp_path / "src", None)
    (source / "keep").mkdir()
    (source / "keep/tricky.txt").write_text(
        "reset refs/heads/main\nfrom :1\n", encoding="utf-8"
    )
    commit_all(source, "tricky")
    (source / "keep/tricky.txt").write_text(
        "reset refs/heads/main\nfrom :1\nmore\n", encoding="utf-8"
    )
    (source / "outside.txt").write_text("not moved\n", encoding="utf-8")
    commit_all(source, "tricky again")
    target = new_repo(tmp_path / "dst", None)
    before = git(target, "rev-parse", "HEAD")
    count = storemigrate.transfer(source, "HEAD", ["keep"], target)
    assert count == 2
    assert (target / "keep/tricky.txt").read_text() == "reset refs/heads/main\nfrom :1\nmore\n"
    assert not (target / "outside.txt").exists()
    assert git(target, "log", "--format=%s", "--", "keep/tricky.txt").splitlines() == [
        "tricky again", "tricky",
    ]
    assert gitq.is_ancestor(target, before, "HEAD")
    assert "refs/invariant" not in git(target, "for-each-ref")
    assert storemigrate.transfer(source, "HEAD", [], target) == 0


def test_transfer_into_a_dirty_path_stops(tmp_path):
    source = new_repo(tmp_path / "src", None)
    (source / "keep").mkdir()
    (source / "keep/a.txt").write_text("a\n", encoding="utf-8")
    commit_all(source, "a")
    target = new_repo(tmp_path / "dst", None)
    (target / "keep").mkdir()
    (target / "keep/a.txt").write_text("in the way\n", encoding="utf-8")
    head = git(target, "rev-parse", "HEAD")
    with pytest.raises(UsageError):
        storemigrate.transfer(source, "HEAD", ["keep"], target)
    assert git(target, "rev-parse", "HEAD") == head
    assert "refs/invariant" not in git(target, "for-each-ref")


# -- the marker -----------------------------------------------------------------


def test_the_marker_names_the_session_where_a_store_does_not_list_the_repo(
    tmp_path, monkeypatch, capsys
):
    repo = new_repo(tmp_path / "half", "https://host.example/team/half.git")
    monkeypatch.chdir(repo)
    code, text = run(capsys, "store", "init")
    assert code == 0, text
    store = invariant_home() / "stores" / "half"
    body = yaml.safe_load((store / "invariant.yaml").read_text())
    body["store"]["repos"] = {}
    (store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    state = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    root = state / "invariant" / "state"
    with pytest.raises(ManifestError) as plain:
        Manifest.locate(repo)
    assert "store migrate" not in str(plain.value)
    storemigrate.write_marker(root, {"session": "m0000001", "to": "repo", "store": str(store)})
    with pytest.raises(ManifestError) as named:
        Manifest.locate(repo)
    assert "m0000001" in str(named.value)
    assert "invariant session resume" in str(named.value)
    storemigrate.clear_marker(root, "other")
    assert storemigrate.read_marker(root)["session"] == "m0000001"
    storemigrate.clear_marker(root, "m0000001")
    assert storemigrate.read_marker(root) is None
    (root / storemigrate.MARKER).write_text("{not json", encoding="utf-8")
    assert storemigrate.read_marker(root) is None


def test_an_abandoned_migration_clears_its_marker(toy, capsys):
    code, out = migrate(capsys)
    assert code == 0, out
    sid = out["session"]["id"]
    state = Manifest.locate(toy).state_root
    assert storemigrate.read_marker(state)["session"] == sid
    code, text = run(
        capsys, "session", "transition", "abandoned", "--id", sid,
        "--force", "--reason", "test fixture",
    )
    assert code == 0, text
    assert storemigrate.read_marker(state) is None


def test_the_identity_is_the_local_id_without_an_origin(tmp_path, monkeypatch, capsys):
    repo = new_repo(tmp_path / "solo", None)
    monkeypatch.chdir(repo)
    code, text = run(capsys, "init")
    assert code == 0, text
    commit_all(repo, "invariant init")
    code, out = migrate(capsys)
    assert code == 0, out
    identity = repo_identity(repo)
    assert identity is not None and identity.local_id
    body = yaml.safe_load((Path(out["store"]) / "invariant.yaml").read_text())
    assert identity.key in body["store"]["repos"]
    assert out["remote"] is False


# -- the edges ------------------------------------------------------------------


def test_to_store_into_an_absent_named_folder_creates_the_store_there(toy, tmp_path, capsys):
    target = tmp_path / "mine" / "store"
    (toy / "invariant-docs/_build").mkdir()
    (toy / "invariant-docs/_build/index.html").write_text("built\n", encoding="utf-8")
    code, out = migrate(capsys, "--store", str(target))
    assert code == 0, out
    assert out["created"] is True
    assert Path(out["store"]) == target.resolve()
    assert out["left"] == ["invariant-docs/_build/"]
    user = yaml.safe_load(user_config_path().read_text())
    assert Path(user["repos"][TOY]["store"]).expanduser().resolve() == target.resolve()


def test_refused_on_a_detached_head(toy, capsys):
    git(toy, "checkout", "-q", "--detach")
    text = refused(capsys, toy)
    assert "detached" in text


def test_refused_when_the_repository_is_linked_to_another_store(toy, tmp_path, capsys):
    elsewhere = tmp_path / "elsewhere"
    user = user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(
        yaml.safe_dump({"version": 1, "repos": {TOY: {"store": str(elsewhere)}}}),
        encoding="utf-8",
    )
    text = refused(capsys, toy)
    assert "already linked" in text


def test_refused_over_an_uncommitted_store_configuration(toy, tmp_path, capsys):
    store = tmp_path / "dirty"
    store.mkdir()
    git(store, "init", "-q", "-b", "main")
    body = {"version": 1, "store": {"repos": {"host.example/x/y": {}}}}
    (store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    commit_all(store, "dirty")
    body["project"] = {"name": "edited"}
    (store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    text = refused(capsys, toy, "--store", str(store))
    assert "uncommitted" in text


def test_the_output_names_no_override_for_the_docs_gates(toy, capsys):
    """docs.builds_strict and docs.needs_traceable read the store leg
    (BUG_123), so the output advises no recorded override for them, and never
    a forced merge."""
    code, text = run(capsys, "store", "migrate", "--to", "store")
    assert code == 0, text
    assert "docs.builds_strict" not in text and "docs.needs_traceable" not in text
    assert "--force" not in text
    assert "session merge --id" in text


def test_the_migrate_session_passes_the_docs_gates_once_built(toy, tmp_path, capsys, monkeypatch):
    """BUG_123 end to end: the migrate session's moved docs project is on its
    store leg, so both docs-project gates ask for a build; one strict build
    run from the session's worktree, recorded in the project's state root,
    passes them, with no override."""
    code, out = migrate(capsys)
    assert code == 0, out
    sid, worktree = out["session"]["id"], Path(out["worktree"])
    monkeypatch.chdir(worktree)
    srcdir = needs.srcdir_of(Manifest.locate(worktree), worktree)
    assert srcdir is not None and srcdir.is_dir()
    assert not srcdir.is_relative_to(worktree.resolve())
    rules = ("docs.builds_strict", "docs.needs_traceable")
    for rule in rules:
        verdict = gate(worktree, rule, sid)
        assert not verdict.ok and "no docs build recorded" in verdict.message, rule

    fake = Fake(srcdir, tmp_path / "fake")
    code, text = run(capsys, "docs", "build")
    assert code == 0, text
    assert fake.runs()
    for rule in rules:
        verdict = gate(worktree, rule, sid)
        assert verdict.ok and not verdict.skipped, (rule, verdict.message)


def test_a_failure_part_way_names_what_was_done(toy, capsys, monkeypatch):
    def broken(*_args, **_kwargs):
        raise UsageError("the history could not be laid down")

    monkeypatch.setattr(storemigrate, "transfer", broken)
    code, text = run(capsys, "store", "migrate", "--to", "store")
    assert code == 2, text
    assert "stopped part way" in text and "nothing is rolled back" in text
    assert "linked" in text


def test_docs_project_reads_the_srcdir_then_the_folder_above_paths_docs(tmp_path):
    class Fake:
        def __init__(self, root, values):
            self.root, self.values = root, values

        def get(self, key):
            return self.values.get(key)

    root = tmp_path / "r"
    root.mkdir()
    assert storemigrate.docs_project(Fake(root, {"paths.docs": "design/arch"})) == "design"
    assert storemigrate.docs_project(Fake(root, {"paths.docs": "arch"})) is None
    assert storemigrate.docs_project(Fake(root, {"paths.docs": "/abs/arch"})) is None
    outside = storemigrate.docs_project(
        Fake(root, {"paths.docs": "a/b", "docs.needs.srcdir": str(tmp_path / "x")})
    )
    assert outside == "a"


def test_finish_names_a_wiring_install_that_failed(tmp_path, monkeypatch):
    from invariant.cli import migrate as cli_migrate
    from invariant.core.errors import StateError

    class Sessions:
        def __init__(self):
            self.finished: list[str] = []

        def finish_migrate(self, sid):
            self.finished.append(sid)

    class Ctx:
        def __init__(self):
            self.sessions = Sessions()

    from types import SimpleNamespace

    session = SimpleNamespace(
        id="m0000001",
        migrate={"to": "store", "store": "/s/toy", "wiring": {"hooks": True}, "finished": None},
    )

    def broken(*_args, **_kwargs):
        raise StateError("no project here")

    monkeypatch.setattr("invariant.cli.main.Context", broken)
    ctx = Ctx()
    lines = cli_migrate.finish(ctx, session, tmp_path)
    assert any("was not installed" in line for line in lines)
    assert ctx.sessions.finished == ["m0000001"]

    done = SimpleNamespace(id="m0000002", migrate={"to": "store", "finished": 1.0})
    assert cli_migrate.finish(Ctx(), done, tmp_path) == []


def test_standing_reads_the_store_and_moves_nothing(tmp_path):
    from invariant.core import storesync

    store = new_repo(tmp_path / "s", None)
    sync = storesync.SyncSettings()
    assert storesync.standing(store, sync).state == "no_remote"
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(store, "remote", "add", "origin", str(remote))
    out = storesync.standing(store, sync)
    assert out.state == "ahead" and not out.stops
    git(store, "push", "-q", "-u", "origin", "main")
    assert storesync.standing(store, sync).state == "current"
    commit_all_empty = ["commit", "-q", "--allow-empty", "-m", "mine"]
    git(store, *commit_all_empty)
    assert storesync.standing(store, sync).state == "ahead"
    git(store, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    out = storesync.standing(store, sync)
    assert out.state == "offline" and not out.stops
    out = storesync.standing(store, storesync.SyncSettings(offline="stop"))
    assert out.stops


def test_the_git_readers_refuse_an_option_shaped_path(tmp_path):
    repo = new_repo(tmp_path / "r", None)
    assert gitq.files_under(repo, []) == []
    assert gitq.files_under(repo, ["-x"]) is None
    assert gitq.changed_under(repo, []) == []
    assert gitq.changed_under(repo, ["-x"]) is None
    assert gitq.fast_export(repo, "-x", ["a"]) is None
    assert gitq.delete_ref(repo, "HEAD") is False
    assert gitq.files_under(tmp_path, ["a"]) is None
    (repo / "app.py").write_text("moved\n", encoding="utf-8")
    git(repo, "mv", "app.py", "renamed.py")
    assert gitq.changed_under(repo, ["app.py", "renamed.py"]) == ["renamed.py"]
