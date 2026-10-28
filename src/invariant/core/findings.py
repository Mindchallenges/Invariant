"""Mechanical findings: the ``pattern``, ``boundary`` and ``cochange`` evaluators.

ADR-012 D4, D5 and D6, and the A3 rulings (DN-2 to DN-4, DN-13). A finding is
one place in a changed file where a mechanical guideline is broken, one
changed file whose companion change is missing, or one file a guideline
covers that nothing could answer for. ``core/rules`` asks this module through
``guidelines.mechanical_clean``, and prints what it answers in D5's record
shape. Nothing here is persisted (DN-16).

Pure. The file bytes, the lexical mask, the references and what git knows
about the change arrive as callables, so this module does no file read and no
``git`` call: ``core/rules`` does every one of them, from the tree in hand,
and decides which files changed (D4, ``_changed_set``). It imports the
ruleset model it evaluates and the manifest's one spelling of a declared
path, and nothing else of the engine's.

- **pattern** (DN-2). Each line of the masked text is matched, one finding per
  matching line. The mask is the region the rule's ``in`` names, through the
  adapter's lexical mask; ``any`` is the raw text and asks no mask. The
  fingerprint is taken from the RAW line, so a match inside a line is the
  same finding however the rest of the line is masked.
- **boundary** (DN-A1 to DN-A3, DN-3). Each reference a changed file under
  ``applies_to`` makes is checked against the rule's ``forbid`` deny-list or
  its allow-only ``only`` list of project path globs.
- **cochange** (DN-A4 to DN-A9). A ``pair`` triggers on a changed file under
  ``applies_to`` (``any``, ``added`` or ``executable``, narrowed by an added
  line matching ``when.match``), and is one finding when what ``then`` names
  did not change with it. A ``registry`` is one finding per open entry, as
  the registry stands after the change, listing a changed file. A cochange
  finding describes the change, not the tree: it has no line and no
  fingerprint, so no baseline can hold it (DN-4).

Unanswerable is a failing finding, never a pass: a mask or references answer
of ``None``, a file that could not be read, a mask that did not keep the
file's lines, and any fact about the change git or an adapter could not give.
A file that does not decode as UTF-8 is not text: the pattern kind does not
apply to it, nor does a cochange ``when.match``, and the evaluation names it,
so the verdict can.

The ratchet (D6, A3 increment 5) is :func:`ratchet`: a finding a baseline
entry records is ``baselined``, never counted, and an entry of a touched file
that no finding holds is stale. ``core/rules`` reads the baseline and hands
the entries in; this module only does the arithmetic.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from collections.abc import Callable, Container, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .guidelines import (
    DIGEST_LENGTH,
    BaselineEntry,
    Guideline,
    GuidelineException,
    Registry,
    Requirement,
    Trigger,
)
from .manifest import glob_matches, project_relative

#: The kinds this module evaluates: every mechanical kind (D4). ``engine`` is
#: its registered rule's, and ``judgement`` is a verdict's (A4).
EVALUATED_KINDS = ("pattern", "boundary", "cochange")

#: What a finding can be. ``new`` and ``unanswerable`` count against the
#: rule's severity; ``excepted`` is a reasoned exception that applied, named
#: and never counted (D4); ``baselined`` is a finding the baseline records as
#: pre-existing, named and never counted (D6).
STATUSES = ("new", "unanswerable", "excepted", "baselined")
_COUNTED = frozenset({"new", "unanswerable"})

#: The separator between the parts a fingerprint hashes. No rule id, line or
#: path holds it, so no two different (rule, key) pairs hash the same text.
_SEPARATOR = "\x00"


class ReferenceLike(Protocol):
    """What an adapter's ``references`` answer carries for one import."""

    target: str
    line: int
    text: str


#: ``read(rel)``: the file's bytes, or ``None`` when it could not be read.
Read = Callable[[str], "bytes | None"]
#: ``mask(rel, text, keep)``: the text with every region outside ``keep``
#: blanked in place, or ``None`` when no adapter can mask the file.
Mask = Callable[[str, str, str], "str | None"]
#: ``references(rel)``: every project file the file imports, or ``None``
#: when no adapter can answer for it.
References = Callable[[str], "Iterable[ReferenceLike] | None"]


