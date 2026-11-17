"""`init`'s docs offers and the all-at-once plan (ADR-016 D4, D5, ruling 9).

Offers only (ruling D): every question goes through a
:class:`~invariant.cli.prompts.Prompter`, and a run that may not ask never
reaches this module's questions. The existing docs directory is never
written: reuse leaves it as it is, and another path points `paths.docs`
elsewhere, in the home the configuration lives in (ADR-020 D4): the
repository, or the store, where the path is store-relative.

The store itself is the home's, placed by :func:`place_store` (ADR-020 D1):
created or linked, then this repository's block written with every record's
location and `paths.docs`, or printed to add by hand where that file holds
comments a rewrite would drop. An in-repo manifest cannot carry a `store`
record, since an in-repo project does no store lookup (manifest.md, "Records
and their locations"), so the store home writes no `.claude/invariant.yaml`.

The plan is shared with `invariant docs assess --plan`: one parked
`docs`-lane session holding one deliverable per undocumented area, recorded
only once the base carries the manifest.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import plan as plan_reader
from ..core.manifest import Manifest, manifest_tracking, project_relative
from ..core.stores import (
    HomeStore,
    StoreWrite,
    by_hand_block,
    init_store,
    link_repo,
    write_store_settings,
)
from ..domains.docs import (
    STARTER_SRCDIR,
    Assessment,
    plan_deliverables,
    refused_srcdir,
    setting_lines,
)
from ..domains.session import Session
from .prompts import Prompter

#: The task slug of the parked session the all-at-once plan sits on.
PLAN_TASK = "document-architecture"

#: Where the docs go (D4.1), within the home: the repository or the store
#: (ADR-020 D4). The home itself is chosen before, by `home:location`.
LOCATIONS = (
    ("reuse", "keep it as invariant's architecture docs; nothing is written"),
    ("path", "point paths.docs at another directory in {within}"),
)

#: How the gaps are closed (D4.3). `not_now` is the default: it writes nothing.
ONBOARDING = (
    ("not_now", "write nothing; doctor and 'invariant docs assess' keep the gaps visible"),
    ("incremental", "each session owes the doc of an undocumented area it touches"),
    ("all_at_once", "a deliverable plan, one line per undocumented area, in the docs lane"),
)

#: How many entries of an existing docs directory are named before "and N more".
_LISTED = 8


@dataclass(frozen=True)
class DocsChoice:
    """Where `init` puts invariant's architecture docs."""

    #: ``reuse`` or ``path``.
    kind: str
    #: ``paths.docs``: relative to the home, the repository or the store.
    docs: str


@dataclass
class StorePlacement:
    """The store `init` created or linked, and what it says about it.

    ``held`` is set when the store's file holds comments, so nothing was
    written to it: every setting `init` would have written collects there,
    to be printed once as the block to add by hand.
    """

    root: Path
    key: str
    lines: tuple[str, ...]
    held: StoreWrite | None = None
    held_repo: dict[str, Any] = field(default_factory=dict)
    held_top: dict[str, Any] = field(default_factory=dict)
    #: Keys to take out of the block by hand (ADR-017 ruling 12).
    held_removals: tuple[str, ...] = ()

    def write(self, repo: Mapping[str, Any], remove: Sequence[str] = ()) -> str:
        """Write ``repo`` into this repository's block, and take the dotted
        keys ``remove`` out of it (ADR-017 ruling 12), or hold them with the
        rest when the file is held; the action line either way."""
        settings = ", ".join([*setting_lines(repo), *(f"remove {key}" for key in remove)])
        if self.held is None:
            result = write_store_settings(self.root, self.key, repo=repo, remove_repo=remove)
            if result.written:
                return (
                    f"write {settings} to {result.path} "
                    f"(uncommitted: review and commit it in the store)"
                )
            self.held = result
        self.held_repo = _merge(self.held_repo, repo)
        self.held_removals += tuple(remove)
        return f"not written, to add by hand: {settings} (see below)"

    def write_top(self, settings: Mapping[str, Any]) -> str:
        """Write ``settings`` at the store's own level, or hold them with the
        rest when the file is held; the action line either way."""
        spelled = ", ".join(setting_lines(settings))
        if self.held is None:
            result = write_store_settings(self.root, self.key, top=settings)
            if result.written:
                return f"write {spelled} to the store's own level, {result.path}"
            self.held = result
        self.held_top = _merge(self.held_top, settings)
        return f"not written, to add by hand: {spelled} (see below)"

    def by_hand(self) -> dict[str, str] | None:
        """The block to add by hand, where it goes and why; ``None`` when
        everything was written."""
        if self.held is None:
            return None
        reason = self.held.reason
        if self.held_removals:
            keys = ", ".join(f"store.repos.{self.key}.{k}" for k in self.held_removals)
            reason += f"; remove {keys} by hand as well"
        return {
            "path": str(self.held.path),
            "reason": reason,
            "yaml": by_hand_block(self.key, self.held_top or None, self.held_repo),
        }


