"""Claude Code's status line: the plan's usage it reports, and installing it.

ADR-027 ruling 10. Claude Code writes no usage file; the command a user sets
as its ``statusLine`` gets JSON on stdin whose ``rate_limits.five_hour`` and
``rate_limits.seven_day`` each carry ``used_percentage`` and ``resets_at``.
:func:`reading_from_statusline` turns that into the host-neutral
``plan_usage`` fact the oracle reads, and :func:`statusline_text` is the
short line the status line prints. :func:`install_statusline` is the opt-in:
it sets the ``statusLine`` of the user's ``settings.json`` and never replaces
one that is already there. This module knows nothing of the oracle; the CLI
records the fact.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.errors import InvariantError

HOST = "claude_code"
#: A reset time above this is milliseconds, not seconds.
_MILLISECONDS = 10**11


def _percent(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _epoch(value: Any) -> float | None:
    """A reset time as epoch seconds: seconds, milliseconds or ISO 8601 accepted."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000 if value > _MILLISECONDS else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _window(limits: dict[str, Any], name: str) -> tuple[float | None, float | None]:
    window = limits.get(name)
    if not isinstance(window, dict):
        return None, None
    return _percent(window.get("used_percentage")), _epoch(window.get("resets_at"))


def reading_from_statusline(payload: Any, now: float) -> dict[str, Any] | None:
    """The ``plan_usage`` fact in one status-line payload, or ``None`` without one.

    A payload with neither window's percentage is no reading: Claude Code
    leaves ``rate_limits`` out before the first answer of a session and for
    a plan without these limits.
    """
    limits = payload.get("rate_limits") if isinstance(payload, dict) else None
    if not isinstance(limits, dict):
        return None
    weekly, weekly_reset = _window(limits, "seven_day")
    five, five_reset = _window(limits, "five_hour")
    if weekly is None and five is None:
        return None
    return {"weekly": weekly, "weekly_resets_at": weekly_reset, "five_hour": five,
            "five_hour_resets_at": five_reset, "read_at": now, "host": HOST}


def statusline_text(fact: dict[str, Any] | None) -> str:
    """The status line itself: ``week 41% · 5h 12%``; ``usage ?`` without a reading."""
    if not fact:
        return "usage ?"

    def shown(value: Any) -> str:
        return "?" if value is None else f"{float(value):.0f}%"

    return f"week {shown(fact.get('weekly'))} · 5h {shown(fact.get('five_hour'))}"


def install_statusline(settings: Path, command: str) -> str:
    """Set ``command`` as the ``statusLine`` of ``settings``; ``installed`` or ``already``.

    Refused, writing nothing, when the file is not a JSON object or already
    has a different ``statusLine``: the user's own status line is never
    replaced.
    """
    settings = Path(settings)
    document: dict[str, Any] = {}
    if settings.exists():
        try:
            document = json.loads(settings.read_text(encoding="utf-8") or "{}")
        except ValueError as exc:
            raise InvariantError(f"{settings} is not JSON ({exc}); nothing was written") from exc
        if not isinstance(document, dict):
            raise InvariantError(f"{settings} is not a JSON object; nothing was written")
    wanted = {"type": "command", "command": command}
    existing = document.get("statusLine")
    if existing == wanted:
        return "already"
    if existing is not None:
        raise InvariantError(
            f"{settings} already has a statusLine ({json.dumps(existing)}); it is not "
            f"replaced. To record the plan's usage, run `{command}` from it, or remove it "
            f"and install again"
        )
    document["statusLine"] = wanted
    settings.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings.with_name(f".{settings.name}.tmp")
    tmp.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    tmp.replace(settings)
    return "installed"
