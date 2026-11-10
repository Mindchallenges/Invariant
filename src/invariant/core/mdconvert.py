"""Markdown converted to reStructuredText through pandoc's document tree (ADR-018 D14).

pandoc does the conversion, run from the docs environment (``pypandoc_binary``
pinned there), so the engine gains no dependency. This module never runs it:
the caller hands in ``pandoc(args, text) -> output``. It reads the Markdown
as pandoc's JSON tree (``-t json``), rewrites the tree, and has pandoc write
the reStructuredText (``-f json -t rst``).

The rewrite is what pandoc cannot know:

* a link is mapped by the caller's ``link(url)``: to a need (``:need:``), a
  document of the docs project (``:doc:``), left as it is (an external URL),
  or refused, naming why;
* raw HTML, and a link the caller refused, cannot be converted. The smallest
  block holding one is left as an ``.. invariant-unconverted::`` directive,
  its reason as the argument and its Markdown as the content. No extension
  registers that directive, so a strict build fails on it (an unknown
  directive is an error), and nothing half-converted lands;
* a ``mermaid`` or ``plantuml`` code block becomes the diagram directive;
* section ids are dropped, because pandoc writes them as labels, and labels
  are global in a Sphinx project, where every record has a "Context".

A decision record (:func:`convert_adr`) is a page of decision needs (D7): the
record need, holding the record's metadata, and one need per ``D<k>``
heading, ``part_of`` the record; each ruling under a "Rulings" heading is its
own paragraph under the label ``adr-<nnn>-ruling-<k>``. Element needs are
never invented: what is an element is a design judgement (D14).

Pure: no file is read, and nothing is run but the callable handed in.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import UsageError

#: The directive an unconvertible construct is left as. Nothing registers it.
UNCONVERTED = "invariant-unconverted"
#: pandoc's reader for the Markdown, unless the caller names another.
READER = "gfm"
#: Code-block languages that are diagrams, and the directive each becomes.
DIAGRAMS = {"mermaid": "mermaid", "plantuml": "uml", "uml": "uml"}

Pandoc = Callable[[Sequence[str], str], str]

#: One HTML comment, the whole of a raw block.
_COMMENT = re.compile(r"^\s*<!--(.*?)-->\s*$", re.DOTALL)


@dataclass(frozen=True)
class Target:
    """Where a link goes: ``role`` and ``target`` for a reference role
    (``need``, ``doc``), ``problem`` when it cannot be converted, nothing to
    leave it as it is. ``note`` is said beside the result (an anchor
    dropped)."""

    role: str | None = None
    target: str | None = None
    problem: str | None = None
    note: str | None = None


Link = Callable[[str], Target]


@dataclass(frozen=True)
class Converted:
    """A converted page: its text, the reason of each construct left as
    :data:`UNCONVERTED`, the notes, and for a record its number, title and
    the need ids it holds."""

    text: str
    unconverted: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()
    number: int | None = None
    title: str = ""


# -- reading the tree ---------------------------------------------------------


def plain(inlines: Sequence[Any]) -> str:
    """The text of a list of inlines, markup dropped."""
    out: list[str] = []
    for node in inlines or ():
        kind, c = node.get("t"), node.get("c")
        if kind == "Str":
            out.append(c)
        elif kind in ("Space", "SoftBreak", "LineBreak"):
            out.append(" ")
        elif kind in ("Code", "Math", "RawInline"):
            out.append(c[-1])
        elif kind in ("Emph", "Strong", "Strikeout", "Superscript", "Subscript",
                      "SmallCaps", "Underline"):
            out.append(plain(c))
        elif kind in ("Quoted",):
            out.append(f'"{plain(c[1])}"')
        elif kind in ("Link", "Image", "Span", "Cite"):
            out.append(plain(c[1]))
    return "".join(out)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _indent(text: str) -> list[str]:
    return [f"   {line}" if line.strip() else "" for line in text.rstrip("\n").split("\n")]


#: The last line of every raw block this module writes. pandoc puts a raw
#: block and the block after it on adjacent lines, which ends an rST
#: directive "without a blank line" (a strict-build warning), so the line is
#: turned back into the blank line it stands for once pandoc has written.
_BLANK = "invariant-blank-line-after-raw-rst"


def _raw(text: str) -> dict:
    return {"t": "RawBlock", "c": ["rst", f"{text}\n{_BLANK}"]}


def _blank_lines(text: str) -> str:
    """Each :data:`_BLANK` line as a blank line, or dropped where one follows."""
    lines = text.split("\n")
    out = []
    for n, line in enumerate(lines):
        if line.strip() != _BLANK:
            out.append(line)
        elif n + 1 < len(lines) and lines[n + 1].strip():
            out.append("")
    return "\n".join(out)


def _escape_role(text: str) -> str:
    return text.replace("\\", "\\\\").replace("`", "\\`").replace("<", "\\<")


# -- the rewrite ----------------------------------------------------------------


class _Rewrite:
    """One page's rewrite: the link callable, what was left unconverted, the
    notes, and whether headers are nested in a need (and so rubrics)."""

    def __init__(self, link: Link, pandoc: Pandoc, reader: str, api: Any) -> None:
        self.link = link
        self.pandoc = pandoc
        self.reader = reader
        self.api = api
        self.unconverted: list[str] = []
        self.notes: list[str] = []

    # pandoc, both directions
    def rst(self, blocks: list) -> str:
        tree = {"pandoc-api-version": self.api, "meta": {}, "blocks": blocks}
        written = self.pandoc(["-f", "json", "-t", "rst", "--wrap=preserve"], json.dumps(tree))
        return _blank_lines(written)

    def markdown(self, block: dict) -> str:
        tree = {"pandoc-api-version": self.api, "meta": {}, "blocks": [block]}
        return self.pandoc(["-f", "json", "-t", self.reader], json.dumps(tree))

    # blocks
    def blocks(self, blocks: Sequence[dict], nested: bool = False) -> list[dict]:
        return [self.block(b, nested) for b in blocks]

    def block(self, block: dict, nested: bool = False) -> dict:
        kind, c = block.get("t"), block.get("c")
        if kind == "BulletList":
            return {"t": kind, "c": [self.blocks(item, nested) for item in c]}
        if kind == "OrderedList":
            return {"t": kind, "c": [c[0], [self.blocks(item, nested) for item in c[1]]]}
        if kind == "BlockQuote":
            return {"t": kind, "c": self.blocks(c, nested)}
        if kind == "Div":
            return {"t": kind, "c": [c[0], self.blocks(c[1], nested)]}
        if kind == "CodeBlock":
            (_, classes, _), text = c
            directive = next((DIAGRAMS[k] for k in classes if k in DIAGRAMS), None)
            if directive:
                return _raw("\n".join([f".. {directive}::", "", *_indent(text)]))
            return block
        if kind == "RawBlock":
            if c[0] == "rst":
                return block
            comment = _COMMENT.match(c[1]) if c[0] == "html" else None
            if comment:
                # A comment stays a comment: a privacy exemption marker
                # (``<!-- invariant: allow-privacy -->``) keeps its block.
                lines = comment.group(1).strip().split("\n")
                return _raw("\n".join([f".. {lines[0].strip()}".rstrip(),
                                       *(f"   {line.strip()}" for line in lines[1:])]))
            return self.leave(block, [f"raw {c[0]} {_one_line(c[1])[:60]}"])
        problems: list[str] = []
        rewritten = self.inlines_in(block, problems)
        if problems:
            return self.leave(block, problems)
        if kind == "Header":
            level, attr, inlines = rewritten["c"]
            if nested:
                return _raw(f".. rubric:: {_one_line(plain(inlines))}")
            rewritten["c"] = [level, ["", attr[1], attr[2]], inlines]
        return rewritten

    def leave(self, block: dict, problems: list[str]) -> dict:
        """The block as the directive nothing registers, its Markdown inside."""
        reason = _one_line("; ".join(dict.fromkeys(problems)))
        self.unconverted.append(reason)
        source = self.markdown(block)
        return _raw("\n".join([f".. {UNCONVERTED}:: {reason}", "", *_indent(source)]))

    # inlines, anywhere inside a block
    def inlines_in(self, node: Any, problems: list[str]) -> Any:
        if isinstance(node, list):
            return [self.inlines_in(n, problems) for n in node]
        if not isinstance(node, dict):
            return node
        kind = node.get("t")
        if kind == "Link":
            return self.mapped(node, problems)
        if kind == "RawInline" and node["c"][0] != "rst":
            problems.append(f"raw {node['c'][0]} {_one_line(node['c'][1])[:60]}")
            return node
        return {k: self.inlines_in(v, problems) if k == "c" else v for k, v in node.items()}

    def mapped(self, node: dict, problems: list[str]) -> dict:
        attr, inlines, (url, title) = node["c"]
        found = self.link(url)
        if found.note:
            self.notes.append(found.note)
        if found.problem:
            problems.append(found.problem)
            return node
        if found.role and found.target:
            text = _one_line(plain(inlines))
            body = f"{_escape_role(text)} <{found.target}>" if text else found.target
            return {"t": "Code", "c": [["", ["interpreted-text"], [["role", found.role]]], body]}
        return {"t": "Link", "c": [attr, self.inlines_in(inlines, problems), [url, title]]}


def _tree(markdown: str, pandoc: Pandoc, reader: str) -> dict:
    out = pandoc(["-f", reader, "-t", "json"], markdown)
    try:
        tree = json.loads(out)
    except (TypeError, ValueError) as exc:
        raise UsageError(f"pandoc gave no document tree ({exc})") from exc
    if not isinstance(tree, dict) or not isinstance(tree.get("blocks"), list):
        raise UsageError("pandoc gave no document tree (no blocks)")
    return tree


def convert_prose(
    markdown: str, *, pandoc: Pandoc, link: Link, reader: str = READER
) -> Converted:
    """A Markdown page as an rST page, through pandoc's tree."""
    tree = _tree(markdown, pandoc, reader)
    rewrite = _Rewrite(link, pandoc, reader, tree.get("pandoc-api-version"))
    text = rewrite.rst(rewrite.blocks(tree["blocks"]))
    return Converted(
        text=text.rstrip("\n") + "\n",
        unconverted=tuple(rewrite.unconverted),
        notes=tuple(dict.fromkeys(rewrite.notes)),
    )


