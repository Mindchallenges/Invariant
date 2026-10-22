"""The watcher: a tick every few seconds, and one alert per rising edge.

Each tick does four things in order:

1. **scan** every registered project (:func:`scan_all`, read-only);
2. **assess** it (:func:`assess`) — the ``oracle list --json`` document, with
   the gate cache's answers as recommendation detail when a
   :class:`~invariant.oracle.gates.GateCache` is given. The cache lives across
   ticks and evaluates in its own threads; a tick waits ``gate_wait`` seconds
   for it at most (none, by default), so a slow rule cannot stretch the tick;
3. **write the snapshot**, ``<oracle home>/oracle/snapshot.json``: that
   document plus ``version``, ``generated``, ``watcher_pid`` and ``interval``,
   atomically replaced, so a reader never sees half of one;
4. **de-duplicate and notify** through the ledger,
   ``<oracle home>/oracle/alerts.json``.

**The ledger is what makes "once" true.** It maps each alert's signal key to
``{since, notified}``. A key already there is not news. A key the tick no
longer sees is dropped, so the same condition coming back is a new edge and
alerts again; a condition that recurs as a new occurrence has a new
discriminator, so a new key, and alerts again too. There is no repeat timer.
``notified: false`` means the edge is owed: it was seen with no notifier
(``--no-notify``) or delivery failed, and the next tick that can deliver it
does. One exception to dropping: a project that could not be read this tick
keeps its keys, because "cannot read" is not "cleared", and a parse error
must not re-alert everything in the project once it is fixed.

Only alerts notify; markers never do. The read-modify-write runs under the
home's lock, so two watchers — the daemon and a ``watch --once`` — cannot both
announce one edge. Both files are derived: deleting them costs at most one
repeat of each live alert, and an unreadable ledger is replaced.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.errors import StateError
from .gates import GateCache
from .index import HomeStore, ProjectIndex
from .notify import Notification, Notifier
from .recommend import assess, row_label
from .scan import ROW_ERROR, ScanCache, scan_all
from .signals import HIGH, MEDIUM, Thresholds

SNAPSHOT_FILE = "oracle/snapshot.json"
LEDGER_FILE = "oracle/alerts.json"
SNAPSHOT_VERSION = 1
LEDGER_VERSION = 1

#: Seconds between ticks. A prompt is seen within one interval plus one tick.
DEFAULT_INTERVAL = 5.0

#: The sound a high or medium alert plays. A low one is a silent banner.
ALERT_SOUND = "Glass"
_AUDIBLE = (HIGH, MEDIUM)


def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _iso(epoch: float) -> str:
    """UTC, to the second, ISO 8601 — the attention sidecar's spelling (ADR-007 D9)."""
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


