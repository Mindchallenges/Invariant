"""The sweep's pure half: its record, the diff against the last one, and age.

A sweep runs the whole suite on a cadence and files what it finds (ADR-010
D4). Everything here is arithmetic over records and keys, apart from reading
committed records through ``core/git`` — the same shape as ``core/plan``, so
that a rule (``sweep.fresh``, and the advisory coverage verdict) can ask about
the last sweep without importing a domain. Running anything, filing anything
and committing anything is ``domains/sweep``'s.

A finding is named by one KEY, and the key is what deduplicates it across
sweeps and machines: ``test:<id>`` a test red again when run alone,
``flaky:<id>`` one green alone, ``floor:<path>`` a held file under the floor,
and ``suite:red`` a red run whose failing tests cannot be named.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from . import git
from .manifest import project_relative

RECORD_VERSION = 1

#: At most this many new reds are re-run alone (ruling 8). Beyond it the run is
#: read as systemic — a broken build, a fixture, an environment leak — and one
#: ``suite:red`` item stands for all of them.
RERUN_CAP = 20

SUITE_RED = "suite:red"

#: The manifest command keys a sweep may run. The record names the key, never
#: the resolved command: that carries a per-run path under the state root.
COMMAND_KEYS = ("coverage", "test")

#: Item statuses that claim the defect is gone. An item in one of these whose
#: key is still red is a regression signal, named as such and never re-filed.
_CLAIMED_FIXED = frozenset({"fixed", "verified"})


def test_key(test_id: str) -> str:
    return f"test:{test_id}"


def flaky_key(test_id: str) -> str:
    return f"flaky:{test_id}"


def floor_key(path: str) -> str:
    return f"floor:{path}"


def test_id_of(key: str) -> str | None:
    """The test identity a ``test:`` or ``flaky:`` key names, else ``None``."""
    for prefix in ("test:", "flaky:"):
        if key.startswith(prefix):
            return key[len(prefix):]
    return None


def claiming_keys(key: str) -> tuple[str, ...]:
    """Every key an open item may carry that claims ``key`` (D7).

    A test is one defect whether it was filed red or flaky, so either form
    suppresses both: an item filed by hand claims a flaky test by carrying
    ``sweep_key: flaky:<id>``, and the sweep will not file its red form beside it.
    """
    test_id = test_id_of(key)
    if test_id is None:
        return (key,)
    return (test_key(test_id), flaky_key(test_id))


#: Where the records live when ``paths.sweeps`` is not set (ADR-010 D9).
DEFAULT_DIRECTORY = "docs/sweeps"

#: How late a sweep may be before it is called stale, when the manifest does
#: not say (ruling 5): the warning starts after day 10 on a weekly cadence.
DEFAULT_GRACE_DAYS = 3


def directory_of(manifest: Any) -> str:
    """``paths.sweeps``, spelled project-relative: the one spelling every reader uses."""
    declared = manifest.get("paths.sweeps") or DEFAULT_DIRECTORY
    return project_relative(declared, [Path(manifest.root)]) or DEFAULT_DIRECTORY


def cadence_of(manifest: Any) -> tuple[int | None, int]:
    """``(cadence_days, grace_days)`` as configured; ``None`` is no cadence."""
    schedule = manifest.get("methodology.gates.schedule") or {}
    return schedule.get("cadence_days"), int(schedule.get("grace_days", DEFAULT_GRACE_DAYS))


def utc_today() -> date:
    """Today as a bare UTC date, the only clock a record's age is read against."""
    return datetime.now(timezone.utc).date()


class SweepRecordError(ValueError):
    """A committed sweep record that does not have the version 1 shape."""


#: How many hex digits of a commit hash a sweep persists (BUG_090). A full
#: forty-digit hash is exactly what the opt-in ``device_ids`` privacy pattern
#: matches, so a record, a filed item or a commit message carrying one is
#: refused by the scan a project with that pattern runs on the sweep's output.
SHORT_HASH = 12

#: The shortest prefix a reader takes as naming a commit, git's own default.
MIN_PREFIX = 7


def short_hash(commit: str) -> str:
    """The first :data:`SHORT_HASH` hex digits: the one spelling a sweep writes."""
    return commit[:SHORT_HASH]