def collapse(text: str) -> str:
    """A line with its whitespace collapsed: runs to one space, ends trimmed."""
    return " ".join(text.split())


def fingerprint(rule_id: str, *key: str) -> str:
    """The first twelve hex digits of SHA-256 over the rule id and a key.

    The key is kind-specific and never a line number (D6): a pattern's is
    the matched raw line, collapsed; a boundary's is the referencing line,
    collapsed, and the target path (DN-3). A2's exception fingerprints are
    this function's answers, so an exception names a finding exactly.
    """
    text = _SEPARATOR.join((rule_id, *key))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def matches(rel: str, glob: str) -> bool:
    """Does ``rel`` fall under a glob as the ruleset wrote it?

    The glob is put in its canonical spelling first, and one that names
    nothing inside the project matches nothing.
    """
    canonical = project_relative(glob)
    if canonical in (None, "."):
        return False
    return glob_matches(rel, canonical)


@dataclass(frozen=True)
class Finding:
    """One finding of a mechanical guideline in one changed file."""

    rule: str
    kind: str
    severity: str
    path: str
    status: str
    line: int | None = None
    fingerprint: str | None = None
    #: The referenced file, for a boundary finding.
    target: str | None = None
    #: Why a file is unanswerable, what a cochange owes, or the reason an
    #: exception gives.
    note: str = ""
    #: What to do about it, when the guideline says more than the gate's fix.
    fix: str = ""

    @property
    def counts(self) -> bool:
        """New or unanswerable: it is reported against the rule's severity."""
        return self.status in _COUNTED

    @property
    def blocks(self) -> bool:
        """A counted finding of a blocker rule, which fails the family gate."""
        return self.counts and self.severity == "blocker"

    def describe(self) -> str:
        if self.status == "unanswerable":
            return f"{self.rule} {self.path}: unanswerable ({self.note})"
        where = self.path if self.line is None else f"{self.path}:{self.line}"
        target = f" -> {self.target}" if self.target else ""
        return f"{self.rule} {where}{target}"

    def record(self, ruleset: str) -> dict[str, Any]:
        """The finding in D5's record shape: rule, status, cited lines, hash.

        The citation is computed by the engine, so it carries no blob: a
        finding is computed afresh at every evaluation and kept nowhere, so
        nothing can go stale against it.
        """
        lines = None if self.line is None else [self.line, self.line]
        return {
            "rule": self.rule,
            "kind": self.kind,
            "severity": self.severity,
            "status": "fail" if self.counts else "pass",
            "finding": self.status,
            "citations": [{"path": self.path, "lines": lines}],
            "fingerprint": self.fingerprint,
            "target": self.target,
            "note": self.note,
            "fix": self.fix,
            "ruleset": ruleset,
        }


@dataclass(frozen=True)
class Evaluation:
    """What the mechanical guidelines found in the changed files."""

    findings: tuple[Finding, ...] = ()
    #: Changed files a pattern rule covers that do not decode as UTF-8.
    not_text: tuple[str, ...] = ()
    #: Changed files at least one evaluated rule covers.
    scanned: int = 0
    #: Rules of an evaluated kind.
    rules: int = 0


@dataclass(frozen=True)
class History:
    """What git knows about the change, for the ``cochange`` kind.

    Every answer arrives as a callable, so this module makes no ``git`` call:
    ``core/rules`` makes them from the tree in hand. ``None`` from any of them
    is "git could not say", and a finding resting on it is unanswerable.

    - ``existed(rel)``: was the path a file where the branch left its base?
    - ``before(rel)``: its bytes there, asked only of a path that existed.
    - ``lines()``: every file git shows the change touched, each with the
      lines it added (``git.changed_lines``, spelled project-relative). Asked
      at most once, and only when a rule needs it.
    - ``executable(rel, text)``: the adapters' ``executable_text``.
    """

    existed: Callable[[str], bool | None]
    before: Callable[[str], bytes | None]
    lines: Callable[[], Mapping[str, Container[int]] | None]
    executable: Callable[[str, str], str | None]


