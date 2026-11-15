"""``invariant store migrate``: the one route between a project's two homes
(ADR-014 D12, increment 7a; ADR-020 D3).

``--to store`` moves the configuration and every record of an in-repo
project into a store, as its own chore-lane session, which the command
starts parked and isolated, as ``docs assess --plan`` parks its plan:

1. ``core/storemigrate`` plans the move and refuses, naming every reason,
   before anything is written.
2. The store leg is written first (ruling 8): this repository's block in
   the store's ``invariant.yaml``, the user level's link (written at once:
   the in-repo manifest is found first until the merge), and the records
   with their history, under the session's docs-lock grants. The units are
   released at once, which lands the store leg (``landing.store``).
3. The repository leg is a commit on the session's branch: the manifest,
   the moved records and the committed wiring removed. It lands at ``session
   merge``, after review, like any change.
4. After it lands, the wiring the project had committed is installed again
   as ``local`` (:func:`finish`), never before, so nothing fires twice.

``--to repo`` is increment 7b; until then it is refused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import storemigrate
from ..core.errors import InvariantError, UsageError

#: The task the session is registered under.
TASK = "store migrate to store"


def _host(manifest, root: Path):
    """The host adapter that wires ``root``, or ``None`` for a host kind with
    none: only `hosts/` reads or writes host files."""
    if manifest.get("host.kind") != "claude_code":
        return None
    from ..hosts.claude_code import ClaudeCodeHost

    return ClaudeCodeHost(root, manifest=manifest)


def to_store(args) -> tuple[dict[str, Any], str]:
    """Run ``store migrate --to store``: ``(payload, text)``."""
    from ..adapters.sphinx import HOMES as SPHINX_HOMES
    from ..core.stores import link_repo
    from .main import Context, _ensure_store_hooks, _release_docs

    if args.records:
        raise UsageError(
            "store migrate --to store moves everything: the configuration lives in "
            "the repository only while every record does, so a subset cannot leave "
            "it (ADR-020 D3). --records moves single records within a store project, "
            "with --to repo (ADR-014 increment 7b). Nothing was written."
        )
    cwd = Path.cwd()
    ctx = Context(required=False)
    host = _host(ctx.manifest, cwd) if ctx.manifest.located else None
    committed = host.committed_wiring() if host is not None else {"hooks": [], "agents": []}
    plan = storemigrate.plan_to_store(
        ctx.manifest,
        cwd,
        store=Path(args.store) if args.store else None,
        content_folders=SPHINX_HOMES,
        wiring=[*committed["hooks"], *committed["agents"]],
    )
    wiring = {"hooks": bool(committed["hooks"]), "agents": bool(committed["agents"])}

    done: list[str] = []
    try:
        storemigrate.write_store(plan)
        done.append(
            f"{'created' if plan.new_store else 'wrote this repository into'} the "
            f"store {plan.store}"
        )
        linked = link_repo(plan.top, plan.store)
        done.append(f"linked {linked.key} to it in the user level")
        hook_notes = _ensure_store_hooks(plan.store)

        session = ctx.sessions.start(TASK, parked=True)
        done.append(f"registered the parked session {session.name}")
        isolated = ctx.sessions.isolate(session.id, execute=True)
        worktree = Path(isolated["worktree"])
        ctx.pipelines.start(
            session.id,
            methodology="chore",
            task=f"move the configuration and records to store {plan.store.name}",
            session=ctx.sessions.require(session.id).to_dict(),
            cwd=worktree,
        )
        ctx.sessions.record_migrate(
            session.id,
            {
                "to": "store",
                "store": str(plan.store),
                "identity": plan.key,
                "wiring": wiring,
                "roots": list(plan.roots),
            },
        )

        changed: list[str] = []
        kept_agents: list[str] = []
        if any(wiring.values()):
            wt_host = _host(ctx.manifest, worktree)
            changed, kept_agents = wt_host.remove_committed(**wiring)
        repo_sha = storemigrate.remove_from_repo(plan, worktree, changed)
        done.append(f"committed the repository leg on {isolated['branch']}")

        # The store leg, read as the session's worktree now resolves it: a store
        # project, whose docs lock governs what the history writes.
        store_ctx = Context(cwd=worktree)
        store_ctx.sessions.docs_write(session.id, [plan.store / rel for rel in plan.moved])
        commits = storemigrate.transfer(plan.top, plan.head, plan.roots, plan.store)
        if not plan.new_store:
            storemigrate.commit_store(plan)
        pull, _out = _release_docs(
            store_ctx,
            session.id,
            (),
            "store migrate stopped after writing the store: its docs are still held. "
            "Resolve it in the store's main checkout, then run 'invariant docs unlock'.",
        )
        landed = store_ctx.sessions.record_store_landed(session.id)
        done.append("landed the store leg")
    except InvariantError as exc:
        if not done:
            raise
        raise type(exc)(
            f"{exc}\nstore migrate stopped part way; nothing is rolled back (ADR-014 "
            f"ruling 8). Done before it stopped: " + "; ".join(done) + "."
        ) from exc

    session = ctx.sessions.require(session.id)
    payload = {
        "to": "store",
        "store": str(plan.store),
        "created": plan.new_store,
        "identity": plan.key,
        "session": {"id": session.id, "name": session.name},
        "worktree": str(worktree),
        "branch": isolated["branch"],
        "repo_commit": repo_sha,
        "store_commits": commits,
        "landing": landed.landing,
        "roots": plan.roots,
        "moved": plan.moved,
        "records": plan.records,
        "kept": plan.kept,
        "wiring": wiring,
        "kept_agents": kept_agents,
        "left": plan.left,
        "remote": bool(plan.remote),
    }
    return payload, "\n".join(_lines(plan, session, isolated, repo_sha, commits, landed,
                                      wiring, kept_agents, pull, hook_notes))


def _shown(store: Path, rel: str) -> str:
    """A moved root as the output names it: a folder with its slash."""
    return f"{rel}/" if (store / rel).is_dir() else rel


def _lines(plan, session, isolated, repo_sha, commits, landed, wiring, kept_agents, pull,
           hook_notes) -> list[str]:
    """What the command says. Never ``session <id> (``: a host binds its
    caller to the session that sequence names, and this session is parked
    for whoever picks it up."""
    moving = sorted(r for r, where in plan.records.items() if where == "store")
    store_sha = (landed.landing or {}).get("store") or "?"
    lines = [
        (
            f"store migrate --to store: the configuration and records of this "
            f"repository move to store {plan.store.name}, on the parked chore-lane "
            f"session {session.name}"
        ),
        (
            f"  store {plan.store} created" + (f" ({plan.note})" if plan.note else "")
            if plan.new_store else f"  store {plan.store}: this repository added to it"
        ),
        (
            f"  store leg (landed at {store_sha[:12]}): store.repos.{plan.key} in "
            f"invariant.yaml, and {commits} commit(s) of history for "
            + (", ".join(_shown(plan.store, r) for r in plan.roots) or "no file")
        ),
        (
            f"  repository leg (lands at the merge): {repo_sha[:12]} on "
            f"{isolated['branch']} removes .claude/invariant.yaml, "
            f"{len(plan.moved)} record file(s)"
            + (" and the committed wiring" if any(wiring.values()) else "")
        ),
        f"  records: {', '.join(moving) or 'none'} -> store; hooks, agents -> local",
        *[f"  {record} stays in the repository: {why}" for record, why in plan.kept.items()],
        f"  linked {plan.key} -> {plan.store} in the user level",
        *[f"  {line}" for line in hook_notes],
        *([f"  {line}" for line in pull.lines()] if pull is not None else []),
    ]
    if any(wiring.values()):
        kinds = " and ".join(k for k, v in wiring.items() if v)
        lines.append(
            f"  wiring: the committed {kinds} become local (.claude/settings.local.json, "
            f"listed in .git/info/exclude) once the repository leg lands"
        )
    lines += [
        f"  {rel} was edited here, so it stays committed" for rel in kept_agents
    ]
    if plan.left:
        lines.append(
            "  ignored files under the moved folders stay in this working tree, and "
            "once the merge takes their .gitignore away git status shows them; "
            "remove them, or make them again in the store: "
            + ", ".join(plan.left[:8]) + (" ..." if len(plan.left) > 8 else "")
        )
    lines.append(
        "  until the merge, this checkout still reads .claude/invariant.yaml; the "
        "session's worktree already reads the store"
    )
    if plan.remote:
        lines.append(
            f"  this repository has a remote ({plan.remote}): after the merge every "
            f"other clone, teammates' and CI's, finds no configuration until it links "
            f"the store ('invariant store clone <the store's remote>', ADR-014 D7)"
        )
    lines.append(
        f"  next: invariant session resume {session.name}; review it, then "
        f"'invariant session merge --id {session.id}'"
    )
    return lines


def finish(ctx, session, main: Path) -> list[str]:
    """What follows a migrate session's repository leg landing (ADR-020 D3):
    the wiring the project had committed installed again as ``local``, the
    move recorded complete and its marker removed. Nothing for any other
    session, or for one already finished. A wiring install that fails is
    named with its repair; the move is complete either way."""
    move = session.migrate or {}
    if not move or move.get("finished") is not None:
        return []
    from .main import Context

    lines: list[str] = []
    wiring = move.get("wiring") or {}
    if move.get("to") == "store" and any(wiring.values()):
        try:
            fresh = Context(cwd=main)
            host = _host(fresh.manifest, main)
            if host is not None:
                if wiring.get("hooks"):
                    host.install_hooks()
                if wiring.get("agents"):
                    host.install_assets(only="agent")
            kinds = " and ".join(k for k, v in wiring.items() if v)
            lines.append(
                f"the {kinds} are wired locally now (.claude/settings.local.json, "
                f"listed in .git/info/exclude)"
            )
        except InvariantError as exc:
            lines.append(
                f"the local wiring was not installed: {exc}; run 'invariant hooks "
                f"install' and 'invariant agents install'"
            )
    ctx.sessions.finish_migrate(session.id)
    lines.append(
        f"store migrate --to {move.get('to')} is complete: the configuration resolves "
        f"from store {Path(move.get('store', '')).name}"
    )
    return lines