@dataclass
class TickResult:
    """What one tick saw and did."""

    document: dict[str, Any]
    #: The alerts this tick announced: each new edge, and each owed one it
    #: delivered. ``notified`` says whether a notification went out.
    alerts: list[dict[str, Any]] = field(default_factory=list)
    elapsed: float = 0.0
    snapshot_path: Path | None = None
    ledger_path: Path | None = None

    @property
    def rows(self) -> int:
        return sum(len(p["rows"]) for p in self.document["projects"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot": str(self.snapshot_path),
            "ledger": str(self.ledger_path),
            "rows": self.rows,
            "alerts": self.alerts,
            "elapsed": self.elapsed,
        }


class Watcher:
    """Scan, assess, write the snapshot, alert once per rising edge."""

    def __init__(
        self,
        index: ProjectIndex,
        notifier: Notifier | None,
        clock: Callable[[], float] = time.time,
        interval: float = DEFAULT_INTERVAL,
        cache: ScanCache | None = None,
        thresholds: Thresholds | None = None,
        sleep: Callable[[float], None] | None = None,
        log: Callable[[str], None] | None = None,
        gates: GateCache | None = None,
        gate_wait: float = 0.0,
    ) -> None:
        if not interval > 0:
            raise ValueError(f"the interval must be positive, not {interval!r}")
        if gate_wait < 0:
            raise ValueError(f"the gate wait cannot be negative, not {gate_wait!r}")
        self.index = index
        self.notifier = notifier
        self.clock = clock
        self.interval = interval
        self.cache = cache if cache is not None else ScanCache()
        self.thresholds = thresholds or Thresholds()
        self._sleep = sleep
        self.log = log or _stderr
        self.gates = gates
        self.gate_wait = gate_wait
        self.store = HomeStore(index.home)

    @property
    def snapshot_path(self) -> Path:
        return self.index.home / SNAPSHOT_FILE

    @property
    def ledger_path(self) -> Path:
        return self.index.home / LEDGER_FILE

    # -- one tick -------------------------------------------------------

    def tick(self) -> TickResult:
        started = time.perf_counter()
        now = self.clock()
        snapshots = scan_all(self.index, clock=lambda: now, cache=self.cache)
        reports = (
            self.gates.refresh(snapshots, now, wait=self.gate_wait)
            if self.gates is not None
            else None
        )
        document = assess(snapshots, now, self.thresholds, gates=reports)
        document.update(
            home=str(self.index.home),
            version=SNAPSHOT_VERSION,
            generated=_iso(now),
            watcher_pid=os.getpid(),
            interval=self.interval,
        )
        self.store.write(SNAPSHOT_FILE, document)
        alerts = self._announce(document, now)
        return TickResult(
            document=document,
            alerts=alerts,
            elapsed=time.perf_counter() - started,
            snapshot_path=self.snapshot_path,
            ledger_path=self.ledger_path,
        )

    def _announce(self, document: dict[str, Any], now: float) -> list[dict[str, Any]]:
        current, unreadable = _alerts_in(document)
        self._replace_if_unreadable()
        announced: list[dict[str, Any]] = []
        with self.store.locked(LEDGER_FILE) as ledger:
            entries = _entries(ledger)
            for key in list(entries):
                kept = any(key.startswith(f"{project}|") for project in unreadable)
                if key not in current and not kept:
                    del entries[key]
            delivering = self.notifier is not None
            for key, (project_name, row, signal) in current.items():
                entry = entries.get(key)
                rising = entry is None
                if rising:
                    entry = entries[key] = {"since": _iso(now), "notified": False}
                elif entry.get("notified") is not False:
                    continue
                if delivering:
                    try:
                        self.notifier.notify(_notification(project_name, row, signal))
                        entry["notified"] = True
                    except Exception as exc:  # noqa: BLE001 - owed, retried next tick
                        self.log(f"oracle watch: could not notify {key}: {exc}")
                        delivering = False
                if rising or entry["notified"]:
                    announced.append(_alert(key, project_name, row, signal, entry))
        return announced

    def _replace_if_unreadable(self) -> None:
        """A ledger that is not a JSON object is derived state: start it again."""
        try:
            document = self.store.read(LEDGER_FILE)
            problem = None if document is None or isinstance(document, dict) else (
                "it is not a JSON object"
            )
        except StateError as exc:
            problem = str(exc)
        if problem:
            self.log(f"oracle watch: replacing an unreadable ledger: {problem}")
            self.store.write(LEDGER_FILE, {"version": LEDGER_VERSION, "alerts": {}})

    # -- the loop -------------------------------------------------------

    def run(
        self,
        once: bool = False,
        max_ticks: int | None = None,
        on_tick: Callable[[TickResult], None] | None = None,
    ) -> None:
        """Tick until stopped. A tick that raises is logged and the loop goes on."""
        ticks = 0
        while True:
            started = time.monotonic()
            try:
                result = self.tick()
            except Exception as exc:  # noqa: BLE001 - one bad tick must not end the watch
                self.log(f"oracle watch: tick failed: {type(exc).__name__}: {exc}")
            else:
                if on_tick is not None:
                    on_tick(result)
            ticks += 1
            if once or (max_ticks is not None and ticks >= max_ticks):
                return
            pause = max(0.0, self.interval - (time.monotonic() - started))
            (self._sleep or time.sleep)(pause)


def _alerts_in(document: dict[str, Any]) -> tuple[dict[str, tuple], set[str]]:
    """Every alert in ``document`` by key, and the projects that could not be read."""
    current: dict[str, tuple] = {}
    unreadable: set[str] = set()
    for project in document["projects"]:
        name = project["project"]["name"]
        for row in project["rows"]:
            if row["kind"] == ROW_ERROR and not row.get("session_id"):
                unreadable.add(row["project"])
            for signal in row["signals"]:
                if signal["alert"]:
                    current[signal["key"]] = (name, row, signal)
    return current, unreadable


def _entries(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The ledger's entries, made readable in place.

    A ledger of another version or shape is started again: it is derived, and
    the cost is one repeat of each live alert. An entry that is not an object
    is dropped.
    """
    alerts = ledger.get("alerts")
    if ledger.get("version") != LEDGER_VERSION or not isinstance(alerts, dict):
        ledger.clear()
        ledger.update(version=LEDGER_VERSION, alerts={})
        return ledger["alerts"]
    for key in [k for k, v in alerts.items() if not isinstance(v, dict)]:
        del alerts[key]
    return alerts


def _notification(project_name: str, row: dict, signal: dict) -> Notification:
    return Notification(
        title=f"invariant: {project_name}",
        subtitle=row_label(row),
        message=signal["detail"],
        sound=ALERT_SOUND if signal["severity"] in _AUDIBLE else None,
    )


def _alert(key: str, project_name: str, row: dict, signal: dict, entry: dict) -> dict:
    return {
        "key": key,
        "project": project_name,
        "subject": row_label(row),
        "kind": signal["kind"],
        "severity": signal["severity"],
        "detail": signal["detail"],
        "since": entry["since"],
        "notified": entry["notified"],
    }
