"""Reading Claude Code's own record of what it spent.

Separate from ``claude_code`` because it is a different relationship with the
same host: that module *drives* the host through hooks and installed assets,
this one *reads back* what the host already wrote down. Both are host-specific
knowledge and neither belongs anywhere else, but a reader has no business in a
module about hook payload schemas.

Three facts make this worth having rather than instrumenting the hooks:

* Hooks never see a token count. Not "not yet" — the payload has no usage in
  it at all, so a hook-only design could report which tools ran and never what
  they cost.
* The transcripts are already on disk, so a store built on them starts with
  history instead of accumulating it from the day it shipped.
* The host session id is the join key ``domains/session`` already records, so
  the two records line up without anything new being written down.

What is **not** here is any promise of stability. This is an undocumented
on-disk format belonging to another program. Every field access below tolerates
absence, every record that cannot be read is skipped, and no input reaches an
exception the caller has to handle: the cost of that format changing is a gap
in a report, never a broken session. That is ADR-002's consequence stated as
code, and ``tests/test_host_telemetry.py`` is mostly malformed input for
exactly this reason.

This module implements ``core.stats.SampleSource`` and imports nothing but
``core``. The arrow points down (ADR-002 D2): ``core`` and ``domains`` never
import ``hosts``, and the CLI is what injects this class into the domain.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..core import git
from ..core.stats import Sample, SourceRef, Usage

#: Where the host keeps its per-project transcripts. Overridable so a test can
#: point at a fixture tree instead of a home directory — and so that nothing in
#: this library has to write a home path down.
CLAUDE_HOME_ENV = "CLAUDE_CONFIG_DIR"

#: A response the host synthesised locally rather than paying for. Skipped
#: rather than left unpriced: a single one would otherwise make its whole
#: session unpriceable, which is the correct handling of an *unknown* model and
#: the wrong handling of one known to be free.
SYNTHETIC_MODEL = "<synthetic>"

#: What the host puts in place of a path separator when it names a project
#: directory after the path it was run from. The mangling is **lossy** — it is
#: also what the host puts in place of a leading dot — which is the whole
#: difficulty in :func:`_under_origin` below.
PATH_MARK = "-"

#: The two record types that are a conversational turn. Everything else in a
#: transcript — ``last-prompt``, ``cost-state``, ``bridge-session``,
#: ``system``, ``attachment`` and whatever the host adds next — is bookkeeping,
#: and the host appends it long after the conversation has gone quiet (BUG_078).
TURN_TYPES = ("user", "assistant")

#: How much :func:`last_turn` reads per step, from the end of the file. The
#: newest turn is nearly always within the last few kilobytes.
TAIL_BLOCK_BYTES = 32 << 10

#: How much :func:`last_turn` reads at most before giving up. A transcript's
#: tail can be one enormous line (a tool result holding a whole file), so the
#: bound is what keeps a scan of every transcript cheap.
TAIL_BUDGET_BYTES = 2 << 20

#: The most text a :class:`TranscriptTurn` carries, in characters.
TURN_TEXT_CAP = 4000


def claude_home() -> Path:
    return Path(os.environ.get(CLAUDE_HOME_ENV) or (Path.home() / ".claude"))


def _parse_timestamp(raw) -> float | None:
    """A time, or ``None``. Never an exception, and never a reason to skip.

    A record whose time will not parse still spent tokens. The prior-art sketch
    dropped it, which kept the timing tidy at the cost of silently
    under-counting the one thing here that is actually measured.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _usage_from(raw: dict) -> Usage:
    """Read one message's usage block.

    ``cache_creation`` splits the write into 5-minute and 1-hour buckets, which
    price at 1.25x and 2x. Older records carry only the flat
    ``cache_creation_input_tokens``; those are attributed to the 5-minute
    bucket, which under-prices a long-lived cache rather than over-pricing a
    short one — the direction that reports a surprise rather than hiding one.
    """
    creation = raw.get("cache_creation")
    if not isinstance(creation, dict):
        creation = {}
    write_1h = creation.get("ephemeral_1h_input_tokens")
    write_5m = creation.get("ephemeral_5m_input_tokens")
    if not creation:
        write_5m = raw.get("cache_creation_input_tokens")
    return Usage.from_dict(
        {
            "input": raw.get("input_tokens"),
            "output": raw.get("output_tokens"),
            "cache_read": raw.get("cache_read_input_tokens"),
            "cache_write_5m": write_5m,
            "cache_write_1h": write_1h,
        }
    )


