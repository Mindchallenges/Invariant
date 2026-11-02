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

**Hooks are shims; agents are renders.** A hook can be three lines that exec
the CLI, so it has nothing to drift. An agent definition has to be a real file
the host reads, so it is rendered from the library's host-neutral source
(``core/agents``, ADR-012 D8) and the project's overlay, and written with a
marker recorded *inside* its frontmatter: the source's digest, the overlay's,
and the body's own, its witness (the A6 ruling 1). ``doctor`` reports drift
rather than silently overwriting local edits.

The marker's placement is not a detail. It was once written above the
frontmatter, which moved that block off line 1 and made this host skip every
agent it had just installed — silently, and with ``verify`` reporting success,
because the thing ``verify`` looked for was the marker that caused the skip.
Loadability and currency are separate checks here for that reason, and neither
is allowed to stand in for the other.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..core import agents as agent_sources
from ..core import plan as plan_reader
from ..core.agents import AgentSource, OverlayError, OverlayProblem
from ..core.errors import InvariantError, UsageError
from ..core.guidelines import render_edit, render_turn_index
from ..core.manifest import Manifest, project_relative
from ..core.privacy import PrivacyScanner
from ..core.rules import (
    AGENT_TOOLS,
    EDIT_TOOLS,
    HELD_OUT_OF_STANDDOWN,
    AgentCopy,
    RuleContext,
    Verdict,
    agents_currency,
    dispatched_agent,
    edit_advisory,
    edit_guidelines,
    evaluate_event,
    is_architecture_doc,
    is_exempt_path,
    standdown_in_force,
    turn_guidelines,
)
from ..domains.pipeline import NO_PIPELINE, VIA_ACTOR
from ..domains.session import ambiguity_message
from . import assets
from .assets import ABSENT, CURRENT, LOCAL, OLDER, digest_of
from .claude_code_launch import HANDOFF_ENV

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

