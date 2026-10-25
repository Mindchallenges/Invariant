"""The read-only scan: one snapshot of every session in every project.

``scan_project`` reads one registered project and returns its rows. Nothing it
does writes: sessions, pipelines and attention records are read through an
:class:`~invariant.core.store.InertStore` built on the project's state root,
the manifest and the machines are only parsed, and git is only asked
``worktree list``. A scan that wrote would race every host in every project it
read, and the oracle's whole claim is that a project behaves the same whether
or not it is watched (ADR-007 D1).

**Rows.** Four kinds, so that the classifier and the recommender see one shape:

* ``session`` — a live invariant session (any state its machine does not call
  terminal), with every host session it has had nested under it: the current
  binding first, then the earlier ones, oldest first.
* ``host`` — an orphan host: an attention record or a recent transcript that no
  live session claims. A host bound only to a session that has since ended
  counts as an orphan, because a host outlives the session it finished.
* ``worktree`` — a linked worktree no live session records.
* ``error`` — a project, or one session record, that could not be read. One
  broken project becomes one error row and never hides the others.

**No state name is written here.** Projects may shadow the session machine, so
where a session is in its life is derived from the machine's structure
(:func:`session_stages`): the merge is the state whose entry gates include
``session.ff_merge_target``, the reviewed stage is the one whose edges lead
into it, and so on outward.

**Host facts are injected.** Which transcripts belong to a project, and whether
a process is the host, are Claude Code knowledge. The CLI hands them in as a
:class:`HostProbe`, the shape ``CLI -. injects .-> CCTEL`` already has, so this
package imports nothing from ``hosts/`` (ADR-007 D11).

**Cheap on a second pass.** :class:`ScanCache` keeps the git-backed answers
(worktree lists, transcript roots) and the process-name checks for a short
time, so a watcher calling this every few seconds re-reads only small JSON
files, directory listings and the tails of the transcripts that changed.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..core import git
from ..core.errors import InvariantError, ManifestError
from ..core.fsm import Machine
from ..core.machines import ABANDON_ROLE
from ..core.manifest import MANIFEST_RELPATH, Manifest
from ..core.store import InertStore
from ..domains.pipeline import PipelineDomain
from ..domains.session import Session, SessionDomain
from .attention import ATTENTION_DIR, AttentionStore
from .index import ProjectEntry, ProjectIndex

ROW_SESSION = "session"
ROW_HOST = "host"
ROW_WORKTREE = "worktree"
ROW_ERROR = "error"

#: Where a session is in its life, derived from its machine's structure.
STAGE_EARLY = "early"
STAGE_WORKING = "working"
STAGE_REVIEWED = "reviewed"
STAGE_LANDED = "landed"
STAGE_ENDED = "ended"

#: The gate that defines the merge. Every session machine that merges gates
#: the merge on it, so it identifies the state without naming it.
MERGE_GATE = "session.ff_merge_target"

#: A host no live session claims is shown while it is alive, or for this long
#: after its last sign of life.
ORPHAN_WINDOW_SECONDS = 24 * 3600

#: How long a git-backed answer or a process-name check is reused.
CACHE_TTL_SECONDS = 60.0


class TranscriptIndex(Protocol):
    """A host's transcripts for one project."""

    def activity(self) -> dict[str, float]:
        """Host session id to its last conversational turn, as an epoch.

        The host decides what a turn is and what to answer when it finds none
        (Claude Code: the newest timestamped user or assistant entry, else the
        file's modification time).
        """
        ...


class HostProcess(Protocol):
    """One running host process, as the host's own per-process record says.

    ``start`` is an opaque token the host also gives :class:`ProcessFacts`,
    so the two are compared for equality and never parsed here.
    """

    session_id: str
    pid: int
    name: str | None
    cwd: str | None
    started: float | None
    start: str | None


class ProcessFacts(Protocol):
    """What the process table says about a live pid."""

    is_host: bool
    start: str | None
    tty: str | None


class HostProbe(Protocol):
    """What the agent host knows that the oracle does not.

    ``host_processes`` and ``process_facts`` are optional (IDEA_024): a probe
    without them, like one whose read raises, leaves liveness to the
    attention record's pid, as before the records were read.
    """

    def transcripts(self, root: Path) -> TranscriptIndex:
        """The transcripts of the project rooted at ``root``."""
        ...

    def is_host_process(self, pid: int) -> bool:
        """Whether the live process ``pid`` is the agent host."""
        ...

    def host_processes(self) -> Iterable[HostProcess]:
        """Every running host's per-process record. Raises when the source
        cannot be used (missing, unreadable, an unexpected shape)."""
        ...

    def process_facts(self, pid: int) -> ProcessFacts | None:
        """``pid``'s name check, start token and terminal, or ``None``."""
        ...


