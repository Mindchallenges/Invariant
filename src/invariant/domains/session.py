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
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import git
from ..core import plan as plan_reader
from ..core.errors import GateFailure, StateError, TransitionError, UsageError
from ..core.fsm import Machine
from ..core.machines import ABANDON_ROLE, load_session_machine
from ..core.rules import Evaluated, RuleContext, evaluate, get_rule
from ..core.stats import Usage, WatermarkSource
from ..core.store import JsonStore, PreservesUnknownKeys

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
class Session(PreservesUnknownKeys):
    """One session's whole record.

    ``to_dict`` and ``from_dict`` come from `core.store.PreservesUnknownKeys`
    and carry the round trip in BOTH directions: a field written before it
    existed loads at its default, and a field this build has never heard of is
    written back rather than deleted (BUG_061).
    """

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
    #:
    #: Spent, not final. A spent binding is exactly the state
    #: :meth:`SessionDomain.resume` picks up from, and replacing it is not the
    #: hand-over ADR-001 D19 forbids: nobody is on the other end to have a
    #: pipeline taken away from them.
    host_ended: bool = False
    #: How many times a host session picked this one up. The count of
    #: :meth:`SessionDomain.resume` calls that succeeded, which is how many
    #: agent-host sessions this one has outlived.
    resumed_count: int = 0
    #: Every host session this one was bound to BEFORE the current one, oldest
    #: first. Provenance, so that a record spanning four Claude sessions can
    #: say which four.
    #:
    #: The *current* binding is ``host_session_id`` and is deliberately not
    #: repeated here: one fact in two places makes "which one is live" a
    #: question about list position. A legacy record seeds this list from the
    #: scalar the first time the binding moves, so nothing has to be migrated
    #: for a session to acquire its own history.
    hosts: list[str] = field(default_factory=list)
    base: str = "master"
    files: list[str] = field(default_factory=list)
    #: The deliverable set this session owes, in the order it was recorded.
    #: ``[{"n": int, "title": str, "done": bool, "done_at": float | None}]``.
    #:
    #: A field rather than a file. It is per-session, it is read on the same
    #: turn as the state it qualifies, and it is small — so a second document
    #: would buy a second thing to keep consistent and a second thing to lose.
    #: ``core/plan`` holds every question asked of it; this domain holds the
    #: mutations.
    plan: list[dict] = field(default_factory=list)
    #: When the plan last MOVED. Deliberately **not** ``heartbeat``: that field
    #: anchors the per-transition ``elapsed`` telemetry (ADR-002 D3), so
    #: stamping it on a checklist edit would silently shorten the next
    #: transition's recorded duration — an edit to the record of what a state
    #: cost, made by an operation that is not a transition at all.
    plan_touched: float = 0.0
    push_count: int = 0
    history: list[dict] = field(default_factory=list)
    #: Overrides that have been SPENT — a forced transition, a structural edge
    #: walked deliberately, or a one-shot token consumed at a hook. The audit
    #: trail. Silent is not an option the API has.
    overrides: list[dict] = field(default_factory=list)
    #: Overrides the operator has GRANTED and nothing has spent yet: one entry
    #: per rule, carrying the reason and how many calls are left on it.
    #:
    #: This is the appeal that reaches the delivery surface. Every gate the
    #: CLI evaluates could already be passed with `--force --reason`, and the
    #: gates that actually stop an agent are not evaluated by the CLI — twelve
    #: rules fire from hooks, where a blocking verdict was final: no flag, no
    #: marker, no prompt, no recorded exception (BUG_049). A grant here is
    #: consumed by the next evaluation of that rule at a hook, and consuming it
    #: moves a record into `overrides` above.
    pending_overrides: list[dict] = field(default_factory=list)
    #: Enforcement held for an INTERVAL rather than for a call, or ``None``.
    #:
    #: The other half of the position. `pending_overrides` above answers "let
    #: this call through"; this answers "stop asking for the next while", and
    #: they are different wants even though they share a seam. Carries the
    #: reason, the scope that ends it, and the ids explicitly folded in — see
    #: `core.rules.standdown_in_force`, which derives whether it still holds
    #: rather than trusting a flag somebody has to remember to clear.
    standdown: dict | None = None
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
    #: Keys a document carried that this build does not declare, kept verbatim
    #: so that saving the record cannot delete them. Never written under this
    #: name — `to_dict` merges it back at the top level. See
    #: `core.store.PreservesUnknownKeys`.
    _extra: dict[str, Any] = field(default_factory=dict)

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

    # -- the plan, read through the one module that owns the questions --
    #
    # Four one-line properties rather than four call sites doing the
    # arithmetic. `plan_complete` is the reason: an empty plan is *unplanned*
    # rather than finished, a later increment switches an entire mode on that,
    # and the obvious inline spelling (`done == total`) gets it backwards for
    # exactly the case that matters.

    @property
    def plan_total(self) -> int:
        return plan_reader.progress(self.plan).total

    @property
    def plan_open(self) -> list[dict]:
        """The deliverables still owed, in the order they were recorded."""
        return list(plan_reader.progress(self.plan).remaining)

    @property
    def plan_done_count(self) -> int:
        return plan_reader.progress(self.plan).done

    @property
    def plan_complete(self) -> bool:
        """Every recorded deliverable is done, and there is at least one."""
        return plan_reader.progress(self.plan).complete


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

    def resolve_name(self, query: str) -> tuple[Session | None, list[Session]]:
        """Find a session by the thing a human would type.

        A ladder, first hit wins, and each rung is narrower than the one below
        so that an exact answer is never beaten by a fuzzy one:

        1. an exact session **id**;
        2. an exact **name** — which carries the id, so it is unique;
        3. a **bare slug** — the name without its id, which is what somebody
           reading a task list would type;
        4. a unique **name prefix**.

        Rungs 2 to 4 are :func:`name_rank`'s, which is where their order is
        written down. ``domains/stats`` climbs the same three over telemetry
        records, and a second spelling of this precedence would drift without
        either copy ever looking wrong.

        Returns ``(session, [])`` on a hit, ``(None, candidates)`` when a rung
        matched more than one, and ``(None, [])`` when nothing matched.
        Ambiguity is handed back rather than raised because this is also the
        lookup a *display* caller uses, and it must be able to describe the
        situation without failing; the caller that needs a refusal builds one
        from :func:`name_ambiguity_message`.

        Rungs 2 to 4 are scoped to :meth:`live`, because a torn-down session is
        not something to resume by name. Rung 1 is **not**, deliberately: an id
        that names a finished session should be told it is finished, and "no
        session 'abcd1234'" is a worse answer than "that one was torn down".
        """
        if not query:
            return None, []

        by_id = self.get(query)
        if by_id is not None:
            return by_id, []

        ranked = [(name_rank(s.name, s.id, query), s) for s in self.live()]
        for rung in (RANK_FULL_NAME, RANK_BARE_SLUG, RANK_NAME_PREFIX):
            hits = [s for rank, s in ranked if rank == rung]
            if len(hits) == 1:
                return hits[0], []
            if len(hits) > 1:
                return None, hits
        return None, []

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

        # `and not session.host_ended` narrows this by one clause, and the
        # clause is the whole session-outlives-its-host feature. A binding
        # whose host has ENDED is spent: there is nobody on the other end to
        # have a pipeline, an evidence trail or a file set taken away from
        # them, so replacing it is not the hand-over this refusal exists to
        # prevent. Only a LIVE binding is.
        if (
            not force
            and session.host_session_id
            and not session.host_ended
            and session.host_session_id != host_session_id
        ):
            raise UsageError(
                f"session {session.id} is bound to a LIVE host session. "
                f"Rebinding would hand over its pipeline and file set to an "
                f"owner that never did the work; pass force to do it "
                f"deliberately.\n"
                f"  A binding whose host has ENDED is spent, and picking the "
                f"session up again is not that hand-over:\n"
                f"    invariant session resume {session.name}"
            )

        if not force:
            other = self.by_host_session(host_session_id)
            if other is not None and other.id != session.id:
                raise UsageError(
                    f"this host session already owns session {other.id} "
                    f"({other.name}). One host session, one invariant session."
                )

        return self._bind_host(session, host_session_id)

    def _bind_host(self, session: Session, host_session_id: str) -> Session:
        """Write the binding. The one place any route does it.

        Extracted so that :meth:`adopt_host_session` and :meth:`resume` cannot
        disagree about what binding a host *means*, because two of the three
        things it does are easy to leave out and expensive to leave out:

        * **``host_ended`` is cleared.** :meth:`by_host_session` filters on it,
          so a session whose binding was replaced while the flag was still set
          resolves to nobody for every subsequent hook in the new host — the
          session would be picked up and instantly unaddressable.
        * **The outgoing id is kept.** A record spanning several host sessions
          should be able to say which ones. The list seeds itself from the
          scalar the first time the binding moves, so a legacy record acquires
          its own history without a migration, and an id already in the list is
          not appended twice: how *many* times the session was picked up is
          ``resumed_count``'s question, not this list's.
        """
        outgoing = session.host_session_id
        if outgoing and outgoing != host_session_id and outgoing not in session.hosts:
            session.hosts.append(outgoing)
        session.host_session_id = host_session_id
        session.host_ended = False
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

    # -- outliving a host session --------------------------------------

    def resumable(self, cwd: Path | None = None) -> list[Session]:
        """Sessions waiting to be picked up by a new host session.

        The **exact complement** of :meth:`attributable`'s host-ended
        exclusion. That method answers "who could be calling right now", and a
        session whose host is gone is definitely not the caller; this one
        answers "what did somebody put down here", and only a session whose
        host is gone qualifies. The two lists are disjoint by construction and
        that is the point of writing this from :meth:`live` rather than
        widening ``attributable`` with a flag: widening it would make every
        caller of the attribution path — the hooks that decide whether an edit
        is allowed — start seeing candidates that are provably not them, which
        is the ambiguity the 1:1 binding exists to remove.

        The other two filters remove candidates that are not really on offer.
        A **stranded** session has no worktree to work in. A session registered
        somewhere else is somebody else's; ``origin`` is compared the same way
        :meth:`origin_candidates` compares it, so "here" means the same thing
        in both.

        Ordered by when the session last MOVED — the later of the plan's last
        edit and the last transition — because the useful default when a list
        is offered is the thing most recently put down.
        """
        cwd = Path(cwd or Path.cwd()).resolve()
        out = [
            s
            for s in self.live()
            if s.host_ended
            and not s.is_stranded
            and s.origin
            and _contains(Path(s.origin), cwd)
        ]
        out.sort(key=lambda s: max(s.plan_touched, s.heartbeat), reverse=True)
        return out

    def resume(
        self,
        session_id: str,
        host_session_id: str | None = None,
        force: bool = False,
    ) -> Session:
        """Bind a new host session to work that is already under way.

        The operation this whole increment exists for: a plan needing several
        agent-host sessions runs on ONE invariant session, and merges once, at
        the end. Without it the second host session starts a second session,
        the plan forks, and two branches hold half the work each.

        Resumption is never automatic. A host session that finds a resumable
        session has not thereby claimed it — picking up somebody's unfinished
        work is a decision with consequences (its pipeline state, its declared
        file set and its branch all come along), so it is a command somebody
        runs.

        What it refuses, and why each refusal is a refusal rather than a
        warning:

        * **Torn down or abandoned.** There is nothing to resume. The record is
          kept as evidence and the work, if any, is elsewhere.
        * **Merged or pushed**, without ``force``. The deliverable set already
          landed. Accumulating new commits onto a merged branch puts them
          behind a spent ``push_barrier``, so they would be finished work with
          no route out of the session that holds them.
        * **Stranded.** The worktree is gone. The branch may still hold the
          commits, so this says so rather than offering a cleanup that would
          discard them.
        * **Bound to a live host.** That is the hand-over ADR-001 D19 forbids,
          and resume must not become a back door to it.
        * **The resuming host already owns a live session.** One host session,
          one invariant session — the same rule :meth:`start` enforces, in the
          same shape, because arriving at it by a different command does not
          make it a different rule.
        """
        host_session_id = host_session_id or os.environ.get("INVARIANT_HOST_SESSION_ID")
        session = self.require(session_id)

        if session.state in {"torn_down", "abandoned"}:
            raise UsageError(
                f"session {session.name} is {session.state}; there is nothing "
                f"to resume — start a new one with "
                f"'invariant session start <task-name>'"
            )
        if session.state in {"merged", "pushed"} and not force:
            raise UsageError(
                f"session {session.name} is {session.state}: its deliverable "
                f"set already landed.\n"
                f"  Accumulating onto a merged branch puts those commits "
                f"behind a spent push_barrier, with no route out of this "
                f"session.\n"
                f"  Start a new session for the next piece of work, or pass "
                f"force if this really is the same one."
            )
        if session.is_stranded:
            raise UsageError(
                f"session {session.name} recorded a worktree that is no longer "
                f"on disk:\n"
                f"  {session.worktree}\n"
                f"  The branch {session.branch or '(none recorded)'} may still "
                f"hold the work; 'invariant session reap' abandons the session "
                f"only once the branch is gone too."
            )
        if (
            not force
            and session.host_session_id
            and not session.host_ended
            and session.host_session_id != host_session_id
        ):
            raise UsageError(
                f"session {session.name} is bound to a LIVE host session. "
                f"Resuming it would hand its pipeline and file set to an "
                f"owner that never did the work — the hand-over the binding "
                f"refuses everywhere else, and this must not be a back door "
                f"to it. Wait for that host to end, or pass force to take it "
                f"over deliberately."
            )
        if not force and host_session_id:
            other = self.by_host_session(host_session_id)
            if other is not None and other.id != session.id:
                raise UsageError(
                    f"this host session already owns session {other.id} "
                    f"({other.name}, {other.state}).\n"
                    f"  Finish it first: invariant session teardown --id "
                    f"{other.id}\n"
                    f"  Or end it deliberately: invariant session transition "
                    f"abandoned --id {other.id} --force --reason '<why>'"
                )

        previous_host = session.host_session_id
        if host_session_id:
            self._bind_host(session, host_session_id)
        # No host identifier to bind to: the record keeps its spent binding
        # untouched. Clearing `host_ended` with a stale id would hand the
        # session straight back to the host that ended, through
        # `by_host_session`, and put it back into `attributable` for everyone
        # else — the widening this method is careful not to do.

        session.resumed_count += 1
        # Recorded AFTER the rebind, so the row's `usage` watermark is read
        # from the NEW host — the seam where the counter changes identity sits
        # on a row that says it is a seam (ADR-002 D3, BUG_014).
        now = self._note(
            session,
            "resumed",
            extra={
                "resumed": True,
                "host": host_session_id,
                "previous_host": previous_host,
            },
        )
        session.plan_touched = now
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
        force: bool = False,
    ) -> Session:
        host_session_id = host_session_id or os.environ.get("INVARIANT_HOST_SESSION_ID")
        slug = _slug(name)

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

        origin = str(git.toplevel(self.manifest.root) or self.manifest.root)

        # The failure this whole feature exists to prevent: a plan silently
        # forking into two sessions because the second Claude session started
        # a fresh one under the same task name. A warning printed above a
        # successful `start` is prose-as-control-flow — it does not stop the
        # fork, and by the time anyone reads it there are two branches holding
        # half the work each. So it refuses, in the same shape as the
        # one-session-per-host refusal above: name the address, say what state
        # it is in and how far along, give the command that picks it up, and
        # offer the escape for a genuinely separate session.
        #
        # Scoped to RESUMABLE sessions, not to live ones. A name in use by a
        # session somebody is currently working in is not a plan waiting to be
        # picked up, and refusing there would block a second concurrent
        # session on a related task for no reason.
        if not force:
            for candidate in self.resumable(cwd=Path(origin)):
                if candidate.name == qualified_name(slug, candidate.id):
                    raise UsageError(self._would_fork_a_plan_message(candidate))

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
            # Qualified HERE, after the identifier is settled — including on
            # the uuid4 fallback path, where until this line there is no id to
            # append.
            name=qualified_name(slug, session_id),
            base=base,
            origin=origin,
            host_session_id=host_session_id,
            heartbeat=self.clock(),
            usage_at_start=baseline,
            usage_at_start_host=baseline_host,
        )
        self._save(session)
        self._register(session)
        return session

    def _would_fork_a_plan_message(self, candidate: Session) -> str:
        """What to do instead of starting a second session for one plan."""
        lines = [
            (
                "a session for this task is already open and waiting for a "
                "host session:"
            ),
            (
                f"  {candidate.name}  [{candidate.state}]  "
                f"{plan_reader.progress(candidate.plan).describe()}"
            ),
        ]
        lines += [f"    {line}" for line in plan_reader.render_lines(candidate.plan)]
        lines += [
            (
                "Its plan is the one this work belongs to. Pick it up rather "
                "than forking it:"
            ),
            f"  invariant session resume {candidate.name}",
            (
                "If this really is a separate piece of work that happens to "
                "share a name, add --force."
            ),
        ]
        return "\n".join(lines)

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

    #: Longer than any path this could name. macOS caps a path at 1024 bytes
    #: and Linux at 4096, so a string past the larger bound is not a path that
    #: was mistyped — it is not a path at all.
    _PATH_MAX = 4096

    def add_files(self, session_id: str, paths: list[str]) -> Session:
        session = self.require(session_id)
        for path in paths:
            self._reject_if_not_a_path(path)
            if path not in session.files:
                session.files.append(path)
        self._save(session)
        return session

    def _reject_if_not_a_path(self, path: str) -> None:
        """Refuse a string that cannot name a file, before it is written down.

        A declared set is only useful if every entry can be opened, and an
        entry that cannot is worse than a missing one: `privacy.clean` unions
        this list in at the review boundary, fails to scan the impossible
        entry, and blocks the merge on a path nobody can delete — the CLI
        offering `add` and, until now, no way back.

        The observed cause was shell word-splitting: `session files add $FILES`
        unquoted under zsh, which does NOT split on whitespace, so 48 paths
        arrived as ONE argument. That is why the length bound is the check and
        a space is not — a filename may legitimately contain spaces, while
        nothing on any supported platform may exceed PATH_MAX.
        """
        if not path or not path.strip():
            raise UsageError(
                "a declared path cannot be empty. An empty entry scans as "
                "nothing and blocks as something."
            )
        if "\n" in path or "\r" in path:
            raise UsageError(
                f"a declared path cannot contain a newline (got {len(path)} "
                "characters). This is usually a command substitution that "
                "produced a list where one path was expected."
            )
        if len(path) > self._PATH_MAX:
            raise UsageError(
                f"a declared path cannot exceed {self._PATH_MAX} characters "
                f"(got {len(path)}). Under zsh an unquoted $VAR does NOT "
                "word-split, so a whole file list arrives as a single "
                "argument. Quote the expansion, or pass the paths as "
                "separate arguments: invariant session files add a.py b.py"
            )

    # -- the deliverables plan -----------------------------------------
    #
    # `plan_reopen` and `plan_remove` exist from day one, and that is a
    # decision rather than completeness for its own sake. `session files add`
    # shipped without a `remove`, and the result was a declared set with no way
    # back: a path nobody could delete blocked the review boundary until the
    # missing command was written (BUG_057). A `done` that cannot be undone is
    # that bug one field over, against a gate that blocks a turn.

    def plan_add(self, session_id: str, title: str) -> Session:
        """Record a deliverable this session owes."""
        session = self.require(session_id)
        self._reject_if_not_a_deliverable(title)
        session.plan.append(
            {
                "n": plan_reader.next_n(session.plan),
                "title": title.strip(),
                "done": False,
                "done_at": None,
            }
        )
        self._touch_plan(session, self.clock())
        return session

    def plan_done(self, session_id: str, n: int) -> Session:
        """Mark deliverable ``n`` done."""
        session = self.require(session_id)
        entry = self._plan_entry(session, n)
        now = self.clock()
        # The moment it was FIRST finished. Marking a done entry done again is
        # not an event, and re-stamping would quietly move the record of when
        # the work actually landed.
        if not entry.get("done"):
            entry["done_at"] = now
        entry["done"] = True
        self._touch_plan(session, now)
        return session

    def plan_reopen(self, session_id: str, n: int) -> Session:
        """Undo a `plan done`. The way back from marking the wrong number."""
        session = self.require(session_id)
        entry = self._plan_entry(session, n)
        entry["done"] = False
        entry["done_at"] = None
        self._touch_plan(session, self.clock())
        return session

    def plan_remove(self, session_id: str, n: int) -> Session:
        """Drop a deliverable. Its number is retired, never reissued."""
        session = self.require(session_id)
        self._plan_entry(session, n)
        session.plan = [e for e in session.plan if e.get("n") != n]
        self._touch_plan(session, self.clock())
        return session

    def _plan_entry(self, session: Session, n: int) -> dict:
        """The RAW stored entry, so a caller can change it.

        Deliberately not routed through ``core.plan.entries``, which hands back
        copies: a mutation applied to a copy saves nothing and reports success.
        """
        for entry in session.plan:
            if isinstance(entry, dict) and entry.get("n") == n:
                return entry
        known = ", ".join(str(e["n"]) for e in plan_reader.entries(session.plan))
        raise UsageError(
            f"no deliverable {n} in session {session.name}; this session has "
            f"{known or 'no deliverables recorded'}"
        )

    def _touch_plan(self, session: Session, now: float) -> None:
        """Stamp the plan's own clock and save. Never the heartbeat.

        ``heartbeat`` anchors the per-transition ``elapsed`` telemetry, so
        stamping it here would shorten the next transition's recorded duration
        by however long the checklist edit happened to be after the last one —
        a plan edit silently rewriting the record of what a *state* cost.

        ``now`` is passed in rather than read here because each mutation must
        take exactly one reading: two calls to a clock are two different times,
        and an entry whose ``done_at`` and whose ``plan_touched`` disagree by a
        few microseconds is a record with two answers to one question.
        """
        session.plan_touched = now
        self._save(session)

    def _reject_if_not_a_deliverable(self, title: str) -> None:
        """Refuse a string that cannot name a deliverable.

        The same three guards as :meth:`_reject_if_not_a_path`, and the same
        bound, because the failure they catch belongs to the SHELL rather than
        to the field: `session plan add $TITLES` unquoted under zsh does not
        word-split, so a whole list arrives as one argument. A length bound
        catches that where a space cannot — a deliverable may legitimately
        contain spaces, and does.
        """
        if not title or not title.strip():
            raise UsageError(
                "a deliverable cannot be empty. An unnamed entry is a thing "
                "the plan is waiting for that nobody can do."
            )
        if "\n" in title or "\r" in title:
            raise UsageError(
                f"a deliverable cannot contain a newline (got {len(title)} "
                "characters). This is usually a command substitution that "
                "produced a list where one title was expected."
            )
        if len(title) > self._PATH_MAX:
            raise UsageError(
                f"a deliverable cannot exceed {self._PATH_MAX} characters "
                f"(got {len(title)}). Under zsh an unquoted $VAR does NOT "
                "word-split, so a whole list arrives as a single argument. "
                "Quote the expansion, or add the deliverables one at a time."
            )

    def remove_files(self, session_id: str, paths: list[str]) -> Session:
        """Drop declared paths. The way back from a bad `add`.

        `add` without `remove` meant a session could record a path it could
        never un-record, and the only routes left were editing the state file
        by hand — which the engine correctly refuses — or an override, which
        spends a gate's credibility on a typo.
        """
        session = self.require(session_id)
        wanted = set(paths)
        session.files = [f for f in session.files if f not in wanted]
        self._save(session)
        return session

    # -- one-shot overrides -------------------------------------------
    #
    # The appeal that reaches a hook. Everything the CLI evaluates already had
    # one — `session transition --force`, `pipeline advance --force`, `item
    # transition --force`, each appending an override to the record. The gates
    # that actually stop an agent are not evaluated by the CLI: seven rules
    # fire at `pre-tool-use` and five at `stop`, and a blocking verdict there
    # was final, with the only relief a permanent edit to project policy made
    # mid-task to get past a single call (BUG_049).
    #
    # Deliberately a GRANT and not a flag. A tool call carries no arguments the
    # operator can add and no environment a running host will re-read, so the
    # override has to be written down before the call and found by it — which
    # also means it cannot be spent without leaving a record, and that is the
    # property that makes it an override rather than a hole.

    def grant_override(
        self, session_id: str, rule_id: str, reason: str, calls: int = 1
    ) -> Session:
        """Let the next ``calls`` evaluations of ``rule_id`` at a hook through."""
        if not reason or not reason.strip():
            raise UsageError(
                "an override requires --reason. An override with no stated "
                "reason is indistinguishable from the gate never having run, "
                "which is the thing this library exists to remove."
            )
        if get_rule(rule_id) is None:
            # The same loudness `core.rules.evaluate` already applies to a gate
            # naming a rule that does not exist, for the same reason: a typo
            # here is an authority the operator believes they exercised and did
            # not (BUG_050 is this fault at the configuration layer).
            raise UsageError(
                f"no rule {rule_id!r}; 'invariant rules list' shows them all"
            )
        if calls < 1:
            raise UsageError("--calls must be at least 1")

        session = self.require(session_id)
        session.pending_overrides = [
            o for o in session.pending_overrides if o.get("rule") != rule_id
        ]
        session.pending_overrides.append(
            {
                "rule": rule_id,
                "reason": reason.strip(),
                "calls": int(calls),
                "granted": self.clock(),
            }
        )
        self._save(session)
        return session

    def clear_override(self, session_id: str, rule_id: str | None = None) -> Session:
        """Withdraw an unspent grant, or all of them."""
        session = self.require(session_id)
        session.pending_overrides = (
            []
            if rule_id is None
            else [o for o in session.pending_overrides if o.get("rule") != rule_id]
        )
        self._save(session)
        return session

    def consume_override(
        self, session_id: str, rule_id: str, surface: str
    ) -> dict | None:
        """Spend one call of a grant for ``rule_id``, or answer ``None``.

        Spending it appends to ``overrides`` — the same list a forced
        transition writes to — so the compliance record shows a gate that was
        passed deliberately rather than a gate that quietly did not fire.
        """
        session = self.get(session_id)
        if session is None:
            return None
        grant = next(
            (o for o in session.pending_overrides if o.get("rule") == rule_id), None
        )
        if grant is None:
            return None

        remaining = int(grant.get("calls", 1)) - 1
        if remaining > 0:
            grant["calls"] = remaining
        else:
            session.pending_overrides.remove(grant)

        spent = {
            "rule": rule_id,
            "reason": grant.get("reason", "overridden"),
            "surface": surface,
            "one_shot": True,
            "remaining": max(0, remaining),
        }
        session.overrides.append(spent)
        self._save(session)
        return spent

    # -- stand-down ----------------------------------------------------

    def stand_down(
        self,
        session_id: str,
        reason: str,
        until: str = "pipeline",
        include: Sequence[str] = (),
        pipeline_started: float | None = None,
    ) -> Session:
        """Hold every gate, reversibly, without editing project policy.

        The want this exists for is not convenience. A tool that cannot be told
        to stand down for one task gets uninstalled during that task, and what
        returns afterwards is usually nothing. The README already argues the
        adjacent half — *"a gate that fights legitimate exceptions gets the
        whole tool switched off"* — and this is the supported, recorded version
        of what an operator improvises instead. Improvised it leaves no record;
        this leaves one.

        The routes that looked like this were not it. `loose` is an observation
        mode and still blocks; `INVARIANT_DISABLE` takes exact ids with no
        wildcard and does not reach a hook mid-session, because the hook
        subprocess inherits the environment the host was launched with; and a
        manifest edit is permanent by construction — a temporary policy change
        living in committed configuration either lands in a commit by accident
        or quietly becomes the project's real policy.
        """
        if not reason or not reason.strip():
            raise UsageError(
                "standing enforcement down requires --reason. This is the one "
                "state that must never be discoverable only by noticing that "
                "nothing is stopping you."
            )
        if until not in ("pipeline", "session"):
            raise UsageError(
                f"--until takes 'pipeline' or 'session', not {until!r}"
            )
        for rule_id in include:
            if get_rule(rule_id) is None:
                raise UsageError(
                    f"no rule {rule_id!r}; 'invariant rules list' shows them all"
                )

        session = self.require(session_id)
        session.standdown = {
            "reason": reason.strip(),
            "until": until,
            "include": sorted(set(include)),
            "pipeline_started": pipeline_started if until == "pipeline" else None,
            "granted": self.clock(),
        }
        self._save(session)
        return session

    def stand_up(self, session_id: str) -> Session:
        """Lift a stand-down. Idempotent — lifting nothing is not an error."""
        session = self.require(session_id)
        session.standdown = None
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
        # An abandon destination takes only its OWN entry gates, matching
        # `PipelineDomain.gates_for`. The exit gates of the state being left
        # exist to protect the merge, and this is the edge that never reaches
        # it — holding it to them makes finishing the precondition of stopping
        # (BUG_037).
        try:
            abandoning = ABANDON_ROLE in self.machine.state(target).roles
        except UsageError:
            abandoning = False
        sources = [] if abandoning else [(session.state, "exit_gates")]

        gate_ids: list[str] = []
        for state_id, which in [*sources, (target, "entry_gates")]:
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
        self._note(session, reason, to=target)
        session.state = target
        self._save(session)
        self._register(session)
        return verdicts

    def _note(
        self,
        session: Session,
        reason: str | None,
        extra: dict | None = None,
        to: str | None = None,
    ) -> float:
        """One history row and one heartbeat. Returns the moment it stamped.

        ADR-002 D3. The anchor is the heartbeat, which this domain already
        stamped on every transition — so ``elapsed`` costs no new stored field
        and no new clock time, and the entry carries a duration and a pair of
        counters rather than a time.

        ``to`` defaults to the state the session is already in, which is what a
        **standstill** row is: something happened that the machine has no edge
        for, and the record should say so where the durations are read from.
        :meth:`resume` is the case. Routing that through
        ``_advance(session, session.state)`` instead would evaluate the current
        state's entry gates on something that is not an entry — from
        ``isolated`` it would ask ``session.named_after_isolating`` about a
        session that isolated days ago.

        Two reasons a resume row is not decoration. It is where the usage
        counter **changes identity**, and where the caller supplied the new
        host id the row carries ``usage`` and ``usage_host`` read from it, so
        the seam sits on a record that says it is a seam.

        That is the bound case and not the only one. A ``session resume`` run
        from a shell that exports no host identifier cannot see the new host —
        the host adapter rebinds afterwards, from a hook — so this row's
        readings come from the OLD counter, and the change of identity lands on
        the NEXT transition instead. Nothing is mis-measured either way: this
        row subtracts two readings of one counter, and the next one is declined
        by ``core.stats._incommensurable``'s host check exactly as a rebind
        should be. What is lost is the LABEL — the seam is detected on a row
        that does not say why it is a seam. IDEA_007 is where that reading
        would acquire an owner.

        And it **absorbs the dormant interval**:
        ``elapsed`` is wall time since the last stamp, so without a row here
        the days between two host sessions land on the next real transition and
        read as "this state took three days".

        The moment is returned rather than re-read by the caller, because two
        readings of a clock are two different times and a caller that also has
        to record it must record the same one.
        """
        now = self.clock()
        usage, usage_host = self._reading(session.host_session_id)
        entry = {
            "from": session.state,
            "to": session.state if to is None else to,
            "reason": reason,
            "elapsed": max(0.0, now - session.heartbeat) if session.heartbeat else None,
            "usage": usage,
            "usage_host": usage_host,
        }
        entry.update(extra or {})
        session.history.append(entry)
        session.heartbeat = now
        return now

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
        (
            f"{len(candidates)} live sessions were registered here and none "
            f"of their worktrees contains this directory:"
        )
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


