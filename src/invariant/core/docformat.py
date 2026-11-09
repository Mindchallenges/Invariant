"""Per-format readers of documentation text (ADR-016 D1, ruling 5).

The docs assessment reads headings and links through a reader chosen by
``docs.format``, so the same checks hold whatever the docs are written in.
Two readers are built here: Markdown (ATX and setext headings, inline and
reference links) and reStructuredText, ``docs.format: sphinx`` (section
titles, and the roles, directives and hyperlink targets that name a file;
ADR-018 increment 2). A format with no reader answers ``None`` from
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
    #: The target names a Sphinx document, written without its suffix (an rST
    #: ``:doc:`` target or ``toctree`` entry), rather than a file.
    document: bool = False


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


#: A line that is one punctuation character repeated: a section title's
#: underline or overline, or a transition.
_ADORNMENT = re.compile(r"^([!-/:-@\[-`{-~])\1*[ \t]*$")
#: Explicit markup: ``.. `` and what follows, or a bare ``..``.
_EXPLICIT = re.compile(r"^([ \t]*)\.\.(?:[ \t]+(.*))?$")
_DIRECTIVE = re.compile(r"^([\w][\w:.+-]*)::(?:[ \t]+(.*?))?[ \t]*$")
_TARGET = re.compile(r"^_(?:`[^`]+`|[^:`]*):(?:[ \t]+(.*?))?[ \t]*$")
_INLINE_LITERAL = re.compile(r"``(?!\s).*?(?<!\s)``(?!`)")
#: ``:doc:`` (also as ``:std:doc:``) and ``:download:``; ``:ref:`` names a
#: label, not a file, and is not a link here.
_ROLE = re.compile(r"(?<![\w`]):(?:std:)?(doc|download):`([^`]+)`")
#: A hyperlink reference with an embedded target: ```text <target>`_``.
_EMBEDDED = re.compile(r"(?<![:\w])`[^`]*?<([^<>`]+)>`__?(?!\w)")
#: The directives whose argument is a file: ``include`` and its kin.
_FILE_DIRECTIVES = frozenset({"include", "literalinclude", "image", "figure"})
#: The directives whose body is code or other text that is not rST.
_CODE_DIRECTIVES = frozenset(
    {
        "code",
        "code-block",
        "sourcecode",
        "parsed-literal",
        "highlight",
        "math",
        "raw",
        "mermaid",
        "uml",
        "plantuml",
        "graphviz",
        "digraph",
        "graph",
        "doctest",
        "testcode",
        "testoutput",
        "testsetup",
        "testcleanup",
        "ipython",
        "jupyter-execute",
    }
)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _rst_prose(text: str) -> list[str | None]:
    """Every line, ``None`` where it is code or comment rather than rST.

    Held out: a literal block (the indented block after a paragraph ending in
    ``::``), the body of a code directive (``code-block``, ``mermaid``,
    ``math`` and their kin), a comment and its body, and a doctest block.
    The list is as long as the text, so an index is a line number less one.
    """
    lines = text.splitlines()
    kept: list[str | None] = []
    #: The indent a skipped block's lines must exceed; ``None`` when not skipping.
    skip: int | None = None
    doctest = False
    for line in lines:
        stripped = line.strip()
        if skip is not None:
            if not stripped or _indent(line) > skip:
                kept.append(None)
                continue
            skip = None
        if doctest:
            if stripped:
                kept.append(None)
                continue
            doctest = False
        if stripped.startswith(">>>"):
            doctest = True
            kept.append(None)
            continue
        explicit = _EXPLICIT.match(line)
        if explicit:
            body = explicit.group(2) or ""
            directive = _DIRECTIVE.match(body)
            if directive:
                if directive.group(1).lower() in _CODE_DIRECTIVES:
                    skip = _indent(line)
                kept.append(line)
                continue
            if body[:1] in ("_", "[", "|"):
                kept.append(line)
                continue
            # A comment: its line and its body hold nothing rST renders.
            skip = _indent(line)
            kept.append(None)
            continue
        if stripped.endswith("::"):
            skip = _indent(line)
        kept.append(line)
    return kept


def _is_adornment(line: str | None) -> bool:
    return line is not None and bool(_ADORNMENT.match(line))


def _is_text(line: str | None) -> bool:
    return line is not None and bool(line.strip()) and not _is_adornment(line)


def _long_enough(adornment: str, title: str) -> bool:
    """docutils keeps a title whose adornment is shorter than it, with a
    warning, once the adornment is four characters or more."""
    length = len(adornment.rstrip())
    return length >= len(title) or length >= 4


def _overlined_title(line: str, following: str | None, after: str | None) -> str | None:
    """The title between an overline and an underline, ``""`` for three lines
    shaped like one that docutils refuses (the two lines differ, or are too
    short), and ``None`` when the lines are not shaped like one at all."""
    if not (_is_adornment(line) and _is_text(following) and _is_adornment(after)):
        return None
    title = (following or "").strip()
    if line.rstrip() == (after or "").rstrip() and _long_enough(line, title):
        return title
    return ""


class RstReader:
    """reStructuredText's section titles and file links, as a docs check needs them.

    A title is a line of text under a line of one repeated punctuation
    character (an underline), or between two identical ones (an overline as
    well). It starts in the first column, as every section title does, unless
    an overline insets it, and follows a blank line. Links are what can name
    a file: ``:doc:`` targets and ``toctree`` entries, which name a document
    without its suffix (``Link.document``), ``:download:``, the ``include``,
    ``literalinclude``, ``image`` and ``figure`` arguments, embedded
    hyperlink targets (```text <target>`_``) and hyperlink target
    definitions (``.. _name: target``). ``:ref:`` names a label, which is
    not a file, so it is not read as a link. Code (literal blocks, code
    directives, doctest blocks, inline literals) and comments are skipped.
    """

    suffixes: tuple[str, ...] = (".rst",)

    def headings(self, text: str) -> list[Heading]:
        kept = _rst_prose(text)
        found: list[Heading] = []
        index = 0
        while index < len(kept):
            line, following, after = (
                kept[index + offset] if index + offset < len(kept) else None
                for offset in range(3)
            )
            if line is None:
                index += 1
                continue
            overlined = _overlined_title(line, following, after)
            if overlined is not None:
                if overlined:
                    found.append(Heading(index + 2, overlined))
                index += 3
                continue
            previous = kept[index - 1] if index else None
            if (
                _is_text(line)
                and _indent(line) == 0
                and not (previous or "").strip()
                and following is not None
                and _is_adornment(following)
                and _long_enough(following, line.strip())
            ):
                found.append(Heading(index + 1, line.strip()))
                index += 2
                continue
            index += 1
        return found

    def links(self, text: str) -> list[Link]:
        found: list[Link] = []
        toctree: int | None = None
        for index, line in enumerate(_rst_prose(text)):
            number = index + 1
            if line is None:
                continue
            if toctree is not None:
                if not line.strip():
                    continue
                if _indent(line) > toctree:
                    entry = _toctree_entry(line.strip())
                    if entry is not None:
                        found.append(Link(number, *entry))
                    continue
                toctree = None
            explicit = _EXPLICIT.match(line)
            if explicit:
                body = explicit.group(2) or ""
                directive = _DIRECTIVE.match(body)
                if directive:
                    name = directive.group(1).lower()
                    argument = (directive.group(2) or "").strip()
                    if name == "toctree":
                        toctree = _indent(line)
                    elif name in _FILE_DIRECTIVES and argument and not argument.startswith("<"):
                        found.append(Link(number, argument))
                    continue
                target = _TARGET.match(body)
                if target:
                    uri = (target.group(1) or "").strip()
                    if uri and not uri.endswith("_"):
                        found.append(Link(number, uri))
                    continue
            found.extend(_inline_links(number, line))
        return found


def _role_target(content: str) -> str:
    """A role's target: the part in angle brackets when there is a title."""
    explicit = re.search(r"<([^<>]+)>\s*$", content)
    target = explicit.group(1) if explicit else content
    return target.strip().lstrip("~!")


