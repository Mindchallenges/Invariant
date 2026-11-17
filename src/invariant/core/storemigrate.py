"""Moving a project's records between its repository and a store (ADR-014 D12).

``invariant store migrate`` is the one route between a project's two homes
(ADR-020 D3): everything moves together, and the configuration has one home
at a time. This module owns what the move is, independent of hosts and
sessions:

- **The plan** (:func:`plan_to_store`): which records move and which paths
  they cover, the block the store gains, the target store, and every refusal,
  checked before anything is written. A refusal names each reason and exits
  2 (:class:`MigrateRefused`).
- **The history** (:func:`transfer`): ``git fast-export <rev> -- <paths>``
  from one home, its stream re-aimed at a temporary ref on the other's HEAD
  (:func:`retarget`, which honours every ``data <n>`` length), imported, and
  fast-forwarded onto. The same relative paths, so nothing is rewritten; the
  original commits, so ``git log -- <path>`` shows them. Usable both ways.
- **The marker** (:data:`MARKER`): one file in the state root naming the
  session that is moving the project, which ``Manifest.locate`` reads to name
  it when a store no longer lists the repository mid-move.

The session that carries the move, its landing and the host wiring are the
CLI's to sequence: this module knows nothing of hosts (ADR-014 ruling 12).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

import yaml

from . import git
from .errors import ManifestError, StateError, UsageError
from .home import display_path, invariant_home, load_user_config
from .identity import Identity, new_local_id, repo_identity, write_local_id
from .stores import (
    STORE_MANIFEST,
    STORES_DIR,
    StoreLink,
    holds_comments,
    holds_only_init_comment,
    lists,
    plan_store_path,
    read_store_manifest,
    repo_name,
)

#: The marker, in the state root: which session is moving this project.
MARKER = "migrate.json"

#: The temporary ref an import writes and the fast-forward then follows.
MIGRATE_REF = "refs/invariant/migrate"

#: The records that move with their files, in the order they are named.
PATH_RECORDS = ("docs", "backlog", "guidelines", "memory", "reports", "sweeps")

#: Where the host wiring goes in a store project (ADR-020 D1, D9).
WIRING_RECORDS = {"hooks": "local", "agents": "local"}

#: A store project's ``records``, as ``init`` writes them for the store home
#: and a move into a store makes them (ADR-020 D1): every data and policy
#: record in the store, the host wiring local to the clone.
STORE_HOME_RECORDS = {**dict.fromkeys(PATH_RECORDS, "store"), **WIRING_RECORDS}

#: Path keys that are no root of their own: ``paths.memory_index`` is a file
#: name inside ``paths.memory`` (``domains/memorystore``).
NOT_A_ROOT = ("paths.memory_index",)

#: The in-repo manifest, as git spells it.
MANIFEST_REL = ".claude/invariant.yaml"

#: The repository's pointer (ADR-014 D3), which only names a store.
POINTER_REL = ".invariant.yaml"

#: Keys a store project alone may set, which an in-repo manifest refuses at
#: load: ``sync`` (ADR-014 D7) and ``privacy.store`` (D8). ``--to repo``
#: leaves them behind, named, rather than write a manifest that cannot load.
STORE_ONLY = ("sync", "privacy.store")

#: How many paths one reason lists before it says how many more there are.
_LISTED = 12


class MigrateRefused(UsageError):
    """``store migrate`` refused, each reason named, nothing written (exit 2)."""

    def __init__(self, direction: str, reasons: Sequence[str]) -> None:
        self.reasons = list(reasons)
        lines = [
            f"store migrate --to {direction} is refused, and nothing was written:",
            *[f"  - {reason}" for reason in self.reasons],
        ]
        super().__init__("\n".join(lines))


# -- the marker -------------------------------------------------------------------


def read_marker(state_root: Path) -> dict[str, Any] | None:
    """The migration in progress, or ``None``; an unreadable file is none."""
    path = Path(state_root) / MARKER
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) and raw.get("session") else None


def write_marker(state_root: Path, data: Mapping[str, Any]) -> None:
    """Record the migration in progress, atomically."""
    root = Path(state_root)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".{MARKER}.tmp"
    tmp.write_text(json.dumps(dict(data), sort_keys=True), encoding="utf-8")
    os.replace(tmp, root / MARKER)


def clear_marker(state_root: Path, session: str) -> bool:
    """Remove the marker when it names ``session``; another's is kept."""
    marker = read_marker(state_root)
    if marker is None or marker.get("session") != session:
        return False
    (Path(state_root) / MARKER).unlink(missing_ok=True)
    return True


def marker_note(state_root: Path) -> str | None:
    """What a refusal adds while a migration is moving this project."""
    marker = read_marker(state_root)
    if marker is None:
        return None
    session = marker["session"]
    worktree = marker.get("worktree")
    return (
        f"store migrate --to {marker.get('to', '?')} is moving this project, in "
        f"session {session}: neither home is complete until its repository leg "
        f"lands. Review it in its worktree"
        + (f", {worktree}" if worktree else "")
        + f"; finish it with 'invariant session merge --id {session}' or "
        f"'invariant session resume --id {session}', which run here while the move "
        f"is under way (ADR-014 D6, ADR-020 D3)."
    )


# -- the stream -------------------------------------------------------------------

_DATA = re.compile(rb"^data (\d+)$")


