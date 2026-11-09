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

The docs project (ADR-018 D4, D5) is the other part that runs anything:
:func:`sync_venv` creates or re-syncs ``<srcdir>/.venv`` with ``uv`` from the
pinned ``requirements.txt`` and records the file's hash there;
:func:`build_docs` runs the environment's own ``sphinx-build`` strictly, HTML
with ``needs_build_json`` forced, and records the result under the state root
for the source tree's digest, which ``docs.builds_strict`` and
``docs.needs_traceable`` read (``core/needs`` holds the record's shape).
``doctor``'s ``docs venv``, ``plantuml`` and ``docs mandate`` rows are
answered here too.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

import yaml

from ..adapters import get_adapter
from ..adapters.sphinx import diagnostics
from ..core import git
from ..core import guidelines as guidelines_reader
from ..core import needs as needs_reader
from ..core.docformat import (
    Link,
    Reader,
    detect_format,
    normalise_heading,
    reader_for,
    rst_directives,
)
from ..core.errors import InvariantError, LockTimeout, StateError, UsageError
from ..core.manifest import (
    ADOPT_MODES,
    ASSESS_CHECKS,
    DATA_DIR,
    Manifest,
    glob_matches,
    project_relative,
)
from ..core.store import JsonStore

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
        areas, area_gaps = _areas(manifest, settings["areas"], _naming_texts(manifest, texts))
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
            exists = _resolves((docs_root / rel).parent / target, link, reader)
            checked.append((f"{rel}:{link.line}:{target}", exists))
            if not exists:
                gaps.append(
                    Gap("links", f"{rel}:{link.line}", f"{link.target} does not exist")
                )
    return gaps, checked


def _resolves(path: Path, link: Link, reader: Reader) -> bool:
    """Whether a link's target exists. A Sphinx document name carries no
    suffix, so it resolves to the file with any suffix the reader reads."""
    if path.exists():
        return True
    return link.document and any(
        path.with_name(path.name + suffix).is_file() for suffix in reader.suffixes
    )


def _naming_texts(manifest: Manifest, texts: dict[str, str]) -> dict[str, str]:
    """The text an area may be named in (D2's whole-word rule).

    Every doc's text, unless the docs are a sphinx-needs project: then only
    the title and content of each ``element`` need, since an area is
    documented when an element need names it (ADR-018 D11). "A sphinx-needs
    project" is ``docs.format: sphinx`` with a ``conf.py`` at the docs
    project's source directory that lists ``sphinx_needs``; one whose
    extensions cannot be read is read as before.
    """
    if manifest.get("docs.format") != "sphinx":
        return texts
    srcdir = needs_reader.srcdir_of(manifest)
    if srcdir is None:
        return texts
    from ..adapters.sphinx import conf_facts

    extensions = conf_facts(srcdir / "conf.py").extensions
    if extensions is None or "sphinx_needs" not in extensions:
        return texts
    elements = {
        kind.directive for kind in needs_reader.types_of(manifest) if kind.role == "element"
    }
    return {
        rel: "\n".join(
            f"{need.argument}\n{need.content}"
            for need in rst_directives(text)
            if need.name in elements
        )
        for rel, text in texts.items()
    }


def _areas(
    manifest: Manifest, settings: dict[str, Any], texts: dict[str, str]
) -> tuple[tuple[Area, ...], list[Gap]]:
    areas, gaps = _assessed_areas(manifest, settings)
    corpus = "\n".join(texts.values())
    for area in areas:
        if not re.search(rf"(?<!\w){re.escape(area.name)}(?!\w)", corpus):
            gaps.append(Gap("areas", area.path, f"{area.name} is named in no doc"))
    return areas, gaps


def _assessed_areas(
    manifest: Manifest, settings: dict[str, Any]
) -> tuple[tuple[Area, ...], list[Gap]]:
    """The areas the assessment holds the docs to, sorted, the excluded ones
    left out, and a gap where they cannot be derived."""
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


# -- the starter (ADR-018 D11) ----------------------------------------------------

#: The starter's shipped text: templates read by eye, as the agents and the
#: machines are, with ``{{name}}`` placeholders this module fills.
STARTER_DIR = DATA_DIR / "scaffold" / "sphinx"

#: How each role's type is drawn by sphinx-needs: (title, colour, style).
_ROLE_LOOK = {
    "requirement": ("Requirement", "#BFD8D2", "node"),
    "decision": ("Decision", "#FEDCD2", "node"),
    "element": ("Element", "#DCB239", "component"),
    "ui": ("UI", "#F4D6A0", "node"),
    "rule": ("Guideline rule", "#E8E8E8", "node"),
    "idea": ("Idea", "#FFF2B3", "node"),
    "bug": ("Bug", "#FFB3B3", "node"),
}
_EXTENSION_LOOK = ("#D7D7D7", "node")


class StarterNotWritten(InvariantError):
    """Why `init` writes no starter where the docs would go: never over a
    file that is there, and never with the root as the docs project."""


@dataclass(frozen=True)
class Starter:
    """The docs project `init` writes (D11) and the settings it owes.

    ``files`` are ``(path, text)`` with the path relative to ``srcdir``;
    ``settings`` are the manifest keys that make the project enforced on it
    (D10), relative to ``base``, the root ``records.docs`` names.
    """

    base: Path
    srcdir: Path
    files: tuple[tuple[str, str], ...]
    settings: dict[str, Any]

    def paths(self) -> list[Path]:
        return [self.srcdir / rel for rel, _text in self.files]


def plan_starter(manifest: Manifest, *, project: str, base: Path, docs: str) -> Starter:
    """The starter for ``docs`` (``paths.docs``, relative to ``base``).

    The source directory is the directory above ``docs`` (D3: the starter's
    ``docs/`` around ``docs/architecture``). ``manifest`` is the
    configuration being written, read for the need types, the assessment's
    sections and the source areas, one ``element`` need each. Nothing is
    written here. Raises :class:`StarterNotWritten` where the source
    directory would be ``base`` itself, already holds a ``conf.py``, or holds
    any file the starter would write.
    """
    rel = project_relative(docs)
    parts = PurePosixPath(rel).parts
    if len(parts) < 2:
        raise StarterNotWritten(
            f"paths.docs {docs} has no directory above it but the root, and the "
            f"root is never the docs project (ADR-018 D3), so the docs starter was "
            f"not written. Set paths.docs to <dir>/{rel}, then run 'invariant init "
            f"--force'."
        )
    srcdir = Path(base) / PurePosixPath(*parts[:-1])
    spelled = PurePosixPath(*parts[:-1]).as_posix()
    if (srcdir / "conf.py").exists():
        raise StarterNotWritten(
            f"{spelled}/conf.py exists, so {spelled}/ is a Sphinx project already and "
            f"the docs starter was not written over it; the docs project for this "
            f"one is ADR-018 D13's (increment 8). Point paths.docs at a directory "
            f"whose parent holds no conf.py, then run 'invariant init --force'."
        )
    types = needs_reader.types_of(manifest)
    element = needs_reader.type_of_role(types, "element")
    rule = needs_reader.type_of_role(types, "rule")
    areas, _gaps = _assessed_areas(manifest, manifest.get("docs.assess")["areas"])
    fill = {
        "project": json.dumps(project),
        "title": project,
        "types": _types_literal(types),
        "item_files": json.dumps([_BACKLOG]),
        "architecture": f"{parts[-1]}/overview",
        "element": element.directive,
        "diagram": _diagram(project, areas, element),
        "elements": _element_needs(areas, element),
        "sections": _extra_sections(manifest.get("docs.assess.sections")),
    }
    files = [
        ("conf.py", _template("conf.py", fill)),
        ("index.rst", _template("index.rst", fill, f"{project} docs")),
        ("requirements.txt", _template("requirements.txt", fill)),
        (".gitignore", _template("gitignore", fill)),
        (
            f"{parts[-1]}/overview.rst",
            _template("architecture/overview.rst", fill, f"{project} architecture"),
        ),
        ("requirements.rst", _template("requirements.rst", fill)),
        ("decisions/index.rst", _template("decisions/index.rst", fill)),
        ("ui.rst", _template("ui.rst", fill)),
        (
            guidelines_reader.RULES_PAGE,
            guidelines_reader.render_rule_needs(
                None, source=None, directive=rule.directive, prefix=rule.prefix
            ),
        ),
        ("checklist.rst", guidelines_reader.render_checklist(None, "sphinx", source=None)),
        (_BACKLOG, _template("backlog.rst", fill)),
    ]
    there = [name for name, _text in files if (srcdir / name).exists()]
    if there:
        raise StarterNotWritten(
            f"{', '.join(f'{spelled}/{name}' for name in there)} already "
            f"exist{'s' if len(there) == 1 else ''}, so the docs starter was not "
            f"written over {'it' if len(there) == 1 else 'them'}. Move "
            f"{'it' if len(there) == 1 else 'them'}, then run 'invariant init --force'."
        )
    settings = {
        "docs": {"format": "sphinx", "needs": {"state": "enforced", "srcdir": spelled}},
        "items": {"backend": "sphinx_needs", "path": f"{spelled}/{_BACKLOG}"},
    }
    return Starter(base=Path(base), srcdir=srcdir, files=tuple(files), settings=settings)


def write_starter(starter: Starter) -> list[Path]:
    """Write the starter's files; the paths written."""
    written = []
    for rel, text in starter.files:
        path = starter.srcdir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


#: The items store's page in the source directory (``items.path``).
_BACKLOG = "backlog.rst"


def _template(name: str, fill: Mapping[str, str], heading: str | None = None) -> str:
    text = (STARTER_DIR / name).read_text(encoding="utf-8")
    values = dict(fill)
    if heading is not None:
        values["heading"] = f"{heading}\n{'=' * len(heading)}"
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _types_literal(types: Sequence[needs_reader.NeedType]) -> str:
    """``needs_types`` as Python, one type per line."""
    lines = ["["]
    for kind in types:
        if kind.role is None:
            title, (colour, style) = kind.directive.capitalize(), _EXTENSION_LOOK
        else:
            title, colour, style = _ROLE_LOOK[kind.role]
        entry = {
            "directive": kind.directive,
            "title": title,
            "prefix": kind.prefix,
            "color": colour,
            "style": style,
        }
        lines.append(f"    {json.dumps(entry)},")
    lines.append("]")
    return "\n".join(lines)


def _element_ids(areas: Sequence[Area], prefix: str) -> list[str]:
    """One need id per area, from its name; from its path where two names
    would give one id."""
    stems = [needs_reader.id_stem(area.name) for area in areas]
    return [
        prefix + (stem if stems.count(stem) == 1 else needs_reader.id_stem(area.path))
        for stem, area in zip(stems, areas)
    ]


def _diagram(project: str, areas: Sequence[Area], element: needs_reader.NeedType) -> str:
    def label(text: str) -> str:
        return text.replace('"', "#quot;")

    if not areas:
        return f'       PROJECT["{label(project)}"]'
    return "\n".join(
        f'       {need}["{label(area.name)}"]'
        for need, area in zip(_element_ids(areas, element.prefix), areas)
    )


def _element_needs(areas: Sequence[Area], element: needs_reader.NeedType) -> str:
    if not areas:
        return (
            "No source area was found when this page was written. Add an element "
            "need for each component as it appears."
        )
    blocks = []
    for need, area in zip(_element_ids(areas, element.prefix), areas):
        blocks.append(
            f".. {element.directive}:: {area.name}\n"
            f"   :id: {need}\n"
            f"\n"
            f"   The ``{area.path}`` source area. Say what it owns, what it never\n"
            f"   does, and which elements it uses."
        )
    return "\n\n".join(blocks)


#: The sections the overview template already holds, as the assessment
#: compares them.
_TEMPLATE_SECTIONS = ("layers", "component responsibilities", "compliance checklist")


def _extra_sections(required: Sequence[str]) -> str:
    """A section for each heading ``docs.assess.sections`` requires beyond
    the template's three, so the starter has no gap on its own."""
    extra = [h for h in required if normalise_heading(h) not in _TEMPLATE_SECTIONS]
    return "".join(
        f"\n{heading}\n{'-' * len(heading)}\n\nWrite this section.\n" for heading in extra
    )


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


def _refuse_under_the_mandate(manifest: Manifest, source: str, mode: str, fmt: str) -> None:
    """ADR-018 D11: Markdown is adopted by copy only, into a migrating
    project, whose migration converts it (D14); an enforced project's docs
    are a sphinx-needs project, so it adopts only rST."""
    if manifest.get("docs.needs.state") == "enforced" and fmt != "sphinx":
        raise UsageError(
            f"{source} is written in {fmt}, and this project is enforced "
            f"(docs.needs.state: enforced): its docs are a sphinx-needs project "
            f"(ADR-018 D1, D11). Adopt it by copy into a migrating project, whose "
            f"migration converts it, or convert it to rST first."
        )
    if fmt == "markdown" and mode == "in_place":
        raise UsageError(
            f"{source} is written in Markdown, which is adopted by copy only, "
            f"into a migrating project whose migration converts it (ADR-018 D11): "
            f"in place, the Markdown would stay the design record. Adopt it by "
            f"copy instead: invariant docs adopt {source} --mode copy"
        )


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
    _refuse_under_the_mandate(manifest, source, "in_place", fmt)
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
    _refuse_under_the_mandate(manifest, source, "copy", fmt)
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


# -- the docs project: environment and strict build (ADR-018 D4, D5) -----------

#: The environment's directory under the source directory, and the file in it
#: holding the hash of the requirements it was synced from.
VENV = ".venv"
VENV_HASH = "invariant-requirements.sha256"
REQUIREMENTS = "requirements.txt"
#: Where the strict build writes its HTML, under the state root (D3, D5).
BUILD_OUTPUT = Path("docs") / "build" / "html"
#: How long a second build waits for one already running before refusing.
_BUILD_LOCK_SECONDS = 1.0


def _run(argv: Sequence[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run one tool, captured. A seam: the suite replaces it."""
    return subprocess.run(
        list(argv), cwd=str(cwd), capture_output=True, text=True, check=False
    )


def _uv() -> str | None:
    """The ``uv`` this process would run. A seam: the suite replaces it."""
    return shutil.which("uv")


def docs_project(manifest: Manifest) -> Path:
    """The docs project's source directory, or a refusal naming the fix."""
    srcdir = needs_reader.srcdir_of(manifest)
    if srcdir is None or not srcdir.is_dir():
        raise UsageError(
            "no docs project: no conf.py at or above paths.docs "
            f"({manifest.get('paths.docs')}). Set docs.needs.srcdir to the docs "
            "project's source directory, or create one there."
        )
    return srcdir


def _sha_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


@dataclass(frozen=True)
class VenvState:
    """Whether ``<srcdir>/.venv`` is there and matches ``requirements.txt``."""

    srcdir: Path
    present: bool
    expected: str | None
    recorded: str | None

    @property
    def venv(self) -> Path:
        return self.srcdir / VENV

    @property
    def sphinx_build(self) -> Path:
        return self.venv / "bin" / "sphinx-build"

    @property
    def in_sync(self) -> bool:
        return self.present and self.expected is not None and self.expected == self.recorded

    def describe(self) -> str:
        if self.expected is None:
            return (
                f"no {REQUIREMENTS} in {self.srcdir}: the environment has no pins; "
                "write one pinning every package with ==, then run 'invariant docs venv'"
            )
        if not self.present:
            return f"no environment at {self.venv}; run 'invariant docs venv'"
        if not self.in_sync:
            return (
                f"{self.venv} does not match {REQUIREMENTS} (synced from "
                f"{(self.recorded or 'nothing recorded')[:12]}, the file is now "
                f"{self.expected[:12]}); run 'invariant docs venv'"
            )
        return f"{self.venv} in sync with {REQUIREMENTS} ({self.expected[:12]})"


def venv_state(srcdir: Path) -> VenvState:
    srcdir = Path(srcdir)
    venv = srcdir / VENV
    recorded = None
    try:
        recorded = (venv / VENV_HASH).read_text(encoding="utf-8").strip() or None
    except OSError:
        pass
    return VenvState(
        srcdir=srcdir,
        present=(venv / "bin" / "sphinx-build").is_file() or (venv / "bin" / "python").is_file(),
        expected=_sha_file(srcdir / REQUIREMENTS),
        recorded=recorded,
    )


def sync_venv(srcdir: Path, *, run=None, uv: str | None = "") -> VenvState:
    """Create ``<srcdir>/.venv`` if absent, ``uv pip sync`` it to the pinned
    ``requirements.txt``, and record the file's hash there (D4).

    The hash is written only after a sync that succeeded, so a failed one
    leaves the environment reading out of sync.
    """
    run = run or _run
    uv = _uv() if uv == "" else uv
    srcdir = Path(srcdir)
    requirements = srcdir / REQUIREMENTS
    if not requirements.is_file():
        raise UsageError(
            f"no {REQUIREMENTS} in {srcdir}: the docs environment is built from "
            f"its pins (ADR-018 D4). Write one with every package pinned with ==."
        )
    if not uv:
        raise UsageError(
            "uv is not on PATH: the docs environment is created with uv "
            "(https://docs.astral.sh/uv/). Install it, then run "
            "'invariant docs venv' again."
        )
    venv = srcdir / VENV
    steps = []
    if not (venv / "bin" / "python").is_file():
        steps.append([uv, "venv", str(venv)])
    steps.append([uv, "pip", "sync", "--python", str(venv / "bin" / "python"), str(requirements)])
    for argv in steps:
        done = run(argv, srcdir)
        if done.returncode != 0:
            said = (done.stderr or done.stdout or "").strip()
            raise UsageError(
                f"{' '.join(argv[1:3])} failed (exit {done.returncode}): {said}"
            )
    expected = _sha_file(requirements)
    (venv / VENV_HASH).write_text(f"{expected}\n", encoding="utf-8")
    return venv_state(srcdir)


def _build_argv(sphinx_build: Path, srcdir: Path, outdir: Path) -> list[str]:
    """D5's command: strict, HTML, needs.json forced through ``-D``."""
    return [
        str(sphinx_build),
        "-E", "-W", "--keep-going", "-n", "-b", "html",
        "-D", "needs_build_json=1",
        str(srcdir), str(outdir),
    ]


def build_docs(
    manifest: Manifest, *, force: bool = False, run=None, now: datetime | None = None
) -> tuple[needs_reader.BuildRecord, bool]:
    """Build the docs project strictly and record it for its tree's digest.

    Answers the record and whether it was built now: a digest already
    recorded green is not built again unless ``force``. An absent or stale
    environment is refused naming ``invariant docs venv`` and records
    nothing, so the gate has no green record (D5).
    """
    run = run or _run
    srcdir = docs_project(manifest)
    digest = needs_reader.tree_digest(srcdir)
    state_root = manifest.state_root
    known = needs_reader.read_record(state_root, digest)
    if known is not None and known.passed and not force:
        return known, False
    env = venv_state(srcdir)
    if not env.in_sync or not env.sphinx_build.is_file():
        said = (
            env.describe()
            if not env.in_sync
            else f"no {env.sphinx_build}; run 'invariant docs venv'"
        )
        raise UsageError(f"the docs build runs the environment's own sphinx-build: {said}")
    store = JsonStore(state_root)
    outdir = state_root / BUILD_OUTPUT
    argv = _build_argv(env.sphinx_build, srcdir, outdir)
    try:
        with store.lock("docs/build", timeout=_BUILD_LOCK_SECONDS):
            done = run(argv, srcdir)
            built = outdir / "needs.json"
            kept = None
            if built.is_file():
                kept = state_root / needs_reader.needs_copy_relpath(digest)
                kept.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(built, kept)
                built.unlink()
    except LockTimeout as exc:
        raise UsageError(
            "another docs build is running on this repository; run "
            "'invariant docs build' again when it finishes"
        ) from exc
    except StateError as exc:
        raise UsageError(str(exc)) from exc
    project = Path(manifest.root).resolve()
    found = [
        _spelled_diagnostic(d.render(), Path(manifest.root))
        for d in diagnostics(done.stderr or "") + diagnostics(done.stdout or "")
    ]
    counts = [
        int(a) + int(c)
        for a, c in re.findall(
            r"updating environment: (?:\[[^\]]*\] )?(\d+) added, (\d+) changed",
            f"{done.stdout}\n{done.stderr}",
        )
    ]
    record = needs_reader.BuildRecord(
        digest=digest,
        passed=done.returncode == 0 and not found,
        exit_code=done.returncode,
        diagnostics=tuple(found),
        needs_json=None if kept is None else str(kept),
        srcdir=_spelled(project, srcdir),
        sphinx_build=str(env.sphinx_build),
        command=shlex.join(argv),
        documents=sum(counts) if counts else None,
        at=(now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    )
    store.write(needs_reader.record_relpath(digest), record.to_dict())
    return record, True


def _spelled_diagnostic(text: str, project: Path) -> str:
    """A diagnostic's file spelled from the project, never the machine."""
    for base in sorted({f"{project}/", f"{project.resolve()}/"}, key=len, reverse=True):
        text = text.replace(base, "")
    return text


# -- doctor's rows (D4, D10) --------------------------------------------------


def mandate_row(manifest: Manifest) -> tuple[str, bool, str]:
    """`docs mandate`: passes when enforced, fails while migrating (D10)."""
    state = manifest.get("docs.needs.state")
    if state == "enforced":
        return ("docs mandate", True, "enforced: the docs are a strict-building sphinx-needs project")
    return (
        "docs mandate",
        False,
        (
            f"migrating (docs.format {manifest.get('docs.format')}, items.backend "
            f"{manifest.get('items.backend')}): the migration to a sphinx-needs docs "
            "project is owed (ADR-018 D14); its last step sets docs.needs.state: enforced"
        ),
    )


def venv_row(manifest: Manifest, *, run=None) -> tuple[str, bool, str]:
    """`docs venv`: present, in sync with ``requirements.txt``, and its
    ``sphinx-build`` runs (D4). Not required while a project migrates on
    another docs format."""
    run = run or _run
    if manifest.get("docs.needs.state") != "enforced" and manifest.get("docs.format") != "sphinx":
        return (
            "docs venv",
            True,
            f"not required until the migration (docs.format {manifest.get('docs.format')})",
        )
    try:
        srcdir = docs_project(manifest)
    except UsageError as exc:
        return ("docs venv", False, str(exc))
    env = venv_state(srcdir)
    if not env.in_sync:
        return ("docs venv", False, env.describe())
    try:
        done = run([str(env.sphinx_build), "--version"], srcdir)
    except OSError as exc:
        return ("docs venv", False, f"{env.sphinx_build} does not run: {exc}")
    if done.returncode != 0:
        return (
            "docs venv",
            False,
            (
                f"{env.sphinx_build} does not run (exit {done.returncode}); "
                "run 'invariant docs venv'"
            ),
        )
    version = (done.stdout or done.stderr or "").strip().splitlines()
    return ("docs venv", True, env.describe() + (f"; {version[0]}" if version else ""))


def plantuml_row(manifest: Manifest) -> tuple[str, bool, str] | None:
    """`plantuml`, when ``docs.diagrams`` lists it: the command ``conf.py``
    names (``plantuml``, the extension's default, when it names none) is
    found. PlantUML needs Java, outside the environment (D4)."""
    if "plantuml" not in manifest.diagram_formats():
        return None
    srcdir = needs_reader.srcdir_of(manifest)
    command = "plantuml"
    if srcdir is not None:
        command = _conf_string(srcdir / "conf.py", "plantuml") or command
    try:
        program = shlex.split(command)[0]
    except (ValueError, IndexError):
        return ("plantuml", False, f"conf.py's plantuml command {command!r} cannot be read")
    found = shutil.which(program)
    if found is None:
        return (
            "plantuml",
            False,
            (
                f"{program!r} (conf.py's plantuml command: {command!r}) is not found; "
                "PlantUML needs Java and a plantuml launcher outside the docs environment"
            ),
        )
    return ("plantuml", True, f"{command!r} found at {found}")


def _conf_string(conf: Path, name: str) -> str | None:
    """A string literal ``conf.py`` assigns to ``name``, read without running it."""
    import ast

    try:
        tree = ast.parse(conf.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        return None
    value = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            value = node.value.value
    return value
