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
import sys
from collections.abc import Mapping
from pathlib import Path

from ..core.errors import GateFailure, InvariantError, UsageError
from ..core.manifest import Manifest
from ..core.rules import RuleContext, all_rules, evaluate
from ..core.store import JsonStore
from ..domains.pipeline import PipelineDomain, describe_run, summarise
from ..domains.session import SessionDomain

EXIT_OK = 0


# -- output helpers ----------------------------------------------------


def emit(args, payload: dict, text: str = "") -> int:
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    elif text:
        print(text)
    return EXIT_OK


def report_verdicts(verdicts, args) -> None:
    if getattr(args, "json", False):
        return
    for verdict in verdicts:
        if verdict.verdict.skipped and not getattr(args, "verbose", False):
            continue
        if verdict.verdict.ok and not getattr(args, "verbose", False):
            continue
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
    ) -> None:
        self.cwd = Path(cwd or Path.cwd())
        self.manifest = Manifest.load(self.cwd, required=required)
        self.store = JsonStore(self.manifest.state_root)
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

    @property
    def sessions(self) -> SessionDomain:
        return SessionDomain(self.manifest, self.store)

    @property
    def pipelines(self) -> PipelineDomain:
        return PipelineDomain(self.manifest, self.store)

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
        """Who this tool call belongs to, using the call's own destination.

        The path a write is aimed at is the strongest attribution signal a hook
        ever gets: it is in the payload, it is absolute, and it does not depend
        on the working directory the host happens to be in or on an environment
        variable a tool call cannot carry.

        When that path identifies a session which has no host identifier
        recorded — every session registered before the identifier existed — the
        mapping is learned here, so the *next* call from the same host session
        resolves by identifier alone and works for tool calls that carry no
        path at all.
        """
        if self.session_id:
            return self.sessions.require(self.session_id)

        tool_input = payload.get("tool_input")
        target = None
        if isinstance(tool_input, Mapping):
            raw = tool_input.get("file_path") or tool_input.get("notebook_path")
            target = Path(str(raw)) if raw else None

        resolution = self.sessions.resolve(
            self.cwd, target=target, host_session_id=self.host_session_id
        )
        session = resolution.session
        if (
            resolution.how == "target"
            and session is not None
            and self.host_session_id
            and not session.host_session_id
        ):
            try:
                session = self.sessions.adopt_host_session(
                    session.id, self.host_session_id
                )
            except InvariantError:
                pass  # a failed optimisation must not deny the tool call
        return session

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

    JsonStore(root / ".claude" / "invariant" / "state").ensure_root()
    return emit(
        args,
        {"actions": actions, "ecosystems": ecosystems},
        "initialised:\n"
        + "\n".join(f"  - {a}" for a in actions)
        + "\n\nCommit these before starting a session. A session works in a "
        "linked\nworktree, and a worktree checks out a commit — an uncommitted "
        "manifest\nis invisible from inside one.",
    )


