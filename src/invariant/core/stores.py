"""Stores: where a project's records live outside its repository (ADR-014 D2).

A store is a git repository at any path, ``~/.invariant/stores/<name>`` by
default. Its root holds ``invariant.yaml``, the store level of the config
(D3, 5b), which declares the repos it serves under ``store.repos``. A repo
names its store through its pointer, ``.invariant.yaml`` at the repo root, or
through the user level's ``repos:`` map, keyed on the repo's identity (D4);
the first that answers wins. A store must list the repo's identity, or it is
refused naming both, so a wrong link cannot make one project's gates read
another project's docs.

This module reads stores and pointers and writes the user-level link. It
knows nothing of hosts (ruling 12) and does not resolve a configuration:
``Manifest.locate`` asks it where the levels are and merges them.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import git
from .errors import ManifestError, StateError
from .home import (
    UserConfig,
    display_path,
    invariant_home,
    load_user_config,
    user_config_path,
)
from .identity import Identity, new_local_id, repo_identity, write_local_id

#: The store level of the config, at the store's root.
STORE_MANIFEST = "invariant.yaml"

#: The repo pointer, at the repo's root (D3, 5c).
POINTER = ".invariant.yaml"

#: The plain-text index of linked identities, beside the user config. A
#: user-level hook shim (D11) reads it to tell a linked repo from any other
#: without starting Python.
LINKED_INDEX = "linked-repos.txt"

#: Where stores are created by default, inside the home.
STORES_DIR = "stores"

#: The keys a pointer may hold. Policy lives in the store, reviewed there.
POINTER_KEYS = ("version", "store", "records")

#: The ``store`` keys this build reads. ``docs_lock`` arrives with D5
#: (increment 4); ``publish`` was dropped at acceptance.
STORE_KEYS = ("repos",)

#: An scp-like remote, ``user@host:path``.
_SCP_REMOTE = re.compile(r"^[\w.-]+@[\w.-]+:")


@dataclass(frozen=True)
class StoreLink:
    """A repo's store, as the lookup found it."""

    #: The store's root, its main checkout.
    root: Path
    #: The store's folder name, shown in provenance labels for reading only:
    #: a store is matched by identity, never by its name (ruling 14).
    name: str
    #: The identity of the repo that named it.
    identity: Identity | None
    #: The key the store lists the repo under: the identity's key, or its
    #: local ID when the link predates the origin.
    key: str
    #: ``pointer`` or ``user``: which level named the store.
    via: str
    #: The repo has an origin the link is not keyed on: it was linked by its
    #: local ID before a remote was added (D4). ``doctor`` says so.
    mismatch: bool = False

    @property
    def label(self) -> str:
        """The provenance label of the store's own level."""
        return f"store:{self.name}"

    @property
    def repo_label(self) -> str:
        """The provenance label of the store's block for this repo."""
        return f"store:{self.name}#{self.key}"


@dataclass(frozen=True)
class Pointer:
    """A repo's pointer: which store, and where its records live."""

    path: Path
    store: str | None
    #: The pointer's level of the config: its ``records`` block, if any.
    level: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoreLevels:
    """What a store contributes to a configuration (D3, 5b)."""

    link: StoreLink
    #: The store's own level, without its ``store`` block.
    top: dict[str, Any]
    #: ``store.repos.<key>``: the store's settings for this repo alone.
    repo: dict[str, Any]


def _read_yaml(path: Path, what: str) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ManifestError(f"{what} {path} is not valid YAML: {exc}") from exc
    except OSError as exc:
        raise ManifestError(f"{what} {path} cannot be read: {exc}") from exc


def is_remote(value: str) -> bool:
    """Does ``value`` name a remote rather than a path on this machine?"""
    return "://" in value or bool(_SCP_REMOTE.match(value))