def _rewrite(stream: bytes, ref: str, parent: str | None) -> tuple[bytes, int]:
    """:func:`retarget`, also counting the commits."""
    target = ref.encode()
    out: list[bytes] = []
    if parent:
        out.append(b"reset " + target + b"\nfrom " + parent.encode() + b"\n\n")
    commits = 0
    pos, size = 0, len(stream)
    dropping_from = False
    while pos < size:
        end = stream.find(b"\n", pos)
        line = stream[pos:] if end < 0 else stream[pos:end]
        nxt = size if end < 0 else end + 1
        data = _DATA.match(line)
        if data:
            length = int(data.group(1))
            if nxt + length > size:
                raise UsageError(
                    "the exported history is cut short: a data block claims "
                    f"{length} bytes past the stream's end, so nothing was imported"
                )
            out.append(line + b"\n" + stream[nxt:nxt + length])
            pos = nxt + length
            dropping_from = False
            continue
        if line.startswith(b"reset "):
            # The export's own branch resets: the stream is re-aimed at one
            # ref, which the prefix above already set to the target's HEAD.
            dropping_from = True
            pos = nxt
            continue
        if dropping_from and line.startswith(b"from "):
            dropping_from = False
            pos = nxt
            continue
        dropping_from = False
        if line.startswith(b"commit "):
            line = b"commit " + target
            commits += 1
        out.append(line + (b"\n" if end >= 0 else b""))
        pos = nxt
    return b"".join(out), commits


def retarget(stream: bytes, ref: str, parent: str | None) -> bytes:
    """Re-aim a ``git fast-export`` stream at ``ref``, on top of ``parent``.

    Every ``commit <ref>`` becomes ``commit <ref>``; the export's ``reset``
    lines, and the ``from`` that may follow one, are dropped; and with a
    ``parent`` the stream opens by resetting ``ref`` to it, so the first
    commit, which names no ``from``, continues from the parent and the rest
    follow their marks. A ``data <n>`` block is copied byte for byte, so a
    blob or a message that reads like a command (``reset ...``) is never
    taken for one. A block cut short refuses.
    """
    return _rewrite(stream, ref, parent)[0]


def transfer(
    source: Path, rev: str, paths: Sequence[str], target: Path, *, ref: str = MIGRATE_REF
) -> int:
    """Carry the history of ``paths`` at ``rev`` in ``source`` onto ``target``'s
    checked-out branch. A WRITE in ``target``. Returns the commits carried.

    The same relative paths; the original authors, dates and messages; only
    the commits that touch those paths, carrying only their blobs. The import
    goes to a temporary ref, the branch fast-forwards onto it, and the ref is
    deleted whatever happened. A fast-forward git refuses (a file in the way)
    moves nothing and refuses, naming git's reason. No paths carries nothing.
    """
    if not paths:
        return 0
    stream = git.fast_export(source, rev, paths)
    if stream is None:
        raise StateError(f"git could not export the history of {', '.join(paths)} at {rev}")
    parent = git.resolve_commit(target, "HEAD")
    if parent is None:
        raise StateError(f"{target} has no commit to carry the history onto")
    rewritten, commits = _rewrite(stream, ref, parent)
    if not commits:
        return 0
    try:
        imported, said = git.fast_import(target, rewritten)
        if not imported:
            raise StateError(f"git fast-import refused the history in {target}: {said}")
        moved, said = git.fast_forward(target, ref)
        if not moved:
            raise UsageError(
                f"the history of {', '.join(paths)} could not be laid onto {target}'s "
                f"branch, so nothing moved there: {said}"
            )
    finally:
        git.delete_ref(target, ref)
    return commits


# -- the plan ---------------------------------------------------------------------


@dataclass
class ToStorePlan:
    """Everything ``store migrate --to store`` will write, decided up front."""

    top: Path
    identity: Identity
    #: Written to the repository's local git config first: it had no identity.
    new_local_id: str | None
    base: str
    head: str
    store: Path
    new_store: bool
    #: ``store init``'s naming note (ruling 14), when the default name was taken.
    note: str | None
    #: Repository-relative files and folders that move, nested ones folded.
    roots: list[str]
    #: Every tracked file under ``roots`` at HEAD.
    moved: list[str]
    #: Each record's location in the store project.
    records: dict[str, str]
    #: Records that stay in the repository, each with the reason.
    kept: dict[str, str]
    #: ``store.repos.<identity>``: the manifest's keys and ``records``.
    block: dict[str, Any]
    #: Ignored files under ``roots`` that are left in the working tree.
    left: list[str] = field(default_factory=list)
    #: The repository has a remote: other clones lose the configuration.
    remote: str | None = None

    @property
    def key(self) -> str:
        return self.identity.key


def _listed(paths: Sequence[str]) -> str:
    shown = ", ".join(paths[:_LISTED])
    more = len(paths) - _LISTED
    return shown + (f" and {more} more" if more > 0 else "")


def _relative(value: Any) -> str | None:
    """``value`` as a repository-relative path, or ``None`` when it is
    absolute or climbs out."""
    text = str(value).strip()
    if not text or os.path.isabs(text) or PurePosixPath(text).is_absolute():
        return None
    parts = PurePosixPath(text).parts
    if ".." in parts:
        return None
    rel = PurePosixPath(*[p for p in parts if p not in ("", ".")]).as_posix()
    return None if rel in ("", ".") else rel


def _values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return list(value.values())
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def _under(value: Any, base: Path) -> str | None:
    """``value`` spelled from ``base``: a relative path as written, an absolute
    one when it lies under ``base``; ``None`` when it climbs out."""
    text = str(value).strip()
    if text and (os.path.isabs(text) or PurePosixPath(text).is_absolute()):
        try:
            rel = Path(text).resolve().relative_to(Path(base).resolve()).as_posix()
        except (OSError, ValueError):
            return None
        return None if rel in ("", ".") else rel
    return _relative(text)


