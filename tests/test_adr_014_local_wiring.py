"""ADR-014 increment 3: D11 for Claude Code, as ADR-020 D9 amends it.

`records.hooks` and `records.agents` take `repo` or `local`; `user` is retired.
`local` wires the project's own `.claude/` with nothing committed: the hook
entries in `.claude/settings.local.json`, the shims in `.claude/hooks/`, the
agents in `.claude/agents/`, each path in `.git/info/exclude`, and the store's
main checkout in `permissions.additionalDirectories`. A receipt in the state
root makes uninstall the exact inverse. `session isolate` renders the wiring
into a new worktree. Nothing is written at user level, and `init` installs
nothing unless the user says so.

Every test runs with a scratch `HOME` and the suite's scratch
`INVARIANT_HOME`; the real `~/.claude` is never read or written. A live Claude
Code firing cannot run here: the wiring files are proven instead, and each
shim is run as the host would run it.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.errors import ManifestError
from invariant.core.home import invariant_home
from invariant.core.manifest import Manifest
from invariant.hosts.claude_code import EVENTS, ClaudeCodeHost
from invariant.hosts.claude_code_wiring import LOCAL, REPO, Receipt, wiring_plan

ORIGIN = "https://host.example/team/widgets.git"
IDENTITY = "host.example/team/widgets"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def run(capsys, *argv) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def tree(path: Path) -> dict[str, bytes]:
    """Every file under ``path`` with its bytes: what a scratch HOME holds."""
    if not path.exists():
        return {}
    return {
        str(p.relative_to(path)): p.read_bytes()
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A scratch HOME, seeded with a user-level Claude Code settings file that
    must come out byte for byte as it went in."""
    scratch = tmp_path / "home"
    (scratch / ".claude").mkdir(parents=True)
    (scratch / ".claude" / "settings.json").write_text('{"theme": "dark"}\n', encoding="utf-8")
    monkeypatch.setenv("HOME", str(scratch))
    return scratch


@pytest.fixture
def repo(tmp_path, monkeypatch, home):
    root = tmp_path / "widgets"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    git(root, "remote", "add", "origin", ORIGIN)
    monkeypatch.chdir(root)
    return root


def store_project(repo: Path, capsys, records: dict | None = None) -> Path:
    """`store init`, then this repository's block sets the wiring records."""
    code, text = run(capsys, "store", "init")
    assert code == 0, text
    store = invariant_home() / "stores" / "widgets"
    body = yaml.safe_load((store / "invariant.yaml").read_text(encoding="utf-8"))
    body["store"]["repos"][IDENTITY] = {
        "records": records if records is not None else {"hooks": "local", "agents": "local"}
    }
    (store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    return store


def exclude_file(repo: Path) -> Path:
    return repo / ".git" / "info" / "exclude"


def install(capsys) -> None:
    for command in (("hooks", "install"), ("agents", "install")):
        code, text = run(capsys, *command)
        assert code == 0, text


def receipt_path(repo: Path) -> Path:
    return repo / ".git" / "invariant" / "state" / "host" / "claude_code" / "installed.json"


def ours(settings: dict) -> int:
    """How many hook entries point at invariant's shims, across every event."""
    return sum(
        "/.claude/hooks/invariant-" in json.dumps(entry)
        for entries in (settings.get("hooks") or {}).values()
        for entry in entries
    )


# -- the records ----------------------------------------------------------


class TestRecords:
    @pytest.mark.parametrize("key", ["hooks", "agents"])
    def test_local_loads(self, tmp_path, key):
        m = Manifest.from_raw(
            {"records": {key: "local"}}, root=tmp_path, path=tmp_path / "m.yaml", env={}
        )
        assert m.get(f"records.{key}") == "local"

    @pytest.mark.parametrize("key", ["hooks", "agents"])
    def test_user_is_retired_with_the_fix_named(self, tmp_path, key):
        with pytest.raises(ManifestError) as caught:
            Manifest.from_raw(
                {"records": {key: "user"}}, root=tmp_path, path=tmp_path / "m.yaml", env={}
            )
        text = str(caught.value)
        assert "retired" in text
        assert "local" in text
        assert "ADR-020 D9" in text


# -- the plan ---------------------------------------------------------------


class TestWiringPlan:
    def test_repo_is_todays_committed_wiring(self, tmp_path):
        plan = wiring_plan(tmp_path, REPO)
        assert plan.settings == tmp_path / ".claude" / "settings.json"
        assert plan.hooks_dir == tmp_path / ".claude" / "hooks"
        assert plan.agents_dir == tmp_path / ".claude" / "agents"
        assert plan.additional_directories == ()
        assert plan.committed

    def test_local_is_the_personal_settings_file_and_the_store(self, tmp_path):
        plan = wiring_plan(tmp_path, LOCAL, store_root=tmp_path / "store")
        assert plan.settings == tmp_path / ".claude" / "settings.local.json"
        assert plan.additional_directories == (str(tmp_path / "store"),)
        assert not plan.committed
        assert plan.exclude_line(plan.settings) == "/.claude/settings.local.json"

    def test_an_unknown_location_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="user"):
            wiring_plan(tmp_path, "user")

    def test_the_host_reads_each_record(self, repo, capsys):
        store_project(repo, capsys, {"hooks": "local", "agents": "repo"})
        host = ClaudeCodeHost(repo, manifest=Manifest.locate(repo))
        assert host.wiring_plan(host.hooks_location).location == LOCAL
        assert host.wiring_plan(host.agents_location).location == REPO


