"""Judgement verdicts: the record, the parsing of a submission, and D5's checks.

ADR-012 D5 and the A4 rulings (DN-1 to DN-12). A judgement guideline is
answered by a structured, cited verdict, which any host, a script or a person
submits through the CLI. This module holds the record, reads a submission into
:class:`Submitted` values, and holds the pure checks behind D5's predicates:

- **P1**, the rule exists and applies: it is a judgement guideline, the
  ruleset in the tree is the one the pipeline pinned (DN-6), the change meets
  its ``applies_to`` through a changed file present in the tree, under
  ``applies_to`` and not path-excepted (DN-3), the state is a review-role
  state, and the rule's phases name review, landing or always (DN-2);
- **P2**, a ``pass`` or ``fail`` rests on evidence of a kind the rule's
  ``evidence`` allows;
- **P3**, each citation is spelled project-relative and names a UTF-8 text
  file the change touched, present in the tree (DN-4, DN-5), with a line range
  inside it; for ``scope: diff`` one cited range meets a line the change added;
- **P4**, each test is a selector a recorded run in this pipeline named,
  exactly, and for a ``pass`` the latest such run is green (DN-8);
- **P5**, an ``na`` names a declared condition of the rule, and carries no
  citations and no tests; a mechanical one holds only when the engine finds
  it true over the change (``core/findings.na_answer``, A4.2);
- **P6**, the note is capped and privacy-scanned.

Pure (DN-1). The file bytes, the added lines, the test runs, the pin and the
privacy scan arrive as values or callables in :class:`Facts`; this module
does no file read, no ``git`` call and no clock read. ``core/rules`` makes
every read, from the tree in hand, in ``plan_verdicts``, and the
``guidelines.verdicts_complete`` gate (A4.3) reads the same :class:`Facts`.
Recording is ``domains/pipeline``'s.

At the gate, :func:`standings` asks which judgement rules the change owes a
verdict (:func:`owed`, DN-2 and DN-3), lets a mechanical N/A condition the
engine finds true answer a rule itself (D5 item 5), and checks the latest
recorded verdict on every other owed rule again with :func:`check`, so a
verdict whose citations, tests, N/A or ruleset no longer hold is stale, and a
stale verdict is a missing one (D5, "Staleness"). :func:`not_owed` is the one
reading of why a rule is or is not owed, and :func:`unowed` names the recorded
verdicts on rules the change no longer owes, for ``guidelines verdicts show``
(A4.4), which prints the gate's own read. A live grant naming a judgement
guideline passes every failure of it, a warn one's included (A4.5, DN-9),
and :func:`unused_grants` names each grant that passes nothing.

A refused verdict never enters evidence (ruling 6): a check answers the record
only when no predicate failed, and names every problem when one did. The
engine never reads the note (D5, item 6), and no check reads the acting agent
(D1 item 2, DN-12): :attr:`VerdictRecord.agent` and the submission's
:attr:`VerdictRecord.token` are audit only.
"""

from __future__ import annotations

from collections.abc import Callable, Container, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from difflib import get_close_matches
from typing import Any

from .findings import NaAnswer
from .guidelines import Guideline, NaCondition, Ruleset, digest_of, meets
from .manifest import project_relative

#: What a verdict can say (D5).
STATUSES = ("pass", "fail", "na")
#: The phases that owe a verdict (DN-2): a rule seen only while designing is
#: applied by the design agent and answered by nobody.
OWED_PHASES = frozenset({"review", "landing", "always"})
#: The role a state must carry for a verdict to be accepted there (DN-2).
ACCEPTING_ROLE = "review"
#: Where the CLI's submissions come from. The acting agent, where the host
#: can name one, is stamped on afterwards (A4.8), and the surface stays this.
SURFACE_CLI = "cli"

#: The keys of one submitted object (D5's schema). ``ruleset`` and a
#: citation's ``blob`` are the engine's to record; a submission may carry
#: them, and then they must equal what the engine records.
_ENTRY_KEYS = frozenset(
    {"rule", "status", "citations", "tests", "na_condition", "note", "ruleset"}
)
_CITATION_KEYS = frozenset({"path", "lines", "blob"})

#: The predicate a problem of a submission's shape is named by.
SHAPE = "shape"
#: The predicate a refusal of the whole is named by when there is nothing to
#: check a verdict against: no session, no running pipeline, no ruleset.
PRECONDITION = "pipeline"
#: The predicate a refusal of the whole is named by when the submitted file
#: cannot be read, or is not JSON.
SUBMISSION = "submission"
#: Every name a refused submission gives a problem: the whole's two, a
#: shape problem, and D5's six predicates. A host, a script or the
#: conformance harness compares these, never the prose (A4 increment 7).
PREDICATES = (PRECONDITION, SUBMISSION, SHAPE, "P1", "P2", "P3", "P4", "P5", "P6")


@dataclass(frozen=True)
class Problem:
    """One reason a verdict is refused: the predicate, what failed, the fix."""

    predicate: str
    message: str
    fix: str

    def describe(self) -> str:
        return f"{self.predicate}: {self.message}; fix: {self.fix}"