def read_pointer(top: Path) -> Pointer | None:
    """The pointer at ``top``, or ``None``. A pointer setting policy is refused."""
    path = Path(top) / POINTER
    if not path.is_file():
        return None
    raw = _read_yaml(path, "the pointer")
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ManifestError(f"the pointer {path} must contain a mapping")
    for key in raw:
        if key not in POINTER_KEYS:
            raise ManifestError(
                f"the pointer {path} sets {key!r}. A pointer holds only "
                f"{', '.join(POINTER_KEYS)} (ADR-014 D3): a store project's policy "
                f"lives in its store, where it is reviewed."
            )
    if raw.get("version", 1) != 1:
        raise ManifestError(
            f"the pointer {path} is version {raw['version']}; this release "
            f"understands version 1"
        )
    store = raw.get("store")
    if store is not None and not (isinstance(store, str) and store.strip()):
        raise ManifestError(f"the pointer {path}: store must be a path, got {store!r}")
    level = {"records": raw["records"]} if "records" in raw else {}
    return Pointer(path=path, store=store, level=level)


def find_store(
    top: Path, identity: Identity | None, pointer: Pointer | None, user: UserConfig
) -> StoreLink | None:
    """The store the repo at ``top`` names, or ``None`` when it names none.

    The pointer's ``store`` first, then the user level's entry for the
    identity, then, when the repo has an origin the user level does not
    know, its entry for the local ID (D4: a remote added after the link).
    A store that is named and absent is refused: cloning one is explicit and
    is ADR-014 increment 5.
    """
    if pointer is not None and pointer.store is not None:
        value = pointer.store.strip()
        if is_remote(value):
            raise ManifestError(
                f"the pointer {pointer.path} names the store {value}, a remote. "
                f"Cloning a store is explicit and not built yet (ADR-014 D7, "
                f"increment 5): clone it, then point at the local path."
            )
        if identity is None:
            raise ManifestError(
                f"the pointer {pointer.path} names a store, but the repository "
                f"at {top} has no identity: no origin and no invariant.id. Run "
                f"'invariant store link {value}' to write one."
            )
        path = Path(value).expanduser()
        root = path if path.is_absolute() else Path(top) / path
        return _existing(root, f"the pointer {pointer.path}", identity, identity.key, "pointer")
    if identity is None:
        return None
    key, mismatch = identity.key, False
    entry = user.repos.get(key)
    if entry is None and identity.local_id and identity.local_id != key:
        key, mismatch = identity.local_id, True
        entry = user.repos.get(key)
    if entry is None:
        return None
    path = Path(entry["store"]).expanduser()
    root = path if path.is_absolute() else user.path.parent / path
    source = f"the user config {display_path(user.path)} (repos.{key})"
    return _existing(root, source, identity, key, "user", mismatch)


def _existing(
    root: Path, source: str, identity: Identity, key: str, via: str, mismatch: bool = False
) -> StoreLink:
    root = root.resolve()
    if not root.is_dir():
        raise ManifestError(
            f"the store {root}, named by {source}, does not exist on this machine. "
            f"Cloning a store is explicit and not built yet (ADR-014 increment 5)."
        )
    if not (root / STORE_MANIFEST).is_file():
        raise ManifestError(
            f"{root}, named by {source}, has no {STORE_MANIFEST}, so it is no store"
        )
    return StoreLink(root=root, name=root.name, identity=identity, key=key, via=via,
                     mismatch=mismatch)


