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
In a sphinx-needs docs project it renders the rules as needs too (ADR-018
D12, ``render_rule_needs``): one need of the ``rule`` role's type per rule,
its id the rule id as a need id (``rule_need_id``), and the need ids in its
``links`` and ``amended[].adr`` as ``decided_by``. Two rule ids that would
give one need id are refused at load.

And it renders the managed block an instruction file carries (A2 increment
4): one block, ``guidelines/index``, holding the rule index and the
``always`` rules, capped (ruling DN-C). The block sits between a begin and an
end marker, the begin marker holds the digest of the body, and everything
outside the pair is the project's. Reading the pairs back, and refusing a
malformed one by line, is here too; deciding whether to write is not.

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
from dataclasses import dataclass, field, replace
from datetime import date
from difflib import get_close_matches
from pathlib import Path
from typing import Any

import yaml

from . import needs as needs_reader
from .errors import InvariantError
from .manifest import glob_matches, project_relative

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
#: The scopes only an adapter's mask can answer; ``any`` reads the text whole.
MASKED_SCOPES = ("code", "comments", "strings")
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
    path: Path,
    *,
    registered: Collection[str],
    root: Path | None = None,
    engines: Collection[str] | None = None,
) -> Ruleset:
    """Read and validate the ruleset at ``path``, or refuse it whole.

    ``root`` is the project tree the ruleset was read from, which a registry
    path is resolved against. ``engines`` is as :func:`parse` takes it.
    """
    try:
        data = Path(path).read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RulesetError([f"the ruleset could not be read: {exc}"]) from exc
    return parse(text, registered=registered, root=root, engines=engines)