@dataclass(frozen=True)
class Refusal:
    """One line of a refused submission, and the names a reader compares.

    ``line`` is what the refusal prints. ``predicate`` is one of
    :data:`PREDICATES`; ``entry`` (counted from 1) and ``rule`` say which
    entry it was asked of, and are ``None`` for a problem of the whole, or
    a rule an entry did not name.
    """

    predicate: str
    line: str
    entry: int | None = None
    rule: str | None = None

    def record(self) -> dict[str, Any]:
        """As plain data: what a refusal prints under ``--json``."""
        return {"entry": self.entry, "rule": self.rule, "predicate": self.predicate,
                "problem": self.line}


@dataclass(frozen=True)
class Cited:
    """A citation as submitted: a path and a line range, and a claimed blob."""

    path: str
    first: int
    last: int
    blob: str | None = None


@dataclass(frozen=True)
class Submitted:
    """One verdict as submitted, before any check."""

    rule: str
    status: str
    citations: tuple[Cited, ...] = ()
    tests: tuple[str, ...] = ()
    na_condition: str | None = None
    note: str | None = None
    #: The ruleset digest the submitter claims, which must be the tree's.
    ruleset: str | None = None


@dataclass(frozen=True)
class Citation:
    """A checked citation, with the blob of the cited file the engine read."""

    path: str
    first: int
    last: int
    #: ``digest_of`` the cited file's bytes in the tree at submission, so a
    #: later edit of the file makes the verdict stale (D5, "Staleness").
    blob: str

    def record(self) -> dict[str, Any]:
        return {"path": self.path, "lines": [self.first, self.last], "blob": self.blob}


@dataclass(frozen=True)
class VerdictRecord:
    """A verdict the checks accepted, as ``Pipeline.evidence["verdicts"]`` holds it.

    No clock time (the record is evidence about the change, not about when it
    was written). ``token`` is the submission's: one random value on every
    record one submission writes, set by ``domains/guidelines`` and printed
    by the command, so the host can find these records again (A4.8, the
    user's ruling b). ``agent`` is what the host then stamps there, once
    (DN-12, ruling a). Each is written only when set, and no gate may ever
    read either (D1 item 2).
    """

    rule: str
    status: str
    citations: tuple[Citation, ...]
    tests: tuple[str, ...]
    na_condition: str | None
    note: str | None
    #: The ruleset digest the verdict was judged against: the pin.
    ruleset: str
    #: The pipeline state it was submitted in.
    state: str
    surface: str
    agent: str | None = None
    token: str | None = None

    def record(self) -> dict[str, Any]:
        recorded: dict[str, Any] = {
            "rule": self.rule,
            "status": self.status,
            "citations": [c.record() for c in self.citations],
            "tests": list(self.tests),
            "na_condition": self.na_condition,
            "note": self.note,
            "ruleset": self.ruleset,
            "state": self.state,
            "surface": self.surface,
        }
        if self.agent is not None:
            recorded["agent"] = self.agent
        if self.token is not None:
            recorded["token"] = self.token
        return recorded


@dataclass(frozen=True)
class Entry:
    """One entry of a submission, numbered from one, parsed or not."""

    index: int
    submitted: Submitted | None
    problems: tuple[Problem, ...] = ()


@dataclass(frozen=True)
class Parsed:
    """A whole submission: its entries, and problems of the whole."""

    entries: tuple[Entry, ...]
    problems: tuple[Problem, ...] = ()


#: ``added()``: every touched file with the lines the change added to it, or
#: ``None`` when git cannot say (no base recorded, no merge base, a leg it
#: could not read). Asked only for a ``scope: diff`` rule.
Added = Callable[[], "Mapping[str, Container[int]] | None"]


@dataclass(frozen=True)
class Facts:
    """Everything the checks read, gathered by ``core/rules`` (DN-1)."""

    #: The digest of the ruleset in the tree in hand.
    tree_digest: str
    #: The digest the pipeline pinned at start; ``None`` for no pin, or a pin
    #: with a null digest, and then the tree's digest stands (DN-6).
    pin: str | None
    #: The pipeline's current state and the roles the machine gives it.
    state: str
    roles: tuple[str, ...]
    #: The changed files present in the tree, project-relative; ``None`` when
    #: the changed set could not be established.
    touched: tuple[str, ...] | None
    #: ``read(rel)``: a touched file's bytes in the tree, or ``None``.
    read: Callable[[str], bytes | None]
    added: Added
    #: This pipeline's ``test_runs`` records, oldest first.
    test_runs: tuple[Any, ...] = ()
    note_max_chars: int = 280
    #: ``scan(note)``: each privacy finding in the note, described.
    scan: Callable[[str], Iterable[str]] = lambda text: ()
    surface: str = SURFACE_CLI
    #: Why the state's roles could not be read (a methodology that no longer
    #: loads); ``None`` when they could.
    roles_problem: str | None = None
    #: ``na(rule, condition, touched)``: the engine's answer to a mechanical
    #: N/A condition over the touched files (``core/findings.na_answer``,
    #: A4.2). ``None`` when it was not read, and an ``na`` naming a
    #: mechanical condition is then refused.
    na: Callable[[Guideline, NaCondition, tuple[str, ...]], NaAnswer] | None = None


@dataclass(frozen=True)
class Checked:
    """One entry checked: the record when every predicate held, else why not."""

    index: int
    rule: str | None
    record: VerdictRecord | None
    problems: tuple[Problem, ...] = field(default=())

    def describe(self) -> list[str]:
        return [refusal.line for refusal in self.refusals()]

    def refusals(self) -> list[Refusal]:
        """Each problem as a refusal line, named by this entry and its rule."""
        named = f"entry {self.index} ({self.rule})" if self.rule else f"entry {self.index}"
        return [
            Refusal(problem.predicate, f"{named}: {problem.describe()}", self.index, self.rule)
            for problem in self.problems
        ]


