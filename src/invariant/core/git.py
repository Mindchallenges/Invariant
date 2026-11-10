"""Thin git queries used by the session rules.

Everything here is a read unless the name says otherwise, and every function
degrades to a safe answer when git is absent or the directory is not a
repository — a rule that crashes is worse than a rule that abstains.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

#: Whether the git calls made in this context must take no optional lock.
_NO_OPTIONAL_LOCKS: ContextVar[bool] = ContextVar("git_no_optional_locks", default=False)


@contextmanager
def without_optional_locks() -> Iterator[None]:
    """Run the git reads inside the block with ``GIT_OPTIONAL_LOCKS=0``.

    A plain ``git status`` or ``git diff`` may refresh the index's stat cache
    as a side effect, and that takes ``index.lock``. For a command run in its
    own turn, in its own worktree, that is the wanted, ordinary behaviour. A
    reader asking about a worktree somebody else is working in (the oracle's
    gate cache, ADR-007 D16) must not take that lock under them.

    The scope is a context variable rather than ``os.environ``: it reaches
    exactly the git calls made inside the block, in the thread that entered
    it, and changes nothing else in the process.
    """
    token = _NO_OPTIONAL_LOCKS.set(True)
    try:
        yield
    finally:
        _NO_OPTIONAL_LOCKS.reset(token)


def _env() -> dict[str, str] | None:
    """The environment for a git call: inherited, unless optional locks are off."""
    if not _NO_OPTIONAL_LOCKS.get():
        return None
    return {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}


def _run(args: list[str], cwd: Path) -> tuple[int, str, str]:
    # UnicodeDecodeError belongs with the other two: ``text=True`` decodes in
    # ``communicate``, so a command whose output is not valid UTF-8 raises
    # from INSIDE the call rather than returning something to inspect. Every
    # caller here is written against "git could not answer", and a decode that
    # escapes turns that into a crashed rule — which this module's own
    # docstring rules out.
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, "", "git unavailable"
    except UnicodeDecodeError:
        return 1, "", "git output is not text"
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _run_bytes(args: list[str], cwd: Path) -> tuple[int, bytes]:
    """Like :func:`_run`, undecoded and unstripped.

    Two properties the text form cannot offer, both load-bearing for reading a
    blob: bytes that are not text are an ANSWER here rather than a failure,
    and stripping would move every line number a scan of the result reports.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            timeout=30,
            check=False,
            env=_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, b""
    return proc.returncode, proc.stdout


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str | None
    is_main: bool


def is_repo(cwd: Path) -> bool:
    return _run(["rev-parse", "--is-inside-work-tree"], cwd)[1] == "true"


def current_branch(cwd: Path) -> str | None:
    """The branch ``cwd`` has checked out, or ``None`` when there is not one.

    ``rev-parse --abbrev-ref HEAD`` prints the literal ``HEAD`` on a detached
    head, which is an answer shaped like a branch name and is not one. It is
    folded into ``None`` here so that a caller taking the result as a ref —
    `session start` derives the base branch from it — cannot record a base no
    later command can resolve.
    """
    code, out, _ = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if code != 0 or not out or out == "HEAD":
        return None
    return out


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


def staged_files(cwd: Path, *, exclude_deleted: bool = False) -> list[Path] | None:
    """Paths currently staged, or ``None`` when git could not say.

    An empty list is a real answer — nothing is staged. ``None`` is the
    absence of one, and a caller deciding whether to *relax* must treat it as
    "do not relax": this used to answer ``[]`` on a git failure, which the
    file-set gate read as "0 staged file(s), all declared" (AUD-07).

    ``exclude_deleted`` drops the paths this commit REMOVES. A caller that
    names paths — the file-set gate — wants them; a caller that goes on to
    read each path's staged content does not, because a deleted path has no
    staged content, and reading its absence as "could not be read" would fail
    closed on every commit that deletes a file.
    """
    args = ["diff", "--cached", "--name-only"]
    if exclude_deleted:
        args.append("--diff-filter=d")
    code, out, _ = _run(args, cwd)
    return [Path(p) for p in out.splitlines() if p] if code == 0 else None


