"""The Claude Code host adapter.

The only module that knows hook payload shapes, the hook response schema, or
where a host keeps its agent files. Everything above this reaches the engine
through the CLI, which is what makes a second host an addition rather than a
rewrite.

**Hooks are shims; agents are copies.** A hook can be three lines that exec the
CLI, so it has nothing to drift. An agent definition has to be a real file the
host reads, so it is copied with a content hash recorded *inside* its
frontmatter, and ``doctor`` reports drift rather than silently overwriting
local edits.

The marker's placement is not a detail. It was once written above the
frontmatter, which moved that block off line 1 and made this host skip every
agent it had just installed — silently, and with ``verify`` reporting success,
because the thing ``verify`` looked for was the marker that caused the skip.
Loadability and currency are separate checks here for that reason, and neither
is allowed to stand in for the other.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..core.errors import InvariantError
from ..core.rules import evaluate_event
from ..domains.session import ambiguity_message

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

EVENTS = {
    "session-start": "SessionStart",
    "user-prompt-submit": "UserPromptSubmit",
    "pre-tool-use": "PreToolUse",
    "post-tool-use": "PostToolUse",
    "session-end": "SessionEnd",
}

SHIM_TEMPLATE = """#!/usr/bin/env bash
# Installed by invariant. This file is a shim on purpose: all logic lives in
# the library, so upgrading it never means merging changes back into here.
# Project-specific behaviour belongs in .claude/invariant.yaml, not in this file.
set -euo pipefail

# Resolution order. Every candidate is on PATH or expressed relative to $HOME
# or the project — never an absolute path carrying a username, because this
# file is committed.
for candidate in \\
  "$(command -v invariant 2>/dev/null || true)" \\
  "$HOME/.local/bin/invariant" \\
  "${{CLAUDE_PROJECT_DIR:-.}}/.claude/invariant/bin/invariant"
do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    exec "$candidate" hook {event}
  fi
done

# Not installed. Staying silent is correct: a missing tool must not break every
# tool call in the project.
echo '{{}}'
"""

#: Provenance goes *inside* the frontmatter, as a YAML comment.
#:
#: It used to be an HTML comment on line 1. That pushed the frontmatter to line
#: 2, and this host only recognises a frontmatter block that opens the file —
#: so every agent installed that way was copied into place and then ignored,
#: with nothing reporting it. A comment within the block is invisible to the
#: YAML parser and still greppable, which is what provenance actually needs.
MARKER_PREFIX = "# invariant-managed:"
MARKER_TEMPLATE = MARKER_PREFIX + " {digest}"

FENCE = "---"

#: Check labels. Named constants because ``doctor`` prints them and the tests
#: assert on them, and a silently renamed check is a silently dropped test.
CHECK_HOOKS = "hook shims installed"
CHECK_WIRED = "hooks wired in settings"
CHECK_AGENTS_LOADABLE = "agents loadable by host"
CHECK_AGENTS_CURRENT = "library agents current"

#: Tools whose invocation means "a subagent ran". Recorded as pipeline
#: evidence so the agent-role gates can read a fact instead of a claim.
AGENT_TOOLS = {"Task", "Agent"}


def digest_of(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _split_frontmatter(text: str) -> tuple[list[str], str] | None:
    """``(frontmatter lines, body)`` — or ``None`` if the host would skip it.

    Deliberately strict about the opening fence being the first line, because
    that is the condition the host itself applies. A helper that is more
    forgiving than the thing it models cannot be used to predict it.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != FENCE:
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == FENCE:
            return lines[1:index], "\n".join(lines[index + 1 :])
    return None


#: A frontmatter key line. Deliberately narrow: a key is a bare identifier at
#: the start of a line, everything after the first colon is the value.
_KEY_LINE = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$")


def agent_frontmatter(text: str) -> dict | None:
    """The agent's frontmatter as a mapping, or ``None`` if it will not load.

    Parsed line by line rather than with a YAML parser, because the host reads
    it that way and the host is what this predicts. Every agent file shipped
    here — and the project-local one that has always loaded correctly — has a
    ``description`` containing ``": "``, which strict YAML rejects outright.
    Validating with ``yaml.safe_load`` would therefore condemn four agents that
    work. A checker that is stricter than the system it models produces false
    alarms exactly as reliably as a loose one produces misses.
    """
    split = _split_frontmatter(text)
    if split is None:
        return None
    front: dict[str, str] = {}
    for line in split[0]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _KEY_LINE.match(stripped)
        if match:  # anything else is a continuation line, not a new key
            front.setdefault(match.group(1), match.group(2).strip())
    return front or None


def _marker_digest(text: str) -> str | None:
    split = _split_frontmatter(text)
    if split is None:
        return None
    for line in split[0]:
        stripped = line.strip()
        if stripped.startswith(MARKER_PREFIX):
            return stripped[len(MARKER_PREFIX) :].strip()
    return None


