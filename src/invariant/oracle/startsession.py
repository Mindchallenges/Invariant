"""Starting a new Claude window in a registered project (IDEA_030, ADR-009 D16).

What ``invariant oracle start-session`` decides before it launches, kept apart
from the launch itself so the panel's confirm sheet (increment 2) can show the
same facts and the same name from the same functions:

* **the name**: the project and a label, slugged, with ``-2``, ``-3`` added
  until no live Claude window holds it (:func:`session_name`);
* **the facts the confirm shows**: how many Claude windows are open in the
  project and whether its main checkout has uncommitted changes
  (:func:`start_facts`). They inform; neither blocks a start;
* **the window, once it is there**: the row of a later scan whose live host
  was launched by the start's name (:func:`find_started`), which ends the
  panel's "Starting…" card.

The page reaches the command's own path through a :class:`Starter` the CLI
builds, so a start from the phone and one from a terminal are one code path.

Everything here is pure but :func:`checkout_changes`, which asks git one
read-only question with optional locks off, so it never takes ``index.lock``
under a session working in that checkout (ADR-007 D16). The oracle imports
nothing from ``hosts/``: which names are in use and how a window is opened are
Claude Code's, and the CLI joins them.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core import git
from ..domains.session import handoff_name
from .scan import ROW_HOST, ROW_SESSION

#: The default label: the month, day, hour and minute the start was asked
#: for, in local time, so ``heap-1026-0930`` reads as "this morning's".
LABEL_FORMAT = "%m%d-%H%M"

#: A job that starts a new Claude window: the kind beside ``run`` and
#: ``terminal`` that the page's actions and the panel's words key on.
START = "start"


@dataclass(frozen=True)
class Starter:
    """How the page's start reaches ``invariant oracle start-session``'s own path.

    The CLI builds it (the oracle imports nothing from ``hosts/`` or the CLI):

    * ``prepare(project, label, prompt, scanned)`` answers what the command
      would print with ``--dry-run --json`` for that selector, label and
      prompt, the facts read from ``scanned``, one project of the ``assess``
      document (``{}`` when the read has none). It refuses, with an
      ``InvariantError``, whatever the command refuses: a project the index
      does not name, a label with no letter or digit, a prompt that reads as
      a flag. Nothing is launched.
    * ``launch(prepared)`` opens the window ``prepare`` described, through
      the one launcher, and returns the lines the command prints; it raises
      ``InvariantError`` when the window did not open.
    """

    prepare: Callable[[str, str | None, str | None, dict[str, Any]], dict[str, Any]]
    launch: Callable[[dict[str, Any]], str]


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


def find_started(document: dict[str, Any], key: str, name: str | None) -> dict[str, Any] | None:
    """The row of project ``key`` whose live host was launched as ``name``; else ``None``.

    ``document`` is an ``assess`` document. The host is matched by the name
    Claude Code's per-process record carries (``launched_name``), and only a
    host known to be alive counts. The row is the orphan host a new window is
    at first, or the session that took it once Claude started one.
    """
    if not name:
        return None
    for project in document.get("projects") or []:
        if (project.get("project") or {}).get("key") != key:
            continue
        for row in project.get("rows") or []:
            if row.get("kind") not in (ROW_SESSION, ROW_HOST):
                continue
            if any(h.get("alive") is True and h.get("launched_name") == name
                   for h in row.get("hosts") or []):
                return row
    return None


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