def docs_project(manifest: Any, base: Path | None = None) -> str | None:
    """The docs project's folder, spelled from ``base`` (the repository, or
    the store for a docs project kept there): the resolved
    ``docs.needs.srcdir``, else the folder above ``paths.docs``; ``None``
    where ``paths.docs`` is at the root or outside ``base``."""
    from .needs import srcdir_of  # local: needs imports manifest

    root = Path(base if base is not None else manifest.root)
    srcdir = srcdir_of(manifest)
    if srcdir is not None:
        try:
            rel = Path(srcdir).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = None
        if rel not in (None, "", "."):
            return rel
    docs = _under(manifest.get("paths.docs") or "", root)
    if docs is None:
        return None
    parent = PurePosixPath(docs).parent.as_posix()
    return None if parent in ("", ".") else parent


def _content_folder(
    folder: str | None, content_folders: Collection[str], reasons: list[str]
) -> None:
    """A reason when the docs project is a documentation repository's content
    folder (ADR-020 D8), in either direction."""
    if folder is not None and folder in content_folders:
        reasons.append(
            f"the docs project is {folder}/, a folder a documentation repository's "
            f"content lives in (ADR-020 D8): invariant's docs project takes "
            f"invariant-docs/, the same name in both homes. Move it to "
            f"invariant-docs/ and set docs.needs.srcdir and paths.docs, then migrate."
        )


def records_to_move(manifest: Any) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """``(records, kept, candidate roots)`` for a move into a store.

    Every data and policy record moves, and its path keys become
    store-relative with the same spelling, unless one of them is absolute or
    climbs out of the repository: such a record stays ``repo``, named with
    the key, since a store path must be store-relative (D1) and nothing of it
    lies in the repository to move. The docs record moves its docs project
    folder whole. ``hooks`` and ``agents`` become ``local`` (ADR-020 D1, D9).
    """
    from .manifest import RECORD_PATH_KEYS  # local: manifest imports this module lazily

    records: dict[str, str] = {}
    kept: dict[str, str] = {}
    roots: list[str] = []
    for record in PATH_RECORDS:
        found: list[str] = []
        outside = None
        for key in RECORD_PATH_KEYS.get(record, ()):
            if key in NOT_A_ROOT:
                continue
            for value in _values(manifest.get(key)):
                rel = _relative(value)
                if rel is None:
                    outside = f"{key} is {value}, outside the repository"
                    break
                found.append(rel)
            if outside:
                break
        if outside:
            records[record] = "repo"
            kept[record] = outside
            continue
        records[record] = "store"
        if record == "docs":
            folder = docs_project(manifest)
            if folder is not None:
                found.insert(0, folder)
        roots.extend(found)
    records.update(WIRING_RECORDS)
    return records, kept, roots


def _fold(roots: Sequence[str]) -> list[str]:
    """``roots`` once each, with a root under another folded into it."""
    out: list[str] = []
    for root in sorted(set(roots), key=lambda r: (len(PurePosixPath(r).parts), r)):
        if any(root == o or root.startswith(o + "/") for o in out):
            continue
        out.append(root)
    return sorted(out)


