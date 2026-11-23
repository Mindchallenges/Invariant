"""Claude Code's side of the oracle page talking to a running session (ADR-029 D4).

The oracle names what it needs as ``SessionTalk`` (``oracle/talk``); this
module answers it for Claude Code, and the CLI joins the two
(``_oracle_talk``). It imports nothing from ``oracle/``: the values it returns
have the shape ``oracle/talk`` reads, and nothing more.

**Increments 1 and 2 read, and only read.** :meth:`ClaudeCodeTalk.conversation`
answers a live window's recent conversation from its transcript: the user's
prompts, Claude's text and, since increment 2, each of Claude's tool calls as
one line (:func:`tool_line`), oldest first. The page's last-message excerpt
and its Conversation screen are both taken from it (ADR-009 D17, ADR-029 D11,
D19). Typing, a pending prompt and the receipt are later increments.

**Fail closed, by named shapes** (ADR-029 ruling 11). The transcript is
another program's undocumented format, read backwards from its end through
``claude_code_telemetry``'s tail reader, so its open-tail, join and budget
rules hold here too. Unlike the telemetry reader, which skips what it cannot
parse, this one **stops** at the first entry whose shape it does not know and
says so (:class:`Unreadable`): a page that showed an older text as the newest
would show something wrong. What it skips is only what it knows is not the
conversation:

* a record whose ``type`` is not ``user`` or ``assistant``: Claude Code's
  bookkeeping (``last-prompt``, ``system``, ``attachment`` and the rest);
* a subagent's entry (``isSidechain``);
* a ``user`` entry Claude Code wrote itself (``isMeta``, a compact summary,
  an entry only in the transcript), one that hands back a tool result, and
  one that opens with one of Claude Code's own tags (:data:`HOST_TAGS`: a
  background task's notice, a local command's echo and output);
* what has no text and is no tool call: thinking, an image.

The shapes known are those seen on Claude Code 2.1 (2.1.291 when this was
written): content a string or a list of the blocks in
:data:`ASSISTANT_BLOCKS` and :data:`USER_BLOCKS`, and a tool call that names
its tool and gives its input as an object.

**Capped** (ADR-009 D17): at most :data:`READ_BUDGET_BYTES` read back, at most
:data:`CONVERSATION_BYTES` of text, each entry's text cut at
:data:`ENTRY_TEXT_CAP` characters and a tool call's line at
:data:`TOOL_LINE_CAP`. The read ends once it holds
:data:`CONVERSATION_ENTRIES` entries **and** Claude's newest text: past a run
of tool calls it reads on, within those caps, so the excerpt is still the
text Claude last wrote (ADR-029 D19); the oracle shows the newest
:data:`CONVERSATION_ENTRIES`.

**Held in memory only.** A read is kept against its transcript's size and
modification time, so an unchanged transcript is not read again, for at most
``keep`` transcripts. Nothing is written anywhere.

**Increment 3 types a message** (ADR-029 D3, D6, D25 to D27). The keystrokes
go through ``claude_code_launch``'s :class:`TerminalTyper`, the text an
``on run argv`` argument, never part of a script; a look at the tab goes
through its :class:`TerminalReader`. What is Claude Code's here:

* :func:`input_box`: Claude Code's input box in the tab's visible text, the
  lines from the rule above its prompt line (``>`` or ``❯``) to the rule
  under it, and whether that line is empty (its ``Try "…"`` hint counts as
  empty). A tab with no box this module knows is ``Unreadable`` (ruling 11);
  only the box is digested, so a status line that changes on its own is not
  taken for somebody typing;
* :func:`prompt_digest`: the receipt's hash, the SHA-256 of a prompt as
  ``UserPromptSubmit`` reports it, line endings made ``\n`` and the ends
  trimmed. The hook side records it (``claude_code``), the oracle compares it;
* :data:`MODE_PREFIXES`: the first characters that turn Claude Code's input
  into something other than a message (a slash command, the shell, memory,
  a background task). A message that opens with one is refused, so nothing
  typed from the phone reaches a shell (ADR-029 D12).

**Increment 4 stops a turn** (ADR-029 D34 to D36). :meth:`ClaudeCodeTalk.interrupt`
types the ``escape`` key through :class:`TerminalTyper`'s named keys, and
:meth:`ClaudeCodeTalk.receipt` is the evidence D3 asks for where Claude Code
fires no hook: the transcript's next entry of the person's after the key,
which for an interrupt is Claude Code's own marker
(:data:`INTERRUPT_MARKERS`, hashed as :data:`INTERRUPT_DIGEST`) and for
anything else typed in its place that prompt's digest.

**Slash commands** (ADR-029 ruling 15, D37 to D39). A message opening with
``/`` is typed like any other, unless it names a command Claude Code opens a
menu for when given no arguments (:data:`MENU_COMMANDS`, seen on
:data:`MENU_COMMANDS_SEEN_ON`), which the phone could not drive. A built-in
command fires no prompt hook, so its receipt is the transcript's: its echo
(:func:`command_text`), or the plain entry ``/compact`` writes at once; a
``/clear`` is found in the new session Claude Code starts in the same process
(its per-process record names it), and ``/exit`` is the process ending
(:data:`ENDED_DIGEST`). In the conversation a command reads as the person's
turn, ``/name args``, and its output is not shown.

**Increment 5 answers a question** (ADR-029 D8, D40 to D43). What is Claude
Code's here, every shape named with the version it was seen on
(:data:`QUESTION_SEEN_ON`):

* :func:`read_pending`: the transcript's open ``AskUserQuestion`` call, its
  questions and their choices (:func:`question_call`); a call of a shape this
  module does not know is ``Unreadable``;
* :func:`question_screen`: the question view Claude Code draws in the tab:
  the tab line, the question, its numbered choices, the "Type something" row
  ("Other"), a multi-select's boxes and Submit row, "Chat about this" and the
  footer, or the "Review your answers" screen after several questions;
* :meth:`ClaudeCodeTalk.answer`: the keys, measured in a pseudo-terminal on
  2.1.330. Terminal's ``do script`` ends every text with a Return, so each
  step is one script of arrow keys and, for "Other", its text, whose Return
  is the step's own: a choice, a toggle, the Submit row, the review's Submit
  answers. Digits sent together are not read as keys, a line feed is not a
  Return, and a Return on an empty "Type something" declines the question,
  so none is used. The tab is read before the first key and after every
  step, and a screen that did not move as the step meant stops the typing;
* :func:`answer_digest`: the receipt's hash of an answer, the same from the
  hook's ``PostToolUse`` payload (:func:`answer_receipt`), the transcript's
  tool result (:func:`answered_after`) and the answer the oracle typed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .claude_code_telemetry import (
    TAIL_BLOCK_BYTES,
    TAIL_BUDGET_BYTES,
    claude_home,
    parse_timestamp,
    scan_tail,
)

#: An entry's text is cut here, in characters (ADR-009 D17).
ENTRY_TEXT_CAP = 20_000

#: At most this many entries a read (ADR-009 D17).
CONVERSATION_ENTRIES = 40

#: At most this much text a read, in UTF-8 bytes (ADR-009 D17).
CONVERSATION_BYTES = 512 << 10

#: How far back a read walks: the tail reader's budget (ADR-029 D11).
READ_BUDGET_BYTES = TAIL_BUDGET_BYTES

#: A tool call's line is at most this long, in characters (ADR-029 D11).
TOOL_LINE_CAP = 160

#: The roles an entry has, as ``oracle/talk`` names them.
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"

#: The blocks that are a tool call of Claude's: Claude Code's own tools, and a
#: tool the API runs itself (a web search).
TOOL_BLOCKS = frozenset({"tool_use", "server_tool_use"})

#: Each tool's line (ADR-029 D20): its verb, and the input field it names,
#: with whether that field is a path. A tool not here, or one without the
#: field, is "Used <name>": never wrong, only less said.
TOOL_VERBS: dict[str, tuple[str, str, bool]] = {
    "Bash": ("Ran", "command", False),
    "Read": ("Read", "file_path", True),
    "Edit": ("Edited", "file_path", True),
    "MultiEdit": ("Edited", "file_path", True),
    "NotebookEdit": ("Edited", "notebook_path", True),
    "Write": ("Wrote", "file_path", True),
    "Glob": ("Searched for", "pattern", False),
    "Grep": ("Searched for", "pattern", False),
    "WebFetch": ("Fetched", "url", False),
    "WebSearch": ("Searched the web for", "query", False),
    "web_search": ("Searched the web for", "query", False),
    "Agent": ("Started an agent:", "description", False),
    "Task": ("Started an agent:", "description", False),
}

#: Tools whose line names no input.
TOOL_DEEDS = {"TodoWrite": "Updated the to-do list", "AskUserQuestion": "Asked you a question"}

_ELLIPSIS = "…"

#: The content blocks each role is known to carry. Only ``text`` is shown.
ASSISTANT_BLOCKS = frozenset(
    {"text", "thinking", "redacted_thinking", "tool_use", "server_tool_use",
     "web_search_tool_result"})
USER_BLOCKS = frozenset({"text", "tool_result", "image", "document"})

#: The flags on a ``user`` entry Claude Code wrote itself, not the person.
HOST_WRITTEN = ("isMeta", "isCompactSummary", "isVisibleInTranscriptOnly")

#: The tags Claude Code opens a ``user`` entry of its own with: a background
#: task's notice, a slash command's echo, a local command's output.
HOST_TAGS = frozenset(
    {"task-notification", "command-name", "command-message", "command-args",
     "local-command-stdout", "local-command-stderr", "local-command-caveat",
     "bash-input", "bash-stdout", "bash-stderr", "system-reminder",
     "user-prompt-submit-hook"})

_OPENING_TAG = re.compile(r"\s*<([a-z][a-z-]*)[\s>]")

#: A host session id that can name one transcript file and nothing else.
_HOST_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z-]{0,127}")

#: The input kind that is a key, not a text (``oracle/talk``'s name for it).
KIND_INTERRUPT = "interrupt"

#: How many reads a reader keeps.
KEEP = 64

#: The first characters that switch Claude Code 2.1's input into another
#: mode, and the code a message that opens with one is refused with: ``!``
#: the shell, ``#`` memory and ``&`` a background task. Leading blanks are not
#: counted. A ``/`` is a slash command, typed since ADR-029 increment 4 unless
#: it opens a menu (:data:`MENU_COMMANDS`).
MODE_PREFIXES = {"!": "input_mode", "#": "input_mode", "&": "input_mode"}

#: The built-in commands Claude Code opens a menu or a dialog for when typed
#: with no arguments, as seen on :data:`MENU_COMMANDS_SEEN_ON`. The menu hides
#: the input box, and the phone could not drive it, so such a command is
#: refused at the tap (ADR-029 ruling 15, the user's, 2026-11-20). A command
#: not listed is typed; one that opens a menu all the same reads "not
#: confirmed" and turns send off for the window (D29).
MENU_COMMANDS = frozenset({
    "add-dir", "config", "cost", "effort", "export", "help", "hooks", "mcp", "memory",
    "model", "permissions", "plugin", "privacy-settings", "release-notes", "resume",
    "rewind", "sandbox", "status", "tasks", "theme", "usage"})
MENU_COMMANDS_SEEN_ON = "2.1.291"

#: The code a menu command is refused with.
MENU_REFUSAL = "menu_command"

#: The command that ends the window, whose evidence is the process ending.
EXIT_COMMAND = "/exit"

#: A command's echo in the transcript: its name, with its slash, and its
#: arguments, in either order Claude Code writes them (a built-in's or a
#: custom command's).
_COMMAND_NAME = re.compile(r"<command-name>(/[^\s<]+)</command-name>")
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_ECHO_OPENS = ("command-name", "command-message")

#: The text Claude Code 2.1 writes as the person's entry when a turn is
#: interrupted: while Claude writes, and while a tool runs or waits on its
#: permission prompt (seen on 2.1.291). The receipt's evidence of an interrupt.
INTERRUPT_MARKERS = frozenset(
    {"[Request interrupted by user]", "[Request interrupted by user for tool use]"})

#: The receipt's hash of an interrupt: of no text the person typed, so it is
#: never a prompt's digest.
INTERRUPT_DIGEST = hashlib.sha256(b"\x00interrupt").hexdigest()

#: The receipt's hash of a window whose process has ended: ``/exit``'s
#: evidence, since Claude Code writes nothing for it (seen on 2.1.291).
ENDED_DIGEST = hashlib.sha256(b"\x00ended").hexdigest()

#: How far back the receipt looks: the newest part of the transcript only,
#: since what it looks for was written in the last few seconds.
RECEIPT_BUDGET_BYTES = 256 << 10

#: A rule Claude Code draws above and under its input line: a run of box
#: lines, with the corners of the older boxed input.
_RULE = re.compile(r"\s*[╭╰]?[─━]{8,}[╮╯]?\s*")
#: The input line: the prompt sign, then what is typed, inside the older box's
#: sides when it has them.
_PROMPT = re.compile(r"\s*│?\s*[>❯](?: (?P<typed>.*?))?\s*│?\s*")
#: The hint Claude Code shows in an empty input line.
_HINT = re.compile(r'Try "[^"\n]*"')
#: How far up from the bottom of the tab the input box is looked for.
BOX_LINES_UP = 30

# -- a question (ADR-029 increment 5) -------------------------------------------------

#: The tool Claude Code asks the person a question with, and the version its
#: call, its question view and its keys were seen on.
QUESTION_TOOL = "AskUserQuestion"
QUESTION_SEEN_ON = "2.1.330"

#: The input kind an answer is (``oracle/talk``'s name for it).
KIND_QUESTION = "question"

#: The most questions in one call, and choices in one question, read: more
#: than Claude Code 2.1 asks, fewer than a screen could not show.
MAX_QUESTIONS = 8
MAX_CHOICES = 16

#: The keys a question and a choice of the known call carry.
_QUESTION_KEYS = frozenset({"question", "header", "options", "multiSelect"})
_CHOICE_KEYS = frozenset({"label", "description"})

#: The question view, as Claude Code 2.1.330 draws it (seen with the classic and
#: the fullscreen renderer alike): a numbered row, the highlight sign ``❯`` in
#: its first column; a multi-select's box; the unnumbered Submit row; the rows
#: under the lower rule; the footer; the review.
_ROW = re.compile(r"(?P<sel>[❯ ]) (?P<n>\d+)\. (?P<rest>.*)")
_BOX = re.compile(r"\[(?P<mark>[ ✔])\] (?P<label>.*)")
_SUBMIT_ROW = re.compile(r"(?P<sel>[❯ ])\s+Submit")
_CHAT = "Chat about this"
_FOOTER = "Enter to select · "
_REVIEW = "Review your answers"
_TAB = re.compile(r"([☐☒]) ")
_OTHER_SINGLE = "Type something."
_OTHER_MULTI = "Type something"
#: How close to the bottom of the tab the view's last line must be: the view
#: is what Claude Code draws last, so anything under it is something else.
VIEW_TAIL_LINES = 3

#: How long the tab is given to show a step's effect, and how often it is read
#: meanwhile, in seconds; an "Other" text whose Return was not taken is given
#: a lone Return after :data:`STEP_SETTLE`.
STEP_WAIT = 3.0
STEP_POLL = 0.25
STEP_SETTLE = 1.0


@dataclass(frozen=True)
class Look:
    """One look at a tab (ADR-029 D6): a digest of its input box and whether
    the input line is empty, or why it cannot be typed into. Since increment 5
    it says what it ``shows``: the input box, or a question, whose ``empty``
    is that nothing on it is chosen yet (D41)."""

    digest: str | None = None
    empty: bool | None = None
    refused: str | None = None
    shows: str = "input"


def input_box(contents: str) -> tuple[str, bool] | None:
    """Claude Code's input box in a tab's visible text: its lines, and whether
    the input line is empty; ``None`` when no box this module knows is there."""
    lines = contents.splitlines()
    low = max(0, len(lines) - BOX_LINES_UP)
    for at in range(len(lines) - 1, low, -1):
        prompt = _PROMPT.fullmatch(lines[at])
        if prompt is None or not _RULE.fullmatch(lines[at - 1]):
            continue
        end = next((n for n in range(at + 1, len(lines)) if _RULE.fullmatch(lines[n])), None)
        if end is None:
            return None
        typed = (prompt.group("typed") or "").strip()
        empty = end == at + 1 and (not typed or _HINT.fullmatch(typed) is not None)
        return "\n".join(lines[at - 1:end + 1]), empty
    return None


def normalise_prompt(text: str) -> str:
    """A prompt as ``UserPromptSubmit`` reports it, for the receipt (ADR-029 D3)."""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def prompt_digest(text: str) -> str:
    """The receipt's hash of a prompt: SHA-256 of its normalised UTF-8."""
    return hashlib.sha256(normalise_prompt(text).encode("utf-8")).hexdigest()


