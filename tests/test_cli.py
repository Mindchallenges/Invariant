"""CLI tests.

The CLI is the library's entire API — hooks call it, agents call it, and a
human debugging a wedged state calls it. Anything untested here is untested
everywhere it matters.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from invariant.cli.main import main, report_verdicts, worth_reporting
from invariant.core.manifest import Level
from invariant.core.rules import Evaluated, Verdict


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
        # The row names the remedy. "docs-first is core policy" told a fresh
        # adopter what the rule was and nothing about how to satisfy it, and
        # the gate it stood for blocks the first `pipeline advance`.
        text = out(capsys)
        assert "overview.md" in text
        assert "invariant init" in text
        assert "enforcement.rules.docs.read_architecture: warn" in text

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
        # A session name carries its id, so the name is an address.
        session = payload(capsys)["session"]
        assert session["name"] == f"add-widget-{session['id']}"

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

    def test_methodology_override_does_not_wedge_the_pipeline(
        self, project, capsys, monkeypatch
    ):
        """AUD-02: the override was honored by `start` and by nothing after it.

        Every later invocation loaded the manifest-default machine and asked it
        about a state it does not have, which errored `status`, `gates` and
        `advance` — and, through the hook's fail-open path, turned all
        enforcement off for the host session.
        """
        self.setup_pipeline(project, monkeypatch)
        run("pipeline", "start", "--methodology", "chore")
        assert run("pipeline", "status") == 0
        assert run("pipeline", "gates") == 0
        capsys.readouterr()
        run("--json", "pipeline", "status")
        assert payload(capsys)["state"] == "doing"
        assert run("pipeline", "advance", "reviewing") == 0


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

    def test_hook_without_a_manifest_reports_degraded_not_silence(
        self, tmp_path, capsys, monkeypatch
    ):
        """AUD-04, second half: `{}` here read as "the gate passed".

        A shim only exists where `init` installed it, so a hook that finds no
        manifest is misconfigured, not innocent — and until this change it
        returned an empty verdict with no degraded marker, which is enforcement
        silently off. It must still exit zero and must not deny (a missing
        manifest may never wedge the host), but the response has to say that
        nothing was checked.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.stdin", _Stdin("{}"))
        assert run("hook", "pre-tool-use") == 0
        response = payload(capsys)
        assert response.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
        assert response.get("invariantDegraded")
        assert "enforcement is OFF" in json.dumps(response)

    def test_hook_resolves_the_manifest_from_the_project_dir(
        self, project, capsys, monkeypatch
    ):
        """AUD-04: a hook firing with cwd outside the project must still enforce.

        The host guarantees CLAUDE_PROJECT_DIR to its hooks; the process
        working directory it does not. Resolving from cwd alone meant a hook
        run anywhere else found no manifest and silently disabled every gate.
        """
        run("init", "--ecosystem", "python")
        commit_all(project, "adopt")
        elsewhere = project.parent / "elsewhere"
        elsewhere.mkdir(exist_ok=True)
        monkeypatch.chdir(elsewhere)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
        payload_in = json.dumps(
            {"tool_name": "Write",
             "tool_input": {"file_path": str(project / "app.py"), "content": "x"}}
        )
        monkeypatch.setattr("sys.stdin", _Stdin(payload_in))
        assert run("hook", "pre-tool-use") == 0
        decision = payload(capsys)["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"

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
        target = next(s for s in listed if s["name"].startswith("second-"))["id"]

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
            # Keyed on the bare task slug; the recorded name carries the id.
            worktrees[row["name"].rsplit("-", 1)[0]] = row["worktree"]
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
        beta = [s for s in payload(capsys)["sessions"]
                if s["name"].startswith("beta-")][0]

        ctx = Context(session_id=beta["id"], host_session_id="host-xyz")
        resolved = ctx.session_for_payload(
            {"tool_input": {"file_path": str(Path(worktrees["alpha"]) / "f.swift")}}
        )
        assert resolved.id == beta["id"]

    def test_the_target_decides_when_nothing_is_named(self, project, capsys):
        from invariant.cli.main import Context

        worktrees = self.setup(project, capsys)
        run("session", "list", "--json")
        alpha = [s for s in payload(capsys)["sessions"]
                 if s["name"].startswith("alpha-")][0]

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
        """The refusal is the machine's own gate, not a hardcoded ``if``.

        `pushed`'s entry gates carried `session.push_enabled` and the command
        never read them — the policy lived twice, once in the machine and once
        in the CLI, and only the copy the machine did not own ever ran
        (AUD-05). The gate's verdict carries the same remedies the hardcoded
        message did.
        """
        self.project_with(project, push=False)
        run("session", "start", "x")
        assert run("session", "push") == 1
        captured = capsys.readouterr()
        message = captured.err + captured.out
        assert "session.push_enabled" in message
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
        captured = capsys.readouterr()
        assert "session lifecycle" not in captured.err + captured.out

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


class TestGenericTransitionEvaluatesGates:
    """AUD-05: `session transition reviewed` then `merged` used to walk the
    lifecycle with zero gates, because only `session review` and `session
    merge` read the machine's gate lists. The gates now live in `_advance`,
    which every route passes through."""

    def strict_working_session(self, project, capsys):
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
        run("session", "transition", "working", "--id", sid)
        return sid

    def test_the_transition_is_refused_by_a_blocking_gate(self, project, capsys):
        sid = self.strict_working_session(project, capsys)
        assert run("session", "transition", "reviewed", "--id", sid) == 1
        captured = capsys.readouterr()
        assert "pipeline.complete" in captured.err + captured.out
        run("session", "status", "--id", sid, "--json")
        assert payload(capsys)["session"]["state"] == "working"

    def test_force_still_works_and_records_the_override(self, project, capsys):
        sid = self.strict_working_session(project, capsys)
        assert run(
            "session", "transition", "reviewed", "--id", sid,
            "--force", "--reason", "fixture",
        ) == 0
        run("session", "status", "--id", sid, "--json")
        overrides = payload(capsys)["session"]["overrides"]
        assert overrides and "pipeline.complete" in overrides[-1]["gates"]


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
        row = [s for s in payload(capsys)["sessions"]
               if s["name"].startswith(f"{name}-")][0]
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


# -- BUG_020: a block-level N/A must reach the reader -------------------


class TestSkippedGateVisibility:
    """A gate that did not apply is not a gate that passed.

    `pipeline advance` dropped every skipped verdict unless `--verbose`, so a
    BLOCKING gate going inert was indistinguishable from one that passed. That
    is how BUG_012's inert docs gates survived: the tool's own output was, by
    construction, incapable of saying a gate it was told to enforce never ran.
    """

    def evaluated(self, level, rule_id="docs.updated"):
        return Evaluated(
            rule_id=rule_id, level=level, verdict=Verdict.na("nothing to check")
        )

    def report(self, verdicts, capsys, verbose=False):
        report_verdicts(verdicts, argparse.Namespace(json=False, verbose=verbose))
        return capsys.readouterr().err

    def test_a_skipped_blocking_gate_is_reported_without_verbose(self, capsys):
        err = self.report([self.evaluated(Level.BLOCK)], capsys)
        assert "docs.updated" in err

    def test_a_skipped_warning_gate_stays_behind_verbose(self, capsys):
        assert self.report([self.evaluated(Level.WARN, "privacy.clean")], capsys) == ""

    def test_verbose_still_shows_a_skipped_warning_gate(self, capsys):
        err = self.report(
            [self.evaluated(Level.WARN, "privacy.clean")], capsys, verbose=True
        )
        assert "privacy.clean" in err

    def test_a_skipped_blocking_gate_is_not_mistaken_for_a_pass(self, capsys):
        err = self.report([self.evaluated(Level.BLOCK)], capsys)
        assert "[ok]" not in err

    def test_the_level_of_a_skipped_gate_reaches_the_reader(self):
        assert "BLOCK" in self.evaluated(Level.BLOCK).render()
        assert "BLOCK" not in self.evaluated(Level.WARN).render()

    def test_a_skipped_off_gate_stays_behind_verbose(self, capsys):
        assert self.report([self.evaluated(Level.OFF, "x.y")], capsys) == ""


class TestAssetsCommands:
    """`assets list` and `assets install`, which had no tests at all.

    They are the repair path for a host that cannot load an agent file, and the
    only place ownership is reported — a project's own file is not drift and
    must never be named as such. Both distinctions are invisible to every other
    command, so nothing else would catch them regressing.
    """

    def initialised(self, project, capsys):
        run("init", "--ecosystem", "python")
        capsys.readouterr()
        return project

    def rows(self, capsys):
        return payload(capsys)["assets"]

    def test_installed_assets_are_listed_with_their_kind_and_owner(
        self, project, capsys
    ):
        self.initialised(project, capsys)
        assert run("assets", "list", "--json") == 0
        rows = self.rows(capsys)
        assert rows, "init installed no assets"
        assert {r["kind"] for r in rows} <= {"agent", "command", "skill"}
        assert all(r["owner"] == "library" for r in rows)

    def test_a_library_asset_reports_itself_loadable(self, project, capsys):
        self.initialised(project, capsys)
        run("assets", "list", "--json")
        assert all(r["status"] == "loadable" for r in self.rows(capsys))

    def test_the_kind_filter_narrows_the_listing(self, project, capsys):
        """`init` installs agents and no other kind here, so the filter is
        pinned by what it EXCLUDES rather than by a count: a project-owned
        command must not appear in an agent-filtered listing."""
        self.initialised(project, capsys)
        commands = project / ".claude" / "commands"
        commands.mkdir(parents=True, exist_ok=True)
        (commands / "mine.md").write_text("body\n", encoding="utf-8")

        run("assets", "list", "--json")
        every = {r["name"] for r in self.rows(capsys)}
        assert "mine" in every

        run("assets", "list", "--kind", "agent", "--json")
        agents = self.rows(capsys)
        assert agents and all(r["kind"] == "agent" for r in agents)
        assert "mine" not in {r["name"] for r in agents}
        assert len(agents) < len(every)

    def test_a_project_owned_file_is_reported_as_the_projects_not_as_drift(
        self, project, capsys
    ):
        """The distinction the command exists for. A file the project wrote is
        not a library copy that drifted, and calling it drift invites someone
        to 'repair' it by overwriting work nobody asked to lose."""
        self.initialised(project, capsys)
        mine = project / ".claude" / "agents" / "mine.md"
        mine.write_text("---\nname: mine\ndescription: d\n---\n\nbody\n", encoding="utf-8")

        run("assets", "list", "--json")
        owners = {r["name"]: r["owner"] for r in self.rows(capsys)}
        assert owners["mine"] == "project"

    def test_an_unloadable_asset_is_named_with_its_problem(self, project, capsys):
        """Loadability and currency are separate checks, and this is the one
        that answers "will the host read this at all"."""
        self.initialised(project, capsys)
        broken = project / ".claude" / "agents" / "broken.md"
        broken.write_text("no frontmatter here at all\n", encoding="utf-8")

        run("assets", "list", "--json")
        statuses = {r["name"]: r["status"] for r in self.rows(capsys)}
        assert statuses["broken"].startswith("UNLOADABLE — ")

    def test_listing_an_uninitialised_project_says_none_rather_than_failing(
        self, project, capsys
    ):
        run("init", "--ecosystem", "python")
        for kind in ("agents", "commands", "skills"):
            target = project / ".claude" / kind
            if target.is_dir():
                for child in sorted(target.rglob("*")):
                    if child.is_file():
                        child.unlink()
        capsys.readouterr()
        assert run("assets", "list") == 0
        assert "(none installed)" in out(capsys)

    def test_install_reinstalls_the_library_assets(self, project, capsys):
        self.initialised(project, capsys)
        agents = project / ".claude" / "agents"
        removed = min(agents.glob("*.md"))
        removed.unlink()

        assert run("assets", "install", "--json") == 0
        assert payload(capsys)["installed"]
        assert removed.is_file()

    def test_install_reports_loadable_when_nothing_is_broken(self, project, capsys):
        self.initialised(project, capsys)
        assert run("assets", "install", "--json") == 0
        assert payload(capsys)["loadable"] is True

    def test_a_dry_run_changes_nothing(self, project, capsys):
        self.initialised(project, capsys)
        agents = project / ".claude" / "agents"
        removed = min(agents.glob("*.md"))
        removed.unlink()

        assert run("assets", "install", "--dry-run", "--json") == 0
        assert payload(capsys)["dry_run"] is True
        assert not removed.is_file(), "a dry run reinstalled an asset"

    def test_install_narrowed_to_one_kind(self, project, capsys):
        self.initialised(project, capsys)
        assert run("assets", "install", "--kind", "agent", "--json") == 0
        installed = payload(capsys)["installed"]
        assert installed and all("agent" in str(i) for i in installed)


class TestCheckReportsTheSameWayAdvanceDoes:
    """`check` kept its own copy of the skipped-verdict filter, and it drifted.

    The copy read `args.verbose` directly while the `check` subparser defines
    no such flag, so the command raised `AttributeError` on every invocation —
    a whole CLI command down, and invisible because nothing exercised it. That
    is the second-copy failure this codebase already names elsewhere: "keeping
    a second copy here is exactly how they came to be wrong while this one was
    right". Both paths now share one predicate.
    """

    def test_check_runs_at_all(self, project, capsys):
        run("init", "--ecosystem", "python")
        capsys.readouterr()
        assert run("check") in (0, 1)

    def test_check_honours_the_same_skip_rule_as_report_verdicts(self):
        """A blocking skip is reported; an advisory skip waits for --verbose."""
        blocking = Evaluated("a", Level.BLOCK, Verdict.na("nothing here"))
        advisory = Evaluated("b", Level.WARN, Verdict.na("nothing here"))
        assert worth_reporting(blocking, verbose=False)
        assert not worth_reporting(advisory, verbose=False)
        assert worth_reporting(advisory, verbose=True)

    def test_a_pass_is_quiet_unless_verbose(self):
        passed = Evaluated("c", Level.BLOCK, Verdict.clean("all good"))
        assert not worth_reporting(passed, verbose=False)
        assert worth_reporting(passed, verbose=True)

    def test_a_failure_is_always_reported(self):
        for level in (Level.BLOCK, Level.WARN):
            assert worth_reporting(Evaluated("d", level, Verdict.fail("nope")), verbose=False)


class TestRepeatedSelectorsSurviveTheParser:
    """BUG_031 — `--selector` was declared without `action="append"`, so
    argparse's documented behaviour for a repeated single-value option
    applied: the last occurrence silently overwrote the earlier ones. The
    run then reported a healthy green for a scope nobody asked for.

    `item new --field` in the same parser has carried `action="append"` for
    exactly this reason, so this is one option declared without the idiom
    rather than a missing capability.
    """

    def test_both_selectors_reach_the_command(self):
        from invariant.cli.main import build_parser

        args = build_parser().parse_args(
            ["pipeline", "test", "--selector", "A", "--selector", "B"]
        )
        assert args.selector == ["A", "B"]

    def test_one_selector_is_still_a_list(self):
        from invariant.cli.main import build_parser

        assert build_parser().parse_args(["pipeline", "test", "--selector", "A"]).selector == ["A"]


# -- a session that outlives its host, at the surface ------------------
#
# The domain and the rules landed in two earlier increments; these are the
# commands over them. The rules' `fixes` name three of these commands by
# spelling, so the first test in this block is the one that keeps a fix from
# being advice nobody can take.


def spend_the_binding(project, host_session_id):
    """What `SessionEnd` does, without a hook.

    A session is resumable exactly when its binding is SPENT — the complement
    of the attributable set — so every test here has to get a session into that
    state, and the hook route would drag a payload fixture along for a fact the
    domain already owns.
    """
    from invariant.core.manifest import Manifest
    from invariant.core.store import JsonStore
    from invariant.domains.session import SessionDomain

    manifest = Manifest.load(project)
    return SessionDomain(manifest, JsonStore(manifest.state_root)).end_host_session(
        host_session_id
    )


class ParkedSessionHarness:
    """A session with a spent binding, which is what `resume` picks up."""

    def parked(self, project, capsys, monkeypatch, name="add-widget", plan=()):
        assert run("init", "--name", "demo") == 0
        commit_all(project, "manifest")
        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", "host-first-0000")
        run("session", "start", name, "--json")
        session = payload(capsys)["session"]
        run("session", "isolate", "--execute", "--id", session["id"])
        for title in plan:
            run("session", "plan", "add", title, "--id", session["id"])
        spend_the_binding(project, "host-first-0000")
        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", "host-second-1111")
        capsys.readouterr()
        return session


class TestEveryCommandARuleNamesCanBeRun(ParkedSessionHarness):
    """A `fixes` line is a promise that the command exists, spelled that way.

    Two increments shipped rules whose fixes named `session plan done`,
    `session plan remove` and `session sync` before any of them was written —
    accepted as a within-branch forward reference on the explicit basis that
    this increment closes it. Nothing but a test keeps that closed: a fix
    string is a literal, so a renamed subcommand breaks it silently and the
    only reader who finds out is somebody already stuck at a blocking gate.

    The scan reads the SOURCE of the two modules that emit these strings,
    rather than evaluating rules, because a `fixes` tuple is built inside a
    verdict at refusal time and there is no other way to enumerate the ones a
    happy run never produces.
    """

    #: `#` is the ONE annotation form a fix may use for prose after the
    #: command, because it is the one a shell ignores: a `fixes` line is
    #: written to be pasted by somebody already blocked, and a parenthetical
    #: annotation makes the paste a syntax error (IDEA_006). Nothing but this
    #: normaliser keeps that closed — it used to strip parentheses too, which
    #: is how three of them shipped.
    def commands_named_in(self, module) -> list[str]:
        import inspect
        import re as _re

        source = inspect.getsource(module)
        # `\s*` inside the quotes: a fix is often written INDENTED, as one
        # line of a multi-line refusal. The indentation belongs to the message.
        found = _re.findall(r'"\s*(invariant [a-z][^"\n]*)"', source)
        found += _re.findall(r"'\s*(invariant [a-z][^'\n]*)'", source)
        return sorted(set(found))

    def runnable(self, text: str) -> list[str]:
        import shlex

        stripped = text.split("#")[0].strip()
        tokens = shlex.split(stripped.replace('\\"', '"'))[1:]
        # A placeholder for a NUMBER is the one substitution the parser needs:
        # `<n>` is typed by a human as an integer and the parser is what says
        # so. Every other placeholder is a string wherever it lands.
        return ["1" if t == "<n>" else t for t in tokens]

    def test_every_command_the_rules_name_parses(self):
        import invariant.core.rules as rules_module
        from invariant.cli.main import build_parser

        parser = build_parser()
        named = self.commands_named_in(rules_module)
        assert "invariant session sync" in named  # the scan found the fixes
        for text in named:
            parser.parse_args(self.runnable(text))

    def test_every_command_the_session_domain_names_parses(self):
        """The domain names `session resume <name>` in two refusals."""
        import invariant.domains.session as session_module
        from invariant.cli.main import build_parser

        parser = build_parser()
        named = self.commands_named_in(session_module)
        assert any("session resume" in text for text in named)
        for text in named:
            parser.parse_args(self.runnable(text))

    def test_every_command_the_stats_domain_names_parses(self):
        """The third module that names commands in a refusal (IDEA_006).

        Unscanned until now, and it showed: `stats archive` was named without
        the `<session>` its parser requires, so the one command the orphaned
        record advice hands the reader could not be run as printed.
        """
        import invariant.domains.stats as stats_module
        from invariant.cli.main import build_parser

        parser = build_parser()
        named = self.commands_named_in(stats_module)
        assert any("stats archive" in text for text in named)
        for text in named:
            parser.parse_args(self.runnable(text))

    def test_the_deliverables_gate_fix_is_runnable_as_written(
        self, project, capsys, monkeypatch
    ):
        """End to end, not just through the parser: the gate names it, it runs."""
        session = self.parked(project, capsys, monkeypatch, plan=("wire it",))
        assert run("session", "plan", "done", "1", "--id", session["id"]) == 0
        run("session", "plan", "show", "--id", session["id"], "--json")
        assert payload(capsys)["complete"] is True


class GreetingHarness:
    """`SessionStart`, run the way the host runs it.

    Through the hook rather than through `invariant context`, because the
    resumable offer is on the GREETING path and `context` is the every-turn
    one. Testing it through a fake context would prove only that a list was
    formatted; the value of the block is that `resumable()` picks the right
    sessions, which only real records can show.
    """

    def greet(self, monkeypatch, host_session_id="host-second-1111"):
        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", host_session_id)
        monkeypatch.setattr(
            "sys.stdin", _Stdin(json.dumps({"session_id": host_session_id}))
        )
        assert run("hook", "session-start") == 0

    def greeting(self, capsys, monkeypatch, host_session_id="host-second-1111"):
        self.greet(monkeypatch, host_session_id)
        return (
            payload(capsys)
            .get("hookSpecificOutput", {})
            .get("additionalContext", "")
        )


class TestTheGreetingOffersParkedWork(ParkedSessionHarness, GreetingHarness):
    """The discoverability hole this whole feature exists to close.

    The greeting listed only sessions with NO owner. A session whose host
    ENDED has an owner — a spent one — so the single session that could
    actually be picked up was the one the greeting could not mention, and the
    only way to find it was to already know it was there.
    """

    def test_a_parked_session_is_offered_with_its_progress(
        self, project, capsys, monkeypatch
    ):
        session = self.parked(
            project, capsys, monkeypatch, plan=("wire the toolbar", "add the icons")
        )
        text = self.greeting(capsys, monkeypatch)

        assert "A session OUTLIVES a host session" in text
        assert f"invariant session resume {session['name']}" in text
        assert "0/2 deliverables" in text
        assert "next: wire the toolbar" in text

    def test_the_offer_says_it_is_an_offer(self, project, capsys, monkeypatch):
        """Resumption is never automatic, and the line that offers it says so.

        Picking up unfinished work brings a pipeline state, a declared file set
        and a branch along with it. A greeting that read as an instruction
        would make that decision on the reader's behalf.
        """
        self.parked(project, capsys, monkeypatch)
        text = self.greeting(capsys, monkeypatch)
        assert "offered, never automatic" in text
        assert "new work starts a new session" in text

    def test_a_parked_session_with_no_plan_says_so_rather_than_a_fraction(
        self, project, capsys, monkeypatch
    ):
        self.parked(project, capsys, monkeypatch)
        text = self.greeting(capsys, monkeypatch)
        assert "no deliverables recorded" in text
        assert "0/0" not in text

    def unowned(self, project, capsys, monkeypatch, *names):
        """Sessions with no owner at all — the list that was already there.

        TWO at least, deliberately. One unbound session is not ambiguous: it is
        the sole attributable candidate, so `current_session` resolves to it at
        the origin step and the greeting never reaches the no-session branch
        this block lives in.
        """
        monkeypatch.delenv("INVARIANT_HOST_SESSION_ID", raising=False)
        for name in names:
            run("session", "start", name, "--force")
        capsys.readouterr()

    def test_a_parked_session_whose_plan_is_finished_says_all_done(
        self, project, capsys, monkeypatch
    ):
        """Parked and complete is a real state, and not the same as parked.

        The session did what it said it was for and its host went away before
        it landed. The offer says so rather than printing a next deliverable
        there is none of.
        """
        session = self.parked(project, capsys, monkeypatch, plan=("wire it",))
        run("session", "plan", "done", "1", "--id", session["id"])
        capsys.readouterr()

        text = self.greeting(capsys, monkeypatch)
        assert "1/1 deliverables, all done" in text
        assert "next:" not in text

    def test_the_offer_is_above_the_unowned_list(self, project, capsys, monkeypatch):
        """A definite offer outranks a hedged one.

        A resumable session has a name, a state and a progress count, and the
        reader probably parked it themselves. An unowned session is a
        conditional — "claim one only if it is yours". Printing the hedged list
        first buries the answer under the caveat.
        """
        self.parked(project, capsys, monkeypatch)
        self.unowned(project, capsys, monkeypatch, "spare-a", "spare-b")

        text = self.greeting(capsys, monkeypatch)
        assert text.index("can be picked up") < text.index("have no owner")

    def test_at_most_three_are_offered_and_the_rest_are_named(
        self, project, capsys, monkeypatch
    ):
        """Capped at three against six for the unowned list, and deliberately.

        Each of these lines carries a progress clause and is about twice as
        wide, and the whole block is injected on every SessionStart. The
        overflow is not dropped; it is named, behind the command that lists it.
        """
        assert run("init", "--name", "demo") == 0
        commit_all(project, "manifest")
        for n in range(4):
            host = f"host-{n}-0000"
            monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", host)
            run("session", "start", f"task-{n}")
            spend_the_binding(project, host)
        capsys.readouterr()

        text = self.greeting(capsys, monkeypatch)
        assert text.count("invariant session resume ") == 3
        assert "1 more" in text
        assert "invariant session list" in text

    def test_the_greeting_is_byte_identical_when_nothing_is_parked(
        self, project, capsys, monkeypatch
    ):
        """What makes this change additive for every project that never adopts it.

        A project with no parked sessions must see exactly the greeting it saw
        before — not a reworded one, not an extra blank line. Anything else is
        a change every consumer pays for and none of them asked for.
        """
        assert run("init", "--name", "demo") == 0
        commit_all(project, "manifest")
        self.unowned(project, capsys, monkeypatch, "spare-a", "spare-b")

        offered = unowned_candidates(project, "host-second-1111")
        assert len(offered) == 2
        assert self.greeting(capsys, monkeypatch) == "\n".join(
            [
                "invariant: this host session owns no session yet.",
                "  Start one before editing: invariant session start <task-name>",
                (
                    "  It binds to this host session automatically — one host "
                    "session, one invariant session."
                ),
                (
                    "  2 session(s) here have no owner; claim one only if it "
                    "is yours:"
                ),
            ]
            + [
                f"    invariant session adopt <host-id> --id {s.id}"
                f"   ({s.name}, {s.state})"
                for s in offered
            ]
        )


class TestSessionStartWritesNothing(ParkedSessionHarness, GreetingHarness):
    """A greeting is a READ. It must not move any state to produce one.

    The block it now prints comes from `resumable()`, which is written from
    `live()` and sorts a list. Nothing on that path saves — and nothing may
    start to, because a hook that writes on every SessionStart would stamp
    heartbeats, touch mtimes and race every other host against the same
    registry, all to say hello.
    """

    def fingerprint(self, project):
        from invariant.core.manifest import Manifest

        root = Manifest.load(project).state_root
        return {
            str(f.relative_to(root)): (f.stat().st_mtime_ns, f.stat().st_size)
            for f in sorted(root.rglob("*"))
            if f.is_file()
        }

    def test_the_greeting_moves_no_state(self, project, capsys, monkeypatch):
        self.parked(project, capsys, monkeypatch, plan=("wire it",))
        before = self.fingerprint(project)
        assert before, "the fixture wrote no state, so this proves nothing"

        self.greet(monkeypatch)
        capsys.readouterr()

        assert self.fingerprint(project) == before


class TestTheTurnProjectionShowsThePlan(ParkedSessionHarness):
    """What the turn is ABOUT, placed where that is what it reads as.

    Below the stand-down banner, which keeps its position as the only line
    reporting an absence of enforcement, and above `session may go to:`, which
    is machinery.
    """

    def resumed(self, project, capsys, monkeypatch, plan=()):
        session = self.parked(project, capsys, monkeypatch, plan=plan)
        assert run("session", "resume", session["name"]) == 0
        capsys.readouterr()
        return session

    def context(self, capsys, session):
        assert run("context", "--id", session["id"]) == 0
        return out(capsys)

    def test_a_resumed_session_says_it_spans_host_sessions(
        self, project, capsys, monkeypatch
    ):
        session = self.resumed(
            project, capsys, monkeypatch, plan=("wire the toolbar", "add the icons")
        )
        text = self.context(capsys, session)
        assert "spans host sessions — resumed 1 time(s)" in text
        assert "it merges when the plan is done" in text

    def test_the_plan_shows_the_next_and_one_then(
        self, project, capsys, monkeypatch
    ):
        session = self.resumed(
            project, capsys, monkeypatch, plan=("wire it", "ice it", "ship it")
        )
        text = self.context(capsys, session)
        assert "plan 0/3 done — next: wire it" in text
        assert "then: ice it" in text
        assert "ship it" not in text

    def test_a_resumed_session_with_no_plan_is_told_to_record_one(
        self, project, capsys, monkeypatch
    ):
        """The one moment the plan is still cheap to write down.

        Whatever is left is in somebody's head right now. Claiming "it merges
        when the plan is done" against a plan that does not exist would name a
        condition nothing can meet.
        """
        session = self.resumed(project, capsys, monkeypatch)
        text = self.context(capsys, session)
        assert "spans host sessions" in text
        assert "it merges when the plan is done" not in text
        assert 'invariant session plan add "<deliverable>"' in text

    def test_a_complete_plan_names_the_finish(self, project, capsys, monkeypatch):
        session = self.resumed(project, capsys, monkeypatch, plan=("wire it",))
        run("session", "plan", "done", "1", "--id", session["id"])
        capsys.readouterr()
        text = self.context(capsys, session)
        assert "plan 1/1 done — COMPLETE" in text
        assert "invariant session review" in text

    def test_the_projection_adds_at_most_three_lines(
        self, project, capsys, monkeypatch
    ):
        session = self.resumed(
            project, capsys, monkeypatch, plan=("a", "b", "c", "d", "e")
        )
        text = self.context(capsys, session)
        added = [
            line
            for line in text.splitlines()
            if "spans host sessions" in line
            or line.strip().startswith(("plan ", "then:"))
        ]
        assert len(added) == 3

    def test_an_unplanned_never_resumed_session_projects_exactly_as_before(
        self, project, capsys, monkeypatch
    ):
        """The additive guarantee, from the other end.

        A session with no plan that was never resumed is every session in every
        project that does not use this feature. Its turn projection must gain
        nothing at all.
        """
        assert run("init", "--name", "demo") == 0
        commit_all(project, "manifest")
        run("session", "start", "plain", "--json")
        session = payload(capsys)["session"]
        capsys.readouterr()

        text = self.context(capsys, session)
        assert "spans host sessions" not in text
        assert "plan " not in text
        assert "then:" not in text
        assert text.splitlines()[1].strip().startswith("session may go to:")


def unowned_candidates(project, host_session_id):
    """The sessions with no owner, in the order the greeting lists them.

    Read from the domain rather than hardcoded, because the ASSERTION is about
    the rendering — the exact words, spacing and punctuation of a block every
    project sees on every SessionStart — and not about which ids a fixture
    happened to mint.
    """
    from invariant.core.manifest import Manifest
    from invariant.core.store import JsonStore
    from invariant.domains.session import SessionDomain

    manifest = Manifest.load(project)
    domain = SessionDomain(manifest, JsonStore(manifest.state_root))
    return [
        s
        for s in domain.origin_candidates(project, host_session_id=host_session_id)
        if not s.host_session_id
    ]


class HostClaimHarness:
    """Push a real command's real output through the real host binder.

    The two halves of the binding contract live in different modules and are
    only a contract because they are tested against each other: the CLI shapes
    a line, and `hosts/claude_code` parses it. A test that hand-writes the
    line proves the parser reads a string somebody made up.
    """

    def claim(self, text, host_session_id="host-second-1111"):
        from types import SimpleNamespace

        from invariant.hosts.claude_code import ClaudeCodeHost

        class Recorder:
            def __init__(self):
                self.adopted = []

            def adopt_host_session(self, session_id, host_session_id, force=False):
                self.adopted.append((session_id, host_session_id))

        ctx = SimpleNamespace(sessions=Recorder(), host_session_id=host_session_id)
        ClaudeCodeHost(Path("."))._bind_claimed_session(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "invariant session resume add-widget"},
                "tool_response": text,
            },
            ctx,
        )
        return ctx.sessions.adopted


