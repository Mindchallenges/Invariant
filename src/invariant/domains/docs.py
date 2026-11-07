"""The docs domain: the assessment of the architecture docs (ADR-016 D1, D2).

Not an FSM, like ``stats`` and ``guidelines``. :func:`assess` reads the docs
at the resolved ``paths.docs`` (joined onto the store root when
``records.docs: store``) and returns their gaps, grouped by check, never one
pass or fail (ruling A):

- ``sections``: each required heading (``docs.assess.sections``) found in no
  doc, compared after whitespace normalisation and case folding, counted once
  across the docs;
- ``areas``: each source area no doc names as a whole word, the areas coming
  from the adapter, from globs or from a declared list (D2);
- ``links``: each relative link whose target file does not exist, by file and
  line. Anchors are stripped and external URLs never fetched.

Deterministic by construction (ruling B): files are read in sorted order, and
the result carries a digest of every input, so two runs over an unchanged tree
print byte-identical output. Read-only: it writes nothing anywhere.

Headings and links are read through ``core/docformat``'s reader for
``docs.format``. A format with no reader makes ``sections`` and ``links``
report "not assessable", never pass; ``areas`` still reads every file's text.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ..adapters import get_adapter
from ..core import git
from ..core.docformat import Reader, normalise_heading, reader_for
from ..core.manifest import ASSESS_CHECKS, Manifest, glob_matches, project_relative

#: A link target naming a scheme (``https:``, ``mailto:``) or a network path.
_EXTERNAL = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")


@dataclass(frozen=True)
class Gap:
    check: str
    subject: str
    detail: str = ""

    def render(self) -> str:
        return f"{self.subject}: {self.detail}" if self.detail else self.subject


@dataclass(frozen=True)
class Area:
    """A source area: a directory, spelled from the project root, and its name."""

    name: str
    path: str


@dataclass(frozen=True)
class Assessment:
    location: str
    format: str
    present: bool
    documents: int
    checks: tuple[str, ...]
    off: tuple[str, ...]
    areas: tuple[Area, ...]
    gaps: tuple[Gap, ...]
    digest: str

    def by_check(self, check: str) -> list[Gap]:
        return [gap for gap in self.gaps if gap.check == check]

    def summary(self) -> str:
        counts = ", ".join(f"{c} {len(self.by_check(c))}" for c in self.checks)
        return f"{len(self.gaps)} gap(s): {counts}" if self.checks else "every check is off"

    def row_detail(self) -> str:
        """Every gap on one line, grouped by check: the doctor row's detail."""
        return "; ".join(
            f"{check}: " + ", ".join(gap.render() for gap in self.by_check(check))
            for check in self.checks
            if self.by_check(check)
        )

    def render(self) -> str:
        where = f"{self.location} ({self.format}, {self.documents} document(s))"
        if not self.present:
            where = f"{self.location} ({self.format}, absent)"
        lines = [f"docs assessment: {where}"]
        for check in ASSESS_CHECKS:
            if check in self.off:
                lines.append(f"{check}: off")
                continue
            found = self.by_check(check)
            lines.append(f"{check}: {len(found)} gap(s)")
            lines.extend(f"  - {gap.render()}" for gap in found)
        lines.append(f"summary: {self.summary()}")
        lines.append(f"digest {self.digest}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "format": self.format,
            "present": self.present,
            "documents": self.documents,
            "checks": {
                check: [
                    {"subject": gap.subject, "detail": gap.detail}
                    for gap in self.by_check(check)
                ]
                for check in self.checks
            },
            "off": list(self.off),
            "areas": [{"name": area.name, "path": area.path} for area in self.areas],
            "gaps": len(self.gaps),
            "summary": self.summary(),
            "digest": self.digest,
        }