def mode_refusal(text: str) -> str | None:
    """The code a message is refused with when it would switch the input's
    mode, or is a command that opens a menu the phone cannot drive."""
    stripped = text.strip()
    if stripped.startswith("/"):
        words = stripped[1:].split(None, 1)
        menu = len(words) == 1 and words[0] in MENU_COMMANDS
        return MENU_REFUSAL if menu else None
    return MODE_PREFIXES.get(text.lstrip()[:1])


def process_alive(pid: int) -> bool:
    """Whether a process of this id runs: one the system cannot find has ended."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def command_text(record: Any) -> str | None:
    """A slash command's echo as the command typed, ``/name args``; ``None``
    for any record that is no echo. Claude Code writes the echo as the
    person's entry, or for a command that opened a menu as a ``system``
    ``local_command`` record (seen on 2.1.291)."""
    if not isinstance(record, dict):
        return None
    if record.get("type") == ROLE_USER:
        if record.get("isSidechain") is True or any(record.get(f) is True
                                                   for f in HOST_WRITTEN):
            return None
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
    elif record.get("type") == "system" and record.get("subtype") == "local_command":
        content = record.get("content")
    else:
        return None
    if not isinstance(content, str):
        return None
    tag = _OPENING_TAG.match(content)
    name = _COMMAND_NAME.search(content)
    if tag is None or tag.group(1) not in _ECHO_OPENS or name is None:
        return None
    args = _COMMAND_ARGS.search(content)
    return normalise_prompt(f"{name.group(1)} {args.group(1) if args else ''}")


@dataclass(frozen=True)
class Receipt:
    """The evidence an input left (ADR-029 D3): the digest of the first entry
    of the person's after it, and when that entry was written."""

    hash: str
    at: float