def _store_body(target: Path) -> dict[str, Any]:
    raw = yaml.safe_load((target / STORE_MANIFEST).read_text(encoding="utf-8")) or {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def _held_units(target: Path, rels: Sequence[str]) -> list[str]:
    """Each unit of ``target``'s docs lock a path in ``rels`` falls under that
    a session holds or queues for, named with the session."""
    from .doclock import DocsLock, read_config, unit_of  # local: stores imports doclock

    config = read_config(target)
    lock = DocsLock(SimpleNamespace(store=SimpleNamespace(root=target)), config=config)
    record = lock.read()
    units = sorted({unit_of(rel, config) for rel in rels})
    found: list[str] = []
    for unit in units:
        entry = record["units"].get(unit) or {}
        holder = entry.get("holder")
        session = (record["leases"].get(holder or "") or {}).get("session")
        if session:
            found.append(f"{unit} is held by session {session}")
        elif entry.get("queue"):
            found.append(f"{unit} has sessions queued for it")
    return found


def _placed(manifest: Any, cwd: Path, direction: str) -> Path:
    """The repository's top level, or a refusal: the checks that hold in
    either direction about where the command runs (ADR-020 D3). A move runs
    in a project's main checkout, at its top level, since it starts its own
    session and worktree."""
    cwd = Path(cwd).resolve()
    top = git.toplevel(cwd)
    if not getattr(manifest, "located", True) or top is None:
        raise MigrateRefused(
            direction,
            [
                (
                    f"there is no project at {cwd}: no .claude/invariant.yaml and no "
                    f"store link. Run it in the project's main checkout."
                )
            ],
        )
    top = top.resolve()
    if cwd != top or Path(manifest.root).resolve() != top:
        raise MigrateRefused(
            direction,
            [f"run it at the repository's top level, {top}; it was run below it, in {cwd}"],
        )
    main = git.main_checkout(cwd)
    if main is None or main.resolve() != top:
        raise MigrateRefused(
            direction,
            [
                (
                    f"run it from the main checkout{f', {main}' if main else ''}, not "
                    f"from a linked worktree: the move starts its own session and worktree"
                )
            ],
        )
    return top


def _branch(top: Path, reasons: list[str]) -> tuple[str, str]:
    """``(branch, HEAD)`` of the main checkout, or a reason."""
    base = git.current_branch(top)
    head = git.resolve_commit(top, "HEAD")
    if not base or head is None:
        reasons.append(f"{top} has no branch checked out (detached HEAD, or no commit)")
    return base or "", head or ""


def plan_to_store(
    manifest: Any,
    cwd: Path,
    *,
    store: Path | None = None,
    content_folders: Collection[str] = (),
    wiring: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
) -> ToStorePlan:
    """Decide ``store migrate --to store``, or refuse naming every reason.

    ``manifest`` is the project ``cwd`` resolves; ``content_folders`` the
    folders a documentation repository's content lives in, which the docs
    project may not be (ADR-020 D8); ``wiring`` the repository-relative files
    of the committed host wiring the repository leg will change. Nothing is
    written: a refusal leaves the repository, the user level and every store
    as they were.
    """
    direction = "store"
    top = _placed(manifest, cwd, direction)
    if manifest.store is not None or manifest.location.kind != "repo":
        name = manifest.store.name if manifest.store is not None else "(a pointer)"
        raise MigrateRefused(
            direction,
            [f"the configuration is already in store {name}: this is a store project"],
        )

    # local: manifest imports this module lazily
    from .manifest import manifest_tracking

    reasons: list[str] = []
    tracking = manifest_tracking(top)
    if tracking != "committed":
        reasons.append(
            f"{MANIFEST_REL} is not committed at HEAD ({tracking or 'unreadable'}): the "
            f"move takes the committed configuration. Commit it first."
        )
    elif git.changed_under(top, [MANIFEST_REL]):
        reasons.append(
            f"{MANIFEST_REL} has uncommitted changes: the move takes the committed "
            f"configuration. Commit or discard them first."
        )
    base, head = _branch(top, reasons)

    records, kept, candidates = records_to_move(manifest)
    if records.get("docs") == "store":
        _content_folder(docs_project(manifest), content_folders, reasons)
    roots = _fold(
        [r for r in candidates if git.files_under(top, [r]) or (top / r).is_file()]
    )
    moved = git.files_under(top, roots) or []
    if roots and not moved:
        reasons.append("git could not list the files the move would carry")
    dirty = git.changed_under(top, [*roots, *wiring])
    if dirty is None:
        reasons.append("git could not say whether the paths the move changes are clean")
    elif dirty:
        reasons.append(
            "these paths the move would change carry uncommitted changes: "
            f"{_listed(sorted(dirty))}. Commit or discard them first."
        )
    left = git.files_under(top, roots, kind="ignored") or []

    identity = repo_identity(top)
    local_id = None
    if identity is None:
        local_id = new_local_id(top.name)
        identity = Identity(key=local_id, origin=None, local_id=local_id)
    user = load_user_config(env)
    linked = user.repos.get(identity.key)

    target, new_store, note = _target(identity, top, store, user, env, reasons)
    if target is not None and linked is not None:
        path = Path(linked["store"]).expanduser()
        path = (path if path.is_absolute() else user.path.parent / path).resolve()
        if path != target:
            reasons.append(
                f"{identity.key} is already linked to {path} in "
                f"{display_path(user.path)}; a project has one store"
            )

    top_level: dict[str, Any] = {}
    if target is not None and not new_store:
        top_level = _check_existing(target, identity, moved, roots, reasons)

    raw = yaml.safe_load((top / MANIFEST_REL).read_text(encoding="utf-8")) or {}
    block = {k: v for k, v in dict(raw).items() if k != "version"}
    block["records"] = {**(block.get("records") or {}), **records}
    if target is not None and not reasons:
        if new_store:
            top_level = {"project": {"name": repo_name(identity, top)}}
        _check_loads(manifest, top, target, identity, top_level, block, env, reasons)

    if reasons or target is None:
        raise MigrateRefused(direction, reasons)
    remotes = git.remotes(top) or []
    return ToStorePlan(
        top=top,
        identity=identity,
        new_local_id=local_id,
        base=base,
        head=head,
        store=target,
        new_store=new_store,
        note=note,
        roots=roots,
        moved=sorted(moved),
        records=records,
        kept=kept,
        block=block,
        left=sorted(left),
        remote=git.config_value(top, f"remote.{remotes[0]}.url") if remotes else None,
    )


def _target(
    identity: Identity,
    top: Path,
    store: Path | None,
    user: Any,
    env: Mapping[str, str] | None,
    reasons: list[str],
) -> tuple[Path | None, bool, str | None]:
    """``(target, new, naming note)``; ``target`` ``None`` with a reason."""
    if store is not None:
        target = Path(store).expanduser().resolve()
        if (target / STORE_MANIFEST).is_file():
            return target, False, None
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            reasons.append(
                f"{target} is no store (it has no {STORE_MANIFEST}) and is not empty: "
                f"name a store, or an empty or absent folder for a new one"
            )
            return None, False, None
        return target, True, None
    try:
        target, note = plan_store_path(
            identity, top, user.store_naming, invariant_home(env) / STORES_DIR
        )
    except ManifestError as exc:
        reasons.append(
            f"{exc}. The store already lists this repository ({identity.key}), "
            f"which would make a second home"
            if "already exists" in str(exc)
            else str(exc)
        )
        return None, False, None
    return target.resolve(), True, note


def _check_existing(
    target: Path,
    identity: Identity,
    moved: Sequence[str],
    roots: Sequence[str],
    reasons: list[str],
) -> dict[str, Any]:
    """Refusals that read an existing store; returns its own level."""
    try:
        top_level, _repos = read_store_manifest(target)
    except ManifestError as exc:
        reasons.append(str(exc))
        return {}
    if lists(target, identity):
        reasons.append(
            f"the store {target} already lists this repository ({identity.key}) under "
            f"store.repos, which would make a second home: link it with "
            f"'invariant store link {target}' instead"
        )
    path = target / STORE_MANIFEST
    if holds_comments(path) and not holds_only_init_comment(path):
        reasons.append(
            f"{path} holds comments that writing this repository's block would drop: "
            f"add the block by hand, or move the comments out, then migrate"
        )
    present = sorted(
        set(git.files_under(target, list(roots)) or [])
        | set(git.files_under(target, list(roots), kind="untracked") or [])
        | {rel for rel in moved if (target / rel).exists()}
    )
    if present:
        reasons.append(
            f"already in store {target.name}, and store migrate never writes over a "
            f"file or merges a folder: {_listed(present)}"
        )
    if git.changed_under(target, [STORE_MANIFEST]):
        reasons.append(f"{path} has uncommitted changes: commit them in the store first")
    try:
        held = _held_units(target, list(moved))
    except (ManifestError, StateError, UsageError) as exc:
        held = [f"the store's docs lock cannot be read: {exc}"]
    if held:
        reasons.append(
            "another session holds the docs lock on a path the move would write: "
            + "; ".join(held)
            + ". Wait for its release (ADR-014 D5)."
        )
    from .storesync import settings_of, standing  # local: storesync imports stores

    try:
        sync = settings_of(top_level.get("sync") or {})
    except ManifestError as exc:
        reasons.append(str(exc))
        return top_level
    out = standing(target, sync)
    if out.stops:
        reasons.append(
            " ".join(out.lines())
            + ("" if "invariant store pull" in " ".join(out.lines()) else
               " Run 'invariant store pull' first.")
        )
    return top_level


def _check_loads(
    manifest: Any,
    top: Path,
    target: Path,
    identity: Identity,
    top_level: Mapping[str, Any],
    block: Mapping[str, Any],
    env: Mapping[str, str] | None,
    reasons: list[str],
) -> None:
    """The configuration resolves from the store as written, or a reason."""
    from .manifest import Manifest  # local: manifest imports this module lazily

    link = StoreLink(target, target.name, identity, identity.key, "user")
    user = load_user_config(env)
    below = [(user.label, user.defaults)] if user.defaults else []
    below += [(link.label, dict(top_level)), (link.repo_label, dict(block))]
    try:
        Manifest.from_raw(
            {}, root=top, path=target / STORE_MANIFEST, env=env, below=below,
            store=link, label="repo:.invariant.yaml",
        )
    except ManifestError as exc:
        reasons.append(f"the configuration would not load from the store: {exc}")


# -- the plan back to the repository (ADR-020 D3) --------------------------------


@dataclass
class ToRepoPlan:
    """Everything ``store migrate --to repo`` will write, decided up front."""

    top: Path
    store: Path
    #: The key the store lists this repository under.
    key: str
    base: str
    head: str
    #: The store's HEAD, whose history the repository leg carries.
    store_head: str
    #: Store-relative files and folders that move back, nested ones folded.
    roots: list[str]
    #: Every tracked file under ``roots`` in the store.
    moved: list[str]
    #: Each record's location in the store project, before the move.
    records: dict[str, str]
    #: ``.claude/invariant.yaml`` as it will be written.
    body: dict[str, Any]
    #: The dotted keys copied from the store's own level, not its block.
    from_store_level: list[str]
    #: The dotted keys left behind: a store project's alone (``STORE_ONLY``).
    dropped: list[str]
    #: The committed pointer the repository leg removes, if any.
    pointer: str | None = None
    #: Ignored files under ``roots`` in the store, left in its working tree.
    left: list[str] = field(default_factory=list)
    #: The store lists no other repository once this one leaves.
    serves_none: bool = False


def _leaves(mapping: Mapping[str, Any], prefix: str = "") -> list[str]:
    """Every leaf of ``mapping`` as a dotted key."""
    out: list[str] = []
    for key, value in mapping.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, Mapping) and value:
            out += _leaves(value, f"{dotted}.")
        else:
            out.append(dotted)
    return out