def _reconstruct_source(text: str) -> str | None:
    """The installed file with the marker removed — i.e. its source form.

    Comparing this against the shipped file is what detects a *local edit*.
    Comparing the marker against the shipped digest only detects an *old
    install*; the two are different situations and the original check could
    see neither.
    """
    split = _split_frontmatter(text)
    if split is None:
        return None
    front, body = split
    kept = [line for line in front if not line.strip().startswith(MARKER_PREFIX)]
    return "\n".join([FENCE, *kept, FENCE, body])


def loadability_problem(text: str, expected_name: str) -> str | None:
    """Why the host would refuse this file, or ``None`` if it would load it."""
    if _split_frontmatter(text) is None:
        return "no frontmatter on line 1"
    front = agent_frontmatter(text)
    if front is None:
        return "frontmatter declares no keys"
    if not front.get("name"):
        return "no name"
    if not front.get("description"):
        return "no description, so nothing can route work to it"
    if str(front["name"]) != expected_name:
        return f"declares name {front['name']!r} but the file is {expected_name}.md"
    return None


class ClaudeCodeHost:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.claude_dir = self.root / ".claude"

    # -- installation --------------------------------------------------

    def plan_install(self) -> list[str]:
        actions = [f"install {len(EVENTS)} hook shims into .claude/hooks/"]
        actions.append("wire the shims into .claude/settings.json")
        agents = sorted((DATA_DIR / "agents").glob("*.md"))
        actions.append(f"install {len(agents)} library agents into .claude/agents/")
        return actions

    def install(self) -> None:
        hooks_dir = self.claude_dir / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        for event in EVENTS:
            path = hooks_dir / f"invariant-{event}.sh"
            path.write_text(SHIM_TEMPLATE.format(event=event), encoding="utf-8")
            path.chmod(0o755)

        agents_dir = self.claude_dir / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        for source in sorted((DATA_DIR / "agents").glob("*.md")):
            self.install_agent(source, agents_dir / source.name)

        self._wire_settings()

    def install_agent(self, source: Path, target: Path) -> None:
        body = source.read_text(encoding="utf-8")
        split = _split_frontmatter(body)
        if split is None:
            raise InvariantError(
                f"{source.name} has no YAML frontmatter starting on line 1. "
                f"This host identifies an agent by that block; a file without "
                f"one is copied into place and then silently ignored, so "
                f"installing it would look like success and produce nothing."
            )
        front, rest = split
        marker = MARKER_TEMPLATE.format(digest=digest_of(body))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "\n".join([FENCE, marker, *front, FENCE, rest]), encoding="utf-8"
        )

    def _wire_settings(self) -> None:
        settings_path = self.claude_dir / "settings.json"
        settings = {}
        if settings_path.is_file():
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise InvariantError(
                    f".claude/settings.json is not valid JSON ({exc}); fix it "
                    f"before running init, so nothing here overwrites your work"
                ) from exc

        hooks = settings.setdefault("hooks", {})
        for event, host_event in EVENTS.items():
            command = f"${{CLAUDE_PROJECT_DIR}}/.claude/hooks/invariant-{event}.sh"
            entries = hooks.setdefault(host_event, [])
            if any(
                command in json.dumps(entry) for entry in entries
            ):
                continue
            entry = {"hooks": [{"type": "command", "command": command, "timeout": 15}]}
            if host_event in {"PreToolUse", "PostToolUse"}:
                entry["matcher"] = "*"
            entries.append(entry)

        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    # -- verification --------------------------------------------------

    def verify(self) -> list[tuple[str, bool, str]]:
        checks: list[tuple[str, bool, str]] = []
        hooks_dir = self.claude_dir / "hooks"
        missing = [e for e in EVENTS if not (hooks_dir / f"invariant-{e}.sh").is_file()]
        checks.append(
            (CHECK_HOOKS, not missing, f"{len(EVENTS) - len(missing)}/{len(EVENTS)}")
        )

        settings_path = self.claude_dir / "settings.json"
        wired = False
        if settings_path.is_file():
            try:
                text = settings_path.read_text(encoding="utf-8")
                wired = all(f"invariant-{e}.sh" in text for e in EVENTS)
            except OSError:
                wired = False
        checks.append((CHECK_WIRED, wired, str(settings_path)))
        checks.extend(self.agent_checks())
        return checks

    def agent_checks(self) -> list[tuple[str, bool, str]]:
        """Two questions, kept apart on purpose.

        *Loadable* asks whether the host can turn this file into an agent at
        all. *Current* asks whether it is the version the library ships. The
        original implementation asked only the second, and answered it by
        looking for a marker whose placement was what broke the first — so a
        project with zero working agents was reported as fully in order. One
        check standing in for the other is the whole bug.
        """
        unloadable: list[str] = []
        stale: list[str] = []
        total = 0

        for source in sorted((DATA_DIR / "agents").glob("*.md")):
            total += 1
            target = self.claude_dir / "agents" / source.name
            if not target.is_file():
                unloadable.append(f"{source.stem} (absent)")
                stale.append(f"{source.stem} (absent)")
                continue

            text = target.read_text(encoding="utf-8")
            problem = loadability_problem(text, source.stem)
            if problem:
                unloadable.append(f"{source.stem} ({problem})")

            shipped = source.read_text(encoding="utf-8")
            reconstructed = _reconstruct_source(text)
            if reconstructed is None:
                stale.append(f"{source.stem} (unreadable)")
            elif reconstructed != shipped:
                if _marker_digest(text) == digest_of(shipped):
                    stale.append(f"{source.stem} (edited locally)")
                else:
                    stale.append(f"{source.stem} (installed from an older version)")

        return [
            (
                CHECK_AGENTS_LOADABLE,
                not unloadable,
                (
                    ", ".join(unloadable) + " — repair with: invariant agents install"
                    if unloadable
                    else f"{total} agent(s) parse and are addressable by name"
                ),
            ),
            (
                CHECK_AGENTS_CURRENT,
                not stale,
                ", ".join(stale) if stale else "all match the shipped version",
            ),
        ]

    # -- request handling ----------------------------------------------

    def handle(self, event: str, raw: str, ctx) -> dict:
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {}

        if event == "pre-tool-use":
            return self._pre_tool_use(payload, ctx)
        if event == "post-tool-use":
            return self._post_tool_use(payload, ctx)
        if event == "user-prompt-submit":
            return _context_response("UserPromptSubmit", build_context(ctx))
        if event == "session-start":
            return _context_response("SessionStart", build_context(ctx, greeting=True))
        if event == "session-end":
            return self._session_end(ctx)
        return {}

    def _bind_started_session(self, payload: dict, ctx) -> None:
        if not ctx.host_session_id:
            return
        tool_input = payload.get("tool_input") or {}
        command = str(tool_input.get("command", ""))
        if not _SESSION_START.search(command):
            return

        response = payload.get("tool_response")
        if isinstance(response, dict):
            response = response.get("stdout") or response.get("output") or ""
        session_id = _bind_started_session_id(str(response or ""))
        if not session_id:
            return

        try:
            ctx.sessions.adopt_host_session(session_id, ctx.host_session_id)
        except InvariantError:
            # Already bound, or this host already owns one. Both are the 1:1
            # rule working; neither is a reason to fail a completed command.
            pass

    def _session_end(self, ctx) -> dict:
        """The host session is over; its invariant session is nobody's now.

        Marked, not abandoned. The session may hold unmerged work in a live
        worktree, and destroying that because a terminal closed would be the
        library doing the exact thing it exists to prevent.
        """
        if not ctx.host_session_id:
            return {}
        try:
            ctx.sessions.end_host_session(ctx.host_session_id)
        except InvariantError:
            pass
        return {}

    def _pre_tool_use(self, payload: dict, ctx) -> dict:
        rule_ctx = ctx.rule_context(payload=payload)
        verdicts = evaluate_event("pre-tool-use", rule_ctx)

        blocking = [v for v in verdicts if v.blocks]
        warning = [v for v in verdicts if v.warns]

        if blocking:
            reason = "\n".join(v.render() for v in blocking)
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        if warning:
            return _context_response(
                "PreToolUse", "\n".join(v.render() for v in warning)
            )
        return {}

    def _post_tool_use(self, payload: dict, ctx) -> dict:
        # Two facts get recorded here, for the same reason: a gate that reads
        # what happened is a gate that cannot be satisfied by assertion.
        # Reading an architecture doc feeds the docs-first gate; dispatching a
        # subagent feeds the agent-role gates.
        tool = str(payload.get("tool_name", ""))

        # A session registered through the CLI cannot see the host identifier:
        # a Bash tool call carries no hook payload, so `session start` has
        # nothing to record. This event does have it, and it brackets the exact
        # call that did the registering — so the binding is made here, where
        # both halves are in hand, rather than left to a flag someone has to
        # remember to pass.
        if tool == "Bash":
            self._bind_started_session(payload, ctx)
            return {}

        if tool != "Read" and tool not in AGENT_TOOLS:
            return {}

        tool_input = payload.get("tool_input") or {}
        session = ctx.current_session(strict=False)
        if session is None:
            return {}

        try:
            if tool == "Read":
                target = tool_input.get("file_path")
                if target:
                    ctx.pipelines.record_docs_read(session.id, str(target))
            else:
                agent_type = tool_input.get("subagent_type")
                if agent_type:
                    ctx.pipelines.record_agent_run(session.id, str(agent_type))
        except InvariantError:
            pass
        return {}