def parse(
    text: str,
    *,
    registered: Collection[str],
    root: Path | None = None,
    engines: Collection[str] | None = None,
    read: Callable[[str], bytes | None] | None = None,
) -> Ruleset:
    """Validate ruleset ``text`` against schema version 1.

    ``registered`` is every rule id the registry carries, which is what an
    ``engine`` check must name, and what no guideline id may be (DN-15). ``root`` is the project tree a registry path
    is read from. With none, a ruleset naming a registry is refused, because
    a registry nobody read cannot be said to validate (DN-A9).

    ``engines`` is every guideline engine the project's configured adapters
    offer (``Adapter.guideline_engines``). A pattern rule reading ``code``,
    ``comments`` or ``strings`` needs ``mask``, and is refused without it
    (ADR-012 D4): evaluated over raw text instead, it would read more than it
    says. So do a cochange ``when.in`` and a judgement's ``no_added_match.in``
    (A3.4, A4.2). A ``boundary`` rule needs ``imports`` (DN-A14), and is refused
    without it: no adapter could answer for any file it names. ``None`` asks
    the schema question alone; ``guidelines.ruleset_valid`` always passes
    what the configured adapters offer.

    ``read`` answers a registry's bytes by its project-relative path, or
    ``None`` when it cannot, and is asked instead of ``root``. It is how a
    ruleset is read as a revision held it rather than as the tree holds it
    (``guidelines.baseline_only_shrinks`` reads the base branch's).
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RulesetError([f"the ruleset is not valid YAML: {exc}"]) from exc
    if not isinstance(raw, Mapping):
        raise RulesetError(["the ruleset must be a mapping with version, sections and rules"])
    reader = _Reader(registered, root, engines, read)
    ruleset = reader.ruleset(raw, digest_of(text.encode("utf-8")))
    if reader.problems:
        raise RulesetError(reader.problems)
    return ruleset


class _Reader:
    """One validation pass, collecting every problem it meets."""

    def __init__(
        self,
        registered: Collection[str],
        root: Path | None,
        engines: Collection[str] | None = None,
        read: Callable[[str], bytes | None] | None = None,
    ) -> None:
        self.registered = frozenset(registered)
        self.root = root
        self.read = read
        self.engines = None if engines is None else frozenset(engines)
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
        #: Each need id stem met so far and the rule id that gave it (D12).
        stems: dict[str, str] = {}
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
            stem = needs_reader.id_stem(rule_id)
            twin = stems.setdefault(stem, rule_id)
            if twin != rule_id:
                # ADR-018 D12: each rule is rendered as the need RULE_<stem>, and
                # two needs with one id fail the docs project's build.
                self.problem(
                    f"{where} and rule {twin!r} collide as one need id, "
                    f"{rule_need_id(rule_id)}: a rule id upper-cased, with every "
                    f"character that is not a letter or a digit as _, names one "
                    f"rule need (ADR-018 D12). Rename one of them"
                )
                continue
            if rule_id in self.registered:
                # DN-15: `invariant override <id>` names a registered rule or a
                # guideline, and must never be able to mean both.
                self.problem(
                    f"{where} is the id of a registered rule; a guideline id is never "
                    f"one, so `invariant override <id>` names one thing. Rename the "
                    f"guideline, or point at the rule with check kind engine"
                )
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
        check = self.check(where, raw["check"], applies_to, str(severity))
        exceptions = self.exceptions(where, raw.get("exceptions", []))
        if check.kind == "cochange" and any(e.fingerprint for e in exceptions):
            # DN-4: a cochange finding describes the change, not the tree, so
            # it has no fingerprint, and such an exception could never apply.
            self.problem(
                f"{where}: an exception names a fingerprint, and a cochange finding "
                f"has no fingerprint; set the trigger file aside by path alone"
            )
        return Guideline(
            id=str(raw["id"]),
            section=str(section),
            title=self.text(where, raw, "title"),
            statement=self.text(where, raw, "statement"),
            severity=str(severity),
            phases=self.phases(where, raw["phases"]),
            applies_to=applies_to,
            check=check,
            exceptions=exceptions,
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
        if scope in MASKED_SCOPES and self.engines is not None and "mask" not in self.engines:
            self.problem(
                f"{where}: in {scope!r} needs a lexical mask, and no configured "
                f"ecosystem's adapter offers one; read in: any, or declare an "
                f"ecosystem whose adapter masks these files"
            )
        match = self.regex(where, "match", raw["match"])
        return Check(kind="pattern", engine=engine, scope=scope, match=match)

    # -- boundary (DN-A1 to DN-A3, DN-A14) ---------------------------------------

    def boundary(self, where: str, raw: Mapping, applies_to: tuple[str, ...]) -> Check:
        engine = self.engine(where, raw["engine"], BOUNDARY_ENGINES, "boundary")
        if engine in BOUNDARY_ENGINES and self.engines is not None and engine not in self.engines:
            self.problem(
                f"{where}: engine {engine!r} needs an adapter that reads the import "
                f"graph, and no configured ecosystem's adapter offers one; declare "
                f"an ecosystem whose adapter does (python), or write the rule as "
                f"judgement"
            )
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
        scope = self.lexical_scope(where, "when.in", raw["in"])
        if scope in MASKED_SCOPES and self.engines is not None and "mask" not in self.engines:
            # A3.1 deferred this to the increment that evaluates the trigger:
            # an added line is matched through the mask its in names.
            self.problem(
                f"{where}: when.in {scope!r} needs a lexical mask, and no configured "
                f"ecosystem's adapter offers one; read in: any, or declare an "
                f"ecosystem whose adapter masks these files"
            )
        return Trigger(
            change=str(change),
            scope=scope,
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
        data = self.registry_bytes(where, rel)
        if data is None:
            return None
        try:
            raw = yaml.safe_load(data.decode("utf-8"))
        except UnicodeDecodeError as exc:
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

    def registry_bytes(self, where: str, rel: str) -> bytes | None:
        if self.read is not None:
            data = self.read(rel)
            if data is None:
                self.problem(f"{where}: registry {rel} could not be read")
            return data
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
            return path.read_bytes()
        except OSError as exc:
            self.problem(f"registry {rel} could not be read: {exc}")
            return None

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
        scope = self.lexical_scope(at, "no_added_match.in", test["in"])
        if scope in MASKED_SCOPES and self.engines is not None and "mask" not in self.engines:
            # A3.1 deferred this to A4.2, which evaluates the condition: an
            # added line is matched through the mask its in names.
            self.problem(
                f"{at}: no_added_match.in {scope!r} needs a lexical mask, and no "
                f"configured ecosystem's adapter offers one; read in: any, or declare "
                f"an ecosystem whose adapter masks these files"
            )
        return NaCondition(
            id=condition_id,
            mechanical="no_added_match",
            scope=scope,
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
                self.problem(
                    f"{at}: adr must be a non-empty string naming an ADR file or a "
                    f"decision need id"
                )
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


def render_checklist(ruleset: Ruleset | None, fmt: str, *, source: str | None) -> str:
    """The human checklist for ``ruleset`` in ``fmt``, marker line first.

    ``source`` is the ruleset's project-relative path, named in the header so
    a reader knows where to make a change. The marker's digest is the digest
    of everything after the marker line, which is what makes a hand edit
    detectable by :func:`split_managed` alone. With no ruleset, the page
    says how to get one: the docs starter writes it so that its toctree
    names a page that exists before any ruleset does (ADR-018 D12).
    """
    _format(fmt)
    return _managed(_MarkdownWriter() if fmt == "markdown" else _RstWriter(), ruleset, source)


#: The rules page's file name, in the docs project's source directory (ADR-018 D12).
RULES_PAGE = "rules.rst"
#: The ``rule`` role's default type, as ``core/needs`` declares it.
DEFAULT_RULE_DIRECTIVE = "rule"
DEFAULT_RULE_PREFIX = "RULE_"


def rule_need_id(rule_id: str, prefix: str = DEFAULT_RULE_PREFIX) -> str:
    """The need id a rule is rendered as: ``prefix`` and the rule id upper-cased,
    every character that is not a letter or a digit as ``_`` (ADR-018 D12)."""
    return prefix + needs_reader.id_stem(rule_id)


def render_rule_needs(
    ruleset: Ruleset | None,
    *,
    source: str | None,
    directive: str = DEFAULT_RULE_DIRECTIVE,
    prefix: str = DEFAULT_RULE_PREFIX,
) -> str:
    """The rules as needs (ADR-018 D12), rST, managed marker first.

    One ``directive`` need per rule, under its section's title, with id
    :func:`rule_need_id`. The need ids among the rule's ``links`` and its
    amendments' ``adr`` are its ``decided_by`` links, so a ``rule → decision``
    link rule can be met; a path link stays text. The need's content is the
    checklist's text of the rule. With no ruleset, a page with no rules.
    """
    return _managed(_RuleNeedsWriter(directive, prefix), ruleset, source)


def _managed(writer: _Writer, ruleset: Ruleset | None, source: str | None) -> str:
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

    def render(self, ruleset: Ruleset | None, source: str | None) -> str:
        lines = self.heading(self.title, 1)
        if ruleset is None or source is None:
            lines.append(
                f"No guidelines ruleset is configured, so there are no rules yet. "
                f"Set {self.code('guidelines.ruleset')} to the project's ruleset, "
                f"then run {self.code('invariant guidelines render')}: it writes "
                f"this page from the ruleset."
            )
            return "\n".join(lines).rstrip("\n") + "\n"
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


class _RuleNeedsWriter(_RstWriter):
    """The checklist's rST, each rule a need rather than a subsection."""

    title = "Guideline rules"

    def __init__(self, directive: str, prefix: str) -> None:
        self.directive = directive
        self.prefix = prefix

    def rule(self, rule: Guideline) -> list[str]:
        decided = list(
            dict.fromkeys(
                [link for link in rule.links if needs_reader.is_need_id(link)]
                + [
                    a.adr for a in rule.amended
                    if a.adr is not None and needs_reader.is_need_id(a.adr)
                ]
            )
        )
        shown = replace(
            rule, links=tuple(link for link in rule.links if not needs_reader.is_need_id(link))
        )
        # The checklist's text of the rule, without its subsection title.
        content = super().rule(shown)[3:]
        first = next(i for i, line in enumerate(content) if line.startswith("- "))
        content.insert(first, f"- Rule: {self.code(rule.id)}")
        lines = [
            f".. {self.directive}:: {' '.join(rule.title.split())}",
            f"   :id: {rule_need_id(rule.id, self.prefix)}",
        ]
        if decided:
            lines.append(f"   :decided_by: {', '.join(decided)}")
        lines.append("")
        lines += [f"   {line}" if line else "" for line in content]
        return lines