# -- install, in a store project ------------------------------------------


class TestLocalInstall:
    def test_git_status_is_empty_after_install(self, repo, capsys, home):
        store = store_project(repo, capsys)
        before_home = tree(home)
        install(capsys)

        assert git(repo, "status", "--porcelain", "--untracked-files=all") == ""
        settings = json.loads((repo / ".claude" / "settings.local.json").read_text())
        assert ours(settings) == len(EVENTS)
        assert settings["permissions"]["additionalDirectories"] == [str(store)]
        assert not (repo / ".claude" / "settings.json").exists()
        for event in EVENTS:
            assert os.access(repo / ".claude" / "hooks" / f"invariant-{event}.sh", os.X_OK)
        assert (repo / ".claude" / "agents" / "feature-architect.md").is_file()
        lines = exclude_file(repo).read_text().splitlines()
        assert "/.claude/settings.local.json" in lines
        assert "/.claude/hooks/invariant-stop.sh" in lines
        assert "/.claude/agents/feature-architect.md" in lines
        assert not (repo / ".gitignore").exists()
        assert tree(home) == before_home

    def test_the_receipt_lists_what_was_added(self, repo, capsys):
        store = store_project(repo, capsys)
        install(capsys)
        receipt = json.loads(receipt_path(repo).read_text())
        assert receipt["settings"]["path"] == ".claude/settings.local.json"
        hooks = receipt["settings"]["hooks"]
        assert sorted(hooks) == sorted(EVENTS.values())
        assert receipt["settings"]["additional_directories"] == [str(store)]
        assert "/.claude/agents/feature-architect.md" in receipt["exclude"]["lines"]
        agent = repo / ".claude" / "agents" / "feature-architect.md"
        digest = hashlib.sha256(agent.read_bytes()).hexdigest()
        assert receipt["files"][".claude/agents/feature-architect.md"] == digest

    def test_a_second_install_adds_nothing(self, repo, capsys):
        store_project(repo, capsys)
        install(capsys)
        snapshot = {
            p: p.read_bytes()
            for p in (
                exclude_file(repo),
                repo / ".claude" / "settings.local.json",
                receipt_path(repo),
            )
        }
        install(capsys)
        assert {p: p.read_bytes() for p in snapshot} == snapshot

    def test_an_existing_local_settings_file_keeps_its_own_keys(self, repo, capsys):
        store_project(repo, capsys)
        (repo / ".claude").mkdir()
        (repo / ".claude" / "settings.local.json").write_text(
            '{"permissions": {"allow": ["Bash(ls)"]}}', encoding="utf-8"
        )
        install(capsys)
        settings = json.loads((repo / ".claude" / "settings.local.json").read_text())
        assert settings["permissions"]["allow"] == ["Bash(ls)"]
        assert ours(settings) == len(EVENTS)

    def test_one_firing_per_event_in_the_main_checkout(self, repo, capsys):
        """One place: invariant's entries are in settings.local.json alone,
        once per event, and each shim answers as the host would call it."""
        store_project(repo, capsys)
        install(capsys)
        settings = json.loads((repo / ".claude" / "settings.local.json").read_text())
        for host_event in EVENTS.values():
            assert ours({"hooks": {host_event: settings["hooks"][host_event]}}) == 1
        shim = repo / ".claude" / "hooks" / "invariant-session-start.sh"
        assert "invariant hook session-start" in shim.read_text().replace('"$candidate"', "invariant")

    def test_repo_wiring_in_an_in_repo_project_is_unchanged(self, repo, capsys):
        code, text = run(capsys, "init", "--ecosystem", "python", "--host-install")
        assert code == 0, text
        assert ours(json.loads((repo / ".claude" / "settings.json").read_text())) == len(EVENTS)
        assert not (repo / ".claude" / "settings.local.json").exists()
        assert not receipt_path(repo).exists()
        assert "/.claude/" not in exclude_file(repo).read_text()


