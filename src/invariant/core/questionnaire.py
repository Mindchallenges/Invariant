"""The configuration questionnaire: its catalogue and what each question resolves to.

ADR-017 D1 and D2. The catalogue is data, ``data/questionnaire/catalogue.yaml``:
sections of questions, each over one manifest key, and three steps the engine
asks by code (``commands``, ``docs``, ``store``). This module loads and checks
it, and resolves every question against a project's configuration: its
default (the value the configuration resolves to now), that value's
provenance label as ``config show`` prints it, its current value at the
target level, the levels allowed to hold its key, and its choices.

It prompts for nothing and writes nothing. It reads ``core/manifest``,
``core/machines``, ``core/home``, ``core/privacy`` and the adapters, and
imports nothing above ``core``, so the oracle boundary holds by construction.
``cli/main`` renders its report after a non-interactive ``init`` (D9).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .. import adapters
from . import machines
from .errors import ManifestError
from .home import PROJECT_SPECIFIC
from .manifest import (
    COMMAND_NAMES,
    DATA_DIR,
    DEFAULTS,
    ONBOARDING_MODES,
    RECORD_LOCATIONS,
    Manifest,
    profile_names,
)
from .privacy import PATTERNS
from .stores import POINTER_KEYS

#: The shipped catalogue (D1).
CATALOGUE = DATA_DIR / "questionnaire" / "catalogue.yaml"

#: A question's kinds (D1). ``step`` is asked by code.
KINDS = ("choice", "text", "path", "count", "percent", "bool", "list", "command", "step")

#: The kinds that take choices, fixed or from a source.
_CHOICE_KINDS = ("choice", "list")

#: The steps the engine ships, each a handler in code (D1).
STEPS = ("commands", "docs", "store")

#: The named sources a question's choices may come from (D1).
CHOICE_SOURCES = (
    "profiles",
    "pipeline_machines",
    "ecosystems",
    "library_agents",
    "privacy_patterns",
    "record_locations",
)

#: The config levels an answer can be written at, lowest first (D4).
LEVELS = ("user", "store", "store-repo", "repo")

#: Keys the user level refuses beyond the project-specific blocks:
#: `docs.adopt.source` (ADR-016 ruling 11, `Manifest._validate_docs_adopt`).
#: The suite loads each at the user level to prove the manifest refuses it.
USER_REFUSED_KEYS = ("docs.adopt.source",)

#: The placeholders a command must carry, by command name (D1, D6).
COMMAND_PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    "test_one": ("selector",),
    "coverage_one": ("selector",),
}

#: The commands the non-interactive report lists for each ecosystem (D9);
#: the rest are in `configure --questions`.
REPORTED_COMMANDS = ("test", "test_one")

_TOP_KEYS = ("version", "core", "advanced", "sections")
_SECTION_KEYS = ("title", "questions")
_QUESTION_KEYS = ("id", "key", "kind", "prompt", "choices", "choices_from", "placeholders")


@dataclass(frozen=True)
class Entry:
    """A question as the catalogue writes it."""

    id: str
    kind: str
    prompt: str
    #: The manifest key written; ``None`` for a step.
    key: str | None = None
    choices: tuple[Any, ...] | None = None
    choices_from: str | None = None
    placeholders: tuple[str, ...] = ()


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    questions: tuple[Entry, ...]


@dataclass(frozen=True)
class Catalogue:
    """The sections, and which are core and which advanced, in order."""

    core: tuple[str, ...]
    advanced: tuple[str, ...]
    sections: Mapping[str, Section]

    def ordered(self) -> list[tuple[str, Section]]:
        """``(tier, section)`` for every section asked: core, then advanced."""
        return [
            *(("core", self.sections[sid]) for sid in self.core),
            *(("advanced", self.sections[sid]) for sid in self.advanced),
        ]


@dataclass(frozen=True)
class Question:
    """A question resolved against a project's configuration (D2)."""

    id: str
    section: str
    key: str | None
    kind: str
    prompt: str
    choices: tuple[Any, ...] | None
    placeholders: tuple[str, ...]
    #: The value the configuration resolves to now.
    default: Any
    #: Where that value came from, as `config show --provenance` labels it.
    provenance: str
    #: The target level's own value, when it sets one.
    current: Any
    set_at_target: bool
    #: The levels that may hold the key, lowest first.
    allowed: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "section": self.section,
            "key": self.key,
            "kind": self.kind,
            "prompt": self.prompt,
            "choices": None if self.choices is None else list(self.choices),
            "placeholders": list(self.placeholders),
            "default": self.default,
            "provenance": self.provenance,
            "current": self.current,
            "set_at_target": self.set_at_target,
            "allowed": list(self.allowed),
        }

    def report_dict(self) -> dict[str, Any]:
        """The row `init --json` carries under `configuration` (D9)."""
        return {
            "id": self.id,
            "key": self.key,
            "value": self.default,
            "provenance": self.provenance,
        }


