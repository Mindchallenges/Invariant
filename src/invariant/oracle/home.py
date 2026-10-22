"""Where the oracle keeps its user-level state.

Everything else this library stores lives under a project's state root. The
oracle answers a question no single project can — every session on the
machine — so it keeps one directory per USER, outside every project. That is
the first state of its kind here, and the reasons it is shaped as it is are
recorded in ADR-007.

Some of what lives there is private to its owner: the paired devices' token
hashes and the log of what the web page ran (ADR-008). Those are created
owner-only, through :func:`private_dir` and :func:`append_private`, rather
than created with the process default and narrowed afterwards, which would
leave them readable for the moment in between.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from ..core.errors import StateError

#: Overrides the home directory. The test suite sets it for every test so that
#: nothing reaches the real one; an operator can set it to keep the oracle's
#: state somewhere else.
HOME_ENV = "INVARIANT_HOME"

#: The directory name under the user's home when nothing overrides it.
DEFAULT_DIRNAME = ".invariant"

#: A private directory: its owner alone may list or enter it.
PRIVATE_DIR_MODE = 0o700
#: A private file: its owner alone may read or write it.
PRIVATE_FILE_MODE = 0o600


def oracle_home(env: Mapping[str, str] | None = None) -> Path:
    """``$INVARIANT_HOME`` when set and non-empty, else ``~/.invariant``.

    Resolved on every call rather than cached, so a process that changes the
    variable — a test, chiefly — is answered with the value it set.
    """
    env = os.environ if env is None else env
    configured = env.get(HOME_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / DEFAULT_DIRNAME


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
