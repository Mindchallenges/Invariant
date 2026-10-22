"""Where the oracle keeps its user-level state.

Everything else this library stores lives under a project's state root. The
oracle answers a question no single project can — every session on the
machine — so it keeps one directory per USER, outside every project. That is
the first state of its kind here, and the reasons it is shaped as it is are
recorded in ADR-007.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

#: Overrides the home directory. The test suite sets it for every test so that
#: nothing reaches the real one; an operator can set it to keep the oracle's
#: state somewhere else.
HOME_ENV = "INVARIANT_HOME"

#: The directory name under the user's home when nothing overrides it.
DEFAULT_DIRNAME = ".invariant"


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
