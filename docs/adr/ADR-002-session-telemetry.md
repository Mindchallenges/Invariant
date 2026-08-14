# ADR-002 — Session telemetry: measured tokens, modelled cost, no clock times

- **Status:** Accepted
- **Date:** 2026-08-11
- **Supersedes:** nothing. Extends ADR-001's state model with a fifth domain.

## Context

The engine records what happened to a session's state and nothing about what
that state cost. A finished session leaves behind which transitions occurred,
which gates passed, and which files were declared — and no answer at all to
"how long did this take", "what did it cost", or "which part of the pipeline
consumed it". The discipline ADR-001 made inspectable is, so far, inspectable
only in kind and never in quantity.

The data to answer those questions already exists. The host writes a JSONL
transcript per session under `~/.claude/projects/<project>/<host-session>.jsonl`,
one record per exchange, and every assistant record carries the model, a full
`usage` block, and a timestamp. The host session identifier naming that file is
the same identifier `domains/session` already records as `host_session_id`. So
the join needs nothing new written down, and the measurement needs no model in
the loop — it is parsing, and parsing is deterministic. That last property was a
requirement, not a convenience: a measurement that varies between runs is not a
measurement.

Four properties of the available data shape every decision below.

**Cost is not in the data.** The transcripts carry token counts and no price.
Any currency figure is therefore computed from a table maintained by hand, and a
hand-maintained table is knowledge with an expiry date.

**Subagent spend is not in the data either.** Across the transcripts on disk,
zero records carry a sidechain marker while several hundred record a subagent
launch. A subagent's internal loop is billed and never written down: the parent
transcript shows the launch and the returned result, never what was spent
getting there. In a project that deliberately routes most work through agents,
the unrecorded share is most of the work.

**Recorded history carries no time.** Both `session.history` and
`pipeline.history` append `{from, to, reason}` and nothing else. Attributing
anything to a pipeline state requires knowing when the states began and ended,
and that was never recorded.

**Times may not be recorded at all.** The projects this library serves forbid
clock times in anything written down, and `privacy.patterns` lists
`clock_times`. The obvious repair for the previous paragraph — stamp each
transition with the current time — is therefore unavailable.

## Decision

Add a fifth state domain that measures sessions, storing raw records privately
and rendering a scrubbed report on demand.

The shape follows from the two hardest constraints rather than from
convenience. Because cost is modelled and tokens are measured, the two are kept
structurally apart and only one of them is ever called a record. Because clock
times may not be stored, per-state attribution is derived from durations and
from differences between counters, never from timestamps — which turns out to
be the better design independent of the constraint.

## Decisions recorded

### D1 — A fifth domain

Statistics live in `domains/stats`, a domain that is not a state machine. The
precedent is `memorystore`, already described as "a store with invariants and
repair operations" rather than an FSM, and the reason is the same: there is no
lifecycle to advance, but there is a set of invariants over a store and a set
of operations that repair it.

The alternatives were to keep the logic in `core/` with the CLI orchestrating,
or to fold it into `domains/session`. The first was rejected because the join
from a host session to an invariant session is knowledge of *what a session is*,
which `overview.md` reserves to domains; letting the CLI hold it trades a
documentation edit for an architecture violation. The second was rejected
because `domains/session` already owns isolation, binding, and the push barrier
across 752 lines, and measurement is a different responsibility with a different
lifetime.

The cost of this decision is that two documents and one diagram stop saying
"four domains". That is a real edit and it is the cheaper of the two prices.

### D2 — The layer inversion, forced by the dependency rule

`core/stats` owns the record types, the price table, the arithmetic, and a
`SampleSource` protocol. `hosts/` implements that protocol against the host's
own on-disk format. The CLI injects the concrete implementation into the domain.

There is no arrow from `core/` or `domains/` into `hosts/`, which is what makes
this consistent with `overview.md`'s rule that arrows point down and never back
up. The precedent is `core/items`, which already defines a model with pluggable
backends. This is recorded rather than debated because the docs decide it.

### D3 — Elapsed durations and usage watermarks, never a stored time

Each recorded transition gains two fields: `elapsed`, the seconds spent in the
state just left, and a **usage watermark**, the cumulative token counts observed
for that session at the moment of the transition.

Per-state duration is then the `elapsed` value directly. Per-state spend is the
difference between consecutive watermarks. Neither is a clock time, so the
privacy rule is satisfied by construction rather than by scrubbing.