# -- the managed instruction-file block (ADR-012 D3, rulings 8 and DN-C) ---------------

#: The one managed block (DN-C): the rule index plus the ``always`` rules.
INDEX_BLOCK = "guidelines/index"
#: Every block render knows how to write. A block of another name is left
#: alone: it is not render's to rewrite.
BLOCKS = (INDEX_BLOCK,)

_BEGIN_PREFIX = "<!-- invariant:begin"
_END_PREFIX = "<!-- invariant:end"
_BEGIN = re.compile(r"<!-- invariant:begin (\S+) digest=([0-9a-f]{12}) -->")
_END = re.compile(r"<!-- invariant:end (\S+) -->")
_SHOW = "invariant guidelines show"


class BlockError(InvariantError):
    """A malformed begin/end pair. It names the line, and render writes
    nothing to a file that holds one (D3)."""

    def __init__(self, line: int, message: str) -> None:
        self.line = line
        super().__init__(f"line {line}: {message}")


@dataclass(frozen=True)
class Block:
    """One managed block found in a file. ``begin`` and ``end`` are the
    zero-based indices of its marker lines; ``body`` is the text between."""

    name: str
    digest: str
    begin: int
    end: int
    body: str


def render_index_block(ruleset: Ruleset, *, source: str, max_lines: int) -> str:
    """The body of the ``guidelines/index`` block, at most ``max_lines`` lines.

    A header line naming the ruleset, then one line per rule: the ``always``
    rules first, each with its statement, then every other rule as its id and
    title (ruling 8). Past the cap the last line says how many rules were left
    out and where to read them (DN-C), so the cap never hides that it cut.
    """
    if max_lines < 2:
        raise ValueError(
            "the block needs at least 2 lines: its header and the line that "
            "counts what the cap left out"
        )
    code = _MarkdownWriter().code
    header = (
        f"Coding guidelines from {code(source)} (ruleset {ruleset.digest}), "
        f"generated by {code('invariant guidelines render')}: change the "
        f"ruleset, then render."
    )
    always = [r for r in ruleset.rules if "always" in r.phases]
    rest = [r for r in ruleset.rules if "always" not in r.phases]
    lines = [
        f"- {code(r.id)} (always): {r.title.rstrip('.')}. "
        + " ".join(_paragraphs(r.statement))
        for r in always
    ]
    lines += [f"- {code(r.id)}: {r.title}" for r in rest]
    if 1 + len(lines) > max_lines:
        kept = max_lines - 2
        lines = [*lines[:kept], f"- {len(lines) - kept} more: {code(_SHOW)}"]
    return "\n".join([header, *lines]) + "\n"