EVENTS = {
    "session-start": "SessionStart",
    "user-prompt-submit": "UserPromptSubmit",
    "pre-tool-use": "PreToolUse",
    "post-tool-use": "PostToolUse",
    "stop": "Stop",
    "session-end": "SessionEnd",
    "notification": "Notification",
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

#: Check labels. Named constants because ``doctor`` prints them and the tests
#: assert on them, and a silently renamed check is a silently dropped test.
CHECK_HOOKS = "hook shims installed"
CHECK_HOOKS_RUN = "hook shims actually run"
CHECK_WIRED = "hooks wired in settings"

#: Set on a hook response when the engine failed and fell back to advisory
#: silence. See ``_hook_degraded``.
DEGRADED_KEY = "invariantDegraded"

#: Marks a hook payload as ``doctor``'s liveness probe. A host never sends it.
#: ``invariant hook`` honours it before anything else is built: the call runs
#: the whole path against a state store that discards every write, so a probe
#: cannot bind, record, spend or end anything (BUG_059). Only the literal
#: ``True`` counts, so nothing reaches it by accident.
PROBE_KEY = "invariantProbe"

#: Payloads for the liveness check. Each exercises the whole path — shim
#: resolution, interpreter start, module import, rule evaluation, JSON out —
#: and each carries :data:`PROBE_KEY`, which is what makes it write nothing.
#:
#: The marker is the guarantee, not the payload. These were once chosen to be
#: harmless on their own (no file_path, no session_id), and that held only
#: until the hook found the caller's identity by another route: it falls back
#: to ``INVARIANT_HOST_SESSION_ID`` from the environment ``doctor`` passes on,
#: so the session-end probe ended the caller's real host binding. Scrubbing
#: that variable would close one route and leave the rest — the cwd and the
#: worktree resolve a session too — so inertness is enforced at the store.
_PROBE_PAYLOADS = {
    "session-start": {PROBE_KEY: True},
    "user-prompt-submit": {PROBE_KEY: True},
    "pre-tool-use": {
        PROBE_KEY: True,
        "tool_name": "Bash",
        "tool_input": {"command": "true"},
    },
    "post-tool-use": {PROBE_KEY: True, "tool_name": "Read", "tool_input": {}},
    # `stop_hook_active` is what a real turn sets once the closeout has already
    # been demanded, so the probe exercises the whole path and provably cannot
    # block: a liveness check that could halt the caller's turn would be a
    # worse defect than the outage it looks for.
    "stop": {PROBE_KEY: True, "stop_hook_active": True},
    "session-end": {PROBE_KEY: True},
    "notification": {
        PROBE_KEY: True,
        "notification_type": "idle_prompt",
        "message": "invariant doctor probe",
    },
}


def is_probe(payload: object) -> bool:
    """Whether a parsed hook payload is ``doctor``'s probe."""
    return isinstance(payload, dict) and payload.get(PROBE_KEY) is True


# -- attention -----------------------------------------------------------
#
# What a hook event says about whether the host is waiting on its person.
# This module translates the hook PROTOCOL into that fact — which notification
# type means a permission prompt, which tool is a question — and stops there:
# the CLI writes it into the oracle's sidecar, so this module imports nothing
# of the oracle's and the host adapter stays a layer below it (ADR-007 D6).

ATTENTION_WAITING = "waiting"
ATTENTION_ANSWERED = "answered"
ATTENTION_PROMPT = "prompt"
ATTENTION_TURN_STOPPED = "turn-stopped"
ATTENTION_TURN_ENDED = "turn-ended"
ATTENTION_HOST_ENDED = "host-ended"
ATTENTION_BACKGROUND_STARTED = "background-started"
ATTENTION_BACKGROUND_DONE = "background-done"


@dataclass(frozen=True)
class AttentionSignal:
    """One hook event's news about the host's attention, host-agnostic.

    ``kind`` and ``message`` accompany ``waiting``; ``rules`` accompanies
    ``turn-ended`` and names the closeout rules that refused the turn, if any.
    ``turn-stopped`` is the Stop as it arrives, before the closeout has said
    anything; see :meth:`ClaudeCodeHost.arrival_attention`.

    ``background-started`` names, in ``tasks``, the one piece of work the
    main turn launched in the background, and in ``kind`` whether it is an
    ``agent`` or a ``command``. ``background-done`` is a completion notice
    arriving as a turn; ``tasks`` are the ones it says finished (BUG_094).
    """

    op: str
    kind: str | None = None
    message: str | None = None
    rules: tuple[str, ...] = ()
    tasks: tuple[str, ...] = ()


#: ``notification_type`` values as Claude Code sends them.
_NOTIFICATION_TYPES = {
    "permission_prompt": "permission",
    "elicitation_dialog": "question",
    "idle_prompt": "idle",
}

#: The fallback when the type is missing or renamed: the field is newer than
#: the event, and a version that spells it differently must still turn a
#: permission prompt into a waiting host. Asked in order; first match wins.
_NOTIFICATION_PATTERNS = (
    (re.compile(r"permission", re.IGNORECASE), "permission"),
    (re.compile(r"waiting for your input|\bidle\b", re.IGNORECASE), "idle"),
    (re.compile(r"question|needs your (?:input|answer|response)", re.IGNORECASE), "question"),
)

#: The tool whose call puts a question to the person.
_QUESTION_TOOL = "AskUserQuestion"

#: The shell tool, whose ``tool_response`` names a ``backgroundTaskId`` when
#: the command runs on in the background.
_SHELL_TOOL = "Bash"

#: A task id becomes a key in the attention record: a plain name, bounded.
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: How Claude Code tells the main turn background work ended: a user turn
#: whose text is one or more ``<task-notification>`` blocks, each with the
#: task's ``<task-id>`` and a ``<status>``. The tags are what is read; the
#: prose around them is not, except the one note that says the agent stopped
#: with work of its own still running and will notify again.
_TASK_NOTIFICATION = "<task-notification>"
_TASK_BLOCK = re.compile(r"<task-notification>(.*?)</task-notification>", re.DOTALL)
_TASK_DONE = frozenset({"completed", "failed", "killed", "stopped"})
_STILL_RUNNING = re.compile(r"<note>[^<]*\bstill running\b", re.IGNORECASE)


def notification_kind(payload: dict) -> str | None:
    """What a ``Notification`` payload says the host is waiting on, if anything."""
    kind = _NOTIFICATION_TYPES.get(str(payload.get("notification_type") or ""))
    if kind:
        return kind
    message = payload.get("message")
    if not isinstance(message, str):
        return None
    for pattern, named in _NOTIFICATION_PATTERNS:
        if pattern.search(message):
            return named
    return None


def _question_text(tool_input: object) -> str | None:
    """The first question an ``AskUserQuestion`` call asks, when readable."""
    questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if isinstance(questions, list) and questions and isinstance(questions[0], dict):
        text = questions[0].get("question")
        return text if isinstance(text, str) else None
    return None


#: The agent kind's two rows from `asset_checks`, by label.
CHECK_AGENTS_LOADABLE = "agents loadable by host"
CHECK_AGENTS_CURRENT = "library agents current"


@dataclass
class AssetPlan:
    """What an install would do to one target."""

    kind: str
    identity: str
    source: Path
    target: Path
    state: str
    write: bool
    #: What the host reads, before its marker: an agent rendered from its
    #: neutral source and its overlay, any other kind the source as shipped.
    text: str
    #: The two inputs' digests the marker records beside the witness (the A6
    #: ruling 1): the shipped source's, and the overlay's or ``None``.
    source_digest: str = ""
    overlay_digest: str | None = None
    #: For a stale copy, which input moved (``assets.changed_input``).
    why: str | None = None


@dataclass
class InstallResult:
    """What an install DID, in two lists rather than one.

    It used to return one list — every asset, all overwritten — and the command
    printed "installed N asset(s)" with no mention that one of them had been
    changed by hand. A write and a skip are different facts, and the operator
    needs the skip most.
    """

    installed: list[str] = field(default_factory=list)
    #: (label, why) for a target left alone because it is the operator's work.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    #: (label, which input changed) for each stale copy rewritten.
    changed: list[tuple[str, str]] = field(default_factory=list)


def _IS_OURS(entry) -> bool:
    """Is this settings entry one this library wrote?

    The shim path is the identity, and it is the SAME test `_wire_settings`
    uses to avoid writing a duplicate. One test, both directions: an install
    that recognises its own entries and a removal that does not must be
    impossible to write.
    """
    return "/.claude/hooks/invariant-" in json.dumps(entry)


#: The plan state of a target whose render is refused, in a plan for removal
#: only: a copy nothing can be judged against is kept (see `plan_assets`).
UNCLASSIFIED = "not classifiable: its render is refused"


def _currency_detail(stale: list[str], repair: str) -> str:
    """The currency row's text, and — where an edit is the cause — what to do.

    A locally edited asset is a persistent FAILED check, and it must not read
    as a demand to discard the edit: the repair `doctor` names now leaves it
    alone, so the row has to say both that the divergence is the operator's own
    and how to reverse it if that is what they want. Without the second half
    the operator is looking at a red row with no way to clear it.
    """
    if not stale:
        return "all match the shipped version"
    detail = ", ".join(stale)
    if any(LOCAL in entry for entry in stale):
        detail += (
            f" — your edits are kept; {repair} --overwrite-local "
            f"replaces them with the shipped version"
        )
    return detail


def _verdict_row(verdict: Verdict) -> str:
    """A rule's verdict as one `doctor` row: the message, its detail and each
    fix, so the row says exactly what the rule says."""
    return "; ".join(
        [
            verdict.message,
            *([verdict.detail] if verdict.detail else []),
            *(f"try: {fix}" for fix in verdict.fixes),
        ]
    )


def classify_installed(shipped: str, target: Path) -> str:
    """How ``target`` diverges from the ``shipped`` text this host reads.

    The file read around `assets.classify`, which owns the rule. Every reader
    goes through the install plan, so `doctor`, both install commands and
    `uninstall` cannot disagree about what an edit is (BUG_051, BUG_106).
    ``shipped`` is the rendered text, not the neutral source: the host reads
    the render, so an installed copy is judged against it.
    """
    installed = target.read_text(encoding="utf-8") if target.is_file() else None
    return assets.classify(installed, shipped)


#: The files this host reads as standing instructions, project-relative. The
#: default of ``guidelines.instruction_files`` (ADR-012 D3): which file a host
#: reads is a host question, not a library constant.
INSTRUCTION_FILES = ("CLAUDE.md",)

#: Each neutral capability as this host's tools (ADR-012 D8). ``dispatch`` is
#: the host's own agent tool, the one :data:`AGENT_TOOLS` names today.
CAPABILITY_TOOLS = {
    "read": ("Read", "Grep", "Glob"),
    "edit": ("Edit", "Write"),
    "shell": ("Bash",),
    "dispatch": ("Agent",),
}

#: The order a rendered ``tools:`` line lists them in: the order every shipped
#: agent file carried before the sources were neutral, so the render is the
#: same bytes.
TOOL_ORDER = ("Bash", "Read", "Edit", "Write", "Grep", "Glob", "Agent")

#: This host's own words, which a neutral source must not speak (the A6
#: ruling 12): its tool names, and the tool it asks the user a question with.
HOST_VOCABULARY: tuple[str, ...] = (
    *TOOL_ORDER,
    *sorted(AGENT_TOOLS - set(TOOL_ORDER)),
    "AskUserQuestion",
)


def render_agent(source: AgentSource) -> str:
    """A neutral agent source as this host's agent file, before its marker.

    The capabilities become a ``tools:`` line and the section anchors are
    dropped. No model line is ever written: a model reaches this file only
    through the operator's ``host.tier_map`` (ADR-012 D9), which A7 builds.
    """
    granted = {
        tool for capability in source.capabilities for tool in CAPABILITY_TOOLS[capability]
    }
    tools = ", ".join(tool for tool in TOOL_ORDER if tool in granted)
    return "\n".join(
        [
            assets.FENCE,
            f"name: {source.name}",
            f"description: {source.description}",
            f"tools: {tools}",
            assets.FENCE,
            source.body(),
        ]
    )


class ClaudeCodeHost:
    INSTRUCTION_FILES = INSTRUCTION_FILES

    def __init__(self, root: Path, manifest: Manifest | None = None) -> None:
        self.root = Path(root)
        self.claude_dir = self.root / ".claude"
        #: The project's manifest, for the `agents` keys an install reads
        #: (`library`, `overlays`), the privacy scan and the isolation rule.
        #: ``None`` is a project with none yet (`init`): every shipped agent,
        #: and no overlay.
        self.manifest = manifest
        #: What the last :meth:`handle` call signalled about the host's
        #: attention; see there.
        self.attention: AttentionSignal | None = None
        #: Labels of the passing rows the last :meth:`asset_checks` call
        #: reported as warnings (agent drift), which `doctor` prints as `warn`.
        self.warned_rows: set[str] = set()

    # -- installation --------------------------------------------------

    def plan_install(self) -> list[str]:
        actions = [f"install {len(EVENTS)} hook shims into .claude/hooks/"]
        actions.append("wire the shims into .claude/settings.json")
        agents = self._library(assets.BY_NAME["agent"])
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
        """What `init` installs: the hooks, and the library agents through the
        same plan every other install reads, so an edited copy is kept here
        too."""
        self.install_hooks()
        self.install_assets(only="agent")

    def _wire_settings(self) -> None:
        settings = self._read_settings()

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

        settings_path = self.claude_dir / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    # -- removal -------------------------------------------------------
    #
    # The inverse of `install_hooks`, and it exists for the same reason that
    # method does: the operator's authority over their own project includes the
    # authority to leave. Adoption was one command and withdrawal was hand
    # surgery — delete each shim, then hand-edit the JSON `_wire_settings`
    # merged its entries INTO, and know which entries were the library's. Miss
    # one and the shim still fires; it then finds no binary on PATH and exits
    # silently by design, so the leftover is invisible until it is not
    # (IDEA_004).

    def plan_uninstall(
        self, remove_assets: bool = False, remove_manifest: bool = False
    ) -> dict:
        """Everything an uninstall would touch, before it touches any of it.

        Split from the doing so `--dry-run` can enumerate. Removal is the one
        operation whose dry run is not a nicety: what it takes away cannot be
        got back by running it again.
        """
        hooks_dir = self.claude_dir / "hooks"
        shims = sorted(
            hooks_dir / f"invariant-{event}.sh"
            for event in EVENTS
            if (hooks_dir / f"invariant-{event}.sh").is_file()
        )

        settings, wired = self._read_settings(), []
        for host_event, entries in (settings.get("hooks") or {}).items():
            for entry in entries:
                if _IS_OURS(entry):
                    wired.append(host_event)

        asset_files: list[Path] = []
        protected: list[str] = []
        if remove_assets:
            for step in self.plan_assets(for_removal=True):
                if step.state == ABSENT:
                    continue
                if step.write:
                    asset_files.append(step.target)
                else:
                    protected.append(f"{step.kind}:{step.identity}")

        manifest = self.root / ".claude" / "invariant.yaml"
        return {
            "shims": shims,
            "wired": sorted(set(wired)),
            "assets": asset_files,
            "protected": protected,
            "manifest": manifest if remove_manifest and manifest.is_file() else None,
        }

    def uninstall(
        self, remove_assets: bool = False, remove_manifest: bool = False
    ) -> dict:
        """Remove what this library wrote, and only that.

        Two properties matter more than the command existing:

        * **It removes only what it wrote.** `_wire_settings` recognises its own
          entries by the shim path; removal is the same match, and anything else
          in the settings file is left exactly as found. The same care that
          method takes with malformed JSON — refuse rather than overwrite —
          applies here, and more so: there it would cost the operator their
          hook wiring, here it would cost them the whole file on the way out.

        * **It never deletes the operator's data.** Sessions, work items,
          memories, architecture docs and the manifest survive by default, so
          uninstalling is not a decision about the backlog. An asset the
          operator has EDITED survives even `--remove-assets`, by the same rule
          `install_assets` follows: their work is not this command's to discard.
        """
        plan = self.plan_uninstall(
            remove_assets=remove_assets, remove_manifest=remove_manifest
        )

        for path in plan["shims"]:
            path.unlink()
        self._unwire_settings()
        for path in plan["assets"]:
            path.unlink()
            # A skill lives in its own directory; take the directory with it
            # once nothing is left in it, and never if something is.
            parent = path.parent
            if parent != self.claude_dir and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        if plan["manifest"] is not None:
            plan["manifest"].unlink()

        hooks_dir = self.claude_dir / "hooks"
        if hooks_dir.is_dir() and not any(hooks_dir.iterdir()):
            hooks_dir.rmdir()
        return plan

    def _read_settings(self) -> dict:
        settings_path = self.claude_dir / "settings.json"
        if not settings_path.is_file():
            return {}
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # Refuse, never overwrite. This file is the operator's, and this
            # library is a guest in it: on the way IN a bad parse would cost
            # them their hook wiring, and on the way OUT it would cost them the
            # whole file as the last thing the tool ever did to their project.
            raise UsageError(
                f".claude/settings.json is not valid JSON ({exc}); fix it "
                f"first, so nothing here overwrites your work"
            ) from exc
        return loaded if isinstance(loaded, dict) else {}

    def _unwire_settings(self) -> None:
        settings_path = self.claude_dir / "settings.json"
        if not settings_path.is_file():
            return
        settings = self._read_settings()
        hooks = settings.get("hooks")
        if not isinstance(hooks, dict):
            return

        for host_event in list(hooks):
            entries = hooks.get(host_event)
            if not isinstance(entries, list):
                continue
            kept = [entry for entry in entries if not _IS_OURS(entry)]
            # An emptied list is removed rather than left as `[]`. A leftover
            # empty key is the visible residue of a tool that claimed to be
            # gone, which is the impression this command exists not to leave.
            if kept:
                hooks[host_event] = kept
            else:
                del hooks[host_event]

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
        if len(missing) == len(EVENTS):
            # Nothing is half-gone: this is a project that uninstalled, or one
            # that never installed. Offering `hooks install` here pushed back
            # toward the state the operator was leaving, which is not a repair
            # (IDEA_004). Naming `init` states the way back without pretending
            # a deliberate removal is damage.
            detail = "not installed — run 'invariant init' to set this project up"
        elif missing:
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
                    check=False,
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

    def asset_checks(
        self, only: str | None = None, repair: str = "invariant assets install"
    ) -> list[tuple[str, bool, str]]:
        """Loadability and currency, for every kind the host reads.

        Both questions for every kind, because they answer different things and
        neither may stand in for the other — that conflation is what left four
        agents unloadable while `verify` called them current.

        Loadability covers PROJECT-OWNED files too. A local command that
        silently fails to load is the same outage as a shipped one, and until
        now nothing looked at commands or skills at all.

        ``only`` narrows to one kind and ``repair`` names the command the rows
        point at, so `agents install` reads these rows rather than a second
        copy of them: it once kept its own, with its own classifier loop.
        """
        checks: list[tuple[str, bool, str]] = []
        self.warned_rows = set()
        for kind in assets.KINDS:
            if only and kind.name != only:
                continue
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
            # wrong thing by the check whose whole job is that question. An
            # agent the project left out of agents.library is not missing.
            #
            # A plan that is refused (an unshipped agents.library name, an
            # overlay that does not apply) is reported on the currency row,
            # naming its fix, rather than taking `doctor` down with it.
            refused: InvariantError | None = None
            try:
                shipped = self._library(kind)
                stale = None if kind.name == "agent" else self._stale_for(kind)
            except InvariantError as exc:
                refused, shipped, stale = exc, [], None
            present = {kind.identity(path) for path in installed}
            for source in shipped:
                if kind.identity(source) not in present:
                    unloadable.append(f"{kind.identity(source)} (absent)")

            checks.append(
                (
                    f"{kind.name}s loadable by host",
                    not unloadable,
                    (
                        ", ".join(unloadable) + f"  — repair with: {repair}"
                        if unloadable
                        else f"{len(installed)} {kind.name}(s) parse and are addressable"
                    )
                    if installed or unloadable
                    else f"no {kind.name}s installed",
                )
            )

            if kind.name == "agent":
                # The agents' currency is `agents.rendered_current`'s answer,
                # asked of this host's own plan, so the row and the rule cannot
                # disagree (ADR-012 D8, the A6 ruling 10). Drift (absent,
                # older, edited locally) warns, as the rule does: the row
                # passes and is listed in `warned_rows`. A REFUSED plan is a
                # defect, not drift, and fails. An empty library has no row.
                refusals: list[InvariantError] = []

                def plan(noted: list[InvariantError] = refusals) -> list[AgentCopy]:
                    try:
                        return self.agent_copies()
                    except InvariantError as exc:
                        noted.append(exc)
                        raise

                verdict = agents_currency(plan)
                if not verdict.skipped:
                    drift = not verdict.ok and not refusals
                    if drift:
                        self.warned_rows.add(CHECK_AGENTS_CURRENT)
                    checks.append(
                        (CHECK_AGENTS_CURRENT, verdict.ok or drift, _verdict_row(verdict))
                    )
            elif refused is not None:
                checks.append(
                    (
                        f"library {kind.name}s current",
                        False,
                        f"{refused} — {repair} refuses until it is fixed",
                    )
                )
            elif stale is not None:
                checks.append(
                    (
                        f"library {kind.name}s current",
                        not stale,
                        _currency_detail(stale, repair),
                    )
                )
        return checks

    def agent_copies(self) -> list[AgentCopy]:
        """Each library agent's installed copy against the render, off the plan.

        The plan `agents install --dry-run` prints, as the host-neutral
        answer `agents.rendered_current` reads: the CLI hands this method to
        the rule context, since `core/` imports no host. A refused plan raises,
        as every install does; the rule reports it.
        """
        return [
            AgentCopy(
                name=step.identity,
                state=step.state,
                current=step.state == CURRENT,
                edited=step.state == LOCAL,
                why=step.why,
            )
            for step in self.plan_assets(only="agent")
        ]

    def _sources_for(self, kind) -> list[Path]:
        """The files this library ships of a kind, in a stable order."""
        source_root = DATA_DIR / kind.directory
        if not source_root.is_dir():
            return []
        if not kind.nested_file:
            return sorted(source_root.glob("*.md"))
        return sorted(
            c / kind.nested_file
            for c in source_root.iterdir()
            if (c / kind.nested_file).is_file()
        )

    def _stale_for(self, kind) -> list[str] | None:
        """Which shipped assets of this kind differ from what we ship.

        Read off the install plan, so the currency row and the install it
        points at give one answer. ``None`` when the library ships none of
        this kind — a project owning every command is not drift, and reporting
        "0 stale" would imply this library had an opinion about files it never
        wrote.
        """
        plan = self.plan_assets(only=kind.name)
        if not plan:
            return None
        return [f"{s.identity} ({s.state})" for s in plan if s.state != CURRENT]

    def _library(self, kind) -> list[Path]:
        """The shipped sources an install covers, in a stable order.

        For agents, those `agents.library` lists (the A6 ruling 11), so a
        project can leave one out and own an agent of that name under
        `agents.local`, which no install touches. A listed name the library
        does not ship is refused naming the ones it does. With no manifest,
        every shipped agent.
        """
        sources = self._sources_for(kind)
        if kind.name != "agent" or self.manifest is None:
            return sources
        library = list(self.manifest.get("agents.library") or [])
        shipped = {kind.identity(path) for path in sources}
        unshipped = [name for name in library if name not in shipped]
        if unshipped:
            raise InvariantError(
                f"agents.library names {', '.join(repr(n) for n in unshipped)}, which "
                f"this library does not ship; it ships {', '.join(sorted(shipped))}. "
                f"Remove it from agents.library; a project's own agent belongs "
                f"in agents.local."
            )
        return [path for path in sources if kind.identity(path) in library]

    def agent_source(self, name: str) -> AgentSource:
        """One library agent as the host would install it: its neutral source,
        parsed, with the project's overlay applied.

        A name the library does not ship is refused naming the ones it does.
        """
        kind = assets.BY_NAME["agent"]
        sources = {kind.identity(path): path for path in self._sources_for(kind)}
        if name not in sources:
            raise UsageError(
                f"the library ships no agent named {name!r}; it ships "
                + ", ".join(sorted(sources))
            )
        return self._agent(name, sources[name].read_text(encoding="utf-8"))[0]

    def _agent(self, name: str, raw: str) -> tuple[AgentSource, str | None]:
        """The source merged with its overlay, and the overlay's digest.

        Every refusal is raised before anything is written, each with its
        fix: a source that does not parse, an overlay outside the project,
        exempt from isolation, missing or invalid, and merged text the
        privacy scan flags.
        """
        parsed = agent_sources.parse(raw, name)
        overlay = self._overlay_text(name)
        if overlay is None:
            return parsed, None
        rel, text = overlay
        subject = f"the overlay {rel} for {name!r}"
        try:
            merged = agent_sources.merge(parsed, text)
        except OverlayError as exc:
            raise OverlayError(exc.problems, subject) from exc
        self._scan_overlay(subject, rel, render_agent(parsed), render_agent(merged))
        return merged, digest_of(text)

    def _overlay_text(self, name: str) -> tuple[str, str] | None:
        """``(project-relative path, text)`` of the agent's overlay, if named.

        Only a path `agents.overlays` names is read: there is no implicit
        location (the A6 ruling 6). An overlay is policy (ADR-012 D10), so a
        path exempt from session isolation is refused like one outside the
        project: an exempt overlay would land on the base branch unreviewed.
        """
        if self.manifest is None:
            return None
        configured = (self.manifest.get("agents.overlays") or {}).get(name)
        if not configured:
            return None
        subject = f"the overlay for {name!r} ({configured})"
        convention = f"docs/agents/{name}.yaml"
        rel = project_relative(configured, [self.root])
        if rel is None or rel == ".":
            raise OverlayError(
                [
                    OverlayProblem(
                        f"agents.overlays.{name} names a path outside the project",
                        f"give a project-relative path, by convention {convention}",
                    )
                ],
                subject,
            )
        path = self.root / rel
        if is_exempt_path(self.manifest, self.root, path):
            raise OverlayError(
                [
                    OverlayProblem(
                        f"{rel} is exempt from session isolation, and an overlay is "
                        f"policy, reviewed like any edit (ADR-012 D10)",
                        f"move it outside session.exempt_paths and the declared "
                        f"stores, by convention {convention}",
                    )
                ],
                subject,
            )
        try:
            return rel, path.read_text(encoding="utf-8")
        except OSError as exc:
            problem = "no such file" if not path.exists() else type(exc).__name__
            raise OverlayError(
                [
                    OverlayProblem(
                        f"{rel}: {problem}",
                        f"create it, or remove agents.overlays.{name}",
                    )
                ],
                subject,
            ) from exc

    def _scan_overlay(self, subject: str, rel: str, base: str, merged: str) -> None:
        """Refuse merged text the privacy scan flags, whatever `privacy.scope`
        says: it is about to be written into the project. Only what the
        overlay brings counts; the shipped render is the library's."""
        scanner = PrivacyScanner.from_manifest(self.manifest)
        shipped = Counter((f.pattern, f.raw) for f in scanner.scan_text(base))
        brought = []
        for finding in scanner.scan_text(merged):
            key = (finding.pattern, finding.raw)
            if shipped[key]:
                shipped[key] -= 1
            else:
                brought.append(finding)
        if brought:
            named = ", ".join(f"{f.pattern} {f.redacted}" for f in brought)
            raise OverlayError(
                [
                    OverlayProblem(
                        f"the merged text fails the privacy scan ({named})",
                        f"remove it from {rel}, then install again",
                    )
                ],
                subject,
            )

    def _shipped_text(self, kind, source: Path) -> tuple[str, str, str | None]:
        """What the host reads for one shipped asset, before its marker, with
        the source's and the overlay's digests.

        An agent is rendered from its neutral source and its overlay; a source
        or an overlay that does not validate is refused naming every problem,
        before anything is written.
        """
        text = source.read_text(encoding="utf-8")
        if kind.name != "agent":
            return text, digest_of(text), None
        merged, overlay_digest = self._agent(kind.identity(source), text)
        return render_agent(merged), digest_of(text), overlay_digest

    def _target_for(self, kind, identity: str) -> Path:
        base = self.claude_dir / kind.directory
        if kind.nested_file:
            return base / identity / kind.nested_file
        return base / f"{identity}.md"

    def plan_assets(
        self,
        only: str | None = None,
        overwrite_local: bool = False,
        *,
        for_removal: bool = False,
    ) -> list[AssetPlan]:
        """What an install would do to each target, before it does any of it.

        The one plan: `doctor`'s currency rows, both install commands, `init`
        and `uninstall --remove-assets` all read it, so none of them can
        classify a target differently from the others.

        Separated from the doing so that ``--dry-run`` can enumerate. It used
        to return before any per-file work and print "would reinstall library
        assets", which names nothing it would flatten — the one thing a dry run
        of a destructive command is for (BUG_051).

        ``for_removal`` is `uninstall`'s plan. An agent whose render is
        refused (an overlay that does not apply, a source that does not parse)
        refuses an install, which would otherwise write a render the overlay
        was meant to change. A removal writes nothing, so it plans that agent
        through :meth:`_removal_step` instead of refusing every other target
        with it.
        """
        plan: list[AssetPlan] = []
        for kind in assets.KINDS:
            if only and kind.name != only:
                continue
            for source in self._library(kind):
                identity = kind.identity(source)
                target = self._target_for(kind, identity)
                try:
                    text, source_digest, overlay_digest = self._shipped_text(kind, source)
                except InvariantError:
                    if not (for_removal and kind.name == "agent"):
                        raise
                    plan.append(self._removal_step(kind, identity, source, target))
                    continue
                state = classify_installed(text, target)
                why = None
                if state == OLDER:
                    installed = target.read_text(encoding="utf-8")
                    why = assets.changed_input(installed, source_digest, overlay_digest)
                # Everything but a local edit is written, including a target
                # that already matches: rewriting an identical file costs
                # nothing, and a repair command that reports "installed 0"
                # on a healthy project reads as a failure. The ONE case that
                # is withheld is the one where writing would destroy work.
                plan.append(
                    AssetPlan(
                        kind=kind.name,
                        identity=identity,
                        source=source,
                        target=target,
                        state=state,
                        write=not (state == LOCAL and not overwrite_local),
                        text=text,
                        source_digest=source_digest,
                        overlay_digest=overlay_digest,
                        why=why,
                    )
                )
        return plan

    def _removal_step(self, kind, identity: str, source: Path, target: Path) -> AssetPlan:
        """An agent whose render is refused, planned for removal only.

        A removal asks one thing of a copy: is it still what some install
        wrote? The classifier answers that from the copy's own witness, so it
        is judged against the render without its overlay: an untouched copy
        is removed whatever overlay wrote it, and an edited one is kept. A
        source that does not parse leaves nothing to judge against, and the
        copy is kept (the A6.3 review). Neither is ever written.
        """
        try:
            text = render_agent(
                agent_sources.parse(source.read_text(encoding="utf-8"), identity)
            )
        except InvariantError:
            state = UNCLASSIFIED if target.is_file() else ABSENT
            return AssetPlan(
                kind=kind.name, identity=identity, source=source, target=target,
                state=state, write=False, text="",
            )
        state = classify_installed(text, target)
        return AssetPlan(
            kind=kind.name, identity=identity, source=source, target=target,
            state=state, write=state != LOCAL, text=text,
        )

    def install_assets(
        self, only: str | None = None, overwrite_local: bool = False
    ) -> InstallResult:
        """Install library-shipped assets, leaving the operator's edits alone.

        A file in the operator's repository is the operator's. This library may
        say "that differs from what I ship" — it already does, per asset, and
        it can tell an edit apart from a stale copy — and it may not silently
        revert one. It used to overwrite every target unconditionally while
        holding exactly the knowledge that would have protected the edit, and
        `doctor` reported such a file as a FAILED check, so the repair the tool
        recommended was the destructive one (BUG_051).

        The tension is real and is settled rather than dodged: these are
        MANAGED files with a digest marker, so "managed" reasonably means the
        library owns the bytes. But the project already offers `agents.local`
        for project-owned agents, and an operator who edits a shipped one is
        saying something — usually "your version is nearly right". So the
        library owns the SHIPPED copy and the operator owns the INSTALLED one,
        and an install that would diverge from what is on disk is a question
        rather than a write.
        """
        result = InstallResult()
        for step in self.plan_assets(only=only, overwrite_local=overwrite_local):
            label = f"{step.kind}:{step.identity}"
            if not step.write:
                result.skipped.append((label, step.state))
                continue
            try:
                rendered = assets.render_managed(
                    step.text,
                    source_digest=step.source_digest,
                    overlay_digest=step.overlay_digest,
                )
            except ValueError as exc:
                raise InvariantError(
                    f"{step.kind} {step.identity!r} has no YAML frontmatter starting "
                    f"on line 1. The host identifies it by that block; a file "
                    f"without one is copied into place and then silently "
                    f"ignored, so installing it would look like success and "
                    f"produce nothing."
                ) from exc
            step.target.parent.mkdir(parents=True, exist_ok=True)
            step.target.write_text(rendered, encoding="utf-8")
            result.installed.append(label)
            if step.why:
                result.changed.append((label, step.why))
        return result

    # -- request handling ----------------------------------------------

    def arrival_attention(self, event: str) -> AttentionSignal | None:
        """What ``event`` says about the host's attention before any work is done.

        Only a Stop says anything this early: the host has ended the turn,
        whatever the closeout then decides. The closeout is the slow part of
        the Stop path, and the host cancels a hook at its timeout, so a turn
        end learned only from :meth:`handle` is lost exactly when the closeout
        runs long (BUG_095). The caller records this before calling
        :meth:`handle`, which then refines it with the closeout's verdict.
        """
        if event == "stop":
            return AttentionSignal(ATTENTION_TURN_STOPPED)
        return None

    def handle(self, event: str, raw: str, ctx) -> dict:
        """The host's response to ``event``; :attr:`attention` is left set too.

        ``attention`` is what the event says about whether the host is waiting
        on its person — an :class:`AttentionSignal`, or ``None`` when it says
        nothing. It is a by-product the caller may record, never an input to
        the response, which is computed first and does not read it.
        """
        self.attention = None
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {}

        if event == "pre-tool-use":
            response = self._pre_tool_use(payload, ctx)
            self.attention = _signal_or_none(lambda: _question_signal(payload, response))
            return response
        if event == "post-tool-use":
            response = self._post_tool_use(payload, ctx)
            self.attention = _signal_or_none(
                lambda: _background_launch(payload)
            ) or AttentionSignal(ATTENTION_ANSWERED)
            return response
        if event == "user-prompt-submit":
            response = _context_response("UserPromptSubmit", build_context(ctx))
            self.attention = _signal_or_none(
                lambda: _task_notification(payload)
            ) or AttentionSignal(ATTENTION_PROMPT)
            return response
        if event == "session-start":
            return _context_response("SessionStart", build_context(ctx, greeting=True))
        if event == "stop":
            return self._stop(payload, ctx)
        if event == "session-end":
            response = self._session_end(ctx)
            self.attention = AttentionSignal(ATTENTION_HOST_ENDED)
            return response
        if event == "notification":
            return self._notification(payload)
        return {}

    def _notification(self, payload: dict) -> dict:
        """The host is telling its person something; nothing here can answer.

        The only event that sees a permission prompt. It carries no decision —
        the host discards anything a Notification hook returns but its exit
        status — so the response is always empty and the event's whole value
        is the attention it signals.
        """
        kind = notification_kind(payload)
        if kind is not None:
            message = payload.get("message")
            self.attention = AttentionSignal(
                ATTENTION_WAITING,
                kind=kind,
                message=message if isinstance(message, str) else None,
            )
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

        # `ask` cannot reach this surface — the host discards a question asked
        # at the end of a turn — so an ask-level verdict blocks here like any
        # other. The one-shot token is the appeal that does reach it, and it is
        # why the token exists alongside the ask level rather than instead of
        # it (AUTH-01).
        blocking, spent = _spend_overrides(
            [v for v in verdicts if v.blocks], rule_ctx, ctx, surface="stop"
        )
        notices = [_override_notice(v, record) for v, record in spent]
        self.attention = _signal_or_none(
            lambda: AttentionSignal(
                ATTENTION_TURN_ENDED, rules=tuple(str(v.rule_id) for v in blocking)
            )
        )

        if blocking:
            return {
                "decision": "block",
                "reason": "invariant — the session is not finished:\n"
                + "\n".join(v.render() for v in blocking),
            }

        notices += [v.render() for v in verdicts if v.warns]
        if notices:
            # Advisory here means advisory: surfaced, and the turn still ends.
            return {"systemMessage": "\n".join(notices)}
        return {}

    def _bind_claimed_session(self, payload: dict, ctx) -> None:
        """Record which host session a `session start` or `resume` claimed.

        Both commands run through a shell, so the CLI never sees the host
        identifier; PostToolUse has it and brackets the exact call, so it reads
        the id out of that command's own output rather than guessing.

        **Both verbs bind through `adopt_host_session`, and `resume` is not an
        exception to that.** What is owed here is the BINDING, and only the
        binding: the resume itself has already happened inside the command
        whose output this is, which incremented `resumed_count` and wrote the
        `resumed` standstill row. Calling `SessionDomain.resume` again would
        count one pickup twice and leave two rows describing one event.
        `adopt_host_session` is the half the CLI could not do and nothing more
        — and it is sufficient, because since the session-outlives-its-host
        increment its refusal names a *live* binding only, so a SPENT one goes
        through to `_bind_host`, which clears `host_ended` (without which the
        session resolves to nobody for every later hook) and keeps the outgoing
        id in `hosts`.

        **The same call claims the handoff this host was launched for.** The
        handoff launcher sets `HANDOFF_ENV` in front of `claude`, and this
        hook subprocess inherits the environment its host was started with, so
        a token here names the one note this host session exists to pick up
        (IDEA_008). It is consumed on the first `start` or `resume` and never
        at `SessionStart`, which must not write; the domain makes every later
        claim with the same token a no-op.
        """
        if not ctx.host_session_id:
            return
        tool_input = payload.get("tool_input") or {}
        command = str(tool_input.get("command", ""))
        if not _SESSION_CLAIM.search(command):
            return

        session_id = _bind_claimed_session_id(_response_text(payload))
        if not session_id:
            return

        try:
            ctx.sessions.adopt_host_session(session_id, ctx.host_session_id)
        except InvariantError:
            # Already bound, or this host already owns one. Both are the 1:1
            # rule working; neither is a reason to fail a completed command,
            # and PostToolUse must never turn a refusal into a denial.
            pass

        token = os.environ.get(HANDOFF_ENV)
        if token:
            try:
                ctx.sessions.claim_handoff(token, session_id, ctx.host_session_id)
            except InvariantError:
                # An unreadable record is not a reason to fail a command that
                # already ran; the note then simply goes on being offered.
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
        target = _edit_target(payload)
        if target is None:
            return

        resolution = ctx.sessions.resolve(
            ctx.cwd, target=target, host_session_id=ctx.host_session_id
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

    def _report_landed_edit(self, payload: dict, ctx) -> None:
        """Tell the session domain an edit tool's bytes landed, and where.

        That is all this does. Whether the edit was a session's FIRST — the
        writer identified, the session in ``isolated``, the path inside its own
        worktree and not exempt — is `SessionDomain.advance_on_edit`'s
        judgement, made where the transition lives (BUG_062).

        PostToolUse is the right moment for the reason it is for
        `_bind_by_successful_edit` above: the tool has provably run. A call
        refused at pre-tool-use never gets here, so a refused edit cannot
        advance anything. A write made through a shell is deliberately not
        reported — a command line names no destination the hook can trust,
        and recovering one by parsing it would be a guess about where bytes
        landed (the reason BUG_027 screened staged content instead).
        """
        target = _edit_target(payload)
        if target is None:
            return
        try:
            ctx.sessions.advance_on_edit(
                target, cwd=ctx.cwd, host_session_id=ctx.host_session_id
            )
        except InvariantError:
            # The edit has already happened. Failing to record that it moved
            # the session is a lifecycle row missed, and the operator can still
            # walk the edge by hand; raising would fail a completed tool call.
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
        """The surface where the operator meets the tool, and the only one with
        a human in the loop.

        Three decisions leave here, not two. The host's contract has carried
        ``ask`` alongside ``allow`` and ``deny`` all along — it routes the call
        to the human with the reason attached — and this library only ever
        emitted ``deny``, so the human was never offered the choice on a gate
        the project might want to be asked about rather than stopped by. That
        is a delivery question, which is the axis ``Level`` already exists on
        (AUTH-01).

        A ``deny`` still has an appeal, because ``ask`` is not one: an
        ask-level rule is one the project chose to soften, and the operator
        cannot soften a rule from inside the call it is refusing. A one-shot
        override granted beforehand is spent here, and spending it is recorded.
        """
        rule_ctx = ctx.rule_context(payload=payload)
        verdicts = evaluate_event("pre-tool-use", rule_ctx)

        asking = [v for v in verdicts if v.asks]
        denying, spent = _spend_overrides(
            [v for v in verdicts if v.blocks and not v.asks],
            rule_ctx,
            ctx,
            surface="pre-tool-use",
        )
        notices = [_override_notice(v, record) for v, record in spent]
        notices += [v.render() for v in verdicts if v.warns]

        if denying or asking:
            stopped = denying or asking
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny" if denying else "ask",
                    "permissionDecisionReason": "\n".join(
                        v.render() for v in stopped
                    ),
                }
            }
        if notices:
            return _context_response("PreToolUse", "\n".join(notices))
        return {}

    def _post_tool_use(self, payload: dict, ctx) -> dict:
        # Three facts get recorded here, for the same reason: a gate that reads
        # what happened is a gate that cannot be satisfied by assertion.
        # Reading an architecture doc feeds the docs-first gate; dispatching a
        # subagent feeds the agent-role gates; and so does being one. A fourth
        # is recorded for audit and read by no gate: the agent that submitted
        # a verdict (ADR-012 DN-12).
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
        # A call that reached into another project and could not be placed
        # there records NOTHING, here or anywhere: not the binding, not the
        # first edit, not the actor, not the evidence. Recording it in this
        # project by the host's binding is how a gate here went green on work
        # done there (BUG_010, ADR-011 D7). Placed calls never get here with
        # this set; they arrive on a context rooted in the project they name.
        unplaced = getattr(ctx, "unplaced", None)
        if unplaced is not None:
            return self._report_unplaced(tool, payload, unplaced)

        if tool == "Bash":
            self._bind_claimed_session(payload, ctx)
        elif tool in EDIT_TOOLS:
            self._bind_by_successful_edit(payload, ctx)
            # After the binding, so an edit that just taught the host which
            # session it is can also be that session's first edit.
            self._report_landed_edit(payload, ctx)

        self._record_acting_agent(payload, ctx)
        if tool == "Bash":
            # After the actor row, and through the same place check: the agent
            # stamped on a verdict is the agent the actor row would name.
            self._stamp_verdict_agent(payload, ctx)

        if tool in EDIT_TOOLS:
            return self._edit_advisory(payload, ctx)
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

        # Ordered before the `session is None` return below, because a read
        # that resolved to NOBODY is the case that wedges the docs gate and it
        # still has somebody to tell: `ambiguous` means several live sessions
        # were candidates, which is not the ungoverned turn that return is
        # about (BUG_046).
        if tool == "Read":
            return self._record_docs_read(tool_input, resolution, ctx)

        if session is None:
            # Nobody is identifiable, so there is nothing to record and nobody
            # to tell. Deliberately silent: an ungoverned turn in a governed
            # repository is ordinary, and a note on every such dispatch would
            # be noise rather than the signal below.
            return {}

        try:
            agent_type = dispatched_agent(payload)
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

    @staticmethod
    def _edit_advisory(payload: dict, ctx) -> dict:
        """What the edit that just landed carries about the guidelines.

        ADR-012 ruling 9, D7, DN-8 and the A5 rulings D4 to D9: advisory
        context after the edit, never a deny. The engine decides both halves,
        the new findings (``core.rules.edit_advisory``) and the judgement
        rules' statements (``core.rules.edit_guidelines``), and
        ``core.guidelines.render_edit`` writes them under one cap,
        ``guidelines.injection.max_lines``; this only wraps the lines. The
        rule context is built at most once, and only when the engine knows a
        ruleset is configured, so a project without one pays nothing here.
        The edit has already happened, so nothing that goes wrong here may
        fail the call: any exception leaves the hook silent.
        """
        target = _edit_target(payload)
        manifest = getattr(ctx, "manifest", None)
        if target is None or manifest is None:
            return {}
        try:
            built: list = []

            def context():
                if not built:
                    built.append(ctx.rule_context(payload=payload))
                return built[0]

            advisory = edit_advisory(manifest, target, context)
            guidance = edit_guidelines(manifest, target, context)
            cap = manifest.get("guidelines.injection.max_lines", ADVISORY_MAX_LINES)
            return _context_response(
                "PostToolUse", "\n".join(render_edit(advisory, guidance, int(cap)))
            )
        except Exception:  # noqa: BLE001 - an advisory may never fail its hook
            return {}

    @staticmethod
    def _report_unplaced(tool: str, payload: dict, unplaced) -> dict:
        """Say that a call into another project was recorded nowhere, if it matters.

        The restraint is `_record_docs_read`'s (BUG_046): only a call that
        would have been evidence speaks. A dispatch naming an agent always
        would. A `Read` would only if it is an architecture doc of THAT
        project, asked through the gate's own helper against that project's
        manifest. An edit outside a live worktree is nobody's first edit, so
        nothing any gate waits for was lost and it stays silent (ADR-011 D7).
        """
        agent = dispatched_agent(payload) if tool in AGENT_TOOLS else None
        if agent:
            what = str(agent)
        elif tool == "Read" and unplaced.manifest is not None:
            try:
                if not is_architecture_doc(
                    unplaced.manifest, unplaced.project, unplaced.path
                ):
                    return {}
            except InvariantError:  # pragma: no cover - manifest already loaded
                return {}
            what = "this read"
        else:
            return {}

        if unplaced.reason == "ambiguous":
            why = (
                "the paths it names reach more than one session ("
                + ", ".join(unplaced.sessions)
                + "), and a call is placed only when every match lands on one"
            )
        elif unplaced.reason == "unreadable":
            why = f"that project's manifest could not be read ({unplaced.detail})"
        else:
            why = (
                "no live session's worktree there contains it (a torn-down or "
                "stranded session's worktree does not count)"
            )
        return _context_response("PostToolUse", _UNPLACED_CALL.format(
            what=what, path=unplaced.path, project=unplaced.project, why=why
        ))

    def _record_docs_read(self, tool_input: dict, resolution, ctx) -> dict:
        """Record a Read as docs evidence, or say why it was not.

        The recording condition is `proven`, and stays `proven`. A row written
        on a guess is how a foreign session's work came to clear a blocking
        gate (BUG_028), and relaxing it on the `docs_read` key would be that
        regression by another door.

        What was wrong is the other half. `docs.read_architecture` is
        block-level, and while sessions are live the MAIN-CHECKOUT spelling of
        a document resolves to nobody in particular — so the read the agent
        just performed is discarded, and the gate then reports "no architecture
        doc has been read in this pipeline" with a remedy line advising the
        very action that was thrown away. Nothing told the two apart: a
        discarded read and an absent one looked identical from inside, which is
        the difference between a session that reports a bug and one that argues
        with the engine (BUG_046). This is `record_agent_run`'s lesson one
        branch over — a drop a blocking gate will read as absence gets said out
        loud.

        Silent for an ordinary file, and that restraint is the point. `Read`
        fires on everything an agent opens, so a note on every unattributed one
        would be noise on the common case rather than signal on the rare one.
        Only a read that WOULD have been evidence speaks, and only when there
        is a governed session to say it to.
        """
        target = tool_input.get("file_path")
        if not target:
            return {}

        if resolution.proven:
            try:
                ctx.pipelines.record_docs_read(resolution.session.id, str(target))
            except InvariantError:
                # Evidence is a side effect; see the dispatch branch above.
                pass
            return {}

        if resolution.session is None and not resolution.candidates:
            # `none`: no session was registered anywhere near this call. An
            # ungoverned read in a governed repository is ordinary and has no
            # gate waiting for it.
            return {}

        try:
            if not is_architecture_doc(ctx.manifest, ctx.cwd, target):
                return {}
        except InvariantError:  # pragma: no cover - manifest already loaded
            return {}

        return _context_response(
            "PostToolUse",
            _UNRECORDED_DOCS_READ.format(path=target, how=resolution.how),
        )

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

    def _stamp_verdict_agent(self, payload: dict, ctx) -> None:
        """Stamp the acting agent on the verdicts this shell call recorded.

        ADR-012 DN-12, A4 increment 8. A verdict is recorded by the CLI child
        of a shell call, and a shell call carries no hook payload, so the CLI
        cannot know who submitted it. This event can: Claude Code stamps
        ``agent_type`` on a subagent's own tool calls. It is the
        `_bind_claimed_session` shape: the call is matched by its command,
        and the records by the submission's token read out of that command's
        own output (the user's ruling c), never by a guess about which
        records are recent.

        The session is placed by `_actor_session`, the check an actor row
        clears, because the stamp claims the same thing an actor row does:
        this agent worked here. A main-loop turn names no agent, and a host
        that cannot name one sends none; either way nothing is stamped and
        the verdict, already accepted, stands. Silent, like the actor row:
        evidence for audit is never a reason to fail a completed call.
        """
        agent_type = payload.get("agent_type")
        if not isinstance(agent_type, str) or not agent_type:
            return
        tool_input = payload.get("tool_input") or {}
        command = str(tool_input.get("command", ""))
        if not _VERDICT_SUBMISSION.search(command):
            return
        tokens = _submission_tokens(_response_text(payload))
        if not tokens:
            return
        try:
            resolution = ctx.resolution_for_payload(payload)
            session = self._actor_session(resolution, payload, ctx)
            if session is None:
                return
            for token in tokens:
                ctx.pipelines.stamp_verdict_agent(session.id, token, agent_type)
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

        return ctx.sessions.by_worktree_containing(_edit_target(payload) or ctx.cwd)


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