class TestResumeBindsTheHostThatRanIt(ParkedSessionHarness, HostClaimHarness):
    """The other end of the contract `TestSessionResume` tests one side of.

    `session resume` runs through a shell, so the CLI cannot see the host
    identifier and the binding has to be made in PostToolUse off the command's
    own stdout. Until the claim pattern admitted `resume`, the command whose
    entire purpose is to pick work up left the host bound to nothing: the
    record said resumed and every subsequent hook resolved to no session.
    """

    def test_the_success_line_binds_the_host_that_ran_it(
        self, project, capsys, monkeypatch
    ):
        session = self.parked(project, capsys, monkeypatch, plan=("wire it",))
        assert run("session", "resume", session["name"]) == 0
        captured = capsys.readouterr()
        assert self.claim(captured.out + captured.err) == [
            (session["id"], "host-second-1111")
        ]

    def test_the_claim_counts_no_second_resume(
        self, project, capsys, monkeypatch
    ):
        """One pickup, one count, one standstill row.

        The CLI's `resume` has already incremented `resumed_count` and written
        the `resumed` history row by the time PostToolUse sees its output. The
        hook owes only the half the CLI could not do — WHICH host claimed it —
        so it binds through `adopt_host_session`, which clears the spent
        `host_ended`, keeps the outgoing id in `hosts`, and touches no counter.
        Routing it through `SessionDomain.resume` instead counts the one
        pickup twice and leaves two rows describing one event.
        """
        from invariant.core.manifest import Manifest
        from invariant.core.store import JsonStore
        from invariant.domains.session import SessionDomain

        session = self.parked(project, capsys, monkeypatch)
        assert run("session", "resume", session["name"]) == 0
        captured = capsys.readouterr()
        self.claim(captured.out + captured.err)

        manifest = Manifest.load(project)
        domain = SessionDomain(manifest, JsonStore(manifest.state_root))
        after = domain.require(session["id"])
        assert after.resumed_count == 1
        assert len([h for h in after.history if h.get("resumed")]) == 1

    def test_the_binding_actually_lands_so_later_hooks_resolve(
        self, project, capsys, monkeypatch
    ):
        """`host_ended` must be CLEARED, or the pickup is unaddressable.

        `by_host_session` filters on the flag, so a session claimed with it
        still set resolves to nobody for every hook in its new host — resumed
        in the record and invisible in practice.
        """
        from invariant.core.manifest import Manifest
        from invariant.core.store import JsonStore
        from invariant.domains.session import SessionDomain

        session = self.parked(project, capsys, monkeypatch)
        assert run("session", "resume", session["name"]) == 0
        captured = capsys.readouterr()

        manifest = Manifest.load(project)
        domain = SessionDomain(manifest, JsonStore(manifest.state_root))
        domain.adopt_host_session(*self.claim(captured.out + captured.err)[0])

        found = domain.by_host_session("host-second-1111")
        assert found is not None and found.id == session["id"]
        assert found.host_ended is False
        assert "host-first-0000" in found.hosts

    def test_claiming_a_resumed_session_does_not_widen_attributable(
        self, project, capsys, monkeypatch
    ):
        """The host-side counterpart of the domain's own guard.

        A vacated binding would rejoin `attributable()`; a peer host that owned
        nothing would then see two origin candidates, `resolve` would answer
        ambiguous, `rule_context` would render `session=None`, and
        `pre-tool-use` DENIES an unattributed edit. Claiming a session must
        never be able to refuse another session's tool call, so the claim is
        checked from the PEER's point of view rather than from its own.
        """
        from invariant.core.manifest import Manifest
        from invariant.core.store import JsonStore
        from invariant.domains.session import SessionDomain

        session = self.parked(project, capsys, monkeypatch)
        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", "host-peer-2222")
        run("session", "start", "peer", "--json")
        peer = payload(capsys)["session"]
        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", "host-second-1111")
        capsys.readouterr()

        assert run("session", "resume", session["name"]) == 0
        captured = capsys.readouterr()

        manifest = Manifest.load(project)
        domain = SessionDomain(manifest, JsonStore(manifest.state_root))
        domain.adopt_host_session(*self.claim(captured.out + captured.err)[0])

        resolution = domain.resolve(cwd=project, host_session_id="host-peer-2222")
        assert resolution.session is not None
        assert resolution.session.id == peer["id"]


