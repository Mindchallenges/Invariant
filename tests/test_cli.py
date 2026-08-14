"""CLI tests.

The CLI is the library's entire API — hooks call it, agents call it, and a
human debugging a wedged state calls it. Anything untested here is untested
everywhere it matters.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from invariant.cli.main import main


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "docs" / "architecture").mkdir(parents=True)
    (root / "docs" / "architecture" / "overview.md").write_text(
        "# Overview\n\n```mermaid\ngraph TD\n  A-->B\n```\n", encoding="utf-8"
    )
    (root / "app.py").write_text("print(1)\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    monkeypatch.chdir(root)
    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
    return root


def run(*argv) -> int:
    return main(list(argv))


def out(capsys) -> str:
    return capsys.readouterr().out


def payload(capsys) -> dict:
    """The last JSON document printed.

    Several tests run setup commands first, so stdout carries their output too;
    the document under test is the final one.
    """
    text = out(capsys)
    lines = text.splitlines()
    for start in range(len(lines) - 1, -1, -1):
        if not lines[start].lstrip().startswith("{"):
            continue
        try:
            return json.loads("\n".join(lines[start:]))
        except json.JSONDecodeError:
            continue
    raise AssertionError(f"no JSON document in output:\n{text}")


def commit_all(root, message="wip"):
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=root, check=True)


class TestInit:
    def test_creates_everything(self, project, capsys):
        assert run("init", "--ecosystem", "python") == 0
        assert (project / ".claude" / "invariant.yaml").is_file()
        assert (project / ".claude" / "hooks" / "invariant-pre-tool-use.sh").is_file()
        assert (project / ".claude" / "settings.json").is_file()
        assert len(list((project / ".claude" / "agents").glob("*.md"))) == 4

    def test_hooks_are_executable_shims(self, project):
        run("init", "--ecosystem", "python")
        shim = project / ".claude" / "hooks" / "invariant-pre-tool-use.sh"
        assert shim.stat().st_mode & 0o111
        body = shim.read_text(encoding="utf-8")
        assert "hook pre-tool-use" in body  # dispatches this event to the CLI
        assert len(body.splitlines()) < 30  # a shim, not a program

    def test_shim_carries_no_absolute_home_path(self, project):
        """It is committed, so it must not bake in a username."""
        run("init", "--ecosystem", "python")
        for shim in (project / ".claude" / "hooks").glob("invariant-*.sh"):
            body = shim.read_text(encoding="utf-8")
            assert "/Users/" not in body and "/home/" not in body
            assert "$HOME" in body

    def test_shim_finds_a_user_installed_cli(self, project, tmp_path):
        """The case that motivated the resolution order.

        uv and pipx install into a user bin directory that is not on PATH by
        default, so a PATH-only lookup finds nothing on a working install.
        """
        run("init", "--ecosystem", "python")
        fake_home = tmp_path / "home"
        (fake_home / ".local" / "bin").mkdir(parents=True)
        stub = fake_home / ".local" / "bin" / "invariant"
        stub.write_text('#!/usr/bin/env bash\necho "STUB $*"\n', encoding="utf-8")
        stub.chmod(0o755)

        shim = project / ".claude" / "hooks" / "invariant-pre-tool-use.sh"
        proc = subprocess.run(
            ["bash", str(shim)],
            capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(fake_home),
                 "CLAUDE_PROJECT_DIR": str(project)},
        )
        assert proc.stdout.strip() == "STUB hook pre-tool-use"

    def test_shim_is_silent_when_the_library_is_absent(self, project, tmp_path):
        run("init", "--ecosystem", "python")
        shim = project / ".claude" / "hooks" / "invariant-pre-tool-use.sh"
        empty_home = tmp_path / "empty-home"
        empty_home.mkdir()
        proc = subprocess.run(
            ["bash", str(shim)],
            capture_output=True, text=True,
            # HOME must be isolated too: on a machine where the tool IS
            # installed, the real user bin directory would satisfy the lookup
            # and this would test nothing.
            env={"PATH": "/usr/bin:/bin", "HOME": str(empty_home),
                 "CLAUDE_PROJECT_DIR": str(project)},
        )
        # A missing tool must not break every tool call in the project.
        assert proc.returncode == 0
        assert proc.stdout.strip() == "{}"

    def test_dry_run_writes_nothing(self, project, capsys):
        assert run("init", "--dry-run") == 0
        assert "would:" in out(capsys)
        assert not (project / ".claude" / "invariant.yaml").exists()

    def test_refuses_to_clobber(self, project, capsys):
        run("init", "--ecosystem", "python")
        assert run("init") == 2
        assert run("init", "--force") == 0

    def test_state_root_is_gitignored_or_inside_git(self, project):
        run("init", "--ecosystem", "python")
        assert ".claude/invariant/state/" in (project / ".gitignore").read_text()

    def test_detects_the_ecosystem(self, project, capsys):
        (project / "pyproject.toml").write_text("", encoding="utf-8")
        run("--json", "init")
        assert "python" in payload(capsys)["ecosystems"]

    def test_settings_json_is_preserved(self, project):
        settings = project / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"model": "opus", "hooks": {}}), encoding="utf-8")
        run("init", "--ecosystem", "python")
        merged = json.loads(settings.read_text(encoding="utf-8"))
        assert merged["model"] == "opus"
        assert "PreToolUse" in merged["hooks"]

    def test_corrupt_settings_is_refused_rather_than_overwritten(self, project):
        settings = project / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text("{not json", encoding="utf-8")
        assert run("init", "--ecosystem", "python") != 0
        assert settings.read_text(encoding="utf-8") == "{not json"

    def test_wiring_is_idempotent(self, project):
        run("init", "--ecosystem", "python")
        run("init", "--force", "--ecosystem", "python")
        settings = json.loads((project / ".claude" / "settings.json").read_text())
        assert len(settings["hooks"]["PreToolUse"]) == 1


class TestDoctorAndConfig:
    def test_doctor_passes_on_a_fresh_install(self, project, capsys):
        run("init", "--ecosystem", "python")
        run("doctor")
        text = out(capsys)
        assert "hook shims installed" in text
        assert "library agents current" in text

    def test_doctor_reports_agent_drift(self, project, capsys):
        run("init", "--ecosystem", "python")
        agent = next((project / ".claude" / "agents").glob("*.md"))
        agent.write_text("locally edited\n", encoding="utf-8")
        assert run("doctor") == 1
        assert "library agents current" in out(capsys)

    def test_doctor_flags_absent_architecture_docs(self, project, capsys):
        import shutil

        run("init", "--ecosystem", "python")
        shutil.rmtree(project / "docs")
        assert run("doctor") == 1
        assert "docs-first is core policy" in out(capsys)

    def test_config_show_reports_provenance(self, project, capsys):
        run("init", "--ecosystem", "python")
        run("config", "show", "--provenance")
        text = out(capsys)
        assert "[adapter:python]" in text
        assert "[profile:standard]" in text

    def test_global_flag_works_after_the_subcommand(self, project, capsys):
        run("init", "--ecosystem", "python")
        run("config", "show", "--json")
        assert "config" in payload(capsys)


class TestSessionCommands:
    def setup_project(self, project):
        run("init", "--ecosystem", "python")
        commit_all(project, "adopt invariant")

    def test_start_then_status(self, project, capsys):
        self.setup_project(project)
        run("session", "start", "add-widget")
        run("session", "status", "--json")
        assert payload(capsys)["session"]["name"] == "add-widget"

    def test_status_with_no_session(self, project, capsys):
        self.setup_project(project)
        run("session", "status")
        assert "no session is active" in out(capsys)

    def test_isolate_plan_then_execute(self, project, capsys):
        self.setup_project(project)
        run("session", "start", "x")
        run("session", "isolate")
        assert "git worktree add" in out(capsys)
        assert run("session", "isolate", "--execute") == 0
        run("session", "status", "--json")
        assert payload(capsys)["session"]["state"] == "isolated"

    def test_illegal_transition_exits_one(self, project, capsys):
        self.setup_project(project)
        run("session", "start", "x")
        assert run("session", "transition", "pushed") == 1

    def test_files_declaration(self, project, capsys):
        self.setup_project(project)
        run("session", "start", "x")
        run("session", "files", "add", "a.py", "b.py", "--json")
        assert payload(capsys)["files"] == ["a.py", "b.py"]

    def test_list(self, project, capsys):
        self.setup_project(project)
        run("session", "start", "one")
        run("session", "list", "--json")
        assert len(payload(capsys)["sessions"]) == 1


class TestPipelineCommands:
    def setup_pipeline(self, project, monkeypatch):
        run("init", "--ecosystem", "python")
        commit_all(project, "adopt invariant")
        run("session", "start", "x")
        run("session", "isolate", "--execute")
        run("--json", "session", "status")
        return project

    def test_gates_query_does_not_advance(self, project, capsys, monkeypatch):
        self.setup_pipeline(project, monkeypatch)
        run("pipeline", "start", "--task", "t")
        capsys.readouterr()
        run("pipeline", "gates", "--state", "red")
        assert "docs.read_architecture" in out(capsys)
        run("pipeline", "status", "--json")
        assert payload(capsys)["state"] == "designing"

    def test_advance_blocked_then_allowed(self, project, capsys, monkeypatch):
        self.setup_pipeline(project, monkeypatch)
        run("pipeline", "start", "--task", "t")
        assert run("pipeline", "advance", "red") == 1

        doc = project / "docs" / "architecture" / "overview.md"
        run("--json", "session", "status")
        session_id = payload(capsys)["session"]["id"]
        from invariant.core.manifest import Manifest
        from invariant.domains.pipeline import PipelineDomain

        PipelineDomain(Manifest.load(project)).record_docs_read(session_id, str(doc))
        assert run("pipeline", "advance", "red") == 0

    def test_force_requires_a_reason(self, project, capsys, monkeypatch):
        self.setup_pipeline(project, monkeypatch)
        run("pipeline", "start", "--task", "t")
        assert run("pipeline", "advance", "red", "--force") == 2
        assert run("pipeline", "advance", "red", "--force", "--reason", "no docs needed") == 0

    def test_methodology_override(self, project, capsys, monkeypatch):
        self.setup_pipeline(project, monkeypatch)
        run("--json", "pipeline", "start", "--methodology", "chore")
        assert payload(capsys)["methodology"] == "chore"


class TestItemCommands:
    def setup_project(self, project):
        run("init", "--ecosystem", "python")

    def test_new_list_show(self, project, capsys):
        self.setup_project(project)
        run("--json", "item", "new", "bug", "--title", "A defect")
        item = payload(capsys)
        assert item["id"] == "BUG_001" and item["status"] == "filed"

        run("item", "show", "BUG_001")
        assert "A defect" in out(capsys)

    def test_fields_are_stored(self, project, capsys):
        self.setup_project(project)
        run("--json", "item", "new", "bug", "--title", "x",
            "--field", "severity=high", "--field", "batch=P1")
        assert payload(capsys)["fields"] == {"severity": "high", "batch": "P1"}

    def test_transition_gate_blocks_an_unreproduced_fix(self, project):
        self.setup_project(project)
        run("item", "new", "bug", "--title", "x")
        assert run("item", "transition", "BUG_001", "fixed") == 1

    def test_residue_add_and_clear(self, project, capsys):
        self.setup_project(project)
        run("item", "new", "bug", "--title", "x")
        run("--json", "item", "residue", "BUG_001", "--add", "a leg is owed")
        assert payload(capsys)["residue"] == ["a leg is owed"]
        run("--json", "item", "residue", "BUG_001", "--clear", "a leg is owed")
        assert payload(capsys)["residue"] == []

    def test_unknown_item(self, project):
        self.setup_project(project)
        assert run("item", "show", "BUG_999") == 2


class TestMemoryCommands:
    def test_write_check_index(self, project, capsys):
        run("init", "--ecosystem", "python")
        run("memory", "write", "a-fact", "--type", "project", "--description", "d",
            "--body", "The body.")
        assert run("memory", "check") == 0
        run("memory", "check", "--json")
        assert payload(capsys)["count"] == 1

    def test_check_fails_on_a_stale_index(self, project, capsys):
        run("init", "--ecosystem", "python")
        run("memory", "write", "one", "--type", "project", "--description", "d", "--body", "b")
        memory_dir = project / ".claude" / "memory"
        (memory_dir / "two.md").write_text(
            "---\nname: two\ndescription: d2\nmetadata:\n  type: project\n---\n\nb\n",
            encoding="utf-8",
        )
        assert run("memory", "check") == 1
        assert run("memory", "index") == 0
        assert run("memory", "check") == 0


class TestChecksAndPrivacy:
    def test_rules_list_shows_resolved_levels(self, project, capsys):
        run("init", "--ecosystem", "python")
        run("rules", "list")
        text = out(capsys)
        assert "privacy.clean" in text and "block" in text

    def test_privacy_scan_finds_and_redacts(self, project, capsys):
        run("init", "--ecosystem", "python")
        target = project / "docs" / "leak.md"
        target.write_text("mail a.person@example.com\n", encoding="utf-8")
        assert run("privacy", "scan", str(target)) == 1
        text = out(capsys)
        assert "a.person@example.com" not in text
        assert "emails" in text

    def test_privacy_scan_clean(self, project, capsys):
        run("init", "--ecosystem", "python")
        assert run("privacy", "scan", str(project / "app.py")) == 0

    def test_reveal_is_opt_in(self, project, capsys):
        run("init", "--ecosystem", "python")
        target = project / "docs" / "leak.md"
        target.write_text("mail a.person@example.com\n", encoding="utf-8")
        run("privacy", "scan", str(target), "--reveal")
        assert "a.person@example.com" in out(capsys)


class TestHookBridge:
    def test_pre_tool_use_denies_an_unattributed_edit(self, project, capsys, monkeypatch):
        run("init", "--ecosystem", "python")
        commit_all(project, "adopt")
        payload_in = json.dumps(
            {"tool_name": "Write",
             "tool_input": {"file_path": str(project / "app.py"), "content": "x"}}
        )
        monkeypatch.setattr("sys.stdin", _Stdin(payload_in))
        run("hook", "pre-tool-use")
        response = payload(capsys)
        decision = response["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "session" in decision["permissionDecisionReason"]

    def test_hook_is_silent_without_a_manifest(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.stdin", _Stdin("{}"))
        assert run("hook", "pre-tool-use") == 0
        assert payload(capsys) == {}

    def test_malformed_payload_does_not_crash(self, project, capsys, monkeypatch):
        run("init", "--ecosystem", "python")
        monkeypatch.setattr("sys.stdin", _Stdin("not json at all"))
        assert run("hook", "pre-tool-use") == 0

    def test_context_is_empty_without_a_session(self, project, capsys):
        run("init", "--ecosystem", "python")
        assert run("context") == 0

    def test_several_live_sessions_do_not_deadlock_the_hook(
        self, project, capsys, monkeypatch
    ):
        """The regression this whole guard exists for.

        Two sessions registered in the main checkout and neither isolated: the
        directory cannot say which one is calling. That ambiguity used to raise
        out of the hook, and a pre-tool-use hook exiting non-zero is a deny —
        so every tool call in the project was refused, including the
        `session isolate` that would have resolved it. Nothing could reach the
        state that unblocks it. The hook must exit zero and must not deny.
        """
        run("init", "--ecosystem", "python")
        commit_all(project, "adopt")
        run("session", "start", "first")
        run("session", "start", "second")

        monkeypatch.setattr("sys.stdin", _Stdin(json.dumps({"tool_name": "Bash"})))
        assert run("hook", "pre-tool-use") == 0
        decision = payload(capsys).get("hookSpecificOutput", {})
        assert decision.get("permissionDecision") != "deny"

    def test_ambiguity_names_the_sessions_and_the_way_out(self, project, capsys):
        run("init", "--ecosystem", "python")
        run("session", "start", "first")
        run("session", "start", "second")
        assert run("context") == 0
        text = out(capsys)
        assert "first" in text and "second" in text
        # The remedy must be the command that actually resolves it.
        assert "isolate" in text and "--id" in text

    def test_context_accepts_the_id_its_own_error_recommends(self, project, capsys):
        run("init", "--ecosystem", "python")
        run("session", "start", "first")
        run("session", "start", "second")

        capsys.readouterr()
        run("session", "list", "--json")
        listed = payload(capsys)["sessions"]
        target = next(s for s in listed if s["name"] == "second")["id"]

        assert run("context", "--id", target) == 0
        text = out(capsys)
        assert target in text and "second" in text

    def test_a_broken_hook_reports_instead_of_denying(
        self, project, capsys, monkeypatch
    ):
        """Enforcement may fail. It may not fail closed.

        A hook that cannot evaluate has lost the ability to judge, which is not
        the same as having judged 'no'. Losing a gate is recoverable; refusing
        every tool call is not.
        """
        run("init", "--ecosystem", "python")

        def explode(*a, **k):
            raise RuntimeError("rule engine is wedged")

        monkeypatch.setattr(
            "invariant.hosts.claude_code.ClaudeCodeHost.handle", explode
        )
        monkeypatch.setattr("sys.stdin", _Stdin(json.dumps({"tool_name": "Bash"})))
        assert run("hook", "pre-tool-use") == 0
        response = payload(capsys)
        assert response.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
        assert "enforcement is OFF" in json.dumps(response)


class TestStateMaintenance:
    def test_state_show(self, project, capsys):
        run("init", "--ecosystem", "python")
        run("session", "start", "x")
        run("state", "show", "--json")
        assert len(payload(capsys)["sessions"]) == 1

    def test_repair_dry_run(self, project, capsys):
        run("init", "--ecosystem", "python")
        assert run("state", "repair", "--dry-run") == 0
        assert "would repair" in out(capsys)


class _Stdin:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text

    def isatty(self) -> bool:
        return False


def test_no_manifest_names_the_init_command(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["session", "status"]) == 2


class TestHookAttribution:
    """The hook must attribute from the payload, not from the directory."""

    def _payload(self, host_session_id, file_path):
        return json.dumps({
            "session_id": host_session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": str(file_path), "content": "x"},
        })

    def test_the_hook_attributes_the_edit_to_the_right_session(
        self, project, capsys, monkeypatch
    ):
        run("init", "--ecosystem", "python")
        commit_all(project, "adopt")
        run("session", "start", "alpha", "--host-session-id", "aa-1111-11110000aaaa")
        run("session", "start", "beta", "--host-session-id", "bb-2222-22220000bbbb")
        run("session", "isolate", "--execute", "--id", "0000bbbb")
        capsys.readouterr()

        # cwd is the main checkout — ambiguous by directory, resolvable by id.
        from invariant.core.manifest import Manifest
        from invariant.domains.session import SessionDomain

        worktree = SessionDomain(Manifest.load(project)).get("0000bbbb").worktree
        monkeypatch.setattr(
            "sys.stdin", _Stdin(self._payload("bb-2222-22220000bbbb",
                                              Path(worktree) / "app.py"))
        )
        run("hook", "pre-tool-use")
        response = payload(capsys)
        # Attributed to an isolated session editing inside its own worktree:
        # nothing to deny.
        assert response == {} or response.get(
            "hookSpecificOutput", {}
        ).get("permissionDecision") != "deny"

    def test_an_unattributable_edit_is_still_denied(self, project, capsys, monkeypatch):
        run("init", "--ecosystem", "python")
        commit_all(project, "adopt")
        run("session", "start", "alpha", "--host-session-id", "aa-1111-11110000aaaa")
        capsys.readouterr()
        monkeypatch.setattr(
            "sys.stdin",
            _Stdin(self._payload("unknown-9999", project / "app.py")),
        )
        run("hook", "pre-tool-use")
        decision = payload(capsys)["hookSpecificOutput"]["permissionDecision"]
        assert decision == "deny"

    def test_adopt_binds_a_host_id_to_an_existing_session(self, project, capsys):
        run("init", "--ecosystem", "python")
        run("session", "start", "legacy", "--id", "deadbeef")
        capsys.readouterr()
        run("--json", "session", "adopt", "ff-0000-00009999", "--id", "deadbeef")
        assert payload(capsys)["host_session_id"] == "ff-0000-00009999"


class TestAgentsCommand:
    """Repair must not cost the caller their enforcement configuration."""

    def test_install_makes_every_agent_loadable(self, project, capsys):
        assert run("init", "--name", "demo") == 0
        agents = project / ".claude" / "agents"
        (agents / "feature-architect.md").write_text("broken\n", encoding="utf-8")

        assert run("agents", "install", "--json") == 0
        data = payload(capsys)
        assert data["loadable"] is True
        assert (agents / "feature-architect.md").read_text(encoding="utf-8").startswith("---\n")

    def test_install_does_not_touch_the_manifest(self, project):
        """The reason this is not `init --force`.

        `init --force` rewrites the manifest. A project that has tuned its
        profile and rule levels would silently lose all of it while repairing
        an unrelated file.
        """
        assert run("init", "--name", "demo") == 0
        manifest = project / ".claude" / "invariant.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + "\nenforcement:\n  profile: strict\n  rules:\n    test.all_pass: warn\n",
            encoding="utf-8",
        )
        before = manifest.read_text(encoding="utf-8")

        assert run("agents", "install") == 0
        assert manifest.read_text(encoding="utf-8") == before

    def test_install_does_not_touch_settings_or_hooks(self, project):
        assert run("init", "--name", "demo") == 0
        settings = project / ".claude" / "settings.json"
        settings.write_text('{"model": "opus"}', encoding="utf-8")

        assert run("agents", "install") == 0
        assert settings.read_text(encoding="utf-8") == '{"model": "opus"}'

    def test_dry_run_writes_nothing(self, project, capsys):
        assert run("init", "--name", "demo") == 0
        target = project / ".claude" / "agents" / "feature-architect.md"
        target.write_text("broken\n", encoding="utf-8")

        assert run("agents", "install", "--dry-run", "--json") == 0
        assert "feature-architect" in payload(capsys)["would_install"]
        assert target.read_text(encoding="utf-8") == "broken\n"

    def test_list_reports_a_broken_agent_as_unloadable(self, project, capsys):
        assert run("init", "--name", "demo") == 0
        (project / ".claude" / "agents" / "feature-architect.md").write_text(
            "<!-- marker -->\n---\nname: feature-architect\ndescription: d\n---\nb\n",
            encoding="utf-8",
        )
        assert run("agents", "list", "--json") == 0
        rows = {r["agent"]: r for r in payload(capsys)["agents"]}
        assert "UNLOADABLE" in rows["feature-architect"]["status"]
        assert rows["compliance-reviewer"]["status"] == "loadable"

    def test_list_names_the_role_each_agent_answers_for(self, project, capsys):
        assert run("init", "--name", "demo") == 0
        assert run("agents", "list", "--json") == 0
        rows = {r["agent"]: r for r in payload(capsys)["agents"]}
        assert rows["feature-architect"]["role"] == "design"
        assert rows["compliance-reviewer"]["role"] == "review"

    def test_doctor_reports_loadability_as_its_own_row(self, project, capsys):
        """Doctor goes red on this, and not via the currency check.

        The failure that shipped was "library agents current: ok" on a project
        whose agents all failed to load, so the two rows are asserted apart.
        """
        assert run("init", "--name", "demo") == 0
        run("doctor", "--json")  # exits 1 here: commands.build/test are unset
        rows = {c["check"]: c["ok"] for c in payload(capsys)["checks"]}
        assert rows["agents loadable by host"] is True

        (project / ".claude" / "agents" / "feature-architect.md").write_text(
            "<!-- marker -->\n---\nname: feature-architect\ndescription: d\n---\nb\n",
            encoding="utf-8",
        )
        run("doctor", "--json")
        rows = {c["check"]: c["ok"] for c in payload(capsys)["checks"]}
        assert rows["agents loadable by host"] is False


class EditHookHarness:
    """Shared setup for the hook-boundary suites. Not collected on its own."""

    def setup(self, project, capsys):
        assert run("init", "--name", "demo") == 0
        commit_all(project, "invariant")
        worktrees = {}
        for name in ("alpha", "beta"):
            run("session", "start", name, "--json")
            sid = payload(capsys)["session"]["id"]
            run("session", "isolate", "--execute", "--id", sid)
        run("session", "list", "--json")
        for row in payload(capsys)["sessions"]:
            worktrees[row["name"]] = row["worktree"]
        return worktrees

    def hook(self, event, body):
        import io

        stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(body))
        try:
            return main(["hook", event])
        finally:
            sys.stdin = stdin

    def edit_payload(self, path, session_id="host-1111-2222-3333"):
        return {
            "session_id": session_id,
            "tool_name": "Edit",
            "tool_input": {"file_path": str(path), "old_string": "a", "new_string": "b"},
        }


class TestEditAttributionThroughTheHook(EditHookHarness):
    """The blocked-session scenario, reproduced at the hook boundary.

    Several live sessions, the hook running from the main checkout (as this
    host does — it resets cwd between tool calls), no host identifier recorded
    on any session, and an Edit aimed into one session's worktree.
    """

    def test_an_edit_into_a_worktree_is_allowed(self, project, capsys):
        worktrees = self.setup(project, capsys)
        target = Path(worktrees["alpha"]) / "src" / "thing.swift"

        assert self.hook("pre-tool-use", self.edit_payload(target)) == 0
        response = payload(capsys)
        decision = response.get("hookSpecificOutput", {}).get("permissionDecision")
        assert decision != "deny", response

    def test_the_same_edit_is_denied_in_the_shared_checkout(self, project, capsys):
        """The rule still works — this is not attribution turned off."""
        self.setup(project, capsys)
        target = project / "src" / "thing.swift"

        self.hook("pre-tool-use", self.edit_payload(target))
        response = payload(capsys)
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_the_denial_no_longer_prescribes_an_environment_variable(
        self, project, capsys
    ):
        """An Edit call carries no environment, so that advice was unusable."""
        self.setup(project, capsys)
        self.hook("pre-tool-use", self.edit_payload(project / "src" / "f.swift"))
        reason = payload(capsys)["hookSpecificOutput"]["permissionDecisionReason"]
        assert "INVARIANT_SESSION_ID=" not in reason
        assert "aim the edit inside your worktree" in reason

    def test_the_host_mapping_is_learned_from_the_first_edit(self, project, capsys):
        """After one attributed edit, a call carrying no path resolves too.

        The learning is the intent and is kept. What moved is WHEN: post-tool-
        use binds, because that event only fires for a tool that actually ran.
        """
        worktrees = self.setup(project, capsys)
        target = Path(worktrees["beta"]) / "f.swift"
        body = self.edit_payload(target, session_id="host-abc-def")
        self.hook("pre-tool-use", body)
        self.hook("post-tool-use", body)

        assert self.hook("user-prompt-submit", {"session_id": "host-abc-def"}) == 0
        text = out(capsys)
        assert "beta" in text

    def test_an_unattributable_edit_is_still_denied(self, project, capsys):
        """Learning must not degrade into guessing."""
        self.setup(project, capsys)
        self.hook("pre-tool-use", self.edit_payload(project / "app.py"))
        assert payload(capsys)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_an_explicit_id_outranks_the_target(self, project, capsys):
        """Naming a session is still the last word."""
        from invariant.cli.main import Context

        worktrees = self.setup(project, capsys)
        run("session", "list", "--json")
        beta = [s for s in payload(capsys)["sessions"] if s["name"] == "beta"][0]

        ctx = Context(session_id=beta["id"], host_session_id="host-xyz")
        resolved = ctx.session_for_payload(
            {"tool_input": {"file_path": str(Path(worktrees["alpha"]) / "f.swift")}}
        )
        assert resolved.id == beta["id"]

    def test_the_target_decides_when_nothing_is_named(self, project, capsys):
        from invariant.cli.main import Context

        worktrees = self.setup(project, capsys)
        run("session", "list", "--json")
        alpha = [s for s in payload(capsys)["sessions"] if s["name"] == "alpha"][0]

        ctx = Context(host_session_id="host-xyz")
        resolved = ctx.session_for_payload(
            {"tool_input": {"file_path": str(Path(worktrees["alpha"]) / "f.swift")}}
        )
        assert resolved.id == alpha["id"]
        # Resolved, NOT claimed. Adoption here made ownership a side effect of
        # asking a question — see TestAskingIsNotOwning below.
        assert resolved.host_session_id is None

    def test_a_failed_adoption_does_not_deny_the_edit(self, project, capsys, monkeypatch):
        """Learning is an optimisation; it may not acquire veto power.

        If recording the mapping can fail the call, an unwritable state
        directory turns every edit in the project into a denial — strictly
        worse than never learning anything.
        """
        worktrees = self.setup(project, capsys)
        from invariant.core.errors import StateError
        from invariant.domains.session import SessionDomain

        def explode(self, session_id, host_session_id):
            raise StateError("state root is read-only")

        monkeypatch.setattr(SessionDomain, "adopt_host_session", explode)

        target = Path(worktrees["alpha"]) / "f.swift"
        assert self.hook("pre-tool-use", self.edit_payload(target)) == 0
        response = payload(capsys)
        assert response.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"



class TestPublishingIsOptInThroughTheCli:
    def project_with(self, project, push: bool):
        assert run("init", "--name", "demo") == 0
        manifest = project / ".claude" / "invariant.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + f"\nsession:\n  push: {str(push).lower()}\n",
            encoding="utf-8",
        )
        commit_all(project, "adopt")

    def test_push_is_refused_and_names_the_setting(self, project, capsys):
        self.project_with(project, push=False)
        run("session", "start", "x")
        assert run("session", "push") == 2
        message = capsys.readouterr().err
        assert "not part of this project's session lifecycle" in message
        assert "session.push: true" in message
        assert "invariant session teardown" in message

    def test_the_refusal_does_not_depend_on_the_barrier_verdict(self, project, capsys):
        """With publishing off the barrier abstains, so it cannot be the check.

        If `session push` consulted only the barrier it would find no
        objection and publish — which is the opposite of what the setting says.
        """
        self.project_with(project, push=False)
        run("session", "start", "x")
        run("rules", "list", "--json")
        assert run("session", "push") != 0

    def test_enabling_it_makes_the_command_reachable_again(self, project, capsys):
        self.project_with(project, push=True)
        run("session", "start", "x")
        # Refused by the barrier now (not merged), not by the setting.
        assert run("session", "push") != 0
        assert "session lifecycle" not in capsys.readouterr().err

    def test_teardown_reports_what_it_removed(self, project, capsys):
        self.project_with(project, push=False)
        run("session", "start", "x", "--json")
        sid = payload(capsys)["session"]["id"]
        run("session", "isolate", "--execute", "--id", sid)
        run("session", "list", "--json")
        branch = payload(capsys)["sessions"][0]["branch"]
        subprocess.run(
            ["git", "merge", "--ff-only", branch],
            cwd=project, check=True, capture_output=True,
        )
        for state in ("working", "reviewed", "merged"):
            run("session", "transition", state, "--id", sid, "--force",
                "--reason", "test fixture")
        assert run("session", "teardown", "--id", sid) == 0
        text = out(capsys)
        assert "worktree" in text and branch in text


class TestTeardownWillNotRemoveTheGroundUnderTheCaller:
    """BUG_009, through the surface that actually got somebody wedged.

    The domain owns the precondition, but it is only reachable if the CLI
    hands it the caller's working directory — and the CLI is where the
    original defect was left unclosed one layer up.
    """

    def merged_session(self, project, capsys, monkeypatch):
        assert run("init", "--name", "demo") == 0
        commit_all(project, "manifest")
        # A live binding: a host session that has started and not ended, which
        # is what every session driven by an agent host carries.
        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", "host-cli-0000-1111")
        run("session", "start", "wedge", "--json")
        sid = payload(capsys)["session"]["id"]
        run("session", "isolate", "--execute", "--id", sid)
        run("session", "list", "--json")
        entry = payload(capsys)["sessions"][0]
        subprocess.run(
            ["git", "merge", "--ff-only", entry["branch"]],
            cwd=project, check=True, capture_output=True,
        )
        for state in ("working", "reviewed", "merged"):
            run("session", "transition", state, "--id", sid, "--force",
                "--reason", "test fixture")
        return sid, Path(entry["worktree"])

    def test_it_is_refused_from_inside_the_worktree(self, project, capsys, monkeypatch):
        sid, worktree = self.merged_session(project, capsys, monkeypatch)
        monkeypatch.chdir(worktree)

        assert run("session", "teardown", "--id", sid) != 0
        assert worktree.is_dir()
        message = capsys.readouterr().err
        assert "invariant session teardown" in message
        assert "--force" in message

    def test_force_carries_it_through(self, project, capsys, monkeypatch):
        sid, worktree = self.merged_session(project, capsys, monkeypatch)
        monkeypatch.chdir(worktree)

        assert run("session", "teardown", "--id", sid, "--force") == 0
        assert not worktree.exists()

    def test_the_documented_route_from_the_main_checkout_still_works(
        self, project, capsys, monkeypatch
    ):
        """The regression that a liveness-only refusal would have caused."""
        sid, worktree = self.merged_session(project, capsys, monkeypatch)
        assert Path.cwd() == project

        assert run("session", "teardown", "--id", sid) == 0
        assert not worktree.exists()

    def test_keeping_both_removes_nothing_and_says_so(
        self, project, capsys, monkeypatch
    ):
        """A teardown that removed nothing must not report a removal.

        Both flags together, because git refuses to delete a branch a live
        worktree still has checked out — keeping only the worktree fails on
        that unrelated refusal instead.
        """
        sid, worktree = self.merged_session(project, capsys, monkeypatch)

        assert run(
            "session", "teardown", "--id", sid, "--keep-worktree", "--keep-branch"
        ) == 0
        assert worktree.is_dir()
        assert "removed" not in out(capsys).splitlines()[-1]


class TestMergeIsGatedBeforeItRuns:
    """A validation that runs after the irreversible step is a report.

    `session merge` used to advance git and THEN refuse the transition, leaving
    the branch merged on the base while the record still read `isolated` — git
    and the registry disagreeing, which is the phantom-session class arrived at
    from the other end.
    """

    def isolated_session(self, project, capsys):
        assert run("init", "--name", "demo") == 0
        commit_all(project, "adopt")
        run("session", "start", "x", "--json")
        sid = payload(capsys)["session"]["id"]
        run("session", "isolate", "--execute", "--id", sid)
        return sid

    def base_head(self, project):
        return subprocess.run(
            ["git", "rev-parse", "master"], cwd=project, capture_output=True, text=True
        ).stdout.strip()

    def test_an_illegal_transition_refuses_before_touching_git(self, project, capsys):
        sid = self.isolated_session(project, capsys)
        before = self.base_head(project)

        assert run("session", "merge", "--id", sid) != 0
        assert "nothing has been merged" in capsys.readouterr().err
        assert self.base_head(project) == before

    def test_the_refusal_names_the_legal_states(self, project, capsys):
        sid = self.isolated_session(project, capsys)
        run("session", "merge", "--id", sid)
        message = capsys.readouterr().err
        assert "working" in message and "abandoned" in message

    def test_a_legal_merge_still_works(self, project, capsys):
        sid = self.isolated_session(project, capsys)
        for state in ("working", "reviewed"):
            run("session", "transition", state, "--id", sid, "--force",
                "--reason", "test fixture")
        assert run("session", "merge", "--id", sid) == 0


class TestIdIsAcceptedConsistently:
    """Every ambiguity message tells the caller to pass --id; a subcommand that
    then rejects it makes the advice a dead end."""

    def setup_session(self, project, capsys):
        assert run("init", "--name", "demo") == 0
        commit_all(project, "adopt")
        run("session", "start", "x", "--json")
        return payload(capsys)["session"]["id"]

    def test_status_accepts_id(self, project, capsys):
        sid = self.setup_session(project, capsys)
        assert run("session", "status", "--id", sid, "--json") == 0
        assert payload(capsys)["session"]["id"] == sid

    def test_files_accepts_id(self, project, capsys):
        sid = self.setup_session(project, capsys)
        assert run("session", "files", "add", "a.py", "--id", sid, "--json") == 0
        assert "a.py" in payload(capsys)["files"]


class TestSessionReviewExists:
    """It was documented in CLAUDE.md and the CLI reference, and implemented
    nowhere. A doc-promised check that was never written is the exact failure
    this library exists to make impossible."""

    def working_session(self, project, capsys):
        assert run("init", "--name", "demo") == 0
        commit_all(project, "adopt")
        run("session", "start", "x", "--json")
        sid = payload(capsys)["session"]["id"]
        run("session", "isolate", "--execute", "--id", sid)
        run("session", "transition", "working", "--id", sid, "--force", "--reason", "fixture")
        return sid

    def test_the_command_exists(self, project, capsys):
        assert run("session", "review", "--id", self.working_session(project, capsys)) == 0

    def test_it_advances_the_session(self, project, capsys):
        sid = self.working_session(project, capsys)
        run("session", "review", "--id", sid)
        run("session", "status", "--id", sid, "--json")
        assert payload(capsys)["session"]["state"] == "reviewed"

    def test_it_refuses_from_a_state_that_cannot_review(self, project, capsys):
        assert run("init", "--name", "demo") == 0
        commit_all(project, "adopt")
        run("session", "start", "x", "--json")
        sid = payload(capsys)["session"]["id"]
        assert run("session", "review", "--id", sid) != 0
        assert "not legal" in capsys.readouterr().err


class TestItemVocabularyCheck:
    """A project whose backlog says open/fixed while the machine says
    filed/triaged has entries invisible to every query keyed off its own
    vocabulary — and nothing said so. The machine is already pluggable via
    methodology.extra_machines, so this was a mismatch nobody was told about
    rather than a missing feature."""

    def project_with_item(self, project, status):
        assert run("init", "--name", "demo") == 0
        backlog = project / "docs" / "backlog"
        backlog.mkdir(parents=True, exist_ok=True)
        (backlog / "BUG_001.md").write_text(
            f"---\nid: BUG_001\ntype: bug\ntitle: A bug\nstatus: {status}\n---\n\nBody.\n",
            encoding="utf-8",
        )

    def row(self, capsys):
        return {c["check"]: c for c in payload(capsys)["checks"]}

    def test_a_foreign_status_is_reported(self, project, capsys):
        self.project_with_item(project, "investigating")
        run("doctor", "--json")
        row = self.row(capsys)["item statuses match their machine"]
        assert row["ok"] is False
        assert "investigating" in row["detail"]

    def test_a_status_from_the_machine_passes(self, project, capsys):
        self.project_with_item(project, "filed")
        run("doctor", "--json")
        assert self.row(capsys)["item statuses match their machine"]["ok"] is True

    def test_the_check_names_how_to_fix_it(self, project, capsys):
        self.project_with_item(project, "investigating")
        run("doctor", "--json")
        detail = self.row(capsys)["item statuses match their machine"]["detail"]
        assert "extra_machines" in detail


class TestTheMergeCommandEvaluatesItsGates:
    """`merged`'s entry gates were declared and never read.

    `session merge` checked only that the edge existed, then ran git. The merge
    drives git through a subprocess rather than a tool call, so the pre-tool-use
    hook never sees it either — between the two, the irreversible step was the
    least guarded one in the whole lifecycle.

    Every assertion here is made against a project whose profile actually
    blocks. The default profile leaves `pipeline.complete` advisory, so a suite
    written against the default would pass whether or not the gate was wired at
    all — which is how a gate comes to look tested while being inert.
    """

    def strict_project(self, project, capsys):
        assert run("init", "--name", "demo") == 0
        manifest = project / ".claude" / "invariant.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + "\nenforcement:\n  profile: strict\n",
            encoding="utf-8",
        )
        commit_all(project, "adopt")
        run("session", "start", "x", "--json")
        sid = payload(capsys)["session"]["id"]
        run("session", "isolate", "--execute", "--id", sid)
        for state in ("working", "reviewed"):
            run("session", "transition", state, "--id", sid, "--force",
                "--reason", "test fixture")
        return sid

    def base_head(self, project):
        return subprocess.run(
            ["git", "rev-parse", "master"], cwd=project, capture_output=True, text=True
        ).stdout.strip()

    def test_a_refusing_gate_stops_the_merge(self, project, capsys):
        """The known positive: legal edge, no pipeline, must not merge."""
        sid = self.strict_project(project, capsys)
        before = self.base_head(project)

        assert run("session", "merge", "--id", sid) != 0
        assert self.base_head(project) == before

    def test_the_refusal_names_the_gate_and_says_nothing_merged(
        self, project, capsys
    ):
        sid = self.strict_project(project, capsys)
        run("session", "merge", "--id", sid)
        captured = capsys.readouterr()
        message = captured.err + captured.out
        assert "pipeline.complete" in message
        assert "nothing has been merged" in message

    def test_force_records_the_override_rather_than_hiding_it(
        self, project, capsys
    ):
        """A gate with no honest exit gets worked around instead of obeyed."""
        sid = self.strict_project(project, capsys)
        assert run("session", "merge", "--id", sid, "--force") == 0


class TestHooksAreInstallableWithoutRewritingTheManifest:
    """A library that gains an event must be able to ship it to a live project.

    Until this existed the shim was simply absent, enforcement for that event
    was silently off, and `doctor`'s only repair was `init --force` — which
    rewrites the manifest and discards every considered downgrade the project
    recorded. Nobody trades their configuration for a hook, so the honest
    outcome was that the hook never got installed.
    """

    def adopted(self, project):
        assert run("init", "--name", "demo") == 0
        manifest = project / ".claude" / "invariant.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + "\nenforcement:\n  profile: strict\n  rules:\n    test.all_pass: warn\n",
            encoding="utf-8",
        )
        return manifest

    def test_a_missing_shim_is_restored(self, project, capsys):
        self.adopted(project)
        shim = project / ".claude" / "hooks" / "invariant-stop.sh"
        shim.unlink()
        assert run("hooks", "install") == 0
        assert shim.is_file()

    def test_the_projects_configuration_survives(self, project, capsys):
        """The whole reason this is not `init --force`."""
        manifest = self.adopted(project)
        before = manifest.read_text(encoding="utf-8")
        (project / ".claude" / "hooks" / "invariant-stop.sh").unlink()
        run("hooks", "install")
        assert manifest.read_text(encoding="utf-8") == before
        assert "test.all_pass: warn" in before

    def test_settings_gain_the_new_event(self, project, capsys):
        self.adopted(project)
        settings = project / ".claude" / "settings.json"
        data = json.loads(settings.read_text(encoding="utf-8"))
        data["hooks"].pop("Stop", None)
        settings.write_text(json.dumps(data), encoding="utf-8")

        run("hooks", "install")
        assert "invariant-stop.sh" in settings.read_text(encoding="utf-8")

    def test_doctor_names_this_repair_rather_than_init_force(self, project, capsys):
        """A failure with no safe repair gets ignored, which is worse than none."""
        self.adopted(project)
        (project / ".claude" / "hooks" / "invariant-stop.sh").unlink()
        run("doctor")
        text = out(capsys)
        assert "invariant hooks install" in text
        assert "stop" in text


class TestAskingIsNotOwning(EditHookHarness):
    """Ownership must not be a side effect of asking whether an edit is allowed.

    Adoption used to run inside pre-tool-use rule evaluation, so it fired for
    EVERY evaluated call — including ones whose verdict was `deny`, and
    including a diagnostic that never touched a file. One stray call aimed into
    an unowned worktree transferred a peer's session to whoever made it,
    silently, with no way for the real owner to reclaim it.

    This was found the way these things are found: a probe run to diagnose
    something else bound a live session to a host named `probe-xyz`, which then
    changed which session the next real turn appeared to own.
    """

    def owner_of(self, project, name, capsys):
        run("session", "list", "--json")
        row = [s for s in payload(capsys)["sessions"] if s["name"] == name][0]
        state = json.loads(
            (project / ".git" / "invariant" / "state" / "sessions" / f"{row['id']}.json")
            .read_text(encoding="utf-8")
        )
        return state.get("host_session_id")

    def test_a_probe_that_never_edits_claims_nothing(self, project, capsys):
        """The exact call that corrupted real state."""
        worktrees = self.setup(project, capsys)
        target = Path(worktrees["alpha"]) / "f.swift"
        self.hook("pre-tool-use", self.edit_payload(target, session_id="probe-xyz"))

        assert self.owner_of(project, "alpha", capsys) is None

    def test_a_denied_edit_claims_nothing(self, project, capsys):
        """A refused call is evidence of nothing at all."""
        self.setup(project, capsys)
        self.hook(
            "pre-tool-use",
            self.edit_payload(project / "src" / "f.swift", session_id="host-deny"),
        )
        assert self.owner_of(project, "alpha", capsys) is None
        assert self.owner_of(project, "beta", capsys) is None

    def test_an_edit_that_ran_does_claim_it(self, project, capsys):
        """The learning is kept — it just needs the tool to have run."""
        worktrees = self.setup(project, capsys)
        body = self.edit_payload(
            Path(worktrees["alpha"]) / "f.swift", session_id="host-real"
        )
        self.hook("pre-tool-use", body)
        self.hook("post-tool-use", body)

        assert self.owner_of(project, "alpha", capsys) == "host-real"

    def test_a_peer_cannot_take_an_already_owned_session(self, project, capsys):
        worktrees = self.setup(project, capsys)
        body = self.edit_payload(
            Path(worktrees["alpha"]) / "f.swift", session_id="host-real"
        )
        self.hook("pre-tool-use", body)
        self.hook("post-tool-use", body)

        stolen = self.edit_payload(
            Path(worktrees["alpha"]) / "g.swift", session_id="host-peer"
        )
        self.hook("post-tool-use", stolen)
        assert self.owner_of(project, "alpha", capsys) == "host-real"
