"""Adapter registry.

``generic`` is always present and is the fallback: it runs literal commands
from the manifest and parses nothing, which is the honest behaviour for an
ecosystem the library does not know.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Adapter, Hazard, TestResult
from .node import NodeAdapter
from .python_ import PythonAdapter
from .swift_xcode import SwiftXcodeAdapter

_REGISTRY: dict[str, type[Adapter]] = {
    "generic": Adapter,
    "swift": SwiftXcodeAdapter,
    "swift_xcode": SwiftXcodeAdapter,
    "python": PythonAdapter,
    "node": NodeAdapter,
}

__all__ = [
    "Adapter",
    "Hazard",
    "TestResult",
    "available",
    "defaults_for",
    "detect",
    "get_adapter",
]


def available() -> list[str]:
    return sorted(_REGISTRY)


def get_adapter(ecosystem: str) -> Adapter:
    cls = _REGISTRY.get(ecosystem)
    if cls is None:
        return Adapter()
    return cls()


def defaults_for(ecosystem: str) -> dict[str, Any]:
    cls = _REGISTRY.get(ecosystem)
    return dict(getattr(cls, "manifest_defaults", {})) if cls else {}


def detect(root: Path) -> list[str]:
    """Ecosystems whose marker files are present. Ordered, deduped."""
    found: list[str] = []
    for name in ("swift_xcode", "python", "node"):
        if _REGISTRY[name]().detect(Path(root)) and name not in found:
            found.append(name)
    return found or ["generic"]
