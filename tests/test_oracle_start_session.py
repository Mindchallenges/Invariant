"""IDEA_030 increment 1: the oracle starts a new Claude window in a registered project.

Three layers, each tested on its own: the shared launcher in
``hosts/claude_code_launch`` (an optional first prompt, the default
permission mode only, no inherited claim token), the pure facts and naming in
``oracle/startsession``, and ``invariant oracle start-session`` joining them
behind the CLI's seams. No test opens a window, reads the process table or
reads the real ``~/.claude``: the launcher, the successor watch and the
names-in-use reader are the suite's recorders (``tests/conftest.py``).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.errors import InvariantError
from invariant.hosts import claude_code_launch as launch
from invariant.oracle import startsession
from invariant.oracle.scan import ScanCache

#: A first prompt carrying everything the quoting must survive. Synthetic.
PROMPT = (
    "Look at the flaky test: it's \"quoted\", back\\slash $HOME `tick` *glob* ; && |\n"
    "second line · arrow ->"
)


# -- the shared launcher --------------------------------------------------


class TestAPlanWithoutAPrompt:
    def test_the_command_ends_at_the_remote_control_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr(launch, "launch_dir", lambda: tmp_path / "launch")
        plan = launch.plan_launch("/opt/bin/claude", "demo-1026-0930", None, tmp_path)
        assert plan.argv == (
            "/opt/bin/claude", "--name", "demo-1026-0930",
            "--remote-control", "demo-1026-0930",
        )
        lines = Path(plan.launch_file).read_text(encoding="utf-8").splitlines()
        assert tuple(shlex.split(lines[2])) == ("exec", *plan.argv)

    def test_an_empty_prompt_is_no_prompt(self, tmp_path):
        plan = launch.plan_launch("/opt/bin/claude", "x", "", tmp_path, stage=False)
        assert plan.argv[-1] == "x"
        assert len(plan.argv) == 5


class TestTheDefaultPermissionModeOnly:
    @pytest.mark.parametrize(
        "flag",
        [
            "--dangerously-skip-permissions",
            "--allow-dangerously-skip-permissions",
            "--permission-mode",
            "--permission-mode=bypassPermissions",
        ],
    )
    def test_a_plan_carrying_a_permission_flag_cannot_be_built(self, flag):
        with pytest.raises(InvariantError, match="permission"):
            launch.LaunchPlan(
                argv=("/opt/bin/claude", flag, "--name", "x", "--remote-control", "x"),
                cwd="/w",
                shell_line="",
                applescript="",
                name="x",
            )

    @pytest.mark.parametrize("prompt", ["--dangerously-skip-permissions", "-p hello", "-"])
    def test_a_prompt_that_reads_as_a_flag_is_refused(self, prompt, tmp_path):
        with pytest.raises(InvariantError, match="flag"):
            launch.plan_launch("/opt/bin/claude", "x", prompt, tmp_path, stage=False)

    def test_a_prompt_that_only_mentions_a_flag_is_data(self, tmp_path):
        prompt = "why does --permission-mode exist?"
        plan = launch.plan_launch("/opt/bin/claude", "x", prompt, tmp_path, stage=False)
        assert plan.argv[-1] == prompt


class TestNoInheritedClaimToken:
    """A window this launch opens claims no note it was not launched for."""

    def test_the_typed_line_unsets_the_token_when_it_names_none(self, tmp_path):
        plan = launch.plan_launch("/opt/bin/claude", "x", None, tmp_path, stage=False)
        words = shlex.split(plan.shell_line)
        unset = {words[i + 1] for i, word in enumerate(words) if word == "-u"}
        assert launch.HANDOFF_ENV in unset
        assert not any(w.startswith(f"{launch.HANDOFF_ENV}=") for w in words)

    def test_a_real_shell_starts_claude_without_the_inherited_token(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(launch, "launch_dir", lambda: tmp_path / "launch")
        captured = tmp_path / "seen.json"
        fake = tmp_path / "claude"
        fake.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            f"json.dump({{'argv': sys.argv[1:], 'env': dict(os.environ)}}, "
            f"open({str(captured)!r}, 'w'))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        inherited = {"PATH": os.environ["PATH"], launch.HANDOFF_ENV: "old:note"}

        plan = launch.plan_launch(str(fake), "demo-x", PROMPT, tmp_path)
        subprocess.run(["/bin/sh", "-c", plan.shell_line], check=True, env=inherited)

        got = json.loads(captured.read_text(encoding="utf-8"))
        assert got["argv"] == ["--name", "demo-x", "--remote-control", "demo-x", PROMPT]
        assert launch.HANDOFF_ENV not in got["env"]
        assert not Path(plan.launch_file).exists()

    def test_a_given_token_still_reaches_claude(self, tmp_path):
        plan = launch.plan_launch("/opt/bin/claude", "x", "y", tmp_path, token="s:t",
                                  stage=False)
        assert f"{launch.HANDOFF_ENV}=s:t" in shlex.split(plan.shell_line)


class TestNamesInUse:
    TABLE = (
        "  101 /usr/local/bin/claude --name heap-1026-0930 --remote-control heap-1026-0930\n"
        "  102 node /opt/lib/claude --rc only-rc\n"
        "  103 claude -n short-name\n"
        "  104 claude --name=eq-name --remote-control=eq-rc\n"
        "  105 grep --name not-claude\n"
        "  106 /bin/sh /tmp/handoff-x.sh --name not-claude-either\n"
        "  107 claude --remote-control\n"
        "garbage\n"
    )

    def test_the_process_table_names_every_claude_name_and_rc_name(self):
        assert launch.command_names_in_use(self.TABLE) == {
            "heap-1026-0930", "only-rc", "short-name", "eq-name", "eq-rc",
        }

    def test_live_records_add_their_names_and_dead_ones_do_not(self):
        records = [
            launch.HostProcessRecord(session_id="a", pid=7, name="live-one"),
            launch.HostProcessRecord(session_id="b", pid=8, name="dead-one"),
            launch.HostProcessRecord(session_id="c", pid=9, name=None),
        ]
        names = launch.names_in_use(records, "", alive=lambda pid: pid in (7, 9))
        assert names == {"live-one"}

    def test_both_sources_join(self):
        records = [launch.HostProcessRecord(session_id="a", pid=7, name="rec")]
        names = launch.names_in_use(records, self.TABLE, alive=lambda pid: True)
        assert {"rec", "only-rc", "heap-1026-0930"} <= names


# -- the facts and the name -----------------------------------------------


def _project_doc(*rows):
    return {"project": {"key": "/k", "root": "/r", "name": "demo"}, "rows": list(rows)}


class TestTheOpenWindowCount:
    def test_counts_each_live_host_once_across_sessions_and_orphans(self):
        doc = _project_doc(
            {"kind": "session", "hosts": [
                {"host_session_id": "a", "alive": True},
                {"host_session_id": "b", "alive": False},
                {"host_session_id": "u", "alive": None},
            ]},
            {"kind": "session", "hosts": [{"host_session_id": "a", "alive": True}]},
            {"kind": "host", "hosts": [{"host_session_id": "c", "alive": True}]},
            {"kind": "worktree", "hosts": []},
            {"kind": "error"},
        )
        assert startsession.open_windows(doc) == 2

    def test_nothing_running_is_zero(self):
        assert startsession.open_windows(_project_doc()) == 0
        assert startsession.open_windows({}) == 0


class TestTheDirtyMainCheckout:
    @pytest.fixture
    def repo(self, tmp_path):
        root = tmp_path / "repo"
        root.mkdir()
        for argv in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "t@example.invalid"],
            ["git", "config", "user.name", "T"],
        ):
            subprocess.run(argv, cwd=root, check=True)
        (root / "a.txt").write_text("a\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
        return root

    def test_a_clean_checkout_has_none(self, repo):
        assert startsession.checkout_changes(repo) == 0

    def test_edited_and_untracked_files_count(self, repo):
        (repo / "a.txt").write_text("b\n", encoding="utf-8")
        (repo / "new.txt").write_text("n\n", encoding="utf-8")
        assert startsession.checkout_changes(repo) == 2

    def test_the_read_takes_no_index_lock(self, repo, monkeypatch):
        seen = {}
        real = subprocess.run

        def spy(args, *rest, **kwargs):
            if list(args)[:2] == ["git", "status"]:
                seen["env"] = kwargs.get("env") or {}
            return real(args, *rest, **kwargs)

        monkeypatch.setattr(subprocess, "run", spy)
        startsession.checkout_changes(repo)
        assert seen["env"].get("GIT_OPTIONAL_LOCKS") == "0"

    def test_a_directory_git_cannot_read_is_unknown(self, tmp_path):
        assert startsession.checkout_changes(tmp_path / "missing") is None


class TestTheFacts:
    def test_they_carry_the_count_and_the_checkout_state(self):
        doc = _project_doc({"kind": "host", "hosts": [{"host_session_id": "c", "alive": True}]})
        assert startsession.start_facts(doc, 3) == {
            "open_windows": 1, "uncommitted": 3, "dirty": True,
        }
        assert startsession.start_facts(doc, 0)["dirty"] is False
        assert startsession.start_facts(doc, None)["dirty"] is None

    def test_the_lines_say_both_and_block_nothing(self):
        lines = startsession.fact_lines({"open_windows": 2, "uncommitted": 3, "dirty": True})
        text = "\n".join(lines)
        assert "2 Claude windows" in text and "3 uncommitted" in text
        one = "\n".join(startsession.fact_lines(
            {"open_windows": 1, "uncommitted": 0, "dirty": False}))
        assert "1 Claude window " in one + " " and "no uncommitted" in one
        unknown = "\n".join(startsession.fact_lines(
            {"open_windows": 0, "uncommitted": None, "dirty": None}))
        assert "no Claude window" in unknown and "could not tell" in unknown


class TestTheName:
    def test_the_default_label_is_the_month_day_hour_minute(self):
        moment = time.mktime((2026, 10, 26, 9, 5, 0, 0, 0, -1))
        assert startsession.default_label(moment) == "1026-0905"

    def test_the_name_is_the_project_and_the_label_slugged(self):
        assert startsession.session_name("My Project", "Fix It!", set()) == "my-project-fix-it"

    def test_a_clash_takes_the_next_free_suffix(self):
        taken = {"demo-x", "demo-x-2"}
        assert startsession.session_name("demo", "x", taken) == "demo-x-3"

    def test_a_label_with_no_word_is_refused(self):
        with pytest.raises(InvariantError):
            startsession.session_name("demo", "!!!", set())


# -- the command ------------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    for argv in (
        ["git", "init", "-q", "-b", "master"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(argv, cwd=root, check=True)
    monkeypatch.chdir(root)
    monkeypatch.delenv("INVARIANT_HOST_SESSION_ID", raising=False)
    assert main(["init", "--name", "demo"]) == 0
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "manifest"], cwd=root, check=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return root


@pytest.fixture(autouse=True)
def _scan_without_a_probe(monkeypatch):
    """The facts' scan reads no transcripts and no Claude Code records."""
    monkeypatch.setattr(cli_main, "_oracle_cache", lambda: ScanCache())


