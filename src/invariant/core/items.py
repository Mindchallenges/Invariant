"""The work-item model and its pluggable storage backends.

The model is fixed; storage is not. A project keeps whatever format it already
renders — a directory of markdown files, a JSON document, or directives inside
a reStructuredText page — and the lifecycle semantics stay identical across all
of them, because the machine lives above the backend rather than inside it.

``residue`` is a first-class field, separate from ``status``. What a status
claims and what is still owed are different facts, and a store that can only
express the first produces documents that claim more than was proven.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from .errors import UsageError


@dataclass
class Item:
    id: str
    type: str
    title: str
    status: str
    fields: dict[str, Any] = field(default_factory=dict)
    residue: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    body: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> Item:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in raw.items() if k in known})


class Backend(Protocol):
    def load_all(self) -> list[Item]: ...
    def get(self, item_id: str) -> Item | None: ...
    def save(self, item: Item) -> None: ...
    def add(
        self, item: Item, section: str | None = None, after: str | None = None
    ) -> None: ...
    def taken_ids(self) -> dict[str, Path]: ...
    def next_id(self, item_type: str, title: str = "") -> str: ...


class _Store:
    """What every file-backed store shares: the id scan and adding a new item.

    The ids a store has spent are the ones in its open file and, when the
    manifest declares one, in its closed store (``items.closed_path``) — a
    file or directory of the same format, read by the same backend, holding
    entries retired out of the open one. Minting reads both, so a retired id
    is never handed out again (BUG_074 (c)).
    """

    #: The placement option a caller named, if this format has none to offer.
    _no_placement = ""

    def __init__(
        self,
        path: Path,
        id_prefix: str = "",
        id_strategy: str = "numeric",
        closed_path: Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.id_prefix = id_prefix
        self.id_strategy = id_strategy
        self.closed_path = Path(closed_path) if closed_path is not None else None

    def load_all(self) -> list[Item]:  # pragma: no cover - every store overrides
        raise NotImplementedError

    def get(self, item_id: str) -> Item | None:  # pragma: no cover - overridden
        raise NotImplementedError

    def save(self, item: Item) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def taken_ids(self) -> dict[str, Path]:
        """Every id already spent, and the store that holds it.

        A declared closed store that does not exist is an error rather than an
        empty store: read as empty, a mistyped path would let minting hand out
        a retired id with nothing anywhere saying the scan saw nothing.
        """
        taken: dict[str, Path] = {}
        if self.closed_path is not None:
            if not self.closed_path.exists():
                raise UsageError(
                    f"items.closed_path names {self.closed_path}, which does not "
                    "exist; the ids retired there cannot be read, so none is "
                    "minted. Create it or unset the key."
                )
            closed = type(self)(self.closed_path, self.id_prefix, self.id_strategy)
            taken.update({i.id: self.closed_path for i in closed.load_all()})
        taken.update({i.id: self.path for i in self.load_all()})
        return taken

    def next_id(self, item_type: str, title: str = "") -> str:
        return _next_id(
            set(self.taken_ids()), item_type, self.id_prefix, self.id_strategy, title
        )

    def add(
        self, item: Item, section: str | None = None, after: str | None = None
    ) -> None:
        """Save a NEW item; refuse a placement this format cannot express."""
        _refuse_placement(self._no_placement, section, after)
        if self.get(item.id) is not None:
            raise UsageError(f"{item.id} already exists in {self.path.name}; nothing was filed")
        self.save(item)


def _refuse_placement(why: str, section: str | None, after: str | None) -> None:
    """A placement a store has no structure for is refused, never ignored."""
    named = "--section" if section is not None else "--after" if after is not None else None
    if named:
        raise UsageError(
            f"{named} places an entry inside a document's sections; {why}, so "
            "there is nowhere to place it. Nothing was filed."
        )


# -- markdown ----------------------------------------------------------


class MarkdownBackend(_Store):
    """One file per item, frontmatter for fields. Git-native, no tooling."""

    _no_placement = "the markdown backend stores one file per item"

    def __init__(
        self,
        path: Path,
        id_prefix: str = "",
        id_strategy: str = "numeric",
        closed_path: Path | None = None,
    ) -> None:
        super().__init__(path, id_prefix, id_strategy, closed_path)
        self.root = self.path

    def _path(self, item_id: str) -> Path:
        return self.root / f"{item_id}.md"

    def load_all(self) -> list[Item]:
        if not self.root.is_dir():
            return []
        items = []
        for path in sorted(self.root.glob("*.md")):
            try:
                items.append(self._parse(path))
            except UsageError:
                continue
        return items

    def get(self, item_id: str) -> Item | None:
        path = self._path(item_id)
        return self._parse(path) if path.is_file() else None

    def save(self, item: Item) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        meta = {
            "id": item.id,
            "type": item.type,
            "title": item.title,
            "status": item.status,
            "residue": item.residue,
            "history": item.history,
            **item.fields,
        }
        front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
        self._path(item.id).write_text(
            f"---\n{front}\n---\n\n{item.body.strip()}\n", encoding="utf-8"
        )

    def _parse(self, path: Path) -> Item:
        text = path.read_text(encoding="utf-8")
        match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", text, re.DOTALL)
        if not match:
            raise UsageError(f"{path.name}: no frontmatter")
        meta = yaml.safe_load(match.group(1)) or {}
        reserved = {"id", "type", "title", "status", "residue", "history"}
        return Item(
            id=str(meta.get("id") or path.stem),
            type=str(meta.get("type") or "idea"),
            title=str(meta.get("title") or ""),
            status=str(meta.get("status") or ""),
            residue=list(meta.get("residue") or []),
            history=list(meta.get("history") or []),
            fields={k: v for k, v in meta.items() if k not in reserved},
            body=match.group(2).strip(),
        )


# -- json --------------------------------------------------------------


class JsonBackend(_Store):
    """One document. For machine consumption rather than human editing."""

    _no_placement = "a JSON document has no sections"

    def load_all(self) -> list[Item]:
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise UsageError(f"{self.path.name} is not valid JSON: {exc}") from exc
        return [Item.from_dict(r) for r in raw.get("items", [])]

    def get(self, item_id: str) -> Item | None:
        return next((i for i in self.load_all() if i.id == item_id), None)

    def save(self, item: Item) -> None:
        items = {i.id: i for i in self.load_all()}
        items[item.id] = item
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"items": [i.to_dict() for i in items.values()]}, indent=2, sort_keys=True
            )
            + "\n",
            encoding="utf-8",
        )


# -- sphinx-needs ------------------------------------------------------

DIRECTIVE_RE = re.compile(r"^\.\.\s+(?P<type>idea|bug)::\s*(?P<title>.*)$")

#: An option line: a name between colons standing at the option column. The
#: value may follow after whitespace or, as hand-written backlogs spell it,
#: directly (``:area:value``) — this backend has always read that leniently.
OPTION_RE = re.compile(r"^:(?P<name>[\w-]+):(?P<value>.*)$")

#: An inline role — :doc:`x`, :ref:`y`, :py:func:`z` — opening a line. Its
#: closing colon is followed by a backtick, which no option's is; that is what
#: tells a role from an option when one opens a line (BUG_065).
ROLE_RE = re.compile(r"^:[\w.+-]+(?::[\w.+-]+)*:`")

_RESERVED = ("id", "status", "residue")
_INDENT = "   "

#: The line breaks a backlog is split on: CRLF, a lone CR, LF. ``str.splitlines``
#: also breaks on a form feed, ``\x85`` and the Unicode line separators, which
#: would cut a title holding one in two and write it back joined by a newline
#: that was never in the file.
_BREAK = r"\r\n|\r|\n"


def _split_lines(text: str) -> list[str]:
    """Lines with their own terminators: ``\\r\\n``, ``\\r`` or ``\\n``."""
    return re.findall(rf"[^\r\n]*(?:{_BREAK})|[^\r\n]+\Z", text)


def _one_line(value: str) -> str:
    """A value as one line, its own breaks folded the way a re-read joins them.

    An option or a title cannot span lines; writing one that did would put its
    tail outside the entry.
    """
    parts = [part.strip() for part in re.split(_BREAK, value)]
    return " ".join(part for part in parts if part)


def _marker(stripped: str) -> re.Match | None:
    return None if ROLE_RE.match(stripped) else OPTION_RE.match(stripped)


def _content(line: str) -> str:
    return line.removesuffix("\n").removesuffix("\r")


def _ending(line: str) -> str:
    return line[len(_content(line)) :]


def _leading(line: str) -> str:
    content = _content(line)
    return content[: len(content) - len(content.lstrip())]


def _width(content: str) -> int:
    expanded = content.expandtabs(8)
    return len(expanded) - len(expanded.lstrip())


@dataclass
class _Option:
    name: str
    value: str
    start: int
    end: int


@dataclass
class _Entry:
    """One directive, and where each of its parts sits among the file's lines.

    The parse and the write read the same structure, so the lines a save
    rewrites are exactly the lines the parse said held the field.
    """

    type: str
    title: str
    title_at: int  # where the title starts on the directive line
    start: int
    options: list[_Option]
    head_end: int  # one past the option block's last line
    body: str
    content_end: int  # one past the last non-blank line of the entry
    end: int  # one past the block, trailing blank lines included

    def value(self, name: str) -> str | None:
        found = [o.value for o in self.options if o.name == name]
        return found[-1] if found else None

    @property
    def id(self) -> str:
        return self.value("id") or ""

    def item(self) -> Item:
        residue = self.value("residue") or ""
        return Item(
            id=self.id,
            type=self.type,
            title=self.title,
            status=self.value("status") or "",
            residue=[r.strip() for r in residue.split(";") if r.strip()],
            fields={o.name: o.value for o in self.options if o.name not in _RESERVED},
            body=self.body,
        )


def _scan(lines: list[str]) -> list[_Entry]:
    entries: list[_Entry] = []
    index = 0
    while index < len(lines):
        match = DIRECTIVE_RE.match(_content(lines[index]))
        if not match:
            index += 1
            continue
        entry = _read_entry(lines, index, match)
        entries.append(entry)
        index = entry.end
    return entries


def _read_entry(lines: list[str], start: int, match: re.Match) -> _Entry:
    """Read one directive's option block and body.

    The option block runs until the first blank line, which is what ends it in
    reStructuredText (BUG_007); everything after that is body. Inside it, a
    line is classified by where it stands, not by how it opens:

    * at the option column, an option marker (``:name: value``, or leniently
      ``:name:value``) starts a new option — unless the closing colon is
      followed by a backtick, which makes it an inline role (BUG_065);
    * indented deeper, it continues whatever line is above it, so a wrapped
      value whose next line opens with :doc:`...` stays in that value;
    * at the option column but not an option, it is nothing this backend
      understands. It joins no value and belongs to no option's lines, so no
      save rewrites or removes it.
    """
    options: list[_Option] = []
    body: list[str] = []
    column: int | None = None
    in_options = True
    continues: _Option | None = None  # what a deeper line continues, if anything
    head_end = start + 1
    content_end = start + 1
    index = start + 1
    while index < len(lines):
        content = _content(lines[index])
        stripped = content.strip()
        if stripped and not content.startswith((" ", "\t")):
            break
        if stripped:
            content_end = index + 1

        if in_options:
            if not stripped:
                # A blank line ends the options only once one has been seen; a
                # directive may be padded before its first.
                if options:
                    in_options = False
                index += 1
                continue
            width = _width(content)
            marker = _marker(stripped)
            deeper = column is not None and width > column
            if marker and not deeper:
                if column is None:
                    column = width
                option = _Option(
                    marker.group("name"), marker.group("value").strip(), index, index + 1
                )
                options.append(option)
                continues = option
            elif deeper and continues is not None:
                continues.value = (
                    f"{continues.value} {stripped}" if continues.value else stripped
                )
                continues.end = index + 1
            elif options:
                continues = None  # unclassified; a deeper line now continues it
            else:
                in_options = False
            if in_options:
                head_end = index + 1
                index += 1
                continue

        if stripped or body:
            body.append(content[3:] if content.startswith("   ") else stripped)
        index += 1
    return _Entry(
        type=match.group("type"),
        title=match.group("title").strip(),
        title_at=match.start("title"),
        start=start,
        options=options,
        head_end=head_end,
        body="\n".join(body).strip(),
        content_end=content_end,
        end=index,
    )


#: A section adornment: one punctuation character repeated, nothing else.
ADORNMENT_RE = re.compile(r"^([!-/:-@\[-`{-~])\1*$")


@dataclass
class _Heading:
    title: str
    start: int  # the overline, or the title line when there is none
    end: int  # one past the underline


def _headings(lines: list[str]) -> list[_Heading]:
    """The section titles in the file, of every level, in order.

    A title is a line of text standing at column 0, underlined — and
    optionally overlined with the same line — by one punctuation character
    repeated at least as long as the title. That is docutils' own condition
    for a title it accepts without complaint; a shorter underline is not read
    as a heading here, so a caller naming it is refused rather than guessed
    at. An indented line is never a title, which keeps every directive body
    out of the scan.
    """
    found: list[_Heading] = []
    index = 0
    while index < len(lines):
        text = _content(lines[index]).rstrip()
        if ADORNMENT_RE.match(text) and index + 2 < len(lines):
            title = _content(lines[index + 1]).strip()
            under = _content(lines[index + 2]).rstrip()
            if (
                title
                and not ADORNMENT_RE.match(title)
                and under == text
                and len(text) >= len(title)
            ):
                found.append(_Heading(title, index, index + 3))
                index += 3
                continue
        if (
            text
            and index + 1 < len(lines)
            and not text[0].isspace()
            and not text.startswith("..")
            and not ADORNMENT_RE.match(text)
        ):
            under = _content(lines[index + 1]).rstrip()
            if ADORNMENT_RE.match(under) and len(under) >= len(text):
                found.append(_Heading(text, index, index + 2))
                index += 2
                continue
        index += 1
    return found


def _section_end(lines: list[str], section: str, name: str) -> int:
    """Where an entry placed at the end of ``section`` goes.

    The end of the section's OWN content: after its last non-blank line and
    before the next title of any level. In reStructuredText everything after a
    subsection's title belongs to that subsection, so "the end of the section"
    in the wider sense would file the entry under whichever subsection came
    last — the misplacement this option exists to prevent.
    """
    headings = _headings(lines)
    wanted = _one_line(section)
    at = [n for n, h in enumerate(headings) if h.title == wanted]
    if not at:
        titles = ", ".join(repr(h.title) for h in headings) or "none"
        raise UsageError(
            f"{name}: no section titled {wanted!r} (sections here: {titles}); "
            "nothing was filed"
        )
    if len(at) > 1:
        rows = ", ".join(str(headings[n].start + 1) for n in at)
        raise UsageError(
            f"{name}: {len(at)} sections are titled {wanted!r} (lines {rows}); "
            "name an entry to follow with --after instead. Nothing was filed."
        )
    heading = headings[at[0]]
    stop = headings[at[0] + 1].start if at[0] + 1 < len(headings) else len(lines)
    position = heading.end
    for index in range(heading.end, stop):
        if _content(lines[index]).strip():
            position = index + 1
    return position


def _insert_at(lines: list[str], position: int, rendered: list[str], eol: str) -> str:
    """Insert an entry after a non-blank line, one blank line either side."""
    follows = position < len(lines) and bool(_content(lines[position]).strip())
    insert = [eol] + rendered + ([eol] if follows else [])
    return _apply(lines, [(position, position, insert)], eol)


def _option_line(indent: str, name: str, value: str, ending: str) -> str:
    return f"{indent}:{name}:" + (f" {value}" if value else "") + ending


def _wanted_options(item: Item) -> dict[str, str | None]:
    """What each option should hold, in the form the file spells it."""
    wanted: dict[str, str | None] = {"status": _one_line(item.status) or None}
    for key, value in item.fields.items():
        if key not in _RESERVED:
            wanted[key] = _one_line(str(value))
    wanted["residue"] = _one_line("; ".join(item.residue)) or None
    return wanted


def _apply(lines: list[str], edits: list[tuple[int, int, list[str]]], eol: str) -> str:
    """Splice ``(start, end, replacement)`` edits into the lines.

    Edits are non-overlapping. At one position an insertion (``start == end``)
    lands before a replacement, and insertions keep the order they were made.
    A file that ended without a terminator still does.
    """
    unterminated = bool(lines) and not _ending(lines[-1])
    ordered = sorted(
        enumerate(edits), key=lambda e: (e[1][0], e[1][1] != e[1][0], e[0])
    )
    out: list[str] = []
    cursor = 0
    for _, (start, end, replacement) in ordered:
        out.extend(lines[cursor:start])
        if replacement and out and not _ending(out[-1]):
            out[-1] += eol
        out.extend(replacement)
        cursor = max(cursor, end)
    out.extend(lines[cursor:])
    text = "".join(out)
    if unterminated and text.endswith(eol):
        text = text[: -len(eol)]
    return text


class SphinxNeedsBackend(_Store):
    """Directives inside a reStructuredText page.

    For projects already rendering a needs graph. The parser is deliberately
    tolerant: it round-trips the fields it understands and preserves any option
    it does not, so a project's own directive options survive an edit made
    through this library.

    A save is a patch, not a re-render. Only the lines holding a value that
    changed are rewritten; every other byte — a wrapped option, its
    continuation indentation, an inline role, the blank lines around the
    entry, the file's line endings — is the file's own and stays put. Writing
    an entry back from its parsed fields is what reflowed hand-wrapped values
    and dropped the blank line before the next heading (BUG_065).

    ``history`` is not persisted by this backend: the directive has no
    representation for it yet.
    """

    def _read(self) -> str:
        if not self.path.is_file():
            return ""
        # newline="" keeps CRLF as written, so a save can hand it back.
        with self.path.open(encoding="utf-8", newline="") as handle:
            return handle.read()

    def load_all(self) -> list[Item]:
        return [entry.item() for entry in _scan(_split_lines(self._read()))]

    def get(self, item_id: str) -> Item | None:
        return next((i for i in self.load_all() if i.id == item_id), None)

    def save(self, item: Item) -> None:
        self._store(item)

    def add(
        self, item: Item, section: str | None = None, after: str | None = None
    ) -> None:
        """Save a NEW entry, at the end of ``section`` or after entry ``after``.

        With neither, the entry goes where :meth:`save` puts one (BUG_006).
        A placement that names nothing in the file is an error, never a fall
        back to that default: an entry filed in the wrong section is the
        commit-shaped repair job the option exists to remove (BUG_074).
        """
        if section is not None and after is not None:
            raise UsageError(
                "--section and --after are two placements for one entry; give "
                "one. Nothing was filed."
            )
        self._store(item, new=True, section=section, after=after)

    def _store(
        self,
        item: Item,
        new: bool = False,
        section: str | None = None,
        after: str | None = None,
    ) -> None:
        if not _one_line(item.id):
            raise UsageError(
                f"{self.path.name}: an item needs an id to be saved; nothing written"
            )
        lines = _split_lines(self._read())
        eol = next((_ending(line) for line in lines if _ending(line)), "\n")
        entries = _scan(lines)
        entry = next((e for e in entries if e.id == item.id), None)
        if entry is not None and new:
            raise UsageError(
                f"{item.id} already exists in {self.path.name}; nothing was filed"
            )
        if entry is None:
            text = self._insert_new(lines, entries, item, eol, section, after)
        else:
            text = _apply(lines, self._patch(lines, entry, item, eol), eol)
        self._check(text, item, len(entries) + (entry is None))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    def _check(self, text: str, item: Item, count: int) -> None:
        """Refuse a save whose result would not read back as the item saved.

        A patch edits the lines the scan located. If the scan located the wrong
        ones, the result reads back differently from what was meant — a status
        that did not land, an option that is not there, an entry too many — and
        writing it would leave a stray line for a person to find. Nothing is
        written instead.
        """
        entries = _scan(_split_lines(text))
        entry = next((e for e in entries if e.id == item.id), None)
        problem = None
        if len(entries) != count:
            problem = f"{len(entries)} entries where {count} were expected"
        elif entry is None:
            problem = "the entry is not found again"
        else:
            had = _wanted_options(entry.item())
            wanted = _wanted_options(item)
            wrong = [
                name
                for name in ["status", *item.fields]
                if name in wanted and had.get(name) != wanted[name]
            ]
            if entry.title != _one_line(item.title):
                wrong.insert(0, "title")
            if wrong:
                problem = "it would read back with a different " + ", ".join(wrong)
        if problem:
            raise UsageError(
                f"{self.path.name}: refusing to save {item.id}: {problem}. "
                "The file is unchanged."
            )

    def _insert_new(
        self,
        lines: list[str],
        entries: list[_Entry],
        item: Item,
        eol: str,
        section: str | None = None,
        after: str | None = None,
    ) -> str:
        """Place a new entry where the caller named, or beside its own kind.

        A named placement — the end of a section, or right after a named entry
        — is honoured or refused. What follows is the rule with none named.

        Place a new entry beside its own kind, not at the end of the file.

        Appending to the end put an entry outside whatever section it belonged
        to — after the closing prose, past any summary table — so a human then
        had to move it. A file the tool writes and a person must repair is
        worse than one it declines to write, because the broken state is
        commit-shaped.

        This does not parse the document's structure, and does not pretend to:
        it inserts after the LAST directive of the same type, which keeps the
        entry inside whichever section that type already lives in. With no
        entry of that type to anchor to, it falls back to the end and says
        nothing clever. Either way the new entry is separated from what comes
        before and after it by a blank line, and nothing already in the file
        is rewritten.
        """
        rendered = self._render(item, eol)
        if section is not None:
            position = _section_end(lines, section, self.path.name)
            return _insert_at(lines, position, rendered, eol)
        if after is not None:
            anchor = next((e for e in entries if e.id == after), None)
            if anchor is None:
                raise UsageError(
                    f"{self.path.name}: --after {after!r} names no entry in this "
                    "file; nothing was filed"
                )
            return _insert_at(lines, anchor.content_end, rendered, eol)
        if not "".join(lines).strip():
            return "".join(rendered)
        anchors = [e for e in entries if e.type == item.type]
        if not anchors:
            last_blank = not _content(lines[-1]).strip()
            lead = [] if last_blank else [eol]
            return _apply(lines, [(len(lines), len(lines), lead + rendered)], eol)
        return _insert_at(lines, anchors[-1].content_end, rendered, eol)

    def _patch(
        self, lines: list[str], entry: _Entry, item: Item, eol: str
    ) -> list[tuple[int, int, list[str]]]:
        """The line edits that turn ``entry`` into ``item``, and no others."""
        edits: list[tuple[int, int, list[str]]] = []

        title = _one_line(item.title)
        if entry.title != title:
            line = lines[entry.start]
            prefix = _content(line)[: entry.title_at]
            if not prefix.endswith((" ", "\t")):
                prefix += " "
            edits.append(
                (entry.start, entry.start + 1, [prefix + title + _ending(line)])
            )

        indent = _leading(lines[entry.options[0].start]) if entry.options else _INDENT
        last_option = entry.options[-1].end if entry.options else entry.start + 1
        id_spans = [o for o in entry.options if o.name == "id"]
        status_at = id_spans[-1].end if id_spans else last_option

        had = _wanted_options(entry.item())
        wanted = _wanted_options(item)
        present = [o.name for o in entry.options if o.name != "id"]
        for name in dict.fromkeys(present + list(wanted)):
            old, new = had.get(name), wanted.get(name)
            if old == new:
                continue
            spans = [o for o in entry.options if o.name == name]
            if new is None:
                edits.extend((o.start, o.end, []) for o in spans)
            elif spans:
                span = spans[-1]
                own = _leading(lines[span.start])
                ending = _ending(lines[span.end - 1])
                edits.append(
                    (span.start, span.end, [_option_line(own, name, new, ending)])
                )
            else:
                at = status_at if name == "status" else last_option
                edits.append((at, at, [_option_line(indent, name, new, eol)]))

        body = (item.body or "").strip()
        if entry.body != body:
            stop = max(entry.content_end, entry.head_end)
            edits.append((entry.head_end, stop, self._render_body(body, eol)))
        return edits

    @staticmethod
    def _render_body(body: str, eol: str) -> list[str]:
        if not body:
            return []
        return [eol] + [
            f"{_INDENT}{line}{eol}" if line.strip() else eol
            for line in re.split(_BREAK, body)
        ]

    def _render(self, item: Item, eol: str = "\n") -> list[str]:
        lines = [
            f".. {item.type}:: {_one_line(item.title)}{eol}",
            _option_line(_INDENT, "id", _one_line(item.id), eol),
        ]
        for name, value in _wanted_options(item).items():
            if value is not None:
                lines.append(_option_line(_INDENT, name, value, eol))
        return lines + self._render_body((item.body or "").strip(), eol)


# -- null --------------------------------------------------------------


class NullBackend:
    def load_all(self) -> list[Item]:
        return []

    def get(self, item_id: str) -> Item | None:
        return None

    def save(self, item: Item) -> None:
        raise UsageError(
            "the work-item domain is disabled (items.backend is 'none'); set a "
            "backend in the manifest to file items"
        )

    def add(
        self, item: Item, section: str | None = None, after: str | None = None
    ) -> None:
        self.save(item)

    def taken_ids(self) -> dict[str, Path]:
        return {}

    def next_id(self, item_type: str, title: str = "") -> str:
        raise UsageError("the work-item domain is disabled")


# -- selection ---------------------------------------------------------


def backend_for(manifest) -> Backend:
    name = manifest.get("items.backend", "markdown")
    prefix = manifest.get("items.id_prefix", "") or ""
    strategy = manifest.get("items.id_strategy", "numeric") or "numeric"
    if name == "none":
        return NullBackend()
    path = (manifest.root / (manifest.get("items.path") or "docs/backlog")).resolve()
    closed = manifest.get("items.closed_path")
    closed_path = (manifest.root / closed).resolve() if closed else None
    stores = {"markdown": MarkdownBackend, "json": JsonBackend, "sphinx_needs": SphinxNeedsBackend}
    if name in stores:
        return stores[name](path, prefix, strategy, closed_path)
    raise UsageError(f"unknown work-item backend {name!r}")


def _slug_id(stem: str, title: str, taken: set[str]) -> str:
    """A descriptive id derived from the title.

    For backlogs whose entries are referenced by name — as :need: targets in
    architecture docs, say — where a numeric id is both inconsistent with every
    neighbour and unlinkable.
    """
    words = re.findall(r"[A-Za-z0-9]+", title.upper())[:8]
    base = f"{stem}{'_'.join(words)}" if words else stem.rstrip("_")
    candidate, suffix = base, 2
    while candidate in taken:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _next_id(
    taken: set[str],
    item_type: str,
    prefix: str,
    strategy: str = "numeric",
    title: str = "",
) -> str:
    """The next id for ``item_type``, given every id already spent."""
    stem = f"{prefix}{item_type.upper()}_"
    if strategy == "slug":
        return _slug_id(stem, title, taken)
    if strategy != "numeric":
        raise UsageError(
            f"unknown items.id_strategy {strategy!r}; expected 'numeric' or 'slug'"
        )
    highest = 0
    for spent in taken:
        match = re.match(rf"^{re.escape(stem)}(\d+)$", spent)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{stem}{highest + 1:03d}"
