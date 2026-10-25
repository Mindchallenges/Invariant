"""The control panel's view model: groups, counts and every word the phone shows.

The oracle's web page is a mobile control panel (IDEA_027, ADR-009). What it
draws is decided here, from the :func:`~invariant.oracle.recommend.assess`
document and nothing else, so it is pure and the suite tests it without a
browser:

* **Groups by attention** (ADR-009 D2). A row joins the first group whose rule
  holds: needs you, problems, working, idle. The quiet orphan hosts and leaked
  worktrees are leftovers, counted on the strip and listed on a screen of
  their own. Within a group, higher severity comes first, then the oldest
  condition, then the document's order.
* **One wording table** (ADR-009 D3). Every chip, sentence, button, confirm
  title and fixed label the page shows is spelled in this module, keyed by the
  engine's facts: the signal and its cause, the methodology and phase, the
  recommended command. A fact the table does not know gets no phrase, and is
  never shown raw on a card. The engine's own names (state, lane, host id,
  pid, path) appear only under "Technical details".
* **The page holds no vocabulary.** :data:`WORDS` is embedded in the page by
  ``oracle/web``; the rest arrives with every ``/api/state`` read.

It imports nothing that runs anything: no ``actions``, no ``webactions``, no
``hosts/``. The table is the design of record's, in
``docs/architecture/oracle-panel.md``, "Wording".
"""

from __future__ import annotations

import math
import os
import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .recommend import CLI, RUN, TERMINAL
from .scan import ROW_ERROR, ROW_HOST, ROW_WORKTREE, row_key
from .signals import (
    ERROR,
    FINISHED,
    GATE_BLOCKED,
    HIGH,
    HOST_GONE,
    INFO,
    LOW,
    MEDIUM,
    ORPHAN_HOST,
    PARKED,
    STRANDED,
    STUCK,
    TEARDOWN_PENDING,
    TURN_BUSY,
    WAITING,
    WAITING_INPUT,
    YOUR_TURN,
)

NEEDS = "needs"
PROBLEMS = "problems"
WORKING = "working"
IDLE = "idle"
LEFTOVERS = "leftovers"

#: Chip tones: the accent each is drawn in.
TONE_NEEDS = "needs"
TONE_PROBLEM = "problem"
TONE_WORKING = "working"
TONE_IDLE = "idle"

#: Leftover rows shown before "Show N more".
LEFTOVERS_SHOWN = 3

_SEVERITY = {HIGH: 0, MEDIUM: 1, LOW: 2, INFO: 3}


@dataclass(frozen=True)
class Group:
    id: str
    heading: str
    #: The strip's label; ``None`` for a group without a tile.
    tile: str | None
    compact: bool
    #: The signal kinds that put a row here.
    kinds: frozenset[str] = frozenset()


GROUPS = (
    Group(NEEDS, "Needs you", "Need you", False, frozenset({WAITING, FINISHED})),
    Group(PROBLEMS, "Problems", "Problems", False,
          frozenset({ERROR, GATE_BLOCKED, STUCK, HOST_GONE, STRANDED})),
    Group(WORKING, "Working", "Working", True),
    Group(IDLE, "Idle", None, True),
)

#: The fixed strings: labels, headings and templates. ``{name}`` is filled in.
WORDS: dict[str, str] = {
    "title": "Sessions",
    "doc_title": "({count}) Sessions",
    "app_name": "Session Oracle",
    "app_short_name": "Oracle",
    "live": "Live · updated {age} s ago",
    "stale": "Stale · updated {age} s ago",
    "never": "Stale · not updated yet",
    "refresh": "Refresh now",
    "empty": "Nothing needs you.",
    "leftovers_tile": "Leftovers",
    "details": "Details",
    "back": "Sessions",
    "going_on": "What is going on",
    "next_step": "Suggested next step",
    "more_actions": "More actions",
    "recent": "Recent",
    "technical": "Technical details",
    "command": "Command",
    "copy": "Copy",
    "copy_command": "Copy command",
    "copied": "Copied",
    "exact": "Exact folder and environment",
    "runs_as": "Runs as you on the Mac, without the marks of any Claude window.",
    "cancel": "Cancel",
    "expires": "This confirm expires in {time}",
    "expired": "This confirm expired.",
    "look_again": "Look again",
    "this_deletes": "This deletes",
    "this_ends": "This ends",
    "checked_now": "Checked just now",
    "outcome": "What happened",
    "cannot_undo": "This cannot be undone.",
    "exact_command": "Exact command",
    "why": "Why",
    "show_output": "Show full output",
    "done": "Done",
    "close": "Close",
    "loading": "Asking the Mac what would run…",
    "nothing_to_run": "Nothing to run here.",
    "not_run": "Not run: {reason}",
    "confirm_failed": "Could not load the confirm: {error}",
    "unreached": "The action may not have reached the Mac: {error}",
    "refresh_failed": "Refresh failed {count} times in a row ({error}). Retrying.",
    "not_paired_any_more": "This device is not paired any more.",
    "moved": "The page moved to HTTPS. Opening it there.",
    "lost": ("The server restarted and no longer knows this action's result. The Mac's "
             "audit log has it: web/actions.log in the oracle home."),
    "remote_control": "Pick it up from Remote Control on your phone as {name}.",
    "audit_error": "The audit line for its end could not be written: {error}",
}

