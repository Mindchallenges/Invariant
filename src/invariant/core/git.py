"""Thin git queries used by the session rules.

Everything here is a read unless the name says otherwise, and every function
degrades to a safe answer when git is absent or the directory is not a
repository — a rule that crashes is worse than a rule that abstains.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterator, Sequence
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


def is_ignored(cwd: Path, path: str) -> bool | None:
    """Whether git ignores the untracked ``path``: ``True``, ``False``, or ``None``.

    Any ignore source counts: ``.gitignore``, ``.git/info/exclude`` or the
    global excludes file. A tracked path is never ignored. ``None`` is git
    unable to answer, outside a repository for one.
    """
    code, _, _ = _run(["check-ignore", "-q", "--", path], cwd)
    if code in (0, 1):
        return code == 0
    return None


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


def tree_signature(cwd: Path) -> tuple[str, str] | None:
    """The tree in ``cwd`` as a step run judges it (ADR-021 D3): its commit,
    and a digest of everything uncommitted.

    The digest covers ``git diff HEAD`` (staged and unstaged alike, binary
    content included) and every untracked file git does not ignore, by path
    and content. Two trees with one commit and one digest hold the same
    files, so a run recorded over one counts for the other, and a run from
    before the last edit, committed or not, does not. ``None`` when git
    cannot answer: no repository, or no commit yet.

    It also covers what those two reads leave out (ADR-026 D1, a receipt never
    vouches for content it did not hash): an untracked file's executable bit,
    an untracked symlink's target, the contents of a submodule or an embedded
    repository (signed the same way, recursively), and a tracked file git is
    told not to look at (``--assume-unchanged``, ``--skip-worktree``), hashed
    from disk. A file ``.gitignore`` names stays out, and so does the index:
    staging changes no file on disk. A plain untracked file hashes as it
    always has, so a record taken before these were added still matches.
    """
    commit = resolve_commit(cwd, "HEAD")
    if commit is None:
        return None
    digest = _uncommitted_digest(Path(cwd), commit)
    return None if digest is None else (commit, digest)


def _uncommitted_digest(cwd: Path, commit: str | None) -> str | None:
    """The digest half of :func:`tree_signature`; ``None`` when git cannot
    answer. ``commit`` is ``None`` for a repository with no commit yet, which
    only an embedded repository is allowed to be."""
    import hashlib

    digest = hashlib.sha256()
    if commit is not None:
        code, diff = _run_bytes(
            ["diff", "HEAD", "--binary", "--no-color", "--no-ext-diff"], cwd
        )
        if code != 0:
            return None
        digest.update(diff)
    code, listed = _run_bytes(["ls-files", "--others", "--exclude-standard", "-z"], cwd)
    if code != 0:
        return None
    for rel in sorted(_nul_separated(listed)):
        entry = _entry_digest(cwd / rel)
        if entry is None:
            return None
        digest.update(b"\0untracked\0" + rel.encode("utf-8", "surrogateescape") + b"\0")
        digest.update(entry)
    code, tagged = _run_bytes(["ls-files", "-v", "-z"], cwd)
    if code != 0:
        return None
    hidden = sorted(
        rel[2:]
        for rel in _nul_separated(tagged)
        if len(rel) > 2 and (rel[0].islower() or rel[0] == "S")
    )
    for rel in hidden:
        entry = _entry_digest(cwd / rel)
        if entry is None:
            return None
        digest.update(b"\0hidden\0" + rel.encode("utf-8", "surrogateescape") + b"\0")
        digest.update(entry)
    code, staged = _run_bytes(["ls-files", "--stage", "-z"], cwd)
    if code != 0:
        return None
    for line in sorted(_nul_separated(staged)):
        if not line.startswith("160000 ") or "\t" not in line:
            continue
        rel = line.split("\t", 1)[1]
        if not (cwd / rel / ".git").exists():
            continue  # not checked out: git diff HEAD already names its commit
        inner = _nested_digest(cwd / rel)
        if inner is None:
            return None
        digest.update(b"\0submodule\0" + rel.encode("utf-8", "surrogateescape") + b"\0")
        digest.update(inner)
    return digest.hexdigest()


def _nested_digest(path: Path) -> bytes | None:
    """A submodule's or an embedded repository's state: its commit and digest."""
    commit = resolve_commit(path, "HEAD")
    inner = _uncommitted_digest(path, commit)
    if inner is None:
        return None
    return f"{commit or ''}\0{inner}".encode()


def _entry_digest(path: Path) -> bytes | None:
    """One file as the signature hashes it, without following a symlink.

    A plain file is the SHA-256 of its content, exactly as before; an
    executable one is marked; a symlink is its target; a directory, which
    ``ls-files --others`` lists only for an embedded repository, is that
    repository's own signature. ``None`` for a directory that is no
    repository, which no reading here can sign.
    """
    import hashlib
    import stat

    try:
        st = path.lstat()
    except OSError:
        return b"absent"
    if stat.S_ISLNK(st.st_mode):
        try:
            return b"symlink\0" + os.fsencode(os.readlink(path))
        except OSError:
            return b"unreadable"
    if stat.S_ISDIR(st.st_mode):
        if not (path / ".git").exists():
            return None
        inner = _nested_digest(path)
        return None if inner is None else b"repository\0" + inner
    try:
        content = hashlib.sha256(path.read_bytes()).digest()
    except OSError:
        return b"unreadable"
    if st.st_mode & 0o111:
        return b"executable\0" + content
    return content


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


def is_ancestor(cwd: Path, ancestor: str, descendant: str) -> bool | None:
    """Is ``ancestor`` contained in ``descendant``'s history (or the same commit)?

    ``None`` when git cannot say: a name that resolves to no commit, or one
    opening with ``-``, which is never handed to git as an option. A landing
    record checked against git (ADR-014 D6) must stop on ``None``, never read
    it as "contained".
    """
    if not ancestor or not descendant or ancestor.startswith("-") or descendant.startswith("-"):
        return None
    if resolve_commit(cwd, ancestor) is None or resolve_commit(cwd, descendant) is None:
        return None
    code, _out, _err = _run(["merge-base", "--is-ancestor", ancestor, descendant], cwd)
    if code in (0, 1):
        return code == 0
    return None


def commit_lines(
    cwd: Path, start: str, end: str, paths: Sequence[str] = ()
) -> list[str] | None:
    """``<sha> <subject>`` for each commit in ``start..end``, newest first,
    kept to those touching ``paths`` when any are named; ``None`` when git
    cannot read the range."""
    if not start or not end or start.startswith("-") or end.startswith("-"):
        return None
    args = ["log", "--format=%H %s", f"{start}..{end}"]
    if paths:
        args += ["--", *paths]
    code, out, _err = _run(args, cwd)
    return None if code != 0 else [line for line in out.splitlines() if line.strip()]


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


def commit_only(
    cwd: Path, paths: list[str], message: str, *, force: bool = False
) -> tuple[bool, str]:
    """Commit exactly ``paths`` and nothing else that may be staged. A WRITE.

    One of this module's writes, each named as one (ADR-010 D9): the sweep commits
    its own record and the items it filed. The paths are added first, because
    ``--only`` refuses a path git does not yet track; ``--only`` then commits
    those paths alone, leaving anything else in the index where it was. It
    never pushes. ``force`` adds a path git ignores, which ``store migrate
    --to repo`` commits: the local wiring it makes committed is listed in
    ``.git/info/exclude`` until the move lands (ADR-020 D3). Returns
    ``(committed, git's own output)``.
    """
    if not paths:
        return False, "nothing to commit"
    code, out, err = _run(["add", *(["--force"] if force else []), "--", *paths], cwd)
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


def diff_names(cwd: Path, start: str, end: str) -> list[str] | None:
    """The paths that differ between the commits ``start`` and ``end``.

    Two endpoints, not a merge base: a docs-lock span (ADR-014 D5) is a stretch
    of the store's own main line, so what changed across it is the plain diff.
    ``None`` when git cannot answer.
    """
    if not start or not end or start.startswith("-") or end.startswith("-"):
        return None
    code, out = _run_bytes(["diff", "--name-only", "-z", start, end], cwd)
    return None if code != 0 else _nul_separated(out)


def dirty_paths(cwd: Path) -> list[str] | None:
    """Every path ``git status`` reports changed or untracked, each file named.

    Untracked directories are listed file by file, so a new file inside a
    docs-lock unit is seen as that file. ``None`` when git cannot answer.
    """
    code, out = _run_bytes(["status", "--porcelain", "-z", "--untracked-files=all"], cwd)
    if code != 0:
        return None
    entries = _nul_separated(out)
    paths: list[str] = []
    skip = False
    for entry in entries:
        if skip:
            skip = False
            continue
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        # A rename or copy carries its source as the next entry.
        if entry[0] in "RC":
            skip = True
    return paths


# -- a store's remote (ADR-014 D7) -------------------------------------------

#: What a call that talks to a remote runs under: no credential prompt, and an
#: ssh that never asks a question, so a remote that would ask counts as
#: unreachable instead of hanging the command (ADR-014 D7).
_REMOTE_ENV = {"GIT_TERMINAL_PROMPT": "0"}
_BATCH_SSH = "ssh -o BatchMode=yes"


def _remote_env() -> dict[str, str]:
    env = {**os.environ, **_REMOTE_ENV}
    env.setdefault("GIT_SSH_COMMAND", _BATCH_SSH)
    return env


def _run_remote(args: list[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
    """A git call that may reach a remote: no prompt, and ``timeout`` seconds.

    A time limit reached is git unable to answer, which is what a caller
    asking "could the remote be reached" must hear.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_remote_env(),
        )
    except subprocess.TimeoutExpired:
        return 1, "", f"no answer from the remote within {timeout:g}s"
    except (OSError, UnicodeDecodeError) as exc:
        return 1, "", f"git unavailable: {exc}"
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def config_value(cwd: Path, key: str) -> str | None:
    """One value of the repository's git config, or ``None`` when unset."""
    code, out, _ = _run(["config", "--get", key], cwd)
    return out if code == 0 and out else None