def person_digest(record: Any) -> str | None:
    """The digest of a transcript record when it is the person's entry: an
    interrupt's marker (:data:`INTERRUPT_DIGEST`) or a prompt's digest;
    ``None`` for anything else (Claude's entries, a tool's result, a
    subagent's, Claude Code's own notes and echoes, bookkeeping)."""
    command = command_text(record)
    if command is not None:
        return prompt_digest(command)
    if not isinstance(record, dict) or record.get("type") != ROLE_USER:
        return None
    if record.get("isSidechain") is True or any(record.get(f) is True for f in HOST_WRITTEN):
        return None
    content = (record.get("message") or {}).get("content") if isinstance(
        record.get("message"), dict) else None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        blocks = [b for b in content if isinstance(b, dict)]
        if any(b.get("type") == "tool_result" for b in blocks):
            return None
        text = "\n\n".join(b["text"] for b in blocks
                            if b.get("type") == "text" and isinstance(b.get("text"), str))
    else:
        return None
    if text.strip() in INTERRUPT_MARKERS:
        return INTERRUPT_DIGEST
    tag = _OPENING_TAG.match(text)
    if not text.strip() or (tag is not None and tag.group(1) in HOST_TAGS):
        return None
    return prompt_digest(text)


def first_after(path: Path, since: float, budget: int | None = None) -> Receipt | None:
    """The oldest entry of the person's written at or after ``since``, read
    back from the end of the transcript at ``path`` within ``budget`` bytes.

    The read stops at the first record older than ``since``. A line it cannot
    read ends the search with no evidence: the oracle then says "not
    confirmed", never "delivered" on a guess (ADR-029 ruling 11).
    """
    found: list[Receipt] = []
    unreadable = [False]

    def take(raw: bytes) -> bool | None:
        try:
            record = json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, RecursionError):
            unreadable[0] = True
            return True
        if not isinstance(record, dict):
            unreadable[0] = True
            return True
        at = parse_timestamp(record.get("timestamp"))
        if at is None:
            return None
        if at < since:
            return True
        digest = person_digest(record)
        if digest is not None:
            found.append(Receipt(digest, at))
        return None

    try:
        with Path(path).open("rb") as handle:
            scan_tail(handle, take, TAIL_BLOCK_BYTES,
                      RECEIPT_BUDGET_BYTES if budget is None else budget)
    except OSError:
        return None
    if unreadable[0] or not found:
        return None
    return min(found, key=lambda r: r.at)


@dataclass(frozen=True)
class Entry:
    """One turn with text: who said it, what, when, and whether it was cut."""

    role: str
    text: str
    at: float
    cut: bool
    #: A slash command's echo (:func:`command_text`), which a plain entry of
    #: the same command just before it stands for.
    echo: bool = False


@dataclass(frozen=True)
class Unreadable:
    """Why a read stopped: the shape it did not know, and the Claude Code
    version the entry names, where it names one."""

    reason: str
    version: str | None