class TestARefusedResumeBindsNothing(ParkedSessionHarness, HostClaimHarness):
    """The refusals go through the parser that would bind on them.

    `TestSessionResume` asserts the refusal text carries no `session <id> (`
    sequence. That is the property; this is the consequence, checked end to end
    through the code that would act on it — because the reader searches the
    WHOLE tool response and a host that merges streams puts stderr in it, so a
    refusal carrying the sequence binds the caller to a session it just
    declined to give them.
    """

    def test_a_name_that_matches_nothing(self, project, capsys, monkeypatch):
        self.parked(project, capsys, monkeypatch)
        assert run("session", "resume", "no-such-task") == 2
        captured = capsys.readouterr()
        assert "no-such-task" in captured.out + captured.err
        assert self.claim(captured.out + captured.err) == []

    def test_a_name_that_matches_several(self, project, capsys, monkeypatch):
        assert run("init", "--name", "demo") == 0
        commit_all(project, "manifest")
        for host in ("host-a-0000", "host-b-1111"):
            monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", host)
            run("session", "start", "add-widget", "--force")
            spend_the_binding(project, host)
        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", "host-second-1111")
        capsys.readouterr()

        assert run("session", "resume", "add-widget") == 2
        captured = capsys.readouterr()
        assert "names 2 sessions" in captured.out + captured.err
        assert self.claim(captured.out + captured.err) == []


