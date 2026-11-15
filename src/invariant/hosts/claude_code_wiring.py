"""Where Claude Code is wired in one project, and how to take it back out.

ADR-014 D11, as ADR-020 D9 amends it. ``records.hooks`` and ``records.agents``
each name a location, and :func:`wiring_plan` says what that location means for
this host:

- ``repo`` is the committed wiring: ``.claude/settings.json``,
  ``.claude/hooks/`` and ``.claude/agents/``, reviewed like any other file.
- ``local`` is the same project directory with nothing committed: the hook
  entries in ``.claude/settings.local.json`` (Claude Code's personal,
  per-project settings file), the same shims and agents, every path listed in
  the clone's ``.git/info/exclude``, and the store's main checkout in
  ``permissions.additionalDirectories``.

Nothing here ever writes outside the project and its git directory. The shared
``.gitignore`` is never edited, and no user-level file is read or written.

**The receipt is what makes uninstall an exact inverse.** A ``local`` install
writes ``<state root>/host/claude_code/installed.json``: each settings entry it
added, verbatim, each exclude line it added, and each file with its hash. It
also keeps each file's text from before invariant first touched it, so that
removing invariant's entries gives back the original bytes whenever nothing
else in the file changed since; a key the user added in between survives, and
then the file is written as JSON. A file whose hash no longer matches was
edited by the user and is left in place. A ``repo`` install writes no receipt:
its files are tracked, and git is their record.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import git
from ..core.errors import UsageError

REPO = "repo"
LOCAL = "local"
LOCATIONS = (REPO, LOCAL)

#: The settings file each location wires its hook entries into.
SETTINGS_FILES = {REPO: "settings.json", LOCAL: "settings.local.json"}

#: The receipt, under the state root (the git common directory), so every
#: worktree of the clone reads the same one and no clone ever copies it.
RECEIPT = Path("host") / "claude_code" / "installed.json"

#: How ``doctor`` and ``uninstall`` spell the exclude file to a person.
EXCLUDE_LABEL = ".git/info/exclude"


@dataclass(frozen=True)
class WiringPlan:
    """Where one location puts this host's files, for one project root."""

    location: str
    root: Path
    settings: Path
    hooks_dir: Path
    agents_dir: Path
    #: The store's main checkout, which a ``local`` wiring grants the host
    #: (ADR-014 D11). A committed file never carries one: it would hold this
    #: machine's home directory.
    additional_directories: tuple[str, ...] = ()

    @property
    def committed(self) -> bool:
        return self.location == REPO

    def exclude_line(self, path: Path) -> str:
        """``path`` as an exclude pattern anchored at the worktree's top, so
        one line covers the main checkout and every session worktree."""
        return "/" + Path(path).relative_to(self.root).as_posix()


def wiring_plan(root: Path, location: str, store_root: Path | None = None) -> WiringPlan:
    """What ``location`` means for Claude Code in the project at ``root``."""
    if location not in LOCATIONS:
        raise ValueError(
            f"Claude Code is wired at repo or local, not {location!r} "
            f"(user was retired by ADR-020 D9)"
        )
    root = Path(root)
    claude = root / ".claude"
    granted = (str(store_root),) if location == LOCAL and store_root is not None else ()
    return WiringPlan(
        location=location,
        root=root,
        settings=claude / SETTINGS_FILES[location],
        hooks_dir=claude / "hooks",
        agents_dir=claude / "agents",
        additional_directories=granted,
    )


def exclude_file(root: Path) -> Path | None:
    """The clone's ``info/exclude``: in the git common directory, so it is
    shared by every worktree, never copied by a clone and never tracked."""
    common = git.common_dir(Path(root))
    return None if common is None else common / "info" / "exclude"


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_settings(path: Path, label: str) -> dict[str, Any]:
    """A settings file's object, ``{}`` when absent. Malformed JSON is refused,
    never overwritten: the file is the user's, and this library is a guest."""
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise UsageError(
            f"{label} is not valid JSON ({exc}); fix it first, so nothing here "
            f"overwrites your work"
        ) from exc
    return loaded if isinstance(loaded, dict) else {}


