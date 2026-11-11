"""The store's docs lock: units, their global order, one lease per session (ADR-014 D5).

A session takes an exclusive lock on the store docs it reads for the whole
read-design-write cycle (rulings 13 and 16). The unit of the lock is a
declared area, ``area:<name>``, or, for a path outside every declared area,
that one file, ``file:<store-relative path>``. Keys are ordered as plain
strings, and a session may WAIT only for a unit whose key sorts after every
unit it holds, so two sessions can never each hold what the other waits for.

The lease record is one JSON document in the store's git directory,
``<store>/.git/invariant/locks/docs.json``, untracked, shared by every session
of every repository the store serves. Every change to it is one
read-modify-write under :meth:`JsonStore.locked`, so a release and the
hand-over to the head of that unit's queue are one write: no race decides who
is next. The lease outlives the short CLI and hook processes that act for it,
which is why it is a record and not a held ``flock``::

    {"version": 1,
     "leases": {"<lease>": {"session", "identity", "host", "state_root",
                            "created", "heartbeat", "wants": [<unit>...]}},
     "units":  {"<unit>": {"holder": "<lease>", "since", "sha",
                           "queue": ["<lease>", ...]}}}

``wants`` is what the lease still has to take, sorted: the unit it is queued
for first, then the ones that follow it. A unit's entry exists while somebody
holds it; its ``since`` and ``sha`` open the holder's span, which the session
domain closes when it releases.

This module knows nothing of hosts and nothing of the oracle: whether a
holder's host process is alive is asked through ``alive``, a callable the CLI
injects. It never fetches (D7, increment 5).
"""

from __future__ import annotations

import errno
import os
import re
import select
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from . import git
from .errors import ManifestError, StateError, UsageError
from .identity import common_dir_of
from .store import JsonStore

#: Where the lease record lives, under the store's git common directory.
LOCK_DIR = "invariant"
LOCK_REL = "locks/docs.json"
#: The named pipes ``--wait`` blocks on, one per waiting lease.
WAKE_DIR = "locks/wake"

#: The ruled default lease (ruling 16).
DEFAULT_LEASE_MINUTES = 30
#: A heartbeat is written at most this often.
HEARTBEAT_SECONDS = 60

#: The ``store.docs_lock`` keys.
DOCS_LOCK_KEYS = ("areas", "lease_minutes")

#: Records that are data, not policy: outside the lock (ruling 20 for
#: memory; D5's recommendation for the backlog, reports and sweeps).
DATA_RECORDS = ("backlog", "memory", "reports", "sweeps")
#: Records that are policy: what an agent designs against, so a read or a
#: write of one is held to the lock (D5, "What the lock covers").
POLICY_RECORDS = ("docs", "guidelines")

#: How :func:`classify` places a path, beside the unit it names.
OUTSIDE = "outside"      # not in the store
DATA = "data"            # a data record kept in the store: no unit (ruling 20)
UNGOVERNED = "ungoverned"  # in the store, under no policy record or area
GOVERNED = "governed"    # a policy path: its unit must be held

AREA = "area:"
FILE = "file:"
_AREA_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

#: The rule id a takeover is recorded under in both sessions' overrides.
TAKEOVER_RULE = "store.docs_lock"


@dataclass(frozen=True)
class DocsLockConfig:
    """``store.docs_lock`` as read: name -> path patterns, and the lease."""

    areas: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    lease_minutes: int = DEFAULT_LEASE_MINUTES


@dataclass(frozen=True)
class Holder:
    """Who a lease acts for: the session, its repo identity, its host, and
    the state root its session record lives under."""

    session: str
    identity: str | None
    host: str | None
    state_root: str