@dataclass(frozen=True)
class Conversation:
    """The entries read, oldest first, and why the read stopped short, if it did.

    The entries are those newer than the entry it could not read.
    """

    entries: tuple[Entry, ...]
    unreadable: Unreadable | None = None


EMPTY = Conversation(())


def _unknown(role: str, what: str, version: str | None) -> Unreadable:
    return Unreadable(f"an {role} entry {what}" if role == ROLE_ASSISTANT
                      else f"a {role} entry {what}", version)


def _cut_end(text: str, room: int) -> str:
    return text if len(text) <= room else text[: max(room - 1, 0)] + _ELLIPSIS


def _cut_path(path: str, room: int) -> str:
    """The path in ``room`` characters, keeping its end: the file name says the most."""
    if len(path) <= room:
        return path
    name = path.rsplit("/", 1)[-1]
    if len(name) + 2 > room:
        return _cut_end(path, room)
    head = path[: room - len(name) - 2]
    return f"{head}{_ELLIPSIS}/{name}"


def _relative(path: str, cwd: Any) -> str:
    if isinstance(cwd, str) and cwd and path.startswith(cwd.rstrip("/") + "/"):
        return path[len(cwd.rstrip("/")) + 1:]
    return path


def tool_line(name: str, given: dict[str, Any], cwd: Any = None) -> str:
    """A tool call as one line of at most :data:`TOOL_LINE_CAP` characters.

    A path is shown from the entry's own folder when it lies under it, and cut
    in its middle; anything else is cut at its end. Only the first line of an
    argument is shown, and never a line break.
    """
    if name in TOOL_DEEDS:
        return TOOL_DEEDS[name]
    verb, field, is_path = TOOL_VERBS.get(name, ("", "", False))
    value = given.get(field) if verb else None
    if not isinstance(value, str) or not value.strip():
        return _cut_end(f"Used {name}", TOOL_LINE_CAP)
    lines = value.strip().splitlines()
    first = " ".join(lines[0].split())
    if len(lines) > 1:
        first += f" {_ELLIPSIS}"
    room = TOOL_LINE_CAP - len(verb) - 1
    shown = _cut_path(_relative(first, cwd), room) if is_path else _cut_end(first, room)
    return f"{verb} {shown}"


def _blocks(role: str, content: Any, version: str | None,
            cwd: Any) -> list[tuple[str, str]] | None | Unreadable:
    """The entry's shown parts in order: ``(role, text)``, a tool call as a
    ``tool`` part; ``None`` for an entry that is not shown at all."""
    if isinstance(content, str):
        return [(role, content)]
    if not isinstance(content, list):
        return _unknown(role, "whose content is neither text nor blocks", version)
    known = ASSISTANT_BLOCKS if role == ROLE_ASSISTANT else USER_BLOCKS
    parts: list[tuple[str, str]] = []
    texts: list[str] = []
    result = False
    for block in content:
        kind = block.get("type") if isinstance(block, dict) else None
        if not isinstance(kind, str) or kind not in known:
            name = kind if isinstance(kind, str) else "?"
            return _unknown(role, f"with an unknown block '{name}'", version)
        if kind == "text":
            if not isinstance(block.get("text"), str):
                return _unknown(role, "with a text block that is not text", version)
            texts.append(block["text"])
        elif kind in TOOL_BLOCKS:
            tool, given = block.get("name"), block.get("input", {})
            if not isinstance(tool, str) or not tool:
                return _unknown(role, "with a tool call that names no tool", version)
            if not isinstance(given, dict):
                return _unknown(role, "with a tool call whose input is not an object", version)
            if texts:
                parts.append((role, "\n\n".join(texts)))
                texts = []
            parts.append((ROLE_TOOL, tool_line(tool, given, cwd)))
        result = result or kind == "tool_result"
    if result:
        return None
    if texts:
        parts.append((role, "\n\n".join(texts)))
    return parts


def entries_from(raw: bytes, cap: int = ENTRY_TEXT_CAP) -> list[Entry] | Unreadable:
    """One complete transcript line: its shown entries in order (none for a
    line that is not part of the conversation), or why it cannot be read."""
    try:
        record = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError):
        return Unreadable("an entry that is not JSON", None)
    if not isinstance(record, dict):
        return Unreadable("an entry that is not an object", None)
    command = command_text(record)
    if command is not None:
        at = parse_timestamp(record.get("timestamp"))
        return [] if at is None else [Entry(ROLE_USER, command[:cap], at,
                                            len(command) > cap, echo=True)]
    role = record.get("type")
    if role not in (ROLE_USER, ROLE_ASSISTANT) or record.get("isSidechain") is True:
        return []
    if role == ROLE_USER and any(record.get(flag) is True for flag in HOST_WRITTEN):
        return []
    version = record.get("version") if isinstance(record.get("version"), str) else None
    message = record.get("message")
    if not isinstance(message, dict):
        return _unknown(role, "with no message", version)
    parts = _blocks(role, message.get("content"), version, record.get("cwd"))
    if parts is None or isinstance(parts, Unreadable):
        return parts if parts is not None else []
    parts = [(who, text) for who, text in parts if text.strip()]
    if role == ROLE_USER and parts:
        tag = _OPENING_TAG.match(parts[0][1])
        if tag is not None and tag.group(1) in HOST_TAGS:
            return []
    if not parts:
        return []
    at = parse_timestamp(record.get("timestamp"))
    if at is None:
        return Unreadable("an entry with no time", version)
    return [Entry(role=who, text=text[:cap], at=at, cut=len(text) > cap)
            for who, text in parts]


def read_conversation(
    path: Path,
    *,
    budget: int = READ_BUDGET_BYTES,
    entries: int = CONVERSATION_ENTRIES,
    text_bytes: int = CONVERSATION_BYTES,
    cap: int = ENTRY_TEXT_CAP,
) -> Conversation:
    """The newest entries of the transcript at ``path``, read from its end.

    A file that cannot be opened is an empty conversation. The read stops at
    the first line it cannot read, before the entry that would pass
    ``text_bytes``, at ``budget`` bytes back, or once it holds ``entries``
    entries and one of them is Claude's text (ADR-029 D19).
    """
    found: list[Entry] = []
    held = [0]
    claude = [False]
    stopped: list[Unreadable] = []

    def take(raw: bytes) -> bool | None:
        read = entries_from(raw, cap)
        if isinstance(read, Unreadable):
            stopped.append(read)
            return True
        for entry in reversed(read):
            size = len(entry.text.encode("utf-8"))
            newer = found[-1] if found else None
            if (newer is not None and newer.echo and not entry.echo
                    and entry.role == ROLE_USER and entry.text == newer.text):
                found[-1] = entry
                continue
            if held[0] + size > text_bytes:
                return True
            found.append(entry)
            held[0] += size
            claude[0] = claude[0] or entry.role == ROLE_ASSISTANT
        return True if len(found) >= entries and claude[0] else None

    try:
        with Path(path).open("rb") as handle:
            scan_tail(handle, take, TAIL_BLOCK_BYTES, budget)
    except OSError:
        return EMPTY
    return Conversation(tuple(reversed(found)), stopped[0] if stopped else None)


# -- a question: the open call, the view in the tab, the receipt (ADR-029 increment 5) --


@dataclass(frozen=True)
class Choice:
    """One choice a question offers: its label and its description."""

    label: str
    description: str