# -- uninstall, the exact inverse ------------------------------------------


class TestLocalUninstall:
    def test_settings_and_exclude_come_back_byte_for_byte(self, repo, capsys):
        store_project(repo, capsys)
        (repo / ".claude").mkdir()
        settings = repo / ".claude" / "settings.local.json"
        own = '{\n    "permissions" : {"allow": ["Bash(ls)"]},\n  "model": "x"\n}'
        settings.write_text(own, encoding="utf-8")
        exclude = exclude_file(repo)
        exclude.write_text(exclude.read_text() + "*.swp", encoding="utf-8")
        before = exclude.read_bytes()
        status = git(repo, "status", "--porcelain", "--untracked-files=all")

        install(capsys)
        assert git(repo, "status", "--porcelain", "--untracked-files=all") == ""
        code, text = run(capsys, "uninstall")
        assert code == 0, text

        assert settings.read_bytes() == own.encode()
        assert exclude.read_bytes() == before
        assert not (repo / ".claude" / "hooks").exists()
        assert not (repo / ".claude" / "agents").exists()
        assert not receipt_path(repo).exists()
        assert git(repo, "status", "--porcelain", "--untracked-files=all") == status

    def test_a_file_it_created_is_removed_with_its_directory(self, repo, capsys):
        store_project(repo, capsys)
        exclude_before = exclude_file(repo).read_bytes()
        install(capsys)
        code, text = run(capsys, "uninstall")
        assert code == 0, text
        assert not (repo / ".claude").exists()
        assert exclude_file(repo).read_bytes() == exclude_before

    def test_a_setting_the_user_added_after_install_survives(self, repo, capsys):
        store_project(repo, capsys)
        install(capsys)
        path = repo / ".claude" / "settings.local.json"
        settings = json.loads(path.read_text())
        settings["model"] = "mine"
        path.write_text(json.dumps(settings), encoding="utf-8")
        exclude = exclude_file(repo)
        exclude.write_text(exclude.read_text() + "mine.log\n", encoding="utf-8")

        code, text = run(capsys, "uninstall")
        assert code == 0, text
        assert json.loads(path.read_text()) == {"model": "mine"}
        assert "mine.log" in exclude.read_text().splitlines()
        # The settings file invariant created stays, holding the user's key,
        # so its exclude line stays with it: git status shows nothing new.
        assert "/.claude/settings.local.json" in exclude.read_text().splitlines()
        assert "/.claude/hooks/invariant-stop.sh" not in exclude.read_text()
        assert git(repo, "status", "--porcelain", "--untracked-files=all") == ""

    def test_an_agent_edited_after_install_is_left_and_named(self, repo, capsys):
        store_project(repo, capsys)
        install(capsys)
        agent = repo / ".claude" / "agents" / "feature-architect.md"
        agent.write_text(agent.read_text() + "\nmy own note\n", encoding="utf-8")

        code, text = run(capsys, "uninstall")
        assert code == 0, text
        assert agent.is_file()
        assert "feature-architect" in text
        assert "edited" in text
        assert not (repo / ".claude" / "agents" / "compliance-reviewer.md").exists()
        assert "/.claude/agents/feature-architect.md" in exclude_file(repo).read_text()
        assert git(repo, "status", "--porcelain", "--untracked-files=all") == ""

    def test_dry_run_names_the_receipt_and_removes_nothing(self, repo, capsys):
        store_project(repo, capsys)
        install(capsys)
        before = tree(repo / ".claude")
        code, text = run(capsys, "uninstall", "--dry-run")
        assert code == 0, text
        assert "settings.local.json" in text
        assert ".git/info/exclude" in text
        assert tree(repo / ".claude") == before

    def test_a_committed_settings_file_of_the_users_is_not_rewritten(self, repo, capsys):
        (repo / ".claude").mkdir()
        mine = repo / ".claude" / "settings.json"
        mine.write_text('{"permissions":{"allow":["Bash(ls)"]}}', encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "settings")
        store_project(repo, capsys)
        install(capsys)
        code, text = run(capsys, "uninstall")
        assert code == 0, text
        assert mine.read_text() == '{"permissions":{"allow":["Bash(ls)"]}}'
        assert git(repo, "status", "--porcelain", "--untracked-files=all") == ""

    def test_the_scratch_home_is_untouched_throughout(self, repo, capsys, home):
        before = tree(home)
        store_project(repo, capsys)
        install(capsys)
        run(capsys, "doctor")
        run(capsys, "uninstall")
        assert tree(home) == before