class TestSessionResume(ParkedSessionHarness):
    def test_it_reports_the_shape_the_host_binds_on(
        self, project, capsys, monkeypatch
    ):
        """The success line is a contract with `hosts/claude_code`.

        That module claims a session by parsing the stdout of the command that
        created it, with one regex. A resume that prints a line the regex
        cannot read is a session the host never binds to.
        """
        from invariant.hosts.claude_code import _bind_claimed_session_id

        session = self.parked(
            project, capsys, monkeypatch, plan=("read the docs", "wire the toolbar")
        )
        assert run("session", "resume", session["name"]) == 0
        text = out(capsys)

        assert _bind_claimed_session_id(text) == session["id"]
        assert text.splitlines()[0].endswith("resumed — 0/2 deliverables done")
        assert "next: read the docs" in text
        assert "invariant session status" in text

    def test_a_bare_slug_resolves(self, project, capsys, monkeypatch):
        session = self.parked(project, capsys, monkeypatch)
        assert run("session", "resume", "add-widget") == 0
        assert session["id"] in out(capsys)

    def test_a_session_with_no_plan_is_told_how_to_record_one(
        self, project, capsys, monkeypatch
    ):
        self.parked(project, capsys, monkeypatch)
        assert run("session", "resume", "add-widget") == 0
        text = out(capsys)
        assert "no deliverables recorded" in text
        assert 'invariant session plan add "<deliverable>"' in text

    def test_a_finished_plan_names_the_landing_step(
        self, project, capsys, monkeypatch
    ):
        session = self.parked(project, capsys, monkeypatch, plan=("wire it",))
        run("session", "plan", "done", "1", "--id", session["id"])
        capsys.readouterr()
        assert run("session", "resume", "add-widget") == 0
        text = out(capsys)
        assert "1/1 deliverables done" in text
        assert "invariant session review" in text

    def test_not_found_binds_nothing(self, project, capsys, monkeypatch):
        """A failed resume must not print anything a host can bind to.

        `_bind_claimed_session_id` searches the WHOLE tool response, and hosts
        that merge streams put stderr in it. A refusal carrying a
        `session <id> (` sequence would bind the caller to whatever it named —
        silently, and to the wrong session.
        """
        from invariant.hosts.claude_code import _STARTED_ID, _bind_claimed_session_id

        self.parked(project, capsys, monkeypatch)
        assert run("session", "resume", "no-such-task") == 2
        captured = capsys.readouterr()
        both = captured.out + captured.err

        assert "no-such-task" in both
        assert _STARTED_ID.search(both) is None
        assert _bind_claimed_session_id(both) is None

    def test_an_ambiguous_name_lists_the_candidates_and_binds_nothing(
        self, project, capsys, monkeypatch
    ):
        from invariant.hosts.claude_code import _STARTED_ID, _bind_claimed_session_id

        assert run("init", "--name", "demo") == 0
        commit_all(project, "manifest")
        for host in ("host-a-0000", "host-b-1111"):
            monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", host)
            run("session", "start", "add-widget", "--force")
            spend_the_binding(project, host)
        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", "host-c-2222")
        capsys.readouterr()

        assert run("session", "resume", "add-widget") == 2
        captured = capsys.readouterr()
        both = captured.out + captured.err

        assert "names 2 sessions" in both
        assert _STARTED_ID.search(both) is None
        assert _bind_claimed_session_id(both) is None

    def test_id_names_the_session_directly(self, project, capsys, monkeypatch):
        session = self.parked(project, capsys, monkeypatch)
        assert run("session", "resume", "--id", session["id"]) == 0
        assert session["id"] in out(capsys)

    def test_a_name_that_contradicts_the_id_is_refused(
        self, project, capsys, monkeypatch
    ):
        session = self.parked(project, capsys, monkeypatch)
        assert run("session", "resume", "something-else", "--id", session["id"]) == 2
        assert "something-else" in capsys.readouterr().err

    def test_naming_nothing_is_a_usage_error(self, project, capsys, monkeypatch):
        self.parked(project, capsys, monkeypatch)
        assert run("session", "resume") == 2
        assert "invariant session list" in capsys.readouterr().err

    def test_the_record_counts_the_resume(self, project, capsys, monkeypatch):
        self.parked(project, capsys, monkeypatch)
        run("session", "resume", "add-widget", "--json")
        assert payload(capsys)["session"]["resumed_count"] == 1

    def test_a_merged_session_is_refused_without_force(
        self, project, capsys, monkeypatch
    ):
        session = self.parked(project, capsys, monkeypatch)
        for state in ("working", "reviewed", "merged"):
            run("session", "transition", state, "--id", session["id"], "--force",
                "--reason", "test fixture")
        capsys.readouterr()

        assert run("session", "resume", session["name"]) == 2
        assert "push_barrier" in capsys.readouterr().err