@dataclass
class Outcome:
    """What one call to the lock did, and where the lease now stands."""

    lease: str | None = None
    granted: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    #: The unit the lease is queued for, with its position and holder.
    waiting: dict[str, Any] | None = None
    #: The units the lease will take, in order, after the one it waits for.
    pending: list[str] = field(default_factory=list)
    #: Units released by a back-off (ruling 21).
    backed_off: list[str] = field(default_factory=list)
    #: Units released by this call: their closed spans.
    closed: list[dict] = field(default_factory=list)
    #: unit -> the session it passed to.
    handed: dict[str, str] = field(default_factory=dict)
    #: The spans this call opened for the caller's own lease.
    opened: list[dict] = field(default_factory=list)

    @property
    def queued(self) -> dict[str, dict[str, Any]]:
        if not self.waiting:
            return {}
        return {
            self.waiting["unit"]: {
                "position": self.waiting["position"],
                "holder": self.waiting["holder"],
            }
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "lease": self.lease,
            "granted": self.granted,
            "held": self.held,
            "queued": self.queued,
            "pending": self.pending,
            "backed_off": [s["unit"] for s in self.closed if s.get("backed_off")],
            "released": [s["unit"] for s in self.closed if not s.get("backed_off")],
            "handed": self.handed,
        }


# -- the configuration ------------------------------------------------------


def parse_docs_lock(raw: Any, path: Path) -> DocsLockConfig:
    """``store.docs_lock`` checked, or refused naming what is wrong.

    Areas may not overlap: a pattern of one area that matches another's
    pattern, read as a path, is refused naming both, so every path has one
    unit. Overlaps no pattern text shows (``docs/*.md`` beside ``docs/x*``)
    are refused when a path that both match is first resolved (:func:`unit_of`).
    """
    where = f"the store config {path}: store.docs_lock"
    if raw is None:
        return DocsLockConfig()
    if not isinstance(raw, Mapping):
        raise ManifestError(f"{where} must be a mapping")
    for key in raw:
        if key not in DOCS_LOCK_KEYS:
            raise ManifestError(
                f"{where}.{key} is not read; it reads {', '.join(DOCS_LOCK_KEYS)}"
            )
    minutes = raw.get("lease_minutes", DEFAULT_LEASE_MINUTES)
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1:
        raise ManifestError(f"{where}.lease_minutes must be a whole number of minutes, at least 1")
    areas_raw = raw.get("areas")
    areas_raw = {} if areas_raw is None else areas_raw
    if not isinstance(areas_raw, Mapping):
        raise ManifestError(f"{where}.areas must map an area's name to its path patterns")
    areas: dict[str, tuple[str, ...]] = {}
    for name, patterns in areas_raw.items():
        name = str(name)
        if not _AREA_NAME.match(name):
            raise ManifestError(
                f"{where}.areas: {name!r} is no area name: letters, digits, '.', '_' and '-'"
            )
        if not isinstance(patterns, list):
            raise ManifestError(f"{where}.areas.{name} must be a list of path patterns")
        if not patterns:
            raise ManifestError(f"{where}.areas.{name} declares no path pattern")
        areas[name] = tuple(_pattern(p, f"{where}.areas.{name}") for p in patterns)
    _refuse_overlap(areas, where)
    return DocsLockConfig(areas=areas, lease_minutes=minutes)


def _pattern(value: Any, where: str) -> str:
    text = str(value).strip() if isinstance(value, str) else ""
    pure = PurePosixPath(text)
    if not text or pure.is_absolute() or ".." in pure.parts:
        raise ManifestError(
            f"{where}: {value!r} must be a store-relative path pattern, with no '..' "
            f"and never absolute (ADR-014 D1)"
        )
    return text.removeprefix("./").rstrip("/")


def _refuse_overlap(areas: Mapping[str, tuple[str, ...]], where: str) -> None:
    names = sorted(areas)
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            for a in areas[first]:
                for b in areas[second]:
                    if _matches(a, b) or _matches(b, a):
                        raise ManifestError(
                            f"{where}.areas: {first!r} ({a}) and {second!r} ({b}) "
                            f"overlap; every path must belong to one area (ADR-014 D5)"
                        )


def _matches(rel: str, pattern: str) -> bool:
    from .manifest import (
        glob_matches,  # local: manifest imports stores, which imports us
    )

    return glob_matches(rel, pattern)


def unit_of(rel: str, config: DocsLockConfig) -> str:
    """The unit a store-relative path belongs to: its area, else the file."""
    rel = rel.removeprefix("./")
    found = sorted(
        name for name, patterns in config.areas.items()
        if any(_matches(rel, p) for p in patterns)
    )
    if len(found) > 1:
        raise ManifestError(
            f"{rel} is matched by the areas {', '.join(repr(n) for n in found)}; "
            f"areas may not overlap (ADR-014 D5): narrow one of them"
        )
    return f"{AREA}{found[0]}" if found else f"{FILE}{rel}"


