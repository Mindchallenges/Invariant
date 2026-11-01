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
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

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
