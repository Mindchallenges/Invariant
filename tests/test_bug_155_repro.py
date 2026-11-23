"""Repro for BUG_155 — see the work-item entry.

Both tests fail while the defect is live. When the fix lands, delete the
expected-failure wrapper; each assertion must then pass unchanged.

The symptom: a LaunchAgent the oracle installed cannot start ``claude`` ("no
claude on PATH to start; claude was not found"), because the PATH baked into
its plist holds no directory ``claude`` lives in. Two halves:

1. The watcher runs the ADR-027 chain step, which opens ``claude``; installed
   from a shell where ``claude`` resolves outside the system directories, its
   plist's PATH must still find it.
2. An install of the web agent from a process where ``claude`` does not
   resolve must not pass silently: it warns or refuses.

Neither asserts the suspected cause (``NEEDED_PROGRAMS`` omitting ``claude``;
``search_path`` dropping an unresolved program) — each asserts what the plist
or the installer's output gives the person.
"""

from __future__ import annotations

import plistlib
import shutil
import stat

from invariant.cli.main import main


def _claude_only_in(directory) -> str:
    """A PATH where ``claude`` lives only in ``directory``, beside the system dirs."""
    directory.mkdir(parents=True)
    claude = directory / "claude"
    claude.write_text("#!/bin/sh\nexit 0\n")
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return f"{directory}:/usr/bin:/bin:/usr/sbin:/sbin"


def test_BUG_155_watcher_plist_path_finds_claude(
    _no_real_launchd, monkeypatch, tmp_path, capsys
):
    claude_dir = tmp_path / "usr-local-bin"
    monkeypatch.setenv("PATH", _claude_only_in(claude_dir))
    assert shutil.which("claude") == str(claude_dir / "claude")

    assert main(["oracle", "install"]) == 0
    capsys.readouterr()
    plist = plistlib.loads(_no_real_launchd.plist_path.read_bytes())
    agent_path = plist["EnvironmentVariables"]["PATH"]

    # What the chain step's resolve_claude() would see inside the watcher.
    assert shutil.which("claude", path=agent_path) is not None, (
        f"the watcher agent's PATH {agent_path!r} finds no claude"
    )


def test_BUG_155_web_install_without_claude_is_not_silent(
    _no_real_web_launchd, monkeypatch, capsys
):
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    assert shutil.which("claude") is None

    status = main(["oracle", "install", "--web"])
    captured = capsys.readouterr()
    said = captured.out + captured.err

    assert status != 0 or "claude" in said, (
        f"the web agent was installed (exit {status}) with no claude on its PATH "
        f"and nothing said so:\n{said}"
    )