_UNRECORDED_DOCS_READ = (
    "invariant — this read was NOT recorded as evidence for "
    "docs.read_architecture. {path} is an architecture doc, but nothing in "
    "this call proved WHICH session read it (resolved by {how!r}, which is a "
    "guess, not an identification), and evidence is never written on a guess: "
    "a pipeline that receives another session's reading clears a blocking gate "
    "on work it did not do.\n"
    "  the gate is block-level and will report this as 'no architecture doc "
    "has been read in this pipeline'. That is a DISCARDED read, not an absent "
    "one — reading it again by the same path will not change the verdict.\n"
    "  try: read the copy inside your session's own worktree, which is the "
    "spelling that identifies the reader (`invariant context` names it). A "
    "session with no worktree yet gets one with `invariant session isolate "
    "--execute`."
)

#: Said for a call that named a path in another project and could not be
#: placed there (ADR-011 D7). Nothing was recorded anywhere, the host's own
#: project included, and the turn that made the call is the one told.
_UNPLACED_CALL = (
    "invariant — {what} was NOT recorded anywhere. It names {path}, inside the "
    "invariant project at {project}, but {why}. A call is recorded in another "
    "project only when its path lies in exactly one live session worktree "
    "there (ADR-011); recording it in this host's project instead would credit "
    "a pipeline with work done elsewhere.\n"
    "  try: name exactly one live session worktree of that project, or drive "
    "its pipeline from a host session rooted in it"
)

