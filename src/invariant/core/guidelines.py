"""The guidelines ruleset: its model, its schema and its validation.

A project declares its coding guidelines in one YAML ruleset (ADR-012 D3,
rulings 1 and 2), by default at ``docs/guidelines/ruleset.yaml``. This module
reads one and refuses it whole, naming every problem, when it does not
validate. ``core/rules`` asks it through ``guidelines.ruleset_valid``; later
increments add what is rendered from it, what is checked against it and the
verdicts recorded about it.

Refused whole, never read in part. A rule dropped because one of its keys was
misspelled stops being enforced with nothing to say so, which is the
grandfathered record the coverage baseline already refuses to read. So every
problem is collected and the ruleset either loads with all of them absent or
does not load at all.

The schema is version 1, and every one of its five check kinds is validated
(D4, and the check-kind amendment's rulings DN-A1 to DN-A14):

- ``engine``, a pointer at a registered rule, so a guideline that restates a
  gate the engine already runs has no second implementation;
- ``pattern`` with the ``regex`` engine;
- ``boundary`` with the ``imports`` engine, as a ``forbid`` deny-list or an
  allow-only ``only`` list, exactly one of the two;
- ``cochange``, in an explicit ``pair`` or ``registry`` form. A registry is a
  file of invariant's own format, and it is read whole or refused as part of
  the ruleset (DN-A9), so it is resolved against the project tree the caller
  hands in;
- ``judgement``, with its evidence, its citation scope and its declared N/A
  conditions.

``syntax`` and ``symbols`` are deferred engines (ruling 3): a rule needing one
is written as ``judgement`` until it ships. Nothing here evaluates a check:
A3 evaluates the mechanical kinds and A4 the verdicts.

It also renders the human checklist (A2 increment 3): text in the project's
``docs.format`` whose first line is a managed marker holding the digest of
everything after it, so a hand edit is detectable without re-rendering
(ruling DN-B). Rendering is pure; writing the file is ``domains/guidelines``'.

This module is pure apart from reading the ruleset and the registries it
names. It takes the registered rule ids as a parameter rather than importing
``core/rules``, which imports it.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import string
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from difflib import get_close_matches
from pathlib import Path
from typing import Any

import yaml

from .errors import InvariantError
from .manifest import project_relative

RULESET_VERSION = 1

#: Every check kind schema version 1 names (D4), and the ones this build can
#: validate. A kind outside the second set is refused, never waved through.
KINDS = ("engine", "pattern", "boundary", "cochange", "judgement")
VALIDATED_KINDS = frozenset(KINDS)

#: The state roles a rule can belong to (D7).
PHASES = ("design", "review", "landing", "always")
SEVERITIES = ("blocker", "warn")

#: What a pattern's regex runs over, once the adapter's lexical mask applies (D4).
PATTERN_SCOPES = ("code", "comments", "strings", "any")
PATTERN_ENGINES = ("regex",)
#: Engines v1 refuses everywhere (ruling 3).
DEFERRED_ENGINES = ("syntax", "symbols")
#: A boundary is the import graph in v1 (ruling 3, DN-A14), stated as a
#: deny-list or an allow-only list, exactly one of the two (DN-A3).
BOUNDARY_ENGINES = ("imports",)
BOUNDARY_LISTS = ("forbid", "only")

#: A cochange names its form (DN-A4), what about a file triggers it (DN-A6)
#: and exactly one shape of what must change with it (DN-A5).
COCHANGE_FORMS = ("pair", "registry")
CHANGES = ("any", "added", "executable")
THEN_SHAPES = ("all_of", "any_of", "each")
TEMPLATE_TOKENS = ("path", "dir", "name", "stem", "suffix")

#: The registry file's own schema (DN-A7). Only an open entry produces a
#: finding (DN-A8).
REGISTRY_VERSION = 1
REGISTRY_STATUSES = ("open", "resolved")

#: What a judgement verdict must rest on (DN-A10), where its citations may lie
#: (DN-A11), and the N/A conditions the engine answers itself (DN-A12).
EVIDENCE = ("citation", "test", "either", "both")
VERDICT_SCOPES = ("diff", "file")
MECHANICAL_CONDITIONS = ("no_added_match", "no_executable_change")

#: The length every persisted hash is written in (BUG_090), and the shape of an
#: exception's optional line fingerprint (D6).
DIGEST_LENGTH = 12
_FINGERPRINT = re.compile(r"[0-9a-f]{12}")

#: The generated checklist's suffix for each ``docs.format`` it can be written
#: in (ruling DN-B). A null ``guidelines.checklist`` is ``checklist`` plus this
#: suffix, beside the ruleset.
CHECKLIST_SUFFIXES = {"markdown": ".md", "sphinx": ".rst"}

#: The managed marker on a generated file's first line, per format (DN-B). It
#: holds the digest of every byte after that line.
_MARKERS = {
    "markdown": re.compile(r"<!-- invariant-managed: ([0-9a-f]{12}) -->"),
    "sphinx": re.compile(r"\.\. invariant-managed: ([0-9a-f]{12})"),
}
_NO_WHITESPACE = re.compile(r"\S+")

_TOP_KEYS = frozenset({"version", "sections", "rules"})
_SECTION_KEYS = frozenset({"id", "title"})
_RULE_REQUIRED = (
    "id", "section", "title", "statement", "severity", "phases", "applies_to", "check",
)
_RULE_OPTIONAL = frozenset({"exceptions", "rationale", "links", "amended"})
_EXCEPTION_REQUIRED = ("path", "reason")
_EXCEPTION_OPTIONAL = frozenset({"fingerprint"})
_AMENDED_REQUIRED = ("on", "note")
_AMENDED_OPTIONAL = frozenset({"adr"})
#: Per kind, its required keys and its optional ones. A cochange adds the
#: keys of its form once the form is known (DN-A4).
_CHECK_KEYS: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {
    "engine": (("kind", "rule"), frozenset()),
    "pattern": (("kind", "engine", "in", "match"), frozenset()),
    "boundary": (("kind", "engine"), frozenset(BOUNDARY_LISTS)),
    "cochange": (("kind", "form"), frozenset()),
    "judgement": (("kind", "evidence", "scope"), frozenset({"not_applicable_when"})),
}
_FORM_KEYS: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {
    "pair": (("when", "then"), frozenset()),
    "registry": (("registry", "outcomes"), frozenset({"fixes"})),
}
_WHEN_KEYS = frozenset({"change", "match", "in"})
_CONDITION_KEYS = frozenset({"id", "claim", "mechanical"})
_NO_ADDED_MATCH_KEYS = ("in", "match")
_REGISTRY_TOP_KEYS = frozenset({"version", "entries"})
_ENTRY_REQUIRED = ("id", "paths", "status", "class")
_ENTRY_OPTIONAL = frozenset({"note"})
_GLOB_CHARS = frozenset("*?[")


class RulesetError(InvariantError):
    """A ruleset that cannot be read, or does not validate. Every problem is named."""

    def __init__(self, problems: Iterable[str]) -> None:
        self.problems = tuple(problems)
        super().__init__(
            f"the ruleset has {len(self.problems)} problem(s): " + "; ".join(self.problems)
        )


@dataclass(frozen=True)
class Section:
    id: str
    title: str


@dataclass(frozen=True)
class Trigger:
    """A pair cochange's ``when`` (DN-A6). ``match`` and ``scope`` (the
    ruleset's ``in``) narrow it to a file where an added line matches."""

    change: str
    scope: str | None = None
    match: str | None = None