# -- doctor --------------------------------------------------------------


def doctor_rows(capsys) -> dict[str, dict]:
    _code, text = run(capsys, "doctor", "--json")
    return {row["check"]: row for row in json.loads(text)["checks"]}


class TestDoctor:
    def test_local_wiring_passes_and_says_where(self, repo, capsys):
        store_project(repo, capsys)
        install(capsys)
        row = doctor_rows(capsys)["host wiring"]
        assert row["ok"], row
        assert "local" in row["detail"]

    def test_not_installed_passes_and_names_the_commands(self, repo, capsys):
        store_project(repo, capsys)
        rows = doctor_rows(capsys)
        row = rows["host wiring"]
        assert row["ok"], row
        assert "not installed" in row["detail"]
        assert "invariant hooks install" in row["detail"]
        assert "invariant agents install" in row["detail"]
        assert rows["hook shims installed"]["ok"]
        assert rows["agents loadable by host"]["ok"]

    def test_a_missing_exclude_line_fails(self, repo, capsys):
        store_project(repo, capsys)
        install(capsys)
        exclude = exclude_file(repo)
        exclude.write_text(
            exclude.read_text().replace("/.claude/hooks/invariant-stop.sh\n", ""),
            encoding="utf-8",
        )
        row = doctor_rows(capsys)["host wiring"]
        assert not row["ok"]
        assert "/.claude/hooks/invariant-stop.sh" in row["detail"]
        assert ".git/info/exclude" in row["detail"]

    def test_a_receipt_entry_missing_from_the_settings_fails(self, repo, capsys):
        store_project(repo, capsys)
        install(capsys)
        path = repo / ".claude" / "settings.local.json"
        settings = json.loads(path.read_text())
        del settings["hooks"]["Stop"]
        path.write_text(json.dumps(settings), encoding="utf-8")
        row = doctor_rows(capsys)["host wiring"]
        assert not row["ok"]
        assert "Stop" in row["detail"]

    def test_the_hook_rows_read_the_local_settings(self, repo, capsys):
        store_project(repo, capsys)
        install(capsys)
        rows = doctor_rows(capsys)
        assert rows["hooks wired in settings"]["ok"], rows["hooks wired in settings"]
        assert "settings.local.json" in rows["hooks wired in settings"]["detail"]
        assert rows["hook shims actually run"]["ok"], rows["hook shims actually run"]


# -- session worktrees ---------------------------------------------------


