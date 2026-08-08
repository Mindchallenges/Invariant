"""The memory domain.

Not a state machine — a store with invariants and repair operations. It is in
this library for the same reason the machines are: a hand-maintained index over
hundreds of files drifts, and the drift is silent. Making the index a generated
artifact turns "is it current" from a question nobody asks into a command.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

from ..core.errors import UsageError
from ..core.privacy import PrivacyScanner

VALID_TYPES = {"user", "feedback", "project", "reference"}
LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)

#: Similarity above which two memories are proposed as duplicates. Deliberately
#: high: this rule advises even in the strict profile, because a similarity
#: score proposes a candidate rather than proving a duplicate.
DUPLICATE_THRESHOLD = 0.86


@dataclass
class Memory:
    name: str
    description: str
    type: str
    body: str
    path: Path

    @property
    def links(self) -> list[str]:
        return LINK_RE.findall(self.body)


@dataclass
class MemoryReport:
    count: int = 0
    schema: list[str] = field(default_factory=list)
    index: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    privacy: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.schema or self.index or self.links or self.duplicates or self.privacy)

    def problems(self) -> list[str]:
        return [*self.schema, *self.index, *self.links, *self.duplicates, *self.privacy]


class MemoryStore:
    def __init__(
        self,
        root: Path,
        index_path: Path | None = None,
        scanner: PrivacyScanner | None = None,
    ) -> None:
        self.root = Path(root)
        self.index_path = Path(index_path) if index_path else self.root / "MEMORY.md"
        self.scanner = scanner

    @classmethod
    def from_manifest(cls, manifest) -> "MemoryStore":
        root = manifest.path_for("memory")
        index_name = manifest.get("paths.memory_index") or "MEMORY.md"
        index_path = root / index_name if not Path(index_name).is_absolute() else Path(index_name)
        scanner = (
            PrivacyScanner.from_manifest(manifest)
            if manifest.get("privacy.enabled", True)
            else None
        )
        return cls(root=root, index_path=index_path, scanner=scanner)

    # -- reading -------------------------------------------------------

    def files(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted(
            p for p in self.root.glob("*.md") if p.resolve() != self.index_path.resolve()
        )

    def load(self, path: Path) -> Memory:
        text = Path(path).read_text(encoding="utf-8")
        match = FRONTMATTER_RE.match(text)
        if not match:
            raise UsageError(f"{path.name}: no frontmatter block")
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            raise UsageError(f"{path.name}: frontmatter is not valid YAML ({exc})") from exc
        if not isinstance(meta, dict):
            raise UsageError(f"{path.name}: frontmatter is not a mapping")

        name = str(meta.get("name") or "").strip()
        description = str(meta.get("description") or "").strip()
        mem_type = str(((meta.get("metadata") or {}) or {}).get("type") or "").strip()

        missing = [
            label
            for label, value in (("name", name), ("description", description), ("metadata.type", mem_type))
            if not value
        ]
        if missing:
            raise UsageError(f"{path.name}: missing {', '.join(missing)}")
        if mem_type not in VALID_TYPES:
            raise UsageError(
                f"{path.name}: type {mem_type!r} is not one of "
                + ", ".join(sorted(VALID_TYPES))
            )
        return Memory(
            name=name,
            description=description,
            type=mem_type,
            body=match.group(2),
            path=Path(path),
        )

    def load_all(self) -> tuple[list[Memory], list[str]]:
        memories: list[Memory] = []
        problems: list[str] = []
        for path in self.files():
            try:
                memories.append(self.load(path))
            except UsageError as exc:
                problems.append(str(exc))
        return memories, problems

    # -- checking ------------------------------------------------------

    def check(self) -> MemoryReport:
        memories, schema_problems = self.load_all()
        report = MemoryReport(count=len(memories), schema=schema_problems)

        known = {m.name for m in memories}
        for memory in memories:
            for link in memory.links:
                if link not in known:
                    report.links.append(f"{memory.path.name}: dead link [[{link}]]")

        report.index = self._index_problems(memories)
        report.duplicates = self._duplicate_problems(memories)

        if self.scanner:
            for memory in memories:
                for finding in self.scanner.scan_file(memory.path):
                    report.privacy.append(
                        f"{memory.path.name}:{finding.line} [{finding.pattern}] "
                        f"{finding.redacted}"
                    )
        return report

    def _index_problems(self, memories: Iterable[Memory]) -> list[str]:
        if not self.index_path.is_file():
            return [f"no index at {self.index_path.name}"]
        text = self.index_path.read_text(encoding="utf-8")
        problems: list[str] = []
        referenced = set(re.findall(r"\(([^)]+\.md)\)", text))
        for memory in memories:
            if memory.path.name not in referenced:
                problems.append(f"{memory.path.name} is not in the index")
        for name in sorted(referenced):
            if not (self.root / name).is_file():
                problems.append(f"index references missing file {name}")
        return problems

    def _duplicate_problems(self, memories: list[Memory]) -> list[str]:
        problems: list[str] = []
        for i, a in enumerate(memories):
            for b in memories[i + 1 :]:
                if a.type != b.type:
                    continue
                ratio = difflib.SequenceMatcher(None, a.description, b.description).ratio()
                if ratio >= DUPLICATE_THRESHOLD:
                    problems.append(
                        f"{a.path.name} and {b.path.name} describe the same fact "
                        f"({ratio:.0%} similar)"
                    )
        return problems

    # -- writing -------------------------------------------------------

    def write(self, name: str, description: str, mem_type: str, body: str) -> Path:
        if mem_type not in VALID_TYPES:
            raise UsageError(
                f"type {mem_type!r} is not one of " + ", ".join(sorted(VALID_TYPES))
            )
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if not slug:
            raise UsageError("name must contain at least one alphanumeric character")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{slug}.md"
        frontmatter = yaml.safe_dump(
            {"name": slug, "description": description, "metadata": {"type": mem_type}},
            sort_keys=False,
            allow_unicode=True,
        ).strip()
        path.write_text(f"---\n{frontmatter}\n---\n\n{body.strip()}\n", encoding="utf-8")
        return path

    def regenerate_index(self, title: str = "Memory Index") -> str:
        """Render the index from the directory. The index is an artifact."""
        memories, _ = self.load_all()
        by_type: dict[str, list[Memory]] = {}
        for memory in memories:
            by_type.setdefault(memory.type, []).append(memory)

        lines = [f"# {title}", ""]
        for mem_type in sorted(by_type):
            lines.append(f"## {mem_type}")
            lines.append("")
            for memory in sorted(by_type[mem_type], key=lambda m: m.name):
                lines.append(f"- [{memory.name}]({memory.path.name}) — {memory.description}")
            lines.append("")
        rendered = "\n".join(lines).rstrip() + "\n"
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(rendered, encoding="utf-8")
        return rendered