This is a better design than the timestamp it replaces, and not only a
compliant one. Attributing spend by comparing a response's timestamp against a
transition's timestamp has a boundary problem: a response that spans a
transition must be assigned to one side arbitrarily, and the arbitrariness is
invisible in the output. A watermark has no boundary. Per-state spend is a
subtraction of two counts and is exact.

The cost is a permanent asymmetry. Every session recorded before this change
has neither field and can never acquire one, because the information was not
merely unstored — it was never observed. Those sessions report totals and no
breakdown, **and the report must say so explicitly rather than rendering a zero
or a blank.** A blank invites the reader to conclude the work was free.

**The first state has no earlier reading, so the machine records its own
baseline.** "The difference between consecutive watermarks" is silent on what
the first state is a difference *from*. Both `domains/pipeline` and
`domains/session` close that gap the same way: `usage_at_start` is read once,
at `start` / registration, and stored on the machine itself rather than as a
transition field — it is a property of *when the machine began*, not of any
one state it passed through. Without it the first state would still get a
correct `elapsed`, and D3 would still hold, but it would never get a spend,
which is a worse asymmetry than the one above because it falls on the state
every machine has at least one of. The cost is the same telemetry read as
every other watermark, taken one extra time; the failure mode is the same
`None`, rendered as "no breakdown" rather than a zero, never a reason the
`start` call itself can fail.

**A watermark is commensurable only with another from the same counter, so
each is stored with the counter it came from.** "The difference between
consecutive watermarks" silently assumes both readings count the same
transcript. That holds while the binding is stable and `adopt_host_session`
exists precisely to move it: a forced rebind mid-pipeline leaves the earlier
readings counted against the outgoing host session, and the difference across
the boundary is well-formed and measures nothing. Where the new transcript's
cumulative total is the higher, it is also *positive*, which is worse than
wrong — it wears the units and the formatting of a measurement. So every
reading is recorded beside the host session it was read from (`usage_host`,
`usage_at_start_host`), and the one state that spans a rebind is declined and
named rather than subtracted. Re-baselining instead was rejected: it would
overwrite readings that were honestly taken with readings nobody took.

The same rule settles what an *unmoved* watermark means. Two equal readings
say only that nothing was appended to the transcript in between, which is
what an entire subagent run looks like from the parent's side — the launch and
the result are recorded and the loop between them never is. A zero there would
say the work was free, so an unchanged watermark is declined like any other
unobservable spend. This is the same argument as the paragraph above, applied
to a difference of nothing rather than a difference of the wrong two things.

**"No breakdown" is three different facts and says which one it is.** A
machine that was never instrumented, one that has not left its first state
yet, and a session that has not run a pipeline at all all have nothing to
attribute, and only the first of them lost anything. Reporting the
pre-instrumentation cause for all three states a history that did not happen —
the failure this decision exists to prevent, reached through the sentence it
installed to prevent it. Each case carries its own reason from the layer that
knows it, and the original wording is kept for the case it was written for.

The rejected alternative was a separate append-only transitions log, which
leaves `history` untouched at the price of two records of the same fact — a
single-source-of-truth violation from the moment they disagree.

### D4 — A manifest-declared report store

`cli.md` states as a flat invariant that no command ever writes to the
repository outside the state root, the item store, and the memory store. A
committed report breaks it, so the invariant is amended — but generalised
rather than extended.

The manifest gains a report-store key, in exactly the way `items.path` and
`paths.memory` already declare writable locations, and the invariant becomes
"the state root and the stores the manifest declares". The alternative of
appending the report path to the existing list was rejected because it turns a
crisp guarantee into an enumeration that will grow again on the next feature;
the second exception is always easier to justify than the first.

Writing to stdout only was rejected for a subtler reason: `stats check` needs a
rendered artifact to compare against, so without a written report the
self-describing coverage header and the entire staleness contract lose their
anchor.

### D5 — A rebuildable cache, with an explicit act for permanence

The raw store holds one record per host session, keyed on a watermark of the
source transcript, and is rebuildable from the transcripts at any time. It may
be dropped by state collection like any other derived artifact.

This is a cache only while the transcripts survive. Once the host prunes them
the store becomes the only copy, so an explicit `stats archive` promotes a
record to permanent. The decision the user makes is therefore *what outlives the
host's retention*, made deliberately rather than discovered after a cleanup.