_UNRECORDED_DISPATCH = (
    "invariant — {agent} was NOT recorded as evidence: session {session} has "
    "no pipeline running, so there is no gate for it to be evidence for. This "
    "is allowed; it is said out loud because agents.*_ran is block-level and a "
    "pipeline started after the fact will not contain this dispatch.\n"
    "  try: invariant pipeline start --task \"<what you are doing>\" first, or "
    "let the agent record itself by dispatching it after the pipeline exists"
)


#: The two commands that CLAIM a session for this host session:
#: `invariant session start <name>` and `invariant session resume <name>` —
#: with any flags, and however the command was reached (a bare call, or one
#: wrapped in `cd … &&`). Both run through a shell, which carries no hook
#: payload, so the binding has to be read out of their own stdout.
#:
#: **Exactly those two verbs, and not "any `session` subcommand".** The id
#: reader below matches `session <id> (` anywhere in the tool response, and
#: `session plan show` prints a header spelled
#: `plan for session ab12cd34 (add-widget) — 2/5 done`. Loosened to the
#: subcommand-agnostic form, a read-only query would bind this host to
#: whatever session it happened to name — silently, and quite possibly to one
#: the caller was only looking at. `session status`, `session list`,
#: `session sync` and `context` all print the same sequence.
_SESSION_CLAIM = re.compile(r"\binvariant\b[^|;&]*?\bsession\s+(start|resume)\b")

