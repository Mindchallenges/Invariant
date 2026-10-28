"""Mechanical findings: the ``pattern`` and ``boundary`` evaluators.

ADR-012 D4, D5 and D6, and the A3 rulings (DN-2, DN-3, DN-13). A finding is
one place in a changed file where a mechanical guideline is broken, or one
file a guideline covers that nothing could answer for. ``core/rules`` asks
this module through ``guidelines.mechanical_clean``, and prints what it
answers in D5's record shape. Nothing here is persisted (DN-16).

Pure. The file bytes, the lexical mask and the references arrive as
callables, so this module does no file read and no ``git`` call: ``core/rules``
does every one of them, from the tree in hand, and decides which files
changed (D4, ``_changed_set``). It imports the ruleset model it evaluates and
the manifest's one spelling of a declared path, and nothing else of the
engine's.

- **pattern** (DN-2). Each line of the masked text is matched, one finding per
  matching line. The mask is the region the rule's ``in`` names, through the
  adapter's lexical mask; ``any`` is the raw text and asks no mask. The
  fingerprint is taken from the RAW line, so a match inside a line is the
  same finding however the rest of the line is masked.
- **boundary** (DN-A1 to DN-A3, DN-3). Each reference a changed file under
  ``applies_to`` makes is checked against the rule's ``forbid`` deny-list or
  its allow-only ``only`` list of project path globs.

Unanswerable is a failing finding, never a pass: a mask or references answer
of ``None``, a file that could not be read, and a mask that did not keep the
file's lines. A file that does not decode as UTF-8 is not text: the pattern
kind does not apply to it, and the evaluation names it, so the verdict can.

No baseline exists in this build, so every finding is ``new``. The baseline
(D6) and ``cochange`` arrive in later increments.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .guidelines import DIGEST_LENGTH, Guideline, GuidelineException
from .manifest import glob_matches, project_relative

#: The kinds this module evaluates. ``cochange`` is A3 increment 4.
EVALUATED_KINDS = ("pattern", "boundary")

#: What a finding can be. ``new`` and ``unanswerable`` count against the
#: rule's severity; ``excepted`` is a reasoned exception that applied, named
#: and never counted (D4).
STATUSES = ("new", "unanswerable", "excepted")
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
    #: Why a file is unanswerable, or the reason an exception gives.
    note: str = ""

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


def evaluate(
    rules: Iterable[Guideline],
    changed: Iterable[str],
    *,
    read: Read,
    mask: Mask,
    references: References,
) -> Evaluation:
    """Every finding of the ``pattern`` and ``boundary`` rules in ``changed``.

    ``changed`` is the changed files that exist in the tree, project-relative.
    Rules are taken in declared order and files in sorted order, so the same
    change gives the same findings in the same order. Each file is read once.
    """
    files = sorted(set(changed))
    evaluated = [rule for rule in rules if rule.check.kind in EVALUATED_KINDS]
    texts = _Texts(read)
    found: list[Finding] = []
    covered: set[str] = set()
    for rule in evaluated:
        for rel in files:
            if not any(matches(rel, glob) for glob in rule.applies_to):
                continue
            covered.add(rel)
            exempt = _path_exception(rule, rel)
            if exempt is not None:
                found.append(_finding(rule, rel, "excepted", note=exempt.reason))
            elif rule.check.kind == "pattern":
                found.extend(_pattern(rule, rel, texts, mask))
            else:
                found.extend(_boundary(rule, rel, references))
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

    def get(self, rel: str) -> str | None | object:
        """The text, ``None`` when it is not text, or ``_UNREAD``."""
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
        if value is None:
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
