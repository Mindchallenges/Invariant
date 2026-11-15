"""A store's privacy screen (ADR-014 D8, increment 6).

Every store commit is scanned before it can leave. The screen reads the
**store's own** ``privacy`` block, at the top level of its ``invariant.yaml``,
as it stands in the commit being judged: its ``allow``, its ``patterns`` and
``extra``, and its ``scope``, which in a store defaults to ``all`` (D8 item 4).
Paths are spelled store-relative. A commit's message is always read.

Three places call it. The push (``store push``, push after landing, and the
store's ``pre-push`` git hook) scans every outgoing commit, and a finding
refuses it. The ``pre-commit`` and ``commit-msg`` hooks run the same scan at
commit time. ``privacy.clean`` asks it for a store path at the edit, at a
commit made in the store and at the review boundary. The hooks live under
the store's git common directory and are reached through its local
``core.hooksPath``, so a global hooks path cannot skip them; they fire from a
linked worktree of the store too.

The relaxation (ruling 10): ``privacy.store.scan: false`` turns the
commit-time screens off only while the store has no remote. A push always
scans, and while the key is present it scans the whole history it pushes.

Nothing here reaches a network, pushes or decides what a finding means for a
command: a caller refuses on a finding, and on every question git cannot
answer.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from . import git
from .errors import ManifestError
from .privacy import Finding, PrivacyScanner, parse_allow
from .stores import STORE_MANIFEST

#: The git hooks the screen installs in a store.
SCREEN_HOOKS = ("pre-commit", "commit-msg", "pre-push")

#: A store's scope when its privacy block names none (D8 item 4): the store
#: is invariant's own record, and the part of a project that can be shared.
DEFAULT_SCOPE = ("all",)

#: Where the hooks live, under the store's git common directory.
HOOKS_RELDIR = PurePosixPath("invariant") / "hooks"

#: The line every installed hook carries, by which ``hooks_state`` knows it.
HOOK_MARKER = "# invariant: the store's privacy screen (ADR-014 D8)"

#: The keys ``privacy.store`` may hold.
STORE_PRIVACY_KEYS = ("scan",)

#: Scope keywords and the ``paths.*`` keys each resolves through, store-relative.
_KEYWORD_PATHS = {
    "docs": ("docs",),
    "memory": ("memory", "memory_index"),
    "reports": ("reports",),
}

_ZERO_SHA = "0" * 40


def check_store_key(value: Any, where: str) -> bool:
    """``privacy.store`` checked; whether it turns the scan off as written."""
    if not isinstance(value, Mapping):
        raise ManifestError(f"{where}: privacy.store must be a mapping, got {value!r}")
    for key in value:
        if key not in STORE_PRIVACY_KEYS:
            raise ManifestError(
                f"{where}: privacy.store.{key} is not a setting; use "
                f"{', '.join(STORE_PRIVACY_KEYS)}"
            )
    scan = value.get("scan", True)
    if not isinstance(scan, bool):
        raise ManifestError(f"{where}: privacy.store.scan must be true or false, got {scan!r}")
    return scan is False


# -- the screen -------------------------------------------------------------------


@dataclass(frozen=True)
class StoreScreen:
    """The store's privacy block as one tree holds it, ready to scan with."""

    root: Path
    scanner: PrivacyScanner
    scope: tuple[str, ...]
    #: Store-relative prefixes the scope's keywords and literals name.
    prefixes: tuple[str, ...] = ()
    #: ``privacy.store.scan: false`` as written, whether or not it is honoured.
    relaxed: bool = False

    def covers(self, rel: str) -> bool:
        """Is the store-relative ``rel`` inside the store's scope?"""
        if "all" in self.scope:
            return True
        rel = PurePosixPath(rel).as_posix()
        return any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in self.prefixes)

    def scan(self, rel: str, text: str) -> list[Finding]:
        return self.scanner.scan_text(text, path=Path(rel))

    def scan_message(self, text: str) -> list[Finding]:
        return self.scanner.scan_text(text)


