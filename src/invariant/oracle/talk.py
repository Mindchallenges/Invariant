"""The oracle page talking to running sessions: the host-neutral half (ADR-029).

ADR-029 D4 splits the talk path in two. This module is the oracle's half: it
names what it needs from a host as :class:`SessionTalk`, and decides what to
make of the answer. A host module implements the seam (Claude Code's is
``hosts/claude_code_talk``) and the CLI hands it in (``_oracle_talk``), the
shape ADR-007 D11's ``HostProbe`` has. Nothing here imports ``hosts/``.

**Increment 1 is the read of a session's last message** (ADR-009 D17,
ADR-029 D11's excerpt). The seam has one method, ``conversation``, and this
module the rule :func:`last_message`: Claude's newest text, and the prompt it
answers. ``oracle/web`` asks for it on a paired device's state read and hands
it to ``oracle/panel``.

**Increment 2 is the Conversation screen** (ADR-029 D11, D19 to D22). The
same read, through the same one method, is served by ``GET /api/session`` as
:func:`turns`: the newest :data:`CONVERSATION_ENTRIES` entries, a tool call
among them as one line (role :data:`ROLE_TOOL`), each capped again here
whatever the host did. :func:`cursor` names what was served, so a read that
finds nothing new answers "unchanged" and no text; :func:`offered` says what
the page may do with the session, which in this increment is nothing, and
why.

Neither is ever in the ``assess`` document, so never in the watcher's
snapshot, ``oracle list`` or the TUI, and nothing here writes either
anywhere.

**Increment 3 sends a message** (ADR-029 D1 to D7, D25 onwards).
:class:`Talker` is the second power beside the confirmed action: one tap,
held :data:`HOLD_SECONDS` on the server for Undo (D2), bound to the
:class:`Fingerprint` the person's read saw through a one-tap talk nonce,
then typed once the tab has been quiet (D6), checked against a fresh scan
just before typing under the tab's typist lock (D3, D6), and confirmed by
the receipt the next ``UserPromptSubmit`` leaves on the attention sidecar
(D3). A message whose turn still runs waits in a bounded ring in memory
(D7). Every step is an audit line without the text (D5); nothing is ever
typed twice. The answers are later increments: a message a host says would
switch Claude's input mode, or is a command opening a menu the phone cannot
drive, is refused here.

**Increment 4 stops a turn** (ADR-029 D2, D3, D6, D7, D34 to D36). An
interrupt is a send of the kind :data:`KIND_INTERRUPT` with no text: the same
one tap, nonce and Undo hold, then typed at once, never queued and with no
wait for quiet, but only into a turn that still runs with no prompt open and
an empty input line, checked under the typist's lock just before the key.
Its receipt is the host's evidence in the window's transcript
(``SessionTalk.receipt``), since no hook fires for it.

A slash command is a message (D1, ruling 15): typed as one, and where no
prompt hook confirms it (a built-in command fires none), the same host
evidence is its receipt (D38).

**Increment 5 answers a question** (ADR-029 D8, D40 to D46). The seam's
``pending`` names the question the window waits on (:func:`read_question`),
which ``/api/session`` serves the page as plain words (:func:`pending_view`)
and binds to the talk nonce. An answer, of the kind :data:`KIND_QUESTION`,
names each question's choices by index and an optional one-line "Other"
(:func:`picks`), checked against that read at the tap. It takes the same one
tap and Undo hold, is never queued, waits for the question on the tab to be
untouched and unchanged, and is typed by the host (``SessionTalk.answer``)
only after the fresh scan finds the same question waiting and a fresh
``pending`` reads the same call. Its receipt is the answered tool's hook,
the sidecar's ``received``, or the window's evidence where none came.

**Fail closed.** A host stops its read at an entry it cannot read and returns
only what is newer (``unreadable`` says why). What is newer stands; anything
the rule would need from further back is not guessed at. An entry of a role
this module does not know stops :func:`turns` the same way, and a read that
raises is unreadable as a whole.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any, Protocol

from ..core.errors import StateError
from ..core.store import InertStore
from .attention import AttentionStore

#: The roles a conversation's entry has: the person, Claude, and one of
#: Claude's tool calls, whose text is its one-line summary.
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"
ROLES = (ROLE_USER, ROLE_ASSISTANT, ROLE_TOOL)

#: The most of one entry's text the oracle serves, in characters (ADR-009
#: D17). The host's reader cuts there; this is the backstop for any reader.
ENTRY_TEXT_CAP = 20_000

#: The most of a tool call's line the oracle serves (ADR-029 D11).
TOOL_LINE_CAP = 160

#: The most entries the Conversation screen is served (ADR-009 D17).
CONVERSATION_ENTRIES = 40

#: Why the page may not act on a session (:func:`offered`): this increment
#: only reads; the read stopped at a shape the host does not know; the window
#: is gone.
WHY_READING_ONLY = "reading_only"
WHY_UNREADABLE = "unreadable"
WHY_GONE = "gone"


@dataclass(frozen=True)
class TalkHost:
    """A live window, as the oracle names it to the seam: its host session id,
    the terminal it runs on, which typing and looking need (increment 3), and
    its process, which a receipt follows past a new session or an exit
    (increment 4)."""

    host_id: str
    tty: str | None = None
    pid: int | None = None


class Entry(Protocol):
    """One turn with text, oldest first in a :class:`Conversation`."""

    @property
    def role(self) -> str: ...

    @property
    def text(self) -> str: ...

    @property
    def at(self) -> float: ...

    @property
    def cut(self) -> bool: ...


class Unreadable(Protocol):
    """Why a read stopped: the shape the host did not know, and its version."""

    @property
    def reason(self) -> str: ...

    @property
    def version(self) -> str | None: ...


class Conversation(Protocol):
    """A window's recent entries, oldest first, and why the read stopped short."""

    @property
    def entries(self) -> Sequence[Entry]: ...

    @property
    def unreadable(self) -> Unreadable | None: ...


class Look(Protocol):
    """One look at a window's tab (ADR-029 D6): a digest of what it shows and
    whether Claude's input line is empty, or why the tab cannot be typed into
    (``refused``: ``no_tab``, ``no_claude``, ``not_allowed``)."""

    @property
    def digest(self) -> str | None: ...

    @property
    def empty(self) -> bool | None: ...

    @property
    def refused(self) -> str | None: ...


class SessionTalk(Protocol):
    """What the oracle asks a host about a live window (ADR-029 D4).

    ``conversation`` (increments 1 and 2): the recent turns with text and
    Claude's tool calls, read on demand, capped by the host's reader, never
    stored. The page's ``after`` is the oracle's (:func:`cursor`), not the
    host's: the host answers the whole recent read each time.

    Increment 3 adds what a message needs (D25): ``quiet``, one look at the
    tab, which the oracle repeats to time the quiet; ``deliver``, which types
    the text and answers ``(code, detail)``; ``digest``, the hash the host's
    receipt carries for that text, normalised as the host reports it; and
    ``refuses``, the host's reason a text cannot be typed as a message (it
    would switch the input into another mode), or ``None``.

    Increment 4 adds what an interrupt needs (D36): ``interrupt``, which types
    the host's interrupt key and answers ``(code, detail)``, and ``receipt``,
    the evidence an input that fires no hook left: the first entry of the
    person's in the window written since ``since`` (an epoch), as a value
    with its ``hash`` and ``at``, or ``None``.

    Increment 5 adds what a question's answer needs (D41 to D43): ``pending``,
    the question the window waits on (``id``, ``kind`` ``question``,
    ``questions``, each with its ``text``, ``header``, ``multi`` and
    ``choices`` of ``label`` and ``description``), ``None``, or
    ``Unreadable``; ``answer``, which types the :class:`Pick` for each
    question and answers ``(code, detail)``; and ``answer_digest``, the hash
    the answered tool's receipt will carry. ``quiet`` then also says what the
    tab ``shows``: Claude's input box (``input``) or a question
    (``question``), whose ``empty`` is that nothing on it is chosen yet.
    """

    def conversation(self, host: TalkHost) -> Conversation: ...

    def quiet(self, host: TalkHost) -> Look | Unreadable: ...

    def deliver(self, host: TalkHost, text: str) -> tuple[str, str]: ...

    def digest(self, kind: str, text: str) -> str: ...

    def refuses(self, kind: str, text: str) -> str | None: ...

    def interrupt(self, host: TalkHost) -> tuple[str, str]: ...

    def receipt(self, host: TalkHost, kind: str, since: float) -> Any: ...

    def pending(self, host: TalkHost) -> Any: ...

    def answer(self, host: TalkHost, pending: Any, picks: Sequence[Pick]) -> tuple[str, str]: ...

    def answer_digest(self, pending: Any, picks: Sequence[Pick]) -> str: ...


