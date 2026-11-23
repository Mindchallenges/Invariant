"""The guidelines ruleset: its model, its schema and its validation.

A project declares its coding guidelines in one YAML ruleset (ADR-012 D3,
rulings 1 and 2), named by ``guidelines.ruleset``. Its documented place is
:func:`ruleset_location`, ``<docs project>/guidelines/ruleset.yaml``:
``invariant-docs/guidelines/ruleset.yaml`` for a new project (ADR-020 D10),
``docs/guidelines/ruleset.yaml`` for invariant and every project whose docs
project is ``docs/`` (ADR-012 ruling 1). This module
reads one and refuses it whole, naming every problem, when it does not
validate. ``core/rules`` asks it through ``guidelines.ruleset_valid``; later
increments add what is rendered from it, what is checked against it and the
verdicts recorded about it.

Refused whole, never read in part. A rule dropped because one of its keys was
misspelled stops being enforced with nothing to say so, which is the
grandfathered record the coverage baseline already refuses to read. So every
problem is collected and the ruleset either loads with all of them absent or
does not load at all.

Schema versions 1 and 2 are read (ADR-032 D2, increment 1a); a version this
build does not read is refused by its version alone. Every one of the five
check kinds is validated (D4, and the check-kind amendment's rulings DN-A1 to
DN-A14):

- ``engine``, a pointer at a registered rule, so a guideline that restates a
  gate the engine already runs has no second implementation;
- ``pattern`` with the ``regex`` engine;
- ``boundary`` with the ``imports`` engine, as a ``forbid`` deny-list or an
  allow-only ``only`` list, exactly one of the two; or, in version 2, with the
  ``fitness`` engine, naming a project's own ``test`` (ADR-032 D3);
- ``cochange``, in an explicit ``pair`` or ``registry`` form. A registry is a
  file of invariant's own format, and it is read whole or refused as part of
  the ruleset (DN-A9), so it is resolved against the project tree the caller
  hands in;
- ``judgement``, with its evidence, its citation scope and its declared N/A
  conditions.

Version 2 adds the layer map, from which one direction rule per layer is
derived at load (``layers.<id>-depends-inward``), on the layer's evidence:
``imports``, ``fitness`` with the block's ``test``, or judgement; the ``owner``, ``ruled`` and
``origin`` rule keys, with ADR-032 D7's two load-time refusals on an
``owner: user`` rule; and the pattern catalogue a ruleset names, a file of
invariant's own format read whole or refused with the ruleset, as a registry
is, and rendered beside the rules page (``render_patterns``).

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

This module is pure apart from reading the ruleset and the registries and
catalogue it names. It takes the registered rule ids as a parameter rather than importing
``core/rules``, which imports it.
"""

from __future__ import annotations

import copy
import hashlib
import json
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

#: The schema versions this build reads, and the newest of them (ADR-032 D2).
RULESET_VERSIONS = (1, 2)
RULESET_VERSION = RULESET_VERSIONS[-1]

#: Every check kind schema version 1 names (D4), and the ones this build can
#: validate. A kind outside the second set is refused, never waved through.
KINDS = ("engine", "pattern", "boundary", "cochange", "judgement")
VALIDATED_KINDS = frozenset(KINDS)

#: The state roles a rule can belong to (D7).
PHASES = ("design", "review", "landing", "always")
SEVERITIES = ("blocker", "warn")
#: A version-2 rule or map may also be ``off``: paused, kept in the ruleset,
#: never evaluated (ADR-032 ruling 13). Ranked for what lowers what.
OFF = "off"
RULE_SEVERITIES = (*SEVERITIES, OFF)
SEVERITY_RANK = {"blocker": 2, "warn": 1, OFF: 0}

#: What a pattern's regex runs over, once the adapter's lexical mask applies (D4).
PATTERN_SCOPES = ("code", "comments", "strings", "any")
#: The scopes only an adapter's mask can answer; ``any`` reads the text whole.
MASKED_SCOPES = ("code", "comments", "strings")
PATTERN_ENGINES = ("regex",)
#: Engines v1 refuses everywhere (ruling 3).
DEFERRED_ENGINES = ("syntax", "symbols")
#: A boundary is the import graph in v1 (ruling 3, DN-A14), stated as a
#: deny-list or an allow-only list, exactly one of the two (DN-A3). Version 2
#: adds ``fitness`` (ADR-032 D3): a project's own test, named by its ``test``
#: selector in place of either list, answered from the pipeline's recorded
#: runs and so by no adapter.
IMPORTS = "imports"
FITNESS = "fitness"
BOUNDARY_ENGINES = (IMPORTS, FITNESS)
BOUNDARY_LISTS = ("forbid", "only")
#: The boundary engines a configured adapter must offer (DN-A14).
ADAPTER_ENGINES = (IMPORTS,)
#: The adapters that offer ``imports``, named where a refusal says who would.
IMPORTS_ADAPTERS = "python, dart, flutter or node"

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

#: Who may except, loosen or reword a rule (ADR-032 D7): the project's agents
#: and people, or the user alone.
OWNERS = ("project", "user")

#: The layer map's evidence (ADR-032 D3): the import graph, a project's own
#: fitness test (with the block's ``test``), or a cited judgement where no
#: adapter reads the graph.
LAYER_EVIDENCE = (IMPORTS, FITNESS, "judgement")
#: The section every rule derived from the layer map sits in, declared for
#: them when the ruleset does not declare it.
LAYERS_SECTION = "layers"
LAYERS_SECTION_TITLE = "Layers"
#: The layer map's defaults (ADR-032 D2).
LAYER_SEVERITY = "blocker"
LAYER_PHASES = ("design", "review", "landing")

#: The pattern catalogue's own schema (ADR-032 D5).
CATALOGUE_VERSION = 1
PATTERN_FAMILIES = ("creational", "structural", "behavioural", "enterprise", "domain")

#: The digest a template's ``origin`` holds until :func:`stamp_origins` fills it.
ORIGIN_PLACEHOLDER = "000000000000"

#: The length every persisted hash is written in (BUG_090), and the shape of an
#: exception's optional line fingerprint (D6).
DIGEST_LENGTH = 12
_FINGERPRINT = re.compile(r"[0-9a-f]{12}")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

#: The folder the guidelines files sit in, inside the docs project
#: (ADR-020 D10), and the ruleset's own name there.
GUIDELINES_DIR = "guidelines"
RULESET_NAME = "ruleset.yaml"


def ruleset_location(docs_folder: str, name: str = RULESET_NAME) -> str:
    """Where a guidelines file belongs: ``<docs_folder>/guidelines/<name>``.

    ``docs_folder`` is the docs project's folder, project-relative
    (``core/needs.docs_folder``). The ruleset by default; ``name`` places a
    baseline or a registry beside it. A documented place, never a load-time
    default: ``guidelines.ruleset`` null still means no guidelines (D13).
    """
    return posixpath.join(docs_folder, GUIDELINES_DIR, name)


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
_TOP_KEYS_V2 = _TOP_KEYS | {"layers", "catalogue", "retired"}
_SECTION_KEYS = frozenset({"id", "title"})
_RULE_REQUIRED = (
    "id", "section", "title", "statement", "severity", "phases", "applies_to", "check",
)
_RULE_OPTIONAL = frozenset({"exceptions", "rationale", "links", "amended"})
_RULE_OPTIONAL_V2 = _RULE_OPTIONAL | {"owner", "ruled", "origin"}
_EXCEPTION_REQUIRED = ("path", "reason")
_EXCEPTION_OPTIONAL = frozenset({"fingerprint"})
_EXCEPTION_OPTIONAL_V2 = _EXCEPTION_OPTIONAL | {"ruled"}
_RULED_KEYS = ("by", "date", "reason")
_ORIGIN_KEYS = ("set", "version", "digest")
_LAYERS_KEYS = frozenset(
    {"map", "severity", "phases", "evidence", "test", "owner", "ruled", "origin"}
)
_LAYER_REQUIRED = ("id", "title", "paths")
_LAYER_OPTIONAL = frozenset({"exclude", "exceptions"})
_CATALOGUE_TOP_KEYS = frozenset({"version", "patterns"})
_PATTERN_KEYS = ("id", "name", "family", "intent", "use_when", "avoid_when")
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
    #: A boundary's deny-list or allow-only list; exactly one is non-empty
    #: on ``imports``, and neither on ``fitness``, which names its ``test``.
    forbid: tuple[str, ...] = ()
    only: tuple[str, ...] = ()
    test: str | None = None
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
class Ruling:
    """The user's ruling on a rule or an exception (ADR-032 D7, ADR-021 D6's
    shape): who ruled, on which day (ISO), and why."""

    by: str
    date: str
    reason: str


@dataclass(frozen=True)
class Origin:
    """Where a default rule was copied from (ADR-032 D8): the defaults' set
    and version, and :func:`origin_digest` of the rule as copied."""

    set: str
    version: int
    digest: str


@dataclass(frozen=True)
class GuidelineException:
    """A reasoned, path-scoped exception (D4). Never silent: a later
    increment names every one that applied."""

    path: str
    reason: str
    fingerprint: str | None = None
    #: The user's ruling, which an exception on an ``owner: user`` rule needs.
    ruled: Ruling | None = None


@dataclass(frozen=True)
class Layer:
    """One layer of the map (ADR-032 D2): the files ``paths`` names, less
    those ``exclude`` names."""

    id: str
    title: str
    paths: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    exceptions: tuple[GuidelineException, ...] = ()

    def holds(self, rel: str) -> bool:
        """Is ``rel`` in this layer: under ``paths`` and outside ``exclude``?"""
        return _within(rel, self.paths, self.exclude)


