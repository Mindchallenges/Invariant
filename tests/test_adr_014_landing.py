"""ADR-014 increment 5b: landing (D6) and a pulled command named (ruling 22).

The store's docs land when the session releases its docs-lock units: the
release fetches first, stops on 5a's same-file check showing both diffs and
keeping the units, and otherwise replays the span's commits on top of the
remote. The landing record says where each leg landed. A repo merge that
fails leaves the session half-landed, recorded, and `session merge` or
`session resume` finishes it, checking the store still holds what landed.
A session whose docs landed is held out of `abandoned` unless forced, and a
forced abandon names the store commits that stay. Session start names every
`commands.*` value a pull changed, old and new.

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
from invariant.core.errors import UsageError
from invariant.core.home import invariant_home
from invariant.core.manifest import Manifest
from invariant.domains.session import Session, SessionDomain

WIDGETS = "host.example/team/widgets"
CLI_DOC = "docs/architecture/cli.rst"
OVERVIEW = "docs/architecture/overview.rst"


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


def set_store(repo: Path, update: dict, push: bool = True) -> None:
    """Merge ``update`` into the store config at ``repo`` and commit it."""
    if push:
        git(repo, "pull", "-q", "--ff-only")
    body = yaml.safe_load((repo / "invariant.yaml").read_text(encoding="utf-8"))
    body.update(update)
    (repo / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "settings")
    if push:
        git(repo, "push", "-q", "origin", "main")


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
    for rel in (OVERVIEW, CLI_DOC, "docs/notes.rst"):
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


def record(repo: Path, sid: str = "s0000001") -> Session:
    return SessionDomain(Manifest.locate(repo)).require(sid)


def lease_record(store: Path) -> dict:
    path = store / ".git" / "invariant" / "locks" / "docs.json"
    return json.loads(path.read_text()) if path.exists() else {"units": {}}


def start(capsys, sid: str = "s0000001", name: str = "work") -> None:
    code, text = run(capsys, "session", "start", name, "--id", sid)
    assert code == 0, text


def lock_and_write(synced, capsys, rel: str = CLI_DOC, text: str = "mine\n",
                   sid: str = "s0000001") -> None:
    code, out = run(capsys, "docs", "lock", rel, "--id", sid)
    assert code == 0, out
    edit(synced.store, rel, text, message=f"docs: {rel}")


def isolate_with_change(synced, capsys, monkeypatch, sid: str = "s0000001") -> Path:
    """Isolate the session, commit app.py in its worktree, and walk it to
    ``reviewed``; returns the worktree."""
    code, text = run(capsys, "session", "isolate", "--id", sid, "--execute")
    assert code == 0, text
    worktree = synced.widgets.parent / f"widgets-wt-{sid}"
    (worktree / "app.py").write_text("x = 2\n", encoding="utf-8")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-qm", "the code")
    monkeypatch.chdir(synced.widgets)
    for state in ("working", "reviewed"):
        code, text = run(
            capsys, "session", "transition", state, "--id", sid,
            "--force", "--reason", "test fixture",
        )
        assert code == 0, text
    return worktree


# -- the release (D6 step 1) -------------------------------------------------


def test_the_release_replays_the_span_commits_on_top_of_a_moved_remote(synced, capsys):
    start(capsys)
    lock_and_write(synced, capsys)
    edit(synced.colleague, OVERVIEW, "theirs\n", push=True)
    theirs = head(synced.colleague)
    code, text = run(capsys, "docs", "unlock", "--id", "s0000001")
    assert code == 0, text
    assert "replayed 1 unpublished commit" in text
    assert "released  file:docs/architecture/cli.rst" in text
    assert git(synced.store, "rev-parse", "HEAD~1") == theirs
    assert git(synced.store, "log", "-1", "--format=%s") == f"docs: {CLI_DOC}"
    assert lease_record(synced.store)["units"] == {}
    session = record(synced.widgets)
    assert session.landing == {"store": head(synced.store), "repo": None, "failed": None}
    assert session.landing_state == "docs_landed"
    spans = session.legs[0]["lock_spans"]
    assert spans[0]["released_at_sha"] == head(synced.store)


def test_a_same_file_conflict_at_release_is_shown_and_the_units_kept(synced, capsys):
    start(capsys)
    lock_and_write(synced, capsys)
    edit(synced.colleague, CLI_DOC, "theirs\n", push=True)
    before = head(synced.store)
    code, text = run(capsys, "docs", "unlock", "--id", "s0000001")
    assert code == 2, text
    assert "changed on both sides" in text
    assert CLI_DOC in text
    assert "+mine" in text and "+theirs" in text
    assert "every unit is still held" in text
    assert head(synced.store) == before
    assert list(lease_record(synced.store)["units"]) == [f"file:{CLI_DOC}"]
    session = record(synced.widgets)
    assert session.landing_state is None
    assert not session.legs[0]["lock_spans"][0].get("released_at")


def test_a_release_with_nothing_written_fast_forwards_and_lands(synced, capsys):
    start(capsys)
    code, text = run(capsys, "docs", "lock", CLI_DOC, "--id", "s0000001")
    assert code == 0, text
    edit(synced.colleague, OVERVIEW, "theirs\n", push=True)
    code, text = run(capsys, "docs", "unlock", "--id", "s0000001")
    assert code == 0, text
    assert "fast-forwarded" in text
    assert head(synced.store) == head(synced.colleague)
    assert record(synced.widgets).landing["store"] == head(synced.store)


def test_an_offline_release_warns_and_releases(synced, capsys, tmp_path):
    start(capsys)
    lock_and_write(synced, capsys)
    git(synced.store, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    code, text = run(capsys, "docs", "unlock", "--id", "s0000001")
    assert code == 0, text
    assert "could not reach its remote" in text
    session = record(synced.widgets)
    assert session.store_pull == "stale"
    assert session.landing["store"] == head(synced.store)


def test_an_offline_release_stops_when_the_store_says_so(synced, capsys, tmp_path):
    set_store(synced.store, {"sync": {"offline": "stop"}})
    start(capsys)
    lock_and_write(synced, capsys)
    git(synced.store, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    code, text = run(capsys, "docs", "unlock", "--id", "s0000001")
    assert code == 2, text
    assert "sync.offline: stop" in text
    assert list(lease_record(synced.store)["units"]) == [f"file:{CLI_DOC}"]


def test_a_release_with_pull_off_fetches_nothing(synced, capsys):
    set_store(synced.store, {"sync": {"pull_at_start": False}})
    start(capsys)
    lock_and_write(synced, capsys)
    edit(synced.colleague, OVERVIEW, "theirs\n", push=True)
    before = head(synced.store)
    code, data = run_json(capsys, "docs", "unlock", "--id", "s0000001")
    assert code == 0
    assert data["store_pull"] is None
    assert head(synced.store) == before
    assert data["landing"]["store"] == before


def test_a_release_in_a_store_with_no_remote_lands_as_before(synced, capsys):
    git(synced.store, "remote", "remove", "origin")
    start(capsys)
    lock_and_write(synced, capsys)
    code, data = run_json(capsys, "docs", "unlock", "--id", "s0000001")
    assert code == 0
    assert data["store_pull"]["state"] == "no_remote"
    assert data["released"] == [f"file:{CLI_DOC}"]
    assert data["landing"]["store"] == head(synced.store)


def test_a_dirty_unit_is_refused_before_anything_is_fetched(synced, capsys):
    start(capsys)
    code, text = run(capsys, "docs", "lock", CLI_DOC, "--id", "s0000001")
    assert code == 0, text
    edit(synced.colleague, OVERVIEW, "theirs\n", push=True)
    (synced.store / CLI_DOC).write_text("half written\n", encoding="utf-8")
    before = head(synced.store)
    code, text = run(capsys, "docs", "unlock", "--id", "s0000001")
    assert code == 2, text
    assert "uncommitted changes" in text
    assert head(synced.store) == before
    assert list(lease_record(synced.store)["units"]) == [f"file:{CLI_DOC}"]


def test_the_release_replays_over_a_unit_another_session_holds(synced, capsys):
    start(capsys)
    start(capsys, "s0000002", "other")
    code, text = run(capsys, "docs", "lock", "docs/notes.rst", "--id", "s0000002")
    assert code == 0, text
    lock_and_write(synced, capsys)
    edit(synced.colleague, OVERVIEW, "theirs\n", push=True)
    code, text = run(capsys, "docs", "unlock", "--id", "s0000001")
    assert code == 0, text
    assert "replayed" in text
    assert list(lease_record(synced.store)["units"]) == ["file:docs/notes.rst"]


def test_a_replay_leaves_another_holders_rewritten_span_readable(synced, capsys):
    """Decision 1's cost: the replay rewrites another holder's unpublished
    commits, its span's start among them. The old shas stay in the object
    store, so its span still reads, its store leg still names only its own
    unit, its base still answers, and its own release lands its own commit."""
    from invariant.core.rules import RuleContext, _changed_set, _store_base

    notes = "docs/notes.rst"
    start(capsys)
    start(capsys, "s0000002", "other")
    lock_and_write(synced, capsys)
    mine = head(synced.store)  # unpublished: s0000002's span starts on it
    lock_and_write(synced, capsys, rel=notes, text="other's\n", sid="s0000002")
    edit(synced.colleague, OVERVIEW, "theirs\n", push=True)

    code, text = run(capsys, "docs", "unlock", "--id", "s0000001")
    assert code == 0, text
    assert "replayed 2 unpublished commits" in text
    assert gitq.is_ancestor(synced.store, mine, "HEAD") is False  # rewritten

    manifest = Manifest.locate(synced.widgets)
    other = SessionDomain(manifest).require("s0000002")
    span = other.legs[0]["lock_spans"][0]
    assert span["acquired_at_sha"] == mine
    assert gitq.exists_at(synced.store, mine, notes) is True
    assert gitq.blob_at(synced.store, mine, notes) == f"{notes}\n".encode()

    ctx = RuleContext(manifest=manifest, cwd=synced.widgets, session=other.to_dict())
    leg = _changed_set(ctx, "store")
    assert leg.answerable
    assert {p.as_posix().removeprefix(f"{synced.store.as_posix()}/") for p in leg.paths} == {
        notes
    }
    base = _store_base(ctx)
    assert base.point == mine
    assert gitq.blob_at(synced.store, base.point, "invariant.yaml") is not None

    code, text = run(capsys, "docs", "unlock", "--id", "s0000002")
    assert code == 0, text
    other = record(synced.widgets, "s0000002")
    assert other.legs[0]["lock_spans"][0]["changed"] == [notes]
    assert other.landing_state == "docs_landed"
    landed = SessionDomain(manifest).landed_store_commits(other)
    assert [line[41:] for line in landed] == [f"docs: {notes}"]
    assert landed[0][:40] == head(synced.store)


def test_a_release_stops_on_another_holders_uncommitted_edit(synced, capsys):
    """The replay never rewrites under a held unit's work in progress: an
    uncommitted edit in the store's main checkout stops the release."""
    start(capsys)
    start(capsys, "s0000002", "other")
    code, text = run(capsys, "docs", "lock", "docs/notes.rst", "--id", "s0000002")
    assert code == 0, text
    lock_and_write(synced, capsys)
    (synced.store / "docs/notes.rst").write_text("half written\n", encoding="utf-8")
    edit(synced.colleague, OVERVIEW, "theirs\n", push=True)
    before = head(synced.store)
    code, text = run(capsys, "docs", "unlock", "--id", "s0000001")
    assert code == 2, text
    assert "uncommitted changes" in text
    assert head(synced.store) == before
    assert f"file:{CLI_DOC}" in lease_record(synced.store)["units"]


