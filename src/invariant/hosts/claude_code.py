"""The Claude Code host adapter.

The only module that knows the hook *protocol* — wire format, response schema,
settings wiring — or where a host keeps its agent files. Everything above this
reaches the engine through the CLI, which is what makes a second host an
addition rather than a rewrite.

What travels upward is a payload dict spelled in
``core.rules.NORMALISED_PAYLOAD_KEYS``. That tuple is the contract, and it is
stated in exactly one place so this module and ``docs/architecture/overview.md``
cannot drift apart about what a host owes the engine — they already had, which
is what BUG_022 was filed about. A second host adapter's obligation is to fill
those keys; nothing above reads anything else.

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
import os
import re
from pathlib import Path

from ..core.errors import InvariantError
from ..core.rules import EDIT_TOOLS, evaluate_event
from ..domains.pipeline import NO_PIPELINE, VIA_ACTOR
from ..domains.session import ambiguity_message

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

EVENTS = {
    "session-start": "SessionStart",
    "user-prompt-submit": "UserPromptSubmit",
    "pre-tool-use": "PreToolUse",
    "post-tool-use": "PostToolUse",
    "stop": "Stop",
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
CHECK_HOOKS_RUN = "hook shims actually run"
CHECK_WIRED = "hooks wired in settings"

#: Set on a hook response when the engine failed and fell back to advisory
#: silence. See ``_hook_degraded``.
DEGRADED_KEY = "invariantDegraded"

#: Payloads for the liveness check. Each is chosen to exercise the whole path
#: — shim resolution, interpreter start, module import, rule evaluation, JSON
#: out — while writing nothing: no file_path so no session is adopted and no
#: docs read is recorded, no session_id so no host binding is ended, and a
#: command that matches no rule's trigger.
_PROBE_PAYLOADS = {
    "session-start": {},
    "user-prompt-submit": {},
    "pre-tool-use": {"tool_name": "Bash", "tool_input": {"command": "true"}},
    "post-tool-use": {"tool_name": "Read", "tool_input": {}},
    # `stop_hook_active` is what a real turn sets once the closeout has already
    # been demanded, so the probe exercises the whole path and provably cannot
    # block: a liveness check that could halt the caller's turn would be a
    # worse defect than the outage it looks for.
    "stop": {"stop_hook_active": True},
    "session-end": {},
}
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

    def install_hooks(self) -> list[str]:
        """Shims and settings wiring, and nothing else.

        Separate from :meth:`install` because it has to be runnable against a
        project that is already set up. When the library gains an event, an
        existing project has no shim for it and enforcement for that event is
        simply off — silently, which is the failure mode this library exists to
        remove. Until this existed the only repair ``doctor`` could offer was
        ``init --force``, and that rewrites the manifest, discarding every
        considered downgrade a project had recorded. Nobody trades their
        configuration for a hook, so the honest outcome was that the hook never
        got installed.

        Returns what changed, so a caller can say so rather than claim work it
        did not do.
        """
        hooks_dir = self.claude_dir / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for event in EVENTS:
            path = hooks_dir / f"invariant-{event}.sh"
            body = SHIM_TEMPLATE.format(event=event)
            existed = path.is_file() and path.read_text(encoding="utf-8") == body
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
            if not existed:
                written.append(f"invariant-{event}.sh")
        self._wire_settings()
        return written

    def install(self) -> None:
        self.install_hooks()

        agents_dir = self.claude_dir / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        for source in sorted((DATA_DIR / "agents").glob("*.md")):
            self.install_agent(source, agents_dir / source.name)

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
        # Name the repair. A failure whose only known fix is `init --force`
        # gets left alone, because that rewrites the manifest and nobody trades
        # a project's configuration for a hook.
        detail = f"{len(EVENTS) - len(missing)}/{len(EVENTS)}"
        if missing:
            detail += (
                f" — missing: {', '.join(missing)}. Repair with: invariant hooks install"
            )
        checks.append((CHECK_HOOKS, not missing, detail))

        settings_path = self.claude_dir / "settings.json"
        wired = False
        if settings_path.is_file():
            try:
                text = settings_path.read_text(encoding="utf-8")
                wired = all(f"invariant-{e}.sh" in text for e in EVENTS)
            except OSError:
                wired = False
        checks.append((CHECK_WIRED, wired, str(settings_path)))
        checks.append(self.hooks_run_check())
        checks.extend(self.asset_checks())
        return checks

    def hooks_run_check(self) -> tuple[str, bool, str]:
        """Invoke every hook and see whether it actually works.

        The other two hook checks confirm the shims *exist* and are *wired*.
        Both stayed green through an entire session in which every hook crashed
        on import and enforcement was off for every call — because "installed"
        is a fact about a file and "works" is a fact about running it, and only
        one of them was ever checked.

        That is the same mistake, in this same module, as the one that left
        four agents unloadable while ``verify`` reported them current: a check
        on the artifact of an installation is not a check on the installation.
        """
        import subprocess

        hooks_dir = self.claude_dir / "hooks"
        broken: list[str] = []

        for event in EVENTS:
            shim = hooks_dir / f"invariant-{event}.sh"
            if not shim.is_file():
                broken.append(f"{event} (absent)")
                continue
            try:
                proc = subprocess.run(
                    ["bash", str(shim)],
                    input=json.dumps(_PROBE_PAYLOADS.get(event, {})),
                    cwd=str(self.root),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env={**os.environ, "CLAUDE_PROJECT_DIR": str(self.root)},
                )
            except (OSError, subprocess.SubprocessError) as exc:
                broken.append(f"{event} ({type(exc).__name__})")
                continue

            if proc.returncode != 0:
                broken.append(f"{event} (exit {proc.returncode})")
                continue
            try:
                response = json.loads(proc.stdout or "{}")
            except json.JSONDecodeError:
                broken.append(f"{event} (output is not JSON)")
                continue
            # The case that matters. A crashed engine answers with valid JSON
            # and exit zero deliberately — it must, or a broken library would
            # deny every tool call — so only the marker separates "it ran" from
            # "it worked".
            if isinstance(response, dict) and response.get(DEGRADED_KEY):
                broken.append(f"{event}: {response[DEGRADED_KEY]}")

        return (
            CHECK_HOOKS_RUN,
            not broken,
            (
                "; ".join(broken) + "  — enforcement is OFF for these"
                if broken
                else f"all {len(EVENTS)} evaluated and returned a verdict"
            ),
        )

    def asset_checks(self) -> list[tuple[str, bool, str]]:
        """Loadability and currency, for every kind the host reads.

        Both questions for every kind, because they answer different things and
        neither may stand in for the other — that conflation is what left four
        agents unloadable while `verify` called them current.

        Loadability covers PROJECT-OWNED files too. A local command that
        silently fails to load is the same outage as a shipped one, and until
        now nothing looked at commands or skills at all.
        """
        from . import assets

        checks: list[tuple[str, bool, str]] = []
        for kind in assets.KINDS:
            installed = kind.paths(self.claude_dir)
            unloadable: list[str] = []
            for path in installed:
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError as exc:
                    unloadable.append(f"{kind.identity(path)} ({type(exc).__name__})")
                    continue
                problem = assets.loadability_problem(kind, text, kind.identity(path))
                if problem:
                    unloadable.append(f"{kind.identity(path)} ({problem})")

            # A shipped asset that is absent cannot load, and absence is the
            # strongest form of that. Currency reports it too, but a reader
            # seeing "loadable: ok" beside a missing agent has been told the
            # wrong thing by the check whose whole job is that question.
            present = {kind.identity(path) for path in installed}
            for identity in self._shipped_identities(kind):
                if identity not in present:
                    unloadable.append(f"{identity} (absent)")

            checks.append(
                (
                    f"{kind.name}s loadable by host",
                    not unloadable,
                    (
                        ", ".join(unloadable) + "  — repair with: invariant assets install"
                        if unloadable
                        else f"{len(installed)} {kind.name}(s) parse and are addressable"
                    )
                    if installed
                    else f"no {kind.name}s installed",
                )
            )

            stale = self._stale_for(kind)
            if stale is not None:
                checks.append(
                    (
                        f"library {kind.name}s current",
                        not stale,
                        ", ".join(stale) if stale else "all match the shipped version",
                    )
                )
        return checks

    def _shipped_identities(self, kind) -> list[str]:
        """What this library ships of a kind, by name."""
        root = DATA_DIR / kind.directory
        if not root.is_dir():
            return []
        if kind.nested_file:
            return sorted(
                c.name for c in root.iterdir() if (c / kind.nested_file).is_file()
            )
        return sorted(p.stem for p in root.glob("*.md"))

    def _stale_for(self, kind) -> list[str] | None:
        """Which shipped assets of this kind differ from what we ship.

        ``None`` when the library ships none of this kind — a project owning
        every command is not drift, and reporting "0 stale" would imply this
        library had an opinion about files it never wrote.
        """
        from . import assets

        source_root = DATA_DIR / kind.directory
        sources = (
            sorted(source_root.glob("*.md"))
            if not kind.nested_file
            else sorted(
                c / kind.nested_file
                for c in source_root.iterdir()
                if (c / kind.nested_file).is_file()
            )
            if source_root.is_dir()
            else []
        )
        if not source_root.is_dir() or not sources:
            return None

        stale: list[str] = []
        for source in sources:
            identity = kind.identity(source)
            target = self._target_for(kind, identity)
            if not target.is_file():
                stale.append(f"{identity} (absent)")
                continue
            text = target.read_text(encoding="utf-8")
            shipped = source.read_text(encoding="utf-8")
            rebuilt = assets.reconstruct_source(text)
            if rebuilt is None:
                stale.append(f"{identity} (unreadable)")
            elif rebuilt != shipped:
                if assets.marker_digest(text) == digest_of(shipped):
                    stale.append(f"{identity} (edited locally)")
                else:
                    stale.append(f"{identity} (installed from an older version)")
        return stale

    def _target_for(self, kind, identity: str) -> Path:
        base = self.claude_dir / kind.directory
        if kind.nested_file:
            return base / identity / kind.nested_file
        return base / f"{identity}.md"

    def install_assets(self, only: str | None = None) -> list[str]:
        """Install every library-shipped asset, or one kind of them."""
        from . import assets

        installed: list[str] = []
        for kind in assets.KINDS:
            if only and kind.name != only:
                continue
            source_root = DATA_DIR / kind.directory
            if not source_root.is_dir():
                continue
            sources = (
                sorted(source_root.glob("*.md"))
                if not kind.nested_file
                else sorted(
                    c / kind.nested_file
                    for c in source_root.iterdir()
                    if (c / kind.nested_file).is_file()
                )
            )
            for source in sources:
                identity = kind.identity(source)
                target = self._target_for(kind, identity)
                target.parent.mkdir(parents=True, exist_ok=True)
                body = source.read_text(encoding="utf-8")
                try:
                    target.write_text(
                        assets.render_managed(body, digest_of(body)), encoding="utf-8"
                    )
                except ValueError as exc:
                    raise InvariantError(
                        f"{kind.name} {identity!r} has no YAML frontmatter starting "
                        f"on line 1. The host identifies it by that block; a file "
                        f"without one is copied into place and then silently "
                        f"ignored, so installing it would look like success and "
                        f"produce nothing."
                    ) from exc
                installed.append(f"{kind.name}:{identity}")
        return installed

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
        if event == "stop":
            return self._stop(payload, ctx)
        if event == "session-end":
            return self._session_end(ctx)
        return {}

    def _stop(self, payload: dict, ctx) -> dict:
        """The turn is ending. The last point at which the closeout can be asked for.

        Every other gate fires while work is happening. The Definition of Done
        is the one obligation that comes due when work *stops*, and until this
        existed nothing fired there: the pipeline's terminal state hands off to
        the session machine, and the session machine was never asked again.

        ``SessionEnd`` looks like the right place by name and is the wrong one
        — the host runs it for cleanup and discards what it says, so a demand
        made there is made to nobody. This event is the one that can still be
        answered, because blocking here returns the turn to the agent with the
        reason attached instead of ending it.
        """
        rule_ctx = ctx.rule_context(payload=payload)
        verdicts = evaluate_event("stop", rule_ctx)

        blocking = [v for v in verdicts if v.blocks]
        if blocking:
            return {
                "decision": "block",
                "reason": "invariant — the session is not finished:\n"
                + "\n".join(v.render() for v in blocking),
            }

        warning = [v for v in verdicts if v.warns]
        if warning:
            # Advisory here means advisory: surfaced, and the turn still ends.
            return {"systemMessage": "\n".join(v.render() for v in warning)}
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

    def _bind_by_successful_edit(self, payload: dict, ctx) -> None:
        """Claim an unowned session from an edit that actually landed in it.

        This used to happen during pre-tool-use rule evaluation, which made
        ownership a side effect of asking a question: every evaluation ran it,
        including ones whose verdict was DENY, so a refused edit still claimed
        the session — as did a diagnostic that never touched a file. One stray
        call aimed into an unowned worktree transferred a peer's session to
        whoever made it, silently, with no way for the owner to reclaim it.

        Here the tool has provably run. That is the difference between evidence
        of ownership and evidence of curiosity.
        """
        if not ctx.host_session_id:
            return
        tool_input = payload.get("tool_input") or {}
        raw = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not raw:
            return

        resolution = ctx.sessions.resolve(
            ctx.cwd, target=Path(str(raw)), host_session_id=ctx.host_session_id
        )
        session = resolution.session
        # Only the path-based answer, and only for a session nobody owns. Any
        # other route either already knows the owner or is guessing.
        if resolution.how != "target" or session is None or session.host_session_id:
            return
        try:
            ctx.sessions.adopt_host_session(session.id, ctx.host_session_id)
        except InvariantError:
            # Already bound, or this host owns one. Both are the 1:1 rule
            # working, and neither is a reason to fail a completed tool call.
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
        # Three facts get recorded here, for the same reason: a gate that reads
        # what happened is a gate that cannot be satisfied by assertion.
        # Reading an architecture doc feeds the docs-first gate; dispatching a
        # subagent feeds the agent-role gates; and so does being one.
        tool = str(payload.get("tool_name", ""))

        # A session registered through the CLI cannot see the host identifier:
        # a Bash tool call carries no hook payload, so `session start` has
        # nothing to record. This event does have it, and it brackets the exact
        # call that did the registering — so the binding is made here, where
        # both halves are in hand, rather than left to a flag someone has to
        # remember to pass.
        #
        # Ordered before the actor recording below on purpose: the call being
        # bracketed is very often the subagent's own `session start`, and the
        # binding it makes is what lets every following call from that subagent
        # resolve by host identifier instead of by guess.
        if tool == "Bash":
            self._bind_started_session(payload, ctx)
        elif tool in EDIT_TOOLS:
            self._bind_by_successful_edit(payload, ctx)

        self._record_acting_agent(payload, ctx)

        if tool != "Read" and tool not in AGENT_TOOLS:
            return {}

        tool_input = payload.get("tool_input") or {}
        # Attributed the same way an edit is: by the payload, whose `file_path`
        # is absolute and does not depend on the directory the host happens to
        # be standing in. `current_session` used to answer this, which is steps
        # 2 and 4 of the documented order and skips step 3 — so a Read naming a
        # file inside a session's worktree was NOT attributed to that session
        # while several were live, even though the payload carried the answer
        # and the recording below pulls that same key out to store it
        # (BUG_010, narrow half). A dispatch carries no `file_path`, so it
        # reaches the same route through the worktree its brief names; routing
        # both through one call is what keeps a second, divergent copy of the
        # order from existing here. What that route still cannot do is attribute
        # a dispatch made BEFORE its session existed, which is why the actor is
        # recorded separately above rather than by making this guess harder
        # (BUG_028).
        resolution = ctx.resolution_for_payload(payload)
        session = resolution.session
        if session is None:
            # Nobody is identifiable, so there is nothing to record and nobody
            # to tell. Deliberately silent: an ungoverned turn in a governed
            # repository is ordinary, and a note on every such dispatch would
            # be noise rather than the signal below.
            return {}

        try:
            if tool == "Read":
                target = tool_input.get("file_path")
                if target and resolution.proven:
                    ctx.pipelines.record_docs_read(session.id, str(target))
                return {}

            agent_type = tool_input.get("subagent_type")
            if not agent_type:
                return {}

            # A session WAS identified, so somebody is working under the gates.
            # From here every path that fails to record says so. `agents.*_ran`
            # is block-level, and the whole cost of BUG_028 was that a dropped
            # write was indistinguishable from a write that landed until the
            # gate refused at the end of the phase — by which point no action
            # available to that session could satisfy it.
            if not resolution.proven:
                return _context_response("PostToolUse", _UNPROVEN_DISPATCH.format(
                    agent=agent_type, session=session.id, how=resolution.how
                ))

            outcome = ctx.pipelines.record_agent_run(session.id, str(agent_type))
            if outcome == NO_PIPELINE:
                return _context_response("PostToolUse", _UNRECORDED_DISPATCH.format(
                    agent=agent_type, session=session.id
                ))
        except InvariantError:
            # Evidence is a side effect. A hook that raises turns every dispatch
            # in the project into a failed tool call, which is strictly worse
            # than a gate missing one data point.
            pass
        return {}

    def _record_acting_agent(self, payload: dict, ctx) -> None:
        """Record the agent the HOST says is making this call.

        The dispatch and the run are different events at different times, and
        only the second one can be attributed when a subagent creates its own
        session. A `feature-architect` dispatched to design something runs
        `session start`, `session isolate` and `pipeline start` ITSELF: at the
        moment the parent's Task call was recorded there was no session to
        record it against and no pipeline to be evidence for, so the agent that
        created the pipeline could never appear in it. That is not a lookup bug
        that a better guess would fix — the destination did not exist yet
        (BUG_028).

        What does exist by the time the subagent acts is the subagent. Claude
        Code stamps the acting agent's type onto every hook payload, the ones
        the subagent's own tool calls raise included, so the run can be
        observed from the actor's side even though the dispatch could not be
        attributed from the parent's. Recorded as ``actor`` rather than
        ``dispatch`` because it is the stronger fact: this agent demonstrably
        operated in this pipeline, rather than somebody having asked for it.

        Silent when it cannot record, unlike a dispatch. This fires on EVERY
        tool call, so a note here would be a note on every call the subagent
        makes; the dispatch is the one event rare enough to speak up about.
        """
        agent_type = payload.get("agent_type")
        if not isinstance(agent_type, str) or not agent_type:
            # Absent in an ordinary main-loop turn, and absent from any host
            # that does not report an actor. Both mean "no such fact", not
            # "the fact is empty".
            return

        try:
            resolution = ctx.resolution_for_payload(payload)
            session = self._actor_session(resolution, payload, ctx)
            if session is None:
                return
            ctx.pipelines.record_agent_run(session.id, agent_type, how=VIA_ACTOR)
        except InvariantError:
            pass

    @staticmethod
    def _actor_session(resolution, payload: dict, ctx):
        """The session this agent is *working in*, or ``None``.

        Stricter than :attr:`Resolution.proven`, and deliberately so. `proven`
        asks whether the session was IDENTIFIED rather than guessed, which is
        the right question for recording a dispatch — somebody asked for an
        agent, and the request resolved here. An actor row claims more: that
        the agent did work in this pipeline. So it needs the call to be PLACED
        in the session, not merely attributable to the host that owns it.

        The distinction is load-bearing because a subagent shares its parent's
        `session_id`. The host route therefore answers for every agent the
        parent dispatched, including the ones working somewhere else entirely,
        and one host session that owned an isolated session also ran a
        `backlog-curator` and a `general-purpose` agent against the shared
        checkout — both filed against the isolated session's pipeline, which
        had recorded none of its own work. Writing those as ACTORS would be
        worse than the dispatch rows already there, because an actor row
        asserts the stronger thing.

        The host route is the one that has to be asked twice. It deliberately
        outranks the path a call names — who is acting beats where the bytes
        land, which is what keeps a session reaching into a peer's worktree a
        detectable crossing — so `how` alone cannot tell an architect writing
        inside its own worktree from a curator filing a bug in the shared
        checkout. Both resolve as `host`. The location is therefore asked for
        explicitly here rather than inferred from the route, and only for this
        one route.

        A session with no worktree passes on the binding alone: there is no
        location for it to contradict, and an agent working in the shared
        checkout for a session that has not isolated is working in the only
        place that session has.

        Where the location DOES contradict the binding, the location wins and
        the call is recorded against the session it actually landed in. That
        inverts step 2 for this route only, and for the reason the dispatch
        route already inverts it: the host is not acting on that worktree, it
        is delegating into it, and the run being recorded is the delegate's.
        One host binding holds one session, so a parent driving two tracks
        cannot represent the second — and refusing outright would leave the
        second track unable to record any agent that never names a path,
        which is the same wedge from the other end. Reading a peer's file
        stays a detectable crossing because that inversion applies only when
        the host names an ACTOR, which a main-loop turn never does.
        """
        if not resolution.proven:
            return None
        if resolution.how != "host":
            return resolution.session

        session = resolution.session
        if not getattr(session, "worktree", None):
            return session

        tool_input = payload.get("tool_input") or {}
        raw = tool_input.get("file_path") or tool_input.get("notebook_path")
        here = Path(str(raw)) if raw else ctx.cwd
        return ctx.sessions.by_worktree_containing(here)


#: Said to the session that made a dispatch nothing could file. Both are the
#: removal of the silence BUG_028 is about: the dispatcher used to learn its
#: evidence had gone nowhere at a blocking gate, after the phase it needed the
#: evidence for was over.
_UNPROVEN_DISPATCH = (
    "invariant — {agent} was NOT recorded as evidence. The nearest session is "
    "{session}, but nothing in this call proved it (resolved by {how!r}, which "
    "is a guess, not an identification). Evidence is never written on a guess, "
    "because a pipeline that receives another session's agent clears its gate "
    "on work that never happened.\n"
    "  try: run the dispatch from inside the session's worktree, name that "
    "worktree in the brief, or set INVARIANT_SESSION_ID"
)

_UNRECORDED_DISPATCH = (
    "invariant — {agent} was NOT recorded as evidence: session {session} has "
    "no pipeline running, so there is no gate for it to be evidence for. This "
    "is allowed; it is said out loud because agents.*_ran is block-level and a "
    "pipeline started after the fact will not contain this dispatch.\n"
    "  try: invariant pipeline start --task \"<what you are doing>\" first, or "
    "let the agent record itself by dispatching it after the pipeline exists"
)


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
        lines.append(
            f"  pipeline {pipeline.methodology}: {pipeline.state}"
            + (f"  (task: {pipeline.task})" if pipeline.task else "")
        )
        try:
            # The machine the pipeline was started under, not the manifest
            # default — a `--methodology` override wedged this line before
            # the machine was pinned to the record (AUD-02).
            nxt = ctx.pipelines.machine_for(pipeline).next_states(pipeline.state)
        except InvariantError as exc:
            lines.append(f"  machine unloadable: {exc}")
        else:
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