# -- a decision record (D7) -------------------------------------------------------

#: A record's title: ``ADR-<n>``, a dash or colon, the title.
_TITLE = re.compile(r"^ADR[-_ ]?0*(\d+)\s*(?:[—–:-]+\s*(.*))?$", re.IGNORECASE)
#: A decision's heading: ``D<k>``, a dash, colon or full stop, its title.
_DECISION = re.compile(r"^D(\d+)\s*[—–:.-]+\s*(.+)$")
#: A heading the rulings stand under.
_RULINGS = re.compile(r"^rulings?\b", re.IGNORECASE)
#: A lettered ruling in a bullet list: ``A.`` opening the item.
_LETTER = re.compile(r"^([A-Za-z])\.$")
#: An ADR named in text, or a link target naming its file.
_ADR = re.compile(r"\bADR-0*(\d+)")
_VERB = re.compile(r"\b(amends|supersedes)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=\.)\s+(?=[A-Z\[`])")


@dataclass(frozen=True)
class _Meta:
    key: str
    inlines: list


def _key_of(item: list) -> _Meta | None:
    first = item[0] if item else None
    if not first or first.get("t") not in ("Plain", "Para") or not first["c"]:
        return None
    head, *rest = first["c"]
    if head.get("t") != "Strong" or not plain(head["c"]).rstrip().endswith(":"):
        return None
    return _Meta(plain(head["c"]).strip().rstrip(":").strip(), rest)


