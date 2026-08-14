"""Session telemetry: the records, the price table, and the arithmetic.

Host-agnostic by construction (ADR-002 D2). This module knows what a token
costs and how to add a session up; it knows nothing about where the numbers
came from. A host adapter implements :class:`SampleSource` against whatever
that host writes to disk, and the CLI injects it — so ``core`` never imports
``hosts`` and the arrows in ``docs/architecture/overview.md`` keep pointing one
way.

Two numbers with different standing live here, and keeping them apart is the
whole reason the module is shaped this way (D8):

* **Tokens are measured.** They come off the host's own record of what it sent
  and received. They do not go stale.
* **Cost is modelled.** It is tokens multiplied by a table maintained by hand,
  and a hand-maintained table is knowledge with an expiry date. Every table
  carries the date it was true, and that date is rendered beside every figure
  it produced.

Conflating the two would let a stale table quietly corrupt the record. Kept
apart, a table that goes out of date degrades the estimate and leaves the
measurement intact.

Two further properties are load-bearing rather than incidental:

* **Nothing here stores a clock time** (D3). Timestamps are read at the
  boundary, turned into *durations* and a *bare date*, and discarded. Grep this
  module for a stored field holding an epoch and there is none.
* **Every currency figure is a floor** (D6). Subagent spend is not in the data
  at all, so the honest answer to "what did this cost" is "at least this much",
  and :func:`format_cost` says so rather than leaving the reader to assume a
  total.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Mapping, Protocol, Sequence, runtime_checkable

#: The date the price table below was known to be correct. Rendered next to
#: every cost figure. When someone updates a rate they update this too — a
#: table with no date is indistinguishable from a table nobody has checked.
RATES_AS_OF = "2026-06-24"

#: Multipliers applied to the base input rate. These are properties of the
#: caching product rather than of any one model, which is why they are not in
#: the per-model table: a cache read costs a tenth of an input token whatever
#: produced it, and a cache write costs more the longer it is held.
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_5M_MULTIPLIER = 1.25
CACHE_WRITE_1H_MULTIPLIER = 2.0

#: How long a gap between messages before a session counts as idle rather than
#: working. Wall time answers "how long was this open"; the cap is what turns
#: that into "how long was anyone doing anything". Five minutes is a judgement,
#: not a measurement, and it is named here so the judgement stays visible.
IDLE_GAP_SECONDS = 300.0

#: Attached to every currency figure. Subagent spend is unrecoverable from the
#: transcripts — the parent record shows a launch and a returned result, never
#: what was spent getting there — so a total is a lower bound, and in a project
#: that routes most work through agents the unrecorded share is most of it.
COST_FLOOR_LABEL = "excludes subagent spend"

#: What a session recorded before ``elapsed`` and watermarks existed reports
#: instead of a breakdown. Deliberately not a zero and not a blank: a blank
#: invites the reader to conclude the work was free (D3).
NO_BREAKDOWN = "no breakdown available — recorded before per-state attribution"

#: A pipeline that started *after* the instrumentation landed and has not left
#: its first state yet. It has a baseline and an empty history: nothing was
#: lost, there is simply nothing to subtract yet. Saying it predates the
#: instrumentation would be a fabricated history (BUG_013), which is the
#: failure D3 exists to prevent, arrived at through D3's own sentence.
NO_BREAKDOWN_YET = (
    "no breakdown available yet — no pipeline state has been left, "
    "so there is nothing to attribute"
)

#: A session that has never started a pipeline. Same rule: what is missing is
#: the pipeline, not the measurement.
NO_BREAKDOWN_NO_PIPELINE = "no breakdown available — this session has not run a pipeline"

#: Why a breakdown was declined, as a code rather than a sentence: the record
#: is rebuildable but a stored sentence would still outlive a change of wording,
#: and the reader is owed the *reason*, which is the thing that differs.
DECLINED_PRE_INSTRUMENTATION = "pre_instrumentation"
DECLINED_NOT_YET = "not_yet"
DECLINED_NO_PIPELINE = "no_pipeline"

NO_BREAKDOWN_SENTENCES: Mapping[str, str] = {
    DECLINED_PRE_INSTRUMENTATION: NO_BREAKDOWN,
    DECLINED_NOT_YET: NO_BREAKDOWN_YET,
    DECLINED_NO_PIPELINE: NO_BREAKDOWN_NO_PIPELINE,
}

#: Attached to a state whose spend two readings cannot describe. Each names
#: what was seen rather than what it might mean, and none of them is a number:
#: the whole point of D3's asymmetry is that an unattributable spend renders as
#: unattributable and never as a plausible figure.
SPEND_NOTE_BACKWARDS = "usage watermark went backwards; spend not attributable"
SPEND_NOTE_REBOUND = "the host session changed here; spend not attributable"
SPEND_NOTE_UNMOVED = "usage watermark did not move; spend not observed"


@dataclass(frozen=True)
class Rate:
    """Dollars per million tokens, before cache multipliers."""

    input: float
    output: float


#: Base rates per million tokens. A model absent from this table is not priced
#: at zero — :func:`estimate_cost` returns ``None`` for it and the renderer
#: reports it as unpriced. Silently pricing an unknown model at nothing is the
#: exact failure this table exists to avoid.
PRICES: Mapping[str, Rate] = {
    "claude-fable-5": Rate(10.0, 50.0),
    "claude-mythos-5": Rate(10.0, 50.0),
    "claude-opus-5": Rate(5.0, 25.0),
    "claude-opus-4-8": Rate(5.0, 25.0),
    "claude-opus-4-7": Rate(5.0, 25.0),
    "claude-opus-4-6": Rate(5.0, 25.0),
    "claude-opus-4-5": Rate(5.0, 25.0),
    "claude-sonnet-5": Rate(3.0, 15.0),
    "claude-sonnet-4-6": Rate(3.0, 15.0),
    "claude-sonnet-4-5": Rate(3.0, 15.0),
    "claude-haiku-4-5": Rate(1.0, 5.0),
}


def _as_int(value) -> int:
    """A count, or zero. Never an exception.

    Every field here comes from another program's undocumented format, so a
    string, a ``None`` and an absent key all have to mean the same safe thing.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