def _without_dotted(mapping: Mapping[str, Any], dotted: str) -> dict[str, Any]:
    """``mapping`` without ``dotted``, a parent left empty removed too."""
    head, _, rest = dotted.partition(".")
    out = dict(mapping)
    if head not in out:
        return out
    if not rest:
        del out[head]
    elif isinstance(out[head], Mapping):
        inner = _without_dotted(out[head], rest)
        if inner:
            out[head] = inner
        else:
            del out[head]
    return out


def materialise(manifest: Any) -> tuple[dict[str, Any], list[str], list[str]]:
    """``(body, from the store's level, dropped)``: the in-repo manifest that
    resolves what the store resolves now (ADR-020 D3).

    Every key the store's own level and this repository's block set, the
    block winning, less ``store`` and ``records`` (an in-repo manifest's
    records are all ``repo``, the load-time default) and the keys only a
    store project may set (:data:`STORE_ONLY`), which are named. The user
    level is the operator's and is not copied.
    """
    from .manifest import _overlaid  # local: manifest imports this module lazily

    link = manifest.store
    levels = dict(manifest.levels)
    top = {k: v for k, v in (levels.get(link.label) or {}).items() if k != "store"}
    block = dict(levels.get(link.repo_label) or {})
    merged = _overlaid(top, block)
    for key in ("version", "store", "records"):
        merged.pop(key, None)
    dropped: list[str] = []
    for dotted in STORE_ONLY:
        node: Any = merged
        for part in dotted.split("."):
            node = node.get(part) if isinstance(node, Mapping) else None
        if node is None:
            continue
        if isinstance(node, Mapping) and node:
            dropped += [f"{dotted}.{leaf}" for leaf in _leaves(node)]
        else:
            dropped.append(dotted)
        merged = _without_dotted(merged, dotted)
    theirs, own = set(_leaves(top)), set(_leaves(block))
    from_top = sorted(k for k in _leaves(merged) if k in theirs and k not in own)
    return {"version": 1, **merged}, from_top, sorted(dropped)


