"""IDEA_042: every command that resolves a session takes it as ``--id``.

A Claude Code permission rule matches a command by its prefix, so a line that
starts ``INVARIANT_SESSION_ID=<id> invariant ...`` is not covered by
``Bash(invariant:*)`` and raises a prompt on every call. The engine used to
recommend that form because several session-resolving commands had no other
way to name their session. Now each one inherits one shared ``--id``, the
argument outranks the variable, a disagreement between the two is refused,
and nothing the engine prints offers the prefix form to copy.

The parser walk at the top is the guard that keeps it that way: it reads which
commands resolve a session from the code paths they run, not from their
names, and fails when one of them lacks the shared flag.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import subprocess
from pathlib import Path

import pytest

from invariant.cli import main as cli
from invariant.cli.main import build_parser, main
from invariant.domains.session import ambiguity_message

#: The shared flag's help; absent before IDEA_042, so read without importing.
SESSION_ID_HELP = getattr(cli, "SESSION_ID_HELP", object())

SRC = Path(cli.__file__).resolve().parents[1]

#: What, in a command's code path, means it resolves a session: the CLI's own
#: routes onto ``SessionDomain.resolve`` (step 1 of which is the variable).
RESOLVES = re.compile(
    r"require_session\(|current_session\(|rule_context\(|build_context\("
    r"|session_for_payload\(|resolution_for_payload\(|for_payload\("
    r"|sessions\.resolve\(|sessions\.current\(|_guidelines_domain\("
)

#: Commands that resolve a session and still take no ``--id``, each with why.
EXEMPT = {
    # Invoked by the host with a payload, never typed: the payload's host id
    # and paths identify the session, and a variable in front of a tool call
    # never reaches it either (BUG_003).
    ("hook",): "host-invoked; the payload identifies the session",
}


def _leaves(parser: argparse.ArgumentParser, path=()):
    """Every command a person can run, by its words, with its parser."""
    subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if parser._defaults.get("func") is not None:
        yield path, parser
    for action in subs:
        for name, child in action.choices.items():
            yield from _leaves(child, path + (name,))


def _module_functions() -> dict[str, str]:
    found: dict[str, str] = {}
    for name, obj in vars(cli).items():
        if inspect.isfunction(obj) and obj.__module__ == cli.__name__:
            found[name] = inspect.getsource(obj)
    return found


def _resolving_functions() -> set[str]:
    """Module functions whose code path reaches a session resolution."""
    sources = _module_functions()
    calls = {
        name: set(re.findall(r"\b(\w+)\(", body)) & set(sources)
        for name, body in sources.items()
    }
    found = {name for name, body in sources.items() if RESOLVES.search(body)}
    grew = True
    while grew:
        grew = False
        for name, callees in calls.items():
            if name not in found and callees & found:
                found.add(name)
                grew = True
    return found


def _session_id_action(parser):
    return next(
        (a for a in parser._actions if "--id" in a.option_strings), None
    )


class TestTheParserWalk:
    def test_the_walk_finds_the_commands_the_audit_listed(self):
        """The heuristic is not vacuous: it sees the commands IDEA_042 named."""
        resolving = _resolving_functions()
        for name in (
            "cmd_pipeline_start", "cmd_pipeline_test", "cmd_pipeline_gates",
            "cmd_pipeline_advance", "cmd_pipeline_status", "cmd_check",
            "cmd_guidelines_verdict", "cmd_guidelines_verdicts_show",
            "cmd_guidelines_verdicts_submit", "cmd_guidelines_baseline",
        ):
            assert name in resolving, name

    def test_every_session_resolving_command_takes_the_shared_id(self):
        resolving = _resolving_functions()
        missing = []
        for path, parser in _leaves(build_parser()):
            func = parser._defaults["func"]
            if func.__name__ not in resolving or path in EXEMPT:
                continue
            action = _session_id_action(parser)
            if action is None or action.help != SESSION_ID_HELP or action.dest != "id":
                missing.append(" ".join(path))
        assert missing == [], (
            "these commands resolve a session and lack the shared --id: "
            + ", ".join(missing)
        )

    def test_the_shared_id_is_on_no_command_that_resolves_nothing(self):
        """The help says 'session id'; a command that never resolves one
        must not claim to take it."""
        resolving = _resolving_functions()
        stray = [
            " ".join(path)
            for path, parser in _leaves(build_parser())
            if (a := _session_id_action(parser)) is not None
            and a.help == SESSION_ID_HELP
            and parser._defaults["func"].__name__ not in resolving
        ]
        assert stray == []

    def test_an_exemption_still_names_a_real_command(self):
        paths = {path for path, _ in _leaves(build_parser())}
        assert set(EXEMPT) <= paths


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
    (root / "docs" / "architecture").mkdir(parents=True)
    (root / "docs" / "architecture" / "overview.md").write_text(
        "# Overview\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    monkeypatch.chdir(root)
    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
    assert main(["init", "--ecosystem", "python"]) == 0
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "adopt"], cwd=root, check=True)
    return root


def _two_sessions(capsys) -> tuple[str, str]:
    """Two sessions registered in the main checkout: the directory names neither."""
    assert main(["session", "start", "first"]) == 0
    assert main(["session", "start", "second"]) == 0
    capsys.readouterr()
    assert main(["session", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)["sessions"]
    by = {s["name"].split("-")[0]: s["id"] for s in listed}
    return by["first"], by["second"]


def _state(root: Path) -> dict[str, bytes]:
    state = root / ".invariant"
    return {
        str(p.relative_to(state)): p.read_bytes()
        for p in sorted(state.rglob("*")) if p.is_file()
    }


class TestIdNamesTheSession:
    def test_pipeline_commands_act_on_the_named_session(self, project, capsys):
        first, second = _two_sessions(capsys)

        assert main(["pipeline", "start", "--id", second, "--task", "t2"]) == 0
        capsys.readouterr()
        assert main(["pipeline", "status", "--id", second, "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["task"] == "t2"

        assert main(["pipeline", "gates", "--id", second]) == 0
        assert "to red" in capsys.readouterr().out

        # Gated, not ambiguous: exit 1 is a gate, 2 would be the ambiguity.
        assert main(["pipeline", "advance", "red", "--id", second]) == 1
        capsys.readouterr()

        # The other session was never touched.
        assert main(["pipeline", "status", "--id", first]) == 0
        assert "no pipeline is running" in capsys.readouterr().out

    def test_without_id_the_same_directory_is_ambiguous(self, project, capsys):
        _two_sessions(capsys)
        assert main(["pipeline", "status"]) == 2
        err = capsys.readouterr().err
        assert "--id" in err

    def test_check_and_guidelines_and_doctor_take_it(self, project, capsys):
        _, second = _two_sessions(capsys)
        assert main(["check", "--id", second]) in (0, 1)
        assert main(["guidelines", "show", "--id", second]) == 0
        assert main(["guidelines", "verdicts", "show", "--id", second]) in (0, 1)
        assert main(["doctor", "--id", second]) in (0, 1)

    def test_id_from_another_directory_beats_where_it_stands(
        self, project, capsys, monkeypatch, tmp_path
    ):
        _, second = _two_sessions(capsys)
        sub = project / "docs"
        monkeypatch.chdir(sub)
        assert main(["pipeline", "start", "--id", second, "--task", "x"]) == 0


class TestTheVariable:
    def test_alone_it_still_names_the_session(self, project, capsys, monkeypatch):
        _, second = _two_sessions(capsys)
        monkeypatch.setenv("INVARIANT_SESSION_ID", second)
        assert main(["pipeline", "start", "--task", "env"]) == 0
        capsys.readouterr()
        assert main(["pipeline", "status", "--id", second, "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["task"] == "env"

    def test_agreeing_with_id_is_no_conflict(self, project, capsys, monkeypatch):
        _, second = _two_sessions(capsys)
        monkeypatch.setenv("INVARIANT_SESSION_ID", second)
        assert main(["pipeline", "start", "--id", second, "--task", "same"]) == 0

    def test_disagreeing_with_id_is_refused_naming_both(
        self, project, capsys, monkeypatch
    ):
        first, second = _two_sessions(capsys)
        before = _state(project)
        monkeypatch.setenv("INVARIANT_SESSION_ID", first)

        assert main(["pipeline", "start", "--id", second, "--task", "no"]) == 2
        err = capsys.readouterr().err
        assert first in err and second in err
        assert "INVARIANT_SESSION_ID" in err
        assert _state(project) == before, "a refused conflict recorded something"

    def test_the_refusal_holds_on_every_scoped_route(self, project, capsys, monkeypatch):
        first, second = _two_sessions(capsys)
        monkeypatch.setenv("INVARIANT_SESSION_ID", first)
        for argv in (
            ["session", "status", "--id", second],
            ["context", "--id", second],
            ["check", "--id", second],
            ["guidelines", "show", "--id", second],
        ):
            assert main(argv) == 2, argv
            assert second in capsys.readouterr().err


class TestAdviceNamesOnlyTheArgument:
    def test_the_ambiguity_message_offers_no_prefix_form(self):
        class S:
            def __init__(self, i):
                self.id, self.name, self.state, self.worktree = i, f"n-{i}", "registered", None

        text = ambiguity_message([S("aaaa1111"), S("bbbb2222")])
        assert "invariant <command> --id <id>" in text
        assert not any(
            line.strip().startswith("INVARIANT_SESSION_ID=") for line in text.splitlines()
        )
        assert "INVARIANT_SESSION_ID" not in text

    def test_no_session_here_names_the_argument(self, project, capsys):
        assert main(["pipeline", "status"]) == 2
        err = capsys.readouterr().err
        assert "--id" in err and "INVARIANT_SESSION_ID" not in err

    def test_the_unproven_dispatch_hint_drops_the_variable(self):
        from invariant.hosts.claude_code import _UNPROVEN_DISPATCH

        assert "INVARIANT_SESSION_ID" not in _UNPROVEN_DISPATCH

    def test_no_shipped_source_or_asset_spells_the_prefix_form(self):
        hits = [
            f"{path.relative_to(SRC)}:{n}"
            for path in sorted(SRC.rglob("*"))
            if path.is_file() and path.suffix in {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".sh"}
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if "INVARIANT_SESSION_ID=" in line
        ]
        assert hits == []