# -- parsing ---------------------------------------------------------------------------


def _shape(message: str, fix: str = "write the entry in D5's schema") -> Problem:
    return Problem(SHAPE, message, fix)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_citation(number: int, raw: Any) -> tuple[Cited | None, list[Problem]]:
    where = f"citation {number}"
    if not isinstance(raw, Mapping):
        return None, [_shape(f"{where} is not an object",
                             'a citation is {"path": ..., "lines": [first, last]}')]
    problems = [
        _shape(f"{where} has unknown key {key}", "a citation holds path, lines and blob")
        for key in sorted(set(raw) - _CITATION_KEYS, key=str)
    ]
    path = raw.get("path")
    if not isinstance(path, str) or not path:
        problems.append(_shape(f"{where} names no path", "give the cited file's path"))
    lines = raw.get("lines")
    if not (isinstance(lines, list) and len(lines) == 2 and all(_is_int(n) for n in lines)):
        problems.append(_shape(f"{where}'s lines must be two integers, got {lines!r}",
                               "give the first and the last cited line"))
    blob = raw.get("blob")
    if blob is not None and not isinstance(blob, str):
        problems.append(_shape(f"{where}'s blob must be a string, got {blob!r}",
                               "leave the blob out; the engine records it"))
    if problems:
        return None, problems
    return Cited(path=path, first=lines[0], last=lines[1], blob=blob), []


def parse_entry(raw: Any) -> tuple[Submitted | None, tuple[Problem, ...]]:
    """One submitted object read into a :class:`Submitted`, or every reason not."""
    if not isinstance(raw, Mapping):
        return None, (_shape(f"the entry is not an object, got {type(raw).__name__}"),)
    problems = [
        _shape(f"unknown key {key}", f"an entry holds {', '.join(sorted(_ENTRY_KEYS))}")
        for key in sorted(set(raw) - _ENTRY_KEYS, key=str)
    ]
    rule = raw.get("rule")
    if not isinstance(rule, str) or not rule or len(rule.split()) != 1 or rule != rule.strip():
        problems.append(_shape("the entry names no rule", "give the judgement guideline's id"))
    status = raw.get("status")
    if status not in STATUSES:
        problems.append(_shape(f"status must be one of {', '.join(STATUSES)}, got {status!r}",
                               "answer pass, fail or na"))
    citations: list[Cited] = []
    raw_citations = raw.get("citations", [])
    if not isinstance(raw_citations, list):
        problems.append(_shape("citations must be a list", "give a list of citations"))
    else:
        for number, item in enumerate(raw_citations, start=1):
            cited, wrong = _parse_citation(number, item)
            problems.extend(wrong)
            if cited is not None:
                citations.append(cited)
    tests = raw.get("tests", [])
    if not (isinstance(tests, list) and all(isinstance(t, str) and t.strip() for t in tests)):
        problems.append(_shape("tests must be a list of selectors",
                               "give each test as the selector a recorded run named"))
        tests = []
    condition = raw.get("na_condition")
    if condition is not None and not (isinstance(condition, str) and condition):
        problems.append(_shape(f"na_condition must be null or a condition id, got {condition!r}",
                               "name one of the rule's claim conditions"))
    note = raw.get("note")
    if note is not None and not isinstance(note, str):
        problems.append(_shape(f"note must be null or a string, got {note!r}",
                               "write the note as text"))
    ruleset = raw.get("ruleset")
    if ruleset is not None and not isinstance(ruleset, str):
        problems.append(_shape(f"ruleset must be null or a digest, got {ruleset!r}",
                               "leave the ruleset out; the engine records it"))
    if problems:
        return None, tuple(problems)
    return Submitted(
        rule=rule, status=status, citations=tuple(citations), tests=tuple(tests),
        na_condition=condition, note=note, ruleset=ruleset,
    ), ()


def parse_submission(data: Any) -> Parsed:
    """A bulk submission: a JSON list of D5 objects, each entry parsed."""
    if not isinstance(data, list):
        return Parsed(entries=(), problems=(
            _shape("a submission is a JSON list of verdict objects",
                   "wrap the verdicts in a list"),
        ))
    if not data:
        return Parsed(entries=(), problems=(
            _shape("the submission holds no verdict", "give at least one verdict"),
        ))
    entries = []
    for index, raw in enumerate(data, start=1):
        submitted, problems = parse_entry(raw)
        entries.append(Entry(index=index, submitted=submitted, problems=problems))
    return Parsed(entries=tuple(entries))


# -- P1, the rule exists and applies --------------------------------------------------------


