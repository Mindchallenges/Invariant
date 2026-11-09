"""The docs project's needs (ADR-018 D2, D3, D5, D6).

What the gates key on is a **role**, never a prefix (ruling 11). A role is a
fixed engine concept; a type is the project's own sphinx-needs directive,
mapped to one role or to none (an extension type). ``docs.needs.types`` holds
the map, and the defaults are the seven baseline types.

This module is pure apart from reading a file it is handed:

- the roles, the types and their load-time check (:func:`type_problems`,
  which ``core/manifest`` calls), the required-link rules
  (:func:`link_rule_problems`), and the docs project's source directory
  (:func:`resolve_srcdir`, the increment-2 rule as the default of
  ``docs.needs.srcdir``);
- a built ``needs.json`` read into :class:`NeedsFile` (sphinx-needs 8.x and
  9.x write the same shape: ``versions`` → the current version → ``needs``),
  and checked against the types, the link rules and the decision statuses
  (:func:`check`), each problem named by need, file and line;
- the strict build's record (:class:`BuildRecord`): where it lives under the
  state root, keyed by the digest of the source directory's tree
  (:func:`tree_digest`), and reading it back. ``domains/docs`` runs the build
  and writes the record; the gates in ``core/rules`` only read it.

It never runs a build and never parses rST: ``needs.json`` is what
sphinx-needs itself resolved, so a gate reading it cannot disagree with the
build (ADR-018, "Alternatives rejected").
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import InvariantError

#: The seven roles (ruling 11), in the order the ADR's table gives them.
ROLES = ("requirement", "decision", "element", "ui", "rule", "idea", "bug")
#: The roles whose one type is an item type the items backend reads and mints.
ITEM_ROLES = ("idea", "bug")

#: Each role's default type: (directive, prefix). The item roles' prefixes
#: are what the items backend mints, so they follow ``items.id_prefix``.
_DEFAULT_TYPES = (
    ("requirement", "req", "REQ_"),
    ("decision", "dec", "DEC_"),
    ("element", "comp", "COMP_"),
    ("ui", "ui", "UI_"),
    ("rule", "rule", "RULE_"),
    ("idea", "idea", "IDEA_"),
    ("bug", "bug", "BUG_"),
)

DEFAULT_DECISION_STATUSES = ("proposed", "accepted", "superseded", "rejected")
DEFAULT_REQUIRE_LINKS = (
    {"from": "decision", "to": "element"},
    {"from": "bug", "to": "element"},
)

#: The two states of a project (D10).
STATES = ("migrating", "enforced")

PREFIX = re.compile(r"^[A-Z][A-Z0-9_]*_$")
#: A need id: a prefix (:data:`PREFIX`) and at least one more character. The
#: starter's ``needs_id_regex`` is this pattern, and it is how a ruleset's
#: ``links`` and ``amended[].adr`` tell a need id from a path (D12).
NEED_ID = re.compile(r"^[A-Z][A-Z0-9_]*_[A-Z0-9_]+$")
_NOT_ALNUM = re.compile(r"[^A-Za-z0-9]")
_TYPE_KEYS = ("directive", "prefix", "role")
_LINK_KEYS = ("from", "to", "via")


class NeedsJsonError(InvariantError):
    """A file that is not a readable needs.json."""

    exit_code = 1


# -- roles and types --------------------------------------------------------


@dataclass(frozen=True)
class NeedType:
    directive: str
    prefix: str
    role: str | None = None

    def to_dict(self) -> dict[str, str]:
        body = {"directive": self.directive, "prefix": self.prefix}
        if self.role is not None:
            body["role"] = self.role
        return body


def item_prefix(role: str, id_prefix: str = "") -> str:
    """What the items backend mints for an item role: ``BUG_``, ``HEAP_BUG_``."""
    return f"{id_prefix or ''}{role.upper()}_"


def default_types(id_prefix: str = "") -> tuple[NeedType, ...]:
    """The seven baseline types, the item types following ``items.id_prefix``."""
    return tuple(
        NeedType(
            directive,
            item_prefix(role, id_prefix) if role in ITEM_ROLES else prefix,
            role,
        )
        for role, directive, prefix in _DEFAULT_TYPES
    )


def type_problems(raw: Any, id_prefix: str = "") -> list[str]:
    """Every reason ``raw`` (a ``docs.needs.types`` value) is refused (D2)."""
    if not isinstance(raw, list):
        return [f"must be a list of {{directive, prefix, role}}, got {raw!r}"]
    problems: list[str] = []
    directives: Counter[str] = Counter()
    prefixes: Counter[str] = Counter()
    by_role: dict[str, list[dict]] = {}
    for entry in raw:
        if not isinstance(entry, Mapping):
            problems.append(f"entry {entry!r} must be a mapping of {', '.join(_TYPE_KEYS)}")
            continue
        unknown = sorted(str(k) for k in entry if k not in _TYPE_KEYS)
        if unknown:
            problems.append(
                f"entry {dict(entry)!r} has unknown key(s) "
                + ", ".join(repr(k) for k in unknown)
                + f"; the keys are {', '.join(_TYPE_KEYS)}"
            )
        directive, prefix, role = entry.get("directive"), entry.get("prefix"), entry.get("role")
        if not isinstance(directive, str) or not directive.strip():
            problems.append(f"entry {dict(entry)!r} needs a directive")
        else:
            directives[directive] += 1
        if not isinstance(prefix, str) or not PREFIX.match(prefix):
            problems.append(
                f"entry {dict(entry)!r}: prefix {prefix!r} does not match {PREFIX.pattern}"
            )
        else:
            prefixes[prefix] += 1
        if role is not None:
            if role not in ROLES:
                problems.append(
                    f"entry {dict(entry)!r} names unknown role {role!r}; the roles are "
                    + ", ".join(ROLES)
                )
            else:
                by_role.setdefault(role, []).append(dict(entry))
    problems += [f"directive {d!r} is used twice" for d, n in directives.items() if n > 1]
    problems += [f"prefix {p!r} is used twice" for p, n in prefixes.items() if n > 1]
    for role in ROLES:
        mapped = by_role.get(role, [])
        if not mapped:
            problems.append(f"role {role!r} has no type; map at least one directive to it")
            continue
        if role not in ITEM_ROLES:
            continue
        wanted = item_prefix(role, id_prefix)
        if len(mapped) != 1 or mapped[0].get("directive") != role:
            problems.append(
                f"role {role!r} takes exactly one type, the directive {role!r} the "
                f"items backend reads and mints; got "
                + ", ".join(repr(entry.get("directive")) for entry in mapped)
            )
        elif mapped[0].get("prefix") != wanted:
            problems.append(
                f"role {role!r}: prefix {mapped[0].get('prefix')!r} differs from what "
                f"the items backend mints, {wanted!r} (items.id_prefix "
                f"{id_prefix or ''!r} followed by {role.upper()}_)"
            )
    return problems


def parse_types(raw: Sequence[Mapping[str, Any]]) -> tuple[NeedType, ...]:
    """``raw`` as types; :func:`type_problems` has already refused a bad one."""
    return tuple(
        NeedType(str(entry["directive"]), str(entry["prefix"]), entry.get("role"))
        for entry in raw
    )


def types_of(manifest: Any) -> tuple[NeedType, ...]:
    """The project's types: ``docs.needs.types`` when set, else the defaults."""
    raw = manifest.get("docs.needs.types")
    if raw is None:
        return default_types(str(manifest.get("items.id_prefix") or ""))
    return parse_types(raw)


