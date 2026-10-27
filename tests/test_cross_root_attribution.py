"""Attribution across project roots (ADR-011, BUG_040).

A host session is rooted in project A; the work happens in a live session
worktree of project B. Every test here drives the real ``invariant hook``
entry point with ``CLAUDE_PROJECT_DIR`` naming A and the process standing in
A, and then reads what each project's own state root recorded.

SAFETY: both projects live under ``tmp_path`` and each keeps its state in its
own git common directory; the session variables are removed; the host-session
ids are synthetic. Nothing here touches a real home directory or host.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.cli.main import Context, main
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain

HOST = "host-rooted-in-a"


class _Stdin:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text

    def isatty(self) -> bool:
        return False


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(root), check=True, capture_output=True, text=True
    )


def _project(root: Path, name: str, raw_manifest: str | None = None) -> Path:
    """A minimal invariant project: a manifest, one architecture doc, one commit."""
    root.mkdir(parents=True)
    (root / MANIFEST_RELPATH).parent.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_RELPATH).write_text(
        raw_manifest
        if raw_manifest is not None
        else yaml.safe_dump(
            {
                "version": 1,
                "project": {"name": name},
                "session": {"worktree_dir": ".claude/worktrees"},
            }
        ),
        encoding="utf-8",
    )
    docs = root / "docs" / "architecture"
    docs.mkdir(parents=True)
    (docs / "overview.md").write_text("# overview\n", encoding="utf-8")
    (root / "src.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "master")
    # Not address-shaped: the privacy gate blocks anything that parses as one.
    _git(root, "config", "user.email", "tester")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


def _live_session(project: Path, name: str, *, pipeline: bool = True):
    manifest = Manifest.load(project)
    sessions = SessionDomain(manifest)
    session = sessions.start(name)
    sessions.isolate(session.id, execute=True)
    if pipeline:
        PipelineDomain(manifest).start(session.id, task=f"{name} work")
    return sessions.get(session.id)


def _evidence(project: Path, session_id: str) -> dict:
    pipeline = PipelineDomain(Manifest.load(project)).current(session_id)
    return dict(pipeline.evidence) if pipeline else {}


def _session(project: Path, session_id: str):
    return SessionDomain(Manifest.load(project)).get(session_id)


@pytest.fixture
def world(tmp_path, monkeypatch):
    for var in ("INVARIANT_STATE_ROOT", "INVARIANT_SESSION_ID",
                "INVARIANT_HOST_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    a = _project(tmp_path / "project-a", "host-project")
    b = _project(tmp_path / "project-b", "track-project")
    track = _live_session(b, "track")
    monkeypatch.chdir(a)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
    return {"a": a, "b": b, "track": track, "worktree": Path(track.worktree)}


def _hook(monkeypatch, capsys, event: str, payload: dict) -> dict:
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(payload)))
    assert main(["hook", event]) == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else {}


def _dispatch(prompt: str, agent: str = "feature-architect", host: str = HOST) -> dict:
    return {
        "session_id": host,
        "hook_event_name": "PostToolUse",
        "tool_name": "Task",
        "tool_input": {
            "subagent_type": agent,
            "description": "do the thing",
            "prompt": prompt,
        },
        "tool_response": {"content": "done"},
    }


def _tool(tool: str, path: Path, host: str = HOST, **extra) -> dict:
    payload = {
        "session_id": host,
        "tool_name": tool,
        "tool_input": {"file_path": str(path), **extra},
        "tool_response": {"success": True},
    }
    return payload


def _context_text(response: dict) -> str:
    return str((response.get("hookSpecificOutput") or {}).get("additionalContext", ""))


# -- placed: the call lands on exactly one live foreign session ------------------


class TestPlacedAcrossRoots:
    def test_a_read_inside_the_foreign_worktree_feeds_its_docs_gate(
        self, world, monkeypatch, capsys
    ):
        doc = world["worktree"] / "docs" / "architecture" / "overview.md"
        _hook(monkeypatch, capsys, "post-tool-use", _tool("Read", doc))

        assert _evidence(world["b"], world["track"].id).get("docs_read") == [str(doc)]

    def test_a_read_is_placed_even_when_the_host_owns_a_session_at_home(
        self, world, monkeypatch, capsys
    ):
        home = _live_session(world["a"], "home")
        SessionDomain(Manifest.load(world["a"])).adopt_host_session(home.id, HOST)
        doc = world["worktree"] / "docs" / "architecture" / "overview.md"

        _hook(monkeypatch, capsys, "post-tool-use", _tool("Read", doc))

        assert _evidence(world["b"], world["track"].id).get("docs_read") == [str(doc)]
        assert not _evidence(world["a"], home.id).get("docs_read"), (
            "a read of another project's file was recorded in the host's own "
            "pipeline (BUG_010's junk row)"
        )

    def test_a_dispatch_naming_the_foreign_worktree_reaches_its_pipeline(
        self, world, monkeypatch, capsys
    ):
        response = _hook(
            monkeypatch, capsys, "post-tool-use",
            _dispatch(f"Review the change in {world['worktree']}/src.py."),
        )
        assert response == {}
        assert _evidence(world["b"], world["track"].id).get("agents_run") == [
            "feature-architect"
        ]

    def test_a_brief_naming_an_ungoverned_path_too_is_still_placed(
        self, world, monkeypatch, capsys, tmp_path
    ):
        elsewhere = tmp_path / "scratch" / "notes.txt"
        _hook(
            monkeypatch, capsys, "post-tool-use",
            _dispatch(f"Read {elsewhere} then work inside {world['worktree']}."),
        )
        assert _evidence(world["b"], world["track"].id).get("agents_run") == [
            "feature-architect"
        ]

    def test_a_host_checkout_path_in_the_brief_is_not_a_match(
        self, world, monkeypatch, capsys
    ):
        """Only live worktrees are matches; the host's shared checkout is not one."""
        _hook(
            monkeypatch, capsys, "post-tool-use",
            _dispatch(
                f"Port {world['a']}/src.py into {world['worktree']}/src.py."
            ),
        )
        assert _evidence(world["b"], world["track"].id).get("agents_run") == [
            "feature-architect"
        ]

    def test_an_edit_inside_the_foreign_worktree_is_that_sessions_first_edit(
        self, world, monkeypatch, capsys
    ):
        target = world["worktree"] / "src.py"
        assert _session(world["b"], world["track"].id).state == "isolated"

        _hook(monkeypatch, capsys, "post-tool-use",
              _tool("Edit", target, old_string="1", new_string="2"))

        after = _session(world["b"], world["track"].id)
        assert after.state == "working"
        # D5: no binding crosses roots. Nothing could ever mark it spent.
        assert after.host_session_id is None

    def test_a_write_inside_the_foreign_worktree_is_placed_too(
        self, world, monkeypatch, capsys
    ):
        target = world["worktree"] / "new_module.py"
        _hook(monkeypatch, capsys, "post-tool-use",
              _tool("Write", target, content="y = 2\n"))
        assert _session(world["b"], world["track"].id).state == "working"

    def test_a_subagent_acting_in_the_foreign_worktree_is_recorded_as_actor(
        self, world, monkeypatch, capsys
    ):
        payload = _tool("Edit", world["worktree"] / "src.py",
                        old_string="1", new_string="2")
        payload["agent_type"] = "compliance-reviewer"
        _hook(monkeypatch, capsys, "post-tool-use", payload)

        assert _evidence(world["b"], world["track"].id).get("agents_run") == [
            "compliance-reviewer"
        ]

    def test_the_attention_sidecar_stays_with_the_hosts_project(
        self, world, monkeypatch, capsys
    ):
        from invariant.core.store import JsonStore
        from invariant.oracle.attention import AttentionStore

        # The host is waiting in BOTH state roots; a finished tool call answers
        # the wait, and only the host's own project may see that write.
        stores = {
            key: AttentionStore(JsonStore(Manifest.load(world[key]).state_root))
            for key in ("a", "b")
        }
        for store in stores.values():
            store.wait(HOST, "permission", "may I?")

        doc = world["worktree"] / "docs" / "architecture" / "overview.md"
        _hook(monkeypatch, capsys, "post-tool-use", _tool("Read", doc))

        assert stores["a"].read(HOST).waiting is None
        assert stores["b"].read(HOST).waiting is not None

    def test_pre_tool_use_is_governed_by_the_foreign_project(self, world):
        payload = _tool("Edit", world["worktree"] / "src.py")
        ctx = Context(manifest_start=world["a"], host_session_id=HOST)

        placed = ctx.for_payload(payload)

        assert placed is not ctx
        assert placed.manifest.root == world["b"].resolve()
        assert placed.crossed_from == world["a"].resolve()
        assert placed.cwd == world["worktree"].resolve()
        assert placed.host_session_id is None
        assert placed.unplaced is None
        rule_ctx = placed.rule_context(payload=payload)
        assert rule_ctx.session["id"] == world["track"].id

    def test_the_dispatch_warning_judges_the_foreign_sessions_pipeline(
        self, world, monkeypatch, capsys
    ):
        """`agents.gated_dispatch_has_pipeline` reads the session the brief places.

        B's `track` has a pipeline, so a design dispatch into it is not warned
        about; `idle`, also in B, has none, so one into it is.
        """
        idle = _live_session(world["b"], "idle", pipeline=False)
        pre = {**_dispatch(f"Work inside {world['worktree']}."),
               "hook_event_name": "PreToolUse"}
        pre.pop("tool_response")
        assert "gated_dispatch_has_pipeline" not in json.dumps(
            _hook(monkeypatch, capsys, "pre-tool-use", pre)
        )

        pre["tool_input"] = {**pre["tool_input"],
                             "prompt": f"Work inside {idle.worktree}."}
        warned = json.dumps(_hook(monkeypatch, capsys, "pre-tool-use", pre))
        assert "no pipeline" in warned.lower()

    def test_a_probe_stays_inert_across_roots(self, world):
        from invariant.core.store import InertStore

        payload = _tool("Read", world["worktree"] / "src.py")
        ctx = Context(manifest_start=world["a"], host_session_id=HOST, probe=True)
        assert isinstance(ctx.for_payload(payload).store, InertStore)