def derive(template: str, rel: str) -> str | None:
    """The path an ``each`` template derives from a trigger file (DN-A5).

    ``{path}`` is the file, ``{dir}`` its directory (``.`` at the top),
    ``{name}`` its base name, ``{stem}`` the name without its last suffix and
    ``{suffix}`` that suffix with its dot. The result is spelled canonically,
    and one that names nothing inside the project is ``None``.
    """
    name = posixpath.basename(rel)
    stem, suffix = posixpath.splitext(name)
    derived = template.format(
        path=rel, dir=posixpath.dirname(rel) or ".", name=name, stem=stem, suffix=suffix
    )
    canonical = project_relative(derived)
    return None if canonical in (None, ".") else canonical


def evaluate(
    rules: Iterable[Guideline],
    changed: Iterable[str],
    *,
    read: Read,
    mask: Mask,
    references: References,
    gone: Iterable[str] = (),
    history: History | None = None,
    registries: Iterable[Registry] | None = None,
) -> Evaluation:
    """Every finding of the mechanical rules in the change.

    ``changed`` is the changed files that exist in the tree, project-relative,
    and ``gone`` the changed paths that do not. ``pattern`` and ``boundary``
    read only the first: a file that is gone has no line to match and imports
    nothing. ``cochange`` reads both, because removing a file is a change.
    ``history`` is what git knows about the change, and ``registries`` the
    registry files as the change leaves them; a cochange rule without them is
    unanswerable.

    Rules are taken in declared order and files in sorted order, so the same
    change gives the same findings in the same order. Each file is read once.
    """
    files = sorted(set(changed))
    present = set(files)
    everything = sorted(present | set(gone))
    evaluated = [rule for rule in rules if rule.check.kind in EVALUATED_KINDS]
    texts = _Texts(read)
    pairs = _Pairs(history, texts, mask, everything)
    by_path = {registry.path: registry for registry in registries or ()}
    found: list[Finding] = []
    covered: set[str] = set()
    for rule in evaluated:
        for rel in everything if rule.check.kind == "cochange" else files:
            if not any(matches(rel, glob) for glob in rule.applies_to):
                continue
            covered.add(rel)
            exempt = _path_exception(rule, rel)
            if exempt is not None:
                found.append(_finding(rule, rel, "excepted", note=exempt.reason))
            elif rule.check.kind == "pattern":
                found.extend(_pattern(rule, rel, texts, mask))
            elif rule.check.kind == "boundary":
                found.extend(_boundary(rule, rel, references))
            elif rule.check.form == "registry":
                found.extend(_registry(rule, rel, by_path.get(rule.check.registry or "")))
            else:
                found.extend(pairs.evaluate(rule, rel, present=rel in present))
    return Evaluation(
        findings=tuple(found),
        not_text=tuple(sorted(texts.not_text)),
        scanned=len(covered),
        rules=len(evaluated),
    )


class _Texts:
    """Each file read once, and what could not be read or is not text."""

    _UNREAD = object()

    def __init__(self, read: Read) -> None:
        self._read = read
        self._cache: dict[str, Any] = {}
        self.not_text: set[str] = set()

    def get(self, rel: str, *, name: bool = True) -> str | None | object:
        """The text, ``None`` when it is not text, or ``_UNREAD``.

        A file that is not text is named in the evaluation, unless the
        caller reads it for a question that makes it unanswerable instead.
        """
        if rel not in self._cache:
            data = self._read(rel)
            if data is None:
                self._cache[rel] = self._UNREAD
            else:
                try:
                    self._cache[rel] = data.decode("utf-8")
                except UnicodeDecodeError:
                    self._cache[rel] = None
        value = self._cache[rel]
        if value is None and name:
            self.not_text.add(rel)
        return value


def _pattern(rule: Guideline, rel: str, texts: _Texts, mask: Mask) -> list[Finding]:
    text = texts.get(rel)
    if text is _Texts._UNREAD:
        return [_finding(rule, rel, "unanswerable", note="the file could not be read")]
    if text is None:
        return []
    scope = rule.check.scope or "any"
    masked = text if scope == "any" else mask(rel, text, scope)
    if masked is None:
        return [_finding(
            rule, rel, "unanswerable",
            note=f"no configured adapter can mask {scope} in this file",
        )]
    raw_lines = text.split("\n")
    masked_lines = masked.split("\n")
    if len(raw_lines) != len(masked_lines):
        return [_finding(
            rule, rel, "unanswerable",
            note="the lexical mask did not keep the file's lines",
        )]
    regex = re.compile(rule.check.match or "")
    found: list[Finding] = []
    for number, (raw, region) in enumerate(zip(raw_lines, masked_lines, strict=True), 1):
        if regex.search(region):
            found.append(_matched(rule, rel, number, fingerprint(rule.id, collapse(raw))))
    return found