def applies_problems(
    sub: Submitted,
    rule: Guideline | None,
    facts: Facts,
    ids: Sequence[str] = (),
    *,
    submitting: bool = True,
) -> list[Problem]:
    """P1. With no rule, or one that is not judgement, nothing else is asked.

    The review-role predicate is asked only at submission (DN-2): the gate
    checks a recorded verdict again at the landing, which is no review state,
    and the record was refused in any state the pipeline had since left.
    """
    if rule is None:
        near = get_close_matches(sub.rule, list(ids), n=1, cutoff=0.6)
        hint = f"did you mean {near[0]!r}? " if near else ""
        return [Problem(
            "P1", f"no guideline {sub.rule!r} in the ruleset",
            f"{hint}'invariant guidelines show' lists the guidelines",
        )]
    if rule.check.kind != "judgement":
        return [Problem(
            "P1",
            f"{rule.id} is a {rule.check.kind} guideline; the engine answers it, "
            f"and no verdict is asked for",
            "invariant check guidelines.mechanical_clean"
            if rule.check.kind != "engine" else f"invariant check {rule.check.rule}",
        )]
    problems: list[Problem] = []
    pinned = facts.pin or facts.tree_digest
    if facts.tree_digest != pinned:
        problems.append(Problem(
            "P1",
            f"the ruleset in the tree is {facts.tree_digest}, and the pipeline pinned "
            f"{pinned}; a verdict is judged against the pin (DN-6)",
            "restore the pinned ruleset, or finish this pipeline and start a fresh one "
            "under the edited ruleset",
        ))
    if sub.ruleset is not None and sub.ruleset != facts.tree_digest:
        problems.append(Problem(
            "P1",
            f"the entry names ruleset {sub.ruleset}, and the tree's is {facts.tree_digest}",
            "leave the ruleset out; the engine records the pinned one",
        ))
    if submitting:
        problems.extend(_role_problems(facts))
    if not set(rule.phases) & OWED_PHASES:
        problems.append(Problem(
            "P1",
            f"{rule.id}'s phases ({', '.join(rule.phases)}) name neither review, landing "
            f"nor always, so no verdict is owed",
            "answer only the guidelines the review owes",
        ))
    if facts.touched is None:
        problems.append(Problem(
            "P1",
            f"the changed set could not be established, so whether {rule.id} applies "
            f"cannot be said",
            "invariant session files add <path>, or check the session's base branch",
        ))
    elif not any(meets(rule, rel) for rel in facts.touched):
        problems.append(Problem(
            "P1",
            f"no changed file in the tree falls under {rule.id}'s applies_to "
            f"({', '.join(rule.applies_to)}) outside its path exceptions (DN-3)",
            "submit a verdict only for a guideline the change meets",
        ))
    return problems


def _role_problems(facts: Facts) -> list[Problem]:
    """P1's review-role predicate (DN-2), asked at submission only."""
    if facts.roles_problem is not None:
        return [Problem(
            "P1", f"the state's roles cannot be read: {facts.roles_problem}",
            "invariant pipeline status",
        )]
    if ACCEPTING_ROLE not in facts.roles:
        return [Problem(
            "P1",
            f"state {facts.state!r} carries no {ACCEPTING_ROLE} role; verdicts are "
            f"accepted only in a review-role state (DN-2)",
            f"advance the pipeline to its {ACCEPTING_ROLE} state, then submit",
        )]
    return []


# -- P2, evidence of an allowed kind --------------------------------------------------------


def evidence_problems(sub: Submitted, rule: Guideline) -> list[Problem]:
    """P2. A ``pass`` or ``fail`` rests on evidence the rule's ``evidence`` allows.

    A kind the rule does not allow is refused, not ignored: a test named on a
    citation-only rule would be recorded as evidence no check had weighed.
    """
    if sub.status == "na":
        return []
    evidence = rule.check.evidence
    cited, tested = bool(sub.citations), bool(sub.tests)
    wrong = {
        "citation": (not cited or tested, "a citation, and no test"),
        "test": (not tested or cited, "a test, and no citation"),
        "either": (not (cited or tested), "a citation or a test"),
        "both": (not (cited and tested), "a citation and a test"),
    }
    failed, wanted = wrong.get(evidence, (True, f"evidence {evidence!r}"))
    if not failed:
        return []
    return [Problem(
        "P2",
        f"{rule.id} takes evidence: {evidence}, so a {sub.status} rests on {wanted}",
        "cite with --cite <path> <first> <last>" if evidence == "citation"
        else "name a test with --test <selector>" if evidence == "test"
        else "give the evidence the rule declares",
    )]


# -- P3, each citation is real ---------------------------------------------------------------


