"""The ``invariant`` command line.

The CLI is the whole API. Hooks are shims that call it, agents are instructed
to call it, and a human debugging a wedged state calls the same commands. There
is no second path into the engine, which is what keeps "what the agent was
told" reproducible: it is this program's output.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from ..core import plan as plan_reader
from ..core.errors import GateFailure, InvariantError, ManifestError, UsageError
from ..core.manifest import DATA_DIR, DEFAULTS, MANIFEST_RELPATH, Manifest
from ..core.rules import (
    HELD_OUT_OF_STANDDOWN,
    RuleContext,
    all_rules,
    evaluate,
)
from ..core.store import InertStore, JsonStore
from ..domains.pipeline import PipelineDomain, describe_run, summarise
from ..domains.session import (
    SessionDomain,
    name_ambiguity_message,
    name_matches,
)

#: Absolute paths inside free text. Used only to find a session worktree an
#: agent prompt names — every hit is checked against the live session registry
#: before it means anything, so a loose match costs nothing.
_ABSOLUTE_PATH = re.compile(r"/[^\s'\"`,;]+")

EXIT_OK = 0

#: The starter architecture doc `init` writes when the docs directory is
#: absent. Shipped text lives under `data/` beside the agents and the
#: machines; `{project}` is the one substitution.
ARCHITECTURE_SCAFFOLD = DATA_DIR / "scaffold" / "architecture-overview.md"

#: Distinguishes "not resolved yet" from "resolved to nothing". A plain None
#: default would re-run host detection on every access for a project whose
#: host has no telemetry reader.
_UNSET = object()


# -- output helpers ----------------------------------------------------


#: How the operator asks for the shipped version back. Printed wherever an
#: install leaves a file alone, because a skip the operator cannot reverse is
#: only half an improvement on a write they could not prevent.
_OVERWRITE_LOCAL_HINT = "  pass --overwrite-local to replace them with the shipped version"


def emit(args, payload: dict, text: str = "") -> int:
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    elif text:
        print(text)
    return EXIT_OK


def worth_reporting(verdict, verbose: bool) -> bool:
    """Does this verdict earn a line on the default (non-verbose) output?

    One predicate, because there were two and they drifted. `cmd_check` kept
    its own inline copy which read `args.verbose` directly — and the `check`
    subparser defines no such flag, so the command raised on every invocation
    until this was pulled out. The rule now lives in one place for the same
    reason `_changed_set` does: a second copy is how the two come to disagree
    while each looks right on its own.
    """
    if verdict.verdict.skipped:
        # A BLOCKING gate going inapplicable is reported on the default path:
        # the project declared it must hold, and the reader is owed the fact
        # that it did not run rather than a silence that reads exactly like a
        # pass (BUG_020). Advisory skips stay behind --verbose, where they
        # were, because there is nothing to act on.
        return verdict.skipped_blocking or verbose
    if verdict.verdict.ok:
        return verbose
    return True


def report_verdicts(verdicts, args) -> None:
    if getattr(args, "json", False):
        return
    verbose = getattr(args, "verbose", False)
    for verdict in verdicts:
        if worth_reporting(verdict, verbose):
            print(verdict.render(), file=sys.stderr)


def verdicts_payload(verdicts) -> list[dict]:
    return [
        {
            "rule": v.rule_id,
            "level": v.level.value,
            "ok": v.verdict.ok,
            "skipped": v.verdict.skipped,
            "message": v.verdict.message,
            "detail": v.verdict.detail,
            "fixes": list(v.verdict.fixes),
        }
        for v in verdicts
    ]


# -- context assembly --------------------------------------------------


class Context:
    """Everything a command needs, assembled once."""

    def __init__(
        self,
        cwd: Path | None = None,
        required: bool = True,
        session_id: str | None = None,
        host_session_id: str | None = None,
        manifest_start: Path | None = None,
        probe: bool = False,
    ) -> None:
        self.cwd = Path(cwd or Path.cwd())
        #: ``manifest_start`` separates "which project governs this call" from
        #: "where is this process standing". Hooks pass the host's project
        #: directory here (AUD-04): the host guarantees that to its hooks,
        #: while the process working directory can be anywhere — and resolving
        #: the manifest from a cwd outside the project used to silently disable
        #: every gate. ``cwd`` stays honest for the rules that ask git about
        #: the directory the call actually runs in.
        self.manifest = Manifest.load(manifest_start or self.cwd, required=required)
        #: A probe reads the real state and writes none of it. Every domain
        #: this context builds shares this one store, so swapping it here
        #: covers every write a hook can reach (BUG_059).
        self.store = (InertStore if probe else JsonStore)(self.manifest.state_root)
        #: An explicit --id, carried rather than exported. Writing it into
        #: os.environ would work and would also outlive the command, silently
        #: re-answering the same question for everything that ran afterwards.
        self.session_id = session_id
        #: The agent host's session identifier, from the hook payload. The one
        #: attribution signal that survives the host resetting cwd between
        #: calls — without it, concurrent sessions are indistinguishable from
        #: the main checkout, which is exactly when attribution matters.
        self.host_session_id = host_session_id or os.environ.get(
            "INVARIANT_HOST_SESSION_ID"
        )
        #: Resolved once and cached, because every transition reads a watermark
        #: through it and rebuilding the reader per property access would
        #: re-scan the transcript directory each time.
        self._sample_source: object = _UNSET

    @property
    def sample_source(self):
        """The concrete host reader, chosen here and nowhere else (ADR-002 D2).

        This is what the protocol buys: ``core`` and ``domains`` know only
        ``SampleSource``, and the one line that names a host lives in the CLI.
        A host with no reader in this release resolves to ``None``, and every
        command that *reads* the store keeps working — only ingest refuses,
        and it says why.
        """
        if self._sample_source is _UNSET:
            self._sample_source = None
            if self.manifest.get("host.kind") == "claude_code":
                from ..hosts.claude_code_telemetry import ClaudeCodeTranscripts

                self._sample_source = ClaudeCodeTranscripts(origin=self.manifest.root)
        return self._sample_source

    @property
    def watermarks(self):
        """Injected into both FSM domains so their transitions carry one (D3).

        Never a reason a transition can fail — see ``TranscriptWatermarks``.
        """
        from ..core.stats import TranscriptWatermarks

        return TranscriptWatermarks(self.sample_source)

    @property
    def stats(self):
        from ..domains.stats import StatsDomain

        return StatsDomain(self.manifest, self.store, source=self.sample_source)

    @property
    def sessions(self) -> SessionDomain:
        return SessionDomain(self.manifest, self.store, watermarks=self.watermarks)

    @property
    def pipelines(self) -> PipelineDomain:
        return PipelineDomain(self.manifest, self.store, watermarks=self.watermarks)

    @property
    def items(self):
        from ..domains.workitem import WorkItemDomain

        return WorkItemDomain(self.manifest)

    @property
    def memory(self):
        from ..domains.memorystore import MemoryStore

        return MemoryStore.from_manifest(self.manifest)

    def current_session(self, strict: bool = True, target=None):
        if self.session_id:
            return self.sessions.require(self.session_id)
        return self.sessions.current(
            self.cwd,
            strict=strict,
            target=target,
            host_session_id=self.host_session_id,
        )

    def require_session(self):
        session = self.current_session()
        if session is None:
            raise UsageError(
                "no session is active here. Start one with 'invariant session "
                "start <task-name>', or set INVARIANT_SESSION_ID."
            )
        return session

    def session_for_payload(self, payload: dict):
        """Who this tool call belongs to, using the path the call itself names.

        The path a call is aimed at is the strongest attribution signal a hook
        ever gets: it is in the payload, it is absolute, and it does not depend
        on the working directory the host happens to be in or on an environment
        variable a tool call cannot carry. That is true of a *read* as much as
        of a write, which is why post-tool-use recording resolves through here
        too rather than by working directory alone (BUG_010). An agent dispatch
        carries no ``file_path``, but it is not therefore pathless: a dispatch
        whose prompt names exactly one live session's worktree is aimed at that
        session as plainly as an edit landing inside it, so that is read as the
        target too. It is one route with one precedence, not two.

        Reading it is what lets ONE host session drive several tracks at once.
        Without it a dispatch resolves by working directory and host identifier
        alone, so a parent driving two sessions records every agent against
        whichever session it happens to be bound to. The other track's pipeline
        then shows that no agent ran, and its block-level ``agents.*_ran`` gates
        become unsatisfiable by anything that track can do — the wedge shape
        this project treats as an engine defect rather than a reason to force.

        When that path identifies a session which has no host identifier
        recorded — every session registered before the identifier existed — the
        mapping is learned here, so the *next* call from the same host session
        resolves by identifier alone and works for tool calls that carry no
        path at all.
        """
        return self.resolution_for_payload(payload).session

    def resolution_for_payload(self, payload: dict):
        """:meth:`session_for_payload`, plus *how* it decided.

        Callers that only need the answer use ``session_for_payload``. The
        ``how`` matters to the evidence writers: ``origin`` returns a real
        session and is still a guess, and evidence written on a guess is what
        put a foreign session's agent into a pipeline that never dispatched it
        (BUG_028). :attr:`Resolution.proven` is the question they ask.
        """
        from ..domains.session import Resolution

        if self.session_id:
            return Resolution(self.sessions.require(self.session_id), "explicit")

        tool_input = payload.get("tool_input")
        target = None
        if isinstance(tool_input, Mapping):
            raw = tool_input.get("file_path") or tool_input.get("notebook_path")
            target = Path(str(raw)) if raw else None
            if target is None:
                named = self._worktree_named_in_prompt(tool_input.get("prompt"))
                if named is not None:
                    # A dispatch outranks the host's own binding, where a read
                    # or an edit does not. The distinction is who acts. Reading
                    # a peer's file is the host reaching across a boundary, and
                    # attributing it to the peer would hide the crossing — which
                    # is why step 2 beats step 3 there, and still does.
                    #
                    # A dispatch is the opposite shape: the host is not acting
                    # on the named worktree, it is DELEGATING into it, and the
                    # run being recorded is the delegate's. One host binding
                    # holds one session, so it cannot represent two concurrent
                    # delegations — a parent driving two tracks banks every
                    # agent on whichever session it started last. That is the
                    # wedge this route exists to close: the OTHER track's
                    # block-level ``agents.*_ran`` gates become unsatisfiable by
                    # anything that track can do, because the evidence was filed
                    # under a session it does not own. Adding the route without
                    # this precedence left it unreachable in the one case it was
                    # written for.
                    owner = self.sessions.by_worktree_containing(named)
                    if owner is not None:
                        return Resolution(owner, "prompt")
                    target = named

        resolution = self.sessions.resolve(
            self.cwd, target=target, host_session_id=self.host_session_id
        )
        # Resolution only. This used to ADOPT here — bind the resolved session
        # to the calling host — and that made ownership a side effect of asking
        # a question. Every pre-tool-use evaluation ran it, including calls that
        # were then DENIED, so a refused edit still claimed the session, and so
        # did a diagnostic that never touched a file. One stray call aimed into
        # an unowned worktree transferred a peer's session to whoever made it,
        # silently and with no way for the real owner to reclaim it.
        #
        # The binding now happens in post-tool-use, where the tool provably ran.
        # "Someone successfully edited inside this worktree" is evidence of
        # ownership; "someone asked whether they could" is not.
        return resolution

    def _worktree_named_in_prompt(self, prompt) -> Path | None:
        """The one live session worktree an agent prompt names, if exactly one.

        Deliberately conservative. Candidate paths are matched only against
        worktrees that are already registered and live — never against
        arbitrary text — and the answer is discarded unless every match lands
        on the SAME session. A brief that names two tracks, or none, resolves
        exactly as it did before: ambiguity must fall through to the ordinary
        order rather than pick a winner, because attributing a dispatch to the
        wrong session is worse than not attributing it at all.
        """
        if not isinstance(prompt, str) or "/" not in prompt:
            return None

        found: dict[str, Path] = {}
        for raw in _ABSOLUTE_PATH.findall(prompt):
            candidate = Path(raw.rstrip(".,;:)]}'\"`"))
            owner = self.sessions.by_worktree_containing(candidate)
            if owner is None:
                continue
            found[owner.id] = candidate
            if len(found) > 1:
                return None
        return next(iter(found.values()), None)

    def rule_context(self, payload=None, extra=None) -> RuleContext:
        # Non-strict on purpose. Rules are evaluated on behalf of hooks, and an
        # unidentifiable session is something the rules already model: no
        # session means the session-scoped gates abstain. Raising here would
        # convert "I do not know who you are" into "you may not act".
        session = self.session_for_payload(payload or {})
        pipeline = self.pipelines.current(session.id) if session else None
        ambiguous = (
            []
            if session
            else [
                s.to_dict()
                for s in self.sessions.origin_candidates(
                    self.cwd, host_session_id=self.host_session_id
                )
            ]
        )
        return RuleContext(
            manifest=self.manifest,
            cwd=self.cwd,
            session=session.to_dict() if session else None,
            pipeline=pipeline.to_dict() if pipeline else None,
            payload=payload or {},
            extra=extra or {},
            ambiguous=ambiguous,
        )


# -- init / doctor / config -------------------------------------------


def cmd_init(args) -> int:
    from ..adapters import detect
    from ..hosts.claude_code import ClaudeCodeHost

    root = Path(args.root or Path.cwd()).resolve()
    manifest_path = root / ".claude" / "invariant.yaml"

    if manifest_path.exists() and not args.force:
        raise UsageError(
            f"{manifest_path.relative_to(root)} already exists; pass --force to "
            f"overwrite, or edit it directly"
        )

    ecosystems = [args.ecosystem] if args.ecosystem else detect(root)
    body = "\n".join(
        [
            "version: 1",
            "",
            "project:",
            f"  name: {args.name or root.name}",
            f"  ecosystems: [{', '.join(ecosystems)}]",
            "",
            "enforcement:",
            f"  profile: {args.profile}",
            "",
            "# Everything else has a default. 'invariant config show --provenance'",
            "# prints the resolved configuration and where each value came from.",
            "",
        ]
    )

    actions = [f"write {manifest_path.relative_to(root)}"]
    host = ClaudeCodeHost(root)
    actions.extend(host.plan_install())
    actions.append("add the state root to .gitignore")
    actions.append("register this project in the oracle index")
    # A starter architecture doc, ONLY where the docs directory is absent. A
    # fresh project otherwise starts out failing its own docs-first gate with
    # nothing to read: `doctor` reports the directory missing and the first
    # `pipeline advance` blocks on `docs.read_architecture` against an empty
    # path. A directory that exists — even empty — is the project's, and
    # `--force` does not change that: it rewrites the manifest, not the docs.
    docs_dir = root / DEFAULTS["paths"]["docs"]
    scaffold = docs_dir / "overview.md" if not docs_dir.exists() else None
    if scaffold is not None:
        actions.append(
            f"write {scaffold.relative_to(root)} (a starter architecture doc; "
            f"the docs-first gate reads this directory)"
        )

    if args.dry_run:
        return emit(
            args,
            {"actions": actions},
            "would:\n" + "\n".join(f"  - {a}" for a in actions),
        )

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(body, encoding="utf-8")
    host.install()

    gitignore = root / ".gitignore"
    marker = ".claude/invariant/state/"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    if marker not in existing:
        with gitignore.open("a", encoding="utf-8") as handle:
            handle.write(
                f"\n# invariant runtime state: machine-local and session-scoped\n{marker}\n"
            )

    if scaffold is not None:
        scaffold.parent.mkdir(parents=True, exist_ok=True)
        scaffold.write_text(
            ARCHITECTURE_SCAFFOLD.read_text(encoding="utf-8").replace(
                "{project}", args.name or root.name
            ),
            encoding="utf-8",
        )

    JsonStore(root / ".claude" / "invariant" / "state").ensure_root()
    registration = _register_after_init(root)
    note = (
        f"\n\nNot registered in the oracle index: {registration['error']}\n"
        f"  The project is initialised; 'invariant oracle projects add' retries."
        if "error" in registration
        else ""
    )
    return emit(
        args,
        {"actions": actions, "ecosystems": ecosystems, "oracle": registration},
        "initialised:\n"
        + "\n".join(f"  - {a}" for a in actions)
        + "\n\nCommit these before starting a session. A session works in a "
        "linked\nworktree, and a worktree checks out a commit — an uncommitted "
        "manifest\nis invisible from inside one."
        + note,
    )


def _register_after_init(root: Path) -> dict:
    """Record a freshly initialised project in the oracle's index.

    Reported, never raised: by the time this runs the project IS initialised,
    and failing the command over a user-level index would tell the operator
    the opposite. The note names the command that retries it.
    """
    from ..oracle.home import oracle_home
    from ..oracle.index import ProjectIndex

    try:
        entry, added = ProjectIndex(oracle_home()).register(
            Manifest.load(root), via="init"
        )
    except InvariantError as exc:
        return {"error": str(exc)}
    return {"added": added, "key": entry.key}


def cmd_doctor(args) -> int:
    from ..hosts.claude_code import ClaudeCodeHost

    checks: list[tuple[str, bool, str]] = []

    # `doctor` is the command someone runs BECAUSE the manifest is wrong, and
    # it used to resolve the manifest before it could report anything — so a
    # manifest that refuses to load took out the one command that would explain
    # it, and the operator got a single line from the argument parser's error
    # path instead of a diagnosis. Enumerating problems is this command's whole
    # job; being stopped by the first one is the opposite of it (BUG_050).
    try:
        ctx = Context(required=False)
    except ManifestError as exc:
        path = Manifest.find_root(Path.cwd())
        checks = [
            ("manifest present", True, str((path or Path.cwd()) / MANIFEST_RELPATH)),
            ("manifest valid", False, str(exc)),
        ]
        return _report_checks(args, checks)

    manifest_exists = ctx.manifest.path.is_file()
    checks.append(("manifest present", manifest_exists, str(ctx.manifest.path)))

    if manifest_exists:
        checks.append(("manifest valid", True, f"version {ctx.manifest.get('version')}"))
        state_ok = True
        try:
            ctx.store.ensure_root()
        except InvariantError as exc:
            state_ok, detail = False, str(exc)
        else:
            detail = str(ctx.manifest.state_root)
        checks.append(("state root writable", state_ok, detail))

        # A failing row names the remedy. "docs-first is core policy" stated
        # the rule to an adopter whose first `pipeline advance` was about to
        # block on it, and said nothing about how to satisfy it.
        docs = ctx.manifest.path_for("docs")
        checks.append(
            (
                "architecture docs exist",
                docs.is_dir(),
                str(docs)
                + (
                    ""
                    if docs.is_dir()
                    else "  missing: create overview.md there (invariant init "
                    "scaffolds one when this directory is absent), or set "
                    "enforcement.rules.docs.read_architecture: warn"
                ),
            )
        )

        for name in ("build", "test"):
            configured = ctx.manifest.get(f"commands.{name}")
            checks.append(
                (f"commands.{name} configured", bool(configured), str(configured or "unset"))
            )

        # The adapter's own tooling, resolved the way THIS process resolves it
        # rather than the way a shell with hand-exported variables would.
        from ..adapters import get_adapter

        for label, ok, detail in get_adapter(ctx.manifest.ecosystems[0]).self_check():
            checks.append((label, ok, detail))

        host = ClaudeCodeHost(ctx.manifest.root)
        for label, ok, detail in host.verify():
            checks.append((label, ok, detail))

        # Item statuses against the configured machine. A project whose backlog
        # says open/investigating/fixed while the machine says filed/triaged
        # has entries invisible to every query keyed off its own vocabulary,
        # and nothing said so — the machine is already pluggable through
        # methodology.extra_machines, so this is a mismatch nobody was told
        # about rather than a missing feature.
        try:
            unknown: dict[str, set[str]] = {}
            for item in ctx.items.backend.load_all():
                if not item.status:
                    continue
                try:
                    machine = ctx.items.machine(item.type)
                except InvariantError:
                    continue
                if item.status not in machine.states:
                    unknown.setdefault(item.type, set()).add(item.status)
            detail = (
                "; ".join(
                    f"{kind}: {', '.join(sorted(values))}"
                    for kind, values in sorted(unknown.items())
                )
                + " — not states of the configured machine; add one via "
                "methodology.extra_machines"
                if unknown
                else "every item status is a state of its machine"
            )
            checks.append(("item statuses match their machine", not unknown, detail))
        except InvariantError:
            pass

        # What `end_host_session` has promised in its docstring since it was
        # written: a spent binding is a session somebody parked, and nothing
        # told them where. Reported in the ONE command people run when they
        # suspect they have lost something.
        #
        # Informational, never a failure row. `_report_checks` turns any false
        # row into exit 1, and a parked session is this feature's designed
        # resting state — a `doctor` that goes permanently red on every project
        # adopting the plan is the gate that gets ignored and then switched
        # off. Guarded like the sibling checks: a diagnosis that cannot be made
        # must not take the diagnosis command down with it.
        try:
            parked = ctx.sessions.resumable(cwd=ctx.cwd)
            checks.append(
                (
                    "sessions parked here",
                    True,
                    "; ".join(
                        f"{s.name} ({plan_reader.progress(s.plan).describe()})"
                        for s in parked
                    )
                    + " — pick one up: invariant session resume <name>"
                    if parked
                    else "none parked here",
                )
            )
            owing = [s for s in ctx.sessions.live() if s.plan_open]
            checks.append(
                (
                    "open deliverable plans",
                    True,
                    "; ".join(
                        f"{s.name}: {s.plan_done_count}/{s.plan_total} done"
                        for s in owing
                    )
                    if owing
                    else "no live session has an open plan",
                )
            )
        except InvariantError:
            pass

        if ctx.manifest.get("memory.managed", True) and ctx.memory.root.exists():
            report = ctx.memory.check()
            checks.append(
                (
                    "memory store clean",
                    report.clean,
                    (
                        f"{report.count} memories, "
                        f"{len(report.problems())} problem(s)"
                    ),
                )
            )

    # Machine-wide, so outside the manifest block: an installed oracle agent
    # names an absolute interpreter, and an editable install can move. Reads
    # the plist only; launchd is not asked.
    agent = _oracle_installer().interpreter_check()
    if agent is not None:
        checks.append(("oracle agent interpreter", *agent))

    return _report_checks(args, checks)


def _report_checks(args, checks: list[tuple[str, bool, str]]) -> int:
    lines = [
        f"  {'ok ' if ok else 'FAIL'}  {label:<28} {detail}" for label, ok, detail in checks
    ]
    failed = [c for c in checks if not c[1]]
    emit(args, {"checks": [{"check": c, "ok": o, "detail": d} for c, o, d in checks]},
         "\n".join(lines))
    return 1 if failed else EXIT_OK


# -- agents ------------------------------------------------------------


def cmd_hooks_install(args) -> int:
    """Install and wire the hook shims, touching nothing else.

    Exists because the library gaining an event used to be unshippable to a
    project that already had one: the shim was absent, enforcement for that
    event was silently off, and `doctor`'s only repair was `init --force`,
    which rewrites the manifest and discards every considered downgrade the
    project had recorded.
    """
    from ..hosts.claude_code import ClaudeCodeHost

    ctx = Context()
    host = ClaudeCodeHost(ctx.manifest.root)
    written = host.install_hooks()
    return emit(
        args,
        {"installed": written},
        "hook shims: " + (", ".join(written) if written else "all already current")
        + "\n  settings wiring refreshed"
        + "\n  verify they actually RUN: invariant doctor",
    )


def cmd_uninstall(args) -> int:
    """Remove what `init` installed, and say what is being left behind.

    The right to stop using a tool is part of the operator's authority over
    their own project, and it was the part this library made hardest: adoption
    is one command, withdrawal was hand surgery over a settings file the
    library had merged itself into. A tool that is easy to adopt and awkward to
    remove is asking for a trust it has not earned yet (IDEA_004).
    """
    from ..hosts.claude_code import ClaudeCodeHost

    ctx = Context(required=False)
    root = ctx.manifest.root if ctx.manifest.path.is_file() else ctx.cwd
    host = ClaudeCodeHost(root)

    plan = host.plan_uninstall(
        remove_assets=args.remove_assets, remove_manifest=args.remove_manifest
    )
    if not args.dry_run:
        host.uninstall(
            remove_assets=args.remove_assets, remove_manifest=args.remove_manifest
        )

    verb = "would remove" if args.dry_run else "removed"
    lines = [f"{verb} {len(plan['shims'])} hook shim(s) from .claude/hooks/"]
    lines += [f"    {p.name}" for p in plan["shims"]]
    lines.append(
        f"{verb} invariant's entries from .claude/settings.json"
        + (f"  ({', '.join(plan['wired'])})" if plan["wired"] else "")
        + "  — yours are untouched"
    )
    if plan["assets"]:
        lines.append(f"{verb} {len(plan['assets'])} library asset(s)")
    for label in plan["protected"]:
        lines.append(f"  kept    {label} (edited locally — your work, not ours)")
    if plan["manifest"] is not None:
        lines.append(f"{verb} {plan['manifest'].name}")

    # Named, always. What survives is the operator's data, and a removal that
    # does not say what it left behind invites the reader to assume it took
    # everything — which is the opposite of the guarantee being made.
    lines.append("leaves")
    if plan["manifest"] is None:
        lines.append("    .claude/invariant.yaml            (--remove-manifest to drop it)")
    if not args.remove_assets:
        lines.append("    .claude/agents/, commands/, skills/  (--remove-assets to drop them)")
    lines.append("    the state directory, with its session records")
    lines.append("    work items, memories and architecture docs")
    if not args.dry_run:
        lines.append("run 'invariant init' to come back; nothing above was destroyed")

    return emit(
        args,
        {
            "dry_run": args.dry_run,
            "shims": [str(p) for p in plan["shims"]],
            "wired": plan["wired"],
            "assets": [str(p) for p in plan["assets"]],
            "kept": plan["protected"],
            "manifest": str(plan["manifest"]) if plan["manifest"] else None,
        },
        "\n".join(lines),
    )


def cmd_assets_list(args) -> int:
    """Every host asset, what kind it is, whether it loads, and who owns it."""
    from ..hosts import assets as A
    from ..hosts.claude_code import ClaudeCodeHost

    ctx = Context()
    host = ClaudeCodeHost(ctx.manifest.root)
    roles = ctx.manifest.get("agents.roles") or {}
    role_of = {a: role for role, agents in roles.items() for a in (agents or [])}

    rows = []
    for kind in A.KINDS:
        if args.kind and kind.name != args.kind:
            continue
        for path in kind.paths(host.claude_dir):
            identity = kind.identity(path)
            text = path.read_text(encoding="utf-8")
            problem = A.loadability_problem(kind, text, identity)
            rows.append(
                {
                    "kind": kind.name,
                    "name": identity,
                    "status": f"UNLOADABLE — {problem}" if problem else "loadable",
                    # Ownership, because a project's own file is not drift and
                    # must never be reported as such.
                    "owner": "library" if A.is_managed(text) else "project",
                    "role": role_of.get(identity, ""),
                }
            )

    width = max((len(r["name"]) for r in rows), default=1)
    text = "\n".join(
        f"  {r['kind']:<8} {r['name']:<{width}}  {r['status']:<12} [{r['owner']}]"
        + (f"  ({r['role']} role)" if r["role"] else "")
        for r in rows
    )
    return emit(args, {"assets": rows}, text or "  (none installed)")


def cmd_assets_install(args) -> int:
    """Reinstall library-shipped assets and nothing else.

    Separate from ``init`` deliberately: ``init --force`` rewrites the manifest,
    so routing a repair through it would trade a broken asset file for a
    discarded enforcement configuration.
    """
    from ..hosts.claude_code import ClaudeCodeHost

    ctx = Context()
    host = ClaudeCodeHost(ctx.manifest.root)

    if args.dry_run:
        plan = host.plan_assets(only=args.kind, overwrite_local=args.overwrite_local)
        lines = [f"would install into {host.claude_dir}:"]
        for step in plan:
            verb = "install" if step.write else "SKIP   "
            lines.append(f"  {verb} {step.kind}:{step.identity} ({step.state})")
        if any(not step.write for step in plan):
            lines.append(_OVERWRITE_LOCAL_HINT)
        return emit(
            args,
            {
                "dry_run": True,
                "plan": [
                    {
                        "kind": s.kind,
                        "identity": s.identity,
                        "state": s.state,
                        "write": s.write,
                        "target": str(s.target),
                    }
                    for s in plan
                ],
            },
            "\n".join(lines),
        )

    result = host.install_assets(only=args.kind, overwrite_local=args.overwrite_local)
    failures = [
        (label, detail)
        for label, ok, detail in host.asset_checks()
        if not ok and "loadable" in label
    ]
    lines = [f"installed {len(result.installed)} asset(s)"]
    lines += [f"  {i}" for i in result.installed]
    # Named, never counted. The whole defect was an install that reported a
    # tidy total while a file it had flattened went unmentioned; a skip nobody
    # can see is the same fault with the opposite sign.
    for label, why in result.skipped:
        lines.append(f"  skipped {label} ({why})")
    if result.skipped:
        lines.append(_OVERWRITE_LOCAL_HINT)
    for label, detail in failures:
        lines.append(f"  STILL BROKEN — {label}: {detail}")
    if not failures:
        lines.append("  reload the host's configuration for it to pick them up")
    emit(
        args,
        {
            "installed": result.installed,
            "skipped": [{"asset": a, "why": w} for a, w in result.skipped],
            "loadable": not failures,
        },
        "\n".join(lines),
    )
    return EXIT_OK if not failures else 1


def cmd_agents_list(args) -> int:
    """What is installed, whether the host can load it, and who owns which role."""
    from ..hosts.claude_code import DATA_DIR, ClaudeCodeHost, loadability_problem

    ctx = Context()
    host = ClaudeCodeHost(ctx.manifest.root)
    roles = ctx.manifest.get("agents.roles") or {}
    role_of = {a: role for role, agents in roles.items() for a in (agents or [])}

    rows = []
    for source in sorted((DATA_DIR / "agents").glob("*.md")):
        target = host.claude_dir / "agents" / source.name
        if not target.is_file():
            status = "NOT INSTALLED"
        else:
            problem = loadability_problem(
                target.read_text(encoding="utf-8"), source.stem
            )
            status = f"UNLOADABLE — {problem}" if problem else "loadable"
        rows.append(
            {"agent": source.stem, "status": status, "role": role_of.get(source.stem, "")}
        )

    width = max((len(r["agent"]) for r in rows), default=1)
    text = "\n".join(
        f"  {r['agent']:<{width}}  {r['status']}"
        + (f"   [{r['role']} role]" if r["role"] else "")
        for r in rows
    )
    return emit(args, {"agents": rows}, text or "  (none shipped)")


def cmd_agents_install(args) -> int:
    """Reinstall the library agents and nothing else.

    Kept separate from ``init`` deliberately. ``init --force`` rewrites the
    manifest, so pointing an agent repair at it would trade a broken agent file
    for a discarded enforcement configuration — a worse loss than the fault
    being repaired.
    """
    from ..hosts.claude_code import (
        CHECK_AGENTS_LOADABLE,
        DATA_DIR,
        LOCAL,
        ClaudeCodeHost,
        classify_installed,
    )

    ctx = Context()
    host = ClaudeCodeHost(ctx.manifest.root)
    agents_dir = host.claude_dir / "agents"
    sources = sorted((DATA_DIR / "agents").glob("*.md"))
    states = {
        s.stem: classify_installed(s, agents_dir / s.name) for s in sources
    }
    protected = [
        stem
        for stem, state in states.items()
        if state is LOCAL and not args.overwrite_local
    ]

    if args.dry_run:
        lines = [f"would install into {agents_dir}:"]
        for source in sources:
            state = states[source.stem]
            verb = "SKIP     " if source.stem in protected else "install  "
            lines.append(f"  {verb} {source.stem} ({state})")
        if protected:
            lines.append(_OVERWRITE_LOCAL_HINT)
        return emit(
            args,
            {
                "would_install": [
                    s.stem for s in sources if s.stem not in protected
                ],
                "would_skip": protected,
                "into": str(agents_dir),
            },
            "\n".join(lines),
        )

    agents_dir.mkdir(parents=True, exist_ok=True)
    installed = []
    for source in sources:
        host.install_agent(
            source, agents_dir / source.name, overwrite_local=args.overwrite_local
        )
        if source.stem not in protected:
            installed.append(source.stem)

    checks = {label: (ok, detail) for label, ok, detail in host.agent_checks()}
    ok, detail = checks[CHECK_AGENTS_LOADABLE]
    lines = [f"installed {len(installed)} agent(s) into {agents_dir}"]
    lines += [f"  skipped {stem} ({LOCAL})" for stem in protected]
    if protected:
        lines.append(_OVERWRITE_LOCAL_HINT)
    lines.append(f"  {detail}")
    if ok:
        lines.append("  reload the host's configuration for it to pick them up")
    emit(
        args,
        {"installed": installed, "skipped": protected, "loadable": ok},
        "\n".join(lines),
    )
    return EXIT_OK if ok else 1


def cmd_config_show(args) -> int:
    ctx = Context()
    rows = list(ctx.manifest.iter_provenance())
    if args.provenance:
        width = max(len(k) for k, _, _ in rows)
        text = "\n".join(f"  {k:<{width}}  {v!r:<28} [{src}]" for k, v, src in rows)
    else:
        text = "\n".join(f"  {k} = {v!r}" for k, v, _ in rows)
    return emit(args, {"config": ctx.manifest.data, "provenance": ctx.manifest.provenance}, text)


# -- session -----------------------------------------------------------


def cmd_session_start(args) -> int:
    ctx = Context()
    session = ctx.sessions.start(
        args.name,
        session_id=args.id,
        base=args.base,
        host_session_id=args.host_session_id or ctx.host_session_id,
        # The escape from the refusal that stops a plan forking. The domain
        # refuses a task name whose parked session is sitting right there and
        # ends "add --force" — which, until this argument was passed through,
        # named a flag the CLI did not have. A refusal whose remedy cannot be
        # run is the same dead end as a `fixes` line naming an unwritten
        # command, one layer up.
        force=args.force,
    )
    plan = ctx.sessions.isolation_plan(session)
    return emit(
        args,
        {"session": session.to_dict(), "plan": plan},
        # Line one is a CONTRACT with `hosts/claude_code`: it binds a session
        # by parsing this command's own stdout for `session <id> (`, which is
        # how a registration made through a shell — carrying no hook payload —
        # gets attributed at all. Anything added here goes BELOW it.
        f"session {session.id} ({session.name}) registered\n"
        # The base is shown because it is CHOSEN here and read nowhere before
        # `isolate`, where a wrong one used to surface as git's "invalid
        # reference" on every repository whose default branch is not master.
        f"  base: {session.base}"
        + ("" if args.base else "  (the main checkout's branch; --base overrides)")
        + "\n"
        f"  next: invariant session isolate --execute\n"
        f"        worktree {plan['worktree']}  branch {plan['branch']}\n"
        # The name carries the id, so it is an address. Printed at the one
        # moment the operator is looking, because the alternative route to it
        # is remembering that `session list` exists a week later.
        f"  resume it later with: invariant session resume {session.name}",
    )


def cmd_session_isolate(args) -> int:
    ctx = Context()
    session = ctx.require_session() if not args.id else ctx.sessions.require(args.id)
    plan = ctx.sessions.isolate(session.id, execute=args.execute)
    if args.execute:
        return emit(args, plan, f"isolated into {plan['worktree']} on {plan['branch']}")
    return emit(args, plan, plan.get("command", ""))


def cmd_session_status(args) -> int:
    ctx = Context()
    session = (
        ctx.sessions.require(args.id) if getattr(args, "id", None)
        else ctx.current_session()
    )
    if session is None:
        return emit(args, {"session": None}, "no session is active in this directory")
    pipeline = ctx.pipelines.current(session.id)
    text = [
        f"session {session.id}  {session.name}",
        f"  state      {session.state}",
        f"  worktree   {session.worktree or '(none)'}",
        f"  branch     {session.branch or '(none)'}",
        f"  files      {len(session.files)} declared",
        f"  pushes     {session.push_count}",
    ]
    if pipeline:
        text.append(f"  pipeline   {pipeline.methodology} in {pipeline.state}")
    # The plan, where somebody picking the session up will look for it. Only
    # when there IS one: an unplanned session is a legitimate resting state —
    # the same judgement `session.deliverables_complete` makes when it skips
    # rather than fails — and a permanent "plan (none)" line on every one-line
    # chore is a nag, which is how a line stops being read.
    progress = plan_reader.progress(session.plan)
    if progress.total:
        tail = (
            f"next: {progress.remaining[0]['title']}"
            if progress.remaining
            else "complete"
        )
        text.append(
            f"  plan       {progress.done}/{progress.total} done — {tail}"
        )
    if session.resumed_count:
        text.append(
            f"  resumed    {session.resumed_count} host session(s) picked "
            f"this up"
        )
    # Loud, and above the fold. A held override is enforcement the operator
    # turned off and may well have forgotten; the whole reason it is a recorded
    # grant rather than a flag is that it can be seen and withdrawn.
    if session.standdown:
        held = session.standdown
        text.append(
            f"  STOOD DOWN  {held['reason']} "
            f"(until this {held['until']} ends; invariant standdown --clear)"
        )
    for grant in session.pending_overrides:
        text.append(
            f"  override   {grant['rule']} — {grant['reason']} "
            f"({grant.get('calls', 1)} call(s) left)"
        )
    return emit(
        args,
        {"session": session.to_dict(), "pipeline": pipeline.to_dict() if pipeline else None},
        "\n".join(text),
    )


def cmd_override(args) -> int:
    """Grant, or withdraw, a one-shot pass for a rule that fires at a hook.

    Deliberately a top-level command rather than a flag on something. There is
    nothing for it to be a flag ON: the call it covers is a tool call the agent
    host makes, which carries no arguments the operator can add and no
    environment a running host will re-read. The grant has to exist before the
    call and be found by it (BUG_049).
    """
    ctx = Context()
    session = ctx.require_session() if not args.id else ctx.sessions.require(args.id)

    if args.clear:
        updated = ctx.sessions.clear_override(session.id, args.rule)
        return emit(
            args,
            updated.to_dict(),
            f"withdrew any unspent override for {args.rule}",
        )

    updated = ctx.sessions.grant_override(
        session.id, args.rule, args.reason, calls=args.calls
    )
    level = ctx.manifest.rule_level(args.rule).value
    return emit(
        args,
        updated.to_dict(),
        f"override granted for {args.rule} ({level} in this project)\n"
        f"  reason: {args.reason}\n"
        f"  good for {args.calls} call(s) at a hook, then it is spent\n"
        f"  spending it is recorded on this session; withdraw it with "
        f"--clear",
    )


def cmd_standdown(args) -> int:
    """Hold every gate for an interval, reversibly, with the hold recorded.

    The per-call sibling is `invariant override`. They share a seam and are not
    the same want: one is "let this call through", the other is "stop asking
    for the next while".
    """
    ctx = Context()
    session = ctx.require_session() if not args.id else ctx.sessions.require(args.id)

    if args.clear:
        updated = ctx.sessions.stand_up(session.id)
        return emit(args, updated.to_dict(), "enforcement is back on")

    pipeline = ctx.pipelines.current(session.id)
    updated = ctx.sessions.stand_down(
        session.id,
        args.reason,
        until=args.until,
        include=args.include or (),
        pipeline_started=pipeline.started if pipeline else None,
    )
    record = updated.standdown
    ends = (
        "when this session ends"
        if record["until"] == "session"
        else (
            "when this pipeline finishes"
            if pipeline
            else "when a pipeline starts and finishes"
        )
    )
    held_out = sorted(HELD_OUT_OF_STANDDOWN - set(record["include"]))
    lines = [
        f"enforcement STOOD DOWN — {record['reason']}",
        f"  lifts {ends}, or on: invariant standdown --clear",
    ]
    if held_out:
        lines.append(
            "  still enforced: "
            + ", ".join(held_out)
            + "  (name it with --include to fold it in)"
        )
    lines.append("  every turn will say so until it lifts")
    return emit(args, updated.to_dict(), "\n".join(lines))


def cmd_session_transition(args) -> int:
    ctx = Context()
    session = ctx.require_session() if not args.id else ctx.sessions.require(args.id)
    try:
        updated = ctx.sessions.transition(
            session.id, args.state, reason=args.reason, force=args.force, cwd=ctx.cwd
        )
    except GateFailure as exc:
        # The domain evaluates the machine's gates on EVERY route (AUD-05);
        # this command's job is only to show the verdicts it was refused on.
        report_verdicts(exc.verdicts, args)
        if getattr(args, "json", False):
            print(json.dumps({"advanced": False, "gates": verdicts_payload(exc.verdicts)}, indent=2))
        _record_cli_refusal(ctx, session, exc.verdicts)
        raise
    _lift_cli_refusal(ctx, session)
    return emit(args, updated.to_dict(), f"session {updated.id} -> {updated.state}")


def cmd_session_files(args) -> int:
    ctx = Context()
    session = (
        ctx.sessions.require(args.id) if getattr(args, "id", None)
        else ctx.require_session()
    )
    if args.action == "remove":
        before = len(session.files)
        updated = ctx.sessions.remove_files(session.id, args.paths)
        dropped = before - len(updated.files)
        return emit(
            args,
            updated.to_dict(),
            f"dropped {dropped} entry(ies); {len(updated.files)} file(s) "
            f"declared for session {updated.id}",
        )
    updated = ctx.sessions.add_files(session.id, args.paths)
    return emit(
        args,
        updated.to_dict(),
        f"{len(updated.files)} file(s) declared for session {updated.id}",
    )


def cmd_session_list(args) -> int:
    ctx = Context()
    sessions = ctx.sessions.list()
    if not sessions:
        return emit(args, {"sessions": []}, "no registered sessions")
    # The fraction, not the checklist. This is the list somebody scans to find
    # the session they parked, and "how far along" is the one thing that
    # distinguishes two sessions on the same task. A session with no plan reads
    # `-` rather than `0/0`, because those are different facts.
    text = "\n".join(
        f"  {s.id}  {s.state:<12} {s.name:<24} {_plan_fraction(s):<7} "
        f"{s.worktree or ''}"
        for s in sessions
    )
    return emit(args, {"sessions": [s.to_dict() for s in sessions]}, text)


def cmd_session_adopt(args) -> int:
    ctx = Context()
    session_id = args.id
    if not session_id:
        current = ctx.current_session(strict=False)
        if current is None:
            raise UsageError(
                "cannot tell which session to adopt from here; pass --id. "
                "'invariant session list' shows the registered ones."
            )
        session_id = current.id
    updated = ctx.sessions.adopt_host_session(
        session_id, args.host_session_id, force=args.force
    )
    return emit(
        args,
        updated.to_dict(),
        f"session {updated.id} now answers to this host session",
    )


def cmd_session_resume(args) -> int:
    """Pick up work a previous host session put down.

    The sibling of :func:`cmd_session_adopt`, and the distinction between them
    is which binding they replace. `adopt` claims a session that has NO owner;
    this one replaces a **spent** binding — a host session that ended, leaving
    a worktree, a branch, a pipeline and a plan behind. Neither may replace a
    LIVE binding without --force, because that is the hand-over ADR-001 D19
    forbids: work changing owner behind its owner's back.

    Two properties of the OUTPUT are contracts rather than presentation.

    **The success line is parsed.** `hosts/claude_code` binds a host session to
    an invariant session by reading `session <id> (` out of the stdout of the
    command that claimed it — a command run through a shell carries no hook
    payload, so this text is the only evidence of which session was claimed.

    **The refusals must not be parseable that way.** The same reader searches
    the WHOLE tool response, and a host that merges streams puts stderr in it.
    A refusal carrying `session <id> (` would bind the caller to a session it
    just refused to give them — silently, and to the wrong one. So every
    refusal below names sessions in the `<name>  [<state>]` shape the ambiguity
    messages already use, which has no such sequence in it.
    """
    ctx = Context()
    session = _resume_target(ctx, args)
    updated = ctx.sessions.resume(
        session.id, host_session_id=ctx.host_session_id, force=args.force
    )

    progress = plan_reader.progress(updated.plan)
    if not progress.total:
        headline = "no deliverables recorded"
        # An unplanned session resumed is the moment the plan is worth
        # recording: whatever is left is in somebody's head right now, and this
        # is the last point before it is in a commit instead.
        second = 'record what is left: invariant session plan add "<deliverable>"'
    else:
        headline = f"{progress.done}/{progress.total} deliverables done"
        second = (
            f"next: {progress.remaining[0]['title']}"
            if progress.remaining
            else "every deliverable is done; land it: invariant session review"
        )
    return emit(
        args,
        {"session": updated.to_dict(), "plan": _plan_payload(updated)},
        "\n".join(
            [
                f"session {updated.id} ({updated.name}) resumed — {headline}",
                f"  {second}",
                (
                    f"  worktree {updated.worktree or '(not isolated yet)'}"
                    f"   branch {updated.branch or '(none)'}"
                ),
                (
                    "  it binds to this host session on the next tool call; "
                    "confirm with: invariant session status"
                ),
            ]
        ),
    )


def _resume_target(ctx, args):
    """Which session `resume` was asked for, or a refusal that says why not.

    `--id` is accepted alongside the name because every ambiguity message in
    this library tells the caller to pass it, and a subcommand that then
    rejected it would make that advice a dead end. When both are given they
    must AGREE: silently preferring one would let a copied-and-half-edited
    command resume a session the caller did not name.
    """
    if args.id:
        session = ctx.sessions.require(args.id)
        if args.name and not name_matches(session.name, session.id, args.name):
            raise UsageError(
                f"--id names {session.name}, which does not answer to "
                f"{args.name!r}.\n"
                f"  Name it one way or the other, not two ways that disagree."
            )
        return session

    if not args.name:
        raise UsageError(
            "name the session to resume.\n"
            "  invariant session list   shows what is registered here\n"
            "  invariant session resume <name>   takes a name, a bare task "
            "slug, or an id"
        )

    session, candidates = ctx.sessions.resolve_name(args.name)
    if session is not None:
        return session
    if candidates:
        raise UsageError(f"{args.name!r} — {name_ambiguity_message(candidates)}")
    raise UsageError(
        f"no session answers to {args.name!r}.\n"
        "  invariant session list   shows what is registered here\n"
        "  invariant session start <task-name>   begins a new one"
    )


def _real_handoff_launcher():
    from ..hosts.claude_code_launch import TerminalLauncher

    return TerminalLauncher()


def _handoff_launcher():
    """The launcher `session handoff` uses. The one seam a test replaces."""
    return _real_handoff_launcher()


def _real_handoff_closer():
    from ..hosts.claude_code_launch import DetachedCloser

    return DetachedCloser()


def _handoff_closer():
    """The closer that ends the launching host after a handoff. A seam, like
    `_handoff_launcher`: the suite replaces it everywhere, so no test signals
    a process (IDEA_009)."""
    return _real_handoff_closer()


def _real_handoff_watch():
    from ..hosts.claude_code_launch import ProcessTableWatch

    return ProcessTableWatch()


def _handoff_successor():
    """The watch that proves a handoff started a live successor (BUG_075). A
    seam, like `_handoff_launcher`: the suite replaces it everywhere, so no
    test waits on the process table, and a test that wants a launch to count
    says so."""
    return _real_handoff_watch()


def _launch_successor(launch, plan) -> int:
    """Launch ``plan`` and return the pid of the live successor it started.

    Terminal accepting the script is not a launch: the window's shell can
    still run nothing, as a tty-cut line did (BUG_075). Sessions of the same
    name already running are read first and never counted. On any failure the
    launch file is removed, so a window that opens late runs nothing rather
    than contradicting the failure just reported, and the error carries the
    command to run by hand. The caller signals nothing either way.
    """
    watch = _handoff_successor()
    before = watch.running(plan.name)
    try:
        _handoff_launcher().launch(plan)
        pid = watch.wait_for(plan.name, exclude=before)
    except BaseException:
        plan.discard()
        raise
    if pid is None:
        plan.discard()
        raise InvariantError(
            f"the handoff did not start a successor: no claude with "
            f"--name {plan.name} --remote-control {plan.name} is running "
            f"{launch.SUCCESSOR_GRACE_SECONDS}s after it appeared, or it never "
            f"appeared within {launch.SUCCESSOR_TIMEOUT_SECONDS}s. This session "
            f"stays open, and the note is recorded. Run it by hand:\n"
            f"    {plan.by_hand}"
        )
    return pid


def _close_launching_host(launch, keep_open: bool) -> tuple[dict, str]:
    """After a successful launch, schedule the end of the host that ran it.

    Returns the JSON `close` document and the one line the output carries.
    Every route that does not schedule says why and leaves the session open:
    `--keep-open`, no usable `CLAUDE_PID`, or a pid that does not name a
    `claude` process now. The closer checks the name again at kill time.
    """
    close = {"scheduled": False, "pid": None, "delay": None}
    if keep_open:
        return close, "  left this session open (--keep-open)"
    raw = os.environ.get(launch.HOST_PID_ENV)
    pid = launch.host_pid(os.environ)
    if pid is None:
        why = (
            f"{launch.HOST_PID_ENV} is not set"
            if not (raw or "").strip()
            else f"{launch.HOST_PID_ENV}={raw!r} names no process"
        )
        return close, f"  leaving this session open: {why}, so there is no claude to end"
    close["pid"] = pid
    closer = _handoff_closer()
    name = closer.process_name(pid)
    if not launch.is_claude_process_name(name):
        what = name or "no running process"
        return close, (
            f"  leaving this session open: {launch.HOST_PID_ENV} {pid} names "
            f"{what}, not claude"
        )
    delay = launch.CLOSE_DELAY_SECONDS
    closer.close(pid, delay)
    close.update({"scheduled": True, "delay": delay})
    return close, (
        f"  this session ends in {delay}s: SIGTERM to claude (pid {pid}); "
        f"--keep-open keeps it next time"
    )


def _dismiss_handoff(args) -> int:
    """`session handoff --dismiss`: retire the note the greeting is showing.

    Takes no note, name or id — it acts on what an unbound greeting here
    offers, which is the latest fresh note, and naming anything else would be
    a second selector over the same list. Like the handoff, it never prints
    ``session <id> (``: it claims nothing.
    """
    stray = [
        flag
        for flag, value in (
            ("--note", args.note),
            ("--name", args.name),
            ("--id", args.id),
            ("--keep-open", args.keep_open),
        )
        if value
    ]
    if stray:
        raise UsageError(
            f"--dismiss retires the latest fresh note here and takes no "
            f"{', '.join(stray)}"
        )
    ctx = Context()
    if args.dry_run:
        session, entry = ctx.sessions.dismissable_handoff(ctx.cwd)
        verb = "would dismiss"
    else:
        resolution = ctx.sessions.resolve(ctx.cwd, host_session_id=ctx.host_session_id)
        by = resolution.session.id if resolution.proven and resolution.session else None
        session, entry = ctx.sessions.dismiss_handoff(
            ctx.cwd, session_id=by, host_session_id=ctx.host_session_id
        )
        verb = "dismissed"
    result = {
        "dismissed": not args.dry_run,
        "dry_run": bool(args.dry_run),
        "session": session.id,
        "session_name": session.name,
        "name": entry.get("name"),
        "note": entry.get("note"),
    }
    return emit(
        args,
        result,
        "\n".join(
            [
                f"{verb} handoff {entry.get('name')} from {session.name}:",
                f"    {entry.get('note')}",
            ]
        ),
    )


def cmd_session_handoff(args) -> int:
    """Hand the work to a fresh host session, in one command.

    Records the note on the session, then opens a new Terminal window in the
    MAIN checkout running ``claude --name <slug> --remote-control <slug>
    "<note>"``. The order of the steps is the design:

    1. **Vet the note and settle the name first.** Both are refusals that must
       happen before anything is written or launched — a note with personal
       data in it is not recorded, and a session nobody can name is not
       started. `--dry-run` runs exactly these, so it refuses what the real run
       would.
    2. **Refuse what cannot launch**, still before writing: another host kind,
       a platform without Terminal, no `claude` on PATH. A refusal leaves no
       trace; the non-macOS one prints the command to run by hand.
    3. **Record, then launch.** If the launch fails the note is already on
       the record, and the greeting of whatever session starts next shows it —
       which is the fallback for a lost prompt argument, too.
    4. **A launch succeeds only once a successor is seen alive** (BUG_075):
       a new process whose argv carries ``--name <slug> --remote-control
       <slug>``, still running after a grace period. Without one the launch
       failed, whatever Terminal answered: exit non-zero, nothing signalled.
    5. **End the old host, last, and only after a launch that succeeded**
       (IDEA_009). The output is printed first; the launching `claude`
       (`CLAUDE_PID`) is sent SIGTERM `CLOSE_DELAY_SECONDS` later, by a
       detached sleeper that checks the pid still names `claude` before it
       signals. `--keep-open` opts out; a dry run, a refusal and a failed
       launch never get here, and a missing or foreign pid is reported and
       left alone.

    The launched line carries the claim token naming the note just recorded,
    so the new host's first `session start` / `session resume` consumes that
    note (IDEA_008). A dry run and the by-hand line on another platform carry
    none: nothing was recorded, so there is no note to name.

    The output never prints ``session <id> (``: that sequence is the binding
    contract `session start` and `session resume` own (see their docstrings),
    and a handoff claims nothing.
    """
    from ..core import git
    from ..domains.session import handoff_name, handoff_token
    from ..hosts import claude_code_launch as launch

    if args.dismiss:
        return _dismiss_handoff(args)
    if not args.note:
        raise UsageError(
            "session handoff needs --note \"<line>\" to hand off, or --dismiss "
            "to retire the latest fresh note without acting on it"
        )

    ctx = Context(session_id=args.id)
    explicit = args.id or os.environ.get("INVARIANT_SESSION_ID")
    if explicit:
        session = ctx.sessions.require(explicit)
    else:
        resolution = ctx.sessions.resolve(
            ctx.cwd, host_session_id=ctx.host_session_id
        )
        session = (
            resolution.session
            if resolution.proven
            else ctx.sessions.handoff_candidate(ctx.cwd, ctx.host_session_id)
        )
    if session is None:
        raise UsageError(
            "no session to record a handoff on: none is registered at or above "
            "this directory.\n"
            "  Name one: invariant session handoff --note \"...\" --id <id>"
        )

    note = ctx.sessions.vet_handoff_note(args.note)
    name = handoff_name(note, name=args.name)
    if not name:
        raise UsageError(
            "the note has no 'next:' item to name the new session after.\n"
            "  Add one — next: <what the next session is for> — or name it: "
            "--name <slug>"
        )

    main_checkout = git.main_checkout(ctx.manifest.root) or ctx.manifest.root
    claude = launch.resolve_claude()
    plan = launch.plan_launch(
        claude or launch.CLAUDE_BINARY, name, note, main_checkout, stage=False
    )
    result = {
        "session": session.id,
        "session_name": session.name,
        "name": name,
        "note": note,
        "argv": list(plan.argv),
        "cwd": plan.cwd,
        "shell_line": plan.shell_line,
        "by_hand": plan.by_hand,
        "launch_file": None,
        "applescript": plan.applescript,
        "token": None,
        "dry_run": bool(args.dry_run),
        "successor": None,
        "close": {"scheduled": False, "pid": None, "delay": None},
    }

    if args.dry_run:
        lines = [
            (
                f"handoff {name} from {session.name} [{session.state}] — dry run, "
                "nothing recorded or launched"
            ),
            f"  command:     {plan.by_hand}",
            f"  typed:       {plan.shell_line}",
            (
                f"  a real run writes the command to a private launch file "
                f"(mode 0600, under {launch.launch_dir()}), which removes "
                f"itself as claude starts"
            ),
            "  applescript:",
            *[f"    {line}" for line in plan.applescript.splitlines()],
            (
                f"  a real run sets {launch.HANDOFF_ENV}=<session>:<recorded-at> "
                f"before claude, so the new session claims this note"
            ),
            (
                "  --keep-open: a real run leaves this session open"
                if args.keep_open
                else (
                    f"  a real run then ends this session: SIGTERM to "
                    f"{launch.HOST_PID_ENV} after {launch.CLOSE_DELAY_SECONDS}s, "
                    f"unless --keep-open"
                )
            ),
        ]
        if claude is None:
            lines.append(
                f"  note: {launch.CLAUDE_BINARY} is not on PATH; a real run "
                f"refuses until it is"
            )
        return emit(args, result, "\n".join(lines))

    kind = ctx.manifest.get("host.kind")
    if kind != "claude_code":
        raise UsageError(
            f"session handoff starts a Claude Code session, and this project's "
            f"host.kind is {kind!r}, not 'claude_code'"
        )
    if claude is None:
        raise UsageError(
            f"{launch.CLAUDE_BINARY} is not on PATH, so there is nothing to "
            f"launch. Install Claude Code, or put it on PATH, and run the "
            f"handoff again; nothing was recorded."
        )
    if launch.host_platform() != "darwin":
        raise UsageError(
            "session handoff opens a new window through macOS Terminal, and "
            "this is not macOS; nothing was recorded. Run it by hand:\n"
            f"  {plan.by_hand}"
        )

    updated = ctx.sessions.record_handoff(session.id, note, name)
    token = handoff_token(updated.id, updated.handoffs[-1]["at"])
    plan = launch.plan_launch(claude, name, note, main_checkout, token=token)
    result.update(
        {
            "token": token,
            "shell_line": plan.shell_line,
            "by_hand": plan.by_hand,
            "launch_file": plan.launch_file,
            "applescript": plan.applescript,
        }
    )
    successor = _launch_successor(launch, plan)
    result["successor"] = {"pid": successor}
    close, close_line = _close_launching_host(launch, args.keep_open)
    result["close"] = close
    return emit(
        args,
        result,
        "\n".join(
            [
                f"handed off as {name}",
                f"  recorded on {updated.name} [{updated.state}]",
                f"  opened a new Terminal window in {plan.cwd}:",
                f"    {plan.shell_line}",
                f"  successor claude is running as {name} (pid {successor})",
                close_line,
            ]
        ),
    )


def cmd_session_plan(args) -> int:
    """The deliverable set this session owes: add, tick, untick, drop, show.

    **Nested subparsers rather than `session files`' flat `action` + positional
    shape**, because the argument shapes genuinely differ where that command's
    do not: `files` takes a list of paths for both of its actions, while `add`
    takes one free-text title, three actions take an integer, and `show` takes
    nothing. Flattened, the parser could only accept a string and the command
    would hand-roll the type check — so `plan done abc` would reach the domain
    and come back as "no deliverable 'abc' in session …", a report about the
    plan for what is a malformed command. One function still serves all five,
    so the dispatch stays in one place.

    `add` takes exactly ONE positional, so a lost quote is a usage error rather
    than a truncated title. That is the same failure the domain's own length
    bound catches from the other direction — a shell handing a whole list over
    as one argument — and both refusals are cheaper than a plan entry that
    silently says half of what was meant.

    Every action prints the WHOLE plan, and the same JSON document. A mutation
    that reported only itself would leave the caller re-running `show` to see
    the numbering it just changed, and two output shapes for one subcommand is
    two things for a consumer to parse.
    """
    ctx = Context()
    session = (
        ctx.sessions.require(args.id) if getattr(args, "id", None)
        else ctx.require_session()
    )
    action = args.plan_action
    if action == "add":
        session = ctx.sessions.plan_add(session.id, args.title)
    elif action == "done":
        session = ctx.sessions.plan_done(session.id, args.n)
    elif action == "reopen":
        session = ctx.sessions.plan_reopen(session.id, args.n)
    elif action == "remove":
        session = ctx.sessions.plan_remove(session.id, args.n)
    return emit(args, _plan_payload(session), _plan_text(session))


def _plan_payload(session) -> dict:
    """The plan as a machine reads it.

    ``planned`` and ``complete`` are SEPARATE keys and neither is derivable
    from the other by a consumer that only has one. ``planned`` is "a checklist
    exists"; ``complete`` is "and nothing on it is open". Collapsed into a
    single flag, a session that never recorded a plan is indistinguishable from
    one that finished its plan — so a reviewer switching mode on it either
    defers the merge forever on every project that does not keep checklists, or
    passes every unplanned session as finished. ``core.plan.progress`` is where
    the distinction is computed, once, for exactly this reason.
    """
    progress = plan_reader.progress(session.plan)
    return {
        "session": session.id,
        "name": session.name,
        "plan": plan_reader.entries(session.plan),
        "total": progress.total,
        "done": progress.done,
        "next": progress.remaining[0]["title"] if progress.remaining else None,
        "planned": progress.total > 0,
        "complete": progress.complete,
    }


def _plan_text(session) -> str:
    """The plan as a person reads it.

    The entries come from ``core.plan.render_lines`` rather than from a second
    renderer here. There will be another caller — the state a host injects at
    the top of a turn — and two renderings of one list is two chances for the
    numbers printed to disagree with the numbers the commands take.
    """
    progress = plan_reader.progress(session.plan)
    lines = [
        (
            f"plan for session {session.id} ({session.name}) — "
            f"{progress.done}/{progress.total} done"
        )
    ]
    lines += [f"  {line}" for line in plan_reader.render_lines(session.plan)]
    if progress.remaining:
        lines.append(f"  next: {progress.remaining[0]['title']}")
    elif not progress.total:
        lines.append(
            '  record what is owed: invariant session plan add "<deliverable>"'
        )
    return "\n".join(lines)


def _plan_fraction(session) -> str:
    """`2/5`, or `-` where no plan was ever recorded."""
    total = session.plan_total
    return f"{session.plan_done_count}/{total}" if total else "-"


def cmd_session_sync(args) -> int:
    """Rebase this session's branch onto its base, from inside its worktree.

    What `session.branch_is_current` names as its fix. The model has always
    said "rebase onto the local base after every commit"; until this command
    existed, saying it was all anything did.

    **Every refusal runs before git does**, in the same shape as
    `cmd_session_merge`: a validation that runs after an irreversible step is a
    report, not a gate, and a rebase rewrites commits.

    **It refuses to run outside the session's own worktree**, which is the
    mirror of `session.ff_merge_target`'s reasoning one command over. `git
    rebase` acts on whatever HEAD the calling tree has checked out, so run from
    the main checkout it rebases the BASE branch onto itself, reports success
    and exits zero — the invisible-success shape this library exists to catch.
    The same argument makes a detached HEAD a refusal: the branch this session
    owns is what must move, and HEAD is what would.

    **A conflict is reported, never resolved.** The rebase is aborted, which
    puts the commits back exactly where they were, and the operator is left
    with the decision. Resolving a conflict is a judgement about two people's
    intent, and a program that guesses at it produces a merge nobody reviewed.
    """
    import subprocess

    from ..core import git

    ctx = Context()
    session = ctx.sessions.require(args.id) if args.id else ctx.require_session()

    if not session.branch:
        raise UsageError(
            f"session {session.name} owns no branch, so there is nothing to "
            f"rebase.\n  invariant session isolate --execute  creates one"
        )

    # Where the caller is standing, answered by the domain rather than by a
    # second path comparison here: `by_worktree_containing` is what every other
    # caller asks, and it already excludes a stranded worktree.
    here = ctx.sessions.by_worktree_containing(ctx.cwd)
    if here is None or here.id != session.id:
        where = session.worktree or "(this session never isolated)"
        raise UsageError(
            f"a rebase acts on the branch the CALLING tree has checked out, "
            f"and this is not session {session.name}'s worktree.\n"
            f"  Run it from: {where}\n"
            f"  Run from anywhere else it rebases a different branch and "
            f"exits zero, which is the same invisible success "
            f"session.ff_merge_target refuses at the merge."
        )

    branch = git.current_branch(ctx.cwd)
    if branch != session.branch:
        raise UsageError(
            f"this worktree has {branch or 'a detached HEAD'} checked out, not "
            f"{session.branch}. A rebase moves HEAD's branch, so it would move "
            f"the wrong one.\n  git switch {session.branch}"
        )

    # Uncommitted work in TRACKED files is what `git rebase` itself refuses on,
    # and asking first buys a message that names the way out. Untracked files
    # are deliberately not counted: git only minds them when a replayed commit
    # would overwrite one, and refusing on scratch files would make this
    # command unusable in the tree it is meant to be run from.
    dirty = git.changed_files(ctx.cwd)
    if dirty:
        listed = ", ".join(str(path) for path in dirty[:5])
        raise UsageError(
            f"{len(dirty)} file(s) carry uncommitted changes: {listed}\n"
            f"  A rebase replays commits, so it refuses to run over work that "
            f"is not in one.\n"
            f"  Commit them, or set them aside with 'git stash', then retry."
        )

    # The same helper and the same argument order as `session.branch_is_current`
    # reads, so the number this command acts on and the number that gate
    # reports cannot disagree.
    behind = git.commits_ahead(ctx.cwd, session.base, session.branch)
    if behind == 0:
        return emit(
            args,
            {"rebased": False, "behind": 0, "branch": session.branch},
            f"{session.branch} already carries everything on {session.base}",
        )

    proc = subprocess.run(
        ["git", "rebase", session.base],
        cwd=str(ctx.cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        abort = subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=str(ctx.cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise UsageError(
            f"the rebase onto {session.base} stopped: "
            f"{detail[-1] if detail else 'git said nothing'}\n"
            + (
                "  It was aborted, so the branch and its commits are exactly "
                "as they were.\n"
                if abort.returncode == 0
                else "  The abort ALSO failed; check 'git status' before "
                "anything else.\n"
            )
            + "  Resolve it by hand — a conflict is a decision about two "
            "people's intent, and this command will not guess at one."
        )

    return emit(
        args,
        {
            "rebased": True,
            "behind": behind,
            "branch": session.branch,
            "base": session.base,
        },
        f"{session.branch} rebased onto {session.base} — it was {behind} "
        f"commit(s) behind and now carries everything on it",
    )


def cmd_session_reap(args) -> int:
    ctx = Context()
    reaped = ctx.sessions.reap_stranded(dry_run=args.dry_run)
    verb = "would abandon" if args.dry_run else "abandoned"
    text = (
        "\n".join(f"  {verb} {s.id} ({s.name}) — worktree and branch both gone"
                   for s in reaped)
        or "  nothing to reap"
    )
    return emit(args, {"reaped": [s.id for s in reaped]}, text)


def cmd_session_review(args) -> int:
    """Evaluate the review gates, and advance if they hold.

    Documented in two places and implemented in none — the CLI rejected it as
    an unknown subcommand while `CLAUDE.md` and the CLI reference both told
    people to run it. A doc-promised check that was never written is the exact
    thing this library exists to make impossible, so it is written rather than
    deleted from the docs.

    The gates themselves live in the domain's ``_advance`` now, where every
    route — including the generic ``session transition`` — runs them. This
    command used to carry its own copy of the gate list, which is exactly how
    the generic path came to have none (AUD-05).
    """
    ctx = Context()
    session = ctx.sessions.require(args.id) if args.id else ctx.require_session()

    machine = ctx.sessions.machine
    if not machine.can(session.state, "reviewed"):
        legal = ", ".join(machine.next_states(session.state)) or "(none)"
        raise UsageError(
            f"session {session.id} is {session.state!r}; review is not legal "
            f"from there.\n  Legal from {session.state!r}: {legal}"
        )

    try:
        ctx.sessions.transition(
            session.id, "reviewed", reason=args.reason or "review gates evaluated",
            force=args.force, cwd=ctx.cwd,
        )
    except GateFailure as exc:
        report_verdicts(exc.verdicts, args)
        _record_cli_refusal(ctx, session, exc.verdicts)
        raise GateFailure(
            "the review gates refused this session", verdicts=exc.verdicts
        ) from None
    _lift_cli_refusal(ctx, session)
    return emit(
        args,
        {"session": session.id},
        f"session {session.id} reviewed",
    )


def cmd_session_merge(args) -> int:
    import subprocess

    from ..core import git

    ctx = Context()
    session = ctx.require_session() if not args.id else ctx.sessions.require(args.id)
    if not session.branch:
        raise UsageError(f"session {session.id} has no branch to merge")

    main = git.main_checkout(ctx.cwd)
    if main is None:
        raise UsageError("not inside a git repository")

    # Checked BEFORE the merge runs, not after. This used to advance git and
    # then refuse the transition, leaving the branch merged and the record
    # still reading `isolated` — git and the registry disagreeing, which is the
    # phantom-session class arrived at from the other end. A validation that
    # runs after the irreversible step is not a gate, it is a report.
    if not ctx.sessions.machine.can(session.state, "merged"):
        legal = ", ".join(ctx.sessions.machine.next_states(session.state)) or "(none)"
        raise UsageError(
            f"session {session.id} is {session.state!r}; a merge is not legal "
            f"from there, and nothing has been merged.\n"
            f"  Legal from {session.state!r}: {legal}\n"
            f"  Walk it forward first: invariant session transition <state> "
            f"--id {session.id}"
        )

    # The gates the machine declares for this edge, evaluated BEFORE the merge.
    #
    # They were declared and never read: this command checked only that the
    # edge existed, so `merged`'s entry gates were decoration. The merge runs
    # git through a subprocess rather than a tool call, so the pre-tool-use
    # hook never sees it either — between the two, the irreversible step was
    # the least guarded one in the lifecycle.
    #
    # The list comes from the domain (`pending_gates`), not from a copy here:
    # the transition after the merge evaluates the same list inside
    # `_advance`, so what is asked before the irreversible step and what the
    # machine holds every other route to cannot drift apart (AUD-05).
    verdicts = ctx.sessions.pending_gates(session, "merged", cwd=ctx.cwd)
    blocking = [v for v in verdicts if v.blocks]
    if blocking and not args.force:
        report_verdicts(verdicts, args)
        _record_cli_refusal(ctx, session, verdicts)
        raise GateFailure(
            "the merge gates refused this session — nothing has been merged",
            verdicts=verdicts,
        )

    # Run from the main checkout, never from the branch's own worktree: that
    # merges the branch into itself and the follow-up push pushes an unchanged
    # target, with both commands exiting zero.
    steps = [
        (["git", "switch", session.base], main),
        (["git", "merge", "--ff-only", session.branch], main),
    ]
    performed = []
    for command, cwd in steps:
        proc = subprocess.run(
            command, cwd=str(cwd), capture_output=True, text=True, check=False
        )
        performed.append(" ".join(command))
        if proc.returncode != 0:
            raise UsageError(
                f"{' '.join(command)} failed: {proc.stderr.strip()}\n"
                f"Rebase onto {session.base} first, then retry."
            )

    if not git.branch_is_merged_into(main, session.branch, session.base):
        raise UsageError(
            f"{session.branch} still is not contained in {session.base} after a "
            f"merge that reported success — verify with 'git branch --contains'"
        )

    # Force travels through: the pre-flight above was forced past these same
    # gates, and the domain records the override rather than re-refusing a
    # merge that has already happened.
    ctx.sessions.transition(
        session.id, "merged", reason="ff-merge verified", force=args.force,
        cwd=ctx.cwd,
    )
    _lift_cli_refusal(ctx, session)
    return emit(
        args,
        {"steps": performed, "verified": True},
        f"merged {session.branch} into {session.base} (containment verified)",
    )


def cmd_session_push(args) -> int:
    import subprocess

    ctx = Context()
    session = ctx.require_session() if not args.id else ctx.sessions.require(args.id)

    # `pushed`'s entry gates, read from the machine. `session.push_enabled`
    # used to be duplicated here as a hardcoded `if`, so the policy lived
    # twice and only the copy the machine did not own ever ran (AUD-05).
    # Evaluated BEFORE the push, because a push is a remote event: refusing
    # the transition after it would leave the remote changed and the record
    # not. The gates first, then the structural check — the policy verdict
    # ("publishing is not a session step here") is the useful refusal, and
    # the barrier already refuses any state the edge would.
    verdicts = ctx.sessions.pending_gates(session, "pushed", cwd=ctx.cwd)
    blocking = [v for v in verdicts if v.blocks]
    if blocking and not args.force:
        report_verdicts(verdicts, args)
        raise GateFailure("the push gates refused this push", verdicts=verdicts)

    if not ctx.sessions.machine.can(session.state, "pushed") and not args.force:
        legal = ", ".join(ctx.sessions.machine.next_states(session.state)) or "(none)"
        raise UsageError(
            f"session {session.id} is {session.state!r}; a push is not legal "
            f"from there, and nothing has been pushed.\n"
            f"  Legal from {session.state!r}: {legal}"
        )

    proc = subprocess.run(
        ["git", "push", args.remote, session.base],
        cwd=str(ctx.cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise UsageError(f"push failed: {proc.stderr.strip()}")
    ctx.sessions.record_push(session.id, cwd=ctx.cwd, force=args.force)
    return emit(args, {"pushed": True}, f"pushed {session.base} to {args.remote}")


def cmd_session_teardown(args) -> int:
    ctx = Context()
    session = ctx.require_session() if not args.id else ctx.sessions.require(args.id)
    updated = ctx.sessions.teardown(
        session.id,
        remove_worktree=not args.keep_worktree,
        remove_branch=not args.keep_branch,
        # Where the caller is standing, which is the evidence the occupancy
        # precondition is built from (BUG_009). Passed rather than left to
        # default so that the domain reads the *caller's* directory and never
        # this process's, and so that the check is testable without a chdir.
        cwd=ctx.cwd,
        force=args.force,
    )
    removed = []
    if not args.keep_worktree and session.worktree:
        removed.append("worktree")
    if not args.keep_branch and session.branch:
        removed.append(f"branch {session.branch}")
    detail = f" ({', '.join(removed)} removed)" if removed else ""
    return emit(args, updated.to_dict(), f"session {updated.id} torn down{detail}")


# -- pipeline ----------------------------------------------------------


def cmd_pipeline_start(args) -> int:
    ctx = Context()
    session = ctx.require_session()
    pipeline = ctx.pipelines.start(
        session.id,
        methodology=args.methodology,
        task=args.task or "",
        # The baseline watermark every later per-state spend is subtracted
        # from (ADR-002 D3). Without it the first state would have a duration
        # and no spend, because there would be no earlier reading.
        host_session_id=session.host_session_id,
    )
    return emit(
        args,
        pipeline.to_dict(),
        f"pipeline {pipeline.methodology} started in {pipeline.state}\n"
        f"  gates here: invariant pipeline gates",
    )


def cmd_pipeline_ack(args) -> int:
    ctx = Context()
    session = ctx.require_session() if not args.id else ctx.sessions.require(args.id)
    pipeline = ctx.pipelines.acknowledge(session.id, args.rule, args.reason)
    return emit(
        args,
        pipeline.to_dict(),
        f"acknowledged {args.rule} for this pipeline\n"
        f"  reason: {args.reason}\n"
        f"  it will report as skipped, with that reason, until this pipeline ends",
    )


def cmd_pipeline_coverage(args) -> int:
    ctx = Context()
    session = ctx.require_session() if not args.id else ctx.sessions.require(args.id)
    result = ctx.pipelines.run_coverage(session.id, cwd=ctx.cwd)
    lines = [
        # Echo what was actually run. Discovery substitutes values nobody typed
        # — a simulator id most of all — and a wrong one produces a plausible
        # result with no sign of where it came from.
        f"ran: {result['command']}",
        f"suite: {describe_run(result)}",
        f"recorded coverage for {result['files']} file(s)",
    ]
    for path, pct in sorted(result["coverage"].items())[:10]:
        lines.append(f"  {path}  {pct:g}%")
    return emit(args, result, "\n".join(lines))


def cmd_pipeline_status(args) -> int:
    ctx = Context()
    session = ctx.require_session()
    pipeline = ctx.pipelines.current(session.id)
    if pipeline is None:
        return emit(args, {"pipeline": None}, "no pipeline is running")
    machine = ctx.pipelines.machine_for(pipeline)
    text = [
        f"pipeline {pipeline.methodology}  task: {pipeline.task or '(unnamed)'}",
        f"  state   {pipeline.state}",
        f"  legal   {', '.join(machine.next_states(pipeline.state)) or '(terminal)'}",
    ]
    if pipeline.overrides:
        text.append(f"  forced  {len(pipeline.overrides)} override(s) recorded")
    return emit(args, pipeline.to_dict(), "\n".join(text))


def cmd_pipeline_gates(args) -> int:
    ctx = Context()
    session = ctx.require_session()
    if args.state:
        verdicts = ctx.pipelines.evaluate_gates(
            session.id, target=args.state, session=session.to_dict(), cwd=ctx.cwd
        )
        text = "\n".join(v.render() for v in verdicts) or "no gates on this transition"
        text += f"\n\n{summarise(verdicts)}"
        return emit(args, {"gates": verdicts_payload(verdicts)}, text)

    # No target named: report per destination rather than as one list. Since
    # the gates moved onto the outcomes they protect (BUG_037) the edges out of
    # a state no longer owe the same things, and a flat list would say a review
    # state owes the ship gates while hiding that the retreats owe none of
    # them — which is the misreading the bug was reported as.
    by_target = ctx.pipelines.evaluate_gates_by_destination(
        session.id, session=session.to_dict(), cwd=ctx.cwd
    )
    blocks = []
    for target, verdicts in by_target.items():
        rendered = "\n".join("  " + v.render() for v in verdicts) or "  (ungated)"
        blocks.append(f"to {target}:\n{rendered}")
    flat = [v for verdicts in by_target.values() for v in verdicts]
    seen: dict[str, object] = {}
    for verdict in flat:
        seen.setdefault(verdict.rule_id, verdict)
    text = "\n".join(blocks) or "no transitions from here"
    text += f"\n\n{summarise(list(seen.values()))}"
    return emit(
        args,
        {
            "by_destination": {
                target: verdicts_payload(verdicts)
                for target, verdicts in by_target.items()
            },
            "gates": verdicts_payload(list(seen.values())),
        },
        text,
    )


def cmd_pipeline_advance(args) -> int:
    ctx = Context()
    session = ctx.require_session()
    try:
        pipeline, verdicts = ctx.pipelines.advance(
            session.id,
            args.state,
            force=args.force,
            reason=args.reason,
            session=session.to_dict(),
            cwd=ctx.cwd,
        )
    except GateFailure as exc:
        report_verdicts(exc.verdicts, args)
        if getattr(args, "json", False):
            print(json.dumps({"advanced": False, "gates": verdicts_payload(exc.verdicts)}, indent=2))
        _record_cli_refusal(ctx, session, exc.verdicts)
        raise
    _lift_cli_refusal(ctx, session)
    report_verdicts(verdicts, args)
    return emit(
        args,
        {"advanced": True, "pipeline": pipeline.to_dict(), "gates": verdicts_payload(verdicts)},
        f"pipeline -> {pipeline.state}  ({summarise(verdicts)})",
    )


def cmd_pipeline_test(args) -> int:
    ctx = Context()
    session = ctx.require_session()
    record = ctx.pipelines.run_tests(
        session.id, phase=args.phase, selectors=args.selector, cwd=ctx.cwd
    )
    status = "passed" if record["passed"] else "FAILED"
    note = f"\n  note: {record['note']}" if record["note"] else ""
    hazards = (
        "\n  hazards: " + ", ".join(record["hazards"]) if record["hazards"] else ""
    )
    return emit(
        args,
        record,
        f"{status}  {describe_run(record)}{note}{hazards}",
    )


# -- items -------------------------------------------------------------


def _item_fields(pairs: list[str]) -> dict[str, str]:
    """``--field k=v`` pairs, each one honoured or refused — never dropped.

    A pair without ``=`` and a key given twice used to vanish (the first) or
    lose to its last spelling (the second) with the command exiting 0, which
    is the silent drop BUG_074 (a) is about one layer down.
    """
    fields: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        key = key.strip()
        if not sep or not key:
            raise UsageError(f"--field {pair!r} is not key=value; nothing was filed")
        if key in fields:
            raise UsageError(f"--field {key} is given twice; nothing was filed")
        fields[key] = value
    return fields


def cmd_item_new(args) -> int:
    ctx = Context()
    fields = _item_fields(args.field or [])
    item = ctx.items.new(
        args.type,
        args.title,
        fields=fields,
        body=args.body or "",
        section=args.section,
        after=args.after,
    )
    return emit(args, item.to_dict(), f"{item.id}  {item.status}  {item.title}")


def cmd_item_list(args) -> int:
    ctx = Context()
    items = ctx.items.list(item_type=args.type, status=args.status, open_only=args.open)
    if not items:
        return emit(args, {"items": []}, "no matching items")
    return emit(args, {"items": [i.to_dict() for i in items]}, ctx.items.open_table()
                if args.open else "\n".join(
                    f"  {i.id}  {i.status:<16} {i.title}" for i in items))


def cmd_item_show(args) -> int:
    ctx = Context()
    item = ctx.items.get(args.id)
    text = [
        f"{item.id}  [{item.type}]  {item.title}",
        f"  status   {item.status}",
    ]
    if item.fields:
        text.append("  fields   " + ", ".join(f"{k}={v}" for k, v in item.fields.items()))
    if item.residue:
        text.append("  owed     " + "; ".join(item.residue))
    for n, row in enumerate(item.history):
        text.append(("  history  " if n == 0 else " " * 11) + _history_line(row))
    if item.body:
        text.extend(["", item.body])
    return emit(args, item.to_dict(), "\n".join(text))


def cmd_item_transition(args) -> int:
    ctx = Context()
    try:
        # The verdicts are reported by the failure path below; a clean
        # transition has nothing to say about them.
        item, _verdicts = ctx.items.transition(
            args.id,
            args.status,
            reason=args.reason,
            force=args.force,
            cwd=ctx.cwd,
            ruling=args.ruling,
            evidence=args.evidence,
        )
    except GateFailure as exc:
        report_verdicts(exc.verdicts, args)
        raise
    text = f"{item.id} -> {item.status}"
    if len(item.history) > 1 and item.history[-2].get("intermediate"):
        text += f"  (through {item.history[-2]['to']}, recorded on the ruling)"
    return emit(args, item.to_dict(), text)


def _history_line(row: dict) -> str:
    """One history row as ``item show`` prints it."""
    line = f"{row.get('from')} -> {row.get('to')}"
    notes = []
    if row.get("reason"):
        notes.append(str(row["reason"]))
    if row.get("forced"):
        notes.append("forced")
    if row.get("ruling"):
        notes.append(f"ruled by {row['ruling']}")
    if row.get("evidence"):
        notes.append(f"evidence {str(row['evidence'])[:12]}")
    if row.get("intermediate"):
        notes.append("recorded on the ruling, not walked")
    return line + (f"  ({'; '.join(notes)})" if notes else "")


def cmd_item_residue(args) -> int:
    ctx = Context()
    if args.add:
        item = ctx.items.add_residue(args.id, args.add)
    elif args.clear:
        item = ctx.items.clear_residue(args.id, args.clear)
    else:
        item = ctx.items.get(args.id)
    text = "\n".join(f"  - {r}" for r in item.residue) or "  (nothing owed)"
    return emit(args, item.to_dict(), f"{item.id} residue:\n{text}")


# -- memory ------------------------------------------------------------


def cmd_memory_check(args) -> int:
    ctx = Context()
    report = ctx.memory.check()
    text = "\n".join(f"  {p}" for p in report.problems()) or "  clean"
    emit(args, {"count": report.count, "problems": report.problems(),
                "clean": report.clean},
         f"{report.count} memories\n{text}")
    return EXIT_OK if report.clean else 1


def cmd_memory_index(args) -> int:
    ctx = Context()
    ctx.memory.regenerate_index()
    return emit(args, {"index": str(ctx.memory.index_path)},
                f"regenerated {ctx.memory.index_path}")


def cmd_memory_write(args) -> int:
    ctx = Context()
    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else (args.body or "")
    path = ctx.memory.write(args.name, args.description, args.type, body)
    if ctx.manifest.get("memory.index_generated", True):
        ctx.memory.regenerate_index()
    return emit(args, {"path": str(path)}, f"wrote {path.name} and regenerated the index")


# -- stats -------------------------------------------------------------


def cmd_stats_refresh(args) -> int:
    ctx = Context()
    result = ctx.stats.refresh(force=args.force)
    return emit(
        args,
        result,
        f"{result['scanned']} host session(s): {result['rebuilt']} rebuilt, "
        f"{result['unchanged']} unchanged",
    )


def cmd_stats_report(args) -> int:
    ctx = Context()
    stats = ctx.stats
    text = stats.render()
    if args.write:
        path = stats.write_report()
        return emit(
            args,
            {"path": str(path), "fingerprint": stats.fingerprint()},
            f"wrote {path}",
        )
    return emit(args, {"report": text, "fingerprint": stats.fingerprint()}, text)


def cmd_stats_show(args) -> int:
    ctx = Context()
    stats = ctx.stats
    record = stats.require(args.session)
    return emit(args, record.to_dict(), stats.render_session(args.session))


def cmd_stats_archive(args) -> int:
    ctx = Context()
    record = ctx.stats.archive(args.session)
    return emit(
        args,
        record.to_dict(),
        f"{record.label} is archived — it now survives the host pruning its "
        f"transcripts, and state collection will not drop it",
    )


def cmd_stats_check(args) -> int:
    ctx = Context()
    report = ctx.stats.check()
    text = "\n".join(f"  {p}" for p in report.problems()) or "  clean"
    emit(
        args,
        {
            "records": report.records,
            "problems": report.problems(),
            "clean": report.clean,
        },
        f"{report.records} record(s)\n{text}",
    )
    return EXIT_OK if report.clean else 1


# -- rules, checks, privacy -------------------------------------------


def cmd_rules_list(args) -> int:
    ctx = Context()
    rows = [
        (spec.id, ctx.manifest.rule_level(spec.id).value, ",".join(spec.events) or "-", spec.summary)
        for spec in all_rules()
    ]
    width = max(len(r[0]) for r in rows)
    text = "\n".join(f"  {i:<{width}}  {lvl:<6} {ev:<14} {s}" for i, lvl, ev, s in rows)
    return emit(args, {"rules": [
        {"id": i, "level": lvl, "events": ev, "summary": s} for i, lvl, ev, s in rows]}, text)


def cmd_check(args) -> int:
    ctx = Context()
    rule_ids = args.rules or [spec.id for spec in all_rules()]
    verdicts = evaluate(rule_ids, ctx.rule_context())
    verbose = getattr(args, "verbose", False)
    text = "\n".join(
        v.render() for v in verdicts if worth_reporting(v, verbose)
    )
    emit(args, {"verdicts": verdicts_payload(verdicts)}, text or "  nothing to report")
    return 1 if any(v.blocks for v in verdicts) else EXIT_OK


def cmd_privacy_scan(args) -> int:
    from ..core.privacy import PrivacyScanner

    ctx = Context()
    scanner = PrivacyScanner.from_manifest(ctx.manifest)
    findings = scanner.scan_paths([Path(p) for p in args.paths])
    text = "\n".join(f"  {f.describe(reveal=args.reveal)}" for f in findings) or "  clean"
    emit(args, {"findings": [
        {"pattern": f.pattern, "path": str(f.path), "line": f.line,
         "column": f.column, "match": f.raw if args.reveal else f.redacted}
        for f in findings]}, text)
    return 1 if findings else EXIT_OK


# -- state maintenance -------------------------------------------------


def cmd_state_show(args) -> int:
    ctx = Context()
    if args.session:
        session = ctx.sessions.require(args.session)
        pipeline = ctx.pipelines.current(session.id)
        payload = {"session": session.to_dict(),
                   "pipeline": pipeline.to_dict() if pipeline else None}
    else:
        payload = {
            "state_root": str(ctx.manifest.state_root),
            "registry": ctx.store.read("registry.json", {"sessions": {}}),
            "sessions": [s.to_dict() for s in ctx.sessions.list()],
        }
    return emit(args, payload, json.dumps(payload, indent=2, sort_keys=True, default=str))


def cmd_state_repair(args) -> int:
    ctx = Context()
    actions = ctx.sessions.repair(dry_run=args.dry_run)
    text = "\n".join(f"  {a}" for a in actions) or "  nothing to repair"
    prefix = "would repair:\n" if args.dry_run else "repaired:\n"
    return emit(args, {"actions": actions, "dry_run": args.dry_run}, prefix + text)


# -- oracle ------------------------------------------------------------
#
# The oracle's commands read and write the user-level index, not a project's
# state, so none of them builds a Context: they work from any directory,
# including one no manifest governs.


def _project_index():
    from ..oracle.home import oracle_home
    from ..oracle.index import ProjectIndex

    return ProjectIndex(oracle_home())


def _project_line(entry) -> str:
    return f"  {entry.name}  {entry.root}  (via {entry.via})\n      state root {entry.key}"


def cmd_oracle_projects_list(args) -> int:
    index = _project_index()
    entries = index.list()
    text = "\n".join(_project_line(e) for e in entries) or (
        f"  no projects registered in {index.home}"
    )
    return emit(
        args,
        {"home": str(index.home), "projects": [e.to_dict() for e in entries]},
        text,
    )


def cmd_oracle_projects_add(args) -> int:
    manifest = Manifest.load(Path(args.path) if args.path else Path.cwd())
    entry, added = _project_index().register(manifest, via="manual")
    verb = "registered" if added else "already registered:"
    return emit(
        args,
        {"added": added, "project": entry.to_dict()},
        f"{verb} {entry.name}\n{_project_line(entry)}",
    )


def cmd_oracle_projects_remove(args) -> int:
    entry = _project_index().remove(args.key)
    return emit(
        args,
        {"removed": entry.to_dict()},
        f"removed {entry.name} from the oracle index; nothing in the project "
        f"was touched\n{_project_line(entry)}",
    )


def cmd_oracle_projects_prune(args) -> int:
    pruned = _project_index().prune(dry_run=args.dry_run)
    verb = "would prune" if args.dry_run else "pruned"
    text = "\n".join(
        f"  {verb} {e.name} — no manifest at {e.root or '(no root recorded)'}"
        for e in pruned
    ) or "  nothing to prune"
    return emit(
        args,
        {"pruned": [e.to_dict() for e in pruned], "dry_run": args.dry_run},
        text,
    )


def _oracle_cache():
    """The scan cache, with Claude Code's answers about hosts injected.

    A seam, like ``_handoff_closer``: the suite replaces it, so no test reads
    the real transcripts or asks ``ps`` about a real ``claude``. The oracle
    defines the probe's shape and imports nothing from ``hosts/``; this is
    where the two meet (ADR-007 D11).
    """
    from ..hosts.claude_code_launch import ClaudeCodeProbe
    from ..oracle.scan import ScanCache

    return ScanCache(probe=ClaudeCodeProbe())


def _oracle_gates():
    """The gate cache ``oracle list`` and ``oracle watch`` add detail from.

    A seam beside ``_oracle_cache``, so a test can count or stall the
    evaluations. The real one evaluates over an inert store and writes nothing.
    """
    from ..oracle.gates import GateCache

    return GateCache()


def _oracle_row_lines(row: dict) -> list[str]:
    from ..oracle.dashboard import status_flag
    from ..oracle.recommend import row_label

    signals = row["signals"]
    # The flag column: an alert, a marker only, or nothing. One spelling with
    # the dashboard's status column.
    flag = status_flag(row).ljust(5)
    said = "; ".join(f"{s['kind']}: {s['detail']}" for s in signals) or "-"
    lines = [f"  {flag}  {row_label(row):<40} {said}"]
    advice = row["recommendation"]
    if advice:
        command = " ".join(advice["argv"])
        where = f"  (in {advice['cwd']})" if advice["cwd"] else ""
        lines.append(f"         next  {advice['label']}" + (f": {command}{where}" if command else ""))
        if advice.get("detail"):
            lines.append(f"               {advice['detail']}")
    return lines


def cmd_oracle_list(args) -> int:
    """Every live session in every registered project, with its next step.

    A one-shot scan: read-only over each project's state root, one error row
    per project that cannot be read, and no Context, so it runs from anywhere.
    The idle sessions' gates are evaluated once, for the recommendation's
    detail, and the wait for them is bounded by the gate cache's timeout.
    """
    from ..oracle.recommend import assess
    from ..oracle.scan import scan_all

    index = _project_index()
    snapshots = scan_all(index, cache=_oracle_cache(), select=args.project)
    now = time.time()
    gates = _oracle_gates()
    document = assess(snapshots, now, gates=gates.refresh(snapshots, now, wait=gates.timeout))
    document["home"] = str(index.home)

    lines: list[str] = []
    for project in document["projects"]:
        entry = project["project"]
        lines.append(f"{entry['name']}  {entry['root']}  ({project['elapsed']:.2f}s)")
        rows = project["rows"]
        if not rows:
            lines.append("  nothing live")
        for row in rows:
            lines.extend(_oracle_row_lines(row))
    text = "\n".join(lines) or f"  no projects registered in {index.home}"
    return emit(args, document, text)


def _oracle_scan_document(index, cache, gates) -> dict:
    """One in-process read of the machine: the ``oracle list --json`` document.

    The gates are asked without waiting: a dashboard refreshes, so what
    finishes after this read is the next one's detail.
    """
    from ..oracle.recommend import assess
    from ..oracle.scan import scan_all

    snapshots = scan_all(index, cache=cache)
    now = time.time()
    document = assess(snapshots, now, gates=gates.refresh(snapshots, now, wait=0.0))
    document["home"] = str(index.home)
    return document


def _real_oracle_runner():
    from ..oracle.actions import run_subprocess

    return run_subprocess


def _oracle_runner():
    """What a confirmed ``run`` step of the dashboard runs through.

    A seam, like ``_oracle_notifier``: the suite replaces it with a recorder,
    so no test runs a recommended command.
    """
    return _real_oracle_runner()


def _oracle_launch_plan(step, stage: bool):
    """The ``session handoff`` launch plan for a dashboard step that resumes."""
    from ..hosts import claude_code_launch as launch
    from ..oracle.actions import terminal_request

    name, note = terminal_request(step)
    claude = launch.resolve_claude() or launch.CLAUDE_BINARY
    return launch.plan_launch(claude, name, note, step.cwd, stage=stage)


def _oracle_terminal(step) -> str:
    """Open a new host session for a dashboard ``terminal`` step.

    The same launch as ``session handoff``: a Terminal window through
    ``_handoff_launcher``, counted only once a successor is seen alive
    (BUG_075). Unlike a handoff it ends no host and records no note; the
    first prompt asks the new session to run the step's command.
    """
    from ..hosts import claude_code_launch as launch

    if launch.host_platform() != "darwin":
        raise InvariantError(
            "opening a Terminal window needs macOS. Run it by hand:\n"
            f"    {_oracle_launch_plan(step, stage=False).by_hand}"
        )
    if launch.resolve_claude() is None:
        raise InvariantError(f"no claude on PATH to start; {launch.CLAUDE_BINARY} was not found")
    plan = _oracle_launch_plan(step, stage=True)
    pid = _launch_successor(launch, plan)
    return f"opened a Terminal window: claude (pid {pid}) is running {plan.name} in {plan.cwd}"


def _oracle_actions():
    """The dashboard's actions, with the host facts they need injected.

    The oracle names the shapes; the host markers to strip and the terminal
    launcher are Claude Code's, joined here (ADR-007 D18).
    """
    from ..hosts.claude_code_launch import without_host_markers
    from ..oracle.actions import Actions

    return Actions(
        runner=_oracle_runner(),
        environ=lambda: os.environ,
        strip=without_host_markers,
        terminal=_oracle_terminal,
        preview=lambda step: _oracle_launch_plan(step, stage=False).by_hand,
    )


def cmd_oracle_tui(args) -> int:
    """The live dashboard. Needs the ``invariant[oracle]`` extra (Textual).

    Reads the watcher's snapshot while it is fresh, else scans in-process
    behind a banner. Every action is confirmed first.
    """
    try:
        from ..oracle import tui
    except ImportError as exc:
        # Textual, or the rich it brings with it; any other failure is a bug
        # and is not disguised as a missing extra.
        if (exc.name or "").split(".")[0] not in ("textual", "rich"):
            raise
        raise UsageError(
            "the oracle dashboard needs Textual, which ships as the [oracle] extra:\n"
            "    pip install 'invariant[oracle]'\n"
            "  Installed as a uv tool, add it to that environment instead:\n"
            "    uv tool install --with textual <how invariant was installed>"
        ) from exc
    from ..oracle.dashboard import load

    index = _project_index()
    cache = _oracle_cache()
    gates = _oracle_gates()
    app = tui.OracleApp(
        lambda: load(index.home, time.time(), lambda: _oracle_scan_document(index, cache, gates)),
        _oracle_actions(),
    )
    app.run()
    return EXIT_OK


def _real_oracle_notifier():
    from ..hosts.claude_code_launch import applescript_string
    from ..oracle.notify import OsascriptNotifier

    return OsascriptNotifier(quote=applescript_string)


def _oracle_notifier():
    """The notifier ``oracle watch`` and ``notify-test`` post through.

    A seam, like ``_oracle_cache``: the suite replaces it with a recorder, so
    no test posts a real notification. The oracle defines the notifier and
    imports nothing from ``hosts/``; the AppleScript quoting it needs is the
    host launcher's, handed in here (ADR-007 D13).
    """
    return _real_oracle_notifier()


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _oracle_tick_lines(result) -> list[str]:
    lines = [
        f"  ALERT  {a['project']}  {a['subject']}  {a['kind']}: {a['detail']}"
        + ("" if a["notified"] else "  (not notified)")
        for a in result.alerts
    ]
    lines.append(
        f"tick: {_plural(result.rows, 'row')}, {_plural(len(result.alerts), 'new alert')}, "
        f"{result.elapsed:.2f}s; snapshot {result.snapshot_path}"
    )
    return lines


def cmd_oracle_watch(args) -> int:
    """Tick: scan, write the snapshot, alert once per rising edge.

    ``--once`` runs one tick and lets a failure surface as this command's
    error. Without it the loop runs until interrupted, a failed tick is logged
    to stderr and the next one runs, and a tick prints only when it has news
    (with ``--json``, one line per tick).
    """
    from ..oracle.signals import Thresholds
    from ..oracle.watcher import Watcher

    if not args.interval > 0:
        raise UsageError(f"--interval must be positive, not {args.interval:g}")
    if not args.stuck_minutes > 0:
        raise UsageError(f"--stuck-minutes must be positive, not {args.stuck_minutes:g}")
    gates = _oracle_gates()
    watcher = Watcher(
        _project_index(),
        None if args.no_notify else _oracle_notifier(),
        interval=args.interval,
        cache=_oracle_cache(),
        thresholds=Thresholds(stuck_minutes=args.stuck_minutes),
        gates=gates,
        # One tick that exits is a one-shot like `oracle list`: it waits for
        # the gates. The loop never does; what finishes late is the next
        # tick's.
        gate_wait=gates.timeout if args.once else 0.0,
    )
    if args.once:
        result = watcher.tick()
        return emit(args, result.to_dict(), "\n".join(_oracle_tick_lines(result)))

    def report(result) -> None:
        if getattr(args, "json", False):
            print(json.dumps(result.to_dict(), sort_keys=True, default=str), flush=True)
        elif result.alerts:
            print("\n".join(_oracle_tick_lines(result)), flush=True)

    try:
        watcher.run(on_tick=report)
    except KeyboardInterrupt:
        pass
    return EXIT_OK


def _real_oracle_installer():
    from ..oracle.launchd import LaunchdInstaller, agents_dir

    return LaunchdInstaller(agents_dir(), os.getuid())


def _oracle_installer():
    """The LaunchAgent installer ``oracle install``, ``uninstall``, ``status`` and
    ``doctor`` reach launchd through.

    A seam, like ``_oracle_notifier``: the suite replaces it with an installer
    over a temporary directory and a recording launchctl, so no test loads or
    writes a real agent.
    """
    return _real_oracle_installer()


def _argv_line(argv: list[str]) -> str:
    return " ".join(argv)


def cmd_oracle_install(args) -> int:
    """Install the LaunchAgent that keeps ``oracle watch`` running.

    ``--dry-run`` prints the plist and the launchctl calls and changes nothing.
    """
    from ..oracle.home import HOME_ENV, oracle_home
    from ..oracle.launchd import plist_for, search_path

    if not args.interval > 0:
        raise UsageError(f"--interval must be positive, not {args.interval:g}")
    plist = plist_for(
        os.path.abspath(sys.executable),
        oracle_home(),
        args.interval,
        path=search_path(),
        pin_home=bool(os.environ.get(HOME_ENV)),
    )
    result = _oracle_installer().install(plist, dry_run=args.dry_run)
    steps = "\n".join(f"  {_argv_line(step)}" for step in result.steps)
    if args.dry_run:
        text = (
            f"would write {result.plist_path}:\n{result.xml.decode()}\n"
            f"would run:\n{steps}"
        )
    else:
        verb = "replaced" if result.was_loaded else "installed"
        text = (
            f"{verb} the oracle agent: {result.plist_path}\n{steps}\n"
            f"  watcher log: {plist['StandardOutPath']}"
        )
    return emit(args, result.to_dict(), text)


def cmd_oracle_uninstall(args) -> int:
    """Boot the LaunchAgent out and remove its plist."""
    result = _oracle_installer().uninstall(dry_run=args.dry_run)
    steps = "\n".join(f"  {_argv_line(step)}" for step in result.steps)
    present = result.plist_path.exists()
    if not result.steps and not present and not result.removed:
        text = f"the oracle agent is not installed; nothing to do ({result.plist_path})"
    elif args.dry_run:
        text = "would run:\n" + (steps or "  nothing (not loaded)") + (
            f"\nwould remove {result.plist_path}" if present else ""
        )
    else:
        text = "\n".join(
            filter(None, [steps, f"  removed {result.plist_path}" if result.removed else ""])
        )
    return emit(args, result.to_dict(), text)


def cmd_oracle_status(args) -> int:
    """Whether the agent is installed and loaded, and how old the snapshot is."""
    from ..oracle.home import oracle_home
    from ..oracle.watcher import STALE_INTERVALS, snapshot_status

    agent = _oracle_installer().status()
    snapshot = snapshot_status(oracle_home(), time.time())
    if not agent.installed:
        agent_line = f"not installed ({agent.plist_path})"
    else:
        agent_line = (
            f"installed, {'loaded' if agent.loaded else 'NOT loaded'} ({agent.plist_path})"
        )
    lines = [f"  agent        {agent_line}"]
    if agent.error:
        lines.append(f"  interpreter  {agent.error}")
    elif agent.interpreter is not None:
        state = "ok" if agent.interpreter_exists else "MISSING"
        lines.append(f"  interpreter  {state}  {agent.interpreter}")
    if not snapshot["exists"]:
        lines.append(f"  snapshot     no snapshot yet ({snapshot['path']})")
    else:
        freshness = (
            f"STALE: older than {STALE_INTERVALS} intervals" if snapshot["stale"] else "fresh"
        )
        lines.append(
            f"  snapshot     {freshness}; written {snapshot['age']:.0f}s ago, "
            f"interval {snapshot['interval']:g}s ({snapshot['path']})"
        )
    return emit(args, {"agent": agent.to_dict(), "snapshot": snapshot}, "\n".join(lines))


def cmd_oracle_notify_test(args) -> int:
    """Post one notification through the same seam the watcher uses."""
    from ..oracle.notify import Notification, NotifyError

    try:
        _oracle_notifier().notify(
            Notification(
                title="invariant oracle",
                message="notify-test: oracle alerts will reach you here",
            )
        )
    except NotifyError as exc:
        raise NotifyError(
            f"the test notification was not delivered: {exc}\n"
            f"  On macOS, check that notifications are allowed for the app "
            f"osascript posts as (System Settings > Notifications)."
        ) from exc
    return emit(args, {"sent": True}, "sent one notification")


def _real_oracle_web_addresses():
    from ..oracle import web

    return lambda: web.discover_addresses(web.run_program)


def _oracle_web_addresses():
    """How ``oracle web`` finds this Mac's Tailscale addresses.

    A seam, like ``_oracle_installer``: the suite replaces it with one that
    finds no tailnet until a test names one, so no test asks the real
    ``tailscale`` or binds a real tailnet address (ADR-008).
    """
    return _real_oracle_web_addresses()


def _real_oracle_web_server():
    from ..oracle.web import WebServer

    return WebServer


def _oracle_web_server():
    """What ``oracle web`` listens through: ``(app, addresses, port)`` to a server.

    A seam: the suite replaces it with a recorder that binds nothing.
    """
    return _real_oracle_web_server()


def _web_port(port: int) -> int:
    if not 1 <= port <= 65535:
        raise UsageError(f"--port must be between 1 and 65535, not {port}")
    return port


def cmd_oracle_web(args) -> int:
    """Serve the dashboard on this Mac's Tailscale address, in the foreground.

    Read-only: the page and its JSON, to paired devices only. Refuses to start
    with no Tailscale address unless ``--allow-loopback``.
    """
    from ..oracle.dashboard import load
    from ..oracle.devices import DeviceStore
    from ..oracle.web import LOOPBACK, WebApp, choose_bind

    port = _web_port(args.port)
    addresses = choose_bind(_oracle_web_addresses()(), allow_loopback=args.allow_loopback)
    index = _project_index()
    cache = _oracle_cache()
    gates = _oracle_gates()
    devices = DeviceStore(index.home)
    app = WebApp(
        devices,
        lambda: load(index.home, time.time(), lambda: _oracle_scan_document(index, cache, gates)),
        addresses,
    )
    server = _oracle_web_server()(app, addresses, port)
    urls = server.start()
    paired = len(devices.devices())
    lines = [f"serving the oracle on {url}" for url in urls]
    if addresses == [LOOPBACK]:
        lines.append("  loopback only: no Tailscale address was found; this Mac alone can open it")
    lines.append(
        f"  {_plural(paired, 'paired device')}; pair one with 'invariant oracle web pair'. "
        f"Ctrl-C stops it."
    )
    print("\n".join(lines), flush=True)
    try:
        server.serve()
    finally:
        server.close()
    return EXIT_OK


def _minutes(seconds: float) -> str:
    if seconds >= 60 and seconds % 60 == 0:
        return _plural(int(seconds // 60), "minute")
    return f"{seconds:g} seconds"


def cmd_oracle_web_pair(args) -> int:
    """Issue a one-time pairing code, and the link a phone opens to use it."""
    from ..oracle.devices import DeviceStore
    from ..oracle.home import oracle_home
    from ..oracle.web import LOOPBACK, tailnet_addresses, url_for

    port = _web_port(args.port)
    found = tailnet_addresses(_oracle_web_addresses()())
    if not found and args.allow_loopback:
        found = [LOOPBACK]
    pairing = DeviceStore(oracle_home()).issue(args.name, ttl=args.ttl)
    links = [url_for(address, port, f"/pair?code={pairing.code}") for address in found]
    lines = [
        f"pairing code for {pairing.name}: {pairing.code[:5]}-{pairing.code[5:]}",
        f"  it works once, for {_minutes(args.ttl)}",
    ]
    if links:
        lines.append("  open on the device, while 'invariant oracle web' runs:")
        lines.extend(f"    {link}" for link in links)
    else:
        lines.append(
            "  no Tailscale address on this Mac yet; start Tailscale, run "
            "'invariant oracle web', and open its address with /pair on the device"
        )
    return emit(
        args,
        {"code": pairing.code, "name": pairing.name, "expires": pairing.expires,
         "links": links},
        "\n".join(lines),
    )


def cmd_oracle_web_devices(args) -> int:
    """The paired devices, and how many codes are still pending."""
    from ..oracle.devices import DeviceStore
    from ..oracle.home import oracle_home

    store = DeviceStore(oracle_home())
    devices = store.devices()
    pending = store.pending()
    text = "\n".join(f"  {d.id}  {d.name}  paired {d.paired}" for d in devices) or (
        "  no paired devices"
    )
    if pending:
        text += f"\n  {_plural(pending, 'pairing code')} not yet used"
    return emit(args, {"devices": [d.to_dict() for d in devices], "pending": pending}, text)


def cmd_oracle_web_revoke(args) -> int:
    """Forget one paired device; the others keep working."""
    from ..oracle.devices import DeviceStore
    from ..oracle.home import oracle_home

    device = DeviceStore(oracle_home()).revoke(args.id)
    return emit(
        args,
        {"revoked": device.to_dict()},
        f"revoked {device.id} ({device.name}); its next request gets nothing",
    )


# -- host bridge -------------------------------------------------------


def cmd_hook(args) -> int:
    from ..hosts.claude_code import DEGRADED_KEY, ClaudeCodeHost, is_probe

    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    body: object = None
    try:
        body = json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError:
        pass
    host_session_id = body.get("session_id") if isinstance(body, dict) else None
    # The manifest is resolved from the project the HOST is working in, not
    # from wherever this process happens to be standing (AUD-04). The host
    # guarantees CLAUDE_PROJECT_DIR to its hooks; the working directory it
    # does not, and a hook firing with cwd outside the project used to find
    # no manifest and return an empty verdict — every gate silently off, with
    # evidence recording into whichever repository the cwd resolved to.
    manifest_start: Path | None = None
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir and Manifest.find_root(Path(project_dir)):
        manifest_start = Path(project_dir)
    try:
        ctx = Context(
            required=True,
            host_session_id=host_session_id,
            manifest_start=manifest_start,
            probe=is_probe(body),
        )
    except InvariantError as exc:
        # No manifest found anywhere. A shim only exists where `init` put it,
        # so this is a misconfiguration, not an ungoverned repository — but a
        # hook must still exit zero with valid JSON, because a pre-tool-use
        # hook that fails is a *deny* and a missing manifest must never wedge
        # the host. What it may not do is stay silent: an empty verdict here
        # read as "the gate passed", and enforcement was off with no marker.
        print(json.dumps(_hook_degraded(args.event, exc)))
        return EXIT_OK
    host = ClaudeCodeHost(ctx.manifest.root)
    # The probe writes nothing anywhere (BUG_059): not the index, and not the
    # attention sidecar, whose pid fallback would also read the process table.
    probe = isinstance(ctx.store, InertStore)
    try:
        response = host.handle(args.event, raw, ctx)
    except Exception as exc:  # noqa: BLE001 - see below
        # The same reasoning as the guard above, extended to the part that does
        # the work. A pre-tool-use hook that exits non-zero is a *deny*, so any
        # unhandled failure in here does not merely lose enforcement — it
        # refuses every tool call in the project, including the ones that would
        # diagnose and repair the cause. That is a deadlock, and it is strictly
        # worse than the unenforced gate it was trying to protect.
        #
        # A block is a verdict, and verdicts come back as a return value. An
        # exception is this library breaking, and this library breaking must
        # never be spelled "the agent may not act".
        degraded = _hook_degraded(args.event, exc)
        if not probe:
            error = degraded[DEGRADED_KEY]
            _record_attention(
                ctx,
                host_session_id,
                lambda store, hid, pid: store.degraded(
                    hid, args.event, error, pid_source=pid
                ),
            )
        print(json.dumps(degraded))
        return EXIT_OK
    if not probe:
        if args.event == "session-start":
            _register_on_session_start(ctx)
        signal = host.attention
        if signal is not None:
            _record_attention(
                ctx,
                host_session_id,
                lambda store, hid, pid: _apply_signal(store, hid, pid, signal),
            )
    print(json.dumps(response))
    return EXIT_OK


# -- the attention sidecar ---------------------------------------------
#
# The host says what an event means for its person's attention; the oracle
# owns the record; this is where the two meet, so the host adapter never
# imports the oracle (ADR-007 D6). Every write goes through
# `_record_attention`, and nothing it does can change a response.


def _attention_process_lookup():
    """The ``(ppid, comm)`` reader the pid fallback walks with.

    A seam, like ``_handoff_closer``: the suite replaces it everywhere, so no
    hook a test runs reads the real process table.
    """
    from ..hosts.claude_code_launch import parent_process

    return parent_process


def _attention_pid_source():
    """The host pid to record, given the one already recorded (ADR-007 D7).

    ``CLAUDE_PID`` when the hook inherited it — it costs nothing and is right
    even when a resumed host kept its session id under a new process. Without
    it, a pid already recorded stands, and only a host with none is found by
    walking the parent chain to a ``claude`` process: one ``ps`` per hop, once
    per host.
    """
    from ..hosts.claude_code_launch import find_host_pid, host_pid

    def source(current: int | None) -> int | None:
        return (
            host_pid(os.environ)
            or current
            or find_host_pid(os.getppid(), _attention_process_lookup())
        )

    return source


def _apply_signal(store, host_id: str, pid_source, signal) -> None:
    """Apply the sidecar write an :class:`AttentionSignal` calls for."""
    from ..hosts import claude_code as host

    if signal.op == host.ATTENTION_WAITING:
        store.wait(host_id, signal.kind, signal.message, pid_source=pid_source)
    elif signal.op == host.ATTENTION_ANSWERED:
        store.answered(host_id, pid_source=pid_source)
    elif signal.op == host.ATTENTION_PROMPT:
        store.prompt(host_id, pid_source=pid_source)
    elif signal.op == host.ATTENTION_TURN_ENDED:
        store.turn_ended(host_id, signal.rules, pid_source=pid_source)
    elif signal.op == host.ATTENTION_HOST_ENDED:
        store.host_ended(host_id)


def _record_attention(ctx: Context, host_session_id, write) -> None:
    """Apply one sidecar write for ``host_session_id``, swallowing any failure.

    The one guarded path every attention write takes (ADR-007 D10). The hook's
    response is computed before this runs and printed after it, and nothing
    here reads or returns it — so a sidecar that cannot be written costs the
    oracle one event and never costs the host a verdict, an exit status or a
    degraded marker. A payload with no host id has no record to write.
    """
    if not host_session_id:
        return
    try:
        from ..oracle.attention import AttentionStore

        write(AttentionStore(ctx.store), host_session_id, _attention_pid_source())
    except Exception:  # noqa: BLE001, S110 - see the docstring
        pass


def _bound_host(session) -> str | None:
    """The live host session bound to ``session``, if it has one."""
    if session is None or getattr(session, "host_ended", False):
        return None
    return getattr(session, "host_session_id", None) or None


def _record_cli_refusal(ctx: Context, session, verdicts) -> None:
    """Record a gate refusal on the sidecar of the host the session is bound to.

    "Gate blocked" in the oracle means a refusal somebody actually met, never
    a fresh evaluation by the watcher (ADR-007 D8). No pid is recorded: the
    command may have been run by somebody other than the bound host.
    """
    _record_attention(
        ctx,
        _bound_host(session),
        lambda store, hid, pid: store.refused(
            hid, [v.rule_id for v in verdicts if v.blocks]
        ),
    )


def _lift_cli_refusal(ctx: Context, session) -> None:
    """A gated CLI transition went through, so its earlier refusal no longer stands."""
    _record_attention(
        ctx, _bound_host(session), lambda store, hid, pid: store.refusal_lifted(hid)
    )


def _register_on_session_start(ctx: Context) -> None:
    """Record the project in the oracle's index the first time a host starts here.

    Read first: a known project is answered by one small read and takes no
    lock, so the common case writes nothing. Never raised and never reported:
    the response is already computed, and this is bookkeeping for a different
    program — a user-level index that cannot be read or written must not turn
    a session start into a degraded one. Skipped for ``doctor``'s probe, which
    writes nothing anywhere (BUG_059); the caller checks the inert store.
    """
    from ..oracle.home import oracle_home
    from ..oracle.index import ProjectIndex

    try:
        ProjectIndex(oracle_home()).register(ctx.manifest, via="session-start")
    except Exception:  # noqa: BLE001, S110 - see the docstring
        pass


def _hook_degraded(event: str, exc: Exception) -> dict:
    """Report a broken hook as context, never as a decision."""
    from ..hosts.claude_code import DEGRADED_KEY, EVENTS, _context_response

    text = (
        f"invariant: the {event} hook could not evaluate and was skipped — "
        f"enforcement is OFF for this call.\n"
        f"  {type(exc).__name__}: {exc}\n"
        f"  Diagnose with 'invariant doctor'. Gates are not being checked "
        f"until this is fixed."
    )
    response = _context_response(EVENTS.get(event, "PreToolUse"), text)
    # A machine-readable marker, because this path exits zero with valid JSON
    # by design — it must, or a broken library would deny every tool call in
    # the project. That makes "the hook ran" indistinguishable from "the hook
    # worked" to anything checking from outside, and an entire session went
    # unenforced behind exactly that gap.
    response[DEGRADED_KEY] = f"{type(exc).__name__}: {exc}"
    return response


def cmd_context(args) -> int:
    # --id is not a convenience here. Every ambiguity message tells the caller
    # to pass it, and 'context' is the first command they reach for after
    # reading one; without it the advice is a dead end.
    ctx = Context(session_id=getattr(args, "id", None))
    from ..hosts.claude_code import build_context

    text = build_context(ctx)
    return emit(args, {"context": text}, text)


# -- parser ------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    # A parent carrying the global flags, attached to every leaf command as
    # well as the root, so that `--json` works in the position a person
    # naturally types it rather than only before the subcommand.
    # SUPPRESS rather than a False default: the same flag is attached at every
    # level, and a plain default would have the subparser overwrite a value the
    # root parser already set — so `invariant --json init` would silently print
    # text. With SUPPRESS the attribute is written only where the flag actually
    # appeared, and the root's set_defaults survives.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="machine-readable output",
    )
    common.add_argument(
        "--verbose", "-v", action="store_true", default=argparse.SUPPRESS
    )

    parser = argparse.ArgumentParser(
        prog="invariant",
        description="Deterministic, scripted state for agent workflows.",
        parents=[common],
    )
    # Deliberately NO set_defaults(json=False) here. `parents=` copies action
    # objects by reference, and set_defaults reaches into every action with a
    # matching dest — so setting a default on the root would overwrite SUPPRESS
    # on every subparser's copy of the same action, reinstating the bug above.
    # Readers use getattr(args, "json", False) instead.
    class _Sub(argparse._SubParsersAction):
        """Attaches the common flags to every subparser, at every depth."""

        def add_parser(self, name, **kwargs):  # type: ignore[override]
            parents = list(kwargs.pop("parents", []))
            if common not in parents:
                parents.append(common)
            created = super().add_parser(name, parents=parents, **kwargs)
            # Register on the child too, so nested groups (`config show`) also
            # inherit rather than silently losing the flags one level down.
            created.register("action", "parsers", _Sub)
            return created

    parser.register("action", "parsers", _Sub)
    sub = parser.add_subparsers(dest="command", required=True)

    # init / doctor / config
    p = sub.add_parser("init", help="write a manifest and install host shims")
    p.add_argument("--root")
    p.add_argument("--name")
    p.add_argument("--ecosystem")
    p.add_argument("--profile", default="standard")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_init)

    sub.add_parser("doctor", help="check the installation").set_defaults(func=cmd_doctor)

    p = sub.add_parser(
        "uninstall", help="remove the shims and settings entries init installed"
    )
    p.add_argument("--remove-assets", action="store_true",
                   help="also remove library agents, commands and skills")
    p.add_argument("--remove-manifest", action="store_true",
                   help="also remove .claude/invariant.yaml")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_uninstall)

    # agents — repair without touching the manifest, which is why this is not
    # a flag on `init`.
    kinds = [k.name for k in __import__(
        "invariant.hosts.assets", fromlist=["KINDS"]
    ).KINDS]
    aset = sub.add_parser(
        "assets", help="host assets: agents, commands, skills"
    ).add_subparsers(dest="sub", required=True)
    q = aset.add_parser("list", help="every asset, whether it loads, and who owns it")
    q.add_argument("--kind", choices=kinds)
    q.set_defaults(func=cmd_assets_list)
    q = aset.add_parser("install", help="reinstall library-shipped assets")
    q.add_argument(
        "--overwrite-local",
        action="store_true",
        help="replace assets you have edited; without it they are skipped and named",
    )
    q.add_argument("--kind", choices=kinds)
    q.add_argument("--dry-run", action="store_true")
    q.set_defaults(func=cmd_assets_install)

    agents = sub.add_parser("agents").add_subparsers(dest="sub", required=True)
    agents.add_parser("list", help="installed agents and whether the host can load them"
                      ).set_defaults(func=cmd_agents_list)
    q = agents.add_parser("install", help="reinstall the library agents")
    q.add_argument(
        "--overwrite-local",
        action="store_true",
        help="replace agents you have edited; without it they are skipped and named",
    )
    q.add_argument("--dry-run", action="store_true")
    q.set_defaults(func=cmd_agents_install)

    p = sub.add_parser("config").add_subparsers(dest="sub", required=True)
    q = p.add_parser("show")
    q.add_argument("--provenance", action="store_true")
    q.set_defaults(func=cmd_config_show)

    # session
    session = sub.add_parser("session").add_subparsers(dest="sub", required=True)

    q = session.add_parser("start")
    q.add_argument("name")
    q.add_argument("--id")
    q.add_argument(
        "--base",
        help="the branch to cut the worktree from and merge back into; "
        "defaults to the branch the main checkout has checked out",
    )
    q.add_argument(
        "--force",
        action="store_true",
        help="start a second session for a task that already has a parked one",
    )
    q.add_argument(
        "--host-session-id",
        help="the agent host's session identifier; attribution survives a cwd reset with it",
    )
    q.set_defaults(func=cmd_session_start)

    q = session.add_parser("isolate")
    q.add_argument("--id")
    q.add_argument("--execute", action="store_true")
    q.set_defaults(func=cmd_session_isolate)

    # --id everywhere it can mean something. Every ambiguity message tells the
    # caller to pass it, and a subcommand that then rejects it makes the advice
    # a dead end.
    q = session.add_parser("status")
    q.add_argument("--id")
    q.set_defaults(func=cmd_session_status)
    session.add_parser("list").set_defaults(func=cmd_session_list)

    q = session.add_parser("transition")
    q.add_argument("state")
    q.add_argument("--id")
    q.add_argument("--reason")
    q.add_argument("--force", action="store_true")
    q.set_defaults(func=cmd_session_transition)

    q = session.add_parser("files")
    # `remove` exists because `add` can record a path that cannot be opened,
    # and a declared set with no way back blocks the review boundary on an
    # entry nobody can delete.
    q.add_argument("action", choices=["add", "remove"])
    q.add_argument("paths", nargs="+")
    q.add_argument("--id")
    q.set_defaults(func=cmd_session_files)

    q = session.add_parser("adopt", help="bind a host session id to a session")
    q.add_argument("host_session_id")
    q.add_argument("--id")
    q.add_argument("--force", action="store_true", help="rebind deliberately")
    q.set_defaults(func=cmd_session_adopt)

    q = session.add_parser(
        "resume", help="pick up a session a previous host session put down"
    )
    # Optional, because --id must be able to name the session on its own: every
    # ambiguity message in this library tells the caller to pass --id, and a
    # subcommand that required a name alongside it would make that advice a
    # dead end.
    q.add_argument("name", nargs="?", help="a name, a bare task slug, or an id")
    q.add_argument("--id")
    q.add_argument(
        "--force",
        action="store_true",
        help="take over a live binding, or resume a session that already landed",
    )
    q.set_defaults(func=cmd_session_resume)

    q = session.add_parser(
        "handoff",
        help="hand the work to a fresh Claude Code session: record a note, "
        "open a new Terminal window running it",
        description=(
            "Privacy-scan and record --note on this session (any state, torn "
            "down included), then open a new macOS Terminal window in the main "
            "checkout running: claude --name <slug> --remote-control <slug> "
            "\"<note>\". The slug comes from the note's 'next:' item unless "
            "--name gives one. Once the launch succeeds, this Claude Code session "
            "(CLAUDE_PID) is sent SIGTERM after a short delay unless --keep-open. "
            "--dismiss instead retires the latest fresh note without acting on it."
        ),
    )
    q.add_argument(
        "--note",
        help="one terse keyword line; its 'next:' item names the new session",
    )
    q.add_argument(
        "--dismiss",
        action="store_true",
        help="retire the latest fresh note here without acting on it; it is "
        "recorded as dismissed",
    )
    q.add_argument("--name", help="the new session's name, instead of deriving it")
    q.add_argument(
        "--dry-run",
        action="store_true",
        help="print the exact command and AppleScript; record and launch nothing",
    )
    q.add_argument(
        "--keep-open",
        action="store_true",
        help="launch the successor but leave this Claude Code session running; "
        "by default it is sent SIGTERM shortly after a successful launch",
    )
    q.add_argument("--id", help="the session handing off, when the directory cannot say")
    q.set_defaults(func=cmd_session_handoff)

    # Nested subparsers rather than `files`' flat `action` + positional: `add`
    # takes one title, three actions take an integer, and `show` takes nothing.
    # A flat shape could only accept strings, so the integer check would move
    # out of the parser and into a refusal about the plan.
    plan = session.add_parser(
        "plan", help="the deliverable set this session owes"
    ).add_subparsers(dest="plan_action", required=True)

    q = plan.add_parser("add", help="record a deliverable this session owes")
    # ONE positional, deliberately. `plan add wire the toolbar` unquoted is then
    # a usage error rather than a deliverable called "wire".
    q.add_argument("title")
    q.add_argument("--id")
    q.set_defaults(func=cmd_session_plan)

    for action, help_text in (
        ("done", "mark a deliverable finished"),
        ("reopen", "undo a done — the way back from ticking the wrong number"),
        ("remove", "drop a deliverable; its number is retired, never reissued"),
    ):
        q = plan.add_parser(action, help=help_text)
        q.add_argument("n", type=int)
        q.add_argument("--id")
        q.set_defaults(func=cmd_session_plan)

    q = plan.add_parser("show", help="the plan, and what is next on it")
    q.add_argument("--id")
    q.set_defaults(func=cmd_session_plan)

    q = session.add_parser(
        "sync", help="rebase this session's branch onto its base"
    )
    q.add_argument("--id")
    q.set_defaults(func=cmd_session_sync)

    q = session.add_parser(
        "reap", help="abandon sessions whose worktree and branch are both gone"
    )
    q.add_argument("--dry-run", action="store_true")
    q.set_defaults(func=cmd_session_reap)

    q = session.add_parser("review", help="evaluate the review gates and advance")
    q.add_argument("--id")
    q.add_argument("--force", action="store_true")
    q.add_argument("--reason")
    q.set_defaults(func=cmd_session_review)

    q = session.add_parser("merge")
    q.add_argument("--id")
    q.add_argument(
        "--force",
        action="store_true",
        help="merge despite a refusing gate; the override is recorded",
    )
    q.set_defaults(func=cmd_session_merge)

    q = session.add_parser("push")
    q.add_argument("--id")
    q.add_argument("--remote", default="origin")
    q.add_argument("--force", action="store_true")
    q.set_defaults(func=cmd_session_push)

    q = session.add_parser("teardown")
    q.add_argument("--id")
    q.add_argument("--keep-worktree", action="store_true")
    q.add_argument("--keep-branch", action="store_true")
    q.add_argument(
        "--force",
        action="store_true",
        help="remove a worktree you are standing in, knowing the host has moved",
    )
    q.set_defaults(func=cmd_session_teardown)

    # pipeline
    pipeline = sub.add_parser("pipeline").add_subparsers(dest="sub", required=True)

    q = pipeline.add_parser(
        "ack", help="record a deliberate decision to proceed against an advisory gate"
    )
    q.add_argument("rule")
    q.add_argument("--reason", required=True)
    q.add_argument("--id")
    q.set_defaults(func=cmd_pipeline_ack)

    q = pipeline.add_parser(
        "coverage", help="run the configured coverage command and record it"
    )
    q.add_argument("--id")
    q.set_defaults(func=cmd_pipeline_coverage)

    q = pipeline.add_parser("start")
    q.add_argument("--methodology")
    q.add_argument("--task")
    q.set_defaults(func=cmd_pipeline_start)

    pipeline.add_parser("status").set_defaults(func=cmd_pipeline_status)

    q = pipeline.add_parser("gates")
    q.add_argument("--state")
    q.set_defaults(func=cmd_pipeline_gates)

    q = pipeline.add_parser("advance")
    q.add_argument("state")
    q.add_argument("--force", action="store_true")
    q.add_argument("--reason")
    q.set_defaults(func=cmd_pipeline_advance)

    q = pipeline.add_parser("test", help="run the configured suite and record it")
    q.add_argument("--phase")
    # Repeatable. Declared without `action="append"`, argparse's documented
    # behaviour for a repeated single-value option applied — the last
    # occurrence silently overwrote the earlier ones, and the run reported a
    # healthy green for a scope nobody asked for (BUG_031). `item new --field`
    # a few lines below has carried the idiom all along.
    q.add_argument("--selector", action="append")
    q.set_defaults(func=cmd_pipeline_test)

    # items
    item = sub.add_parser("item").add_subparsers(dest="sub", required=True)

    q = item.add_parser("new")
    q.add_argument("type", choices=["idea", "bug"])
    q.add_argument("--title", required=True, dest="title")
    q.add_argument("--field", action="append")
    q.add_argument("--body")
    q.add_argument("--section", help="file at the end of the section with this title")
    q.add_argument("--after", metavar="ID", help="file directly after this entry")
    q.set_defaults(func=cmd_item_new)

    q = item.add_parser("list")
    q.add_argument("--type", choices=["idea", "bug"])
    q.add_argument("--status")
    q.add_argument("--open", action="store_true")
    q.set_defaults(func=cmd_item_list)

    q = item.add_parser("show")
    q.add_argument("id")
    q.set_defaults(func=cmd_item_show)

    q = item.add_parser("transition")
    q.add_argument("id")
    q.add_argument("status")
    q.add_argument("--reason")
    q.add_argument("--force", action="store_true")
    q.add_argument("--ruling", help="who ruled, and when; needs --evidence")
    q.add_argument("--evidence", help="the commit the ruling rests on; needs --ruling")
    q.set_defaults(func=cmd_item_transition)

    q = item.add_parser("residue")
    q.add_argument("id")
    q.add_argument("--add")
    q.add_argument("--clear")
    q.set_defaults(func=cmd_item_residue)

    # memory
    memory = sub.add_parser("memory").add_subparsers(dest="sub", required=True)
    memory.add_parser("check").set_defaults(func=cmd_memory_check)
    memory.add_parser("index").set_defaults(func=cmd_memory_index)

    q = memory.add_parser("write")
    q.add_argument("name")
    q.add_argument("--type", required=True,
                   choices=["user", "feedback", "project", "reference"])
    q.add_argument("--description", required=True)
    q.add_argument("--body")
    q.add_argument("--body-file")
    q.set_defaults(func=cmd_memory_write)

    # stats
    stats = sub.add_parser(
        "stats", help="what sessions took and what they cost"
    ).add_subparsers(dest="sub", required=True)

    q = stats.add_parser(
        "refresh", help="rebuild the raw store from the host's transcripts"
    )
    q.add_argument(
        "--force",
        action="store_true",
        help="re-parse even where the source has not changed",
    )
    q.set_defaults(func=cmd_stats_refresh)

    q = stats.add_parser("report", help="render the report")
    q.add_argument(
        "--write",
        action="store_true",
        help="write it to the manifest's paths.reports instead of stdout",
    )
    q.set_defaults(func=cmd_stats_report)

    q = stats.add_parser("show", help="one session in full, with its breakdown")
    q.add_argument("session", help="an invariant session name or id")
    q.set_defaults(func=cmd_stats_show)

    q = stats.add_parser(
        "archive",
        help="promote a record to permanent, so it outlives the host's retention",
    )
    q.add_argument("session", help="an invariant session name or id")
    q.set_defaults(func=cmd_stats_archive)

    stats.add_parser(
        "check", help="staleness, unpriced models, and records that are the only copy"
    ).set_defaults(func=cmd_stats_check)

    # rules / checks
    q = sub.add_parser("rules").add_subparsers(dest="sub", required=True)
    q.add_parser("list").set_defaults(func=cmd_rules_list)

    q = sub.add_parser("check")
    q.add_argument("rules", nargs="*")
    q.set_defaults(func=cmd_check)

    q = sub.add_parser("privacy").add_subparsers(dest="sub", required=True)
    r = q.add_parser("scan")
    r.add_argument("paths", nargs="+")
    r.add_argument("--reveal", action="store_true",
                   help="print raw matches (for a terminal, never for a file)")
    r.set_defaults(func=cmd_privacy_scan)

    # state
    state = sub.add_parser("state").add_subparsers(dest="sub", required=True)
    q = state.add_parser("show")
    q.add_argument("--session")
    q.set_defaults(func=cmd_state_show)

    q = state.add_parser("repair")
    q.add_argument("--dry-run", action="store_true")
    q.set_defaults(func=cmd_state_repair)

    # oracle
    oracle = sub.add_parser(
        "oracle", help="the machine-wide view of every invariant project"
    ).add_subparsers(dest="sub", required=True)
    projects = oracle.add_parser(
        "projects", help="the user-level index of registered projects"
    ).add_subparsers(dest="action", required=True)
    projects.add_parser("list", help="every registered project").set_defaults(
        func=cmd_oracle_projects_list
    )
    q = projects.add_parser(
        "add", help="register a project by hand (init and session start do it too)"
    )
    q.add_argument("path", nargs="?", help="a directory inside the project (default: here)")
    q.set_defaults(func=cmd_oracle_projects_add)
    q = projects.add_parser(
        "remove",
        help="drop a project from the index; refused when nothing matches",
    )
    q.add_argument("key", help="its state-root key, its root, or its name")
    q.set_defaults(func=cmd_oracle_projects_remove)
    q = projects.add_parser(
        "prune", help="drop every project whose root no longer has a manifest"
    )
    q.add_argument("--dry-run", action="store_true")
    q.set_defaults(func=cmd_oracle_projects_prune)
    q = oracle.add_parser(
        "list", help="every live session in every project, and its next step"
    )
    q.add_argument("--project", help="one project, by its key, root or name")
    q.set_defaults(func=cmd_oracle_list)
    q = oracle.add_parser(
        "watch",
        help="scan every few seconds, write the snapshot, alert once per rising edge",
    )
    q.add_argument("--once", action="store_true", help="one tick, then exit")
    q.add_argument("--interval", type=float, default=5.0, help="seconds between ticks")
    q.add_argument(
        "--stuck-minutes", type=float, default=20.0,
        help="a running turn silent this long is stuck",
    )
    q.add_argument(
        "--no-notify", action="store_true",
        help="post nothing; new alerts stay owed to a watcher that does",
    )
    q.set_defaults(func=cmd_oracle_watch)
    q = oracle.add_parser(
        "tui", help="the live dashboard; every action is confirmed first (needs invariant[oracle])"
    )
    q.set_defaults(func=cmd_oracle_tui)
    q = oracle.add_parser("notify-test", help="post one notification, to check delivery")
    q.set_defaults(func=cmd_oracle_notify_test)
    q = oracle.add_parser(
        "install", help="install the LaunchAgent that keeps `oracle watch` running"
    )
    q.add_argument("--dry-run", action="store_true",
                   help="print the plist and the launchctl calls; change nothing")
    q.add_argument("--interval", type=float, default=5.0, help="seconds between ticks")
    q.set_defaults(func=cmd_oracle_install)
    q = oracle.add_parser("uninstall", help="unload the LaunchAgent and remove its plist")
    q.add_argument("--dry-run", action="store_true",
                   help="print the launchctl calls; change nothing")
    q.set_defaults(func=cmd_oracle_uninstall)
    q = oracle.add_parser(
        "status", help="is the agent loaded, and how old is the watcher's snapshot"
    )
    q.set_defaults(func=cmd_oracle_status)
    web = oracle.add_parser(
        "web",
        help="serve the dashboard to paired devices on this Mac's Tailscale address",
    )
    web.add_argument("--port", type=int, default=7341)
    web.add_argument(
        "--allow-loopback", action="store_true",
        help="with no Tailscale address, serve on 127.0.0.1 for a test on this Mac",
    )
    web.set_defaults(func=cmd_oracle_web)
    web_actions = web.add_subparsers(dest="web_action")
    q = web_actions.add_parser("pair", help="a one-time code that pairs one device")
    q.add_argument("--name", default="device", help="what to call the device")
    q.add_argument("--ttl", type=float, default=300.0, help="seconds the code lives")
    q.add_argument("--port", type=int, default=7341, help="the port the link names")
    q.add_argument("--allow-loopback", action="store_true",
                   help="with no Tailscale address, link 127.0.0.1")
    q.set_defaults(func=cmd_oracle_web_pair)
    web_actions.add_parser("devices", help="the paired devices").set_defaults(
        func=cmd_oracle_web_devices
    )
    q = web_actions.add_parser("revoke", help="forget one paired device")
    q.add_argument("id", help="its id, from 'oracle web devices'")
    q.set_defaults(func=cmd_oracle_web_revoke)

    hooks = sub.add_parser("hooks").add_subparsers(dest="sub", required=True)
    q = hooks.add_parser("install")
    q.set_defaults(func=cmd_hooks_install)

    # host bridge
    q = sub.add_parser("hook")
    q.add_argument("event")
    q.set_defaults(func=cmd_hook)

    q = sub.add_parser(
        "override",
        help="pass a rule that fires at a hook, once, with the decision recorded",
    )
    q.add_argument("rule")
    q.add_argument("--reason", help="required unless --clear")
    q.add_argument("--calls", type=int, default=1, help="how many calls it covers")
    q.add_argument("--clear", action="store_true", help="withdraw an unspent grant")
    q.add_argument("--id")
    q.set_defaults(func=cmd_override)

    q = sub.add_parser(
        "standdown",
        help="hold every gate for an interval, reversibly, with the hold recorded",
    )
    q.add_argument("--reason", help="required unless --clear")
    q.add_argument(
        "--until",
        choices=("pipeline", "session"),
        default="pipeline",
        help="what lifts it; the default is the shortest scope that is useful",
    )
    q.add_argument(
        "--include",
        action="append",
        help="fold a held-out rule in; repeatable (privacy.clean is the only one)",
    )
    q.add_argument("--clear", action="store_true", help="lift it now")
    q.add_argument("--id")
    q.set_defaults(func=cmd_standdown)

    q = sub.add_parser("context")
    q.add_argument("--id", help="the session to report on, when the directory cannot say")
    q.set_defaults(func=cmd_context)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except InvariantError as exc:
        print(f"invariant: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
