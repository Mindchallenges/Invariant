"""``invariant store migrate``: the one route between a project's two homes
(ADR-014 D12, increments 7a and 7b; ADR-020 D3).

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

``--to repo`` (increment 7b) is the same sequence the other way: the
repository leg carries the records' history back onto the session's branch
with the materialised ``.claude/invariant.yaml`` and the local wiring in its
committed form; the store leg removes this repository's block and records
from the store and lands at once. Until the merge the main checkout finds
neither home and refuses, naming the session; after it the local wiring and
the user level's link are removed (:func:`finish`).

``--records``, moving single records within a store project (ADR-020 D3),
is in no increment yet and is refused in either direction.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from ..core import storemigrate
from ..core.errors import InvariantError

#: The task each direction's session is registered under.
TASK = "store migrate to store"
TASK_REPO = "store migrate to repo"


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


def refuse_records(args) -> None:
    """``--records`` is in no increment yet (ADR-020 D3 keeps it for moving
    single records within a store project): refused in either direction,
    before anything is read or written."""
    if not args.records:
        return
    if args.to == "store":
        why = (
            "--to store moves everything: the configuration lives in the repository "
            "only while every record does, so a subset cannot leave it (ADR-020 D3)"
        )
    else:
        why = "--to repo moves everything back, the configuration with it (ADR-020 D3)"
    raise storemigrate.MigrateRefused(
        args.to,
        [
            (
                f"{why}. --records, moving single records within a store project, is "
                f"in no increment of ADR-014 or ADR-020 yet"
            )
        ],
    )


def to_repo(args) -> tuple[dict[str, Any], str]:
    """Run ``store migrate --to repo``: ``(payload, text)``."""
    from ..adapters.sphinx import HOMES as SPHINX_HOMES
    from ..adapters.sphinx import conf_excludes
    from ..core.manifest import Manifest
    from .main import Context, _release_docs

    if args.store:
        raise storemigrate.MigrateRefused(
            "repo",
            [
                (
                    "--store names the store --to store moves into; --to repo moves "
                    "out of the project's own store"
                )
            ],
        )
    cwd = Path.cwd()
    ctx = Context(required=False)
    host = _host(ctx.manifest, cwd) if ctx.manifest.located else None
    local = host.local_wiring() if host is not None else {"hooks": [], "agents": []}
    plan = storemigrate.plan_to_repo(
        ctx.manifest,
        cwd,
        content_folders=SPHINX_HOMES,
        conf_excludes=conf_excludes,
        wiring=[*local["hooks"], *local["agents"]],
    )
    wiring = {"hooks": bool(local["hooks"]), "agents": bool(local["agents"])}

    done: list[str] = []
    try:
        session = ctx.sessions.start(TASK_REPO, parked=True)
        done.append(f"registered the parked session {session.name}")
        isolated = ctx.sessions.isolate(session.id, execute=True)
        worktree = Path(isolated["worktree"])
        ctx.pipelines.start(
            session.id,
            methodology="chore",
            task=f"move the configuration and records back from store {plan.store.name}",
            session=ctx.sessions.require(session.id).to_dict(),
            cwd=worktree,
        )
        ctx.sessions.record_migrate(
            session.id,
            {
                "to": "repo",
                "store": str(plan.store),
                "identity": plan.key,
                "wiring": wiring,
                "roots": list(plan.roots),
            },
        )

        # The repository leg, on the session's branch: the history, the
        # manifest, and the wiring in its committed form, read as the
        # worktree now resolves (an in-repo project).
        commits = storemigrate.lay_repo_leg(plan, worktree)
        paths: list[str] = []
        edited: list[str] = []
        if any(wiring.values()):
            wt_host = _host(Manifest.locate(worktree), worktree)
            if wt_host is not None:
                paths, edited = wt_host.commit_local(**wiring)
        repo_sha = storemigrate.commit_repo_leg(plan, worktree, paths)
        done.append(f"committed the repository leg on {isolated['branch']}")

        # The store leg, under the session's docs-lock grants as the main
        # checkout still resolves it (a store project), then released, which
        # lands it (ruling 8).
        ctx.sessions.docs_write(session.id, [plan.store / rel for rel in plan.moved])
        store_sha = storemigrate.remove_from_store(plan)
        done.append(f"removed this repository from store {plan.store.name}")
        pull, _out = _release_docs(
            ctx,
            session.id,
            (),
            "store migrate stopped after writing the store: its docs are still held. "
            "Resolve it in the store's main checkout, then run 'invariant docs unlock' "
            "from the session's worktree.",
        )
        landed = ctx.sessions.record_store_landed(session.id)
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
        "to": "repo",
        "store": str(plan.store),
        "identity": plan.key,
        "session": {"id": session.id, "name": session.name},
        "worktree": str(worktree),
        "branch": isolated["branch"],
        "repo_commit": repo_sha,
        "repo_commits": commits,
        "store_commit": store_sha,
        "landing": landed.landing,
        "roots": plan.roots,
        "moved": plan.moved,
        "records": plan.records,
        "from_store_level": plan.from_store_level,
        "dropped": plan.dropped,
        "wiring": wiring,
        "edited": edited,
        "pointer": plan.pointer,
        "left": plan.left,
        "serves_none": plan.serves_none,
    }
    return payload, "\n".join(
        _repo_lines(plan, session, worktree, isolated, repo_sha, commits, landed, wiring,
                    edited, pull)
    )


def _repo_lines(plan, session, worktree, isolated, repo_sha, commits, landed, wiring, edited,
                pull) -> list[str]:
    """What ``--to repo`` says; never ``session <id> (``, as for ``--to store``."""
    store_sha = (landed.landing or {}).get("store") or "?"
    moving = sorted(r for r, where in plan.records.items() if where == "store")
    lines = [
        (
            f"store migrate --to repo: the configuration and records of this "
            f"repository move back from store {plan.store.name}, on the parked "
            f"chore-lane session {session.name}"
        ),
        (
            f"  store leg (landed at {store_sha[:12]}): store.repos.{plan.key} and "
            + (", ".join(_shown_rel(r) for r in plan.roots) or "no record file")
            + f" removed from store {plan.store.name}; its own level is unchanged"
        ),
        (
            f"  repository leg (lands at the merge): {repo_sha[:12]} on "
            f"{isolated['branch']} adds .claude/invariant.yaml and {len(plan.moved)} "
            f"record file(s) with {commits} commit(s) of history"
            + (" and the committed wiring" if any(wiring.values()) else "")
            + (f", and removes {plan.pointer}" if plan.pointer else "")
        ),
        f"  records: {', '.join(moving) or 'none'} -> repo",
    ]
    if plan.from_store_level:
        lines.append(
            "  copied from the store's own level, not this repository's block: "
            + ", ".join(plan.from_store_level)
        )
    if plan.dropped:
        lines.append(
            "  left behind, since only a store project may set them: "
            + ", ".join(plan.dropped)
        )
    if plan.serves_none:
        lines.append(
            f"  store {plan.store.name} now serves no repository; it is kept, at "
            f"{plan.store}"
        )
    if pull is not None:
        lines += [f"  {line}" for line in pull.lines()]
    if any(wiring.values()):
        kinds = " and ".join(k for k, v in wiring.items() if v)
        lines.append(
            f"  wiring: the local {kinds} become committed (.claude/settings.json, "
            f".claude/hooks/, .claude/agents/); the local entries and their "
            f".git/info/exclude lines are removed once the repository leg lands"
        )
    lines += [
        f"  {rel} was edited here, so it is committed as edited" for rel in edited
    ]
    if plan.left:
        lines.append(
            "  ignored files under the moved folders stay in the store's working "
            "tree: " + ", ".join(plan.left[:8]) + (" ..." if len(plan.left) > 8 else "")
        )
    lines += [
        (
            "  until the merge, this checkout finds neither home and refuses, naming "
            "the session; the session's worktree reads .claude/invariant.yaml"
        ),
        "  the user level's link is removed once the repository leg lands",
        (
            f"  next: review it in the session's worktree ({worktree}), picked up "
            f"with 'invariant session resume {session.name}'; then, here in the main "
            f"checkout, 'invariant session merge --id {session.id}'"
        ),
    ]
    return lines


def _shown_rel(rel: str) -> str:
    """A moved root as the store leg names it."""
    return rel if PurePosixPath(rel).suffix else f"{rel}/"


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
    if move.get("to") == "repo":
        lines += _finish_repo(move, main)
    elif any(wiring.values()):
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
        "store migrate --to repo is complete: the configuration resolves from "
        ".claude/invariant.yaml"
        if move.get("to") == "repo"
        else f"store migrate --to {move.get('to')} is complete: the configuration "
        f"resolves from store {Path(move.get('store', '')).name}"
    )
    return lines


def _finish_repo(move: dict, main: Path) -> list[str]:
    """``--to repo``'s steps once its repository leg has landed (ADR-020 D3):
    the local wiring's entries and exclude lines retired, its files now
    committed; the user level's link removed, ``store link``'s inverse. Each
    that fails is named with what to do; the move is complete either way."""
    from ..core.identity import repo_identity
    from ..core.stores import unlink_repo
    from .main import Context

    lines: list[str] = []
    wiring = move.get("wiring") or {}
    if any(wiring.values()):
        try:
            host = _host(Context(cwd=main).manifest, main)
            removed = host.retire_local() if host is not None else []
            lines.append(
                "the wiring is committed now; the local entries in "
                ".claude/settings.local.json and "
                f"{len(removed)} line(s) of .git/info/exclude are removed"
            )
        except InvariantError as exc:
            lines.append(
                f"the local wiring was not retired: {exc}; remove invariant's entries "
                f"from .claude/settings.local.json and its lines from .git/info/exclude"
            )
    identity = repo_identity(main)
    keys = [move.get("identity"), *(
        [identity.key, identity.local_id] if identity is not None else []
    )]
    try:
        removed_keys = unlink_repo([k for k in keys if k], Path(move.get("store", "")))
        lines.append(
            f"the user level's link of {', '.join(removed_keys)} to store "
            f"{Path(move.get('store', '')).name} is removed"
            if removed_keys
            else "the user level held no link to remove"
        )
    except InvariantError as exc:
        lines.append(f"the user level's link was not removed: {exc}")
    return lines
