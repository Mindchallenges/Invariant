"""The conformance replay: a scenario driven through the CLI, in-process.

The engine layer of the conformance harness (IDEA_031 A1, ADR-012 D11). A
replay seeds a fresh repository from the scenario's ``tree/``, writes the
scenario's manifest into it, commits it, and runs the steps in order: a
``cli`` step through ``invariant.cli.main.main``, a ``hook`` step as
``invariant hook <event>`` with its payload on stdin, a ``write`` step as a
file write, and a ``commit`` step as ``git add -A`` and a commit. What the CLI
printed is reduced by ``core/conformance`` to an outcome; nothing here judges
a gate.

It lives in ``cli/`` and not in ``core/`` because it drives the CLI, and
``core/`` knows nothing about the CLI. The CLI itself is a parameter, so a
test can stand a recorder in its place.

Each replay is isolated from whoever started it. Every ``INVARIANT_*`` and
``CLAUDE_*`` variable is stripped for its duration (the host's markers, the
session a caller names, a state root override; ``domains/pipeline``'s
``child_env`` strips the first three for the same reason), ``INVARIANT_HOME``
points into the replay's own directory so the oracle's index is never the
real one, and git reads no global or system configuration, so a signing or
hook setting on the machine cannot change what a step does. The caller's
environment, working directory and stdin are restored afterwards, whatever
happened.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..core.conformance import (
    Outcome,
    Scenario,
    ScenarioError,
    Step,
    StepResult,
    reduce,
)
from ..core.manifest import MANIFEST_RELPATH

#: A CLI entry point: ``argv`` in, exit code out, its document on stdout.
Cli = Callable[[list[str]], int]

#: Variable prefixes a replay never inherits.
STRIPPED_PREFIXES = ("INVARIANT_", "CLAUDE_")

#: The synthetic author of every commit a replay makes.
_AUTHOR = ("conformance", "conformance")


@dataclass(frozen=True)
class Replay:
    outcome: Outcome
    steps: tuple[StepResult, ...]


def replay(scenario: Scenario, *, workdir: Path, run: Cli | None = None) -> Replay:
    """Run ``scenario`` once in a fresh repository under ``workdir``.

    ``workdir`` must not exist yet, or be empty: a replay that found a tree
    already there would be measuring whatever left it.
    """
    if run is None:
        from .main import main as run
    workdir = Path(workdir)
    if workdir.exists() and any(workdir.iterdir()):
        raise ScenarioError(f"{scenario.name}: {workdir} already holds a replay")
    repo = workdir / "repo"
    home = workdir / "home"
    gitconfig = workdir / "gitconfig"
    workdir.mkdir(parents=True, exist_ok=True)
    gitconfig.write_text("", encoding="utf-8")

    results: list[StepResult] = []
    with _isolated(home, gitconfig):
        _seed(scenario, repo)
        for step in scenario.steps:
            cwd = repo if step.where == "root" else _worktree(scenario, repo)
            os.chdir(cwd)
            results.append(_run_step(step, run, repo, cwd))
    return Replay(outcome=reduce(results), steps=tuple(results))


@contextlib.contextmanager
def _isolated(home: Path, gitconfig: Path) -> Iterator[None]:
    saved_env = dict(os.environ)
    saved_cwd = Path.cwd()
    saved_stdin = sys.stdin
    try:
        for name in list(os.environ):
            if name.startswith(STRIPPED_PREFIXES):
                del os.environ[name]
        os.environ["INVARIANT_HOME"] = str(home)
        os.environ["GIT_CONFIG_GLOBAL"] = str(gitconfig)
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        yield
    finally:
        sys.stdin = saved_stdin
        os.chdir(saved_cwd)
        os.environ.clear()
        os.environ.update(saved_env)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _seed(scenario: Scenario, repo: Path) -> None:
    shutil.copytree(scenario.tree, repo)
    manifest = repo / MANIFEST_RELPATH
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(yaml.safe_dump(dict(scenario.manifest), sort_keys=False), encoding="utf-8")
    # The state root is machine-local; a commit step's `git add -A` must not
    # sweep it into the scenario's history.
    (repo / ".gitignore").write_text(".claude/invariant/\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", _AUTHOR[0])
    _git(repo, "config", "user.email", _AUTHOR[1])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "scenario tree")


def _worktree(scenario: Scenario, repo: Path) -> Path:
    """The one linked worktree beside ``repo``, which a session step made."""
    listing = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout
    paths = [Path(line[len("worktree "):]) for line in listing.splitlines()
             if line.startswith("worktree ")]
    linked = [p for p in paths if p.resolve() != repo.resolve()]
    if len(linked) != 1:
        raise ScenarioError(
            f"{scenario.name}: a step runs in the worktree, but there is no session "
            f"worktree to run it in ({len(linked)} linked worktree(s))"
        )
    return linked[0]


def _fill(value: Any, places: Mapping[str, str]) -> Any:
    """``{root}`` and ``{worktree}`` replaced in every string of ``value``."""
    if isinstance(value, str):
        for key, path in places.items():
            value = value.replace("{" + key + "}", path)
        return value
    if isinstance(value, Mapping):
        return {k: _fill(v, places) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, places) for v in value]
    return value


def _run_step(step: Step, run: Cli, repo: Path, cwd: Path) -> StepResult:
    places = {"root": str(repo), "worktree": str(cwd)}
    if step.kind == "write":
        target = cwd / step.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(step.content, encoding="utf-8")
        return StepResult(step.label, step.kind, (), 0, None)
    if step.kind == "commit":
        _git(cwd, "add", "-A")
        _git(cwd, "commit", "-qm", step.message)
        return StepResult(step.label, step.kind, (), 0, None)
    if step.kind == "hook":
        argv = ["hook", step.event]
        stdin = json.dumps(_fill(dict(step.payload), places))
    else:
        argv = ["--json", *_fill(list(step.argv), places)]
        stdin = ""
    code, out = _call(run, argv, stdin)
    return StepResult(step.label, step.kind, tuple(argv), code, _document(out))


def _call(run: Cli, argv: list[str], stdin: str) -> tuple[int, str]:
    sys.stdin = io.StringIO(stdin)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = run(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return int(code or 0), buffer.getvalue()


def _document(text: str) -> Any:
    """The JSON document the CLI printed, or ``None``."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
