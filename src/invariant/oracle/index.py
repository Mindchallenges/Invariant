"""The project index: every invariant project on this machine, once each.

One JSON document, ``<oracle home>/projects.json``::

    {"version": 1,
     "projects": [{"key": <state root>, "root": <main checkout>,
                   "name": <project name>, "via": "init|session-start|manual"}]}

**Keyed by the state root.** A project is "a place sessions are recorded", and
that place is :attr:`Manifest.state_root` — the git common directory, which
every linked worktree of a repository resolves to identically. Keyed by the
project root instead, each worktree a session started from would register as
a separate project, and so would each of several manifests in one repository:
the same sessions listed two or five times over. ``root`` records where a
person would ``cd`` to, which is the main checkout's spelling of the project
whichever tree registered it.

**No clock times.** An entry says what the project is and how it arrived,
never when — the same rule the stats records follow (state_model.md, "Storing
what was measured, not when").

**Writes are locked; reads are not.** Several hosts can start their first
session in several projects at once, so a write is a read-modify-write under
:meth:`JsonStore.locked`. The SessionStart path reads FIRST and takes the lock
only for a project that is missing, so a known project costs one small read
per host start and writes nothing.

**Keys this build does not know are kept.** Whole entries and the document are
mutated in place rather than rebuilt from :class:`ProjectEntry`, so a field a
later build adds survives being written by this one (the BUG_061 lesson).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core import git
from ..core.errors import StateError, UsageError
from ..core.manifest import MANIFEST_RELPATH, Manifest
from ..core.store import JsonStore

INDEX_FILE = "projects.json"
VERSION = 1

#: How an entry arrived. ``init`` and ``session-start`` are the two automatic
#: routes; ``manual`` is ``invariant oracle projects add``.
VIA = ("init", "session-start", "manual")


@dataclass(frozen=True)
class ProjectEntry:
    """One registered project, as this build reads it."""

    key: str
    root: str
    name: str
    via: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ProjectEntry:
        return cls(
            key=row["key"],
            root=str(row.get("root") or ""),
            name=str(row.get("name") or ""),
            via=str(row.get("via") or ""),
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @property
    def has_manifest(self) -> bool:
        """Whether the recorded root still carries a manifest."""
        return bool(self.root) and (Path(self.root) / MANIFEST_RELPATH).is_file()


class HomeStore(JsonStore):
    """A JSON store over the oracle home.

    The base class's ``ensure_root`` lays out a PROJECT state root —
    ``sessions/``, ``pipelines/`` — which has no meaning here. The home holds
    the index, its lock directory and the watcher's ``oracle/`` directory
    (:mod:`invariant.oracle.watcher`), and nothing else.
    """

    def ensure_root(self) -> None:
        try:
            (self.root / "locks").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StateError(f"cannot create the oracle home {self.root}: {exc}") from exc


def _rows(document: Any) -> list[dict[str, Any]]:
    """The entry list of a stored document, refusing any shape it cannot read.

    A newer version is refused rather than rewritten: this build would drop
    whatever the new version means, and the index is one file every project's
    registration goes through.
    """
    if not isinstance(document, dict):
        raise StateError(f"{INDEX_FILE} is not a JSON object; inspect or remove it")
    version = document.get("version", VERSION)
    if version != VERSION:
        raise StateError(
            f"{INDEX_FILE} is version {version}, and this build reads version "
            f"{VERSION}; upgrade invariant rather than rewrite it"
        )
    rows = document.get("projects", [])
    if not isinstance(rows, list) or not all(
        isinstance(row, dict) and isinstance(row.get("key"), str) for row in rows
    ):
        raise StateError(
            f"{INDEX_FILE} has a malformed project list; every entry needs a "
            f"string 'key'"
        )
    return rows


def _canonical_root(root: Path) -> Path:
    """The main checkout's spelling of a project root.

    A manifest found from inside a linked worktree has the worktree as its
    root, and the worktree goes away at teardown — after which ``prune`` would
    drop a project that still exists. The same relative position in the main
    checkout is recorded instead, provided a manifest is there too; otherwise
    the root as found, which is all the evidence there is.
    """
    top = git.toplevel(root)
    main = git.main_checkout(root)
    if top is None or main is None:
        return root
    try:
        relative = root.resolve().relative_to(top.resolve())
    except ValueError:
        return root
    candidate = (main / relative).resolve()
    return candidate if (candidate / MANIFEST_RELPATH).is_file() else root


class ProjectIndex:
    """The project index under one oracle home."""

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self._store = HomeStore(self.home)

    # -- reads ---------------------------------------------------------

    def _read_entries(self) -> list[ProjectEntry]:
        document = self._store.read(INDEX_FILE, default=None)
        if document is None:
            return []
        return [ProjectEntry.from_row(row) for row in _rows(document)]

    def list(self) -> list[ProjectEntry]:
        return self._read_entries()

    def get(self, key: str) -> ProjectEntry | None:
        return next((e for e in self._read_entries() if e.key == key), None)

    # -- writes --------------------------------------------------------

    def register(self, manifest: Manifest, via: str) -> tuple[ProjectEntry, bool]:
        """Record ``manifest``'s project once. Returns the entry and whether it
        was added; an existing entry is returned unchanged, ``via`` and all."""
        if via not in VIA:
            raise UsageError(f"unknown registration route {via!r}; one of {', '.join(VIA)}")
        key = str(manifest.state_root)
        existing = next((e for e in self._read_entries() if e.key == key), None)
        if existing is not None:
            return existing, False

        root = _canonical_root(manifest.root)
        named = manifest.source_of("project.name") == "manifest"
        name = str(manifest.get("project.name")) if named else root.name
        entry = ProjectEntry(key=key, root=str(root), name=name, via=via)

        with self._store.locked(INDEX_FILE) as document:
            rows = _rows(document)
            # Another host may have registered it between the read above and
            # taking the lock; the first registration stands.
            for row in rows:
                if row["key"] == key:
                    return ProjectEntry.from_row(row), False
            rows.append(entry.to_dict())
            document["version"] = VERSION
            document["projects"] = rows
        return entry, True

    def match(self, selector: str) -> ProjectEntry:
        """The one entry ``selector`` names: a key, a root, or a name.

        Tried in that order, first rung with a hit wins. A root may be given
        relative to the working directory. A name shared by several entries is
        refused with every candidate's key, which cannot be ambiguous.
        """
        entries = self._read_entries()
        if not entries:
            raise UsageError("no projects are registered in the oracle index")
        for entry in entries:
            if entry.key == selector:
                return entry
        as_path = str(Path(selector).expanduser().resolve())
        for entry in entries:
            if as_path in (entry.root, entry.key):
                return entry
        named = [e for e in entries if e.name == selector]
        if len(named) == 1:
            return named[0]
        if named:
            keys = "\n".join(f"  {e.key}  ({e.root})" for e in named)
            raise UsageError(
                f"{len(named)} projects are named {selector!r}; remove one by "
                f"its key:\n{keys}"
            )
        known = ", ".join(sorted({e.name for e in entries}))
        raise UsageError(
            f"no registered project matches {selector!r} by key, root or name "
            f"(registered: {known})"
        )

    def remove(self, selector: str) -> ProjectEntry:
        """Remove the entry ``selector`` names (see :meth:`match`)."""
        entry = self.match(selector)
        with self._store.locked(INDEX_FILE) as document:
            document["projects"] = [r for r in _rows(document) if r["key"] != entry.key]
        return entry

    def prune(self, dry_run: bool = False) -> list[ProjectEntry]:
        """Drop every entry whose root no longer carries a manifest.

        Only the index changes; nothing under any project is touched. The
        manifest is re-checked under the lock, so a project restored in the
        meantime is kept.
        """
        stale = [e for e in self._read_entries() if not e.has_manifest]
        if dry_run or not stale:
            return stale
        keys = {e.key for e in stale}
        with self._store.locked(INDEX_FILE) as document:
            document["projects"] = [
                row
                for row in _rows(document)
                if row["key"] not in keys or ProjectEntry.from_row(row).has_manifest
            ]
        return stale
