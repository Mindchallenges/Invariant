"""The user's home for invariant, and the user level of the config (ADR-014 D3).

One directory lives outside every project, per user: ``~/.invariant``, which
``$INVARIANT_HOME`` moves (ADR-007). The oracle keeps its index there, and
the user level of the configuration is its ``config.yaml``:

.. code-block:: yaml

    version: 1
    store_naming: repo          # repo | url (ADR-014 ruling 14)
    defaults: {...}             # level 5a: below every project's own settings
    repos:                      # repo identity -> the store it uses (D2)
      <identity>: {store: <path>}

The user level holds no project-specific keys except ``repos:`` (D3), so a
project's policy can never be set by one operator's home directory alone:
``defaults`` may not carry a ``project`` or ``records`` block, and an entry
under ``repos`` names a store and nothing else. Every malformed shape is
refused naming the file, because a value that loads and does nothing is the
silence this library exists to remove (BUG_050).

This module lives in core, not in the oracle, because the manifest reads it
and nothing in core may import the oracle; ``oracle.home`` asks it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import ManifestError

#: Overrides the home directory. The test suite sets it for every test so
#: that nothing reaches the real one.
HOME_ENV = "INVARIANT_HOME"

#: The directory name under the user's home when nothing overrides it.
DEFAULT_DIRNAME = ".invariant"

#: The user level of the config, inside the home.
USER_CONFIG = "config.yaml"

#: The ways a store's folder may be named (ADR-014 ruling 14).
STORE_NAMINGS = ("repo", "url")

#: The keys the user level may hold.
_TOP_KEYS = ("version", "store_naming", "defaults", "repos")

#: Blocks no user default may set: they say what one project IS, and where
#: its records live, which only that project's own levels may say.
_PROJECT_SPECIFIC = ("project", "records")

#: The keys an entry under ``repos`` may hold.
_REPO_ENTRY_KEYS = ("store",)


def invariant_home(env: Mapping[str, str] | None = None) -> Path:
    """``$INVARIANT_HOME`` when set and non-empty, else ``~/.invariant``.

    Resolved on every call rather than cached, so a process that changes the
    variable, a test chiefly, is answered with the value it set.
    """
    env = os.environ if env is None else env
    configured = env.get(HOME_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / DEFAULT_DIRNAME


def user_config_path(env: Mapping[str, str] | None = None) -> Path:
    """Where the user level of the config is read from."""
    return invariant_home(env) / USER_CONFIG


def display_path(path: Path) -> str:
    """``path`` for reading: under the user's home directory, spelled ``~/...``.

    Used for provenance labels, so ``config show`` prints the same label on
    every machine for the default location.
    """
    try:
        return "~/" + Path(path).relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


@dataclass(frozen=True)
class UserConfig:
    """The user level of the config: level 5a and the repo-to-store map."""

    path: Path
    defaults: dict[str, Any] = field(default_factory=dict)
    repos: dict[str, dict[str, Any]] = field(default_factory=dict)
    store_naming: str = "repo"

    @property
    def label(self) -> str:
        """The provenance label of every value this level supplies."""
        return f"user:{display_path(self.path)}"


def load_user_config(env: Mapping[str, str] | None = None) -> UserConfig:
    """Read and check the user level. A missing file is an empty level."""
    path = user_config_path(env)
    where = display_path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        # Absent, or under a home that is no directory, so it cannot exist.
        # Either way there is no user level, and an unusable home must not
        # change what any project resolves.
        return UserConfig(path=path)
    except OSError as exc:
        raise ManifestError(f"the user config {where} cannot be read: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"the user config {where} is not valid YAML: {exc}") from exc
    if raw is None:
        return UserConfig(path=path)
    if not isinstance(raw, Mapping):
        raise ManifestError(f"the user config {where} must contain a mapping")
    return UserConfig(
        path=path,
        defaults=_defaults(raw, where),
        repos=_repos(raw, where),
        store_naming=_store_naming(raw, where),
    )


def _defaults(raw: Mapping[str, Any], where: str) -> dict[str, Any]:
    for key in raw:
        if key not in _TOP_KEYS:
            raise ManifestError(
                f"the user config {where} has {key!r}, which is not one of "
                f"{', '.join(_TOP_KEYS)}. The user level holds defaults for every "
                f"project and the repo-to-store map, nothing project-specific."
            )
    version = raw.get("version", 1)
    if version != 1:
        raise ManifestError(
            f"the user config {where} is version {version}; this release "
            f"understands version 1"
        )
    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, Mapping):
        raise ManifestError(f"the user config {where}: defaults must be a mapping")
    for key in _PROJECT_SPECIFIC:
        if key in defaults:
            raise ManifestError(
                f"the user config {where} sets defaults.{key}, which is "
                f"project-specific: it belongs in the project's own level "
                f"(its store or its repo), never in one operator's home."
            )
    return dict(defaults)


def _repos(raw: Mapping[str, Any], where: str) -> dict[str, dict[str, Any]]:
    repos = raw.get("repos") or {}
    if not isinstance(repos, Mapping):
        raise ManifestError(
            f"the user config {where}: repos must be a mapping of repo identity "
            f"to {{store: <path>}}"
        )
    found: dict[str, dict[str, Any]] = {}
    for identity, entry in repos.items():
        if not isinstance(identity, str) or not identity.strip():
            raise ManifestError(
                f"the user config {where}: every key under repos must be an "
                f"identity, got {identity!r}"
            )
        if not isinstance(entry, Mapping):
            raise ManifestError(
                f"the user config {where}: repos.{identity} must be a mapping, "
                f"got {entry!r}"
            )
        for key in entry:
            if key not in _REPO_ENTRY_KEYS:
                raise ManifestError(
                    f"the user config {where}: repos.{identity} has {key!r}; an "
                    f"entry names its store and nothing else, so a project's "
                    f"policy is never set from one operator's home."
                )
        store = entry.get("store")
        if not isinstance(store, str) or not store.strip():
            raise ManifestError(
                f"the user config {where}: repos.{identity}.store must be a path"
            )
        found[identity] = dict(entry)
    return found


def _store_naming(raw: Mapping[str, Any], where: str) -> str:
    naming = raw.get("store_naming", "repo")
    if naming not in STORE_NAMINGS:
        raise ManifestError(
            f"the user config {where}: store_naming is {naming!r}; use one of "
            f"{', '.join(STORE_NAMINGS)}"
        )
    return str(naming)
