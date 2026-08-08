"""The session domain.

The only domain with genuine cross-process concurrency: several sessions run
against one repository at once, which is the situation the whole isolation
protocol exists for. Per-session documents are written by their owner alone;
the registry that indexes them is written by everyone and is therefore the only
locked file.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..core import git
from ..core.errors import StateError, TransitionError, UsageError
from ..core.fsm import Machine
from ..core.machines import load_session_machine
from ..core.store import JsonStore

REGISTRY = "registry.json"


@dataclass
class Session:
    id: str
    name: str
    state: str = "registered"
    branch: str | None = None
    worktree: str | None = None
    #: Where the session was registered. A session that has not isolated yet
    #: has no worktree, and without this it would be unfindable — which would
    #: make the very next step, isolating it, impossible.
    origin: str | None = None
    base: str = "master"
    files: list[str] = field(default_factory=list)
    push_count: int = 0
    history: list[dict] = field(default_factory=list)
    overrides: list[dict] = field(default_factory=list)
    heartbeat: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Session":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})


class SessionDomain:
    def __init__(self, manifest, store: JsonStore | None = None) -> None:
        self.manifest = manifest
        self.store = store or JsonStore(manifest.state_root)
        self.machine: Machine = load_session_machine(manifest)

    # -- lookup --------------------------------------------------------

    def _path(self, session_id: str) -> str:
        return f"sessions/{session_id}.json"

    def get(self, session_id: str) -> Session | None:
        raw = self.store.read(self._path(session_id))
        return Session.from_dict(raw) if raw else None

    def require(self, session_id: str) -> Session:
        session = self.get(session_id)
        if session is None:
            raise UsageError(
                f"no session {session_id!r}; 'invariant session list' shows the "
                f"registered ones"
            )
        return session

    def list(self) -> list[Session]:
        return [
            s
            for s in (self.get(sid) for sid in self.store.list_ids("sessions"))
            if s is not None
        ]

    def current(self, cwd: Path | None = None) -> Session | None:
        """The session this invocation belongs to.

        An explicit identifier wins. Otherwise the session whose worktree
        contains the working directory does — which is what makes a hook
        running inside a worktree find its own session without being told.
        """
        explicit = os.environ.get("INVARIANT_SESSION_ID")
        if explicit:
            return self.get(explicit)

        cwd = Path(cwd or Path.cwd()).resolve()
        best: Session | None = None
        best_len = -1
        for session in self.list():
            if session.state in {"torn_down", "abandoned"}:
                continue
            if not session.worktree:
                continue
            try:
                worktree = Path(session.worktree).resolve()
            except OSError:
                continue
            if cwd == worktree or worktree in cwd.parents:
                # Deepest match wins, so a nested worktree beats its parent.
                if len(str(worktree)) > best_len:
                    best, best_len = session, len(str(worktree))
        if best is not None:
            return best

        # No worktree contains the working directory. Fall back to where the
        # session was registered: a session that has not isolated yet has no
        # worktree at all, and one that just isolated leaves the caller
        # standing in the origin. Both need to stay addressable.
        candidates = [
            s
            for s in self.list()
            if s.state not in {"torn_down", "abandoned"}
            and s.origin
            and _contains(Path(s.origin), cwd)
        ]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise UsageError(
                f"{len(candidates)} live sessions were registered here and none "
                f"of their worktrees contains this directory: "
                + ", ".join(f"{s.id} ({s.name})" for s in candidates)
                + ".\nSet INVARIANT_SESSION_ID, pass --id, or run from inside "
                "the session's worktree."
            )
        return None

    # -- lifecycle -----------------------------------------------------

    def start(self, name: str, session_id: str | None = None, base: str = "master") -> Session:
        session_id = session_id or os.environ.get("INVARIANT_SESSION_ID") or uuid.uuid4().hex[:8]
        if self.get(session_id) is not None:
            raise UsageError(f"session {session_id!r} already exists")
        session = Session(
            id=session_id,
            name=_slug(name),
            base=base,
            origin=str(git.toplevel(Path.cwd()) or Path.cwd()),
            heartbeat=time.time(),
        )
        self._save(session)
        self._register(session)
        return session

    def isolation_plan(self, session: Session) -> dict[str, str]:
        """Where this session's worktree and branch would go.

        Derived from the session identifier so that two sessions never collide,
        and placed beside the repository rather than inside it so that the
        worktree is not itself a tracked path.
        """
        root = git.toplevel(Path.cwd()) or self.manifest.root
        prefix = self.manifest.get("session.worktree_prefix") or f"{root.name}-wt"
        return {
            "worktree": str(root.parent / f"{prefix}-{session.id}"),
            "branch": f"session/{session.id}",
            "base": session.base,
        }

    def isolate(self, session_id: str, execute: bool = False) -> dict[str, Any]:
        session = self.require(session_id)
        plan = self.isolation_plan(session)
        if not execute:
            plan["command"] = (
                f"git worktree add {plan['worktree']} -b {plan['branch']} {plan['base']}"
            )
            return plan

        if Path(plan["worktree"]).exists():
            raise UsageError(
                f"{plan['worktree']} already exists; remove it or pick another "
                f"session identifier"
            )
        proc = subprocess.run(
            ["git", "worktree", "add", plan["worktree"], "-b", plan["branch"], plan["base"]],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise StateError(f"worktree creation failed: {proc.stderr.strip()}")

        session.worktree = plan["worktree"]
        session.branch = plan["branch"]
        self._advance(session, "isolated", reason="worktree created")
        return plan

    def transition(
        self,
        session_id: str,
        target: str,
        reason: str | None = None,
        force: bool = False,
    ) -> Session:
        session = self.require(session_id)
        self._advance(session, target, reason=reason, force=force)
        return session

    def add_files(self, session_id: str, paths: list[str]) -> Session:
        session = self.require(session_id)
        for path in paths:
            if path not in session.files:
                session.files.append(path)
        self._save(session)
        return session

    def record_push(self, session_id: str) -> Session:
        session = self.require(session_id)
        session.push_count += 1
        self._advance(session, "pushed", reason="push recorded")
        return session

    def teardown(self, session_id: str, remove_worktree: bool = True) -> Session:
        session = self.require(session_id)
        if remove_worktree and session.worktree and Path(session.worktree).exists():
            proc = subprocess.run(
                ["git", "worktree", "remove", session.worktree],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise StateError(
                    f"could not remove {session.worktree}: {proc.stderr.strip()}"
                )
        self._advance(session, "torn_down", reason="teardown")
        self._deregister(session.id)
        return session

    # -- maintenance ---------------------------------------------------

    def repair(self, dry_run: bool = False) -> list[str]:
        """Reconcile the registry against session files and real worktrees.

        A session whose worktree no longer exists is marked abandoned rather
        than deleted: the record of what happened is the thing that makes a
        crashed session diagnosable afterwards.
        """
        actions: list[str] = []
        live_worktrees = {str(w.path.resolve()) for w in git.worktrees(Path.cwd())}

        for session in self.list():
            if session.state in {"torn_down", "abandoned"}:
                continue
            if session.worktree and str(Path(session.worktree).resolve()) not in live_worktrees:
                actions.append(
                    f"{session.id}: worktree is gone, marking abandoned "
                    f"(was {session.state})"
                )
                if not dry_run:
                    session.state = "abandoned"
                    session.history.append(
                        {"to": "abandoned", "reason": "worktree no longer exists", "repair": True}
                    )
                    self._save(session)
                    self._deregister(session.id)

        known = set(self.store.list_ids("sessions"))
        registry = self.store.read(REGISTRY, {"sessions": {}})
        for session_id in list(registry.get("sessions", {})):
            if session_id not in known:
                actions.append(f"{session_id}: registry entry with no session file")
                if not dry_run:
                    self._deregister(session_id)
        return actions

    # -- internals -----------------------------------------------------

    def _advance(
        self,
        session: Session,
        target: str,
        reason: str | None = None,
        force: bool = False,
    ) -> None:
        if session.state != target:
            try:
                self.machine.require_edge(session.state, target)
            except TransitionError:
                if not force:
                    raise
                session.overrides.append(
                    {"from": session.state, "to": target, "reason": reason or "forced"}
                )
        session.history.append({"from": session.state, "to": target, "reason": reason})
        session.state = target
        session.heartbeat = time.time()
        self._save(session)
        self._register(session)

    def _save(self, session: Session) -> None:
        self.store.ensure_root()
        self.store.write(self._path(session.id), session.to_dict())

    def _register(self, session: Session) -> None:
        with self.store.locked(REGISTRY, default={"sessions": {}}) as registry:
            registry.setdefault("sessions", {})[session.id] = {
                "name": session.name,
                "state": session.state,
                "worktree": session.worktree,
                "branch": session.branch,
            }

    def _deregister(self, session_id: str) -> None:
        with self.store.locked(REGISTRY, default={"sessions": {}}) as registry:
            registry.setdefault("sessions", {}).pop(session_id, None)


def _contains(root: Path, candidate: Path) -> bool:
    try:
        root = root.resolve()
    except OSError:
        return False
    return candidate == root or root in candidate.parents


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    if not slug:
        raise UsageError("a session name must contain at least one alphanumeric character")
    return slug