def block_text(name: str, body: str) -> str:
    """A block with its markers: the begin marker holds the body's digest."""
    return (
        f"{_BEGIN_PREFIX} {name} digest={digest_of(body.encode('utf-8'))} -->\n"
        f"{body}"
        f"{_END_PREFIX} {name} -->\n"
    )


def find_blocks(text: str) -> dict[str, Block]:
    """Every well-formed block in ``text``, by name.

    A marker is a whole line, give or take surrounding spaces, so a marker
    quoted inside a sentence is prose. Raises :class:`BlockError` naming the
    line of the first malformed pair: a begin with no end, an end with no
    begin, a nested or crossed pair, a second block of one name, or a line
    that opens like a marker and is not one.
    """
    lines = text.splitlines(keepends=True)
    found: dict[str, Block] = {}
    open_name: str | None = None
    open_at = 0
    digest = ""
    for index, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith(_BEGIN_PREFIX):
            match = _BEGIN.fullmatch(line)
            if match is None:
                raise BlockError(index + 1, "a malformed begin marker")
            name = match.group(1)
            if open_name is not None:
                raise BlockError(
                    index + 1,
                    f"the begin marker of {name} is nested inside {open_name}, "
                    f"opened on line {open_at + 1}",
                )
            if name in found:
                raise BlockError(
                    index + 1,
                    f"a second {name} block; the first begins on line "
                    f"{found[name].begin + 1}",
                )
            open_name, open_at, digest = name, index, match.group(2)
        elif line.startswith(_END_PREFIX):
            match = _END.fullmatch(line)
            if match is None:
                raise BlockError(index + 1, "a malformed end marker")
            name = match.group(1)
            if open_name is None:
                raise BlockError(index + 1, f"an end marker of {name} with no begin")
            if name != open_name:
                raise BlockError(
                    index + 1,
                    f"the end marker of {name} does not close {open_name}, "
                    f"opened on line {open_at + 1}",
                )
            body = "".join(lines[open_at + 1 : index])
            found[name] = Block(name, digest, open_at, index, body)
            open_name = None
    if open_name is not None:
        raise BlockError(open_at + 1, f"the begin marker of {open_name} has no end")
    return found


def block_state(block: Block, body: str) -> str:
    """``current``, ``stale`` or ``edited``: the three-way rule of
    ``classify_installed``, with the begin marker's digest as the witness.

    A body that no longer matches its own digest was changed after render
    wrote it, which is the operator's work. One that matches its digest but
    not what would be rendered now came from an older ruleset.
    """
    if block.digest != digest_of(block.body.encode("utf-8")):
        return "edited"
    return "current" if block.body == body else "stale"


def replace_block(text: str, block: Block, body: str) -> str:
    """``text`` with ``block`` rewritten to ``body``; every other line kept."""
    lines = text.splitlines(keepends=True)
    return (
        "".join(lines[: block.begin])
        + block_text(block.name, body)
        + "".join(lines[block.end + 1 :])
    )


def append_block(text: str, name: str, body: str) -> str:
    """``text`` with a new block at the end, after one blank line (``--insert``)."""
    if not text:
        return block_text(name, body)
    return text.rstrip("\n") + "\n\n" + block_text(name, body)


# -- selection by phase and file (ADR-012 D7, the A5 rulings D1 to D3) --------------

#: The rule phases a state role selects. A design state sees the build-time
#: subset (design and always); a review state sees review, landing and
#: always (ruling D2); a landing or abandon state sees nothing, since no
#: agent works in it.
ROLE_PHASES: dict[str, tuple[str, ...]] = {
    "design": ("design", "always"),
    "review": ("review", "landing", "always"),
    "landing": (),
    "abandon": (),
}

#: What a build state sees (ruling D1): a state carrying none of the roles
#: above, such as tdd's red, green and refactor or chore's doing.
BUILD_PHASES: tuple[str, ...] = ("design", "always")