def cmd_doctor(args) -> int:
    from ..hosts.claude_code import ClaudeCodeHost

    ctx = Context(required=False)
    checks: list[tuple[str, bool, str]] = []

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

        docs = ctx.manifest.path_for("docs")
        checks.append(
            (
                "architecture docs exist",
                docs.is_dir(),
                str(docs) + ("" if docs.is_dir() else "  (docs-first is core policy)"),
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

        if ctx.manifest.get("memory.managed", True) and ctx.memory.root.exists():
            report = ctx.memory.check()
            checks.append(
                ("memory store clean", report.clean, f"{report.count} memories, "
                 f"{len(report.problems())} problem(s)")
            )

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
        return emit(args, {"dry_run": True}, "would reinstall library assets")

    installed = host.install_assets(only=args.kind)
    failures = [
        (label, detail)
        for label, ok, detail in host.asset_checks()
        if not ok and "loadable" in label
    ]
    lines = [f"installed {len(installed)} asset(s)"] + [f"  {i}" for i in installed]
    for label, detail in failures:
        lines.append(f"  STILL BROKEN — {label}: {detail}")
    if not failures:
        lines.append("  reload the host's configuration for it to pick them up")
    emit(args, {"installed": installed, "loadable": not failures}, "\n".join(lines))
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
        ClaudeCodeHost,
    )

    ctx = Context()
    host = ClaudeCodeHost(ctx.manifest.root)
    agents_dir = host.claude_dir / "agents"
    sources = sorted((DATA_DIR / "agents").glob("*.md"))

    if args.dry_run:
        return emit(
            args,
            {"would_install": [s.stem for s in sources], "into": str(agents_dir)},
            "\n".join(
                [f"would install into {agents_dir}:"] + [f"  {s.stem}" for s in sources]
            ),
        )

    agents_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        host.install_agent(source, agents_dir / source.name)

    checks = {label: (ok, detail) for label, ok, detail in host.agent_checks()}
    ok, detail = checks[CHECK_AGENTS_LOADABLE]
    lines = [f"installed {len(sources)} agent(s) into {agents_dir}", f"  {detail}"]
    if ok:
        lines.append("  reload the host's configuration for it to pick them up")
    emit(args, {"installed": [s.stem for s in sources], "loadable": ok}, "\n".join(lines))
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
    )
    plan = ctx.sessions.isolation_plan(session)
    return emit(
        args,
        {"session": session.to_dict(), "plan": plan},
        f"session {session.id} ({session.name}) registered\n"
        f"  next: invariant session isolate --execute\n"
        f"        worktree {plan['worktree']}  branch {plan['branch']}",
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
    return emit(
        args,
        {"session": session.to_dict(), "pipeline": pipeline.to_dict() if pipeline else None},
        "\n".join(text),
    )


def cmd_session_transition(args) -> int:
    ctx = Context()
    session = ctx.require_session() if not args.id else ctx.sessions.require(args.id)
    updated = ctx.sessions.transition(session.id, args.state, reason=args.reason, force=args.force)
    return emit(args, updated.to_dict(), f"session {updated.id} -> {updated.state}")


def cmd_session_files(args) -> int:
    ctx = Context()
    session = (
        ctx.sessions.require(args.id) if getattr(args, "id", None)
        else ctx.require_session()
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
    text = "\n".join(
        f"  {s.id}  {s.state:<12} {s.name:<24} {s.worktree or ''}" for s in sessions
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

    gates = machine.state("reviewed").entry_gates + machine.state(
        session.state
    ).exit_gates
    verdicts = evaluate(list(dict.fromkeys(gates)), ctx.rule_context())
    blocking = [v for v in verdicts if v.blocks]
    if blocking and not args.force:
        report_verdicts(verdicts, args)
        raise GateFailure("the review gates refused this session", verdicts=verdicts)

    ctx.sessions.transition(
        session.id, "reviewed", reason=args.reason or "review gates evaluated",
        force=args.force,
    )
    return emit(
        args,
        {"session": session.id, "verdicts": verdicts_payload(verdicts)},
        f"session {session.id} reviewed ({len(verdicts)} gate(s) evaluated)",
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
    machine = ctx.sessions.machine
    gates = list(
        dict.fromkeys(
            machine.state("merged").entry_gates + machine.state(session.state).exit_gates
        )
    )
    verdicts = evaluate(gates, ctx.rule_context())
    blocking = [v for v in verdicts if v.blocks]
    if blocking and not args.force:
        report_verdicts(verdicts, args)
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
        proc = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True)
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

    ctx.sessions.transition(session.id, "merged", reason="ff-merge verified")
    return emit(
        args,
        {"steps": performed, "verified": True},
        f"merged {session.branch} into {session.base} (containment verified)",
    )


def cmd_session_push(args) -> int:
    import subprocess

    ctx = Context()
    session = ctx.require_session() if not args.id else ctx.sessions.require(args.id)
    if not ctx.manifest.get("session.push", False):
        # Refused outright rather than gated. With publishing off, the barrier
        # abstains by design, so consulting it would report no objection to a
        # command the project does not want run at all.
        raise UsageError(
            "publishing is not part of this project's session lifecycle "
            "(session.push is off).\n"
            "  A session ends at the merge: 'invariant session teardown'.\n"
            "  Every worktree already sees the merged work — they share one "
            "object database.\n"
            "  To make publishing a session step, set session.push: true."
        )
    verdicts = evaluate(
        ["session.push_barrier"], ctx.rule_context(extra={"pushing": True})
    )
    blocking = [v for v in verdicts if v.blocks]
    if blocking and not args.force:
        report_verdicts(verdicts, args)
        raise GateFailure("the push barrier refused this push", verdicts=verdicts)

    proc = subprocess.run(
        ["git", "push", args.remote, session.base],
        cwd=str(ctx.cwd),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise UsageError(f"push failed: {proc.stderr.strip()}")
    ctx.sessions.record_push(session.id)
    return emit(args, {"pushed": True}, f"pushed {session.base} to {args.remote}")


def cmd_session_teardown(args) -> int:
    ctx = Context()
    session = ctx.require_session() if not args.id else ctx.sessions.require(args.id)
    updated = ctx.sessions.teardown(
        session.id,
        remove_worktree=not args.keep_worktree,
        remove_branch=not args.keep_branch,
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
    pipeline = ctx.pipelines.start(session.id, methodology=args.methodology, task=args.task or "")
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
    machine = ctx.pipelines.machine
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
    verdicts = ctx.pipelines.evaluate_gates(
        session.id, target=args.state, session=session.to_dict(), cwd=ctx.cwd
    )
    text = "\n".join(v.render() for v in verdicts) or "no gates on this transition"
    text += f"\n\n{summarise(verdicts)}"
    return emit(args, {"gates": verdicts_payload(verdicts)}, text)


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
        raise
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
        session.id, phase=args.phase, selector=args.selector, cwd=ctx.cwd
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


def cmd_item_new(args) -> int:
    ctx = Context()
    fields = dict(f.split("=", 1) for f in (args.field or []) if "=" in f)
    item = ctx.items.new(args.type, args.title, fields=fields, body=args.body or "")
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
    if item.body:
        text.extend(["", item.body])
    return emit(args, item.to_dict(), "\n".join(text))


def cmd_item_transition(args) -> int:
    ctx = Context()
    try:
        item, verdicts = ctx.items.transition(
            args.id, args.status, reason=args.reason, force=args.force, cwd=ctx.cwd
        )
    except GateFailure as exc:
        report_verdicts(exc.verdicts, args)
        raise
    return emit(args, item.to_dict(), f"{item.id} -> {item.status}")


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
    failures = [v for v in verdicts if not v.verdict.ok and not v.verdict.skipped]
    text = "\n".join(v.render() for v in verdicts if not v.verdict.skipped or args.verbose)
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


# -- host bridge -------------------------------------------------------


def cmd_hook(args) -> int:
    from ..hosts.claude_code import ClaudeCodeHost

    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    host_session_id = None
    try:
        host_session_id = (json.loads(raw) or {}).get("session_id")
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    try:
        ctx = Context(required=True, host_session_id=host_session_id)
    except InvariantError:
        # A repository without a manifest is not this hook's business. Staying
        # silent is correct; failing would break every tool call in a project
        # that merely has the shims installed.
        print(json.dumps({}))
        return EXIT_OK
    host = ClaudeCodeHost(ctx.manifest.root)
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
        print(json.dumps(_hook_degraded(args.event, exc)))
        return EXIT_OK
    print(json.dumps(response))
    return EXIT_OK


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
    q.add_argument("--kind", choices=kinds)
    q.add_argument("--dry-run", action="store_true")
    q.set_defaults(func=cmd_assets_install)

    agents = sub.add_parser("agents").add_subparsers(dest="sub", required=True)
    agents.add_parser("list", help="installed agents and whether the host can load them"
                      ).set_defaults(func=cmd_agents_list)
    q = agents.add_parser("install", help="reinstall the library agents")
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
    q.add_argument("--base", default="master")
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
    q.add_argument("action", choices=["add"])
    q.add_argument("paths", nargs="+")
    q.add_argument("--id")
    q.set_defaults(func=cmd_session_files)

    q = session.add_parser("adopt", help="bind a host session id to a session")
    q.add_argument("host_session_id")
    q.add_argument("--id")
    q.add_argument("--force", action="store_true", help="rebind deliberately")
    q.set_defaults(func=cmd_session_adopt)

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
    q.add_argument("--selector")
    q.set_defaults(func=cmd_pipeline_test)

    # items
    item = sub.add_parser("item").add_subparsers(dest="sub", required=True)

    q = item.add_parser("new")
    q.add_argument("type", choices=["idea", "bug"])
    q.add_argument("--title", required=True, dest="title")
    q.add_argument("--field", action="append")
    q.add_argument("--body")
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

    hooks = sub.add_parser("hooks").add_subparsers(dest="sub", required=True)
    q = hooks.add_parser("install")
    q.set_defaults(func=cmd_hooks_install)

    # host bridge
    q = sub.add_parser("hook")
    q.add_argument("event")
    q.set_defaults(func=cmd_hook)

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