#: `invariant session start <name>` — with any flags, and however the command
#: was reached (a bare call, or one wrapped in `cd … &&`).
_SESSION_START = re.compile(r"\binvariant\b[^|;&]*?\bsession\s+start\b")

#: The identifier `session start` prints. Parsed rather than guessed, so the
#: binding names the session that command actually created.
_STARTED_ID = re.compile(r"session\s+([0-9a-z]+)\s+\(")


def _bind_started_session_id(response: str) -> str | None:
    """The session id out of `session start` output, text or JSON."""
    try:
        data = json.loads(response)
    except (json.JSONDecodeError, TypeError):
        data = None
    if isinstance(data, dict):
        session = data.get("session")
        if isinstance(session, dict) and session.get("id"):
            return str(session["id"])
    match = _STARTED_ID.search(response or "")
    return match.group(1) if match else None


def _context_response(event_name: str, text: str) -> dict:
    if not text.strip():
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }


def build_context(ctx, greeting: bool = False) -> str:
    """The state projection injected into the agent's context.

    Short and about *now*, rather than long and about everything. It cannot go
    stale relative to what happened, because it is read from the record of what
    happened.
    """
    lines: list[str] = []
    session = ctx.current_session(strict=False)

    if session is None:
        # "None" has two causes and they need opposite advice: nothing is
        # registered here (start one), or several are and the directory cannot
        # say which (name one). Telling an agent with three live sessions to
        # start a fourth is how this deadlocked in the first place.
        #
        # Under the 1:1 binding, a host session that owns nothing is in the
        # first case however many sessions exist — the others are provably not
        # it, because they answer to a different host. Listing them as
        # candidates asked the reader to choose between options that were all
        # wrong.
        candidates = ctx.sessions.origin_candidates(
            ctx.cwd, host_session_id=ctx.host_session_id
        )
        unbound = [s for s in candidates if not s.host_session_id]

        if ctx.host_session_id:
            # A host session that owns nothing is not ambiguous — it owns
            # nothing. Listing other sessions and asking it to "name the one
            # you mean" poses a question with no right answer, because under a
            # 1:1 binding none of them is this caller. The two real options are
            # to start a session or to claim one that has no owner.
            if not greeting:
                return ""
            lines = [
                "invariant: this host session owns no session yet.",
                "  Start one before editing: invariant session start <task-name>",
                "  It binds to this host session automatically — one host "
                "session, one invariant session.",
            ]
            if unbound:
                lines.append(
                    f"  {len(unbound)} session(s) here have no owner; claim one "
                    f"only if it is yours:"
                )
                for session in unbound[:6]:
                    lines.append(
                        f"    invariant session adopt <host-id> --id {session.id}"
                        f"   ({session.name}, {session.state})"
                    )
            return "\n".join(lines)

        # No host identifier at all — a bare CLI call rather than a hook. Here
        # the directory really is the only evidence, so ambiguity is genuine.
        if len(unbound) > 1:
            return "invariant: " + ambiguity_message(unbound)
        if greeting:
            return (
                "invariant: no session is active in this directory.\n"
                "  Start one before editing: invariant session start <task-name>"
            )
        return ""

    lines.append(f"invariant — session {session.id} ({session.name}) is {session.state}")

    machine = ctx.sessions.machine
    legal = machine.next_states(session.state)
    if legal:
        lines.append(f"  session may go to: {', '.join(legal)}")

    pipeline = ctx.pipelines.current(session.id)
    if pipeline is None:
        lines.append(
            "  no pipeline running — start one with 'invariant pipeline start "
            "--task \"...\"' before editing"
        )
    else:
        pmachine = ctx.pipelines.machine
        lines.append(
            f"  pipeline {pipeline.methodology}: {pipeline.state}"
            + (f"  (task: {pipeline.task})" if pipeline.task else "")
        )
        nxt = pmachine.next_states(pipeline.state)
        lines.append(f"  may advance to: {', '.join(nxt) or '(terminal)'}")

        try:
            verdicts = ctx.pipelines.evaluate_gates(
                session.id, session=session.to_dict(), cwd=ctx.cwd
            )
        except InvariantError:
            verdicts = []
        owed = [v for v in verdicts if not v.verdict.ok and not v.verdict.skipped]
        if owed:
            lines.append("  gates currently owed here:")
            for verdict in owed:
                lines.append(f"    - {verdict.rule_id}: {verdict.verdict.message}")

    if session.push_count:
        lines.append(f"  pushes used: {session.push_count} (the barrier allows one)")

    return "\n".join(lines)