def type_of_role(types: Sequence[NeedType], role: str) -> NeedType:
    """The first type mapped to ``role``: the one a generated need is written
    as. Every role has one, which :func:`type_problems` makes sure of."""
    found = next((t for t in types if t.role == role), None)
    if found is None:
        raise ValueError(f"no type is mapped to the role {role!r}")
    return found


def is_need_id(text: str) -> bool:
    return bool(NEED_ID.match(text))


def id_stem(text: str) -> str:
    """``text`` as the part of a need id after its prefix: upper case, every
    character that is not a letter or a digit as ``_`` (D12)."""
    return _NOT_ALNUM.sub("_", text).upper()


# -- required links -----------------------------------------------------------


@dataclass(frozen=True)
class LinkRule:
    source: str
    target: str
    via: tuple[str, ...] = ()

    def render(self) -> str:
        through = f" via {', '.join(self.via)}" if self.via else ""
        return f"{self.source} -> {self.target}{through}"


def link_rule_problems(raw: Any) -> list[str]:
    """Every reason ``raw`` (a ``docs.needs.require_links`` value) is refused."""
    if not isinstance(raw, list):
        return [f"must be a list of {{from, to, via}}, got {raw!r}"]
    problems: list[str] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            problems.append(f"entry {entry!r} must be a mapping of {', '.join(_LINK_KEYS)}")
            continue
        unknown = sorted(str(k) for k in entry if k not in _LINK_KEYS)
        if unknown:
            problems.append(
                f"entry {dict(entry)!r} has unknown key(s) "
                + ", ".join(repr(k) for k in unknown)
            )
        for key in ("from", "to"):
            role = entry.get(key)
            if role is None:
                problems.append(f"entry {dict(entry)!r} needs {key!r}, a role")
            elif role not in ROLES:
                problems.append(
                    f"entry {dict(entry)!r} names unknown role {role!r}; the roles are "
                    + ", ".join(ROLES)
                )
        via = entry.get("via")
        if via is not None and (
            not isinstance(via, list)
            or not via
            or not all(isinstance(v, str) and v.strip() for v in via)
        ):
            problems.append(
                f"entry {dict(entry)!r}: via must be a non-empty list of link options"
            )
    return problems