class TestIsolate:
    def test_isolate_renders_the_local_wiring_into_the_worktree(self, repo, capsys):
        store = store_project(repo, capsys)
        install(capsys)
        code, text = run(capsys, "session", "start", "wiring", "--id", "abc12345")
        assert code == 0, text
        code, text = run(capsys, "session", "isolate", "--id", "abc12345", "--execute")
        assert code == 0, text
        worktree = repo.parent / "widgets-wt-abc12345"

        settings = json.loads((worktree / ".claude" / "settings.local.json").read_text())
        assert ours(settings) == len(EVENTS)
        assert settings["permissions"]["additionalDirectories"] == [str(store)]
        for event in EVENTS:
            assert os.access(worktree / ".claude" / "hooks" / f"invariant-{event}.sh", os.X_OK)
        assert (worktree / ".claude" / "agents" / "feature-architect.md").is_file()
        assert git(worktree, "status", "--porcelain", "--untracked-files=all") == ""
        assert "rendered" in text

        # The receipt still describes the main checkout alone.
        receipt = json.loads(receipt_path(repo).read_text())
        assert receipt["root"] == str(repo)

    def test_a_worktree_host_fires_once(self, repo, capsys):
        store_project(repo, capsys)
        install(capsys)
        run(capsys, "session", "start", "wiring", "--id", "abc12345")
        run(capsys, "session", "isolate", "--id", "abc12345", "--execute")
        worktree = repo.parent / "widgets-wt-abc12345"
        settings = json.loads((worktree / ".claude" / "settings.local.json").read_text())
        for host_event in EVENTS.values():
            assert ours({"hooks": {host_event: settings["hooks"][host_event]}}) == 1
        assert not (worktree / ".claude" / "settings.json").exists()
        check = ClaudeCodeHost(worktree, manifest=Manifest.locate(worktree)).hooks_run_check()
        assert check[1], check

    def test_isolate_renders_nothing_where_nothing_is_installed(self, repo, capsys):
        store_project(repo, capsys)
        run(capsys, "session", "start", "wiring", "--id", "abc12345")
        code, text = run(capsys, "session", "isolate", "--id", "abc12345", "--execute")
        assert code == 0, text
        assert not (repo.parent / "widgets-wt-abc12345" / ".claude").exists()

    def test_the_worktree_stays_removable_holding_the_rendered_wiring(self, repo, capsys):
        """Teardown runs `git worktree remove` without --force, which refuses
        a worktree with untracked files; the rendered files are excluded, so
        git counts the worktree clean."""
        store_project(repo, capsys)
        install(capsys)
        run(capsys, "session", "start", "wiring", "--id", "abc12345")
        run(capsys, "session", "isolate", "--id", "abc12345", "--execute")
        worktree = repo.parent / "widgets-wt-abc12345"
        assert (worktree / ".claude" / "settings.local.json").is_file()
        git(repo, "worktree", "remove", str(worktree))
        assert not worktree.exists()


# -- instructions ---------------------------------------------------------


class TestInstructions:
    def test_no_managed_block_under_local_agents(self, repo, capsys):
        store_project(repo, capsys)
        m = Manifest.locate(repo)
        assert cli_main.host_instruction_files(m) == ()

    @pytest.mark.parametrize(("agents", "gap"), [("local", False), ("repo", True)])
    def test_no_always_layer_gap_is_raised_for_a_block_never_written(
        self, tmp_path, agents, gap
    ):
        """With no instruction file to carry it, the always layer is the hook
        context's under local agents, and a gap only under repo."""
        from types import SimpleNamespace

        from invariant.core.rules import _always_layer_gap

        plan = SimpleNamespace(always=["conduct.x"], blocks=[])
        m = Manifest.from_raw(
            {"records": {"agents": agents}}, root=tmp_path, path=tmp_path / "m.yaml", env={}
        )
        assert (_always_layer_gap(plan, m) is not None) is gap

    def test_repo_agents_keep_claude_md(self, repo, capsys):
        store_project(repo, capsys, {"hooks": "local", "agents": "repo"})
        m = Manifest.locate(repo)
        assert cli_main.host_instruction_files(m) == ("CLAUDE.md",)


