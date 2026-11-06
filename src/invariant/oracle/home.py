"""Where the oracle keeps its user-level state.

Everything else this library stores lives under a project's state root. The
oracle answers a question no single project can — every session on the
machine — so it keeps one directory per USER, outside every project. That is
the first state of its kind here, and the reasons it is shaped as it is are
recorded in ADR-007.

Some of what lives there is private to its owner: the paired devices' token
hashes, the log of what the web page ran and its certificate's private key
(ADR-008). Those are created owner-only, through :func:`private_dir`,
:func:`append_private` and :func:`write_private`, rather
than created with the process default and narrowed afterwards, which would
leave them readable for the moment in between.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from ..core.errors import StateError
from ..core.home import HOME_ENV, invariant_home

# HOME_ENV is named here too: the CLI's oracle commands report it by name.
__all__ = [
    "HOME_ENV",
    "PRIVATE_DIR_MODE",
    "PRIVATE_FILE_MODE",
    "append_private",
    "oracle_home",
    "private_dir",
    "write_private",
]

#: A private directory: its owner alone may list or enter it.
PRIVATE_DIR_MODE = 0o700
#: A private file: its owner alone may read or write it.
PRIVATE_FILE_MODE = 0o600


def oracle_home(env: Mapping[str, str] | None = None) -> Path:
    """``$INVARIANT_HOME`` when set and non-empty, else ``~/.invariant``.

    The one resolution is ``core.home.invariant_home``, which the manifest
    also reads for the user level of the config (ADR-014 D3); the oracle's
    home and the user config's are therefore the same directory by
    construction.
    """
    return invariant_home(env)


def private_dir(path: Path) -> Path:
    """``path`` as a directory only its owner can use, created that way.

    One made wider by an older build is narrowed before anything is written
    into it.
    """
    try:
        path.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
        if path.stat().st_mode & 0o077:
            os.chmod(path, PRIVATE_DIR_MODE)
    except OSError as exc:
        raise StateError(f"cannot make {path} private to its owner: {exc}") from exc
    return path


def write_private(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` atomically, in a file created owner-only.

    The bytes go into a temporary file beside it, created ``0600`` and
    exclusively (a stale one left by a crash is removed first, never reused),
    which is then renamed over ``path``. A reader sees the old file or the new
    one, and nobody else can read either.
    """
    private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, flags, PRIVATE_FILE_MODE)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise StateError(f"cannot write {path}: {exc}") from exc


def append_private(path: Path, text: str) -> None:
    """Append ``text`` to ``path``, a file created owner-only if it is new.

    A symbolic link is refused rather than followed, so the line cannot be
    sent to a file somebody else chose.
    """
    private_dir(path.parent)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, PRIVATE_FILE_MODE)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as exc:
        raise StateError(f"cannot append to {path}: {exc}") from exc