def test_releasing_only_a_queued_want_pulls_nothing(synced, capsys):
    start(capsys)
    start(capsys, "s0000002", "other")
    code, text = run(capsys, "docs", "lock", CLI_DOC, "--id", "s0000002")
    assert code == 0, text
    code, text = run(capsys, "docs", "lock", CLI_DOC, "--id", "s0000001")
    assert code == 0, text
    edit(synced.colleague, OVERVIEW, "theirs\n", push=True)
    before = head(synced.store)
    code, data = run_json(capsys, "docs", "unlock", CLI_DOC, "--id", "s0000001")
    assert code == 0
    assert data["store_pull"] is None
    assert head(synced.store) == before
    assert record(synced.widgets).landing is None


def test_taking_a_unit_again_clears_the_landed_store(synced, capsys):
    start(capsys)
    lock_and_write(synced, capsys)
    code, text = run(capsys, "docs", "unlock", "--id", "s0000001")
    assert code == 0, text
    assert record(synced.widgets).landing_state == "docs_landed"
    code, text = run(capsys, "docs", "lock", OVERVIEW, "--id", "s0000001")
    assert code == 0, text
    session = record(synced.widgets)
    assert session.landing["store"] is None
    assert session.landing_state is None


def test_releasing_one_of_two_units_does_not_land_yet(synced, capsys):
    start(capsys)
    code, text = run(capsys, "docs", "lock", CLI_DOC, OVERVIEW, "--id", "s0000001")
    assert code == 0, text
    code, text = run(capsys, "docs", "unlock", CLI_DOC, "--id", "s0000001")
    assert code == 0, text
    assert record(synced.widgets).landing_state is None
    code, text = run(capsys, "docs", "unlock", "--id", "s0000001")
    assert code == 0, text
    assert record(synced.widgets).landing_state == "docs_landed"