def records_to_repo(manifest: Any) -> tuple[dict[str, str], list[str]]:
    """``(records, candidate roots)`` for a move out of the store.

    Every record kept in the store moves back with its files, at the same
    relative path: its path keys, read against the store, and for the docs
    record its docs project's folder whole. A record already in the
    repository stays where it is.
    """
    from .manifest import RECORD_PATH_KEYS  # local: manifest imports this module lazily

    store_root = Path(manifest.store.root)
    records: dict[str, str] = {}
    roots: list[str] = []
    for record in PATH_RECORDS:
        where = manifest.get(f"records.{record}") or "repo"
        records[record] = where
        if where != "store":
            continue
        found: list[str] = []
        for key in RECORD_PATH_KEYS.get(record, ()):
            if key in NOT_A_ROOT:
                continue
            found += [
                rel for value in _values(manifest.get(key))
                if (rel := _under(value, store_root)) is not None
            ]
        if record == "docs":
            folder = docs_project(manifest, store_root)
            if folder is not None:
                found.insert(0, folder)
        roots.extend(found)
    return records, roots


def _shared(manifest: Any, store: Path, key: str, roots: Sequence[str],
            env: Mapping[str, str] | None) -> list[str]:
    """Each other repository of the store whose records name a path under
    one of ``roots``, or one above it, with the path: moving it out would
    take that repository's records too."""
    from .manifest import Manifest  # local: manifest imports this module lazily

    try:
        top_level, repos = read_store_manifest(store)
    except ManifestError:
        return []
    user = load_user_config(env)
    below = [(user.label, user.defaults)] if user.defaults else []
    found: list[str] = []
    for other, block in sorted(repos.items()):
        if other == key:
            continue
        link = StoreLink(store, store.name, None, other, "user")
        try:
            theirs = Manifest.from_raw(
                {}, root=Path(manifest.root), path=store / STORE_MANIFEST, env=env,
                below=[*below, (link.label, dict(top_level)), (link.repo_label, dict(block))],
                store=link, label="repo:.invariant.yaml",
            )
        except ManifestError:
            continue
        for rel in records_to_repo(theirs)[1]:
            for root in roots:
                if rel == root or rel.startswith(root + "/") or root.startswith(rel + "/"):
                    found.append(f"{rel} is also a record of {other}")
    return sorted(set(found))


def confs_above(top: Path, folder: str) -> list[tuple[Path, str, str]]:
    """Each ``conf.py`` at or above the docs project's folder, strictly
    above the folder itself, in the tree at ``top``: ``(path, the folder
    spelled from it, the conf.py spelled from top)``. A content ``conf.py``
    reads every folder below it (ADR-020 D8)."""
    parts = PurePosixPath(folder).parts
    found = []
    for depth in range(len(parts)):
        here = PurePosixPath(*parts[:depth]).as_posix() if depth else ""
        conf = top / here / "conf.py" if here else top / "conf.py"
        if conf.is_file():
            below = PurePosixPath(*parts[depth:]).as_posix()
            found.append((conf, below, f"{here}/conf.py" if here else "conf.py"))
    return found


def conf_readers(
    top: Path, folder: str, conf_excludes: Callable[[Path, str], bool | None] | None,
    reasons: list[str],
) -> None:
    """A reason for each ``conf.py`` at or above the docs project's folder
    that would read it (ADR-020 D8): a content ``conf.py`` reads every
    folder below it, so it must exclude invariant's docs project. The one
    reading of the rule: ``store migrate --to repo`` refuses on it, ``init``
    does not offer the repository home on it, and ``doctor``'s ``docs
    folder`` row names it."""
    for conf, below, spelled in confs_above(top, folder):
        said = conf_excludes(conf, below) if conf_excludes is not None else None
        if said:
            continue
        how = "does not exclude it" if said is False else "cannot be read to tell"
        reasons.append(
            f"{spelled} would read {folder}/, the docs project, and {how} (ADR-020 "
            f"D8): add '{below}' to its exclude_patterns, e.g. exclude_patterns = "
            f"[..., \"{below}\"]. invariant never edits it."
        )