def remotes(cwd: Path) -> list[str] | None:
    """The repository's remotes by name; ``None`` when git cannot answer."""
    code, out, _ = _run(["remote"], cwd)
    return None if code != 0 else [line for line in out.splitlines() if line.strip()]


def ahead_behind(cwd: Path, upstream: str) -> tuple[int, int] | None:
    """How many commits ``HEAD`` has that ``upstream`` lacks, and the reverse.

    ``None`` when either cannot be read.
    """
    code, out, _ = _run(["rev-list", "--left-right", "--count", f"HEAD...{upstream}"], cwd)
    parts = out.split()
    if code != 0 or len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1])


def diff_text(cwd: Path, start: str, end: str, path: str) -> str | None:
    """The unified diff of one path between two commits; ``None`` when unreadable."""
    code, out = _run_bytes(["diff", "--no-color", start, end, "--", path], cwd)
    return None if code != 0 else out.decode("utf-8", "replace")


def fetch(cwd: Path, remote: str, timeout: float) -> tuple[bool, str]:
    """Fetch ``remote`` into its remote-tracking refs. A WRITE.

    Named as one (ADR-014 D7): the store's pull at session start, resume and
    ``docs lock``. It moves no branch and no working tree. It runs with no
    prompt and the time limit, so a remote asking for credentials, or not
    answering, fails here as unreachable. Returns ``(fetched, git's output)``.
    """
    code, out, err = _run_remote(["fetch", "--quiet", "--no-tags", remote], cwd, timeout)
    return code == 0, (out if code == 0 else (err or out or f"git exited {code}"))