# -- init asks (ADR-020 D9) ------------------------------------------------


class TestInitAsks:
    def test_a_non_interactive_init_installs_nothing_and_names_the_commands(
        self, repo, capsys, home
    ):
        before = tree(home)
        code, text = run(capsys, "init", "--ecosystem", "python")
        assert code == 0, text
        assert not (repo / ".claude" / "hooks").exists()
        assert not (repo / ".claude" / "settings.json").exists()
        assert not (repo / ".claude" / "agents").exists()
        assert "invariant hooks install" in text
        assert "invariant agents install" in text
        assert tree(home) == before

    def test_json_says_nothing_was_installed(self, repo, capsys):
        code, text = run(capsys, "init", "--ecosystem", "python", "--json")
        assert code == 0, text
        payload = json.loads(text)
        assert payload["host_install"] == {
            "installed": False,
            "commands": ["invariant hooks install", "invariant agents install"],
        }

    def test_host_install_flag_installs(self, repo, capsys):
        code, text = run(capsys, "init", "--ecosystem", "python", "--host-install")
        assert code == 0, text
        assert (repo / ".claude" / "hooks" / "invariant-stop.sh").is_file()
        assert (repo / ".claude" / "agents" / "feature-architect.md").is_file()

    def test_the_dry_run_lists_no_install_unless_asked(self, repo, capsys):
        code, text = run(capsys, "init", "--ecosystem", "python", "--dry-run")
        assert code == 0, text
        assert "install 7 hook shims" not in text
        assert "invariant hooks install" in text
        code, text = run(
            capsys, "init", "--ecosystem", "python", "--dry-run", "--host-install"
        )
        assert "install 7 hook shims" in text

    @pytest.mark.parametrize(("answer", "installed"), [("yes", True), ("no", False), ("", False)])
    def test_an_interactive_init_asks_with_default_no(
        self, repo, capsys, monkeypatch, answer, installed
    ):
        asked: list[tuple[str, str]] = []

        class Prompter:
            def say(self, text):
                pass

            def choose(self, question, options, default):
                asked.append((question, default))
                return answer or default

            def ask(self, question, default=""):
                return default

        monkeypatch.setattr(cli_main, "_host_install_prompter", lambda args: Prompter())
        code, text = run(capsys, "init", "--ecosystem", "python")
        assert code == 0, text
        assert asked == [("Install invariant's hooks and agents in this project?", "no")]
        assert (repo / ".claude" / "hooks").exists() is installed


# -- the linked-identity index is gone ---------------------------------------


class TestNoLinkedIndex:
    def test_store_link_writes_no_index(self, repo, capsys):
        store_project(repo, capsys)
        assert not (invariant_home() / "linked-repos.txt").exists()


class TestReceipt:
    def test_an_absent_receipt_loads_empty(self, tmp_path):
        receipt = Receipt.load(tmp_path / "installed.json", tmp_path)
        assert receipt.empty