def screen_from(root: Path, text: str | bytes | None, where: str) -> StoreScreen:
    """The screen a store config's text declares; the defaults with no text.

    Refused (``ManifestError``) when the text, or its privacy block, would load
    and screen something other than what it says.
    """
    root = Path(root)
    raw: Any = {}
    if text is not None:
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        try:
            raw = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise ManifestError(f"{where} is not valid YAML: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ManifestError(f"{where} must contain a mapping")
    block = raw.get("privacy") or {}
    if not isinstance(block, Mapping):
        raise ManifestError(f"{where}: privacy must be a mapping")
    relaxed = check_store_key(block["store"], where) if "store" in block else False
    scope = block.get("scope")
    if scope is None:
        scope = list(DEFAULT_SCOPE)
    if not isinstance(scope, list) or not all(isinstance(s, str) for s in scope):
        raise ManifestError(f"{where}: privacy.scope must be a list of entries")
    try:
        parse_allow(block.get("allow") or [])
        scanner = PrivacyScanner(
            patterns=block.get("patterns"),
            extra=block.get("extra") or (),
            allow=block.get("allow") or (),
            root=root,
            manifest_path=root / STORE_MANIFEST,
        )
    except ValueError as exc:
        raise ManifestError(f"{where}: {exc}") from exc
    return StoreScreen(
        root=root,
        scanner=scanner,
        scope=tuple(scope),
        prefixes=tuple(_prefixes(raw, scope)),
        relaxed=relaxed,
    )


def _prefixes(raw: Mapping, scope: Iterable[str]) -> list[str]:
    """The store-relative prefixes ``scope`` names: each literal entry, and
    each keyword's ``paths.*`` as the store's top level and its repository
    blocks spell them."""
    levels: list[Mapping] = [raw]
    repos = (raw.get("store") or {}).get("repos") if isinstance(raw.get("store"), Mapping) else {}
    if isinstance(repos, Mapping):
        levels += [block for block in repos.values() if isinstance(block, Mapping)]
    out: list[str] = []
    for entry in scope:
        if entry in ("all", "commit_messages"):
            continue
        if entry not in _KEYWORD_PATHS:
            out.append(_store_relative(entry))
            continue
        for level in levels:
            paths = level.get("paths") if isinstance(level.get("paths"), Mapping) else {}
            for key in _KEYWORD_PATHS[entry]:
                value = paths.get(key)
                if isinstance(value, str) and value and not Path(value).is_absolute():
                    out.append(_store_relative(value))
    return [p for p in dict.fromkeys(out) if p and p != "."]


def _store_relative(path: str) -> str:
    return PurePosixPath(os.path.normpath(path)).as_posix().strip("/")


def screen_at(root: Path, rev: str) -> StoreScreen:
    """The screen as the commit ``rev`` holds the store's config."""
    root = Path(root)
    present = git.exists_at(root, rev, STORE_MANIFEST)
    if present is None:
        raise ManifestError(f"the store's commit {rev[:12]} cannot be read")
    text = git.blob_at(root, rev, STORE_MANIFEST) if present else None
    if present and text is None:
        raise ManifestError(f"{STORE_MANIFEST} at {rev[:12]} cannot be read")
    return screen_from(root, text, f"{STORE_MANIFEST} at {rev[:12]}")


def screen_staged(root: Path) -> StoreScreen:
    """The screen as the commit being made will hold it: the staged config,
    else the defaults (a store whose config is not in the commit)."""
    root = Path(root)
    text = git.staged_blob(root, STORE_MANIFEST)
    return screen_from(root, text, f"the staged {STORE_MANIFEST}")


def screen_now(root: Path) -> StoreScreen:
    """The screen as the store's working tree holds it."""
    path = Path(root) / STORE_MANIFEST
    try:
        text: str | None = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = None
    except OSError as exc:
        raise ManifestError(f"{path} cannot be read: {exc}") from exc
    return screen_from(root, text, str(path))


# -- what a scan found ------------------------------------------------------------


@dataclass
class Screened:
    """What one run of the screen looked at, found, or could not read."""

    scanned: list[str] = field(default_factory=list)
    #: ``(where, finding)``: where is ``<sha> <path>``, ``<sha> message``, a
    #: staged path or ``commit message``.
    findings: list[tuple[str, Finding]] = field(default_factory=list)
    #: Set when something in scope could not be read: a refusal, never a pass.
    problem: str | None = None

    @property
    def clean(self) -> bool:
        return self.problem is None and not self.findings

    def lines(self) -> list[str]:
        """The findings, one per line, redacted."""
        out = []
        if self.problem:
            out.append(f"  could not be screened: {self.problem}")
        for where, finding in self.findings[:20]:
            out.append(f"  {where}:{finding.line}:{finding.column}  [{finding.pattern}]  "
                       f"{finding.redacted}")
        if len(self.findings) > 20:
            out.append(f"  ... and {len(self.findings) - 20} more")
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": len(self.scanned),
            "findings": [
                {"where": where, "line": f.line, "column": f.column, "pattern": f.pattern,
                 "redacted": f.redacted}
                for where, f in self.findings
            ],
            "problem": self.problem,
        }


def _text(blob: bytes) -> str:
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        # Bytes that do not decode carry nothing that can leak in text form,
        # the answer `PrivacyScanner.scan_file` gives a binary file.
        return ""