def _line_count(text: str) -> int:
    """Lines split on line feeds, as findings count them; a last line without
    a line feed counts, and an empty file has none."""
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def citation_problems(
    sub: Submitted, rule: Guideline, facts: Facts, *, submitting: bool = True
) -> tuple[tuple[Citation, ...], list[Problem]]:
    """P3. Each citation checked, with its blob; the diff asked for ``scope: diff``.

    A blob that is not the file's is a submitter's slip at submission, and at
    the gate (``submitting=False``) the file changed after the verdict, which
    a fresh verdict answers (D5, "Staleness").
    """
    citations: list[Citation] = []
    problems: list[Problem] = []
    touched = set(facts.touched or ())
    for number, cited in enumerate(sub.citations, start=1):
        where = f"citation {number} ({cited.path}:{cited.first}-{cited.last})"
        canonical = project_relative(cited.path)
        if canonical != cited.path or canonical in (None, "."):
            fix = (f"spell it {canonical}" if canonical not in (None, ".")
                   else "cite a file inside the project, project-relative")
            problems.append(Problem("P3", f"{where} is not spelled project-relative", fix))
            continue
        if cited.path not in touched:
            problems.append(Problem(
                "P3", f"{where} is not a file the change touched in the tree (DN-4)",
                "cite a file the change touched",
            ))
            continue
        data = facts.read(cited.path)
        if data is None:
            problems.append(Problem("P3", f"{where}: the file could not be read",
                                    "cite a file that can be read"))
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(Problem("P3", f"{where}: the file is not UTF-8 text",
                                    "cite a text file"))
            continue
        count = _line_count(text)
        if not 1 <= cited.first <= cited.last <= count:
            problems.append(Problem(
                "P3", f"{where} is not a range inside the file's {count} line(s)",
                "cite 1 <= first <= last <= the file's line count",
            ))
            continue
        blob = digest_of(data)
        if cited.blob is not None and cited.blob != blob:
            problems.append(Problem(
                "P3", f"{where} names blob {cited.blob}, and the file in the tree is {blob}",
                "leave the blob out; the engine records it",
            ) if submitting else Problem(
                "P3",
                f"{where}: the file is {blob} in the tree, and was {cited.blob} when the "
                f"verdict was recorded",
                f"answer {rule.id} again on the file as it is now: "
                f"invariant guidelines verdict {rule.id}",
            ))
            continue
        citations.append(Citation(cited.path, cited.first, cited.last, blob))
    # Asked over the citations that held, and only when one did: a verdict
    # with no valid citation is refused already, by the problems above.
    if rule.check.verdict_scope == "diff" and citations and sub.status != "na":
        problems.extend(_diff_problems(rule, citations, facts))
    return tuple(citations), problems


def _diff_problems(rule: Guideline, citations: list[Citation], facts: Facts) -> list[Problem]:
    added = facts.added()
    if added is None:
        return [Problem(
            "P3",
            f"{rule.id} is scope: diff, and the lines the change added are unanswerable: "
            f"no base recorded, no merge base, or a diff git could not give",
            "check the session's base branch is reachable",
        )]
    for citation in citations:
        lines = added.get(citation.path)
        if lines is not None and any(
            n in lines for n in range(citation.first, citation.last + 1)
        ):
            return []
    return [Problem(
        "P3",
        f"{rule.id} is scope: diff, and no valid cited range meets a line the change added",
        "cite a range that holds a line this change added",
    )]


# -- P4, each test is real ---------------------------------------------------------------------


def selector_problems(sub: Submitted, runs: Sequence[Any]) -> list[Problem]:
    """P4 (DN-8). A selector counts only when a recorded run named it exactly;
    a full run never vouches for a named test. For a ``pass`` the latest run
    naming it must be green: passed, and not read as red."""
    problems: list[Problem] = []
    for selector in sub.tests:
        naming = [
            run for run in runs
            if isinstance(run, Mapping)
            and isinstance(run.get("selectors"), list)
            and selector in run["selectors"]
        ]
        if not naming:
            problems.append(Problem(
                "P4",
                f"no recorded run in this pipeline named {selector}; a full run never "
                f"vouches for a named test (DN-8)",
                f"invariant pipeline test --selector {selector}",
            ))
            continue
        latest = naming[-1]
        if sub.status == "pass" and not (
            latest.get("passed") is True and not latest.get("read_as_red")
        ):
            problems.append(Problem(
                "P4", f"the latest run naming {selector} was not green",
                f"fix it, then: invariant pipeline test --selector {selector}",
            ))
    return problems


# -- P5, na is chosen, not written -----------------------------------------------------------------


def na_problems(sub: Submitted, rule: Guideline, facts: Facts) -> list[Problem]:
    """P5. An ``na`` names one of the rule's declared conditions, and nothing
    else of a verdict; a ``pass`` or ``fail`` names none. A claim is the
    agent's pick, recorded for audit. A mechanical condition holds only when
    the engine finds it true over the change (A4.2): false or unanswerable,
    and the ``na`` is refused, naming why."""
    claims = [c.id for c in rule.check.not_applicable_when if c.claim is not None]
    if sub.status != "na":
        if sub.na_condition is not None:
            return [Problem("P5", f"a {sub.status} names no na condition",
                            "drop --na-condition, or answer na")]
        return []
    problems: list[Problem] = []
    if sub.citations or sub.tests:
        problems.append(Problem("P5", "an na carries no citations and no tests",
                                "drop the citations and tests, or answer pass or fail"))
    declared = [c.id for c in rule.check.not_applicable_when]
    if not declared:
        problems.append(Problem(
            "P5", f"{rule.id} declares no not_applicable_when condition, so na is not available",
            "answer pass or fail",
        ))
        return problems
    if sub.na_condition is None:
        problems.append(Problem("P5", "an na names the condition it rests on",
                                f"name one of {', '.join(declared)}"))
        return problems
    condition = next(
        (c for c in rule.check.not_applicable_when if c.id == sub.na_condition), None
    )
    if condition is None:
        problems.append(Problem("P5", f"{rule.id} declares no condition {sub.na_condition!r}",
                                f"name one of {', '.join(declared)}"))
    elif condition.mechanical is not None:
        problems.extend(_mechanical_problems(rule, condition, facts, claims))
    return problems


