"""Managed host assets — agents, commands, skills.

Three file kinds the host loads by reading YAML-ish frontmatter off line 1.
They were managed to three different degrees: agents were installed, hashed and
drift-checked; commands and skills were not managed at all, and nothing checked
that any of them could be loaded.

That asymmetry was not a decision. Agents got the attention because an agent
failing to load is what someone noticed — four of them sat unloadable for a day
while ``doctor`` reported them current, because the check looked for the marker
whose placement caused the failure. Commands and skills have exactly the same
shape and exactly the same failure mode; they simply had not failed yet.

So the kinds are described here once, as data, and every operation reads that
description. A fourth kind is a table entry, and a bug fixed in loadability is
fixed for all of them at once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Provenance goes INSIDE the frontmatter, as a comment.
#:
#: It was once an HTML comment above the block, which pushed the frontmatter to
#: line 2 and made the host skip the file. A comment within the block is
#: nothing to a parser and still greppable, which is all provenance needs.
MARKER_PREFIX = "# invariant-managed:"
MARKER_TEMPLATE = MARKER_PREFIX + " {digest}"

FENCE = "---"

#: A frontmatter key line. A key is a bare identifier at the start of a line;
#: everything after the first colon is the value.
_KEY_LINE = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$")


@dataclass(frozen=True)
class AssetKind:
    """How one class of host file is laid out and identified."""

    #: Singular noun used in messages: "agent", "command", "skill".
    name: str
    #: Directory under ``.claude/`` holding them.
    directory: str
    #: Frontmatter keys without which the host cannot use the file.
    required: tuple[str, ...]
    #: Whether the frontmatter's ``name`` must match the file's own name.
    #: Commands are addressed by FILENAME and carry no name key, so a mismatch
    #: is not a thing that can happen to them.
    name_must_match: bool
    #: True when each asset is a directory containing a single named file,
    #: rather than a file directly.
    nested_file: str | None = None

    def paths(self, claude_dir: Path) -> list[Path]:
        root = claude_dir / self.directory
        if not root.is_dir():
            return []
        if self.nested_file:
            return sorted(
                child / self.nested_file
                for child in root.iterdir()
                if (child / self.nested_file).is_file()
            )
        return sorted(root.glob("*.md"))

    def identity(self, path: Path) -> str:
        """What the host calls this asset."""
        return path.parent.name if self.nested_file else path.stem


#: Every kind the Claude Code host loads from frontmatter.
KINDS: tuple[AssetKind, ...] = (
    AssetKind(
        name="agent",
        directory="agents",
        required=("name", "description"),
        name_must_match=True,
    ),
    AssetKind(
        name="command",
        directory="commands",
        # A command is addressed by its filename and carries no `name` key.
        # Requiring one would condemn every correct command in existence.
        required=("description",),
        name_must_match=False,
    ),
    AssetKind(
        name="skill",
        directory="skills",
        required=("name", "description"),
        name_must_match=True,
        nested_file="SKILL.md",
    ),
)

BY_NAME = {kind.name: kind for kind in KINDS}


def split_frontmatter(text: str) -> tuple[list[str], str] | None:
    """``(frontmatter lines, body)`` — or ``None`` if the host would skip it.

    Strict about the opening fence being the first line, because that is the
    condition the host itself applies. A helper more forgiving than the thing
    it models cannot be used to predict it.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != FENCE:
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == FENCE:
            return lines[1:index], "\n".join(lines[index + 1 :])
    return None


def frontmatter(text: str) -> dict | None:
    """The frontmatter as a mapping, or ``None`` if it will not load.

    Parsed line by line rather than with a YAML parser, because the host reads
    it that way. Every agent and command shipped here has a ``description``
    containing ``": "``, which strict YAML rejects outright — validating with
    ``yaml.safe_load`` would condemn files that work. A checker stricter than
    the system it models produces false alarms as reliably as a loose one
    produces misses.
    """
    split = split_frontmatter(text)
    if split is None:
        return None
    found: dict[str, str] = {}
    for line in split[0]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _KEY_LINE.match(stripped)
        if match:  # anything else is a continuation, not a new key
            found.setdefault(match.group(1), match.group(2).strip())
    return found or None


def loadability_problem(kind: AssetKind, text: str, expected: str) -> str | None:
    """Why the host would refuse this file, or ``None`` if it would load it."""
    if split_frontmatter(text) is None:
        return "no frontmatter on line 1"
    front = frontmatter(text)
    if front is None:
        return "frontmatter declares no keys"
    for key in kind.required:
        if not front.get(key):
            return f"no {key}"
    if kind.name_must_match and str(front["name"]) != expected:
        return f"declares name {front['name']!r} but is installed as {expected}"
    return None


def marker_digest(text: str) -> str | None:
    split = split_frontmatter(text)
    if split is None:
        return None
    for line in split[0]:
        stripped = line.strip()
        if stripped.startswith(MARKER_PREFIX):
            return stripped[len(MARKER_PREFIX) :].strip()
    return None


def is_managed(text: str) -> bool:
    """Whether this library installed it, as opposed to the project owning it.

    A project's own command is not drift and must never be reported as such —
    but it IS still checked for loadability, because a local file that silently
    fails to load is the same outage as a shipped one.
    """
    return marker_digest(text) is not None


def reconstruct_source(text: str) -> str | None:
    """The installed file with the marker removed — its source form."""
    split = split_frontmatter(text)
    if split is None:
        return None
    front, body = split
    kept = [line for line in front if not line.strip().startswith(MARKER_PREFIX)]
    return "\n".join([FENCE, *kept, FENCE, body])


def render_managed(source_text: str, digest: str) -> str:
    """A source file with the provenance marker inserted into its frontmatter."""
    split = split_frontmatter(source_text)
    if split is None:
        raise ValueError("source has no frontmatter on line 1")
    front, body = split
    return "\n".join([FENCE, MARKER_TEMPLATE.format(digest=digest), *front, FENCE, body])
