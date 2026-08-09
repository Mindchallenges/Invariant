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
    def from_dict(cls, raw: dict) -> "Item":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in raw.items() if k in known})


class Backend(Protocol):
    def load_all(self) -> list[Item]: ...
    def get(self, item_id: str) -> Item | None: ...
    def save(self, item: Item) -> None: ...
    def next_id(self, item_type: str) -> str: ...


# -- markdown ----------------------------------------------------------


class MarkdownBackend:
    """One file per item, frontmatter for fields. Git-native, no tooling."""

    def __init__(self, path: Path, id_prefix: str = "") -> None:
        self.root = Path(path)
        self.id_prefix = id_prefix

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

    def next_id(self, item_type: str) -> str:
        return _next_id(self.load_all(), item_type, self.id_prefix)

    def _parse(self, path: Path) -> Item:
        text = path.read_text(encoding="utf-8")
        match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", text, re.S)
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


class JsonBackend:
    """One document. For machine consumption rather than human editing."""

    def __init__(self, path: Path, id_prefix: str = "") -> None:
        self.path = Path(path)
        self.id_prefix = id_prefix

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

    def next_id(self, item_type: str) -> str:
        return _next_id(self.load_all(), item_type, self.id_prefix)


# -- sphinx-needs ------------------------------------------------------

DIRECTIVE_RE = re.compile(
    r"^\.\.\s+(?P<type>idea|bug)::\s*(?P<title>.*)$", re.M
)


class SphinxNeedsBackend:
    """Directives inside a reStructuredText page.

    For projects already rendering a needs graph. The parser is deliberately
    tolerant: it round-trips the fields it understands and preserves any option
    it does not, so a project's own directive options survive an edit made
    through this library.
    """

    def __init__(self, path: Path, id_prefix: str = "") -> None:
        self.path = Path(path)
        self.id_prefix = id_prefix

    def load_all(self) -> list[Item]:
        if not self.path.is_file():
            return []
        return self._parse(self.path.read_text(encoding="utf-8"))

    def get(self, item_id: str) -> Item | None:
        return next((i for i in self.load_all() if i.id == item_id), None)

    def save(self, item: Item) -> None:
        text = self.path.read_text(encoding="utf-8") if self.path.is_file() else ""
        rendered = self._render(item)
        existing = self._locate(text, item.id)
        if existing:
            start, end = existing
            text = text[:start] + rendered + text[end:]
        else:
            text = text.rstrip() + "\n\n" + rendered
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")

    def next_id(self, item_type: str) -> str:
        return _next_id(self.load_all(), item_type, self.id_prefix)

    # -- parsing ------------------------------------------------------

    def _parse(self, text: str) -> list[Item]:
        items: list[Item] = []
        lines = text.splitlines()
        index = 0
        while index < len(lines):
            match = DIRECTIVE_RE.match(lines[index])
            if not match:
                index += 1
                continue
            item_type, title = match.group("type"), match.group("title").strip()
            index += 1
            options: dict[str, str] = {}
            body: list[str] = []
            # The option block runs until the first blank line, which is what
            # ends it in reStructuredText. Everything after that is body.
            #
            # This used to end the option block at the first line that did not
            # itself look like an option — so a WRAPPED option value fell into
            # the body, and because the test was `option and not body`, every
            # option after it fell in too. Silent, and on data this library did
            # not write: entries authored by hand round-tripped with their
            # fields moved into prose and nothing reported it.
            in_options = True
            last_option: str | None = None
            while index < len(lines):
                line = lines[index]
                if line.strip() and not line.startswith((" ", "\t")):
                    break
                stripped = line.strip()

                if in_options:
                    if not stripped:
                        # A blank line ends the options only once one has been
                        # seen; a directive may be padded before its first.
                        if options:
                            in_options = False
                        index += 1
                        continue
                    option = re.match(r"^:([\w-]+):\s*(.*)$", stripped)
                    if option:
                        last_option = option.group(1)
                        options[last_option] = option.group(2).strip()
                        index += 1
                        continue
                    if last_option is not None:
                        # A continuation of the option above it.
                        existing = options[last_option]
                        options[last_option] = (
                            f"{existing} {stripped}".strip() if existing else stripped
                        )
                        index += 1
                        continue
                    in_options = False

                if stripped or body:
                    body.append(line[3:] if line.startswith("   ") else stripped)
                index += 1
            reserved = {"id", "status", "residue"}
            items.append(
                Item(
                    id=options.get("id", ""),
                    type=item_type,
                    title=title,
                    status=options.get("status", ""),
                    residue=[
                        r.strip() for r in options.get("residue", "").split(";") if r.strip()
                    ],
                    fields={k: v for k, v in options.items() if k not in reserved},
                    body="\n".join(body).strip(),
                )
            )
        return items

    def _locate(self, text: str, item_id: str) -> tuple[int, int] | None:
        lines = text.splitlines(keepends=True)
        offsets, position = [], 0
        for line in lines:
            offsets.append(position)
            position += len(line)

        for index, line in enumerate(lines):
            if not DIRECTIVE_RE.match(line.rstrip("\n")):
                continue
            end = index + 1
            found_id = None
            while end < len(lines):
                stripped = lines[end].strip()
                if stripped and not lines[end].startswith((" ", "\t")):
                    break
                option = re.match(r"^:id:\s*(.*)$", stripped)
                if option:
                    found_id = option.group(1).strip()
                end += 1
            if found_id == item_id:
                stop = offsets[end] if end < len(lines) else position
                return offsets[index], stop
        return None

    def _render(self, item: Item) -> str:
        lines = [f".. {item.type}:: {item.title}", f"   :id: {item.id}"]
        if item.status:
            lines.append(f"   :status: {item.status}")
        for key, value in item.fields.items():
            lines.append(f"   :{key}: {value}")
        if item.residue:
            lines.append(f"   :residue: {'; '.join(item.residue)}")
        lines.append("")
        for line in (item.body or "").splitlines():
            lines.append(f"   {line}" if line.strip() else "")
        lines.append("")
        return "\n".join(lines)


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

    def next_id(self, item_type: str) -> str:
        raise UsageError("the work-item domain is disabled")


# -- selection ---------------------------------------------------------


def backend_for(manifest) -> Backend:
    name = manifest.get("items.backend", "markdown")
    prefix = manifest.get("items.id_prefix", "") or ""
    if name == "none":
        return NullBackend()
    path = (manifest.root / (manifest.get("items.path") or "docs/backlog")).resolve()
    if name == "markdown":
        return MarkdownBackend(path, prefix)
    if name == "json":
        return JsonBackend(path, prefix)
    if name == "sphinx_needs":
        return SphinxNeedsBackend(path, prefix)
    raise UsageError(f"unknown work-item backend {name!r}")


def _next_id(items: list[Item], item_type: str, prefix: str) -> str:
    stem = f"{prefix}{item_type.upper()}_"
    highest = 0
    for item in items:
        match = re.match(rf"^{re.escape(stem)}(\d+)$", item.id)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{stem}{highest + 1:03d}"
