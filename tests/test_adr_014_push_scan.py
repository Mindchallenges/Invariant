# ruff: noqa: F811 - the fixtures imported below are named as parameters
"""ADR-014 increment 6: every store commit is scanned before it can leave (D8).

The push scan: ``store push``, push after landing (D7) and the store's
``pre-push`` git hook each scan every outgoing commit's content and message,
with the store's own ``privacy`` block read at that commit, and a finding
refuses the push; the first push after linking a remote scans the whole
unpublished history. The earlier screens: ``pre-commit`` and ``commit-msg``
hooks reached through the store's local ``core.hooksPath``, and
``privacy.clean`` spelling store paths against the store at the edit, at a
commit made in the store and at the review boundary. The relaxation
(ruling 10): ``privacy.store.scan: false`` honoured only while the store has
no remote, named by ``doctor``. A store's default scope is ``all``.

The proof is a home path refused at push from a host (``invariant store
push`` from the repository, and push after landing), from a shell (a plain
``git push`` in the store, through the ``pre-push`` hook) and from inside the
store (``invariant store push`` run there).

Every remote is a local bare repository: nothing here reaches a network. The
home is the suite's scratch one. A commit carrying a home path on purpose is
made with ``--no-verify``, the one way past the commit hooks, so the push is
what is under test.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml
from test_adr_014_landing import (  # noqa: F401 - fixtures are used by name
    CLI_DOC,
    OVERVIEW,
    WIDGETS,
    docs_landed,
    git,
    head,
    identify,
    new_repo,
    record,
    run,
    run_json,
    start,
    synced,
)

from invariant.core import git as gitq
from invariant.core.errors import ManifestError
from invariant.core.home import invariant_home
from invariant.core.manifest import Manifest
from invariant.core.rules import RuleContext, get_rule
from invariant.domains.session import SessionDomain


class _Lazy:
    """``core/storescan``, imported when first used, so a build without it
    fails each test that needs it rather than the whole module."""

    def __getattr__(self, name):
        import importlib

        return getattr(importlib.import_module("invariant.core.storescan"), name)


storescan = _Lazy()

#: A home path, assembled so this file carries none, and the part of it the
#: home-path pattern matches, which an allow entry names.
HOME_PATH = "/Us" + "ers/someone/app"
HOME = "/Us" + "ers/someone"


def sh(cwd: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """git as a shell would run it: hooks fire, and the exit code is kept."""
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False,
        env={**os.environ, **(env or {})},
    )


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sneak(repo: Path, rel: str, text: str, message: str = "sneaked") -> str:
    """Commit past the commit hooks, the one way they can be skipped."""
    write(repo, rel, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", message)
    return head(repo)


def commit(repo: Path, rel: str, text: str, message: str = "docs") -> subprocess.CompletedProcess:
    write(repo, rel, text)
    git(repo, "add", "-A")
    return sh(repo, "commit", "-q", "-m", message)


def store_config(store: Path) -> dict:
    return yaml.safe_load((store / "invariant.yaml").read_text(encoding="utf-8"))


def set_privacy(store: Path, block: dict, *, verify: bool = True) -> None:
    body = store_config(store)
    body["privacy"] = block
    (store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    git(store, "add", "-A")
    git(store, "commit", "-q", *([] if verify else ["--no-verify"]), "-m", "privacy")


def remote_head(remote: Path) -> str:
    return git(remote, "rev-parse", "main")


# -- the proof: a home path refused at push from a host, a shell and the store ----


class TestTheProof:
    def test_a_shell_push_is_refused_by_the_pre_push_hook(self, synced):
        before = remote_head(synced.remote)
        bad = sneak(synced.store, CLI_DOC, f"see {HOME_PATH}\n")
        pushed = sh(synced.store, "push", "origin", "main")
        assert pushed.returncode != 0
        assert "privacy" in pushed.stderr
        assert bad[:12] in pushed.stderr
        assert HOME_PATH not in pushed.stderr
        assert remote_head(synced.remote) == before

    def test_a_host_push_is_refused(self, synced, capsys):
        before = remote_head(synced.remote)
        bad = sneak(synced.store, CLI_DOC, f"see {HOME_PATH}\n")
        code, text = run(capsys, "store", "push")
        assert code == 1, text
        assert "refused" in text
        assert bad[:12] in text
        assert HOME_PATH not in text
        assert remote_head(synced.remote) == before

    def test_a_push_from_inside_the_store_is_refused(self, synced, capsys, monkeypatch):
        before = remote_head(synced.remote)
        sneak(synced.store, CLI_DOC, f"see {HOME_PATH}\n")
        monkeypatch.chdir(synced.store / "docs")
        code, text = run(capsys, "store", "push")
        assert code == 1, text
        assert "refused" in text
        assert remote_head(synced.remote) == before

    def test_a_home_path_in_a_message_is_refused_at_push(self, synced, capsys):
        sneak(synced.store, CLI_DOC, "clean\n", message=f"from {HOME_PATH}")
        code, text = run(capsys, "store", "push")
        assert code == 1, text
        assert "message" in text

    def test_a_clean_push_from_the_host_publishes(self, synced, capsys):
        sneak(synced.store, CLI_DOC, "clean\n")
        code, out = run_json(capsys, "store", "push")
        assert code == 0, out
        assert out["state"] == "pushed"
        assert out["commits"] == 1
        assert remote_head(synced.remote) == head(synced.store)

    def test_a_clean_shell_push_goes_through_the_hook(self, synced):
        assert commit(synced.store, CLI_DOC, "clean\n").returncode == 0
        pushed = sh(synced.store, "push", "-q", "origin", "main")
        assert pushed.returncode == 0, pushed.stderr
        assert remote_head(synced.remote) == head(synced.store)

    def test_a_push_in_step_publishes_nothing(self, synced, capsys):
        code, out = run_json(capsys, "store", "push")
        assert code == 0, out
        assert out["state"] == "current"


# -- push after landing (D7) ------------------------------------------------------


def turn_on_push_after_landing(synced) -> None:
    body = store_config(synced.store)
    body["sync"] = {"push_after_landing": True}
    (synced.store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    git(synced.store, "add", "-A")
    git(synced.store, "commit", "-qm", "push after landing")
    git(synced.store, "push", "-q", "origin", "main")


class TestPushAfterLanding:
    def test_the_refusal_is_lifted(self, synced):
        turn_on_push_after_landing(synced)
        assert Manifest.locate(synced.widgets).get("sync.push_after_landing") is True

    def test_a_landing_pushes_the_store_after_the_scan(self, synced, capsys, monkeypatch):
        turn_on_push_after_landing(synced)
        docs_landed(synced, capsys, monkeypatch)
        sneak(synced.store, "docs/notes.rst", "clean\n")
        code, text = run(capsys, "session", "merge", "--id", "s0000001")
        assert code == 0, text
        assert "pushed" in text
        assert remote_head(synced.remote) == head(synced.store)

    def test_a_landing_whose_push_is_refused_stays_merged(self, synced, capsys, monkeypatch):
        turn_on_push_after_landing(synced)
        docs_landed(synced, capsys, monkeypatch)
        before = remote_head(synced.remote)
        sneak(synced.store, "docs/notes.rst", f"see {HOME_PATH}\n")
        code, text = run(capsys, "session", "merge", "--id", "s0000001")
        assert code == 0, text
        assert record(synced.widgets).state == "merged"
        assert "invariant store push" in text
        assert "refused" in text
        assert remote_head(synced.remote) == before

    def test_a_forced_landing_still_screens_its_push(self, synced, capsys, monkeypatch):
        # Decision 8: a forced merge passes the merge gates, never the screen.
        turn_on_push_after_landing(synced)
        docs_landed(synced, capsys, monkeypatch)
        before = remote_head(synced.remote)
        sneak(synced.store, "docs/notes.rst", f"see {HOME_PATH}\n")
        code, text = run(capsys, "session", "merge", "--id", "s0000001", "--force")
        assert code == 0, text
        assert record(synced.widgets).state == "merged"
        assert "refused" in text
        assert remote_head(synced.remote) == before

    def test_with_push_after_landing_off_nothing_is_pushed(self, synced, capsys, monkeypatch):
        docs_landed(synced, capsys, monkeypatch)
        sneak(synced.store, "docs/notes.rst", "clean\n")
        before = remote_head(synced.remote)
        code, text = run(capsys, "session", "merge", "--id", "s0000001")
        assert code == 0, text
        assert remote_head(synced.remote) == before


# -- the first push after linking a remote ------------------------------------------


@pytest.fixture
def unpublished(tmp_path, monkeypatch, capsys):
    """widgets linked to a store with no remote yet."""
    widgets = new_repo(tmp_path / "widgets", f"https://{WIDGETS}.git")
    monkeypatch.chdir(widgets)
    code, text = run(capsys, "store", "init")
    assert code == 0, text
    store = invariant_home() / "stores" / "widgets"
    identify(store)
    git(store, "add", "-A")
    git(store, "commit", "-qm", "the store")
    git(store, "branch", "-M", "main")

    class Unpublished:
        pass

    t = Unpublished()
    t.widgets, t.store, t.tmp = widgets, store, tmp_path
    return t


def link_remote(t) -> Path:
    remote = t.tmp / "remote.git"
    git(t.tmp, "init", "-q", "--bare", "-b", "main", str(remote))
    git(t.store, "remote", "add", "origin", str(remote))
    return remote


class TestTheFirstPush:
    def test_it_scans_the_whole_unpublished_history(self, unpublished, capsys):
        old = sneak(unpublished.store, "docs/early.rst", f"see {HOME_PATH}\n")
        for n in range(2):
            sneak(unpublished.store, f"docs/later{n}.rst", "clean\n")
        link_remote(unpublished)
        code, out = run_json(capsys, "store", "push")
        assert code == 1, out
        assert out["state"] == "refused"
        assert out["commits"] == 4
        assert any(f["where"].startswith(old[:12]) for f in out["screen"]["findings"])

    def test_a_store_with_no_remote_has_nothing_to_push(self, unpublished, capsys):
        code, out = run_json(capsys, "store", "push")
        assert code == 1, out
        assert out["state"] == "no_remote"


# -- the commit hooks ---------------------------------------------------------------


class TestTheCommitHooks:
    def test_store_init_installs_them_through_the_local_hooks_path(self, synced):
        target = synced.store / ".git" / "invariant" / "hooks"
        assert Path(git(synced.store, "config", "--local", "core.hooksPath")) == target
        for name in ("pre-commit", "commit-msg", "pre-push"):
            assert os.access(target / name, os.X_OK)
        assert storescan.hooks_state(synced.store) is None

    def test_pre_commit_refuses_a_home_path(self, synced):
        before = head(synced.store)
        made = commit(synced.store, CLI_DOC, f"see {HOME_PATH}\n")
        assert made.returncode != 0
        assert "privacy" in made.stderr
        assert HOME_PATH not in made.stderr
        assert head(synced.store) == before

    def test_commit_msg_refuses_a_home_path(self, synced):
        before = head(synced.store)
        made = commit(synced.store, CLI_DOC, "clean\n", message=f"from {HOME_PATH}")
        assert made.returncode != 0
        assert "message" in made.stderr
        assert head(synced.store) == before

    def test_a_global_hooks_path_does_not_skip_them(self, synced, tmp_path):
        elsewhere = tmp_path / "global-hooks"
        elsewhere.mkdir()
        config = tmp_path / "gitconfig"
        config.write_text(f"[core]\n\thooksPath = {elsewhere}\n", encoding="utf-8")
        write(synced.store, CLI_DOC, f"see {HOME_PATH}\n")
        git(synced.store, "add", "-A")
        made = sh(synced.store, "commit", "-qm", "x", env={"GIT_CONFIG_GLOBAL": str(config)})
        assert made.returncode != 0

    def test_they_fire_from_a_linked_worktree_of_the_store(self, synced, tmp_path):
        linked = tmp_path / "store-linked"
        git(synced.store, "worktree", "add", "-q", "-b", "side", str(linked))
        made = commit(linked, CLI_DOC, f"see {HOME_PATH}\n")
        assert made.returncode != 0
        assert "privacy" in made.stderr

    def test_the_stores_own_hook_still_runs_after_the_screen(self, synced):
        own = synced.store / ".git" / "hooks" / "pre-commit"
        own.write_text("#!/bin/sh\necho own hook ran >&2\nexit 3\n", encoding="utf-8")
        own.chmod(0o755)
        made = commit(synced.store, CLI_DOC, "clean\n")
        assert made.returncode != 0
        assert "own hook ran" in made.stderr

    def test_a_clean_commit_passes(self, synced):
        assert commit(synced.store, CLI_DOC, "clean\n").returncode == 0

    def test_session_start_repairs_a_missing_hook(self, synced, capsys):
        (synced.store / ".git" / "invariant" / "hooks" / "pre-push").unlink()
        assert "pre-push" in storescan.hooks_state(synced.store)
        start(capsys)
        assert storescan.hooks_state(synced.store) is None

    def test_store_link_installs_them_too(self, synced, capsys, tmp_path, monkeypatch):
        git(synced.store, "config", "--local", "--unset", "core.hooksPath")
        monkeypatch.chdir(synced.widgets)
        code, text = run(capsys, "store", "link", str(synced.store))
        assert code == 0, text
        assert storescan.hooks_state(synced.store) is None

    def test_a_local_hooks_path_elsewhere_is_named_not_overwritten(self, synced, tmp_path):
        git(synced.store, "config", "--local", "core.hooksPath", str(tmp_path / "theirs"))
        done = storescan.install_hooks(synced.store, "/nonexistent/python", "/nonexistent")
        assert "core.hooksPath" in done.problem
        assert "theirs" in git(synced.store, "config", "--local", "core.hooksPath")
        assert "not its privacy hooks" in storescan.hooks_state(synced.store)

    def test_with_no_invariant_reachable_the_hook_fails_closed(self, synced):
        storescan.install_hooks(synced.store, "/nonexistent/python", "/nonexistent")
        write(synced.store, CLI_DOC, "clean\n")
        git(synced.store, "add", "-A")
        made = sh(synced.store, "commit", "-qm", "x", env={"PATH": "/usr/bin:/bin"})
        assert made.returncode != 0
        assert "cannot run" in made.stderr


# -- the store's privacy block, read at the commit ------------------------------------


class TestTheStoresBlock:
    def test_the_default_scope_is_all(self, synced, capsys):
        sneak(synced.store, "notes/anywhere.txt", f"see {HOME_PATH}\n")
        code, text = run(capsys, "store", "push")
        assert code == 1, text

    def test_a_narrower_scope_leaves_other_paths_unscanned(self, synced, capsys):
        set_privacy(synced.store, {"scope": ["docs"]}, verify=False)
        body = store_config(synced.store)
        body["paths"] = {"docs": "docs/architecture"}
        (synced.store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
        sneak(synced.store, "notes/anywhere.txt", f"see {HOME_PATH}\n")
        code, text = run(capsys, "store", "push")
        assert code == 0, text
        sneak(synced.store, OVERVIEW, f"see {HOME_PATH}\n")
        code, text = run(capsys, "store", "push")
        assert code == 1, text

    def test_an_allow_entry_landing_with_its_value_is_read_at_that_commit(self, synced, capsys):
        body = store_config(synced.store)
        body["privacy"] = {"allow": [HOME]}
        (synced.store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
        write(synced.store, CLI_DOC, f"see {HOME_PATH}\n")
        git(synced.store, "add", "-A")
        assert sh(synced.store, "commit", "-qm", "allowed").returncode == 0
        code, text = run(capsys, "store", "push")
        assert code == 0, text

    def test_an_allow_entry_added_later_does_not_excuse_an_earlier_commit(
        self, synced, capsys
    ):
        sneak(synced.store, CLI_DOC, f"see {HOME_PATH}\n")
        set_privacy(synced.store, {"allow": [HOME]})
        code, text = run(capsys, "store", "push")
        assert code == 1, text

    def test_a_block_that_does_not_load_at_a_commit_refuses_the_push(self, synced, capsys):
        set_privacy(synced.store, {"patterns": ["no_such_pattern"]}, verify=False)
        code, out = run_json(capsys, "store", "push")
        assert code == 1, out
        assert "no_such_pattern" in out["screen"]["problem"]


# -- the relaxation (ruling 10) --------------------------------------------------------


class TestTheRelaxation:
    def test_honoured_while_the_store_has_no_remote(self, unpublished, capsys):
        set_privacy(unpublished.store, {"store": {"scan": False}})
        assert commit(unpublished.store, CLI_DOC, f"see {HOME_PATH}\n").returncode == 0
        _code, text = run(capsys, "doctor")
        assert "store privacy" in text
        assert "active exception" in text

    def test_ignored_once_a_remote_exists(self, unpublished, capsys):
        set_privacy(unpublished.store, {"store": {"scan": False}})
        link_remote(unpublished)
        assert commit(unpublished.store, CLI_DOC, f"see {HOME_PATH}\n").returncode != 0
        code, text = run(capsys, "doctor")
        assert code == 1
        assert "remove" in text and "privacy.store.scan" in text

    def test_a_push_while_it_is_present_scans_the_whole_history(self, unpublished, capsys):
        set_privacy(unpublished.store, {"store": {"scan": False}})
        commit(unpublished.store, "docs/early.rst", f"see {HOME_PATH}\n")
        remote = link_remote(unpublished)
        git(unpublished.store, "push", "-q", "--no-verify", "origin", "main")
        sneak(unpublished.store, "docs/later.rst", "clean\n")
        code, out = run_json(capsys, "store", "push")
        assert code == 1, out
        assert out["whole"] is True
        assert remote_head(remote) != head(unpublished.store)

    def test_the_doctor_row_without_the_key_names_the_hooks(self, synced, capsys):
        _code, text = run(capsys, "doctor")
        assert "store privacy" in text
        assert "pre-push" in text

    def test_the_doctor_row_fails_on_missing_hooks(self, synced, capsys):
        git(synced.store, "config", "--local", "--unset", "core.hooksPath")
        code, text = run(capsys, "doctor")
        assert code == 1
        assert "core.hooksPath" in text


# -- the routes: privacy.clean spells store paths against the store ---------------------


def gate(repo: Path, payload: dict, sid: str = "s0000001"):
    manifest = Manifest.locate(repo)
    session = SessionDomain(manifest).require(sid)
    return get_rule("privacy.clean").fn(
        RuleContext(
            manifest=manifest, cwd=repo, session=session.to_dict(), payload=payload,
        )
    )


class TestTheRoutes:
    def test_an_edit_into_a_store_doc_is_screened(self, synced, capsys):
        start(capsys)
        target = synced.store / "docs" / "notes.rst"
        verdict = gate(synced.widgets, {
            "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": f"see {HOME_PATH}\n"},
        })
        assert not verdict.ok
        assert "personal-data" in verdict.message

    def test_a_clean_edit_into_a_store_doc_is_named_as_scanned(self, synced, capsys):
        start(capsys)
        target = synced.store / "docs" / "notes.rst"
        verdict = gate(synced.widgets, {
            "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": "clean\n"},
        })
        assert verdict.ok and not verdict.skipped
        assert "notes.rst" in verdict.message

    def test_a_commit_made_in_the_store_is_screened(self, synced, capsys):
        start(capsys)
        write(synced.store, CLI_DOC, f"see {HOME_PATH}\n")
        git(synced.store, "add", "-A")
        verdict = gate(synced.widgets, {
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {synced.store} commit -m docs"},
        })
        assert not verdict.ok
        assert "personal-data" in verdict.message

    def test_a_store_commit_message_is_screened(self, synced, capsys):
        start(capsys)
        write(synced.store, CLI_DOC, "clean\n")
        git(synced.store, "add", "-A")
        verdict = gate(synced.widgets, {
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {synced.store} commit -m 'from {HOME_PATH}'"},
        })
        assert not verdict.ok

    def test_a_clean_store_commit_passes_named(self, synced, capsys):
        start(capsys)
        write(synced.store, CLI_DOC, "clean\n")
        git(synced.store, "add", "-A")
        verdict = gate(synced.widgets, {
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {synced.store} commit -m docs"},
        })
        assert verdict.ok and not verdict.skipped
        assert "cli.rst" in verdict.message

    def test_the_relaxation_is_named_at_the_edit(self, unpublished, capsys):
        set_privacy(unpublished.store, {"store": {"scan": False}})
        start(capsys)
        target = unpublished.store / "docs" / "notes.rst"
        verdict = gate(unpublished.widgets, {
            "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": f"see {HOME_PATH}\n"},
        })
        assert verdict.ok
        assert "privacy.store.scan" in verdict.message + (verdict.detail or "")


# -- the settings --------------------------------------------------------------------------


class TestTheSettings:
    def test_privacy_store_must_be_a_mapping(self, synced):
        set_privacy(synced.store, {"store": "off"}, verify=False)
        with pytest.raises(ManifestError, match="privacy.store must be a mapping"):
            Manifest.locate(synced.widgets)

    def test_an_unknown_key_is_refused(self, synced):
        set_privacy(synced.store, {"store": {"push": False}}, verify=False)
        with pytest.raises(ManifestError, match="privacy.store.push is not a setting"):
            Manifest.locate(synced.widgets)

    def test_scan_must_be_a_boolean(self, synced):
        set_privacy(synced.store, {"store": {"scan": "no"}}, verify=False)
        with pytest.raises(ManifestError, match="true or false"):
            Manifest.locate(synced.widgets)

    def test_a_repository_block_may_not_set_it(self, synced):
        body = store_config(synced.store)
        body["store"]["repos"][WIDGETS]["privacy"] = {"store": {"scan": False}}
        (synced.store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
        with pytest.raises(ManifestError, match="store's top level"):
            Manifest.locate(synced.widgets)

    def test_an_in_repo_project_may_not_set_it(self, tmp_path):
        repo = new_repo(tmp_path / "plain", "https://host.example/team/plain.git")
        (repo / ".claude").mkdir()
        (repo / ".claude" / "invariant.yaml").write_text(
            yaml.safe_dump({"version": 1, "privacy": {"store": {"scan": False}}}),
            encoding="utf-8",
        )
        with pytest.raises(ManifestError, match="store's top level"):
            Manifest.locate(repo)

    def test_store_push_in_an_in_repo_project_is_refused(self, tmp_path, monkeypatch, capsys):
        repo = new_repo(tmp_path / "plain", "https://host.example/team/plain.git")
        (repo / ".claude").mkdir()
        (repo / ".claude" / "invariant.yaml").write_text("version: 1\n", encoding="utf-8")
        monkeypatch.chdir(repo)
        code, text = run(capsys, "store", "push")
        assert code == 2, text
        assert "no store" in text


# -- the screen's own parts ------------------------------------------------------------------


class TestTheScreen:
    def test_defaults_with_no_config(self, tmp_path):
        screen = storescan.screen_from(tmp_path, None, "nothing")
        assert screen.scope == ("all",)
        assert screen.covers("any/where.txt")
        assert not screen.relaxed

    def test_keywords_resolve_through_the_store_and_its_repository_blocks(self, tmp_path):
        text = yaml.safe_dump({
            "paths": {"docs": "docs/architecture"},
            "privacy": {"scope": ["docs", "memory", "notes/", "commit_messages"]},
            "store": {"repos": {"a": {"paths": {"memory": "mem", "memory_index": "/abs"}}}},
        })
        screen = storescan.screen_from(tmp_path, text, "here")
        assert screen.covers("docs/architecture/x.rst")
        assert screen.covers("mem/one.md")
        assert screen.covers("notes/a.txt")
        assert not screen.covers("docs/other.rst")
        assert not screen.covers("abs/x")

    @pytest.mark.parametrize(
        ("text", "match"),
        [
            ("[1, 2", "not valid YAML"),
            ("- a\n", "must contain a mapping"),
            ("privacy: [1]\n", "privacy must be a mapping"),
            ("privacy: {scope: docs}\n", "privacy.scope must be a list"),
            ("privacy: {allow: [{paths: []}]}\n", "here"),
        ],
    )
    def test_a_block_that_would_screen_something_else_is_refused(self, tmp_path, text, match):
        with pytest.raises(ManifestError, match=match):
            storescan.screen_from(tmp_path, text, "here")

    def test_bytes_are_read_as_text(self, tmp_path):
        screen = storescan.screen_from(tmp_path, b"privacy: {store: {scan: false}}\n", "b")
        assert screen.relaxed

    def test_a_binary_blob_carries_nothing(self, synced):
        (synced.store / "img.bin").write_bytes(b"\xff\xfe\x00")
        git(synced.store, "add", "-A")
        assert storescan.scan_staged(synced.store).clean
        git(synced.store, "commit", "-q", "--no-verify", "-m", "binary")
        assert storescan.scan_commits(synced.store, [head(synced.store)]).clean

    def test_unreadable_answers_refuse(self, synced, monkeypatch):
        sha = head(synced.store)
        monkeypatch.setattr(gitq, "commit_paths", lambda *_a: None)
        assert "files of commit" in storescan.scan_commits(synced.store, [sha]).problem
        monkeypatch.undo()
        monkeypatch.setattr(gitq, "commit_message", lambda *_a: None)
        assert "message" in storescan.scan_commits(synced.store, [sha]).problem
        monkeypatch.undo()
        monkeypatch.setattr(gitq, "blob_at", lambda *_a: None)
        assert storescan.scan_commits(synced.store, [sha]).problem
        monkeypatch.undo()
        monkeypatch.setattr(gitq, "exists_at", lambda *_a: None)
        assert "cannot be read" in storescan.scan_commits(synced.store, [sha]).problem
        monkeypatch.undo()
        write(synced.store, CLI_DOC, "x\n")
        git(synced.store, "add", "-A")
        monkeypatch.setattr(gitq, "staged_files", lambda *_a, **_k: None)
        assert storescan.scan_staged(synced.store).problem
        monkeypatch.undo()
        monkeypatch.setattr(gitq, "staged_blob", lambda *_a: None)
        assert "cannot be read" in storescan.scan_staged(synced.store).problem
        monkeypatch.undo()
        monkeypatch.setattr(gitq, "remotes", lambda *_a: None)
        assert not storescan.relaxation(synced.store).honoured

    def test_a_store_config_that_does_not_load_relaxes_nothing(self, synced):
        (synced.store / "invariant.yaml").write_text("[1, 2", encoding="utf-8")
        found = storescan.relaxation(synced.store)
        assert not found.honoured and found.problem
        assert storescan.scan_staged(synced.store).clean
        (synced.store / "invariant.yaml").write_text("[1, 2", encoding="utf-8")
        git(synced.store, "add", "invariant.yaml")
        assert storescan.scan_staged(synced.store).problem
        assert storescan.scan_message(synced.store, "x").problem

    def test_message_comment_lines_are_not_the_message(self, synced):
        assert storescan.scan_message(synced.store, f"ok\n# {HOME_PATH}\n").clean
        assert not storescan.scan_message(synced.store, f"ok {HOME_PATH}\n").clean

    def test_the_findings_render_redacted_and_capped(self, synced):
        text = "".join(f"{HOME_PATH}/{n}\n" for n in range(25))
        found = storescan.scan_message(synced.store, text)
        lines = found.lines()
        assert lines[-1].startswith("  ... and")
        assert HOME_PATH not in "\n".join(lines)
        assert found.to_dict()["findings"]

    def test_pre_push_skips_a_deletion_and_a_malformed_line(self, synced):
        commits, screened = storescan.pre_push(
            synced.store, "origin", f"refs/heads/x {'0' * 40} refs/heads/x {'1' * 40}\nbad\n"
        )
        assert commits == [] and screened.clean

    def test_pre_push_to_an_unknown_ref_is_refused(self, synced, monkeypatch):
        monkeypatch.setattr(gitq, "commits_to_publish", lambda *_a, **_k: None)
        _commits, screened = storescan.pre_push(
            synced.store, "origin", f"refs/heads/main {'a' * 40} refs/heads/main {'0' * 40}\n"
        )
        assert screened.problem

    def test_hooks_outside_a_repository(self, tmp_path):
        assert storescan.install_hooks(tmp_path, "py", "src").problem
        assert "not a git repository" in storescan.hooks_state(tmp_path)

    def test_a_hooks_path_that_cannot_be_set_is_named(self, synced, monkeypatch):
        git(synced.store, "config", "--local", "--unset", "core.hooksPath")
        monkeypatch.setattr(gitq, "set_local_config", lambda *_a: (False, "locked"))
        assert "locked" in storescan.install_hooks(synced.store, "py", "src").problem

    def test_the_hooks_path_unset_is_named(self, synced):
        git(synced.store, "config", "--local", "--unset", "core.hooksPath")
        assert "not set" in storescan.hooks_state(synced.store)


# -- core/git's new reads and its write -------------------------------------------------------


class TestGit:
    def test_commits_to_publish_refuses_what_it_cannot_read(self, synced):
        assert gitq.commits_to_publish(synced.store, "no-such-rev") is None
        assert gitq.commits_to_publish(synced.store, "HEAD", remote="-x") is None
        assert gitq.commits_to_publish(synced.store, "HEAD", remote="origin") == []

    def test_commit_paths_and_message(self, synced):
        assert gitq.commit_paths(synced.store, "no-such-rev") is None
        assert gitq.commit_message(synced.store, "no-such-rev") is None
        root = git(synced.store, "rev-list", "--max-parents=0", "HEAD")
        assert "invariant.yaml" in gitq.commit_paths(synced.store, root)

    def test_push_refuses_an_option_shaped_name(self, synced):
        assert gitq.push(synced.store, "-x", "HEAD:main", 5)[0] is False
        assert gitq.push(synced.store, "nowhere", "HEAD:refs/heads/main", 5)[0] is False

    def test_local_config_value(self, synced):
        assert gitq.local_config_value(synced.store, "no.such") is None


# -- what a push cannot do ---------------------------------------------------------------------


class TestAPushThatDoesNotPublish:
    def test_a_push_git_rejects_is_named_as_failed(self, synced, capsys):
        edit_remote = synced.colleague
        write(edit_remote, CLI_DOC, "theirs\n")
        git(edit_remote, "add", "-A")
        git(edit_remote, "commit", "-qm", "theirs")
        git(edit_remote, "push", "-q", "origin", "main")
        sneak(synced.store, OVERVIEW, "mine\n")
        code, out = run_json(capsys, "store", "push")
        assert code == 1, out
        assert out["state"] == "failed"
        assert out["detail"]

    def test_a_detached_store_is_not_pushed(self, synced, capsys):
        git(synced.store, "checkout", "-q", "--detach")
        code, out = run_json(capsys, "store", "push")
        assert code == 1, out
        assert out["state"] == "blocked"

    def test_an_unlistable_history_is_not_pushed(self, synced, capsys, monkeypatch):
        monkeypatch.setattr(gitq, "commits_to_publish", lambda *_a, **_k: None)
        code, out = run_json(capsys, "store", "push")
        assert code == 1, out
        assert out["state"] == "blocked"


class TestTheHookCommand:
    def test_outside_a_repository_it_refuses(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        code, text = run(capsys, "store", "hook", "pre-commit")
        assert code == 1
        assert "outside a git repository" in text

    def test_commit_msg_without_its_file_refuses(self, synced, monkeypatch, capsys):
        monkeypatch.chdir(synced.store)
        code, text = run(capsys, "store", "hook", "commit-msg")
        assert code == 1
        assert "could not be read" in text

    def test_honoured_relaxation_passes_the_commit_hooks_unread(
        self, unpublished, monkeypatch, capsys
    ):
        set_privacy(unpublished.store, {"store": {"scan": False}})
        monkeypatch.chdir(unpublished.store)
        message = unpublished.tmp / "msg"
        message.write_text(f"from {HOME_PATH}\n", encoding="utf-8")
        code, _text = run(capsys, "store", "hook", "commit-msg", str(message))
        assert code == 0


class TestTheRouteRefusals:
    def test_a_store_block_that_does_not_load_refuses_the_edit(self, synced, capsys):
        start(capsys)
        manifest = Manifest.locate(synced.widgets)
        session = SessionDomain(manifest).require("s0000001")
        (synced.store / "invariant.yaml").write_text("[1, 2", encoding="utf-8")
        verdict = get_rule("privacy.clean").fn(
            RuleContext(
                manifest=manifest, cwd=synced.widgets, session=session.to_dict(),
                payload={
                    "tool_name": "Write",
                    "tool_input": {
                        "file_path": str(synced.store / CLI_DOC), "content": "clean\n",
                    },
                },
            )
        )
        assert not verdict.ok
        assert "privacy block could not be read" in verdict.message

    def test_a_store_commit_whose_staged_content_cannot_be_read_refuses(
        self, synced, capsys, monkeypatch
    ):
        start(capsys)
        write(synced.store, CLI_DOC, "clean\n")
        git(synced.store, "add", "-A")
        monkeypatch.setattr(gitq, "staged_files", lambda *_a, **_k: None)
        verdict = gate(synced.widgets, {
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {synced.store} commit -m docs"},
        })
        assert not verdict.ok
        assert "staged content could not be scanned" in verdict.message

    def test_a_relaxed_store_commit_is_named_not_screened(self, unpublished, capsys):
        set_privacy(unpublished.store, {"store": {"scan": False}})
        start(capsys)
        write(unpublished.store, CLI_DOC, f"see {HOME_PATH}\n")
        git(unpublished.store, "add", "-A")
        verdict = gate(unpublished.widgets, {
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {unpublished.store} commit -m docs"},
        })
        assert verdict.ok
        assert "privacy.store.scan" in (verdict.detail or "") + verdict.message

    def test_a_relaxed_store_commit_message_is_not_read(self, unpublished, capsys):
        # Decision 4: the relaxation silences the message read as well, as
        # the commit-msg hook does; the push still reads it.
        set_privacy(unpublished.store, {"store": {"scan": False}})
        start(capsys)
        write(unpublished.store, CLI_DOC, "clean\n")
        git(unpublished.store, "add", "-A")
        verdict = gate(unpublished.widgets, {
            "tool_name": "Bash",
            "tool_input": {
                "command": f"git -C {unpublished.store} commit -m 'from {HOME_PATH}'",
            },
        })
        assert verdict.ok
        assert "privacy.store.scan" in (verdict.detail or "") + verdict.message