@dataclass
class Usage:
    """Token counts for one model.

    ``cache_write_5m`` and ``cache_write_1h`` are separate because they are
    priced differently — 1.25x and 2x the input rate. Collapsing them is not a
    rounding error: across the corpus this was first measured on, 99.8% of
    cache creation is the 1-hour tier, and lumping it in with the 5-minute one
    under-reports by roughly $854.
    """

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input + other.input,
            self.output + other.output,
            self.cache_read + other.cache_read,
            self.cache_write_5m + other.cache_write_5m,
            self.cache_write_1h + other.cache_write_1h,
        )

    def __sub__(self, other: "Usage") -> "Usage":
        """Componentwise, and deliberately unclamped.

        A watermark is monotonic, so a negative component means the two
        readings do not describe the same run. Clamping would turn that into a
        plausible-looking small number; :func:`breakdown` checks the sign and
        reports the anomaly instead.
        """
        return Usage(
            self.input - other.input,
            self.output - other.output,
            self.cache_read - other.cache_read,
            self.cache_write_5m - other.cache_write_5m,
            self.cache_write_1h - other.cache_write_1h,
        )

    @property
    def total_input(self) -> int:
        """Everything billed at an input rate, cached or not."""
        return self.input + self.cache_read + self.cache_write_5m + self.cache_write_1h

    @property
    def total_tokens(self) -> int:
        return self.total_input + self.output

    @property
    def is_negative(self) -> bool:
        return min(
            self.input,
            self.output,
            self.cache_read,
            self.cache_write_5m,
            self.cache_write_1h,
        ) < 0

    @property
    def cache_read_share(self) -> float:
        """Fraction of input served from cache.

        The single most useful number here: it says whether the context resent
        every turn is being paid for once or every time.
        """
        total = self.total_input
        return self.cache_read / total if total else 0.0

    def to_dict(self) -> dict:
        return {
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_write_5m": self.cache_write_5m,
            "cache_write_1h": self.cache_write_1h,
        }

    @classmethod
    def from_dict(cls, data: Mapping | None) -> "Usage":
        data = data or {}
        return cls(
            input=_as_int(data.get("input")),
            output=_as_int(data.get("output")),
            cache_read=_as_int(data.get("cache_read")),
            cache_write_5m=_as_int(data.get("cache_write_5m")),
            cache_write_1h=_as_int(data.get("cache_write_1h")),
        )


