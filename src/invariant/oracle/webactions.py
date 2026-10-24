"""The web page's confirm-first actions: a transport over ``oracle/actions``.

The rules are the TUI's (ADR-007 D18): ``step_for`` turns a row into a step,
``refusal`` says why it may not run, ``confirm_lines`` (``Actions.describe``,
with the launch previewed once) is the confirm text and ``Actions.perform`` is
the dispatch. Nothing here adds a rule about what may run. What a confirm from
another device needs on top is here (ADR-008 D8–D12):

* **The server decides what runs.** The page names a row by its stable key
  (:func:`~invariant.oracle.scan.row_key`) and nothing else. The server reads
  the machine afresh, finds that row, and derives the step itself, for the
  confirm and again for the action. An ``argv``, ``cwd`` or environment sent
  by a client is never read.
* **A confirm is a nonce** (:class:`Confirms`). It is issued with the confirm
  view, bound to the device and the row, lives :data:`CONFIRM_TTL` seconds,
  is kept only in memory, and is spent by the first action that presents it,
  whoever presents it. It also remembers what it agreed to run
  (``command_of``): a step that changed in between is not run.
* **One action at a time per row** (:meth:`RemoteActions.act`). A second one
  on a row whose first is still running is refused, and at most
  :data:`MAX_RUNNING` run at once. Each runs in a thread of its own; the page
  polls for its result.
* **Every action is audited** (:class:`AuditLog`): a line when it starts and a
  line when it ends, in ``<oracle home>/web/actions.log``, owner-only from
  creation. An action that cannot be recorded does not run.

The confirm and every job also carry their words from ``oracle/panel``: the
sheet's title, sentence and deed, and the result's headline (ADR-009 D3, D6).
A failed job's "Why" is :func:`summarise`'s: it reads the output for a line
invariant itself wrote and for the last meaningful line, and the panel words
them. It names a cause only from invariant's own line, and never guesses one
(ADR-009 D9). The output itself is kept as it was, behind "Show full output".

The oracle never answers a permission prompt, and neither does this: there is
no route to one, and ``refusal`` refuses a step on a row that waits on one.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any

from ..core.errors import StateError
from . import panel
from .actions import (
    Actions,
    Outcome,
    Step,
    command_of,
    confirm_lines,
    refusal,
    step_for,
    terminal_request,
)
from .devices import Device
from .home import append_private
from .recommend import INFO, TERMINAL
from .scan import row_key

#: The audit log, under the oracle home. A record, not derived state.
AUDIT_FILE = "web/actions.log"

#: How much of the audit log's end :meth:`AuditLog.last` reads: many lines.
TAIL_BYTES = 8192

#: Seconds a confirm view's nonce stays good.
CONFIRM_TTL = 120.0

#: Nonces kept at once; the oldest is dropped first.
MAX_PENDING = 64

#: Actions running at once, across every row.
MAX_RUNNING = 4

#: Finished actions remembered for the page.
MAX_KEPT = 20

#: Characters of a command's output kept: its tail, where the verdict is.
MAX_OUTPUT = 65536

#: Characters of a reason's quoted line; a longer one ends in "…".
MAX_REASON = 160

#: How invariant's CLI starts the line it writes for an error it raised.
OWN_PREFIX = "invariant: "

RUNNING = "running"
DONE = "done"


@dataclass(frozen=True)
class Answer:
    """An HTTP status and the JSON body that goes with it."""

    status: int
    body: dict[str, Any]


def find_row(document: dict[str, Any], key: str) -> dict[str, Any] | None:
    """The one row of ``document`` named ``key``; ``None`` for none or several."""
    found = [
        row
        for project in document.get("projects", [])
        for row in project.get("rows", [])
        if row_key(row) == key
    ]
    return found[0] if len(found) == 1 else None


def start_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True, name="oracle-web-action").start()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _tail(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return f"[... {len(text) - MAX_OUTPUT} characters truncated ...]\n" + text[-MAX_OUTPUT:]


# -- the failure summary (ADR-009 D9) ------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_TRACEBACK = "Traceback (most recent call last):"
_CHAINED = ("During handling of the above exception", "The above exception was the direct")
_FRAME = re.compile(r'^\s+File "|^\s*[\^~]+\s*$')
_PROGRESS = re.compile(
    r"[█▉▊▋▌▍▎▏━░▒▓]{3,}"  # a bar drawn in blocks
    r"|\[[=#>\-. ]{5,}\]"  # [=====>    ]
    r"|\[\s*\d{1,3}%\]\s*$"  # pytest's trailing [ 50%]
    r"|^\s*\d{1,3}%\|"  # tqdm's 45%|
)
_RULE = re.compile(r"^[=\-_*~]+$")
_RULED = re.compile(r"^[=\-_*]{3,}\s*(.*?)\s*[=\-_*]{3,}$")


@dataclass(frozen=True)
class Evidence:
    """What a job's output states, for its reason. Read, never inferred."""

    #: The message of the last line invariant wrote (``invariant: <message>``).
    own: str | None
    #: The last line that is not blank, a traceback frame, a progress bar or a rule.
    last: str | None
    #: Whether the output holds anything but whitespace.
    printed: bool = True