def _boundary(rule: Guideline, rel: str, references: References) -> list[Finding]:
    answer = references(rel)
    if answer is None:
        return [_finding(
            rule, rel, "unanswerable",
            note="no configured adapter can answer this file's references",
        )]
    found: list[Finding] = []
    for ref in answer:
        if not _crosses(rule, ref.target):
            continue
        stamp = fingerprint(rule.id, collapse(ref.text), ref.target)
        found.append(_matched(rule, rel, ref.line, stamp, target=ref.target))
    return found


class _Unanswerable(Exception):
    """A fact a cochange finding rests on that git or an adapter cannot give."""


class _Pairs:
    """The ``pair`` form of ``cochange`` (DN-A4 to DN-A6).

    "If a file under ``applies_to`` changed in this way, did the files
    ``then`` names change too?" A file satisfies ``then`` only when it is in
    the changed set AND git shows it changed: the changed set alone holds
    declared files, and declaring a file is not changing it.
    """

    _UNASKED = object()

    def __init__(
        self, history: History | None, texts: _Texts, mask: Mask, candidates: list[str]
    ) -> None:
        self._history = history
        self._texts = texts
        self._mask = mask
        self._candidates = candidates
        self._lines: Any = self._UNASKED

    def evaluate(self, rule: Guideline, rel: str, *, present: bool) -> list[Finding]:
        when, then = rule.check.when, rule.check.then
        if when is None or then is None:
            return []
        try:
            if not self._triggered(when, rel, present):
                return []
            missing = self._missing(then, rel)
        except _Unanswerable as why:
            return [_finding(rule, rel, "unanswerable", note=str(why))]
        if not missing:
            return []
        owed = " and ".join(missing) if then.shape != "any_of" else (
            "one of " + ", ".join(missing)
        )
        trigger = _TRIGGERS[when.change] + (
            f", with an added line in {when.scope} matching {when.match!r}" if when.match else ""
        )
        return [_finding(
            rule, rel, "new",
            note=f"{trigger}, and this change touches no {owed}",
            fix=(
                f"change {owed} in this change, as {rule.id} asks; a new file counts once "
                "it is declared (invariant session files add), staged or committed"
            ),
        )]

    # -- the trigger (DN-A6) ---------------------------------------------------------

    def _triggered(self, when: Trigger, rel: str, present: bool) -> bool:
        if when.change == "added" and not self._added(rel, present):
            return False
        if when.change == "executable" and not self._executable(rel, present):
            return False
        return self._matched(when, rel, present) if when.match else True

    def _facts(self) -> History:
        if self._history is None:
            raise _Unanswerable("what git knows about this change was not read")
        return self._history

    def _existed(self, rel: str) -> bool:
        existed = self._facts().existed(rel)
        if existed is None:
            raise _Unanswerable(
                "whether the file existed where the branch left its base could not be read"
            )
        return existed

    def _added(self, rel: str, present: bool) -> bool:
        return present and not self._existed(rel)

    def _executable(self, rel: str, present: bool) -> bool:
        existed = self._existed(rel)
        if not present and not existed:
            return False
        before = self._decoded(self._facts().before(rel), "its base") if existed else ""
        after = self._current(rel) if present else ""
        reduce = self._facts().executable
        reduced = reduce(rel, before), reduce(rel, after)
        if None in reduced:
            raise _Unanswerable(
                "no configured adapter can reduce this file to its executable text"
            )
        return reduced[0] != reduced[1]

    def _matched(self, when: Trigger, rel: str, present: bool) -> bool:
        if not present:
            return False
        text = self._texts.get(rel)
        if text is _Texts._UNREAD:
            raise _Unanswerable("the file could not be read")
        if text is None:
            return False
        added = self._changed().get(rel)
        if added is None:
            return False
        scope = when.scope or "any"
        masked = text if scope == "any" else self._mask(rel, text, scope)
        if masked is None:
            raise _Unanswerable(f"no configured adapter can mask {scope} in this file")
        lines = masked.split("\n")
        if len(lines) != len(text.split("\n")):
            raise _Unanswerable("the lexical mask did not keep the file's lines")
        regex = re.compile(when.match or "")
        return any(
            number in added and regex.search(line)
            for number, line in enumerate(lines, 1)
        )

    def _current(self, rel: str) -> str:
        text = self._texts.get(rel, name=False)
        if text is _Texts._UNREAD:
            raise _Unanswerable("the file could not be read")
        if text is None:
            raise _Unanswerable("the file is not UTF-8 text, so it has no executable text")
        return text  # type: ignore[return-value]

    @staticmethod
    def _decoded(data: bytes | None, where: str) -> str:
        if data is None:
            raise _Unanswerable(f"the file could not be read at {where}")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise _Unanswerable(
                f"the file is not UTF-8 text at {where}, so it has no executable text"
            ) from None

    # -- what must change with it (DN-A5) ----------------------------------------------

    def _changed(self) -> Mapping[str, Container[int]]:
        if self._lines is self._UNASKED:
            self._lines = self._facts().lines()
        if self._lines is None:
            raise _Unanswerable("the files and lines this change touched could not be read from git")
        return self._lines

    def _missing(self, then: Requirement, rel: str) -> list[str]:
        changed = self._changed()
        touched = [path for path in self._candidates if path in changed]
        if then.shape == "each":
            derived = derive(then.template or "", rel)
            if derived is None:
                raise _Unanswerable(
                    f"then.each {then.template!r} derives no path inside the project from it"
                )
            return [] if derived in touched else [derived]
        unmet = [g for g in then.globs if not any(matches(p, g) for p in touched)]
        if then.shape == "any_of":
            return list(then.globs) if len(unmet) == len(then.globs) else []
        return unmet