def pid_exists(pid: int | None) -> bool:
    """Whether a process ``pid`` exists. Signal 0 sends nothing."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class ScanCache:
    """The answers a scan reuses across passes, each for ``ttl`` seconds."""

    def __init__(
        self,
        probe: HostProbe | None = None,
        ttl: float = CACHE_TTL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        pid_alive: Callable[[int | None], bool] = pid_exists,
    ) -> None:
        self.probe = probe
        self.ttl = ttl
        self.monotonic = monotonic
        self.pid_alive = pid_alive
        self._entries: dict[tuple[str, Any], tuple[float, Any]] = {}
        #: Whether the last :meth:`host_processes` read was used, and why not.
        self.host_process_status: dict[str, Any] = _source_status(False, 0, "not read yet")

    def _cached(self, kind: str, key: Any, compute: Callable[[], Any]) -> Any:
        now = self.monotonic()
        hit = self._entries.get((kind, key))
        if hit is not None and now - hit[0] < self.ttl:
            return hit[1]
        value = compute()
        self._entries[(kind, key)] = (now, value)
        return value

    def worktrees(self, root: Path) -> list[git.Worktree]:
        """``git worktree list`` for ``root``, reused for ``ttl``."""
        return self._cached("worktrees", str(root), lambda: git.worktrees(root))

    def transcript_activity(self, root: Path) -> dict[str, float]:
        """Host id to its last transcript turn, for the project at ``root``.

        The transcript source is built once per ``ttl``, because building it
        asks git for the project's worktrees; asking it again is a directory
        read plus a tail read of each transcript that changed since. No probe,
        or one that fails, answers nothing.
        """
        if self.probe is None:
            return {}
        try:
            source = self._cached("transcripts", str(root), lambda: self.probe.transcripts(root))
            return dict(source.activity())
        except Exception:  # noqa: BLE001 - a host format we cannot read costs one column
            return {}

    def host_alive(self, pid: int | None) -> bool | None:
        """Whether ``pid`` is a live host; ``None`` when that cannot be asked.

        The process must exist, checked every time because that is cheap and a
        host can exit at any moment, and it must still be the host, because a
        pid is reused after its process exits (ADR-007 D7). The name check
        costs a ``ps``, so its answer is kept for ``ttl`` and dropped as soon
        as the pid is seen dead.
        """
        if not isinstance(pid, int) or isinstance(pid, bool):
            return None
        if not self.pid_alive(pid):
            self._entries.pop(("host", pid), None)
            return False
        if self.probe is None:
            return True
        try:
            return bool(self._cached("host", pid, lambda: self.probe.is_host_process(pid)))
        except Exception:  # noqa: BLE001 - an unanswerable check is unknown, not dead
            return None

    def host_processes(self) -> dict[str, list[HostProcess]]:
        """The host's per-process records by host session id, read once per ``ttl``.

        :attr:`host_process_status` says whether the source was used. A probe
        without the read, or a read that raises, is "not used", and the scan
        then behaves exactly as it did before the records existed.
        """
        by_host, status = self._cached("processes", None, self._read_host_processes)
        self.host_process_status = status
        return by_host

    def _read_host_processes(self) -> tuple[dict[str, list[HostProcess]], dict[str, Any]]:
        read = getattr(self.probe, "host_processes", None)
        if read is None:
            reason = "no host probe" if self.probe is None else "the host probe does not read them"
            return {}, _source_status(False, 0, f"not used: {reason}")
        by_host: dict[str, list[HostProcess]] = {}
        try:
            records = list(read())
            for record in records:
                by_host.setdefault(record.session_id, []).append(record)
        except Exception as exc:  # noqa: BLE001 - an undocumented format must cost only itself
            return {}, _source_status(False, 0, f"not used: {_describe(exc)}")
        return by_host, _source_status(
            True, len(records), f"{len(records)} per-process records read"
        )

    def process_alive(self, pid: int | None, start: str | None) -> tuple[bool | None, str | None]:
        """Whether a recorded pid is still that host, and its terminal.

        :meth:`host_alive`'s two checks, plus one: when the record and the
        process table both give a start, they must be equal, because a pid is
        reused after its process exits and a reused pid can be another
        ``claude``. A start either side cannot give is not compared. The
        facts are kept for ``ttl`` and dropped when the pid is seen dead, as
        the name check is. A live pid ``ps`` cannot describe is unknown.
        """
        if not isinstance(pid, int) or isinstance(pid, bool):
            return None, None
        if not self.pid_alive(pid):
            self._entries.pop(("facts", pid), None)
            self._entries.pop(("host", pid), None)
            return False, None
        ask = getattr(self.probe, "process_facts", None)
        if ask is None:
            return self.host_alive(pid), None
        try:
            facts = self._cached("facts", pid, lambda: ask(pid))
        except Exception:  # noqa: BLE001 - an unanswerable check is unknown, not dead
            return None, None
        if facts is None:
            return None, None
        if not facts.is_host:
            return False, None
        if start and facts.start and start != facts.start:
            return False, None
        return True, facts.tty


@dataclass
class HostView:
    """One host session, as the scan saw it."""

    host_session_id: str
    #: The session's live binding (always true on an orphan row); false for
    #: the hosts a session had before it.
    current: bool = True
    #: The host's pid, when its sources name one, or agree on one.
    pid: int | None = None
    #: ``None`` when no pid is known, or the sources disagree, so liveness
    #: could not be asked.
    alive: bool | None = None
    attention: dict[str, Any] | None = None
    attention_written: float | None = None
    transcript_written: float | None = None
    #: Where ``pid`` came from: ``attention``, ``record`` (Claude Code's
    #: per-process record), ``both`` when they agree, ``conflict`` when they
    #: do not; ``None`` with no pid at all (IDEA_024).
    pid_source: str | None = None
    #: On a conflict, every pid named, ``{source, pid, alive}`` each. The
    #: oracle never picks one silently.
    pid_conflict: list[dict[str, Any]] = field(default_factory=list)
    #: From the per-process record: the name the host was launched with, its
    #: working directory, when it started (epoch), and its start token, which
    #: a later check compares again against a reused pid.
    launched_name: str | None = None
    cwd: str | None = None
    started: float | None = None
    process_start: str | None = None
    #: The terminal the verified host runs on, from ``ps`` (``None`` without
    #: one, or when the pid came from the attention record alone).
    tty: str | None = None


@dataclass
class PipelineView:
    """The facts about a session's pipeline that the classifier reads."""

    methodology: str
    state: str
    task: str = ""
    terminal: bool = False
    #: Terminal and not the abandon state: the methodology ran to its end.
    landed: bool = False
    red_phase: str = "red"
    test_runs: int = 0
    last_test: dict[str, Any] | None = None
    #: How many transitions the pipeline has made, and when its record was
    #: last written (``st_mtime_ns``; ``None`` if it could not be read). Two
    #: of the facts the gate cache's fingerprint is made of: evidence is
    #: recorded without a transition, so the write time is what moves.
    transitions: int = 0
    written_ns: int | None = None