def _clip(text: str) -> str:
    return text if len(text) <= MAX_REASON else text[: MAX_REASON - 1].rstrip() + "…"


def _lines(output: str) -> list[str]:
    """The output's lines as a terminal left them: colour gone, ``\\r`` overwrites applied."""
    lines = []
    for raw in output.split("\n"):
        parts = [p for p in _ANSI.sub("", raw).split("\r") if p.strip()]
        lines.append(parts[-1].rstrip() if parts else "")
    return lines


def evidence(output: str) -> Evidence:
    """The lines of ``output`` a reason may quote. Pure."""
    own = last = None
    in_traceback = False
    for line in _lines(output):
        if line.startswith(OWN_PREFIX) and line[len(OWN_PREFIX):].strip():
            own = line[len(OWN_PREFIX):].strip()
        if line.startswith(_TRACEBACK):
            in_traceback = True
            continue
        if in_traceback and line[:1].isspace():
            continue
        in_traceback = False
        text = line.strip()
        if (not text or _FRAME.match(line) or line.startswith(_CHAINED)
                or _PROGRESS.search(text) or _RULE.match(text)):
            continue
        ruled = _RULED.match(text)
        last = ruled[1] if ruled and ruled[1] else text
    return Evidence(own and _clip(own), last and _clip(last), bool(output.strip()))


def summarise(ok: bool | None, status: int | None, output: str) -> str:
    """A finished job's one plain line of reason; nothing for one that succeeded."""
    if ok:
        return ""
    found = evidence(output)
    return panel.why_words(status, found.own, found.last, found.printed)


# -- the nonce book ------------------------------------------------------------


@dataclass(frozen=True)
class Confirm:
    nonce: str
    device: str
    row: str
    command: tuple[Any, ...]
    expires: float


class Confirms:
    """The confirms issued and not yet spent. In memory only."""

    def __init__(
        self,
        clock: Callable[[], float],
        token: Callable[[], str] | None = None,
        ttl: float = CONFIRM_TTL,
        cap: int = MAX_PENDING,
    ) -> None:
        self.clock = clock
        self.token = token or (lambda: secrets.token_urlsafe(24))
        self.ttl = ttl
        self.cap = cap
        self._book: dict[str, Confirm] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._book)

    def issue(self, device: str, row: str, step: Step) -> Confirm:
        now = self.clock()
        confirm = Confirm(self.token(), device, row, command_of(step), now + self.ttl)
        with self._lock:
            for nonce in [n for n, c in self._book.items() if c.expires <= now]:
                del self._book[nonce]
            while len(self._book) >= self.cap:
                del self._book[next(iter(self._book))]
            self._book[confirm.nonce] = confirm
        return confirm

    def spend(self, nonce: str, device: str, row: str) -> tuple[tuple[Any, ...] | None, str | None]:
        """What the confirm agreed to run, or why it is no good. Spent either way."""
        with self._lock:
            confirm = self._book.pop(nonce, None) if nonce else None
        if confirm is None:
            return None, "this confirm is unknown or already used; nothing ran. Open it again"
        if confirm.expires <= self.clock():
            return None, "this confirm expired; nothing ran. Open it again"
        if confirm.device != device:
            return None, "this confirm was issued to another device; nothing ran"
        if confirm.row != row:
            return None, "this confirm is for another row; nothing ran"
        return confirm.command, None


# -- the audit log -------------------------------------------------------------