# -- not crossing: the ordinary order, unchanged ------------------------------


class TestNotACrossing:
    def test_a_path_in_no_invariant_project_changes_nothing(self, world, tmp_path):
        loose = tmp_path / "loose" / "file.txt"
        ctx = Context(manifest_start=world["a"], host_session_id=HOST)
        assert ctx.for_payload(_tool("Read", loose)) is ctx
        assert ctx.unplaced is None

    def test_a_path_inside_the_host_project_changes_nothing(self, world):
        ctx = Context(manifest_start=world["a"], host_session_id=HOST)
        assert ctx.for_payload(_tool("Read", world["a"] / "src.py")) is ctx
        assert ctx.unplaced is None

    def test_a_linked_worktree_of_the_host_repository_is_home(self, world, tmp_path):
        sibling = tmp_path / "project-a-sibling"
        _git(world["a"], "worktree", "add", "-q", str(sibling), "-b", "side")
        ctx = Context(manifest_start=world["a"], host_session_id=HOST)
        assert ctx.for_payload(_tool("Read", sibling / "src.py")) is ctx
        assert ctx.unplaced is None

    def test_an_explicit_session_id_disables_the_route(self, world, monkeypatch):
        monkeypatch.setenv("INVARIANT_SESSION_ID", "anything")
        ctx = Context(manifest_start=world["a"], host_session_id=HOST)
        assert ctx.for_payload(_tool("Read", world["worktree"] / "src.py")) is ctx

    def test_a_payload_naming_no_path_changes_nothing(self, world):
        ctx = Context(manifest_start=world["a"], host_session_id=HOST)
        assert ctx.for_payload({"tool_name": "Bash", "tool_input": {}}) is ctx
        assert ctx.for_payload({"tool_name": "Bash"}) is ctx
        assert ctx.for_payload(_dispatch("no paths at all")) is ctx

    def test_a_brief_whose_only_match_is_at_home_resolves_at_home(
        self, world, monkeypatch, capsys
    ):
        home = _live_session(world["a"], "home")
        _hook(
            monkeypatch, capsys, "post-tool-use",
            _dispatch(
                f"Work inside {home.worktree}; compare with {world['b']}/src.py."
            ),
        )
        assert _evidence(world["a"], home.id).get("agents_run") == ["feature-architect"]
        assert not _evidence(world["b"], world["track"].id).get("agents_run")