def scan_commits(root: Path, shas: Sequence[str]) -> Screened:
    """Every commit in ``shas``: its content in scope, and its message, each
    read with the store's privacy block as that commit holds it."""
    root = Path(root)
    out = Screened()
    for sha in shas:
        short = sha[:12]
        try:
            screen = screen_at(root, sha)
        except ManifestError as exc:
            out.problem = str(exc)
            return out
        paths = git.commit_paths(root, sha)
        if paths is None:
            out.problem = f"the files of commit {short} cannot be read"
            return out
        for rel in paths:
            if not screen.covers(rel):
                continue
            blob = git.blob_at(root, sha, rel)
            if blob is None:
                out.problem = f"{rel} at {short} is inside the store's scope and cannot be read"
                return out
            out.scanned.append(f"{short} {rel}")
            out.findings += [(f"{short} {rel}", f) for f in screen.scan(rel, _text(blob))]
        message = git.commit_message(root, sha)
        if message is None:
            out.problem = f"the message of commit {short} cannot be read"
            return out
        out.scanned.append(f"{short} message")
        out.findings += [(f"{short} message", f) for f in screen.scan_message(message)]
    return out


def scan_staged(root: Path, screen: StoreScreen | None = None) -> Screened:
    """What the commit being made in the store would put into its history."""
    root = Path(root)
    out = Screened()
    try:
        screen = screen or screen_staged(root)
    except ManifestError as exc:
        out.problem = str(exc)
        return out
    listed = git.staged_files(root, exclude_deleted=True)
    if listed is None:
        out.problem = "git could not list what is staged in the store"
        return out
    for path in listed:
        rel = Path(path).as_posix()
        if not screen.covers(rel):
            continue
        blob = git.staged_blob(root, rel)
        if blob is None:
            out.problem = f"{rel} is staged inside the store's scope and cannot be read"
            return out
        out.scanned.append(rel)
        out.findings += [(rel, f) for f in screen.scan(rel, _text(blob))]
    return out


def scan_message(root: Path, text: str, screen: StoreScreen | None = None) -> Screened:
    """A commit message about to be written in the store. Lines git strips
    as comments are not part of it."""
    out = Screened()
    try:
        screen = screen or screen_staged(Path(root))
    except ManifestError as exc:
        out.problem = str(exc)
        return out
    kept = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    out.scanned.append("commit message")
    out.findings += [("commit message", f) for f in screen.scan_message(kept)]
    return out


# -- what a push publishes ----------------------------------------------------------


def outgoing(
    root: Path,
    tip: str,
    remote: str | None,
    *,
    remote_sha: str | None = None,
    whole: bool = False,
) -> list[str] | None:
    """The commits a push of ``tip`` to ``remote`` publishes, oldest first.

    What ``remote``'s tracking refs and ``remote_sha`` already hold is left
    out, so the first push after linking a remote lists the whole unpublished
    history. ``whole`` lists every commit ``tip`` reaches: a push while
    ``privacy.store.scan: false`` is present (ruling 10). ``None`` when git
    cannot say.
    """
    if whole:
        return git.commits_to_publish(Path(root), tip)
    exclude = [remote_sha] if remote_sha and remote_sha != _ZERO_SHA else []
    return git.commits_to_publish(Path(root), tip, exclude, remote)


def pre_push(root: Path, remote: str, lines: str) -> tuple[list[str], Screened]:
    """The ``pre-push`` hook: every commit each pushed ref would publish.

    ``lines`` is what git hands the hook on stdin, ``<local ref> <local sha>
    <remote ref> <remote sha>`` per ref; a deletion publishes nothing.
    ``remote`` is the name, or the URL when the push named no remote.
    Returns the commits and the scan.
    """
    root = Path(root)
    named = remote in (git.remotes(root) or [])
    whole = relaxation(root).declared
    commits: list[str] = []
    for line in lines.splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[1] == _ZERO_SHA:
            continue
        found = outgoing(
            root, parts[1], remote if named else None, remote_sha=parts[3], whole=whole
        )
        if found is None:
            return commits, Screened(problem=f"git cannot list what {parts[0]} would publish")
        commits += [sha for sha in found if sha not in commits]
    return commits, scan_commits(root, commits)


# -- the relaxation (ruling 10) ------------------------------------------------------


@dataclass(frozen=True)
class Relaxation:
    """``privacy.store.scan: false``, and whether the store lets it count."""

    declared: bool
    remotes: tuple[str, ...] = ()
    problem: str | None = None

    @property
    def honoured(self) -> bool:
        """Honoured only while the store has no remote (D8 item 3)."""
        return self.declared and not self.remotes and self.problem is None