@dataclass(frozen=True)
class LayerMap:
    """The ruleset's ``layers`` block, innermost layer first (ADR-032 D2)."""

    layers: tuple[Layer, ...]
    severity: str = LAYER_SEVERITY
    phases: tuple[str, ...] = LAYER_PHASES
    evidence: str = IMPORTS
    #: The fitness test's selector, set exactly when ``evidence`` is fitness.
    test: str | None = None
    owner: str = "project"
    ruled: Ruling | None = None
    origin: Origin | None = None

    def layers_of(self, rel: str) -> tuple[Layer, ...]:
        """Every layer holding ``rel``: one, none, or more (a map to fix)."""
        return tuple(layer for layer in self.layers if layer.holds(rel))

    def outer_than(self, layer_id: str) -> tuple[Layer, ...]:
        """The layers outside the one named, innermost first."""
        ids = [layer.id for layer in self.layers]
        return self.layers[ids.index(layer_id) + 1 :] if layer_id in ids else ()

    def title_of(self, layer_id: str) -> str:
        return next((layer.title for layer in self.layers if layer.id == layer_id), layer_id)


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
    #: Globs taken out of ``applies_to``; only a rule derived from a layer
    #: with ``exclude`` has any (ADR-032 D2).
    exclude: tuple[str, ...] = ()
    #: Version 2's keys (ADR-032 D7, D8).
    owner: str = "project"
    ruled: Ruling | None = None
    origin: Origin | None = None
    #: The layer a derived rule was derived for, and the map it came from;
    #: ``None`` on every rule the ruleset declares.
    layer: str | None = None
    layer_map: LayerMap | None = field(default=None, compare=False, repr=False)


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
class Pattern:
    """One entry of the pattern catalogue (ADR-032 D5)."""

    id: str
    name: str
    family: str
    intent: str
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...]


@dataclass(frozen=True)
class Catalogue:
    """The pattern catalogue a ruleset names, read whole (ADR-032 D5)."""

    path: str
    version: int
    patterns: tuple[Pattern, ...]
    digest: str


@dataclass(frozen=True)
class Retired:
    """A rule the user retired (ADR-032 ruling 13): its id and the ruling."""

    id: str
    ruled: Ruling


@dataclass(frozen=True)
class Ruleset:
    version: int
    sections: tuple[Section, ...]
    rules: tuple[Guideline, ...]
    #: The content hash: what a pipeline pins and a verdict records (D3).
    digest: str
    #: Every registry a rule names, each read once, in first-named order.
    registries: tuple[Registry, ...] = ()
    #: Version 2: the layer map the derived rules came from, the catalogue,
    #: and what the load notes without refusing (a layer mapping no files).
    layers: LayerMap | None = None
    catalogue: Catalogue | None = None
    notes: tuple[str, ...] = ()
    #: Ruling 13: the rules at ``severity: off`` (a derived rule of a map at
    #: ``off`` included), kept apart from ``rules`` so no gate evaluates
    #: them, and the rules the user retired.
    paused: tuple[Guideline, ...] = ()
    retired: tuple[Retired, ...] = ()

    def by_id(self, rule_id: str) -> Guideline | None:
        """An active rule: what the gates evaluate. A paused one is not found."""
        return next((r for r in self.rules if r.id == rule_id), None)

    def any_rule(self, rule_id: str) -> Guideline | None:
        """A rule the ruleset holds, active or paused (ruling 13)."""
        return self.by_id(rule_id) or next((r for r in self.paused if r.id == rule_id), None)


def digest_of(data: bytes) -> str:
    """The first twelve hex digits of the SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()[:DIGEST_LENGTH]


def _either(names: list[str]) -> str:
    """``A``, ``A or B``, ``A, B or C``."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"


def _derived_rule(layer_map: LayerMap, layer: Layer, outer: list[Layer]) -> Guideline:
    """The direction rule ``layer`` gets from the map (ADR-032 D2).

    Its ``forbid`` lists every outer layer's ``paths``; whether a reference
    lands in an outer layer is read through the map itself, so an outer
    layer's ``exclude`` counts (``core/findings``). The statement and the fix
    are written from the titles.
    """
    names = _either([o.title for o in outer])
    statement = (
        f"Nothing in {layer.title} imports {names}.\n\n"
        f"Fix: define the shape {layer.title} needs as an interface in "
        f"{layer.title}, implement it outward, and inject it."
    )
    if layer_map.evidence == IMPORTS:
        check = Check(
            kind="boundary", engine=IMPORTS,
            forbid=tuple(glob for o in outer for glob in o.paths),
        )
    elif layer_map.evidence == FITNESS:
        check = Check(kind="boundary", engine=FITNESS, test=layer_map.test)
    else:
        check = Check(kind="judgement", evidence="citation", verdict_scope="diff")
    return Guideline(
        id=f"layers.{layer.id}-depends-inward",
        section=LAYERS_SECTION,
        title=f"{layer.title} depends inward",
        statement=statement,
        severity=layer_map.severity,
        phases=layer_map.phases,
        applies_to=layer.paths,
        check=check,
        exceptions=layer.exceptions,
        rationale=(
            "An inner layer never depends on an outer one, so the policy it holds "
            "is built and tested without the details around it. Derived from the "
            "layer map, innermost layer first: change the map, not this rule."
        ),
        exclude=layer.exclude,
        owner=layer_map.owner,
        layer=layer.id,
        layer_map=layer_map,
    )