# -- session status ------------------------------------------------------------


def test_status_says_docs_landed_code_pending(synced, capsys):
    start(capsys)
    lock_and_write(synced, capsys)
    run(capsys, "docs", "unlock", "--id", "s0000001")
    code, text = run(capsys, "session", "status", "--id", "s0000001")
    assert code == 0, text
    assert "docs landed, code pending" in text
    assert head(synced.store)[:12] in text


def test_an_in_repo_session_shows_no_landing(tmp_path, monkeypatch, capsys):
    repo = new_repo(tmp_path / "plain", "https://host.example/team/plain.git")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "invariant.yaml").write_text("version: 1\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    start(capsys)
    code, text = run(capsys, "session", "status", "--id", "s0000001")
    assert code == 0, text
    assert "landed" not in text
    assert record(repo).landing is None


# -- the repo leg, and a half-landed session finished (D6 steps 2 and 3) -------


def docs_landed(synced, capsys, monkeypatch) -> Path:
    start(capsys)
    lock_and_write(synced, capsys)
    code, text = run(capsys, "docs", "unlock", "--id", "s0000001")
    assert code == 0, text
    return isolate_with_change(synced, capsys, monkeypatch)


def fail_the_merge(synced, capsys) -> str:
    """A repo merge forced to fail: an untracked file in the way of the merge."""
    (synced.widgets / "app.py").write_text("in the way\n", encoding="utf-8")
    code, text = run(capsys, "session", "merge", "--id", "s0000001")
    assert code != 0, text
    return text


def test_a_merge_records_both_legs(synced, capsys, monkeypatch):
    docs_landed(synced, capsys, monkeypatch)
    store_sha = head(synced.store)
    code, text = run(capsys, "session", "merge", "--id", "s0000001")
    assert code == 0, text
    session = record(synced.widgets)
    assert session.state == "merged"
    assert session.landing == {"store": store_sha, "repo": head(synced.widgets), "failed": None}
    assert session.landing_state == "landed"


def test_a_failed_repo_merge_leaves_the_session_half_landed(synced, capsys, monkeypatch):
    docs_landed(synced, capsys, monkeypatch)
    base = head(synced.widgets)
    text = fail_the_merge(synced, capsys)
    assert "half-landed" in text
    assert "invariant session resume" in text
    session = record(synced.widgets)
    assert session.state == "reviewed"
    assert session.landing_state == "half_landed"
    assert session.landing["repo"] is None
    failed = session.landing["failed"]
    assert failed["head"] == head(synced.widgets.parent / "widgets-wt-s0000001")
    assert failed["base"] == "main"
    assert "app.py" in failed["detail"]
    assert head(synced.widgets) == base
    code, text = run(capsys, "session", "status", "--id", "s0000001")
    assert code == 0, text
    assert "half-landed" in text
    assert head(synced.store)[:12] in text
    assert "app.py" in text


def test_a_forced_repo_leg_failure_is_finished_by_resume(synced, capsys, monkeypatch):
    worktree = docs_landed(synced, capsys, monkeypatch)
    fail_the_merge(synced, capsys)
    (synced.widgets / "app.py").unlink()
    code, text = run(capsys, "session", "resume", "s0000001")
    assert code == 0, text
    assert text.splitlines()[0].startswith("session s0000001 (")
    assert "finished the half-landed merge" in text
    session = record(synced.widgets)
    assert session.state == "merged"
    assert session.landing["repo"] == head(synced.widgets)
    assert session.landing["failed"] is None
    assert gitq.branch_is_merged_into(synced.widgets, "session/s0000001", "main") or (
        head(synced.widgets) == head(worktree)
    )


def test_session_merge_finishes_a_half_landed_session_too(synced, capsys, monkeypatch):
    docs_landed(synced, capsys, monkeypatch)
    fail_the_merge(synced, capsys)
    (synced.widgets / "app.py").unlink()
    code, text = run(capsys, "session", "merge", "--id", "s0000001")
    assert code == 0, text
    assert record(synced.widgets).landing_state == "landed"


def test_a_finish_that_fails_again_keeps_the_record_and_exits_1(synced, capsys, monkeypatch):
    docs_landed(synced, capsys, monkeypatch)
    fail_the_merge(synced, capsys)
    code, text = run(capsys, "session", "resume", "s0000001")
    assert code == 1, text
    assert text.splitlines()[0].startswith("session s0000001 (")
    assert "still half-landed" in text
    session = record(synced.widgets)
    assert session.landing_state == "half_landed"
    assert session.resumed_count == 1


def test_a_finish_stops_when_the_store_no_longer_holds_the_landed_docs(
    synced, capsys, monkeypatch
):
    docs_landed(synced, capsys, monkeypatch)
    fail_the_merge(synced, capsys)
    (synced.widgets / "app.py").unlink()
    landed = record(synced.widgets).landing["store"]
    git(synced.store, "commit", "-q", "--amend", "-m", "rewritten")
    rewritten = head(synced.store)
    code, text = run(capsys, "session", "merge", "--id", "s0000001")
    assert code == 2, text
    assert landed in text and rewritten in text
    assert record(synced.widgets).landing_state == "half_landed"
    code, text = run(capsys, "session", "resume", "s0000001")
    assert code == 1, text
    assert landed in text and "session merge --force" in text
    code, text = run(capsys, "session", "merge", "--id", "s0000001", "--force")
    assert code == 0, text
    session = record(synced.widgets)
    assert session.landing_state == "landed"
    assert any(o.get("rule") == "session.landing" for o in session.overrides)


def test_a_branch_merged_by_hand_is_recorded_without_merging_again(
    synced, capsys, monkeypatch
):
    worktree = docs_landed(synced, capsys, monkeypatch)
    fail_the_merge(synced, capsys)
    (synced.widgets / "app.py").unlink()
    git(synced.widgets, "merge", "-q", "--ff-only", "session/s0000001")
    code, text = run(capsys, "session", "resume", "s0000001")
    assert code == 0, text
    assert "already merged" in text
    session = record(synced.widgets)
    assert session.state == "merged"
    assert session.landing["repo"] == head(worktree)


def test_a_merge_with_units_still_held_lands_the_store_first(synced, capsys, monkeypatch):
    start(capsys)
    lock_and_write(synced, capsys)
    isolate_with_change(synced, capsys, monkeypatch)
    edit(synced.colleague, OVERVIEW, "theirs\n", push=True)
    code, text = run(capsys, "session", "merge", "--id", "s0000001")
    assert code == 0, text
    assert "replayed" in text
    assert lease_record(synced.store)["units"] == {}
    session = record(synced.widgets)
    assert session.landing["store"] == head(synced.store)
    assert session.landing_state == "landed"


def test_a_store_conflict_at_merge_merges_nothing(synced, capsys, monkeypatch):
    start(capsys)
    lock_and_write(synced, capsys)
    isolate_with_change(synced, capsys, monkeypatch)
    edit(synced.colleague, CLI_DOC, "theirs\n", push=True)
    base = head(synced.widgets)
    code, text = run(capsys, "session", "merge", "--id", "s0000001")
    assert code == 2, text
    assert "changed on both sides" in text
    assert "nothing was merged" in text
    assert head(synced.widgets) == base
    assert list(lease_record(synced.store)["units"]) == [f"file:{CLI_DOC}"]
    assert record(synced.widgets).state == "reviewed"


def test_a_store_session_that_never_locked_merges_with_no_store_landing(
    synced, capsys, monkeypatch
):
    start(capsys)
    isolate_with_change(synced, capsys, monkeypatch)
    code, text = run(capsys, "session", "merge", "--id", "s0000001")
    assert code == 0, text
    session = record(synced.widgets)
    assert session.landing == {"store": None, "repo": head(synced.widgets), "failed": None}


def test_a_failed_merge_with_no_docs_landed_is_recorded_not_half_landed(
    synced, capsys, monkeypatch
):
    start(capsys)
    isolate_with_change(synced, capsys, monkeypatch)
    text = fail_the_merge(synced, capsys)
    assert "half-landed" not in text
    session = record(synced.widgets)
    assert session.landing["failed"]["detail"]
    assert session.landing_state is None
    code, text = run(capsys, "session", "status", "--id", "s0000001")
    assert code == 0, text
    assert "the repo merge into main failed" in text


def test_an_in_repo_failed_merge_records_nothing(tmp_path, monkeypatch, capsys):
    repo = new_repo(tmp_path / "plain", "https://host.example/team/plain.git")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "invariant.yaml").write_text("version: 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "manifest")
    monkeypatch.chdir(repo)
    start(capsys)
    code, text = run(capsys, "session", "isolate", "--id", "s0000001", "--execute")
    assert code == 0, text
    worktree = repo.parent / "plain-wt-s0000001"
    (worktree / "app.py").write_text("x = 2\n", encoding="utf-8")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-qm", "the code")
    for state in ("working", "reviewed"):
        run(capsys, "session", "transition", state, "--id", "s0000001",
            "--force", "--reason", "fixture")
    (repo / "app.py").write_text("in the way\n", encoding="utf-8")
    code, text = run(capsys, "session", "merge", "--id", "s0000001")
    assert code != 0, text
    assert "half-landed" not in text
    assert record(repo).landing is None
    (repo / "app.py").unlink()
    code, text = run(capsys, "session", "merge", "--id", "s0000001")
    assert code == 0, text
    assert record(repo).landing is None


# -- abandon -------------------------------------------------------------------


def test_abandon_is_held_while_the_docs_landed(synced, capsys):
    start(capsys)
    lock_and_write(synced, capsys)
    run(capsys, "docs", "unlock", "--id", "s0000001")
    code, text = run(
        capsys, "session", "transition", "abandoned", "--reason", "dropped",
        "--id", "s0000001",
    )
    assert code == 2, text
    assert "--force" in text
    assert head(synced.store)[:12] in text
    assert record(synced.widgets).state == "registered"


def test_a_forced_abandon_names_the_store_commits_that_stay_landed(
    synced, capsys, monkeypatch
):
    docs_landed(synced, capsys, monkeypatch)
    fail_the_merge(synced, capsys)
    landed = head(synced.store)
    code, text = run(
        capsys, "session", "transition", "abandoned", "--reason", "dropped",
        "--id", "s0000001", "--force",
    )
    assert code == 0, text
    assert "stay landed" in text
    assert landed[:12] in text
    assert f"docs: {CLI_DOC}" in text
    session = record(synced.widgets)
    assert session.state == "abandoned"
    override = [o for o in session.overrides if o.get("rule") == "session.landing"][-1]
    assert override["store_commits"][0].startswith(landed)


def test_abandon_with_units_held_and_nothing_landed_is_not_held(synced, capsys):
    start(capsys)
    lock_and_write(synced, capsys)
    code, text = run(
        capsys, "session", "transition", "abandoned", "--reason", "dropped",
        "--id", "s0000001",
    )
    assert code == 0, text


def test_repair_and_reap_hold_a_session_whose_docs_landed(synced, capsys, monkeypatch):
    worktree = docs_landed(synced, capsys, monkeypatch)
    git(synced.widgets, "worktree", "remove", "--force", str(worktree))
    git(synced.widgets, "branch", "-D", "session/s0000001")
    domain = SessionDomain(Manifest.locate(synced.widgets))
    assert domain.reap_stranded() == []
    actions = domain.repair()
    assert any("held" in a and "s0000001" in a for a in actions)
    assert domain.require("s0000001").state == "reviewed"


# -- ruling 22: a command a pull changed --------------------------------------


def test_a_command_a_pull_changed_is_named_at_session_start(synced, capsys):
    set_store(synced.colleague, {"commands": {"test": "pytest -q"}})
    code, text = run(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 0, text
    assert text.splitlines()[0].startswith("session s0000001 (")
    assert "commands.test" in text
    assert "pytest -q" in text
    set_store(synced.colleague, {"commands": {"test": "pytest -x"}})
    code, data = run_json(capsys, "session", "start", "more", "--id", "s0000002")
    assert code == 0
    assert data["commands_changed"] == [
        {"key": "commands.test", "old": "pytest -q", "new": "pytest -x"}
    ]


def test_a_pull_that_changes_no_command_names_none(synced, capsys):
    edit(synced.colleague, OVERVIEW, "theirs\n", push=True)
    code, data = run_json(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 0
    assert data["commands_changed"] == []
    code, text = run(capsys, "session", "start", "more", "--id", "s0000002")
    assert "changed a command" not in text


def test_resume_names_a_command_a_pull_changed(synced, capsys):
    start(capsys)
    set_store(synced.colleague, {"commands": {"lint": "ruff check ."}})
    code, text = run(capsys, "session", "resume", "s0000001")
    assert code == 0, text
    assert "commands.lint" in text and "ruff check ." in text


def test_an_in_repo_start_names_no_command(tmp_path, monkeypatch, capsys):
    repo = new_repo(tmp_path / "plain", "https://host.example/team/plain.git")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "invariant.yaml").write_text("version: 1\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    code, data = run_json(capsys, "session", "start", "work", "--id", "s0000001")
    assert code == 0
    assert data["commands_changed"] == []


# -- the pieces ----------------------------------------------------------------


def test_is_ancestor_answers_yes_no_and_cannot_tell(synced):
    first = git(synced.store, "rev-parse", "HEAD")
    edit(synced.store, OVERVIEW, "next\n")
    assert gitq.is_ancestor(synced.store, first, "HEAD") is True
    assert gitq.is_ancestor(synced.store, "HEAD", first) is False
    assert gitq.is_ancestor(synced.store, "0" * 40, "HEAD") is None
    assert gitq.is_ancestor(synced.store, "-x", "HEAD") is None


def test_commit_lines_names_the_commits_on_paths(synced):
    first = git(synced.store, "rev-parse", "HEAD")
    edit(synced.store, OVERVIEW, "next\n", message="overview")
    edit(synced.store, CLI_DOC, "next\n", message="cli")
    lines = gitq.commit_lines(synced.store, first, "HEAD", [CLI_DOC])
    assert len(lines) == 1 and lines[0].endswith(" cli")
    assert len(gitq.commit_lines(synced.store, first, "HEAD")) == 2
    assert gitq.commit_lines(synced.store, "nope", "HEAD") is None


def test_the_landing_state_of_a_record():
    assert Session(id="a", name="a").landing_state is None
    assert Session(id="a", name="a", landing={"store": "s"}).landing_state == "docs_landed"
    assert Session(
        id="a", name="a", landing={"store": "s", "failed": {"detail": "x"}}
    ).landing_state == "half_landed"
    assert Session(id="a", name="a", landing={"repo": "r"}).landing_state == "landed"


def test_a_record_written_before_the_landing_loads_with_none():
    session = Session.from_dict({"id": "a", "name": "a"})
    assert session.landing is None


def test_the_domain_refuses_an_unforced_abandon_while_landed(synced, capsys):
    start(capsys)
    lock_and_write(synced, capsys)
    run(capsys, "docs", "unlock", "--id", "s0000001")
    domain = SessionDomain(Manifest.locate(synced.widgets))
    with pytest.raises(UsageError, match="docs landed"):
        domain.transition("s0000001", "abandoned", reason="x")


def test_a_landing_with_nothing_written_is_held_and_named_as_such(synced, capsys):
    start(capsys)
    code, text = run(capsys, "docs", "lock", CLI_DOC, "--id", "s0000001")
    assert code == 0, text
    run(capsys, "docs", "unlock", "--id", "s0000001")
    code, text = run(
        capsys, "session", "transition", "abandoned", "--reason", "dropped",
        "--id", "s0000001",
    )
    assert code == 2, text
    assert "the spans changed nothing" in text
    code, text = run(
        capsys, "session", "transition", "abandoned", "--reason", "dropped",
        "--id", "s0000001", "--force",
    )
    assert code == 0, text
    assert "(its spans changed nothing)" in text


def test_resume_of_a_failed_merge_with_nothing_landed_says_so(synced, capsys, monkeypatch):
    start(capsys)
    isolate_with_change(synced, capsys, monkeypatch)
    fail_the_merge(synced, capsys)
    code, text = run(capsys, "session", "resume", "s0000001")
    assert code == 1, text
    assert "its repo merge is still not landed" in text


def test_status_shortens_a_long_merge_failure(synced, capsys, monkeypatch):
    start(capsys)
    lock_and_write(synced, capsys)
    run(capsys, "docs", "unlock", "--id", "s0000001")
    domain = SessionDomain(Manifest.locate(synced.widgets))
    domain.record_repo_failure("s0000001", "why " * 200, None)
    code, text = run(capsys, "session", "status", "--id", "s0000001")
    assert code == 0, text
    line = next(ln for ln in text.splitlines() if "half-landed" in ln)
    assert line.endswith("...")


def test_an_in_repo_session_has_no_store_landing_to_check(tmp_path, monkeypatch, capsys):
    repo = new_repo(tmp_path / "plain", "https://host.example/team/plain.git")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "invariant.yaml").write_text("version: 1\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    start(capsys)
    domain = SessionDomain(Manifest.locate(repo))
    session = domain.require("s0000001")
    session.landing = {"store": "abc", "repo": None, "failed": None}
    assert domain.store_landing_mismatch(session) is None
    assert domain.landed_store_commits(session) == []


def test_a_store_git_cannot_read_is_a_mismatch(synced, capsys):
    start(capsys)
    domain = SessionDomain(Manifest.locate(synced.widgets))
    session = domain.require("s0000001")
    session.landing = {"store": "0" * 40, "repo": None, "failed": {"detail": "x"}}
    assert domain.store_landing_mismatch(session) == ("0" * 40, head(synced.store))


def test_an_abandon_with_no_landing_prints_nothing_more(synced, capsys, monkeypatch):
    start(capsys)
    isolate_with_change(synced, capsys, monkeypatch)
    code, text = run(
        capsys, "session", "transition", "abandoned", "--reason", "dropped",
        "--id", "s0000001",
    )
    assert code == 0, text
    assert "stay landed" not in text


def test_landed_store_commits_reads_only_the_store_leg(synced, capsys):
    start(capsys)
    domain = SessionDomain(Manifest.locate(synced.widgets))
    session = domain.require("s0000001")
    session.legs = [{"kind": "repo", "lock_spans": [{"changed": ["x"]}]}]
    assert domain.landed_store_commits(session) == []


def test_git_answers_none_where_it_cannot(synced, monkeypatch):
    assert gitq.commit_lines(synced.store, "-x", "HEAD") is None
    monkeypatch.setattr(gitq, "_run", lambda args, cwd: (128, "", "broken"))
    monkeypatch.setattr(gitq, "resolve_commit", lambda cwd, rev: "a" * 40)
    assert gitq.is_ancestor(synced.store, "HEAD", "HEAD") is None
