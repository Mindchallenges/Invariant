"""The Claude Code host adapter.

The only module that knows hook payload shapes, the hook response schema, or
where a host keeps its agent files. Everything above this reaches the engine
through the CLI, which is what makes a second host an addition rather than a
rewrite.

**Hooks are shims; agents are copies.** A hook can be three lines that exec the
CLI, so it has nothing to drift. An agent definition has to be a real file the
host reads, so it is copied with a provenance header and a content hash, and
``doctor`` reports drift rather than silently overwriting local edits.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from ..core.errors import InvariantError
from ..core.rules import evaluate_event

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
if command -v invariant >/dev/null 2>&1; then
  exec invariant hook {event}
elif [ -x "${{CLAUDE_PROJECT_DIR:-.}}/.claude/invariant/bin/invariant" ]; then
  exec "${{CLAUDE_PROJECT_DIR:-.}}/.claude/invariant/bin/invariant" hook {event}
else
  # The library is not installed. Staying silent is correct: a missing tool
  # must not break every tool call in the project.
  echo '{{}}'
fi
"""

MANAGED_HEADER = "<!-- invariant-managed: {digest} -->"


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
            self._install_agent(source, agents_dir / source.name)

        self._wire_settings()

    def _install_agent(self, source: Path, target: Path) -> None:
        body = source.read_text(encoding="utf-8")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        target.write_text(f"{MANAGED_HEADER.format(digest=digest)}\n{body}", encoding="utf-8")

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
            ("hook shims installed", not missing, f"{len(EVENTS) - len(missing)}/{len(EVENTS)}")
        )

        settings_path = self.claude_dir / "settings.json"
        wired = False
        if settings_path.is_file():
            try:
                text = settings_path.read_text(encoding="utf-8")
                wired = all(f"invariant-{e}.sh" in text for e in EVENTS)
            except OSError:
                wired = False
        checks.append(("hooks wired in settings", wired, str(settings_path)))

        drifted = []
        for source in sorted((DATA_DIR / "agents").glob("*.md")):
            target = self.claude_dir / "agents" / source.name
            if not target.is_file():
                drifted.append(f"{source.name} (absent)")
                continue
            expected = hashlib.sha256(
                source.read_text(encoding="utf-8").encode("utf-8")
            ).hexdigest()[:16]
            first = target.read_text(encoding="utf-8").splitlines()[:1]
            if not first or expected not in first[0]:
                drifted.append(source.name)
        checks.append(
            (
                "library agents current",
                not drifted,
                ", ".join(drifted) if drifted else "all match the shipped version",
            )
        )
        return checks

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
        # Reading an architecture doc is the evidence the docs-first gate
        # consumes. Recording it here is what makes that gate a check on
        # something that happened rather than on something claimed.
        if payload.get("tool_name") != "Read":
            return {}
        target = (payload.get("tool_input") or {}).get("file_path")
        if not target:
            return {}
        session = ctx.current_session()
        if session is None:
            return {}
        try:
            ctx.pipelines.record_docs_read(session.id, str(target))
        except InvariantError:
            pass
        return {}


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
    session = ctx.current_session()

    if session is None:
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