class TestRefusalsAndDrift:
    def test_a_malformed_local_settings_file_is_refused_not_overwritten(self, repo, capsys):
        store_project(repo, capsys)
        (repo / ".claude").mkdir()
        bad = repo / ".claude" / "settings.local.json"
        bad.write_text("{not json", encoding="utf-8")
        code, text = run(capsys, "hooks", "install")
        assert code != 0
        assert "settings.local.json is not valid JSON" in text
        assert bad.read_text() == "{not json"

    def test_a_malformed_receipt_is_refused(self, repo, capsys):
        store_project(repo, capsys)
        install(capsys)
        receipt_path(repo).write_text("{", encoding="utf-8")
        code, text = run(capsys, "uninstall")
        assert code != 0
        assert "receipt" in text

    def test_a_granted_directory_and_a_file_gone_missing_are_named(self, repo, capsys):
        store = store_project(repo, capsys)
        install(capsys)
        path = repo / ".claude" / "settings.local.json"
        settings = json.loads(path.read_text())
        settings["permissions"]["additionalDirectories"] = []
        path.write_text(json.dumps(settings), encoding="utf-8")
        (repo / ".claude" / "agents" / "bug-reproducer.md").unlink()
        row = doctor_rows(capsys)["host wiring"]
        assert not row["ok"]
        assert f"{store} is missing" in row["detail"]
        assert ".claude/agents/bug-reproducer.md is missing" in row["detail"]

    def test_a_second_uninstall_is_a_no_op(self, repo, capsys):
        store_project(repo, capsys)
        exclude_before = exclude_file(repo).read_bytes()
        install(capsys)
        assert run(capsys, "uninstall")[0] == 0
        code, text = run(capsys, "uninstall")
        assert code == 0, text
        assert exclude_file(repo).read_bytes() == exclude_before

    def test_local_wiring_outside_git_is_refused(self, tmp_path):
        from invariant.core.errors import UsageError
        from invariant.hosts.claude_code_wiring import add_exclude

        receipt = Receipt.load(tmp_path / "r.json", tmp_path)
        with pytest.raises(UsageError, match="not in a git repository"):
            add_exclude(tmp_path, ["/x"], receipt)

    def test_a_line_the_user_appended_after_ours_survives(self, repo, capsys):
        store_project(repo, capsys)
        install(capsys)
        exclude = exclude_file(repo)
        exclude.write_text(exclude.read_text() + "late.log\n", encoding="utf-8")
        assert run(capsys, "uninstall")[0] == 0
        lines = exclude.read_text().splitlines()
        assert lines[-1] == "late.log"
        assert not any(line.startswith("/.claude/") for line in lines)


class TestMixedLocations:
    """Each record is read on its own: local hooks beside committed agents."""

    def test_local_hooks_and_repo_agents(self, repo, capsys):
        store_project(repo, capsys, {"hooks": "local", "agents": "repo"})
        install(capsys)
        lines = exclude_file(repo).read_text().splitlines()
        assert "/.claude/hooks/invariant-stop.sh" in lines
        assert not any(line.startswith("/.claude/agents/") for line in lines)
        # The committed agents show, to be committed; the local hooks do not.
        status = git(repo, "status", "--porcelain", "--untracked-files=all")
        assert "?? .claude/agents/feature-architect.md" in status
        assert ".claude/hooks/" not in status
        assert "settings.local.json" not in status
        row = doctor_rows(capsys)["host wiring"]
        assert row["ok"], row
        assert "hooks local, agents repo" in row["detail"]

        run(capsys, "session", "start", "wiring", "--id", "abc12345")
        code, text = run(capsys, "session", "isolate", "--id", "abc12345", "--execute")
        assert code == 0, text
        worktree = repo.parent / "widgets-wt-abc12345"
        assert (worktree / ".claude" / "settings.local.json").is_file()
        assert not (worktree / ".claude" / "agents").exists()

        code, text = run(capsys, "uninstall", "--remove-assets")
        assert code == 0, text
        assert not (repo / ".claude" / "hooks").exists()
        assert not (repo / ".claude" / "agents" / "feature-architect.md").exists()

    def test_the_install_plan_names_the_local_settings_and_the_exclude(self, repo, capsys):
        store_project(repo, capsys)
        host = ClaudeCodeHost(repo, manifest=Manifest.locate(repo))
        plan = "\n".join(host.plan_install())
        assert ".claude/settings.local.json" in plan
        assert ".git/info/exclude" in plan

    def test_remove_assets_leaves_the_receipt_to_take_local_agents(self, repo, capsys):
        store_project(repo, capsys)
        install(capsys)
        code, text = run(capsys, "uninstall", "--remove-assets", "--dry-run")
        assert code == 0, text
        assert "library asset(s)" not in text
        assert ".claude/agents/feature-architect.md" in text

    def test_a_malformed_settings_file_counts_as_installed(self, repo, capsys):
        store_project(repo, capsys)
        (repo / ".claude").mkdir()
        (repo / ".claude" / "settings.local.json").write_text("{", encoding="utf-8")
        assert ClaudeCodeHost(repo, manifest=Manifest.locate(repo)).hooks_installed()