def phases_for(roles: Iterable[str]) -> tuple[str, ...]:
    """The rule phases a state with ``roles`` sees, in :data:`PHASES` order.

    Decided from the machine's roles alone, with no history (ruling D1). A
    state with several roles sees the union. A role this mapping does not
    know carries no phases of its own, so a state with only such roles is a
    build state: the direction that shows more rules, never fewer.
    """
    known = [role for role in roles if role in ROLE_PHASES]
    if not known:
        return BUILD_PHASES
    seen = {phase for role in known for phase in ROLE_PHASES[role]}
    return tuple(phase for phase in PHASES if phase in seen)


def matches(rel: str, glob: str) -> bool:
    """Does ``rel`` fall under a glob as the ruleset wrote it?

    The glob is put in its canonical spelling first, and one that names
    nothing inside the project matches nothing.
    """
    canonical = project_relative(glob)
    if canonical in (None, "."):
        return False
    return glob_matches(rel, canonical)


def path_exception(rule: Guideline, rel: str) -> GuidelineException | None:
    """The path exception that sets the whole of ``rel`` aside, if any.

    Only an exception with no fingerprint does: one naming a fingerprint
    excuses one finding, never a file.
    """
    return next(
        (exc for exc in rule.exceptions if exc.fingerprint is None and matches(rel, exc.path)),
        None,
    )


def meets(rule: Guideline, rel: str) -> bool:
    """Does ``rel`` bring the rule into play (A4's DN-3)?

    ``rel`` is under the rule's ``applies_to`` and not set aside by a path
    exception. The one reading: the findings, the verdicts owed and the
    selection all ask it.
    """
    if not any(matches(rel, glob) for glob in rule.applies_to):
        return False
    return path_exception(rule, rel) is None


def select(
    ruleset: Ruleset, roles: Iterable[str], paths: Collection[str]
) -> tuple[Guideline, ...]:
    """The rules a state with ``roles`` sees over ``paths``, in declared order.

    Pure. A rule is selected when one of its phases is among
    :func:`phases_for`'s and, when ``paths`` is not empty, at least one of
    the project-relative ``paths`` meets it. Empty ``paths`` is no file
    filter: every rule of the phases.
    """
    phases = set(phases_for(roles))
    return tuple(
        rule
        for rule in ruleset.rules
        if phases.intersection(rule.phases)
        and (not paths or any(meets(rule, rel) for rel in paths))
    )


def render_selected(
    ruleset: Ruleset,
    rules: Iterable[Guideline],
    *,
    source: str,
    phases: Iterable[str],
    files: Collection[str],
) -> str:
    """The full text of the selected ``rules``, in markdown.

    A header line naming the ruleset, the phases and the file scope, then
    each rule as the checklist writes it, so the two texts cannot drift.
    """
    writer = _MarkdownWriter()
    rules = tuple(rules)
    phases = tuple(phases)
    scope = (
        f"over {len(files)} {'file' if len(files) == 1 else 'files'}"
        if files else "with no file filter"
    )
    header = (
        f"Guidelines for {', '.join(phases) or 'no phases'} from "
        f"{writer.code(source)} (ruleset {ruleset.digest}): {len(rules)} of "
        f"{len(ruleset.rules)} rules, {scope}."
    )
    lines = [header, ""]
    for rule in rules:
        lines += writer.rule(rule)
    if not rules:
        lines.append("No rule is selected.")
    return "\n".join(lines).rstrip("\n") + "\n"


# -- the turn index (ADR-012 ruling 8, D7, the A5 rulings D3, D10 and D12) ----------


@dataclass(frozen=True)
class TurnIndex:
    """What the turn projection says about the guidelines, as plain data.

    ``core/rules.turn_guidelines`` decides it and :func:`render_turn_index`
    writes it. ``rules`` holds the always rules first, then the state's
    other rules over the file scope, each in declared order. ``show`` is the
    command that prints their full text. ``pin`` is the pipeline's pinned
    digest when it differs from ``digest``, the tree's (DN-6). ``problem``
    stands in for everything else: one line saying why there is no index.
    """

    source: str = ""
    digest: str = ""
    total: int = 0
    phases: tuple[str, ...] = ()
    rules: tuple[Guideline, ...] = ()
    files: tuple[str, ...] = ()
    #: The changed set could not be established and nothing was declared, so
    #: the empty scope is the whole role index rather than an answer.
    scope_unknown: bool = False
    show: str = _SHOW
    pin: str | None = None
    problem: str | None = None