#: The two commands that RECORD verdicts: `invariant guidelines verdict` and
#: `invariant guidelines verdicts submit`, however reached. `verdicts show`
#: is a read and is left out, as the claim pattern above leaves out every
#: session query: `verdict\b` does not match `verdicts`.
_VERDICT_SUBMISSION = re.compile(
    r"\binvariant\b[^|;&]*?\bguidelines\s+(?:verdict\b|verdicts\s+submit\b)"
)

#: The submission token those commands print: `token <hex>` in the text form,
#: `"token": "<hex>"` under `--json` (`domains/guidelines.new_submission_token`).
_SUBMISSION_TOKEN = re.compile(r'(?:\btoken |"token":\s*")([0-9a-f]{32})\b')

#: The identifier a claiming command prints. Parsed rather than guessed, so
#: the binding names the session that command actually created or picked up.
_STARTED_ID = re.compile(r"session\s+([0-9a-z]+)\s+\(")


def _signal_or_none(build) -> AttentionSignal | None:
    """``build()``, or ``None`` if it raises.

    The signal is a by-product of the response, and reading it must not be a
    way for the response to be lost: a hook that raised here would answer
    degraded instead of with the verdict it had already reached (ADR-007 D10).
    """
    try:
        return build()
    except Exception:  # noqa: BLE001 - see the docstring
        return None


