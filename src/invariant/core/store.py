"""Concurrency-safe JSON persistence for the state root.

Two access patterns, deliberately different (``docs/architecture/state_model.md``):

* **Per-session files** are written only by their owning session. They need
  atomicity — a reader must never see a half-written file — but not locking.
  ``os.replace`` over a same-directory temporary file provides exactly that.
* **The registry** is written by every session on the machine, so a
  read-modify-write must hold an advisory lock across both halves.

Everything is plain JSON on purpose. When a state file wedges, the recovery
path is a human opening it, so the format has to survive being read by one.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .errors import LockTimeout, StateError

LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.05


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