@dataclass(frozen=True)
class Requirement:
    """A pair cochange's ``then``: exactly one shape (DN-A5). ``all_of`` and
    ``any_of`` carry globs, and ``each`` carries a path template."""

    shape: str
    globs: tuple[str, ...] = ()
    template: str | None = None


@dataclass(frozen=True)
class NaCondition:
    """A judgement rule's declared N/A condition (D5, DN-A12). Either a
    ``claim`` the agent picks by id, or a ``mechanical`` test the engine
    answers itself: ``no_added_match`` (with ``scope`` and ``match``) or
    ``no_executable_change``."""

    id: str
    claim: str | None = None
    mechanical: str | None = None
    scope: str | None = None
    match: str | None = None


@dataclass(frozen=True)
class Check:
    """How a guideline is checked. Only the fields of its kind are set."""

    kind: str
    rule: str | None = None
    engine: str | None = None
    #: The ruleset's ``in``: code, comments, strings or any.
    scope: str | None = None
    match: str | None = None
    #: A boundary's deny-list or allow-only list; exactly one is non-empty.
    forbid: tuple[str, ...] = ()
    only: tuple[str, ...] = ()
    #: A cochange's form, and the keys of that form.
    form: str | None = None
    when: Trigger | None = None
    then: Requirement | None = None
    registry: str | None = None
    #: (class, blocker | warn) and (class, fix) pairs, in declared order.
    outcomes: tuple[tuple[str, str], ...] = ()
    fixes: tuple[tuple[str, str], ...] = ()
    #: A judgement's evidence and the ruleset's ``scope`` (diff or file).
    evidence: str | None = None
    verdict_scope: str | None = None
    not_applicable_when: tuple[NaCondition, ...] = ()


@dataclass(frozen=True)
class GuidelineException:
    """A reasoned, path-scoped exception (D4). Never silent: a later
    increment names every one that applied."""

    path: str
    reason: str
    fingerprint: str | None = None


@dataclass(frozen=True)
class Amendment:
    on: str
    note: str
    adr: str | None = None


@dataclass(frozen=True)
class Guideline:
    """One rule of the project's ruleset. Not a registry rule: the registry
    stays fixed, and a guideline is answered by its family gate (D12)."""

    id: str
    section: str
    title: str
    statement: str
    severity: str
    phases: tuple[str, ...]
    applies_to: tuple[str, ...]
    check: Check
    exceptions: tuple[GuidelineException, ...] = ()
    rationale: str = ""
    links: tuple[str, ...] = ()
    amended: tuple[Amendment, ...] = ()


@dataclass(frozen=True)
class RegistryEntry:
    """One entry of a registry file (DN-A7). ``cls`` is the file's ``class``,
    which a rule's ``outcomes`` maps; ``note`` is inert."""

    id: str
    paths: tuple[str, ...]
    status: str
    cls: str
    note: str = ""


@dataclass(frozen=True)
class Registry:
    """A registry file a ``cochange`` rule names, read whole (DN-A9)."""

    path: str
    entries: tuple[RegistryEntry, ...]
    digest: str


@dataclass(frozen=True)
class Ruleset:
    version: int
    sections: tuple[Section, ...]
    rules: tuple[Guideline, ...]
    #: The content hash: what a pipeline pins and a verdict records (D3).
    digest: str
    #: Every registry a rule names, each read once, in first-named order.
    registries: tuple[Registry, ...] = ()

    def by_id(self, rule_id: str) -> Guideline | None:
        return next((r for r in self.rules if r.id == rule_id), None)


def digest_of(data: bytes) -> str:
    """The first twelve hex digits of the SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()[:DIGEST_LENGTH]


def load(
    path: Path, *, registered: Collection[str], root: Path | None = None
) -> Ruleset:
    """Read and validate the ruleset at ``path``, or refuse it whole.

    ``root`` is the project tree the ruleset was read from, which a registry
    path is resolved against.
    """
    try:
        data = Path(path).read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RulesetError([f"the ruleset could not be read: {exc}"]) from exc
    return parse(text, registered=registered, root=root)


def parse(
    text: str, *, registered: Collection[str], root: Path | None = None
) -> Ruleset:
    """Validate ruleset ``text`` against schema version 1.

    ``registered`` is every rule id the registry carries, which is what an
    ``engine`` check must name. ``root`` is the project tree a registry path
    is read from. With none, a ruleset naming a registry is refused, because
    a registry nobody read cannot be said to validate (DN-A9).
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RulesetError([f"the ruleset is not valid YAML: {exc}"]) from exc
    if not isinstance(raw, Mapping):
        raise RulesetError(["the ruleset must be a mapping with version, sections and rules"])
    reader = _Reader(registered, root)
    ruleset = reader.ruleset(raw, digest_of(text.encode("utf-8")))
    if reader.problems:
        raise RulesetError(reader.problems)
    return ruleset