def qualified_name(slug: str, session_id: str) -> str:
    """A name that is an address.

    The id is appended to the task slug, so a session can be named by the thing
    a human would type. Uniqueness then costs nothing to enforce, because it is
    **structural**: ids are unique, therefore ``slug-id`` is unique, and there
    is no collision check here because there is no collision to check for. The
    rejected alternative was to keep bare slugs and refuse a duplicate, which
    refuses the case this feature exists for — two sessions on one long task,
    started weeks apart.

    Idempotent, and that is load-bearing rather than tidy: the name people copy
    back out of a greeting already carries the id, so
    ``session resume add-widget-abcd1234`` must not produce
    ``add-widget-abcd1234-abcd1234``.

    Not folded into :func:`_slug`. That one is a pure normaliser with no access
    to an identifier, and it runs before the id is settled — including on the
    ``uuid4`` fallback path, where the id does not exist yet.
    """
    return slug if slug.endswith(f"-{session_id}") else f"{slug}-{session_id}"


#: The name rungs of :meth:`SessionDomain.resolve_name`'s ladder, narrowest
#: first, as the numbers :func:`name_rank` answers with. They are *ordering*
#: and nothing else — never stored, never printed — so a rung inserted between
#: two of these is a renumbering and not a migration.
RANK_FULL_NAME = 0
RANK_BARE_SLUG = 1
RANK_NAME_PREFIX = 2