def estimate_cost(usage: Usage, model: str) -> float | None:
    """Dollars for ``usage`` at ``model``'s rates, or ``None`` if unpriced.

    ``None`` is a real answer and callers must render it as one. A model this
    table has never heard of costs an unknown amount, not nothing.
    """
    rate = PRICES.get(model)
    if rate is None:
        return None
    per_in = rate.input / 1_000_000
    per_out = rate.output / 1_000_000
    return (
        usage.input * per_in
        + usage.output * per_out
        + usage.cache_read * per_in * CACHE_READ_MULTIPLIER
        + usage.cache_write_5m * per_in * CACHE_WRITE_5M_MULTIPLIER
        + usage.cache_write_1h * per_in * CACHE_WRITE_1H_MULTIPLIER
    )


# -- what a host produces ----------------------------------------------


@dataclass(frozen=True)
class Sample:
    """One billed model response, as observed by a host adapter.

    The unit of ingest; everything else in this module is arithmetic over
    these. ``timestamp`` is optional and stays optional all the way down: a
    record whose time cannot be parsed still spent tokens, and dropping it to
    keep the timing tidy would under-count the thing that is actually measured.
    """

    host_session_id: str
    timestamp: float | None
    model: str
    usage: Usage
    tools: tuple[str, ...] = ()
    #: Subagent launches seen in this response, as ``(agent_type, model)``.
    #: What ran and on which model is recorded; what it *spent* is not in the
    #: data at all, which is why D6 keeps cost off the agents view entirely.
    agents: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SourceRef:
    """One host session the source can produce samples for.

    ``watermark`` is a content fingerprint of the underlying artifact, not a
    modification time — a stored mtime would be a stored clock time, and a
    fingerprint answers the only question the cache asks ("has this changed")
    without recording when.
    """

    host_session_id: str
    watermark: str
    label: str = ""


@runtime_checkable
class SampleSource(Protocol):
    """The seam ``hosts/`` implements and the CLI injects (ADR-002 D2).

    Everything above this line is arithmetic; everything below it is one
    program's undocumented on-disk format. The protocol is what lets the
    arithmetic be tested without a disk and the format be replaced without
    touching the arithmetic.
    """

    def sessions(self) -> list[SourceRef]:
        """Every host session this source can currently see."""

    def find(self, host_session_id: str) -> SourceRef | None:
        """One host session by identifier, or ``None`` if it is not there."""

    def samples(self, ref: SourceRef) -> Iterable[Sample]:
        """Every billed response for ``ref``. Unparseable records are skipped."""


@runtime_checkable
class WatermarkSource(Protocol):
    """Cumulative usage for a host session, at the moment of asking (D3)."""

    def watermark(self, host_session_id: str | None) -> Usage | None:
        """The running total, or ``None`` when it cannot be observed."""