def same_commit(recorded: str, named: str) -> bool:
    """Whether ``named`` names the commit a record holds, by prefix either way.

    Records written before BUG_090 hold full hashes and later ones hold short
    hashes, so neither side is assumed to be the longer: a short name matches
    an old full hash, and a full one pasted from ``git log`` matches a new short
    hash. Fewer than :data:`MIN_PREFIX` digits on either side names nothing.
    """
    recorded, named = recorded.strip().lower(), named.strip().lower()
    if min(len(recorded), len(named)) < MIN_PREFIX:
        return False
    return recorded.startswith(named) or named.startswith(recorded)


def record_name(ran_on: str, head: str) -> str:
    """``<date>-<short head>.json``: one file per sweep."""
    return f"{ran_on}-{short_hash(head)}.json"


@dataclass(frozen=True)
class SweepRecord:
    """One sweep, as committed (ADR-010 D9).

    Short hashes, a bare date, project-relative paths, test identities and a
    manifest command key: nothing that is a clock time, a host, a session, an
    absolute path or a full commit hash, so the record is privacy-clean by
    construction under every shipped pattern (and scanned anyway before it is
    committed). ``head`` and ``previous`` are written short; a record committed
    before BUG_090 holds full hashes and is read as it is, so a reader compares
    them through :func:`same_commit`.
    """

    seq: int
    ran_on: str
    branch: str
    head: str
    previous: str | None
    command: str
    executed: int | None
    failed: int | None
    #: Whether the run exited green. Not derivable from ``failed``: an adapter
    #: that counts nothing reports ``None`` for both outcomes.
    passed: bool
    failing: list[str] | None
    flaky: list[str] = field(default_factory=list)
    files: dict[str, float] = field(default_factory=dict)
    floor: float = 0.0
    hazards: list[str] = field(default_factory=list)
    filed: list[dict] = field(default_factory=list)
    still_broken: list[dict] = field(default_factory=list)
    recovered: list[str] = field(default_factory=list)
    version: int = RECORD_VERSION

    @property
    def name(self) -> str:
        return record_name(self.ran_on, self.head)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, raw: Any) -> SweepRecord:
        """A record from its committed form, or :class:`SweepRecordError`.

        Strict, because a record is a BASELINE: a field read wrongly moves
        every finding of the next sweep between "new" and "still broken".
        """
        if not isinstance(raw, Mapping):
            raise SweepRecordError("a sweep record is a JSON object")
        if raw.get("version") != RECORD_VERSION:
            raise SweepRecordError(f"unsupported sweep record version {raw.get('version')!r}")
        missing = [f.name for f in fields(cls) if f.name not in raw]
        if missing:
            raise SweepRecordError(f"missing field(s): {', '.join(missing)}")
        checks = (
            ("seq", lambda v: isinstance(v, int) and not isinstance(v, bool)),
            ("ran_on", lambda v: isinstance(v, str)),
            ("branch", lambda v: isinstance(v, str)),
            ("head", lambda v: isinstance(v, str) and bool(v)),
            ("previous", lambda v: v is None or isinstance(v, str)),
            ("command", lambda v: v in COMMAND_KEYS),
            ("passed", lambda v: isinstance(v, bool)),
            ("failing", lambda v: v is None or _strings(v)),
            ("flaky", _strings),
            ("files", lambda v: isinstance(v, Mapping)),
            ("hazards", _strings),
            ("recovered", _strings),
            ("filed", lambda v: isinstance(v, list)),
            ("still_broken", lambda v: isinstance(v, list)),
        )
        for name, ok in checks:
            if not ok(raw[name]):
                raise SweepRecordError(f"field {name!r} has the wrong shape: {raw[name]!r}")
        try:
            files = {str(k): float(v) for k, v in raw["files"].items()}
            floor = float(raw["floor"])
        except (TypeError, ValueError) as exc:
            raise SweepRecordError(f"a figure is not a number: {exc}") from None
        values = {f.name: raw[f.name] for f in fields(cls)}
        values.update(files=files, floor=floor)
        return cls(**values)


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def read_records(
    cwd: Path, ref: str, directory: str
) -> tuple[list[SweepRecord], list[str]] | None:
    """Every committed record under ``directory`` at ``ref``, and the unreadable.

    Read from git, never from the working tree (D9): a record left uncommitted
    by a refused commit is not a baseline, and a worktree cut before the
    latest sweep still sees it through its base branch. Returns ``(records,
    paths that are not readable records)``; ``None`` when the ref cannot be
    read at all. Files that are not ``.json`` are not records and are ignored.
    """
    directory = str(directory).strip().removeprefix("./").strip("/")
    paths = git.tree_paths(cwd, ref, directory)
    if paths is None:
        return None
    records: list[SweepRecord] = []
    unreadable: list[str] = []
    for path in sorted(paths):
        if not path.endswith(".json"):
            continue
        text = git.file_at(cwd, ref, path)
        try:
            records.append(SweepRecord.from_dict(json.loads(text or "")))
        except (ValueError, TypeError):
            unreadable.append(path)
    return records, unreadable


