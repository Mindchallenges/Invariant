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

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

#: Provenance goes INSIDE the frontmatter, as a comment.
#:
#: It was once an HTML comment above the block, which pushed the frontmatter to
#: line 2 and made the host skip the file. A comment within the block is
#: nothing to a parser and still greppable, which is all provenance needs.
#:
#: Since A6 increment 3 it records three digests (ADR-012, the A6 ruling 1):
#: ``source=<d> overlay=<d|none> body=<d>``. A copy A6.1 or A6.2 installed
#: carries one, the body's alone, and is read as that (:attr:`Marker.legacy`).
MARKER_PREFIX = "# invariant-managed:"
MARKER_TEMPLATE = MARKER_PREFIX + " source={source} overlay={overlay} body={body}"

#: How the marker spells "no overlay".
NO_OVERLAY = "none"

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


@dataclass(frozen=True)
class Marker:
    """What an installed copy's marker records.

    ``body`` is the witness: the digest of the very text the install wrote.
    ``source`` and ``overlay`` are the digests of the two inputs it was
    rendered from, so an install can say which one moved; ``overlay`` is
    ``None`` when the agent had none. A one-digest marker from A6.1 or A6.2
    records the witness alone, and is ``legacy``.
    """

    body: str
    source: str | None = None
    overlay: str | None = None
    legacy: bool = False


def read_marker(text: str) -> Marker | None:
    """The marker in this file's frontmatter, or ``None`` if it has none.

    A three-digest marker without a ``body`` has no witness, and reads as no
    marker: nothing then says the copy is the library's to rewrite.
    """
    split = split_frontmatter(text)
    if split is None:
        return None
    for line in split[0]:
        stripped = line.strip()
        if not stripped.startswith(MARKER_PREFIX):
            continue
        value = stripped[len(MARKER_PREFIX) :].strip()
        if "=" not in value:
            return Marker(body=value, legacy=True) if value else None
        fields = dict(part.partition("=")[::2] for part in value.split())
        if not fields.get("body"):
            return None
        overlay = fields.get("overlay")
        return Marker(
            body=fields["body"],
            source=fields.get("source") or None,
            overlay=None if overlay in (None, "", NO_OVERLAY) else overlay,
        )
    return None


def marker_digest(text: str) -> str | None:
    """The witness a marker records: the body's own digest, in either form."""
    marker = read_marker(text)
    return marker.body if marker else None


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


def digest_of(source_text: str) -> str:
    """A digest as a managed marker records it. Of the installed text,
    marker-free, it is the witness; of an input, the source's or overlay's."""
    return hashlib.sha256(source_text.encode("utf-8")).hexdigest()[:16]


#: How an installed asset differs from what the library ships. These are the
#: strings `doctor` has always printed; naming them keeps the classifier and
#: its readers from drifting apart about what a divergence IS.
CURRENT = "current"
ABSENT = "absent"
LOCAL = "edited locally"
OLDER = "installed from an older version"
UNREADABLE = "unreadable"


def classify(installed: str | None, shipped: str) -> str:
    """How an installed copy diverges from the source this library ships.

    The one answer, read by `doctor`, every install and `uninstall`. The
    marker is its own witness: an install writes the digest of the very text
    it writes, so a copy whose body still hashes to its own marker is exactly
    what SOME release put there, and one whose body does not was changed
    after. That is the operator's work, whichever release wrote the marker.

    The first rule asked a different question, whether the marker held
    today's digest, and so could only see an edit made under the current
    release. A copy an earlier release installed and the operator then edited
    carries that release's digest, read as "installed from an older version",
    and both install commands rewrote it unnamed (BUG_106). A copy with
    frontmatter but no marker is the project's own file under a shipped name,
    and is never flattened either.

    The rule reads only the witness, so it is the same for a one-digest
    marker (A6.1, A6.2) and a three-digest one (A6.3): no history is needed
    to classify a copy any release wrote. Which input made a stale copy
    stale is :func:`changed_input`'s question, not this one's.
    """
    if installed is None:
        return ABSENT
    rebuilt = reconstruct_source(installed)
    if rebuilt is None:
        return UNREADABLE
    if rebuilt == shipped:
        return CURRENT
    if marker_digest(installed) != digest_of(rebuilt):
        return LOCAL
    return OLDER


#: Which input moved, for an install to say (A6 increment 3). Read off a
#: stale copy's marker against today's source and overlay digests.
LIBRARY_CHANGED = "the library changed"
OVERLAY_CHANGED = "the overlay changed"
BOTH_CHANGED = "the library and the overlay changed"
RENDERER_CHANGED = "the renderer changed"
#: A one-digest marker records no inputs, so of the two it cannot say which.
UNRECORDED = "the library or the renderer changed; its one-digest marker does not say which"


def changed_input(installed: str, source_digest: str, overlay_digest: str | None) -> str | None:
    """Which input moved since ``installed`` was written, or ``None`` with no marker.

    Asked of a copy the classifier calls stale. Where neither input moved,
    the render did. A one-digest marker predates overlays, so an overlay
    configured now is a change; otherwise it cannot tell library from
    renderer, and says so.
    """
    marker = read_marker(installed)
    if marker is None:
        return None
    if marker.legacy:
        return OVERLAY_CHANGED if overlay_digest is not None else UNRECORDED
    library = marker.source != source_digest
    overlay = marker.overlay != overlay_digest
    if library and overlay:
        return BOTH_CHANGED
    if library:
        return LIBRARY_CHANGED
    if overlay:
        return OVERLAY_CHANGED
    return RENDERER_CHANGED


def render_managed(text: str, *, source_digest: str, overlay_digest: str | None) -> str:
    """The host's text with the three-digest marker inserted into its frontmatter.

    ``text`` is what the host reads, and its own digest is the witness.
    """
    split = split_frontmatter(text)
    if split is None:
        raise ValueError("source has no frontmatter on line 1")
    front, body = split
    marker = MARKER_TEMPLATE.format(
        source=source_digest,
        overlay=overlay_digest or NO_OVERLAY,
        body=digest_of(text),
    )
    return "\n".join([FENCE, marker, *front, FENCE, body])