def render_turn_index(index: TurnIndex, max_lines: int) -> list[str]:
    """The turn index as plain lines, at most ``max_lines`` of them.

    A header naming the phases, the ruleset and the file scope, then the
    pin line when the tree's ruleset is not the pinned one, then one line
    per rule: its id and title, an always rule marked as the managed block
    marks it. Never a statement and never a verdict standing (D10). Past the
    cap the last line counts the rules left out and names ``index.show``.
    A problem is that one line alone. Nothing selected and nothing to say
    is silence.
    """
    if index.problem is not None:
        return [f"guidelines: {index.problem}"]
    items = []
    if index.pin is not None:
        items.append(
            f"- the ruleset in the tree ({index.digest}) is not the one this "
            f"pipeline pinned ({index.pin}): a verdict is refused until they agree"
        )
    pinned = len(items)
    items += [
        f"- {rule.id} (always): {rule.title}" if "always" in rule.phases
        else f"- {rule.id}: {rule.title}"
        for rule in index.rules
    ]
    if not items:
        return []
    count = len(index.files)
    if count:
        scope = f"over {count} {'file' if count == 1 else 'files'}"
    elif index.scope_unknown:
        scope = "with no file filter, since the changed files could not be established"
    else:
        scope = "with no file filter"
    header = (
        f"guidelines for {', '.join(index.phases)} from {index.source} "
        f"(ruleset {index.digest}): {len(index.rules)} of {index.total} rules, {scope}"
    )
    if 1 + len(items) <= max_lines:
        return [header, *items]
    kept = items[: max(max_lines - 2, 0)]
    left = len(index.rules) - max(len(kept) - pinned, 0)
    return [header, *kept, f"- {left} more: {index.show}"]


# -- what an edit carries (ADR-012 ruling 9, D7, A3 increment 8, the A5 rulings D4 to D8) --


@dataclass(frozen=True)
class AdvisedFinding:
    """One finding the landing gate would count as new, stated as plain facts."""

    rule: str
    severity: str
    path: str
    line: int | None
    #: The matched line, its whitespace collapsed and its length capped.
    text: str
    target: str | None
    fix: str


@dataclass(frozen=True)
class EditAdvisory:
    """What one landed edit adds that ``guidelines.mechanical_clean`` would hold.

    ``core/rules.edit_advisory`` decides it and :func:`render_advisory`
    writes it.
    """

    path: str
    findings: tuple[AdvisedFinding, ...] = ()
    #: One short line: the file, the ruleset or the baseline could not be read.
    problem: str | None = None


@dataclass(frozen=True)
class EditGuidelines:
    """The judgement rules an edit carries the statements of, as plain data.

    ``core/rules.edit_guidelines`` decides it (the A5 ruling D4) and
    :func:`render_edit` writes it after the findings. ``show`` is the command
    that prints the rules in full for this file. ``problem`` stands in for
    the rules: one line saying why there are none to show.
    """

    path: str
    phases: tuple[str, ...] = ()
    rules: tuple[Guideline, ...] = ()
    show: str = _SHOW
    problem: str | None = None


#: The label every line an edit carries starts from.
ADVISORY_LABEL = "guidelines advisory: "

#: Where the findings past the cap are read in full.
ADVISORY_MORE = "invariant check guidelines.mechanical_clean"


def render_advisory(advisory: EditAdvisory, max_lines: int) -> list[str]:
    """The findings an edit adds, alone, at most ``max_lines`` lines.

    A header naming the file and the gate, then the problem line if there is
    one, then one line per finding: rule id, severity, path:line, the matched
    line and the fix. A problem alone is that one line. Past the cap the last
    line counts what was left out and names where to read it, as the managed
    block does (DN-C), so N counts lines and items alike. It is
    :func:`render_edit` with no statements.
    """
    return render_edit(advisory, None, max_lines)


def render_edit(
    advisory: EditAdvisory | None, guidance: EditGuidelines | None, max_lines: int
) -> list[str]:
    """Everything one edit carries, at most ``max_lines`` lines (the A5 ruling D6).

    The findings first, as :func:`render_advisory` writes them, then the
    statements: a header naming the file and the phases, and one line per
    judgement rule, its id, severity and statement on one line. One shared
    cap. Past it the last line counts the items left out and names the
    command that reads each half in full; a header left with nothing under
    it is dropped, unless it is the first line. A statements problem that
    says what the findings' problem already says is not said twice.
    Nothing to say is silence.
    """
    parts = _advisory_parts(advisory) if advisory is not None else []
    if guidance is not None:
        repeated = (
            guidance.problem is not None
            and advisory is not None
            and guidance.problem == advisory.problem
        )
        parts += [] if repeated else _guidance_parts(guidance)
    if not any(source for _, source in parts):
        return []
    if len(parts) <= max_lines:
        return [line for line, _ in parts]
    kept = parts[: max(max_lines - 1, 1)]
    while len(kept) > 1 and kept[-1][1] is None:
        kept.pop()
    cut = [source for _, source in parts[len(kept):] if source]
    commands = [
        ADVISORY_MORE if source == "findings" else guidance.show
        for source in dict.fromkeys(cut)
    ]
    return [*(line for line, _ in kept), f"- {len(cut)} more: {'; '.join(commands)}"]


