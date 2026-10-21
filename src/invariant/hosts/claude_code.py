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
from dataclasses import dataclass, field
from pathlib import Path

from ..core import plan as plan_reader
from ..core.errors import InvariantError, UsageError
from ..core.rules import (
    EDIT_TOOLS,
    HELD_OUT_OF_STANDDOWN,
    evaluate_event,
    is_architecture_doc,
    standdown_in_force,
)
from ..domains.pipeline import NO_PIPELINE, VIA_ACTOR
from ..domains.session import ambiguity_message
from .claude_code_launch import HANDOFF_ENV

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
}


def is_probe(payload: object) -> bool:
    """Whether a parsed hook payload is ``doctor``'s probe."""
    return isinstance(payload, dict) and payload.get(PROBE_KEY) is True


CHECK_AGENTS_LOADABLE = "agents loadable by host"
CHECK_AGENTS_CURRENT = "library agents current"

#: Tools whose invocation means "a subagent ran". Recorded as pipeline
#: evidence so the agent-role gates can read a fact instead of a claim.
AGENT_TOOLS = {"Task", "Agent"}


#: How an installed asset differs from what the library ships. These are the
#: strings `doctor` has always printed; naming them keeps the classifier and
#: its two readers from drifting apart about what a divergence IS.
CURRENT = "current"
ABSENT = "absent"
LOCAL = "edited locally"
OLDER = "installed from an older version"
UNREADABLE = "unreadable"


@dataclass
class AssetPlan:
    """What an install would do to one target."""

    kind: str
    identity: str
    source: Path
    target: Path
    state: str
    write: bool


@dataclass
class InstallResult:
    """What an install DID, in three lists rather than one.

    It used to return one list — every asset, all overwritten — and the command
    printed "installed N asset(s)" with no mention that one of them had been
    changed by hand. The three cases are different facts and the operator needs
    the middle one most.
    """

    installed: list[str] = field(default_factory=list)
    #: (label, why) for a target left alone because it is the operator's work.
    skipped: list[tuple[str, str]] = field(default_factory=list)


def _IS_OURS(entry) -> bool:
    """Is this settings entry one this library wrote?

    The shim path is the identity, and it is the SAME test `_wire_settings`
    uses to avoid writing a duplicate. One test, both directions: an install
    that recognises its own entries and a removal that does not must be
    impossible to write.
    """
    return "/.claude/hooks/invariant-" in json.dumps(entry)


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