class TestSessionPlan(ParkedSessionHarness):
    def started(self, project, capsys, monkeypatch):
        assert run("init", "--name", "demo") == 0
        commit_all(project, "manifest")
        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", "host-plan-0000")
        run("session", "start", "plan-work", "--json")
        session = payload(capsys)["session"]
        capsys.readouterr()
        return session["id"]

    def test_add_then_show(self, project, capsys, monkeypatch):
        sid = self.started(project, capsys, monkeypatch)
        assert run("session", "plan", "add", "read the toolbar docs") == 0
        assert run("session", "plan", "add", "wire the toolbar") == 0
        capsys.readouterr()

        assert run("session", "plan", "show") == 0
        lines = out(capsys).splitlines()
        assert lines[0].startswith(f"plan for session {sid} (")
        assert lines[0].endswith("— 0/2 done")
        assert lines[1] == "  [ ] 1  read the toolbar docs"
        assert lines[-1] == "  next: read the toolbar docs"

    def test_done_reopen_and_remove_round_trip(self, project, capsys, monkeypatch):
        self.started(project, capsys, monkeypatch)
        run("session", "plan", "add", "one")
        run("session", "plan", "add", "two")

        assert run("session", "plan", "done", "1") == 0
        run("session", "plan", "show", "--json")
        assert payload(capsys)["done"] == 1

        assert run("session", "plan", "reopen", "1") == 0
        run("session", "plan", "show", "--json")
        assert payload(capsys)["done"] == 0

        assert run("session", "plan", "remove", "1") == 0
        run("session", "plan", "show", "--json")
        after = payload(capsys)
        assert [e["n"] for e in after["plan"]] == [2]

        # A retired number is never reissued, so `plan done 3` means the same
        # thing after a remove as it did before one.
        assert run("session", "plan", "add", "three") == 0
        run("session", "plan", "show", "--json")
        assert [e["n"] for e in payload(capsys)["plan"]] == [2, 3]

    def test_planned_and_complete_are_separate_keys(
        self, project, capsys, monkeypatch
    ):
        """A session that never planned must not read as one that finished.

        The reviewer contract switches mode on exactly these two. Collapsed
        into one key, every project that never adopts a checklist either
        defers its merge forever or gets a vacuous green.
        """
        self.started(project, capsys, monkeypatch)

        run("session", "plan", "show", "--json")
        empty = payload(capsys)
        assert empty["planned"] is False
        assert empty["complete"] is False

        run("session", "plan", "add", "one")
        run("session", "plan", "show", "--json")
        open_plan = payload(capsys)
        assert open_plan["planned"] is True
        assert open_plan["complete"] is False
        assert open_plan["next"] == "one"

        run("session", "plan", "done", "1")
        run("session", "plan", "show", "--json")
        finished = payload(capsys)
        assert finished["planned"] is True
        assert finished["complete"] is True
        assert finished["next"] is None

    def test_the_json_document_carries_the_session_and_its_name(
        self, project, capsys, monkeypatch
    ):
        sid = self.started(project, capsys, monkeypatch)
        run("session", "plan", "add", "one", "--json")
        document = payload(capsys)
        assert document["session"] == sid
        assert document["name"].endswith(sid)
        assert document["total"] == 1

    def test_an_empty_plan_says_how_to_record_one(self, project, capsys, monkeypatch):
        self.started(project, capsys, monkeypatch)
        assert run("session", "plan", "show") == 0
        text = out(capsys)
        assert "(no deliverables recorded)" in text
        assert 'invariant session plan add "<deliverable>"' in text

    def test_a_number_that_is_not_one_is_a_usage_error(
        self, project, capsys, monkeypatch
    ):
        """argparse answers this, so the refusal is about the COMMAND.

        Reaching the domain as a string, the same mistake comes back as "no
        deliverable 'abc' in session …" — a report about the plan for what is
        a malformed command.
        """
        self.started(project, capsys, monkeypatch)
        with pytest.raises(SystemExit) as exc:
            run("session", "plan", "done", "abc")
        assert exc.value.code == 2

    def test_an_unknown_number_is_refused(self, project, capsys, monkeypatch):
        self.started(project, capsys, monkeypatch)
        run("session", "plan", "add", "one")
        assert run("session", "plan", "done", "9") == 2
        assert "no deliverable 9" in capsys.readouterr().err

    def test_an_unquoted_title_is_refused_by_the_parser(
        self, project, capsys, monkeypatch
    ):
        """One positional, so a lost quote is a usage error and not a title."""
        self.started(project, capsys, monkeypatch)
        with pytest.raises(SystemExit) as exc:
            run("session", "plan", "add", "wire", "the", "toolbar")
        assert exc.value.code == 2


