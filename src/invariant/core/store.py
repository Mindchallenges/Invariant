"""Concurrency-safe JSON persistence for the state root.

Two access patterns, deliberately different (``docs/architecture/state_model.md``):

* **Per-session files** are written only by their owning session. They need
  atomicity — a reader must never see a half-written file — but not locking.
  ``os.replace`` over a same-directory temporary file provides exactly that.
* **The registry** is written by every session on the machine, so a
  read-modify-write must hold an advisory lock across both halves.

Everything is plain JSON on purpose. When a state file wedges, the recovery
path is a human opening it, so the format has to survive being read by one.

The one rule about a document's CONTENTS that lives here is
:class:`PreservesUnknownKeys`, and it is a rule about the format rather than
about any record's meaning: a key this build cannot account for is written back
rather than deleted, so the two builds at either end of a document need not be
the same one.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, TypeVar

from .errors import LockTimeout, StateError

LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.05


#: The attribute a record parks the keys it did not recognise under, and a
#: RESERVED key in the document format: nothing ever writes it, because
#: :meth:`PreservesUnknownKeys.to_dict` lifts its contents back to the top
#: level, and nothing ever reads it, because :meth:`from_dict` holds it out of
#: the field set it matches against.
#:
#: A leading underscore where every real field is public is the signal to
#: somebody reading the JSON by hand: structure, not data. It is also why a
#: future field will not collide with it — the fields on these records are
#: named for what they hold.
UNKNOWN_KEYS = "_extra"

#: `typing.Self` says this more directly and arrives in 3.11; this project
#: supports 3.10, so the bound TypeVar is the spelling that works on both.
_R = TypeVar("_R", bound="PreservesUnknownKeys")


class PreservesUnknownKeys:
    """A persisted record that survives being written back by an older build.

    ``from_dict`` filtering unknown keys is what makes a new field cost no
    migration: a document written before the field existed loads with it at its
    default and behaves as it did. That is the FORWARD direction, and it was
    designed. The reverse was not. An older build that loads such a document
    and saves it emits only the fields it declares, so every field it has never
    heard of is deleted — silently, completely, by a command that succeeded
    (BUG_061). In this repository the older build is the ordinary case: an
    editable install on ``PATH`` points at the main checkout while a feature
    branch adds fields, and every hook subprocess writes through it.

    So the keys a build does not recognise are kept rather than dropped, and
    written back out at the TOP LEVEL — where the build that owns them will
    look, which is where they were. Nesting them under a container would be the
    same deletion one indirection along, since no other build's ``from_dict``
    reads that container.

    Two rejected shapes, because both look cheaper from here:

    *Reconstructing the unknown keys at save time* — re-reading the stored
    document and merging — puts a store read inside every write, races anything
    else touching the file between the two, and does not work at all for the
    callers of ``to_dict`` that have no store: a gate context would then be
    built from a different document than the one about to be written.

    *Setting the unrecognised keys onto the instance* as attributes would put a
    hand-editable file in reach of ``__class__``, and would leave ``asdict``
    and ``repr`` disagreeing about what the record holds. They stay dict
    entries and are never named as attributes.

    What this cannot do is rescue a record from a build that is already
    installed, which has neither the fields nor this contract. It stops the
    NEXT asymmetry, not the one already on disk; that one closes when the
    branch merges. Any fix has that property, including the version-stamp
    alternative BUG_061 weighs against this one.
    """

    @classmethod
    def from_dict(cls: type[_R], raw: Mapping[str, Any]) -> _R:  # noqa: PYI019
        """Build a record, keeping every key this build cannot account for."""
        if not isinstance(raw, Mapping):
            # The store promises hand-inspectable, hand-fixable files, so a
            # hand edit that leaves a list where an object was earns the same
            # repair advice a malformed document already gets — rather than an
            # AttributeError from a comprehension two frames down.
            raise StateError(
                f"a {type(raw).__name__} is not a {cls.__name__} record; "
                f"inspect it or run 'invariant state repair'"
            )
        # UNKNOWN_KEYS is held out of the match, so the container can only ever
        # be filled by this method. A document carrying a key of that name is
        # therefore preserved as ordinary data under its own name, which is
        # what keeps a hand-edited value from being expanded into the record.
        declared = set(cls.__dataclass_fields__) - {UNKNOWN_KEYS}  # type: ignore[attr-defined]
        known: dict[str, Any] = {}
        unknown: dict[str, Any] = {}
        for key, value in raw.items():
            (known if key in declared else unknown)[key] = value
        return cls(**known, **{UNKNOWN_KEYS: unknown})

    def to_dict(self) -> dict:
        """The document, with the preserved keys merged back at the top level.

        A declared field always wins a collision with a preserved key of the
        same name. The live attribute is what the running code means; a
        preserved value is by definition one this build could not interpret, so
        letting it shadow the field would discard the value the code just
        computed — this defect inverted, and harder to see. ``from_dict``
        cannot produce such a collision, since a key that matches a field never
        reaches the container; the ordering is for a record built by hand.
        """
        body = asdict(self)  # type: ignore[call-overload]
        preserved = body.pop(UNKNOWN_KEYS, None) or {}
        return {**preserved, **body}


class JsonStore:
    """A directory of JSON documents addressed by relative path."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- paths ---------------------------------------------------------

    def _path(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        root = self.root.resolve()
        # A relative path that escapes the state root is a bug in the caller,
        # not something to normalise away quietly.
        if root != path and root not in path.parents:
            raise StateError(f"path {rel!r} escapes the state root")
        return path

    def ensure_root(self) -> None:
        try:
            (self.root / "sessions").mkdir(parents=True, exist_ok=True)
            (self.root / "pipelines").mkdir(parents=True, exist_ok=True)
            (self.root / "locks").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StateError(f"cannot create state root {self.root}: {exc}") from exc

    # -- reads ---------------------------------------------------------

    def exists(self, rel: str) -> bool:
        return self._path(rel).is_file()

    def read(self, rel: str, default: Any = None) -> Any:
        path = self._path(rel)
        if not path.is_file():
            return default
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except json.JSONDecodeError as exc:
            raise StateError(
                f"{rel} is not valid JSON ({exc}); inspect it or run "
                f"'invariant state repair'"
            ) from exc
        except OSError as exc:
            raise StateError(f"cannot read {rel}: {exc}") from exc

    def list_ids(self, subdir: str) -> list[str]:
        directory = self._path(subdir)
        if not directory.is_dir():
            return []
        return sorted(p.stem for p in directory.glob("*.json"))

    # -- writes --------------------------------------------------------

    def write(self, rel: str, data: Any) -> None:
        """Atomically replace ``rel`` with ``data``.

        The temporary file is created in the destination directory so that
        ``os.replace`` stays within one filesystem, which is what makes it
        atomic.
        """
        path = self._path(rel)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            raise StateError(f"cannot write {rel}: {exc}") from exc

    def delete(self, rel: str) -> bool:
        path = self._path(rel)
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError as exc:
            raise StateError(f"cannot delete {rel}: {exc}") from exc
        return True

    # -- locked read-modify-write --------------------------------------

    @contextmanager
    def locked(
        self,
        rel: str,
        default: Any = None,
        timeout: float = LOCK_TIMEOUT_SECONDS,
    ) -> Iterator[dict]:
        """Yield ``rel``'s contents for mutation, holding an exclusive lock.

        The yielded object is written back on clean exit. On an exception the
        write is skipped, so a failed mutation leaves the previous document
        intact rather than a partial one.
        """
        self.ensure_root()
        lock_path = self._path(f"locks/{Path(rel).stem}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        handle = open(lock_path, "a+")  # noqa: SIM115 - released in finally
        try:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LockTimeout(
                            f"could not lock {rel} within {timeout:g}s — another "
                            f"session may be wedged; try 'invariant state repair'"
                        ) from None
                    time.sleep(LOCK_POLL_SECONDS)

            data = self.read(rel, default if default is not None else {})
            yield data
            self.write(rel, data)
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