def _question_signal(payload: dict, response: dict) -> AttentionSignal | None:
    """``waiting: question`` for an ``AskUserQuestion`` call the hook let through.

    A denied call never reaches the person, so it asks them nothing.
    """
    if payload.get("tool_name") != _QUESTION_TOOL:
        return None
    decision = response.get("hookSpecificOutput", {}).get("permissionDecision")
    if decision == "deny":
        return None
    return AttentionSignal(
        ATTENTION_WAITING, kind="question", message=_question_text(payload.get("tool_input"))
    )


def _background_launch(payload: dict) -> AttentionSignal | None:
    """``background-started`` when the main turn's tool call runs on after it
    returned: an Agent the host answered as launched, or a shell command it
    gave a background task id.

    A subagent's own call (the payload carries its ``agent_id``) is not the
    host's: its completion notifies that subagent, never the main turn, so
    nothing would ever close the entry.
    """
    if payload.get("agent_id"):
        return None
    response = payload.get("tool_response")
    if not isinstance(response, dict):
        return None
    tool = payload.get("tool_name")
    if tool in AGENT_TOOLS:
        launched = response.get("isAsync") is True or response.get("status") == "async_launched"
        task, kind = (response.get("agentId") if launched else None), "agent"
    elif tool == _SHELL_TOOL:
        task, kind = response.get("backgroundTaskId"), "command"
    else:
        return None
    if not isinstance(task, str) or not _TASK_ID.match(task):
        return None
    return AttentionSignal(ATTENTION_BACKGROUND_STARTED, kind=kind, tasks=(task,))


