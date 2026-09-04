"""The deliverable set a session owes, read without owning it.

A session that outlives one agent-host session needs somewhere to record what
it is still for. That record is a list of dicts on the session document, and
everything in this module *reads* it: what the entries are, what number the
next one gets, how far along it is, and how to print it.

**Why `core/` and not the domain.** The mutations belong to
``domains/session`` — it owns the store, the locking and the refusals — but the
questions do not. A later increment asks "is this session's plan still open?"
from inside a *rule*, and ``docs/architecture/overview.md`` states the rule that
makes that possible: arrows point down, so ``core/`` may not import
``domains/``. The precedent is ``core/stats.breakdown``, which does the
arithmetic that ``domains/stats`` renders. One home for the logic, two callers
above it.

**Why every reader tolerates rubbish.** ``docs/architecture/state_model.md``
promises the state files are "JSON, hand-inspectable, and hand-fixable", which
is the same sentence as "a hand can get one wrong". A reader that raises on a
malformed entry turns a typo in a checklist into a session nobody can query —
including the query that would tell them what they broke. So a non-mapping
entry is dropped, a missing key takes its default, and an unusable number falls
back to the entry's position. Nothing here refuses; refusing is
:meth:`SessionDomain.plan_add`'s job, at the one boundary where a bad entry can
still be kept out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: One entry as it is stored. Spelled out here rather than in a docstring
#: because three callers construct the rendering from it.
#:
#: ``{"n": int, "title": str, "done": bool, "done_at": float | None}``
_DEFAULTS: dict[str, Any] = {"n": 0, "title": "", "done": False, "done_at": None}


@dataclass(frozen=True)
class Progress:
    """How far along a plan is, and whether "along" is even the right word.

    ``complete`` is the field with teeth. An **empty** plan is not complete: a
    session with no deliverables is *unplanned*, not *finished*, and a later
    increment switches an entire mode on that distinction. Written at each call
    site it would be written as ``done == total`` at least once, which is
    ``True`` for an empty plan and quietly declares every unplanned session
    finished. It is computed once, here.
    """

    total: int
    done: int
    #: The open entries, in the order they were recorded. A checklist read out
    #: of order is a different checklist.
    remaining: tuple[dict, ...]
    complete: bool

    def describe(self) -> str:
        """One phrase, for a refusal that has to fit on a line."""
        if not self.total:
            return "no deliverables recorded"
        return f"{self.done} of {self.total} done"

    @property
    def fraction(self) -> str:
        """``2/5``. The compact spelling, for a clause inside a sentence.

        A sibling of :meth:`describe` rather than a replacement: that one
        reads as prose and answers the empty case in words, this one is two
        numbers and a slash. Four call sites want the compact form — the
        resume line, the ``plan show`` header, the turn projection and the
        greeting's offer — and four hand-built f-strings is four chances to
        print a total that disagrees with the numbers the commands take.

        The empty plan renders ``0/0`` rather than a placeholder. A ``-``
        belongs to a TABLE column, where every cell must be filled with
        something; a caller writing a sentence wants the numbers, and a caller
        that wants to say "unplanned" has :attr:`total` and :meth:`describe`.
        """
        return f"{self.done}/{self.total}"


def entries(plan: Any) -> list[dict]:
    """The plan as a list of well-formed entries, in recorded order.

    Hands back **copies**. A caller that wants to change an entry goes through
    the domain, which is the only thing that can persist the change — handing
    out live references would offer a mutation that silently does not save.
    """
    if not isinstance(plan, Sequence) or isinstance(plan, (str, bytes)):
        return []
    out: list[dict] = []
    for position, raw in enumerate(plan, start=1):
        if not isinstance(raw, Mapping):
            continue
        entry = dict(_DEFAULTS)
        entry.update({k: raw[k] for k in _DEFAULTS if k in raw})
        entry["n"] = _number(entry["n"], position)
        entry["title"] = str(entry["title"] or "")
        entry["done"] = bool(entry["done"])
        out.append(entry)
    return out


def next_n(plan: Any) -> int:
    """The number the next deliverable gets. Never one that has been used.

    ``max(existing) + 1``, deliberately, rather than ``len(plan) + 1``: numbers
    are how a caller names an entry, so reusing one would make ``plan done 3``
    mean a different thing after a ``plan remove`` than it did before it. The
    cost is gaps in the sequence, which are cheap and visible; the alternative
    costs a command that acts on the wrong deliverable and looks like it worked.
    """
    return max((e["n"] for e in entries(plan)), default=0) + 1


def progress(plan: Any) -> Progress:
    """Totals, the open set, and whether the plan is finished."""
    got = entries(plan)
    remaining = tuple(e for e in got if not e["done"])
    return Progress(
        total=len(got),
        done=len(got) - len(remaining),
        remaining=remaining,
        complete=bool(got) and not remaining,
    )


def render_lines(plan: Any) -> list[str]:
    """The plan as lines, ready to indent.

    One renderer, because three callers will want this and three renderings of
    one list is three chances for the numbers to disagree with the numbers a
    command takes. The empty case is answered *here* rather than left to each
    caller, for the same reason: "unplanned" is a state worth naming, and a
    caller handed an empty list will print nothing and say nothing.
    """
    got = entries(plan)
    if not got:
        return ["(no deliverables recorded)"]
    width = max(len(str(e["n"])) for e in got)
    return [
        f"[{'x' if e['done'] else ' '}] {e['n']:>{width}}  {e['title']}" for e in got
    ]


def summary_lines(plan: Any) -> list[str]:
    """The plan as a turn projection shows it: a headline and what is next.

    Deliberately **not** :func:`render_lines`, and the difference is the
    question each answers. ``render_lines`` was asked — somebody typed
    ``session plan show`` — so it lists every entry against the number the
    commands take. This one is injected into every turn of every project, so
    it is capped at two lines however long the plan is and everything past the
    second open deliverable lives behind the command that prints it.

    Two behaviours are the reason this is a second reader rather than a flag
    on the first.

    **An empty plan renders nothing at all**, which is the exact opposite of
    ``render_lines``' ``(no deliverables recorded)``. That answer is right for
    a direct question and wrong here: most projects keep no checklist, and a
    permanent line reporting the absence of one is the noise that teaches a
    reader to skip the whole block — including the turns where it says
    something.

    **A complete plan names the finish.** ``5/5 done`` is the state
    ``session.deliverables_complete`` is waiting for, so the line that reports
    it names the command that acts on it. Left unsaid, the projection would
    report a finished checklist every turn and never say what to do about one.

    Both numbers come from :func:`progress`, which is what ``render_lines``
    counts from too, so the two renderings cannot disagree about how far along
    the plan is.
    """
    got = progress(plan)
    if not got.total:
        return []
    if got.complete:
        return [
            f"plan {got.fraction} done — COMPLETE",
            "  every deliverable is done; land it: invariant session review",
        ]
    lines = [f"plan {got.fraction} done — next: {got.remaining[0]['title']}"]
    if len(got.remaining) > 1:
        lines.append(f"  then: {got.remaining[1]['title']}")
    return lines


def _number(value: Any, position: int) -> int:
    """The entry's own number, or where it sits if that cannot be read."""
    try:
        return int(value) or position
    except (TypeError, ValueError):
        return position