def read_store_manifest(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """A store's own level and its ``store.repos`` map, each checked."""
    path = Path(root) / STORE_MANIFEST
    raw = _read_yaml(path, "the store config")
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ManifestError(f"the store config {path} must contain a mapping")
    if "store_naming" in raw:
        raise ManifestError(
            f"the store config {path} sets store_naming. A store's folder is named "
            f"when 'invariant store init' creates it, before any store level "
            f"exists, so only the user level's store_naming is read."
        )
    block = raw.get("store") or {}
    if not isinstance(block, Mapping):
        raise ManifestError(f"the store config {path}: store must be a mapping")
    for key in block:
        if key not in STORE_KEYS:
            raise ManifestError(
                f"the store config {path} sets store.{key}, which this build does "
                f"not read; it reads {', '.join(STORE_KEYS)}"
            )
    repos = block.get("repos") or {}
    if not isinstance(repos, Mapping):
        raise ManifestError(
            f"the store config {path}: store.repos must be a mapping of repo "
            f"identity to its own settings"
        )
    checked: dict[str, dict[str, Any]] = {}
    for identity, settings in repos.items():
        settings = settings or {}
        if not isinstance(settings, Mapping):
            raise ManifestError(
                f"the store config {path}: store.repos.{identity} must be a mapping"
            )
        checked[str(identity)] = dict(settings)
    top = {key: value for key, value in raw.items() if key != "store"}
    return top, checked


def store_levels(link: StoreLink) -> StoreLevels:
    """The levels ``link``'s store contributes, refused unless it lists the repo."""
    top, repos = read_store_manifest(link.root)
    key = link.key
    if key not in repos and link.via == "pointer" and link.identity is not None:
        local = link.identity.local_id
        if local and local in repos:
            key = local
    if key not in repos:
        raise ManifestError(
            f"the store {link.root} does not list this repository ({link.key}) "
            f"under store.repos, so it is not read for it: a wrong link must not "
            f"make one project's gates read another's records. If this store "
            f"serves it, add '{link.key}: {{}}' under store.repos in "
            f"{link.root / STORE_MANIFEST}."
        )
    if key != link.key:
        link = StoreLink(link.root, link.name, link.identity, key, link.via, link.mismatch)
    return StoreLevels(link=link, top=top, repo=repos[key])


# -- naming (ruling 14) ---------------------------------------------------------


def repo_name(identity: Identity, top: Path) -> str:
    """The repo form: the origin's last segment, or the directory name."""
    if identity.origin:
        return identity.origin.rsplit("/", 1)[-1]
    return Path(top).name


def url_name(identity: Identity) -> str:
    """The URL form: the origin with slashes as underscores, else the local ID."""
    if identity.origin:
        return identity.origin.replace("/", "_")
    return (identity.local_id or identity.key).replace(":", "_")


def lists(root: Path, identity: Identity) -> bool:
    """Is ``root`` a store that already lists ``identity``?"""
    try:
        _top, repos = read_store_manifest(root)
    except ManifestError:
        return False
    return identity.key in repos or (identity.local_id or "") in repos


def plan_store_path(
    identity: Identity, top: Path, naming: str, parent: Path
) -> tuple[Path, str | None]:
    """Where ``store init`` creates the store, and what it must report.

    Under ``repo``, a name taken by another store, or by anything else, falls
    back to the URL form (the local ID with no remote) and the second string
    says so, naming both. A store that already lists this repo is refused:
    it wants linking, not creating.
    """
    names = [repo_name(identity, top), url_name(identity)] if naming == "repo" else [url_name(identity)]
    taken: list[Path] = []
    for name in names:
        candidate = parent / name
        if not candidate.exists():
            note = (
                f"{taken[0]} already exists for another repository, so this store "
                f"is named {name} instead (ruling 14)"
                if taken
                else None
            )
            return candidate, note
        if lists(candidate, identity):
            raise ManifestError(
                f"a store for this repository already exists at {candidate}; run "
                f"'invariant store link {candidate}' to use it"
            )
        taken.append(candidate)
    raise ManifestError(
        "no store name is free: " + ", ".join(str(p) for p in taken) + " already "
        "exist for other repositories. Pass a path: 'invariant store init <path>'."
    )


# -- writes ---------------------------------------------------------------------


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def ensure_local_id(top: Path) -> Identity:
    """The repo's identity, writing ``invariant.id`` first when it has none.

    Written even when a remote exists (D4), so a remote added or renamed
    later is still recognised.
    """
    identity = repo_identity(top)
    if identity is None or identity.local_id is None:
        write_local_id(top, new_local_id(Path(top).name))
        identity = repo_identity(top)
    if identity is None:  # pragma: no cover - a write git accepted and did not keep
        raise StateError(f"invariant.id written at {top} does not read back")
    return identity


def _user_raw(env: Mapping[str, str] | None) -> dict[str, Any]:
    load_user_config(env)  # refuses a malformed file before it is rewritten
    path = user_config_path(env)
    if not path.is_file():
        return {"version": 1}
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {"version": 1})


def _write_user(raw: Mapping[str, Any], env: Mapping[str, str] | None) -> None:
    _write_atomic(user_config_path(env), yaml.safe_dump(dict(raw), sort_keys=False))
    load_user_config(env)


def linked_index_path(env: Mapping[str, str] | None = None) -> Path:
    return invariant_home(env) / LINKED_INDEX


def _add_to_index(names: list[str], env: Mapping[str, str] | None) -> None:
    path = linked_index_path(env)
    known: set[str] = set()
    if path.is_file():
        known = {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}
    known |= {name for name in names if name}
    known.discard("")
    _write_atomic(path, "".join(f"{name}\n" for name in sorted(known)))


