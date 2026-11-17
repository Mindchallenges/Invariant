"""ADR-014 increment 7b: ``store migrate --to repo`` (D12, with ADR-020 D3).

The round trip ADR-020's increment 2 lists: a toy in-repo project, written by
``invariant init --host-install`` with its wiring committed, moves to a store
(``--to store``, increment 7a) and back (``--to repo``). ``config show``
resolves the same values, labelled ``store:`` and then ``repo:``; the records
come back with their history and no path rewritten; the wiring goes from
committed to local and back; ``git status`` is clean in both homes.

The move back runs as its own chore-lane session the command starts. Its
store leg lands at once: this repository's block and its records leave the
store, under the session's docs-lock grants, and the store's own level is
never changed. Its repository leg (the records' history, the materialised
``.claude/invariant.yaml`` and the committed wiring) is a commit on the
session's branch that lands at ``session merge``. Until then the main
checkout finds neither home and refuses, naming the session; the session's
worktree resolves. After the merge the local wiring and the user level's
link are removed.

Every refusal exits 2 with nothing written. Remotes are local bare
repositories; the home is the suite's scratch one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from test_adr_014_migrate import (
    DOC,
    TOY,
    commit_all,
    comparable,
    doc_log,
    git,
    land,
    migrate,
    new_repo,
    resolved,
    run,
    run_json,
    snapshot,
    walk,
)

from invariant.adapters import sphinx
from invariant.core import storemigrate
from invariant.core.errors import ManifestError, UsageError
from invariant.core.home import user_config_path
from invariant.core.manifest import Manifest
from invariant.domains.session import SessionDomain

AGENT = ".claude/agents/backlog-curator.md"


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    """Commits the command makes in the store and the worktree need an author."""
    for key, value in (
        ("GIT_AUTHOR_NAME", "T"),
        ("GIT_AUTHOR_EMAIL", "t@example.invalid"),
        ("GIT_COMMITTER_NAME", "T"),
        ("GIT_COMMITTER_EMAIL", "t@example.invalid"),
    ):
        monkeypatch.setenv(key, value)


def to_repo(capsys, *extra) -> tuple[int, dict]:
    return run_json(capsys, "store", "migrate", "--to", "repo", *extra)


def exclude_text(repo: Path) -> str:
    path = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    exclude = path / "info" / "exclude"
    return exclude.read_text() if exclude.is_file() else ""


def tracked(repo: Path, rel: str) -> bool:
    return bool(git(repo, "ls-files", "--", rel))


@pytest.fixture
def toy(tmp_path, monkeypatch, capsys) -> Path:
    """A toy in-repo project, with its wiring committed and a docs history."""
    repo = new_repo(tmp_path / "toy")
    monkeypatch.chdir(repo)
    code, text = run(capsys, "init", "--home", "repo", "--host-install")
    assert code == 0, text
    commit_all(repo, "invariant init")
    (repo / "invariant-docs/architecture/data.rst").write_text("Data\n====\n", encoding="utf-8")
    commit_all(repo, "docs: the data page")
    return repo


@pytest.fixture
def stored(toy, capsys) -> tuple[Path, Path]:
    """The toy moved to a store and landed: a store project wired locally."""
    code, out = migrate(capsys)
    assert code == 0, out
    land(capsys, out["session"]["id"])
    store = Path(out["store"])
    assert resolved(toy)["paths.docs"][1] == f"store:toy#{TOY}"
    return toy, store


def land_from(capsys, monkeypatch, worktree: Path, sid: str) -> str:
    """Land a ``--to repo`` session: walked in its worktree, since the main
    checkout finds neither home until the merge, and merged from the main
    checkout, which runs the merge while the move is under way."""
    main = Path(git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")).parent
    monkeypatch.chdir(worktree)
    walk(capsys, sid)
    monkeypatch.chdir(main)
    code, text = run(capsys, "session", "merge", "--id", sid)
    assert code == 0, text
    return text


# -- the proof: the round trip ------------------------------------------------


def test_the_round_trip_resolves_the_same_values_labelled_store_then_repo(
    toy, capsys, monkeypatch
):
    before = resolved(toy)
    assert before["paths.docs"][1] == "repo:.claude/invariant.yaml"
    files = sorted(git(toy, "ls-files", "invariant-docs").splitlines())
    contents = {rel: (toy / rel).read_bytes() for rel in files}
    history = doc_log(toy)

    code, out = migrate(capsys)
    assert code == 0, out
    land(capsys, out["session"]["id"])
    store = Path(out["store"])
    middle = resolved(toy)
    assert comparable(middle, store) == comparable(before, toy)
    for key in ("paths.docs", "project.name", "enforcement.profile"):
        assert middle[key][1].startswith("store:"), (key, middle[key])
    assert (toy / ".claude/settings.local.json").is_file()
    assert not tracked(toy, ".claude/settings.json")

    code, back = to_repo(capsys)
    assert code == 0, back
    sid, worktree = back["session"]["id"], Path(back["worktree"])
    assert back["to"] == "repo"
    assert back["wiring"] == {"hooks": True, "agents": True}
    assert back["from_store_level"] == []

    # The store leg landed at once: the block and the records left the store,
    # its own level unchanged, kept though it now serves no repository.
    body = yaml.safe_load((store / "invariant.yaml").read_text())
    assert TOY not in (body.get("store") or {}).get("repos", {})
    assert body["project"] == {"name": "toy"}
    assert not git(store, "ls-files", "invariant-docs")
    assert git(store, "status", "--porcelain") == ""
    assert back["serves_none"] is True
    domain = SessionDomain(Manifest.locate(worktree))
    session = domain.require(sid)
    assert session.migrate["to"] == "repo"
    assert session.landing["store"] == git(store, "rev-parse", "HEAD")
    assert session.landing_state == "docs_landed"

    # Mid-move the main checkout finds neither home, and names the session.
    with pytest.raises(ManifestError) as half:
        Manifest.locate(toy)
    assert sid in str(half.value) and "session merge" in str(half.value)
    assert str(worktree) in str(half.value)

    # The repository leg, on the session's branch: the records with their
    # history, the same bytes at the same paths, the materialised manifest
    # and the committed wiring; the worktree resolves it, labelled repo:.
    assert sorted(git(worktree, "ls-files", "invariant-docs").splitlines()) == files
    for rel, data in contents.items():
        assert (worktree / rel).read_bytes() == data
    assert set(history) <= set(doc_log(worktree))
    manifest = yaml.safe_load((worktree / ".claude/invariant.yaml").read_text())
    assert "records" not in manifest and "store" not in manifest and "sync" not in manifest
    assert manifest["version"] == 1
    assert resolved(worktree)["paths.docs"][1] == "repo:.claude/invariant.yaml"
    for rel in (".claude/settings.json", ".claude/hooks/invariant-pre-tool-use.sh",
                ".claude/agents/feature-architect.md"):
        assert tracked(worktree, rel), rel
    assert not (worktree / ".claude/settings.local.json").exists()
    assert git(worktree, "status", "--porcelain") == ""

    text = land_from(capsys, monkeypatch, worktree, sid)
    assert "complete" in text

    # After the merge: one home, the repository, with the same values.
    after = resolved(toy)
    assert comparable(after, toy) == comparable(before, toy)
    for key in ("paths.docs", "project.name", "enforcement.profile", "items.path"):
        assert after[key][1] == "repo:.claude/invariant.yaml", (key, after[key])
    # The wiring is committed again, and nothing local is left of it.
    assert tracked(toy, ".claude/settings.json")
    assert tracked(toy, ".claude/hooks/invariant-pre-tool-use.sh")
    assert tracked(toy, ".claude/agents/feature-architect.md")
    assert not (toy / ".claude/settings.local.json").exists()
    assert "/.claude/" not in exclude_text(toy)
    state = Manifest.locate(toy).state_root
    assert not (state / "host/claude_code/installed.json").exists()
    # The user level's link is gone; the marker too; both homes are clean.
    user = yaml.safe_load(user_config_path().read_text()) or {}
    assert TOY not in (user.get("repos") or {})
    assert storemigrate.read_marker(state) is None
    finished = SessionDomain(Manifest.locate(toy)).require(sid)
    assert finished.state == "merged" and finished.migrate["finished"] is not None
    assert git(toy, "status", "--porcelain") == ""
    assert git(store, "status", "--porcelain") == ""
    code, text = run(capsys, "config", "show", "--provenance")
    assert code == 0, text
    assert "repo:.claude/invariant.yaml" in text and "store:toy" not in text


def test_a_failed_repo_merge_is_half_landed_and_resume_finishes_it(
    stored, capsys, monkeypatch
):
    toy, _store = stored
    code, out = to_repo(capsys)
    assert code == 0, out
    sid, name, worktree = out["session"]["id"], out["session"]["name"], Path(out["worktree"])
    monkeypatch.chdir(worktree)
    walk(capsys, sid)
    monkeypatch.chdir(toy)
    # A file in the main checkout where the branch adds one stops git's merge.
    (toy / DOC).parent.mkdir(parents=True, exist_ok=True)
    (toy / DOC).write_text("in the way\n", encoding="utf-8")
    code, text = run(capsys, "session", "merge", "--id", sid)
    assert code == 2, text
    assert "half-landed" in text
    session = SessionDomain(Manifest.locate(worktree)).require(sid)
    assert session.landing_state == "half_landed"
    with pytest.raises(ManifestError) as half:
        Manifest.locate(toy)
    assert sid in str(half.value)

    (toy / DOC).unlink()
    code, text = run(capsys, "session", "resume", name, "--force")
    assert code == 0, text
    assert "finished the half-landed merge" in text
    assert resolved(toy)["paths.docs"][1] == "repo:.claude/invariant.yaml"
    assert storemigrate.read_marker(Manifest.locate(toy).state_root) is None
    assert git(toy, "status", "--porcelain") == ""


def test_an_edited_local_agent_is_committed_as_edited_and_named(stored, capsys, monkeypatch):
    toy, _store = stored
    agent = toy / AGENT
    edited = agent.read_text() + "\nHouse rule.\n"
    agent.write_text(edited, encoding="utf-8")
    code, out = to_repo(capsys)
    assert code == 0, out
    assert AGENT in out["edited"]
    worktree = Path(out["worktree"])
    assert (worktree / AGENT).read_text() == edited
    land_from(capsys, monkeypatch, worktree, out["session"]["id"])
    assert (toy / AGENT).read_text() == edited
    assert tracked(toy, AGENT)
    assert git(toy, "status", "--porcelain") == ""


def test_a_project_with_no_wiring_installed_gets_none(tmp_path, monkeypatch, capsys):
    repo = new_repo(tmp_path / "bare")
    monkeypatch.chdir(repo)
    code, text = run(capsys, "init", "--home", "repo")
    assert code == 0, text
    commit_all(repo, "invariant init")
    code, out = migrate(capsys)
    assert code == 0, out
    land(capsys, out["session"]["id"])
    code, back = to_repo(capsys)
    assert code == 0, back
    assert back["wiring"] == {"hooks": False, "agents": False}
    land_from(capsys, monkeypatch, Path(back["worktree"]), back["session"]["id"])
    assert not (repo / ".claude/settings.json").exists()
    assert not (repo / ".claude/hooks").exists()
    assert resolved(repo)["project.name"][1] == "repo:.claude/invariant.yaml"
    assert git(repo, "status", "--porcelain") == ""


def test_a_shared_store_keeps_its_other_repository_and_its_own_level(
    toy, tmp_path, capsys, monkeypatch
):
    store = tmp_path / "shared"
    store.mkdir()
    git(store, "init", "-q", "-b", "main")
    (store / "invariant.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "project": {"name": "shared"},
                "docs": {"diagrams": "mermaid"},
                "store": {"repos": {"host.example/team/other": {}}},
            }
        ),
        encoding="utf-8",
    )
    commit_all(store, "the shared store")
    code, out = migrate(capsys, "--store", str(store))
    assert code == 0, out
    land(capsys, out["session"]["id"])

    code, back = to_repo(capsys)
    assert code == 0, back
    assert back["serves_none"] is False
    body = yaml.safe_load((store / "invariant.yaml").read_text())
    assert set(body["store"]["repos"]) == {"host.example/team/other"}
    assert body["project"] == {"name": "shared"}
    assert back["from_store_level"] == ["docs.diagrams"]
    worktree = Path(back["worktree"])
    manifest = yaml.safe_load((worktree / ".claude/invariant.yaml").read_text())
    assert manifest["project"]["name"] == "toy"
    assert manifest["docs"]["diagrams"] == "mermaid"


def test_the_pointer_goes_with_the_move(tmp_path, monkeypatch, capsys):
    """A project that names its store through a committed pointer: the
    repository leg removes the pointer, which only names the store."""
    repo = new_repo(tmp_path / "pointed", "https://host.example/team/pointed.git")
    store = tmp_path / "pstore"
    store.mkdir()
    git(store, "init", "-q", "-b", "main")
    (store / "invariant.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "project": {"name": "pointed"},
                "store": {"repos": {"host.example/team/pointed": {}}},
            }
        ),
        encoding="utf-8",
    )
    commit_all(store, "the store")
    (repo / ".invariant.yaml").write_text(
        yaml.safe_dump({"version": 1, "store": str(store)}), encoding="utf-8"
    )
    commit_all(repo, "the pointer")
    monkeypatch.chdir(repo)
    code, out = to_repo(capsys)
    assert code == 0, out
    assert out["pointer"] == ".invariant.yaml"
    worktree = Path(out["worktree"])
    assert not (worktree / ".invariant.yaml").exists()
    assert (worktree / ".claude/invariant.yaml").is_file()
    assert git(worktree, "status", "--porcelain") == ""
    land_from(capsys, monkeypatch, worktree, out["session"]["id"])
    assert not (repo / ".invariant.yaml").exists()
    assert resolved(repo)["project.name"] == ("pointed", "repo:.claude/invariant.yaml")


def test_store_only_keys_are_left_out_and_named(stored, capsys):
    _toy, store = stored
    body = yaml.safe_load((store / "invariant.yaml").read_text())
    body["sync"] = {"pull_at_start": False}
    body["privacy"] = {"store": {"scan": False}}
    (store / "invariant.yaml").write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    commit_all(store, "store-only settings")
    code, out = to_repo(capsys)
    assert code == 0, out
    assert "sync.pull_at_start" in out["dropped"]
    assert "privacy.store.scan" in out["dropped"]
    manifest = yaml.safe_load((Path(out["worktree"]) / ".claude/invariant.yaml").read_text())
    assert "sync" not in manifest
    assert "store" not in (manifest.get("privacy") or {})


def test_a_failure_part_way_names_what_was_done(stored, capsys, monkeypatch):
    def broken(*_args, **_kwargs):
        raise UsageError("the history could not be laid down")

    monkeypatch.setattr(storemigrate, "transfer", broken)
    code, text = run(capsys, "store", "migrate", "--to", "repo")
    assert code == 2, text
    assert "stopped part way" in text and "nothing is rolled back" in text
    assert "registered the parked session" in text


def test_an_abandoned_move_back_clears_its_marker(stored, capsys, monkeypatch):
    _toy, _store = stored
    code, out = to_repo(capsys)
    assert code == 0, out
    sid = out["session"]["id"]
    monkeypatch.chdir(Path(out["worktree"]))
    state = Manifest.locate(Path(out["worktree"])).state_root
    assert storemigrate.read_marker(state)["session"] == sid
    code, text = run(
        capsys, "session", "transition", "abandoned", "--id", sid,
        "--force", "--reason", "test fixture",
    )
    assert code == 0, text
    assert storemigrate.read_marker(state) is None


# -- the refusals: exit 2, nothing written ------------------------------------


def store_snapshot(store: Path) -> dict:
    return {
        "head": git(store, "rev-parse", "HEAD"),
        "status": git(store, "status", "--porcelain"),
        "config": (store / "invariant.yaml").read_text(),
    }


def refused(capsys, repo: Path, store: Path | None = None, *extra) -> str:
    before = snapshot(repo)
    store_before = store_snapshot(store) if store is not None else None
    code, text = run(capsys, "store", "migrate", "--to", "repo", *extra)
    assert code == 2, text
    assert "nothing was written" in text, text
    assert snapshot(repo) == before
    if store is not None:
        assert store_snapshot(store) == store_before
    return text


def test_refused_in_an_in_repo_project(toy, capsys):
    text = refused(capsys, toy)
    assert "already in the repository" in text


def test_refused_outside_a_project(tmp_path, monkeypatch, capsys):
    repo = new_repo(tmp_path / "plain")
    monkeypatch.chdir(repo)
    text = refused(capsys, repo)
    assert "no project" in text


def test_refused_below_the_top_level(stored, monkeypatch, capsys):
    toy, store = stored
    (toy / "pkg").mkdir()
    monkeypatch.chdir(toy / "pkg")
    text = refused(capsys, toy, store)
    assert "top level" in text


def test_refused_from_a_linked_worktree(stored, tmp_path, monkeypatch, capsys):
    toy, store = stored
    worktree = tmp_path / "wt"
    git(toy, "worktree", "add", "-q", str(worktree), "-b", "elsewhere")
    monkeypatch.chdir(worktree)
    text = refused(capsys, toy, store)
    assert "main checkout" in text


def test_refused_on_a_detached_head(stored, capsys):
    toy, store = stored
    git(toy, "checkout", "-q", "--detach")
    text = refused(capsys, toy, store)
    assert "detached" in text


def test_refused_over_a_target_path_already_in_the_repository(stored, capsys):
    toy, store = stored
    (toy / "invariant-docs/architecture").mkdir(parents=True)
    (toy / "invariant-docs/architecture/overview.rst").write_text("mine\n", encoding="utf-8")
    text = refused(capsys, toy, store)
    assert "invariant-docs/architecture/overview.rst" in text and "never" in text


def test_refused_over_a_wiring_path_git_would_not_overwrite(stored, capsys):
    toy, store = stored
    (toy / ".claude/settings.json").write_text("{}\n", encoding="utf-8")
    text = refused(capsys, toy, store)
    assert ".claude/settings.json" in text


def test_refused_over_an_uncommitted_change_in_the_store(stored, capsys):
    toy, store = stored
    (store / DOC).write_text("changed\n", encoding="utf-8")
    text = refused(capsys, toy, store)
    assert DOC in text and "uncommitted" in text


def test_refused_while_the_store_file_holds_comments_or_changes(stored, capsys):
    toy, store = stored
    path = store / "invariant.yaml"
    original = path.read_text()
    path.write_text("# ours\n" + original, encoding="utf-8")
    commit_all(store, "a comment")
    text = refused(capsys, toy, store)
    assert "comment" in text
    path.write_text(original, encoding="utf-8")
    commit_all(store, "no comment")
    path.write_text(original + "\n", encoding="utf-8")
    text = refused(capsys, toy, store)
    assert "uncommitted" in text


def test_refused_while_the_store_is_behind_its_remote(stored, tmp_path, capsys):
    toy, store = stored
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(store, "remote", "add", "origin", str(remote))
    git(store, "push", "-q", "-u", "origin", "main")
    colleague = tmp_path / "colleague"
    git(tmp_path, "clone", "-q", str(remote), str(colleague))
    (colleague / "note.rst").write_text("theirs\n", encoding="utf-8")
    commit_all(colleague, "theirs")
    git(colleague, "push", "-q", "origin", "main")
    git(store, "fetch", "-q")
    text = refused(capsys, toy, store)
    assert "behind" in text and "invariant store pull" in text


def test_refused_while_another_session_holds_a_unit_it_would_write(
    stored, capsys, monkeypatch
):
    toy, store = stored
    code, text = run(capsys, "session", "start", "design", "--id", "w0000001")
    assert code == 0, text
    code, text = run(capsys, "docs", "lock", DOC, "--id", "w0000001")
    assert code == 0, text
    text = refused(capsys, toy, store)
    assert "w0000001" in text


def test_refused_while_another_repository_shares_a_record(stored, capsys):
    toy, store = stored
    body = yaml.safe_load((store / "invariant.yaml").read_text())
    mine = body["store"]["repos"][TOY]
    body["store"]["repos"]["host.example/team/twin"] = {
        "records": {"docs": "store"},
        "paths": {"docs": mine["paths"]["docs"]},
        "docs": {"needs": {"srcdir": "invariant-docs"}},
    }
    (store / "invariant.yaml").write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    commit_all(store, "a twin")
    text = refused(capsys, toy, store)
    assert "host.example/team/twin" in text and "invariant-docs" in text


def test_refused_under_a_root_conf_py_that_reads_the_docs_project(stored, capsys):
    toy, store = stored
    (toy / "conf.py").write_text("project = 'content'\nexclude_patterns = ['_build']\n",
                                 encoding="utf-8")
    commit_all(toy, "the content's conf.py")
    text = refused(capsys, toy, store)
    assert "conf.py" in text and "exclude_patterns" in text and "invariant-docs" in text


def test_accepted_under_a_root_conf_py_that_excludes_it(stored, capsys):
    toy, _store = stored
    (toy / "conf.py").write_text(
        "project = 'content'\nexclude_patterns = ['_build', 'invariant-docs']\n",
        encoding="utf-8",
    )
    commit_all(toy, "the content's conf.py")
    code, out = to_repo(capsys)
    assert code == 0, out


def test_refused_with_a_store_named(stored, tmp_path, capsys):
    toy, store = stored
    text = refused(capsys, toy, store, "--store", str(tmp_path / "x"))
    assert "--store" in text


def test_refused_under_a_root_conf_py_it_cannot_read(stored, capsys):
    toy, store = stored
    (toy / "conf.py").write_text("exclude_patterns = patterns()\n", encoding="utf-8")
    commit_all(toy, "a computed conf.py")
    text = refused(capsys, toy, store)
    assert "cannot be read" in text and "invariant-docs" in text


def test_refused_over_an_uncommitted_pointer(stored, capsys):
    toy, store = stored
    (toy / ".invariant.yaml").write_text("version: 1\n", encoding="utf-8")
    text = refused(capsys, toy, store)
    assert ".invariant.yaml" in text and "not committed" in text


def test_refused_in_a_project_its_pointer_alone_defines(tmp_path, monkeypatch, capsys):
    repo = new_repo(tmp_path / "pointer-only", "https://host.example/team/po.git")
    (repo / ".invariant.yaml").write_text("version: 1\n", encoding="utf-8")
    commit_all(repo, "a pointer naming no store")
    monkeypatch.chdir(repo)
    text = refused(capsys, repo)
    assert "names no store" in text


def test_refused_when_the_docs_project_is_a_content_folder(tmp_path, monkeypatch, capsys):
    repo = new_repo(tmp_path / "old", "https://host.example/team/old.git")
    store = tmp_path / "ostore"
    store.mkdir()
    git(store, "init", "-q", "-b", "main")
    (store / "invariant.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "project": {"name": "old"},
                "store": {
                    "repos": {
                        "host.example/team/old": {
                            "records": {"docs": "store"},
                            "paths": {"docs": "docs/architecture"},
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (store / "docs/architecture").mkdir(parents=True)
    (store / "docs/conf.py").write_text("project = 'old'\n", encoding="utf-8")
    (store / "docs/architecture/overview.rst").write_text("Overview\n", encoding="utf-8")
    commit_all(store, "old layout")
    monkeypatch.chdir(repo)
    code, text = run(capsys, "store", "link", str(store))
    assert code == 0, text
    text = refused(capsys, repo, store)
    assert "docs/" in text and "invariant-docs" in text


def test_a_records_subset_is_refused_in_either_direction(stored, capsys):
    toy, _store = stored
    for direction in ("repo", "store"):
        before = snapshot(toy)
        code, text = run(
            capsys, "store", "migrate", "--to", direction, "--records", "docs"
        )
        assert code == 2, text
        assert "--records" in text and "nothing was written" in text
        assert snapshot(toy) == before


# -- the pieces -------------------------------------------------------------------


def test_conf_excludes_reads_literal_patterns(tmp_path):
    conf = tmp_path / "conf.py"
    for body, folder, expected in (
        ("exclude_patterns = ['invariant-docs']\n", "invariant-docs", True),
        ("exclude_patterns = ['invariant-docs/**']\n", "invariant-docs", True),
        ("exclude_patterns = ['invariant-docs/']\n", "invariant-docs", True),
        ("exclude_patterns = ['invariant-*']\n", "invariant-docs", True),
        ("exclude_patterns = ['_build']\nexclude_patterns += ['invariant-docs']\n",
         "invariant-docs", True),
        ("exclude_patterns = ['_build']\n", "invariant-docs", False),
        ("project = 'x'\n", "invariant-docs", False),
        ("exclude_patterns = ['docs/*']\n", "design/arch", False),
        ("exclude_patterns = ['design']\n", "design/arch", True),
        ("exclude_patterns = make()\n", "invariant-docs", None),
        ("exclude_patterns = [\n", "invariant-docs", None),
    ):
        conf.write_text(body, encoding="utf-8")
        assert sphinx.conf_excludes(conf, folder) is expected, body
    assert sphinx.conf_excludes(tmp_path / "absent.py", "x") is None


def test_unlink_repo_removes_only_this_repositorys_entry(tmp_path):
    from invariant.core.stores import unlink_repo

    user = user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    store = (tmp_path / "s").resolve()
    user.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "defaults": {"enforcement": {"profile": "standard"}},
                "repos": {TOY: {"store": str(store)}, "other": {"store": str(store)}},
            }
        ),
        encoding="utf-8",
    )
    assert unlink_repo([TOY], store) == [TOY]
    raw = yaml.safe_load(user.read_text())
    assert raw["repos"] == {"other": {"store": str(store)}}
    assert raw["defaults"] == {"enforcement": {"profile": "standard"}}
    assert unlink_repo([TOY], store) == []
    assert unlink_repo(["other"], tmp_path / "elsewhere") == []
    assert unlink_repo(["other"], store) == ["other"]
    assert "repos" not in yaml.safe_load(user.read_text())


def test_the_marker_note_for_a_move_back_names_the_worktree(tmp_path):
    root = tmp_path / "state"
    storemigrate.write_marker(
        root, {"session": "m0000001", "to": "repo", "worktree": "/wt/m"}
    )
    note = storemigrate.marker_note(root)
    assert "m0000001" in note and "/wt/m" in note and "run here" in note


def test_the_move_back_start_is_reached_only_mid_move_back(tmp_path, monkeypatch):
    """``session merge`` and ``session resume`` read the project from the
    session's worktree only while a ``--to repo`` marker names one that exists
    and the checkout finds no home; in every other case they resolve as ever."""
    from invariant.cli.main import _move_back_start

    repo = new_repo(tmp_path / "main")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.chdir(repo)
    state = repo / ".git" / "invariant" / "state"
    assert _move_back_start() is None  # no marker at all

    storemigrate.write_marker(state, {"session": "m1", "to": "store", "worktree": str(worktree)})
    assert _move_back_start() is None  # a move into a store
    storemigrate.write_marker(state, {"session": "m1", "to": "repo"})
    assert _move_back_start() is None  # no worktree named
    storemigrate.write_marker(
        state, {"session": "m1", "to": "repo", "worktree": str(tmp_path / "gone")}
    )
    assert _move_back_start() is None  # the named worktree is gone
    storemigrate.write_marker(state, {"session": "m1", "to": "repo", "worktree": str(worktree)})
    assert _move_back_start() == worktree  # mid move back: the worktree

    (repo / ".claude").mkdir()
    (repo / ".claude/invariant.yaml").write_text("version: 1\n", encoding="utf-8")
    assert _move_back_start() is None  # the checkout resolves: the merge landed