def _metadata(block: dict | None) -> list[_Meta]:
    """A record's metadata list: the ``- **Key:** value`` items of the list
    right under its title, which opens with one. Its other items (a note in
    bold with no colon) are the record's content, not keys."""
    if not block or block.get("t") != "BulletList" or not block["c"]:
        return []
    if _key_of(block["c"][0]) is None:
        return []
    return [meta for meta in map(_key_of, block["c"]) if meta is not None]


def _sentences(inlines: list) -> list[tuple[str, list[int]]]:
    """Each sentence's text and the ADR numbers it names, in its text or as a
    link's target (a link's text need not name the record it points at)."""
    joined = []
    for node in inlines:
        joined.append(plain([node]))
        if node.get("t") == "Link":
            joined += [f"\x00ADR-{n}\x00" for n in _ADR.findall(node["c"][2][0])]
    out = []
    for sentence in _SENTENCE.split("".join(joined)):
        named = sorted({int(n) for n in _ADR.findall(sentence)})
        out.append((re.sub(r"\x00[^\x00]*\x00", "", sentence), named))
    return out


def _record_links(meta: list[_Meta], number: int) -> dict[str, list[int]]:
    """``amends`` and ``supersedes`` from the metadata (D7).

    A sentence saying "amends" or "supersedes" links each ADR it names; under
    a ``Supersedes`` key the first sentence supersedes, and under an
    ``Amends`` key every sentence amends. "Amended by" and "Superseded by"
    are the other record's links, and are not written here.
    """
    links: dict[str, list[int]] = {"supersedes": [], "amends": []}
    for entry in meta:
        key = entry.key.lower()
        if key.endswith(" by"):
            continue
        for n, (sentence, named) in enumerate(_sentences(entry.inlines)):
            said = _VERB.search(sentence)
            if said:
                kind = said.group(1).lower()
            elif key == "supersedes" and n == 0:
                kind = "supersedes"
            elif key == "amends":
                kind = "amends"
            else:
                continue
            for k in named:
                if k != number and k not in links[kind]:
                    links[kind].append(k)
    return links