def staged_blob(cwd: Path, path: str) -> bytes | None:
    """The exact bytes staged for ``path``, or ``None`` when git could not
    produce them.

    Bytes rather than text, because what an UNDECODABLE blob means is policy
    and belongs to the caller: a scan for personal data reads it as "nothing
    that can leak in text form", which is the same answer
    ``PrivacyScanner.scan_file`` gives a binary file, while ``None`` here is
    the unanswered question that a caller must not read as a pass.
    """
    code, out = _run_bytes(["show", f":{path}"], cwd)
    return out if code == 0 else None


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


def uncommitted_count(cwd: Path) -> int | None:
    """How many paths ``git status`` reports as changed or untracked in ``cwd``.

    Ignored files do not count. ``None`` when git cannot answer: not a
    repository, or no git. A reader asking about a checkout somebody else may
    be working in wraps this in :func:`without_optional_locks`.
    """
    code, out, _ = _run(["status", "--porcelain", "--untracked-files=normal"], cwd)
    if code != 0:
        return None
    return len([line for line in out.splitlines() if line.strip()])


def branch_files(cwd: Path, base: str, tip: str = "HEAD") -> list[Path] | None:
    """Paths touched by commits on ``tip`` since ``base``.

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
    # ``tip`` defaults to HEAD because the caller is normally standing in the
    # branch's own worktree. It is passed explicitly when it is not: from the
    # main checkout at merge time HEAD *is* the base, and `base...HEAD` is
    # empty — an answer indistinguishable from "this branch changed nothing".
    #
    # Three dots: the merge base, not the tip. Diffing against the tip
    # attributes every commit the base gained since the branch left to this
    # branch, which on a busy base branch is most of the project.
    code, out, _ = _run(["diff", "--name-only", f"{base}...{tip}"], cwd)
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


def _is_revision(cwd: Path, rev: str) -> bool:
    """Whether ``rev`` names a commit here. A name opening with ``-`` never
    reaches git, where it would be read as an option."""
    if not rev or rev.startswith("-"):
        return False
    code, _, _ = _run(["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], cwd)
    return code == 0


def merge_base(cwd: Path, base: str, tip: str = "HEAD") -> str | None:
    """The commit ``tip`` left ``base`` at, or ``None`` if git cannot say.

    The point a change is measured from: what the base gained after the
    branch left is not the branch's (the three-dot rule ``branch_files``
    keeps).
    """
    if not _is_revision(cwd, base) or not _is_revision(cwd, tip):
        return None
    code, out, _ = _run(["merge-base", base, tip], cwd)
    return out if code == 0 and out else None


def exists_at(cwd: Path, rev: str, path: str) -> bool | None:
    """Whether ``path`` was a file at ``rev``: ``True``, ``False``, or ``None``.

    The question :func:`file_at` cannot answer, because its ``None`` merges
    "the path was absent" with "the revision could not be read". A caller
    deciding that a file is NEW on a branch needs the first alone, and must
    read the second as unanswered. A directory is not a file. The path is
    read literally, never as a pathspec glob, from the top of the tree.
    """
    if not path or not _is_revision(cwd, rev):
        return None
    code, out = _run_bytes(
        ["--literal-pathspecs", "ls-tree", "--full-tree", "-z", rev, "--", path], cwd
    )
    if code != 0:
        return None
    for entry in out.split(b"\0"):
        meta, _, name = entry.partition(b"\t")
        if name.decode("utf-8", "surrogateescape") == path:
            return meta.split(b" ")[1:2] == [b"blob"]
    return False


def blob_at(cwd: Path, rev: str, path: str) -> bytes | None:
    """The exact bytes of ``path`` at ``rev``, unstripped, or ``None``.

    :func:`file_at` strips and decodes, which a comparison of two revisions
    tolerates and a reduction to executable text should not have to.
    """
    if not path or not _is_revision(cwd, rev):
        return None
    code, out = _run_bytes(["show", f"{rev}:{path}"], cwd)
    return out if code == 0 else None


@dataclass(frozen=True)
class AddedLines:
    """The lines a change added to one file, by their working-tree numbers.

    ``whole`` is a file git does not track yet: every line it has is added,
    and none of them needs counting to say so.
    """

    lines: frozenset[int] = frozenset()
    whole: bool = False

    def __contains__(self, number: object) -> bool:
        return self.whole or number in self.lines


#: A hunk header of a zero-context diff: the new side's first line and count.
_HUNK = re.compile(rb"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
#: Git's C-style escapes in a quoted path.
_ESCAPES = {
    "a": "\a", "b": "\b", "t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r",
    '"': '"', "\\": "\\",
}


def changed_lines(cwd: Path, base: str) -> dict[str, AddedLines] | None:
    """Every file this change touched, and the lines it added to each.

    Measured from the merge base of ``base`` and ``HEAD`` to the working
    tree, so committed, staged and unstaged work all count, and what the base
    gained after the branch left does not. A file nobody tracks yet is
    counted whole. A file the change touched without adding a line (one it
    deleted, a binary, a line it only removed) is a key with no lines, so the
    keys answer "which files did this change touch" by git's own account,
    and the values answer "which lines did it add". Renames are not followed:
    a moved file's every line is added at its new path.

    Paths are spelled from the top of the tree, as every other diff answer
    in this module is. ``None`` when git cannot answer, which relaxes
    nothing.
    """
    mb = merge_base(cwd, base)
    top = toplevel(cwd)
    if mb is None or top is None:
        return None
    quiet = ["-c", "core.quotePath=false"]
    code, names = _run_bytes(
        [*quiet, "diff", "--no-renames", "--name-only", "-z", mb, "--"], top
    )
    if code != 0:
        return None
    code, patch = _run_bytes(
        [
            *quiet, "diff", "--no-color", "--no-ext-diff", "--no-renames", "-U0",
            "--src-prefix=a/", "--dst-prefix=b/", mb, "--",
        ],
        top,
    )
    if code != 0:
        return None
    added = _added_by_patch(patch)
    if added is None:
        return None
    result = {
        name: AddedLines(lines=frozenset(added.get(name, ())))
        for name in _nul_separated(names)
    }
    code, untracked = _run_bytes(["ls-files", "--others", "--exclude-standard", "-z"], top)
    if code != 0:
        return None
    for name in _nul_separated(untracked):
        result[name] = AddedLines(whole=True)
    return result


def _nul_separated(data: bytes) -> list[str]:
    return [p.decode("utf-8", "surrogateescape") for p in data.split(b"\0") if p]


def _added_by_patch(patch: bytes) -> dict[str, set[int]] | None:
    """The added line numbers per file, read from a zero-context patch.

    A ``+++`` line names the file only inside a header: in a hunk the same
    bytes are an added line whose text begins ``++``. ``None`` for a header
    this reader cannot spell, rather than lines filed under a wrong name.
    """
    added: dict[str, set[int]] = {}
    current: str | None = None
    in_header = False
    for line in patch.split(b"\n"):
        if line.startswith(b"diff --git "):
            in_header, current = True, None
            continue
        if in_header and line.startswith(b"+++ "):
            current = _patch_path(line[4:])
            if current is False:
                return None
            continue
        hunk = _HUNK.match(line)
        if hunk is None:
            continue
        in_header = False
        first, count = int(hunk.group(1)), int(hunk.group(2) or 1)
        if current and count:
            added.setdefault(current, set()).update(range(first, first + count))
    return added


def _patch_path(raw: bytes) -> str | None | bool:
    """A ``+++`` header's path: the name, ``None`` for /dev/null, ``False``
    when it cannot be read."""
    text = raw.decode("utf-8", "surrogateescape")
    if text == "/dev/null":
        return None
    if text.startswith('"'):
        text = _unquote(text)
        if text is None:
            return False
    return text[2:] if text.startswith("b/") else False


def _unquote(text: str) -> str | None:
    """Git's C-style quoting undone: escapes and octal bytes, decoded as UTF-8."""
    if len(text) < 2 or not text.endswith('"'):
        return None
    body, out, i = text[1:-1], bytearray(), 0
    while i < len(body):
        char = body[i]
        if char != "\\":
            out += char.encode("utf-8", "surrogateescape")
            i += 1
            continue
        nxt = body[i + 1 : i + 2]
        if nxt in _ESCAPES:
            out += _ESCAPES[nxt].encode()
            i += 2
        elif re.fullmatch(r"[0-7]{3}", body[i + 1 : i + 4]):
            out.append(int(body[i + 1 : i + 4], 8))
            i += 4
        else:
            return None
    return out.decode("utf-8", "surrogateescape")