def _mechanical_problems(
    rule: Guideline, condition: NaCondition, facts: Facts, claims: Sequence[str]
) -> list[Problem]:
    """A mechanical condition, put to the engine. It is asked only over a
    changed set that could be established; P1 names the one that could not."""
    otherwise = "answer pass or fail with the evidence the rule declares" + (
        f", or name a claim that is true ({', '.join(claims)})" if claims else ""
    )
    mechanical = f"{condition.id} is a mechanical condition"
    if facts.touched is None:
        return [Problem("P5", f"{mechanical}, and the changed set could not be established",
                        otherwise)]
    if facts.na is None:
        return [Problem("P5", f"{mechanical}, and the engine's answer to it was not read",
                        otherwise)]
    answer = facts.na(rule, condition, facts.touched)
    if answer.holds is True:
        return []
    if answer.holds is False:
        return [Problem(
            "P5", f"{mechanical}, and the engine finds it false over this change: "
            f"{answer.reason}", otherwise,
        )]
    return [Problem(
        "P5", f"{mechanical}, and the engine finds it unanswerable over this change: "
        f"{answer.reason}",
        otherwise,
    )]


# -- P6, the note is bounded and inert ---------------------------------------------------------------


def note_problems(sub: Submitted, facts: Facts) -> list[Problem]:
    """P6. At most ``note_max_chars`` characters, and no privacy finding."""
    if sub.note is None:
        return []
    problems: list[Problem] = []
    if len(sub.note) > facts.note_max_chars:
        problems.append(Problem(
            "P6",
            f"the note is {len(sub.note)} characters, over the cap of {facts.note_max_chars}",
            "shorten it; guidelines.verdicts.note_max_chars sets the cap",
        ))
    found = list(facts.scan(sub.note))
    if found:
        problems.append(Problem(
            "P6", f"the note fails the privacy scan ({'; '.join(found)})",
            "remove it from the note",
        ))
    return problems


# -- one entry, and a whole submission -------------------------------------------------------------


def check(
    sub: Submitted,
    rule: Guideline | None,
    facts: Facts,
    ids: Sequence[str] = (),
    index: int = 0,
    *,
    submitting: bool = True,
) -> Checked:
    """Every predicate over one submitted verdict, every problem named.

    ``submitting=False`` is the gate's re-check of a recorded verdict: every
    predicate but the review-role one (DN-2).
    """
    problems = applies_problems(sub, rule, facts, ids, submitting=submitting)
    if rule is None or rule.check.kind != "judgement":
        return Checked(index=index, rule=sub.rule, record=None, problems=tuple(problems))
    problems += evidence_problems(sub, rule)
    citations, cited = citation_problems(sub, rule, facts, submitting=submitting)
    problems += cited
    problems += selector_problems(sub, facts.test_runs)
    problems += na_problems(sub, rule, facts)
    problems += note_problems(sub, facts)
    if problems:
        return Checked(index=index, rule=sub.rule, record=None, problems=tuple(problems))
    return Checked(index=index, rule=sub.rule, record=VerdictRecord(
        rule=rule.id, status=sub.status, citations=citations, tests=sub.tests,
        na_condition=sub.na_condition, note=sub.note,
        ruleset=facts.pin or facts.tree_digest, state=facts.state, surface=facts.surface,
    ))


def check_all(parsed: Parsed, ruleset: Ruleset, facts: Facts) -> tuple[Checked, ...]:
    """Each entry checked. One rule named twice is refused at the second entry,
    because which of the two answers the rule could not be said."""
    ids = tuple(r.id for r in ruleset.rules)
    first: dict[str, int] = {}
    results: list[Checked] = []
    for entry in parsed.entries:
        if entry.submitted is None:
            results.append(Checked(index=entry.index, rule=None, record=None,
                                   problems=entry.problems))
            continue
        result = check(entry.submitted, ruleset.by_id(entry.submitted.rule), facts, ids,
                       index=entry.index)
        seen = first.setdefault(entry.submitted.rule, entry.index)
        if seen != entry.index:
            twice = _shape(
                f"rule {entry.submitted.rule} is answered twice, here and in entry {seen}",
                "give one verdict per rule in a submission",
            )
            result = Checked(index=entry.index, rule=entry.submitted.rule, record=None,
                             problems=(twice, *result.problems))
        results.append(result)
    return tuple(results)


# -- the gate: which rules are owed, and how each stands (A4.3) ------------------------------------

#: How an owed rule stands at the gate.
#: ``current``: its latest verdict still holds. ``missing``: it has none.
#: ``stale``: its latest no longer holds, and is treated as missing (D5).
#: ``engine``: a mechanical N/A condition the engine finds true answers it.
CURRENT = "current"
MISSING = "missing"
STALE = "stale"
ENGINE = "engine"

#: The keys of a record the engine writes and a submission may not carry.
_RECORD_ONLY_KEYS = frozenset({"state", "surface", "agent", "token"})