def plan_to_repo(
    manifest: Any,
    cwd: Path,
    *,
    content_folders: Collection[str] = (),
    conf_excludes: Callable[[Path, str], bool | None] | None = None,
    wiring: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
) -> ToRepoPlan:
    """Decide ``store migrate --to repo``, or refuse naming every reason.

    ``manifest`` is the store project ``cwd`` resolves; ``content_folders``
    the folders a documentation repository's content lives in;
    ``conf_excludes`` answers whether a ``conf.py`` keeps a folder out of
    its build (the ``sphinx`` adapter's, handed in by the CLI); ``wiring`` the
    repository-relative files the committed host wiring will add. Nothing
    is written: a refusal leaves the repository, the store and the user
    level as they were.
    """
    direction = "repo"
    top = _placed(manifest, cwd, direction)
    if manifest.store is None:
        raise MigrateRefused(
            direction,
            [
                "the configuration is already in the repository: this is an in-repo "
                "project"
                if manifest.location.kind == "repo"
                else "this project names no store: its pointer alone defines it, so "
                "there is nothing to move back"
            ],
        )
    store = Path(manifest.store.root).resolve()
    key = manifest.store.key
    reasons: list[str] = []
    base, head = _branch(top, reasons)
    store_head = git.resolve_commit(store, "HEAD")
    if store_head is None:
        reasons.append(f"the store {store} has no commit to carry the history from")

    records, candidates = records_to_repo(manifest)
    folder = docs_project(manifest, store) if records.get("docs") == "store" else None
    _content_folder(folder, content_folders, reasons)
    if folder is not None:
        conf_readers(top, folder, conf_excludes, reasons)
    roots = _fold(
        [r for r in candidates if git.files_under(store, [r]) or (store / r).is_file()]
    )
    moved = git.files_under(store, roots) or []
    if roots and not moved:
        reasons.append("git could not list the files the move would carry")
    dirty = git.changed_under(store, [*roots, STORE_MANIFEST])
    if dirty is None:
        reasons.append("git could not say whether the store's paths the move changes are clean")
    elif dirty:
        reasons.append(
            f"these paths in store {store.name} carry uncommitted changes: "
            f"{_listed(sorted(dirty))}. Commit or discard them in the store first."
        )
    path = store / STORE_MANIFEST
    if holds_comments(path) and not holds_only_init_comment(path):
        reasons.append(
            f"{path} holds comments that removing this repository's block would drop: "
            f"remove the block by hand, or move the comments out, then migrate"
        )
    left = git.files_under(store, roots, kind="ignored") or []

    present = sorted(
        {rel for rel in (MANIFEST_REL, *moved) if (top / rel).exists()}
        | set(git.files_under(top, list(roots)) or [])
        | set(git.files_under(top, list(roots), kind="untracked") or [])
        | set(git.files_under(top, list(roots), kind="ignored") or [])
    )
    if present:
        reasons.append(
            f"already in the repository, and store migrate never writes over a file "
            f"or merges a folder: {_listed(present)}"
        )
    in_way = sorted(
        rel for rel in wiring
        if (top / rel).exists() and git.exists_at(top, "HEAD", rel) is not True
        and git.is_ignored(top, rel) is False
    )
    if in_way:
        reasons.append(
            f"these files the committed wiring adds are in the main checkout, neither "
            f"tracked nor ignored, so the merge would stop on them: {_listed(in_way)}. "
            f"Move them out first."
        )
    pointer = None
    if (top / POINTER_REL).is_file():
        if git.exists_at(top, "HEAD", POINTER_REL) is True:
            pointer = POINTER_REL
            if git.changed_under(top, [POINTER_REL]):
                reasons.append(
                    f"{POINTER_REL} has uncommitted changes; the move removes it. "
                    f"Commit or discard them first."
                )
        else:
            reasons.append(
                f"{POINTER_REL} is not committed; the in-repo manifest will be found "
                f"first, so remove it, or commit it for the move to remove"
            )

    shared = _shared(manifest, store, key, roots, env)
    if shared:
        reasons.append(
            "the store's records are shared with another repository, so moving them "
            "out would take theirs too: " + "; ".join(shared)
        )
    try:
        held = _held_units(store, list(moved))
    except (ManifestError, StateError, UsageError) as exc:
        held = [f"the store's docs lock cannot be read: {exc}"]
    if held:
        reasons.append(
            "another session holds the docs lock on a path the move would remove: "
            + "; ".join(held)
            + ". Wait for its release (ADR-014 D5)."
        )
    from .storesync import settings, standing  # local: storesync imports stores

    out = standing(store, settings(manifest))
    if out.stops:
        reasons.append(
            " ".join(out.lines())
            + ("" if "invariant store pull" in " ".join(out.lines()) else
               " Run 'invariant store pull' first.")
        )

    body, from_top, dropped = materialise(manifest)
    if not reasons:
        _check_loads_in_repo(top, body, env, reasons)
    if reasons:
        raise MigrateRefused(direction, reasons)
    try:
        _top_level, repos = read_store_manifest(store)
    except ManifestError:  # pragma: no cover - the manifest loaded it a moment ago
        repos = {}
    return ToRepoPlan(
        top=top,
        store=store,
        key=key,
        base=base,
        head=head,
        store_head=store_head or "",
        roots=roots,
        moved=sorted(moved),
        records=records,
        body=body,
        from_store_level=from_top,
        dropped=dropped,
        pointer=pointer,
        left=sorted(left),
        serves_none=not [k for k in repos if k != key],
    )


def _check_loads_in_repo(
    top: Path, body: Mapping[str, Any], env: Mapping[str, str] | None, reasons: list[str]
) -> None:
    """The materialised manifest loads in the repository, or a reason."""
    from .manifest import Manifest  # local: manifest imports this module lazily

    user = load_user_config(env)
    below = [(user.label, user.defaults)] if user.defaults else []
    try:
        Manifest.from_raw(dict(body), root=top, path=top / MANIFEST_REL, env=env, below=below)
    except ManifestError as exc:
        reasons.append(f"the configuration would not load from the repository: {exc}")


# -- the writes -------------------------------------------------------------------