@dataclass(frozen=True)
class LastMessage:
    """Claude's newest text, and the person's prompt it answers, if read."""

    claude: str
    you: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {"claude": self.claude, "you": self.you}


def last_message(conversation: Conversation) -> LastMessage | None:
    """The newest assistant entry with text, and the newest prompt before it.

    The prompt is the one Claude's text answers, so a prompt sent after it (a
    turn under way) is not shown as its context. ``None`` when the read holds
    no text from Claude: none was written, or the read stopped before one.
    """
    entries = list(conversation.entries)
    claude: str | None = None
    for entry in reversed(entries):
        if claude is None:
            if entry.role == ROLE_ASSISTANT and entry.text:
                claude = entry.text[:ENTRY_TEXT_CAP]
            continue
        if entry.role == ROLE_USER and entry.text:
            return LastMessage(claude, entry.text[:ENTRY_TEXT_CAP])
    return LastMessage(claude, None) if claude is not None else None


# -- the Conversation screen (ADR-029 increment 2) -------------------------------------


@dataclass(frozen=True)
class Turns:
    """What the Conversation screen is served: its turns, oldest first, as
    plain values, and whether the read stopped among them at a shape it could
    not read."""

    turns: tuple[dict[str, Any], ...]
    unreadable: bool

    def to_dict(self) -> dict[str, Any]:
        return {"turns": list(self.turns), "unreadable": self.unreadable}


def turns(conversation: Conversation) -> Turns:
    """The newest :data:`CONVERSATION_ENTRIES` entries, each capped again.

    A stop the host met is said only when it lies among the turns shown: past
    the newest forty, nothing shown depends on it. An entry of a role not in
    :data:`ROLES` stops the turns there, keeping only what is newer.
    """
    entries = list(conversation.entries)
    stopped = conversation.unreadable is not None and len(entries) <= CONVERSATION_ENTRIES
    shown: list[dict[str, Any]] = []
    for entry in reversed(entries[-CONVERSATION_ENTRIES:]):
        if entry.role not in ROLES:
            stopped = True
            break
        cap = TOOL_LINE_CAP if entry.role == ROLE_TOOL else ENTRY_TEXT_CAP
        text = entry.text
        cut = entry.role != ROLE_TOOL and (bool(entry.cut) or len(text) > cap)
        if len(text) > cap:
            text = text[: cap - 1] + "…" if entry.role == ROLE_TOOL else text[:cap]
        shown.append({"role": entry.role, "text": text, "at": entry.at, "cut": cut})
    return Turns(tuple(reversed(shown)), stopped)


def read_turns(talk: SessionTalk, host: TalkHost) -> Turns:
    """The window's turns read now; a read that raises, for any reason, is
    unreadable and shows nothing (ADR-029 ruling 11)."""
    try:
        conversation = talk.conversation(host)
    except Exception:  # noqa: BLE001 - fail closed: the page says it cannot read it
        return Turns((), True)
    return turns(conversation)