def _inline_links(number: int, line: str) -> list[Link]:
    prose = _INLINE_LITERAL.sub(lambda match: " " * len(match.group(0)), line)
    hits: list[tuple[int, Link]] = []
    for match in _ROLE.finditer(prose):
        target = _role_target(match.group(2))
        if target:
            hits.append((match.start(), Link(number, target, document=match.group(1) == "doc")))
    for match in _EMBEDDED.finditer(prose):
        target = "".join(match.group(1).split())
        if target and not target.endswith("_"):
            hits.append((match.start(), Link(number, target)))
    return [link for _start, link in sorted(hits, key=lambda hit: hit[0])]


#: A scheme (``https:``, ``mailto:``): a toctree entry that is a URL.
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _toctree_entry(entry: str) -> tuple[str, bool] | None:
    """A toctree line's target and whether it names a document, or ``None``
    for an option, ``self`` and a glob pattern."""
    if entry.startswith(":"):
        return None
    explicit = re.search(r"<([^<>]+)>\s*$", entry)
    target = (explicit.group(1) if explicit else entry).strip()
    if not target or target == "self" or any(c in target for c in "*?["):
        return None
    if _SCHEME.match(target):
        return target, False
    return target, True


#: Each ``docs.format`` with a reader: Markdown, and reStructuredText as
#: ``sphinx`` (ADR-018 increment 2).
READERS: dict[str, Reader] = {"markdown": MarkdownReader(), "sphinx": RstReader()}


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