def parse_link_rules(raw: Iterable[Mapping[str, Any]]) -> tuple[LinkRule, ...]:
    return tuple(
        LinkRule(str(entry["from"]), str(entry["to"]), tuple(entry.get("via") or ()))
        for entry in raw
    )


# -- the source directory (D3) --------------------------------------------------


def resolve_srcdir(base: Path, configured: str | None, docs: str | None) -> Path | None:
    """The docs project's source directory under ``base``, or ``None``.

    ``configured`` (``docs.needs.srcdir``) wins, read against ``base`` unless
    absolute. Otherwise the increment-2 rule (ADR-018 D3, as built): the
    nearest directory at or above ``base / docs`` holding a ``conf.py``,
    strictly below ``base``.
    """
    from ..adapters.sphinx import docs_project_srcdir  # local: adapters import core

    base = Path(base)
    if configured is not None:
        return (base / configured).resolve()
    if docs is None:
        return None
    found = docs_project_srcdir(base, docs)
    return None if found is None else found.resolve()


def srcdir_of(manifest: Any, tree: Path | None = None) -> Path | None:
    """The docs project's source directory, read against ``tree`` when given
    (a session's worktree) and the manifest's root otherwise.

    A ``records.docs: store`` path is already absolute by load, so it names
    the store whichever tree is asked.
    """
    from .manifest import project_relative  # local: manifest calls back here

    base = Path(tree) if tree is not None else Path(manifest.root)
    configured = manifest.get("docs.needs.srcdir")
    if configured is not None:
        relative = project_relative(configured, [manifest.root])
        configured = str(configured) if relative is None else relative
    docs = project_relative(manifest.get("paths.docs"), [manifest.root])
    return resolve_srcdir(base, configured, docs)


# -- needs.json -------------------------------------------------------------------


@dataclass(frozen=True)
class Need:
    id: str
    type: str
    docname: str
    doctype: str
    lineno: int | None
    status: str | None
    links: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    external: bool = False

    def where(self, prefix: str = "") -> str:
        place = f"{prefix}{self.docname}{self.doctype}" if self.docname else "?"
        return f"{place}:{self.lineno}" if self.lineno is not None else place

    def linked(self, options: Iterable[str] | None = None) -> set[str]:
        chosen = self.links if options is None else {o: self.links.get(o, ()) for o in options}
        return {target for targets in chosen.values() for target in targets}