def cursor(served: dict[str, Any]) -> str:
    """A name for what was served, the ``after`` of the page's next read.

    Equal reads have equal names, so the next read that finds the same answers
    "unchanged" and carries no text; any change, a stop included, is a new
    name and the whole conversation again (ADR-029 D21).
    """
    canonical = json.dumps(served, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def offered(live: bool, unreadable: bool) -> dict[str, Any]:
    """What a server that only reads lets the page do: nothing, and why.

    A server that sends asks :meth:`Talker.offered` instead.
    """
    why = WHY_GONE if not live else WHY_UNREADABLE if unreadable else WHY_READING_ONLY
    return {"send": False, "answer": False, "interrupt": False, "why": why}


# -- sending a message (ADR-029 increment 3) -------------------------------------------

#: The inputs typed so far (D1): a message (increment 3), an interrupt, the
#: interrupt key with no text (increment 4), and a question's answer
#: (increment 5). The permission answer is increment 6's.
KIND_MESSAGE = "message"
KIND_INTERRUPT = "interrupt"
KIND_QUESTION = "question"
KINDS = (KIND_MESSAGE, KIND_INTERRUPT, KIND_QUESTION)

#: What a tab shows on one look (D41): Claude's input box, or a question.
SHOWS_INPUT = "input"
SHOWS_QUESTION = "question"

#: The Undo window, held on the server (D2).
HOLD_SECONDS = 5.0
#: How long a one-tap talk nonce stays good (D2).
NONCE_TTL = 120.0
#: The tab must look the same, its input line empty, this long (D6).
QUIET_SECONDS = 3.0
#: How often the tab is looked at while waiting for quiet (D6).
QUIET_STEP = 0.5
#: Busy this long from when the send fell due is a refusal (D6).
BUSY_LIMIT = 60.0
#: The typist's lock is held this long after the typing (D6).
TYPED_SETTLE = 0.5
#: The receipt must arrive within this long of the typing (D3).
RECEIPT_SECONDS = 5.0
RECEIPT_STEP = 0.25
#: A receipt's time is to the second; one this much older than the typing
#: still counts, so the rounding never loses one (D27).
RECEIPT_SKEW = 2.0
#: The ring: waiting sends per window, and across the server (D7).
RING_PER_HOST = 8
RING_TOTAL = 64
#: A talk request's body, and a message's length in characters (D2).
BODY_CAP = 32 << 10
MESSAGE_CAP = 8000
#: The delivery loop's beat (D7).
LOOP_SECONDS = 1.0
#: A queued send whose window a fresh scan found not ready is looked at again
#: after this long, not every beat (D28).
LOOK_AGAIN = 5.0
#: A finished send is listed this long, and at most this many are kept (D29).
FINISHED_SHOWN = 600.0
FINISHED_KEPT = 64
#: How long a typist waits for another to leave the tab (D6).
TYPIST_WAIT = 10.0

#: A send's states (D2, D3, D7). ``due`` is the wait for quiet and the typing.
HELD = "held"
QUEUED = "queued"
DUE = "due"
TYPED = "typed"
DELIVERED = "delivered"
NOT_CONFIRMED = "not_confirmed"
REFUSED = "refused"
CANCELLED = "cancelled"
DROPPED = "dropped"
LIVE = (HELD, QUEUED, DUE, TYPED)

#: Each state's audit event (D5).
EVENTS = {HELD: "talk-held", QUEUED: "talk-queued", TYPED: "talk-typed",
          DELIVERED: "talk-delivered", NOT_CONFIRMED: "talk-not-confirmed",
          CANCELLED: "talk-cancelled", DROPPED: "talk-dropped", REFUSED: "talk-refused"}

#: Why a send was refused, dropped or not confirmed; each is worded by the
#: panel (``talk_why_<code>``) and logged by its code.
WHY_TYPING = "typing"
WHY_CHANGED = "changed"
WHY_NO_TAB = "no_tab"
WHY_NO_CLAUDE = "no_claude"
WHY_NOT_ALLOWED = "not_allowed"
WHY_FAILED = "failed"
WHY_MENU = "menu_command"
WHY_MODE = "input_mode"
WHY_RING = "ring"
WHY_REVOKED = "revoked"
WHY_OTHER_HASH = "other_hash"
WHY_NO_RECEIPT = "no_receipt"
#: An interrupt's own refusals (D35): the turn it was meant for has ended, or
#: Claude now waits on a question or a permission prompt.
WHY_ENDED = "ended"
WHY_ASKING = "asking"
#: An answer's own refusal (D44): the question it answers no longer waits,
#: was replaced, or was answered at the Mac.
WHY_ANSWERED = "answered"
#: An answer that does not fit the question the read saw (D42).
WHY_ANSWER = "answer"

#: What changed under a fingerprint (D3).
CHANGED_WINDOW = "window"
CHANGED_PROCESS = "process"
CHANGED_TERMINAL = "terminal"
CHANGED_TURN = "turn"

#: The message types, beyond its text, only line breaks: any other control
#: character could reach the terminal as a key (Esc, Ctrl-C) (D26).
_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")

#: A terminal's name, as ``ps`` gives it; the only kind of lock name made.
_TTY = re.compile(r"tty[a-z]*[0-9]+")

_ASKING = ("permission", "question")
_VERIFIED = ("record", "both")
_STATUS_READY = ("idle", "shell")
_STATUS_KNOWN = ("busy", "waiting", "idle", "shell")


@dataclass(frozen=True)
class Fingerprint:
    """What a send is bound to (D3): the window, its process, its terminal,
    what it waited on when the person looked, and whether it waited for
    input then."""

    host: str
    pid: int | None
    start: str | None
    tty: str | None
    waiting: str | None
    since: str | None
    ready: bool
    #: Whether a question or permission prompt is open: the record's
    #: ``waiting`` kind, or Claude Code's own ``waiting`` status (BUG_087).
    asking: bool = False


def _live_host(row: Mapping[str, Any]) -> dict[str, Any] | None:
    hosts = [h for h in row.get("hosts") or [] if isinstance(h, dict)]
    host = next((h for h in hosts if h.get("current")), hosts[0] if hosts else None)
    if not host or host.get("alive") is not True:
        return None
    host_id = host.get("host_session_id")
    return host if isinstance(host_id, str) and host_id else None


def attention_ready(record: Mapping[str, Any] | None) -> bool:
    """Whether the attention record says Claude waits for the next message.

    Its turn ended or it sits at the idle prompt, with no question or
    permission prompt open. A record that says nothing is not ready: the
    oracle never types on a guess (D28).
    """
    record = record or {}
    waiting = record.get("waiting") if isinstance(record.get("waiting"), dict) else None
    kind = waiting.get("kind") if waiting else None
    if kind in _ASKING:
        return False
    return kind == "idle" or record.get("turn") == "ended"


def host_ready(host: Mapping[str, Any]) -> bool:
    """Whether a scanned window waits for input now (D28).

    Claude Code's own status, where the per-process record vouched for the
    pid, outranks the attention record (BUG_087): ``idle`` and ``shell`` wait
    for input; ``busy`` and ``waiting`` (on a prompt) do not. Otherwise the
    attention record decides (:func:`attention_ready`).
    """
    status = host.get("status")
    if host.get("pid_source") in _VERIFIED and status in _STATUS_KNOWN:
        return status in _STATUS_READY
    return attention_ready(host.get("attention") if isinstance(host.get("attention"), dict)
                           else None)


def fingerprint(row: Mapping[str, Any] | None) -> Fingerprint | None:
    """The fingerprint of a document row's live window, ``None`` without one."""
    host = _live_host(row or {})
    if host is None:
        return None
    record = host.get("attention") if isinstance(host.get("attention"), dict) else {}
    waiting = record.get("waiting") if isinstance(record.get("waiting"), dict) else None
    if waiting:
        kind, since = waiting.get("kind"), waiting.get("since")
    else:
        kind, since = f"turn:{record.get('turn')}", record.get("turn_since")
    pid = host.get("pid")
    asking = kind in _ASKING or (host.get("pid_source") in _VERIFIED
                                 and host.get("status") == "waiting")
    return Fingerprint(
        host=host["host_session_id"],
        pid=pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
        start=host.get("process_start") if isinstance(host.get("process_start"), str) else None,
        tty=host.get("tty") if isinstance(host.get("tty"), str) else None,
        waiting=kind if isinstance(kind, str) else None,
        since=since if isinstance(since, str) else None,
        ready=host_ready(host),
        asking=asking,
    )


def identity_change(old: Fingerprint, new: Fingerprint) -> str | None:
    """What makes ``new`` another window than ``old``, if anything (D3)."""
    if new.host != old.host:
        return CHANGED_WINDOW
    if new.pid != old.pid or new.start != old.start:
        return CHANGED_PROCESS
    if new.tty != old.tty:
        return CHANGED_TERMINAL
    return None


def text_hash(text: str) -> str:
    """The SHA-256 of a text as typed, the audit line's ``hash`` (D5)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_attention(project: str, host_id: str) -> dict[str, Any] | None:
    """One window's attention record, read through an inert store; ``None``
    when there is none or it cannot be read. The delivery loop's cheap read
    (D7): a row's ``project`` is its state root."""
    try:
        record = AttentionStore(InertStore(Path(project))).read(host_id)
    except Exception:  # noqa: BLE001 - unreadable reads as nothing known
        return None
    return record.to_dict() if record is not None else None


@contextmanager
def typist(home: Path, tty: str, wait: float = TYPIST_WAIT) -> Iterator[None]:
    """Hold ``locks/type-<tty>.lock`` in the oracle home: one typist per tab (D6).

    A file lock, so the web server and the watcher's chain nudge exclude each
    other. A name that is not a terminal's, or a lock not had within
    ``wait`` seconds, is a :class:`StateError` and nothing is typed.
    """
    from .index import HomeStore

    if not isinstance(tty, str) or not _TTY.fullmatch(tty):
        raise StateError(f"{tty!r} is not a terminal")
    with HomeStore(Path(home)).lock(f"type-{tty}", timeout=wait):
        yield


def _epoch(text: Any) -> float | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _start_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True, name="oracle-talk").start()