def fast_forward(cwd: Path, rev: str) -> tuple[bool, str]:
    """Fast-forward the checked-out branch to ``rev``, never merging. A WRITE.

    Named as one (ADR-014 D7): a store that is behind its remote. Returns
    ``(moved, git's output)``.
    """
    code, out, err = _run(["merge", "--ff-only", "--quiet", rev], cwd)
    return code == 0, (out if code == 0 else (err or out or f"git exited {code}"))


def rebase(cwd: Path, onto: str) -> tuple[bool, str]:
    """Replay the checked-out branch's own commits onto ``onto``. A WRITE.

    Named as one (ADR-014 D7, ``store pull --replay``). A rebase that stops
    is aborted, so the branch is left where it was; the caller has already
    refused a file changed on both sides. Returns ``(replayed, git's output)``.
    """
    code, out, err = _run(["rebase", "--quiet", onto], cwd)
    if code == 0:
        return True, out
    _run(["rebase", "--abort"], cwd)
    return False, err or out or f"git exited {code}"


def clone(remote: str, dest: Path, timeout: float) -> tuple[bool, str]:
    """Clone ``remote`` into the new directory ``dest``. A WRITE.

    Named as one (ADR-014 D7, ``store clone``, ruling 18): the one route by
    which a store first reaches a machine. No prompt, and the time limit.
    Returns ``(cloned, git's output)``.
    """
    code, out, err = _run_remote(
        ["clone", "--quiet", remote, str(dest)], Path(dest).parent, timeout
    )
    return code == 0, (out if code == 0 else (err or out or f"git exited {code}"))