#: How a finding states what triggered it.
_TRIGGERS = {
    "any": "this change touches it",
    "added": "it is new on this branch",
    "executable": "this change alters its executable code",
}


def _registry(rule: Guideline, rel: str, registry: Registry | None) -> list[Finding]:
    """The ``registry`` form (DN-A7, DN-A8): an open entry listing the file.

    The registry is read as the change leaves it, so the change that resolves
    a file strikes it from its entry, or resolves the entry, and the finding
    clears. Each open entry listing the file is one finding, at the outcome
    its class maps to, with the class's fix.
    """
    if registry is None:
        return [_finding(
            rule, rel, "unanswerable",
            note=f"the registry {rule.check.registry} was not read",
        )]
    outcomes, fixes = dict(rule.check.outcomes), dict(rule.check.fixes)
    found: list[Finding] = []
    for entry in registry.entries:
        if entry.status != "open" or rel not in entry.paths:
            continue
        found.append(Finding(
            rule=rule.id, kind=rule.check.kind,
            severity=outcomes.get(entry.cls, rule.severity), path=rel, status="new",
            note=f"listed in open entry {entry.id} ({entry.cls}) of {registry.path}",
            fix=fixes.get(entry.cls) or (
                f"resolve entry {entry.id} in this change, and strike {rel} from it in "
                f"{registry.path} or mark the entry resolved"
            ),
        ))
    return found


def _crosses(rule: Guideline, target: str) -> bool:
    """Does a reference to ``target`` break the rule's one polarity (DN-A3)?"""
    if rule.check.forbid:
        return any(matches(target, glob) for glob in rule.check.forbid)
    return not any(matches(target, glob) for glob in rule.check.only)


def _matched(
    rule: Guideline, rel: str, line: int, stamp: str, *, target: str | None = None
) -> Finding:
    exception = _fingerprint_exception(rule, rel, stamp)
    if exception is not None:
        return _finding(
            rule, rel, "excepted", line=line, fingerprint=stamp, target=target,
            note=exception.reason,
        )
    return _finding(rule, rel, "new", line=line, fingerprint=stamp, target=target)