class TestSessionSync(ParkedSessionHarness):
    def isolated(self, project, capsys, monkeypatch):
        assert run("init", "--name", "demo") == 0
        commit_all(project, "manifest")
        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", "host-sync-0000")
        run("session", "start", "sync-work", "--json")
        session = payload(capsys)["session"]
        run("session", "isolate", "--execute", "--id", session["id"])
        run("session", "list", "--json")
        entry = payload(capsys)["sessions"][0]
        capsys.readouterr()
        return entry, Path(entry["worktree"])

    def advance_the_base(self, project, name="peer.py", body="print('peer')\n"):
        (project / name).write_text(body, encoding="utf-8")
        commit_all(project, f"peer adds {name}")
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True,
            check=False,
        ).stdout.strip()

    def tip(self, worktree):
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True,
            check=False,
        ).stdout.strip()

    def test_it_rebases_onto_the_base(self, project, capsys, monkeypatch):
        entry, worktree = self.isolated(project, capsys, monkeypatch)
        peer = self.advance_the_base(project)
        monkeypatch.chdir(worktree)

        assert run("session", "sync", "--id", entry["id"]) == 0
        contains = subprocess.run(
            ["git", "branch", "--contains", peer, "--format=%(refname:short)"],
            cwd=worktree, capture_output=True, text=True, check=False,
        ).stdout.split()
        assert entry["branch"] in contains
        assert "master" in out(capsys)

    def test_a_branch_already_current_is_said_so(self, project, capsys, monkeypatch):
        entry, worktree = self.isolated(project, capsys, monkeypatch)
        monkeypatch.chdir(worktree)
        before = self.tip(worktree)

        assert run("session", "sync", "--id", entry["id"]) == 0
        assert "already" in out(capsys)
        assert self.tip(worktree) == before

    def test_it_is_refused_from_outside_the_worktree(
        self, project, capsys, monkeypatch
    ):
        """The mirror of `session.ff_merge_target`.

        A rebase run from the wrong tree rebases whatever branch that tree has
        checked out and exits zero, which is the invisible-success shape this
        library exists to catch.
        """
        entry, worktree = self.isolated(project, capsys, monkeypatch)
        self.advance_the_base(project)
        assert Path.cwd() == project
        before = self.tip(project)

        assert run("session", "sync", "--id", entry["id"]) == 2
        message = capsys.readouterr().err
        assert str(worktree) in message
        # Nothing was touched: the refusal ran before git did.
        assert self.tip(project) == before

    def test_a_dirty_worktree_is_refused_before_git_runs(
        self, project, capsys, monkeypatch
    ):
        entry, worktree = self.isolated(project, capsys, monkeypatch)
        self.advance_the_base(project)
        (worktree / "app.py").write_text("print('edited')\n", encoding="utf-8")
        monkeypatch.chdir(worktree)
        before = self.tip(worktree)

        assert run("session", "sync", "--id", entry["id"]) == 2
        assert "uncommitted" in capsys.readouterr().err
        assert self.tip(worktree) == before
        assert (worktree / "app.py").read_text() == "print('edited')\n"

    def test_a_detached_head_is_refused(self, project, capsys, monkeypatch):
        """HEAD is what a rebase acts on; the branch is only what we expect."""
        entry, worktree = self.isolated(project, capsys, monkeypatch)
        subprocess.run(
            ["git", "checkout", "-q", "--detach"], cwd=worktree, check=True
        )
        monkeypatch.chdir(worktree)

        assert run("session", "sync", "--id", entry["id"]) == 2
        assert entry["branch"] in capsys.readouterr().err

    def test_a_conflict_aborts_and_keeps_the_commits(
        self, project, capsys, monkeypatch
    ):
        entry, worktree = self.isolated(project, capsys, monkeypatch)
        (worktree / "shared.py").write_text("session\n", encoding="utf-8")
        commit_all(worktree, "session writes shared.py")
        session_tip = self.tip(worktree)
        self.advance_the_base(project, name="shared.py", body="base\n")
        monkeypatch.chdir(worktree)

        assert run("session", "sync", "--id", entry["id"]) == 2
        message = capsys.readouterr().err
        assert "conflict" in message.lower()
        # The commits are exactly where they were, and no rebase is in flight.
        assert self.tip(worktree) == session_tip
        state = subprocess.run(
            ["git", "status", "--porcelain=v2", "--branch"],
            cwd=worktree, capture_output=True, text=True, check=False,
        ).stdout
        assert entry["branch"] in state


