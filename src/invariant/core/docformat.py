"""Per-format readers of documentation text (ADR-016 D1, ruling 5).

The docs assessment reads headings and links through a reader chosen by
``docs.format``, so the same checks hold whatever the docs are written in.
Markdown is the reader built here: ATX and setext headings, inline and
reference links. A format with no reader answers ``None`` from
:func:`reader_for`, and the assessment reports it "not assessable", never a
pass. :func:`detect_format` names the format a set of files is written in,
for `docs adopt` (D7).

Pure: every reader takes text and returns what it found, with 1-based line
numbers, in the order it appears. Nothing here touches the file system.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol


@dataclass(frozen=True)
class Heading:
    line: int
    text: str


@dataclass(frozen=True)
class Link:
    line: int
    target: str


class Reader(Protocol):
    #: The file suffixes a doc in this format carries.
    suffixes: tuple[str, ...]

    def headings(self, text: str) -> list[Heading]: ...

    def links(self, text: str) -> list[Link]: ...


def normalise_heading(text: str) -> str:
    """A heading as the sections check compares it: whitespace collapsed, case folded."""
    return " ".join(text.split()).casefold()


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_ATX_CLOSE = re.compile(r"(?:^|[ \t]+)#+$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_CODE_SPAN = re.compile(r"(`+).*?\1")
_INLINE_LINK = re.compile(r"\]\(\s*(<[^>]*>|[^)\s]*)(?:\s+[\"'(][^)]*)?\s*\)")
_REFERENCE = re.compile(r"^ {0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)")


def _prose_lines(text: str) -> list[tuple[int, str]]:
    """Every line outside a fenced code block, with its 1-based number."""
    lines: list[tuple[int, str]] = []
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        opened = _FENCE.match(line)
        if fence is not None:
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence):
                fence = None
            continue
        if opened:
            fence = opened.group(1)
            continue
        lines.append((number, line))
    return lines


def _target(raw: str) -> str:
    return raw[1:-1].strip() if raw.startswith("<") and raw.endswith(">") else raw


class MarkdownReader:
    """CommonMark's headings and links, as far as a docs check needs them."""

    suffixes: tuple[str, ...] = (".md", ".markdown")

    def headings(self, text: str) -> list[Heading]:
        found: list[Heading] = []
        previous: tuple[int, str] | None = None
        for number, line in _prose_lines(text):
            atx = _ATX.match(line)
            if atx:
                title = _ATX_CLOSE.sub("", (atx.group(2) or "").strip()).strip()
                if title:
                    found.append(Heading(number, title))
                previous = None
                continue
            if (
                _SETEXT.match(line)
                and previous is not None
                and previous[0] == number - 1
                and previous[1].strip()
            ):
                found.append(Heading(previous[0], previous[1].strip()))
                previous = None
                continue
            previous = (number, line)
        return found

    def links(self, text: str) -> list[Link]:
        found: list[Link] = []
        for number, line in _prose_lines(text):
            reference = _REFERENCE.match(line)
            if reference:
                target = _target(reference.group(1))
                if target:
                    found.append(Link(number, target))
                continue
            for match in _INLINE_LINK.finditer(_CODE_SPAN.sub("", line)):
                target = _target(match.group(1))
                if target:
                    found.append(Link(number, target))
        return found


#: Each ``docs.format`` with a reader. rST lands with ADR-015's increment 2.
READERS: dict[str, Reader] = {"markdown": MarkdownReader()}


def reader_for(fmt: str) -> Reader | None:
    """The reader for ``fmt``, or ``None`` when this build has none."""
    return READERS.get(fmt)


#: The format a document file's suffix names, for :func:`detect_format`.
#: reStructuredText is ``sphinx``, the format that selects its reader (D1).
DOCUMENT_SUFFIXES: dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".rst": "sphinx",
    ".adoc": "asciidoc",
    ".asciidoc": "asciidoc",
    ".org": "org",
    ".tex": "latex",
    ".txt": "text",
    ".html": "html",
    ".htm": "html",
}


def detect_format(names: Iterable[str]) -> str:
    """The ``docs.format`` a set of files is written in (ADR-016 D7).

    The format most of the document files carry, a tie going to the name
    that sorts first, so the answer never depends on the order the files
    were listed. Files that are no document (images, a ``conf.py``) are not
    counted. With no document file, the commonest suffix, without its dot,
    names the format, and with no suffix at all it is ``unknown``: either
    way a format with no reader, which the caller reports.
    """
    suffixes = [PurePosixPath(name).suffix.lower() for name in names]
    documents = Counter(DOCUMENT_SUFFIXES[s] for s in suffixes if s in DOCUMENT_SUFFIXES)
    if documents:
        return _commonest(documents)
    others = Counter(s[1:] for s in suffixes if s)
    return _commonest(others) if others else "unknown"


def _commonest(counts: Counter[str]) -> str:
    return min(counts, key=lambda name: (-counts[name], name))
