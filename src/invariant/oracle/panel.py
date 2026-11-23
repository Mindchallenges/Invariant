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

* **A start shows until its window does** (IDEA_030, ADR-009 D16). A start
  from this device is a "Starting…" card under Working until a scan sees a
  live host launched by its name; then that host's own row is drawn under
  Working by the name, with its detail. A window that has not appeared a
  minute after the start is a failure with its reason.

* **An older oracle is one problem, not one per project** (BUG_089). When the
  document says the oracle is older than the installed invariant, the first
  card under Problems says so once, names the projects it left unread, and
  says whether it restarts itself; no project is called unreadable for it.

* **The chain needs you on cards, and has a screen per project** (ADR-027
  increment 3, rulings 14 to 16). Given the chain's read
  (``oracle/chainview.ChainReader``), a proposal the step made, a stall its
  nudge did not clear or could not make, a paused chain and the weekly-usage
  hold the step said are cards under Needs you; each project's row under
  Projects carries a chain line and opens its Chain screen: the queue with
  up, down and remove, the proposals with Start, Pause or Resume, and the
  current chained start with its stall actions. A button is drawn only for an
  action the read offers, and every word is spelled here.

* **A live window's last message** (ADR-029 increment 1, ADR-009 D17).
  :func:`excerpt_hosts` names the window each row's excerpt is read from;
  ``oracle/web`` reads it and hands the texts back as ``excerpts``. A card
  carries the start of Claude's last text, a leftover window's row too, and
  the detail the whole of it with the prompt it answers, folded past
  :data:`EXCERPT_FOLD_CHARS`. The text is the session's own, not a word of
  this table, and is passed through as it is: the page draws it as text.

* **A live window's conversation** (ADR-029 increment 2). A detail whose row
  has a live window the page can read offers "Open conversation"
  (``conversation``); :func:`conversation_head` is that screen's kicker,
  title and chips. The turns themselves are ``oracle/talk``'s, read by
  ``oracle/web`` on demand; only their labels are spelled here.

* **A question answered from the card** (ADR-029 D10, increment 5). Given
  ``prompts``, each waiting question's read by ``oracle/web``, a Needs you
  card waiting on a question the page can answer offers "Answer…", and its
  note is the question's first line, the session's own text, drawn as text;
  a question that cannot be read says so instead. A permission prompt keeps
  its card until increment 6 answers it.