@dataclass(frozen=True)
class Question:
    """One question of a call: its text, its tab's header, whether it takes
    several choices, and its choices in order."""

    text: str
    header: str
    multi: bool
    choices: tuple[Choice, ...]


@dataclass(frozen=True)
class Pending:
    """The question a window waits on: the call's id, its questions, and that
    Claude Code offers "Other" (its "Type something" row) on each."""

    id: str
    questions: tuple[Question, ...]
    version: str | None = None
    kind: str = KIND_QUESTION
    other: bool = True


def _words(text: str) -> str:
    return " ".join(text.split())


def question_call(block: Any, version: str | None = None) -> Pending | Unreadable:
    """An ``AskUserQuestion`` call's questions, or why its shape is not known.

    The known shape (seen on :data:`QUESTION_SEEN_ON`): ``questions``, one to
    :data:`MAX_QUESTIONS`, each with a ``question``, a ``header``, a boolean
    ``multiSelect`` and two to :data:`MAX_CHOICES` ``options``, each a
    ``label`` and a ``description``. Any other key on a question or a choice,
    a repeated question or label, or a value of another type is a shape this
    module does not know: drawn differently, it could be answered wrongly.
    """
    def unknown(what: str) -> Unreadable:
        return Unreadable(f"a question call {what}", version)

    call = block.get("id") if isinstance(block, dict) else None
    given = block.get("input") if isinstance(block, dict) else None
    if not isinstance(call, str) or not call or not isinstance(given, dict):
        return unknown("with no id or input")
    asked = given.get("questions")
    if not isinstance(asked, list) or not 1 <= len(asked) <= MAX_QUESTIONS:
        return unknown("with no questions this build reads")
    questions: list[Question] = []
    for item in asked:
        if not isinstance(item, dict) or set(item) - _QUESTION_KEYS:
            return unknown("with a question of another shape")
        text, header = item.get("question"), item.get("header", "")
        multi, options = item.get("multiSelect", False), item.get("options")
        if (not isinstance(text, str) or not text.strip() or not isinstance(header, str)
                or not isinstance(multi, bool) or not isinstance(options, list)
                or not 2 <= len(options) <= MAX_CHOICES):
            return unknown("with a question of another shape")
        choices: list[Choice] = []
        for option in options:
            if not isinstance(option, dict) or set(option) - _CHOICE_KEYS:
                return unknown("with a choice of another shape")
            label, description = option.get("label"), option.get("description", "")
            if (not isinstance(label, str) or not label.strip()
                    or not isinstance(description, str)):
                return unknown("with a choice of another shape")
            choices.append(Choice(label, description))
        if len({_words(c.label) for c in choices}) != len(choices):
            return unknown("with a choice named twice")
        questions.append(Question(text, header, multi, tuple(choices)))
    if len({_words(q.text) for q in questions}) != len(questions):
        return unknown("with a question asked twice")
    return Pending(call, tuple(questions), version)


def read_pending(path: Path, budget: int = READ_BUDGET_BYTES) -> Pending | Unreadable | None:
    """The question the transcript at ``path`` waits on: its newest
    ``AskUserQuestion`` call of the window's own (no subagent's) that no tool
    result has answered; ``None`` when there is none.

    Read back from the end, the open last line left out. The newest call
    decides: answered, nothing waits. A person's prompt newer than any call
    ends the search, as a question waits only at the end of a turn. A line it
    cannot read is ``Unreadable``, never a guess (ruling 11).
    """
    results: set[str] = set()
    found: list[Pending | Unreadable | None] = []

    def take(raw: bytes) -> bool | None:
        try:
            record = json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, RecursionError):
            found.append(Unreadable("an entry that is not JSON", None))
            return True
        if not isinstance(record, dict):
            found.append(Unreadable("an entry that is not an object", None))
            return True
        if record.get("isSidechain") is True:
            return None
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if record.get("type") == ROLE_USER:
            blocks = content if isinstance(content, list) else []
            ids = [b.get("tool_use_id") for b in blocks
                   if isinstance(b, dict) and b.get("type") == "tool_result"]
            results.update(i for i in ids if isinstance(i, str))
            if not ids and person_digest(record) is not None:
                found.append(None)
                return True
            return None
        if record.get("type") != ROLE_ASSISTANT or not isinstance(content, list):
            return None
        version = record.get("version") if isinstance(record.get("version"), str) else None
        for block in reversed(content):
            if (isinstance(block, dict) and block.get("type") == "tool_use"
                    and block.get("name") == QUESTION_TOOL):
                answered = block.get("id") in results
                found.append(None if answered else question_call(block, version))
                return True
        return None

    try:
        with Path(path).open("rb") as handle:
            scan_tail(handle, take, TAIL_BLOCK_BYTES, budget)
    except OSError:
        return None
    return found[0] if found else None


@dataclass(frozen=True)
class QuestionScreen:
    """The question view in a tab, as read: the question shown, its choices'
    labels in order, a multi-select's boxes, the "Other" row's text (``None``
    for its placeholder) and box, which row is highlighted (the choices, then
    "Other", then a multi-select's Submit, then "Chat about this"), whether
    the view has tabs (a Submit tab), and which question tabs are answered.
    ``review`` is the "Review your answers" screen, where ``at`` is 0 on
    Submit answers and 1 on Cancel."""

    text: str
    rows: tuple[str, ...]
    multi: bool
    checked: tuple[bool, ...]
    other: str | None
    other_checked: bool
    at: int
    tabbed: bool
    answered: tuple[bool, ...]
    review: bool = False

    @property
    def untouched(self) -> bool:
        """Nothing chosen anywhere yet: no tab answered, no box ticked, no
        "Other" text, not the review."""
        return (not self.review and not any(self.answered) and not any(self.checked)
                and self.other is None and not self.other_checked)

    def shows(self, question: Any) -> bool:
        """Whether this is ``question``'s view: its words, its choices' labels
        in order, and its kind."""
        return (not self.review and self.text == _words(question.text)
                and self.multi == bool(question.multi)
                and self.rows == tuple(_words(c.label) for c in question.choices))