#: Each fact's chip, tone and sentence for "What is going on". ``{n}`` is a
#: count and ``{age}`` an age; a sentence with a ``{age}`` it cannot fill uses
#: its second form.
_FACTS: dict[tuple[str, str], tuple[str, str, str, str]] = {
    (WAITING, "permission"): (
        "Waiting for your permission", TONE_NEEDS,
        ("Claude asked for permission {age} ago. Answer it in Claude on your Mac or phone. "
         "This panel never answers prompts."),
        ("Claude asked for permission. Answer it in Claude on your Mac or phone. "
         "This panel never answers prompts.")),
    (WAITING, "question"): (
        "Waiting for your answer", TONE_NEEDS,
        "Claude asked you a question {age} ago. Answer it in Claude.",
        "Claude asked you a question. Answer it in Claude."),
    (WAITING, WAITING_INPUT): (
        "Waiting for you", TONE_NEEDS,
        ("Claude is waiting for you in its window. Answer it in Claude on your Mac or "
         "phone. This panel never answers prompts."),
        ("Claude is waiting for you in its window. Answer it in Claude on your Mac or "
         "phone. This panel never answers prompts.")),
    (FINISHED, "reviewed"): (
        "Finished, ready to merge", TONE_NEEDS,
        "The review checks passed. It can be merged.", ""),
    (FINISHED, "ready"): (
        "Finished, ready for review", TONE_NEEDS,
        "Its plan is complete and its lane has landed. It is ready for the review checks.",
        ""),
    (ERROR, "tests"): (
        "{n} tests failing", TONE_PROBLEM,
        "The last test run had {n} failures.", "The last test run failed."),
    (GATE_BLOCKED, ""): (
        "Blocked by a check", TONE_PROBLEM,
        "A check refused the last step: {rules}.", "A check refused the last step."),
    (STUCK, ""): (
        "No progress for {age}", TONE_PROBLEM,
        "Claude has been working for {age} with no sign of progress.",
        "Claude has been working with no sign of progress."),
    (ERROR, "degraded"): (
        "A hook failed", TONE_PROBLEM,
        "One of invariant's hooks failed in this session.", ""),
    (ERROR, "scan"): (
        "Cannot read this project", TONE_PROBLEM,
        "invariant cannot read this project's settings.", ""),
    (HOST_GONE, ""): (
        "Paused", TONE_IDLE,
        ("Its Claude window closed without saying so, so nothing is happening until you "
         "resume it."), ""),
    (STRANDED, ""): (
        "Its folder is gone", TONE_PROBLEM,
        "The session's folder no longer exists.", ""),
    (YOUR_TURN, ""): (
        "Waiting for your next message", TONE_IDLE,
        "Claude finished its turn and is waiting for you.", ""),
    (PARKED, ""): (
        "Paused", TONE_IDLE,
        "No Claude window is working on it. Resume it to go on.", ""),
    (TEARDOWN_PENDING, ""): (
        "Merged, ready to clean up", TONE_IDLE,
        "It is merged. Its folder and branch can be removed.", ""),
}

#: A failing-tests chip with no count.
_TESTS_FAILING = "Tests failing"

_RUNNING_CHIP = ("Claude is working", TONE_WORKING)
_RUNNING_SENTENCE = ("Claude is working on it; active {age} ago.", "Claude is working on it.")

#: A gate rule, in plain words. A rule not here is not named.
_RULES = {
    "test.all_pass": "the tests must pass",
    "test.new_test_fails": "a new test must fail first",
    "coverage.meets_floor": "coverage must meet the floor",
    "docs.read_architecture": "the architecture docs must be read first",
    "docs.updated": "the architecture docs must be updated",
    "docs.diagrams_current": "the diagrams must be current",
    "agents.design_agent_ran": "the design agent must run",
    "agents.review_agent_ran": "the review agent must run",
    "privacy.clean": "the privacy check must pass",
    "pipeline.complete": "the pipeline must be finished",
    "session.deliverables_complete": "the session's plan must be complete",
}

#: Methodology: (lane, {phase: phrase}). A machine not here has neither.
_LANES: dict[str, tuple[str | None, dict[str, str]]] = {
    "tdd": ("feature", {"designing": "Designing", "red": "Writing tests",
                        "green": "Making tests pass", "refactor": "Tidying up",
                        "reviewing": "In review"}),
    "bugfix": ("bug fix", {"triaging": "Looking at the bug",
                           "reproducing": "Reproducing the bug",
                           "reproduced": "Bug reproduced",
                           "not_reproducible": "Could not reproduce",
                           "fixing": "Fixing a bug", "verifying": "Checking the fix"}),
    "chore": ("chore", {"doing": "Working", "reviewing": "In review"}),
    "spec-first": ("feature", {"designing": "Designing", "specified": "Spec written",
                               "implementing": "Building", "reviewing": "In review"}),
    "docs": ("docs", {"drafting": "Writing docs", "reviewing": "In review"}),
    "none": (None, {"working": "Working"}),
}

#: Row titles for the rows that are not sessions.
_TITLES = {
    ROW_HOST: "A Claude window with no session",
    ROW_WORKTREE: "A left-over folder",
    ROW_ERROR: "A record invariant cannot read",
}
_UNNAMED = "Unnamed session"