def _task_notification(payload: dict) -> AttentionSignal | None:
    """``background-done`` when the prompt is Claude Code's completion notice.

    Only a prompt that starts with the notice's tag is one, so a person who
    mentions the tag is still a person speaking. Each block whose status is
    terminal closes its task, unless its note says the agent stopped with
    background work of its own still running: it notifies again when that
    ends.
    """
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.lstrip().startswith(_TASK_NOTIFICATION):
        return None
    done = []
    for block in _TASK_BLOCK.findall(prompt):
        task, status = _tag(block, "task-id"), _tag(block, "status")
        if task and _TASK_ID.match(task) and status in _TASK_DONE and not (
            _STILL_RUNNING.search(block)
        ):
            done.append(task)
    return AttentionSignal(ATTENTION_BACKGROUND_DONE, tasks=tuple(done))


def _tag(block: str, name: str) -> str | None:
    """The text of ``block``'s first ``<name>`` element, stripped."""
    found = re.search(rf"<{name}>([^<]*)</{name}>", block)
    return found.group(1).strip() if found else None


def _edit_target(payload: dict) -> Path | None:
    """The path a call names as its destination, or ``None``.

    ``file_path`` for the edit tools and a Read, ``notebook_path`` for a
    notebook edit. One spelling, read by every hook that places a call, so the
    binding, the first-edit report and the actor check cannot disagree about
    where a call landed.
    """
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    return Path(str(raw)) if raw else None


def _spend_overrides(stopped, rule_ctx, ctx, *, surface: str):
    """Let a granted one-shot override past the verdicts that would stop a call.

    Returns the verdicts still stopping it, and the ones an override paid for
    paired with the record of that payment. Consumption happens HERE, at the
    delivery surface, and never inside ``core.rules.evaluate``: a rule is a
    question about the world and asking it must not change the world. It is
    also why the token is spent only where the verdict actually stops
    something — an override burnt by a rule that was passing anyway would be
    gone by the time the operator needed it.
    """
    if not stopped or not rule_ctx.session:
        return list(stopped), []

    session_id = rule_ctx.session.get("id")
    if not session_id:
        return list(stopped), []

    remaining, spent = [], []
    for verdict in stopped:
        record = ctx.sessions.consume_override(
            session_id, verdict.rule_id, surface=surface
        )
        if record is None:
            remaining.append(verdict)
        else:
            spent.append((verdict, record))
    return remaining, spent


def _override_notice(verdict, record: dict) -> str:
    """What a spent override says out loud, in the turn that spent it.

    Loud on purpose. The override is recorded on the session either way, and a
    record nobody reads until afterwards is not the same as being told now that
    a gate one relies on has just been stood aside for this call.
    """
    left = record.get("remaining", 0)
    tail = f"{left} call(s) left" if left else "spent"
    return (
        f"[override] {verdict.rule_id}: passed deliberately — "
        f"{record.get('reason', 'overridden')} ({tail})\n"
        f"        it would have said: {verdict.verdict.message}"
    )


def _response_text(payload: dict) -> str:
    """A shell call's output, however the host spelled it.

    The tool response is either the text itself or an object holding it as
    ``stdout`` or ``output``. One reading, for both commands whose output the
    hook reads: the session claim and the verdict submission.
    """
    response = payload.get("tool_response")
    if isinstance(response, dict):
        response = response.get("stdout") or response.get("output") or ""
    return str(response or "")


def _submission_tokens(response: str) -> tuple[str, ...]:
    """Each submission token a verdict command printed, in order, once each.

    Two submissions chained in one call print two, and both are stamped. A
    refused submission prints none.
    """
    return tuple(dict.fromkeys(_SUBMISSION_TOKEN.findall(response or "")))


def _bind_claimed_session_id(response: str) -> str | None:
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


#: The cap on injected guideline lines when the manifest gives none: the
#: ``guidelines.injection.max_lines`` default (ADR-012 DN-C).
ADVISORY_MAX_LINES = 40


def _context_response(event_name: str, text: str) -> dict:
    if not text.strip():
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }


#: How many parked sessions the greeting offers, against six for the unowned
#: list. Each of these lines carries a progress clause and runs about twice as
#: wide, and the whole block is injected on every SessionStart — so the cap is
#: lower, and the overflow is NAMED rather than dropped.
_RESUMABLE_OFFERED = 3