def _advisory_parts(advisory: EditAdvisory) -> list[tuple[str, str | None]]:
    """The findings half as (line, source) pairs; a header's source is ``None``."""
    if not advisory.findings:
        return [(ADVISORY_LABEL + advisory.problem, "findings")] if advisory.problem else []
    header = (
        f"{ADVISORY_LABEL}{len(advisory.findings)} new finding(s) in {advisory.path}, "
        f"which guidelines.mechanical_clean evaluates at landing"
    )
    parts: list[tuple[str, str | None]] = [(header, None)]
    if advisory.problem:
        parts.append((f"- {advisory.problem}", "findings"))
    for found in advisory.findings:
        where = found.path if found.line is None else f"{found.path}:{found.line}"
        target = f" (references {found.target})" if found.target else ""
        line = (
            f"- {found.rule} ({found.severity}) {where}: {found.text}{target}; "
            f"fix: {found.fix}"
        )
        parts.append((line, "findings"))
    return parts


def _guidance_parts(guidance: EditGuidelines) -> list[tuple[str, str | None]]:
    """The statements half as (line, source) pairs; a header's source is ``None``."""
    if guidance.problem is not None:
        return [(ADVISORY_LABEL + guidance.problem, "statements")]
    if not guidance.rules:
        return []
    header = (
        f"{ADVISORY_LABEL}{len(guidance.rules)} judgement rule(s) for {guidance.path} "
        f"(phases {', '.join(guidance.phases)})"
    )
    return [(header, None)] + [
        (f"- {rule.id} ({rule.severity}): {' '.join(rule.statement.split())}", "statements")
        for rule in guidance.rules
    ]


# -- the baseline (D6) ------------------------------------------------------------
#
# The ratchet's record of pre-existing findings, one entry per line:
#
#     rule-id  path  fingerprint  count
#
# The first field is the rule id, which holds no whitespace (increment 1). The
# last two are a fingerprint (twelve lowercase hex digits, ``findings.
# fingerprint``) and a count of at least 1. Everything between them is the
# path, so a path with spaces still parses. A line is a comment only when its
# first non-blank character is ``#``, so a path holding ``#`` is a path.
#
# Read in full or not at all, for the reason the coverage baseline gives: a
# record read in part stops enforcing and stops honouring with nothing to say
# so. Every problem is collected and named by line, and none yields a smaller
# record. Which rules an entry may name needs the ruleset, so that check is
# its own function; parsing the lines needs nothing but the text.

#: The kinds a baseline entry may name. A cochange finding describes the
#: change, not the tree, and has no fingerprint (DN-4); an engine rule is its
#: registered rule's and a judgement rule is answered by a verdict, so neither
#: has a finding of its own to record.
BASELINE_KINDS = ("pattern", "boundary")

#: The one comment the writer puts first. It carries no managed marker: the
#: baseline is the ratchet's record, not a rendering of the ruleset.
BASELINE_HEADER = (
    "# rule-id  path  fingerprint  count  "
    "(written by invariant guidelines baseline; ADR-012 D6)"
)

#: A count is a positive integer in its plain spelling: no sign, no leading
#: zero, no decimal point.
_COUNT = re.compile(r"[1-9][0-9]*")
#: The rule id, then the path, then the last two fields.
_ENTRY = re.compile(r"(\S+)\s+(.*\S)\s+(\S+)\s+(\S+)")


class BaselineError(InvariantError):
    """A baseline that does not parse, or cannot be written. Every problem is
    named, by line."""

    def __init__(self, problems: Iterable[str]) -> None:
        self.problems = tuple(problems)
        super().__init__(
            f"the baseline has {len(self.problems)} problem(s): " + "; ".join(self.problems)
        )


@dataclass(frozen=True)
class BaselineEntry:
    """One recorded (rule, path, fingerprint) and how many findings it holds."""

    rule: str
    path: str
    fingerprint: str
    count: int
    #: The line it was read from, for naming a problem; 0 for one not read.
    line: int = field(default=0, compare=False)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.rule, self.path, self.fingerprint)