# -- a store's push and its screen (ADR-014 D8) --------------------------------


def local_config_value(cwd: Path, key: str) -> str | None:
    """One value of the repository's LOCAL git config, or ``None`` when unset.

    The store's privacy screen asks whether ``core.hooksPath`` is set there,
    where a global or system value cannot answer for it (ADR-014 D8).
    """
    code, out, _ = _run(["config", "--local", "--get", key], cwd)
    return out if code == 0 and out else None


def commits_to_publish(
    cwd: Path, tip: str, exclude: Sequence[str] = (), remote: str | None = None
) -> list[str] | None:
    """The commits ``tip`` reaches that a push would publish, oldest first.

    Excluded: every commit ``exclude`` names, and with ``remote`` every
    commit one of its remote-tracking refs reaches, which is what the remote
    is known to hold. A remote never fetched has no such refs, so the first
    push after linking a remote lists the whole unpublished history. With
    neither, the whole history ``tip`` reaches. ``None`` when git cannot
    read ``tip``, which a caller screening a push must read as a refusal.
    """
    if not _is_revision(cwd, tip):
        return None
    args = ["rev-list", "--reverse", tip]
    nots = [rev for rev in exclude if _is_revision(cwd, rev)]
    if remote:
        if remote.startswith("-"):
            return None
        nots.append(f"--remotes={remote}")
    if nots:
        args += ["--not", *nots]
    code, out, _ = _run(args, cwd)
    return None if code != 0 else [line for line in out.splitlines() if line.strip()]


def commit_paths(cwd: Path, sha: str) -> list[str] | None:
    """The paths whose content ``sha`` adds or changes, against its first parent.

    A root commit adds every file it holds. A deleted path carries no
    content, so it is not listed. ``None`` when git cannot read the commit.
    """
    if not _is_revision(cwd, sha):
        return None
    code, out, _ = _run(["rev-list", "--parents", "-n", "1", sha], cwd)
    if code != 0 or not out:
        return None
    parents = out.split()[1:]
    if not parents:
        code, raw = _run_bytes(["ls-tree", "-r", "--name-only", "-z", sha], cwd)
    else:
        code, raw = _run_bytes(
            ["diff", "--name-only", "-z", "--no-renames", "--diff-filter=d", parents[0], sha],
            cwd,
        )
    return None if code != 0 else _nul_separated(raw)


def commit_message(cwd: Path, sha: str) -> str | None:
    """The whole message of the commit ``sha``, or ``None`` when unreadable."""
    if not _is_revision(cwd, sha):
        return None
    code, raw = _run_bytes(["log", "-1", "--format=%B", sha], cwd)
    return None if code != 0 else raw.decode("utf-8", "replace")


def push(cwd: Path, remote: str, refspec: str, timeout: float) -> tuple[bool, str]:
    """Push ``refspec`` to ``remote``. A WRITE, and the one that publishes.

    Named as one (ADR-014 D8): ``store push`` and push after landing (D7)
    call it only after the store's privacy screen passed every outgoing
    commit. The store's ``pre-push`` hook runs the screen again. No prompt,
    and the time limit. Returns ``(pushed, git's output)``.
    """
    if not remote or remote.startswith("-") or refspec.startswith("-"):
        return False, f"refused to push {refspec!r} to {remote!r}"
    code, out, err = _run_remote(["push", "--quiet", remote, refspec], cwd, timeout)
    return code == 0, (out if code == 0 else (err or out or f"git exited {code}"))


