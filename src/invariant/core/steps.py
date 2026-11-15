"""A project's own steps (ADR-021): their declarations, and their recorded runs.

A step is a shell command a project declares under ``steps``, at one
lifecycle point, as a rule ``step.<name>`` (D1 to D3). This module is the
read side: what a manifest declares and the load's refusals, which state of
which machine a point guards, which tree a step judges, and the record of
runs and debts under the state root. Running a step and writing the record
are ``domains/steps``'; the rule that judges a step from its record is in
``core/rules``. Nothing here runs a command.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import git
from .errors import ManifestError

#: The rule namespace a step's rule lives in. Fixed rules never use it, so a
#: step can never collide with a registered id (D3).
RULE_PREFIX = "step."

#: The before-points (D2). A failing ``block`` step at one refuses its action.
PIPELINE_ENTER = "pipeline.enter."
BEFORE_MERGE = "session.before_merge"
BEFORE_TEARDOWN = "session.before_teardown"
BEFORE_HANDOFF = "session.before_handoff"

#: The after-point (D2): the merge has happened when it runs, so a failing
#: ``block`` step there cannot refuse it, and holds the next transition
#: instead (ruling 2).
AFTER_MERGE = "session.after_merge"

#: Every session point, in the order a session meets them.
POINTS = (BEFORE_MERGE, AFTER_MERGE, BEFORE_TEARDOWN, BEFORE_HANDOFF)

#: The session state each before-point is an entry gate of, and so the edge
#: whose command runs its steps. The handoff is an event, not an edge
#: (state_model, "Handing off"), so its step is judged by ``session handoff``
#: itself and rides no state.
SESSION_STATES = {BEFORE_MERGE: "merged", BEFORE_TEARDOWN: "torn_down"}

#: The state an after-point's step holds (D5): an entry gate of it, which
#: the edge into it judges and never runs. ``session handoff`` judges it too.
HOLDS = {AFTER_MERGE: "torn_down"}

#: The session states a merge leaves a session in, where an after-merge
#: step's rule is judged; in any other the session has merged nothing yet.
MERGED_STATES = ("merged", "pushed")

#: The keys a declaration may carry; any other is refused (D1).
KEYS = ("at", "run", "level", "owed_exit", "timeout", "summary")
LEVELS = ("block", "warn", "off")
DEFAULT_LEVEL = "block"
DEFAULT_TIMEOUT = 600
NAME = re.compile(r"[a-z][a-z0-9_]*")

#: Where the runs and debts live, under the state root: one document for the
#: project, keyed by step name. Its lock's stem, ``steps``, is no session id.
RECORD = "steps.json"
#: How many runs of one step the record keeps, newest last.
RUNS_KEPT = 20
#: How many lines of a run's output the record keeps.
TAIL_LINES = 20
TAIL_CHARS = 2000

PASS, FAIL, OWED = "pass", "fail", "owed"


@dataclass(frozen=True)
class Step:
    """One declaration, resolved: every default filled."""

    name: str
    at: str
    run: str
    level: str = DEFAULT_LEVEL
    owed_exit: int | None = None
    timeout: float = DEFAULT_TIMEOUT
    summary: str = ""

    @property
    def rule_id(self) -> str:
        return RULE_PREFIX + self.name

    @property
    def pipeline_state(self) -> str | None:
        """The pipeline state this step is an entry gate of, if it is one."""
        return self.at[len(PIPELINE_ENTER):] if self.at.startswith(PIPELINE_ENTER) else None


def rule_id(name: str) -> str:
    return RULE_PREFIX + name


def step_name(rule: str) -> str | None:
    """The step a ``step.<name>`` id names, or ``None`` for any other id."""
    return rule[len(RULE_PREFIX):] if rule.startswith(RULE_PREFIX) else None


def _level(value: Any) -> str:
    # YAML reads a bare `off` as false, as for an enforcement level.
    if value is False:
        return "off"
    return str(value).strip().lower()


def declared(manifest) -> dict[str, Step]:
    """Every step the resolved manifest declares, by name. Validated at load."""
    raw = manifest.get("steps")
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, Step] = {}
    for name, body in raw.items():
        body = body if isinstance(body, Mapping) else {}
        at = str(body.get("at"))
        summary = body.get("summary")
        out[str(name)] = Step(
            name=str(name),
            at=at,
            run=str(body.get("run")),
            level=_level(body.get("level", DEFAULT_LEVEL)),
            owed_exit=body.get("owed_exit"),
            timeout=body.get("timeout") or DEFAULT_TIMEOUT,
            summary=str(summary) if summary else f"the project's step {name}, at {at}",
        )
    return out


def due(manifest, at: str) -> list[Step]:
    """The steps a lifecycle command runs at ``at``: every one not ``off``.

    The level is the resolved rule level, so ``enforcement.rules`` and
    ``INVARIANT_DISABLE`` turning a step off stop it running, too.
    """
    return [
        step for step in declared(manifest).values()
        if step.at == at and manifest.rule_level(step.rule_id).value != "off"
    ]


def pipeline_gates(manifest) -> dict[str, list[str]]:
    """``pipeline.enter`` steps by the state they are entry gates of."""
    gates: dict[str, list[str]] = {}
    for step in declared(manifest).values():
        if step.pipeline_state:
            gates.setdefault(step.pipeline_state, []).append(step.rule_id)
    return gates


def session_gates(manifest) -> dict[str, list[str]]:
    """Session-point steps by the session state they are entry gates of: the
    state a before-point guards, or the one an after-point holds."""
    gates: dict[str, list[str]] = {}
    for step in declared(manifest).values():
        state = SESSION_STATES.get(step.at) or HOLDS.get(step.at)
        if state:
            gates.setdefault(state, []).append(step.rule_id)
    return gates


def gates_for(manifest, state: str, *, pipeline: bool) -> list[str]:
    """The step gates on ``state`` of a pipeline or the session machine."""
    by_state = pipeline_gates(manifest) if pipeline else session_gates(manifest)
    return by_state.get(state, [])


# -- the load's refusals (D1, D2) ----------------------------------------------


def validate(
    raw: Any,
    *,
    lane_states: Callable[[], Mapping[str, tuple[Iterable[str], Iterable[str]]]],
    placeholders_unfilled: Callable[[str], list[str]],
) -> None:
    """Refuse a ``steps`` section that would load as something else.

    ``lane_states`` answers every state of every lane a pipeline may start in
    (``pipeline_ids``), each with the lanes that have an edge into it and the
    lanes that start in it, asked only when a ``pipeline.enter`` point is
    declared. ``placeholders_unfilled`` answers the ``{name}`` placeholders of
    a command that neither ``vars`` nor ``discover`` fills.
    """
    if raw is None:
        return
    if not isinstance(raw, Mapping):
        raise ManifestError(
            f"steps must be a mapping of step name to its declaration, got {raw!r}"
        )
    states: Mapping[str, tuple[Iterable[str], Iterable[str]]] | None = None
    for name, body in raw.items():
        where = f"steps.{name}"
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ManifestError(
                f"{where}: a step name is lower case letters, digits and "
                f"underscores, starting with a letter ([a-z][a-z0-9_]*)"
            )
        if not isinstance(body, Mapping):
            raise ManifestError(
                f"{where} must be a mapping with at least 'at' and 'run', got {body!r}"
            )
        for key in body:
            if key not in KEYS:
                raise ManifestError(
                    f"{where}.{key} is not a key this build reads; a step reads "
                    + ", ".join(f"{where}.{k}" for k in KEYS) + "."
                )
        for required in ("at", "run"):
            if required not in body:
                raise ManifestError(f"{where} has no '{required}'; a step needs one")
        _validate_at(where, body["at"])
        if str(body["at"]).startswith(PIPELINE_ENTER):
            if states is None:
                states = lane_states()
            state = str(body["at"])[len(PIPELINE_ENTER):]
            if state not in states:
                raise ManifestError(
                    f"{where}.at names {body['at']!r}, but no lane a pipeline may "
                    f"start in has a state {state!r}; the states are "
                    + ", ".join(sorted(states)) + "."
                )
            entered, initial = states[state]
            if not list(entered):
                # A pipeline starts in its initial state and is never advanced
                # into it, so a step there would load and never run.
                raise ManifestError(
                    f"{where}.at names {body['at']!r}, but {state!r} is the state "
                    f"the {', '.join(sorted(initial))} lane(s) start in, and no "
                    f"lane has an edge into it: a pipeline starts there and is "
                    f"never advanced into it, so this step would never run. Name "
                    f"the state after it instead."
                )
        run = body["run"]
        if not isinstance(run, str) or not run.strip():
            raise ManifestError(f"{where}.run must be a non-empty shell command")
        unfilled = placeholders_unfilled(run)
        if unfilled:
            raise ManifestError(
                f"{where}.run needs {', '.join('{' + p + '}' for p in unfilled)}, "
                f"which neither vars nor discover supplies"
            )
        if "level" in body and _level(body["level"]) not in LEVELS:
            raise ManifestError(
                f"{where}.level is {body['level']!r}; a step's level is one of "
                + ", ".join(LEVELS) + " (ADR-021 D1)"
            )
        if "owed_exit" in body and body["owed_exit"] is not None:
            owed = body["owed_exit"]
            if isinstance(owed, bool) or not isinstance(owed, int) or not 1 <= owed <= 255:
                raise ManifestError(
                    f"{where}.owed_exit is {owed!r}; it is an exit code from 1 to "
                    f"255 (0 already means pass; 75, EX_TEMPFAIL, is the usual one)"
                )
        if "timeout" in body:
            timeout = body["timeout"]
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
                raise ManifestError(
                    f"{where}.timeout is {timeout!r}; it is a positive number of seconds"
                )
        if "summary" in body and body["summary"] is not None:
            summary = body["summary"]
            if not isinstance(summary, str) or not summary.strip() or summary.splitlines() != [summary]:
                raise ManifestError(f"{where}.summary must be one line of text")


def _validate_at(where: str, at: Any) -> None:
    text = str(at)
    if text.startswith(PIPELINE_ENTER) and len(text) > len(PIPELINE_ENTER):
        return
    if text in POINTS:
        return
    raise ManifestError(
        f"{where}.at is {at!r}, which is no lifecycle point; the points are "
        + ", ".join([PIPELINE_ENTER + "<state>", *POINTS]) + " (ADR-021 D2)"
    )


# -- the tree a step judges ----------------------------------------------------


def tree_of(at: str, session: Mapping[str, Any] | None, cwd: Path) -> Path:
    """Where a step at ``at`` runs, and so the tree its rule judges (D2).

    The session worktree for every point but the handoff and the merge's
    after-point, which run in the main checkout (the merged commit, after a
    merge). With no session, or one with no worktree, the checkout ``cwd``
    lies in.
    """
    if at in (BEFORE_HANDOFF, AFTER_MERGE):
        return git.main_checkout(cwd) or Path(cwd)
    worktree = (session or {}).get("worktree")
    if worktree:
        return Path(worktree)
    return git.toplevel(cwd) or Path(cwd)


# -- the record -------------------------------------------------------------------


def read(store) -> dict[str, Any]:
    """The whole record, ``{name: {"runs": [...], "debts": [...]}}``."""
    data = store.read(RECORD, {})
    return data if isinstance(data, dict) else {}


def _entry(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    entry = data.get(name)
    return entry if isinstance(entry, Mapping) else {}


def runs(store, name: str) -> list[dict]:
    """The recorded runs of one step, oldest first."""
    found = _entry(read(store), name).get("runs")
    return [r for r in found if isinstance(r, dict)] if isinstance(found, list) else []


def last_run(store, name: str) -> dict | None:
    found = runs(store, name)
    return found[-1] if found else None


def open_debts(store, name: str | None = None) -> list[dict]:
    """Every open debt, of one step or of all, in the order they were owed."""
    data = read(store)
    names = [name] if name is not None else sorted(data)
    out: list[dict] = []
    for each in names:
        debts = _entry(data, each).get("debts")
        if isinstance(debts, list):
            out += [d for d in debts if isinstance(d, dict)]
    return out


def matching_run(found: Iterable[Mapping[str, Any]], signature: tuple[str, str]) -> Mapping[str, Any] | None:
    """The latest run recorded over this tree: its commit, and a digest taken
    before or after the run (a step may write its own output)."""
    commit, digest = signature
    for record in reversed(list(found)):
        if record.get("commit") == commit and digest in (record.get("tree"), record.get("tree_after")):
            return record
    return None


def holding_run(
    found: Iterable[Mapping[str, Any]], merged: str, contains: Callable[[str], bool]
) -> Mapping[str, Any] | None:
    """The run that decides whether an after-merge step holds a session
    merged at ``merged`` (ruling 2, D5), or ``None`` when no run does.

    The newest run at the merged commit or at a later commit containing it
    (``contains``) decides: a ``pass`` or an ``owed`` releases the hold, a
    ``fail`` keeps it. One install of the newest build covers every merge
    before it, and a newer build that fails holds every merge it carries.
    ``None`` holds too (fail closed, the parent's ruling of 2026-11-15 under
    ADR-001 D25): a merge that ran no step, forced, walked by a transition or
    made before the step was declared, is held until one runs. A run's tree
    digest is not read: the main checkout is not the session's to edit.
    """
    for record in reversed(list(found)):
        commit = record.get("commit")
        if commit and (commit == merged or contains(str(commit))):
            return record
    return None


def debt_summary(debts: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Open debts grouped by step: ``{step, count, since}``, ``since`` the
    commit of the oldest."""
    grouped: dict[str, dict[str, Any]] = {}
    for debt in debts:
        step = str(debt.get("step"))
        row = grouped.setdefault(step, {"step": step, "count": 0, "since": debt.get("commit")})
        row["count"] += 1
    return list(grouped.values())


def short(commit: Any) -> str:
    return str(commit or "")[:7] or "?"


def debts_line(debts: Iterable[Mapping[str, Any]]) -> str:
    """The open debts in one line, for a handoff note; empty with none."""
    rows = debt_summary(debts)
    if not rows:
        return ""
    return "owed steps: " + ", ".join(
        f"{row['step']} ({row['count']}, since {short(row['since'])})" for row in rows
    )


def last_line(text: str) -> str:
    """The last non-blank line of a run's output, or nothing."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""