It imports nothing that runs anything: no ``actions``, no ``webactions``, no
``hosts/``. The table is the design of record's, in
``docs/architecture/oracle-panel.rst``, "Wording".
"""

from __future__ import annotations

import math
import os
import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..core.queue import BLIND_RUNS
from . import chainview as cv
from .recommend import (
    CLI,
    CLOSE_CLAIMED,
    CLOSE_CWD,
    CLOSE_NEWER,
    CLOSE_NOT_ROOTED,
    CLOSE_OTHER,
    CLOSE_PS,
    CLOSE_RECORDS,
    CLOSE_START,
    RERUN_UNRECORDED,
    RERUN_UNSCOPED,
    RUN,
    TERMINAL,
)
from .scan import ROW_ERROR, ROW_HOST, ROW_SESSION, ROW_WORKTREE, row_key
from .signals import (
    DOCS_GRANTED,
    DOCS_QUEUED,
    ERROR,
    FINISHED,
    GATE_BLOCKED,
    HIGH,
    HOST_GONE,
    INFO,
    INHERITED_ENV,
    LOW,
    MEDIUM,
    ORPHAN_HOST,
    PARKED,
    STALL_ERROR,
    STALL_LIMIT,
    STALLED,
    STRANDED,
    STUCK,
    STUCK_BACKGROUND,
    TEARDOWN_PENDING,
    TURN_BACKGROUND,
    TURN_BUSY,
    WAITING,
    WAITING_INPUT,
    YOUR_TURN,
)
from .startsession import START, find_started

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
    Group(NEEDS, "Needs you", "Need you", False,
          frozenset({WAITING, FINISHED, DOCS_GRANTED})),
    Group(PROBLEMS, "Problems", "Problems", False,
          frozenset({ERROR, GATE_BLOCKED, STUCK, STALLED, HOST_GONE, STRANDED})),
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
    "masked_env": "  (masked; the session's recorded value is used when it runs)",
    "masked_copy": "# Not runnable as shown, {names} masked: ",
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
    "new_session": "New session…",
    "projects": "Projects",
    # Web push, this device's own setting (ADR-027 increment 4, DEC_ADR_027_D42).
    "notifications": "Notifications",
    "push_title": "Alerts on this device",
    "push_on": "On: the Mac's alerts and the chain's notices reach this device.",
    "push_on_error": "On, but the last one did not arrive: {error}.",
    "push_off": ("Off. Turn them on to get the Mac's alerts and the chain's notices here "
                 "as well."),
    "push_turn_on": "Turn on",
    "push_turn_off": "Turn off",
    "push_busy": "Asking this device…",
    "push_no_extra": ("The Mac cannot send them: invariant's oracle extra is not "
                      "installed there."),
    "push_needs_https": "They need the page over HTTPS, which the Mac is not serving yet.",
    "push_unreadable": "The Mac cannot read where it keeps them.",
    "push_home_screen": "Add this page to your Home Screen, then turn them on from there.",
    "push_unsupported": "This browser cannot receive them.",
    "push_blocked": "This browser blocks them for this page; allow them in its settings.",
    "push_failed": "That did not work: {error}",
    "project_field": "Project",
    "label_field": "Name",
    "prompt_field": "First message (optional)",
    "prompt_hint": "Claude starts with this message. Leave it empty to start without one.",
    "model_field": "Model (optional)",
    "model_hint": "Leave it empty for the default.",
    "exact_start": "Exact command and folder",
    "chain_now": "Now",
    "chain_proposed": "Proposed",
    "chain_queue": "Queue",
    # A session's last message (ADR-029 increment 1): its labels, never its text.
    "claude": "Claude",
    "you": "You",
    "last_message": "Last message",
    "show_all": "Show all",
    # A session's conversation (ADR-029 increment 2): its labels, never its text.
    "open_conversation": "Open conversation",
    "conversation": "Conversation",
    "new_messages": "New messages",
    "cut_here": "Cut here: the rest is on the Mac",
    "reading_again": "Reading again…",
    "cant_read_session": "Can't read this session here — use the Mac.",
    "window_gone": "The Claude window is gone.",
    # Sending a message (ADR-029 increment 3): labels, never the text.
    "reply": "Reply…",
    "message_claude": "Message Claude",
    "message_turn_end": "Message Claude — sent when this turn ends",
    "send": "Send",
    "undo": "Undo",
    "stop": "Stop",
    "talk_held": "Sending…",
    "talk_due": "Sending…",
    "talk_queued": "Queued: sent when Claude's turn ends",
    "talk_typed": "Sent · checking",
    "talk_delivered": "Delivered",
    "talk_not_confirmed": "Not confirmed — check the session on the Mac",
    "talk_refused": "Not sent: {reason}",
    "talk_dropped": "Dropped, not delivered",
    "talk_dropped_boot": "Dropped, not delivered: the oracle restarted",
    "talk_dropped_ring": "Dropped, not delivered: too many were queued",
    "talk_unreached": "The message may not have reached the Mac: {error}",
    "talk_alert": "A message from the phone was not confirmed",
    "talk_why_typing": "Someone is typing on the Mac.",
    "talk_why_changed": "The session changed since you looked.",
    "talk_why_gone": "The Claude window is gone.",
    "talk_why_no_tab": "Send from here works for Terminal windows only.",
    "talk_why_no_claude": "Claude is not running in this window.",
    "talk_why_unreadable": "Can't read this session here — use the Mac.",
    "talk_why_not_allowed": "The Mac did not let the oracle type into Terminal.",
    "talk_why_failed": "The Mac could not type it.",
    "talk_why_menu_command": "This command opens a menu — use it on the Mac.",
    "talk_why_input_mode": ("A message starting with !, # or & is a command to Claude, "
                            "not a message; send it from the Mac."),
    "talk_why_revoked": "This device is not paired any more.",
    "talk_why_ring": "Too many messages are waiting to be sent.",
    "talk_why_ended": "Claude's turn had already ended.",
    "talk_why_asking": "Claude is waiting on a prompt — answer it on the Mac.",
    # Answering a question (ADR-029 increment 5): labels, never the question.
    "answer": "Answer…",
    "send_answer": "Send answer",
    "other": "Other…",
    "other_placeholder": "Your answer",
    "your_answer": "Your answer",
    "cant_read_question": "Can't read this question here — use the Mac.",
    "cant_read_prompt": "Can't read this prompt here — use the Mac.",
    "talk_why_answered": "Claude is no longer waiting on that question.",
    "talk_why_answer": "That answer does not fit the question.",
    "changed_window": "It is another Claude window now.",
    "changed_process": "Claude restarted in it.",
    "changed_terminal": "It moved to another terminal.",
    "changed_turn": "A new turn began.",
}

#: The most of a session's last text a card carries; the page shows three
#: lines of it on a full card and one on a compact card or a leftover row.
EXCERPT_CARD_CHARS = 600

#: Past this many characters the detail folds the text behind "Show all"
#: (ADR-029 D11).
EXCERPT_FOLD_CHARS = 1200

#: Each fact's chip, tone and sentence for "What is going on". ``{n}`` is a
#: count and ``{age}`` an age; a sentence with a ``{age}`` it cannot fill uses
#: its second form.
_FACTS: dict[tuple[str, str], tuple[str, str, str, str]] = {
    (WAITING, "permission"): (
        "Waiting for your permission", TONE_NEEDS,
        "Claude asked for permission {age} ago. Answer it in Claude on your Mac or phone.",
        "Claude asked for permission. Answer it in Claude on your Mac or phone."),
    (WAITING, "question"): (
        "Waiting for your answer", TONE_NEEDS,
        "Claude asked you a question {age} ago. Answer it here or on the Mac.",
        "Claude asked you a question. Answer it here or on the Mac."),
    (WAITING, WAITING_INPUT): (
        "Waiting for you", TONE_NEEDS,
        "Claude is waiting for your next message.",
        "Claude is waiting for your next message."),
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
    (STUCK, STUCK_BACKGROUND): (
        "No progress for {age}", TONE_PROBLEM,
        "Its background work has shown no sign of progress for {age}.",
        "Its background work has shown no sign of progress."),
    (STALLED, ""): (
        "No activity for {age}", TONE_PROBLEM,
        "Nothing has happened in this session for {age}, and it is not waiting on you.",
        "Nothing has happened in this session for hours, and it is not waiting on you."),
    (STALLED, STALL_LIMIT): (
        "Stopped at a usage limit", TONE_PROBLEM,
        ("It stopped at a usage limit and has done nothing for {age}. It can go on once "
         "the limit resets."),
        "It stopped at a usage limit. It can go on once the limit resets."),
    (STALLED, STALL_ERROR): (
        "Stopped on an error", TONE_PROBLEM,
        "Its last turn ended in an error, and nothing has happened for {age}.",
        "Its last turn ended in an error, and nothing has happened since."),
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
    (DOCS_GRANTED, ""): (
        "Its docs are free, go on", TONE_NEEDS,
        ("The docs it was waiting for are now its own. Tell Claude to go on: it reads "
         "them again before it designs."), ""),
    (DOCS_QUEUED, ""): (
        "Waiting for the docs", TONE_IDLE,
        "Another session is working on the docs it needs. It goes next when they finish.",
        ""),
    (INHERITED_ENV, ""): (
        "Terminal.app needs a restart", TONE_IDLE,
        ("This Claude inherited settings from its Terminal.app that are meant for a "
         "Claude window's own tools, so it may draw white blocks and keep no "
         "transcript. Quit Terminal.app fully and relaunch it from the Dock."), ""),
}

#: The signals whose card reads "Paused": no live host is working on the session.
_PAUSED = frozenset({PARKED, HOST_GONE})

#: A failing-tests chip with no count.
_TESTS_FAILING = "Tests failing"

_RUNNING_CHIP = ("Claude is working", TONE_WORKING)
_RUNNING_SENTENCE = ("Claude is working on it; active {age} ago.", "Claude is working on it.")

#: A host whose turn ended while work it started runs on (BUG_094): what it
#: waits on, one noun per kind (singular, plural), and the sentence.
_BACKGROUND_CHIP = ("Waiting on {work}", "Waiting on background work")
_BACKGROUND_NOUNS = (("agents", ("agent", "agents")),
                     ("commands", ("background command", "background commands")))
_BACKGROUND_SENTENCE = (
    "Claude finished its turn and is waiting on its background work; active {age} ago.",
    "Claude finished its turn and is waiting on its background work.")

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
    "store.docs_lock_held": "another session is working on the docs it needs",
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
    "docs-project": ("docs", {"drafting": "Writing docs", "reviewing": "In review"}),
    "none": (None, {"working": "Working"}),
}

#: Row titles for the rows that are not sessions.
_TITLES = {
    ROW_HOST: "A Claude window with no session",
    ROW_WORKTREE: "A left-over folder",
    ROW_ERROR: "A record invariant cannot read",
}
_UNNAMED = "Unnamed session"

#: The read's own condition: the oracle is older than the installed invariant
#: (BUG_089). Its card's row key, which no project row can have.
ORACLE_ROW = "oracle|build"
_ORACLE = {
    "kicker": "Session Oracle",
    "title": "The oracle is out of date",
    "restarting": "Restarting",
    "restart": "Restart needed",
    "changed": ("invariant was updated on the Mac after the oracle started. The oracle "
                "restarts itself to catch up, within a minute."),
    "rules": ("The oracle on the Mac is older than the invariant installed there. "
              "Restarting it fixes this; Technical details has the command."),
    "unread_one": "Until then it cannot read 1 project: {names}.",
    "unread": "Until then it cannot read {n} projects: {names}.",
    "note": "Not read: {names}",
    "started": "Started from {started}, installed {installed}",
    "lacks": "Rules this build lacks: {rules}",
    "error": "{project}: {error}",
    "by_hand": ("Restart by hand: invariant oracle install, and invariant oracle install "
                "--web for this page"),
}

#: A waiting card's note where the page has no words of the prompt's own
#: (ADR-029 D45): a permission prompt, or a question it did not read.
_WAITING_NOTE = "Answer it in Claude on your Mac or phone."
_INPUT_NOTE = "Claude is waiting for your next message."
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
        "Runs the tests this session last ran in {project}, in its own folder and with "
        "its own settings, and records the result. Nothing is changed or deleted.",
        "Run tests"),
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

#: The chain's actions from the phone (ADR-027 increment 3), by
#: ``oracle/chainview``'s action. Their titles and sentences are filled with the
#: entry, its ref, its neighbour, the project and, for a start, what it is
#: about. Retry and Close end a window, so their sheet is the destructive one
#: (ruling 14); every other is the safe sheet.
_CHAIN_STEPS = {
    cv.MOVE_UP: StepWords(
        "Move up…", "Move {entry} up?",
        "Puts {entry}, {ref}, before {other}, {other_ref}, in {project}'s queue. Nothing "
        "starts and nothing else moves.", "Move up"),
    cv.MOVE_DOWN: StepWords(
        "Move down…", "Move {entry} down?",
        "Puts {entry}, {ref}, after {other}, {other_ref}, in {project}'s queue. Nothing "
        "starts and nothing else moves.", "Move down"),
    cv.REMOVE: StepWords(
        "Remove…", "Take {entry} off the queue?",
        "Removes {entry}, {ref}, from {project}'s queue, so the chain never starts it. The "
        "work item itself is not touched; queue it again to bring it back.",
        "Remove from queue"),
    cv.START: StepWords(
        "Start…", "Start {what} now?",
        "A new Terminal window on the Mac runs Claude with Remote Control in {project}'s "
        "main folder, on {about}. The chain follows it as its next session.",
        "Start session"),
    cv.PAUSE: StepWords(
        "Pause chain…", "Pause the chain in {project}?",
        "Nothing more starts on its own in {project} until you resume it. A session "
        "already running goes on.", "Pause chain"),
    cv.RESUME: StepWords(
        "Resume chain…", "Resume the chain in {project}?",
        "The chain starts queued work in {project} again. Nothing that failed is retried.",
        "Resume chain"),
    cv.GREENLIGHT: StepWords(
        "Go ahead…", "Let the chain go ahead?",
        "The plan's weekly usage cannot be read, so nothing starts on its own. Go ahead "
        "lets every project's chain start queued work until a fresh reading arrives, "
        "which judges again.", "Go ahead"),
    cv.CONTINUE: StepWords(
        "Continue…", "Type continue again?",
        "Types one more \u201ccontinue\u201d into the Claude window working on {ref} and "
        "watches it again from now. Nothing is closed.", "Type continue"),
    cv.RETRY: StepWords(
        "Retry…", "Close the window and start {entry} afresh?",
        "Ends Claude in the stalled window and closes its Terminal tab, then puts {entry}, "
        "{ref}, back at the head of {project}'s queue to start in a new window. The "
        "session stays parked for you to land or abandon.", "Close and retry",
        destructive=True, ends=True),
    cv.CLOSE: StepWords(
        "Close…", "Close the stalled window?",
        "Ends Claude in the window working on {ref} and closes its Terminal tab, then "
        "marks {entry} failed, closed by you. Nothing is retried; the chain goes on to "
        "the next queued work. The session stays parked for you to land or abandon.",
        "Close and mark failed", destructive=True, ends=True),
    # ruling 21: a landed window's notice, answered by the user
    cv.CLOSE_WINDOW: StepWords(
        "Close…", "Close {entry}'s window?",
        "Ends Claude in the window where {ref} landed and closes its Terminal tab. Its work "
        "is merged; the chain in {project} goes on to the next queued work.",
        "Close window", destructive=True, ends=True),
    cv.LEAVE_OPEN: StepWords(
        "Leave open…", "Leave {entry}'s window open?",
        "Keeps the window where {ref} landed open. The oracle does not ask it again, and "
        "the chain in {project} waits until you close the window yourself.", "Leave open"),
}
_CHAIN_STEPS[cv.START_ITEM] = _CHAIN_STEPS[cv.START]

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
    "machine": "On the Mac, for every project",
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

#: What a chain's Retry or Close says it verified (D29's stall, D37).
_CHECKED_STALL = (
    "It is still the chain's current start, and still stalled",
    "Checked again the moment you press {deed}",
)

#: What a landed window's Close says it verified (ruling 21).
_CHECKED_LANDED = (
    "It is still the window where {entry} landed, and it still waits for you",
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

#: Starting a new session (ADR-009 D16): the sheet, its facts and the card.
_START = {
    "title": "Start a new session?",
    "sentence": ("A new Terminal window on the Mac runs Claude with Remote Control in "
                 "{project}'s main folder."),
    "button": "Start session",
    "name": "Remote Control name: {name}",
    "model": "Runs on {model}",
    "windows_none": "No Claude window is open here",
    "windows_one": "1 Claude window already open here",
    "windows": "{n} Claude windows already open here",
    "clean": "Main folder has no uncommitted changes",
    "dirty_one": "Main folder has uncommitted changes (1 file)",
    "dirty": "Main folder has uncommitted changes ({n} files)",
    "dirty_unknown": "Could not tell whether the main folder has uncommitted changes",
    "starting": "Starting…",
    "failed_chip": "Did not start",
    "timeout": ("The window did not appear on the Mac within a minute; check the Mac (a "
                "trust prompt or a login may be waiting)."),
    "opened": ("Claude is running in a new window on the Mac. Pick it up from Remote "
               "Control on your phone as {name}."),
    "sessions_none": "No open session",
    "sessions_one": "1 open session",
    "sessions": "{n} open sessions",
    "paused": " · {n} paused",
}

#: Seconds a started window has to appear in a scan before its card says it
#: did not; and how long a start that failed stays on the overview.
START_APPEARS_WITHIN = 60.0
START_FAILURE_SHOWN = 3600.0

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
     ("invariant could not get a value for {key}: its discovery found none, or could "
      "not choose between several.")),
    (re.compile(r"discovery for (['\"])(?P<key>.+?)\1 timed out\b"),
     "invariant ran out of time finding a value for {key}."),
)

#: Why a failing session is offered no "Re-run tests…" (BUG_093), by
#: ``Recommendation.reason``. The detail says it under "What is going on".
_NO_RERUN = {
    RERUN_UNSCOPED: ("The phone does not re-run these tests: no scoped test run is on "
                     "record for this session, and the panel never runs a whole test "
                     "suite. Run its tests from the session's own window."),
    RERUN_UNRECORDED: ("The phone does not re-run these tests yet: the last scoped run "
                       "was recorded before invariant kept the settings it found, so it "
                       "cannot be repeated the same way. Run its tests once from the "
                       "session's own window."),
}

#: Why a live leftover is offered no "Close…" (BUG_099), by
#: ``Recommendation.reason``: the guard its evidence did not satisfy, said on
#: its leftovers row in place of a pill.
_NO_CLOSE = {
    CLOSE_PS: "invariant could not read its process",
    CLOSE_START: "invariant could not read when its process started",
    CLOSE_NEWER: "its process may be a newer one",
    CLOSE_RECORDS: "invariant could not read Claude's process records",
    CLOSE_OTHER: "its process now runs another Claude conversation",
    CLOSE_CWD: "invariant could not read its folder",
    CLOSE_NOT_ROOTED: "it was started outside this project",
    CLOSE_CLAIMED: "a session in another project uses it",
}

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
    "docs_held": "Docs lock · holds {unit} · {age}{queue}{stale}",
    "docs_queue": " · queue {sessions}",
    "docs_stale": " · stale: {why}",
    "docs_waiting": "Docs lock · queued for {unit} at position {position} behind {holder}",
    "step_owed": "Step owed · {step} · {count} since {since} · invariant step run {step}",
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
    "no_close": "{state} · window {id} · cannot close from here: {why}",
    "inherited": " · Terminal.app needs a restart: quit it fully and relaunch it from the Dock",
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
    return _chain_step_words(rest)


def copy_line(argv: Iterable[str], env: Mapping[str, str] | None,
              masked: Iterable[str] = ()) -> str:
    """The command as one shell line, its pinned variables leading (ADR-009 D5).

    A line with a masked variable is not the command that runs, so it is a
    shell comment that says so: pasted, it runs nothing (BUG_093).
    """
    pinned = [f"{name}={shlex.quote(value)}" for name, value in sorted((env or {}).items())]
    line = " ".join([*pinned, shlex.join(list(argv))])
    hidden = sorted(set(masked) & set(env or {}))
    if hidden:
        return WORDS["masked_copy"].format(names=", ".join(hidden)) + line
    return line


def _current_host(row: dict[str, Any]) -> dict[str, Any] | None:
    hosts = row.get("hosts") or []
    return next((h for h in hosts if h.get("current")), hosts[0] if hosts else None)


def _attention(row: dict[str, Any]) -> dict[str, Any]:
    host = _current_host(row)
    record = host.get("attention") if host else None
    return record if isinstance(record, dict) else {}


def _running(row: dict[str, Any]) -> bool:
    """Whether Claude is working: the document's mid-turn answer, never the
    attention turn on its own, which a missed Stop leaves running (BUG_087).
    Background work its turn left running counts (BUG_094)."""
    return row.get("turn_state") in (TURN_BUSY, TURN_BACKGROUND)


def _background(row: dict[str, Any]) -> bool:
    return row.get("turn_state") == TURN_BACKGROUND


def _background_chip(row: dict[str, Any]) -> str:
    """"Waiting on 1 agent and 2 background commands", from the row's counts."""
    counts = row.get("background") if isinstance(row.get("background"), dict) else {}
    parts = []
    for key, (one, many) in _BACKGROUND_NOUNS:
        n = counts.get(key)
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            parts.append(f"{n} {one if n == 1 else many}")
    if not parts:
        return _BACKGROUND_CHIP[1]
    return _BACKGROUND_CHIP[0].format(work=" and ".join(parts))


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
    if (STUCK in kinds or STALLED in kinds) and row.get("last_activity"):
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
    elif kind in (STUCK, STALLED):
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
    if _background(row):
        chips.append({"text": _background_chip(row), "tone": TONE_WORKING})
    elif _running(row):
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
        sentence = _BACKGROUND_SENTENCE if _background(row) else _RUNNING_SENTENCE
        last = row.get("last_activity")
        if isinstance(last, (int, float)):
            said.append(sentence[0].format(age=age_text(now - last)))
        else:
            said.append(sentence[1])
    return said[:3]


