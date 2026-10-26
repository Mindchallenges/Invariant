"""Starting a new Claude window in a registered project (IDEA_030, ADR-009 D16).

What ``invariant oracle start-session`` decides before it launches, kept apart
from the launch itself so the panel's confirm sheet (increment 2) can show the
same facts and the same name from the same functions:

* **the name**: the project and a label, slugged, with ``-2``, ``-3`` added
  until no live Claude window holds it (:func:`session_name`);
* **the facts the confirm shows**: how many Claude windows are open in the
  project and whether its main checkout has uncommitted changes
  (:func:`start_facts`). They inform; neither blocks a start.

Everything here is pure but :func:`checkout_changes`, which asks git one
read-only question with optional locks off, so it never takes ``index.lock``
under a session working in that checkout (ADR-007 D16). The oracle imports
nothing from ``hosts/``: which names are in use and how a window is opened are
Claude Code's, and the CLI joins them.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..core import git
from ..domains.session import handoff_name
from .scan import ROW_HOST, ROW_SESSION

#: The default label: the month, day, hour and minute the start was asked
#: for, in local time, so ``heap-1026-0930`` reads as "this morning's".
LABEL_FORMAT = "%m%d-%H%M"


def default_label(now: float) -> str:
    """The label a start gets when none is given: ``MMDD-HHMM``, local time."""
    return time.strftime(LABEL_FORMAT, time.localtime(now))


def session_name(project_name: str, label: str, taken: set[str]) -> str:
    """``<project>-<label>`` as a slug, suffixed until nothing in ``taken`` holds it.

    The slug is a session name's (``handoff_name`` with a given name only
    normalises it): lower case, runs of anything but letters and digits
    folded to one ``-``, so the name cannot start with ``-`` and cannot be
    read as a flag. A label with no letter or digit, and a project name with
    none, is refused rather than replaced. A clash adds ``-2``, then ``-3``,
    the user's settled choice over refusing a name in use.
    """
    base = f"{handoff_name('', name=project_name)}-{handoff_name('', name=label)}"
    name, suffix = base, 2
    while name in taken:
        name, suffix = f"{base}-{suffix}", suffix + 1
    return name


def open_windows(project: dict[str, Any]) -> int:
    """How many Claude windows the scan knows to be alive in one project.

    ``project`` is one entry of the ``assess`` document's ``projects``. A
    host counts once, whether it is a session's current host, an earlier one
    still running, or an orphan; a host whose liveness is unknown (``None``)
    does not count, because nothing says it is open.
    """
    alive = set()
    for row in project.get("rows") or []:
        if row.get("kind") not in (ROW_SESSION, ROW_HOST):
            continue
        for host in row.get("hosts") or []:
            if host.get("alive") is True:
                alive.add(host.get("host_session_id"))
    return len(alive)


def checkout_changes(root: Path | str) -> int | None:
    """How many paths are changed or untracked in ``root``; ``None`` if git cannot say.

    Read with ``GIT_OPTIONAL_LOCKS=0``: the main checkout may be where a
    person or a session is working, and a plain ``git status`` would refresh
    the index under them.
    """
    with git.without_optional_locks():
        return git.uncommitted_count(Path(root))


def start_facts(project: dict[str, Any], changes: int | None) -> dict[str, Any]:
    """What the confirm shows before a start: ``{open_windows, uncommitted, dirty}``.

    ``changes`` is :func:`checkout_changes`'s answer; ``dirty`` is ``None``
    when it is. Neither fact blocks a start: the new session isolates before
    it edits, and a second window in one project is the person's choice.
    """
    return {
        "open_windows": open_windows(project),
        "uncommitted": changes,
        "dirty": None if changes is None else changes > 0,
    }


def fact_lines(facts: dict[str, Any]) -> list[str]:
    """The two facts as the CLI prints them, one line each."""
    count = facts.get("open_windows") or 0
    if count == 0:
        windows = "no Claude window is open in this project"
    else:
        windows = f"{count} Claude window{'s' if count != 1 else ''} already open in this project"
    changes = facts.get("uncommitted")
    if changes is None:
        checkout = "invariant could not tell whether the main checkout has uncommitted changes"
    elif changes == 0:
        checkout = "the main checkout has no uncommitted changes"
    else:
        checkout = (
            f"the main checkout has {changes} uncommitted "
            f"change{'s' if changes != 1 else ''}"
        )
    return [windows, checkout]
