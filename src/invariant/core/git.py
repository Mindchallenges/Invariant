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


def in_linked_worktree(cwd: Path) -> bool | None:
    """Whether ``cwd`` is a linked worktree rather than the main checkout.

    In a linked worktree the per-worktree git dir differs from the common one;
    in the main checkout they are the same path.

    ``None`` means the question could not be answered — git absent, or not a
    repository. It carries the same obligation as :func:`branch_files`'
    ``None``: a caller deciding whether to *relax* must treat it as "do not
    relax". This used to answer ``False``, which the merge gate read as
    "running from the main checkout" — a clean pass on an unreadable
    repository (AUD-07).
    """
    code_a, git_dir, _ = _run(["rev-parse", "--absolute-git-dir"], cwd)
    code_b, common, _ = _run(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    if code_a != 0 or code_b != 0 or not git_dir or not common:
        return None
    return Path(git_dir).resolve() != Path(common).resolve()


def common_dir(cwd: Path) -> Path | None:
    """The git directory shared by every worktree of this repository.

    The same absolute path from the main checkout and from every linked
    worktree, which is what makes it the right home for cross-worktree state.
    """
    code, out, _ = _run(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    return Path(out).resolve() if code == 0 and out else None


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


def staged_files(cwd: Path) -> list[Path] | None:
    """Paths currently staged, or ``None`` when git could not say.

    An empty list is a real answer — nothing is staged. ``None`` is the
    absence of one, and a caller deciding whether to *relax* must treat it as
    "do not relax": this used to answer ``[]`` on a git failure, which the
    file-set gate read as "0 staged file(s), all declared" (AUD-07).
    """
    code, out, _ = _run(["diff", "--cached", "--name-only"], cwd)
    return [Path(p) for p in out.splitlines() if p] if code == 0 else None


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


def branch_files(cwd: Path, base: str) -> list[Path] | None:
    """Paths touched by commits on this branch since ``base``.

    ``changed_files`` answers for the working tree and the index, which is the
    right question while work is in progress and the wrong one afterwards: a
    commit empties both. Any gate that runs at the END of a phase — when the
    work is, by construction, already committed — therefore sees nothing
    changed unless it also asks this.

    ``None`` means the question could not be answered: no base, an unknown
    ref, an unreadable repository. It carries the same obligation as
    :func:`unpushed_files` — a caller deciding whether to *relax* must treat
    ``None`` as "do not relax", because an unanswered question is not a
    negative answer, and reading it as one turns every unusual repository
    state into an open gate.
    """
    if not base:
        return None
    # Three dots: the merge base, not the tip. Diffing against the tip
    # attributes every commit the base gained since the branch left to this
    # branch, which on a busy base branch is most of the project.
    code, out, _ = _run(["diff", "--name-only", f"{base}...HEAD"], cwd)
    if code != 0:
        return None
    return [Path(p) for p in out.splitlines() if p]


def deleted_files(cwd: Path, base: str = "") -> list[Path] | None:
    """Paths this change REMOVED — working tree, index, and the branch's commits.

    Positive evidence of a deletion, which is a different question from "the
    file is not there". A path can be missing from disk because the change
    deleted it, or because whoever named it spelled it wrong, and only git can
    tell those apart. A caller that reads absence as deletion excuses the
    typo too.

    The commit leg is ``log``, not ``diff``: a path added and then removed on
    the same branch is absent from both endpoints of ``base...HEAD``, so the
    net diff never mentions it, while the commit that removed it still does.
    The cost is that a path deleted and later restored also appears here — so
    a caller must pair this with the file's actual absence, and the two
    together are what make the answer sound.

    Each leg is optional and contributes only what it can read: fewer legs
    mean fewer paths reported, which relaxes nothing. ``None`` is reserved for
    the case where no leg answered at all, and carries the same obligation as
    :func:`branch_files`' ``None`` — a caller deciding whether to *relax* must
    treat it as "do not relax".
    """
    found: set[str] = set()
    answered = False
    legs = [
        ["diff", "--diff-filter=D", "--name-only"],
        ["diff", "--cached", "--diff-filter=D", "--name-only"],
    ]
    if base:
        legs.append(
            [
                "log", "--diff-filter=D", "--name-only",
                "--pretty=format:", f"{base}..HEAD",
            ]
        )
    for args in legs:
        code, out, _ = _run(args, cwd)
        if code != 0:
            continue
        answered = True
        found.update(p for p in out.splitlines() if p.strip())
    if not answered:
        return None
    return [Path(p) for p in sorted(found)]


def file_at(cwd: Path, rev: str, path: str) -> str | None:
    """This path's contents at ``rev``, or ``None`` if they cannot be read.

    ``None`` covers every reason at once — no such revision, the path did not
    exist there, a binary blob that will not decode — and does so on purpose.
    A caller comparing two revisions of a file needs BOTH sides to say
    anything at all; distinguishing why one side is missing would only tempt
    it to proceed on one.
    """
    code, out, _ = _run(["show", f"{rev}:{path}"], cwd)
    if code != 0:
        return None
    return out


def unpushed_files(cwd: Path) -> list[Path] | None:
    """Paths touched by commits the upstream does not have yet.

    ``None`` means the question could not be answered — no upstream configured,
    a detached head, an unreadable repository. A caller deciding whether to
    *relax* a rule must treat ``None`` as "do not relax": an unanswered
    question is not a negative answer, and reading it as one turns every
    unusual repository state into an open gate.
    """
    code, upstream, _ = _run(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd
    )
    if code != 0 or not upstream.strip():
        return None
    code, out, _ = _run(["diff", "--name-only", f"{upstream.strip()}...HEAD"], cwd)
    if code != 0:
        return None
    return [Path(p) for p in out.splitlines() if p]


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