def latest(records: Sequence[SweepRecord], branch: str) -> SweepRecord | None:
    """The record with the highest ``seq`` among those on ``branch``."""
    mine = [r for r in records if r.branch == branch]
    return max(mine, key=lambda r: r.seq) if mine else None


@dataclass(frozen=True)
class LastSweep:
    """The last committed sweep on one branch, as a reader of it sees it.

    ``readable`` is False when git could not read the branch at all, which is
    a different answer from "no sweep recorded": the first is a question
    nobody could answer, and neither is a fresh sweep.
    """

    branch: str | None
    record: SweepRecord | None
    readable: bool = True
    unreadable: list[str] = field(default_factory=list)


def last_on(cwd: Path, branch: str | None, directory: str) -> LastSweep:
    """The latest record committed on ``branch``, read at that branch's tip.

    Read from the branch, not ``HEAD``: a session worktree is on its own
    branch, cut before the latest sweep landed on its base, and freshness is a
    question about the base (D9). ``branch`` ``None`` (a detached HEAD with no
    session to name a base) reads nothing and says so.
    """
    if not branch:
        return LastSweep(None, None, readable=False)
    read = read_records(cwd, branch, directory)
    if read is None:
        return LastSweep(branch, None, readable=False)
    records, unreadable = read
    return LastSweep(branch, latest(records, branch), True, unreadable)


# -- the diff ----------------------------------------------------------------


def red_keys(passed: bool, failing: Sequence[str] | None) -> set[str]:
    """The keys a run's reds are named by.

    A red run whose failing tests the adapter could not name — ``None``, or an
    empty list from a run that failed anyway — is one ``suite:red``: there is
    no identity to file under, and inventing one would split one defect into
    several or merge several into one.
    """
    if passed:
        return set()
    if not failing:
        return {SUITE_RED}
    return {test_key(t) for t in failing}


def _recorded_reds(record: SweepRecord) -> set[str]:
    """The red keys a committed record stands for.

    As :func:`red_keys`, except that a red run whose every red was found flaky
    recorded no failing test and stands for nothing red: its flakes are in
    ``flaky``, and reading it as ``suite:red`` would report a recovery from a
    red that was never filed.
    """
    if not record.passed and not record.failing and record.failing is not None and record.flaky:
        return set()
    return red_keys(record.passed, record.failing)


def _floor_keys(below: Mapping[str, float] | None) -> set[str]:
    return {floor_key(p) for p in (below or {})}


@dataclass(frozen=True)
class Diff:
    """This sweep's findings against the last one, by key.

    ``baseline`` is a first sweep on the branch: nothing is new, nothing is
    filed (ruling 7). ``unbaselined`` holds floor keys with no previous figures
    to compare against — the last sweep ran no coverage — which are reported
    and never filed as drops.
    """

    baseline: bool
    current: list[str]
    new: list[str]
    still_broken: list[str]
    recovered: list[str]
    unbaselined: list[str]
    floor_drops: dict[str, float]

    @property
    def new_test_ids(self) -> list[str]:
        return sorted(t for k in self.new if (t := test_id_of(k)) is not None)