class TestPlanIsVisibleWhereWorkIsPickedUp(ParkedSessionHarness):
    def test_status_carries_the_plan_and_the_resume_count(
        self, project, capsys, monkeypatch
    ):
        session = self.parked(
            project, capsys, monkeypatch, plan=("read the docs", "wire the toolbar")
        )
        run("session", "plan", "done", "1", "--id", session["id"])
        run("session", "resume", session["name"])
        capsys.readouterr()

        run("session", "status", "--id", session["id"])
        text = out(capsys)
        assert "plan" in text
        assert "1/2 done — next: wire the toolbar" in text
        assert "resumed" in text
        assert "1" in text

    def test_a_finished_plan_reads_as_complete(self, project, capsys, monkeypatch):
        session = self.parked(project, capsys, monkeypatch, plan=("wire it",))
        run("session", "plan", "done", "1", "--id", session["id"])
        capsys.readouterr()

        run("session", "status", "--id", session["id"])
        assert "1/1 done — complete" in out(capsys)

    def test_an_unplanned_session_gets_no_plan_line(
        self, project, capsys, monkeypatch
    ):
        """A gate that fires on every one-line chore is a gate people switch off.

        The same argument `session.deliverables_complete` makes for `na` over
        `fail`: an unplanned session is a legitimate resting state, so status
        does not nag about it.
        """
        session = self.parked(project, capsys, monkeypatch)
        run("session", "status", "--id", session["id"])
        assert "plan " not in out(capsys)

    def test_list_carries_a_progress_column(self, project, capsys, monkeypatch):
        session = self.parked(project, capsys, monkeypatch, plan=("one", "two"))
        run("session", "plan", "done", "1", "--id", session["id"])
        capsys.readouterr()

        run("session", "list")
        assert "1/2" in out(capsys)

    def test_the_list_json_is_unchanged(self, project, capsys, monkeypatch):
        session = self.parked(project, capsys, monkeypatch, plan=("one",))
        run("session", "list", "--json")
        entry = payload(capsys)["sessions"][0]
        assert entry["id"] == session["id"]
        assert [e["title"] for e in entry["plan"]] == ["one"]

    def test_start_refuses_to_fork_a_parked_plan_and_force_is_reachable(
        self, project, capsys, monkeypatch
    ):
        """The refusal ends "add --force", and the flag has to exist.

        The domain has refused a task name whose parked session is sitting
        right there since the increment that introduced the plan; the CLI had
        no `--force` to pass through, so the remedy the refusal named could not
        be run — a `fixes` line pointing at nothing, one layer up.
        """
        session = self.parked(project, capsys, monkeypatch)

        assert run("session", "start", "add-widget") == 2
        refusal = capsys.readouterr().err
        assert session["name"] in refusal
        assert "--force" in refusal

        assert run("session", "start", "add-widget", "--force") == 0
        assert "registered" in out(capsys)

    def test_start_names_the_way_back(self, project, capsys, monkeypatch):
        """And line one still binds: `TestAutomaticBinding` reads it."""
        from invariant.hosts.claude_code import _bind_claimed_session_id

        assert run("init", "--name", "demo") == 0
        commit_all(project, "manifest")
        run("session", "start", "later-work")
        text = out(capsys)

        assert _bind_claimed_session_id(text) is not None
        assert "invariant session resume later-work-" in text