@dataclass(frozen=True)
class Linked:
    identity: Identity
    store: Path
    key: str
    added: bool


def link_repo(top: Path, store: Path, env: Mapping[str, str] | None = None) -> Linked:
    """Link the repo at ``top`` to the store at ``store`` from the user level.

    Writes ``invariant.id`` if missing, the user level's ``repos`` entry and
    the linked-identity index. Refused, writing nothing to the user level,
    when the store does not list the repo or the repo is linked elsewhere.
    """
    root = Path(store).expanduser().resolve()
    if not (root / STORE_MANIFEST).is_file():
        raise ManifestError(f"{root} has no {STORE_MANIFEST}, so it is no store")
    identity = ensure_local_id(top)
    link = StoreLink(root, root.name, identity, identity.key, "user")
    store_levels(link)
    user = load_user_config(env)
    current = user.repos.get(identity.key)
    if current is not None:
        linked = Path(current["store"]).expanduser()
        linked = (linked if linked.is_absolute() else user.path.parent / linked).resolve()
        if linked != root:
            raise ManifestError(
                f"{identity.key} is already linked to {linked} in "
                f"{display_path(user.path)}; edit that entry to move it"
            )
    raw = _user_raw(env)
    repos = dict(raw.get("repos") or {})
    repos[identity.key] = {"store": display_path(root)}
    raw["repos"] = repos
    _write_user(raw, env)
    _add_to_index([identity.key, identity.local_id or ""], env)
    return Linked(identity=identity, store=root, key=identity.key, added=current is None)


def update_link(top: Path, env: Mapping[str, str] | None = None) -> Linked:
    """Re-key a link made by local ID onto the repo's origin (D4).

    The store must already list the origin: moving the key is the operator's
    explicit act, and the store's ``store.repos`` is reviewed in the store.
    """
    identity = repo_identity(top)
    user = load_user_config(env)
    if (
        identity is None
        or identity.origin is None
        or identity.origin in user.repos
        or not identity.local_id
        or identity.local_id not in user.repos
    ):
        raise ManifestError(
            "nothing to update: the link is already keyed on this repository's "
            "origin, or it has no origin, or it was never linked by its local ID"
        )
    entry = user.repos[identity.local_id]
    path = Path(entry["store"]).expanduser()
    root = (path if path.is_absolute() else user.path.parent / path).resolve()
    store_levels(StoreLink(root, root.name, identity, identity.origin, "user"))
    raw = _user_raw(env)
    repos = dict(raw.get("repos") or {})
    repos[identity.origin] = repos.pop(identity.local_id)
    raw["repos"] = repos
    _write_user(raw, env)
    _add_to_index([identity.origin], env)
    return Linked(identity=identity, store=root, key=identity.origin, added=False)


@dataclass(frozen=True)
class Created:
    store: Path
    note: str | None
    linked: Linked


def init_store(
    top: Path, path: Path | None = None, env: Mapping[str, str] | None = None
) -> Created:
    """Create a store for the repo at ``top`` and link it.

    The store is a new git repository holding ``invariant.yaml``, which names
    the project and lists the repo under ``store.repos``. It is left
    uncommitted, for the operator to review and commit in the store. The repo
    itself gains no tracked file.
    """
    identity = ensure_local_id(top)
    user = load_user_config(env)
    note = None
    if path is None:
        target, note = plan_store_path(
            identity, top, user.store_naming, invariant_home(env) / STORES_DIR
        )
    else:
        target = Path(path).expanduser().resolve()
        if target.exists() and any(target.iterdir()):
            if lists(target, identity):
                raise ManifestError(
                    f"a store for this repository already exists at {target}; run "
                    f"'invariant store link {target}' to use it"
                )
            raise ManifestError(f"{target} already exists and is not empty")
    target.mkdir(parents=True, exist_ok=True)
    made, said = git.init_repository(target)
    if not made:
        raise StateError(f"could not create a git repository at {target}: {said}")
    body = {
        "version": 1,
        "project": {"name": repo_name(identity, top)},
        "store": {"repos": {identity.key: {}}},
    }
    (target / STORE_MANIFEST).write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return Created(store=target, note=note, linked=link_repo(top, target, env))
