"""The docs domain: the architecture docs assessed (ADR-016 D1, D2) and adopted (D7, D8).

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
print byte-identical output. The assessment is read-only: it writes nothing.

The incremental rule, ``docs.area_documented`` (D6), asks the same
assessment which touched areas no doc names: :func:`owning_area` places a
changed file in its area, and :meth:`Assessment.unnamed` and
:meth:`Assessment.doc_for` answer the rest.

Headings and links are read through ``core/docformat``'s reader for
``docs.format``. A format with no reader makes ``sections`` and ``links``
report "not assessable", never pass; ``areas`` still reads every file's text.

Adoption (D7, D8) is the one part that writes: :func:`plan_adoption` says
what `docs adopt` would do, in place or by copy, and refuses what it must;
:func:`copy_adoption` copies into the docs location and writes the receipt,
never touching the original; :func:`adoption_drift` names every original
file changed, added or removed since. The configuration itself is written by
the CLI, at the project's own level.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

from ..adapters import get_adapter
from ..core import git
from ..core.docformat import Reader, detect_format, normalise_heading, reader_for
from ..core.errors import UsageError
from ..core.manifest import (
    ADOPT_MODES,
    ASSESS_CHECKS,
    Manifest,
    glob_matches,
    project_relative,
)

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

    def unnamed(self) -> frozenset[str]:
        """The path of each area no doc names."""
        paths = {area.path for area in self.areas}
        return frozenset(
            gap.subject for gap in self.by_check("areas") if gap.subject in paths
        )

    def underivable(self) -> list[Gap]:
        """Each ``areas`` gap that is not an area: an ecosystem whose areas
        cannot be derived."""
        paths = {area.path for area in self.areas}
        return [gap for gap in self.by_check("areas") if gap.subject not in paths]

    def doc_for(self, area: Area) -> str:
        """The doc an unnamed ``area`` could be written in, in this format."""
        reader = reader_for(self.format)
        suffix = reader.suffixes[0] if reader is not None else ".md"
        return f"{self.location}/{area.name}{suffix}"

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


#: The subject of a check that has no reader for the format.
_NOT_ASSESSABLE = "not assessable"


def _not_assessable(check: str, fmt: str) -> Gap:
    return Gap(check, _NOT_ASSESSABLE, f"no reader for {fmt}")


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


def owning_area(areas: Sequence[Area], path: str) -> Area | None:
    """The area ``path`` (project-relative) lies in: the most specific one
    when areas nest, ``None`` when it lies in none (ADR-016 D6)."""
    inside = [
        area for area in areas if path == area.path or path.startswith(area.path + "/")
    ]
    return max(inside, key=lambda area: len(area.path), default=None)


def plan_deliverables(assessment: Assessment) -> list[str]:
    """The all-at-once plan for ``assessment``'s gaps (ADR-016 D5, ruling 4).

    First, when the ``sections`` check has gaps, one ``Write the missing
    sections: <list>`` line; then one ``Document <area>`` line per area no
    doc names, in the assessment's order. A gap no document can close, a
    check that is not assessable or areas that cannot be derived, is not
    planned: it is a configuration to fix, not a doc to write. A broken link
    is not planned either; it is an edit to a doc that exists.
    """
    lines: list[str] = []
    missing = [
        gap.subject for gap in assessment.by_check("sections")
        if gap.subject != _NOT_ASSESSABLE
    ]
    if missing:
        lines.append("Write the missing sections: " + ", ".join(missing))
    unnamed = assessment.unnamed()
    lines.extend(
        f"Document {gap.subject}"
        for gap in assessment.by_check("areas")
        if gap.subject in unnamed
    )
    return lines



# -- adoption: in place or by copy (ADR-016 D7, D8) --------------------------

#: The copy receipt, written in the docs location (D8). Its name starts with a
#: dot, so the assessment holds it out like anything else dot-named.
RECEIPT = ".invariant-adoption.yaml"
_RECEIPT_VERSION = 1


@dataclass(frozen=True)
class Adoption:
    """What `docs adopt` would do: :func:`plan_adoption`'s answer.

    ``location`` is the docs location from then on: the source itself in
    place, the resolved docs location the files are copied into by copy.
    ``spelled`` is the source as the configuration and the receipt record it.
    ``overwrites`` are the copies a forced re-copy writes over, and ``kept``
    the copies whose original the source no longer has, left as they are.
    """

    mode: str
    source: Path
    spelled: str
    location: Path
    format: str
    assessable: bool
    files: tuple[str, ...]
    settings: dict[str, Any]
    overwrites: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()

    def setting_lines(self) -> list[str]:
        """Each setting as ``dotted.key: value``."""
        return setting_lines(self.settings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "source": self.spelled,
            "location": str(self.location),
            "format": self.format,
            "assessable": self.assessable,
            "files": list(self.files),
            "overwrites": list(self.overwrites),
            "kept": list(self.kept),
            "settings": self.settings,
        }


def setting_lines(settings: Mapping[str, Any], prefix: str = "") -> list[str]:
    """Nested ``settings`` as ``dotted.key: value`` phrases, in their order:
    how `docs adopt` and `init` name what they write."""
    out: list[str] = []
    for key, value in settings.items():
        if isinstance(value, Mapping):
            out += setting_lines(value, f"{prefix}{key}.")
        else:
            out.append(f"{prefix}{key}: {value}")
    return out


def plan_adoption(
    manifest: Manifest, source: str, mode: str, *, cwd: Path, force: bool = False
) -> Adoption:
    """What adopting ``source`` in ``mode`` would write; nothing is written.

    ``source`` is read against ``cwd`` when relative. Refused, with the
    reason and the way on, as a :class:`UsageError`: a mode outside the two,
    a source that is no directory or holds no file, and each refusal of
    :func:`_plan_in_place` and :func:`_plan_copy`.
    """
    if mode not in ADOPT_MODES:
        raise UsageError(f"--mode is {mode!r}; it is one of {', '.join(ADOPT_MODES)}")
    given = Path(source).expanduser()
    path = (given if given.is_absolute() else Path(cwd) / given).resolve()
    if not path.is_dir():
        raise UsageError(f"{source} is not a directory: there is nothing to adopt")
    files = tuple(rel for rel, _path in _source_files(path))
    if not files:
        raise UsageError(f"{source} holds no file to adopt (dot-named files are held out)")
    fmt = detect_format(files)
    if mode == "in_place":
        return _plan_in_place(manifest, source, path, files, fmt)
    return _plan_copy(manifest, source, path, files, fmt, force)


def _source_files(root: Path) -> list[tuple[str, Path]]:
    """Every file under ``root``, sorted, anything dot-named held out."""
    found = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if path.is_file() and not any(part.startswith(".") for part in rel.parts):
            found.append((rel.as_posix(), path))
    return sorted(found)


def _roots(manifest: Manifest) -> dict[str, Path]:
    """The roots a docs path key is read against: the repo's, and the store's."""
    roots = {"repo": manifest.root.resolve()}
    if manifest.store_root is not None:
        roots["store"] = Path(manifest.store_root).resolve()
    return roots


def _docs_base(manifest: Manifest) -> Path:
    """The root `records.docs` names, which a relative source is read against."""
    roots = _roots(manifest)
    return roots.get(str(manifest.get("records.docs")), roots["repo"])


def _plan_in_place(
    manifest: Manifest, source: str, path: Path, files: tuple[str, ...], fmt: str
) -> Adoption:
    """In place (D7): `paths.docs` and `records.docs` point at the source,
    which must lie under the repo root or the store root, in a format with a
    reader (ruling 5)."""
    roots = _roots(manifest)
    inside = {
        record: root for record, root in roots.items() if root == path or root in path.parents
    }
    if not inside:
        named = " or the store root " + str(roots["store"]) if "store" in roots else ""
        raise UsageError(
            f"{source} lies under neither the repo root {roots['repo']}{named}. In "
            f"place, paths.docs points at it, and a path key cannot be absolute "
            f"(ADR-014 D1). Adopt it by copy instead: invariant docs adopt "
            f"{source} --mode copy"
        )
    record = max(inside, key=lambda name: len(inside[name].parts))
    rel = path.relative_to(inside[record]).as_posix()
    if rel == ".":
        raise UsageError(
            f"{source} is the {record} root itself: in place, paths.docs would "
            f"name every file there. Name the directory the docs are in."
        )
    if reader_for(fmt) is None:
        raise UsageError(
            f"{source} is written in {fmt}, and this build has no reader for "
            f"{fmt} (ADR-016 ruling 5): in place, the docs-first gate would read "
            f"docs the assessment cannot. Adopt it by copy instead, where its "
            f"sections and links report not assessable: invariant docs adopt "
            f"{source} --mode copy"
        )
    settings: dict[str, Any] = {}
    if manifest.store is not None:
        settings["records"] = {"docs": record}
    settings["paths"] = {"docs": rel}
    settings["docs"] = {"format": fmt, "adopt": {"mode": "in_place", "source": rel}}
    return Adoption(
        mode="in_place",
        source=path,
        spelled=rel,
        location=path,
        format=fmt,
        assessable=True,
        files=files,
        settings=settings,
    )


def _plan_copy(
    manifest: Manifest,
    source: str,
    path: Path,
    files: tuple[str, ...],
    fmt: str,
    force: bool,
) -> Adoption:
    """By copy (D7, D8): into the resolved docs location, never writing the
    original. Refused, unless ``force``, when it would write over a copy
    edited since the receipt or a file no earlier copy wrote."""
    dest = manifest.path_for("docs")
    if path == dest or dest in path.parents or path in dest.parents:
        raise UsageError(
            f"{source} and the docs location {dest} lie one inside the other: "
            f"the copy would be written inside what it copies"
        )
    base = _docs_base(manifest)
    spelled = path.relative_to(base).as_posix() if base in path.parents else str(path)
    recorded: dict[str, dict[str, str]] = {}
    if (dest / RECEIPT).exists():
        receipt, problem = _read_receipt(dest / RECEIPT)
        if problem:
            raise UsageError(f"{problem}; fix or remove it, then adopt again")
        recorded = receipt["files"]
    edited = [
        rel for rel in files
        if rel in recorded
        and (dest / rel).is_file()
        and _sha(dest / rel) != recorded[rel]["copy"]
    ]
    foreign = [
        rel for rel in files
        if rel not in recorded
        and (dest / rel).is_file()
        and _sha(dest / rel) != _sha(path / rel)
    ]
    if (edited or foreign) and not force:
        said = []
        if edited:
            said.append(f"edited since the receipt: {', '.join(edited)}")
        if foreign:
            said.append(f"not written by an earlier copy: {', '.join(foreign)}")
        raise UsageError(
            f"the copy would write over files in {dest} ({'; '.join(said)}). "
            f"Re-copying would lose them; pass --force to overwrite them."
        )
    return Adoption(
        mode="copy",
        source=path,
        spelled=spelled,
        location=dest,
        format=fmt,
        assessable=reader_for(fmt) is not None,
        files=files,
        settings={"docs": {"format": fmt, "adopt": {"mode": "copy", "source": spelled}}},
        overwrites=tuple(sorted(edited + foreign)),
        kept=tuple(
            sorted(rel for rel in recorded if rel not in files and (dest / rel).exists())
        ),
    )


def copy_adoption(adoption: Adoption, *, now: datetime | None = None) -> Path:
    """Copy ``adoption``'s files and write the receipt (D8); the receipt's path.

    Reads the original and writes only the docs location. The receipt holds
    the source as recorded, the time, and per file the SHA-256 of the
    original and of the copy. A copy whose original is gone stays where it is.
    """
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    entries: dict[str, dict[str, str]] = {}
    for rel in adoption.files:
        data = (adoption.source / rel).read_bytes()
        target = adoption.location / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        entries[rel] = {"original": _sha_bytes(data), "copy": _sha(target)}
    receipt = adoption.location / RECEIPT
    body = {
        "version": _RECEIPT_VERSION,
        "source": adoption.spelled,
        "copied_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": entries,
    }
    temporary = receipt.with_name(receipt.name + ".tmp")
    temporary.write_text(yaml.safe_dump(body, sort_keys=True), encoding="utf-8")
    temporary.replace(receipt)
    return receipt


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _read_receipt(path: Path) -> tuple[dict[str, Any], str]:
    """The receipt at ``path``, or a problem naming what is wrong with it."""
    where = f"the adoption receipt {path}"
    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        return {}, f"{where} cannot be read: {exc}"
    if not isinstance(body, dict):
        return {}, f"{where} is not a mapping"
    if body.get("version") != _RECEIPT_VERSION:
        return {}, (
            f"{where} is version {body.get('version')!r}; this build reads "
            f"version {_RECEIPT_VERSION}"
        )
    if not isinstance(body.get("source"), str) or not body["source"].strip():
        return {}, f"{where} names no source"
    files = body.get("files")
    if not isinstance(files, dict):
        return {}, f"{where}: files must map each copied file to its hashes"
    for rel, hashes in files.items():
        if not (
            isinstance(hashes, dict)
            and isinstance(hashes.get("original"), str)
            and isinstance(hashes.get("copy"), str)
        ):
            return {}, f"{where}: {rel} lacks its original and copy hashes"
    return body, ""


@dataclass(frozen=True)
class Drift:
    """How a copy's original has moved since the receipt (D8, ruling 3).

    Reported, never re-synced: re-running `docs adopt --mode copy` is the
    one way the copy follows its original.
    """

    receipt: str
    source: str
    copied_at: str = ""
    reachable: bool = True
    changed: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    problem: str = ""

    @property
    def clean(self) -> bool:
        return (
            not self.problem
            and self.reachable
            and not (self.changed or self.added or self.removed)
        )

    def _moved(self) -> list[tuple[str, tuple[str, ...]]]:
        return [
            (kind, files)
            for kind, files in (
                ("changed", self.changed),
                ("added", self.added),
                ("removed", self.removed),
            )
            if files
        ]

    def detail(self) -> str:
        """One line: `doctor`'s `docs adoption` row."""
        if self.problem:
            return self.problem
        if not self.reachable:
            return f"original not reachable: {self.source} (copied {self.copied_at})"
        if self.clean:
            return f"the original {self.source} is unchanged since the copy of {self.copied_at}"
        moved = "; ".join(f"{kind} {', '.join(files)}" for kind, files in self._moved())
        return (
            f"the original {self.source} moved since the copy of {self.copied_at}: "
            f"{moved}; nothing re-syncs. try: invariant docs adopt {self.source} "
            f"--mode copy"
        )

    def render(self) -> str:
        """The drift heading `docs assess` prints under the assessment."""
        if self.problem:
            return f"adoption drift: {self.problem}"
        if not self.reachable:
            return f"adoption drift: original not reachable: {self.source}"
        if self.clean:
            return f"adoption drift: none (copy of {self.source}, {self.copied_at})"
        head = (
            f"adoption drift: the original {self.source} moved since the copy of "
            f"{self.copied_at}; nothing re-syncs"
        )
        lines = [head]
        lines += [
            f"  - {kind}: {rel}" for kind, files in self._moved() for rel in files
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt": self.receipt,
            "source": self.source,
            "copied_at": self.copied_at,
            "reachable": self.reachable,
            "changed": list(self.changed),
            "added": list(self.added),
            "removed": list(self.removed),
            "problem": self.problem,
            "clean": self.clean,
        }