def classify_installed(source: Path, target: Path) -> str:
    """How ``target`` diverges from the ``source`` this library ships.

    The one answer, read by `doctor` and by every install. It was written
    inside `_stale_for` and lived only there, so the command `doctor`'s failure
    points at — `assets install` — could not consult it and destroyed the very
    edits this function had just identified as edits (BUG_051).

    The discriminator is the marker digest. A file whose marker still records
    the digest of what we ship, but whose body no longer reconstructs to it,
    was changed AFTER we wrote it: that is the operator's work. A file whose
    marker records some other digest is a copy of an older release, which is
    nobody's work and is the case reinstalling exists for.
    """
    from . import assets

    if not target.is_file():
        return ABSENT
    text = target.read_text(encoding="utf-8")
    shipped = source.read_text(encoding="utf-8")
    rebuilt = assets.reconstruct_source(text)
    if rebuilt is None:
        return UNREADABLE
    if rebuilt == shipped:
        return CURRENT
    return LOCAL if assets.marker_digest(text) == digest_of(shipped) else OLDER


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

    def install_agent(
        self, source: Path, target: Path, overwrite_local: bool = False
    ) -> str:
        """Write one agent, and answer what was there before.

        The return value is the divergence it found, so a caller can report a
        file it left alone. Skipping a local edit here is the same decision
        `install_assets` makes and for the same reason — a file in the
        operator's repository is the operator's (BUG_051).
        """
        state = classify_installed(source, target)
        if state is LOCAL and not overwrite_local:
            return state

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
        return state

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
        from . import assets

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
            for kind in assets.KINDS:
                for source in self._sources_for(kind):
                    identity = kind.identity(source)
                    target = self._target_for(kind, identity)
                    if not target.is_file():
                        continue
                    if classify_installed(source, target) is LOCAL:
                        protected.append(f"{kind.name}:{identity}")
                    else:
                        asset_files.append(target)

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
                        _currency_detail(stale, "invariant assets install"),
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

        ``None`` when the library ships none of this kind — a project owning
        every command is not drift, and reporting "0 stale" would imply this
        library had an opinion about files it never wrote.
        """
        sources = self._sources_for(kind)
        if not sources:
            return None

        stale: list[str] = []
        for source in sources:
            identity = kind.identity(source)
            state = classify_installed(source, self._target_for(kind, identity))
            if state is not CURRENT:
                stale.append(f"{identity} ({state})")
        return stale

    def _target_for(self, kind, identity: str) -> Path:
        base = self.claude_dir / kind.directory
        if kind.nested_file:
            return base / identity / kind.nested_file
        return base / f"{identity}.md"

    def plan_assets(self, only: str | None = None, overwrite_local: bool = False):
        """What an install would do to each target, before it does any of it.

        Separated from the doing so that ``--dry-run`` can enumerate. It used
        to return before any per-file work and print "would reinstall library
        assets", which names nothing it would flatten — the one thing a dry run
        of a destructive command is for (BUG_051).
        """
        from . import assets

        plan: list[AssetPlan] = []
        for kind in assets.KINDS:
            if only and kind.name != only:
                continue
            for source in self._sources_for(kind):
                identity = kind.identity(source)
                target = self._target_for(kind, identity)
                state = classify_installed(source, target)
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
                        write=not (state is LOCAL and not overwrite_local),
                    )
                )
        return plan

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
        from . import assets

        result = InstallResult()
        for step in self.plan_assets(only=only, overwrite_local=overwrite_local):
            label = f"{step.kind}:{step.identity}"
            if not step.write:
                result.skipped.append((label, step.state))
                continue
            step.target.parent.mkdir(parents=True, exist_ok=True)
            body = step.source.read_text(encoding="utf-8")
            try:
                step.target.write_text(
                    assets.render_managed(body, digest_of(body)), encoding="utf-8"
                )
            except ValueError as exc:
                raise InvariantError(
                    f"{step.kind} {step.identity!r} has no YAML frontmatter starting "
                    f"on line 1. The host identifies it by that block; a file "
                    f"without one is copied into place and then silently "
                    f"ignored, so installing it would look like success and "
                    f"produce nothing."
                ) from exc
            result.installed.append(label)
        return result

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

            # One classifier, shared with `_stale_for` and with every install.
            # It used to be written out here as well, which is how the two
            # readers could have come to disagree about what an edit IS — and
            # the install path, which had no classifier at all, destroyed the
            # files this one identifies (BUG_051).
            state = classify_installed(source, target)
            if state is not CURRENT:
                stale.append(f"{source.stem} ({state})")

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
                _currency_detail(stale, "invariant agents install"),
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

        # `ask` cannot reach this surface — the host discards a question asked
        # at the end of a turn — so an ask-level verdict blocks here like any
        # other. The one-shot token is the appeal that does reach it, and it is
        # why the token exists alongside the ask level rather than instead of
        # it (AUTH-01).
        blocking, spent = _spend_overrides(
            [v for v in verdicts if v.blocks], rule_ctx, ctx, surface="stop"
        )
        notices = [_override_notice(v, record) for v, record in spent]

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

        response = payload.get("tool_response")
        if isinstance(response, dict):
            response = response.get("stdout") or response.get("output") or ""
        session_id = _bind_claimed_session_id(str(response or ""))
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
            self._bind_claimed_session(payload, ctx)
        elif tool in EDIT_TOOLS:
            self._bind_by_successful_edit(payload, ctx)
            # After the binding, so an edit that just taught the host which
            # session it is can also be that session's first edit.
            self._report_landed_edit(payload, ctx)

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

#: The identifier a claiming command prints. Parsed rather than guessed, so
#: the binding names the session that command actually created or picked up.
_STARTED_ID = re.compile(r"session\s+([0-9a-z]+)\s+\(")


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

    return "\n".join(lines)