def in_unit(rel: str, unit: str, config: DocsLockConfig) -> bool:
    """Does the store-relative path ``rel`` belong to ``unit``?"""
    try:
        return unit_of(rel, config) == unit
    except ManifestError:
        return unit.startswith(AREA) and any(
            _matches(rel, p) for p in config.areas.get(unit[len(AREA):], ())
        )


def read_config(store_root: Path) -> DocsLockConfig:
    """The store's ``store.docs_lock``, read from its ``invariant.yaml``."""
    from .stores import STORE_MANIFEST  # local: stores imports this module

    path = Path(store_root) / STORE_MANIFEST
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f"the store config {path} cannot be read: {exc}") from exc
    block = (raw.get("store") or {}) if isinstance(raw, Mapping) else {}
    return parse_docs_lock(block.get("docs_lock") if isinstance(block, Mapping) else None, path)


def config_of(manifest) -> DocsLockConfig:
    """The docs lock's configuration for a store project; refuses an in-repo one."""
    return read_config(store_of(manifest))


def store_of(manifest) -> Path:
    """The project's store root, or a refusal naming why there is no lock."""
    if getattr(manifest, "store", None) is None:
        raise UsageError(
            "this project keeps its records in its repository, and the docs lock "
            "governs a store's docs (ADR-014 D5); its sessions take no lock"
        )
    return Path(manifest.store.root)


# -- naming a unit --------------------------------------------------------


def data_paths(manifest) -> list[tuple[str, str]]:
    """``(record, store-relative path)`` for each data record kept in the store."""
    return _record_paths(manifest, DATA_RECORDS)


def policy_paths(manifest) -> list[tuple[str, str]]:
    """``(record, store-relative path)`` for each policy record kept in the store."""
    return _record_paths(manifest, POLICY_RECORDS)


def _record_paths(manifest, kinds: tuple[str, ...]) -> list[tuple[str, str]]:
    from .manifest import RECORD_PATH_KEYS  # local: see _matches

    root = store_of(manifest).resolve()
    records = manifest.get("records") or {}
    out: list[tuple[str, str]] = []
    for record in kinds:
        if records.get(record) != "store":
            continue
        for key in RECORD_PATH_KEYS.get(record, ()):
            value = manifest.get(key)
            # ``agents.overlays`` maps an agent to its overlay file.
            values = value.values() if isinstance(value, Mapping) else [value]
            for one in values:
                if not isinstance(one, str) or not one:
                    continue
                try:
                    rel = Path(one).resolve().relative_to(root)
                except ValueError:
                    continue
                out.append((record, rel.as_posix()))
    return out


def _under(rel: str, base: str) -> bool:
    base = base.rstrip("/")
    return base in ("", ".") or rel == base or rel.startswith(base + "/")


def classify(manifest, config: DocsLockConfig, path: Path | str) -> tuple[str, str | None]:
    """Where a path a tool call names stands with the lock, and its unit.

    ``(OUTSIDE, None)`` outside the store; ``(DATA, None)`` under memory, the
    backlog, reports or sweeps kept there, which are written at any time
    (ruling 20); ``(UNGOVERNED, None)`` in the store under no policy record
    and no declared area (the store's own ``invariant.yaml``, its git
    directory); else ``(GOVERNED, unit)``. A policy path is one under a policy
    record kept in the store (the docs, the guidelines) or matched by a
    declared area, so an area may reach files no record names.
    """
    root = store_of(manifest).resolve()
    try:
        rel_path = Path(path).resolve().relative_to(root)
    except (ValueError, OSError):
        return OUTSIDE, None
    rel = rel_path.as_posix()
    if rel in ("", ".") or rel_path.parts[0] == ".git":
        return UNGOVERNED, None
    if any(_under(rel, base) for _, base in data_paths(manifest)):
        return DATA, None
    in_area = any(_matches(rel, p) for patterns in config.areas.values() for p in patterns)
    if in_area or any(_under(rel, base) for _, base in policy_paths(manifest)):
        return GOVERNED, unit_of(rel, config)
    return UNGOVERNED, None