def parse_baseline(text: str) -> tuple[BaselineEntry, ...]:
    """Every entry of a baseline, or :class:`BaselineError` naming every problem.

    A path is kept in its canonical project-relative spelling, so ``./x`` and
    ``x`` are one path, and recording both is a duplicate (the BUG_071 rule
    the coverage baseline follows). A path that names nothing inside the
    project is refused rather than dropped.
    """
    entries: list[BaselineEntry] = []
    problems: list[str] = []
    seen: dict[tuple[str, str, str], int] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        where = f"line {number} {stripped!r}"
        matched = _ENTRY.fullmatch(stripped)
        if matched is None:
            problems.append(
                f"{where}: fewer than four fields; an entry is "
                f"'rule-id  path  fingerprint  count'"
            )
            continue
        rule, written, stamp, count = matched.groups()
        if not _FINGERPRINT.fullmatch(stamp):
            problems.append(
                f"{where}: {stamp!r} is not a fingerprint (twelve lowercase hex digits)"
            )
            continue
        if not _COUNT.fullmatch(count):
            problems.append(f"{where}: {count!r} is not a positive integer count")
            continue
        path = project_relative(written)
        if path in (None, "."):
            problems.append(f"{where}: the path {written!r} is outside the project")
            continue
        key = (rule, path, stamp)
        if key in seen:
            problems.append(f"{where}: already recorded on line {seen[key]}")
            continue
        seen[key] = number
        entries.append(BaselineEntry(rule, path, stamp, int(count), line=number))
    if problems:
        raise BaselineError(problems)
    return tuple(entries)


def baseline_rule_problems(
    entries: Iterable[BaselineEntry], ruleset: Ruleset
) -> list[str]:
    """What is wrong with the rules the entries name, against the ruleset.

    A rule the ruleset lacks, and a rule whose kind has no fingerprinted
    finding, can protect nothing, and an entry naming one would read as a
    record that holds when it does not.
    """
    problems: list[str] = []
    for entry in entries:
        where = f"line {entry.line} ({entry.rule} {entry.path})"
        rule = ruleset.by_id(entry.rule)
        if rule is None:
            problems.append(f"{where}: the ruleset has no rule {entry.rule!r}")
        elif rule.check.kind not in BASELINE_KINDS:
            reason = (
                "a cochange finding has no fingerprint, so no baseline can hold it (DN-4)"
                if rule.check.kind == "cochange"
                else f"a {rule.check.kind} rule has no finding of its own to record"
            )
            problems.append(
                f"{where}: {entry.rule} is a {rule.check.kind} rule, and only "
                f"{' and '.join(BASELINE_KINDS)} rules are baselined; {reason}"
            )
    return problems


def definition_of(rule: Guideline) -> tuple[Any, ...]:
    """What a rule's findings depend on, for comparing two copies of it (DN-6).

    Its ``applies_to``, its ``check`` and its ``exceptions``, read from the
    validated model rather than from the text, so a respelling of the YAML
    (flow or block style, quoting, key order, ``./`` before a glob) compares
    equal, and so does a reordered list whose order means nothing: globs,
    exceptions, a boundary's targets, a pair's ``then`` globs and a registry
    rule's outcomes and fixes. The wording, severity and phases are not part
    of it: they change what a finding is called, not which findings exist.
    Nor is an exception's ``reason``: an exception is its path and its
    fingerprint, and rewording why it applies sets nothing else aside.
    """
    check = replace(
        rule.check,
        forbid=tuple(sorted(rule.check.forbid)),
        only=tuple(sorted(rule.check.only)),
        outcomes=tuple(sorted(rule.check.outcomes)),
        fixes=tuple(sorted(rule.check.fixes)),
        then=(
            replace(rule.check.then, globs=tuple(sorted(rule.check.then.globs)))
            if rule.check.then is not None
            else None
        ),
    )
    exceptions = sorted({(_spelled(e.path), e.fingerprint or "") for e in rule.exceptions})
    return (tuple(sorted({_spelled(g) for g in rule.applies_to})), check, tuple(exceptions))


def _spelled(declared: str) -> str:
    """A declared path or glob in the one spelling it is matched in."""
    return project_relative(declared) or declared


def render_baseline(entries: Iterable[BaselineEntry]) -> str:
    """The baseline's text: one header comment, then the entries sorted by
    path, rule and fingerprint, so a diff of two recordings shows what moved.

    A path the format cannot hold, one with a line break or with whitespace at
    either end, is :class:`BaselineError`: written, it would read back as
    another path or not at all.
    """
    ordered = sorted(entries, key=lambda e: (e.path, e.rule, e.fingerprint))
    problems = [
        f"the path {e.path!r} cannot be written to a baseline line"
        for e in ordered
        if e.path != e.path.strip() or "\n" in e.path or "\r" in e.path
    ]
    if problems:
        raise BaselineError(problems)
    lines = [BASELINE_HEADER]
    lines.extend(f"{e.rule}  {e.path}  {e.fingerprint}  {e.count}" for e in ordered)
    return "\n".join(lines) + "\n"