@dataclass
class Row:
    """One line of the oracle's list."""

    kind: str
    project: str
    project_name: str = ""
    root: str = ""
    session_id: str | None = None
    name: str | None = None
    state: str | None = None
    stage: str | None = None
    worktree: str | None = None
    branch: str | None = None
    origin: str | None = None
    stranded: bool = False
    host_ended: bool = False
    plan_done: int = 0
    plan_total: int = 0
    plan_complete: bool = False
    heartbeat: float | None = None
    plan_touched: float | None = None
    pipeline: PipelineView | None = None
    hosts: list[HostView] = field(default_factory=list)
    last_activity: float | None = None
    #: A ``worktree`` row's path.
    path: str | None = None
    error: str | None = None
    #: On a ``host`` row: live sessions in the project with no live host.
    adoptable: list[str] = field(default_factory=list)
    #: On a ``host`` row: the ended session it was last bound to, if any.
    previous_session: str | None = None

    @property
    def current_host(self) -> HostView | None:
        return next((h for h in self.hosts if h.current), None)

    @property
    def subject(self) -> str:
        """What a signal's key names: a session, a host, a worktree, a project."""
        host = self.hosts[0].host_session_id if self.hosts else None
        return subject_of(self.kind, self.session_id, host, self.path)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def subject_of(kind: str, session_id: str | None, host: str | None, path: str | None) -> str:
    """A row's subject: its session, else its first host, worktree or project."""
    if session_id:
        return session_id
    if kind == ROW_HOST and host:
        return f"host:{host}"
    if kind == ROW_WORKTREE:
        return f"worktree:{path}"
    return "project"