# -- moving records with their history (ADR-014 D12) -------------------------

#: How long one history export or import may take. A store's records are
#: text, but a long history is still many commits.
TRANSFER_TIMEOUT_SECONDS = 600


def files_under(
    cwd: Path, paths: Sequence[str], *, kind: str = "tracked"
) -> list[str] | None:
    """The files under ``paths``, spelled from the top of the tree.

    ``kind`` is ``tracked`` (the index), ``untracked`` (neither tracked nor
    ignored) or ``ignored`` (untracked and ignored, a wholly ignored folder
    named once with its slash). No paths is an empty list, never every file.
    ``None`` when git cannot answer.
    """
    if not paths:
        return []
    if any(p.startswith("-") for p in paths):
        return None
    flags = {
        "tracked": ["--cached"],
        "untracked": ["--others", "--exclude-standard"],
        "ignored": ["--others", "--ignored", "--exclude-standard", "--directory"],
    }[kind]
    code, out = _run_bytes(["ls-files", "-z", *flags, "--", *paths], cwd)
    return None if code != 0 else _nul_separated(out)


def changed_under(cwd: Path, paths: Sequence[str]) -> list[str] | None:
    """Every path under ``paths`` that ``git status`` reports: changed,
    staged, deleted or untracked and not ignored. ``None`` when git cannot
    answer."""
    if not paths:
        return []
    if any(p.startswith("-") for p in paths):
        return None
    code, out = _run_bytes(
        ["status", "--porcelain", "-z", "--untracked-files=all", "--", *paths], cwd
    )
    if code != 0:
        return None
    found: list[str] = []
    skip = False
    for entry in _nul_separated(out):
        if skip:
            skip = False
            continue
        if len(entry) < 4:
            continue
        found.append(entry[3:])
        if entry[0] in "RC":
            skip = True
    return found


def fast_export(cwd: Path, rev: str, paths: Sequence[str]) -> bytes | None:
    """``git fast-export <rev> -- <paths>``: the history of ``paths`` alone.

    Only the commits that touch those paths, carrying only their blobs,
    linear wherever git's history simplification can make it so. Signed
    tags and commits are exported without their signatures, which a
    rewritten commit could not keep. ``None`` when git cannot answer.
    """
    if not rev or rev.startswith("-") or any(p.startswith("-") for p in paths):
        return None
    base = ["fast-export", "--signed-tags=strip", "--reencode=yes"]
    for extra in (["--signed-commits=strip"], []):
        try:
            proc = subprocess.run(
                ["git", *base, *extra, rev, "--", *paths],
                cwd=str(cwd),
                capture_output=True,
                timeout=TRANSFER_TIMEOUT_SECONDS,
                check=False,
                env=_env(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode == 0:
            return proc.stdout
        # An older git has no --signed-commits and strips by default.
        if not extra or b"signed-commits" not in proc.stderr:
            return None
    return None  # pragma: no cover - the loop returns


def fast_import(cwd: Path, stream: bytes) -> tuple[bool, str]:
    """Feed ``stream`` to ``git fast-import``. A WRITE.

    Named as one (ADR-014 D12): ``store migrate`` writes the exported
    history under a temporary ref, which it then fast-forwards onto and
    deletes. ``--quiet``, and refusing to move a ref it would not move
    forward. Returns ``(imported, git's output)``.
    """
    try:
        proc = subprocess.run(
            ["git", "fast-import", "--quiet"],
            cwd=str(cwd),
            input=stream,
            capture_output=True,
            timeout=TRANSFER_TIMEOUT_SECONDS,
            check=False,
            env=_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    said = proc.stderr.decode("utf-8", "replace").strip()
    return proc.returncode == 0, said


def delete_ref(cwd: Path, ref: str) -> bool:
    """Delete ``ref``. A WRITE, for the temporary ref an import leaves."""
    if not ref.startswith("refs/"):
        return False
    code, _, _ = _run(["update-ref", "-d", ref], cwd)
    return code == 0