@dataclass(frozen=True)
class NeedsFile:
    needs: tuple[Need, ...]
    link_options: tuple[str, ...]

    def by_id(self) -> dict[str, Need]:
        return {n.id: n for n in self.needs}


def parse_needs_json(data: Any) -> NeedsFile:
    """The current version's needs, from a needs.json document."""
    versions = data.get("versions") if isinstance(data, Mapping) else None
    if not isinstance(versions, Mapping) or not versions:
        raise NeedsJsonError("not a needs.json: no 'versions'")
    current = data.get("current_version")
    version = versions.get(current) if current in versions else list(versions.values())[-1]
    if not isinstance(version, Mapping) or not isinstance(version.get("needs"), Mapping):
        raise NeedsJsonError("not a needs.json: the version holds no 'needs'")
    raw_needs = version["needs"]
    properties = (version.get("needs_schema") or {}).get("properties") or {}
    options = sorted(
        name for name, spec in properties.items()
        if isinstance(spec, Mapping) and spec.get("field_type") == "links"
    )
    if not options:
        keys = {key for body in raw_needs.values() if isinstance(body, Mapping) for key in body}
        options = sorted(
            key for key in keys if not key.endswith("_back") and f"{key}_back" in keys
        )
    parsed = []
    for need_id, body in sorted(raw_needs.items()):
        if not isinstance(body, Mapping):
            continue
        links = {
            option: tuple(str(t) for t in body.get(option) or ())
            for option in options
            if body.get(option)
        }
        lineno = body.get("lineno")
        parsed.append(
            Need(
                id=str(body.get("id") or need_id),
                type=str(body.get("type") or ""),
                docname=str(body.get("docname") or ""),
                doctype=str(body.get("doctype") or ".rst"),
                lineno=lineno if isinstance(lineno, int) else None,
                status=body.get("status") if isinstance(body.get("status"), str) else None,
                links=links,
                external=bool(body.get("is_external")),
            )
        )
    return NeedsFile(needs=tuple(parsed), link_options=tuple(options))


def load_needs_json(path: Path) -> NeedsFile:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise NeedsJsonError(f"cannot read needs.json at {path}: {exc}") from exc
    return parse_needs_json(data)


# -- traceability (D6) ------------------------------------------------------------


@dataclass(frozen=True)
class Problem:
    need: str
    where: str
    message: str

    def render(self) -> str:
        return f"{self.where} {self.need}: {self.message}"


def check(
    read: NeedsFile,
    types: Sequence[NeedType],
    rules: Sequence[LinkRule],
    decision_statuses: Sequence[str],
    *,
    prefix: str = "",
) -> list[Problem]:
    """Every need the project's types, link rules and statuses do not hold.

    An external need is another project's and held to nothing here. A role
    with no needs is not a failure (D6 item 2). ``prefix`` spells each file
    from the project: the source directory's project-relative path.
    """
    by_directive = {t.directive: t for t in types}
    roles = {
        n.id: by_directive[n.type].role for n in read.needs if n.type in by_directive
    }
    problems: list[Problem] = []
    for item in read.needs:
        if item.external:
            continue
        where = item.where(prefix)
        declared = by_directive.get(item.type)
        if declared is None:
            problems.append(
                Problem(
                    item.id,
                    where,
                    f"directive {item.type!r} is not declared in docs.needs.types; "
                    "map it to a role, or declare it with no role",
                )
            )
            continue
        if not item.id.startswith(declared.prefix):
            problems.append(
                Problem(
                    item.id,
                    where,
                    f"its id does not start with {declared.directive}'s prefix "
                    f"{declared.prefix!r}",
                )
            )
        if declared.role == "decision":
            if item.status is None:
                problems.append(
                    Problem(item.id, where, "a decision with no status; one of "
                            + ", ".join(decision_statuses))
                )
            elif item.status not in decision_statuses:
                problems.append(
                    Problem(
                        item.id,
                        where,
                        f"status {item.status!r} is not one of "
                        + ", ".join(decision_statuses)
                        + " (docs.needs.decision_statuses)",
                    )
                )
        for rule in rules:
            if declared.role != rule.source:
                continue
            targets = item.linked(rule.via or None)
            if not any(roles.get(target) == rule.target for target in targets):
                problems.append(
                    Problem(
                        item.id,
                        where,
                        f"links to no {rule.target} need (require_links: {rule.render()})",
                    )
                )
    return problems