_WAITING_NOTE = ("Answer it in Claude on your Mac or phone. "
                 "This panel never answers prompts.")
_WINDOW_CLOSED = "Its Claude window closed."

_ACTIVE = ("active {age} ago", "active now")


@dataclass(frozen=True)
class StepWords:
    """A recommended command, in words: the card, the confirm and the deed."""

    button: str
    title: str
    sentence: str
    run: str
    destructive: bool = False
    #: The step ends a process rather than deleting files: its red panel is
    #: "This ends", and its result says what happened (close host).
    ends: bool = False

    @property
    def name(self) -> str:
        return self.title.rstrip("?")


_STEPS = {
    "tests": StepWords(
        "Re-run tests…", "Re-run the tests?",
        "Runs {project}'s test suite in this session's folder and records the result. "
        "Nothing is changed or deleted.", "Run tests"),
    "gates": StepWords(
        "Show blockers…", "Show what is blocking it?",
        "Lists the checks this session still owes and what clears each. Nothing is changed.",
        "Show blockers"),
    "context": StepWords(
        "Show its instructions…", "Show what the session is being told?",
        "Prints the session's current instructions. Nothing is changed.", "Show"),
    "review": StepWords(
        "Review…", "Run the review checks?",
        "Runs the review checks and marks the session reviewed if they pass. "
        "Nothing is merged.", "Run review"),
    "merge": StepWords(
        "Merge…", "Merge this session?",
        "Merges the session's branch from the main checkout. Its folder and branch stay "
        "until clean-up.", "Merge"),
    "teardown": StepWords(
        "Clean up…", "Remove the folder and branch?",
        "Removes the merged session's folder and branch.", "Remove folder and branch",
        destructive=True),
    "reap": StepWords(
        "Check clean-up…", "See what clean-up would abandon?",
        "Lists the sessions whose folder and branch are both gone. Nothing is changed.",
        "Show"),
    "resume": StepWords(
        "Resume…", "Resume in a new Claude window?",
        "Opens Terminal on your Mac and starts Claude in this session's folder. You can "
        "pick it up from Remote Control on your phone.", "Open window"),
    "resume_force": StepWords(
        "Resume…", "Take over in a new Claude window?",
        "Its last Claude window closed without saying so. Opens Terminal on your Mac and "
        "starts Claude there to take over.", "Open window"),
    "adopt": StepWords(
        "Attach…", "Attach this window to its session?",
        "Binds the open Claude window to the one session here that has none.", "Attach"),
    "doctor": StepWords(
        "Check setup…", "Run invariant's setup check?",
        "Checks this project's hooks and settings. Nothing is changed.", "Run check"),
    "close": StepWords(
        "Close…", "Close this Claude window?",
        "Ends Claude in this window, then closes the Terminal tab. You can reopen the "
        "conversation later with claude --resume.", "Close window",
        destructive=True, ends=True),
}

#: ``invariant <words>``: the command's leading words, and its table entry.
_COMMANDS = (
    (("pipeline", "test"), "tests"),
    (("pipeline", "gates"), "gates"),
    (("context",), "context"),
    (("session", "review"), "review"),
    (("session", "merge"), "merge"),
    (("session", "teardown"), "teardown"),
    (("session", "reap", "--dry-run"), "reap"),
    (("session", "adopt"), "adopt"),
    (("doctor",), "doctor"),
    (("oracle", "close-host"), "close"),
)

_PLACE = {
    "worktree": "In {project} › session folder {session}",
    "main": "In {project} › main checkout",
    "other": "In {project}",
}
_DELETES = {
    "folder": "The session folder {name}",
    "branch": "The branch {name}",
    "claude": "Claude in {project} · window {id}",
    "process": "Process {pid}",
    "process_idle": "Process {pid} · idle {age}",
}

#: What a close's "Checked just now" says it verified, and that it will again.
_CHECKED_CLOSE = (
    "No invariant session is using this Claude window",
    "Checked again the moment you press {deed}",
)

_RESULT = {
    "running": "Running…",
    "done": "Done in {seconds} s",
    "opened": "Opened in a new window",
    "failed_exit": "Failed, exit {status}",
    "failed": "Failed",
    "stopped": "{step} · stopped after {seconds} s",
    "unknown_step": "This step",
}

#: A failed job's "Why" (ADR-009 D9). ``said`` and the causes are for a line
#: invariant itself wrote (``invariant: <message>``, the CLI's error line);
#: the rest name no cause, only the exit and the output's last meaningful line.
_WHY = {
    "said": "invariant said: {message}",
    "last": "{headline}. {line}",
    "silent": "{headline}. It printed nothing.",
    "unread": "{headline}. See the full output.",
}

#: invariant's own messages the table words, matched on the message after
#: ``invariant: ``. Each pattern is one ``core/manifest`` raises; an
#: unmatched message is quoted under ``said``, never reworded by a guess.
_OWN_CAUSES = (
    (re.compile(r"discovery for (['\"])(?P<key>.+?)\1 produced nothing\b"),
     "invariant could not find a value for {key}."),
    (re.compile(r"discovery for (['\"])(?P<key>.+?)\1 timed out\b"),
     "invariant ran out of time finding a value for {key}."),
)