def write_settings(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@dataclass
class Receipt:
    """What a ``local`` install added, so that uninstall takes exactly that."""

    path: Path
    #: The checkout the files were written in: the main checkout.
    root: Path
    #: ``{"path", "before", "hooks": {event: [entry]}, "additional_directories"}``
    settings: dict[str, Any] = field(default_factory=dict)
    #: ``{"path", "before", "lines"}``
    exclude: dict[str, Any] = field(default_factory=dict)
    #: Project-relative path to the SHA-256 of the bytes invariant wrote.
    files: dict[str, str] = field(default_factory=dict)
    #: Directories the install created, project-relative.
    dirs: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path, root: Path) -> Receipt:
        if not Path(path).is_file():
            return cls(path=Path(path), root=Path(root))
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise UsageError(
                f"the install receipt {path} is not valid JSON ({exc}); "
                f"uninstall reads it to remove exactly what was added"
            ) from exc
        return cls(
            path=Path(path),
            root=Path(data.get("root") or root),
            settings=dict(data.get("settings") or {}),
            exclude=dict(data.get("exclude") or {}),
            files=dict(data.get("files") or {}),
            dirs=list(data.get("dirs") or []),
        )

    @property
    def empty(self) -> bool:
        return not (
            any(self.settings.get("hooks", {}).values())
            or self.settings.get("additional_directories")
            or self.exclude.get("lines")
            or self.files
        )

    def save(self) -> None:
        if self.empty:
            if self.path.is_file():
                self.path.unlink()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "version": 1,
            "root": str(self.root),
            "settings": self.settings,
            "exclude": self.exclude,
            "files": dict(sorted(self.files.items())),
            "dirs": self.dirs,
        }
        self.path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")

    # -- recording -----------------------------------------------------

    def relative(self, path: Path) -> str:
        return Path(path).relative_to(self.root).as_posix()

    def record_file(self, path: Path) -> None:
        self.files[self.relative(path)] = digest(path)

    def record_dir(self, path: Path) -> None:
        rel = self.relative(path)
        if rel not in self.dirs:
            self.dirs.append(rel)


def make_dirs(path: Path, root: Path, receipt: Receipt | None) -> None:
    """``mkdir -p``, recording each directory it created below ``root``."""
    missing = []
    current = Path(path)
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir()
        if receipt is not None and Path(root) in directory.parents:
            receipt.record_dir(directory)


# -- the settings file ---------------------------------------------------------


def wire(
    plan: WiringPlan, entries: dict[str, dict[str, Any]], receipt: Receipt | None
) -> bool:
    """Add each hook entry the settings file lacks, and the plan's directories.

    An entry is recognised by its command, so a second install adds nothing.
    The file is written only when something was added. Returns whether it was.
    """
    label = f".claude/{plan.settings.name}"
    before = plan.settings.read_text(encoding="utf-8") if plan.settings.is_file() else None
    settings = read_settings(plan.settings, label)
    added: dict[str, list[dict[str, Any]]] = {}
    for host_event, entry in entries.items():
        command = entry["hooks"][0]["command"]
        present = settings.get("hooks", {}).get(host_event, [])
        if any(command in json.dumps(existing) for existing in present):
            continue
        settings.setdefault("hooks", {}).setdefault(host_event, []).append(entry)
        added.setdefault(host_event, []).append(entry)
    granted: list[str] = []
    for directory in plan.additional_directories:
        current = settings.get("permissions", {}).get("additionalDirectories", [])
        if directory in current:
            continue
        permissions = settings.setdefault("permissions", {})
        permissions.setdefault("additionalDirectories", []).append(directory)
        granted.append(directory)
    if not added and not granted:
        return False
    if receipt is not None:
        if not receipt.settings:
            receipt.settings = {
                "path": receipt.relative(plan.settings),
                "before": before,
                "hooks": {},
                "additional_directories": [],
            }
        for host_event, new in added.items():
            receipt.settings["hooks"].setdefault(host_event, []).extend(new)
        receipt.settings["additional_directories"].extend(granted)
        make_dirs(plan.settings.parent, plan.root, receipt)
    write_settings(plan.settings, settings)
    return True


def _without(items: list[Any], removed: Iterable[Any]) -> list[Any]:
    kept = list(items)
    for item in removed:
        if item in kept:
            kept.remove(item)
    return kept


def unwire(receipt: Receipt) -> bool:
    """Take the receipt's settings entries back out; ``True`` if the file changed.

    Every other key is left as the user has it. When what remains is the
    file as it was before the first install, its original bytes are written
    back; when the file did not exist and nothing else is left, it is removed.
    """
    if not receipt.settings:
        return False
    path = receipt.root / receipt.settings["path"]
    if not path.is_file():
        return False
    label = receipt.settings["path"]
    settings = read_settings(path, label)
    before_text = receipt.settings.get("before")
    try:
        before = json.loads(before_text) if before_text else {}
    except json.JSONDecodeError:
        before = None
    before_map = before if isinstance(before, dict) else {}

    hooks = settings.get("hooks")
    if isinstance(hooks, dict):
        for host_event, entries in receipt.settings.get("hooks", {}).items():
            if not isinstance(hooks.get(host_event), list):
                continue
            kept = _without(hooks[host_event], entries)
            if kept or host_event in (before_map.get("hooks") or {}):
                hooks[host_event] = kept
            else:
                del hooks[host_event]
        if not hooks and "hooks" not in before_map:
            del settings["hooks"]
    permissions = settings.get("permissions")
    granted = receipt.settings.get("additional_directories") or []
    if granted and isinstance(permissions, dict):
        listed = permissions.get("additionalDirectories")
        if isinstance(listed, list):
            kept = _without(listed, granted)
            had = (before_map.get("permissions") or {}).get("additionalDirectories")
            if kept or had is not None:
                permissions["additionalDirectories"] = kept
            else:
                del permissions["additionalDirectories"]
        if not permissions and "permissions" not in before_map:
            del settings["permissions"]

    if before_text is not None and settings == before:
        path.write_text(before_text, encoding="utf-8")
    elif before_text is None and settings == {}:
        path.unlink()
    else:
        write_settings(path, settings)
    return True