def resolve_unit(manifest, config: DocsLockConfig, arg: str) -> str:
    """The unit an argument names: an area's name, a unit key, or a path.

    A path is absolute or store-relative. A path under a data record kept in
    the store is refused: memory, the backlog, reports and sweeps are written
    at any time, outside the lock (ruling 20, D5).
    """
    text = str(arg).strip()
    if text in config.areas:
        return f"{AREA}{text}"
    if text.startswith(AREA):
        name = text[len(AREA):]
        if name not in config.areas:
            declared = ", ".join(sorted(config.areas)) or "none"
            raise UsageError(f"the store declares no area {name!r} (declared: {declared})")
        return text
    rel = text.removeprefix(FILE)
    root = store_of(manifest).resolve()
    path = Path(rel)
    if path.is_absolute():
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            raise UsageError(
                f"{text} is outside the store {root}; the docs lock covers the store's docs"
            ) from None
    pure = PurePosixPath(rel)
    if not rel or ".." in pure.parts:
        raise UsageError(f"{text} is outside the store {root}")
    rel = pure.as_posix()
    for record, base in data_paths(manifest):
        if rel == base or rel.startswith(base.rstrip("/") + "/"):
            raise UsageError(
                f"{rel} is {record}, which is written at any time outside the docs "
                f"lock (ADR-014 ruling 20, D5); it needs no unit"
            )
    return unit_of(rel, config)


# -- the lease record -------------------------------------------------------