def _need(
    directive: str,
    title: str,
    need_id: str,
    status: str,
    links: Mapping[str, Sequence[str]],
    content: str,
) -> str:
    lines = [f".. {directive}:: {_one_line(title)}", f"   :id: {need_id}", f"   :status: {status}"]
    lines += [f"   :{name}: {', '.join(ids)}" for name, ids in links.items() if ids]
    if content.strip():
        lines += ["", *_indent(content)]
    return "\n".join(lines)


def _record_number(title: str, number: int | None) -> tuple[int, str]:
    found = _TITLE.match(title)
    named = int(found.group(1)) if found else None
    if named is not None and number is not None and named != number:
        raise UsageError(
            f"the file is ADR-{number:03d} and its title is ADR-{named:03d}; "
            "rename one so both name the record"
        )
    if named is None and number is None:
        raise UsageError(
            f"no record number: neither the file name nor the title ({title!r}) "
            "names ADR-<n>"
        )
    if found and found.group(2):
        return named, found.group(2).strip()
    return (named if named is not None else number), title


def convert_adr(
    markdown: str,
    *,
    number: int | None,
    directive: str,
    prefix: str,
    statuses: Sequence[str],
    pandoc: Pandoc,
    link: Link,
    reader: str = READER,
) -> Converted:
    """A Markdown ADR as a record page of decision needs (D7).

    ``number`` is the one the file name carries, if any; the title's must
    agree. ``directive`` and ``prefix`` are the ``decision`` role's first
    type. Refused, naming the fix: no title, no number, a status that is not
    one of ``statuses`` (or none), and one ``D<k>`` heading twice.
    """
    tree = _tree(markdown, pandoc, reader)
    blocks = tree["blocks"]
    rewrite = _Rewrite(link, pandoc, reader, tree.get("pandoc-api-version"))
    at = next(
        (n for n, b in enumerate(blocks) if b.get("t") == "Header" and b["c"][0] == 1), None
    )
    if at is None:
        raise UsageError("no title: a record opens with a level-1 heading, '# ADR-<n> — <title>'")
    heading = _one_line(plain(blocks[at]["c"][2]))
    number, title = _record_number(heading, number)
    nnn = f"{number:03d}"
    record = f"{prefix}ADR_{nnn}"

    following = blocks[at + 1] if at + 1 < len(blocks) else None
    meta = _metadata(following)
    status_text = next((plain(m.inlines) for m in meta if m.key.lower() == "status"), None)
    if status_text is None:
        raise UsageError(
            "no Status: a record's metadata list, right under its title, has a "
            "'- **Status:** <status>' line"
        )
    word = re.match(r"\s*([A-Za-z]+)", status_text)
    status = word.group(1).lower() if word else ""
    if status not in statuses:
        raise UsageError(
            f"the status {status or status_text!r} is not one of "
            f"docs.needs.decision_statuses ({', '.join(statuses)})"
        )
    record_links = {
        kind: [f"{prefix}ADR_{k:03d}" for k in numbers]
        for kind, numbers in _record_links(meta, number).items()
    }

    out: list[dict] = list(rewrite.blocks(blocks[:at]))
    title_block = rewrite.block(blocks[at])
    out += [_raw(f".. _adr-{nnn}:"), title_block]
    content = rewrite.rst(rewrite.blocks([following], nested=True)) if meta else ""
    out.append(_raw(_need(directive, title, record, status, record_links, content)))

    needs = [record]
    labels: set[str] = set()
    rulings: int | None = None
    rest = blocks[at + 2 :] if meta else blocks[at + 1 :]
    index = 0
    while index < len(rest):
        block = rest[index]
        index += 1
        if block.get("t") == "Header":
            level, _, inlines = block["c"]
            text = _one_line(plain(inlines))
            if rulings is not None and level <= rulings:
                rulings = None
            decided = _DECISION.match(text)
            if decided:
                k = int(decided.group(1))
                need_id = f"{record}_D{k}"
                if need_id in needs:
                    raise UsageError(f"D{k} is two headings; a decision is one need, {need_id}")
                needs.append(need_id)
                body = []
                while index < len(rest) and not (
                    rest[index].get("t") == "Header" and rest[index]["c"][0] <= level
                ):
                    body.append(rest[index])
                    index += 1
                inner = rewrite.rst(rewrite.blocks(body, nested=True)) if body else ""
                out.append(
                    _raw(
                        _need(directive, decided.group(2), need_id, status,
                              {"part_of": [record]}, inner)
                    )
                )
                continue
            if _RULINGS.match(text):
                rulings = level
            out.append(rewrite.block(block))
            continue
        if rulings is not None and block.get("t") in ("OrderedList", "BulletList"):
            out += _ruling_items(block, nnn, labels, rewrite)
            continue
        out.append(rewrite.block(block))

    text = rewrite.rst(out)
    return Converted(
        text=text.rstrip("\n") + "\n",
        unconverted=tuple(rewrite.unconverted),
        notes=tuple(dict.fromkeys(rewrite.notes)),
        needs=tuple(needs),
        number=number,
        title=title,
    )


def _ruling_items(block: dict, nnn: str, labels: set[str], rewrite: _Rewrite) -> list[dict]:
    """Each ruling of a list as its own one-item list, under its label (D7)."""
    out: list[dict] = []
    if block["t"] == "OrderedList":
        attrs, items = block["c"]
        start = attrs[0]
        for n, item in enumerate(items):
            key = str(start + n)
            one = {"t": "OrderedList", "c": [[start + n, *attrs[1:]], [item]]}
            out += _labelled(key, nnn, labels) + [rewrite.block(one)]
        return out
    for item in block["c"]:
        first = item[0] if item else {}
        inlines = first.get("c") if first.get("t") in ("Plain", "Para") else None
        head = inlines[0] if inlines else {}
        letter = _LETTER.match(head.get("c", "")) if head.get("t") == "Str" else None
        key = letter.group(1).lower() if letter else None
        one = {"t": "BulletList", "c": [item]}
        out += (_labelled(key, nnn, labels) if key else []) + [rewrite.block(one)]
    return out


def _labelled(key: str, nnn: str, labels: set[str]) -> list[dict]:
    label = f"adr-{nnn}-ruling-{key}"
    if label in labels:
        return []
    labels.add(label)
    return [_raw(f".. _{label}:")]