def _resumable_offer(ctx) -> list[str]:
    """What somebody put down here, and the command that picks it up.

    Empty when nothing is parked, which is what makes this change additive:
    a project that never adopts the plan sees byte-identical output to before.

    The offer is phrased as an offer. Resumption is never automatic — picking
    up unfinished work brings a pipeline state, a declared file set and a
    branch along with it — so the block says so rather than leaving a greeting
    that reads as an instruction to make that decision for the reader.

    This is a READ and stays one. ``resumable()`` is written from ``live()``
    and sorts a list; nothing on the path saves, and nothing may start to,
    because a hook that wrote on every SessionStart would stamp heartbeats and
    race every other host against the same registry in order to say hello.
    """
    # Unguarded, like the `origin_candidates` call it sits beside. Wrapping
    # only this one would claim it is the more fragile of the two, which it is
    # not — both read the same registry — and the hook already has an outer
    # net: an engine that throws answers with the degraded marker rather than
    # wedging the turn.
    parked = ctx.sessions.resumable(ctx.cwd)
    if not parked:
        return []

    lines = [
        (
            "  A session OUTLIVES a host session. These stopped here and can "
            "be picked up:"
        )
    ]
    for session in parked[:_RESUMABLE_OFFERED]:
        lines.append(
            f"    invariant session resume {session.name}"
            f"   ({session.state}, {_offer_clause(session)})"
        )
    if len(parked) > _RESUMABLE_OFFERED:
        lines.append(
            f"    … and {len(parked) - _RESUMABLE_OFFERED} more: "
            f"invariant session list"
        )
    lines.append(
        "  Resuming is offered, never automatic — new work starts a new session."
    )
    return lines


def _handoff_lines(ctx) -> list[str]:
    """What the last session here said to do next, while it is fresh.

    Shown only where NO session is bound, which is the turn a handed-off
    session starts on: it is the backstop for a prompt argument that did not
    arrive, so the next session sees the note however it was opened. Empty
    when nothing fresh is recorded, so a project that never hands off projects
    exactly as before. A read, like the resumable offer beside it.

    A consumed note is not shown (IDEA_008), and the last line names the one
    command that retires a note nobody is going to act on — the only exit a
    note launched by hand has, short of going stale.
    """
    found = ctx.sessions.latest_handoff(ctx.cwd)
    if found is None:
        return []
    session, entry = found
    return [
        f"  last handoff here, from {session.name} as {entry.get('name')}:",
        f"    {entry.get('note')}",
        "    not acting on it? invariant session handoff --dismiss",
    ]


def _offer_clause(session) -> str:
    """How far along an offered session is, in one parenthesised clause.

    An unplanned session says so in words rather than rendering ``0/0``: the
    fraction would read as "none of nothing done", which is a report about a
    checklist that was never written.
    """
    got = plan_reader.progress(session.plan)
    if not got.total:
        return "no deliverables recorded"
    if got.complete:
        return f"{got.fraction} deliverables, all done"
    return f"{got.fraction} deliverables, next: {got.remaining[0]['title']}"


def _spans_hosts_line(session) -> list[str]:
    """That this session outlived a host session, said once, on the turns it did.

    Silent when ``resumed_count`` is zero, which is every session in every
    project that does not use the plan — their turn projection gains nothing at
    all, which is what makes this additive.

    The clause after the count depends on whether there IS a plan, because the
    two cases want opposite things said. A session with deliverables recorded
    needs to know what ends it: it merges when they are done, and nothing else
    in the projection says so. A session with none needs the opposite — the
    same sentence would name a condition that cannot be met, so it names the
    command that arms it instead. A resume is the last cheap moment to record
    what is left; after it, what remains is in somebody's head and then in a
    commit.
    """
    if not session.resumed_count:
        return []
    tail = (
        "it merges when the plan is done"
        if session.plan_total
        else 'nothing records what is left: invariant session plan add "<deliverable>"'
    )
    return [
        f"  spans host sessions — resumed {session.resumed_count} time(s); {tail}"
    ]


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
                (
                    "  It binds to this host session automatically — one host "
                    "session, one invariant session."
                ),
            ]
            # ABOVE the unowned list, and the ordering is the point. A
            # resumable session is a DEFINITE offer — it has a name, a state
            # and a progress count, and the reader most likely parked it
            # themselves. An unowned one is a conditional, hedged with "only
            # if it is yours". Printing the hedged list first buries the
            # answer under the caveat.
            #
            # Until this block existed the greeting listed only sessions with
            # NO owner, so a session whose host had ENDED — the one that can
            # actually be picked up — was the single thing it could not
            # mention. Finding it required already knowing it was there.
            lines += _handoff_lines(ctx)
            lines += _resumable_offer(ctx)

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
        handoff = _handoff_lines(ctx)
        if greeting:
            return "\n".join(
                [
                    "invariant: no session is active in this directory.",
                    "  Start one before editing: invariant session start <task-name>",
                    *handoff,
                ]
            )
        # `invariant context` run by hand with nothing bound. Silent as before
        # unless a handoff is waiting, which is the one thing worth saying here.
        if not handoff:
            return ""
        return "\n".join(["invariant: no session is active in this directory.", *handoff])

    lines.append(f"invariant — session {session.id} ({session.name}) is {session.state}")

    pipeline = ctx.pipelines.current(session.id)

    # First, above everything, on every turn. A stand-down is enforcement the
    # operator switched off, and the whole reason it is a recorded state rather
    # than an improvised manifest edit is that it can be seen — so the turn
    # that runs under one has to be told, not left to notice that nothing is
    # stopping it. It is the only line here that reports an absence of
    # enforcement rather than a position in a lifecycle.
    held = standdown_in_force(
        session.to_dict(), pipeline.to_dict() if pipeline else None
    )
    if held:
        folded = sorted(set(held.get("include") or ()))
        still = sorted(HELD_OUT_OF_STANDDOWN - set(folded))
        lines.append(
            f"  ENFORCEMENT IS STOOD DOWN — {held.get('reason', 'no reason given')}"
        )
        lines.append(
            f"    every gate reports as stood down until this "
            f"{held.get('until', 'pipeline')} ends"
            + (f"; still enforced: {', '.join(still)}" if still else "")
        )
        lines.append("    put it back with: invariant standdown --clear")

    # After the stand-down banner, before `session may go to:`. The banner
    # keeps its position as the only line reporting an absence of enforcement;
    # the plan is what the turn is ABOUT, and `session may go to:` is
    # machinery. At most three lines land here however long the plan is — a
    # session spanning hosts says so, and `core.plan.summary_lines` caps the
    # rest at the next deliverable and one after it. The whole listing is
    # behind `invariant session plan show`.
    lines += _spans_hosts_line(session)
    lines += [f"  {line}" for line in plan_reader.summary_lines(session.plan)]

    machine = ctx.sessions.machine
    legal = machine.next_states(session.state)
    if legal:
        lines.append(f"  session may go to: {', '.join(legal)}")

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

        # Owed PER DESTINATION. "may advance to: committed, red, refactor,
        # abandoned" followed by one undifferentiated list of owed gates is
        # what made BUG_037 read as an engine that contradicts itself: two of
        # those four were refused by gates that exist to permit shipping, and
        # nothing said which two. Now that the gates hang off the outcomes they
        # protect, the projection can say it.
        try:
            by_target = ctx.pipelines.evaluate_gates_by_destination(
                session.id, session=session.to_dict(), cwd=ctx.cwd
            )
        except InvariantError:
            by_target = {}
        free = []
        for target, verdicts in by_target.items():
            owed = [v for v in verdicts if not v.verdict.ok and not v.verdict.skipped]
            if not owed:
                free.append(target)
                continue
            lines.append(f"  owed to reach {target}:")
            for verdict in owed:
                lines.append(f"    - {verdict.rule_id}: {verdict.verdict.message}")
        if free:
            lines.append(f"  reachable with nothing owed: {', '.join(free)}")

    if session.push_count:
        lines.append(f"  pushes used: {session.push_count} (the barrier allows one)")

    lines += _turn_index_lines(ctx, session, pipeline)

    return "\n".join(lines)


def _turn_index_lines(ctx, session, pipeline) -> list[str]:
    """The guidelines turn index, indented as the projection's other lines.

    ADR-012 ruling 8, D7 and the A5 rulings. The engine decides what to say
    (``core.rules.turn_guidelines``) and ``core.guidelines`` writes it; this
    only indents it, capped by ``guidelines.injection.max_lines``. The rule
    context is built from the session and pipeline already in hand, and only
    once a ruleset is known to be configured, so a project without one pays
    nothing. Any exception in this step is silence: the turn goes on.
    """
    manifest = getattr(ctx, "manifest", None)
    if manifest is None:
        return []
    try:
        index = turn_guidelines(
            manifest,
            lambda: RuleContext(
                manifest=manifest,
                cwd=ctx.cwd,
                session=session.to_dict(),
                pipeline=pipeline.to_dict() if pipeline else None,
            ),
        )
        if index is None:
            return []
        cap = int(manifest.get("guidelines.injection.max_lines", ADVISORY_MAX_LINES))
        return [f"  {line}" for line in render_turn_index(index, cap)]
    except Exception:  # noqa: BLE001 - the projection may never fail its turn
        return []