def _tail_lines(contents: str) -> list[str]:
    lines = [line.rstrip() for line in contents.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _header_at(lines: list[str], top: int, bottom: int) -> int | None:
    """The tab line between ``top`` and ``bottom``: the last line marked with a
    tab box that sits right under a rule."""
    for at in range(bottom - 1, top, -1):
        if _TAB.search(lines[at]) and _RULE.fullmatch(lines[at - 1]):
            return at
    return None


def question_screen(contents: str) -> QuestionScreen | None:
    """The question view at the bottom of a tab's visible text, or ``None``
    when the tab shows no view this module knows (D40)."""
    lines = _tail_lines(contents)
    if not lines:
        return None
    low = max(1, len(lines) - 2 * BOX_LINES_UP)
    footer = next((n for n in range(len(lines) - 1, max(low, len(lines) - VIEW_TAIL_LINES) - 1,
                                    -1) if lines[n].lstrip().startswith(_FOOTER)), None)
    if footer is None:
        return _review_screen(lines, low)
    chat = _ROW.fullmatch(lines[footer - 1]) if footer >= 2 else None
    if chat is None or chat.group("rest") != _CHAT or not _RULE.fullmatch(lines[footer - 2]):
        return None
    rule = footer - 2
    header = _header_at(lines, low, rule)
    if header is None:
        return None
    marks = _TAB.findall(lines[header])
    tabbed = "✔ Submit" in lines[header]
    body = lines[header + 1:rule]
    first = next((n for n, line in enumerate(body) if (m := _ROW.fullmatch(line))
                  and m.group("n") == "1"), None)
    if not first:
        return None
    text = _words(" ".join(body[:first]))
    rows: list[tuple[str, str]] = []
    submit: str | None = None
    for line in body[first:]:
        row = _ROW.fullmatch(line)
        if row is not None:
            if int(row.group("n")) != len(rows) + 1 or submit is not None:
                return None
            rows.append((row.group("sel"), row.group("rest")))
            continue
        found = _SUBMIT_ROW.fullmatch(line)
        if found is not None:
            submit = found.group("sel")
        elif not line.startswith("     "):
            return None
    if len(rows) < 3 or int(chat.group("n")) != len(rows) + 1:
        return None
    multi = submit is not None
    labels: list[str] = []
    checked: list[bool] = []
    for _, rest in rows:
        if multi:
            box = _BOX.fullmatch(rest)
            if box is None:
                return None
            labels.append(_words(box.group("label")))
            checked.append(box.group("mark") == "✔")
        else:
            labels.append(_words(rest))
            checked.append(False)
    other = labels.pop()
    other_checked = checked.pop()
    placeholder = _OTHER_MULTI if multi else _OTHER_SINGLE
    signs = [sel for sel, _ in rows] + ([submit] if multi else []) + [chat.group("sel")]
    if signs.count("❯") != 1:
        return None
    return QuestionScreen(text, tuple(labels), multi, tuple(checked),
                          None if other == placeholder else other, other_checked,
                          signs.index("❯"), tabbed, tuple(m == "☒" for m in marks))


def _review_screen(lines: list[str], low: int) -> QuestionScreen | None:
    """The "Review your answers" screen that ends several questions: its two
    rows at the bottom of the tab, under the tab line."""
    if len(lines) < 4:
        return None
    rows = [_ROW.fullmatch(line) for line in lines[-2:]]
    if (any(r is None for r in rows) or [r.group("rest") for r in rows]  # type: ignore[union-attr]
            != ["Submit answers", "Cancel"]):
        return None
    title = next((n for n in range(len(lines) - 3, low - 1, -1)
                  if lines[n].strip() == _REVIEW), None)
    if title is None or title < 2 or not _TAB.search(lines[title - 1]) \
            or not _RULE.fullmatch(lines[title - 2]):
        return None
    signs = [r.group("sel") for r in rows]  # type: ignore[union-attr]
    if signs.count("❯") != 1:
        return None
    marks = _TAB.findall(lines[title - 1])
    return QuestionScreen("", (), False, (), None, False, signs.index("❯"), True,
                          tuple(m == "☒" for m in marks), review=True)


def _answer_text(question: Question, pick: Any) -> str:
    """One question's answer as Claude Code reports it: the label, or the
    "Other" text; a multi-select's labels and "Other" joined by ", "."""
    parts = [question.choices[c].label for c in sorted(pick.choices)]
    if pick.other is not None:
        parts.append(pick.other.strip())
    return ", ".join(parts)


def answer_digest(call: str, questions: Any, answers: Any) -> str:
    """The receipt's hash of an answer (ADR-029 D43): SHA-256 of the call's id
    and, per question in order, its text and the parts of its answer, split
    at ", ", trimmed and sorted, so the order a multi-select was ticked in
    does not count. Neither the text nor a label is kept, only the hash."""
    shown = answers if isinstance(answers, dict) else {}
    parts = []
    for text in questions:
        given = shown.get(text, "")
        given = given if isinstance(given, str) else ""
        parts.append([text, sorted(p.strip() for p in given.split(", ") if p.strip())])
    canonical = json.dumps([call, parts], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _result_digest(call: Any, result: Any) -> str | None:
    """The digest of an answered question from its call's id and Claude Code's
    result (``questions`` and ``answers``), or ``None`` for another shape."""
    if not isinstance(call, str) or not call or not isinstance(result, dict):
        return None
    asked, answers = result.get("questions"), result.get("answers")
    if not isinstance(asked, list) or not asked or not isinstance(answers, dict):
        return None
    texts = [q.get("question") if isinstance(q, dict) else None for q in asked]
    if any(not isinstance(t, str) for t in texts):
        return None
    if any(not isinstance(a, str) for a in answers.values()):
        return None
    return answer_digest(call, texts, answers)


def answer_receipt(payload: Any) -> str | None:
    """The receipt's hash of an answered question from the ``PostToolUse``
    payload of its ``AskUserQuestion`` (ADR-029 D43): the call's id, its
    questions and Claude Code's ``answers``, from the response or else the
    input; ``None`` for any other payload, a declined question included."""
    if not isinstance(payload, dict) or payload.get("tool_name") != QUESTION_TOOL:
        return None
    given = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    response = payload.get("tool_response")
    answers = response.get("answers") if isinstance(response, dict) else None
    if answers is None:
        answers = given.get("answers")
    return _result_digest(payload.get("tool_use_id"),
                          {"questions": given.get("questions"), "answers": answers})


def answered_after(path: Path, since: float, budget: int | None = None) -> Receipt | None:
    """The oldest answered question written at or after ``since`` in the
    transcript at ``path``: the tool result Claude Code writes for it, with
    the answers (``toolUseResult``), hashed as :func:`answer_digest`. A line
    it cannot read ends the search with no evidence."""
    found: list[Receipt] = []
    unreadable = [False]

    def take(raw: bytes) -> bool | None:
        try:
            record = json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, RecursionError):
            unreadable[0] = True
            return True
        if not isinstance(record, dict):
            unreadable[0] = True
            return True
        at = parse_timestamp(record.get("timestamp"))
        if at is None:
            return None
        if at < since:
            return True
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if record.get("type") != ROLE_USER or not isinstance(content, list):
            return None
        call = next((b.get("tool_use_id") for b in content
                     if isinstance(b, dict) and b.get("type") == "tool_result"), None)
        digest = _result_digest(call, record.get("toolUseResult"))
        if digest is not None:
            found.append(Receipt(digest, at))
        return None

    try:
        with Path(path).open("rb") as handle:
            scan_tail(handle, take, TAIL_BLOCK_BYTES,
                      RECEIPT_BUDGET_BYTES if budget is None else budget)
    except OSError:
        return None
    if unreadable[0] or not found:
        return None
    return min(found, key=lambda r: r.at)


def _moves(at: int, to: int) -> list[str]:
    return ["down"] * (to - at) if to >= at else ["up"] * (at - to)


class _Stopped(Exception):
    """The tab did not follow a step of the answer: stop typing."""


#: What :meth:`ClaudeCodeTalk._until` answers for a tab that shows no view.
_GONE = object()


def _fits(question: Any, pick: Any) -> bool:
    """Whether ``pick`` can be typed into ``question``: choices it offers,
    one answer for a single-select, at least one for a multi-select, and an
    "Other" that is one line of text."""
    choices = getattr(pick, "choices", None)
    other = getattr(pick, "other", None)
    if not isinstance(choices, tuple) or any(
            not isinstance(c, int) or isinstance(c, bool) or not 0 <= c < len(question.choices)
            for c in choices) or len(set(choices)) != len(choices):
        return False
    if other is not None and (not isinstance(other, str) or not other.strip()
                              or any(ch in other for ch in "\r\n\x1b")):
        return False
    count = len(choices) + (other is not None)
    return count >= 1 and (question.multi or count == 1)


class ClaudeCodeTalk:
    """``SessionTalk`` for Claude Code: the conversation, and since increment 3
    a look at the tab, the typing, the receipt's digest and the refusal of a
    message that would switch the input's mode.

    A window is named by its host session id, which is its transcript's file
    name under one of the Claude home's project directories. The directory is
    not taken from the project the oracle filed the window under: a window
    rooted in another repository (BUG_040) still has its own transcript. A
    window is typed into and looked at by its tty. ``typer`` and ``reader``
    are ``claude_code_launch``'s, made on first use.
    """

    def __init__(self, home: Path | None = None, keep: int = KEEP,
                 typer: Any = None, reader: Any = None, alive: Any = None,
                 wait: Any = None) -> None:
        self._home = Path(home) if home is not None else None
        self._alive = alive if alive is not None else process_alive
        self._wait = wait if wait is not None else time.sleep
        self._last_contents = ""
        self._keep = keep
        self._typer = typer
        self._reader = reader
        self._paths: dict[str, Path] = {}
        self._kept: dict[Path, tuple[tuple[int, int], Conversation]] = {}
        self._lock = threading.Lock()

    @property
    def home(self) -> Path:
        return self._home if self._home is not None else claude_home()

    def conversation(self, host: Any) -> Conversation:
        """The window's recent conversation, or :data:`EMPTY` with no transcript."""
        host_id = getattr(host, "host_id", None)
        if not isinstance(host_id, str) or not _HOST_ID.fullmatch(host_id):
            return EMPTY
        with self._lock:
            path = self._transcript(host_id)
            if path is None:
                return EMPTY
            try:
                stat = os.stat(path)
            except OSError:
                return EMPTY
            key = (stat.st_size, stat.st_mtime_ns)
            kept = self._kept.get(path)
            if kept is not None and kept[0] == key:
                return kept[1]
            read = read_conversation(path)
            self._kept.pop(path, None)
            self._kept[path] = (key, read)
            while len(self._kept) > self._keep:
                del self._kept[next(iter(self._kept))]
            return read

    def _transcript(self, host_id: str) -> Path | None:
        known = self._paths.get(host_id)
        if known is not None and os.path.isfile(known):
            return known
        projects = self.home / "projects"
        try:
            names = sorted(os.listdir(projects))
        except OSError:
            return None
        for name in names:
            candidate = projects / name / f"{host_id}.jsonl"
            if os.path.isfile(candidate):
                if len(self._paths) >= self._keep:
                    self._paths.clear()
                self._paths[host_id] = candidate
                return candidate
        return None

    # -- sending (ADR-029 increment 3) ----------------------------------------------

    def quiet(self, host: Any) -> Look | Unreadable:
        """One look at the window's tab: its input box's digest and whether its
        input line is empty; why it cannot be typed into; or ``Unreadable``."""
        tty = getattr(host, "tty", None)
        if not tty:
            return Look(refused="no_tab")
        if self._reader is None:
            from .claude_code_launch import TerminalReader

            self._reader = TerminalReader()
        code, text = self._reader.read(tty)
        if code in ("no_tab", "not_running"):
            return Look(refused="no_tab")
        if code in ("no_claude", "not_allowed"):
            return Look(refused=code)
        if code != "contents":
            return Unreadable(f"the tab could not be read ({code})", None)
        view = question_screen(text)
        if view is not None:
            shown = "\n".join(_tail_lines(text)[-2 * BOX_LINES_UP:])
            return Look(hashlib.sha256(shown.encode("utf-8")).hexdigest(), view.untouched,
                        shows=KIND_QUESTION)
        box = input_box(text)
        if box is None:
            return Unreadable("the tab shows no input box this build knows", None)
        lines, empty = box
        return Look(hashlib.sha256(lines.encode("utf-8")).hexdigest(), empty)

    def deliver(self, host: Any, text: str) -> tuple[str, str]:
        """Type ``text`` into the window's tab, then a lone Return (ADR-027 D28's
        typing): ``typed``, or the typer's code."""
        tty = getattr(host, "tty", None)
        if not tty:
            return "no_tab", ""
        if self._typer is None:
            from .claude_code_launch import TerminalTyper

            self._typer = TerminalTyper()
        return self._typer.type(tty, text)

    def digest(self, kind: str, text: str) -> str:
        if kind == KIND_INTERRUPT:
            return INTERRUPT_DIGEST
        return ENDED_DIGEST if normalise_prompt(text) == EXIT_COMMAND else prompt_digest(text)

    def refuses(self, kind: str, text: str) -> str | None:
        return None if kind == KIND_INTERRUPT else mode_refusal(text)

    # -- answering a question (ADR-029 increment 5) ------------------------------------

    def pending(self, host: Any) -> Pending | Unreadable | None:
        """The question the window waits on (:func:`read_pending`), read fresh
        each time from its transcript; ``None`` when it has none."""
        host_id = getattr(host, "host_id", None)
        if not isinstance(host_id, str) or not _HOST_ID.fullmatch(host_id):
            return None
        with self._lock:
            path = self._transcript(host_id)
        return read_pending(path) if path is not None else None

    def answer_digest(self, pending: Any, picks: Any) -> str:
        """The hash the answered tool's receipt will carry for ``picks``."""
        texts = [q.text for q in pending.questions]
        answers = {q.text: _answer_text(q, p) for q, p in zip(pending.questions, picks)}
        return answer_digest(pending.id, texts, answers)

    def answer(self, host: Any, pending: Any, picks: Any) -> tuple[str, str]:
        """Type ``picks`` into the question ``pending`` the window's tab shows.

        Before the first key the tab must show ``pending``'s first question
        with nothing chosen: no view is ``answered`` when Claude's input box
        is back, else ``unknown_shape``; another question, or one somebody
        began to answer, is ``answered`` or ``busy``. Each step is then typed
        and the tab read until it shows the step's effect; one it does not
        follow stops the typing, ``failed``, since keys have gone in. A tab
        that cannot be typed into says why, as :meth:`deliver` does.
        """
        tty = getattr(host, "tty", None)
        if not tty:
            return "no_tab", ""
        questions = list(getattr(pending, "questions", ()) or ())
        picks = list(picks or ())
        if (not questions or len(picks) != len(questions)
                or any(not _fits(q, p) for q, p in zip(questions, picks))):
            return "unknown_shape", "the answer does not fit the question"
        code, seen = self._look_at(tty)
        if code != "contents":
            return code, ""
        if seen is None:
            box = input_box(self._last_contents)
            return ("answered", "") if box is not None else ("unknown_shape", "")
        if seen.review or not seen.shows(questions[0]):
            mine = any(seen.shows(q) for q in questions[1:]) or seen.review
            return ("busy", "") if mine else ("answered", "")
        if not seen.untouched:
            return "busy", ""
        typed = [False]
        tabbed = seen.tabbed
        try:
            for index, pick in enumerate(picks):
                seen = self._answer_one(tty, seen, index, questions, pick, typed, tabbed)
            if tabbed:
                self._submit(tty, seen, questions, typed)
        except _Stopped as stop:
            return ("failed", str(stop)) if typed[0] else (str(stop), "")
        return "typed", ""

    def _answer_one(self, tty: str, seen: QuestionScreen, index: int, questions: list,
                    pick: Any, typed: list[bool], tabbed: bool) -> QuestionScreen:
        """Type one question's answer; the screen after it, the next
        question's or the review, or the tab with no view left."""
        question = questions[index]
        n = len(question.choices)
        where = f"question {index + 1}"
        if not question.multi:
            target = min(pick.choices) if pick.choices else n
            text = "" if pick.choices else pick.other
            self._step(tty, _moves(seen.at, target), text, typed, where)
            after = self._until(tty, lambda v: self._past(v, index, questions, tabbed),
                                STEP_SETTLE if text else STEP_WAIT)
            if after is None and text:
                stuck = self._read_view(tty)
                if stuck is None or not (stuck.shows(question) and stuck.other == text
                                         and stuck.at == n):
                    raise _Stopped(f"stopped at {where}: its answer did not show")
                self._step(tty, [], "", typed, where)
                after = self._until(tty, lambda v: self._past(v, index, questions, tabbed),
                                    STEP_WAIT)
            return self._gone_or(after, where)
        at = seen.at
        for choice in sorted(pick.choices):
            self._step(tty, _moves(at, choice), "", typed, where)
            seen = self._expect(tty, lambda v, c=choice: v is not None and v.shows(question)
                                and v.checked[c], where)
            at = choice
        if pick.other is not None:
            text = pick.other
            self._step(tty, _moves(at, n), text, typed, where)
            seen = self._expect(tty, lambda v: v is not None and v.shows(question)
                                and v.other == text, where)
            if not seen.other_checked:
                self._step(tty, [], "", typed, where)
                seen = self._expect(tty, lambda v: v is not None and v.shows(question)
                                    and v.other_checked, where)
            at = n
        self._step(tty, _moves(at, n + 1), "", typed, where)
        after = self._until(tty, lambda v: self._past(v, index, questions, tabbed), STEP_WAIT)
        return self._gone_or(after, where)

    def _submit(self, tty: str, seen: Any, questions: list, typed: list[bool]) -> None:
        """The review that ends several questions: Submit answers."""
        if seen is _GONE or not seen.review:
            raise _Stopped("stopped at the review: it did not show")
        self._step(tty, _moves(seen.at, 0), "", typed, "the review")
        if self._until(tty, lambda v: v is None, STEP_WAIT) is None:
            raise _Stopped("stopped at the review: the question did not close")

    @staticmethod
    def _past(view: QuestionScreen | None, index: int, questions: list, tabbed: bool) -> bool:
        """Whether the tab moved past question ``index``: to the next
        question, to the review after the last of a view with tabs, or, after
        a lone single-select question, to no view of it at all."""
        if index + 1 < len(questions):
            return view is not None and view.shows(questions[index + 1])
        if tabbed:
            return view is not None and view.review
        return view is None or not view.shows(questions[index])

    @staticmethod
    def _gone_or(after: Any, where: str) -> Any:
        if after is None:
            raise _Stopped(f"stopped at {where}: the tab did not move on")
        return after

    def _step(self, tty: str, moves: list[str], text: str, typed: list[bool],
              where: str) -> None:
        code, detail = self._typer_of().keys(tty, moves, text)
        if code != "typed":
            raise _Stopped(code if not typed[0] else f"stopped at {where}: {code} {detail}")
        typed[0] = True

    def _expect(self, tty: str, test: Any, where: str) -> QuestionScreen:
        seen = self._until(tty, test, STEP_WAIT)
        if seen is None or seen is _GONE:
            raise _Stopped(f"stopped at {where}: the tab did not show the step")
        return seen

    def _until(self, tty: str, test: Any, limit: float) -> Any:
        """Read the tab until ``test`` holds for what it shows, for at most
        ``limit`` seconds; what it showed (:data:`_GONE` for no view), or
        ``None`` when it never did."""
        waited = 0.0
        while True:
            self._wait(STEP_POLL)
            waited += STEP_POLL
            view = self._read_view(tty)
            if test(view):
                return _GONE if view is None else view
            if waited >= limit:
                return None

    def _read_view(self, tty: str) -> QuestionScreen | None:
        code, seen = self._look_at(tty)
        if code != "contents":
            raise _Stopped(f"stopped: the tab could not be read ({code})")
        return seen

    def _look_at(self, tty: str) -> tuple[str, QuestionScreen | None]:
        if self._reader is None:
            from .claude_code_launch import TerminalReader

            self._reader = TerminalReader()
        code, text = self._reader.read(tty)
        if code in ("no_tab", "not_running"):
            return "no_tab", None
        if code in ("no_claude", "not_allowed"):
            return code, None
        if code != "contents":
            return "unknown_shape", None
        self._last_contents = text
        return "contents", question_screen(text)

    def _typer_of(self) -> Any:
        if self._typer is None:
            from .claude_code_launch import TerminalTyper

            self._typer = TerminalTyper()
        return self._typer

    # -- stopping a turn (ADR-029 increment 4) --------------------------------------

    def interrupt(self, host: Any) -> tuple[str, str]:
        """Type Esc into the window's tab (:data:`claude_code_launch.NAMED_KEYS`):
        ``typed``, or the typer's code."""
        tty = getattr(host, "tty", None)
        if not tty:
            return "no_tab", ""
        if self._typer is None:
            from .claude_code_launch import TerminalTyper

            self._typer = TerminalTyper()
        return self._typer.key(tty, "escape")

    def receipt(self, host: Any, kind: str, since: float) -> Receipt | None:
        """The evidence an input left: the first entry of the person's written
        since ``since`` (:func:`first_after`) in the window's transcript, or in
        the new session its process runs since a ``/clear``; the process
        having ended (:data:`ENDED_DIGEST`, ``/exit``); else ``None``. Read
        fresh each time, never from the kept reads."""
        host_id = getattr(host, "host_id", None)
        if not isinstance(host_id, str) or not _HOST_ID.fullmatch(host_id):
            return None
        if kind == KIND_QUESTION:
            with self._lock:
                path = self._transcript(host_id)
            return answered_after(path, since) if path is not None else None
        found = self._first_after(host_id, since)
        pid = getattr(host, "pid", None)
        if found is not None or not isinstance(pid, int) or pid <= 0:
            return found
        if not self._alive(pid):
            return Receipt(ENDED_DIGEST, time.time())
        now = self._session_of(pid)
        return self._first_after(now, since) if now and now != host_id else None

    def _first_after(self, host_id: str, since: float) -> Receipt | None:
        with self._lock:
            path = self._transcript(host_id)
        return first_after(path, since) if path is not None else None

    def _session_of(self, pid: int) -> str | None:
        """The session Claude Code's per-process record says ``pid`` runs now."""
        try:
            record = json.loads((self.home / "sessions" / f"{pid}.json").read_text("utf-8"))
        except (OSError, ValueError):
            return None
        session = record.get("sessionId") if isinstance(record, dict) else None
        return session if isinstance(session, str) and _HOST_ID.fullmatch(session) else None