class TestDoctorReportsWhatWasLeftParked(ParkedSessionHarness):
    """`end_host_session`'s docstring has promised this since it was written."""

    def test_a_parked_session_is_named(self, project, capsys, monkeypatch):
        session = self.parked(project, capsys, monkeypatch, plan=("wire it",))
        capsys.readouterr()

        run("doctor")
        text = out(capsys)
        assert session["name"] in text
        assert "invariant session resume" in text

    def test_it_never_turns_doctor_red(self, project, capsys, monkeypatch):
        """A parked session is this feature's designed resting state.

        `_report_checks` turns any false row into exit 1, so an informational
        row reported as a failure would make `doctor` permanently red on every
        project that adopts the plan — the "gate that gets ignored, then
        switched off" failure this codebase names repeatedly.
        """
        assert run("init", "--name", "demo") == 0
        commit_all(project, "manifest")
        capsys.readouterr()
        before = run("doctor")
        capsys.readouterr()

        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", "host-parked-0000")
        run("session", "start", "parked-work", "--json")
        session = payload(capsys)["session"]
        run("session", "plan", "add", "wire it", "--id", session["id"])
        spend_the_binding(project, "host-parked-0000")
        capsys.readouterr()

        assert run("doctor") == before
        text = out(capsys)
        assert "FAIL" not in text.split("parked")[0].splitlines()[-1]

    def test_no_parked_session_says_so(self, project, capsys, monkeypatch):
        assert run("init", "--name", "demo") == 0
        commit_all(project, "manifest")
        capsys.readouterr()

        run("doctor")
        assert "none parked here" in out(capsys)


class TestInitScaffoldsTheArchitectureDoc:
    """A fresh project used to fail its own docs-first gate with nothing to read.

    `doctor` printed FAIL on "architecture docs exist" and the first
    `pipeline advance red` blocked on `docs.read_architecture` — "read the
    relevant doc under docs/architecture" — against an empty directory. `init`
    now writes a starter when the directory is absent, and only then: a
    directory that exists, even empty, is the project's.
    """

    def scaffold(self, project):
        return project / "docs" / "architecture" / "overview.md"

    def test_a_fresh_project_gets_a_starter_doc(self, project, capsys):
        import shutil

        shutil.rmtree(project / "docs")
        assert run("init", "--ecosystem", "python", "--name", "widgets") == 0
        assert "docs/architecture/overview.md" in out(capsys)
        body = self.scaffold(project).read_text(encoding="utf-8")
        assert "widgets" in body
        assert "## Layers" in body
        assert "```mermaid" in body
        assert "## Component responsibilities" in body
        assert "## Compliance checklist" in body
        assert "docs.read_architecture" in body and "docs.updated" in body

    def test_an_existing_docs_directory_is_left_alone(self, project, capsys):
        before = self.scaffold(project).read_text(encoding="utf-8")
        assert run("init", "--ecosystem", "python") == 0
        assert "overview.md" not in out(capsys)
        assert self.scaffold(project).read_text(encoding="utf-8") == before

    def test_an_empty_docs_directory_counts_as_present(self, project, capsys):
        import shutil

        shutil.rmtree(project / "docs")
        (project / "docs" / "architecture").mkdir(parents=True)
        assert run("init", "--ecosystem", "python") == 0
        assert "overview.md" not in out(capsys)
        assert list((project / "docs" / "architecture").iterdir()) == []

    def test_dry_run_lists_the_scaffold_and_writes_nothing(self, project, capsys):
        import shutil

        shutil.rmtree(project / "docs")
        assert run("init", "--dry-run") == 0
        assert "docs/architecture/overview.md" in out(capsys)
        assert not (project / "docs").exists()

    def test_force_does_not_touch_existing_docs(self, project, capsys):
        run("init", "--ecosystem", "python")
        self.scaffold(project).write_text("# mine\n", encoding="utf-8")
        assert run("init", "--force", "--ecosystem", "python") == 0
        assert self.scaffold(project).read_text(encoding="utf-8") == "# mine\n"

    def test_the_scaffold_passes_the_privacy_scan(self, project, capsys):
        """It is committed, and the project's own gate screens it."""
        import shutil

        shutil.rmtree(project / "docs")
        run("init", "--ecosystem", "python")
        assert self.scaffold(project).is_file()
        assert run("privacy", "scan", str(self.scaffold(project))) == 0
        assert "clean" in out(capsys)


class TestSessionStartDerivesItsBase:
    """`--base` defaulted to a literal `master`, and a `main` repository found
    out at `isolate --execute`: `fatal: invalid reference: master`."""

    def adopt_on(self, project, branch):
        subprocess.run(["git", "branch", "-m", "master", branch], cwd=project, check=True)
        run("init", "--ecosystem", "python")
        commit_all(project, "adopt invariant")

    def test_a_main_repository_isolates_with_no_flags(self, project, capsys):
        self.adopt_on(project, "main")
        out(capsys)  # drop init's output; the lines under test are start's
        assert run("session", "start", "add-widget") == 0
        lines = out(capsys).splitlines()
        # Line one is the host's binding contract and stays exactly as it was.
        assert lines[0].startswith("session ") and lines[0].endswith("registered")
        assert any("base" in line and "main" in line for line in lines[1:])
        assert run("session", "isolate", "--execute") == 0
        run("session", "status", "--json")
        session = payload(capsys)["session"]
        assert session["base"] == "main"
        assert Path(session["worktree"]).is_dir()

    def test_an_explicit_base_wins(self, project, capsys):
        self.adopt_on(project, "main")
        subprocess.run(["git", "branch", "release"], cwd=project, check=True)
        assert run("--json", "session", "start", "x", "--base", "release") == 0
        assert payload(capsys)["session"]["base"] == "release"

    def test_a_detached_head_is_refused_naming_the_flag(self, project, capsys):
        self.adopt_on(project, "main")
        subprocess.run(["git", "switch", "-q", "--detach"], cwd=project, check=True)
        assert run("session", "start", "x") == 2
        assert "--base" in capsys.readouterr().err