def relaxation(root: Path) -> Relaxation:
    """Whether the store's working config turns its scan off, and its remotes.

    A config that does not load declares nothing, and a store whose remotes
    git cannot list is not known to have none: neither relaxes anything.
    """
    try:
        declared = screen_now(root).relaxed
    except ManifestError as exc:
        return Relaxation(False, problem=str(exc))
    names = git.remotes(Path(root))
    if names is None:
        return Relaxation(declared, problem="git cannot list the store's remotes")
    return Relaxation(declared, tuple(names))


# -- the hooks ------------------------------------------------------------------------


def hooks_dir(root: Path) -> Path | None:
    """The directory the hooks live in, under the store's git common directory."""
    common = git.common_dir(Path(root))
    return None if common is None else common / HOOKS_RELDIR


def hook_script(name: str, python: str, source: str) -> str:
    """The body of one hook: run the screen, then the store's own hook, if any.

    ``python`` and ``source`` name the interpreter and the directory holding
    the ``invariant`` package that installed it, so the hook runs the same
    build; an ``invariant`` on ``PATH`` is the fallback. With neither the
    hook refuses: the screen fails closed. The file lives in the store's git
    directory and is never committed.
    """
    feed = 'input=$(cat)\nsay() { printf \'%s\\n\' "$input"; }\n' if name == "pre-push" else (
        "say() { :; }\n"
    )
    return f"""#!/bin/sh
{HOOK_MARKER}
# Installed by invariant through the store's local core.hooksPath, and kept
# current at session start. It refuses a commit or a push carrying personal
# data, read with the store's own privacy block.
hook={name}
py={shlex.quote(python)}
src={shlex.quote(source)}
{feed}screen() {{
  if [ -x "$py" ] && [ -d "$src/invariant" ]; then
    PYTHONPATH="$src${{PYTHONPATH:+:$PYTHONPATH}}" "$py" -m invariant store hook "$hook" "$@"
  elif command -v invariant >/dev/null 2>&1; then
    invariant store hook "$hook" "$@"
  else
    echo "invariant: the store's $hook privacy screen cannot run: invariant is not installed" >&2
    return 1
  fi
}}
say | screen "$@" || exit $?
own="$(git rev-parse --git-common-dir)/hooks/$hook"
if [ -x "$own" ]; then
  say | "$own" "$@"
  exit $?
fi
exit 0
"""


@dataclass(frozen=True)
class Hooks:
    """What an install did: the hooks written, or why none could be."""

    written: tuple[str, ...] = ()
    problem: str | None = None


def install_hooks(root: Path, python: str, source: str) -> Hooks:
    """Install the screen's hooks in the store and point its local
    ``core.hooksPath`` at them. A WRITE, into the store's git directory.

    Idempotent: a hook already current is left alone. A local
    ``core.hooksPath`` naming somewhere else is not overwritten: the install
    stops naming it, and ``doctor`` fails until it is resolved.
    """
    root = Path(root)
    target = hooks_dir(root)
    if target is None:
        return Hooks(problem=f"{root} is not a git repository")
    current = git.local_config_value(root, "core.hooksPath")
    if current is not None and Path(current).expanduser() != target:
        return Hooks(
            problem=(
                f"the store's local core.hooksPath is {current}, so its privacy hooks "
                f"would not run; remove it with 'git -C {root} config --local --unset "
                f"core.hooksPath' (a hook kept in .git/hooks still runs after the screen)"
            )
        )
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name in SCREEN_HOOKS:
        path = target / name
        body = hook_script(name, python, source)
        if not (path.is_file() and path.read_text(encoding="utf-8") == body):
            path.write_text(body, encoding="utf-8")
            written.append(name)
        if not os.access(path, os.X_OK):
            path.chmod(0o755)
    if current is None:
        ok, said = git.set_local_config(root, "core.hooksPath", str(target))
        if not ok:
            return Hooks(tuple(written), problem=f"core.hooksPath could not be set: {said}")
    return Hooks(tuple(written))


def hooks_state(root: Path) -> str | None:
    """``None`` when every hook is installed and reached; else what is wrong."""
    root = Path(root)
    target = hooks_dir(root)
    if target is None:
        return f"{root} is not a git repository"
    current = git.local_config_value(root, "core.hooksPath")
    if current is None:
        return "the store's local core.hooksPath is not set, so no privacy hook runs"
    if Path(current).expanduser() != target:
        return f"the store's local core.hooksPath is {current}, not its privacy hooks"
    missing = []
    for name in SCREEN_HOOKS:
        path = target / name
        try:
            ours = HOOK_MARKER in path.read_text(encoding="utf-8")
        except OSError:
            ours = False
        if not ours or not os.access(path, os.X_OK):
            missing.append(name)
    if missing:
        return f"hook(s) missing or not invariant's: {', '.join(missing)}"
    return None