def row_key(row: dict[str, Any]) -> str:
    """A row of the assessed document, named stably: ``project|subject``.

    A row's position moves from one scan to the next; its project and subject
    do not. This is what the web page's actions name a row by, so the server
    finds the same row again in its own fresh read.
    """
    hosts = row.get("hosts") or []
    host = hosts[0].get("host_session_id") if hosts else None
    subject = subject_of(row.get("kind", ""), row.get("session_id"), host, row.get("path"))
    return f"{row.get('project', '')}|{subject}"


@dataclass
class ProjectSnapshot:
    """One project's rows, and how long reading them took."""

    project: ProjectEntry
    rows: list[Row] = field(default_factory=list)
    scanned_at: float = 0.0
    elapsed: float = 0.0
    #: Whether the host's per-process records were used for this read:
    #: ``{used, records, detail}``.
    host_processes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project.to_dict(),
            "rows": [r.to_dict() for r in self.rows],
            "scanned_at": self.scanned_at,
            "elapsed": self.elapsed,
            "host_processes": dict(self.host_processes),
        }


def session_stages(machine: Machine) -> dict[str, str]:
    """Every state of a session machine, mapped to where it sits in a life.

    Derived from structure so that a project's own session machine is read by
    the same rule as the shipped one:

    * **ended** — terminal, or the abandon state;
    * **landed** — the merge (a state :data:`MERGE_GATE` guards) and every
      state reachable after it short of the end: the work is on the base and
      only teardown is owed;
    * **reviewed** — a state with an edge into the merge;
    * **working** — a state with an edge into a reviewed one;
    * **early** — everything else: registered, isolated.
    """
    states = machine.states

    def ended(state_id: str) -> bool:
        return machine.is_terminal(state_id) or ABANDON_ROLE in states[state_id].roles

    merges = {s.id for s in states.values() if MERGE_GATE in s.entry_gates}
    landed: set[str] = set()
    pending = list(merges)
    while pending:
        state_id = pending.pop()
        if state_id in landed or state_id not in states or ended(state_id):
            continue
        landed.add(state_id)
        pending.extend(states[state_id].next)

    def leads_into(targets: set[str], exclude: set[str]) -> set[str]:
        return {
            s.id
            for s in states.values()
            if s.id not in exclude and not ended(s.id) and targets.intersection(s.next)
        }

    reviewed = leads_into(merges, landed)
    working = leads_into(reviewed, landed | reviewed)

    stages: dict[str, str] = {}
    for state_id in states:
        if ended(state_id):
            stages[state_id] = STAGE_ENDED
        elif state_id in landed:
            stages[state_id] = STAGE_LANDED
        elif state_id in reviewed:
            stages[state_id] = STAGE_REVIEWED
        elif state_id in working:
            stages[state_id] = STAGE_WORKING
        else:
            stages[state_id] = STAGE_EARLY
    return stages


def scan_project(
    entry: ProjectEntry,
    clock: Callable[[], float] = time.time,
    cache: ScanCache | None = None,
) -> ProjectSnapshot:
    """One project's rows. Anything that stops the read is one error row."""
    cache = cache if cache is not None else ScanCache()
    started = time.perf_counter()
    now = clock()
    snapshot = ProjectSnapshot(project=entry, scanned_at=now)
    try:
        snapshot.rows = _ProjectReader(entry, now, cache).rows()
        snapshot.host_processes = dict(cache.host_process_status)
    except Exception as exc:  # noqa: BLE001 - one broken project must not hide the rest
        snapshot.rows = [
            Row(
                kind=ROW_ERROR,
                project=entry.key,
                project_name=entry.name,
                root=entry.root,
                error=_describe(exc),
            )
        ]
    snapshot.elapsed = time.perf_counter() - started
    return snapshot


def scan_all(
    index: ProjectIndex,
    clock: Callable[[], float] = time.time,
    cache: ScanCache | None = None,
    select: str | None = None,
) -> list[ProjectSnapshot]:
    """Every registered project, or the one ``select`` names (key, root or name)."""
    entries = [index.match(select)] if select else index.list()
    cache = cache if cache is not None else ScanCache()
    return [scan_project(entry, clock, cache) for entry in entries]


# -- reading one project ------------------------------------------------------