def _blocks(message: dict) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Tool names and subagent launches, from whatever shape ``content`` is."""
    tools: list[str] = []
    agents: list[tuple[str, str]] = []
    content = message.get("content")
    if not isinstance(content, list):
        return (), ()
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name")
        if not name or not isinstance(name, str):
            continue
        tools.append(name)
        if name != "Agent":
            continue
        payload = block.get("input")
        if not isinstance(payload, dict):
            payload = {}
        agents.append(
            (
                str(payload.get("subagent_type") or "unspecified"),
                str(payload.get("model") or "inherited"),
            )
        )
    return tuple(tools), tuple(agents)


def read_transcript(path: Path) -> Iterator[Sample]:
    """Yield one :class:`Sample` per billed model response in ``path``.

    One bad line never costs the good ones: parsing is per record, and a record
    that fails any check is skipped without touching the iteration.
    """
    path = Path(path)
    fallback_id = path.stem
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        # Absent, a directory, unreadable — all the same answer. A report with
        # one session missing beats a command that raised.
        return
    with handle:
        for line in handle:
            sample = _sample_from(line, fallback_id)
            if sample is not None:
                yield sample


def _sample_from(line: str, fallback_id: str) -> Sample | None:
    try:
        record = json.loads(line)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(record, dict):
        return None

    message = record.get("message")
    if not isinstance(message, dict):
        return None
    usage_raw = message.get("usage")
    if not isinstance(usage_raw, dict):
        return None
    model = message.get("model")
    if not model or not isinstance(model, str) or model == SYNTHETIC_MODEL:
        return None

    tools, agents = _blocks(message)
    session_id = record.get("sessionId")
    return Sample(
        host_session_id=str(session_id) if session_id else fallback_id,
        timestamp=_parse_timestamp(record.get("timestamp")),
        model=model,
        usage=_usage_from(usage_raw),
        tools=tools,
        agents=agents,
    )


@dataclass(frozen=True)
class TranscriptTurn:
    """One conversational entry: who spoke, when, and what was said.

    ``text`` is the message's text blocks joined by a blank line (a ``user``
    entry's plain-string content is its text), capped at the reader's cap;
    ``truncated`` says the cap cut it. Thinking, tool use and tool results are
    not text, so a ``user`` entry that only hands back a tool result has
    ``text == ""`` and is still a turn: a tool finishing is the host working.
    ``sidechain`` marks a subagent's entry, which is also real work.
    """

    role: str
    timestamp: float
    text: str
    truncated: bool
    sidechain: bool


def _text_of(message, cap: int) -> tuple[str, bool]:
    if not isinstance(message, dict):
        return "", False
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
    else:
        return "", False
    return text[:cap], len(text) > cap


def _turn_from(raw: bytes, cap: int) -> TranscriptTurn | None:
    """The turn on one complete line, or ``None`` for anything else.

    Bytes that are not UTF-8 are replaced, not refused: a stray byte inside a
    message's text leaves its type and timestamp readable, and a line that is
    garbage throughout fails the JSON parse and is skipped like any other.
    """
    try:
        record = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    if not isinstance(record, dict) or record.get("type") not in TURN_TYPES:
        return None
    timestamp = _parse_timestamp(record.get("timestamp"))
    if timestamp is None:
        return None
    text, truncated = _text_of(record.get("message"), cap)
    return TranscriptTurn(
        role=record["type"],
        timestamp=timestamp,
        text=text,
        truncated=truncated,
        sidechain=record.get("isSidechain") is True,
    )


def last_turn(
    path: Path,
    accept: Callable[[TranscriptTurn], bool] | None = None,
    *,
    block_size: int = TAIL_BLOCK_BYTES,
    budget: int = TAIL_BUDGET_BYTES,
    cap: int = TURN_TEXT_CAP,
) -> TranscriptTurn | None:
    """The newest timestamped ``user`` or ``assistant`` entry in ``path``.

    Read backwards from the end in blocks of ``block_size``, so the cost is the
    tail and not the file. A line split across blocks is joined before it is
    parsed. ``accept`` narrows the search (the newest entry with text, say);
    without it any turn answers.

    Three things are never guessed at:

    * the bytes after the file's last newline are a line still being written,
      and are skipped even when they would parse;
    * a line that does not parse, or is not a timestamped turn, is skipped;
    * no more than ``budget`` bytes are read. A turn further back than that,
      or behind one line longer than that, is not found.

    ``None`` means no turn was found: the file is missing, empty, unreadable,
    holds no turn, or hides it beyond the budget. The caller picks the
    fallback; this reader does not.
    """
    try:
        with Path(path).open("rb") as handle:
            return _scan_tail(handle, accept, block_size, budget, cap)
    except OSError:
        return None


def _scan_tail(handle, accept, block_size: int, budget: int, cap: int) -> TranscriptTurn | None:
    position = os.fstat(handle.fileno()).st_size
    pending = b""
    in_open_tail = True
    spent = 0
    while position > 0 and spent < budget:
        size = min(block_size, position, budget - spent)
        position -= size
        spent += size
        handle.seek(position)
        lines = (handle.read(size) + pending).split(b"\n")
        # The first piece runs on into the block before, unless this block
        # starts the file, where the file's first line is complete.
        pending = b"" if position == 0 else lines.pop(0)
        if in_open_tail:
            if not lines:
                pending = b""
                continue
            lines.pop()
            in_open_tail = False
        for raw in reversed(lines):
            found = _turn_from(raw, cap)
            if found is not None and (accept is None or accept(found)):
                return found
    return None


def _watermark(path: Path) -> str:
    """A content fingerprint, deliberately not a modification time.

    ADR-002 D5 keys the raw store on a watermark of the source. A stored mtime
    would be a stored clock time, which the project forbids, and it answers the
    wrong question anyway: the cache asks "has this changed", not "when".

    Hashing is also the cheap half. Validating a cached record costs one pass
    of SHA-256 over the file; rebuilding it costs a JSON parse of every line.
    """
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        # Unreadable is a state, not an error. It gets a watermark that differs
        # from any readable content, so a file that becomes readable later
        # invalidates the cache exactly as a changed one would.
        return "unreadable"
    return digest.hexdigest()


def _resolves_under(base: Path, parts: list[str]) -> bool:
    """Do ``parts`` name a directory that really exists under ``base``?

    The mangling joins path segments with a character that may also appear
    *inside* a segment, so ``a-b`` is either one directory or two and the name
    cannot say which. The filesystem can, so this walks it, trying every way of
    re-joining the leading segments before descending into the match.

    ``Path.is_dir`` answers ``False`` rather than raising for a path it cannot
    stat, so a directory this process may not read narrows the scan instead of
    reaching the caller — which is the rule the whole module is written to.
    """
    if not parts:
        return True
    for cut in range(1, len(parts) + 1):
        child = base / PATH_MARK.join(parts[:cut])
        if child.is_dir() and _resolves_under(child, parts[cut:]):
            return True
    return False


def _under_origin(name: str, prefix: str, origin: Path) -> bool:
    """Is this project directory the origin's own, or one below it?

    A *worktree* of the project lives at a path under the origin, so its
    mangled name extends the origin's and it belongs to the project. A
    *sibling checkout* whose directory name merely starts with the origin's
    extends it in exactly the same way and does not: ``<root>/app/v2`` and
    ``<root>/app-v2`` mangle to one string. No reading of the name alone can
    tell them apart (BUG_015), so two other things are asked, in the order of
    what they prove:

    * **the filesystem** — an extension that names a directory really present
      under the origin is a path under the origin;
    * **the host's own convention** — a hidden directory. ``.claude`` is where
      this library puts session worktrees, and a worktree is *removed at
      teardown*, so its transcript outlives the directory it was recorded in.
      Absence from disk proves nothing about that case, which is why it is not
      left to the check above.

    Anything else is a different project. That is the deliberate direction:
    counting a sibling's spend inflates this project's totals with no signal
    in the output at all, while a session left out is a session the report
    never claims to have measured.
    """
    if name == prefix:
        return True
    if not name.startswith(prefix + PATH_MARK):
        return False
    remainder = name[len(prefix) + len(PATH_MARK):]
    # `/.claude` mangles to `-.claude` here and to `--claude` in the host's own
    # naming; both say the same thing, that the next segment is hidden.
    if remainder.startswith((".", PATH_MARK)):
        return True
    return _resolves_under(origin, remainder.split(PATH_MARK))


class ClaudeCodeTranscripts:
    """``core.stats.SampleSource`` over the host's per-project transcripts.

    ``origin`` narrows the scan to one project tree. The host names a project
    directory after its path with separators replaced, and a session worktree
    is a different directory and therefore a different project — so the scan
    keeps the project's worktrees as well as the project itself, which is what
    keeps their sessions attributed to it instead of scattered across entries
    nobody thinks to look in.

    Which directories those are is the whole difficulty, and it is answered in
    two places: :meth:`roots`, for the worktrees git knows about, and
    :func:`_under_origin`, for the paths below a root. Neither is as simple as
    it looks, because the host's naming is lossy.
    """

    def __init__(self, origin: Path | None = None, home: Path | None = None) -> None:
        self.origin = Path(origin) if origin is not None else None
        self._home = Path(home) if home is not None else None
        self._roots: list[tuple[str, Path]] | None = None
        self._turns: dict[Path, tuple[tuple[int, int], float | None]] = {}

    @property
    def home(self) -> Path:
        return self._home if self._home is not None else claude_home()

    # -- discovery -----------------------------------------------------

    def roots(self) -> list[tuple[str, Path]]:
        """Every path whose sessions belong to this project, and its slug.

        The repository's main checkout, plus its own linked worktrees. A
        worktree placed *beside* the repository is `isolation_plan`'s default,
        and its mangled name extends the origin's without a path boundary,
        which makes it lexically identical to the unrelated sibling checkout
        BUG_015 is about. Git is the only thing that can tell those two apart,
        so it is asked, once per instance and never per transcript.

        **The origin is resolved UPWARD to the main checkout first.** This used
        to be asked only when the origin already WAS the main checkout, leaving
        the open question of what a worktree's own report should contain
        deliberately unanswered — but the default answer that fell out of not
        deciding was the wrong one, and silently so. From inside a worktree the
        origin is the worktree, whose mangled name is LONGER than the parent
        project's, so the parent's transcript directory failed the narrowing in
        :func:`_under_origin` and nothing above the worktree was visible at
        all. Since the methodology MANDATES that a session run in an isolated
        worktree, per-state attribution had effectively never functioned under
        the only workflow the library permits (BUG_017).

        The question is now decided rather than defaulted: a worktree session's
        report is the PROJECT's. A worktree is a place the project is being
        worked on, not a project — its spend is the project's spend, and a
        report that splits them makes every isolated session look free.

        This widens what a worktree sees to the project and no further.
        Resolving upward hands :func:`_under_origin` the main checkout's
        prefix, which is exactly the input the main-checkout case already gets,
        so a sibling checkout stays excluded by the same evidence as before —
        BUG_015 stays red from both directions. A ``None`` from git (not a
        repository) leaves the origin alone, which keeps the narrowing at least
        as tight as it was.
        """
        if self._roots is None:
            self._roots = []
            if self.origin is not None:
                origin = git.main_checkout(self.origin) or self.origin.resolve()
                self._roots.append((str(origin).replace("/", PATH_MARK), origin))
                for worktree in git.worktrees(origin):
                    path = worktree.path.resolve()
                    if path != origin:
                        self._roots.append((str(path).replace("/", PATH_MARK), path))
        return self._roots

    def paths(self) -> list[Path]:
        root = self.home / "projects"
        roots = self.roots()
        found: list[Path] = []
        try:
            projects = sorted(root.iterdir())
        except OSError:
            return []
        for project in projects:
            if not project.is_dir():
                continue
            if roots and not any(
                _under_origin(project.name, prefix, path) for prefix, path in roots
            ):
                continue
            try:
                found.extend(sorted(project.glob("*.jsonl")))
            except OSError:
                continue
        return found

    def activity(self) -> dict[str, float]:
        """Host session id to its last conversational turn, as an epoch.

        Read by the oracle, through the probe the CLI injects, as one of a
        host's signs of life. The turn is :func:`last_turn`'s: the newest
        timestamped ``user`` or ``assistant`` entry, a tool result or a
        subagent's entry included. The file's modification time is NOT the
        answer, because the host keeps appending bookkeeping lines to an idle
        conversation (BUG_078).

        **The mtime is the fallback**, used only when no turn is found within
        the read budget — a transcript with no turn yet, or one whose tail is a
        single line longer than the budget. A transcript can only have been
        written after its last turn, so the fallback errs towards "active",
        which is the direction the mtime always erred in.

        A time is read here and handed on, never stored, so the "no stored
        clock time" rule ``_watermark`` keeps is untouched. Each file's answer
        is remembered for this instance against its size and mtime, so a
        repeat call reads only the transcripts that changed. A transcript that
        vanishes between listing and reading is skipped.
        """
        latest: dict[str, float] = {}
        for path in self.paths():
            try:
                stat = path.stat()
            except OSError:
                continue
            key = (stat.st_size, stat.st_mtime_ns)
            remembered = self._turns.get(path)
            if remembered is not None and remembered[0] == key:
                turn_at = remembered[1]
            else:
                found = last_turn(path)
                turn_at = found.timestamp if found is not None else None
                self._turns[path] = (key, turn_at)
            when = turn_at if turn_at is not None else stat.st_mtime
            latest[path.stem] = max(when, latest.get(path.stem, when))
        return latest

    def sessions(self) -> list[SourceRef]:
        return [
            SourceRef(
                host_session_id=path.stem,
                watermark=_watermark(path),
                label=path.parent.name,
            )
            for path in self.paths()
        ]

    def find(self, host_session_id: str) -> SourceRef | None:
        if not host_session_id:
            return None
        for path in self.paths():
            if path.stem == host_session_id:
                return SourceRef(
                    host_session_id=path.stem,
                    watermark=_watermark(path),
                    label=path.parent.name,
                )
        return None

    # -- reading -------------------------------------------------------

    def samples(self, ref: SourceRef) -> Iterator[Sample]:
        for path in self.paths():
            if path.stem == ref.host_session_id:
                yield from read_transcript(path)
                return
