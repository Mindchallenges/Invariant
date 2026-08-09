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


@dataclass(frozen=True)
class Resolution:
    """Which session, and on what evidence.

    ``how`` is not decoration. A caller that wants to *record* an attribution —
    binding a host identifier to a session so later calls resolve cheaply — may
    only do so when the evidence actually identified the editor. Learning the
    wrong mapping from a weak signal would be worse than not learning one.
    """

    session: "Session | None"
    how: str  # explicit | host | target | cwd | origin | ambiguous | none
    candidates: tuple["Session", ...] = ()


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
    #: The agent host's own session identifier. The only attribution signal
    #: that survives a host resetting the working directory between calls.
    host_session_id: str | None = None
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

    def current(
        self,
        cwd: Path | None = None,
        *,
        strict: bool = True,
        target: Path | str | None = None,
        host_session_id: str | None = None,
    ) -> Session | None:
        """The session this invocation belongs to.

        Resolution order, most reliable first:

        1. An explicit ``INVARIANT_SESSION_ID``.
        2. The **host's** session identifier, when the caller supplies one.
        3. The session whose worktree contains the **file being written to**.
        4. The session whose worktree contains the working directory.
        5. The single session registered at or above the working directory.

        Orders 2 and 3 exist because order 4 cannot be relied on. A host may
        reset the working directory to the project root between tool calls, and
        when it does, every concurrent session looks identical from the main
        checkout — so cwd-based attribution fails precisely when more than one
        session is live, which is the case the isolation rules exist for.

        Order 3 is what unblocks an edit. The host identifier is only present
        on a hook payload, and a session registered before it was recorded has
        none, so order 2 can be silently unavailable — and the documented
        remedy, ``INVARIANT_SESSION_ID``, is an environment variable that an
        Edit tool call has no way to carry. The destination path does not
        depend on the caller's environment at all.

        ``target`` ranks below the host identifier deliberately: who is writing
        beats where the bytes land, so a session writing into a peer's worktree
        stays attributed to the writer and remains a detectable violation
        rather than a silent reattribution.

        ``strict=False`` turns an unresolvable origin into ``None`` rather than
        an error. Ambiguity is a normal state for an advisory caller — a hook
        firing in the main checkout while several sessions are live — and one
        that must not be reported by failing, because a hook that fails denies
        the tool call that would have resolved it.
        """
        resolution = self.resolve(cwd, target=target, host_session_id=host_session_id)
        if resolution.how == "ambiguous" and strict:
            raise UsageError(ambiguity_message(list(resolution.candidates)))
        return resolution.session

    def resolve(
        self,
        cwd: Path | None = None,
        *,
        target: Path | str | None = None,
        host_session_id: str | None = None,
    ) -> "Resolution":
        """:meth:`current`, plus *how* it decided.

        Callers that only need the answer use ``current``. The ``how`` matters
        to one caller: a hook that has just learned which session a host
        identifier belongs to, which may only record that mapping when the
        evidence actually identified the editor.
        """
        explicit = os.environ.get("INVARIANT_SESSION_ID")
        if explicit:
            return Resolution(self.get(explicit), "explicit")

        matched = self.by_host_session(host_session_id)
        if matched is not None:
            return Resolution(matched, "host")

        # The edit's destination, not the caller's location. A host runs hooks
        # from the project root and resets the working directory between tool
        # calls, so with several sessions live, cwd names nobody — which is
        # exactly when the isolation rules need an answer. The path being
        # written to is inside one session's worktree and no other's.
        if target is not None:
            owner = self.by_worktree_containing(target)
            if owner is not None:
                return Resolution(owner, "target")

        cwd = Path(cwd or Path.cwd()).resolve()
        here = self.by_worktree_containing(cwd)
        if here is not None:
            return Resolution(here, "cwd")

        # No worktree contains the working directory. Fall back to where the
        # session was registered: a session that has not isolated yet has no
        # worktree at all, and one that just isolated leaves the caller
        # standing in the origin. Both need to stay addressable.
        candidates = self.origin_candidates(cwd)
        if len(candidates) == 1:
            return Resolution(candidates[0], "origin")
        if len(candidates) > 1:
            return Resolution(None, "ambiguous", tuple(candidates))
        return Resolution(None, "none")

    def by_worktree_containing(self, path: Path | str) -> Session | None:
        """The live session whose worktree contains ``path``, deepest first."""
        try:
            resolved = Path(path).resolve()
        except OSError:
            return None
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
            if resolved == worktree or worktree in resolved.parents:
                # Deepest match wins, so a nested worktree beats its parent.
                if len(str(worktree)) > best_len:
                    best, best_len = session, len(str(worktree))
        return best

    def origin_candidates(self, cwd: Path | None = None) -> list[Session]:
        """Live sessions registered at or above ``cwd``.

        Split out from :meth:`current` so that a caller which must not fail on
        ambiguity can still describe it.
        """
        cwd = Path(cwd or Path.cwd()).resolve()
        return [
            s
            for s in self.list()
            if s.state not in {"torn_down", "abandoned"}
            and s.origin
            and _contains(Path(s.origin), cwd)
        ]

    def by_host_session(self, host_session_id: str | None) -> Session | None:
        """Find the session belonging to a host session identifier.

        Matches a recorded identifier exactly, and otherwise falls back to the
        derived short form. The fallback is what makes this work for sessions
        registered before the identifier was recorded at all — no migration,
        no re-registration.
        """
        if not host_session_id:
            return None

        live = [s for s in self.list() if s.state not in {"torn_down", "abandoned"}]

        for session in live:
            if session.host_session_id and session.host_session_id == host_session_id:
                return session

        slug = host_slug(host_session_id)
        if not slug:
            return None
        for session in live:
            if session.id == slug:
                return session
        return None

    def adopt_host_session(self, session_id: str, host_session_id: str) -> Session:
        """Record the host identifier on an existing session."""
        session = self.require(session_id)
        session.host_session_id = host_session_id
        self._save(session)
        self._register(session)
        return session

    # -- lifecycle -----------------------------------------------------

    def start(
        self,
        name: str,
        session_id: str | None = None,
        base: str = "master",
        host_session_id: str | None = None,
    ) -> Session:
        host_session_id = host_session_id or os.environ.get("INVARIANT_HOST_SESSION_ID")
        # Prefer the host's own short form as the identifier, so a session
        # registered by hand and one seen by a hook agree without being told.
        session_id = (
            session_id
            or os.environ.get("INVARIANT_SESSION_ID")
            or host_slug(host_session_id)
            or uuid.uuid4().hex[:8]
        )
        if self.get(session_id) is not None:
            raise UsageError(f"session {session_id!r} already exists")
        session = Session(
            id=session_id,
            name=_slug(name),
            base=base,
            origin=str(git.toplevel(self.manifest.root) or self.manifest.root),
            host_session_id=host_session_id,
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
        root = git.toplevel(self.manifest.root) or self.manifest.root
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
            cwd=str(self.manifest.root),
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

    def teardown(
        self,
        session_id: str,
        remove_worktree: bool = True,
        remove_branch: bool = True,
    ) -> Session:
        """End the session: worktree gone, branch gone, registry entry gone.

        The branch is deleted with ``git branch -d``, never ``-D``. A safe
        delete refuses a branch holding commits the base does not have, and
        that refusal is the whole point: reaching teardown is supposed to mean
        the work is merged, so a branch that will not delete is evidence the
        merge did not do what it appeared to. Forcing it would destroy the only
        copy of that work and report success.
        """
        session = self.require(session_id)
        if remove_worktree and session.worktree and Path(session.worktree).exists():
            proc = subprocess.run(
                ["git", "worktree", "remove", session.worktree],
                cwd=str(self.manifest.root),
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise StateError(
                    f"could not remove {session.worktree}: {proc.stderr.strip()}"
                )

        if remove_branch and session.branch:
            proc = subprocess.run(
                ["git", "branch", "-d", session.branch],
                cwd=str(self.manifest.root),
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0 and "not found" not in proc.stderr.lower():
                raise StateError(
                    f"could not delete {session.branch}: {proc.stderr.strip()}\n"
                    f"A safe delete refuses a branch whose commits are not in "
                    f"the base — which means this session's work is not merged "
                    f"after all. Merge it, or abandon the session deliberately."
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
        # Registered with git AND present on disk. Git prunes worktree metadata
        # lazily, so a directory someone deleted by hand is still listed —
        # which is exactly the crashed-session case this has to catch. The
        # directory's existence is the signal that does not lag.
        registered = {str(w.path.resolve()) for w in git.worktrees(self.manifest.root)}
        live_worktrees = {p for p in registered if Path(p).is_dir()}

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


def ambiguity_message(candidates: list[Session]) -> str:
    """How to name a session when the working directory cannot.

    The remedies are ordered by what actually works from inside an agent. A
    session that has not isolated yet stands in the origin by definition, so
    "run from inside the worktree" is advice it cannot take — it is listed
    last, and the command that creates that worktree is spelled out instead.
    """
    lines = [
        f"{len(candidates)} live sessions were registered here and none of "
        "their worktrees contains this directory:"
    ]
    for s in candidates:
        where = s.worktree or "(not isolated yet)"
        lines.append(f"  {s.id}  {s.name}  [{s.state}]  {where}")
    lines.append("Name the one you mean:")
    lines.append("  INVARIANT_SESSION_ID=<id> invariant <command>   (or pass --id <id>)")
    lines.append("A session with no worktree gets one with:")
    lines.append("  invariant session isolate --execute --id <id>")
    return "\n".join(lines)


def host_slug(host_session_id: str | None) -> str:
    """The short form of a host session identifier.

    The LAST eight alphanumerics, which is the form this project's own tooling
    has used for its worktree names since before the library existed. Matching
    it means sessions created by that tooling resolve here with no migration.
    Taking the first eight instead would silently match nothing.
    """
    if not host_session_id:
        return ""
    alphanumeric = "".join(c for c in str(host_session_id) if c.isalnum())
    return alphanumeric[-8:] if alphanumeric else ""


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