def _canonical(value: Any) -> Any:
    """A parsed YAML value with every key a string, for a stable digest."""
    if isinstance(value, Mapping):
        return {("on" if k is True else str(k)): _canonical(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical(v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


def origin_digest(raw: Mapping) -> str:
    """What a default rule's ``origin.digest`` holds (ADR-032 D8).

    The digest of the rule's mapping as written into the project, ``origin``
    left out, as canonical JSON: keys sorted, so a reordering is the same
    rule, and a reworded or re-scoped rule is not.
    """
    kept = {key: value for key, value in raw.items() if key != "origin"}
    text = json.dumps(
        _canonical(kept), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return digest_of(text.encode("utf-8"))


def stamp_origins(text: str) -> str:
    """``text`` with every placeholder ``origin.digest`` filled in.

    A default rule, and the layer map, carry ``origin: {..., digest:
    "000000000000"}`` in the shipped template; this writes
    :func:`origin_digest` of each in its place, quoted, leaving every other
    byte of the text as it was.
    """
    data = yaml.safe_load(text)
    root = yaml.compose(text)
    if not isinstance(data, Mapping) or not isinstance(root, yaml.MappingNode):
        return text
    spans: list[tuple[int, int, str]] = []

    def stamp(node: Any, value: Any) -> None:
        origin = _node_value(node, "origin")
        digest = _node_value(origin, "digest")
        if isinstance(digest, yaml.ScalarNode) and digest.value == ORIGIN_PLACEHOLDER:
            spans.append(
                (digest.start_mark.index, digest.end_mark.index, origin_digest(value))
            )

    stamp(_node_value(root, "layers"), data.get("layers"))
    rules = _node_value(root, "rules")
    if isinstance(rules, yaml.SequenceNode) and isinstance(data.get("rules"), list):
        for node, value in zip(rules.value, data["rules"], strict=True):
            stamp(node, value)
    for start, end, digest in sorted(spans, reverse=True):
        text = f'{text[:start]}"{digest}"{text[end:]}'
    return text


@dataclass(frozen=True)
class DefaultsMerged:
    """What :func:`merge_defaults` made of a project's ruleset (ADR-032
    increment 1b, DEC_ADR_032_I1B)."""

    text: str
    #: The version the ruleset was raised to; ``None`` where it was not.
    version: int | None = None
    #: The ``catalogue`` key added; ``None`` where none was.
    catalogue: str | None = None
    #: The section ids added, in the defaults' order.
    sections: tuple[str, ...] = ()
    #: Whether the layer block was added.
    layers: bool = False
    #: The rule ids added, in the defaults' order.
    rules: tuple[str, ...] = ()
    #: The default rule ids the project holds already, left as it wrote them.
    kept: tuple[str, ...] = ()


_TOP_KEY = re.compile(r"([A-Za-z_][\w-]*):(?:\s|$)")
_SEQUENCE_ITEM = re.compile(r"( *)- ")
_LAYER_ID = re.compile(r"\s*- id:\s*(\S+)")
_EMPTY_PATHS = re.compile(r"(\s*)paths:\s*\[\]\s*$")


def merge_defaults(
    project: str, defaults: str, *, layers: Mapping[str, Iterable[str]] | None = None
) -> DefaultsMerged:
    """``project``'s ruleset text with what it lacks of the default set added.

    ``defaults`` is the default set's text as filled for the project and not
    yet stamped (``domains/docs.default_ruleset``). Added, as text: the
    version raised to the defaults', their ``catalogue`` key after the
    ``version`` line, each absent section at the end of ``sections``, the
    layer block before ``rules`` with each layer ``layers`` names given those
    ``paths``, and each absent rule at the end of ``rules``. Nothing the
    project wrote is rewritten or dropped, comments included, and a rule or
    section whose id the project holds is never replaced. The added
    ``origin`` digests are then stamped (:func:`stamp_origins`), so each is
    the digest of the project's copy.

    Refused whole (:class:`RulesetError`) where either text cannot be read
    as a ruleset mapping, the project's version is not one this build reads,
    ``layers`` is given where the project maps its layers already or names a
    layer the defaults do not, a block to add to is missing or written
    inline, or the result does not read back as exactly the project's
    ruleset plus those additions. Pure: no file is read or written.
    """
    ours = _mapping(project, "the project's ruleset")
    theirs = _mapping(defaults, "the defaults")
    version = ours.get("version")
    if version not in RULESET_VERSIONS:
        raise RulesetError(
            [f"the project's ruleset is version {version!r}, which this build does not read"]
        )
    newest = theirs.get("version")
    if newest not in RULESET_VERSIONS:
        raise RulesetError([f"the defaults are version {newest!r}, which this build does not read"])
    if layers and "layers" in ours:
        raise RulesetError([(
            "the project's ruleset already maps its layers; change the map there "
            "rather than merging another over it"
        )])
    lines = project.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    source = defaults.splitlines(keepends=True)
    blocks, sources = _top_blocks(lines), _top_blocks(source)
    inserts: list[tuple[int, int, list[str]]] = []
    expected = dict(ours)

    raised = newest if version < newest else None
    start = blocks["version"][0]
    if raised is not None:
        lines[start] = re.sub(r"^version:\s*[^\s#]+", f"version: {raised}", lines[start])
        expected["version"] = raised
    catalogue = theirs.get("catalogue") if "catalogue" not in ours else None
    if catalogue is not None:
        inserts.append((start + 1, 0, [f"catalogue: {catalogue}\n"]))
        expected["catalogue"] = catalogue

    held = {s.get("id") for s in _entries(ours, "sections")}
    sections = [s for s in _entries(theirs, "sections") if s.get("id") not in held]
    if sections:
        chunks = _chunks(source, sources, "sections", theirs, "the defaults")
        inserts.append(_appended(lines, blocks, "sections", [chunks[s["id"]] for s in sections]))
        expected["sections"] = [*_entries(ours, "sections"), *sections]

    added_layers = "layers" in theirs and "layers" not in ours
    if added_layers:
        filled = _filled_layers(source, sources, theirs["layers"], layers or {})
        expected["layers"] = filled[1]
        inserts.append((_head(lines, blocks, "rules"), 1, filled[0]))

    held = {r.get("id") for r in _entries(ours, "rules")}
    # Ruling 13: a rule the user retired is never added back.
    retired = {r.get("id") for r in _entries(ours, "retired")}
    defaults_rules = [r for r in _entries(theirs, "rules") if r.get("id") not in retired]
    kept = tuple(r["id"] for r in defaults_rules if r.get("id") in held)
    wanted = [r for r in defaults_rules if r.get("id") not in held]
    if wanted:
        chunks = _chunks(source, sources, "rules", theirs, "the defaults")
        inserts.append(_appended(lines, blocks, "rules", [chunks[r["id"]] for r in wanted]))
        expected["rules"] = [*_entries(ours, "rules"), *wanted]

    # Back to front, so each index still names the line it was planned on.
    # At one index the lower order lands first: a list's appended items
    # before the layer block that follows the list.
    for index, _order, added in sorted(inserts, key=lambda i: (i[0], i[1]), reverse=True):
        lines[index:index] = added
    try:
        text = stamp_origins("".join(lines))
    except yaml.YAMLError as exc:
        raise _unread() from exc
    _read_back(text, expected)
    return DefaultsMerged(
        text=text, version=raised, catalogue=catalogue,
        sections=tuple(s["id"] for s in sections),
        layers=added_layers, rules=tuple(r["id"] for r in wanted), kept=kept,
    )


def _mapping(text: str, name: str) -> Mapping:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RulesetError([f"{name} is not valid YAML: {exc}"]) from exc
    if not isinstance(data, Mapping):
        raise RulesetError([f"{name} must be a mapping with version, sections and rules"])
    return data


def _entries(data: Mapping, key: str) -> list:
    value = data.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, Mapping) for v in value):
        raise RulesetError([f"{key} must be a list of mappings to merge into"])
    return value


def _loose(line: str) -> bool:
    """A blank line, or a comment at the left margin, between two blocks."""
    return not line.strip() or line.startswith("#")


def _top_blocks(lines: list[str]) -> dict[str, tuple[int, int]]:
    """Each top-level key's lines, from its key line to the last line before
    the next key that is not loose: a comment above a key is that key's."""
    keys = [(i, m.group(1)) for i, line in enumerate(lines) if (m := _TOP_KEY.match(line))]
    blocks = {}
    for n, (start, key) in enumerate(keys):
        end = keys[n + 1][0] if n + 1 < len(keys) else len(lines)
        while end > start + 1 and _loose(lines[end - 1]):
            end -= 1
        blocks[key] = (start, end)
    return blocks


def _block(lines: list[str], blocks: Mapping, key: str, whose: str) -> tuple[int, int]:
    if key not in blocks:
        raise RulesetError([f"{whose} has no {key} block to merge into"])
    start, end = blocks[key]
    if lines[start].split(":", 1)[1].split("#", 1)[0].strip():
        raise RulesetError([
            f"{whose} writes {key} inline; write it as a block list to merge into it"
        ])
    return start, end


def _item_starts(lines: list[str], start: int, end: int) -> tuple[int | None, list[int]]:
    """The indent of a block list's items and the line each item starts on."""
    indent = None
    starts = []
    for i in range(start + 1, end):
        m = _SEQUENCE_ITEM.match(lines[i])
        if m is None:
            continue
        if indent is None:
            indent = len(m.group(1))
        if len(m.group(1)) == indent:
            starts.append(i)
    return indent, starts


def _chunks(
    lines: list[str], blocks: Mapping, key: str, data: Mapping, whose: str
) -> dict[str, tuple[int, list[str]]]:
    """Each item of a block list by its id: its indent and its lines."""
    start, end = _block(lines, blocks, key, whose)
    indent, starts = _item_starts(lines, start, end)
    items = _entries(data, key)
    if len(starts) != len(items):
        raise RulesetError([f"{whose}' {key} could not be read item by item"])
    bounds = [*starts, end]
    return {
        item["id"]: (indent, lines[bounds[n]:bounds[n + 1]]) for n, item in enumerate(items)
    }


def _reindent(chunk: list[str], by: int) -> list[str]:
    if by >= 0:
        return [(" " * by + line) if line.strip() else line for line in chunk]
    return [line[min(-by, len(line) - len(line.lstrip(" "))):] for line in chunk]


def _appended(
    lines: list[str], blocks: Mapping, key: str, chunks: list[tuple[int, list[str]]]
) -> tuple[int, int, list[str]]:
    """The insertion that appends ``chunks`` to the project's ``key`` list,
    each re-indented to the list's own indent."""
    start, end = _block(lines, blocks, key, "the project's ruleset")
    indent, _starts = _item_starts(lines, start, end)
    added = []
    for source_indent, chunk in chunks:
        target = source_indent if indent is None else indent
        added.extend(_reindent(chunk, target - source_indent))
    return end, 0, added


def _head(lines: list[str], blocks: Mapping, key: str) -> int:
    """Where a block's leading comments begin, or the end with no such block."""
    if key not in blocks:
        return len(lines)
    index = blocks[key][0]
    while index > 0 and _loose(lines[index - 1]):
        index -= 1
    return index


def _filled_layers(
    lines: list[str], blocks: Mapping, data: Any, paths: Mapping[str, Iterable[str]]
) -> tuple[list[str], dict]:
    """The defaults' layer block with ``paths`` filled, as text and as data."""
    start, end = _block(lines, blocks, "layers", "the defaults")
    named = [layer.get("id") for layer in (data or {}).get("map") or []]
    unknown = [layer_id for layer_id in paths if layer_id not in named]
    if unknown:
        raise RulesetError([
            f"layer {layer_id!r} is not one the defaults name ({', '.join(named)})"
            for layer_id in unknown
        ])
    chunk = list(lines[start:end])
    current = None
    filled = set()
    for i, line in enumerate(chunk):
        m = _LAYER_ID.match(line)
        if m is not None:
            current = m.group(1)
        empty = _EMPTY_PATHS.match(line)
        if empty is not None and current in paths:
            values = json.dumps([str(glob) for glob in paths[current]], ensure_ascii=False)
            chunk[i] = f"{empty.group(1)}paths: {values}\n"
            filled.add(current)
    missing = [layer_id for layer_id in paths if layer_id not in filled]
    if missing:
        raise RulesetError([
            f"layer {layer_id!r} has no empty paths in the defaults to fill"
            for layer_id in missing
        ])
    value = copy.deepcopy(dict(data))
    for layer in value.get("map") or []:
        if layer.get("id") in paths:
            layer["paths"] = [str(glob) for glob in paths[layer["id"]]]
    return chunk, value


def _read_back(text: str, expected: Mapping) -> None:
    """Refuse a merge that does not read back as ``expected``, with each added
    placeholder ``origin`` read as the digest it was stamped with."""
    want = copy.deepcopy(dict(expected))
    stamped = [*(want.get("rules") or []), want.get("layers")]
    for entry in stamped:
        origin = entry.get("origin") if isinstance(entry, dict) else None
        if isinstance(origin, dict) and origin.get("digest") == ORIGIN_PLACEHOLDER:
            origin["digest"] = origin_digest(entry)
    if yaml.safe_load(text) != want:
        raise _unread()


def _unread() -> RulesetError:
    return RulesetError([(
        "the merged ruleset does not read back as the project's ruleset plus "
        "the defaults' additions, so nothing was merged; write the project's "
        "sections and rules as block lists, one item per id"
    )])


def _node_value(node: Any, key: str) -> Any:
    if not isinstance(node, yaml.MappingNode):
        return None
    return next((v for k, v in node.value if getattr(k, "value", None) == key), None)


# -- the user's edits, and the adopt writer's reads (ADR-032 increment 2) --------------------
#
# Each edit is made on the text, line by line, so every byte the project
# wrote is kept, comments and folded statements included, as
# :func:`merge_defaults` keeps them; and each is refused unless the result
# reads back as exactly the ruleset meant (DEC_ADR_032_I2).


class EditUnwritable(RulesetError):
    """A ruleset edit the line editor cannot make in place. ``entry`` is the
    text the command prints to add by hand instead."""

    def __init__(self, problems: Iterable[str], entry: str) -> None:
        super().__init__(problems)
        self.entry = entry


@dataclass(frozen=True)
class RulesetEdit:
    """One edit of a ruleset's text: the result, and the lines it wrote."""

    text: str
    #: The written lines as they would be added by hand.
    entry: str


#: How each default the project holds stands against the shipped one
#: (ADR-032 D8): its copy as copied and the shipped one the same; its copy
#: as copied and the shipped one moved since; its copy changed by the user.
CURRENT_DEFAULT = "current"
UPDATABLE_DEFAULT = "updatable"
DIVERGED_DEFAULT = "diverged"
#: The id a standing gives the layer map.
LAYERS_KEY = "layers"


@dataclass(frozen=True)
class DefaultStanding:
    """One default the project holds with an ``origin``, and how it stands."""

    id: str
    standing: str


def _text_lines(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return lines


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _has_content(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _key_at(lines: list[str], lo: int, hi: int, indent: int, key: str) -> int | None:
    """The line in ``[lo, hi)`` holding ``key`` at exactly ``indent``."""
    pattern = re.compile(rf" {{{indent}}}{re.escape(key)}:(?:\s|$)")
    return next((i for i in range(lo, hi) if pattern.match(lines[i])), None)


def _value_end(lines: list[str], key_line: int, hi: int, indent: int) -> int:
    """One past the last content line of the value under ``key_line``."""
    last = key_line + 1
    for i in range(key_line + 1, hi):
        line = lines[i]
        if not _has_content(line):
            continue
        deeper = _indent_of(line) > indent
        listed = _indent_of(line) == indent and line.lstrip(" ").startswith("- ")
        if not (deeper or listed):
            break
        last = i + 1
    return last


def _last_content(lines: list[str], lo: int, hi: int) -> int:
    """One past the last content line in ``[lo, hi)``."""
    return next((i + 1 for i in range(hi - 1, lo - 1, -1) if _has_content(lines[i])), lo + 1)


def _block_indent(lines: list[str], start: int, end: int) -> int:
    """The indent of a top-level block's own keys."""
    return next((_indent_of(lines[i]) for i in range(start + 1, end) if _has_content(lines[i])), 2)


def _inline_value(line: str) -> str:
    return line.split(":", 1)[1].split("#", 1)[0].strip()


def _in_list(
    lines: list[str], key_line: int, end: int, items: list, wanted: str
) -> tuple[int, int, int]:
    """The span of the item whose ``id`` is ``wanted`` in the block list under
    ``key_line``: its first line, one past its last, and its keys' indent."""
    indent, starts = _item_starts(lines, key_line, end)
    if indent is None or len(starts) != len(items):
        raise RulesetError([f"the list holding {wanted!r} could not be read item by item"])
    for n, item in enumerate(items):
        if isinstance(item, Mapping) and item.get("id") == wanted:
            hi = starts[n + 1] if n + 1 < len(starts) else end
            return starts[n], hi, indent + 2
    raise RulesetError([f"no item {wanted!r} in the list"])


def _rule_span(lines: list[str], data: Mapping, rule_id: str) -> tuple[int, int, int]:
    blocks = _top_blocks(lines)
    start, end = _block(lines, blocks, "rules", "the ruleset")
    return _in_list(lines, start, end, _entries(data, "rules"), rule_id)


def _layer_span(lines: list[str], data: Mapping, layer_id: str) -> tuple[int, int, int]:
    blocks = _top_blocks(lines)
    start, end = _block(lines, blocks, "layers", "the ruleset")
    indent = _block_indent(lines, start, end)
    map_line = _key_at(lines, start + 1, end, indent, "map")
    if map_line is None or _inline_value(lines[map_line]):
        raise RulesetError(["the layer map is not written as a block list to edit"])
    stop = _value_end(lines, map_line, end, indent)
    return _in_list(lines, map_line, stop, list((data.get("layers") or {}).get("map") or []), layer_id)


def _layers_span(lines: list[str]) -> tuple[int, int, int]:
    blocks = _top_blocks(lines)
    start, end = _block(lines, blocks, "layers", "the ruleset")
    return start, end, _block_indent(lines, start, end)


def _quoted(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _ruled_lines(ruled: Mapping, indent: int) -> list[str]:
    pad = " " * indent
    return [f"{pad}ruled:\n", *(f"{pad}  {key}: {_quoted(ruled[key])}\n" for key in _RULED_KEYS)]


def _exception_lines(exception: Mapping, indent: int) -> list[str]:
    pad = " " * indent
    lines = [f"{pad}- path: {_quoted(exception['path'])}\n",
             f"{pad}  reason: {_quoted(exception['reason'])}\n"]
    if exception.get("fingerprint"):
        lines.append(f"{pad}  fingerprint: {_quoted(exception['fingerprint'])}\n")
    return lines + _ruled_lines(exception["ruled"], indent + 2)


def _exact(text: str, expected: Any, entry: str) -> None:
    try:
        reread = yaml.safe_load(text)
    except yaml.YAMLError:
        reread = None
    if reread != expected:
        raise EditUnwritable([(
            "the edited ruleset does not read back as the ruleset plus the edit, so "
            "nothing was written; add the entry by hand"
        )], entry)


def add_exception(
    text: str, exception: Mapping, *, rule: str | None = None, layer: str | None = None
) -> RulesetEdit:
    """``text`` with ``exception`` appended to a rule's ``exceptions``, or with
    ``layer``, to that layer's in the map (ADR-032 D7).

    ``exception`` is ``{path, reason, fingerprint?, ruled: {by, date,
    reason}}``. Every other byte is kept. :class:`EditUnwritable` where the
    list is written inline, or the result does not read back as the ruleset
    with the entry added.
    """
    lines = _text_lines(text)
    data = _mapping(text, "the ruleset")
    entry = "".join(_exception_lines(exception, 0))
    expected = copy.deepcopy(data)
    if layer is not None:
        start, end, indent = _layer_span(lines, data, layer)
        node = next(m for m in expected["layers"]["map"] if m.get("id") == layer)
    else:
        start, end, indent = _rule_span(lines, data, str(rule))
        node = next(r for r in expected["rules"] if r.get("id") == rule)
    held = node.get("exceptions")
    if held is not None and not isinstance(held, list):
        raise EditUnwritable(["exceptions is not a list to append to"], entry)
    node["exceptions"] = [*(held or []), {k: v for k, v in exception.items() if v is not None}]
    key_line = _key_at(lines, start + 1, end, indent, "exceptions")
    pad = " " * indent
    if key_line is None:
        at = _last_content(lines, start, end)
        lines[at:at] = [f"{pad}exceptions:\n", *_exception_lines(exception, indent + 2)]
    elif _inline_value(lines[key_line]) == "[]":
        lines[key_line:key_line + 1] = [
            f"{pad}exceptions:\n", *_exception_lines(exception, indent + 2)
        ]
    elif _inline_value(lines[key_line]):
        raise EditUnwritable(["exceptions is written inline, so it is not edited in place"], entry)
    else:
        stop = _value_end(lines, key_line, end, indent)
        listed, _starts = _item_starts(lines, key_line, stop)
        lines[stop:stop] = _exception_lines(exception, indent + 2 if listed is None else listed)
    edited = "".join(lines)
    _exact(edited, expected, entry)
    return RulesetEdit(edited, entry)


def lower_severity(
    text: str, ruled: Mapping, *, rule: str | None = None, to: str = "warn"
) -> RulesetEdit:
    """``text`` with a rule's severity, or with no ``rule`` the layer map's,
    set to ``to`` and its ``ruled`` set (ADR-032 D7), replacing an older
    ruling. Every other byte, a trailing comment on the severity line
    included, is kept; refused as :func:`add_exception` is."""
    lines = _text_lines(text)
    data = _mapping(text, "the ruleset")
    entry = f"severity: {_quoted(to) if to == OFF else to}\n" + "".join(_ruled_lines(ruled, 0))
    expected = copy.deepcopy(data)
    node = (
        expected["layers"] if rule is None
        else next(r for r in expected["rules"] if r.get("id") == rule)
    )
    node["severity"] = to
    node["ruled"] = {key: str(ruled[key]) for key in _RULED_KEYS}

    def span() -> tuple[int, int, int]:
        return _layers_span(lines) if rule is None else _rule_span(lines, data, rule)

    # Quoted where YAML 1.1 would read the bare word as a boolean (``off``).
    spelled = _quoted(to) if to == OFF else to
    start, end, indent = span()
    severity = _key_at(lines, start + 1, end, indent, "severity")
    if severity is not None:
        lines[severity] = re.sub(
            r"(severity:\s*)[^\s#]+", lambda m: m.group(1) + spelled, lines[severity], count=1
        )
    start, end, indent = span()
    held = _key_at(lines, start + 1, end, indent, "ruled")
    if held is not None:
        lines[held:_value_end(lines, held, end, indent)] = _ruled_lines(ruled, indent)
    else:
        at = _last_content(lines, start, end)
        lines[at:at] = _ruled_lines(ruled, indent)
    if severity is None:
        start, end, indent = span()
        at = _last_content(lines, start, end)
        lines[at:at] = [f"{' ' * indent}severity: {spelled}\n"]
    edited = "".join(lines)
    _exact(edited, expected, entry)
    return RulesetEdit(edited, entry)


def retire_rule(text: str, rule_id: str, ruled: Mapping) -> RulesetEdit:
    """``text`` with the rule ``rule_id`` removed and ``{id, ruled}`` appended
    to the top-level ``retired`` list (ADR-032 ruling 13). Every other byte
    is kept; refused as :func:`add_exception` is."""
    lines = _text_lines(text)
    data = _mapping(text, "the ruleset")

    def item(indent: int) -> list[str]:
        return [f"{' ' * indent}- id: {_quoted(rule_id)}\n", *_ruled_lines(ruled, indent + 2)]

    entry = "retired:\n" + "".join(item(2))
    expected = copy.deepcopy(data)
    expected["rules"] = [r for r in expected.get("rules") or [] if r.get("id") != rule_id]
    expected["retired"] = [
        *(expected.get("retired") or []),
        {"id": rule_id, "ruled": {key: str(ruled[key]) for key in _RULED_KEYS}},
    ]
    start, end, _indent = _rule_span(lines, data, rule_id)
    del lines[start:_last_content(lines, start, end)]
    blocks = _top_blocks(lines)
    if "retired" in blocks:
        first, last = _block(lines, blocks, "retired", "the ruleset")
        listed, _starts = _item_starts(lines, first, last)
        lines[last:last] = item(2 if listed is None else listed)
    else:
        lines.append("retired:\n")
        lines.extend(item(2))
    edited = "".join(lines)
    _exact(edited, expected, entry)
    return RulesetEdit(edited, entry)

def fill_defaults(defaults: str, layers: Mapping[str, Iterable[str]] | None = None) -> str:
    """The default set as a project with no ruleset gets it (ADR-032 D8):
    ``defaults`` (``domains/docs.default_ruleset``) with the layers ``layers``
    names given those ``paths`` and every ``origin`` stamped, so each digest is
    of the project's copy. Refused as :func:`merge_defaults` refuses a map."""
    lines = _text_lines(defaults)
    data = _mapping(defaults, "the defaults")
    expected = dict(data)
    if layers:
        blocks = _top_blocks(lines)
        chunk, value = _filled_layers(lines, blocks, data.get("layers"), layers)
        start, end = blocks["layers"]
        lines[start:end] = chunk
        expected["layers"] = value
    text = stamp_origins("".join(lines))
    _read_back(text, expected)
    return text


def default_standings(project: str, defaults: str) -> tuple[DefaultStanding, ...]:
    """How each default ``project`` holds with an ``origin`` stands against
    ``defaults`` as filled for it (ADR-032 D8, DEC_ADR_032_I2).

    Diverged where the copy's digest is not its ``origin.digest``; else
    updatable where the shipped rule's digest is not that either, and
    current where it is. The map's shipped digest is taken with the
    project's own paths, which it filled. A default the project lacks is the
    merge's to add, and not listed.
    """
    ours = _mapping(project, "the project's ruleset")
    theirs = _mapping(defaults, "the defaults")
    shipped = {r.get("id"): r for r in _entries(theirs, "rules")}
    found: list[DefaultStanding] = []
    for rule in _entries(ours, "rules"):
        other = shipped.get(rule.get("id"))
        if isinstance(rule.get("origin"), Mapping) and other is not None:
            found.append(DefaultStanding(
                str(rule["id"]), _standing(rule, origin_digest(other))
            ))
    mine, map_shipped = ours.get("layers"), theirs.get("layers")
    if (
        isinstance(mine, Mapping) and isinstance(mine.get("origin"), Mapping)
        and isinstance(map_shipped, Mapping)
    ):
        paths = {
            layer.get("id"): layer.get("paths")
            for layer in mine.get("map") or [] if isinstance(layer, Mapping)
        }
        filled = copy.deepcopy(dict(map_shipped))
        for layer in filled.get("map") or []:
            if isinstance(layer, dict) and layer.get("id") in paths:
                layer["paths"] = paths[layer["id"]]
        found.append(DefaultStanding(LAYERS_KEY, _standing(mine, origin_digest(filled))))
    return tuple(found)


def _standing(copied: Mapping, shipped: str) -> str:
    recorded = copied["origin"].get("digest")
    if origin_digest(copied) != recorded:
        return DIVERGED_DEFAULT
    return CURRENT_DEFAULT if shipped == recorded else UPDATABLE_DEFAULT


def replace_default(project: str, defaults: str, rule_id: str) -> str:
    """``project`` with the rule ``rule_id`` replaced by the shipped one, its
    ``origin`` stamped (``--update``, ADR-032 D8). Every other byte is kept;
    refused unless the result reads back as the project's ruleset with that
    one rule replaced."""
    lines = _text_lines(project)
    ours = _mapping(project, "the project's ruleset")
    theirs = _mapping(defaults, "the defaults")
    start, end, indent = _rule_span(lines, ours, rule_id)
    source = _text_lines(defaults)
    chunks = _chunks(source, _top_blocks(source), "rules", theirs, "the defaults")
    if rule_id not in chunks:
        raise RulesetError([f"the defaults hold no rule {rule_id!r}"])
    source_indent, chunk = chunks[rule_id]
    chunk = chunk[: _last_content(chunk, 0, len(chunk))]
    lines[start:_last_content(lines, start, end)] = _reindent(chunk, indent - 2 - source_indent)
    expected = copy.deepcopy(dict(ours))
    expected["rules"] = [
        copy.deepcopy(dict(next(r for r in _entries(theirs, "rules") if r.get("id") == rule_id)))
        if r.get("id") == rule_id else r
        for r in expected["rules"]
    ]
    try:
        text = stamp_origins("".join(lines))
    except yaml.YAMLError as exc:
        raise _unread() from exc
    _read_back(text, expected)
    return text


def unruled_changes(before: Ruleset | None, after: Ruleset) -> tuple[str, ...]:
    """Each change from ``before`` (the base's ruleset) to ``after`` of an
    ``owner: user`` rule or map that carries no new ruling (ADR-032 D7,
    DEC_ADR_032_I2), one line each; empty where there is none.

    A rule or the map removed, lowered, made the project's, narrowed or
    reworded needs its ``ruled`` set and different from the base's; an
    exception added needs its own ``ruled``, equal to none the rule held at
    the base. Adding or widening needs nothing. Pure.
    """
    if before is None:
        return ()
    found: list[str] = []
    for old in (*before.rules, *before.paused):
        if old.layer is not None or old.owner != "user":
            continue
        new = after.any_rule(old.id)
        if new is None or new.layer is not None:
            if not _retired_anew(old.id, before, after):
                found.append(
                    f"{old.id}: removed with no new retired entry ruling it "
                    f"(invariant guidelines retire)"
                )
            continue
        changes = _rule_loosened(old, new)
        if changes and (new.ruled is None or new.ruled == old.ruled):
            found.append(f"{old.id}: {', '.join(changes)}, with no new ruled")
        found += _unruled_exceptions(old.id, old.ruled, old.exceptions, new.exceptions)
    if before.layers is not None and before.layers.owner == "user":
        found += _map_unruled(before.layers, after.layers)
    return tuple(found)


def _retired_anew(rule_id: str, before: Ruleset, after: Ruleset) -> bool:
    """Is ``rule_id`` retired in ``after`` with a ruling the base did not hold
    for it (ruling 13)?"""
    held = {r.ruled for r in before.retired if r.id == rule_id}
    return any(r.id == rule_id and r.ruled not in held for r in after.retired)


def _lowered(old: str, new: str) -> bool:
    return SEVERITY_RANK.get(new, 0) < SEVERITY_RANK.get(old, 0)


def _lost(old: Iterable[str], new: Iterable[str]) -> list[str]:
    kept = set(new)
    return [item for item in old if item not in kept]


def _rule_loosened(old: Guideline, new: Guideline) -> list[str]:
    changes: list[str] = []
    if _lowered(old.severity, new.severity):
        changes.append(f"lowered from {old.severity} to {new.severity}")
    if new.owner != "user":
        changes.append("made the project's")
    narrowed = [
        f"{key} {verb} {', '.join(items)}"
        for key, verb, items in (
            ("phases", "loses", _lost(old.phases, new.phases)),
            ("applies_to", "loses", _lost(old.applies_to, new.applies_to)),
            ("exclude", "gains", _lost(new.exclude, old.exclude)),
        )
        if items
    ]
    widened = replace(
        old.check,
        not_applicable_when=tuple(
            c for c in old.check.not_applicable_when if c in new.check.not_applicable_when
        ),
    )
    if new.check != widened:
        narrowed.append("its check changed")
    if narrowed:
        changes.append("narrowed (" + "; ".join(narrowed) + ")")
    if (old.title, old.statement, old.rationale) != (new.title, new.statement, new.rationale):
        changes.append("reworded")
    return changes


def _unruled_exceptions(
    owner: str,
    ruled: Ruling | None,
    old: Iterable[GuidelineException],
    new: Iterable[GuidelineException],
) -> list[str]:
    old = tuple(old)
    held = {ruled, *(exc.ruled for exc in old)} - {None}
    return [
        f"{owner}: an exception for {exc.path} was added with no new ruled"
        for exc in new
        if exc not in old and (exc.ruled is None or exc.ruled in held)
    ]


def _map_unruled(old: LayerMap, new: LayerMap | None) -> list[str]:
    if new is None:
        return ["the layer map: removed, with no new ruled"]
    changes: list[str] = []
    if _lowered(old.severity, new.severity):
        changes.append(f"lowered from {old.severity} to {new.severity}")
    if new.owner != "user":
        changes.append("made the project's")
    if _lost(old.phases, new.phases) or (new.evidence, new.test) != (old.evidence, old.test):
        changes.append("its phases, evidence or test changed")
    ids = [layer.id for layer in new.layers]
    known = {layer.id: layer for layer in new.layers}
    for layer in old.layers:
        now = known.get(layer.id)
        if now is None:
            changes.append(f"layer {layer.id} dropped")
            continue
        if now.title != layer.title:
            changes.append(f"layer {layer.id} retitled")
        if _lost(layer.paths, now.paths) or _lost(now.exclude, layer.exclude):
            changes.append(f"layer {layer.id} narrowed")
    if [i for i in ids if i in {layer.id for layer in old.layers}] != [
        layer.id for layer in old.layers if layer.id in known
    ]:
        changes.append("its layers moved")
    found = []
    if changes and (new.ruled is None or new.ruled == old.ruled):
        found.append(f"the layer map: {', '.join(changes)}, with no new ruled")
    for layer in old.layers:
        now = known.get(layer.id)
        if now is not None:
            found += _unruled_exceptions(
                f"the layer map's {layer.id}", old.ruled, layer.exceptions, now.exceptions
            )
    return found


def load(
    path: Path,
    *,
    registered: Collection[str],
    root: Path | None = None,
    engines: Collection[str] | None = None,
) -> Ruleset:
    """Read and validate the ruleset at ``path``, or refuse it whole.

    ``root`` is the project tree the ruleset was read from, which a registry
    path is resolved against, and the ruleset's path in it is the folder a
    catalogue is named from. ``engines`` is as :func:`parse` takes it.
    """
    try:
        data = Path(path).read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RulesetError([f"the ruleset could not be read: {exc}"]) from exc
    location = None
    if root is not None:
        try:
            location = Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            location = None
    return parse(text, registered=registered, root=root, engines=engines, location=location)


def parse(
    text: str,
    *,
    registered: Collection[str],
    root: Path | None = None,
    engines: Collection[str] | None = None,
    read: Callable[[str], bytes | None] | None = None,
    location: str | None = None,
) -> Ruleset:
    """Validate ruleset ``text`` against the schema version it names.

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

    ``location`` is the ruleset's own project-relative path: a version-2
    ``catalogue`` is named from its folder, and read through ``read`` or
    from ``root`` as a registry is.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RulesetError([f"the ruleset is not valid YAML: {exc}"]) from exc
    if not isinstance(raw, Mapping):
        raise RulesetError(["the ruleset must be a mapping with version, sections and rules"])
    reader = _Reader(registered, root, engines, read, location)
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
        location: str | None = None,
    ) -> None:
        self.registered = frozenset(registered)
        self.root = root
        self.read = read
        self.location = location
        #: The schema version being read; version-2 keys are refused in 1, and
        #: ``newer`` holds the ones the next :meth:`unknown` names as such.
        self.version = 1
        self.newer: frozenset[str] = frozenset()
        self.engines = None if engines is None else frozenset(engines)
        self.problems: list[str] = []
        #: Each registry path read so far, and what it read as. ``None`` is a
        #: registry already refused, so its problems are named once.
        self.registries: dict[str, Registry | None] = {}

    def problem(self, text: str) -> None:
        self.problems.append(text)

    # -- the envelope ---------------------------------------------------------

    def ruleset(self, raw: Mapping, digest: str) -> Ruleset:
        version = raw.get("version")
        readable = type(version) is int and version in RULESET_VERSIONS
        if "version" in raw and not readable:
            # Refused by the version alone (ADR-032 D2): a later schema's keys
            # are not this build's to judge, so naming them would mislead.
            self.problem(
                f"the ruleset is version {version!r}; this build reads versions "
                f"{' and '.join(str(v) for v in RULESET_VERSIONS)}, so a newer "
                f"invariant is needed to read it"
            )
            return Ruleset(RULESET_VERSION, (), (), digest)
        self.version = version if readable else 1
        self.unknown("the ruleset", raw, self.allowed(_TOP_KEYS, _TOP_KEYS_V2))
        missing = sorted(_TOP_KEYS - set(raw))
        if missing:
            self.problem(f"the ruleset is missing {', '.join(missing)}")
        sections = self.sections(raw.get("sections", []))
        declared = {s.id for s in sections}
        rules = self.rules(raw.get("rules", []), declared)
        layer_map = self.layer_map(raw["layers"]) if self.version == 2 and "layers" in raw else None
        notes: list[str] = []
        if layer_map is not None:
            derived = self.derived(layer_map, rules)
            if derived and LAYERS_SECTION not in declared:
                sections += (Section(LAYERS_SECTION, LAYERS_SECTION_TITLE),)
            rules += derived
            notes = [f"layer {layer.title} maps no files" for layer in layer_map.layers
                     if not layer.paths]
        catalogue = (
            self.catalogue(raw["catalogue"]) if self.version == 2 and "catalogue" in raw else None
        )
        registries = tuple(r for r in self.registries.values() if r is not None)
        retired = (
            self.retired(raw["retired"], {r.id for r in rules})
            if self.version == 2 and "retired" in raw else ()
        )
        return Ruleset(
            self.version, sections,
            tuple(r for r in rules if r.severity != OFF), digest, registries,
            layers=layer_map, catalogue=catalogue, notes=tuple(notes),
            paused=tuple(r for r in rules if r.severity == OFF), retired=retired,
        )

    def allowed(self, v1: frozenset[str], v2: frozenset[str]) -> frozenset[str]:
        """The keys the version being read allows; a version-2 key in a
        version-1 file is named as one (see :meth:`unknown`)."""
        self.newer = v2 - v1
        return v2 if self.version == 2 else v1

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
        self.unknown(
            where, raw, frozenset(_RULE_REQUIRED) | self.allowed(_RULE_OPTIONAL, _RULE_OPTIONAL_V2)
        )
        absent = [key for key in _RULE_REQUIRED if key not in raw]
        if absent:
            self.problem(f"{where}: missing {', '.join(absent)}")
            return None
        section = raw["section"]
        if section not in sections:
            self.problem(f"{where} names section {section!r}, which no section declares")
        severity = self.severity(where, raw["severity"], str(raw["severity"]))
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
        owner = self.owner(where, raw.get("owner", "project"))
        if owner == "user":
            self.user_owned(where, exceptions, check)
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
            owner=owner,
            ruled=self.ruling(where, raw["ruled"]) if "ruled" in raw else None,
            origin=self.origin(where, raw["origin"]) if "origin" in raw else None,
        )

    def severity(self, where: str, raw: Any, otherwise: str) -> str:
        """A rule's or the map's severity: ``off`` is version 2's (ruling 13).
        YAML 1.1 reads a bare ``off`` as false, so false is read as ``off``."""
        if raw is False:
            raw = OFF
        allowed = RULE_SEVERITIES if self.version == 2 else SEVERITIES
        if raw not in allowed:
            told = (
                "; off is a version-2 severity" if raw == OFF else ""
            )
            self.problem(
                f"{where}: severity {raw!r} is not one of {', '.join(allowed)}{told}"
            )
            return otherwise
        return str(raw)

    def retired(self, raw: Any, held: set[str]) -> tuple[Retired, ...]:
        """The rules the user retired (ADR-032 ruling 13): ``{id, ruled}``
        each, an id the ruleset still declares refused."""
        if not isinstance(raw, list):
            self.problem("retired must be a list of {id, ruled}")
            return ()
        found: list[Retired] = []
        for position, item in enumerate(raw, start=1):
            at = f"retired[{position}]"
            if not isinstance(item, Mapping):
                self.problem(f"{at} must be a mapping with id and ruled")
                continue
            self.unknown(at, item, frozenset({"id", "ruled"}))
            absent = [key for key in ("id", "ruled") if key not in item]
            if absent:
                self.problem(f"{at}: missing {', '.join(absent)}; a retired rule carries "
                             f"the user's ruled: {{by, date, reason}}")
                continue
            rule_id = str(item["id"])
            if rule_id in held:
                self.problem(
                    f"{at}: {rule_id} is retired and declared both; a retired rule is "
                    f"removed from rules"
                )
            if any(r.id == rule_id for r in found):
                self.problem(f"{at}: {rule_id} is retired twice")
            ruled = self.ruling(f"{at} ({rule_id})", item["ruled"])
            if ruled is not None:
                found.append(Retired(rule_id, ruled))
        return tuple(found)

    # -- version 2's keys (ADR-032 D7, D8) ------------------------------------------

    def owner(self, where: str, raw: Any) -> str:
        if raw not in OWNERS:
            self.problem(f"{where}: owner {raw!r} is not one of {', '.join(OWNERS)}")
            return "project"
        return str(raw)

    def user_owned(
        self, where: str, exceptions: Iterable[GuidelineException], check: Check | None
    ) -> None:
        """ADR-032 D7's two load-time refusals on an ``owner: user`` rule."""
        for exc in exceptions:
            if exc.ruled is None:
                self.problem(
                    f"{where} is owner: user, and its exception for {exc.path} carries "
                    f"no ruled: only the user excepts such a rule, recorded as "
                    f"ruled: {{by, date, reason}} (ADR-032 D7)"
                )
        for condition in () if check is None else check.not_applicable_when:
            if condition.claim is not None:
                self.problem(
                    f"{where} is owner: user, and its N/A condition {condition.id!r} is a "
                    f"claim: an agent never answers such a rule N/A, so only a mechanical "
                    f"condition may stand (ADR-032 D7)"
                )

    def ruling(self, where: str, raw: Any) -> Ruling | None:
        at = f"{where} ruled"
        if not isinstance(raw, Mapping):
            self.problem(f"{at} must be a mapping with by, date and reason")
            return None
        self.unknown(at, raw, frozenset(_RULED_KEYS))
        absent = [key for key in _RULED_KEYS if key not in raw]
        if absent:
            self.problem(f"{at}: missing {', '.join(absent)}")
            return None
        when = raw["date"]
        if isinstance(when, date):
            when = when.isoformat()
        elif not (isinstance(when, str) and _ISO_DATE.fullmatch(when)):
            self.problem(f"{at}: date {when!r} must be a date, YYYY-MM-DD")
            when = ""
        return Ruling(by=self.text(at, raw, "by"), date=str(when),
                      reason=self.text(at, raw, "reason"))

    def origin(self, where: str, raw: Any) -> Origin | None:
        at = f"{where} origin"
        if not isinstance(raw, Mapping):
            self.problem(f"{at} must be a mapping with set, version and digest")
            return None
        self.unknown(at, raw, frozenset(_ORIGIN_KEYS))
        absent = [key for key in _ORIGIN_KEYS if key not in raw]
        if absent:
            self.problem(f"{at}: missing {', '.join(absent)}")
            return None
        version = raw["version"]
        if type(version) is not int or version < 1:
            self.problem(f"{at}: version must be a positive whole number")
        digest = raw["digest"]
        if not (isinstance(digest, str) and _FINGERPRINT.fullmatch(digest)):
            self.problem(f"{at}: digest must be twelve lowercase hex digits")
        return Origin(set=self.text(at, raw, "set"), version=version if type(version) is int
                      else 0, digest=str(digest))

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
        if kind == "boundary":
            optional = self.allowed(optional, optional | {"test"})
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
        if engine == FITNESS:
            return self.fitness(where, raw)
        if "test" in raw:
            self.problem(
                f"{where}: test belongs to engine fitness; an imports boundary "
                f"names forbid or only"
            )
        if engine in ADAPTER_ENGINES and self.engines is not None and engine not in self.engines:
            self.problem(
                f"{where}: engine {engine!r} needs an adapter that reads the import "
                f"graph, and no configured ecosystem's adapter offers one; declare "
                f"an ecosystem whose adapter does ({IMPORTS_ADAPTERS}), write the rule "
                f"as judgement, or name the project's own test with engine fitness "
                f"and test: <selector>"
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

    def fitness(self, where: str, raw: Mapping) -> Check:
        """A project's own test as the boundary's evidence (ADR-032 D3)."""
        if self.version == 1:
            self.problem(
                f"{where}: engine 'fitness' is a version 2 engine: set version: 2"
            )
        if any(key in raw for key in BOUNDARY_LISTS):
            self.problem(
                f"{where}: a fitness boundary names its test in place of forbid and only"
            )
        if "test" not in raw:
            self.problem(f"{where}: missing test, the selector of the project's own test")
            return Check(kind="boundary", engine=FITNESS)
        return Check(kind="boundary", engine=FITNESS, test=self.selector(where, raw["test"]))

    def selector(self, where: str, raw: Any) -> str | None:
        if not isinstance(raw, str) or not raw.strip():
            self.problem(f"{where}: test must be a non-empty string, a test selector")
            return None
        return raw.strip()

    # -- the layer map (ADR-032 D2, D3) -------------------------------------------------

    def layer_map(self, raw: Any) -> LayerMap | None:
        where = "layers"
        if not isinstance(raw, Mapping):
            self.problem("layers must be a mapping with map, the layers innermost first")
            return None
        self.unknown(where, raw, _LAYERS_KEYS)
        if "map" not in raw:
            self.problem("layers: missing map, the layers innermost first")
            return None
        severity = self.severity("layers", raw.get("severity", LAYER_SEVERITY), LAYER_SEVERITY)
        evidence = raw.get("evidence", IMPORTS)
        if evidence not in LAYER_EVIDENCE:
            self.problem(
                f"layers: evidence {evidence!r} is not one of {', '.join(LAYER_EVIDENCE)}"
            )
            evidence = IMPORTS
        test = None
        if evidence == FITNESS:
            if "test" in raw:
                test = self.selector(where, raw["test"])
            else:
                self.problem(
                    "layers: evidence fitness needs test, the selector of the "
                    "project's own layering test"
                )
        elif "test" in raw:
            self.problem("layers: test belongs to evidence fitness")
        owner = self.owner(where, raw.get("owner", "project"))
        layers = self.layers(raw["map"])
        if owner == "user":
            for layer in layers:
                self.user_owned(f"layers map {layer.id!r}", layer.exceptions, None)
        return LayerMap(
            layers=layers,
            severity=str(severity),
            phases=self.phases(where, raw.get("phases", list(LAYER_PHASES))),
            evidence=str(evidence),
            test=test,
            owner=owner,
            ruled=self.ruling(where, raw["ruled"]) if "ruled" in raw else None,
            origin=self.origin(where, raw["origin"]) if "origin" in raw else None,
        )

    def layers(self, raw: Any) -> tuple[Layer, ...]:
        if not isinstance(raw, list):
            self.problem("layers: map must be a list of layers, innermost first")
            return ()
        if not raw:
            self.problem("layers: map names no layer")
        found: list[Layer] = []
        for position, item in enumerate(raw, start=1):
            at = f"layers map[{position}]"
            if not isinstance(item, Mapping):
                self.problem(f"{at} must be a mapping with id, title and paths")
                continue
            layer_id = item.get("id")
            if not isinstance(layer_id, str) or not _NO_WHITESPACE.fullmatch(layer_id):
                # It is a rule id's middle part, so no whitespace (D6).
                self.problem(f"{at}: id must be a non-empty string with no whitespace")
                continue
            at = f"layers map {layer_id!r}"
            if any(layer.id == layer_id for layer in found):
                self.problem(f"{at} is declared twice")
                continue
            self.unknown(at, item, frozenset(_LAYER_REQUIRED) | _LAYER_OPTIONAL)
            title = self.text(at, item, "title")
            if title and any(layer.title == title for layer in found):
                # A finding names layers by title, so two would read as one.
                self.problem(f"{at}: title {title!r} is another layer's title too")
            if "paths" not in item:
                self.problem(f"{at}: missing paths (an empty list is a layer not mapped yet)")
                continue
            found.append(Layer(
                id=layer_id,
                title=title,
                paths=self.project_globs(at, "paths", item["paths"], empty_ok=True),
                exclude=self.project_globs(at, "exclude", item.get("exclude", []), empty_ok=True),
                exceptions=self.exceptions(at, item.get("exceptions", [])),
            ))
        return tuple(found)

    def derived(self, layer_map: LayerMap, declared: tuple[Guideline, ...]) -> tuple[Guideline, ...]:
        """One rule per layer that maps files and has an outer layer that does."""
        found: list[Guideline] = []
        for index, layer in enumerate(layer_map.layers):
            outer = [o for o in layer_map.layers[index + 1 :] if o.paths]
            if layer.paths and outer:
                found.append(_derived_rule(layer_map, layer, outer))
        if (
            found
            and layer_map.evidence == IMPORTS
            and self.engines is not None
            and IMPORTS not in self.engines
        ):
            self.problem(
                "layers: evidence imports needs an adapter that reads the import graph, "
                "and no configured ecosystem's adapter offers one; set layers.evidence: "
                "judgement, or evidence: fitness with layers.test naming the project's "
                f"own layering test, or declare an ecosystem whose adapter does "
                f"({IMPORTS_ADAPTERS})"
            )
        taken = {needs_reader.id_stem(rule.id): rule.id for rule in declared}
        kept: list[Guideline] = []
        for rule in found:
            twin = taken.get(needs_reader.id_stem(rule.id))
            if twin is not None:
                self.problem(
                    f"rule {rule.id!r} is derived from the layer map, and the declared "
                    f"rule {twin!r} has the same id or need id; rename the declared rule "
                    f"or the layer"
                )
                continue
            kept.append(rule)
        return tuple(kept)

    # -- the pattern catalogue (ADR-032 D5) ---------------------------------------------

    def catalogue(self, raw: Any) -> Catalogue | None:
        if (
            not isinstance(raw, str)
            or not raw.strip()
            or _GLOB_CHARS & set(raw)
            or posixpath.isabs(raw)
        ):
            self.problem(
                f"catalogue {raw!r} must name a file beside the ruleset, such as patterns.yaml"
            )
            return None
        if self.location is None:
            self.problem(
                f"catalogue {raw} cannot be read: the ruleset's own path was not given, "
                f"and the catalogue is named from its folder"
            )
            return None
        rel = project_relative(posixpath.join(posixpath.dirname(self.location), raw))
        if rel in (None, "."):
            self.problem(f"catalogue {raw!r} names nothing inside the project")
            return None
        data = self.file_bytes("the ruleset", "catalogue", rel)
        if data is None:
            return None
        try:
            document = yaml.safe_load(data.decode("utf-8"))
        except UnicodeDecodeError as exc:
            self.problem(f"catalogue {rel} could not be read: {exc}")
            return None
        except yaml.YAMLError as exc:
            self.problem(f"catalogue {rel} is not valid YAML: {exc}")
            return None
        before = len(self.problems)
        patterns = self.catalogue_document(rel, document)
        if len(self.problems) > before:
            return None
        return Catalogue(path=rel, version=CATALOGUE_VERSION, patterns=patterns,
                         digest=digest_of(data))

    def catalogue_document(self, rel: str, raw: Any) -> tuple[Pattern, ...]:
        where = f"catalogue {rel}"
        if not isinstance(raw, Mapping):
            self.problem(f"{where} must be a mapping with version and patterns")
            return ()
        self.unknown(where, raw, _CATALOGUE_TOP_KEYS)
        missing = sorted(_CATALOGUE_TOP_KEYS - set(raw))
        if missing:
            self.problem(f"{where} is missing {', '.join(missing)}")
        if "version" in raw and raw["version"] != CATALOGUE_VERSION:
            self.problem(
                f"{where} is version {raw['version']!r}; this build reads version "
                f"{CATALOGUE_VERSION}"
            )
        entries = raw.get("patterns", [])
        if not isinstance(entries, list):
            self.problem(f"{where}: patterns must be a list")
            return ()
        found: list[Pattern] = []
        for position, item in enumerate(entries, start=1):
            entry = self.catalogue_entry(where, position, item, found)
            if entry is not None:
                found.append(entry)
        return tuple(found)

    def catalogue_entry(
        self, where: str, position: int, item: Any, found: list[Pattern]
    ) -> Pattern | None:
        if not isinstance(item, Mapping):
            self.problem(f"{where} patterns[{position}] must be a mapping")
            return None
        pattern_id = item.get("id")
        if not isinstance(pattern_id, str) or not _NO_WHITESPACE.fullmatch(pattern_id):
            self.problem(
                f"{where} patterns[{position}]: id must be a non-empty string with no whitespace"
            )
            return None
        at = f"{where} pattern {pattern_id!r}"
        if any(p.id == pattern_id for p in found):
            self.problem(f"{at} is declared twice; a pattern id names one entry")
            return None
        self.unknown(at, item, frozenset(_PATTERN_KEYS))
        absent = [key for key in _PATTERN_KEYS if key not in item]
        if absent:
            self.problem(f"{at}: missing {', '.join(absent)}")
            return None
        family = item["family"]
        if family not in PATTERN_FAMILIES:
            self.problem(
                f"{at}: family {family!r} is not one of {', '.join(PATTERN_FAMILIES)}"
            )
        return Pattern(
            id=pattern_id,
            name=self.text(at, item, "name"),
            family=str(family),
            intent=self.text(at, item, "intent"),
            use_when=self.lines(at, "use_when", item["use_when"]),
            avoid_when=self.lines(at, "avoid_when", item["avoid_when"]),
        )

    def lines(self, where: str, key: str, raw: Any) -> tuple[str, ...]:
        if (
            not isinstance(raw, list)
            or not raw
            or not all(isinstance(v, str) and v.strip() for v in raw)
        ):
            self.problem(f"{where}: {key} must be a non-empty list of lines")
            return ()
        return tuple(" ".join(v.split()) for v in raw)

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
                f"project, such as <docs project>/guidelines/duplication.yaml, "
                f"beside the ruleset"
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
        data = self.file_bytes(where, "registry", rel)
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

    def file_bytes(self, where: str, what: str, rel: str) -> bytes | None:
        """A file the ruleset names (a registry, the catalogue), by ``read``
        or from ``root``, or ``None`` with the problem named."""
        if self.read is not None:
            data = self.read(rel)
            if data is None:
                self.problem(f"{where}: {what} {rel} could not be read")
            return data
        if self.root is None:
            self.problem(f"{where}: {what} {rel} cannot be read: no project tree was given")
            return None
        path = Path(self.root) / rel
        if not path.is_file():
            # A missing registry is not an empty one (DN-A9), as a missing
            # ruleset is not; nor is a missing catalogue.
            self.problem(f"{where}: {what} {rel} does not exist")
            return None
        try:
            return path.read_bytes()
        except OSError as exc:
            self.problem(f"{what} {rel} could not be read: {exc}")
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

    def project_globs(
        self, where: str, key: str, raw: Any, *, empty_ok: bool = False
    ) -> tuple[str, ...]:
        """Project-relative path globs (DN-A2), each normalised as every other
        declared glob is. A bare string is refused, the AUD-08 precedent."""
        if isinstance(raw, str):
            self.problem(f"{where}: {key} must be a list of glob strings, not a bare string")
            return ()
        if not isinstance(raw, list) or not all(isinstance(g, str) and g for g in raw):
            self.problem(f"{where}: {key} must be a list of glob strings")
            return ()
        if not raw and not empty_ok:
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
            self.unknown(
                at, item,
                frozenset(_EXCEPTION_REQUIRED)
                | self.allowed(_EXCEPTION_OPTIONAL, _EXCEPTION_OPTIONAL_V2),
            )
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
                    ruled=self.ruling(at, item["ruled"]) if "ruled" in item else None,
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
        if not extra:
            return
        newer = [key for key in extra if self.version == 1 and key in self.newer]
        self.newer = frozenset()
        hint = (
            f" ({', '.join(newer)} {'is a' if len(newer) == 1 else 'are'} version 2 "
            f"key{'' if len(newer) == 1 else 's'}: set version: 2)"
            if newer else ""
        )
        self.problem(f"unknown key(s) in {where}: {', '.join(extra)}{hint}")

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
    return _marked(writer, writer.render(ruleset, source))


def managed_text(text: str, fmt: str) -> str:
    """``text`` under ``fmt``'s managed marker, as every generated page here is.

    The marker and its digest are the ones the checklist and the rules page
    carry, so :func:`split_managed` and :func:`managed_state` read a page
    another module renders (the plan page, ADR-032 increment 6) exactly as
    they read these.
    """
    return _marked(_MarkdownWriter() if _format(fmt) == "markdown" else _RstWriter(), text)


def managed_state(existing: str | None, text: str, fmt: str) -> str:
    """How a generated file on disk stands against ``text``, its fresh rendering.

    ``new`` with no file, ``unmanaged`` for a file whose first line is not the
    marker (render did not write it, so it is never overwritten), ``edited``
    when the marker's digest is not its body's, else ``current`` or ``stale``.
    """
    if existing is None:
        return "new"
    digest, body = split_managed(existing, fmt)
    if digest is None:
        return "unmanaged"
    if digest != digest_of(body.encode("utf-8")):
        return "edited"
    return "current" if existing == text else "stale"


def _marked(writer: _Writer, text: str) -> str:
    body = "\n" + text
    return writer.marker(digest_of(body.encode("utf-8"))) + "\n" + body


#: The pattern catalogue's page, beside the rules page (ADR-032 D5).
PATTERNS_PAGE = "patterns.rst"
_FAMILY_TITLES = {
    "creational": "Creational patterns",
    "structural": "Structural patterns",
    "behavioural": "Behavioural patterns",
    "enterprise": "Enterprise patterns",
    "domain": "Domain patterns",
}


def render_patterns(catalogue: Catalogue | None, *, source: str | None) -> str:
    """The pattern catalogue as an rST page, managed marker first (ADR-032 D5).

    One section per family, and per pattern a label holding its id, so a
    design names a pattern by id and a verdict cites ``patterns.rst#<id>``.
    With no catalogue, a page saying how to get one: the docs starter's
    toctree names the page before any catalogue need exist.
    """
    writer = _RstWriter()
    lines = writer.heading("Design patterns", 1)
    if catalogue is None or source is None:
        lines.append(
            "No pattern catalogue is configured, so there are no patterns yet. Name "
            "one with the ruleset's ``catalogue`` key, then run ``invariant "
            "guidelines render``: it writes this page from the catalogue."
        )
        return _marked(writer, "\n".join(lines) + "\n")
    lines += [
        (
            f"Generated by ``invariant guidelines render`` from ``{source}`` (catalogue "
            f"{catalogue.digest}). Do not edit this file: change the catalogue, then "
            f"render. A design names the pattern a new abstraction applies by its id, "
            f"or says why none fits."
        ),
        "",
    ]
    for family in PATTERN_FAMILIES:
        entries = [entry for entry in catalogue.patterns if entry.family == family]
        if not entries:
            continue
        lines += writer.heading(_FAMILY_TITLES[family], 2)
        for entry in entries:
            lines += [f".. _{entry.id}:", ""]
            lines += writer.heading(entry.name, 3)
            for paragraph in _paragraphs(entry.intent):
                lines += [paragraph, ""]
            lines += [f"- Id: ``{entry.id}``", "", "Use when:", ""]
            lines += [f"- {line}" for line in entry.use_when]
            lines += ["", "Avoid when:", ""]
            lines += [f"- {line}" for line in entry.avoid_when]
            lines.append("")
    return _marked(writer, "\n".join(lines).rstrip("\n") + "\n")


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
        if check.engine == FITNESS:
            return (
                f"boundary (fitness): the test {code(check.test or '')} passes on a run "
                f"recorded over the change"
            )
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
        applies = ", ".join(self.code(g) for g in rule.applies_to)
        if rule.exclude:
            applies += f"; except {', '.join(self.code(g) for g in rule.exclude)}"
        lines += [
            f"- Severity: {rule.severity}",
            f"- Phases: {', '.join(rule.phases)}",
            f"- Applies to: {applies}",
            f"- Check: {_check_summary(rule.check, self.code)}",
        ]
        if rule.layer is not None and rule.layer_map is not None:
            lines.append(
                f"- Derived from the layer map (layer "
                f"{rule.layer_map.title_of(rule.layer)}): change the map, not this rule"
            )
        if rule.owner == "user":
            lines.append(
                "- Owner: user (only the user excepts, loosens or rewords it; an "
                "agent proposes)"
            )
        if rule.ruled is not None:
            lines.append(
                f"- Ruled: by {rule.ruled.by} on {rule.ruled.date}: "
                f"{' '.join(rule.ruled.reason.split())}"
            )
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


def _within(rel: str, globs: Iterable[str], exclude: Iterable[str]) -> bool:
    return any(matches(rel, glob) for glob in globs) and not any(
        matches(rel, glob) for glob in exclude
    )


def covers(rule: Guideline, rel: str) -> bool:
    """Is ``rel`` under the rule's ``applies_to`` and outside its ``exclude``?

    The one reading of a rule's file scope (ADR-032 D2): the evaluators, the
    baseline scans and :func:`meets` ask it. Only a rule derived from a
    layer with ``exclude`` has any exclusion.
    """
    return _within(rel, rule.applies_to, rule.exclude)


def meets(rule: Guideline, rel: str) -> bool:
    """Does ``rel`` bring the rule into play (A4's DN-3)?

    ``rel`` is covered by the rule (:func:`covers`) and not set aside by a
    path exception. The one reading: the findings, the verdicts owed and the
    selection all ask it.
    """
    if not covers(rule, rel):
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
#: Why a fitness boundary is never baselined (ADR-032 D3), said wherever an
#: entry or a scan names one.
FITNESS_NOT_BASELINED = (
    "fitness boundary rule, which invariant does not baseline: its finding has "
    "no fingerprint, and the test keeps its own ratchet (ADR-032 D3)"
)


def baselined(rule: Guideline) -> bool:
    """Can a baseline hold this rule's findings: a pattern or an ``imports``
    boundary, never a ``fitness`` one, nor a cochange or a judgement?"""
    return rule.check.kind in BASELINE_KINDS and rule.check.engine != FITNESS

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
        rule = ruleset.any_rule(entry.rule)
        if rule is None:
            problems.append(f"{where}: the ruleset has no rule {entry.rule!r}")
        elif rule.check.kind == "boundary" and not baselined(rule):
            problems.append(f"{where}: {entry.rule} is a {FITNESS_NOT_BASELINED}")
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

    Its ``applies_to`` (with a derived rule's ``exclude``), its ``check`` and
    its ``exceptions``, read from the
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
    return (
        tuple(sorted({_spelled(g) for g in rule.applies_to})),
        tuple(sorted({_spelled(g) for g in rule.exclude})),
        check,
        tuple(exceptions),
    )


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