# -- the catalogue -------------------------------------------------------


def load_catalogue(path: Path | None = None) -> Catalogue:
    """Read and check a catalogue, the shipped one unless ``path`` is given.

    Every malformed entry is refused naming the file and the entry: an
    unknown kind, a question over a key the manifest does not have, choices on
    a kind that takes none, a step the engine does not ship, a section listed
    in both tiers or not at all among the sections, and an id used twice.
    """
    path = CATALOGUE if path is None else Path(path)
    where = f"the questionnaire catalogue {path}"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ManifestError(f"{where} is not valid YAML: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ManifestError(f"{where} must contain a mapping")
    _refuse_unknown(where, raw, _TOP_KEYS)
    if raw.get("version", 1) != 1:
        raise ManifestError(
            f"{where} is version {raw['version']}; this release understands version 1"
        )
    raw_sections = raw.get("sections")
    if not isinstance(raw_sections, Mapping):
        raise ManifestError(f"{where}: sections must be a mapping of section id to section")
    seen: set[str] = set()
    sections = {
        str(sid): _section(where, str(sid), body, seen) for sid, body in raw_sections.items()
    }
    core = _tier(where, raw, "core", sections)
    advanced = _tier(where, raw, "advanced", sections)
    both = sorted(set(core) & set(advanced))
    if both:
        raise ManifestError(
            f"{where}: section {both[0]!r} is listed in both core and advanced"
        )
    return Catalogue(core=core, advanced=advanced, sections=sections)


def _tier(
    where: str, raw: Mapping[str, Any], tier: str, sections: Mapping[str, Section]
) -> tuple[str, ...]:
    ids = raw.get(tier, [])
    if not isinstance(ids, list):
        raise ManifestError(f"{where}: {tier} must be a list of section ids")
    for index, sid in enumerate(ids):
        if sid not in sections:
            raise ManifestError(
                f"{where}: {tier} names {sid!r}, which is not a section; the "
                f"sections are {', '.join(sections)}"
            )
        if sid in ids[:index]:
            raise ManifestError(f"{where}: {tier} lists {sid!r} twice")
    return tuple(ids)


def _section(where: str, sid: str, body: Any, seen: set[str]) -> Section:
    if not isinstance(body, Mapping):
        raise ManifestError(f"{where}: section {sid!r} must be a mapping")
    _refuse_unknown(f"{where}: section {sid!r}", body, _SECTION_KEYS)
    title = body.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ManifestError(f"{where}: section {sid!r} needs a title")
    questions = body.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ManifestError(f"{where}: section {sid!r} needs a list of questions")
    entries = tuple(_entry(f"{where}: section {sid!r}", q) for q in questions)
    for entry in entries:
        if entry.id in seen:
            raise ManifestError(f"{where}: the question {entry.id!r} is listed twice")
        seen.add(entry.id)
    return Section(id=sid, title=title, questions=entries)


def _entry(where: str, raw: Any) -> Entry:
    if not isinstance(raw, Mapping):
        raise ManifestError(f"{where}: every question must be a mapping, got {raw!r}")
    _refuse_unknown(f"{where}: a question", raw, _QUESTION_KEYS)
    qid, kind, prompt = raw.get("id"), raw.get("kind"), raw.get("prompt")
    if not isinstance(qid, str) or not qid:
        raise ManifestError(f"{where}: a question needs an id")
    where = f"{where}, question {qid!r}"
    if kind not in KINDS:
        raise ManifestError(f"{where}: kind {kind!r} is not one of {', '.join(KINDS)}")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ManifestError(f"{where}: a question needs a prompt")
    choices, source = raw.get("choices"), raw.get("choices_from")
    placeholders = raw.get("placeholders") or []
    if kind == "step":
        if qid not in STEPS:
            raise ManifestError(
                f"{where}: there is no step named {qid!r}; the engine ships "
                f"{', '.join(STEPS)}"
            )
        if "key" in raw:
            raise ManifestError(f"{where}: a step writes no key of its own")
        if choices is not None or source is not None:
            raise ManifestError(f"{where}: a step takes no choices")
        return Entry(id=qid, kind=kind, prompt=prompt)
    key = raw.get("key", qid)
    if key != qid:
        raise ManifestError(f"{where}: a plain question writes one key, and its id is its key")
    if not _is_manifest_key(qid):
        raise ManifestError(f"{where}: {qid!r} is not a manifest key")
    if (choices is not None or source is not None) and kind not in _CHOICE_KINDS:
        raise ManifestError(f"{where}: a {kind} question takes no choices")
    if choices is not None and source is not None:
        raise ManifestError(f"{where}: a question has both choices and choices_from")
    if choices is not None and not isinstance(choices, list):
        raise ManifestError(f"{where}: choices must be a list")
    if source is not None and source not in CHOICE_SOURCES:
        raise ManifestError(
            f"{where}: choices_from {source!r} is not one of {', '.join(CHOICE_SOURCES)}"
        )
    if placeholders and kind != "command":
        raise ManifestError(f"{where}: only a command question takes placeholders")
    return Entry(
        id=qid,
        kind=kind,
        prompt=prompt,
        key=qid,
        choices=None if choices is None else tuple(choices),
        choices_from=source,
        placeholders=tuple(placeholders),
    )


def _is_manifest_key(dotted: str) -> bool:
    """Is ``dotted`` a leaf, or a whole mapping value, the manifest holds?

    A key whose value is a mapping of further keys (``methodology.gates``) is
    a block, not a question; one whose mapping is the value itself
    (``agents.tiers``, empty by default) is a key.
    """
    node: Any = DEFAULTS
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return False
        node = node[part]
    return not (isinstance(node, Mapping) and node)


def _refuse_unknown(where: str, mapping: Mapping[str, Any], known: Sequence[str]) -> None:
    for key in mapping:
        if key not in known:
            raise ManifestError(f"{where} has {key!r}, which is not one of {', '.join(known)}")


# -- levels ----------------------------------------------------------------


def targets(manifest: Manifest) -> tuple[str, ...]:
    """The levels this project has, as `config show` names them (D4)."""
    if manifest.store is not None:
        return LEVELS
    return ("user", "repo")


def default_target(manifest: Manifest) -> str:
    """`repo`, or `store-repo` in a store project (D4, ruling 10)."""
    return "store-repo" if manifest.store is not None else "repo"


def allowed_levels(key: str | None, *, store_project: bool) -> tuple[str, ...]:
    """The levels that may hold ``key``, lowest first (D4).

    Read off the rules the manifest enforces at load: the user level refuses
    the project-specific blocks and ``USER_REFUSED_KEYS``; a store project's
    repo level is its pointer, which holds only ``POINTER_KEYS``.
    """
    if key is None:
        return ()
    top = key.split(".")[0]
    allowed = []
    if top not in PROJECT_SPECIFIC and key not in USER_REFUSED_KEYS:
        allowed.append("user")
    if store_project:
        allowed += ["store", "store-repo"]
        if top in POINTER_KEYS:
            allowed.append("repo")
    else:
        allowed.append("repo")
    return tuple(allowed)


def _level_of(manifest: Manifest, label: str) -> str | None:
    if label.startswith("user:"):
        return "user"
    if label.startswith("repo:"):
        return "repo"
    if manifest.store is not None:
        if label == manifest.store.repo_label:
            return "store-repo"
        if label == manifest.store.label:
            return "store"
    return None


def _target_level(manifest: Manifest, target: str) -> Mapping[str, Any]:
    for label, level in manifest.levels:
        if _level_of(manifest, label) == target:
            return level
    return {}


_UNSET = object()


def _dig(mapping: Mapping[str, Any], dotted: str) -> Any:
    node: Any = mapping
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return _UNSET
        node = node[part]
    return node


# -- resolve -----------------------------------------------------------------


def resolve(
    catalogue: Catalogue,
    manifest: Manifest,
    target: str | None = None,
    sections: Iterable[str] | None = None,
) -> list[Question]:
    """Every question of ``sections`` (all asked ones, core then advanced, when
    ``None``), resolved against ``manifest`` for the level ``target``
    (``default_target`` when ``None``). Steps are expanded into the questions
    they ask."""
    target = default_target(manifest) if target is None else target
    offered = targets(manifest)
    if target not in offered:
        raise ManifestError(
            f"the level {target!r} is not offered in this project; it has "
            f"{', '.join(offered)}"
        )
    if sections is None:
        chosen = [section for _tier, section in catalogue.ordered()]
    else:
        chosen = []
        for sid in sections:
            if sid not in catalogue.sections:
                raise ManifestError(
                    f"the questionnaire has no section {sid!r}; it holds "
                    f"{', '.join(catalogue.sections)}"
                )
            chosen.append(catalogue.sections[sid])
    level = _target_level(manifest, target)
    resolver = _Resolver(manifest, level)
    return [
        question
        for section in chosen
        for entry in section.questions
        for question in resolver.questions(section.id, entry)
    ]


class _Resolver:
    def __init__(self, manifest: Manifest, level: Mapping[str, Any]) -> None:
        self.manifest = manifest
        self.level = level

    def questions(self, section: str, entry: Entry) -> list[Question]:
        if entry.id == "commands":
            return self._commands(section)
        if entry.id == "docs":
            return [
                self._question(
                    section,
                    "docs:location",
                    "paths.docs",
                    "path",
                    "Where do the architecture docs live?",
                ),
                self._question(
                    section,
                    "docs:onboarding",
                    "docs.onboarding",
                    "choice",
                    "How are the docs' gaps to be closed?",
                    choices=ONBOARDING_MODES,
                ),
            ]
        if entry.id == "store":
            return [self._store(section)]
        return [
            self._question(
                section,
                entry.id,
                entry.key,
                entry.kind,
                entry.prompt,
                choices=self._choices(entry),
                placeholders=entry.placeholders,
            )
        ]

    def _commands(self, section: str) -> list[Question]:
        found = []
        for eco in self.manifest.declared_ecosystems:
            for name in COMMAND_NAMES:
                key = self.manifest.command_key(name, eco)
                found.append(
                    self._question(
                        section,
                        f"commands:{key.removeprefix('commands.')}",
                        key,
                        "command",
                        f"The {eco} {name} command",
                        placeholders=COMMAND_PLACEHOLDERS.get(name, ()),
                    )
                )
        return found

    def _store(self, section: str) -> Question:
        store = self.manifest.store
        return Question(
            id="store:location",
            section=section,
            key=None,
            kind="path",
            prompt="Where is the store?",
            choices=None,
            placeholders=(),
            default=None if store is None else str(store.root),
            provenance="default" if store is None else store.label,
            current=None,
            set_at_target=False,
            allowed=(),
        )

    def _question(
        self,
        section: str,
        qid: str,
        key: str,
        kind: str,
        prompt: str,
        *,
        choices: Sequence[Any] | None = None,
        placeholders: Sequence[str] = (),
    ) -> Question:
        current = _dig(self.level, key)
        return Question(
            id=qid,
            section=section,
            key=key,
            kind=kind,
            prompt=prompt,
            choices=None if choices is None else tuple(choices),
            placeholders=tuple(placeholders),
            default=self.manifest.get(key),
            provenance=self.manifest.source_of(key),
            current=None if current is _UNSET else current,
            set_at_target=current is not _UNSET,
            allowed=allowed_levels(key, store_project=self.manifest.store is not None),
        )

    def _choices(self, entry: Entry) -> Sequence[Any] | None:
        source = entry.choices_from
        if source is None:
            return entry.choices
        if source == "profiles":
            return profile_names()
        if source == "pipeline_machines":
            return machines.pipeline_ids(self.manifest)
        if source == "ecosystems":
            return adapters.available()
        if source == "library_agents":
            return sorted(p.stem for p in (DATA_DIR / "agents").glob("*.md"))
        if source == "privacy_patterns":
            return tuple(PATTERNS)
        return RECORD_LOCATIONS[str(entry.key).split(".", 1)[1]]


# -- the non-interactive report (D9) -------------------------------------------


def core_report(catalogue: Catalogue, manifest: Manifest) -> list[Question]:
    """Each core question's resolved value and provenance, as a non-interactive
    `init` lists them: every keyed question of the core sections, with each
    ecosystem's commands narrowed to ``REPORTED_COMMANDS``."""
    rows = []
    for question in resolve(catalogue, manifest, sections=catalogue.core):
        if question.key is None:
            continue
        name = question.key.rsplit(".", 1)[1]
        if question.kind == "command" and name not in REPORTED_COMMANDS:
            continue
        rows.append(question)
    return rows


def render_report(rows: Sequence[Question]) -> str:
    """The text `init` prints after its report (D9)."""
    width = max((len(str(q.key)) for q in rows), default=0)
    values = [shown(q.default) for q in rows]
    value_width = max((len(v) for v in values), default=0)
    lines = ["configuration (core questions):"]
    lines += [
        f"  {q.key:<{width}}  {value:<{value_width}}  {q.provenance}"
        for q, value in zip(rows, values, strict=True)
    ]
    lines.append("run 'invariant configure' to change them")
    return "\n".join(lines)


def shown(value: Any) -> str:
    """``value`` as the report prints it: YAML's spelling, without quotes."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(shown(v) for v in value) + "]"
    return str(value)
