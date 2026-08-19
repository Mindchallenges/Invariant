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
from ..core.errors import GateFailure, StateError, TransitionError, UsageError
from ..core.fsm import Machine
from ..core.machines import load_session_machine
from ..core.rules import Evaluated, RuleContext, evaluate
from ..core.stats import Usage, WatermarkSource
from ..core.store import JsonStore

REGISTRY = "registry.json"

#: Context a gate needs to know which question it is being asked, derived from
#: the state being ENTERED. ``session.ff_merge_target`` and
#: ``session.push_barrier`` serve double duty — they also fire from the
#: pre-tool-use hook on raw ``git merge`` / ``git push`` commands — so without
#: these flags they would read an empty command string on a machine
#: transition and abstain, which is exactly the inertness AUD-05 describes.
_GATE_EXTRA_BY_TARGET: dict[str, dict[str, Any]] = {
    "merged": {"merging": True},
    "pushed": {"pushing": True},
}


#: Routes that IDENTIFIED the session, as against merely nominating one.
#:
#: Every member names the session from something the call itself carried: an
#: explicit id, the host identifier, the path the call is aimed at, the
#: worktree the brief names, or the worktree the call is standing in. ``origin``
#: is deliberately absent — it is the last-resort guess, "the only session
#: registered at or above here", and it is the route by which a FOREIGN
#: session's agent was recorded as evidence on a pipeline that never dispatched
#: it (BUG_028). A guess is fine for deciding what to *display*; it is not fine
#: for writing evidence a block-level gate will then read as fact.
PROVEN_ROUTES = frozenset({"explicit", "host", "target", "prompt", "cwd"})


@dataclass(frozen=True)
class Resolution:
    """Which session, and on what evidence.

    ``how`` is not decoration. A caller that wants to *record* an attribution —
    binding a host identifier to a session so later calls resolve cheaply, or
    writing pipeline evidence — may only do so when the evidence actually
    identified the session. Learning the wrong mapping from a weak signal would
    be worse than not learning one.
    """

    session: "Session | None"
    #: explicit | host | target | prompt | cwd | origin | ambiguous | none
    how: str
    candidates: tuple["Session", ...] = ()

    @property
    def proven(self) -> bool:
        """Did the call name this session, or did we pick the only one left?

        The distinction the evidence writers turn on. ``session is not None``
        is not the same question: ``origin`` returns a real session and is
        still a guess.
        """
        return self.session is not None and self.how in PROVEN_ROUTES


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
    #: Set when the host session that owned this one ended. The record stays —
    #: it is the evidence of what happened — but a session whose host is gone
    #: is nobody's current session and must stop competing for attribution.
    host_ended: bool = False
    base: str = "master"
    files: list[str] = field(default_factory=list)
    push_count: int = 0
    history: list[dict] = field(default_factory=list)
    overrides: list[dict] = field(default_factory=list)
    heartbeat: float = 0.0
    #: The usage watermark at registration (ADR-002 D3). The earlier reading
    #: the first state's spend is subtracted from; ``None`` where it could not
    #: be observed, which the report renders as such rather than as zero.
    #:
    #: Written and not yet read: the session-side breakdown is forward-looking,
    #: kept in step with the pipeline's rather than removed, and recorded as
    #: such in ADR-002's Consequences (BUG_016 F4).
    usage_at_start: dict | None = None
    #: The host session that baseline was counted against, so a later reader
    #: can tell whether it is commensurable with the readings that follow it.
    #: A rebind moves the binding and leaves this pointing at the old counter,
    #: which is exactly what makes the crossing visible instead of plausible.
    usage_at_start_host: str | None = None

    @property
    def is_stranded(self) -> bool:
        """Recorded a worktree that is no longer on disk.

        Touches the filesystem, deliberately: git prunes worktree metadata
        lazily, so ``git worktree list`` still names a directory somebody
        deleted by hand. The directory's existence is the signal that does not
        lag. A session that never isolated is not stranded — it has nothing to
        be missing.
        """
        if not self.worktree:
            return False
        try:
            return not Path(self.worktree).exists()
        except OSError:
            return False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Session":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})