class AuditLog:
    """``<oracle home>/web/actions.log``: one JSON line per start and per end.

    It records what the page ran, which no other state can tell again, so it
    is a record and lives in ``web/`` beside the paired devices, not in the
    watcher's derived ``oracle/``. Only :meth:`last` reads it back, for
    ``oracle status`` and ``doctor`` (ADR-008 D17). It holds no token, nonce,
    environment or output.
    """

    def __init__(self, home: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(home) / AUDIT_FILE
        self.clock = clock

    def record(self, entry: dict[str, Any]) -> None:
        line = {"at": _iso(self.clock()), **entry}
        append_private(self.path, json.dumps(line, sort_keys=True) + "\n")

    def last(self) -> dict[str, Any] | None:
        """The last whole line, or ``None`` when there is none to read.

        Reads only the file's tail, so a long log costs nothing. A line cut
        short by a killed write is skipped for the one before it.
        """
        try:
            with self.path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - TAIL_BYTES))
                tail = handle.read()
        except OSError:
            return None
        for line in reversed(tail.splitlines()):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                return entry
        return None


# -- the actions ---------------------------------------------------------------


@dataclass
class Job:
    """One action from the page, running or done."""

    id: str
    device: str
    row: str
    subject: str
    kind: str
    argv: tuple[str, ...]
    cwd: str | None
    started: float
    remote_control: str | None = None
    state: str = RUNNING
    ok: bool | None = None
    status: int | None = None
    output: str = ""
    elapsed: float | None = None
    audit_error: str = ""
    why: str = ""
    _began: float = field(default=0.0, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """The job as the page reads it, with its outcome in the panel's words."""
        body = {
            "id": self.id, "row": self.row, "subject": self.subject, "kind": self.kind,
            "argv": list(self.argv), "cwd": self.cwd, "started": self.started,
            "remote_control": self.remote_control, "state": self.state, "ok": self.ok,
            "status": self.status, "output": self.output, "elapsed": self.elapsed,
            "audit_error": self.audit_error,
        }
        body["words"] = {**panel.result_words(body), "why": self.why}
        return body


class RemoteActions:
    """The confirm view and the action behind the page, on ``Actions``."""

    def __init__(
        self,
        actions: Actions,
        fresh: Callable[[], dict[str, Any]],
        audit: AuditLog,
        clock: Callable[[], float] = time.time,
        timer: Callable[[], float] = time.monotonic,
        spawn: Callable[[Callable[[], None]], None] = start_thread,
        token: Callable[[], str] | None = None,
        ttl: float = CONFIRM_TTL,
    ) -> None:
        self.actions = actions
        #: A fresh read of the machine: the ``assess`` document, scanned now.
        self.fresh = fresh
        self.audit = audit
        self.clock = clock
        self.timer = timer
        self.spawn = spawn
        self.token = token or (lambda: secrets.token_urlsafe(12))
        self.confirms = Confirms(clock, self.token, ttl)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def _read(self, key: str) -> tuple[dict[str, Any] | None, str | None]:
        try:
            document = self.fresh()
        except Exception as exc:  # noqa: BLE001 - shown to the page; the next read runs
            return None, f"the machine could not be read ({type(exc).__name__}: {exc})"
        return find_row(document, key), None

    def confirm(self, device: Device, key: str) -> Answer:
        """What the confirm view shows for row ``key``, and its nonce if it may run.

        ``lines`` are the TUI's confirm, word for word; ``words`` are the same
        step in the panel's words, for the bottom sheet (ADR-009 D6).
        """
        body: dict[str, Any] = {"row": key, "nonce": None, "refused": None, "lines": [],
                                "kind": None, "subject": "", "expires_in": None,
                                "words": None}
        row, failed = self._read(key)
        if failed:
            return Answer(HTTPStatus.SERVICE_UNAVAILABLE, {**body, "refused": failed})
        if row is None:
            return Answer(HTTPStatus.NOT_FOUND, {
                **body, "refused": "that row is not there any more; the page will catch up"})
        step = step_for(row)
        if step is None:
            return Answer(HTTPStatus.OK, {**body, "refused": "nothing is recommended for this row"})
        body.update(kind=step.kind, subject=step.subject)
        reason = refusal(step, row)
        if reason:
            return Answer(HTTPStatus.OK, {**body, "refused": reason})
        if step.kind == INFO:
            return Answer(HTTPStatus.OK, {**body, "lines": [t for t in (step.label, step.detail) if t]})
        launch = self.actions.launch_line(step)
        lines = confirm_lines(step, launch)
        words = panel.confirm_words(row, row.get("project_name") or "", step.argv, step.kind,
                                    step.cwd, step.env, launch)
        if step.kind == TERMINAL:
            name, _ = terminal_request(step)
            lines.append(
                f"the window opens on the Mac whether or not anyone is at it; pick the "
                f"session up from Claude Code's Remote Control as {name}"
            )
        confirm = self.confirms.issue(device.id, key, step)
        return Answer(HTTPStatus.OK, {**body, "lines": lines, "nonce": confirm.nonce,
                                      "expires_in": self.confirms.ttl, "words": words})

    def act(self, device: Device, key: str, nonce: str) -> Answer:
        """Run row ``key``'s step, which ``nonce`` confirmed, if it still may."""
        command, why = self.confirms.spend(nonce, device.id, key)
        if why:
            return _refused(HTTPStatus.FORBIDDEN, why)
        row, failed = self._read(key)
        if failed:
            return _refused(HTTPStatus.SERVICE_UNAVAILABLE, f"{failed}; nothing ran")
        if row is None:
            return _refused(HTTPStatus.CONFLICT, "that row is not there any more; nothing ran")
        step = step_for(row)
        if step is None:
            return _refused(HTTPStatus.CONFLICT,
                            "nothing is recommended for this row any more; nothing ran")
        reason = refusal(step, row)
        if reason:
            return _refused(HTTPStatus.CONFLICT, reason)
        if step.kind == INFO:
            return _refused(HTTPStatus.CONFLICT, "an info step has no action; nothing ran")
        if command_of(step) != command:
            return _refused(HTTPStatus.CONFLICT,
                            "the recommended step changed since it was confirmed; nothing "
                            "ran. Open it again to see what would run")
        job = self._reserve(device, key, step)
        if isinstance(job, str):
            return _refused(HTTPStatus.CONFLICT, job)
        try:
            self.audit.record(self._entry("start", job, device))
        except StateError as exc:
            with self._lock:
                del self._jobs[job.id]
            return _refused(HTTPStatus.SERVICE_UNAVAILABLE,
                            f"the action could not be recorded in the audit log ({exc}); "
                            f"nothing ran")
        self.spawn(lambda: self._run(job, step, device))
        return Answer(HTTPStatus.ACCEPTED, {"job": self.job(device, job.id)})

    def _reserve(self, device: Device, key: str, step: Step) -> Job | str:
        with self._lock:
            running = [j for j in self._jobs.values() if j.state == RUNNING]
            if any(j.row == key for j in running):
                return "an action on this row is already running; wait for its result"
            if len(running) >= MAX_RUNNING:
                return f"{len(running)} actions are already running; wait for one to finish"
            name = terminal_request(step)[0] if step.kind == TERMINAL else None
            job = Job(self.token(), device.id, key, step.subject, step.kind, step.argv,
                      step.cwd, self.clock(), remote_control=name, _began=self.timer())
            self._jobs[job.id] = job
            done = [j for j in self._jobs.values() if j.state == DONE]
            for old in done[: max(0, len(self._jobs) - MAX_KEPT)]:
                del self._jobs[old.id]
        return job

    def _run(self, job: Job, step: Step, device: Device) -> None:
        try:
            outcome = self.actions.perform(step)
        except Exception as exc:  # noqa: BLE001 - the page shows it; the server goes on
            outcome = Outcome(False, f"{type(exc).__name__}: {exc}")
        with self._lock:
            job.state, job.ok, job.status = DONE, outcome.ok, outcome.status
            job.output = _tail(outcome.text)
            job.why = summarise(outcome.ok, outcome.status, job.output)
            job.elapsed = round(self.timer() - job._began, 3)
        try:
            self.audit.record(self._entry("done", job, device))
        except StateError as exc:
            job.audit_error = f"the audit line for its end could not be written: {exc}"

    @staticmethod
    def _entry(event: str, job: Job, device: Device) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "event": event, "job": job.id, "device": device.id, "device_name": device.name,
            "row": job.row, "kind": job.kind, "argv": list(job.argv), "cwd": job.cwd,
        }
        if event == DONE:
            entry.update(exit=job.status, ok=job.ok, elapsed=job.elapsed)
        return entry

    def job(self, device: Device, job_id: str) -> dict[str, Any] | None:
        """One action's state, for the device that started it only."""
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict() if job is not None and job.device == device.id else None

    def recent(self, device: Device) -> list[dict[str, Any]]:
        """The device's remembered actions, newest first."""
        with self._lock:
            return [j.to_dict() for j in reversed(self._jobs.values()) if j.device == device.id]


def _refused(status: int, error: str) -> Answer:
    return Answer(status, {"error": error})
