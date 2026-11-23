"""The plan's usage, as the host reported it, and the hold it puts on the chain.

ADR-027 ruling 10. The host's status line (Claude Code's, opt-in) reports the
plan's weekly and five-hour usage; the CLI records each reading here as a
host-neutral ``plan_usage`` fact, ``<oracle home>/oracle/usage.json``
(:func:`record_usage`), and the chain step reads it back through the seam the
CLI injects (:func:`read_usage`).

:func:`hold` is the rule, pure: a reading that is missing, has no weekly
figure, or is older than :data:`STALE_SECONDS` holds every auto-start and
every proposal as ``unknown``, until a greenlight lifts that hold; a
greenlight is spent once a reading newer than it arrives. A fresh reading at
or over ``chain.weekly_usage_max`` holds as ``over``, whatever a greenlight
said. A start the user asks for is never held (D5). Nothing here imports a
host.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.errors import StateError
from .index import HomeStore

USAGE_FILE = "oracle/usage.json"
#: A reading older than this counts as no reading (D5).
STALE_SECONDS = 30 * 60
#: An unchanged reading is written again only once it is this old, so a
#: status line that refreshes every few seconds does not write every time.
REWRITE_SECONDS = 60
#: ``chain.weekly_usage_max`` when the user config sets none.
DEFAULT_WEEKLY_MAX = 95

OVER = "over"
UNKNOWN = "unknown"

_FIGURES = ("weekly", "weekly_resets_at", "five_hour", "five_hour_resets_at")


@dataclass(frozen=True)
class PlanUsage:
    """One reading: percentages, their reset times (epochs) and when it was read."""

    weekly: float | None
    weekly_resets_at: float | None
    five_hour: float | None
    five_hour_resets_at: float | None
    read_at: float
    host: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Any) -> PlanUsage | None:
        """A reading from its document, ``None`` for anything that is not one."""
        if not isinstance(raw, dict):
            return None
        read_at = _number(raw.get("read_at"))
        if read_at is None:
            return None
        return cls(*(_number(raw.get(name)) for name in _FIGURES), read_at=read_at,
                   host=str(raw.get("host") or ""))


@dataclass(frozen=True)
class Hold:
    """Why auto-starts and proposals are held: ``over`` the cap, or ``unknown``."""

    kind: str
    reason: str


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def weekly_max(user_config) -> float:
    """``chain.weekly_usage_max`` of the user config, else :data:`DEFAULT_WEEKLY_MAX`."""
    return (user_config.chain or {}).get("weekly_usage_max") or DEFAULT_WEEKLY_MAX


def read_usage(home: Path) -> PlanUsage | None:
    """The last reading recorded in the oracle home; ``None`` when there is none to use."""
    try:
        return PlanUsage.from_dict(HomeStore(home).read(USAGE_FILE))
    except StateError:
        return None


def record_usage(home: Path, fact: dict[str, Any], now: float) -> bool:
    """Record one reading; ``True`` when it was written.

    A reading with the same figures as the recorded one is written again only
    when that one is :data:`REWRITE_SECONDS` old. The write is an atomic
    replace, so status lines in several windows may record at once.
    """
    store = HomeStore(home)
    reading = PlanUsage.from_dict({**fact, "read_at": fact.get("read_at", now)})
    if reading is None:
        return False
    before = read_usage(home)
    if (before is not None
            and all(getattr(before, n) == getattr(reading, n) for n in _FIGURES)
            and reading.read_at - before.read_at < REWRITE_SECONDS):
        return False
    store.write(USAGE_FILE, reading.to_dict())
    return True


def spent(reading: PlanUsage | None, greenlight: float | None) -> bool:
    """Whether a reading newer than the greenlight has arrived, which spends it."""
    return reading is not None and greenlight is not None and reading.read_at > greenlight


def hold(reading: PlanUsage | None, now: float, cap: float,
         greenlight: float | None) -> Hold | None:
    """The hold the plan's usage puts on auto-starts and proposals, or ``None``."""
    fresh = (reading is not None and reading.weekly is not None
             and now - reading.read_at <= STALE_SECONDS)
    if fresh:
        if reading.weekly >= cap:
            return Hold(OVER, (
                f"the plan's weekly usage is {reading.weekly:g}%, at or over "
                f"chain.weekly_usage_max ({cap:g}%): nothing starts or is proposed on its "
                f"own until it falls; a start by hand still runs"))
        return None
    if greenlight is not None and not spent(reading, greenlight):
        return None
    if reading is None:
        why = ("no reading: install the status line that records it with invariant "
               "hooks statusline")
    elif reading.weekly is None:
        why = "the last reading has no weekly figure"
    else:
        why = f"the last reading is {int((now - reading.read_at) // 60)} min old"
    return Hold(UNKNOWN, (
        f"the plan's weekly usage cannot be read ({why}), so nothing starts or is "
        f"proposed on its own; invariant oracle chain greenlight lets it go ahead until "
        f"a fresh reading arrives"))
