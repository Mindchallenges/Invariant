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

The schema is version 1. The checks it can validate in this build are
``engine`` (a pointer at a registered rule, so a guideline that restates a gate
the engine already runs has no second implementation) and ``pattern`` with the
``regex`` engine. ``boundary``, ``cochange`` and ``judgement`` are v1 kinds
whose schemas land in a later increment; until then a rule of one of those
kinds is refused with that reason, rather than accepted unchecked. ``syntax``
and ``symbols`` are deferred engines (ruling 3): a rule needing one is written
as ``judgement`` until it ships.

It also renders the human checklist (A2 increment 3): text in the project's
``docs.format`` whose first line is a managed marker holding the digest of
everything after it, so a hand edit is detectable without re-rendering
(ruling DN-B). Rendering is pure; writing the file is ``domains/guidelines``'.

This module is pure apart from reading the file. It takes the registered rule
ids as a parameter rather than importing ``core/rules``, which imports it.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from difflib import get_close_matches
from pathlib import Path
from typing import Any

import yaml

from .errors import InvariantError

RULESET_VERSION = 1

#: Every check kind schema version 1 names (D4), and the ones this build can
#: validate. A kind outside the second set is refused, never waved through.
KINDS = ("engine", "pattern", "boundary", "cochange", "judgement")
VALIDATED_KINDS = frozenset({"engine", "pattern"})

#: The state roles a rule can belong to (D7).
PHASES = ("design", "review", "landing", "always")
SEVERITIES = ("blocker", "warn")

#: What a pattern's regex runs over, once the adapter's lexical mask applies (D4).
PATTERN_SCOPES = ("code", "comments", "strings", "any")
PATTERN_ENGINES = ("regex",)
#: Engines v1 refuses everywhere (ruling 3).
DEFERRED_ENGINES = ("syntax", "symbols")

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
_CHECK_KEYS: dict[str, tuple[str, ...]] = {
    "engine": ("kind", "rule"),
    "pattern": ("kind", "engine", "in", "match"),
}


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
class Check:
    """How a guideline is checked. Only the fields of its kind are set."""

    kind: str
    rule: str | None = None
    engine: str | None = None
    #: The ruleset's ``in``: code, comments, strings or any.
    scope: str | None = None
    match: str | None = None


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
class Ruleset:
    version: int
    sections: tuple[Section, ...]
    rules: tuple[Guideline, ...]
    #: The content hash: what a pipeline pins and a verdict records (D3).
    digest: str

    def by_id(self, rule_id: str) -> Guideline | None:
        return next((r for r in self.rules if r.id == rule_id), None)


def digest_of(data: bytes) -> str:
    """The first twelve hex digits of the SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()[:DIGEST_LENGTH]


def load(path: Path, *, registered: Collection[str]) -> Ruleset:
    """Read and validate the ruleset at ``path``, or refuse it whole."""
    try:
        data = Path(path).read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RulesetError([f"the ruleset could not be read: {exc}"]) from exc
    return parse(text, registered=registered)


def parse(text: str, *, registered: Collection[str]) -> Ruleset:
    """Validate ruleset ``text`` against schema version 1.

    ``registered`` is every rule id the registry carries, which is what an
    ``engine`` check must name.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RulesetError([f"the ruleset is not valid YAML: {exc}"]) from exc
    if not isinstance(raw, Mapping):
        raise RulesetError(["the ruleset must be a mapping with version, sections and rules"])
    reader = _Reader(registered)
    ruleset = reader.ruleset(raw, digest_of(text.encode("utf-8")))
    if reader.problems:
        raise RulesetError(reader.problems)
    return ruleset


class _Reader:
    """One validation pass, collecting every problem it meets."""

    def __init__(self, registered: Collection[str]) -> None:
        self.registered = frozenset(registered)
        self.problems: list[str] = []

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
        return Ruleset(RULESET_VERSION, sections, rules, digest)

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
        return Guideline(
            id=str(raw["id"]),
            section=str(section),
            title=self.text(where, raw, "title"),
            statement=self.text(where, raw, "statement"),
            severity=str(severity),
            phases=self.phases(where, raw["phases"]),
            applies_to=self.globs(where, raw["applies_to"]),
            check=self.check(where, raw["check"]),
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

    def check(self, where: str, raw: Any) -> Check:
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
        if kind not in VALIDATED_KINDS:
            self.problem(
                f"{where}: kind {kind!r} is a v1 kind, but this build does not "
                f"validate its schema yet, and a rule nothing has checked is not "
                f"accepted (IDEA_031 A2 increment 2)"
            )
            return Check(kind=str(kind))
        keys = _CHECK_KEYS[kind]
        self.unknown(where, raw, frozenset(keys))
        absent = [key for key in keys if key not in raw]
        if absent:
            self.problem(f"{where}: missing {', '.join(absent)}")
            return Check(kind=str(kind))
        if kind == "engine":
            return Check(kind="engine", rule=self.registered_rule(where, raw["rule"]))
        return self.pattern(where, raw)

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
        engine = raw["engine"]
        if engine in DEFERRED_ENGINES:
            self.problem(
                f"{where}: engine {engine!r} is deferred in v1 (ADR-012 ruling 3); "
                f"write the rule as judgement until it ships"
            )
        elif engine not in PATTERN_ENGINES:
            self.problem(
                f"{where}: engine {engine!r} is not a pattern engine; "
                f"the pattern engines are {', '.join(PATTERN_ENGINES)}"
            )
        scope = raw["in"]
        if scope not in PATTERN_SCOPES:
            self.problem(
                f"{where}: in {scope!r} is not one of {', '.join(PATTERN_SCOPES)}"
            )
        match = raw["match"]
        if not isinstance(match, str) or not match:
            self.problem(f"{where}: match must be a non-empty string")
        else:
            try:
                re.compile(match)
            except re.error as exc:
                self.problem(f"{where}: match does not compile: {exc}")
        return Check(kind="pattern", engine=str(engine), scope=str(scope), match=str(match))

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
    if check.kind == "engine":
        return f"engine: the gate {code(check.rule or '')}"
    if check.kind == "pattern":
        return f"pattern ({check.engine}, in {check.scope}): {code(check.match or '')}"
    return check.kind


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