def _waiting(row: dict[str, Any]) -> bool:
    return any(s.get("kind") == WAITING for s in _signals(row))


def _waiting_input(row: dict[str, Any]) -> bool:
    """Waiting on the person with no question or permission prompt recorded:
    the Needs you row that offers "Reply…" (ADR-029 D10, increment 3)."""
    return any(s.get("kind") == WAITING and s.get("cause") == WAITING_INPUT
               for s in _signals(row))


def _runnable(row: dict[str, Any]) -> StepWords | None:
    """The row's step words, when the step is one the panel offers a button for."""
    advice = row.get("recommendation") or {}
    if advice.get("kind") not in (RUN, TERMINAL) or _waiting(row):
        return None
    return step_words(advice.get("argv") or ())


def _waiting_question(row: dict[str, Any]) -> bool:
    return any(s.get("kind") == WAITING and s.get("cause") == "question"
               for s in _signals(row))


def _note(row: dict[str, Any], prompt: Mapping[str, Any] | None = None) -> str | None:
    if _waiting(row):
        if _waiting_question(row) and prompt is not None:
            text = prompt.get("text")
            return text if isinstance(text, str) and text else WORDS["cant_read_prompt"]
        return _INPUT_NOTE if _waiting_input(row) else _WAITING_NOTE
    parts = []
    kinds = {s.get("kind") for s in _signals(row)}
    if kinds & _PAUSED:
        parts.append(_WINDOW_CLOSED)
    phase = _phase(row)
    if phase:
        parts.append(f"{phase}.")
    return " ".join(parts) or None


