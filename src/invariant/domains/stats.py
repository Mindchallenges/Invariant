"""The stats domain — measuring what a session cost.

The fifth domain (ADR-002 D1), and like ``memorystore`` it is **not** a state
machine. There is no lifecycle to advance; there is a store, a set of
invariants over it, and operations that repair them:

===========================  ==================================  ==============
Invariant                    Check                               Repair
===========================  ==================================  ==============
Every record parses          the file is readable JSON           report only
Records match the source     stored watermark == current one     ``refresh``
Nothing is silently lost     a record with no transcript is      ``archive``
                             archived
Every model is priced        no model outside the price table    report only
The report matches the       fingerprint in the artifact ==      ``report``
store                        fingerprint of the store
===========================  ==================================  ==============

Two seams keep this honest. The samples come from an injected
``core.stats.SampleSource`` — the domain never imports ``hosts`` — and the join
from a host session to an invariant session lives here rather than in the CLI,
because knowing what a session *is* is what ``overview.md`` reserves to a
domain.

The store is a **cache** while the transcripts survive and the **only copy**
once the host prunes them, which is why promoting a record to permanent is an
explicit act rather than a policy (D5).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import StateError, UsageError
from ..core.stats import (
    COST_FLOOR_LABEL,
    NO_BREAKDOWN,
    RATES_AS_OF,
    SampleSource,
    SessionStats,
    StateSpend,
    TranscriptWatermarks,
    Usage,
    breakdown,
    fold_session,
    format_cost,
    format_duration,
    format_tokens,
)
from ..core.store import JsonStore

STORE_DIR = "stats"
REPORT_NAME = "sessions.md"

#: Long enough to identify a store state, short enough that no privacy pattern
#: mistakes it for a device identifier. The report is a committed artifact and
#: has to pass the gate this library ships.
FINGERPRINT_LENGTH = 16


@dataclass
class StatRecord:
    """One host session, as measured and as joined.

    ``states`` is ``None`` — not an empty list — for a session recorded before
    per-state attribution existed. The two are different facts and the report
    renders them differently (D3).
    """

    host_session_id: str
    watermark: str
    stats: SessionStats
    archived: bool = False
    session_id: str | None = None
    session_name: str | None = None
    states: list[dict] | None = None
    #: True when SOME of this session's pipelines could be attributed and
    #: others could not, so the breakdown below is real but does not add up to
    #: the total. Rendered rather than hidden.
    states_partial: bool = False

    @property
    def label(self) -> str:
        """What a report row is keyed on (D7): a name, never an identifier."""
        return self.session_name or "(unbound session)"

    def to_dict(self) -> dict:
        return {
            "host_session_id": self.host_session_id,
            "watermark": self.watermark,
            "archived": self.archived,
            "session_id": self.session_id,
            "session_name": self.session_name,
            "states": self.states,
            "states_partial": self.states_partial,
            "stats": self.stats.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "StatRecord":
        return cls(
            host_session_id=str(raw.get("host_session_id") or ""),
            watermark=str(raw.get("watermark") or ""),
            stats=SessionStats.from_dict(raw.get("stats") or {}),
            archived=bool(raw.get("archived")),
            session_id=raw.get("session_id"),
            session_name=raw.get("session_name"),
            states=raw.get("states"),
            states_partial=bool(raw.get("states_partial")),
        )


@dataclass
class StatsReport:
    """What :meth:`StatsDomain.check` found. Empty lists mean clean."""

    records: int = 0
    unreadable: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    unrecorded: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)
    unpriced: list[str] = field(default_factory=list)
    report: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.problems()

    def problems(self) -> list[str]:
        return [
            *self.unreadable,
            *self.stale,
            *self.unrecorded,
            *self.orphaned,
            *self.unpriced,
            *self.report,
        ]


class StatsDomain:
    def __init__(
        self,
        manifest,
        store: JsonStore | None = None,
        source: SampleSource | None = None,
        sessions=None,
        pipelines=None,
    ) -> None:
        self.manifest = manifest
        self.store = store or JsonStore(manifest.state_root)
        #: Injected by the CLI (D2). ``None`` is a usable state for everything
        #: that reads the store; only ingest needs a source.
        self.source = source
        self._sessions = sessions
        self._pipelines = pipelines

    # -- collaborators, resolved late so a test can replace them --------

    @property
    def sessions(self):
        if self._sessions is None:
            from .session import SessionDomain

            self._sessions = SessionDomain(self.manifest, self.store)
        return self._sessions

    @property
    def pipelines(self):
        if self._pipelines is None:
            from .pipeline import PipelineDomain

            self._pipelines = PipelineDomain(self.manifest, self.store)
        return self._pipelines

    def watermarks(self) -> TranscriptWatermarks:
        """The :class:`WatermarkSource` the two FSM domains record with (D3)."""
        return TranscriptWatermarks(self.source)

    # -- the store -----------------------------------------------------

    def _path(self, host_session_id: str) -> str:
        return f"{STORE_DIR}/{host_session_id}.json"

    def get(self, host_session_id: str) -> StatRecord | None:
        try:
            raw = self.store.read(self._path(host_session_id))
        except StateError:
            return None
        return StatRecord.from_dict(raw) if raw else None

    def records(self) -> list[StatRecord]:
        """Every readable record. A corrupt one is skipped, not fatal.

        The same discipline as the parser: a store that refuses to list because
        one file wedged is a store that turns a reporting problem into an
        operational one. :meth:`check` is where the corruption is *named*.
        """
        out: list[StatRecord] = []
        for host_session_id in self.store.list_ids(STORE_DIR):
            record = self.get(host_session_id)
            if record is not None:
                out.append(record)
        return sorted(out, key=_row_key)

    def _save(self, record: StatRecord) -> None:
        self.store.ensure_root()
        self.store.write(self._path(record.host_session_id), record.to_dict())

    # -- ingest --------------------------------------------------------

    def require_source(self) -> SampleSource:
        if self.source is None:
            raise UsageError(
                "no telemetry source for this host.\n"
                "  `stats` reads what the agent host wrote down, and this "
                "project's host.kind has no reader in this release.\n"
                "  Everything already in the store still reads: try "
                "'invariant stats report'."
            )
        return self.source

    def refresh(self, force: bool = False) -> dict:
        """Rebuild the store from the source (D5).

        Rebuilds a record whose watermark moved and leaves the rest alone —
        but re-runs the **join** for every record either way, because the
        invariant session may have been named, bound or advanced since the
        transcript last changed, and none of that touches the transcript.
        """
        source = self.require_source()
        bindings = self._bindings()

        rebuilt = 0
        unchanged = 0
        for ref in source.sessions():
            existing = self.get(ref.host_session_id)
            if existing is not None and existing.watermark == ref.watermark and not force:
                record = existing
                unchanged += 1
            else:
                record = StatRecord(
                    host_session_id=ref.host_session_id,
                    watermark=ref.watermark,
                    stats=fold_session(
                        ref.host_session_id, list(source.samples(ref))
                    ),
                    archived=bool(existing and existing.archived),
                )
                rebuilt += 1
            self._join(record, bindings)
            self._save(record)

        return {
            "scanned": rebuilt + unchanged,
            "rebuilt": rebuilt,
            "unchanged": unchanged,
        }

    def _bindings(self) -> dict[str, object]:
        return {
            s.host_session_id: s
            for s in self.sessions.list()
            if s.host_session_id
        }

    def _join(self, record: StatRecord, bindings: dict) -> None:
        """Bind the measurement to the invariant session that produced it.

        This join is the reason the domain exists rather than the CLI holding
        the logic: it is knowledge of what a session is, which ``overview.md``
        reserves to a domain.
        """
        session = bindings.get(record.host_session_id)
        if session is None:
            record.session_id = None
            record.session_name = None
            record.states = None
            record.states_partial = False
            return
        record.session_id = session.id
        record.session_name = session.name
        spends, partial = self._breakdown(session.id)
        record.states = None if spends is None else [_spend_dict(s) for s in spends]
        record.states_partial = partial

    def _breakdown(self, session_id: str) -> tuple[list[StateSpend] | None, bool]:
        """Per-state durations and spend across this session's pipelines.

        A session runs pipelines in sequence, so the states are summed across
        them. A pipeline predating D3 contributes nothing and sets the partial
        flag: reporting its states as zero would claim they were free.
        """
        raw: list[dict] = []
        current = self.pipelines.current(session_id)
        if current is not None:
            raw.append(current.to_dict())
        raw.extend(self.pipelines.history(session_id))
        if not raw:
            return None, False

        merged: dict[str, StateSpend] = {}
        order: list[str] = []
        attributed = 0
        for pipeline in raw:
            spends = breakdown(
                pipeline.get("history") or [],
                Usage.from_dict(pipeline.get("usage_at_start"))
                if pipeline.get("usage_at_start")
                else None,
            )
            if spends is None:
                continue
            attributed += 1
            for spend in spends:
                if spend.state not in merged:
                    merged[spend.state] = StateSpend(state=spend.state, elapsed=0.0)
                    order.append(spend.state)
                target = merged[spend.state]
                target.elapsed = (target.elapsed or 0.0) + (spend.elapsed or 0.0)
                if spend.usage is None:
                    target.note = "some transitions carried no watermark"
                elif target.usage is None:
                    target.usage = spend.usage
                else:
                    target.usage = target.usage + spend.usage

        if not attributed:
            return None, False
        return [merged[name] for name in order], attributed < len(raw)

    # -- repair operations ---------------------------------------------

    def archive(self, selector: str) -> StatRecord:
        """Promote one record to permanent (D5).

        The decision this makes is *what outlives the host's retention*. Taken
        deliberately here rather than discovered after a cleanup has already
        removed the only copy of a month of history.
        """
        record = self.resolve(selector)
        if record is None:
            raise UsageError(
                f"no stats record matches {selector!r}.\n"
                "  A record is named by its invariant session's name, that "
                "session's id, or the host session id.\n"
                "  'invariant stats report' lists what is in the store."
            )
        record.archived = True
        self._save(record)
        return record

    def resolve(self, selector: str) -> StatRecord | None:
        if not selector:
            return None
        for record in self.records():
            if selector in (
                record.host_session_id,
                record.session_id,
                record.session_name,
            ):
                return record
        return None

    # -- invariants ----------------------------------------------------

    def check(self) -> StatsReport:
        report = StatsReport()

        stored: dict[str, StatRecord] = {}
        for host_session_id in self.store.list_ids(STORE_DIR):
            record = self.get(host_session_id)
            if record is None:
                report.unreadable.append(
                    f"{host_session_id}: the record could not be read; "
                    f"'invariant stats refresh --force' rebuilds it"
                )
                continue
            stored[host_session_id] = record
        report.records = len(stored)

        current = {}
        if self.source is not None:
            try:
                current = {r.host_session_id: r.watermark for r in self.source.sessions()}
            except OSError:
                current = {}

        for host_session_id, record in sorted(stored.items()):
            live = current.get(host_session_id)
            if live is None:
                if not record.archived:
                    report.orphaned.append(
                        f"{record.label}: the transcript is gone and this "
                        f"record is not archived — the store is now the only "
                        f"copy, and state collection may drop it. "
                        f"'invariant stats archive' keeps it."
                    )
            elif live != record.watermark:
                report.stale.append(
                    f"{record.label}: the transcript moved since this record "
                    f"was built (stale); 'invariant stats refresh' rebuilds it"
                )
            unpriced = record.stats.unpriced_models
            if unpriced:
                report.unpriced.append(
                    f"{record.label}: unpriced model(s) "
                    + ", ".join(unpriced)
                    + f" — the price table is as of {RATES_AS_OF} and this "
                    "session therefore reports no total at all"
                )

        # Counted rather than listed. Naming them would mean printing host
        # session identifiers, and terminal output gets pasted into commits —
        # the count is the actionable part anyway, since the repair is the same
        # single command whichever ones are missing.
        missing = sum(1 for h in current if h not in stored)
        if missing:
            report.unrecorded.append(
                f"{missing} host session(s) have not been ingested; "
                f"'invariant stats refresh' picks them up"
            )

        path = self.report_path()
        if not path.is_file():
            report.report.append(
                f"no rendered report at {self._relative(path)}; "
                f"'invariant stats report --write' creates it"
            )
        elif self.fingerprint() not in path.read_text(encoding="utf-8"):
            report.report.append(
                f"the report is stale — {self._relative(path)} was rendered "
                f"from a different store; 'invariant stats report --write' "
                f"re-renders it"
            )
        return report

    def _relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.manifest.root))
        except ValueError:
            return path.name

    # -- the report ----------------------------------------------------

    def fingerprint(self) -> str:
        """A short digest of exactly what is in the store.

        The anchor for the staleness contract (D4): a rendered artifact
        carrying this can be compared against the store without re-deriving
        anything, which is why writing the report is not optional.
        """
        digest = hashlib.sha256()
        for record in self.records():
            digest.update(
                f"{record.host_session_id}|{record.watermark}|"
                f"{int(record.archived)}|{record.session_name or ''}\n".encode()
            )
        return digest.hexdigest()[:FINGERPRINT_LENGTH]

    def report_path(self) -> Path:
        return self.manifest.path_for("reports") / REPORT_NAME

    def write_report(self) -> Path:
        path = self.report_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(), encoding="utf-8")
        return path

    def render(self) -> str:
        records = self.records()
        lines = [
            "# Session telemetry",
            "",
            "<!-- Generated by `invariant stats report --write`. Do not edit: "
            "regenerate. -->",
            f"<!-- store fingerprint: {self.fingerprint()} -->",
            "",
            f"Covers **{len(records)} session(s)**. Tokens are **measured** — "
            "they come from the host's own record of what it sent and "
            "received. Cost is **modelled**: tokens multiplied by a table "
            f"maintained by hand, correct as of **{RATES_AS_OF}**.",
            "",
            f"> Every currency figure below is a **floor**: it {COST_FLOOR_LABEL}. "
            "A subagent's own loop is billed and never written down — the "
            "parent record shows the launch and the returned result, never "
            "what was spent getting there. In a project that routes most work "
            "through agents, the unrecorded share is most of the work.",
            "",
        ]

        if not records:
            lines += [
                "No sessions have been ingested yet. "
                "`invariant stats refresh` reads the host's transcripts.",
                "",
            ]
            return "\n".join(lines)

        lines += self._session_table(records)
        lines += self._agent_table(records)
        lines += self._totals(records)
        return "\n".join(lines)

    def _session_table(self, records: list[StatRecord]) -> list[str]:
        lines = [
            "## Sessions",
            "",
            "| session | date | wall | active | tokens | cost (floor) |",
            "|---|---|---|---|---|---|",
        ]
        for record in records:
            stats = record.stats
            lines.append(
                "| {name} | {date} | {wall} | {active} | {tokens} | {cost} |".format(
                    name=record.label + (" · archived" if record.archived else ""),
                    date=stats.date or "unknown",
                    wall=format_duration(stats.wall_seconds),
                    active=format_duration(stats.active_seconds),
                    tokens=format_tokens(stats.total_usage.total_tokens),
                    cost=format_cost(stats.cost, unpriced=stats.unpriced_models),
                )
            )
        lines.append("")
        return lines

    def _agent_table(self, records: list[StatRecord]) -> list[str]:
        launches: dict[str, dict[str, int]] = {}
        for record in records:
            for name, agent in record.stats.agents.items():
                bucket = launches.setdefault(name, {})
                for model, count in agent.models.items():
                    bucket[model] = bucket.get(model, 0) + count
        if not launches:
            return []

        lines = [
            "## Agents",
            "",
            "Launches, the model each declared, and how often. **No cost "
            "column, deliberately** — subagent spend is unrecoverable from "
            "the transcripts, and estimating it from launch counts would "
            "produce an invented number wearing the same units as a measured "
            "one.",
            "",
            "| agent | launches | declared model(s) |",
            "|---|---|---|",
        ]
        for name in sorted(launches):
            models = launches[name]
            lines.append(
                f"| {name} | {sum(models.values())} | "
                + ", ".join(f"{m} x{c}" for m, c in sorted(models.items()))
                + " |"
            )
        lines.append("")
        return lines

    def _totals(self, records: list[StatRecord]) -> list[str]:
        total = Usage()
        cost = 0.0
        unpriced: list[str] = []
        for record in records:
            total = total + record.stats.total_usage
            estimate = record.stats.cost
            if estimate is None:
                unpriced.extend(record.stats.unpriced_models)
            else:
                cost += estimate

        lines = ["## Totals", ""]
        lines.append(f"- tokens: {format_tokens(total.total_tokens)}")
        lines.append(f"- cache read share of input: {total.cache_read_share:.1%}")
        if unpriced:
            lines.append(
                "- cost: "
                + format_cost(None, unpriced=unpriced)
                + " — a session containing an unpriced model reports no total, "
                "so this figure omits those sessions entirely rather than "
                "adding them in at zero"
            )
            lines.append(f"- cost of the priced sessions alone: {format_cost(cost)}")
        else:
            lines.append(f"- cost: {format_cost(cost)}")
        lines.append("")
        return lines

    def render_session(self, selector: str) -> str:
        """One session in full, including its per-state breakdown."""
        record = self.resolve(selector)
        if record is None:
            raise UsageError(
                f"no stats record matches {selector!r}; "
                f"'invariant stats report' lists what is in the store"
            )
        stats = record.stats
        lines = [
            f"{record.label}  {stats.date or 'date unknown'}"
            + ("  [archived]" if record.archived else ""),
            f"  wall     {format_duration(stats.wall_seconds)}",
            f"  active   {format_duration(stats.active_seconds)}",
            f"  messages {stats.message_count}",
            f"  tokens   {format_tokens(stats.total_usage.total_tokens)}"
            f"  ({stats.total_usage.cache_read_share:.1%} of input from cache)",
            f"  cost     {format_cost(stats.cost, unpriced=stats.unpriced_models)}"
            f"  ({COST_FLOOR_LABEL}; rates as of {RATES_AS_OF})",
            "",
        ]

        if record.states is None:
            # D3's asymmetry, stated rather than left blank. A blank invites
            # the reader to conclude the work was free.
            lines.append(f"  {NO_BREAKDOWN}")
        else:
            lines.append("  per state:")
            for entry in record.states:
                usage = entry.get("usage")
                spend = (
                    format_tokens(Usage.from_dict(usage).total_tokens)
                    if usage
                    else "unknown"
                )
                note = f"  ({entry['note']})" if entry.get("note") else ""
                lines.append(
                    f"    {entry['state']:<12} "
                    f"{format_duration(entry.get('elapsed')):>10}  "
                    f"{spend:>8} tokens{note}"
                )
            if record.states_partial:
                lines.append(
                    "  some pipelines in this session predate per-state "
                    "attribution; the rows above do not add up to the total"
                )

        if stats.agents:
            lines.append("")
            lines.append("  agents launched (no cost — see the report header):")
            for name, agent in sorted(stats.agents.items()):
                models = ", ".join(f"{m} x{c}" for m, c in sorted(agent.models.items()))
                lines.append(f"    {name:<20} {agent.launches:>3}  {models}")
        return "\n".join(lines)


def _spend_dict(spend: StateSpend) -> dict:
    return {
        "state": spend.state,
        "elapsed": spend.elapsed,
        "usage": spend.usage.to_dict() if spend.usage is not None else None,
        "note": spend.note,
    }


def _row_key(record: StatRecord):
    """Sorted by date then name, so a re-render of an unchanged store is
    byte-identical. A committed artifact that churns cannot be reviewed."""
    return (record.stats.date or "", record.session_name or "", record.host_session_id)