@pytest.fixture(autouse=True)
def _launch_files_here(monkeypatch, tmp_path):
    directory = tmp_path / "launch"
    monkeypatch.setattr(launch, "launch_dir", lambda: directory)
    return directory


@pytest.fixture
def macos_with_claude(monkeypatch, tmp_path):
    monkeypatch.setattr(launch, "host_platform", lambda: "darwin")
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(launch, "resolve_claude", lambda: str(fake))
    return fake


@pytest.fixture
def successor(_no_real_successor_watch):
    _no_real_successor_watch.pid = 4242
    return _no_real_successor_watch


def _json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


class TestTheCommand:
    def test_an_unregistered_project_is_refused_and_nothing_launches(
        self, project, capsys, _no_real_terminal_launch, macos_with_claude, successor
    ):
        assert main(["oracle", "start-session", "nope"]) == 2
        assert "no registered project matches 'nope'" in capsys.readouterr().err
        assert _no_real_terminal_launch.plans == []

    def test_a_free_path_outside_the_index_is_refused(
        self, project, tmp_path, capsys, _no_real_terminal_launch, macos_with_claude
    ):
        assert main(["oracle", "start-session", str(tmp_path)]) == 2
        assert _no_real_terminal_launch.plans == []

    def test_a_registered_project_opens_one_window_in_its_main_checkout(
        self, project, capsys, _no_real_terminal_launch, macos_with_claude, successor
    ):
        assert main(["oracle", "start-session", "demo", "--json"]) == 0
        out = _json(capsys)
        (plan,) = _no_real_terminal_launch.plans
        assert re.fullmatch(r"demo-\d{4}-\d{4}", out["name"])
        assert Path(out["cwd"]).resolve() == project.resolve()
        assert out["argv"] == [
            str(macos_with_claude), "--name", out["name"], "--remote-control", out["name"],
        ]
        assert list(plan.argv) == out["argv"]
        assert out["successor"] == {"pid": 4242}
        assert out["facts"] == {"open_windows": 0, "uncommitted": 0, "dirty": False}
        assert out["dry_run"] is False
        assert not any("permission" in word for word in plan.argv)

    def test_the_project_resolves_by_its_key_too(
        self, project, capsys, _no_real_terminal_launch, macos_with_claude, successor
    ):
        assert main(["oracle", "projects", "list", "--json"]) == 0
        key = _json(capsys)["projects"][0]["key"]
        assert main(["oracle", "start-session", key, "--json"]) == 0
        assert _json(capsys)["project"]["name"] == "demo"

    def test_the_prompt_reaches_claude_as_one_argument(
        self, project, capsys, _no_real_terminal_launch, macos_with_claude, successor
    ):
        assert main(["oracle", "start-session", "demo", "--prompt", PROMPT, "--json"]) == 0
        out = _json(capsys)
        (plan,) = _no_real_terminal_launch.plans
        assert plan.argv[-1] == PROMPT
        assert out["prompt"] == PROMPT
        lines = Path(plan.launch_file).read_text(encoding="utf-8").splitlines()
        assert shlex.split("\n".join(lines[2:]))[-1] == PROMPT

    def test_the_label_names_it(
        self, project, capsys, _no_real_terminal_launch, macos_with_claude, successor
    ):
        assert main(["oracle", "start-session", "demo", "--name", "Flaky Test", "--json"]) == 0
        assert _json(capsys)["name"] == "demo-flaky-test"

    def test_a_name_in_use_takes_a_suffix(
        self, project, capsys, monkeypatch, _no_real_terminal_launch, macos_with_claude,
        successor
    ):
        monkeypatch.setattr(cli_main, "_oracle_names_in_use", lambda: {"demo-x", "demo-x-2"})
        assert main(["oracle", "start-session", "demo", "--name", "x", "--json"]) == 0
        assert _json(capsys)["name"] == "demo-x-3"

    def test_the_facts_are_reported_and_block_nothing(
        self, project, capsys, _no_real_terminal_launch, macos_with_claude, successor
    ):
        (project / "scratch.txt").write_text("x\n", encoding="utf-8")
        assert main(["oracle", "start-session", "demo"]) == 0
        out = capsys.readouterr().out
        assert "no Claude window" in out
        assert "1 uncommitted" in out
        assert len(_no_real_terminal_launch.plans) == 1

    def test_the_text_names_the_window_and_the_command(
        self, project, capsys, _no_real_terminal_launch, macos_with_claude, successor
    ):
        assert main(["oracle", "start-session", "demo", "--name", "x"]) == 0
        out = capsys.readouterr().out
        assert "demo-x" in out and "pid 4242" in out
        assert "--remote-control demo-x" in out

    def test_a_prompt_that_reads_as_a_flag_is_refused(
        self, project, capsys, _no_real_terminal_launch, macos_with_claude, successor
    ):
        argv = ["oracle", "start-session", "demo", "--prompt=--dangerously-skip-permissions"]
        assert main(argv) == 1
        assert "flag" in capsys.readouterr().err
        assert _no_real_terminal_launch.plans == []

    def test_a_dry_run_launches_nothing_and_writes_nothing(
        self, project, capsys, _no_real_terminal_launch, macos_with_claude, _launch_files_here
    ):
        assert main(["oracle", "start-session", "demo", "--dry-run", "--json"]) == 0
        out = _json(capsys)
        assert out["dry_run"] is True and out["successor"] is None
        assert out["by_hand"].startswith(f"cd {shlex.quote(str(out['cwd']))} && env ")
        assert _no_real_terminal_launch.plans == []
        assert not _launch_files_here.exists() or not any(_launch_files_here.iterdir())

    def test_a_dry_run_text_says_nothing_ran(
        self, project, capsys, monkeypatch, _no_real_terminal_launch
    ):
        monkeypatch.setattr(launch, "resolve_claude", lambda: None)
        assert main(["oracle", "start-session", "demo", "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "dry run" in out and "not on PATH" in out

    def test_off_macos_it_is_refused_with_the_line_to_run_by_hand(
        self, project, capsys, monkeypatch, macos_with_claude, _no_real_terminal_launch
    ):
        monkeypatch.setattr(launch, "host_platform", lambda: "linux")
        assert main(["oracle", "start-session", "demo"]) == 1
        err = capsys.readouterr().err
        assert "macOS" in err and "--remote-control demo-" in err
        assert _no_real_terminal_launch.plans == []

    def test_without_claude_it_is_refused(
        self, project, capsys, monkeypatch, _no_real_terminal_launch
    ):
        monkeypatch.setattr(launch, "host_platform", lambda: "darwin")
        monkeypatch.setattr(launch, "resolve_claude", lambda: None)
        assert main(["oracle", "start-session", "demo"]) == 1
        assert "no claude on PATH" in capsys.readouterr().err
        assert _no_real_terminal_launch.plans == []

    def test_a_window_that_never_appears_fails_and_leaves_no_launch_file(
        self, project, capsys, _no_real_terminal_launch, macos_with_claude
    ):
        assert main(["oracle", "start-session", "demo"]) == 1
        err = capsys.readouterr().err
        assert "did not start a successor" in err
        assert "note is recorded" not in err
        (plan,) = _no_real_terminal_launch.plans
        assert not Path(plan.launch_file).exists()

    def test_a_project_whose_root_lost_its_manifest_is_refused(
        self, project, capsys, _no_real_terminal_launch, macos_with_claude, successor
    ):
        from invariant.core.manifest import MANIFEST_RELPATH

        (project / MANIFEST_RELPATH).unlink()
        assert main(["oracle", "start-session", "demo"]) == 2
        assert "manifest" in capsys.readouterr().err
        assert _no_real_terminal_launch.plans == []

    def test_another_host_kind_is_refused(
        self, project, capsys, monkeypatch, _no_real_terminal_launch, macos_with_claude,
        successor
    ):
        real = cli_main.Manifest.get

        def kind(self, dotted, *rest, **kwargs):
            return "other_host" if dotted == "host.kind" else real(self, dotted, *rest, **kwargs)

        monkeypatch.setattr(cli_main.Manifest, "get", kind)
        assert main(["oracle", "start-session", "demo"]) == 2
        assert "host.kind" in capsys.readouterr().err
        assert _no_real_terminal_launch.plans == []


class TestTheSeamsAreGuarded:
    def test_the_suite_replaces_the_names_in_use_reader(self):
        assert cli_main._oracle_names_in_use() == set()

    def test_the_real_reader_joins_live_records_and_the_process_table(self, monkeypatch):
        records = [
            launch.HostProcessRecord(session_id="a", pid=11, name="live"),
            launch.HostProcessRecord(session_id="b", pid=12, name="gone"),
        ]
        monkeypatch.setattr(launch.ClaudeCodeProbe, "host_processes", lambda self: records)
        monkeypatch.setattr(
            launch.ProcessTableWatch, "table", lambda self: "  13 claude --rc in-table\n"
        )
        monkeypatch.setattr("invariant.oracle.scan.pid_exists", lambda pid: pid == 11)
        assert cli_main._real_oracle_names_in_use() == {"live", "in-table"}

    def test_unreadable_records_leave_the_process_table(self, monkeypatch):
        def unreadable(self):
            raise FileNotFoundError("no sessions directory")

        monkeypatch.setattr(launch.ClaudeCodeProbe, "host_processes", unreadable)
        monkeypatch.setattr(launch.ProcessTableWatch, "table", lambda self: "")
        assert cli_main._real_oracle_names_in_use() == set()