@dataclass(frozen=True)
class Standing:
    """One owed judgement rule at the gate, and the answer it has."""

    rule: str
    severity: str
    standing: str
    #: The answering verdict's status: the current one's, or, for ``engine``,
    #: ``na``. ``None`` for a rule with no verdict that holds.
    status: str | None = None
    #: For ``engine``: the condition that holds and the engine's reason.
    reason: str = ""
    #: For ``stale``: every predicate the latest verdict now fails.
    problems: tuple[Problem, ...] = ()
    #: For ``engine``: the status of a pass or fail recorded on the rule,
    #: which the gate sets aside because the rule is not applicable.
    set_aside: str | None = None
    #: The reason of the live grant naming this rule that passes its failure
    #: in this evaluation (ADR-012 ruling 4, DN-9, A4.5); ``None`` when no
    #: grant passed anything here.
    override: str | None = None

    @property
    def failing(self) -> bool:
        """What the rule's answer alone would do to the gate: no verdict that
        holds, or a current fail at blocker severity. A grant does not change
        it; it is what a grant that passes the rule decided."""
        if self.standing in (MISSING, STALE):
            return True
        return self.standing == CURRENT and self.status == "fail" and self.severity == "blocker"

    @property
    def overridden(self) -> bool:
        """A grant passes this rule's failure here, named, never counted."""
        return self.override is not None

    @property
    def blocks(self) -> bool:
        """It fails the gate: failing, and passed by no grant."""
        return self.failing and not self.overridden

    @property
    def warns(self) -> bool:
        """A current fail at warn severity, passed by no grant: reported,
        never blocking (D5)."""
        return (
            self.standing == CURRENT and self.status == "fail"
            and self.severity != "blocker" and not self.overridden
        )

    def describe(self) -> str:
        told = self._answer()
        if self.override is not None:
            told += (
                f"; passed by override, granted with `invariant override {self.rule}` "
                f"(reason: {self.override})"
            )
        return told

    def _answer(self) -> str:
        if self.standing == MISSING:
            return f"{self.rule}: no verdict"
        if self.standing == STALE:
            why = "; ".join(p.describe() for p in self.problems)
            return f"{self.rule}: the latest verdict no longer holds ({why})"
        if self.standing == ENGINE:
            told = f"{self.rule}: na, answered by the engine ({self.reason})"
            if self.set_aside is not None:
                told += f"; the {self.set_aside} recorded on it is set aside"
            return told
        if self.status == "fail":
            return f"{self.rule}: fail at {self.severity}"
        return f"{self.rule}: {self.status}"

    def unpassed(self) -> str:
        """What the rule's answer alone says, without the grant's clause: the
        failure a grant passed, as the spend names it."""
        return self._answer()

    def record(self) -> dict[str, Any]:
        """The standing as plain data: what ``guidelines verdicts show --json``
        prints for an owed rule, and what the landing keeps (A4.6, DN-7)."""
        return {
            "rule": self.rule, "severity": self.severity, "standing": self.standing,
            "status": self.status, "reason": self.reason,
            "problems": [
                {"predicate": p.predicate, "message": p.message, "fix": p.fix}
                for p in self.problems
            ],
            "set_aside": self.set_aside, "blocks": self.blocks, "warns": self.warns,
            "override": self.override, "overridden": self.overridden,
        }


def not_owed(rule: Guideline, facts: Facts) -> str | None:
    """Why the change owes ``rule`` no verdict, or ``None`` when it owes one.

    The one reading of DN-2 and DN-3, which :func:`owed` asks too, so a rule
    ``guidelines verdicts show`` calls not owed is one the gate does not ask
    about. A rule is owed when it is a judgement guideline, its phases name
    review, landing or always (DN-2), and a changed file present in the tree
    meets it (DN-3, ``guidelines.meets``): under ``applies_to``, not
    path-excepted; a deleted file owes nothing. With no changed set nothing
    is owed, and the gate refuses first.
    """
    if rule.check.kind != "judgement":
        return f"it is a {rule.check.kind} guideline, which the engine answers"
    if not set(rule.phases) & OWED_PHASES:
        return (
            f"its phases ({', '.join(rule.phases)}) name neither review, landing "
            f"nor always"
        )
    if facts.touched is None:
        return "the changed set could not be established"
    if not any(meets(rule, rel) for rel in facts.touched):
        return (
            f"no changed file in the tree falls under its applies_to "
            f"({', '.join(rule.applies_to)}) outside its path exceptions"
        )
    return None


def owed(ruleset: Ruleset, facts: Facts) -> tuple[Guideline, ...]:
    """The judgement rules this change owes a verdict, in ruleset order:
    every rule :func:`not_owed` finds no reason for."""
    return tuple(rule for rule in ruleset.rules if not_owed(rule, facts) is None)


def resubmitted(record: Mapping[str, Any]) -> tuple[Submitted | None, tuple[Problem, ...]]:
    """A recorded verdict read back as the submission it was, to check again.

    The keys only the engine writes (``state``, ``surface``, ``agent``,
    ``token``) are dropped. The recorded ``ruleset`` and blobs stay, so the checks compare
    them with the tree in hand: that is what makes a drifted ruleset or an
    edited cited file stale.
    """
    return parse_entry({k: v for k, v in record.items() if k not in _RECORD_ONLY_KEYS})


def _engine_answer(rule: Guideline, facts: Facts) -> NaAnswer | None:
    """The first mechanical N/A condition the engine finds true, or ``None``.

    False or unanswerable never sets a rule aside; nor does no answer read.
    A claim is never put to the engine.
    """
    if facts.na is None or facts.touched is None:
        return None
    for condition in rule.check.not_applicable_when:
        if condition.mechanical is None:
            continue
        answer = facts.na(rule, condition, facts.touched)
        if answer.holds is True:
            return answer
    return None