class TranscriptWatermarks:
    """A :class:`WatermarkSource` over any :class:`SampleSource`.

    Never raises. This is consulted while a transition is being *recorded*, and
    a telemetry read that could fail a transition would make measurement a
    reason for the pipeline to refuse — which is precisely the trade ADR-002
    declines when it rejects gating on the report. An unobservable watermark is
    ``None``, and ``None`` renders as "no breakdown", never as zero.
    """

    def __init__(self, source: SampleSource | None) -> None:
        self.source = source

    def watermark(self, host_session_id: str | None) -> Usage | None:
        if not host_session_id or self.source is None:
            return None
        try:
            ref = self.source.find(host_session_id)
            if ref is None:
                return None
            total = Usage()
            for sample in self.source.samples(ref):
                total = total + sample.usage
            return total
        except Exception:  # noqa: BLE001 - see the class docstring
            return None


# -- what a session adds up to -----------------------------------------


@dataclass
class AgentLaunches:
    """How often an agent ran and what it declared it was running on.

    No cost field, on purpose (D6). The parent transcript records the launch
    and the returned result and never the loop in between, so any currency
    figure here would be invented rather than measured — modelling stacked on
    modelling, wearing the same units as a measurement.
    """

    launches: int = 0
    models: dict[str, int] = field(default_factory=dict)

    def observe(self, model: str) -> None:
        self.launches += 1
        self.models[model] = self.models.get(model, 0) + 1

    def to_dict(self) -> dict:
        return {"launches": self.launches, "models": dict(self.models)}

    @classmethod
    def from_dict(cls, data: Mapping | None) -> "AgentLaunches":
        data = data or {}
        models = data.get("models")
        return cls(
            launches=_as_int(data.get("launches")),
            models={str(k): _as_int(v) for k, v in (models or {}).items()},
        )


