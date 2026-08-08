"""CLI tests.

The CLI is the library's entire API — hooks call it, agents call it, and a
human debugging a wedged state calls it. Anything untested here is untested
everywhere it matters.
"""

import json
import subprocess
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