def _card(row: dict[str, Any], project: str, group: str, now: float,
          acting: bool, title: str | None = None,
          excerpt: Mapping[str, Any] | None = None, reply: bool = False,
          prompt: Mapping[str, Any] | None = None) -> dict[str, Any]:
    since = _since(row, now)
    words = _runnable(row)
    card: dict[str, Any] = {
        "row": row_key(row),
        "project": project,
        "title": title or _title(row),
        "chips": _chips(row, now),
        "age": age_text(now - since) if since is not None else "",
        "excerpt": _card_excerpt(excerpt),
    }
    if group in (WORKING, IDLE):
        phase, active = _phase(row), _active(row, now)
        if phase and active:
            card["line"] = f"{phase} · {active}"
        else:
            line = phase or active or ""
            card["line"] = line[:1].upper() + line[1:]
        return card
    card["note"] = _note(row, prompt)
    card["button"] = {"text": words.button} if acting and words else None
    # A row waiting for input offers "Reply…", and one waiting on a question
    # the page read offers "Answer…"; each opens its conversation (ADR-029
    # D10). One waiting on a permission prompt, or on a question that cannot
    # be read, has no buttons (ADR-009 D8): the card opens.
    card["reply"] = {"text": WORDS["reply"]} if reply and _waiting_input(row) else None
    readable = prompt is not None and isinstance(prompt.get("text"), str) and prompt["text"]
    card["answer"] = ({"text": WORDS["answer"]}
                      if reply and readable and _waiting_question(row) else None)
    card["tap"] = _waiting(row) and card["reply"] is None and card["answer"] is None
    return card


# -- a live window's last message (ADR-029 increment 1) ---------------------------------


def excerpt_hosts(document: Mapping[str, Any]) -> dict[str, str]:
    """Each row key to the host id its last message is read from.

    A session row's and an orphan host row's window, the one ``current``
    names, else its first, while it is alive. A gone window, or one whose
    liveness is not known, is not read.
    """
    found: dict[str, str] = {}
    for project in document.get("projects", []):
        for row in project.get("rows", []):
            if row.get("kind") not in (ROW_SESSION, ROW_HOST):
                continue
            host = _current_host(row) or {}
            host_id = host.get("host_session_id")
            if host.get("alive") is True and isinstance(host_id, str) and host_id:
                found[row_key(row)] = host_id
    return found


def _claude_text(excerpt: Mapping[str, Any] | None) -> str | None:
    text = (excerpt or {}).get("claude")
    return text if isinstance(text, str) and text else None


def _card_excerpt(excerpt: Mapping[str, Any] | None) -> str | None:
    """The start of Claude's last text, for a card or a row; ``None`` without one."""
    text = _claude_text(excerpt)
    return text[:EXCERPT_CARD_CHARS] if text else None


