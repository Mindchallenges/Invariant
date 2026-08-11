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
from datetime import datetime
from pathlib import Path
from typing import Iterator

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


class ClaudeCodeTranscripts:
    """``core.stats.SampleSource`` over the host's per-project transcripts.

    ``origin`` narrows the scan to one project tree. The host names a project
    directory after its path with separators replaced, and a session worktree
    is a different directory and therefore a different project — so narrowing
    by *prefix* rather than by exact name is what keeps a project's own
    worktrees attributed to it instead of scattered across entries nobody
    thinks to look in.
    """

    def __init__(self, origin: Path | None = None, home: Path | None = None) -> None:
        self.origin = Path(origin) if origin is not None else None
        self._home = Path(home) if home is not None else None

    @property
    def home(self) -> Path:
        return self._home if self._home is not None else claude_home()

    # -- discovery -----------------------------------------------------

    def paths(self) -> list[Path]:
        root = self.home / "projects"
        prefix = None
        if self.origin is not None:
            prefix = str(self.origin.resolve()).replace("/", "-")
        found: list[Path] = []
        try:
            projects = sorted(root.iterdir())
        except OSError:
            return []
        for project in projects:
            if not project.is_dir():
                continue
            if prefix is not None and not project.name.startswith(prefix):
                continue
            try:
                found.extend(sorted(project.glob("*.jsonl")))
            except OSError:
                continue
        return found

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