def assess(manifest: Manifest) -> Assessment:
    """The assessment of ``manifest``'s architecture docs, as the tree stands."""
    settings = manifest.get("docs.assess")
    checks = tuple(c for c in ASSESS_CHECKS if c in settings["checks"])
    fmt = str(manifest.get("docs.format") or "markdown")
    reader = reader_for(fmt)
    docs_root = manifest.path_for("docs")
    files = _documents(docs_root, reader)
    texts = {
        rel: path.read_bytes().decode("utf-8", errors="replace")
        for rel, path in files
    }
    gaps: list[Gap] = []
    areas: tuple[Area, ...] = ()
    targets: list[tuple[str, bool]] = []
    if "sections" in checks:
        gaps.extend(_sections(settings["sections"], texts, reader, fmt))
    if "areas" in checks:
        areas, area_gaps = _areas(manifest, settings["areas"], texts)
        gaps.extend(area_gaps)
    if "links" in checks:
        link_gaps, targets = _links(docs_root, texts, reader, fmt)
        gaps.extend(link_gaps)
    inputs = {
        "settings": settings,
        "checks": checks,
        "format": fmt,
        "documents": [
            [rel, hashlib.sha256(text.encode("utf-8")).hexdigest()]
            for rel, text in texts.items()
        ],
        "areas": [[area.name, area.path] for area in areas],
        "targets": targets,
    }
    digest = hashlib.sha256(
        json.dumps(inputs, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    return Assessment(
        location=_spelled(manifest.root, docs_root),
        format=fmt,
        present=docs_root.is_dir(),
        documents=len(texts),
        checks=checks,
        off=tuple(c for c in ASSESS_CHECKS if c not in checks),
        areas=areas,
        gaps=tuple(gaps),
        digest=digest,
    )


def _spelled(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _documents(docs_root: Path, reader: Reader | None) -> list[tuple[str, Path]]:
    """The docs, sorted by their path under ``docs_root``.

    A reader's suffixes decide what a doc is; with no reader, every file is
    read for the areas check. Anything under a directory or file whose name
    starts with a dot is held out.
    """
    if not docs_root.is_dir():
        return []
    found = []
    for path in docs_root.rglob("*"):
        rel = path.relative_to(docs_root)
        if not path.is_file() or any(part.startswith(".") for part in rel.parts):
            continue
        if reader is not None and path.suffix.lower() not in reader.suffixes:
            continue
        found.append((rel.as_posix(), path))
    return sorted(found)


def _not_assessable(check: str, fmt: str) -> Gap:
    return Gap(check, "not assessable", f"no reader for {fmt}")


def _sections(
    required: Sequence[str], texts: dict[str, str], reader: Reader | None, fmt: str
) -> list[Gap]:
    if reader is None:
        return [_not_assessable("sections", fmt)]
    present = {
        normalise_heading(heading.text)
        for text in texts.values()
        for heading in reader.headings(text)
    }
    return [
        Gap("sections", heading, "found in no doc")
        for heading in required
        if normalise_heading(heading) not in present
    ]


def _links(
    docs_root: Path, texts: dict[str, str], reader: Reader | None, fmt: str
) -> tuple[list[Gap], list[tuple[str, bool]]]:
    """Every relative link whose target does not exist, and what was checked."""
    if reader is None:
        return [_not_assessable("links", fmt)], []
    gaps: list[Gap] = []
    checked: list[tuple[str, bool]] = []
    for rel, text in texts.items():
        for link in reader.links(text):
            target = unquote(link.target.split("#", 1)[0].split("?", 1)[0])
            if not target or target.startswith("/") or _EXTERNAL.match(target):
                continue
            exists = ((docs_root / rel).parent / target).exists()
            checked.append((f"{rel}:{link.line}:{target}", exists))
            if not exists:
                gaps.append(
                    Gap("links", f"{rel}:{link.line}", f"{link.target} does not exist")
                )
    return gaps, checked


def _areas(
    manifest: Manifest, settings: dict[str, Any], texts: dict[str, str]
) -> tuple[tuple[Area, ...], list[Gap]]:
    found, gaps = _derive_areas(manifest, settings)
    exclude = [project_relative(glob) for glob in settings["exclude"]]
    areas = tuple(
        sorted(
            (
                area
                for area in found
                if not any(glob and glob_matches(area.path, glob) for glob in exclude)
            ),
            key=lambda area: (area.path, area.name),
        )
    )
    corpus = "\n".join(texts.values())
    for area in areas:
        if not re.search(rf"(?<!\w){re.escape(area.name)}(?!\w)", corpus):
            gaps.append(Gap("areas", area.path, f"{area.name} is named in no doc"))
    return areas, gaps


def _derive_areas(
    manifest: Manifest, settings: dict[str, Any]
) -> tuple[list[Area], list[Gap]]:
    """The areas from ``docs.assess.areas.from``, and a gap where they cannot be."""
    root = manifest.root
    source = settings["from"]
    if source == "declared":
        areas = []
        for entry in settings["declared"]:
            path = project_relative(entry["path"])
            areas.append(Area(str(entry.get("name") or path.rsplit("/", 1)[-1]), path))
        return _unique(areas), []
    if source == "globs":
        directories = {
            path
            for pattern in settings["globs"]
            for path in root.glob(project_relative(pattern))
            if path.is_dir()
        }
        return _unique(_area_of(root, path) for path in sorted(directories)), []
    tracked_names = git.tracked_files(root)
    tracked = None if tracked_names is None else [Path(name) for name in tracked_names]
    areas: list[Area] = []
    gaps: list[Gap] = []
    for ecosystem in manifest.ecosystems:
        answer = get_adapter(ecosystem).source_areas(root, tracked)
        if answer is None:
            gaps.append(
                Gap(
                    "areas",
                    ecosystem,
                    "areas cannot be derived; set docs.assess.areas.from to "
                    "globs or declared",
                )
            )
            continue
        areas.extend(_area_of(root, path) for path in answer)
    return _unique(areas), gaps


def _area_of(root: Path, path: Path) -> Area:
    rel = Path(path).relative_to(root).as_posix()
    return Area(rel.rsplit("/", 1)[-1], rel)


def _unique(areas) -> list[Area]:
    seen: dict[str, Area] = {}
    for area in areas:
        seen.setdefault(area.path, area)
    return list(seen.values())