Keeping everything forever was rejected for unbounded growth and because a
wrong ingest becomes permanent rather than re-derivable. A pure cache with no
archive was rejected because the transcripts are currently the only copy of a
month of history, and a host-side cleanup would silently delete the trend data
the report exists to show.

### D6 — Reported cost is a floor and says so

Because subagent spend is unrecoverable from the transcripts, every currency
figure the report renders is a lower bound. Each is labelled "excludes subagent
spend", and the agents view reports launches, declared model, and frequency —
never cost.

The honest answer to "what did this session cost" is "at least this much", and
the report says that rather than implying a total. Estimating the missing share
from launch counts and result sizes was considered and rejected: it is modelling
stacked on modelling, producing an invented number wearing the same units as a
measured one, which is precisely the confusion the tokens/cost split exists to
prevent.

### D7 — What the committed report may contain

Rows are keyed on the invariant session's name and a bare date. Durations are
rendered as durations. Host session identifiers are UUIDs and stay in the
private state root, never in the report — `privacy.patterns` includes `tokens`
and `privacy.clean` blocks, so a rendered UUID would fail the project's own
gate.

### D8 — Two standing numbers, kept apart

Tokens are **measured**: they come from the host's own record of what it sent
and received, and they do not go stale. Cost is **modelled**: tokens multiplied
by a table that carries the date it was true, rendered next to every figure it
produces.

A model absent from the table is priced `None`, never zero, and a session
containing one reports no total at all rather than a partial one. A total that
silently omits a model's spend is worse than no total, because it looks like a
total.

## Consequences

- Two documents and one diagram stop saying "four domains".
- `cli.md`'s write-scope invariant is amended, and the manifest gains a key.
- A permanent, visible asymmetry between pre- and post-instrumentation sessions.
- The price table will go stale silently unless its as-of date is rendered
  beside every figure it produced. That rendering is the only defence.
- The library acquires a dependency on another program's undocumented on-disk
  format. Every field access tolerates absence and an unparseable record is
  skipped: a format change degrades a report and never breaks a session.
- Reported cost understates real cost by an unknown margin for as long as
  subagent spend is unrecorded. This is a known gap, not a defect to be fixed
  in this work.
- The baseline is commensurable with the deltas only while the binding is
  stable. A session registered while unbound has no baseline and can never
  acquire one — the reading it needed was the one true at registration — and a
  rebound session's earlier readings belong to a closed counter. Neither is
  repairable after the fact; both are *detectable*, which is why the counter
  is stored with the reading and why the affected state renders as
  unattributable rather than as a number.
- **The session machine's own telemetry is forward-looking.** `Session`
  records `usage_at_start` and stamps every transition with `elapsed` and a
  watermark, and nothing reads them: the rendered breakdown is built from the
  session's *pipelines*. This is recorded as a decision rather than left to be
  rediscovered. The fields stay, in step with the pipeline's — a session-level
  breakdown is a plausible next reader, the readings cannot be taken
  retrospectively if they were not taken at the time, and keeping the two
  machines identical means that reader inherits the commensurability guard
  above rather than the defect it was written for. The Notes below record the
  opposite defect — a field nothing could ever populate — and the distinction
  is exactly that: this one is populated with something real and correct, and
  waiting for a consumer.

## Alternatives considered

**Instrumenting the hooks instead of parsing transcripts.** Rejected on
capability: hook payloads carry no token counts at all, so a hook-only design
could report which tools ran and never what they cost — and it would start with
no history, accumulating one only from the day it shipped.

**A model summarising each session.** Rejected on the user's explicit
constraint and on determinism. Measurement is scripted here; the model's only
role is writing the parser once.

**Gating report regeneration at teardown.** Rejected deliberately: a gate that
fires at teardown makes teardown fail for a reporting reason, and teardown is
the step that must never acquire new ways to refuse.

## Notes

The prior-art sketch that preceded this decision was written inline, without an
agent and without the docs-first read, in violation of the standing rule that
coding happens through the agents. It was reviewed and largely rejected: the
price table and the measured/modelled split survive, and the rest is rewritten
against the protocol in D2. Its specific defects are recorded here because they
are the kind a review catches and an author does not — an accumulator that
silently under-counts on out-of-order input, and a field for per-state timing
that nothing could ever populate.