def _last_message(excerpt: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The detail's "Last message": the prompt, Claude's text, and its fold."""
    text = _claude_text(excerpt)
    if text is None:
        return None
    you = (excerpt or {}).get("you")
    head = (text[:EXCERPT_FOLD_CHARS].rstrip() + "…"
            if len(text) > EXCERPT_FOLD_CHARS else None)
    return {"claude": text, "you": you if isinstance(you, str) and you else None,
            "head": head}


def conversation_head(document: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    """The Conversation screen's kicker, title and chips for the row ``key``.

    ``None`` when the document has no such row. The turns under it are
    ``oracle/talk``'s; this names only whose they are (ADR-029 increment 2).
    """
    now = float(document.get("now") or 0.0)
    for project in document.get("projects", []):
        name = (project.get("project") or {}).get("name", "")
        for row in project.get("rows", []):
            if row_key(row) == key:
                return {"kicker": f"{name} · {WORDS['conversation']}", "title": _title(row),
                        "chips": _chips(row, now, aged=True)}
    return None


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
        "copy": copy_line(advice.get("argv") or (), advice.get("env"),
                          advice.get("masked") or ()),
    }


def _job_step(job: Mapping[str, Any]) -> str:
    """The deed a job did, as its result and "Recent" name it."""
    if job.get("kind") == START:
        return _START["button"]
    words = step_words(job.get("argv") or ())
    return words.run if words else _RESULT["unknown_step"]


def result_words(job: Mapping[str, Any]) -> dict[str, str]:
    """A job's outcome in words: the headline, the line under it, and its tone."""
    name = _job_step(job)
    if job.get("state") != "done":
        return {"headline": _RESULT["running"], "line": name, "tone": "running"}
    seconds = max(1, math.ceil(float(job.get("elapsed") or 0)))
    terminal = job.get("kind") in (TERMINAL, START)
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
            add(_RECENT["job"].format(step=_job_step(job), result=result_words(job)["headline"]),
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
    lines += _docs_lock_lines(row)
    # The project's open step debts (ADR-021 D4): owed by the project, so
    # every live session of it shows them.
    for debt in row.get("step_debts") or []:
        lines.append(_TECHNICAL["step_owed"].format(
            step=debt.get("step"), count=debt.get("count"),
            since=str(debt.get("since") or "")[:7] or "?"))
    if row.get("error"):
        lines.append(_TECHNICAL["error"].format(error=row["error"]))
    path = row.get("worktree") or row.get("path") or row.get("root") or None
    return {"lines": lines, "path": path}


def _docs_lock_lines(row: dict[str, Any]) -> list[str]:
    """A session's docs-lock units and queue, under "Technical details" (ADR-014 D5)."""
    docs = row.get("docs_lock") or {}
    lines = []
    for held in docs.get("held") or []:
        queue = [q for q in held.get("queue") or [] if q]
        lines.append(_TECHNICAL["docs_held"].format(
            unit=held.get("unit"),
            age=age_text(float(held.get("held_seconds") or 0)),
            queue=_TECHNICAL["docs_queue"].format(sessions=", ".join(queue)) if queue else "",
            stale=_TECHNICAL["docs_stale"].format(why=held["stale"]) if held.get("stale") else "",
        ))
    waiting = docs.get("waiting")
    if waiting:
        lines.append(_TECHNICAL["docs_waiting"].format(
            unit=waiting.get("unit"), position=waiting.get("position"),
            holder=waiting.get("holder")))
    return lines


def _detail(row: dict[str, Any], project: str, now: float, acting: bool,
            jobs: list[Mapping[str, Any]], started: str | None = None,
            excerpt: Mapping[str, Any] | None = None,
            conversation: bool = False) -> dict[str, Any]:
    """A row's detail view; ``started`` names the window a start of this page opened,
    ``excerpt`` is its window's last message, ``conversation`` whether it offers
    "Open conversation"."""
    key = row_key(row)
    lane = lane_text((row.get("pipeline") or {}).get("methodology"))
    going_on = _sentences(row, now)
    no_rerun = _NO_RERUN.get((row.get("recommendation") or {}).get("reason") or "")
    if no_rerun:
        going_on = [*going_on, no_rerun]
    if started:
        going_on = [*going_on, _START["opened"].format(name=started)]
    return {
        "row": key,
        "kicker": f"{project} · {lane}" if lane else project,
        "title": started or _title(row),
        "chips": _chips(row, now, aged=True),
        "going_on": going_on,
        "last": _last_message(excerpt),
        "conversation": conversation,
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
                   acting: bool, excerpt: Mapping[str, Any] | None = None) -> dict[str, Any]:
    host = _current_host(row) or {}
    last = row.get("last_activity")
    if isinstance(last, (int, float)) and now - last >= 60:
        title = _LEFTOVER["host_title"].format(project=project, age=age_text(now - last))
    else:
        title = _LEFTOVER["host_title_now"].format(project=project)
    window = str(host.get("host_session_id") or "")[:8]
    words = _runnable(row)
    why = _NO_CLOSE.get((row.get("recommendation") or {}).get("reason") or "")
    line = (_LEFTOVER["no_close"].format(state=_host_state(row, host), id=window, why=why)
            if why else _LEFTOVER["window"].format(state=_host_state(row, host), id=window))
    if any(s.get("kind") == INHERITED_ENV for s in _signals(row)):
        line += _LEFTOVER["inherited"]
    return {"row": row_key(row), "title": title, "line": line,
            "excerpt": _card_excerpt(excerpt),
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


# -- the oracle's own condition (BUG_089) ------------------------------------------------


def _oracle_skew(document: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    oracle = document.get("oracle")
    if not isinstance(oracle, Mapping) or not isinstance(oracle.get("skew"), Mapping):
        return None, []
    unread = [str(n) for n in oracle.get("unread") or []]
    return dict(oracle["skew"]), unread


def _oracle_card(document: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The card and detail saying the oracle is older than the install, if it is."""
    skew, unread = _oracle_skew(document)
    if skew is None:
        return None
    restarts = "changed" in (skew.get("reasons") or [])
    chips = [{"text": _ORACLE["restarting" if restarts else "restart"], "tone": TONE_PROBLEM}]
    names = ", ".join(unread)
    going_on = [_ORACLE["changed" if restarts else "rules"]]
    if unread:
        going_on.append(_ORACLE["unread_one"].format(names=names) if len(unread) == 1
                        else _ORACLE["unread"].format(n=len(unread), names=names))
    started = (skew.get("started") or {}).get("digest")
    installed = (skew.get("installed") or {}).get("digest")
    lines = [_ORACLE["started"].format(started=started, installed=installed)]
    if skew.get("rules"):
        lines.append(_ORACLE["lacks"].format(rules=", ".join(skew["rules"])))
    for project in document.get("projects", []):
        if project.get("unread") is not None:
            lines.append(_ORACLE["error"].format(
                project=(project.get("project") or {}).get("name", ""), error=project["unread"]))
    lines.append(_ORACLE["by_hand"])
    card = {"row": ORACLE_ROW, "project": _ORACLE["kicker"], "title": _ORACLE["title"],
            "chips": chips, "age": "",
            "note": _ORACLE["note"].format(names=names) if unread else None,
            "button": None, "tap": False}
    detail = {"row": ORACLE_ROW, "kicker": _ORACLE["kicker"], "title": _ORACLE["title"],
              "chips": chips, "going_on": going_on, "step": None, "recent": [],
              "technical": {"lines": lines, "path": None}}
    return card, detail


# -- the view --------------------------------------------------------------------------


def view(document: Mapping[str, Any], acting: bool = False,
         jobs: Iterable[Mapping[str, Any]] = (), starting: bool = False,
         chains: Mapping[str, Any] | None = None,
         push: Mapping[str, Any] | None = None,
         excerpts: Mapping[str, Mapping[str, Any]] | None = None,
         talking: bool = False, sending: bool = False,
         prompts: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Everything the panel draws, from one ``assess`` document.

    With ``acting``, a row whose step runs (``run`` or ``terminal``) and that
    waits on no prompt gets its button (ADR-009 D8). ``jobs`` are this
    device's recent actions, listed under "Recent" on their session; its
    starts are drawn under Working (:func:`_starts`). With ``starting`` too,
    the page may start a session, and ``start`` lists the projects it may
    start one in. ``chains`` is the chain's read (``oracle/chainview``): its
    cards join Needs you, each project's row gains its chain line, and
    ``chain`` holds each project's Chain screen (ADR-027 increment 3).
    ``push`` is what this device may do about web push, worded by
    :func:`push_view` (ADR-027 increment 4). ``excerpts`` is each row's live
    window's last message, by row key, as ``oracle/web`` read it for this
    device (:func:`excerpt_hosts`, ADR-029 increment 1). With ``talking``, the
    server reads conversations, and a detail whose row has a live window
    offers its Conversation screen (ADR-029 increment 2). With ``sending`` too,
    the server sends messages, and a Needs you card whose live window waits
    for input offers "Reply…" (ADR-029 D10, increment 3). ``prompts`` is each
    row's waiting question as ``oracle/web`` read it (``text``, its first line,
    or ``unreadable``): the card's note, and with ``sending`` its "Answer…"
    (increment 5).
    """
    now = float(document.get("now") or 0.0)
    excerpts = excerpts or {}
    readable = excerpt_hosts(document) if talking else {}
    jobs = list(jobs)
    pending, named = _starts(document, jobs, now)
    placed: dict[str, list[tuple[int, float, int, dict[str, Any]]]] = {
        g.id: [(-1, math.inf, 0, card) for card in pending] if g.id == WORKING else []
        for g in GROUPS}
    details: dict[str, dict[str, Any]] = {}
    hosts: list[dict[str, Any]] = []
    folders: list[dict[str, str]] = []
    position = 0
    oracle = _oracle_card(document)
    if oracle is not None:
        placed[PROBLEMS].append((-1, -math.inf, 0, oracle[0]))
        details[ORACLE_ROW] = oracle[1]
    if chains is not None:
        for rank, began, card in _chain_cards(chains, now, acting):
            position += 1
            placed[NEEDS].append((rank, began, position, card))
            if card["row"] == CHAIN_USAGE_ROW:
                details[CHAIN_USAGE_ROW] = _usage_detail(chains.get("usage") or {}, card)
    for project in document.get("projects", []):
        name = (project.get("project") or {}).get("name", "")
        for row in project.get("rows", []):
            group = group_of(row)
            key = row_key(row)
            started = named.get(key)
            if started and group == LEFTOVERS and _orphan_class(row) == "active":
                group = WORKING
            if group == LEFTOVERS:
                if row.get("kind") == ROW_HOST:
                    hosts.append(_host_leftover(row, name, now, acting, excerpts.get(key)))
                else:
                    folders.append(_folder_leftover(row, name))
                continue
            since = _since(row, now)
            position += 1
            placed[group].append((_rank(row, group), since if since is not None else math.inf,
                                  position, _card(row, name, group, now, acting, started,
                                                  excerpts.get(key),
                                                  sending and key in readable,
                                                  (prompts or {}).get(key))))
            details[key] = _detail(row, name, now, acting, jobs, started, excerpts.get(key),
                                   key in readable)
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
        "start": _start_projects(document) if acting and starting else None,
        "projects": _project_rows(document, chains),
        "chain": _chain_view(chains, now, acting, jobs),
        "push": push_view(push),
    }


# -- web push, this device's setting (ADR-027 increment 4) -------------------------------

#: Why the Mac offers no push, to the sentence that says so.
_PUSH_WHY = {"extra": "push_no_extra", "http": "push_needs_https", "read": "push_unreadable"}


def push_view(push: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The Notifications row's words, from what the server knows of this device.

    ``push`` is ``{available, why}`` when the Mac offers no push, else
    ``{available, key, on, error}``: the VAPID public key the browser subscribes
    with, whether this device has a subscription, and the last delivery error
    to it. ``line`` says why push is not offered; ``line_on`` is the row's line
    while it is on here. What only the browser knows (no push API, a blocked
    permission, a subscription made in another app) the page words from
    ``WORDS``.
    """
    if push is None:
        return None
    if not push.get("available"):
        why = _PUSH_WHY.get(push.get("why"), "push_no_extra")
        return {"available": False, "line": WORDS[why], "key": None, "on": False,
                "line_on": None}
    error = push.get("error")
    return {"available": True, "line": None, "key": push.get("key"), "on": bool(push.get("on")),
            "line_on": WORDS["push_on_error"].format(error=error) if error
            else WORDS["push_on"]}


# -- starting a new session (ADR-009 D16) ------------------------------------------------


def _starts(document: Mapping[str, Any], jobs: list[Mapping[str, Any]],
            now: float) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """This device's starts: the cards still pending, and the windows they opened.

    A start whose window a scan shows (a live host launched by its name) is
    no card: an orphan host's row is named by it instead (the second answer,
    row key to name), and a session that took the window is drawn as that
    session. One not seen is "Starting…" while it runs and for a minute
    after; then, or when the launch itself failed, a failure with its reason,
    shown for an hour. One seen once and gone since leaves nothing.
    """
    pending: list[dict[str, Any]] = []
    named: dict[str, str] = {}
    for job in jobs:
        if job.get("kind") != START:
            continue
        name = job.get("remote_control") or ""
        row = find_started(dict(document), job.get("project") or "", name)
        if row is not None:
            if row.get("kind") == ROW_HOST:
                named[row_key(row)] = name
            continue
        if job.get("appeared"):
            continue
        age = now - float(job.get("started") or 0.0)
        result = None
        if job.get("state") != "done" or (job.get("ok") and age < START_APPEARS_WITHIN):
            state, line, chips = "starting", _START["starting"], []
        elif age >= START_FAILURE_SHOWN:
            continue
        else:
            state, chips = "failed", [{"text": _START["failed_chip"], "tone": TONE_PROBLEM}]
            line = (job.get("words") or {}).get("why") or ""
            if job.get("ok"):
                line = _START["timeout"]
                result = {"headline": _RESULT["failed"], "line": _START["button"],
                          "tone": "failed", "why": line, "outcome": ""}
        pending.append({"start": job.get("id"), "state": state,
                        "project": job.get("subject") or "", "title": name, "line": line,
                        "chips": chips, "result": result})
    return pending, named


def _paused(row: dict[str, Any]) -> bool:
    """A session with no live host: its host ended, or a card that reads "Paused"."""
    return bool(row.get("host_ended")) or bool(
        {s.get("kind") for s in _signals(row)} & _PAUSED)


def _start_projects(document: Mapping[str, Any]) -> dict[str, Any]:
    """The registered projects a start may name, each with its open sessions counted.

    Open is not ended: every session row, which is every session card across
    Needs you, Problems, Working and Idle. Those with no live host are named
    paused as well (BUG_092).
    """
    projects = []
    for project in document.get("projects", []):
        entry = project.get("project") or {}
        rows = [row for row in project.get("rows", []) if row.get("kind") == ROW_SESSION]
        count = len(rows)
        line = (_START["sessions_none"] if count == 0 else _START["sessions_one"] if count == 1
                else _START["sessions"].format(n=count))
        paused = sum(1 for row in rows if _paused(row))
        if paused:
            line += _START["paused"].format(n=paused)
        projects.append({"id": entry.get("key", ""), "name": entry.get("name", ""),
                         "line": line})
    return {"projects": projects}


def start_words(prepared: Mapping[str, Any], project: str) -> dict[str, Any]:
    """What the "New session" sheet says for a start ``oracle start-session`` prepared.

    ``prepared`` is the command's ``--dry-run --json`` answer. The two facts
    are worded so neither reads as a refusal: they inform, and block nothing.
    """
    facts = prepared.get("facts") or {}
    windows = int(facts.get("open_windows") or 0)
    changes = facts.get("uncommitted")
    return {
        "title": _START["title"],
        "sentence": _START["sentence"].format(project=project),
        "button": _START["button"],
        "name": prepared["name"],
        "name_line": _START["name"].format(name=prepared["name"]),
        "label": prepared.get("label") or "",
        "prompt": prepared.get("prompt") or "",
        "model": prepared.get("model") or "",
        "model_line": (_START["model"].format(model=prepared["model"])
                       if prepared.get("model") else ""),
        "command": copy_line(prepared.get("argv") or (), None),
        "place": _PLACE["main"].format(project=project),
        "cwd": prepared.get("cwd"),
        "facts": [
            (_START["windows_none"] if windows == 0 else _START["windows_one"] if windows == 1
             else _START["windows"].format(n=windows)),
            (_START["dirty_unknown"] if changes is None else _START["clean"] if changes == 0
             else _START["dirty_one"] if changes == 1 else _START["dirty"].format(n=changes)),
        ],
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
                  launch: str | None, now: float | None = None,
                  masked: Iterable[str] = ()) -> dict[str, Any] | None:
    """What the confirm sheet says for a step, or ``None`` for one the table lacks.

    The command line and the place lead; the exact folder, each added variable
    and a terminal's launch line sit behind the sheet's toggle (ADR-009 D6). A
    destructive step lists what goes, from the row (D7). A ``masked`` variable
    is shown masked and says so, and the command's Copy line is not runnable.
    """
    argv = tuple(argv)
    masked = tuple(masked)
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
        "command": copy_line(argv, env, masked),
        "place": _place(row, project, cwd),
        "cwd": cwd,
        "env": [f"{name}={value}" + (WORDS["masked_env"] if name in masked else "")
                for name, value in sorted((env or {}).items())],
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


# -- the chain (ADR-027 increment 3) -----------------------------------------------------

#: Every word the chain's cards, line and screen say (rulings 15 and 16).
_CHAIN = {
    "line": "Chain: {status}",
    "queued": " · {n} queued",
    "proposed": " · {n} proposed",
    cv.RUNNING: ("running {entry}", "Running {entry}", TONE_WORKING),
    cv.STARTING: ("starting {entry}", "Starting {entry}", TONE_WORKING),
    cv.PAUSED: ("paused", "Paused", TONE_NEEDS),
    cv.HELD: ("held", "Held", TONE_NEEDS),
    cv.WAITING: ("waiting", "Waiting", TONE_IDLE),
    cv.IDLE: ("idle", "Idle", TONE_IDLE),
    cv.UNREAD: ("cannot be read", "Cannot be read", TONE_PROBLEM),
    "kicker": "Chain",
    "going_running": "The chain started {entry}, {ref}, and follows it until its session ends.",
    "going_starting": "The chain is opening a window on the Mac for {entry}, {ref}.",
    "going_paused": ("The chain is paused. {why} Nothing starts on its own until you resume "
                     "it."),
    "going_held": "The plan's weekly usage holds the chain, so queued work waits. {why}",
    "going_waiting_one": "1 entry is queued. It starts when nothing else runs in {project}.",
    "going_waiting": ("{n} entries are queued. The next one starts when nothing else runs in "
                      "{project}."),
    "going_idle": "Nothing is queued in {project}.",
    "going_unread": "invariant cannot read this project's chain now.",
    "paused_by": {
        "user": "It was paused by hand.",
        "report": "A session reported that it is blocked or needs you.",
        "failure": "A triage failed, so nobody is left to judge the failure.",
    },
    "paused_other": "It was paused for a reason Technical details names.",
    "empty": "Nothing is queued.",
    "entry_title": "{entry} · {ref}",
    "offered": "Offered, never started on its own",
    "triage_of": "Triage of {entry}",
    "retry_of": "Retry of {entry}",
    "by_user": "Added by you",
    "by_oracle": "Added by the oracle",
    "by_session": "Added by a session",
    "by_backlog": "Added from the backlog",
    "serves": "Serves {item}",
    "blind": "Reproduction saw nothing ({n} of {runs} runs)",
    "up": "Move {entry} up…",
    "down": "Move {entry} down…",
    "from_queue": "From the queue: it starts only when you start it.",
    "from_backlog": "The backlog's first ready item, offered because nothing is queued.",
    "now_running": "Running in a window on the Mac",
    "now_reproducing": "Reproducing in a window on the Mac",
    "now_starting": "Opening its window on the Mac",
    "stall": {"limit": "Stalled at a usage limit", "error": "Stalled on an error"},
    "stall_other": "Stalled",
    "nudged": " · nudged {age} ago",
    "nudged_now": " · nudged just now",
    "still": " · still stalled after one nudge",
    "waits_reset": " · nudged once it resets",
    "not_nudged": " · not nudged",
    # a landed window (ruling 21)
    "landed": {
        "watching": "Landed · its window is asked to close once it is quiet",
        "asking": "Landed · asked whether its window may close",
        "asked": "Landed · asked whether its window may close",
        "consented": "Landed · closing its window, as it agreed",
        "closing": "Landed · closing its window, as it agreed",
        "notified": "Landed · its window is still open and waits for you",
        "left_open": "Landed · left open by you; the chain waits until you close it",
        "users": ("Landed · you typed in its window, so it is yours; the chain waits until "
                  "you close it"),
    },
    "landed_not_yet": "Landed · said not yet: \u201c{note}\u201d; asked again later",
    "landed_other": "Landed",
    "landed_chip": "Window open",
    "landed_close": "Close ends it; Leave open keeps it, and the chain waits.",
    "landed_why": {
        "no_answer": "It landed, but did not answer when asked whether its window may close.",
        "not_yet": "It landed, and said not yet {n} times: \u201c{note}\u201d.",
        "not_typed": ("It landed, but the oracle could not ask it: its Terminal tab was not "
                      "found, or typing failed."),
        "unread": ("It landed, and the oracle cannot tell whether you typed in its window "
                   "since."),
        "close_failed": "It agreed to close, but closing its window failed.",
    },
    "landed_why_other": "It landed, and its window still holds the project.",
    # the cards
    "proposed_chip": "Proposed",
    "stall_still_chip": "Still stalled",
    "stall_not_nudged_chip": "Not nudged",
    "stall_still_note": ("One nudge did not move it. Continue types once more; Retry and Close "
                         "end its window."),
    "stall_no_tab": ("It could not be nudged: its Terminal tab was not found. Continue, "
                     "Retry or Close."),
    "stall_failed": ("It could not be nudged: typing into its tab failed. Continue, Retry or "
                     "Close."),
    "paused_title": "The chain is paused",
    "paused_chip": "Chain paused",
    "usage_project": "Every project",
    "usage_unknown_title": "The weekly usage cannot be read",
    "usage_over_title": "The weekly usage is over the cap",
    "usage_chip": "Held",
    "usage_missing": "No reading yet: the status line that records it is not installed.",
    "usage_no_weekly": "The last reading has no weekly figure.",
    "usage_stale": "The last reading is {age} old.",
    "usage_unknown": ("{why} Nothing starts on its own until a fresh reading arrives; Go "
                      "ahead lets the chain start queued work until then."),
    "usage_over": ("{weekly}% of the week is used, at or over the cap of {cap}%. Nothing "
                   "starts on its own until it falls; a start by hand still runs."),
    # what the destructive sheet names
    "gone": "Its window has already closed; nothing is ended",
    # technical details
    "tech_current": "Chain start {id} · state {state} · session {session} · window {name}",
    "tech_paused": "Paused by {by} since {since}: {reason}",
    "tech_entry": "{entry} {ref} · {state}",
    "tech_reason": ": {reason}",
    "tech_queue_error": "Queue error: {error}",
    "tech_ledger_error": "Ledger error: {error}",
    "tech_usage": "Weekly usage {weekly} · five-hour {five_hour} · cap {cap}%",
    "tech_read_at": "Read at {at}",
    "tech_no_reading": "No reading recorded",
    "tech_greenlight": "Go ahead given {at}",
}

#: Ended entries listed under Technical details, newest last.
CHAIN_ENDED_SHOWN = 5

#: Where a chain card's condition ranks among the Needs-you cards.
_CHAIN_RANK = {"stall": 1, "landed": 1, "paused": 2, "usage": 2, "proposal": 3}

#: The chain's own cards: their row keys, which no scan row can have.
CHAIN_USAGE_ROW = f"{cv.MACHINE}|{cv.SUBJECT}:card:usage"


def _fill_words(template: str, facts: Mapping[str, Any]) -> str:
    """``template`` filled from ``facts``, an unknown name left as it is."""
    return re.sub(r"\{(\w+)\}", lambda m: str(facts.get(m.group(1), m.group(0))), template)


def _offered(state: Mapping[str, Any]) -> dict[tuple[str, str | None], str]:
    return {(o["action"], o["arg"]): o["key"] for o in state.get("offered") or []}


def _chain_button(state: Mapping[str, Any], action: str, arg: str | None,
                  acting: bool) -> dict[str, str] | None:
    key = _offered(state).get((action, arg))
    if not acting or key is None:
        return None
    return {"text": _CHAIN_STEPS[action].button, "row": key}


def chain_line(state: Mapping[str, Any]) -> str:
    """The Projects row's line: "Chain: running Q4 · 3 queued"."""
    status = state["status"]
    current = state.get("current") or {}
    text = _CHAIN["line"].format(status=_CHAIN[status][0].format(entry=current.get("entry")))
    if status == cv.UNREAD:
        return text
    if state["queued"]:
        text += _CHAIN["queued"].format(n=len(state["queued"]))
    if state["proposals"]:
        text += _CHAIN["proposed"].format(n=len(state["proposals"]))
    return text


def _usage_sentence(usage: Mapping[str, Any]) -> str:
    if usage["hold"] == "over":
        return _CHAIN["usage_over"].format(weekly=f"{usage['weekly']:g}", cap=f"{usage['cap']:g}")
    why = usage.get("why")
    if why == "stale":
        reason = _CHAIN["usage_stale"].format(age=age_text(float(usage.get("age") or 0)))
    else:
        reason = _CHAIN["usage_no_weekly" if why == "no_weekly" else "usage_missing"]
    return _CHAIN["usage_unknown"].format(why=reason)


def _paused_why(paused: Mapping[str, Any]) -> str:
    return _CHAIN["paused_by"].get(paused.get("by") or "", _CHAIN["paused_other"])


def _stall_card(state: Mapping[str, Any]) -> tuple[str, str] | None:
    """The stall's chip and note when it needs the user: the nudge did not clear it, or
    could not be made. A stall still waiting for its nudge is no card."""
    stall = (state.get("current") or {}).get("stall")
    if not isinstance(stall, dict):
        return None
    nudge = stall.get("nudge")
    if stall.get("still") or stall.get("still_said"):
        return _CHAIN["stall_still_chip"], _CHAIN["stall_still_note"]
    if nudge in (None, "sending", "typed"):
        return None
    note = _CHAIN["stall_no_tab" if nudge == "no_tab" else "stall_failed"]
    return _CHAIN["stall_not_nudged_chip"], note


def _landed_note(landed: Mapping[str, Any]) -> str:
    """A landed window's card: why it still waits, then what Close and Leave open do."""
    why = _CHAIN["landed_why"].get(landed.get("why") or "", _CHAIN["landed_why_other"])
    return (why.format(n=landed.get("asks") or 0, note=landed.get("note") or "")
            + " " + _CHAIN["landed_close"])


def _landed_line(landed: Mapping[str, Any]) -> str:
    phase = landed.get("state") or ""
    if phase == "watching" and landed.get("note"):
        return _CHAIN["landed_not_yet"].format(note=landed["note"])
    return _CHAIN["landed"].get(phase, _CHAIN["landed_other"])


def _chain_card(row: str, project: str, title: str, chip: str, note: str,
                button: dict[str, str] | None, chain: str | None,
                actions: list[dict[str, str]] | None = None, age: str = "") -> dict[str, Any]:
    return {"row": row, "project": project, "title": title,
            "chips": [{"text": chip, "tone": TONE_NEEDS}], "age": age, "note": note,
            "button": button, "tap": False, "chain": chain, "actions": actions or [],
            "kind": "chain"}


def _age_since(epoch: float | None, now: float) -> tuple[str, float]:
    if epoch is None:
        return "", math.inf
    return (age_text(now - epoch) if now - epoch >= 60 else ""), epoch


def _chain_cards(chains: Mapping[str, Any], now: float,
                 acting: bool) -> list[tuple[int, float, dict[str, Any]]]:
    """The chain's Needs-you cards, each with its rank and when its condition began."""
    cards: list[tuple[int, float, dict[str, Any]]] = []
    usage = chains.get("usage") or {}
    if usage.get("hold") and usage.get("held") and not chains.get("error"):
        title = _CHAIN["usage_over_title" if usage["hold"] == "over" else "usage_unknown_title"]
        offer = next((o for o in chains.get("offered") or []
                      if o["action"] == cv.GREENLIGHT), None)
        button = ({"text": _CHAIN_STEPS[cv.GREENLIGHT].button, "row": offer["key"]}
                  if acting and offer else None)
        cards.append((_CHAIN_RANK["usage"], math.inf, _chain_card(
            CHAIN_USAGE_ROW, _CHAIN["usage_project"], title, _CHAIN["usage_chip"],
            _usage_sentence(usage), button, None)))
    for key, state in (chains.get("projects") or {}).items():
        name = state["name"]
        stall = _stall_card(state)
        if stall is not None:
            current = state["current"]
            since = current["stall"].get("since_epoch")
            age, began = _age_since(since if isinstance(since, (int, float)) else None, now)
            actions = [b for b in (_chain_button(state, a, None, acting)
                                   for a in cv.STALL_ACTIONS) if b]
            cards.append((_CHAIN_RANK["stall"], began, _chain_card(
                f"{key}|{cv.SUBJECT}:card:stall", name, current.get("ref") or "", stall[0],
                stall[1], None, key, actions, age)))
        landed = state.get("landed")
        if landed and landed.get("state") == "notified":
            age, began = _age_since(_epoch(landed.get("since")), now)
            actions = [b for b in (_chain_button(state, a, None, acting)
                                   for a in cv.LANDED_ACTIONS) if b]
            cards.append((_CHAIN_RANK["landed"], began, _chain_card(
                f"{key}|{cv.SUBJECT}:card:landed", name, landed.get("ref") or "",
                _CHAIN["landed_chip"], _landed_note(landed), None, key, actions, age)))
        if state.get("paused"):
            age, began = _age_since(_epoch(state["paused"].get("since")), now)
            cards.append((_CHAIN_RANK["paused"], began, _chain_card(
                f"{key}|{cv.SUBJECT}:card:paused", name, _CHAIN["paused_title"],
                _CHAIN["paused_chip"], _paused_why(state["paused"]),
                _chain_button(state, cv.RESUME, None, acting), key, age=age)))
        for proposal in state.get("proposals") or []:
            age, began = _age_since(_epoch(proposal.get("since")), now)
            backlog = proposal["source"] == "backlog"
            action, arg = ((cv.START_ITEM, proposal["ref"]) if backlog
                           else (cv.START, proposal["entry"]))
            cards.append((_CHAIN_RANK["proposal"], began, _chain_card(
                f"{key}|{cv.SUBJECT}:card:proposal:{arg}", name,
                proposal.get("title") or proposal["ref"], _CHAIN["proposed_chip"],
                _CHAIN["from_backlog" if backlog else "from_queue"],
                _chain_button(state, action, arg, acting), key, age=age)))
    return cards


def _usage_detail(usage: Mapping[str, Any], card: Mapping[str, Any]) -> dict[str, Any]:
    """The weekly-usage card's detail: the hold in words, the reading under Technical details."""
    def shown(value: Any) -> str:
        return "?" if value is None else f"{value:g}%"
    lines = [_CHAIN["tech_usage"].format(weekly=shown(usage.get("weekly")),
                                         five_hour=shown(usage.get("five_hour")),
                                         cap=f"{usage['cap']:g}")]
    read_at = usage.get("read_at")
    lines.append(_CHAIN["tech_read_at"].format(at=datetime.fromtimestamp(
        read_at, timezone.utc).isoformat(timespec="seconds"))
        if isinstance(read_at, (int, float)) else _CHAIN["tech_no_reading"])
    if usage.get("greenlight"):
        lines.append(_CHAIN["tech_greenlight"].format(at=usage["greenlight"]))
    return {"row": card["row"], "kicker": _CHAIN["kicker"], "title": card["title"],
            "chips": card["chips"], "going_on": [card["note"]], "step": None, "recent": [],
            "technical": {"lines": lines, "path": None}}


def _entry_line(entry: Mapping[str, Any]) -> str:
    parts = []
    if entry.get("mode") == "propose":
        parts.append(_CHAIN["offered"])
    if entry.get("triage_of"):
        parts.append(_CHAIN["triage_of"].format(entry=entry["triage_of"]))
    if entry.get("retry_of"):
        parts.append(_CHAIN["retry_of"].format(entry=entry["retry_of"]))
    if entry.get("serves"):
        parts.append(_CHAIN["serves"].format(item=entry["serves"]))
    if entry.get("blind"):
        parts.append(_CHAIN["blind"].format(n=entry["blind"], runs=BLIND_RUNS))
    adder = entry.get("added_by")
    parts.append(_CHAIN[{"user": "by_user", "oracle": "by_oracle", "backlog": "by_backlog"}
                        .get(adder, "by_session")])
    return " · ".join(parts)


def _now_line(current: Mapping[str, Any], now: float) -> str:
    if current.get("state") == "launching":
        return _CHAIN["now_starting"]
    stall = current.get("stall")
    if not isinstance(stall, dict):
        return _CHAIN["now_reproducing" if current.get("reproduce") else "now_running"]
    line = _CHAIN["stall"].get(stall.get("cause") or "", _CHAIN["stall_other"])
    nudge = stall.get("nudge")
    if stall.get("still") or stall.get("still_said"):
        return line + _CHAIN["still"]
    if nudge in ("typed", "sending"):
        nudged = stall.get("nudged_epoch")
        if isinstance(nudged, (int, float)) and now - nudged >= 60:
            return line + _CHAIN["nudged"].format(age=age_text(now - nudged))
        return line + _CHAIN["nudged_now"]
    if nudge is None:
        return line + (_CHAIN["waits_reset"] if stall.get("waiting_said") else "")
    return line + _CHAIN["not_nudged"]


def _chain_technical(state: Mapping[str, Any]) -> dict[str, Any]:
    lines = []
    if state.get("error"):
        source = "tech_queue_error" if state.get("error_from") == cv.FROM_QUEUE \
            else "tech_ledger_error"
        lines.append(_CHAIN[source].format(error=state["error"]))
    current = state.get("current")
    if current:
        lines.append(_CHAIN["tech_current"].format(
            id=current.get("id"), state=current.get("state"),
            session=current.get("session") or "none", name=current.get("name") or "none"))
    paused = state.get("paused")
    if paused:
        lines.append(_CHAIN["tech_paused"].format(by=paused.get("by"), since=paused.get("since"),
                                                  reason=paused.get("reason")))
    ended = [e for e in state.get("entries") or [] if e["state"] in ("done", "failed")]
    for entry in ended[-CHAIN_ENDED_SHOWN:]:
        line = _CHAIN["tech_entry"].format(entry=entry["id"], ref=entry["ref"],
                                           state=entry["state"])
        if entry.get("reason"):
            line += _CHAIN["tech_reason"].format(reason=entry["reason"])
        lines.append(line)
    return {"lines": lines, "path": None}


def _chain_going_on(state: Mapping[str, Any], usage: Mapping[str, Any]) -> list[str]:
    status, current = state["status"], state.get("current") or {}
    values = {"entry": current.get("entry"), "ref": current.get("ref"),
              "project": state["name"], "n": len(state["queued"])}
    if status == cv.PAUSED:
        return [_CHAIN["going_paused"].format(why=_paused_why(state["paused"]))]
    if status == cv.HELD:
        return [_CHAIN["going_held"].format(why=_usage_sentence(usage))]
    if status == cv.WAITING and len(state["queued"]) == 1:
        return [_CHAIN["going_waiting_one"].format(**values)]
    return [_CHAIN[f"going_{status}"].format(**values)]


def _chain_screen(state: Mapping[str, Any], usage: Mapping[str, Any], now: float,
                  acting: bool, jobs: list[Mapping[str, Any]]) -> dict[str, Any]:
    """One project's Chain screen: what is going on, now, proposed, the queue."""
    key, status = state["key"], state["status"]
    current = state.get("current")
    chip = _CHAIN[status]
    control = None
    for action, primary in ((cv.RESUME, True), (cv.PAUSE, False)):
        button = _chain_button(state, action, None, acting)
        if button:
            control = {**button, "primary": primary}
    now_card = None
    if current:
        actions = [b for b in (_chain_button(state, a, None, acting)
                               for a in cv.STALL_ACTIONS) if b]
        now_card = {"title": _CHAIN["entry_title"].format(entry=current.get("entry"),
                                                          ref=current.get("ref")),
                    "line": _now_line(current, now), "actions": actions}
    elif state.get("landed"):
        landed = state["landed"]
        actions = [b for b in (_chain_button(state, a, None, acting)
                               for a in cv.LANDED_ACTIONS) if b]
        now_card = {"title": _CHAIN["entry_title"].format(entry=landed.get("entry"),
                                                          ref=landed.get("ref")),
                    "line": _landed_line(landed), "actions": actions}
    proposed = []
    seen: set[str] = set()
    for proposal in state.get("proposals") or []:
        backlog = proposal["source"] == "backlog"
        action, arg = (cv.START_ITEM, proposal["ref"]) if backlog else (cv.START,
                                                                          proposal["entry"])
        seen.add(arg)
        proposed.append({"title": proposal.get("title") or proposal["ref"],
                         "line": _CHAIN["from_backlog" if backlog else "from_queue"],
                         "button": _chain_button(state, action, arg, acting)})
    rows = []
    for entry in state.get("entries") or []:
        if entry["state"] != "queued":
            continue
        if entry.get("mode") == "propose" and entry["id"] not in seen:
            proposed.append({"title": entry["ref"], "line": _CHAIN["from_queue"],
                             "button": _chain_button(state, cv.START, entry["id"], acting)})
        offered = _offered(state)
        up, down = (offered.get((a, entry["id"])) for a in (cv.MOVE_UP, cv.MOVE_DOWN))
        rows.append({
            "id": entry["id"],
            "title": _CHAIN["entry_title"].format(entry=entry["id"], ref=entry["ref"]),
            "line": _entry_line(entry),
            "brief": entry.get("brief"),
            "up": {"label": _CHAIN["up"].format(entry=entry["id"]), "row": up}
            if acting and up else None,
            "down": {"label": _CHAIN["down"].format(entry=entry["id"]), "row": down}
            if acting and down else None,
            "remove": _chain_button(state, cv.REMOVE, entry["id"], acting),
        })
    recent = [{"text": _RECENT["job"].format(step=_job_step(job),
                                             result=result_words(job)["headline"]),
               "at": job["started"]}
              for job in jobs
              if job.get("row") == f"{key}|{cv.SUBJECT}"
              and isinstance(job.get("started"), (int, float))]
    return {
        "key": key,
        "kicker": _CHAIN["kicker"],
        "title": state["name"],
        "chips": [{"text": chip[1].format(entry=(current or {}).get("entry")),
                   "tone": chip[2]}],
        "going_on": _chain_going_on(state, usage),
        "control": control,
        "now": now_card,
        "proposed": proposed,
        "queue": rows,
        "empty": None if rows or status == cv.UNREAD else _CHAIN["empty"],
        "recent": sorted(recent, key=lambda e: -e["at"]),
        "technical": _chain_technical(state),
    }


def _chain_view(chains: Mapping[str, Any] | None, now: float, acting: bool,
                jobs: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    if chains is None:
        return None
    usage = chains.get("usage") or {}
    return {"projects": {key: _chain_screen(state, usage, now, acting, jobs)
                         for key, state in (chains.get("projects") or {}).items()}}


def _project_rows(document: Mapping[str, Any],
                  chains: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The Projects section's rows: the start picker's, each with its chain line."""
    states = (chains or {}).get("projects") or {}
    return [{**project, "chain": chain_line(states[project["id"]])
             if project["id"] in states else None}
            for project in _start_projects(document)["projects"]]


def chain_confirm_words(action: str, project: str, facts: Mapping[str, Any],
                        argv: Iterable[str], cwd: str | None,
                        now: float | None = None) -> dict[str, Any]:
    """What the confirm sheet says for a chain action (ruling 14), as :func:`confirm_words`.

    ``facts`` are ``oracle/chainview.derive``'s: the entry and its ref, the
    neighbour a move passes, a backlog item's title, and for Retry and Close
    the stalled window as a fresh scan saw it, which the red panel names.
    """
    words = _CHAIN_STEPS[action]
    values = {**facts, "project": project,
              "what": facts.get("entry") or facts.get("ref"),
              "about": facts.get("title") or facts.get("ref")}
    deletes: list[str] = []
    checked: list[str] = []
    if words.ends:
        if facts.get("window"):
            window = {"host_session_id": facts["window"], "pid": facts.get("pid"),
                      "current": True}
            deletes = _ends({"hosts": [window], "last_activity": facts.get("last_activity")},
                            project, now)
        else:
            deletes = [_CHAIN["gone"]]
        lines = _CHECKED_LANDED if action in cv.LANDED_ACTIONS else _CHECKED_STALL
        checked = [_fill_words(line, {**values, "deed": words.run}) for line in lines]
    place = (_PLACE["machine"] if action == cv.GREENLIGHT
             else _PLACE["main"].format(project=project))
    return {
        "title": _fill_words(words.title, values),
        "sentence": _fill_words(words.sentence, values),
        "button": words.run,
        "command": copy_line(argv, None),
        "place": place,
        "cwd": cwd,
        "env": [],
        "launch": None,
        "destructive": words.destructive,
        "deletes_heading": WORDS["this_ends" if words.ends else "this_deletes"],
        "deletes": deletes,
        "checked": checked,
    }


def _chain_step_words(rest: tuple[str, ...]) -> StepWords | None:
    """The chain step an ``invariant`` command's words name, for its result's deed."""
    if rest[:2] == ("queue", "move") and "--if-queued-at" in rest and "--to" in rest:
        try:
            to = int(rest[rest.index("--to") + 1])
            at = int(rest[rest.index("--if-queued-at") + 1])
        except (IndexError, ValueError):
            return None
        return _CHAIN_STEPS[cv.MOVE_UP if to < at else cv.MOVE_DOWN]
    if rest[:2] == ("queue", "remove") and "--if-queued-at" in rest:
        return _CHAIN_STEPS[cv.REMOVE]
    if rest[:2] == ("oracle", "chain") and len(rest) > 2:
        return _CHAIN_STEPS.get(rest[2]) if rest[2] != cv.START_ITEM else None
    return None