def renamed_files(cwd: Path, base: str) -> dict[str, str] | None:
    """Every rename git detects in this change, old path to new.

    Measured as :func:`changed_lines` measures, from the merge base of
    ``base`` and ``HEAD`` to the working tree, so a committed, a staged and an
    unstaged rename all count, and a rename the base made after the branch
    left does not. Detection is git's own (``-M``), asked explicitly so the
    answer does not depend on ``diff.renames``. A file nobody tracks is
    outside the diff, so a move whose destination is untracked is not a
    rename until it is staged. Copies are not renames.

    Paths are spelled from the top of the tree, read with ``-z`` so none is
    quoted. ``None`` when git cannot answer, or when the listing cannot be
    read, never a smaller answer: a
    rename missed reads as a deleted file and an added one.
    """
    mb = merge_base(cwd, base)
    top = toplevel(cwd)
    if mb is None or top is None:
        return None
    code, out = _run_bytes(
        ["diff", "-M", "--name-status", "-z", mb, "--"], top
    )
    if code != 0:
        return None
    fields = _nul_separated(out)
    renames: dict[str, str] = {}
    i = 0
    while i < len(fields):
        status = fields[i]
        width = 3 if status[:1] in ("R", "C") else 2
        if i + width > len(fields):
            return None
        if status[:1] == "R":
            renames[fields[i + 1]] = fields[i + 2]
        i += width
    return renames


