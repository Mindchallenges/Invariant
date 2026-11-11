"""ADR-014 increment 4b: the store's docs lock enforced in the host (D5).

Two repositories share one store, each with a session bound to its own host.
The hook grants a free unit on a read and refuses a busy one
(``store.docs_lock_held``); memory is outside the lock (ruling 20); a read is
recorded with the lease and unit it ran under, and ``docs.read_architecture``
counts only those; the hooks keep the lease's heartbeat; a unit passed on by a
release is told to its new holder in the turn context and by the oracle. The
home is the suite's scratch one; nothing reaches the real ~/.invariant or
~/.claude. 4c's gates per leg are not here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import yaml
from test_adr_014_docs_lock import (  # noqa: F401 - the fixture is used by name
    GADGETS,
    WIDGETS,
    git,
    lease_record,
    run,
    run_json,
    session_record,
    spans,
    two,
)

from invariant.core import doclock
from invariant.core.doclock import DocsLock, DocsLockConfig
from invariant.core.manifest import Manifest
from invariant.core.rules import RuleContext, docs_roots, get_rule
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain

HOST_A = "host-widgets-a"
HOST_B = "host-gadgets-b"


class _Stdin:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text

    def isatty(self) -> bool:
        return False


@pytest.fixture
def hosts(two, monkeypatch, capsys):  # noqa: F811 - the fixture, imported above
    """``two``, with memory kept in the store, each session bound to a host
    and running a tdd pipeline."""
    body = yaml.safe_load((two.store / "invariant.yaml").read_text(encoding="utf-8"))
    for identity in (WIDGETS, GADGETS):
        body["store"]["repos"][identity] = {
            "records": {"docs": "store", "memory": "store"},
            "paths": {"memory": "memory"},
            "docs": {"needs": {"srcdir": "docs"}},
        }
    (two.store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    (two.store / "memory").mkdir()
    (two.store / "memory" / "MEMORY.md").write_text("# memory\n", encoding="utf-8")
    git(two.store, "add", "-A")
    git(two.store, "commit", "-qm", "memory")
    for repo, sid, host in ((two.widgets, "aaaa1111", HOST_A), (two.gadgets, "bbbb2222", HOST_B)):
        two.at(repo)
        code, text = run(capsys, "session", "adopt", host, "--id", sid)
        assert code == 0, text
        code, text = run(capsys, "pipeline", "start", "--task", f"design {sid}", "--id", sid)
        assert code == 0, text

    def hook(event: str, repo: Path, host: str, payload: dict | None = None) -> dict:
        body = {"session_id": host, **(payload or {})}
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
        monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(body)))
        two.at(repo)
        code, text = run(capsys, "hook", event)
        assert code == 0, text
        return json.loads(text)

    def read(repo: Path, host: str, path: Path, tool: str = "Read") -> dict:
        payload = {"tool_name": tool, "tool_input": {"file_path": str(path)}}
        if tool in ("Write", "Edit"):
            payload["tool_input"]["content"] = "x\n"
        return hook("pre-tool-use", repo, host, payload)

    def after(repo: Path, host: str, path: Path, tool: str = "Read") -> dict:
        payload = {"tool_name": tool, "tool_input": {"file_path": str(path)}}
        return hook("post-tool-use", repo, host, payload)

    two.hook, two.read, two.after = hook, read, after
    two.overview = two.store / "docs" / "architecture" / "overview.rst"
    return two


def denied(response: dict) -> str | None:
    out = response.get("hookSpecificOutput") or {}
    if out.get("permissionDecision") == "deny":
        return out.get("permissionDecisionReason")
    return None


def evidence(repo: Path, sid: str) -> list:
    pipeline = PipelineDomain(Manifest.locate(repo)).current(sid)
    return pipeline.evidence.get("docs_read") or []


def gate(repo: Path, sid: str, rule: str = "docs.read_architecture"):
    manifest = Manifest.locate(repo)
    session = SessionDomain(manifest).require(sid)
    pipeline = PipelineDomain(manifest).current(sid)
    ctx = RuleContext(
        manifest=manifest, cwd=repo, session=session.to_dict(), pipeline=pipeline.to_dict()
    )
    return get_rule(rule).fn(ctx)


def passed(verdict) -> bool:
    return verdict.ok and not verdict.skipped


# -- the proof ---------------------------------------------------------------


class TestTheProof:
    def test_the_second_session_is_denied_until_the_first_releases_then_granted(
        self, hosts, capsys
    ):
        # The first read of a free unit takes it in the same hook call.
        assert denied(hosts.read(hosts.widgets, HOST_A, hosts.overview)) is None
        units = lease_record(hosts.store)["units"]
        assert "area:architecture" in units
        holder = units["area:architecture"]["holder"]
        assert lease_record(hosts.store)["leases"][holder]["session"] == "aaaa1111"
        assert [s["unit"] for s in spans(hosts.widgets, "aaaa1111")] == ["area:architecture"]

        # The second session's read is refused, naming the unit and its holder.
        reason = denied(hosts.read(hosts.gadgets, HOST_B, hosts.overview))
        assert reason is not None
        assert "store.docs_lock_held" in reason
        assert "area:architecture" in reason
        assert "aaaa1111" in reason
        assert "invariant docs lock area:architecture --id bbbb2222" in reason
        # A refused read takes nothing and queues nobody.
        assert lease_record(hosts.store)["units"]["area:architecture"]["queue"] == []

        # Queued, it is refused naming its position.
        hosts.at(hosts.gadgets)
        code, out = run_json(capsys, "docs", "lock", "architecture", "--id", "bbbb2222")
        assert code == 0
        assert out["queued"]["area:architecture"]["position"] == 1
        reason = denied(hosts.read(hosts.gadgets, HOST_B, hosts.overview))
        assert "position 1" in reason
        assert "docs lock --wait" in reason

        # The first releases; the unit passes to the second, which is told.
        hosts.at(hosts.widgets)
        code, out = run_json(capsys, "docs", "unlock", "--id", "aaaa1111")
        assert code == 0, out
        assert out["handed"] == {"area:architecture": "bbbb2222"}
        told = hosts.hook("user-prompt-submit", hosts.gadgets, HOST_B, {"prompt": "go on"})
        context = told["hookSpecificOutput"]["additionalContext"]
        assert "docs lock: area:architecture passed to this session" in context
        started = hosts.hook("session-start", hosts.gadgets, HOST_B)
        assert "passed to this session" in started["hookSpecificOutput"]["additionalContext"]

        # Granted: the read passes and is recorded under the lease and unit.
        assert denied(hosts.read(hosts.gadgets, HOST_B, hosts.overview)) is None
        hosts.after(hosts.gadgets, HOST_B, hosts.overview)
        lease = lease_record(hosts.store)["units"]["area:architecture"]["holder"]
        assert evidence(hosts.gadgets, "bbbb2222") == [
            {"path": str(hosts.overview), "lease": lease, "unit": "area:architecture"}
        ]
        assert passed(gate(hosts.gadgets, "bbbb2222"))
        # Its span is open now, so the notice stops.
        told = hosts.hook("user-prompt-submit", hosts.gadgets, HOST_B, {"prompt": "go on"})
        context = told["hookSpecificOutput"]["additionalContext"]
        assert "passed to this session" not in context
        assert "docs lock: holding area:architecture" in context

    def test_a_memory_write_by_a_session_holding_no_unit_is_accepted(self, hosts):
        note = hosts.store / "memory" / "a-note.md"
        response = hosts.read(hosts.gadgets, HOST_B, note, tool="Write")
        assert denied(response) is None, response
        assert lease_record_or_empty(hosts.store)["units"] == {}
        assert DocsLock(Manifest.locate(hosts.gadgets)).lease_of("bbbb2222") is None
        verdict = get_rule("store.docs_lock_held").fn(
            RuleContext(
                manifest=Manifest.locate(hosts.gadgets),
                cwd=hosts.gadgets,
                session=session_record(hosts.gadgets, "bbbb2222"),
                payload={"tool_name": "Write", "tool_input": {"file_path": str(note)}},
            )
        )
        assert verdict.skipped
        assert "ruling 20" in verdict.message


# -- the rule ----------------------------------------------------------------


class TestTheRule:
    def test_an_edit_of_a_held_unit_passes_and_of_a_busy_one_is_refused(self, hosts):
        assert denied(hosts.read(hosts.widgets, HOST_A, hosts.overview, tool="Edit")) is None
        reason = denied(hosts.read(hosts.gadgets, HOST_B, hosts.overview, tool="Write"))
        assert "area:architecture" in reason

    def test_a_refused_grant_writes_nothing(self, hosts):
        assert denied(hosts.read(hosts.widgets, HOST_A, hosts.overview)) is None
        locks = hosts.store / ".git" / "invariant" / "locks" / "docs.json"
        before = (locks.read_bytes(), session_record(hosts.gadgets, "bbbb2222"))
        assert denied(hosts.read(hosts.gadgets, HOST_B, hosts.overview)) is not None
        assert denied(hosts.read(hosts.gadgets, HOST_B, hosts.overview, tool="Edit")) is not None
        after = (locks.read_bytes(), session_record(hosts.gadgets, "bbbb2222"))
        assert after == before

    def test_a_new_file_outside_every_area_is_taken_when_first_written(self, hosts):
        page = hosts.store / "docs" / "new-page.rst"
        assert denied(hosts.read(hosts.widgets, HOST_A, page, tool="Write")) is None
        assert "file:docs/new-page.rst" in lease_record(hosts.store)["units"]

    def test_the_store_config_and_git_dir_are_not_governed(self, hosts):
        for path in (hosts.store / "invariant.yaml", hosts.store / ".git" / "config"):
            assert denied(hosts.read(hosts.gadgets, HOST_B, path)) is None
        assert lease_record_or_empty(hosts.store)["units"] == {}

    def test_a_call_that_resolves_to_no_session_is_ungoverned(self, hosts):
        assert denied(hosts.read(hosts.widgets, HOST_A, hosts.overview)) is None
        response = hosts.read(hosts.gadgets, "host-nobody-owns", hosts.overview)
        assert denied(response) is None

    def test_a_stale_holder_is_named_with_the_takeover(self, hosts):
        assert denied(hosts.read(hosts.widgets, HOST_A, hosts.overview)) is None
        lock_file = hosts.store / ".git" / "invariant" / "locks" / "docs.json"
        record = json.loads(lock_file.read_text())
        for lease in record["leases"].values():
            lease["heartbeat"] = time.time() - 31 * 60
        lock_file.write_text(json.dumps(record))
        reason = denied(hosts.read(hosts.gadgets, HOST_B, hosts.overview))
        assert "stale" in reason
        assert "--take-over area:architecture" in reason

    def test_a_grant_never_breaks_the_global_order(self, hosts, capsys):
        # bbbb waits for area:guidelines, held by aaaa. A read of a free unit
        # that sorts after it would leave bbbb holding a unit sorting after one
        # it waits for, so it is refused, not granted; one that sorts before
        # it adds no wait out of order, so it is granted.
        ruleset = hosts.store / "docs" / "guidelines" / "ruleset.yaml"
        assert denied(hosts.read(hosts.widgets, HOST_A, ruleset)) is None
        hosts.at(hosts.gadgets)
        code, _ = run_json(capsys, "docs", "lock", "guidelines", "--id", "bbbb2222")
        assert code == 0
        notes = hosts.store / "docs" / "notes.rst"
        reason = denied(hosts.read(hosts.gadgets, HOST_B, notes))
        assert reason is not None and "ahead of a unit the session waits for" in reason
        assert "file:docs/notes.rst" not in lease_record(hosts.store)["units"]
        assert denied(hosts.read(hosts.gadgets, HOST_B, hosts.overview)) is None
        assert lease_record(hosts.store)["units"]["area:architecture"]["holder"] == (
            DocsLock(Manifest.locate(hosts.gadgets)).lease_of("bbbb2222")["id"]
        )

    def test_a_guessed_session_is_checked_but_never_granted(self, hosts, capsys):
        # A host bound to nothing, in gadgets' checkout, where one session has
        # no host either: it is the only candidate, so the call resolves to it
        # by a guess. The rule checks it, and no unit is taken for it.
        hosts.at(hosts.gadgets)
        code, text = run(capsys, "session", "start", "unowned", "--id", "dddd4444")
        assert code == 0, text
        reason = denied(hosts.read(hosts.gadgets, "host-unbound", hosts.overview))
        assert reason is not None
        assert "session dddd4444 does not hold area:architecture" in reason
        assert "only for a session the call proves" in reason
        assert lease_record_or_empty(hosts.store)["units"] == {}

    def test_an_overlap_only_a_path_shows_is_refused_naming_the_areas(self, hosts):
        body = yaml.safe_load((hosts.store / "invariant.yaml").read_text(encoding="utf-8"))
        body["store"]["docs_lock"]["areas"] = {"a": ["docs/*.rst"], "b": ["docs/n*"]}
        (hosts.store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
        reason = denied(hosts.read(hosts.gadgets, HOST_B, hosts.store / "docs" / "notes.rst"))
        assert reason is not None and "overlap" in reason


def in_repo(root: Path, name: str) -> Path:
    """A minimal in-repo project: a manifest at its root, its docs, one commit."""
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "invariant.yaml").write_text(
        yaml.safe_dump({"version": 1, "project": {"name": name}}), encoding="utf-8"
    )
    (root / "docs" / "architecture").mkdir(parents=True)
    (root / "docs" / "architecture" / "overview.md").write_text("# o\n", encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "tester")
    git(root, "config", "user.name", "Test")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def lease_record_or_empty(store: Path) -> dict:
    path = store / ".git" / "invariant" / "locks" / "docs.json"
    return json.loads(path.read_text()) if path.exists() else {"units": {}, "leases": {}}


class TestClassify:
    def test_paths_are_placed(self, hosts):
        manifest = Manifest.locate(hosts.gadgets)
        config = doclock.config_of(manifest)
        store = hosts.store
        assert doclock.classify(manifest, config, hosts.gadgets / "conf.py") == (doclock.OUTSIDE, None)
        assert doclock.classify(manifest, config, store / "memory" / "x.md") == (doclock.DATA, None)
        assert doclock.classify(manifest, config, store / "invariant.yaml") == (doclock.UNGOVERNED, None)
        assert doclock.classify(manifest, config, store / ".git" / "HEAD") == (doclock.UNGOVERNED, None)
        assert doclock.classify(manifest, config, store) == (doclock.UNGOVERNED, None)
        assert doclock.classify(manifest, config, hosts.overview) == (
            doclock.GOVERNED, "area:architecture"
        )

    def test_an_area_reaches_files_no_record_names(self, hosts):
        manifest = Manifest.locate(hosts.gadgets)
        config = DocsLockConfig(areas={"lanes": ("methodology/*.yaml",)})
        assert doclock.classify(manifest, config, hosts.store / "methodology" / "x.yaml") == (
            doclock.GOVERNED, "area:lanes"
        )
        assert doclock.classify(manifest, config, hosts.store / "README.md") == (
            doclock.UNGOVERNED, None
        )

    def test_overlays_named_in_a_mapping_are_policy(self, hosts):
        manifest = Manifest.locate(hosts.gadgets)

        class Fake:
            store = manifest.store

            def get(self, key, default=None):
                if key == "records":
                    return {"guidelines": "store"}
                if key == "agents.overlays":
                    return {"feature-architect": str(hosts.store / "overlays" / "fa.md")}
                return None

        assert doclock.policy_paths(Fake()) == [("guidelines", "overlays/fa.md")]


class TestCost:
    """Hooks fire on every tool call: a path the lock does not govern builds
    no lock, and building one asks git nothing."""

    @pytest.fixture
    def no_lock(self, monkeypatch):
        def refuse(*_a, **_k):
            raise AssertionError("a DocsLock was built for an ungoverned path")

        monkeypatch.setattr(doclock, "DocsLock", refuse)

    def test_the_rule_builds_no_lock_for_an_ungoverned_path(self, hosts, no_lock):
        manifest = Manifest.locate(hosts.gadgets)
        session = session_record(hosts.gadgets, "bbbb2222")
        for path in (hosts.store / "memory" / "a.md", hosts.store / "invariant.yaml"):
            verdict = get_rule("store.docs_lock_held").fn(
                RuleContext(
                    manifest=manifest,
                    cwd=hosts.gadgets,
                    session=session,
                    payload={"tool_name": "Read", "tool_input": {"file_path": str(path)}},
                )
            )
            assert verdict.skipped, verdict.message

    def test_the_session_domain_builds_no_lock_for_an_ungoverned_path(self, hosts, no_lock):
        domain = SessionDomain(Manifest.locate(hosts.gadgets))
        note = hosts.store / "memory" / "a.md"
        assert domain.docs_access("bbbb2222", note) is None
        assert domain.docs_read_lease("bbbb2222", note) is None

    def test_a_lock_on_the_store_asks_git_nothing(self, hosts, monkeypatch):
        def refuse(*_a, **_k):
            raise AssertionError("git was asked for the store's common dir")

        monkeypatch.setattr(doclock.git, "common_dir", refuse)
        lock = DocsLock(Manifest.locate(hosts.gadgets))
        assert lock.json.root == (hosts.store / ".git").resolve() / doclock.LOCK_DIR


# -- evidence and the docs gate ----------------------------------------------


class TestEvidence:
    def test_docs_roots_name_the_stores_main_checkout(self, hosts):
        manifest = Manifest.locate(hosts.gadgets)
        assert docs_roots(manifest, hosts.gadgets) == [
            (hosts.store / "docs" / "architecture").resolve()
        ]

    def test_a_read_without_the_lock_does_not_count(self, hosts):
        domain = PipelineDomain(Manifest.locate(hosts.gadgets))
        domain.record_docs_read("bbbb2222", str(hosts.overview))
        domain.record_docs_read(
            "bbbb2222", str(hosts.overview), under={"lease": "nolease", "unit": "area:architecture"}
        )
        verdict = gate(hosts.gadgets, "bbbb2222")
        assert not verdict.ok
        assert "2 architecture doc read(s) in the store were made without the docs lock" in (
            verdict.message
        )
        assert any("invariant docs lock" in f for f in verdict.fixes)

    def test_no_read_at_all_names_the_store(self, hosts):
        verdict = gate(hosts.gadgets, "bbbb2222")
        assert not verdict.ok
        assert "no architecture doc has been read" in verdict.message

    def test_a_read_under_a_lease_the_session_held_still_counts_after_release(
        self, hosts, capsys
    ):
        assert denied(hosts.read(hosts.widgets, HOST_A, hosts.overview)) is None
        hosts.after(hosts.widgets, HOST_A, hosts.overview)
        hosts.at(hosts.widgets)
        code, _ = run_json(capsys, "docs", "unlock", "--id", "aaaa1111")
        assert code == 0
        assert passed(gate(hosts.widgets, "aaaa1111"))

    def test_a_read_of_a_doc_outside_the_lock_is_recorded_as_a_path(self, hosts):
        hosts.after(hosts.gadgets, HOST_B, hosts.gadgets / "conf.py")
        assert evidence(hosts.gadgets, "bbbb2222") == [str(hosts.gadgets / "conf.py")]

    def test_the_lease_row_is_written_once(self, hosts):
        assert denied(hosts.read(hosts.widgets, HOST_A, hosts.overview)) is None
        hosts.after(hosts.widgets, HOST_A, hosts.overview)
        hosts.after(hosts.widgets, HOST_A, hosts.overview)
        assert len(evidence(hosts.widgets, "aaaa1111")) == 1

    def test_docs_read_lease_is_none_for_an_ungoverned_path(self, hosts):
        sessions = SessionDomain(Manifest.locate(hosts.gadgets))
        assert sessions.docs_read_lease("bbbb2222", hosts.store / "invariant.yaml") is None
        assert sessions.docs_read_lease("bbbb2222", hosts.overview) == {
            "lease": None, "unit": "area:architecture"
        }


# -- heartbeat and notice ----------------------------------------------------


class TestHeartbeat:
    def test_a_hook_call_refreshes_the_holders_heartbeat(self, hosts):
        assert denied(hosts.read(hosts.widgets, HOST_A, hosts.overview)) is None
        lock_file = hosts.store / ".git" / "invariant" / "locks" / "docs.json"
        record = json.loads(lock_file.read_text())
        for lease in record["leases"].values():
            lease["heartbeat"] = time.time() - 5 * 60
        lock_file.write_text(json.dumps(record))
        hosts.hook("user-prompt-submit", hosts.widgets, HOST_A, {"prompt": "hello"})
        beat = next(iter(lease_record(hosts.store)["leases"].values()))["heartbeat"]
        assert time.time() - beat < 60

    def test_session_start_writes_no_heartbeat(self, hosts):
        assert denied(hosts.read(hosts.widgets, HOST_A, hosts.overview)) is None
        lock_file = hosts.store / ".git" / "invariant" / "locks" / "docs.json"
        record = json.loads(lock_file.read_text())
        for lease in record["leases"].values():
            lease["heartbeat"] = 1000.0
        lock_file.write_text(json.dumps(record))
        hosts.hook("session-start", hosts.widgets, HOST_A)
        assert next(iter(lease_record(hosts.store)["leases"].values()))["heartbeat"] == 1000.0

    def test_the_heartbeat_is_nothing_without_a_store(self, tmp_path):
        class Bare:
            store = None

        domain = SessionDomain.__new__(SessionDomain)
        domain.manifest = Bare()
        assert domain.docs_heartbeat("x") is False
        assert domain.docs_notice("x") is None
        assert domain.docs_access("x", tmp_path) is None
        assert domain.docs_read_lease("x", tmp_path) is None


# -- routing -----------------------------------------------------------------


class TestRouting:
    def test_store_root_of(self, hosts, tmp_path):
        from invariant.core.stores import store_root_of

        assert store_root_of(hosts.overview) == hosts.store.resolve()
        assert store_root_of(hosts.widgets / "conf.py") is None
        assert store_root_of(tmp_path / "nowhere" / "x") is None
        # A repository whose invariant.yaml is not a store's, or does not parse.
        other = tmp_path / "other"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        (other / "invariant.yaml").write_text("name: not a store\n", encoding="utf-8")
        assert store_root_of(other / "x.txt") is None
        (other / "invariant.yaml").write_text("store: [unclosed\n", encoding="utf-8")
        assert store_root_of(other / "x.txt") is None

    def test_a_read_of_another_projects_store_is_recorded_nowhere_and_said(
        self, hosts, tmp_path, monkeypatch, capsys
    ):
        # A third project, in-repo, whose host reads the widgets store.
        third = in_repo(tmp_path / "third", "third")
        hosts.at(third)
        code, text = run(capsys, "session", "start", "third", "--id", "cccc3333")
        assert code == 0, text
        code, text = run(capsys, "session", "adopt", "host-third", "--id", "cccc3333")
        assert code == 0, text
        code, text = run(capsys, "pipeline", "start", "--task", "t", "--id", "cccc3333")
        assert code == 0, text
        assert denied(hosts.read(third, "host-third", hosts.overview)) is None
        said = hosts.after(third, "host-third", hosts.overview)
        context = said["hookSpecificOutput"]["additionalContext"]
        assert "NOT recorded anywhere" in context
        assert f"the store at {hosts.store.resolve()}" in context
        assert evidence(third, "cccc3333") == []
        assert lease_record_or_empty(hosts.store)["units"] == {}


# -- the oracle ----------------------------------------------------------------


class TestOracle:
    def scan(self, hosts, capsys):
        from invariant.oracle.recommend import assess
        from invariant.oracle.scan import scan_all

        for repo in (hosts.widgets, hosts.gadgets):
            hosts.at(repo)
            code, text = run(capsys, "oracle", "projects", "add", str(repo))
            assert code == 0, text
        from invariant.cli.main import _oracle_cache, _project_index

        snapshots = scan_all(_project_index(), cache=_oracle_cache())
        return assess(snapshots, time.time())

    def rows(self, document) -> dict[str, dict]:
        return {
            row["session_id"]: row
            for project in document["projects"]
            for row in project["rows"]
            if row.get("session_id")
        }

    def test_the_list_shows_holder_queue_and_the_hand_over_alert(self, hosts, capsys):
        from invariant.oracle import panel

        assert denied(hosts.read(hosts.widgets, HOST_A, hosts.overview)) is None
        hosts.at(hosts.gadgets)
        code, _ = run_json(capsys, "docs", "lock", "architecture", "--id", "bbbb2222")
        assert code == 0
        rows = self.rows(self.scan(hosts, capsys))
        held = rows["aaaa1111"]["docs_lock"]["held"]
        assert [(h["unit"], h["queue"], h["new"]) for h in held] == [
            ("area:architecture", ["bbbb2222"], False)
        ]
        assert rows["bbbb2222"]["docs_lock"]["waiting"] == {
            "unit": "area:architecture", "position": 1, "holder": "aaaa1111"
        }
        kinds = {s["kind"]: s for s in rows["bbbb2222"]["signals"]}
        assert kinds["docs_queued"]["alert"] is False

        code, text = run(capsys, "oracle", "list")
        assert code == 0
        assert "docs  holds area:architecture for" in text
        assert "queue: bbbb2222" in text
        assert "docs  queued for area:architecture at position 1 behind aaaa1111" in text

        hosts.at(hosts.widgets)
        code, _ = run_json(capsys, "docs", "unlock", "--id", "aaaa1111")
        assert code == 0
        document = self.scan(hosts, capsys)
        rows = self.rows(document)
        granted = {s["kind"]: s for s in rows["bbbb2222"]["signals"]}["docs_granted"]
        assert granted["alert"] is True
        assert "area:architecture" in granted["detail"]
        assert rows["bbbb2222"]["docs_lock"]["held"][0]["new"] is True

        view = panel.view(document)
        cards = [c for g in view["groups"] for c in g["cards"]]
        chips = [chip["text"] for c in cards for chip in c["chips"]]
        assert "Its docs are free, go on" in chips
        detail = view["details"][panel.row_key(rows["bbbb2222"])]
        assert any(line.startswith("Docs lock · holds area:architecture") for line in
                   detail["technical"]["lines"])

    def test_panel_lines_for_a_queue_and_a_stale_holder(self):
        from invariant.oracle import panel

        row = {"docs_lock": {
            "held": [{"unit": "area:a", "held_seconds": 120, "queue": ["s2", None],
                      "stale": "its heartbeat is 31 min old", "new": False}],
            "waiting": {"unit": "area:b", "position": 2, "holder": "s3"},
        }}
        assert panel._docs_lock_lines(row) == [
            "Docs lock · holds area:a · 2 min · queue s2 · stale: its heartbeat is 31 min old",
            "Docs lock · queued for area:b at position 2 behind s3",
        ]
        assert panel._docs_lock_lines({}) == []

    def test_oracle_list_lines(self):
        from invariant.cli.main import _oracle_docs_lock_lines

        assert _oracle_docs_lock_lines(None) == []
        lines = _oracle_docs_lock_lines({"held": [
            {"unit": "area:a", "held_seconds": 30, "queue": [], "new": True, "stale": "dead"}
        ], "waiting": None})
        assert lines == [
            "         docs  holds area:a for 30s (passed to it; not yet seen); STALE: dead"
        ]

    def test_an_in_repo_project_has_no_views(self, tmp_path):
        from invariant.oracle.scan import docs_lock_views

        class Bare:
            store = None

        assert docs_lock_views(Bare(), [], 0.0) == {}


# -- in-repo projects are unchanged --------------------------------------------


class TestInRepoUnchanged:
    def test_the_rule_is_na_and_reads_stay_paths(self, tmp_path):
        repo = in_repo(tmp_path / "plain", "plain")
        manifest = Manifest.locate(repo)
        assert manifest.store is None
        doc = repo / "docs" / "architecture" / "overview.md"
        verdict = get_rule("store.docs_lock_held").fn(RuleContext(
            manifest=manifest, cwd=repo, session={"id": "x"},
            payload={"tool_name": "Read", "tool_input": {"file_path": str(doc)}},
        ))
        assert verdict.skipped
        assert "in its repository" in verdict.message
        assert docs_roots(manifest, repo) == [(repo / "docs" / "architecture").resolve()]