# -- unplaced: recorded nowhere, and said where it matters ------------------------


class TestUnplacedFailsLoudly:
    def test_an_ambiguous_brief_records_nowhere_and_says_so(
        self, world, monkeypatch, capsys
    ):
        home = _live_session(world["a"], "home")
        SessionDomain(Manifest.load(world["a"])).adopt_host_session(home.id, HOST)

        response = _hook(
            monkeypatch, capsys, "post-tool-use",
            _dispatch(f"Work in {home.worktree} and in {world['worktree']}."),
        )

        assert not _evidence(world["a"], home.id).get("agents_run")
        assert not _evidence(world["b"], world["track"].id).get("agents_run")
        text = _context_text(response)
        assert "NOT recorded" in text
        assert "more than one session" in text
        assert world["track"].id in text and home.id in text

    def test_two_foreign_sessions_in_one_brief_are_ambiguous(
        self, world, monkeypatch, capsys
    ):
        other = _live_session(world["b"], "other")
        response = _hook(
            monkeypatch, capsys, "post-tool-use",
            _dispatch(f"Compare {world['worktree']} with {other.worktree}."),
        )
        assert not _evidence(world["b"], world["track"].id).get("agents_run")
        assert not _evidence(world["b"], other.id).get("agents_run")
        assert "more than one session" in _context_text(response)

    def test_no_match_a_brief_naming_the_foreign_main_checkout_records_nowhere(
        self, world, monkeypatch, capsys
    ):
        home = _live_session(world["a"], "home")
        SessionDomain(Manifest.load(world["a"])).adopt_host_session(home.id, HOST)

        response = _hook(
            monkeypatch, capsys, "post-tool-use",
            _dispatch(f"Fix {world['b']}/src.py please."),
        )

        assert not _evidence(world["a"], home.id).get("agents_run"), (
            "a dispatch aimed into another project was banked in the host's own "
            "pipeline by its binding (BUG_010's residue)"
        )
        assert not _evidence(world["b"], world["track"].id).get("agents_run")
        text = _context_text(response)
        assert "NOT recorded" in text
        assert "no live session" in text
        assert str(world["b"].resolve()) in text

    def test_a_torn_down_sessions_worktree_no_longer_places_a_dispatch(
        self, world, monkeypatch, capsys
    ):
        # The record is marked the way the domain suite marks one, and the
        # worktree is deliberately left on disk: the state alone must be what
        # stops the dispatch from being placed there.
        gone = _live_session(world["b"], "gone", pipeline=False)
        sessions = SessionDomain(Manifest.load(world["b"]))
        gone.state = "torn_down"
        sessions._save(gone)
        assert Path(gone.worktree).is_dir()

        response = _hook(
            monkeypatch, capsys, "post-tool-use",
            _dispatch(f"Pick up the work in {gone.worktree}."),
        )

        assert not _evidence(world["b"], world["track"].id).get("agents_run")
        assert "no live session" in _context_text(response)

    def test_two_home_sessions_and_a_foreign_path_are_ambiguous(
        self, world, monkeypatch, capsys
    ):
        first = _live_session(world["a"], "first")
        second = _live_session(world["a"], "second")
        response = _hook(
            monkeypatch, capsys, "post-tool-use",
            _dispatch(
                f"Merge {first.worktree} into {second.worktree}, "
                f"following {world['b']}/src.py."
            ),
        )
        assert not _evidence(world["a"], first.id).get("agents_run")
        assert not _evidence(world["a"], second.id).get("agents_run")
        text = _context_text(response)
        assert "more than one session" in text
        assert str(world["b"].resolve()) in text

    def test_an_unplaced_architecture_read_says_so(self, world, monkeypatch, capsys):
        doc = world["b"] / "docs" / "architecture" / "overview.md"
        response = _hook(monkeypatch, capsys, "post-tool-use", _tool("Read", doc))

        assert not _evidence(world["b"], world["track"].id).get("docs_read")
        text = _context_text(response)
        assert "NOT recorded" in text and str(doc) in text

    def test_an_unplaced_ordinary_read_is_silent(self, world, monkeypatch, capsys):
        home = _live_session(world["a"], "home")
        SessionDomain(Manifest.load(world["a"])).adopt_host_session(home.id, HOST)

        response = _hook(
            monkeypatch, capsys, "post-tool-use", _tool("Read", world["b"] / "src.py")
        )

        assert response == {}
        assert not _evidence(world["a"], home.id).get("docs_read")

    def test_an_unplaced_edit_is_silent_and_moves_nothing(
        self, world, monkeypatch, capsys
    ):
        response = _hook(
            monkeypatch, capsys, "post-tool-use",
            _tool("Edit", world["b"] / "src.py", old_string="1", new_string="2"),
        )
        assert response == {}
        assert _session(world["b"], world["track"].id).state == "isolated"

    def test_an_unplaced_subagent_records_no_actor_anywhere(
        self, world, monkeypatch, capsys
    ):
        home = _live_session(world["a"], "home")
        SessionDomain(Manifest.load(world["a"])).adopt_host_session(home.id, HOST)
        payload = _tool("Edit", world["b"] / "src.py", old_string="1", new_string="2")
        payload["agent_type"] = "feature-architect"

        _hook(monkeypatch, capsys, "post-tool-use", payload)

        assert not _evidence(world["a"], home.id).get("agents_run")
        assert not _evidence(world["b"], world["track"].id).get("agents_run")

    def test_an_unreadable_foreign_manifest_is_unplaced_not_a_crash(
        self, world, monkeypatch, capsys, tmp_path
    ):
        broken = _project(tmp_path / "project-c", "broken", raw_manifest="- not: [a")
        response = _hook(
            monkeypatch, capsys, "post-tool-use",
            _dispatch(f"Work inside {broken}/src.py."),
        )
        text = _context_text(response)
        assert "NOT recorded" in text and "could not be read" in text

    def test_pre_tool_use_for_an_unplaced_call_stays_at_home(self, world):
        ctx = Context(manifest_start=world["a"], host_session_id=HOST)
        placed = ctx.for_payload(_tool("Edit", world["b"] / "src.py"))
        assert placed is ctx
        assert placed.unplaced is not None
        assert placed.unplaced.reason == "unowned"
        assert placed.unplaced.project == world["b"].resolve()