class _ProjectReader:
    """The read of one project, over an inert store on its state root."""

    def __init__(self, entry: ProjectEntry, now: float, cache: ScanCache) -> None:
        root = Path(entry.root)
        # Loaded from the recorded root only: walking upward from a root that
        # lost its manifest would read some enclosing project's instead.
        if not (root / MANIFEST_RELPATH).is_file():
            raise ManifestError(
                f"no manifest at {root}; 'invariant oracle projects prune' drops it"
            )
        self.entry = entry
        self.root = root
        self.now = now
        self.cache = cache
        self.manifest = Manifest.load(root)
        self.store = InertStore(Path(entry.key))
        self.sessions = SessionDomain(self.manifest, store=self.store)
        self.pipelines = PipelineDomain(self.manifest, store=self.store)
        self.attention = AttentionStore(self.store)
        self.stages = session_stages(self.sessions.machine)
        self._transcripts: dict[str, float] = {}
        self._processes: dict[str, list[HostProcess]] = {}

    def _row(self, kind: str, **fields: Any) -> Row:
        return Row(
            kind=kind,
            project=self.entry.key,
            project_name=self.entry.name,
            root=self.entry.root,
            **fields,
        )

    def rows(self) -> list[Row]:
        records, errors = self._records()
        live = [s for s in records if self.stages.get(s.state, STAGE_EARLY) != STAGE_ENDED]
        self._transcripts = self.cache.transcript_activity(self.root)
        self._processes = self.cache.host_processes()
        session_rows = [self._session_row(s) for s in live]
        return session_rows + self._orphans(records, live) + self._leaked(live) + errors

    def _records(self) -> tuple[list[Session], list[Row]]:
        """Every session record, each read on its own so one bad file is one row."""
        records: list[Session] = []
        errors: list[Row] = []
        for session_id in self.store.list_ids("sessions"):
            try:
                session = self.sessions.get(session_id)
            except (InvariantError, TypeError, ValueError) as exc:
                errors.append(self._row(ROW_ERROR, session_id=session_id, error=_describe(exc)))
                continue
            if session is not None:
                records.append(session)
        return records, errors

    # -- sessions ----------------------------------------------------------

    def _session_row(self, session: Session) -> Row:
        hosts = [
            self._host(host_id, current=(host_id == session.host_session_id))
            for host_id in _hosts_of(session)
        ]
        return self._row(
            ROW_SESSION,
            session_id=session.id,
            name=session.name,
            state=session.state,
            stage=self.stages.get(session.state, STAGE_EARLY),
            worktree=session.worktree,
            branch=session.branch,
            origin=session.origin,
            stranded=session.is_stranded,
            host_ended=session.host_ended,
            plan_done=session.plan_done_count,
            plan_total=session.plan_total,
            plan_complete=session.plan_complete,
            heartbeat=session.heartbeat or None,
            plan_touched=session.plan_touched or None,
            pipeline=self._pipeline(session.id),
            hosts=hosts,
            last_activity=_latest(
                [session.heartbeat, session.plan_touched]
                + [h.attention_written for h in hosts]
                + [h.transcript_written for h in hosts]
            ),
        )

    def _pipeline(self, session_id: str) -> PipelineView | None:
        try:
            pipeline = self.pipelines.current(session_id)
        except (InvariantError, TypeError, ValueError):
            return None
        if pipeline is None:
            return None
        evidence = pipeline.evidence if isinstance(pipeline.evidence, dict) else {}
        runs = evidence.get("test_runs")
        runs = runs if isinstance(runs, list) else []
        view = PipelineView(
            methodology=pipeline.methodology,
            state=pipeline.state,
            task=pipeline.task,
            test_runs=len(runs),
            last_test=runs[-1] if runs and isinstance(runs[-1], dict) else None,
            transitions=len(pipeline.history) if isinstance(pipeline.history, list) else 0,
            written_ns=_mtime_ns(self.store.root / self.pipelines.relpath(session_id)),
        )
        try:
            machine = self.pipelines.machine_for(pipeline)
        except InvariantError:
            return view
        state = machine.states.get(pipeline.state)
        view.terminal = machine.is_terminal(pipeline.state)
        view.landed = view.terminal and state is not None and ABANDON_ROLE not in state.roles
        view.red_phase = machine.red_phase or view.red_phase
        return view

    # -- hosts -------------------------------------------------------------

    def _host(self, host_id: str, current: bool) -> HostView:
        record, written = self._attention_of(host_id)
        pid = record.get("pid") if record else None
        pid = pid if isinstance(pid, int) and not isinstance(pid, bool) else None
        view = HostView(
            host_session_id=host_id,
            current=current,
            attention=record,
            attention_written=written,
            transcript_written=self._transcripts.get(host_id),
        )
        self._liveness(view, pid, self._processes.get(host_id, []))
        return view

    def _liveness(self, view: HostView, pid: int | None, records: list[HostProcess]) -> None:
        """Join the attention record's pid with the per-process records.

        One source names the pid, or both agree on it: that pid, and its
        liveness, the record's check being the stronger one when there is a
        record. They disagree, or two records are both alive: no pid and
        unknown liveness, with every candidate listed (IDEA_024).
        """
        found = [(r, *self.cache.process_alive(r.pid, r.start)) for r in records]
        live = [f for f in found if f[1] is True]
        if len(live) > 1:
            view.pid_source = "conflict"
            view.pid_conflict = [_candidate("record", r.pid, a) for r, a, _ in live]
            return
        chosen = live[0] if live else max(found, key=lambda f: f[0].started or 0.0, default=None)
        if chosen is None:
            if pid is not None:
                view.pid, view.alive, view.pid_source = pid, self.cache.host_alive(pid), "attention"
            return
        process, alive, tty = chosen
        if pid is not None and pid != process.pid:
            view.pid_source = "conflict"
            view.pid_conflict = [
                _candidate("attention", pid, self.cache.host_alive(pid)),
                _candidate("record", process.pid, alive),
            ]
            return
        view.pid, view.alive, view.tty = process.pid, alive, tty
        view.pid_source = "both" if pid is not None else "record"
        view.launched_name, view.cwd = process.name, process.cwd
        view.started, view.process_start = process.started, process.start

    def _attention_of(self, host_id: str) -> tuple[dict[str, Any] | None, float | None]:
        try:
            path = self.store.root / self.attention.relpath(host_id)
        except ValueError:
            return None, None
        written = _mtime(path)
        if written is None:
            return None, None
        try:
            record = self.attention.read(host_id)
        except (InvariantError, TypeError, ValueError):
            return None, written
        return (record.to_dict() if record else None), written

    def _orphans(self, records: list[Session], live: list[Session]) -> list[Row]:
        """Hosts no live session claims, while alive or recently heard from.

        A host bound only to a session that has ended is one of them, and the
        row names that session. A record whose host said ``SessionEnd`` is not:
        that host is over.
        """
        live_ids = {s.id for s in live}
        claimed = {h for s in live for h in _hosts_of(s)}
        ended_owner = {
            h: s.id for s in records if s.id not in live_ids for h in _hosts_of(s)
        }
        adoptable = [
            s.id
            for s in live
            if not s.is_stranded and (not s.host_session_id or s.host_ended)
        ]
        candidates = set(self.store.list_ids(ATTENTION_DIR)) | set(self._transcripts)
        rows = []
        for host_id in sorted(candidates - claimed):
            view = self._host(host_id, current=True)
            if view.attention and view.attention.get("ended"):
                continue
            last = _latest([view.attention_written, view.transcript_written])
            recent = last is not None and self.now - last <= ORPHAN_WINDOW_SECONDS
            if not (view.alive or recent):
                continue
            rows.append(
                self._row(
                    ROW_HOST,
                    hosts=[view],
                    last_activity=last,
                    adoptable=list(adoptable),
                    previous_session=ended_owner.get(host_id),
                )
            )
        return rows

    # -- worktrees ---------------------------------------------------------

    def _leaked(self, live: list[Session]) -> list[Row]:
        """Linked worktrees on disk that no live session records."""
        owned = {_resolved(s.worktree) for s in live if s.worktree}
        rows = []
        for worktree in self.cache.worktrees(self.root):
            if worktree.is_main:
                continue
            path = _resolved(str(worktree.path))
            if path in owned or not Path(path).is_dir():
                continue
            rows.append(self._row(ROW_WORKTREE, path=path, branch=worktree.branch))
        return rows


def _hosts_of(session: Session) -> list[str]:
    """The current binding first, then the earlier ones, oldest first."""
    ordered = [session.host_session_id, *session.hosts]
    seen: list[str] = []
    for host_id in ordered:
        if host_id and isinstance(host_id, str) and host_id not in seen:
            seen.append(host_id)
    return seen


def _latest(values: Iterable[float | None]) -> float | None:
    present = [float(v) for v in values if v]
    return max(present) if present else None


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _resolved(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return path


def _describe(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__


def _source_status(used: bool, records: int, detail: str) -> dict[str, Any]:
    return {"used": used, "records": records, "detail": detail}


def _candidate(source: str, pid: int, alive: bool | None) -> dict[str, Any]:
    return {"source": source, "pid": pid, "alive": alive}