def diff(
    previous: SweepRecord | None,
    *,
    passed: bool,
    failing: Sequence[str] | None,
    below_now: Mapping[str, float],
    below_before: Mapping[str, float] | None,
) -> Diff:
    """Classify this sweep's findings against ``previous``.

    ``below_now`` and ``below_before`` are the held files under the floor now
    and at the last sweep, judged by ONE policy (``core.rules.sweep_below_floor``)
    so a change of policy between sweeps cannot read as a drop. A floor drop is
    a file below now that was not below, or did not exist, last time (D4 step
    5). ``below_before`` is ``None`` when the last sweep measured no coverage.
    """
    reds = red_keys(passed, failing)
    current = reds | _floor_keys(below_now)
    if previous is None:
        return Diff(True, sorted(current), [], [], [], [], {})

    before = _recorded_reds(previous) | _floor_keys(below_before)
    unbaselined: set[str] = set()
    if below_before is None:
        unbaselined = _floor_keys(below_now)
    comparable = current - unbaselined
    new = comparable - before
    drops = {p: below_now[p] for p in below_now if floor_key(p) in new}
    return Diff(
        baseline=False,
        current=sorted(current),
        new=sorted(new),
        still_broken=sorted(comparable & before),
        recovered=sorted(before - current),
        unbaselined=sorted(unbaselined),
        floor_drops=drops,
    )


def needs_reruns(found: Diff) -> bool:
    """Whether each new red is re-run alone, or the cap has been passed."""
    return not found.baseline and 0 < len(found.new_test_ids) <= RERUN_CAP


@dataclass(frozen=True)
class Settled:
    """The new findings once each new red's isolated re-run is known (D7)."""

    to_file: list[str]
    confirmed: list[str]
    flaky: list[str]
    over_cap: bool
    #: How many new test reds there were, re-run or not.
    reds: int = 0


def settle(found: Diff, red_again: Mapping[str, bool] | None) -> Settled:
    """Turn new test reds into ``test:`` and ``flaky:`` keys, or one ``suite:red``.

    ``red_again`` maps a test id to whether its re-run was red. A test with no
    answer is kept as a red: a re-run that never happened proves nothing about
    flakiness. Beyond :data:`RERUN_CAP` no per-test key is filed at all.
    """
    if found.baseline:
        return Settled([], [], [], False)
    others = [k for k in found.new if test_id_of(k) is None]
    ids = found.new_test_ids
    if len(ids) > RERUN_CAP:
        keys = sorted(set(others) | {SUITE_RED})
        return Settled(keys, [], [], True, len(ids))
    answers = red_again or {}
    flaky = [t for t in ids if answers.get(t) is False]
    confirmed = [t for t in ids if t not in flaky]
    keys = others + [test_key(t) for t in confirmed] + [flaky_key(t) for t in flaky]
    return Settled(sorted(keys), confirmed, flaky, False, len(ids))


def dedup(
    keys: Sequence[str], open_items: Sequence[tuple[str, str, str]]
) -> tuple[list[str], list[dict]]:
    """Split ``keys`` into those to file and those an open item already holds.

    ``open_items`` is ``(sweep_key, item id, status)`` for every OPEN item —
    the item machine's non-terminal states, so ``fixed`` and ``verified``
    count (D4 step 7). A held key whose item claims the defect is gone is
    marked ``regression``.
    """
    by_key: dict[str, tuple[str, str]] = {}
    for key, item, status in open_items:
        by_key.setdefault(key, (item, status))
    to_file: list[str] = []
    held: list[dict] = []
    for key in keys:
        claim = next((by_key[k] for k in claiming_keys(key) if k in by_key), None)
        if claim is None:
            to_file.append(key)
            continue
        item, status = claim
        held.append(
            {"key": key, "item": item, "status": status, "regression": status in _CLAIMED_FIXED}
        )
    return to_file, held


# -- age (read by sweep.fresh) -----------------------------------------------


def age_days(ran_on: str, today: date) -> int | None:
    """Whole days from a record's bare date to ``today``; ``None`` if unreadable."""
    try:
        return (today - date.fromisoformat(str(ran_on))).days
    except ValueError:
        return None


def overdue(age: int | None, *, cadence_days: int | None, grace_days: int) -> bool:
    """Whether a sweep this old is past its cadence plus grace (D6).

    No cadence is never overdue. No sweep at all, or one whose age cannot be
    read, is overdue: evidence that is not held is not fresh.
    """
    if cadence_days is None:
        return False
    if age is None:
        return True
    return age > cadence_days + grace_days