_RECENT = {
    "permission": "Claude asked for permission",
    "question": "Claude asked you a question",
    "blocked": "A check refused the last step",
    "degraded": "A hook failed",
    "turn_running": "Claude started working",
    "turn_ended": "Claude finished its turn",
    "plan": "Its plan was updated",
    "job": "{step}: {result}",
}

_TECHNICAL = {
    "session": "Session {id} · state {state}",
    "lane": " · lane {methodology}:{phase}",
    "host": "Host {id} · process id {pid}",
    "host_no_pid": "Host {id} · process id not recorded",
    "host_conflict": "Host {id} · process ids disagree: {pids}",
    "candidate": "{source} {pid} ({liveness})",
    "error": "Error: {error}",
}

#: A disputed pid's liveness, as the TUI words it.
_LIVENESS = {True: "alive", False: "gone", None: "unknown"}

_LEFTOVER = {
    "title": "Leftovers",
    "intro": ("Things that outlived their session. None of them needs you; tidy them "
              "when you like."),
    "hosts": "Claude windows with no session",
    "folders": "Left-over folders",
    "more": "Show {n} more",
    "card": "{n} leftovers to tidy",
    "card_one": "1 leftover to tidy",
    "card_hosts": "{n} Claude windows with no session",
    "card_hosts_one": "1 Claude window with no session",
    "card_folders": "{n} left-over folders",
    "card_folders_one": "1 left-over folder",
    "host_title": "{project} · idle {age}",
    "host_title_now": "{project}",
    "alive": "Still open on the Mac",
    "gone": "Already closed · record only",
    "unknown": "Process id not recorded",
    "conflict": "Its process ids disagree",
    "unmanaged": "Open, never had a session",
    "active": "Active, no session",
    "window": "{state} · window {id}",
    "folder_title": "{project} · {name}",
    "branch": "On branch {branch}",
    "no_branch": "Not on a branch",
}


# -- small readers ---------------------------------------------------------------------


def age_text(seconds: float) -> str:
    """An age, short: ``now``, ``N min``, ``N h`` under two days, then ``N days``."""
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min"
    if seconds < 2 * 86400:
        return f"{int(seconds // 3600)} h"
    return f"{int(seconds // 86400)} days"


def _epoch(text: Any) -> float | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def lane_text(methodology: str | None) -> str | None:
    return _LANES.get(methodology or "", (None, {}))[0]


def phase_text(methodology: str | None, state: str | None) -> str | None:
    return _LANES.get(methodology or "", (None, {}))[1].get(state or "")


def step_words(argv: Iterable[str]) -> StepWords | None:
    """The words for an ``invariant`` command the recommender names; else ``None``."""
    argv = tuple(argv)
    if not argv or argv[0] != CLI:
        return None
    rest = argv[1:]
    if rest[:2] == ("session", "resume"):
        return _STEPS["resume_force" if "--force" in rest else "resume"]
    for words, key in _COMMANDS:
        if rest[: len(words)] == words:
            return _STEPS[key]
    return None


def copy_line(argv: Iterable[str], env: Mapping[str, str] | None) -> str:
    """The command as one shell line, its pinned variables leading (ADR-009 D5)."""
    pinned = [f"{name}={shlex.quote(value)}" for name, value in sorted((env or {}).items())]
    return " ".join([*pinned, shlex.join(list(argv))])


def _current_host(row: dict[str, Any]) -> dict[str, Any] | None:
    hosts = row.get("hosts") or []
    return next((h for h in hosts if h.get("current")), hosts[0] if hosts else None)


def _attention(row: dict[str, Any]) -> dict[str, Any]:
    host = _current_host(row)
    record = host.get("attention") if host else None
    return record if isinstance(record, dict) else {}


def _running(row: dict[str, Any]) -> bool:
    """Whether Claude is working: the document's mid-turn answer, never the
    attention turn on its own, which a missed Stop leaves running (BUG_087)."""
    return row.get("turn_state") == TURN_BUSY


def _signals(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in row.get("signals") or [] if isinstance(s, dict)]


def _fact(signal: dict[str, Any]) -> tuple[str, str, str, str] | None:
    kind, cause = signal.get("kind", ""), signal.get("cause") or ""
    return _FACTS.get((kind, cause)) or _FACTS.get((kind, ""))


def _alerting(row: dict[str, Any]) -> bool:
    return any(s.get("alert") for s in _signals(row))


# -- grouping --------------------------------------------------------------------------


def group_of(row: dict[str, Any]) -> str:
    """The first group whose rule holds for ``row`` (ADR-009 D2)."""
    if row.get("kind") in (ROW_HOST, ROW_WORKTREE) and not _alerting(row):
        return LEFTOVERS
    kinds = {s.get("kind") for s in _signals(row)}
    for group in GROUPS:
        if kinds & group.kinds:
            return group.id
    if row.get("kind") == ROW_ERROR:
        return PROBLEMS
    return WORKING if _running(row) else IDLE


def _rank(row: dict[str, Any], group: str) -> int:
    kinds = next((g.kinds for g in GROUPS if g.id == group), frozenset())
    ranks = [_SEVERITY.get(s.get("severity"), len(_SEVERITY))
             for s in _signals(row) if not kinds or s.get("kind") in kinds]
    return min(ranks, default=len(_SEVERITY))