class _Reader:
    """One validation pass, collecting every problem it meets."""

    def __init__(self, registered: Collection[str], root: Path | None) -> None:
        self.registered = frozenset(registered)
        self.root = root
        self.problems: list[str] = []
        #: Each registry path read so far, and what it read as. ``None`` is a
        #: registry already refused, so its problems are named once.
        self.registries: dict[str, Registry | None] = {}

    def problem(self, text: str) -> None:
        self.problems.append(text)

    # -- the envelope ---------------------------------------------------------

    def ruleset(self, raw: Mapping, digest: str) -> Ruleset:
        self.unknown("the ruleset", raw, _TOP_KEYS)
        missing = sorted(_TOP_KEYS - set(raw))
        if missing:
            self.problem(f"the ruleset is missing {', '.join(missing)}")
        version = raw.get("version")
        if "version" in raw and version != RULESET_VERSION:
            self.problem(
                f"the ruleset is version {version!r}; this build reads version {RULESET_VERSION}"
            )
        sections = self.sections(raw.get("sections", []))
        declared = {s.id for s in sections}
        rules = self.rules(raw.get("rules", []), declared)
        registries = tuple(r for r in self.registries.values() if r is not None)
        return Ruleset(RULESET_VERSION, sections, rules, digest, registries)

    def sections(self, raw: Any) -> tuple[Section, ...]:
        if not isinstance(raw, list):
            self.problem("sections must be a list")
            return ()
        found: list[Section] = []
        for position, item in enumerate(raw, start=1):
            where = f"sections[{position}]"
            if not isinstance(item, Mapping):
                self.problem(f"{where} must be a mapping")
                continue
            section_id = item.get("id")
            if not isinstance(section_id, str) or not section_id.strip():
                self.problem(f"{where}: id must be a non-empty string")
                continue
            where = f"section {section_id!r}"
            self.unknown(where, item, _SECTION_KEYS)
            if any(s.id == section_id for s in found):
                self.problem(f"{where} is declared twice")
                continue
            title = self.text(where, item, "title")
            found.append(Section(section_id, title))
        return tuple(found)

    def rules(self, raw: Any, sections: set[str]) -> tuple[Guideline, ...]:
        if not isinstance(raw, list):
            self.problem("rules must be a list")
            return ()
        found: list[Guideline] = []
        for position, item in enumerate(raw, start=1):
            where = f"rules[{position}]"
            if not isinstance(item, Mapping):
                self.problem(f"{where} must be a mapping")
                continue
            rule_id = item.get("id")
            if not isinstance(rule_id, str) or not _NO_WHITESPACE.fullmatch(rule_id):
                # Whitespace-free because a baseline line and a CLI argument
                # are both split on whitespace (D6).
                self.problem(f"{where}: id must be a non-empty string with no whitespace")
                continue
            where = f"rule {rule_id!r}"
            if any(r.id == rule_id for r in found):
                self.problem(f"{where} is declared twice; a rule id is never reused")
                continue
            rule = self.rule(where, item, sections)
            if rule is not None:
                found.append(rule)
        return tuple(found)

    # -- one rule ---------------------------------------------------------------

    def rule(self, where: str, raw: Mapping, sections: set[str]) -> Guideline | None:
        self.unknown(where, raw, frozenset(_RULE_REQUIRED) | _RULE_OPTIONAL)
        absent = [key for key in _RULE_REQUIRED if key not in raw]
        if absent:
            self.problem(f"{where}: missing {', '.join(absent)}")
            return None
        section = raw["section"]
        if section not in sections:
            self.problem(f"{where} names section {section!r}, which no section declares")
        severity = raw["severity"]
        if severity not in SEVERITIES:
            self.problem(
                f"{where}: severity {severity!r} is not one of {', '.join(SEVERITIES)}"
            )
        applies_to = self.globs(where, raw["applies_to"])
        return Guideline(
            id=str(raw["id"]),
            section=str(section),
            title=self.text(where, raw, "title"),
            statement=self.text(where, raw, "statement"),
            severity=str(severity),
            phases=self.phases(where, raw["phases"]),
            applies_to=applies_to,
            check=self.check(where, raw["check"], applies_to, str(severity)),
            exceptions=self.exceptions(where, raw.get("exceptions", [])),
            rationale=self.optional_text(where, raw, "rationale"),
            links=self.strings(where, "links", raw.get("links", [])),
            amended=self.amendments(where, raw.get("amended", [])),
        )

    def phases(self, where: str, raw: Any) -> tuple[str, ...]:
        if not isinstance(raw, list):
            self.problem(f"{where}: phases must be a list")
            return ()
        if not raw:
            self.problem(f"{where}: phases names no phase")
        for phase in raw:
            if phase not in PHASES:
                self.problem(
                    f"{where}: phase {phase!r} is not one of {', '.join(PHASES)}"
                )
        return tuple(str(p) for p in raw)

    def globs(self, where: str, raw: Any) -> tuple[str, ...]:
        # A bare string would be iterated character by character, and its `*`
        # matches every path: the AUD-08 and coverage-glob precedent.
        if isinstance(raw, str):
            self.problem(
                f"{where}: applies_to must be a list of glob strings, not a bare string"
            )
            return ()
        if not isinstance(raw, list) or not all(isinstance(g, str) and g for g in raw):
            self.problem(f"{where}: applies_to must be a list of glob strings")
            return ()
        if not raw:
            self.problem(f"{where}: applies_to names nothing, so the rule applies nowhere")
        return tuple(raw)

    def check(
        self, where: str, raw: Any, applies_to: tuple[str, ...], severity: str
    ) -> Check:
        where = f"{where} check"
        if not isinstance(raw, Mapping):
            self.problem(f"{where} must be a mapping")
            return Check(kind="")
        kind = raw.get("kind")
        if kind is None:
            self.problem(f"{where}: missing kind")
            return Check(kind="")
        if kind not in KINDS:
            self.problem(
                f"{where}: unknown check kind {kind!r}; the kinds are {', '.join(KINDS)}"
            )
            return Check(kind=str(kind))
        required, optional = _CHECK_KEYS[kind]
        form = raw.get("form")
        if kind == "cochange" and form in _FORM_KEYS:
            form_required, form_optional = _FORM_KEYS[form]
            required, optional = required + form_required, optional | form_optional
        self.unknown(where, raw, frozenset(required) | optional)
        absent = [key for key in required if key not in raw]
        if absent:
            self.problem(f"{where}: missing {', '.join(absent)}")
            return Check(kind=str(kind))
        if kind == "engine":
            return Check(kind="engine", rule=self.registered_rule(where, raw["rule"]))
        if kind == "pattern":
            return self.pattern(where, raw)
        if kind == "boundary":
            return self.boundary(where, raw, applies_to)
        if kind == "cochange":
            return self.cochange(where, raw, severity)
        return self.judgement(where, raw)

    def registered_rule(self, where: str, raw: Any) -> str:
        if not isinstance(raw, str) or not raw:
            self.problem(f"{where}: rule must be a non-empty string naming a registered rule")
            return ""
        if raw not in self.registered:
            near = get_close_matches(raw, sorted(self.registered), n=1, cutoff=0.6)
            hint = f"; did you mean {near[0]!r}?" if near else ""
            self.problem(f"{where}: rule {raw!r} names no registered rule{hint}")
        return raw

    def pattern(self, where: str, raw: Mapping) -> Check:
        engine = self.engine(where, raw["engine"], PATTERN_ENGINES, "pattern")
        scope = self.lexical_scope(where, "in", raw["in"])
        match = self.regex(where, "match", raw["match"])
        return Check(kind="pattern", engine=engine, scope=scope, match=match)

    # -- boundary (DN-A1 to DN-A3, DN-A14) ---------------------------------------

    def boundary(self, where: str, raw: Mapping, applies_to: tuple[str, ...]) -> Check:
        engine = self.engine(where, raw["engine"], BOUNDARY_ENGINES, "boundary")
        given = [key for key in BOUNDARY_LISTS if key in raw]
        if len(given) != 1:
            self.problem(
                f"{where}: give exactly one of forbid and only "
                f"(a deny-list or an allow-only list; there is no carve-out form)"
            )
            return Check(kind="boundary", engine=engine)
        (key,) = given
        targets = self.project_globs(where, key, raw[key])
        if key == "forbid":
            # The source side is applies_to (DN-A1). Forbidding one of its own
            # globs would make every file it names a finding against itself.
            own = {project_relative(g) or g for g in applies_to}
            for glob in targets:
                if glob in own:
                    self.problem(
                        f"{where}: forbid repeats applies_to glob {glob!r}, "
                        f"so every file would forbid itself"
                    )
            return Check(kind="boundary", engine=engine, forbid=targets)
        return Check(kind="boundary", engine=engine, only=targets)

    # -- cochange (DN-A4 to DN-A9) -------------------------------------------------

    def cochange(self, where: str, raw: Mapping, severity: str) -> Check:
        form = raw["form"]
        if form not in COCHANGE_FORMS:
            self.problem(
                f"{where}: form {form!r} is not one of {', '.join(COCHANGE_FORMS)}"
            )
            return Check(kind="cochange")
        if form == "pair":
            return Check(
                kind="cochange",
                form="pair",
                when=self.trigger(where, raw["when"]),
                then=self.requirement(where, raw["then"]),
            )
        return self.registry_check(where, raw, severity)

    def trigger(self, where: str, raw: Any) -> Trigger | None:
        if not isinstance(raw, Mapping):
            self.problem(f"{where}: when must be a mapping")
            return None
        self.unknown(f"{where} when", raw, _WHEN_KEYS)
        if "change" not in raw:
            self.problem(f"{where}: when: missing change")
            return None
        change = raw["change"]
        if change not in CHANGES:
            self.problem(
                f"{where}: when.change {change!r} is not one of {', '.join(CHANGES)}"
            )
        if ("match" in raw) != ("in" in raw):
            self.problem(f"{where}: when.match and when.in are given together or not at all")
            return Trigger(change=str(change))
        if "match" not in raw:
            return Trigger(change=str(change))
        return Trigger(
            change=str(change),
            scope=self.lexical_scope(where, "when.in", raw["in"]),
            match=self.regex(where, "when.match", raw["match"]),
        )

    def requirement(self, where: str, raw: Any) -> Requirement | None:
        if not isinstance(raw, Mapping):
            self.problem(f"{where}: then must be a mapping")
            return None
        self.unknown(f"{where} then", raw, frozenset(THEN_SHAPES))
        given = [shape for shape in THEN_SHAPES if shape in raw]
        if len(given) != 1:
            # D3's "a cochange whose then names nothing", and its twin.
            stated = f"gives {', '.join(given)}" if given else "names nothing"
            self.problem(
                f"{where}: then {stated}: give exactly one of {', '.join(THEN_SHAPES)}"
            )
            return None
        (shape,) = given
        if shape == "each":
            return Requirement(shape="each", template=self.template(where, raw["each"]))
        return Requirement(
            shape=shape, globs=self.project_globs(where, f"then.{shape}", raw[shape])
        )

    def template(self, where: str, raw: Any) -> str:
        if not isinstance(raw, str) or not raw.strip():
            self.problem(f"{where}: then.each must be a non-empty path template")
            return ""
        try:
            fields = [f for f in string.Formatter().parse(raw) if f[1] is not None]
        except ValueError as exc:
            self.problem(f"{where}: then.each is not a well-formed template: {exc}")
            return raw
        if not fields:
            self.problem(
                f"{where}: then.each names no token, so every trigger would owe "
                f"the same file; use all_of or any_of for a fixed path"
            )
            return raw
        known = ", ".join("{" + t + "}" for t in TEMPLATE_TOKENS)
        sound = True
        for _, name, spec, conversion in fields:
            if name not in TEMPLATE_TOKENS or spec or conversion:
                written = name + (f"!{conversion}" if conversion else "") + (
                    f":{spec}" if spec else ""
                )
                self.problem(f"{where}: then.each token {{{written}}} is not one of {known}")
                sound = False
        if sound:
            derived = raw.format(**{token: "x" for token in TEMPLATE_TOKENS})
            if posixpath.isabs(derived) or project_relative(derived) in (None, "."):
                self.problem(
                    f"{where}: then.each {raw!r} does not derive a project-relative path"
                )
        return raw

    def registry_check(self, where: str, raw: Mapping, severity: str) -> Check:
        rel = self.registry_path(where, raw["registry"])
        outcomes = self.outcomes(where, raw["outcomes"], severity)
        fixes = self.fixes(where, raw.get("fixes", {}), {cls for cls, _ in outcomes})
        registry = self.registry(where, rel) if rel else None
        if registry is not None and outcomes:
            mapped = {cls for cls, _ in outcomes}
            for entry in registry.entries:
                if entry.status == "open" and entry.cls not in mapped:
                    # An open entry no outcome maps would read as passing.
                    self.problem(
                        f"{where}: registry {rel} open entry {entry.id} has class "
                        f"{entry.cls!r}, which outcomes does not map"
                    )
        return Check(
            kind="cochange",
            form="registry",
            registry=rel,
            outcomes=outcomes,
            fixes=fixes,
        )

    def registry_path(self, where: str, raw: Any) -> str | None:
        rel = project_relative(raw) if isinstance(raw, str) else None
        if rel in (None, "."):
            self.problem(
                f"{where}: registry must be a project-relative path inside the "
                f"project, such as docs/guidelines/duplication.yaml"
            )
            return None
        return rel

    def outcomes(self, where: str, raw: Any, severity: str) -> tuple[tuple[str, str], ...]:
        if not isinstance(raw, Mapping):
            self.problem(f"{where}: outcomes must be a mapping of class to blocker or warn")
            return ()
        if not raw:
            self.problem(f"{where}: outcomes names no class")
        found: list[tuple[str, str]] = []
        for cls, outcome in raw.items():
            if outcome not in SEVERITIES:
                self.problem(
                    f"{where}: outcome {outcome!r} for class {cls!r} is not one of "
                    f"{', '.join(SEVERITIES)}"
                )
                continue
            if outcome == "blocker" and severity == "warn":
                # The rule's severity is the ceiling, so one value still gives
                # a rule's weight.
                self.problem(
                    f"{where}: outcome 'blocker' for class {cls!r} exceeds the "
                    f"rule's severity warn"
                )
            found.append((str(cls), str(outcome)))
        return tuple(found)

    def fixes(self, where: str, raw: Any, mapped: set[str]) -> tuple[tuple[str, str], ...]:
        if not isinstance(raw, Mapping):
            self.problem(f"{where}: fixes must be a mapping of class to a fix")
            return ()
        found: list[tuple[str, str]] = []
        for cls, fix in raw.items():
            if str(cls) not in mapped:
                self.problem(
                    f"{where}: fixes names class {cls!r}, which outcomes does not map"
                )
            if not isinstance(fix, str) or not fix.strip():
                self.problem(f"{where}: fix for class {cls!r} must be a non-empty string")
                continue
            found.append((str(cls), " ".join(fix.split())))
        return tuple(found)

    # -- a registry file (DN-A7, DN-A9) --------------------------------------------

    def registry(self, where: str, rel: str) -> Registry | None:
        if rel not in self.registries:
            self.registries[rel] = self.read_registry(where, rel)
        return self.registries[rel]

    def read_registry(self, where: str, rel: str) -> Registry | None:
        if self.root is None:
            self.problem(f"{where}: registry {rel} cannot be read: no project tree was given")
            return None
        path = Path(self.root) / rel
        if not path.is_file():
            # A missing registry is not an empty one (DN-A9), as a missing
            # ruleset is not.
            self.problem(f"{where}: registry {rel} does not exist")
            return None
        try:
            data = path.read_bytes()
            raw = yaml.safe_load(data.decode("utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            self.problem(f"registry {rel} could not be read: {exc}")
            return None
        except yaml.YAMLError as exc:
            self.problem(f"registry {rel} is not valid YAML: {exc}")
            return None
        before = len(self.problems)
        entries = self.registry_document(rel, raw)
        if len(self.problems) > before:
            return None
        return Registry(path=rel, entries=entries, digest=digest_of(data))

    def registry_document(self, rel: str, raw: Any) -> tuple[RegistryEntry, ...]:
        where = f"registry {rel}"
        if not isinstance(raw, Mapping):
            self.problem(f"{where} must be a mapping with version and entries")
            return ()
        self.unknown(where, raw, _REGISTRY_TOP_KEYS)
        missing = sorted(_REGISTRY_TOP_KEYS - set(raw))
        if missing:
            self.problem(f"{where} is missing {', '.join(missing)}")
        if "version" in raw and raw["version"] != REGISTRY_VERSION:
            self.problem(
                f"{where} is version {raw['version']!r}; "
                f"this build reads version {REGISTRY_VERSION}"
            )
        entries = raw.get("entries", [])
        if not isinstance(entries, list):
            self.problem(f"{where}: entries must be a list")
            return ()
        found: list[RegistryEntry] = []
        for position, item in enumerate(entries, start=1):
            entry = self.registry_entry(where, position, item, found)
            if entry is not None:
                found.append(entry)
        return tuple(found)

    def registry_entry(
        self, where: str, position: int, item: Any, found: list[RegistryEntry]
    ) -> RegistryEntry | None:
        if not isinstance(item, Mapping):
            self.problem(f"{where} entries[{position}] must be a mapping")
            return None
        entry_id = item.get("id")
        if not isinstance(entry_id, str) or not _NO_WHITESPACE.fullmatch(entry_id):
            self.problem(
                f"{where} entries[{position}]: id must be a non-empty string with no whitespace"
            )
            return None
        at = f"{where} entry {entry_id}"
        if any(e.id == entry_id for e in found):
            self.problem(f"{at} is declared twice")
            return None
        self.unknown(at, item, frozenset(_ENTRY_REQUIRED) | _ENTRY_OPTIONAL)
        absent = [key for key in _ENTRY_REQUIRED if key not in item]
        if absent:
            self.problem(f"{at}: missing {', '.join(absent)}")
            return None
        status = item["status"]
        if status not in REGISTRY_STATUSES:
            self.problem(
                f"{at}: status {status!r} is not one of {', '.join(REGISTRY_STATUSES)}"
            )
        return RegistryEntry(
            id=entry_id,
            paths=self.entry_paths(at, item["paths"]),
            status=str(status),
            cls=self.text(at, item, "class"),
            note=self.optional_text(at, item, "note"),
        )

    def entry_paths(self, at: str, raw: Any) -> tuple[str, ...]:
        if not isinstance(raw, list) or not all(isinstance(p, str) and p for p in raw):
            self.problem(f"{at}: paths must be a list of project-relative paths")
            return ()
        if not raw:
            self.problem(f"{at}: paths names nothing")
        found: list[str] = []
        for path in raw:
            if _GLOB_CHARS & set(path):
                self.problem(f"{at}: path {path!r} is a glob; a registry names files")
                continue
            rel = project_relative(path)
            if rel in (None, "."):
                self.problem(f"{at}: path {path!r} is not a project-relative path")
                continue
            found.append(rel)
        return tuple(found)

    # -- judgement (DN-A10 to DN-A13) ------------------------------------------------

    def judgement(self, where: str, raw: Mapping) -> Check:
        evidence = raw["evidence"]
        if evidence not in EVIDENCE:
            self.problem(
                f"{where}: evidence {evidence!r} is not one of {', '.join(EVIDENCE)}"
            )
        scope = raw["scope"]
        if scope not in VERDICT_SCOPES:
            self.problem(
                f"{where}: scope {scope!r} is not one of {', '.join(VERDICT_SCOPES)}"
            )
        return Check(
            kind="judgement",
            evidence=str(evidence),
            verdict_scope=str(scope),
            not_applicable_when=self.conditions(where, raw.get("not_applicable_when", [])),
        )

    def conditions(self, where: str, raw: Any) -> tuple[NaCondition, ...]:
        if not isinstance(raw, list):
            self.problem(f"{where}: not_applicable_when must be a list")
            return ()
        found: list[NaCondition] = []
        for position, item in enumerate(raw, start=1):
            if not isinstance(item, Mapping):
                self.problem(f"{where}: not_applicable_when[{position}] must be a mapping")
                continue
            condition_id = item.get("id")
            if not isinstance(condition_id, str) or not _NO_WHITESPACE.fullmatch(condition_id):
                # One argument to --na-condition (D5), so no whitespace.
                self.problem(
                    f"{where}: not_applicable_when[{position}]: id must be a non-empty "
                    f"string with no whitespace"
                )
                continue
            if any(c.id == condition_id for c in found):
                self.problem(f"{where}: condition {condition_id!r} is declared twice")
                continue
            condition = self.condition(f"{where} condition {condition_id!r}", item)
            if condition is not None:
                found.append(condition)
        return tuple(found)

    def condition(self, at: str, item: Mapping) -> NaCondition | None:
        self.unknown(at, item, _CONDITION_KEYS)
        condition_id = str(item["id"])
        has_claim, has_mechanical = "claim" in item, "mechanical" in item
        if has_claim == has_mechanical:
            which = "both claim and mechanical" if has_claim else "neither claim nor mechanical"
            self.problem(f"{at} gives {which}; a condition is exactly one of them")
            return None
        if has_claim:
            return NaCondition(id=condition_id, claim=self.text(at, item, "claim"))
        mechanical = item["mechanical"]
        if not isinstance(mechanical, Mapping):
            self.problem(f"{at}: mechanical must be a mapping")
            return None
        self.unknown(f"{at} mechanical", mechanical, frozenset(MECHANICAL_CONDITIONS))
        given = [name for name in MECHANICAL_CONDITIONS if name in mechanical]
        if len(given) != 1:
            stated = f"gives {', '.join(given)}" if given else "names no condition"
            self.problem(
                f"{at}: mechanical {stated}: give exactly one of "
                f"{', '.join(MECHANICAL_CONDITIONS)}"
            )
            return None
        if given == ["no_executable_change"]:
            if mechanical["no_executable_change"] is not True:
                self.problem(f"{at}: no_executable_change must be true")
            return NaCondition(id=condition_id, mechanical="no_executable_change")
        test = mechanical["no_added_match"]
        if not isinstance(test, Mapping):
            self.problem(f"{at}: no_added_match must be a mapping with in and match")
            return None
        self.unknown(f"{at} no_added_match", test, frozenset(_NO_ADDED_MATCH_KEYS))
        absent = [key for key in _NO_ADDED_MATCH_KEYS if key not in test]
        if absent:
            self.problem(f"{at}: no_added_match: missing {', '.join(absent)}")
            return None
        return NaCondition(
            id=condition_id,
            mechanical="no_added_match",
            scope=self.lexical_scope(at, "no_added_match.in", test["in"]),
            match=self.regex(at, "no_added_match.match", test["match"]),
        )

    # -- values shared by the kinds ----------------------------------------------------

    def engine(self, where: str, raw: Any, allowed: tuple[str, ...], family: str) -> str:
        if raw in DEFERRED_ENGINES:
            self.problem(
                f"{where}: engine {raw!r} is deferred in v1 (ADR-012 ruling 3); "
                f"write the rule as judgement until it ships"
            )
        elif raw not in allowed:
            self.problem(
                f"{where}: engine {raw!r} is not a {family} engine; "
                f"the {family} engines are {', '.join(allowed)}"
            )
        return str(raw)

    def lexical_scope(self, where: str, key: str, raw: Any) -> str:
        if raw not in PATTERN_SCOPES:
            self.problem(f"{where}: {key} {raw!r} is not one of {', '.join(PATTERN_SCOPES)}")
        return str(raw)

    def regex(self, where: str, key: str, raw: Any) -> str:
        if not isinstance(raw, str) or not raw:
            self.problem(f"{where}: {key} must be a non-empty string")
        else:
            try:
                re.compile(raw)
            except re.error as exc:
                self.problem(f"{where}: {key} does not compile: {exc}")
        return str(raw)

    def project_globs(self, where: str, key: str, raw: Any) -> tuple[str, ...]:
        """Project-relative path globs (DN-A2), each normalised as every other
        declared glob is. A bare string is refused, the AUD-08 precedent."""
        if isinstance(raw, str):
            self.problem(f"{where}: {key} must be a list of glob strings, not a bare string")
            return ()
        if not isinstance(raw, list) or not all(isinstance(g, str) and g for g in raw):
            self.problem(f"{where}: {key} must be a list of glob strings")
            return ()
        if not raw:
            self.problem(f"{where}: {key} names nothing")
        found: list[str] = []
        for glob in raw:
            rel = project_relative(glob)
            if rel in (None, "."):
                self.problem(
                    f"{where}: {key} glob {glob!r} is not a project-relative path glob"
                )
                continue
            found.append(rel)
        return tuple(found)

    def exceptions(self, where: str, raw: Any) -> tuple[GuidelineException, ...]:
        if not isinstance(raw, list):
            self.problem(f"{where}: exceptions must be a list")
            return ()
        found: list[GuidelineException] = []
        for position, item in enumerate(raw, start=1):
            at = f"{where} exceptions[{position}]"
            if not isinstance(item, Mapping):
                self.problem(f"{at} must be a mapping")
                continue
            self.unknown(at, item, frozenset(_EXCEPTION_REQUIRED) | _EXCEPTION_OPTIONAL)
            absent = [key for key in _EXCEPTION_REQUIRED if key not in item]
            if absent:
                self.problem(f"{at}: missing {', '.join(absent)}")
                continue
            fingerprint = item.get("fingerprint")
            if fingerprint is not None and not (
                isinstance(fingerprint, str) and _FINGERPRINT.fullmatch(fingerprint)
            ):
                self.problem(f"{at}: fingerprint must be twelve lowercase hex digits")
            found.append(
                GuidelineException(
                    path=self.text(at, item, "path"),
                    reason=self.text(at, item, "reason"),
                    fingerprint=None if fingerprint is None else str(fingerprint),
                )
            )
        return tuple(found)

    def amendments(self, where: str, raw: Any) -> tuple[Amendment, ...]:
        if not isinstance(raw, list):
            self.problem(f"{where}: amended must be a list")
            return ()
        found: list[Amendment] = []
        for position, item in enumerate(raw, start=1):
            at = f"{where} amended[{position}]"
            if not isinstance(item, Mapping):
                self.problem(f"{at} must be a mapping")
                continue
            # PyYAML reads YAML 1.1, where a bare `on` key is the boolean
            # true. The schema's key is `on` as D3 writes it, so the boolean
            # is read back as the word it was written as.
            item = {("on" if key is True else key): value for key, value in item.items()}
            self.unknown(at, item, frozenset(_AMENDED_REQUIRED) | _AMENDED_OPTIONAL)
            absent = [key for key in _AMENDED_REQUIRED if key not in item]
            if absent:
                self.problem(f"{at}: missing {', '.join(absent)}")
                continue
            on = item["on"]
            if isinstance(on, date):
                on = on.isoformat()
            elif not isinstance(on, str) or not on.strip():
                self.problem(f"{at}: on must be a date")
                on = ""
            adr = item.get("adr")
            if adr is not None and not (isinstance(adr, str) and adr.strip()):
                self.problem(f"{at}: adr must be a non-empty string naming an ADR file")
                adr = None
            found.append(Amendment(on=on, note=self.text(at, item, "note"), adr=adr))
        return tuple(found)

    # -- primitives -------------------------------------------------------------

    def unknown(self, where: str, raw: Mapping, allowed: frozenset[str]) -> None:
        extra = sorted(str(k) for k in set(raw) - allowed)
        if extra:
            self.problem(f"unknown key(s) in {where}: {', '.join(extra)}")

    def text(self, where: str, raw: Mapping, key: str) -> str:
        if key not in raw:
            self.problem(f"{where}: missing {key}")
            return ""
        value = raw[key]
        if not isinstance(value, str) or not value.strip():
            self.problem(f"{where}: {key} must be a non-empty string")
            return ""
        return value.strip()

    def optional_text(self, where: str, raw: Mapping, key: str) -> str:
        value = raw.get(key, "")
        if not isinstance(value, str):
            self.problem(f"{where}: {key} must be a string")
            return ""
        return value.strip()

    def strings(self, where: str, key: str, raw: Any) -> tuple[str, ...]:
        if not isinstance(raw, list) or not all(isinstance(v, str) and v for v in raw):
            self.problem(f"{where}: {key} must be a list of strings")
            return ()
        return tuple(raw)


# -- the generated checklist (ADR-012 D3, ruling DN-B) ---------------------------


def render_checklist(ruleset: Ruleset, fmt: str, *, source: str) -> str:
    """The human checklist for ``ruleset`` in ``fmt``, marker line first.

    ``source`` is the ruleset's project-relative path, named in the header so
    a reader knows where to make a change. The marker's digest is the digest
    of everything after the marker line, which is what makes a hand edit
    detectable by :func:`split_managed` alone.
    """
    _format(fmt)
    writer = _MarkdownWriter() if fmt == "markdown" else _RstWriter()
    body = "\n" + writer.render(ruleset, source)
    return writer.marker(digest_of(body.encode("utf-8"))) + "\n" + body


def split_managed(text: str, fmt: str) -> tuple[str | None, str]:
    """The digest a managed file's marker holds, and the body after it.

    ``(None, text)`` when the first line is not this format's marker: the file
    is not one render wrote, so its digest is unknown and the whole text is
    the project's.
    """
    first, newline, body = text.partition("\n")
    found = _MARKERS[_format(fmt)].fullmatch(first)
    if found is None or not newline:
        return None, text
    return found.group(1), body


def _format(fmt: str) -> str:
    if fmt not in CHECKLIST_SUFFIXES:
        raise ValueError(
            f"no checklist renderer for docs.format {fmt!r}; "
            f"the formats are {', '.join(CHECKLIST_SUFFIXES)}"
        )
    return fmt


def _paragraphs(text: str) -> list[str]:
    """Paragraphs kept, each one's internal line breaks folded to spaces."""
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()]


def _check_summary(check: Check, code) -> str:
    def codes(values: Iterable[str]) -> str:
        return ", ".join(code(v) for v in values)

    if check.kind == "engine":
        return f"engine: the gate {code(check.rule or '')}"
    if check.kind == "pattern":
        return f"pattern ({check.engine}, in {check.scope}): {code(check.match or '')}"
    if check.kind == "boundary":
        if check.forbid:
            return f"boundary ({check.engine}): may not reference {codes(check.forbid)}"
        return f"boundary ({check.engine}): may reference only {codes(check.only)}"
    if check.kind == "cochange" and check.form == "registry":
        outcomes = ", ".join(f"{cls}: {outcome}" for cls, outcome in check.outcomes)
        fixes = "".join(f"; fix for {cls}: {fix}" for cls, fix in check.fixes)
        return (
            f"cochange: a file in an open entry of {code(check.registry or '')} "
            f"({outcomes}){fixes}"
        )
    if check.kind == "cochange":
        return _pair_summary(check, code, codes)
    conditions = ", ".join(c.id for c in check.not_applicable_when)
    na = f"; N/A when: {conditions}" if conditions else ""
    return (
        f"judgement: a cited verdict, evidence {check.evidence}, "
        f"scope {check.verdict_scope}{na}"
    )


def _pair_summary(check: Check, code, codes) -> str:
    when, then = check.when, check.then
    assert when is not None and then is not None  # a parsed pair carries both
    trigger = f"when a file changes ({when.change})"
    if when.match:
        trigger += f" and an added line in {when.scope} matches {code(when.match)}"
    if then.shape == "each":
        owed = f"each of {code(then.template or '')}, derived per file,"
    else:
        owed = f"{then.shape.replace('_', ' ')} {codes(then.globs)}"
    return f"cochange: {trigger}, {owed} must change too"


class _Writer:
    """What the two formats share: the order and the fields of a rule."""

    title = "Coding guidelines"
    #: Each format supplies ``marker``, ``heading`` and ``code``.
    marker: Callable[[str], str]
    heading: Callable[[str, int], list[str]]
    code: Callable[[str], str]

    def render(self, ruleset: Ruleset, source: str) -> str:
        lines = self.heading(self.title, 1)
        lines.append(
            f"Generated by {self.code('invariant guidelines render')} from "
            f"{self.code(source)} (ruleset {ruleset.digest}). Do not edit this "
            f"file: change the ruleset, then render."
        )
        lines.append("")
        for section in ruleset.sections:
            lines += self.heading(section.title, 2)
            rules = [r for r in ruleset.rules if r.section == section.id]
            if not rules:
                lines += ["No rules in this section.", ""]
            for rule in rules:
                lines += self.rule(rule)
        return "\n".join(lines).rstrip("\n") + "\n"

    def rule(self, rule: Guideline) -> list[str]:
        lines = self.heading(f"{rule.id}: {rule.title}", 3)
        for paragraph in _paragraphs(rule.statement):
            lines += [paragraph, ""]
        lines += [
            f"- Severity: {rule.severity}",
            f"- Phases: {', '.join(rule.phases)}",
            f"- Applies to: {', '.join(self.code(g) for g in rule.applies_to)}",
            f"- Check: {_check_summary(rule.check, self.code)}",
        ]
        if rule.exceptions:
            lines.append("- Exceptions:")
            lines.append("")
            for exc in rule.exceptions:
                line = f" (fingerprint {exc.fingerprint})" if exc.fingerprint else ""
                lines.append(f"  - {self.code(exc.path)}{line}: {' '.join(exc.reason.split())}")
            lines.append("")
        if rule.rationale:
            lines.append(f"- Rationale: {' '.join(rule.rationale.split())}")
        if rule.links:
            lines.append(f"- Links: {', '.join(self.code(link) for link in rule.links)}")
        if rule.amended:
            lines.append("- Amended:")
            lines.append("")
            for amendment in rule.amended:
                adr = f" ({self.code(amendment.adr)})" if amendment.adr else ""
                lines.append(f"  - {amendment.on}: {' '.join(amendment.note.split())}{adr}")
        while lines[-1] == "":
            lines.pop()  # a nested list that ended the rule left its blank line
        return [*lines, ""]


class _MarkdownWriter(_Writer):
    def marker(self, digest: str) -> str:
        return f"<!-- invariant-managed: {digest} -->"

    def heading(self, text: str, level: int) -> list[str]:
        return [f"{'#' * level} {text}", ""]

    def code(self, text: str) -> str:
        # A span holding a backtick is opened with two, padded so a backtick
        # at either end still reads as content (CommonMark's own rule).
        return f"`` {text} ``" if "`" in text else f"`{text}`"


#: The rst underline character per heading level.
_RST_UNDERLINES = ("=", "-", "~")


class _RstWriter(_Writer):
    def marker(self, digest: str) -> str:
        return f".. invariant-managed: {digest}"

    def heading(self, text: str, level: int) -> list[str]:
        return [text, _RST_UNDERLINES[level - 1] * len(text), ""]

    def code(self, text: str) -> str:
        return f"``{text}``"