@dataclass(frozen=True)
class Classified:
    by_role: Mapping[str, int]
    by_type: Mapping[str, int]
    unmapped: Mapping[str, int]


def classify(read: NeedsFile, types: Sequence[NeedType]) -> Classified:
    """How many of this project's needs each role and each type holds."""
    by_directive = {t.directive: t for t in types}
    by_role: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    unmapped: Counter[str] = Counter()
    for item in read.needs:
        if item.external:
            continue
        declared = by_directive.get(item.type)
        if declared is None:
            unmapped[item.type] += 1
            continue
        by_type[item.type] += 1
        if declared.role is not None:
            by_role[declared.role] += 1
    return Classified(dict(by_role), dict(by_type), dict(unmapped))


# -- the build record (D5) -------------------------------------------------------


#: Under the state root: one record per source tree digest.
RECORDS_DIR = Path("docs") / "builds"
#: Directories of a source directory that are never part of its tree.
_UNTREED = frozenset({"_build", "__pycache__"})
_RECORD_VERSION = 1


def tree_digest(srcdir: Path) -> str:
    """The digest of the source directory's tree: every file's path and bytes.

    Hidden directories (the environment ``.venv``) and ``_build`` are not part
    of it: the environment is checked through ``requirements.txt``'s hash,
    which is in the tree, and build output is never an input.
    """
    srcdir = Path(srcdir)
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(srcdir):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in _UNTREED)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            try:
                content = path.read_bytes()
            except OSError:
                continue
            relative = path.relative_to(srcdir).as_posix()
            digest.update(relative.encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(content).hexdigest().encode("ascii") + b"\n")
    return digest.hexdigest()


def short(digest: str) -> str:
    return digest[:12]


@dataclass(frozen=True)
class BuildRecord:
    digest: str
    passed: bool
    exit_code: int
    diagnostics: tuple[str, ...]
    needs_json: str | None
    srcdir: str
    sphinx_build: str
    command: str
    documents: int | None
    at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": _RECORD_VERSION,
            "digest": self.digest,
            "passed": self.passed,
            "exit_code": self.exit_code,
            "diagnostics": list(self.diagnostics),
            "needs_json": self.needs_json,
            "srcdir": self.srcdir,
            "sphinx_build": self.sphinx_build,
            "command": self.command,
            "documents": self.documents,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> BuildRecord:
        return cls(
            digest=str(raw["digest"]),
            passed=bool(raw["passed"]),
            exit_code=int(raw["exit_code"]),
            diagnostics=tuple(str(d) for d in raw.get("diagnostics") or ()),
            needs_json=raw.get("needs_json"),
            srcdir=str(raw.get("srcdir") or ""),
            sphinx_build=str(raw.get("sphinx_build") or ""),
            command=str(raw.get("command") or ""),
            documents=raw.get("documents"),
            at=str(raw.get("at") or ""),
        )


def record_relpath(digest: str) -> str:
    """The record's path under the state root."""
    return (RECORDS_DIR / f"{digest}.json").as_posix()


def needs_copy_relpath(digest: str) -> str:
    """Where the build's needs.json is kept beside its record."""
    return (RECORDS_DIR / f"{digest}.needs.json").as_posix()


def read_record(state_root: Path, digest: str) -> BuildRecord | None:
    """The record ``docs build`` wrote for ``digest``, or ``None``."""
    path = Path(state_root) / record_relpath(digest)
    try:
        return BuildRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError):
        return None