def _since(row: dict[str, Any], now: float) -> float | None:
    """When the row's condition began, if a recorded time says so."""
    record = _attention(row)
    kinds = {s.get("kind"): s for s in _signals(row)}
    waiting = record.get("waiting") if isinstance(record.get("waiting"), dict) else None
    if WAITING in kinds and waiting:
        return _epoch(waiting.get("since"))
    blocked = record.get("blocked") if isinstance(record.get("blocked"), dict) else None
    if GATE_BLOCKED in kinds and blocked:
        return _epoch(blocked.get("since"))
    if STUCK in kinds and row.get("last_activity"):
        return row["last_activity"]
    degraded = record.get("degraded") if isinstance(record.get("degraded"), dict) else None
    if ERROR in kinds and degraded:
        return _epoch(degraded.get("since"))
    return None


# -- words for one row -----------------------------------------------------------------


def _title(row: dict[str, Any]) -> str:
    kind = row.get("kind")
    if kind in _TITLES:
        return _TITLES[kind]
    name, sid = row.get("name") or "", row.get("session_id") or ""
    if name and sid and name.endswith(sid):
        name = name[: -len(sid)]
    name = re.sub(r"[-_\s]+", " ", name).strip()
    if name:
        return name[0].upper() + name[1:]
    task = ((row.get("pipeline") or {}).get("task") or "").strip()
    return task or _UNNAMED


def _phase(row: dict[str, Any]) -> str | None:
    pipe = row.get("pipeline")
    if not pipe or pipe.get("terminal"):
        return None
    return phase_text(pipe.get("methodology"), pipe.get("state"))


def _tests_failed(row: dict[str, Any]) -> int | None:
    last = (row.get("pipeline") or {}).get("last_test") or {}
    failed = last.get("failed")
    return failed if isinstance(failed, int) and failed > 0 else None


def _known_rules(row: dict[str, Any]) -> list[str]:
    blocked = _attention(row).get("blocked")
    rules = blocked.get("rules") if isinstance(blocked, dict) else None
    return [_RULES[r] for r in sorted(rules or []) if r in _RULES]


def _stuck_age(row: dict[str, Any], now: float) -> str | None:
    last = row.get("last_activity")
    return age_text(now - last) if isinstance(last, (int, float)) and now - last >= 60 else None


def _fill(signal: dict[str, Any], row: dict[str, Any], now: float, which: int) -> str | None:
    """A fact's chip (``which`` 0) or sentence (2), filled from the row; else ``None``."""
    fact = _fact(signal)
    if fact is None:
        return None
    kind = signal.get("kind")
    values: dict[str, Any] = {}
    if kind == ERROR and signal.get("cause") == "tests":
        values["n"] = _tests_failed(row)
    elif kind == GATE_BLOCKED:
        rules = _known_rules(row)
        values["rules"] = ", ".join(rules) if rules else None
    elif kind == STUCK:
        values["age"] = _stuck_age(row, now)
    elif kind == WAITING:
        since = _since(row, now)
        values["age"] = age_text(now - since) if since is not None else None
    text = fact[which]
    if any(values.get(name) is None for name in re.findall(r"\{(\w+)\}", text)):
        if which == 0:
            return _TESTS_FAILING if kind == ERROR else text.replace(" for {age}", "")
        return fact[3] or text
    return text.format(**values)


def _chips(row: dict[str, Any], now: float, aged: bool = False) -> list[dict[str, str]]:
    chips: list[dict[str, str]] = []
    for signal in _signals(row):
        fact = _fact(signal)
        text = _fill(signal, row, now, 0)
        if fact is None or text is None:
            continue
        if aged and signal.get("kind") in (WAITING, GATE_BLOCKED, ERROR):
            since = _since(row, now)
            if since is not None and now - since >= 60:
                text = f"{text} {age_text(now - since)}"
        chips.append({"text": text, "tone": fact[1]})
    if _running(row):
        chips.append({"text": _RUNNING_CHIP[0], "tone": _RUNNING_CHIP[1]})
    seen: set[str] = set()
    return [c for c in chips if not (c["text"] in seen or seen.add(c["text"]))]


def _active(row: dict[str, Any], now: float) -> str | None:
    last = row.get("last_activity")
    if not isinstance(last, (int, float)):
        return None
    age = now - last
    return _ACTIVE[1] if age < 60 else _ACTIVE[0].format(age=age_text(age))


def _sentences(row: dict[str, Any], now: float) -> list[str]:
    said: list[str] = []
    for signal in _signals(row):
        text = _fill(signal, row, now, 2)
        if text and text not in said:
            said.append(text)
    if _running(row):
        last = row.get("last_activity")
        if isinstance(last, (int, float)):
            said.append(_RUNNING_SENTENCE[0].format(age=age_text(now - last)))
        else:
            said.append(_RUNNING_SENTENCE[1])
    return said[:3]


def _waiting(row: dict[str, Any]) -> bool:
    return any(s.get("kind") == WAITING for s in _signals(row))


def _runnable(row: dict[str, Any]) -> StepWords | None:
    """The row's step words, when the step is one the panel offers a button for."""
    advice = row.get("recommendation") or {}
    if advice.get("kind") not in (RUN, TERMINAL) or _waiting(row):
        return None
    return step_words(advice.get("argv") or ())