class DocsLock:
    """One store's lease record, and every change made to it.

    ``alive`` answers whether a lease's host process is running: ``True``,
    ``False``, or ``None`` when that cannot be told, which never makes a
    lease stale.
    """

    def __init__(
        self,
        manifest,
        clock: Callable[[], float] = time.time,
        alive: Callable[[dict], bool | None] | None = None,
        config: DocsLockConfig | None = None,
    ) -> None:
        self.manifest = manifest
        self.root = store_of(manifest)
        self.config = config if config is not None else read_config(self.root)
        self.clock = clock
        self.alive = alive
        # Read from disk first: a hook constructs this on every store call,
        # and a subprocess per tool call is the cost to avoid. The store root
        # is a git top level, so the disk read answers; git is asked only
        # when it does not.
        common = common_dir_of(self.root) or git.common_dir(self.root)
        if common is None:
            raise StateError(f"the store {self.root} is not a git repository")
        self.json = JsonStore(common / LOCK_DIR)
        #: Leases to wake once the record that hands them a unit is written:
        #: a waiter woken before the write would read the old record.
        self._wakes: list[str] = []

    # -- reading ------------------------------------------------------

    def read(self) -> dict:
        return _shape(self.json.read(LOCK_REL, {}) or {})

    def lease_id(self, record: dict, session: str) -> str | None:
        for lid, lease in record["leases"].items():
            if lease.get("session") == session:
                return lid
        return None

    def lease_of(self, session: str) -> dict | None:
        record = self.read()
        lid = self.lease_id(record, session)
        return None if lid is None else {"id": lid, **record["leases"][lid]}

    def outcome(self, session: str) -> Outcome:
        """Where ``session``'s lease stands, read only."""
        record = self.read()
        lid = self.lease_id(record, session)
        out = Outcome(lease=lid)
        if lid is not None:
            self._report(record, lid, out)
        return out

    def units(self) -> list[dict]:
        """Every held unit: its holder, since when, and its queue, in key order."""
        record = self.read()
        now = self.clock()
        rows = []
        for key in sorted(record["units"]):
            unit = record["units"][key]
            holder = record["leases"].get(unit.get("holder") or "", {})
            rows.append(
                {
                    "unit": key,
                    "holder": holder.get("session"),
                    "identity": holder.get("identity"),
                    "held_seconds": max(0, int(now - float(unit.get("since") or now))),
                    "queue": [
                        record["leases"].get(lid, {}).get("session") for lid in unit.get("queue", [])
                    ],
                    "stale": self.staleness({"id": unit.get("holder"), **holder}),
                }
            )
        return rows

    def standing(self, session: str, unit: str) -> dict[str, Any]:
        """Where ``session`` stands on one unit, read only.

        ``held`` whether its lease holds it; ``holder`` the session that does,
        if another; ``position`` its place in the unit's queue, if queued;
        ``stale`` why the holder's lease is stale, if it is.
        """
        record = self.read()
        lid = self.lease_id(record, session)
        entry = record["units"].get(unit) or {}
        holder_id = entry.get("holder")
        holder = record["leases"].get(holder_id or "", {})
        queue = entry.get("queue", [])
        return {
            "unit": unit,
            "held": lid is not None and holder_id == lid,
            "holder": holder.get("session"),
            "position": queue.index(lid) + 1 if lid is not None and lid in queue else None,
            "stale": self.staleness({"id": holder_id, **holder}) if holder else None,
        }

    def staleness(self, lease: dict | None) -> str | None:
        """Why the lease is stale, or ``None`` while it is live."""
        if not lease:
            return None
        age = self.clock() - float(lease.get("heartbeat") or 0)
        limit = self.config.lease_minutes * 60
        if age > limit:
            return (
                f"its heartbeat is {int(age // 60)} min old, past the "
                f"{self.config.lease_minutes} min lease"
            )
        if self.alive is not None:
            try:
                running = self.alive(lease)
            except Exception:  # noqa: BLE001 - an unanswerable probe is no answer
                running = None
            if running is False:
                return "its holder's host process is not running"
        return None

    # -- changing -----------------------------------------------------

    def acquire(self, holder: Holder, units: list[str]) -> Outcome:
        """Take ``units`` in key order: grant the free, queue at the first busy.

        A unit that sorts before one the lease holds is a late unit (ruling
        21): granted if free, else the lease backs off, releasing every held
        unit that sorts after it, and queues for it and then for those. The
        back-off refuses, changing nothing, while a unit it would release
        carries uncommitted changes.
        """
        try:
            with self.json.locked(LOCK_REL, default={}) as raw:
                record = _shape(raw)
                raw.clear()
                raw.update(record)
                lid = self._lease_for(record, holder)
                out = Outcome(lease=lid)
                lease = record["leases"][lid]
                held = self._held(record, lid)
                wanted = sorted(set(units) - set(held) - set(lease["wants"]))
                for unit in wanted:
                    held = self._held(record, lid)
                    if held and unit < max(held):
                        if (record["units"].get(unit) or {}).get("holder") is None:
                            self._grant(record, lid, unit, out)
                            continue
                        after = [h for h in held if h > unit]
                        self._refuse_dirty(after, "back off from")
                        for h in after:
                            out.closed.append({**self._release(record, lid, h, out), "backed_off": True})
                        rest = [u for u in wanted if u > unit]
                        lease["wants"] = sorted(set(lease["wants"]) | {unit, *after, *rest})
                        break
                    lease["wants"] = sorted(set(lease["wants"]) | {unit})
                self._drive(record, lid, out)
                self._report(record, lid, out)
                return out
        finally:
            self._flush_wakes()

    def grant_free(self, holder: Holder, unit: str) -> Outcome:
        """The on-access grant (D5, increment 4b): take ``unit`` if it is free.

        Free means nobody holds it and nobody queues for it. A grant never
        waits, so it adds no edge to any wait-for cycle; it is still refused
        when the lease waits for a unit that sorts before ``unit``, because
        the lease would then hold a unit sorting after one it waits for,
        which is the order every wait relies on. Anything not granted is
        left exactly as it was: no lease is created and nobody is queued, and
        a call that grants nothing on a plain read takes no lock and writes
        nothing. ``granted`` says which.
        """
        out = Outcome()
        if not self._grantable(self.read(), holder.session, unit, out):
            return out
        with self.json.locked(LOCK_REL, default={}) as raw:
            record = _shape(raw)
            raw.clear()
            raw.update(record)
            out = Outcome()
            if self._grantable(record, holder.session, unit, out):
                lid = self._lease_for(record, holder)
                out.lease = lid
                self._grant(record, lid, unit, out)
                self._report(record, lid, out)
            return out

    def _grantable(self, record: dict, session: str, unit: str, out: Outcome) -> bool:
        """Would :meth:`grant_free` grant ``unit``? Fills ``out`` when it would not."""
        lid = self.lease_id(record, session)
        out.lease = lid
        entry = record["units"].get(unit)
        held = entry is not None and lid is not None and entry.get("holder") == lid
        busy = entry is not None and bool(entry.get("holder") or entry.get("queue"))
        wants = record["leases"][lid]["wants"] if lid is not None else []
        if held or busy or any(w < unit for w in wants):
            if lid is not None:
                self._report(record, lid, out)
            return False
        return True

    def release(
        self, session: str, units: list[str] | None, check: bool = True
    ) -> Outcome:
        """Release ``units`` (all when ``None``); each passes to its queue's head.

        Releasing never waits, so it cannot deadlock. ``check`` refuses a unit
        whose paths carry uncommitted changes; the lifecycle's release (the
        holder landed, tore down or was abandoned) frees regardless and names
        them in the span. Naming a unit the session only waits for drops the
        want; naming one it neither holds nor wants is refused.
        """
        try:
            with self.json.locked(LOCK_REL, default={}) as raw:
                record = _shape(raw)
                raw.clear()
                raw.update(record)
                lid = self.lease_id(record, session)
                if lid is None:
                    if units:
                        raise UsageError(
                            f"session {session} holds no unit of the store's docs lock; "
                            f"nothing to release ({', '.join(units)})"
                        )
                    return Outcome()
                out = Outcome(lease=lid)
                lease = record["leases"][lid]
                held = self._held(record, lid)
                if units is None:
                    targets, drop = held, list(lease["wants"])
                else:
                    targets = [u for u in units if u in held]
                    drop = [u for u in units if u not in held and u in lease["wants"]]
                    stray = [u for u in units if u not in held and u not in lease["wants"]]
                    if stray:
                        raise UsageError(
                            f"session {session} neither holds nor waits for "
                            f"{', '.join(stray)}; it holds {', '.join(held) or 'nothing'}"
                        )
                if check:
                    self._refuse_dirty(targets, "release")
                for unit in drop:
                    self._dequeue(record, lid, unit)
                lease["wants"] = [u for u in lease["wants"] if u not in drop]
                for unit in sorted(targets):
                    span = self._release(record, lid, unit, out)
                    if not check:
                        span["uncommitted"] = self._dirty([unit])
                    out.closed.append(span)
                self._drive(record, lid, out)
                self._report(record, lid, out)
                self._forget_if_idle(record, lid)
                return out
        finally:
            self._flush_wakes()

    def take_over(self, holder: Holder, unit: str, reason: str) -> tuple[Outcome, dict]:
        """Take one unit of a stale lease over, recording why.

        Refused with no reason, for a free unit, for one the caller holds,
        and while the holder's lease is live. Answers the outcome and the
        previous holder (its lease entry with its closed span under ``span``).
        """
        if not reason or not reason.strip():
            raise UsageError(
                "a takeover needs --reason: a unit taken from another session "
                "with no stated reason is indistinguishable from a stolen one"
            )
        try:
            with self.json.locked(LOCK_REL, default={}) as raw:
                record = _shape(raw)
                raw.clear()
                raw.update(record)
                entry = record["units"].get(unit)
                if entry is None:
                    raise UsageError(f"{unit} is free; take it with 'invariant docs lock {unit}'")
                victim_id = entry["holder"]
                victim = record["leases"].get(victim_id) or {}
                if victim.get("session") == holder.session:
                    raise UsageError(f"session {holder.session} already holds {unit}")
                why = self.staleness({"id": victim_id, **victim})
                if why is None:
                    raise UsageError(
                        f"{unit} is held by session {victim.get('session')}, whose lease is "
                        f"not stale: its heartbeat is recent and its host is not known to "
                        f"be dead. Wait in its queue with 'invariant docs lock {unit}'."
                    )
                lid = self._lease_for(record, holder)
                out = Outcome(lease=lid)
                span = self._release(record, victim_id, unit, out, hand=False)
                span.update({"taken_over": True, "taken_by": holder.session, "reason": reason.strip()})
                self._dequeue(record, lid, unit)
                record["leases"][lid]["wants"] = [
                    u for u in record["leases"][lid]["wants"] if u != unit
                ]
                self._grant(record, lid, unit, out)
                self._drive(record, lid, out)
                self._report(record, lid, out)
                previous = {**victim, "id": victim_id, "why": why, "span": span}
                self._forget_if_idle(record, victim_id)
                return out, previous
        finally:
            self._flush_wakes()

    def touch(self, session: str) -> bool:
        """Refresh the session's heartbeat, at most once a minute."""
        lease = self.lease_of(session)
        if lease is None:
            return False
        now = self.clock()
        if now - float(lease.get("heartbeat") or 0) < HEARTBEAT_SECONDS:
            return False
        with self.json.locked(LOCK_REL, default={}) as raw:
            record = _shape(raw)
            raw.clear()
            raw.update(record)
            lid = self.lease_id(record, session)
            if lid is None:
                return False
            record["leases"][lid]["heartbeat"] = now
        return True

    def wait(self, session: str, timeout: float) -> Outcome:
        """Block until the lease waits for nothing, or until ``timeout``.

        No polling: the waiter blocks in ``select`` on its own named pipe,
        opened before the record is read, and a hand-over writes one byte into
        it. A wake re-reads the record, so a spurious one costs one read.
        """
        record = self.read()
        lid = self.lease_id(record, session)
        if lid is None:
            return Outcome()
        pipe = self._wake_path(lid)
        pipe.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkfifo(pipe)
        except FileExistsError:
            pass
        fd = os.open(pipe, os.O_RDWR | os.O_NONBLOCK)
        deadline = time.monotonic() + max(0.0, timeout)
        try:
            while True:
                out = self.outcome(session)
                if out.waiting is None:
                    return out
                left = deadline - time.monotonic()
                if left <= 0:
                    return out
                ready, _, _ = select.select([fd], [], [], left)
                if ready:
                    try:
                        os.read(fd, 4096)
                    except BlockingIOError:  # pragma: no cover - raced a drain
                        pass
        finally:
            os.close(fd)
            try:
                pipe.unlink()
            except OSError:  # pragma: no cover - another waiter's cleanup won
                pass

    # -- the record's moves -------------------------------------------

    def _lease_for(self, record: dict, holder: Holder) -> str:
        lid = self.lease_id(record, holder.session)
        now = self.clock()
        if lid is None:
            lid = uuid.uuid4().hex[:12]
            record["leases"][lid] = {
                "session": holder.session,
                "identity": holder.identity,
                "host": holder.host,
                "state_root": holder.state_root,
                "created": now,
                "heartbeat": now,
                "wants": [],
            }
        else:
            lease = record["leases"][lid]
            lease["heartbeat"] = now
            if holder.host:
                lease["host"] = holder.host
        return lid

    @staticmethod
    def _held(record: dict, lid: str) -> list[str]:
        return sorted(k for k, u in record["units"].items() if u.get("holder") == lid)

    def _grant(self, record: dict, lid: str, unit: str, out: Outcome) -> None:
        sha = git.resolve_commit(self.root, "HEAD")
        queue = [q for q in (record["units"].get(unit) or {}).get("queue", []) if q != lid]
        record["units"][unit] = {"holder": lid, "since": self.clock(), "sha": sha, "queue": queue}
        if lid == out.lease:
            out.granted.append(unit)
            out.opened.append(
                {"lease": lid, "unit": unit, "acquired_at": self.clock(), "acquired_at_sha": sha}
            )

    def _drive(self, record: dict, lid: str, out: Outcome) -> None:
        """Take what the lease wants, in order, until the first busy unit."""
        lease = record["leases"].get(lid)
        if lease is None:
            return
        wants = sorted(lease["wants"])
        for i, unit in enumerate(wants):
            entry = record["units"].get(unit)
            if entry is not None and entry.get("holder") == lid:
                continue
            held = self._held(record, lid)
            if entry is None or entry.get("holder") is None:
                self._grant(record, lid, unit, out)
                continue
            if not held or unit > max(held):
                for other, e in record["units"].items():
                    if other != unit and lid in e.get("queue", []):
                        e["queue"].remove(lid)
                if lid not in entry["queue"]:
                    entry["queue"].append(lid)
            lease["wants"] = wants[i:]
            return
        lease["wants"] = []

    def _release(
        self, record: dict, lid: str, unit: str, out: Outcome, hand: bool = True
    ) -> dict:
        entry = record["units"].pop(unit)
        sha = git.resolve_commit(self.root, "HEAD")
        span = {
            "lease": lid,
            "unit": unit,
            "acquired_at": entry.get("since"),
            "acquired_at_sha": entry.get("sha"),
            "released_at": self.clock(),
            "released_at_sha": sha,
            "changed": self._changed(unit, entry.get("sha"), sha),
        }
        queue = list(entry.get("queue", []))
        if hand:
            while queue:
                nxt = queue.pop(0)
                if nxt not in record["leases"]:
                    continue
                record["units"][unit] = {
                    "holder": nxt, "since": self.clock(), "sha": sha, "queue": queue,
                }
                out.handed[unit] = record["leases"][nxt]["session"]
                self._drive(record, nxt, out)
                self._wake(nxt)
                break
        elif queue:
            record["units"][unit] = {"holder": None, "since": None, "sha": None, "queue": queue}
        return span

    def _dequeue(self, record: dict, lid: str, unit: str) -> None:
        entry = record["units"].get(unit)
        if entry is not None and lid in entry.get("queue", []):
            entry["queue"].remove(lid)

    def _forget_if_idle(self, record: dict, lid: str) -> None:
        lease = record["leases"].get(lid)
        if lease is not None and not lease["wants"] and not self._held(record, lid):
            del record["leases"][lid]

    def _report(self, record: dict, lid: str, out: Outcome) -> None:
        lease = record["leases"].get(lid)
        out.held = self._held(record, lid)
        if lease is None:
            return
        wants = [u for u in lease["wants"] if u not in out.held]
        for unit in wants:
            entry = record["units"].get(unit) or {}
            if lid in entry.get("queue", []):
                holder = record["leases"].get(entry.get("holder") or "", {})
                out.waiting = {
                    "unit": unit,
                    "position": entry["queue"].index(lid) + 1,
                    "holder": holder.get("session"),
                }
                out.pending = [u for u in wants if u > unit]
                return
        out.pending = wants

    # -- the store's tree ---------------------------------------------

    def _changed(self, unit: str, start: str | None, end: str | None) -> list[str]:
        if not start or not end or start == end:
            return []
        names = git.diff_names(self.root, start, end) or []
        return sorted(p for p in names if in_unit(p, unit, self.config))

    def _dirty(self, units: list[str]) -> list[str]:
        paths = git.dirty_paths(self.root) or []
        return sorted(p for p in paths if any(in_unit(p, u, self.config) for u in units))

    def _refuse_dirty(self, units: list[str], doing: str) -> None:
        dirty = self._dirty(units)
        if dirty:
            raise UsageError(
                f"cannot {doing} {', '.join(units)}: uncommitted changes in "
                f"{', '.join(dirty)}. Commit the finished update on the store's main "
                f"line, or discard the edit, first (ADR-014 D5)."
            )

    # -- waking -------------------------------------------------------

    def _wake_path(self, lid: str) -> Path:
        return self.json.root / WAKE_DIR / f"{lid}.fifo"

    def _wake(self, lid: str) -> None:
        self._wakes.append(lid)

    def _flush_wakes(self) -> None:
        wakes, self._wakes = self._wakes, []
        for lid in wakes:
            self._signal(lid)

    def _signal(self, lid: str) -> None:
        try:
            fd = os.open(self._wake_path(lid), os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ENXIO):
                return  # nobody is waiting
            return  # pragma: no cover - a wake is a courtesy; the record is the truth
        try:
            os.write(fd, b"1")
        except OSError:  # pragma: no cover - a full pipe already wakes the reader
            pass
        finally:
            os.close(fd)


def _shape(raw: Mapping) -> dict:
    """The record with its containers present; unknown keys kept."""
    record = dict(raw) if isinstance(raw, Mapping) else {}
    record.setdefault("version", 1)
    leases = record.get("leases")
    record["leases"] = dict(leases) if isinstance(leases, Mapping) else {}
    units = record.get("units")
    record["units"] = dict(units) if isinstance(units, Mapping) else {}
    for lease in record["leases"].values():
        lease.setdefault("wants", [])
    for entry in record["units"].values():
        entry.setdefault("queue", [])
    return record