def write_store(plan: ToStorePlan) -> str | None:
    """The store leg's configuration: this repository's block. A WRITE.

    A new store is created with its own level naming the project and the
    block, and committed at once, since a store with no commit gives the
    docs lock no sha to start a span at; the commit's sha is returned. In an
    existing store the block is written and left for :func:`commit_store`,
    after the history.
    """
    if plan.new_local_id is not None:
        write_local_id(plan.top, plan.new_local_id)
    if plan.new_store:
        plan.store.mkdir(parents=True, exist_ok=True)
        made, said = git.init_repository(plan.store)
        if not made:
            raise StateError(f"could not create a git repository at {plan.store}: {said}")
        body = {
            "version": 1,
            "project": {"name": repo_name(plan.identity, plan.top)},
            "store": {"repos": {plan.key: plan.block}},
        }
        _write_yaml(plan.store / STORE_MANIFEST, body)
        return commit_store(plan, f"invariant store migrate: the store for {plan.key}")
    body = _store_body(plan.store)
    store_block = dict(body.get("store") or {})
    repos = dict(store_block.get("repos") or {})
    repos[plan.key] = plan.block
    store_block["repos"] = repos
    body["store"] = store_block
    _write_yaml(plan.store / STORE_MANIFEST, body)
    return None


def commit_store(plan: ToStorePlan, message: str | None = None) -> str:
    """Commit the store's ``invariant.yaml``. A WRITE. Returns the sha."""
    message = message or f"invariant store migrate: {plan.key} keeps its records here"
    done, said = git.commit_only(plan.store, [STORE_MANIFEST], message)
    if not done:
        raise StateError(f"could not commit {STORE_MANIFEST} in {plan.store}: {said}")
    sha = git.resolve_commit(plan.store, "HEAD")
    if sha is None:  # pragma: no cover - a commit git made and cannot name
        raise StateError(f"the commit in {plan.store} cannot be read back")
    return sha


def remove_from_repo(plan: ToStorePlan, worktree: Path, changed: Sequence[str]) -> str:
    """The repository leg, in the session's worktree: the manifest and every
    moved root removed, with ``changed`` (the wiring the host took out)
    committed alongside. A WRITE on the session's branch. Returns the sha."""
    import shutil

    worktree = Path(worktree)
    paths = [MANIFEST_REL, *plan.roots]
    for rel in paths:
        path = worktree / rel
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    message = (
        f"invariant store migrate --to store: the configuration and records move "
        f"to store {plan.store.name}"
    )
    done, said = git.commit_only(worktree, sorted({*paths, *changed}), message)
    if not done:
        raise StateError(f"could not commit the repository leg in {worktree}: {said}")
    sha = git.resolve_commit(worktree, "HEAD")
    if sha is None:  # pragma: no cover - a commit git made and cannot name
        raise StateError(f"the commit in {worktree} cannot be read back")
    return sha


def _write_yaml(path: Path, body: Mapping[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(yaml.safe_dump(dict(body), sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)


def lay_repo_leg(plan: ToRepoPlan, worktree: Path) -> int:
    """The repository leg's files, in the session's worktree, not yet
    committed: the records' history carried from the store onto its branch
    (each original commit, the same relative paths), the materialised
    ``.claude/invariant.yaml`` written and the pointer removed. A WRITE.
    Returns the commits carried."""
    worktree = Path(worktree)
    commits = transfer(plan.store, plan.store_head, plan.roots, worktree)
    target = worktree / MANIFEST_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(target, plan.body)
    if plan.pointer is not None:
        (worktree / plan.pointer).unlink(missing_ok=True)
    return commits


def commit_repo_leg(plan: ToRepoPlan, worktree: Path, wiring: Sequence[str]) -> str:
    """Commit the repository leg on the session's branch: the manifest, the
    pointer's removal and ``wiring``, the committed host wiring, which the
    main checkout's ``.git/info/exclude`` still lists, so it is added with
    force. A WRITE. Returns the sha."""
    paths = sorted({MANIFEST_REL, *([plan.pointer] if plan.pointer else []), *wiring})
    message = (
        f"invariant store migrate --to repo: the configuration and records move "
        f"back from store {plan.store.name}"
    )
    done, said = git.commit_only(Path(worktree), paths, message, force=True)
    if not done:
        raise StateError(f"could not commit the repository leg in {worktree}: {said}")
    sha = git.resolve_commit(Path(worktree), "HEAD")
    if sha is None:  # pragma: no cover - a commit git made and cannot name
        raise StateError(f"the commit in {worktree} cannot be read back")
    return sha


def remove_from_store(plan: ToRepoPlan) -> str:
    """The store leg: this repository's block and its records removed from
    the store, its own level left as it is (ADR-020 D3). A WRITE in the
    store's main checkout. Only tracked files go; an ignored one stays,
    named by the plan. Returns the commit's sha."""
    store = plan.store
    for rel in plan.moved:
        (store / rel).unlink(missing_ok=True)
    for rel in sorted({str(PurePosixPath(m).parent) for m in plan.moved},
                      key=lambda d: d.count("/"), reverse=True):
        current = store / rel
        while current != store and current.is_dir() and not any(current.iterdir()):
            current.rmdir()
            current = current.parent
    body = _store_body(store)
    store_block = dict(body.get("store") or {})
    repos = dict(store_block.get("repos") or {})
    repos.pop(plan.key, None)
    store_block["repos"] = repos
    body["store"] = store_block
    _write_yaml(store / STORE_MANIFEST, body)
    message = f"invariant store migrate --to repo: {plan.key} takes its records back"
    done, said = git.commit_only(store, [STORE_MANIFEST, *plan.moved], message)
    if not done:
        raise StateError(f"could not commit the store leg in {store}: {said}")
    sha = git.resolve_commit(store, "HEAD")
    if sha is None:  # pragma: no cover - a commit git made and cannot name
        raise StateError(f"the commit in {store} cannot be read back")
    return sha