def _note(row: dict[str, Any]) -> str | None:
    if _waiting(row):
        return _WAITING_NOTE
    parts = []
    kinds = {s.get("kind") for s in _signals(row)}
    if kinds & {PARKED, HOST_GONE}:
        parts.append(_WINDOW_CLOSED)
    phase = _phase(row)
    if phase:
        parts.append(f"{phase}.")
    return " ".join(parts) or None


def _card(row: dict[str, Any], project: str, group: str, now: float,
          acting: bool) -> dict[str, Any]:
    since = _since(row, now)
    words = _runnable(row)
    card: dict[str, Any] = {
        "row": row_key(row),
        "project": project,
        "title": _title(row),
        "chips": _chips(row, now),
        "age": age_text(now - since) if since is not None else "",
    }
    if group in (WORKING, IDLE):
        phase, active = _phase(row), _active(row, now)
        if phase and active:
            card["line"] = f"{phase} · {active}"
        else:
            line = phase or active or ""
            card["line"] = line[:1].upper() + line[1:]
        return card
    card["note"] = _note(row)
    card["button"] = {"text": words.button} if acting and words else None
    # A row waiting on a prompt has no buttons at all (ADR-009 D8): the card opens.
    card["tap"] = _waiting(row)
    return card


# -- the detail view -------------------------------------------------------------------


def _step(row: dict[str, Any], project: str, acting: bool) -> dict[str, Any] | None:
    advice = row.get("recommendation") or {}
    if advice.get("kind") not in (RUN, TERMINAL):
        return None
    words = step_words(advice.get("argv") or ())
    if words is None:
        return None
    return {
        "name": words.name,
        "sentence": words.sentence.format(project=project),
        "button": {"text": words.button} if acting and not _waiting(row) else None,
        "copy": copy_line(advice.get("argv") or (), advice.get("env")),
    }


def result_words(job: Mapping[str, Any]) -> dict[str, str]:
    """A job's outcome in words: the headline, the line under it, and its tone."""
    words = step_words(job.get("argv") or ())
    name = words.run if words else _RESULT["unknown_step"]
    if job.get("state") != "done":
        return {"headline": _RESULT["running"], "line": name, "tone": "running"}
    seconds = max(1, math.ceil(float(job.get("elapsed") or 0)))
    terminal = job.get("kind") == TERMINAL
    if job.get("ok"):
        headline = _RESULT["opened"] if terminal else _RESULT["done"].format(seconds=seconds)
        return {"headline": headline, "line": name, "tone": "ok"}
    status = job.get("status")
    headline = (_RESULT["failed_exit"].format(status=status) if status is not None
                else _RESULT["failed"])
    return {"headline": headline,
            "line": _RESULT["stopped"].format(step=name, seconds=seconds), "tone": "failed"}


def why_words(status: int | None, own: str | None, last: str | None, printed: bool) -> str:
    """A failed job's one-line reason, from what its output states (ADR-009 D9).

    ``own`` is the message of the line invariant wrote (``invariant: …``), if
    any; ``last`` the output's last meaningful line; ``printed`` whether it
    printed anything at all. Those are ``webactions.evidence``'s. A cause is
    named only from ``own``; anything else is the exit and ``last``.
    """
    if own:
        for pattern, sentence in _OWN_CAUSES:
            found = pattern.match(own)
            if found:
                return sentence.format(key=found["key"])
        return _WHY["said"].format(message=own)
    headline = (_RESULT["failed_exit"].format(status=status) if status is not None
                else _RESULT["failed"])
    if last:
        return _WHY["last"].format(headline=headline, line=last)
    return _WHY["unread" if printed else "silent"].format(headline=headline)


def _recent(row: dict[str, Any], key: str, jobs: Iterable[Mapping[str, Any]]) -> list[dict]:
    record = _attention(row)
    events: list[dict[str, Any]] = []

    def add(text: str, at: float | None) -> None:
        if at is not None:
            events.append({"text": text, "at": at})

    waiting = record.get("waiting") if isinstance(record.get("waiting"), dict) else {}
    if waiting.get("kind") in ("permission", "question"):
        add(_RECENT[waiting["kind"]], _epoch(waiting.get("since")))
    for name in ("blocked", "degraded"):
        part = record.get(name)
        if isinstance(part, dict):
            add(_RECENT[name], _epoch(part.get("since")))
    if record.get("turn") in ("running", "ended"):
        add(_RECENT[f"turn_{record['turn']}"], _epoch(record.get("turn_since")))
    if isinstance(row.get("plan_touched"), (int, float)):
        add(_RECENT["plan"], row["plan_touched"])
    for job in jobs:
        if job.get("row") == key and isinstance(job.get("started"), (int, float)):
            words = step_words(job.get("argv") or ())
            step = words.run if words else _RESULT["unknown_step"]
            add(_RECENT["job"].format(step=step, result=result_words(job)["headline"]),
                job["started"])
    return sorted(events, key=lambda e: -e["at"])


