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
from collections.abc import Collection, Mapping, Sequence
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

#: Path keys that are no root of their own: ``paths.memory_index`` is a file
#: name inside ``paths.memory`` (``domains/memorystore``).
NOT_A_ROOT = ("paths.memory_index",)

#: The in-repo manifest, as git spells it.
MANIFEST_REL = ".claude/invariant.yaml"

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
    return (
        f"store migrate --to {marker.get('to', '?')} is moving this project, in "
        f"session {session}: neither home is complete until its repository leg "
        f"lands. Finish it with 'invariant session merge --id {session}' or "
        f"'invariant session resume --id {session}', run from its worktree "
        f"(ADR-014 D6, ADR-020 D3)."
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


def docs_project(manifest: Any) -> str | None:
    """The docs project's folder, repository-relative: the resolved
    ``docs.needs.srcdir``, else the folder above ``paths.docs``; ``None``
    where ``paths.docs`` is at the root or outside the repository."""
    from .needs import srcdir_of  # local: needs imports manifest

    root = Path(manifest.root)
    srcdir = srcdir_of(manifest)
    if srcdir is not None:
        try:
            rel = Path(srcdir).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = None
        if rel not in (None, "", "."):
            return rel
    docs = _relative(manifest.get("paths.docs") or "")
    if docs is None:
        return None
    parent = PurePosixPath(docs).parent.as_posix()
    return None if parent in ("", ".") else parent


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
    cwd = Path(cwd).resolve()
    top = git.toplevel(cwd)
    if not getattr(manifest, "located", True) or top is None:
        raise MigrateRefused(
            direction,
            [
                (
                    f"there is no project at {cwd}: no .claude/invariant.yaml and no "
                    f"store link. Run it in an in-repo project's main checkout."
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
    base = git.current_branch(top)
    head = git.resolve_commit(top, "HEAD")
    if not base or head is None:
        reasons.append(f"{top} has no branch checked out (detached HEAD, or no commit)")

    records, kept, candidates = records_to_move(manifest)
    if records.get("docs") == "store":
        folder = docs_project(manifest)
        if folder is not None and folder in content_folders:
            reasons.append(
                f"the docs project is {folder}/, a folder a documentation repository's "
                f"content lives in (ADR-020 D8): invariant's docs project takes "
                f"invariant-docs/, the same name in both homes. Move it to "
                f"invariant-docs/ and set docs.needs.srcdir and paths.docs, then migrate."
            )
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
        base=base or "",
        head=head or "",
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