def tracked_files(cwd: Path) -> list[str] | None:
    """Every file the index tracks, spelled from the top of the tree.

    What ``guidelines baseline --record`` scans: a staged new file counts, an
    untracked one does not, and a file deleted from the working tree but not
    from the index is still listed (the caller asks the tree whether it is a
    file). Read with ``-z``, so no path is quoted. ``None`` when git cannot
    answer, which relaxes nothing.
    """
    top = toplevel(cwd)
    if top is None:
        return None
    code, out = _run_bytes(["ls-files", "--cached", "-z"], top)
    if code != 0:
        return None
    return _nul_separated(out)


def resolve_commit(cwd: Path, rev: str) -> str | None:
    """The full hash of the commit ``rev`` names, or ``None`` if it names none.

    ``None`` also covers git being absent and ``cwd`` not being a repository,
    so a caller recording ``rev`` as evidence refuses rather than storing a
    string nothing can check. A name opening with ``-`` is never handed to git,
    where it would be read as an option.
    """
    rev = (rev or "").strip()
    if not rev or rev.startswith("-"):
        return None
    code, out, _ = _run(["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], cwd)
    return out if code == 0 and out else None


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


def commit_messages(cwd: Path, base: str, tip: str = "HEAD") -> str | None:
    """Every message of the commits on ``tip`` since ``base``, joined, or
    ``None`` when git cannot say (an unknown base, an unreadable repository)."""
    code, out, _err = _run(["log", "--format=%B", f"{base}..{tip}"], cwd)
    return out if code == 0 else None


def last_commit_message(cwd: Path) -> str:
    return _run(["log", "-1", "--format=%B"], cwd)[1]


def _status_lines(cwd: Path, args: list[str]) -> list[str] | None:
    """``git status --porcelain`` lines, unstripped: the first column is data."""
    code, out = _run_bytes(["status", "--porcelain", *args], cwd)
    if code != 0:
        return None
    return [line for line in out.decode("utf-8", "replace").splitlines() if line.strip()]


def tracked_changes(cwd: Path) -> list[str] | None:
    """Tracked paths whose working tree or index differs from ``HEAD``.

    Untracked files are not counted: a staged new file is (it is in the index),
    a file nobody has added is not. ``None`` when git cannot answer, which a
    caller deciding whether a tree is clean must read as "not known to be".
    """
    lines = _status_lines(cwd, ["--untracked-files=no"])
    return None if lines is None else [line[3:] for line in lines]


def status_paths(cwd: Path, pathspec: str) -> set[str] | None:
    """Every ``git status`` line under ``pathspec``, untracked files included.

    Lines rather than bare paths, so a file that was untracked before and is
    still untracked after an edit compares equal, while a tracked file that
    became modified does not. ``None`` when git cannot answer.
    """
    lines = _status_lines(cwd, ["--untracked-files=all", "--", pathspec])
    return None if lines is None else set(lines)


def tree_paths(cwd: Path, ref: str, directory: str) -> list[str] | None:
    """The file paths committed under ``directory`` at ``ref``.

    Read from the commit, never from the working tree, so an uncommitted file
    is not in the answer. An absent directory is an empty list; ``None`` means
    the ref could not be read.
    """
    if not ref or ref.startswith("-"):
        return None
    code, _, _ = _run(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd)
    if code != 0:
        return None
    prefix = directory.strip("/")
    code, out, _ = _run(
        ["ls-tree", "-r", "--name-only", "--full-tree", ref, "--", f"{prefix}/"], cwd
    )
    if code != 0:
        return None
    return [line for line in out.splitlines() if line.strip()]


def commit_only(cwd: Path, paths: list[str], message: str) -> tuple[bool, str]:
    """Commit exactly ``paths`` and nothing else that may be staged. A WRITE.

    One of this module's writes, each named as one (ADR-010 D9): the sweep commits
    its own record and the items it filed. The paths are added first, because
    ``--only`` refuses a path git does not yet track; ``--only`` then commits
    those paths alone, leaving anything else in the index where it was. It
    never pushes. Returns ``(committed, git's own output)``.
    """
    if not paths:
        return False, "nothing to commit"
    code, out, err = _run(["add", "--", *paths], cwd)
    if code != 0:
        return False, err or out
    code, out, err = _run(["commit", "--only", "-m", message, "--", *paths], cwd)
    return code == 0, (out if code == 0 else (err or out))


def set_local_config(cwd: Path, key: str, value: str) -> tuple[bool, str]:
    """Set ``key`` in the repository's local git config. A WRITE.

    A write, named as one (ADR-014 D4): ``store link``
    records the repository's ``invariant.id`` here, which every linked
    worktree reads, a move keeps and a clone does not copy. Returns ``(set,
    git's own output)``.
    """
    code, out, err = _run(["config", "--local", key, value], cwd)
    return code == 0, (out if code == 0 else (err or out or f"git exited {code}"))


def init_repository(cwd: Path) -> tuple[bool, str]:
    """Make ``cwd`` a new git repository. A WRITE.

    Named as one (ADR-014 D2): ``store init`` creates a store, which is a git
    repository. Nothing is added or committed. Returns ``(made, git's own
    output)``.
    """
    code, out, err = _run(["init", "-q"], cwd)
    return code == 0, (out if code == 0 else (err or out or f"git exited {code}"))