@dataclass
class Send:
    """One message from a paired device, from its tap to its receipt.

    The text is held only until it is typed or the send is gone (ADR-009
    D17); the rest is what the page and the audit line are told.
    """

    id: str
    device: str
    device_name: str
    row: str
    project: str
    project_name: str
    title: str
    kind: str
    text: str
    length: int
    hash: str
    fp: Fingerprint
    at: float
    due: float
    state: str = HELD
    reason: str | None = None
    what: str | None = None
    ended: float | None = None
    #: When a queued send's window is looked at again (:data:`LOOK_AGAIN`).
    look: float = 0.0
    #: Whether it has been queued (its ``talk-queued`` line is written once).
    queued: bool = False
    #: Set when it is typed: when, the digest its receipt must carry, and the
    #: receipt the sidecar held before, which a new one must differ from.
    typed_at: float = 0.0
    typed_timer: float = 0.0
    digest: str = ""
    before: Any = None
    #: A question's answer (increment 5): the picks, held like a message's
    #: text until typed, and the question the person's read saw.
    picks: tuple[Pick, ...] = ()
    question: Any = None
    #: The answer as the audit line records it (D42): each question's choice
    #: indexes, "Other" as the index after the last choice; never a label.
    choice: list[list[int]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """The page's view: never the text, never the hash."""
        return {"id": self.id, "row": self.row, "kind": self.kind, "state": self.state,
                "reason": self.reason, "what": self.what, "at": self.at,
                "length": self.length}


@dataclass(frozen=True)
class Issued:
    """A one-tap talk nonce and what it binds (D2): with a question waiting,
    the question its read saw, whose choices an answer names by index."""

    nonce: str
    fp: Fingerprint
    project: str
    project_name: str
    title: str
    expires: float
    pending: Any = None


@dataclass(frozen=True)
class Reply:
    """An HTTP status and the JSON body that goes with it."""

    status: int
    body: dict[str, Any]


@dataclass
class Talker:
    """The talk path's server half (ADR-029 D2 to D7): nonces, sends, the ring
    and the delivery loop. In memory only; a restart drops it all, and the
    page learns that from :attr:`boot`.

    ``talk`` is the host's seam; ``fresh`` a fresh scan's ``assess`` document;
    ``audit`` appends one audit line (``AuditLog.record``); ``attention``
    reads one window's record (:func:`read_attention`); ``typist`` holds a
    tab's lock (:func:`typist`); ``alert`` says a send was not confirmed,
    naming its project and session; ``paired`` whether a device is still
    paired. ``spawn`` runs a delivery and ``background`` the loop.
    """

    talk: SessionTalk
    fresh: Callable[[], dict[str, Any]]
    audit: Callable[[dict[str, Any]], None]
    attention: Callable[[str, str], dict[str, Any] | None] = read_attention
    typist: Callable[[str], Any] | None = None
    alert: Callable[[str, str], None] | None = None
    paired: Callable[[str], bool] | None = None
    clock: Callable[[], float] = time.time
    timer: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    token: Callable[[], str] = field(default=lambda: secrets.token_urlsafe(12))
    spawn: Callable[[Callable[[], None]], None] = _start_thread
    background: Callable[[Callable[[], None]], None] = _start_thread
    boot: str = field(default_factory=lambda: secrets.token_urlsafe(9))

    def __post_init__(self) -> None:
        self._sends: dict[str, Send] = {}
        self._nonces: dict[tuple[str, str], Issued] = {}
        #: What a window's tab answered that keeps send off, by window and pid.
        self._marks: dict[tuple[str, int | None], str] = {}
        #: Each window's ``turn_since`` when a send was last typed into it: the
        #: next queued send waits for a turn after it to end (D7).
        self._typed_turn: dict[str, str | None] = {}
        self._inflight: set[str] = set()
        self._lock = threading.Lock()
        self._looping = False
        self.stopped = threading.Event()

    # -- the one-tap nonce (D2) -----------------------------------------------------

    def issue(self, device: str, row: str, fp: Fingerprint, project: str,
              project_name: str, title: str, pending: Any = None) -> str:
        """A talk nonce for ``device`` and ``row``, replacing the one before;
        ``pending`` is the question the read saw, if one waits (D42)."""
        issued = Issued(self.token(), fp, project, project_name, title,
                        self.timer() + NONCE_TTL, pending)
        with self._lock:
            now = self.timer()
            for key in [k for k, v in self._nonces.items() if v.expires <= now]:
                del self._nonces[key]
            self._nonces[(device, row)] = issued
        return issued.nonce

    def _spend(self, device: str, row: str, nonce: str) -> Issued | str:
        with self._lock:
            issued = self._nonces.get((device, row))
            if issued is None or not nonce or not secrets.compare_digest(issued.nonce, nonce):
                return "this page's read is old or already used; nothing was sent. It reads again"
            del self._nonces[(device, row)]
        if issued.expires <= self.timer():
            return "this page's read expired; nothing was sent. It reads again"
        return issued

    # -- what the page may do and see --------------------------------------------------

    def offered(self, fp: Fingerprint | None, live: bool, unreadable: bool,
                asked: QuestionRead | None = None) -> dict[str, Any]:
        """Whether the page may send to the window now, and why not (D25),
        whether it may stop the turn (D35), and whether it may answer the
        question ``asked``, the read's (D44).

        Stop is offered while a turn runs in a Terminal tab where Claude runs,
        with no question or permission prompt open. It stays when the session
        cannot be read (ruling 11): only a tab without Claude turns it off.
        An answer is offered while the attention record says a question
        waits, in a Terminal tab nothing has turned off, when the session and
        the question both read; an unreadable one turns it off (D11).
        """
        why: str | None = None
        mark: str | None = None
        if not live or fp is None:
            why = WHY_GONE
        else:
            with self._lock:
                mark = self._marks.get((fp.host, fp.pid))
            why = WHY_UNREADABLE if unreadable else mark
            if why is None and fp.tty is None:
                why = WHY_NO_TAB
        stop = bool(live and fp is not None and fp.tty is not None and not fp.ready
                    and not fp.asking and mark not in (WHY_NO_TAB, WHY_NO_CLAUDE))
        answer = bool(live and fp is not None and fp.tty is not None
                      and fp.waiting == KIND_QUESTION and mark is None and not unreadable
                      and asked is not None and asked.question is not None
                      and not asked.unreadable)
        return {"send": why is None, "answer": answer, "interrupt": stop, "why": why,
                "running": bool(fp is not None and not fp.ready)}

    def sends(self, device: str, row: str) -> list[dict[str, Any]]:
        """This device's sends to ``row``: those still going, and those finished
        within :data:`FINISHED_SHOWN`; an undone one is not listed."""
        now = self.timer()
        with self._lock:
            return [s.to_dict() for s in self._sends.values()
                    if s.device == device and s.row == row and s.state != CANCELLED
                    and (s.ended is None or now - s.ended <= FINISHED_SHOWN)]

    # -- a tap, and its Undo ----------------------------------------------------------

    def post(self, device: Any, row: str, nonce: str, kind: Any, text: Any,
             answers: Any = None) -> Reply:
        """Hold one message, interrupt or question's answer for its Undo
        window, or refuse it with one reason."""
        if kind not in KINDS:
            return _no(HTTPStatus.BAD_REQUEST, "only a message, a stop or an answer is sent "
                                               "from here; nothing was sent", "kind")
        if kind in (KIND_INTERRUPT, KIND_QUESTION):
            if text not in (None, ""):
                what = "a stop" if kind == KIND_INTERRUPT else "an answer"
                return _no(HTTPStatus.BAD_REQUEST, f"{what} carries no text of its own; "
                                                   "nothing was sent", "text")
            text = ""
        else:
            checked = _message_problem(text)
            if checked is not None:
                return checked
        issued = self._spend(device.id, row, nonce)
        if isinstance(issued, str):
            return _no(HTTPStatus.FORBIDDEN, issued, "nonce")
        fp = issued.fp
        chosen: tuple[Pick, ...] = ()
        if kind == KIND_QUESTION and issued.pending is not None:
            found = picks(issued.pending, answers)
            if isinstance(found, str):
                return _no(HTTPStatus.BAD_REQUEST, f"{found}; nothing was sent", WHY_ANSWER)
            chosen = found
        typed = "".join(p.other or "" for p in chosen)
        send = Send(self.token(), device.id, device.name, row, issued.project,
                    issued.project_name, issued.title, kind, text,
                    len(typed) if kind == KIND_QUESTION else len(text),
                    _picks_hash(chosen) if kind == KIND_QUESTION else text_hash(text),
                    fp, self.clock(), self.timer() + HOLD_SECONDS,
                    picks=chosen, question=issued.pending,
                    choice=_choice_line(issued.pending, chosen))
        if kind == KIND_INTERRUPT:
            refused = self._stop_refusal(fp)
        elif kind == KIND_QUESTION:
            refused = self._answer_refusal(fp, issued.pending)
        else:
            refused = self.offered(fp, True, False)["why"] or self._refuses(kind, text)
        if refused:
            send.state, send.reason, send.text, send.picks = REFUSED, refused, "", ()
            self._record(send)
            return _no(HTTPStatus.CONFLICT, f"not sent: {refused}", refused)
        with self._lock:
            mine = [s for s in self._sends.values()
                    if s.fp.host == fp.host and s.state in (HELD, QUEUED)]
            waiting = [s for s in self._sends.values() if s.state in (HELD, QUEUED)]
            if len(mine) >= RING_PER_HOST or len(waiting) >= RING_TOTAL:
                pool = mine if len(mine) >= RING_PER_HOST else waiting
                oldest = next((s for s in pool if s.state == QUEUED), None)
                if oldest is None:
                    return _no(HTTPStatus.CONFLICT, "too many messages are waiting to be "
                                                    "sent; nothing was sent", WHY_RING)
                self._end(oldest, DROPPED, WHY_RING)
                dropped = oldest
            else:
                dropped = None
            self._sends[send.id] = send
        if dropped is not None:
            self._record(dropped)
        try:
            self.audit(self._line(send))
        except StateError as exc:
            with self._lock:
                del self._sends[send.id]
            return _no(HTTPStatus.SERVICE_UNAVAILABLE,
                       f"the send could not be recorded in the audit log ({exc}); nothing "
                       f"was sent", "audit")
        self._trim()
        self._ensure_loop()
        return Reply(HTTPStatus.ACCEPTED, {"send": send.to_dict(), "boot": self.boot})

    def cancel(self, device: Any, send_id: Any) -> Reply:
        """Undo a held send, or remove a queued one, for the device that made it."""
        with self._lock:
            send = self._sends.get(send_id) if isinstance(send_id, str) else None
            if send is None or send.device != device.id:
                return _no(HTTPStatus.NOT_FOUND, "no such message from this device", "unknown")
            if send.state not in (HELD, QUEUED):
                return _no(HTTPStatus.CONFLICT, "it is already being sent and can no longer "
                                                "be undone", "late")
            self._end(send, CANCELLED, None)
        self._record(send)
        return Reply(HTTPStatus.OK, {"send": send.to_dict()})

    def _stop_refusal(self, fp: Fingerprint) -> str | None:
        """Why the turn the person saw cannot be stopped from here (D35)."""
        if fp.ready:
            return WHY_ENDED
        if fp.asking:
            return WHY_ASKING
        shown = self.offered(fp, True, False)
        return None if shown["interrupt"] else (shown["why"] or WHY_NO_TAB)

    def _answer_refusal(self, fp: Fingerprint, pending: Any) -> str | None:
        """Why the question the person's read saw cannot be answered from here
        (D44): the read saw none, or the window is not one an answer is typed
        into."""
        if pending is None or fp.waiting != KIND_QUESTION:
            return WHY_ANSWERED
        shown = self.offered(fp, True, False, QuestionRead(pending, False))
        return None if shown["answer"] else (shown["why"] or WHY_NO_TAB)

    def _refuses(self, kind: str, text: str) -> str | None:
        try:
            return self.talk.refuses(kind, text)
        except Exception:  # noqa: BLE001 - a host that cannot say refuses
            return WHY_MODE

    # -- the delivery loop (D7) -------------------------------------------------------

    def _ensure_loop(self) -> None:
        with self._lock:
            if self._looping:
                return
            self._looping = True
        self.background(self._run)

    def _run(self) -> None:
        while not self.stopped.wait(LOOP_SECONDS):
            try:
                self.tick()
            except Exception:  # noqa: BLE001, S112 - the next beat tries again
                continue

    def close(self) -> None:
        self.stopped.set()

    def tick(self) -> None:
        """One beat: a held send past its Undo window falls due or is queued; a
        window's oldest queued send falls due once its turn has ended."""
        now = self.timer()
        start: list[tuple[Send, bool]] = []
        ended: list[Send] = []
        with self._lock:
            live = [s for s in self._sends.values() if s.state in (HELD, QUEUED)]
        for send in live:
            if self.paired is not None and not self._paired(send.device):
                with self._lock:
                    if send.state in (HELD, QUEUED):
                        self._end(send, DROPPED, WHY_REVOKED)
                        ended.append(send)
        for send in self._held_due(now):
            if send.kind in (KIND_INTERRUPT, KIND_QUESTION):
                # Never queued (D7): typed at once, unless the window has a
                # delivery under way, which it waits one beat for.
                if send.fp.host not in self._inflight:
                    start.append((send, True))
                continue
            busy = send.fp.host in self._inflight or self._has_queued(send.fp.host, send)
            if not busy and send.fp.ready:
                start.append((send, True))
            elif not busy and attention_ready(self.attention(send.project, send.fp.host)):
                start.append((send, False))
            else:
                self._queue(send)
        for send in self._queued_heads(now):
            record = self.attention(send.project, send.fp.host)
            if not attention_ready(record):
                continue
            turn = (record or {}).get("turn_since")
            with self._lock:
                typed = self._typed_turn.get(send.fp.host, object())
            if turn == typed:
                continue
            start.append((send, False))
        for send in ended:
            self._record(send)
        for send, now_mode in start:
            with self._lock:
                if send.state not in (HELD, QUEUED) or send.fp.host in self._inflight:
                    continue
                send.state = DUE
                self._inflight.add(send.fp.host)
            self.spawn(lambda s=send, m=now_mode: self._deliver(s, m))
        self._trim()

    def _paired(self, device: str) -> bool:
        try:
            return bool(self.paired(device))  # type: ignore[misc]
        except Exception:  # noqa: BLE001 - unknown is not revoked
            return True

    def _held_due(self, now: float) -> list[Send]:
        with self._lock:
            return [s for s in self._sends.values() if s.state == HELD and s.due <= now]

    def _has_queued(self, host: str, before: Send) -> bool:
        with self._lock:
            return any(s.state == QUEUED and s.fp.host == host and s is not before
                       for s in self._sends.values())

    def _queued_heads(self, now: float) -> list[Send]:
        heads: dict[str, Send] = {}
        with self._lock:
            for send in self._sends.values():
                if send.state == QUEUED and send.fp.host not in heads:
                    heads[send.fp.host] = send
            return [s for h, s in heads.items() if h not in self._inflight and s.look <= now]

    def _queue(self, send: Send) -> None:
        """Into the ring (D7). Its bounds hold at the tap, which counts held and
        queued sends alike (:meth:`post`), so queueing never passes them; the
        ``talk-queued`` line is written once."""
        with self._lock:
            send.state = QUEUED
            if send.queued:
                return
            send.queued = True
        self._record(send)

    # -- one delivery: quiet, the fresh check, the typing, the receipt -----------------

    def _deliver(self, send: Send, now_mode: bool) -> None:
        try:
            self._attempt(send, now_mode)
        except Exception:  # noqa: BLE001 - a delivery that breaks is refused, never retried
            if send.state == DUE:
                self._refuse(send, WHY_FAILED)
        finally:
            with self._lock:
                self._inflight.discard(send.fp.host)

    def _attempt(self, send: Send, now_mode: bool) -> None:
        if send.kind == KIND_INTERRUPT:
            self._attempt_interrupt(send)
            return
        if send.kind == KIND_QUESTION:
            self._attempt_answer(send)
            return
        host = TalkHost(send.fp.host, send.fp.tty)
        if not self._wait_quiet(send, host):
            return
        if self.typist is None or send.fp.tty is None:
            self._refuse(send, WHY_NO_TAB)
            return
        try:
            with self.typist(send.fp.tty):
                if not self._type(send, now_mode):
                    return
                self.sleep(TYPED_SETTLE)
        except StateError:
            if send.state == DUE:
                self._refuse(send, WHY_TYPING)
            return
        self._receipt(send)

    def _wait_quiet(self, send: Send, host: TalkHost, shows: str = SHOWS_INPUT) -> bool:
        """Until the tab has looked the same, its input empty, for
        :data:`QUIET_SECONDS`; busy past :data:`BUSY_LIMIT` refuses (D6).

        The tab must show what the send is typed into (D41): Claude's input
        box for a message, the question for an answer. A question on the tab
        is never quiet for a message; the input box back for an answer means
        the question was answered or dismissed at the Mac."""
        began = self.timer()
        digest: str | None = None
        since = 0.0
        while True:
            try:
                look = self.talk.quiet(host)
            except Exception:  # noqa: BLE001 - a look that fails cannot say quiet
                look = None
            now = self.timer()
            refused = getattr(look, "refused", None)
            if refused in (WHY_NO_TAB, WHY_NO_CLAUDE):
                self._mark(send.fp, refused)
                self._refuse(send, refused)
                return False
            if refused is not None:
                self._refuse(send, WHY_NOT_ALLOWED if refused == WHY_NOT_ALLOWED else WHY_FAILED)
                return False
            readable = look is not None and hasattr(look, "digest") and look.empty is not None
            seen = getattr(look, "shows", SHOWS_INPUT) if readable else None
            if shows == SHOWS_QUESTION and seen == SHOWS_INPUT:
                self._refuse(send, WHY_ANSWERED)
                return False
            if not readable or seen != shows:
                self._mark(send.fp, WHY_UNREADABLE)
                self._refuse(send, WHY_UNREADABLE)
                return False
            if look.empty and digest is not None and look.digest == digest:
                if now - since >= QUIET_SECONDS:
                    return True
            elif look.empty:
                digest, since = look.digest, now
            else:
                digest = None
            if now - began >= BUSY_LIMIT:
                self._refuse(send, WHY_TYPING)
                return False
            self.sleep(QUIET_STEP)

    def _type(self, send: Send, now_mode: bool) -> bool:
        """The fresh check and the typing, under the tab's lock. ``False`` when
        nothing was typed (the send was refused or queued again)."""
        try:
            document = self.fresh()
        except Exception:  # noqa: BLE001 - a machine that cannot be read is not typed into
            self._refuse(send, WHY_FAILED)
            return False
        from .webactions import find_row

        now = fingerprint(find_row(document, send.row))
        if now is None:
            self._refuse(send, WHY_GONE)
            return False
        changed = identity_change(send.fp, now)
        if changed is None and now_mode and (
                (now.waiting, now.since) != (send.fp.waiting, send.fp.since) or not now.ready):
            changed = CHANGED_TURN
        if changed is not None:
            self._refuse(send, WHY_CHANGED, changed)
            return False
        if not now.ready:
            with self._lock:
                send.look = self.timer() + LOOK_AGAIN
            self._queue(send)
            return False
        before = self.attention(send.project, send.fp.host) or {}
        digest = self.talk.digest(send.kind, send.text)
        try:
            code, _ = self.talk.deliver(TalkHost(now.host, now.tty), send.text)
        except Exception:  # noqa: BLE001 - it may have typed: the receipt decides
            code = WHY_FAILED
        if code not in ("typed", WHY_FAILED):
            if code in (WHY_NO_TAB, WHY_NO_CLAUDE, "not_running"):
                why = WHY_NO_CLAUDE if code == WHY_NO_CLAUDE else WHY_NO_TAB
                self._mark(send.fp, why)
                self._refuse(send, why)
            else:
                self._refuse(send, WHY_NOT_ALLOWED if code == WHY_NOT_ALLOWED else WHY_FAILED)
            return False
        with self._lock:
            send.state, send.text = TYPED, ""
            send.typed_at, send.typed_timer = self.clock(), self.timer()
            send.digest, send.before = digest, before.get("received")
            send.reason = None if code == "typed" else WHY_FAILED
            self._typed_turn[send.fp.host] = before.get("turn_since")
        self._record(send)
        return True

    def _receipt(self, send: Send) -> None:
        """The hook's ``received`` within :data:`RECEIPT_SECONDS`, carrying the
        text's digest: delivered; anything else is not confirmed (D3). Where no
        hook has answered, the host's evidence in the window answers (D38): a
        built-in slash command fires no hook."""
        deadline = send.typed_timer + RECEIPT_SECONDS
        while True:
            record = self.attention(send.project, send.fp.host) or {}
            received = record.get("received")
            at = _epoch(received.get("at")) if isinstance(received, dict) else None
            if (isinstance(received, dict) and received != send.before and at is not None
                    and at >= send.typed_at - RECEIPT_SKEW):
                if received.get("hash") == send.digest:
                    self._finish(send, DELIVERED, None)
                else:
                    self._not_confirmed(send, WHY_OTHER_HASH)
                return
            found = self._host_evidence(send)
            if found is not None:
                if found == send.digest:
                    self._finish(send, DELIVERED, None)
                else:
                    self._not_confirmed(send, WHY_OTHER_HASH)
                return
            if self.timer() >= deadline:
                self._not_confirmed(send, WHY_NO_RECEIPT)
                return
            self.sleep(RECEIPT_STEP)

    # -- one answer: quiet, the fresh check and read, the typing, the receipt (D44) -----

    def _attempt_answer(self, send: Send) -> None:
        host = TalkHost(send.fp.host, send.fp.tty, send.fp.pid)
        if not self._wait_quiet(send, host, SHOWS_QUESTION):
            return
        if self.typist is None or send.fp.tty is None:
            self._refuse(send, WHY_NO_TAB)
            return
        try:
            with self.typist(send.fp.tty):
                if not self._answer(send):
                    return
                self.sleep(TYPED_SETTLE)
        except StateError:
            if send.state == DUE:
                self._refuse(send, WHY_TYPING)
            return
        self._receipt(send)

    def _answer(self, send: Send) -> bool:
        """Under the tab's lock: the same window still waits on the question
        the person saw, and a fresh read is the same call; then the answer.
        ``False`` when nothing was typed."""
        try:
            document = self.fresh()
        except Exception:  # noqa: BLE001 - a machine that cannot be read is not typed into
            self._refuse(send, WHY_FAILED)
            return False
        from .webactions import find_row

        now = fingerprint(find_row(document, send.row))
        if now is None:
            self._refuse(send, WHY_GONE)
            return False
        changed = identity_change(send.fp, now)
        if changed is not None:
            self._refuse(send, WHY_CHANGED, changed)
            return False
        if now.waiting != KIND_QUESTION or now.since != send.fp.since:
            self._refuse(send, WHY_ANSWERED)
            return False
        host = TalkHost(now.host, now.tty, now.pid)
        asked = read_question(self.talk, host)
        if asked.unreadable:
            self._mark(send.fp, WHY_UNREADABLE)
            self._refuse(send, WHY_UNREADABLE)
            return False
        if asked.question is None or pending_view(asked) != pending_view(
                QuestionRead(send.question, False)):
            self._refuse(send, WHY_ANSWERED)
            return False
        before = self.attention(send.project, send.fp.host) or {}
        digest = self.talk.answer_digest(asked.question, send.picks)
        typed_at = self.clock()
        try:
            code, _ = self.talk.answer(host, asked.question, send.picks)
        except Exception:  # noqa: BLE001 - it may have typed: the receipt decides
            code = WHY_FAILED
        if code not in ("typed", WHY_FAILED):
            if code == "busy":
                self._refuse(send, WHY_TYPING)
            elif code == WHY_ANSWERED:
                self._refuse(send, WHY_ANSWERED)
            elif code == "unknown_shape":
                self._mark(send.fp, WHY_UNREADABLE)
                self._refuse(send, WHY_UNREADABLE)
            else:
                self._refuse_code(send, code)
            return False
        with self._lock:
            send.state, send.picks = TYPED, ()
            send.typed_at, send.typed_timer = typed_at, self.timer()
            send.digest, send.before = digest, before.get("received")
            send.reason = None if code == "typed" else WHY_FAILED
        self._record(send)
        return True

    # -- one interrupt: the fresh check, one look, the key, the receipt (D35, D36) -------

    def _attempt_interrupt(self, send: Send) -> None:
        if self.typist is None or send.fp.tty is None:
            self._refuse(send, WHY_NO_TAB)
            return
        try:
            with self.typist(send.fp.tty):
                if not self._press(send):
                    return
                self.sleep(TYPED_SETTLE)
        except StateError:
            if send.state == DUE:
                self._refuse(send, WHY_TYPING)
            return
        self._evidence(send)

    def _press(self, send: Send) -> bool:
        """Under the tab's lock: the turn the person saw still runs in the same
        window, no prompt is open, and Claude's input line is empty on one
        look; then the key. ``False`` when nothing was typed."""
        try:
            document = self.fresh()
        except Exception:  # noqa: BLE001 - a machine that cannot be read is not typed into
            self._refuse(send, WHY_FAILED)
            return False
        from .webactions import find_row

        now = fingerprint(find_row(document, send.row))
        if now is None:
            self._refuse(send, WHY_GONE)
            return False
        changed = identity_change(send.fp, now)
        if changed is not None:
            self._refuse(send, WHY_CHANGED, changed)
            return False
        if now.ready:
            self._refuse(send, WHY_ENDED)
            return False
        if now.asking:
            self._refuse(send, WHY_ASKING)
            return False
        if (now.waiting, now.since) != (send.fp.waiting, send.fp.since):
            self._refuse(send, WHY_CHANGED, CHANGED_TURN)
            return False
        host = TalkHost(now.host, now.tty)
        if not self._line_empty(send, host):
            return False
        typed_at = self.clock()
        try:
            code, _ = self.talk.interrupt(host)
        except Exception:  # noqa: BLE001 - it may have typed: the receipt decides
            code = WHY_FAILED
        if code not in ("typed", WHY_FAILED):
            self._refuse_code(send, code)
            return False
        with self._lock:
            send.state = TYPED
            send.typed_at, send.typed_timer = typed_at, self.timer()
            send.digest = self.talk.digest(send.kind, "")
            send.reason = None if code == "typed" else WHY_FAILED
        self._record(send)
        return True

    def _line_empty(self, send: Send, host: TalkHost) -> bool:
        """One look at the tab: Claude's input box is there and its line is
        empty. Text on it would be cleared or sent by the key (D35); a box
        the host does not know refuses without turning Stop off."""
        try:
            look = self.talk.quiet(host)
        except Exception:  # noqa: BLE001 - a look that fails cannot say empty
            look = None
        refused = getattr(look, "refused", None)
        if refused is not None:
            self._refuse_code(send, refused)
            return False
        if look is None or not hasattr(look, "digest") or look.empty is None:
            self._refuse(send, WHY_UNREADABLE)
            return False
        if not look.empty:
            self._refuse(send, WHY_TYPING)
            return False
        return True

    def _refuse_code(self, send: Send, code: str) -> None:
        """Refuse with what a tab or a typer answered; a tab without Claude in
        it is remembered for its process (D29)."""
        if code in (WHY_NO_TAB, WHY_NO_CLAUDE, "not_running"):
            why = WHY_NO_CLAUDE if code == WHY_NO_CLAUDE else WHY_NO_TAB
            self._mark(send.fp, why)
            self._refuse(send, why)
        else:
            self._refuse(send, WHY_NOT_ALLOWED if code == WHY_NOT_ALLOWED else WHY_FAILED)

    def _evidence(self, send: Send) -> None:
        """The host's evidence in the window, within :data:`RECEIPT_SECONDS`:
        the first entry of the person's since the key carrying the input's
        digest is delivered; another is not confirmed, and none is too (D3)."""
        deadline = send.typed_timer + RECEIPT_SECONDS
        while True:
            digest = self._host_evidence(send)
            if digest is not None:
                if digest == send.digest:
                    self._finish(send, DELIVERED, None)
                else:
                    self._not_confirmed(send, WHY_OTHER_HASH)
                return
            if self.timer() >= deadline:
                self._not_confirmed(send, WHY_NO_RECEIPT)
                return
            self.sleep(RECEIPT_STEP)

    def _host_evidence(self, send: Send) -> str | None:
        """The digest of the host's evidence of the send in its window, written
        no earlier than :data:`RECEIPT_SKEW` before the typing; ``None`` when
        there is none or it cannot be read."""
        host = TalkHost(send.fp.host, send.fp.tty, send.fp.pid)
        since = send.typed_at - RECEIPT_SKEW
        try:
            found = self.talk.receipt(host, send.kind, since)
        except Exception:  # noqa: BLE001 - a read that fails is no evidence
            return None
        digest, at = getattr(found, "hash", None), getattr(found, "at", None)
        if isinstance(digest, str) and isinstance(at, (int, float)) and at >= since:
            return digest
        return None

    def _not_confirmed(self, send: Send, why: str) -> None:
        self._finish(send, NOT_CONFIRMED, why)
        if self.alert is not None:
            try:
                self.alert(send.project_name, send.title)
            except Exception:  # noqa: BLE001, S110 - the page says it either way
                pass

    # -- bookkeeping ------------------------------------------------------------------

    def _mark(self, fp: Fingerprint, why: str) -> None:
        with self._lock:
            self._marks[(fp.host, fp.pid)] = why
            while len(self._marks) > FINISHED_KEPT:
                del self._marks[next(iter(self._marks))]

    def _end(self, send: Send, state: str, why: str | None, what: str | None = None) -> None:
        """Under the lock: a send is over. Its text, and an answer's picks, go
        with it."""
        send.state, send.reason, send.what = state, why, what
        send.text, send.picks = "", ()
        send.ended = self.timer()

    def _finish(self, send: Send, state: str, why: str | None, what: str | None = None) -> None:
        with self._lock:
            self._end(send, state, why, what)
        self._record(send)

    def _refuse(self, send: Send, why: str, what: str | None = None) -> None:
        self._finish(send, REFUSED, why, what)

    def _record(self, send: Send) -> None:
        """The send's audit line for its state; one that cannot be written past
        the hold costs the record, never the send (D5)."""
        try:
            self.audit(self._line(send))
        except StateError:
            pass

    def _line(self, send: Send) -> dict[str, Any]:
        line: dict[str, Any] = {
            "event": EVENTS[send.state], "send": send.id, "device": send.device,
            "device_name": send.device_name, "row": send.row, "host": send.fp.host,
            "kind": send.kind, "length": send.length, "hash": send.hash}
        if send.kind == KIND_QUESTION and send.question is not None:
            line["choice"] = send.choice
        if send.reason:
            line["reason"] = send.reason
        if send.what:
            line["what"] = send.what
        return line

    def _trim(self) -> None:
        now = self.timer()
        with self._lock:
            done = [s for s in self._sends.values() if s.ended is not None]
            for send in done:
                if now - send.ended > FINISHED_SHOWN:  # type: ignore[operator]
                    del self._sends[send.id]
            done = [s for s in self._sends.values() if s.ended is not None]
            for send in done[: max(0, len(done) - FINISHED_KEPT)]:
                del self._sends[send.id]


def _no(status: int, error: str, reason: str) -> Reply:
    return Reply(status, {"error": error, "reason": reason})


def _message_problem(text: Any) -> Reply | None:
    """Why a message's text is refused at the tap (D2, D26), or ``None``."""
    if not isinstance(text, str) or not text.strip():
        return _no(HTTPStatus.BAD_REQUEST, "the message is empty; nothing was sent", "empty")
    if len(text) > MESSAGE_CAP:
        return _no(HTTPStatus.BAD_REQUEST, f"the message is longer than {MESSAGE_CAP} "
                                           "characters; nothing was sent", "long")
    if _CONTROL.search(text):
        return _no(HTTPStatus.BAD_REQUEST, "a message carries text and line breaks only; "
                                           "nothing was sent", "control")
    return None


# -- a question's answer (ADR-029 increment 5) ---------------------------------------

#: The most of a question's first line a card carries as its note (D45).
NOTE_CHARS = 200


@dataclass(frozen=True)
class Pick:
    """One question's answer (D42): the indexes of its choices, ascending, and
    an "Other" text, one line, its ends trimmed, or ``None``."""

    choices: tuple[int, ...]
    other: str | None


@dataclass(frozen=True)
class QuestionRead:
    """What a read found the window waiting on: a question the host read, or
    nothing, and whether the read could not say (``unreadable``)."""

    question: Any
    unreadable: bool


def read_question(talk: SessionTalk, host: TalkHost) -> QuestionRead:
    """The question the window waits on now, read through the host; a read
    that raises or answers ``Unreadable`` is unreadable (ruling 11), and a
    pending prompt that is no question is nothing here (increment 6 reads
    permission prompts)."""
    try:
        found = talk.pending(host)
    except Exception:  # noqa: BLE001 - fail closed: the page says it cannot read it
        return QuestionRead(None, True)
    if found is None:
        return QuestionRead(None, False)
    if not hasattr(found, "questions") and hasattr(found, "reason"):
        return QuestionRead(None, True)
    if getattr(found, "kind", None) != KIND_QUESTION:
        return QuestionRead(None, False)
    return QuestionRead(found, False)


def pending_view(asked: QuestionRead | None) -> dict[str, Any] | None:
    """The question as the page is served it: plain words, the host's
    objects never (ADR-009 D17); ``None`` when none waits."""
    if asked is None or (asked.question is None and not asked.unreadable):
        return None
    if asked.question is None:
        return {"kind": KIND_QUESTION, "unreadable": True}
    question = asked.question
    other = bool(getattr(question, "other", True))
    return {"kind": KIND_QUESTION, "id": str(question.id), "unreadable": False,
            "questions": [{"text": q.text, "header": q.header, "multi": bool(q.multi),
                           "other": other,
                           "choices": [{"label": c.label, "description": c.description}
                                       for c in q.choices]}
                          for q in question.questions]}


def first_line(asked: QuestionRead | None) -> str | None:
    """The waiting question's first line, a card's note (D45)."""
    if asked is None or asked.question is None or not asked.question.questions:
        return None
    lines = asked.question.questions[0].text.strip().splitlines()
    return lines[0][:NOTE_CHARS] if lines else None


def picks(question: Any, answers: Any) -> tuple[Pick, ...] | str:
    """The answer the page sent, checked against the question the read saw
    (D2, D42): one entry per question, each ``{choices: [index...], other}``.
    A single-select question takes one choice or an "Other" text; a
    multi-select one any number of distinct choices and an "Other", at least
    one in all. "Other" is one line of text. Anything else is why not."""
    asked = list(getattr(question, "questions", ()) or ())
    if not isinstance(answers, list) or len(answers) != len(asked) or not asked:
        return "the answer does not match the question"
    found: list[Pick] = []
    for q, given in zip(asked, answers):
        if not isinstance(given, dict):
            return "the answer does not match the question"
        choices = given.get("choices", [])
        other = given.get("other")
        if not isinstance(choices, list) or any(
                not isinstance(c, int) or isinstance(c, bool) or not 0 <= c < len(q.choices)
                for c in choices) or len(set(choices)) != len(choices):
            return "a choice the question does not offer"
        if other is not None:
            if not isinstance(other, str) or not other.strip():
                return "an empty answer"
            if len(other) > MESSAGE_CAP:
                return f"an answer longer than {MESSAGE_CAP} characters"
            if _CONTROL.search(other) or "\n" in other:
                return "an answer is one line of text"
            other = other.strip()
        count = len(choices) + (other is not None)
        if count == 0 or (not q.multi and count != 1):
            return "one answer per question" if not q.multi else "an empty answer"
        found.append(Pick(tuple(sorted(choices)), other))
    return tuple(found)


def _choice_line(question: Any, chosen: tuple[Pick, ...]) -> list[list[int]]:
    asked = list(getattr(question, "questions", ()) or ())
    return [list(p.choices) + ([len(q.choices)] if p.other is not None else [])
            for q, p in zip(asked, chosen)]


def _picks_hash(chosen: tuple[Pick, ...]) -> str:
    """The audit line's ``hash`` of an answer (D5): of its indexes and its
    "Other" texts, as typed."""
    canonical = json.dumps([[list(p.choices), p.other] for p in chosen], ensure_ascii=False)
    return text_hash(canonical)


# -- for tests --------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordedLook:
    """A look the recorder answers: quiet and empty unless told otherwise."""

    digest: str | None = "same"
    empty: bool | None = True
    refused: str | None = None
    shows: str = SHOWS_INPUT


@dataclass(frozen=True)
class RecordedReceipt:
    """Evidence the recorder answers ``receipt`` with: a digest and its time."""

    hash: str
    at: float


class RecordingTalk:
    """A ``SessionTalk`` that reads what it is given and types nothing.

    The suite installs one for every test in place of ``_oracle_talk``
    (ADR-029 D4): ``typed`` keeps each ``(host, tty, text)`` it was asked to
    type, ``keys`` each ``(host, tty)`` it was asked to interrupt, and
    ``code`` is what typing either answers; ``receipt`` hands out ``receipts`` in
    order. ``questions`` is what ``pending`` reads per host id, and
    ``answered`` keeps each ``(host, tty, picks)`` it was asked to type,
    answering ``answer_code``; a window with a question shows it on a look.
    ``osascript`` is never run.
    """

    def __init__(self) -> None:
        self.conversations: dict[str, Any] = {}
        self.asked: list[str] = []
        self.looks: list[Any] = []
        self.typed: list[tuple[str, str | None, str]] = []
        self.keys: list[tuple[str, str | None]] = []
        self.receipts: list[Any] = []
        self.code = "typed"
        self.refusal: str | None = None
        self.questions: dict[str, Any] = {}
        self.answered: list[tuple[str, str | None, tuple[Pick, ...]]] = []
        self.answer_code = "typed"

    def conversation(self, host: TalkHost) -> Any:
        self.asked.append(host.host_id)
        return self.conversations.get(host.host_id, _EMPTY)

    def quiet(self, host: TalkHost) -> Any:
        if self.looks:
            return self.looks.pop(0)
        asking = self.questions.get(host.host_id) is not None
        return RecordedLook(shows=SHOWS_QUESTION if asking else SHOWS_INPUT)

    def deliver(self, host: TalkHost, text: str) -> tuple[str, str]:
        self.typed.append((host.host_id, host.tty, text))
        return self.code, ""

    def digest(self, kind: str, text: str) -> str:
        return text_hash(KIND_INTERRUPT if kind == KIND_INTERRUPT else text.strip())

    def refuses(self, kind: str, text: str) -> str | None:
        return self.refusal

    def interrupt(self, host: TalkHost) -> tuple[str, str]:
        self.keys.append((host.host_id, host.tty))
        return self.code, ""

    def receipt(self, host: TalkHost, kind: str, since: float) -> Any:
        return self.receipts.pop(0) if self.receipts else None

    def pending(self, host: TalkHost) -> Any:
        return self.questions.get(host.host_id)

    def answer(self, host: TalkHost, pending: Any, picks: Sequence[Pick]) -> tuple[str, str]:
        self.answered.append((host.host_id, host.tty, tuple(picks)))
        return self.answer_code, ""

    def answer_digest(self, pending: Any, picks: Sequence[Pick]) -> str:
        return text_hash(json.dumps([getattr(pending, "id", None),
                                     [[list(p.choices), p.other] for p in picks]]))


@dataclass(frozen=True)
class _Nothing:
    entries: tuple[Any, ...] = ()
    unreadable: Any = None


_EMPTY = _Nothing()