def _technical(row: dict[str, Any]) -> dict[str, Any]:
    lines = []
    if row.get("session_id"):
        line = _TECHNICAL["session"].format(id=row["session_id"], state=row.get("state"))
        pipe = row.get("pipeline")
        if pipe:
            line += _TECHNICAL["lane"].format(methodology=pipe.get("methodology"),
                                             phase=pipe.get("state"))
        lines.append(line)
    for host in row.get("hosts") or []:
        pid, conflict = host.get("pid"), host.get("pid_conflict") or []
        if conflict:
            pids = ", ".join(_TECHNICAL["candidate"].format(
                source=c.get("source"), pid=c.get("pid"), liveness=_LIVENESS.get(c.get("alive")))
                for c in conflict)
            lines.append(_TECHNICAL["host_conflict"].format(id=host.get("host_session_id"),
                                                            pids=pids))
            continue
        template = _TECHNICAL["host"] if pid else _TECHNICAL["host_no_pid"]
        lines.append(template.format(id=host.get("host_session_id"), pid=pid))
    if row.get("error"):
        lines.append(_TECHNICAL["error"].format(error=row["error"]))
    path = row.get("worktree") or row.get("path") or row.get("root") or None
    return {"lines": lines, "path": path}


def _detail(row: dict[str, Any], project: str, now: float, acting: bool,
            jobs: list[Mapping[str, Any]]) -> dict[str, Any]:
    key = row_key(row)
    lane = lane_text((row.get("pipeline") or {}).get("methodology"))
    return {
        "row": key,
        "kicker": f"{project} · {lane}" if lane else project,
        "title": _title(row),
        "chips": _chips(row, now, aged=True),
        "going_on": _sentences(row, now),
        "step": _step(row, project, acting),
        "recent": _recent(row, key, jobs),
        "technical": _technical(row),
    }


# -- leftovers -------------------------------------------------------------------------


def _plural(n: int, one: str, many: str) -> str:
    return _LEFTOVER[one] if n == 1 else _LEFTOVER[many].format(n=n)


def _orphan_class(row: dict[str, Any]) -> str:
    return next((s.get("cause") or "" for s in _signals(row) if s.get("kind") == ORPHAN_HOST),
                "")


def _host_state(row: dict[str, Any], host: dict[str, Any]) -> str:
    """The leftover's state, by its class where the class says more than liveness."""
    if host.get("pid_conflict"):
        return _LEFTOVER["conflict"]
    cause = _orphan_class(row)
    if cause in ("unmanaged", "active"):
        return _LEFTOVER[cause]
    alive = host.get("alive")
    return _LEFTOVER["alive" if alive is True else "gone" if alive is False else "unknown"]


def _host_leftover(row: dict[str, Any], project: str, now: float,
                   acting: bool) -> dict[str, Any]:
    host = _current_host(row) or {}
    last = row.get("last_activity")
    if isinstance(last, (int, float)) and now - last >= 60:
        title = _LEFTOVER["host_title"].format(project=project, age=age_text(now - last))
    else:
        title = _LEFTOVER["host_title_now"].format(project=project)
    window = str(host.get("host_session_id") or "")[:8]
    words = _runnable(row)
    return {"row": row_key(row), "title": title,
            "line": _LEFTOVER["window"].format(state=_host_state(row, host), id=window),
            "button": {"text": words.button} if acting and words else None}


def _folder_leftover(row: dict[str, Any], project: str) -> dict[str, str]:
    name = os.path.basename(str(row.get("path") or "").rstrip("/"))
    branch = row.get("branch")
    return {
        "title": _LEFTOVER["folder_title"].format(project=project, name=name),
        "line": (_LEFTOVER["branch"].format(branch=branch) if branch
                 else _LEFTOVER["no_branch"]),
    }