def adoption_drift(manifest: Manifest) -> Drift | None:
    """Every original file changed, added or removed since the copy (D8);
    ``None`` unless `docs.adopt.mode` is `copy`, so a project that adopted
    nothing reads exactly as before."""
    if manifest.get("docs.adopt.mode") != "copy":
        return None
    path = manifest.path_for("docs") / RECEIPT
    spelled = _spelled(manifest.root, path)
    configured = str(manifest.get("docs.adopt.source"))
    if not path.is_file():
        return Drift(
            receipt=spelled,
            source=configured,
            problem=(
                f"no receipt at {spelled}, so what the copy was taken from is "
                f"unknown; re-run 'invariant docs adopt {configured} --mode copy'"
            ),
        )
    receipt, problem = _read_receipt(path)
    if problem:
        return Drift(receipt=spelled, source=configured, problem=problem)
    recorded = {rel: hashes["original"] for rel, hashes in receipt["files"].items()}
    given = Path(receipt["source"])
    original = given if given.is_absolute() else _docs_base(manifest) / given
    base = Drift(
        receipt=spelled, source=receipt["source"], copied_at=str(receipt.get("copied_at", ""))
    )
    if not original.is_dir():
        return replace(base, reachable=False)
    current = {rel: _sha(file) for rel, file in _source_files(original)}
    return replace(
        base,
        changed=tuple(
            sorted(rel for rel in recorded if rel in current and current[rel] != recorded[rel])
        ),
        added=tuple(sorted(set(current) - set(recorded))),
        removed=tuple(sorted(set(recorded) - set(current))),
    )
