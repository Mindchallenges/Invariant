"""Adapter registry.

``generic`` is always present and is the fallback: it runs literal commands
from the manifest and parses nothing, which is the honest behaviour for an
ecosystem the library does not know.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Adapter, Hazard, TestResult
from .dart import DartAdapter, FlutterAdapter
from .node import NodeAdapter
from .python_ import PythonAdapter
from .sphinx import SphinxAdapter
from .swift_xcode import SwiftXcodeAdapter

_REGISTRY: dict[str, type[Adapter]] = {
    "generic": Adapter,
    "swift": SwiftXcodeAdapter,
    "swift_xcode": SwiftXcodeAdapter,
    "python": PythonAdapter,
    "node": NodeAdapter,
    "dart": DartAdapter,
    "flutter": FlutterAdapter,
    "sphinx": SphinxAdapter,
    # ADR-015 D1: `docs` selects the same adapter.
    "docs": SphinxAdapter,
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


def get_adapter(ecosystem: str, *, docs_srcdir: Path | None = None) -> Adapter:
    """The adapter for ``ecosystem``, ``generic`` for one the library does not
    know. ``docs_srcdir`` is the docs project's resolved source directory,
    which is never the project's content (ADR-020 D8)."""
    return _REGISTRY.get(ecosystem, Adapter)(docs_srcdir=docs_srcdir)


def defaults_for(ecosystem: str) -> dict[str, Any]:
    cls = _REGISTRY.get(ecosystem)
    return dict(getattr(cls, "manifest_defaults", {})) if cls else {}


def detect(root: Path, *, docs_srcdir: Path | None = None) -> list[str]:
    """Ecosystems whose marker files are present. Ordered, deduped.

    ``docs_srcdir`` is the docs project's resolved source directory, handed
    to each adapter so that invariant's own docs project is never detected as
    the project's content (ADR-020 D8)."""
    found: list[str] = []
    for name in ("swift_xcode", "python", "node", "flutter", "dart", "sphinx"):
        adapter = _REGISTRY[name](docs_srcdir=docs_srcdir)
        if adapter.detect(Path(root)) and name not in found:
            found.append(name)
    return found or ["generic"]
