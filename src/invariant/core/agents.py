"""Host-neutral agent sources (ADR-012 D8, A6 increment 2).

A library agent is written once, in a form no host reads directly: a markdown
body under a frontmatter of ``name``, ``description`` and ``capabilities``.
Each host adapter renders it into its own file, so the source never names a
host's tools or a model.

``capabilities`` is a bracketed list drawn from :data:`CAPABILITIES`; the host
maps each to its own tools. ``roles`` was dropped by the A6 ruling 7, because
the manifest's ``agents.roles`` already owns which agent answers for which
role. ``tier`` arrives with A7 and is refused by name until then.

The body is cut into sections by whole-line anchors::

    <!-- invariant:section phase-1 -->

A section runs from its anchor to the next one. Text before the first anchor is
the preamble. The anchors are what an overlay targets, so it never depends on a
heading's wording, and a host strips them when it renders.

Pure: the caller hands in the text. A source is refused whole, naming every
problem, as the ruleset loader refuses a ruleset.

**Overlays (A6 increment 3).** A project amends a library agent with an
overlay instead of an edited copy (the A6 rulings 4 and 5): a versioned YAML
file of operations, run in order, each against an anchor id::

    version: 1
    operations:
      - append: phase-1          # to the section's end
        text: |
          ...
      - insert_after: phase-2    # a new section, with its own new anchor
        id: house-rules
        text: |
          ...
      - replace: phase-3
        text: |
          ...
      - description_append: Also files work items.

Nothing else may change: no capabilities, no model, no name. :func:`merge`
applies one to a parsed source. It is pure too, so the overlay's text is
handed in, and an overlay is refused whole, each problem named with its fix
(:class:`OverlayError`). A section's separator, its trailing run of blank and
``---`` lines, stays where it is: an append lands before it, a replace keeps
it, and an inserted section carries a copy of its anchor's.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from difflib import get_close_matches

import yaml

from .errors import InvariantError

#: What an agent may ask its host for, in the order a renderer lists them.
CAPABILITIES = ("read", "edit", "shell", "dispatch")

FENCE = "---"
ANCHOR_MARK = "invariant:section"
_ANCHOR = re.compile(r"^<!-- invariant:section ([a-z0-9][a-z0-9-]*) -->$")
_KEY_LINE = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$")

_REQUIRED = ("name", "description", "capabilities")

#: Keys a host's own agent file carries, refused by name so the reason is said.
_REFUSED = {
    "tier": "capability tiers arrive with A7 (ADR-012 D9) and are not read yet",
    "model": "a neutral source never names a model (ADR-012 D1, D9)",
    "tools": "a neutral source declares capabilities, not a host's tool names",
    "roles": "dropped by the A6 ruling 7; the manifest's agents.roles owns it",
}


class AgentSourceError(InvariantError):
    """A neutral agent source that does not validate. Every problem is named."""

    def __init__(self, problems: Iterable[str], name: str | None = None) -> None:
        self.problems = tuple(problems)
        subject = f"agent source {name!r}" if name else "the agent source"
        super().__init__(
            f"{subject} has {len(self.problems)} problem(s): " + "; ".join(self.problems)
        )


@dataclass(frozen=True)
class Section:
    """One anchored section: its id, and its lines up to the next anchor."""

    id: str
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass(frozen=True)
class AgentSource:
    name: str
    description: str
    capabilities: tuple[str, ...]
    preamble_lines: tuple[str, ...]
    sections: tuple[Section, ...]

    @property
    def preamble(self) -> str:
        return "\n".join(self.preamble_lines)

    def section_ids(self) -> tuple[str, ...]:
        return tuple(section.id for section in self.sections)

    def body(self) -> str:
        """The body as a host renders it: every anchor line removed."""
        lines = list(self.preamble_lines)
        for section in self.sections:
            lines.extend(section.lines)
        return "\n".join(lines)


def parse(text: str, expected_name: str | None = None) -> AgentSource:
    """Read a neutral agent source, or refuse it naming every problem."""
    lines = text.split("\n")
    close = _closing_fence(lines)
    if close is None:
        raise AgentSourceError(["no frontmatter on line 1"], expected_name)

    problems: list[str] = []
    front = _read_frontmatter(lines[1:close], problems)
    name = front.get("name", "")
    if expected_name is not None and name and name != expected_name:
        problems.append(f"name: declares {name!r} but the source is named {expected_name!r}")
    capabilities = _capabilities(front.get("capabilities"), problems)
    preamble, sections = _sections(lines[close + 1 :], close + 2, problems)

    if problems:
        raise AgentSourceError(problems, expected_name or name or None)
    return AgentSource(
        name=name,
        description=front["description"],
        capabilities=capabilities,
        preamble_lines=preamble,
        sections=sections,
    )


def _closing_fence(lines: list[str]) -> int | None:
    if not lines or lines[0].strip() != FENCE:
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == FENCE:
            return index
    return None


def _read_frontmatter(lines: list[str], problems: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for offset, line in enumerate(lines):
        if not line.strip():
            continue
        match = _KEY_LINE.match(line)
        if not match:
            problems.append(f"line {offset + 2}: not a 'key: value' line")
            continue
        key, value = match.group(1), match.group(2).strip()
        if key in found:
            problems.append(f"{key}: declared twice")
        elif key in _REFUSED:
            problems.append(f"{key}: {_REFUSED[key]}")
        elif key not in _REQUIRED:
            problems.append(f"{key}: not a key of a neutral agent source")
        else:
            found[key] = value
    for key in _REQUIRED:
        if key not in found or (key != "capabilities" and not found[key]):
            problems.append(f"no {key}")
    return found


def _capabilities(value: str | None, problems: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not (value.startswith("[") and value.endswith("]")):
        problems.append(f"capabilities: {value!r} is not a bracketed list, as [read, edit]")
        return ()
    items = [item.strip() for item in value[1:-1].split(",") if item.strip()]
    if not items:
        problems.append("capabilities: none declared")
    seen: list[str] = []
    for item in items:
        if item not in CAPABILITIES:
            problems.append(f"capabilities: {item!r} is not one of {', '.join(CAPABILITIES)}")
        elif item in seen:
            problems.append(f"capabilities: {item!r} listed twice")
        else:
            seen.append(item)
    return tuple(seen)


def _sections(
    lines: list[str], first_line: int, problems: list[str]
) -> tuple[tuple[str, ...], tuple[Section, ...]]:
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for offset, line in enumerate(lines):
        if ANCHOR_MARK not in line:
            (sections[-1][1] if sections else preamble).append(line)
            continue
        where = f"line {first_line + offset}"
        match = _ANCHOR.match(line.strip())
        if not match or line != line.strip():
            problems.append(f"{where}: a malformed section anchor: {line.strip()!r}")
            continue
        anchor = match.group(1)
        if anchor in seen:
            problems.append(f"{where}: section {anchor!r} is anchored twice")
            continue
        seen.add(anchor)
        sections.append((anchor, []))
    return tuple(preamble), tuple(Section(i, tuple(body)) for i, body in sections)


# -- overlays --------------------------------------------------------------

#: The overlay format this build reads.
OVERLAY_VERSION = 1

#: What an overlay operation may do, and the keys each takes beside its own.
OPERATIONS: dict[str, tuple[str, ...]] = {
    "append": ("text",),
    "insert_after": ("id", "text"),
    "replace": ("text",),
    "description_append": (),
}

_OVERLAY_KEYS = ("version", "operations")

#: Keys an overlay may not carry at all, refused by name with the reason.
_OVERLAY_REFUSED = ("capabilities", "model", "tools", "tier", "name", "roles")
_RULING_5 = (
    "an overlay changes body sections and the description only (the A6 ruling 5)"
)
_SHOW_SECTIONS = "'invariant agents show <name> --sections' lists the anchors"


@dataclass(frozen=True)
class OverlayProblem:
    """One reason an overlay is refused, and what to do about it."""

    problem: str
    fix: str

    def __str__(self) -> str:
        return f"{self.problem} (fix: {self.fix})"


class OverlayError(InvariantError):
    """An overlay that is refused. Every problem is named with its fix, and
    nothing is written."""

    def __init__(self, problems: Iterable[OverlayProblem], subject: str) -> None:
        self.problems = tuple(problems)
        self.subject = subject
        super().__init__(
            f"{subject} is refused, and nothing was written; "
            f"{len(self.problems)} problem(s):\n"
            + "\n".join(f"  {problem}" for problem in self.problems)
        )


@dataclass(frozen=True)
class Operation:
    """One overlay step: its kind, the anchor or text it names, and the rest."""

    index: int
    kind: str
    target: str
    text: str = ""
    new_id: str = ""


#: Problems keyed by the operation they belong to; 0 is the overlay as a whole.
_Problems = dict[int, list[OverlayProblem]]


def _read_overlay(text: str) -> tuple[tuple[Operation, ...], _Problems]:
    """The operations that read, and the problems with the rest.

    Refused at once only where nothing can be read: text that is not YAML, or
    not a mapping. Otherwise every problem is collected, so :func:`merge` can
    add the ones only applying the readable operations can find, and report
    them all in operation order.
    """
    found: _Problems = {0: []}
    problems = found[0]
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise OverlayError(
            [OverlayProblem(f"not valid YAML: {exc}", "correct the YAML syntax")], "the overlay"
        ) from exc
    if not isinstance(raw, Mapping):
        raise OverlayError(
            [
                OverlayProblem(
                    "an overlay must be a mapping",
                    "write 'version: 1' and an 'operations:' list at the top level",
                )
            ],
            "the overlay",
        )
    for key in raw:
        if key in _OVERLAY_REFUSED:
            problems.append(
                OverlayProblem(f"{key!r}: {_RULING_5}", f"remove {key!r} from the overlay")
            )
        elif key not in _OVERLAY_KEYS:
            problems.append(
                OverlayProblem(
                    f"{key!r} is not a key of an overlay",
                    "an overlay carries only 'version' and 'operations'",
                )
            )
    version = raw.get("version")
    if version is None:
        problems.append(OverlayProblem("no version", f"add 'version: {OVERLAY_VERSION}'"))
    elif version != OVERLAY_VERSION or isinstance(version, bool):
        problems.append(
            OverlayProblem(
                f"version {version!r} is not one this build reads",
                f"this build reads overlay version {OVERLAY_VERSION}",
            )
        )
    entries = raw.get("operations")
    operations: list[Operation] = []
    if not isinstance(entries, list) or not entries:
        problems.append(
            OverlayProblem(
                "no operations",
                "list at least one of " + ", ".join(OPERATIONS) + " under 'operations'",
            )
        )
    else:
        for index, entry in enumerate(entries, start=1):
            operation = _operation(index, entry, found.setdefault(index, []))
            if operation is not None:
                operations.append(operation)
    return tuple(operations), found


def _in_order(found: _Problems) -> list[OverlayProblem]:
    return [problem for index in sorted(found) for problem in found[index]]


def _operation(index: int, entry: object, problems: list[OverlayProblem]) -> Operation | None:
    where = f"operation {index}"
    known = ", ".join(OPERATIONS)
    if not isinstance(entry, Mapping):
        problems.append(
            OverlayProblem(
                f"{where} is not a mapping",
                f"write it as one of {known}, as 'append: <id>'",
            )
        )
        return None
    kinds = [key for key in entry if key in OPERATIONS]
    if len(kinds) > 1:
        problems.append(
            OverlayProblem(
                f"{where} names more than one operation ({', '.join(kinds)})",
                "split it into one list entry per operation",
            )
        )
        return None
    if not kinds:
        extra = [key for key in entry if key not in ("text", "id")] or list(entry)
        named = extra[0] if extra else None
        if named in _OVERLAY_REFUSED:
            problems.append(
                OverlayProblem(f"{where}: {named!r}: {_RULING_5}", f"remove {where}")
            )
        else:
            problems.append(
                OverlayProblem(
                    f"{where}: {named!r} is not an operation", f"use one of {known}"
                )
            )
        return None
    kind = kinds[0]
    allowed = OPERATIONS[kind]
    found = len(problems)
    for key in entry:
        if key != kind and key not in allowed:
            problems.append(
                OverlayProblem(
                    f"{where} ({kind}): {key!r} is not a key it takes",
                    f"{kind} takes " + (", ".join(allowed) if allowed else "no other key"),
                )
            )
    value = entry[kind]
    if kind == "description_append":
        if not isinstance(value, str) or not value.strip() or "\n" in value.strip():
            problems.append(
                OverlayProblem(
                    f"{where}: description_append must be one line of text, got {value!r}",
                    "give the sentence to add to the description on one line",
                )
            )
        return None if len(problems) > found else Operation(index, kind, value.strip())
    if not isinstance(value, str) or not value:
        problems.append(
            OverlayProblem(
                f"{where} ({kind}): the anchor must be a section id, got {value!r}",
                _SHOW_SECTIONS,
            )
        )
    text = entry.get("text")
    if text is None:
        problems.append(
            OverlayProblem(f"{where} ({kind}): no text", "add 'text:' with what to write")
        )
    elif not isinstance(text, str):
        problems.append(
            OverlayProblem(
                f"{where} ({kind}): text must be a string, got {text!r}",
                "quote it, or use 'text: |'",
            )
        )
    elif any(ANCHOR_MARK in line for line in text.split("\n")):
        problems.append(
            OverlayProblem(
                f"{where} ({kind}): the text carries a section anchor",
                "add a section with insert_after and its id, never an anchor in text",
            )
        )
    new_id = ""
    if kind == "insert_after":
        raw_id = entry.get("id")
        if raw_id is None:
            problems.append(
                OverlayProblem(f"{where} (insert_after): no id", "give the new section an 'id:'")
            )
        elif not isinstance(raw_id, str) or not _ANCHOR.match(f"<!-- {ANCHOR_MARK} {raw_id} -->"):
            problems.append(
                OverlayProblem(
                    f"{where} (insert_after): id {raw_id!r} is not a section id",
                    "use lower-case letters, digits and hyphens, starting with a letter or digit",
                )
            )
        else:
            new_id = raw_id
    if len(problems) > found:
        return None
    return Operation(index, kind, value, text.rstrip("\n"), new_id)


def merge(source: AgentSource, overlay_text: str | None) -> AgentSource:
    """The source with its overlay applied, or the source itself with none.

    Operations run in order, so a section an earlier one inserts is a target
    for a later one. Every problem is named before any is raised.
    """
    if overlay_text is None:
        return source
    operations, found = _read_overlay(overlay_text)
    description = source.description
    sections = [(section.id, list(section.lines)) for section in source.sections]
    for operation in operations:
        if operation.kind == "description_append":
            description = f"{description} {operation.target}"
            continue
        ids = [anchor for anchor, _ in sections]
        where = f"operation {operation.index} ({operation.kind})"
        problems = found[operation.index]
        if operation.target not in ids:
            problems.append(
                OverlayProblem(
                    f"{where}: the agent has no section {operation.target!r}",
                    _nearest(operation.target, ids),
                )
            )
            continue
        if operation.kind == "insert_after" and operation.new_id in ids:
            problems.append(
                OverlayProblem(
                    f"{where}: the inserted id {operation.new_id!r} is already a section",
                    "choose an id the agent does not carry; " + _SHOW_SECTIONS,
                )
            )
            continue
        at = ids.index(operation.target)
        content, tail = _split_tail(sections[at][1])
        text = operation.text.split("\n")
        if operation.kind == "append":
            sections[at] = (operation.target, [*content, *([""] if content else []), *text, *tail])
        elif operation.kind == "replace":
            sections[at] = (operation.target, [*text, *tail])
        else:
            sections.insert(at + 1, (operation.new_id, [*text, *tail]))
    if any(found.values()):
        raise OverlayError(_in_order(found), f"the overlay for {source.name!r}")
    return replace(
        source,
        description=description,
        sections=tuple(Section(anchor, tuple(lines)) for anchor, lines in sections),
    )


def _split_tail(lines: list[str]) -> tuple[list[str], list[str]]:
    """A section's content, and its separator: the trailing blank and ``---``
    lines that divide it from the next."""
    cut = len(lines)
    while cut and lines[cut - 1].strip() in ("", FENCE):
        cut -= 1
    return lines[:cut], lines[cut:]


def _nearest(wanted: str, ids: list[str]) -> str:
    close = get_close_matches(wanted, ids, n=1, cutoff=0.6)
    if close:
        return f"target a real anchor; the nearest is {close[0]!r}"
    return f"target a real anchor, one of {', '.join(ids)}"