def _section(section_id: str, heading: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    hidden = len(rows) - LEFTOVERS_SHOWN
    return {
        "id": section_id, "heading": heading, "count": len(rows),
        "shown": min(len(rows), LEFTOVERS_SHOWN), "rows": rows,
        "more": _LEFTOVER["more"].format(n=hidden) if hidden > 0 else None,
    }


def _leftovers(hosts: list[dict[str, Any]], folders: list[dict[str, str]]):
    screen = {
        "title": _LEFTOVER["title"],
        "intro": _LEFTOVER["intro"],
        "sections": [_section("hosts", _LEFTOVER["hosts"], hosts),
                     _section("folders", _LEFTOVER["folders"], folders)],
    }
    total = len(hosts) + len(folders)
    if not total:
        return screen, None
    parts = []
    if hosts:
        parts.append(_plural(len(hosts), "card_hosts_one", "card_hosts"))
    if folders:
        parts.append(_plural(len(folders), "card_folders_one", "card_folders"))
    card = {"title": _plural(total, "card_one", "card"), "line": ", ".join(parts)}
    return screen, card


# -- the view --------------------------------------------------------------------------


def view(document: Mapping[str, Any], acting: bool = False,
         jobs: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Everything the panel draws, from one ``assess`` document.

    With ``acting``, a row whose step runs (``run`` or ``terminal``) and that
    waits on no prompt gets its button (ADR-009 D8). ``jobs`` are this
    device's recent actions, listed under "Recent" on their session.
    """
    now = float(document.get("now") or 0.0)
    jobs = list(jobs)
    placed: dict[str, list[tuple[int, float, int, dict[str, Any]]]] = {
        g.id: [] for g in GROUPS}
    details: dict[str, dict[str, Any]] = {}
    hosts: list[dict[str, Any]] = []
    folders: list[dict[str, str]] = []
    position = 0
    for project in document.get("projects", []):
        name = (project.get("project") or {}).get("name", "")
        for row in project.get("rows", []):
            group = group_of(row)
            if group == LEFTOVERS:
                if row.get("kind") == ROW_HOST:
                    hosts.append(_host_leftover(row, name, now, acting))
                else:
                    folders.append(_folder_leftover(row, name))
                continue
            since = _since(row, now)
            position += 1
            placed[group].append((_rank(row, group), since if since is not None else math.inf,
                                  position, _card(row, name, group, now, acting)))
            details[row_key(row)] = _detail(row, name, now, acting, jobs)
    groups = []
    for group in GROUPS:
        cards = [card for *_, card in sorted(placed[group.id], key=lambda p: p[:3])]
        if cards:
            groups.append({"id": group.id, "heading": group.heading,
                           "compact": group.compact, "cards": cards})
    summary = {g.id: len(placed[g.id]) for g in GROUPS if g.tile}
    summary[LEFTOVERS] = len(hosts) + len(folders)
    tiles = [{"id": g.id, "label": g.tile, "count": summary[g.id], "href": f"#{g.id}"}
             for g in GROUPS if g.tile]
    tiles.append({"id": LEFTOVERS, "label": WORDS["leftovers_tile"],
                  "count": summary[LEFTOVERS], "href": f"#{LEFTOVERS}"})
    screen, card = _leftovers(hosts, folders)
    attention = summary[NEEDS] + summary[PROBLEMS]
    return {
        "title": WORDS["title"],
        "doc_title": WORDS["doc_title"].format(count=attention) if attention
        else WORDS["title"],
        "summary": summary,
        "tiles": tiles,
        "groups": groups,
        "empty": None if groups else WORDS["empty"],
        "leftovers_card": card,
        "leftovers": screen,
        "details": details,
    }


# -- the confirm -----------------------------------------------------------------------


def _place(row: Mapping[str, Any], project: str, cwd: str | None) -> str:
    if cwd and cwd == row.get("worktree"):
        return _PLACE["worktree"].format(project=project, session=row.get("session_id"))
    if cwd and cwd in (row.get("root"), row.get("origin")):
        return _PLACE["main"].format(project=project)
    return _PLACE["other"].format(project=project)


def confirm_words(row: Mapping[str, Any], project: str, argv: Iterable[str], kind: str,
                  cwd: str | None, env: Mapping[str, str] | None,
                  launch: str | None, now: float | None = None) -> dict[str, Any] | None:
    """What the confirm sheet says for a step, or ``None`` for one the table lacks.

    The command line and the place lead; the exact folder, each added variable
    and a terminal's launch line sit behind the sheet's toggle (ADR-009 D6). A
    destructive step lists what goes, from the row (D7).
    """
    argv = tuple(argv)
    words = step_words(argv)
    if words is None:
        return None
    sentence = words.sentence.format(project=project)
    deletes: list[str] = []
    checked: list[str] = []
    if words.ends:
        deletes = _ends(row, project, now)
        checked = [line.format(deed=words.run) for line in _CHECKED_CLOSE]
    elif words.destructive:
        sentence = f"{sentence} {WORDS['cannot_undo']}"
        if row.get("worktree"):
            folder = os.path.basename(str(row["worktree"]).rstrip("/"))
            deletes.append(_DELETES["folder"].format(name=folder))
        if row.get("branch"):
            deletes.append(_DELETES["branch"].format(name=row["branch"]))
    return {
        "title": words.title,
        "sentence": sentence,
        "button": words.run,
        "command": copy_line(argv, env),
        "place": _place(row, project, cwd),
        "cwd": cwd,
        "env": [f"{name}={value}" for name, value in sorted((env or {}).items())],
        "launch": launch if kind == TERMINAL else None,
        "destructive": words.destructive,
        "deletes_heading": WORDS["this_ends" if words.ends else "this_deletes"],
        "deletes": deletes,
        "checked": checked,
    }


def _ends(row: Mapping[str, Any], project: str, now: float | None) -> list[str]:
    """What a close ends: the window, by project and id, and its process and idle time."""
    host = _current_host(dict(row)) or {}
    window = str(host.get("host_session_id") or "")[:8]
    lines = [_DELETES["claude"].format(project=project, id=window)]
    last = row.get("last_activity")
    if isinstance(last, (int, float)) and now is not None and now - last >= 60:
        lines.append(_DELETES["process_idle"].format(pid=host.get("pid"),
                                                     age=age_text(now - last)))
    else:
        lines.append(_DELETES["process"].format(pid=host.get("pid")))
    return lines


def outcome_words(argv: Iterable[str], ok: bool | None, last: str | None) -> str:
    """What a finished close did, in the line the command wrote last; else nothing.

    Only a step that reports its outcome (close host) has one, and only when
    it succeeded: its last line is the sentence ``oracle/closehost`` prints,
    such as "Claude ended, and its Terminal tab closed." A failure says why
    under "Why" instead (:func:`why_words`).
    """
    words = step_words(argv)
    if not ok or words is None or not words.ends or not last:
        return ""
    return last