def _merge(base: Mapping[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def by_hand_lines(by_hand: Mapping[str, str]) -> list[str]:
    """What `init` prints for a store file it would not rewrite."""
    return [
        f"The store's settings were not written: {by_hand['reason']}.",
        (
            f"Add these by hand to {by_hand['path']}, merging the keys under "
            f"store.repos and at the top level:"
        ),
        "",
        by_hand["yaml"].rstrip("\n"),
    ]


def describe(directory: Path, spelled: str) -> str:
    """What is at the existing docs directory, for the offer to show."""
    entries = sorted(
        p.relative_to(directory).as_posix()
        for p in directory.rglob("*")
        if p.is_file()
    )
    if not entries:
        return f"paths.docs, {spelled}, exists and is empty."
    shown = ", ".join(entries[:_LISTED])
    more = f" and {len(entries) - _LISTED} more" if len(entries) > _LISTED else ""
    return f"paths.docs, {spelled}, exists and holds {len(entries)} file(s): {shown}{more}."


def _relative(
    prompter: Prompter, question: str, default: str, within: str, *, new_project: bool = False
) -> str:
    """A path inside ``within``, asked until one is given. For a new project,
    a path that would put the docs project in a content folder is asked
    again too (ADR-020 D8)."""
    while True:
        answer = prompter.ask(question, default)
        spelled = project_relative(answer) if answer else None
        if not spelled or spelled == "." or Path(answer).is_absolute():
            prompter.say(f"{answer!r} is not a path inside {within}: no '..', not absolute.")
            continue
        folder = refused_srcdir(spelled) if new_project else None
        if folder is None:
            return spelled
        prompter.say(
            f"{answer!r} would put invariant's docs project in {folder}/, a folder a "
            f"documentation repository keeps its own content in. A new project's docs "
            f"project goes in {STARTER_SRCDIR}/ or another folder only invariant owns "
            f"(ADR-020 D8)."
        )


def offer_location(
    prompter: Prompter,
    root: Path,
    docs: str,
    *,
    within: str = "the repository",
    new_project: bool = False,
) -> DocsChoice:
    """D4.1: reuse the existing `paths.docs`, or another path, both inside
    ``root``, the home's root: the repository, or the store (ADR-020 D4).

    For a ``new_project``, a path whose docs project would be a content
    folder is refused and asked again (ADR-020 D8); an existing project
    keeps the location it has.
    """
    prompter.say(describe(root / docs, docs))
    options = [(kind, text.format(within=within)) for kind, text in LOCATIONS]
    kind = prompter.choose("Where should invariant's architecture docs live?", options, "reuse")
    if kind == "path":
        chosen = _relative(
            prompter,
            f"paths.docs, relative to {within.removeprefix('the ')}",
            docs,
            within,
            new_project=new_project,
        )
        return DocsChoice("reuse", docs) if chosen == docs else DocsChoice("path", chosen)
    return DocsChoice("reuse", docs)


def place_store(
    top: Path,
    home: HomeStore,
    project: Mapping[str, Any],
    block: Mapping[str, Any],
    store_level: Mapping[str, Any] | None = None,
) -> StorePlacement:
    """Create or link the store ``home`` names, and write this repository's
    block into it (ADR-020 D1): every record's location and `paths.docs`.

    A store created here is this project's, so ``project`` (what `init` would
    have written in-repo, the project and its profile) goes to its own level;
    a store that already existed keeps its own policy, and only this
    repository's block changes. A store file holding comments is not
    rewritten: the settings are held on the placement, and `init` prints
    them to add by hand. ``store_level`` are answers given at the store's
    own level (ADR-017 D4).
    """
    if home.link:
        linked = link_repo(top, home.root)
        root, key, created = linked.store, linked.key, False
    else:
        made = init_store(top, home.root)
        root, key, created = made.store, made.linked.key, True
    lines = [
        f"link the store {root} as {key}"
        if home.link
        else f"create the store {root} and link it as {key}"
    ]
    if home.note:
        lines.append(home.note)
    placement = StorePlacement(root=root, key=key, lines=())
    if created:
        # A store made a moment ago holds no comment of anyone's, so this
        # write always lands.
        write_store_settings(root, key, top=project)
    if store_level:
        lines.append(placement.write_top(store_level))
    lines.append(placement.write(block))
    placement.lines = tuple(lines)
    return placement


def offer_onboarding(prompter: Prompter, assessment: Assessment) -> str:
    """D4.2-3: show the gaps, then all at once, incremental or not now."""
    prompter.say(assessment.render())
    return prompter.choose(
        "The docs have gaps. How should they be closed?", ONBOARDING, "not_now"
    )


def carries_manifest(manifest: Manifest) -> bool:
    """Does the base carry the manifest (ruling 9)? A store project's lives in
    its store; an in-repo project's must be committed, because a session works
    in a worktree cut from a commit and an uncommitted manifest is invisible
    from inside one. One kept local through ``.git/info/exclude`` counts:
    ``session isolate`` copies it into the worktree (ADR-020 D11). The file
    asked about is the manifest's own location (``Manifest.location``,
    ADR-014 D9)."""
    location = manifest.location
    if location.kind == "store":
        return True
    return manifest_tracking(location.tree, location.relpath) in ("committed", "local")


def record_plan(ctx, assessment: Assessment) -> Session | None:
    """D5: the all-at-once plan on a parked `docs`-lane session, or ``None``
    when no gap is one a doc can close.

    ``ctx`` is the CLI's context: its session and pipeline domains record the
    session, its plan and its `docs` pipeline. A task already parked here is
    refused by the session domain, naming it, so a second run cannot fork
    the plan.
    """
    deliverables = plan_deliverables(assessment)
    if not deliverables:
        return None
    session = ctx.sessions.start(PLAN_TASK, parked=True)
    for title in deliverables:
        session = ctx.sessions.plan_add(session.id, title)
    ctx.pipelines.start(
        session.id,
        methodology="docs",
        task="document the architecture",
        session=session.to_dict(),
        cwd=ctx.manifest.root,
    )
    return session


def plan_payload(session: Session | None) -> dict[str, Any] | None:
    if session is None:
        return None
    return {
        "session": session.name,
        "id": session.id,
        "deliverables": [entry["title"] for entry in session.plan],
    }


def plan_lines(session: Session | None) -> list[str]:
    """What a recorded plan says. Never ``session <id> (``: a host binds its
    caller to the session that sequence names, and a parked plan is for a
    later host to pick up."""
    if session is None:
        return ["nothing to plan: no gap is one a doc can close; nothing recorded"]
    return [
        f"plan recorded on the parked docs-lane session {session.name}:",
        *(f"  {line}" for line in plan_reader.render_lines(session.plan)),
        (
            "  each deliverable is written and reviewed through the docs lane "
            "(drafting, reviewing, committed)"
        ),
        f"  pick it up: invariant session resume {session.name}",
    ]


#: What init says when all at once is chosen and the base does not yet carry
#: the manifest (ruling 9).
DEFERRED_PLAN = (
    "commit init's files, then run 'invariant docs assess --plan' to record the "
    "plan on a parked docs-lane session"
)