# -- the exclude file ------------------------------------------------------------


def add_exclude(root: Path, lines: Iterable[str], receipt: Receipt) -> list[str]:
    """Append each line ``.git/info/exclude`` lacks; returns those it added."""
    path = exclude_file(root)
    if path is None:
        raise UsageError(
            f"{root} is not in a git repository, so local wiring has no "
            f"{EXCLUDE_LABEL} to hide it; use records.hooks/agents: repo"
        )
    text = path.read_text(encoding="utf-8") if path.is_file() else None
    present = set((text or "").splitlines())
    new = [line for line in dict.fromkeys(lines) if line not in present]
    if not new:
        return []
    if not receipt.exclude:
        receipt.exclude = {"path": str(path), "before": text, "lines": []}
    body = text or ""
    if body and not body.endswith("\n"):
        body += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + "".join(f"{line}\n" for line in new), encoding="utf-8")
    receipt.exclude["lines"].extend(new)
    return new


def remove_exclude(receipt: Receipt, keep: Iterable[str] = ()) -> list[str]:
    """Take the receipt's exclude lines back out, except those in ``keep``.

    Returns the lines removed. When what remains is the file as it was before
    the first install, its original bytes are written back.
    """
    if not receipt.exclude:
        return []
    path = Path(receipt.exclude["path"])
    kept_lines = set(keep)
    remove = [line for line in receipt.exclude.get("lines", []) if line not in kept_lines]
    if not path.is_file() or not remove:
        return []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    removed = []
    for line in remove:
        for index in range(len(lines) - 1, -1, -1):
            if lines[index] == line:
                del lines[index]
                removed.append(line)
                break
    before = receipt.exclude.get("before")
    if before is not None and lines == before.splitlines():
        path.write_text(before, encoding="utf-8")
    elif before is None and not lines:
        path.unlink()
    else:
        path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return removed


def excluded(root: Path) -> set[str]:
    """The lines ``.git/info/exclude`` holds now."""
    path = exclude_file(root)
    if path is None or not path.is_file():
        return set()
    return set(path.read_text(encoding="utf-8").splitlines())


# -- files -------------------------------------------------------------------


def remove_files(receipt: Receipt) -> tuple[list[str], list[str]]:
    """Remove each file whose bytes are still the ones invariant wrote.

    ``(removed, edited)``: a file whose hash no longer matches was edited by
    the user and is left in place. Directories the install created are
    removed once empty, deepest first.
    """
    removed, edited = [], []
    for rel, sha in sorted(receipt.files.items()):
        path = receipt.root / rel
        if not path.is_file():
            continue
        if digest(path) == sha:
            path.unlink()
            removed.append(rel)
        else:
            edited.append(rel)
    for rel in sorted(receipt.dirs, key=lambda d: d.count("/"), reverse=True):
        directory = receipt.root / rel
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    return removed, edited


# -- drift, for doctor ----------------------------------------------------------


def drift(receipt: Receipt) -> list[str]:
    """Each receipt entry missing from the files it was added to."""
    problems: list[str] = []
    if receipt.settings:
        rel = receipt.settings["path"]
        path = receipt.root / rel
        try:
            settings = read_settings(path, rel)
        except UsageError as exc:
            return [str(exc)]
        hooks = settings.get("hooks") or {}
        for host_event, entries in receipt.settings.get("hooks", {}).items():
            present = hooks.get(host_event) or []
            if any(entry not in present for entry in entries):
                problems.append(f"the {host_event} entry is missing from {rel}")
        listed = (settings.get("permissions") or {}).get("additionalDirectories") or []
        for directory in receipt.settings.get("additional_directories", []):
            if directory not in listed:
                problems.append(
                    f"{directory} is missing from {rel}'s permissions.additionalDirectories"
                )
    if receipt.exclude:
        path = Path(receipt.exclude["path"])
        present = set(path.read_text(encoding="utf-8").splitlines()) if path.is_file() else set()
        for line in receipt.exclude.get("lines", []):
            if line not in present:
                problems.append(f"{line} is missing from {EXCLUDE_LABEL}")
    for rel in sorted(receipt.files):
        if not (receipt.root / rel).is_file():
            problems.append(f"{rel} is missing")
    return problems


def unexcluded(plan: WiringPlan, paths: Iterable[Path]) -> list[str]:
    """Each existing ``local`` path that ``.git/info/exclude`` does not list:
    one ``git status`` would show."""
    present = excluded(plan.root)
    return [
        plan.exclude_line(path)
        for path in paths
        if os.path.lexists(path) and plan.exclude_line(path) not in present
    ]