def standing_of(
    rule: Guideline,
    records: Sequence[Any],
    facts: Facts,
    grants: Mapping[str, str] | None = None,
) -> Standing:
    """How one owed rule stands: answered by the engine, or by its latest
    recorded verdict checked again, or not at all; and whether a grant
    passes it.

    The latest record answers the rule: it is the submitter's current word,
    and an earlier one it superseded is never brought back, even when the
    latest has gone stale.

    A live grant naming the rule (``grants`` maps a guideline id to its
    reason) passes every failure of it in this evaluation, a warn rule's
    included (DN-9): no verdict, a stale one, and a current fail at either
    severity. It passes nothing where nothing failed: a current pass or
    ``na``, and a rule the engine's N/A answers, since the engine's answer
    stands over any record and any grant (the A4.3 ruling).
    """
    found = _standing_of(rule, records, facts)
    reason = (grants or {}).get(rule.id)
    if reason is not None and (found.failing or found.warns):
        return replace(found, override=reason)
    return found


def _standing_of(rule: Guideline, records: Sequence[Any], facts: Facts) -> Standing:
    mine = [r for r in records if isinstance(r, Mapping) and r.get("rule") == rule.id]
    latest = mine[-1] if mine else None
    answer = _engine_answer(rule, facts)
    if answer is not None:
        status = latest.get("status") if latest is not None else None
        return Standing(
            rule=rule.id, severity=rule.severity, standing=ENGINE, status="na",
            reason=f"{answer.condition} holds: {answer.reason}" if answer.reason
            else f"{answer.condition} holds",
            set_aside=status if status in ("pass", "fail") else None,
        )
    if latest is None:
        return Standing(rule=rule.id, severity=rule.severity, standing=MISSING)
    sub, shape = resubmitted(latest)
    if sub is None:
        return Standing(rule=rule.id, severity=rule.severity, standing=STALE, problems=shape)
    result = check(sub, rule, facts, submitting=False)
    if result.problems:
        return Standing(
            rule=rule.id, severity=rule.severity, standing=STALE, problems=result.problems,
        )
    return Standing(rule=rule.id, severity=rule.severity, standing=CURRENT, status=sub.status)


def standings(
    ruleset: Ruleset,
    records: Sequence[Any],
    facts: Facts,
    grants: Mapping[str, str] | None = None,
) -> tuple[Standing, ...]:
    """Every owed judgement rule's standing, in ruleset order (D5, "The gate"),
    each failure a live grant passes marked with its reason."""
    return tuple(
        standing_of(rule, records, facts, grants) for rule in owed(ruleset, facts)
    )


@dataclass(frozen=True)
class Unused:
    """A live grant naming a judgement guideline that passes nothing here."""

    rule: str
    reason: str
    #: Why it passes nothing: the answer holds, or nothing is owed.
    why: str

    def describe(self) -> str:
        return (
            f"override held for {self.rule} (reason: {self.reason}), unused here: "
            f"{self.why}, so nothing spends it; withdraw it with "
            f"invariant override {self.rule} --clear"
        )


def unused_grants(
    found: Sequence[Standing], grants: Mapping[str, str]
) -> tuple[Unused, ...]:
    """Each grant in ``grants`` that passes none of ``found``, in grant order.

    A grant is named whether used or not, so a held one is never silent: one
    on a rule whose latest verdict holds, one on a rule the engine answers,
    and one on a rule this change does not owe a verdict.
    """
    by_rule = {s.rule: s for s in found}
    unused: list[Unused] = []
    for rule_id, reason in grants.items():
        s = by_rule.get(rule_id)
        if s is not None and s.overridden:
            continue
        if s is None:
            why = "the change does not owe it a verdict"
        elif s.standing == ENGINE:
            why = "the engine answers it"
        else:
            why = "its latest verdict holds"
        unused.append(Unused(rule=rule_id, reason=reason, why=why))
    return tuple(unused)


# -- recorded verdicts the change no longer owes (A4.4) ----------------------------------------


@dataclass(frozen=True)
class Unowed:
    """Verdicts recorded on a rule the change does not owe one, and why not.

    The gate never reads them; ``guidelines verdicts show`` lists them so a
    verdict that stopped counting does not vanish without a word.
    """

    rule: str
    #: The latest record's status as recorded; ``None`` when it is not text.
    status: str | None
    #: How many records name the rule.
    recorded: int
    reason: str

    def describe(self) -> str:
        said = self.status if self.status is not None else "an unreadable record"
        return f"{self.rule}: {said}, {self.recorded} recorded; not owed: {self.reason}"


def unowed(ruleset: Ruleset, records: Sequence[Any], facts: Facts) -> tuple[Unowed, ...]:
    """Each rule with a recorded verdict that the change does not owe one, in
    the order each was first recorded. A record naming no rule names nothing.
    Why it is not owed is :func:`not_owed`'s answer, or the rule is gone from
    the ruleset."""
    owing = {rule.id for rule in owed(ruleset, facts)}
    by_rule: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        if isinstance(record, Mapping) and isinstance(record.get("rule"), str):
            by_rule.setdefault(record["rule"], []).append(record)
    found: list[Unowed] = []
    for rule_id, mine in by_rule.items():
        if rule_id in owing:
            continue
        rule = ruleset.by_id(rule_id)
        reason = (
            f"no guideline {rule_id!r} in the ruleset" if rule is None
            else not_owed(rule, facts) or ""
        )
        status = mine[-1].get("status")
        found.append(Unowed(
            rule=rule_id, status=status if isinstance(status, str) else None,
            recorded=len(mine), reason=reason,
        ))
    return tuple(found)