class SessionDomain:
    def __init__(
        self,
        manifest,
        store: JsonStore | None = None,
        watermarks: WatermarkSource | None = None,
        clock=time.time,
    ) -> None:
        self.manifest = manifest
        self.store = store or JsonStore(manifest.state_root)
        self.machine: Machine = load_session_machine(manifest)
        #: Injected by the CLI (ADR-002 D2). ``None`` is normal: transitions
        #: then record ``usage: None``, which the report reads as "not
        #: observed" and never as "cost nothing".
        self.watermarks = watermarks
        self.clock = clock

    def _reading(self, host_session_id: str | None) -> tuple[dict | None, str | None]:
        """Read the watermark and the counter it came from, or give up quietly.

        A transition is the record that something happened. Letting a
        telemetry read fail it would make measurement a reason for the engine
        to refuse — the trade ADR-002 declines everywhere else too.

        The host session travels with the reading for the reason
        ``domains/pipeline`` records it too: a cumulative count is
        commensurable only with another taken from the same transcript, and
        :meth:`adopt_host_session` can move the binding underneath a session
        that has already recorded some.
        """
        if self.watermarks is None or not host_session_id:
            return None, None
        try:
            usage = self.watermarks.watermark(host_session_id)
        except Exception:  # noqa: BLE001 - see the docstring
            return None, None
        if not isinstance(usage, Usage):
            return None, None
        return usage.to_dict(), host_session_id

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
        3. The session whose worktree contains the **file the call names** —
           written to, or read.
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
        depend on the caller's environment at all. It is not only for edits:
        recording a docs read resolves through the same order, because a read
        names its path in the same payload key and for the same reason
        (BUG_010).

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
        candidates = self.origin_candidates(cwd, host_session_id=host_session_id)
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
            if not session.worktree or session.is_stranded:
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

    def live(self) -> list[Session]:
        return [s for s in self.list() if s.state not in {"torn_down", "abandoned"}]

    def attributable(self, host_session_id: str | None = None) -> list[Session]:
        """Live sessions that could plausibly be the caller.

        Three kinds are excluded, and each exclusion removes a candidate that
        was never a real answer:

        * **Stranded** — a worktree was recorded and is no longer on disk. The
          session cannot be worked in, and left in the list it pads every
          ambiguity report forever.
        * **Host ended** — the host session that owned it is gone. Whoever is
          asking, it is not them.
        * **Bound elsewhere** — owned by a different host session. Under a 1:1
          binding that is a definite no, not an unknown.

        A session with no worktree at all is *not* excluded: registering
        without isolating is legitimate — a device soak owns no code — and the
        binding is host-to-session, never host-to-worktree.
        """
        out = []
        for session in self.live():
            if session.host_ended or session.is_stranded:
                continue
            if (
                host_session_id
                and session.host_session_id
                and session.host_session_id != host_session_id
            ):
                continue
            out.append(session)
        return out

    def origin_candidates(
        self, cwd: Path | None = None, host_session_id: str | None = None
    ) -> list[Session]:
        """Attributable sessions registered at or above ``cwd``.

        Split out from :meth:`current` so that a caller which must not fail on
        ambiguity can still describe it.
        """
        cwd = Path(cwd or Path.cwd()).resolve()
        return [
            s
            for s in self.attributable(host_session_id)
            if s.origin and _contains(Path(s.origin), cwd)
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

        # A session whose host ended is not a match for anyone, including the
        # host id it used to carry: a new host session reusing the record would
        # inherit somebody else's pipeline and file set.
        live = [s for s in self.live() if not s.host_ended]

        for session in live:
            if session.host_session_id and session.host_session_id == host_session_id:
                return session

        slug = host_slug(host_session_id)
        if not slug:
            return None
        for session in live:
            # Only an *unbound* session may be claimed by the derived short
            # form. One already bound to a different host is a coincidence of
            # naming, not a match.
            if session.id == slug and not session.host_session_id:
                return session
        return None

    def adopt_host_session(
        self, session_id: str, host_session_id: str, force: bool = False
    ) -> Session:
        """Bind a host session to this session, 1:1 in both directions.

        Rebinding is refused rather than silently allowed. A session that
        changes hands keeps its pipeline, its evidence and its declared file
        set, so the new owner would inherit work it never did — and the old
        owner would keep editing a session that no longer answers to it.

        What a forced rebind deliberately does **not** do is touch the usage
        watermarks already recorded. They were counted against the outgoing
        host session's transcript and re-baselining them would invent readings
        nobody took. Each is stored with the counter it came from, so the one
        state that spans the rebind is declined by ``core/stats`` and named as
        unattributable instead of rendering as a measured spend (BUG_014).
        """
        session = self.require(session_id)

        if (
            not force
            and session.host_session_id
            and session.host_session_id != host_session_id
        ):
            raise UsageError(
                f"session {session.id} is already bound to another host "
                f"session. Rebinding would hand over its pipeline and file "
                f"set; pass force to do it deliberately."
            )

        if not force:
            other = self.by_host_session(host_session_id)
            if other is not None and other.id != session.id:
                raise UsageError(
                    f"this host session already owns session {other.id} "
                    f"({other.name}). One host session, one invariant session."
                )

        session.host_session_id = host_session_id
        self._save(session)
        self._register(session)
        return session

    def end_host_session(self, host_session_id: str) -> Session | None:
        """Note that the host session owning a session has ended.

        The session is *not* abandoned. It may hold unmerged work in a live
        worktree, and destroying that because a terminal closed would be the
        library doing the one thing it exists to prevent. It stops being an
        attribution candidate, which is the part that was causing harm, and
        ``doctor`` reports it as needing a decision.
        """
        session = self.by_host_session(host_session_id)
        if session is None:
            return None
        session.host_ended = True
        self._save(session)
        self._register(session)
        return session

    def reap_stranded(self, dry_run: bool = False) -> list[Session]:
        """Abandon sessions whose worktree *and* branch are both gone.

        Deliberately narrow. A missing worktree alone is not enough — a
        worktree can be removed while its branch still holds unmerged commits,
        and that work is recoverable. Once the branch is gone too there is
        nothing left to protect, and the record is kept as evidence rather than
        deleted.
        """
        existing = {
            b.strip().lstrip("+* ")
            for b in _branch_names(self.manifest.root)
        }
        reaped: list[Session] = []
        for session in self.live():
            if not session.is_stranded:
                continue
            if session.branch and session.branch in existing:
                continue
            reaped.append(session)
            if not dry_run:
                session.state = "abandoned"
                session.history.append(
                    {
                        "from": session.state,
                        "to": "abandoned",
                        "reason": "worktree and branch both gone",
                    }
                )
                self._save(session)
                self._deregister(session.id)
        return reaped

    # -- lifecycle -----------------------------------------------------

    def start(
        self,
        name: str,
        session_id: str | None = None,
        base: str = "master",
        host_session_id: str | None = None,
    ) -> Session:
        host_session_id = host_session_id or os.environ.get("INVARIANT_HOST_SESSION_ID")

        # Checked before the identifier is settled, so the refusal explains the
        # real problem. Deriving the id from the host means a second attempt
        # collides on the id too, and "session 'aaaa1111' already exists" tells
        # nobody that the rule they hit was one-session-per-host.
        #
        # One host session owns at most one LIVE invariant session. Two would
        # reintroduce the ambiguity the binding exists to remove, and the
        # second would be indistinguishable from the first for the rest of its
        # life. Owning a *worktree* stays optional — a device soak with no code
        # is a legitimate session — so this is a check on the binding, never on
        # isolation.
        if host_session_id:
            existing = self.by_host_session(host_session_id)
            if existing is not None:
                raise UsageError(
                    f"this host session already owns session {existing.id} "
                    f"({existing.name}, {existing.state}).\n"
                    f"  Finish it first: invariant session teardown --id "
                    f"{existing.id}\n"
                    f"  Or end it deliberately: invariant session transition "
                    f"abandoned --id {existing.id} --force --reason '<why>'"
                )

        # Prefer the host's own short form as the identifier, so a session
        # registered by hand and one seen by a hook agree without being told.
        requested = session_id or os.environ.get("INVARIANT_SESSION_ID")
        if requested:
            if self.get(requested) is not None:
                raise UsageError(f"session {requested!r} already exists")
            session_id = requested
        else:
            # A DERIVED identifier that is taken is not an error: a host
            # session that finished one session and starts another derives the
            # same slug from the same host id, and the record of the finished
            # one is deliberately kept. Falling back keeps that legal.
            session_id = host_slug(host_session_id) or uuid.uuid4().hex[:8]
            if self.get(session_id) is not None:
                session_id = uuid.uuid4().hex[:8]

        baseline, baseline_host = self._reading(host_session_id)
        session = Session(
            id=session_id,
            name=_slug(name),
            base=base,
            origin=str(git.toplevel(self.manifest.root) or self.manifest.root),
            host_session_id=host_session_id,
            heartbeat=self.clock(),
            usage_at_start=baseline,
            usage_at_start_host=baseline_host,
        )
        self._save(session)
        self._register(session)
        return session

    def isolation_plan(self, session: Session) -> dict[str, str]:
        """Where this session's worktree and branch would go.

        Derived from the session identifier so that two sessions never collide.
        Placed beside the repository by default, so that the worktree is not
        itself a tracked path; ``session.worktree_dir`` moves it, including to a
        directory inside the repository, for hosts that trust one location and
        confirm every other.
        """
        root = git.toplevel(self.manifest.root) or self.manifest.root
        prefix = self.manifest.get("session.worktree_prefix") or f"{root.name}-wt"
        parent = root.parent
        configured = self.manifest.get("session.worktree_dir")
        if configured:
            configured = Path(str(configured)).expanduser()
            parent = configured if configured.is_absolute() else root / configured
        return {
            "worktree": str(parent / f"{prefix}-{session.id}"),
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
        cwd: Path | None = None,
    ) -> Session:
        session = self.require(session_id)
        self._advance(session, target, reason=reason, force=force, cwd=cwd)
        return session

    def add_files(self, session_id: str, paths: list[str]) -> Session:
        session = self.require(session_id)
        for path in paths:
            if path not in session.files:
                session.files.append(path)
        self._save(session)
        return session

    def record_push(
        self,
        session_id: str,
        cwd: Path | None = None,
        force: bool = False,
        reason: str | None = None,
    ) -> Session:
        session = self.require(session_id)
        # Advance FIRST, then count. `pushed`'s entry gates include the push
        # barrier, and incrementing before evaluating would make the very push
        # being recorded read as a second one.
        self._advance(session, "pushed", reason=reason or "push recorded",
                      force=force, cwd=cwd)
        session.push_count += 1
        self._save(session)
        self._register(session)
        return session

    def teardown(
        self,
        session_id: str,
        remove_worktree: bool = True,
        remove_branch: bool = True,
        cwd: Path | str | None = None,
        force: bool = False,
    ) -> Session:
        """End the session: worktree gone, branch gone, registry entry gone.

        The branch is deleted with ``git branch -d``, never ``-D``. A safe
        delete refuses a branch holding commits the base does not have, and
        that refusal is the whole point: reaching teardown is supposed to mean
        the work is merged, so a branch that will not delete is evidence the
        merge did not do what it appeared to. Forcing it would destroy the only
        copy of that work and report success.

        The third precondition is not a git question, and it is the one that
        was missing (BUG_009): git will happily remove a directory another
        system is standing in. When an agent host is standing in it the result
        is not an inconvenience — that host's own worktree isolation refuses
        every subsequent shell call and orders the caller back into the path
        just deleted, and the commands that would repair it sit behind that
        same refusal. The deadlock is self-sealing, which is why this refuses
        rather than warns: nobody scrolls past a wedged session back into a
        working one.

        The check is a **conjunction**, and dropping either half breaks it in
        a different direction:

        * **The binding is live** — ``host_session_id`` set and ``host_ended``
          false, which is what `docs/architecture/state_model.md` "Host
          binding" defines as live, ``SessionEnd`` being what marks it spent.
          Alone this is far too broad: a session torn down the documented way
          is bound to the very host session running the teardown, so every
          healthy ending carries a live binding.
        * **The worktree is occupied** — the caller is standing inside it.
          Alone this is too broad the other way: with the binding already
          spent, a stranded shell recovers with one ``cd``. Occupancy is
          measured from the caller's own working directory because that is the
          only evidence available; the binding is a scalar with no notion of
          where its host stands (BUG_010), so a *different* host wedged in this
          worktree remains invisible here.
        """
        session = self.require(session_id)

        # Read BEFORE anything is removed, which is the entire point. Teardown
        # reads the binding afterwards too — for the transition's telemetry
        # watermark, and again when the record is serialised — and neither of
        # those is in time to be a precondition on the removal.
        bound_to = session.host_session_id
        binding_is_spent = session.host_ended
        binding_is_live = bool(bound_to) and not binding_is_spent

        if (
            remove_worktree
            and not force
            and binding_is_live
            and session.worktree
            and _contains(Path(session.worktree), _resolved(cwd))
        ):
            raise UsageError(self._occupied_worktree_message(session))

        # Run git from the MAIN checkout, not from wherever the manifest was
        # loaded. Those are the same directory in the ordinary case and are
        # not when teardown is invoked from inside the session's own worktree:
        # `git worktree remove` then deletes the directory the *next* git call
        # is standing in, and that call fails with an unrelated ENOENT after
        # the removal has already happened — worktree gone, branch alive, no
        # transition recorded. Reachable deliberately through ``force``, so it
        # has to be correct there rather than merely refused everywhere else.
        repo = str(git.main_checkout(self.manifest.root) or self.manifest.root)

        if remove_worktree and session.worktree and Path(session.worktree).exists():
            proc = subprocess.run(
                ["git", "worktree", "remove", session.worktree],
                cwd=repo,
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
                cwd=repo,
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

    def _occupied_worktree_message(self, session: Session) -> str:
        """How to get out of the way, spelled as commands.

        A caller who hits this cannot read the source from inside the deadlock
        it is describing, and by the time the deadlock exists it cannot run
        anything at all. So the message carries the way out rather than a
        diagnosis of what went wrong.
        """
        elsewhere = (
            git.main_checkout(self.manifest.root)
            or session.origin
            or "<the main checkout>"
        )
        return (
            f"session {session.id} is bound to a LIVE host session, and this "
            f"command is running inside the worktree it would remove:\n"
            f"  {session.worktree}\n"
            f"Removing it would strand that host in a directory that no longer "
            f"exists. Its own worktree isolation then refuses every shell call "
            f"and orders the caller back into the removed path, and the "
            f"commands that would repair that sit behind the same refusal — "
            f"there is no in-band recovery, which is why this is refused "
            f"rather than warned about.\n"
            f"  Step out of it first, then tear down:\n"
            f"    cd {elsewhere} && invariant session teardown --id "
            f"{session.id}\n"
            f"  The binding is spent once the host session ends (SessionEnd); "
            f"a spent binding, or none at all, tears down from anywhere.\n"
            f"  Deliberately, knowing the host has already moved elsewhere: "
            f"add --force."
        )

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

    def _pipeline_record(self, session_id: str) -> dict | None:
        """The session's current pipeline, read for gate evaluation.

        Read from the shared store rather than through ``PipelineDomain`` —
        the gates want the record, not the operations, and constructing the
        sibling domain here would couple their lifecycles for one dict.
        """
        raw = self.store.read(f"pipelines/{session_id}.json", {})
        current = raw.get("current") if isinstance(raw, dict) else None
        return current if isinstance(current, dict) else None

    def pending_gates(
        self,
        session: Session,
        target: str,
        cwd: Path | None = None,
        extra: dict | None = None,
    ) -> list[Evaluated]:
        """The machine's gates for ``state -> target``, evaluated, no advance.

        Exit gates of the state being left plus entry gates of the one being
        entered — the same shape as ``PipelineDomain.gates_for``. Public so
        that a command with an irreversible step (`session merge`, `session
        push`) can ask BEFORE running it; the transition afterwards evaluates
        the same list, so the two cannot drift apart.
        """
        # A state the machine does not know has no readable gate list. That is
        # the forced-recovery case — a hand-repaired or legacy record — and
        # the structural check has already refused it or been deliberately
        # forced past; failing HERE would wedge the one route out (AUD-01's
        # class of harm). The known side's gates still run.
        gate_ids: list[str] = []
        for state_id, which in ((session.state, "exit_gates"), (target, "entry_gates")):
            try:
                declared = getattr(self.machine.state(state_id), which)
            except UsageError:
                continue
            gate_ids += [g for g in declared if g not in gate_ids]
        if not gate_ids:
            return []
        merged_extra = dict(_GATE_EXTRA_BY_TARGET.get(target, {}))
        merged_extra.update(extra or {})
        ctx = RuleContext(
            manifest=self.manifest,
            cwd=Path(cwd or Path.cwd()),
            session=session.to_dict(),
            pipeline=self._pipeline_record(session.id),
            extra=merged_extra,
        )
        return evaluate(gate_ids, ctx)

    def _advance(
        self,
        session: Session,
        target: str,
        reason: str | None = None,
        force: bool = False,
        cwd: Path | None = None,
        extra: dict | None = None,
    ) -> list[Evaluated]:
        structural_override = False
        if session.state != target:
            try:
                self.machine.require_edge(session.state, target)
            except TransitionError:
                if not force:
                    raise
                structural_override = True

        # The machine's own gates, on EVERY route. They used to be read by
        # exactly two hardcoded call sites (`session review`, `session
        # merge`), so the generic `session transition` walked the lifecycle
        # with zero gates and `session.named_after_isolating` was evaluated by
        # no code path at all (AUD-05). ``PipelineDomain.advance`` has always
        # done it this way; this is the same shape.
        verdicts = self.pending_gates(session, target, cwd=cwd, extra=extra)
        blocking = [v for v in verdicts if v.blocks]
        if blocking and not force:
            raise GateFailure(
                f"{len(blocking)} gate(s) block {session.state!r} -> {target!r}",
                verdicts=verdicts,
            )

        if force and (blocking or structural_override):
            # Recorded, never silent: an override that leaves no trace is
            # indistinguishable from the gate never having run.
            session.overrides.append(
                {
                    "from": session.state,
                    "to": target,
                    "reason": reason or "forced",
                    "structural": structural_override,
                    "gates": [v.rule_id for v in blocking],
                }
            )
        # ADR-002 D3. The anchor is the heartbeat, which this domain already
        # stamped on every transition — so `elapsed` costs no new stored field
        # and no new clock time, and the entry itself carries a duration and a
        # pair of counters rather than a time.
        now = self.clock()
        usage, usage_host = self._reading(session.host_session_id)
        session.history.append(
            {
                "from": session.state,
                "to": target,
                "reason": reason,
                "elapsed": max(0.0, now - session.heartbeat) if session.heartbeat else None,
                "usage": usage,
                "usage_host": usage_host,
            }
        )
        session.state = target
        session.heartbeat = now
        self._save(session)
        self._register(session)
        return verdicts

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
    lines.append("  invariant <command> --id <id>")
    lines.append("  INVARIANT_SESSION_ID=<id> invariant <command>")
    lines.append(
        "  (both name a session for an `invariant` command. Neither reaches a"
    )
    lines.append(
        "   HOOK: it runs as its own process, so a variable exported in front"
    )
    lines.append(
        "   of a tool call never gets there. A hook identifies a session by the"
    )
    lines.append(
        "   host session bound to it, or by the path an edit is aimed at.)"
    )
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


def _branch_names(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _resolved(cwd: Path | str | None) -> Path:
    """The caller's location, defaulting to where this process is standing.

    Defaulted rather than required so that every existing caller keeps
    working, and resolved here so that a symlinked path and the real one are
    the same directory — which is what a host handing over ``/tmp`` on macOS
    actually passes.
    """
    try:
        return Path(cwd or Path.cwd()).resolve()
    except OSError:
        # The working directory has already been removed. Nothing can be
        # inside a path that cannot be named, and refusing to tear down
        # because the location is unreadable would block the cleanup that
        # follows exactly this situation.
        return Path("/")


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
