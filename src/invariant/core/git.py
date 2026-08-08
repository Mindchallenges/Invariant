"""Thin git queries used by the session rules.

Everything here is a read unless the name says otherwise, and every function
degrades to a safe answer when git is absent or the directory is not a
repository — a rule that crashes is worse than a rule that abstains.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


def _run(args: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, "", "git unavailable"
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str | None
    is_main: bool


def is_repo(cwd: Path) -> bool:
    return _run(["rev-parse", "--is-inside-work-tree"], cwd)[1] == "true"


def current_branch(cwd: Path) -> str | None:
    code, out, _ = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    return out or None if code == 0 else None


def toplevel(cwd: Path) -> Path | None:
    code, out, _ = _run(["rev-parse", "--show-toplevel"], cwd)
    return Path(out) if code == 0 and out else None


def in_linked_worktree(cwd: Path) -> bool:
    """True when ``cwd`` is a linked worktree rather than the main checkout.

    In a linked worktree the per-worktree git dir differs from the common one;
    in the main checkout they are the same path.
    """
    code_a, git_dir, _ = _run(["rev-parse", "--absolute-git-dir"], cwd)
    code_b, common, _ = _run(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    if code_a != 0 or code_b != 0 or not git_dir or not common:
        return False
    return Path(git_dir).resolve() != Path(common).resolve()


def main_checkout(cwd: Path) -> Path | None:
    """The main working tree's path, from anywhere in the repository."""
    code, common, _ = _run(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    if code != 0 or not common:
        return None
    git_common = Path(common).resolve()
    # The common git dir is <main>/.git for a normal clone.
    return git_common.parent if git_common.name == ".git" else None


def worktrees(cwd: Path) -> list[Worktree]:
    code, out, _ = _run(["worktree", "list", "--porcelain"], cwd)
    if code != 0:
        return []
    result: list[Worktree] = []
    path: Path | None = None
    branch: str | None = None
    first = True
    for line in out.splitlines() + [""]:
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):])
            branch = None
        elif line.startswith("branch "):
            branch = line[len("branch "):].removeprefix("refs/heads/")
        elif line == "" and path is not None:
            result.append(Worktree(path=path, branch=branch, is_main=first))
            first = False
            path = None
    return result


def staged_files(cwd: Path) -> list[Path]:
    code, out, _ = _run(["diff", "--cached", "--name-only"], cwd)
    return [Path(p) for p in out.splitlines() if p] if code == 0 else []


def changed_files(cwd: Path, since: str | None = None) -> list[Path]:
    args = ["diff", "--name-only"] + ([since] if since else [])
    code, out, _ = _run(args, cwd)
    tracked = [Path(p) for p in out.splitlines() if p] if code == 0 else []
    code, out, _ = _run(["diff", "--cached", "--name-only"], cwd)
    staged = [Path(p) for p in out.splitlines() if p] if code == 0 else []
    seen: dict[str, Path] = {}
    for path in [*tracked, *staged]:
        seen[str(path)] = path
    return sorted(seen.values(), key=str)


def branch_is_merged_into(cwd: Path, branch: str, target: str) -> bool:
    code, out, _ = _run(["branch", "--contains", branch, "--list", target], cwd)
    return code == 0 and bool(out.strip())


def commits_ahead(cwd: Path, branch: str, upstream: str) -> int | None:
    code, out, _ = _run(["rev-list", "--count", f"{upstream}..{branch}"], cwd)
    if code != 0 or not out.isdigit():
        return None
    return int(out)


def last_commit_message(cwd: Path) -> str:
    return _run(["log", "-1", "--format=%B"], cwd)[1]