def name_rank(session_name: str, session_id: str, query: str) -> int | None:
    """WHICH rung of the name ladder answers ``query``, or ``None`` for none.

    The ordered form of :func:`name_matches`, and the one a caller holding
    several matches needs. A predicate can only say *yes*, so a query that is
    one record's bare slug and another's prefix comes back true twice with
    nothing to choose between them — and choosing is the whole point of a
    ladder whose rungs get narrower. The rank hands the precedence back.

    It lives here rather than in the caller because it *is*
    :meth:`SessionDomain.resolve_name`'s ordering, and that method reads it —
    two spellings of one precedence is the duplication that drifts, and this
    one would drift silently, since both spellings would keep answering
    plausibly.

    ``session_id`` is what makes the middle rung possible: a name carries its
    id, so the bare slug is recovered by re-qualifying the query rather than by
    guessing where the name ends.
    """
    if not query or not session_name:
        return None
    if session_name == query:
        return RANK_FULL_NAME
    if session_id and session_name == qualified_name(query, session_id):
        return RANK_BARE_SLUG
    if session_name.startswith(query):
        return RANK_NAME_PREFIX
    return None


def name_matches(session_name: str, session_id: str, query: str) -> bool:
    """Does a session answer to ``query`` by NAME?

    :meth:`SessionDomain.resolve_name`'s ladder minus the parts that need the
    live set: the full name, the bare slug, and a name prefix. Exported at
    module level because a caller that has no session documents to scan still
    has to ask — ``domains/stats`` joins on a name it read off a telemetry
    record, and its records outlive the sessions that produced them.

    It **flattens the ladder's precedence** — which is the honest shape for a
    predicate, and the reason :func:`name_rank` exists beside it. A caller that
    only has to decide whether one session answers to one query (``session
    resume --id X <name>``, where the answer is already pinned) wants this one;
    a caller choosing *between* matches wants the rank. Both are the same
    answer, so they cannot disagree.
    """
    return name_rank(session_name, session_id, query) is not None


def name_ambiguity_message(candidates: list[Session]) -> str:
    """How to name a session when a partial name cannot.

    A sibling of :func:`ambiguity_message` rather than a reuse of it: that one
    is about worktrees and origins — where the caller is standing and which
    sessions were registered above it — and none of that transfers to a query
    that simply matched more than one name.

    Every candidate is printed with its full name, because the full name is the
    answer: a name carries its id, so it is unambiguous by construction and the
    remedy is to copy one of these lines.
    """
    lines = [f"that names {len(candidates)} sessions:"]
    for s in candidates:
        lines.append(
            f"  {s.name}  [{s.state}]  {plan_reader.progress(s.plan).describe()}"
        )
    lines.append(
        "Name one in full. A session name carries its id, so the full name "
        "cannot be ambiguous:"
    )
    if candidates:
        lines.append(f"  invariant session resume {candidates[0].name}")
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