def _finding(rule: Guideline, rel: str, status: str, **fields: Any) -> Finding:
    return Finding(
        rule=rule.id, kind=rule.check.kind, severity=rule.severity, path=rel,
        status=status, **fields,
    )


def _path_exception(rule: Guideline, rel: str) -> GuidelineException | None:
    """A path exception with no fingerprint sets the whole file aside."""
    return _first(rule.exceptions, rel, lambda exc: exc.fingerprint is None)


def _fingerprint_exception(
    rule: Guideline, rel: str, stamp: str
) -> GuidelineException | None:
    return _first(rule.exceptions, rel, lambda exc: exc.fingerprint == stamp)


def _first(
    exceptions: Sequence[GuidelineException],
    rel: str,
    applies: Callable[[GuidelineException], bool],
) -> GuidelineException | None:
    return next(
        (exc for exc in exceptions if applies(exc) and matches(rel, exc.path)),
        None,
    )



# -- the ratchet (D6, ruling 5) ------------------------------------------------------

#: A finding's key in a baseline: (rule, path, fingerprint).
Key = tuple[str, str, str]


@dataclass(frozen=True)
class Stale:
    """Recorded entries of a touched file that no finding holds any more."""

    rule: str
    path: str
    fingerprint: str
    recorded: int
    found: int

    def describe(self) -> str:
        return (
            f"{self.rule} {self.path} {self.fingerprint} "
            f"(recorded {self.recorded}, found {self.found})"
        )


@dataclass(frozen=True)
class Ratcheted:
    """The findings with the baseline applied, and what it holds that is stale."""

    findings: tuple[Finding, ...]
    stale: tuple[Stale, ...] = ()


def ratchet(
    found: Iterable[Finding], entries: Iterable[BaselineEntry], touched: Container[str]
) -> Ratcheted:
    """Apply a baseline to the findings of the changed files (D6).

    For each touched file and rule, F findings of one (rule, path,
    fingerprint) against an entry recording N give min(F, N) baselined, the
    first in file order, and F - N new when F > N. The findings carry the same
    fingerprint, so which of them is the pre-existing one cannot be told;
    file order is the one answer that does not depend on anything else. An
    entry protects its finding whether or not the change touches the file
    (ruling 5): only changed files are scanned, so every finding here is in a
    touched file, and it is baselined all the same.

    N - F is stale when N > F, for an entry whose path the change touched.
    An entry for a file nobody touched is never stale here, because nobody
    looked. A (rule, path) with an unanswerable finding has no stale entries:
    nothing could say what the file holds. Only a new, fingerprinted finding
    is ever baselined, so an excepted finding consumes no entry, and a
    cochange finding, which has no fingerprint, is never baselined (DN-4).
    """
    budget = {entry.key: entry.count for entry in entries}
    recorded = dict(budget)
    result: list[Finding] = []
    seen: dict[Key, int] = {}
    unknown: set[tuple[str, str]] = set()
    for finding in found:
        if finding.status == "unanswerable":
            unknown.add((finding.rule, finding.path))
        key = _key(finding)
        if key is None:
            result.append(finding)
            continue
        seen[key] = seen.get(key, 0) + 1
        if budget.get(key, 0) > 0:
            budget[key] -= 1
            finding = replace(finding, status="baselined")
        result.append(finding)
    stale = tuple(
        Stale(rule, path, stamp, count, seen.get((rule, path, stamp), 0))
        for (rule, path, stamp), count in recorded.items()
        if path in touched
        and (rule, path) not in unknown
        and count > seen.get((rule, path, stamp), 0)
    )
    return Ratcheted(findings=tuple(result), stale=stale)


def counted(found: Iterable[Finding]) -> dict[Key, int]:
    """How many new, fingerprinted findings each (rule, path, fingerprint) has.

    What ``guidelines baseline --record`` writes and ``--prune`` compares a
    recorded count with.
    """
    counts: dict[Key, int] = {}
    for finding in found:
        key = _key(finding)
        if key is not None:
            counts[key] = counts.get(key, 0) + 1
    return counts


def _key(finding: Finding) -> Key | None:
    """The baseline key of a finding a baseline can hold, else ``None``."""
    if finding.status != "new" or finding.fingerprint is None:
        return None
    return (finding.rule, finding.path, finding.fingerprint)