@dataclass
class SessionStats:
    """Everything the transcript says about one host session.

    Note what is absent: no first-seen, no last-seen, no timestamp of any kind.
    Wall time and active time are *durations*, computed once at fold time from
    timestamps that are then dropped. There is nothing in a stored record for a
    privacy scan to find.
    """

    host_session_id: str
    #: A bare date in UTC, or ``None`` when no record carried a parseable time.
    #: UTC rather than local so the same transcript folds to the same date on
    #: every machine — a measurement that varies between runs is not one.
    date: str | None = None
    message_count: int = 0
    usage_by_model: dict[str, Usage] = field(default_factory=dict)
    tools: dict[str, int] = field(default_factory=dict)
    agents: dict[str, AgentLaunches] = field(default_factory=dict)
    wall_seconds: float = 0.0
    active_seconds: float = 0.0

    @property
    def total_usage(self) -> Usage:
        total = Usage()
        for usage in self.usage_by_model.values():
            total = total + usage
        return total

    @property
    def unpriced_models(self) -> list[str]:
        return sorted(m for m in self.usage_by_model if m not in PRICES)

    @property
    def cost(self) -> float | None:
        """Summed per-model estimate, or ``None`` if any model is unpriced.

        Deliberately all-or-nothing (D8). A total that silently omits one
        model's spend is worse than no total, because it looks like a total.
        """
        running = 0.0
        for model, usage in self.usage_by_model.items():
            estimate = estimate_cost(usage, model)
            if estimate is None:
                return None
            running += estimate
        return running

    @property
    def agent_launches(self) -> int:
        return sum(a.launches for a in self.agents.values())

    def to_dict(self) -> dict:
        return {
            "host_session_id": self.host_session_id,
            "date": self.date,
            "message_count": self.message_count,
            "usage_by_model": {m: u.to_dict() for m, u in self.usage_by_model.items()},
            "tools": dict(self.tools),
            "agents": {name: a.to_dict() for name, a in self.agents.items()},
            "wall_seconds": self.wall_seconds,
            "active_seconds": self.active_seconds,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "SessionStats":
        return cls(
            host_session_id=str(data.get("host_session_id") or ""),
            date=data.get("date") or None,
            message_count=_as_int(data.get("message_count")),
            usage_by_model={
                str(model): Usage.from_dict(raw)
                for model, raw in (data.get("usage_by_model") or {}).items()
            },
            tools={str(k): _as_int(v) for k, v in (data.get("tools") or {}).items()},
            agents={
                str(name): AgentLaunches.from_dict(raw)
                for name, raw in (data.get("agents") or {}).items()
            },
            wall_seconds=float(data.get("wall_seconds") or 0.0),
            active_seconds=float(data.get("active_seconds") or 0.0),
        )


def fold(samples: Iterable[Sample]) -> dict[str, SessionStats]:
    """Fold samples into one :class:`SessionStats` per host session.

    **Order-independent by construction.** The prior-art sketch accumulated
    active time as samples arrived and silently under-counted whenever they
    did not arrive in order — the kind of defect that produces a plausible
    number rather than an error. Here every session's samples are bucketed
    first and sorted second, so the result is a function of the *set* of
    samples and of nothing else.
    """
    buckets: dict[str, list[Sample]] = {}
    for sample in samples:
        buckets.setdefault(sample.host_session_id, []).append(sample)

    out: dict[str, SessionStats] = {}
    for host_session_id, bucket in buckets.items():
        out[host_session_id] = fold_session(host_session_id, bucket)
    return out


def fold_session(host_session_id: str, samples: Sequence[Sample]) -> SessionStats:
    """Fold every sample into one record under ``host_session_id``.

    Used where the *file* is the unit rather than the record: a transcript is
    named after its host session, and records inside it that name a different
    one are resume and sidechain artifacts belonging to the same run. Folding
    them under the file's identity keeps a session's spend in one row instead
    of scattering it across rows nobody would think to add up.
    """
    stats = SessionStats(host_session_id=host_session_id)

    for sample in samples:
        stats.message_count += 1
        existing = stats.usage_by_model.get(sample.model)
        stats.usage_by_model[sample.model] = (
            sample.usage if existing is None else existing + sample.usage
        )
        for tool in sample.tools:
            stats.tools[tool] = stats.tools.get(tool, 0) + 1
        for agent_type, model in sample.agents:
            stats.agents.setdefault(agent_type, AgentLaunches()).observe(model)

    times = sorted(s.timestamp for s in samples if s.timestamp is not None)
    if times:
        stats.wall_seconds = max(0.0, times[-1] - times[0])
        stats.active_seconds = sum(
            min(later - earlier, IDLE_GAP_SECONDS)
            for earlier, later in zip(times, times[1:])
        )
        stats.date = _bare_date(times[0])
    return stats


def _bare_date(timestamp: float) -> str | None:
    """A date and never a time. The privacy rule allows exactly this much."""
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


# -- per-state attribution ---------------------------------------------


@dataclass
class StateSpend:
    """What one pipeline or session state cost, as far as it can be known.

    ``elapsed`` and ``usage`` fail independently, and that is the point. A
    transition recorded before watermarks existed still knows how long it took;
    reporting the pair as all-or-nothing would throw away a measurement that
    was actually taken.
    """

    state: str
    elapsed: float | None = None
    usage: Usage | None = None
    note: str = ""

    def cost(self, model: str) -> float | None:
        if self.usage is None:
            return None
        return estimate_cost(self.usage, model)


def breakdown(
    history: Sequence[Mapping],
    baseline: Usage | None = None,
    baseline_host: str | None = None,
) -> list[StateSpend] | None:
    """Per-state durations and spend, or ``None`` when there is no breakdown.

    Three answers, and telling them apart is load-bearing:

    * a **list** — every state that has been left, attributed as far as the
      readings allow;
    * an **empty list** — a machine that has not left a state yet. There is
      nothing to attribute and nothing was lost;
    * ``None`` — a history recorded before D3, where the information was never
      observed and no later work can supply it. One entry missing ``elapsed``
      poisons the whole breakdown rather than producing a partial one with an
      invisible hole in it.

    The empty list used to be a ``None`` as well, which made the report state a
    pre-instrumentation cause for every pipeline that had merely just started
    (BUG_013). "We did not measure this", "this measured zero" and "there is
    nothing to measure yet" are three facts, and the caller renders each
    differently.

    ``baseline`` is the watermark at the moment the machine started, and
    ``baseline_host`` the host session it was counted against. Without a
    baseline the first state has a duration and no spend, which is correct:
    there is no earlier reading to subtract.
    """
    if any(entry.get("elapsed") is None for entry in history):
        return None

    spends: list[StateSpend] = []
    previous = baseline
    previous_host = baseline_host
    for entry in history:
        raw = entry.get("usage")
        current = Usage.from_dict(raw) if isinstance(raw, Mapping) else None
        current_host = entry.get("usage_host") or None
        spend = StateSpend(
            state=str(entry.get("from") or "?"),
            elapsed=float(entry["elapsed"]),
        )
        if current is not None and previous is not None:
            spend.note = _incommensurable(
                previous, previous_host, current, current_host
            )
            if not spend.note:
                spend.usage = current - previous
        spends.append(spend)
        if current is not None:
            previous, previous_host = current, current_host
    return spends


def _incommensurable(
    previous: Usage,
    previous_host: str | None,
    current: Usage,
    current_host: str | None,
) -> str:
    """Why these two readings cannot be subtracted, or ``""`` when they can.

    Each case ends the same way — no ``usage``, a note naming what was seen —
    because a spend that cannot be measured must not render as a number. The
    order is by what the readings prove, strongest first: a counter that
    changed identity makes the sign of the difference meaningless, so it is
    asked before the sign is.
    """
    if previous_host and current_host and previous_host != current_host:
        # Two cumulative counters against two different transcripts. The
        # subtraction is well-formed and measures nothing, and where the new
        # counter is the higher it looks exactly like a measurement (BUG_014).
        return SPEND_NOTE_REBOUND
    delta = current - previous
    if delta.is_negative:
        # Two readings that cannot describe one monotonic run. Clamping would
        # hand the reader a small plausible number instead of a visible anomaly.
        return SPEND_NOTE_BACKWARDS
    if delta.total_tokens == 0:
        # Nothing was appended to the transcript between the two readings —
        # which is the ordinary case for a subagent's whole run, since its loop
        # is never written to the parent's transcript. A zero here would read
        # as "this work was free", the conclusion D3 exists to prevent.
        return SPEND_NOTE_UNMOVED
    return ""


# -- rendering helpers -------------------------------------------------


def no_breakdown_sentence(reason: str | None) -> str:
    """What to print instead of a breakdown, for the reason there is none.

    An unknown or absent reason falls back to :data:`NO_BREAKDOWN`. That is
    what a record written before this distinction existed carries, and its
    sessions really are the ones the original sentence was written for.
    """
    return NO_BREAKDOWN_SENTENCES.get(reason or "", NO_BREAKDOWN)


def format_duration(seconds: float | None) -> str:
    """A duration, in a shape that can never be read as a clock time.

    No colons anywhere. ``12:04`` would be flagged by the project's own
    ``clock_times`` privacy pattern, and a report that fails the gate it ships
    beside is not one this library may write.
    """
    if seconds is None:
        return "unknown"
    total = int(round(max(0.0, seconds)))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours:02d}h"


def format_cost(value: float | None, *, unpriced: Sequence[str] = ()) -> str:
    """A currency figure, always marked as the floor it is (D6).

    ``>=`` is on every figure rather than in a footnote alone, because a table
    cell gets quoted on its own and the footnote does not travel with it.
    """
    if value is None:
        if unpriced:
            return "unpriced (" + ", ".join(sorted(set(unpriced))) + ")"
        return "unpriced"
    return f">= ${value:,.2f}"


def format_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)
