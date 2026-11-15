"""A store kept in step with its remote (ADR-014 D7, increment 5a).

The settings (``sync.*``, read at the store's level with the user's defaults
below it), the pull that session start, resume and ``docs lock`` run first,
its four states, ``store pull --replay``, the same-file check that both the
replay and the release (D6, increment 5b) stop on, the staging half of
the first clone (ruling 18), and the push (D8, increment 6): ``store push``
and push after landing, each publishing only once the store's privacy screen
(``core/storescan``) passed every outgoing commit.

Git is the only sync mechanism (ruling 3). Every call that reaches a remote
runs with no prompt and the configured time limit (``core/git``), and none
runs in a hook: a hook has 15 seconds and must never wait on the network.
Nothing here resolves a conflict (ruling 9): a file changed on both sides
stops the command and is shown.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import git, storescan
from .errors import ManifestError
from .identity import normalise_origin
from .stores import STORE_MANIFEST, read_store_manifest

#: The ruled defaults (ruling 7), in force only in a store project.
SYNC_DEFAULTS: dict[str, Any] = {
    "pull_at_start": True,
    "push_after_landing": False,
    "offline": "warn",
    "diverged": "stop",
    "timeout_seconds": 20,
}

OFFLINE_CHOICES = ("warn", "stop")
#: ``stop`` is the only value in v1 (ruling 9: never auto-resolve).
DIVERGED_CHOICES = ("stop",)

#: The states a pull ends in. ``off``: pull at start is off. ``no_remote``:
#: the store has none, so there is nothing to pull. ``current``, ``behind``
#: (fast-forwarded), ``ahead`` (carried on), ``offline``, ``diverged``;
#: ``replayed`` and ``overlap`` under ``--replay``; ``blocked``, a pull git
#: or the store's own state would not let through, with the reason.
STATES = (
    "off", "no_remote", "current", "behind", "ahead", "offline", "diverged",
    "replayed", "overlap", "blocked",
)


def validate(sync: Any) -> None:
    """Refuse a ``sync`` block that would load and do something else."""
    if not isinstance(sync, Mapping):
        raise ManifestError(f"sync must be a mapping of its settings, got {sync!r}")
    for key in sync:
        if key not in SYNC_DEFAULTS:
            raise ManifestError(
                f"sync.{key} is not a sync setting; use {', '.join(SYNC_DEFAULTS)}"
            )
    for key in ("pull_at_start", "push_after_landing"):
        if key in sync and not isinstance(sync[key], bool):
            raise ManifestError(f"sync.{key} must be true or false, got {sync[key]!r}")
    if "offline" in sync and sync["offline"] not in OFFLINE_CHOICES:
        raise ManifestError(
            f"sync.offline is {sync['offline']!r}; use one of {', '.join(OFFLINE_CHOICES)}"
        )
    if "diverged" in sync and sync["diverged"] not in DIVERGED_CHOICES:
        raise ManifestError(
            f"sync.diverged is {sync['diverged']!r}; stop is the only value "
            f"(ruling 9: a diverged store is never resolved by itself)"
        )
    timeout = sync.get("timeout_seconds", 1)
    if isinstance(timeout, bool) or not isinstance(timeout, int | float) or timeout <= 0:
        raise ManifestError(
            f"sync.timeout_seconds must be a number of seconds above 0, got {timeout!r}"
        )


@dataclass(frozen=True)
class SyncSettings:
    pull_at_start: bool = True
    push_after_landing: bool = False
    offline: str = "warn"
    diverged: str = "stop"
    timeout_seconds: float = 20


def settings(manifest) -> SyncSettings:
    """The resolved ``sync`` block of ``manifest``, its defaults filled in."""
    return settings_of(manifest.get("sync") or {})


def settings_of(block: Mapping[str, Any]) -> SyncSettings:
    """A ``sync`` block as one level writes it, checked, its defaults filled in.

    What ``store push`` reads when it runs inside the store, where no
    repository's configuration resolves: the store's own level.
    """
    validate(block)
    raw = {**SYNC_DEFAULTS, **block}
    return SyncSettings(**{key: raw[key] for key in SYNC_DEFAULTS})


# -- the same-file check (ruling 9) --------------------------------------------


@dataclass(frozen=True)
class Overlap:
    """A file changed on both sides since ``base``, with each side's diff."""

    path: str
    ours: str
    theirs: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "ours": self.ours, "theirs": self.theirs}


def same_file_check(root: Path, base: str, ours: str, theirs: str) -> list[Overlap] | None:
    """Every file changed both from ``base`` to ``ours`` and to ``theirs``.

    The same file, not the same lines: stricter than git, which would merge
    separate hunks of one file, and what ruling 9 says. ``None`` when git
    cannot read one of the three commits, which a caller must treat as a
    stop, never as "no overlap". Used by ``store pull --replay`` and by the
    release (D6).
    """
    mine = git.diff_names(root, base, ours)
    other = git.diff_names(root, base, theirs)
    if mine is None or other is None:
        return None
    overlaps = []
    for path in sorted(set(mine) & set(other)):
        overlaps.append(
            Overlap(
                path=path,
                ours=git.diff_text(root, base, ours, path) or "",
                theirs=git.diff_text(root, base, theirs, path) or "",
            )
        )
    return overlaps


# -- the pull ------------------------------------------------------------------


@dataclass
class Pull:
    """What one pull of a store did, and whether the command must stop."""

    state: str
    store: str
    remote: str | None = None
    upstream: str | None = None
    before: str | None = None
    after: str | None = None
    ahead: int = 0
    behind: int = 0
    #: The reason, for ``offline`` and ``blocked``.
    detail: str = ""
    #: Paths in the way, for ``blocked``: uncommitted changes the incoming
    #: commits touch, or the held units a replay would move.
    paths: list[str] = field(default_factory=list)
    overlaps: list[Overlap] = field(default_factory=list)
    #: Whether the command that pulled must stop here.
    stops: bool = False

    @property
    def moved(self) -> bool:
        """Did the store's main line move (a fast-forward or a replay)?"""
        return self.before is not None and self.after is not None and self.before != self.after

    @property
    def record(self) -> str | None:
        """What a session records as ``store_pull``: ``stale`` after an
        offline pull, ``current`` after one that reached the remote."""
        if self.state == "offline":
            return "stale"
        if self.state in ("current", "behind", "ahead", "replayed"):
            return "current"
        return None

    def lines(self) -> list[str]:
        """What the command says, one fact per line."""
        name, up = self.store, self.upstream or "its remote"
        if self.state == "no_remote":
            return [f"store {name}: no remote, nothing to pull"]
        if self.state == "current":
            return [f"store {name}: in step with its remote ({up})"]
        if self.state == "behind" and self.detail:
            return [
                (
                    f"store {name}: behind its remote by {_commits(self.behind)} "
                    f"({up}); {self.detail}. Run 'invariant store pull' first."
                )
            ]
        if self.state == "behind":
            return [
                (
                    f"store {name}: behind its remote by {_commits(self.behind)}; "
                    f"fast-forwarded to {up}"
                )
            ]
        if self.state == "ahead" and self.detail:
            return [f"store {name}: {self.detail}; carrying on"]
        if self.state == "ahead":
            ahead = _commits(self.ahead, "unpublished ")
            return [f"store {name}: ahead of its remote by {ahead}; carrying on"]
        if self.state == "replayed":
            return [
                (
                    f"store {name}: replayed {_commits(self.ahead, 'unpublished ')} on top "
                    f"of {up}, which brought {_commits(self.behind)}"
                )
            ]
        if self.state == "offline":
            tail = (
                "stopping, as sync.offline: stop says"
                if self.stops
                else "carrying on with the local copy, recorded as a stale pull; "
                "'invariant store pull' tries again"
            )
            return [
                f"store {name}: could not reach its remote {self.remote} "
                + f"({self.detail}); {tail}"
            ]
        if self.state == "diverged":
            return [
                (
                    f"store {name}: diverged from its remote: "
                    f"{_commits(self.ahead, 'unpublished ')} here and "
                    f"{_commits(self.behind)} on {up}, and no branch moved (ruling 9). "
                    f"Run 'invariant store pull --replay' to replay the unpublished "
                    f"commits on top; it stops if a file changed on both sides."
                )
            ]
        if self.state == "overlap":
            out = [
                (
                    f"store {name}: {len(self.overlaps)} file(s) changed on both sides, "
                    f"so nothing was replayed and no branch moved (ruling 9). Resolve "
                    f"each in the store's main checkout, then pull again:"
                )
            ]
            for overlap in self.overlaps:
                out += [
                    f"  {overlap.path}",
                    "    here:",
                    *[f"      {line}" for line in overlap.ours.splitlines()],
                    f"    on {up}:",
                    *[f"      {line}" for line in overlap.theirs.splitlines()],
                ]
            return out
        if self.state == "blocked":
            out = [f"store {name}: not pulled: {self.detail}"]
            out += [f"  {path}" for path in self.paths]
            return out
        return [f"store {name}: pull at start is off (sync.pull_at_start: false)"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "store": self.store,
            "remote": self.remote,
            "upstream": self.upstream,
            "before": self.before,
            "after": self.after,
            "ahead": self.ahead,
            "behind": self.behind,
            "detail": self.detail,
            "paths": list(self.paths),
            "overlaps": [o.to_dict() for o in self.overlaps],
            "stops": self.stops,
        }


def _commits(n: int, kind: str = "") -> str:
    return f"{n} {kind}commit{'s' if n != 1 else ''}"


def _tracking(root: Path) -> tuple[str | None, str | None]:
    """The remote the store's branch follows, and its remote-tracking ref.

    The branch's configured upstream when it has one, else ``origin`` and
    the same branch name; ``(None, None)`` when the store has no remote.
    """
    branch = git.current_branch(root)
    names = git.remotes(root) or []
    if not names:
        return None, None
    remote = (git.config_value(root, f"branch.{branch}.remote") if branch else None) or (
        "origin" if "origin" in names else names[0]
    )
    merge = git.config_value(root, f"branch.{branch}.merge") if branch else None
    target = merge.removeprefix("refs/heads/") if merge else branch
    if not target:
        return remote, None
    return remote, f"refs/remotes/{remote}/{target}"


def pull(
    root: Path,
    sync: SyncSettings,
    *,
    explicit: bool = False,
    replay: bool = False,
    held: Sequence[str] = (),
) -> Pull:
    """Bring the store at ``root`` in step with its remote, as D7 says.

    Implicit pulls (session start, resume, ``docs lock``) honour
    ``sync.pull_at_start``; ``store pull`` is ``explicit``. Behind
    fast-forwards, unless an uncommitted change sits on a path the incoming
    commits touch; ahead carries on; offline warns, or stops under
    ``sync.offline: stop``; diverged stops, unless ``replay``, which replays
    the unpublished commits on top when no file changed on both sides and no
    unit of the docs lock is ``held`` (each named ``unit (session)``).
    """
    root = Path(root)
    name = root.name
    if not explicit and not sync.pull_at_start:
        return Pull(state="off", store=name)
    remote, upstream = _tracking(root)
    if remote is None:
        return Pull(state="no_remote", store=name)
    short = upstream.removeprefix("refs/remotes/") if upstream else None
    out = Pull(state="current", store=name, remote=remote, upstream=short)
    out.before = git.resolve_commit(root, "HEAD")
    out.after = out.before
    fetched, said = git.fetch(root, remote, sync.timeout_seconds)
    if not fetched:
        out.state, out.detail = "offline", said.splitlines()[0] if said else "no answer"
        out.stops = explicit or sync.offline == "stop"
        return out
    if upstream is None or git.resolve_commit(root, upstream) is None:
        # The remote holds no copy of this branch yet: nothing to bring in,
        # and whatever is here is unpublished.
        out.state, out.detail = "ahead", "its remote has no copy of this branch yet"
        return out
    counts = git.ahead_behind(root, upstream)
    if counts is None:
        return _blocked(out, "git could not compare the store with its remote")
    out.ahead, out.behind = counts
    if not out.ahead and not out.behind:
        return out
    if not out.behind:
        out.state = "ahead"
        return out
    if not out.ahead:
        return _fast_forward(root, out, upstream)
    if not replay:
        out.state, out.stops = "diverged", True
        return out
    return _replay(root, out, upstream, held)


def standing(root: Path, sync: SyncSettings) -> Pull:
    """Where the store at ``root`` stands against its remote, moving nothing.

    What ``store migrate`` asks before it writes into a store (ADR-014 D12,
    ADR-020 D3): it fetches, then compares, and never fast-forwards or
    replays. Behind and diverged stop, each naming ``invariant store pull``;
    offline warns, or stops under ``sync.offline: stop``; no remote, in step
    and ahead carry on. Pull at start off still reads: the migrate is not an
    implicit pull, and a store behind its remote must not be written over.
    """
    root = Path(root)
    remote, upstream = _tracking(root)
    if remote is None:
        return Pull(state="no_remote", store=root.name)
    short = upstream.removeprefix("refs/remotes/") if upstream else None
    out = Pull(state="current", store=root.name, remote=remote, upstream=short)
    out.before = out.after = git.resolve_commit(root, "HEAD")
    fetched, said = git.fetch(root, remote, sync.timeout_seconds)
    if not fetched:
        out.state, out.detail = "offline", said.splitlines()[0] if said else "no answer"
        out.stops = sync.offline == "stop"
        return out
    if upstream is None or git.resolve_commit(root, upstream) is None:
        out.state, out.detail = "ahead", "its remote has no copy of this branch yet"
        return out
    counts = git.ahead_behind(root, upstream)
    if counts is None:
        return _blocked(out, "git could not compare the store with its remote")
    out.ahead, out.behind = counts
    if out.behind and out.ahead:
        out.state, out.stops = "diverged", True
    elif out.behind:
        out.state, out.stops = "behind", True
        out.detail = "nothing was moved"
    elif out.ahead:
        out.state = "ahead"
    return out


def _blocked(out: Pull, detail: str, paths: Sequence[str] = ()) -> Pull:
    out.state, out.detail, out.paths, out.stops = "blocked", detail, list(paths), True
    return out


def _fast_forward(root: Path, out: Pull, upstream: str) -> Pull:
    incoming = git.diff_names(root, "HEAD", upstream)
    dirty = git.dirty_paths(root)
    if incoming is None or dirty is None:
        return _blocked(out, "git could not read what the fast-forward would change")
    in_the_way = sorted(set(incoming) & set(dirty))
    if in_the_way:
        return _blocked(
            out,
            f"behind its remote by {_commits(out.behind)}, whose changes touch "
            f"uncommitted changes in the store's main checkout; commit or discard "
            f"them, then pull again",
            in_the_way,
        )
    moved, said = git.fast_forward(root, upstream)
    if not moved:
        return _blocked(out, f"the fast-forward failed: {said}")
    out.state, out.after = "behind", git.resolve_commit(root, "HEAD")
    return out


def _replay(root: Path, out: Pull, upstream: str, held: Sequence[str]) -> Pull:
    if held:
        return _blocked(
            out,
            "a replay rewrites the store's unpublished commits, and these units of "
            "its docs lock are held; replay once they are released",
            held,
        )
    base = git.merge_base(root, upstream, "HEAD")
    overlaps = same_file_check(root, base, "HEAD", upstream) if base else None
    if overlaps is None:
        return _blocked(out, "git could not read the commits the replay compares")
    if overlaps:
        out.state, out.overlaps, out.stops = "overlap", overlaps, True
        return out
    dirty = git.tracked_changes(root)
    if dirty is None or dirty:
        return _blocked(
            out, "the store's main checkout has uncommitted changes; commit them first",
            dirty or [],
        )
    replayed, said = git.rebase(root, upstream)
    if not replayed:
        return _blocked(out, f"the replay failed and was undone: {said}")
    out.state, out.after = "replayed", git.resolve_commit(root, "HEAD")
    return out


# -- the first clone (ruling 18) -------------------------------------------------


def clone_name(remote: str, naming: str) -> str:
    """The folder a cloned store takes, under ruling 14's ``store_naming``.

    ``repo``: the remote's last path segment, ``.git`` dropped. ``url``: the
    normalised remote with slashes as underscores, which a remote naming no
    host (a local path, a ``file://`` URL) does not have, so it takes the
    ``repo`` form.
    """
    if naming == "url":
        origin = normalise_origin(remote)
        if origin:
            return origin.replace("/", "_")
    path = urlsplit(remote).path if "://" in remote else remote.rsplit(":", 1)[-1]
    return path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or "store"


@dataclass(frozen=True)
class Declared:
    """What a store declares that the gates would run, shown before trust."""

    #: ``(dotted key, command, provenance label)``.
    commands: list[tuple[str, str, str]]
    #: ``(lane file, provenance label)``.
    lanes: list[tuple[str, str]]
    repos: list[str]


def declared(root: Path, name: str) -> Declared:
    """Every ``commands.*`` key and lane a store at ``root`` declares, per level.

    The store's own level is labelled ``store:<name>``; each repository's
    block ``store:<name>#<identity>``, as ``config show`` would label them
    (D3). Refused when ``root`` holds no store.
    """
    if not (Path(root) / STORE_MANIFEST).is_file():
        raise ManifestError(
            f"the clone holds no {STORE_MANIFEST}, so it is no store; nothing was registered"
        )
    top, repos = read_store_manifest(root)
    levels = [(f"store:{name}", top)] + [
        (f"store:{name}#{key}", block) for key, block in repos.items()
    ]
    commands: list[tuple[str, str, str]] = []
    lanes: list[tuple[str, str]] = []
    for label, level in levels:
        for key, value in _commands(level.get("commands")):
            commands.append((key, value, label))
        extra = (level.get("methodology") or {}).get("extra_machines") or []
        for lane in extra if isinstance(extra, list) else [extra]:
            lanes.append((str(lane), label))
    return Declared(commands=commands, lanes=lanes, repos=sorted(repos))


def _commands(block: Any) -> list[tuple[str, str]]:
    if not isinstance(block, Mapping):
        return []
    out = []
    for key, value in block.items():
        if isinstance(value, Mapping):
            out += [(f"commands.{key}.{k}", str(v)) for k, v in value.items() if v is not None]
        elif value is not None:
            out.append((f"commands.{key}", str(value)))
    return out


# -- the push (D8, increment 6) ------------------------------------------------------


@dataclass
class Push:
    """What one push of a store did, or why it published nothing."""

    #: ``pushed``; ``current``, nothing to publish; ``no_remote``; ``refused``,
    #: the privacy screen found something or could not read it; ``blocked``,
    #: the push could not be prepared; ``failed``, git did not push.
    state: str
    store: str
    remote: str | None = None
    branch: str | None = None
    #: The commits the push would publish.
    commits: int = 0
    #: Whether the screen read the whole history (``privacy.store.scan: false``
    #: present, ruling 10) rather than the unpublished commits alone.
    whole: bool = False
    detail: str = ""
    screen: storescan.Screened | None = None

    @property
    def published(self) -> bool:
        return self.state in ("pushed", "current")

    def lines(self) -> list[str]:
        name, where = self.store, f"{self.remote}/{self.branch}"
        if self.state == "no_remote":
            return [f"store {name}: no remote, nothing to push"]
        if self.state == "current":
            return [f"store {name}: in step with {where}; nothing to push"]
        if self.state == "pushed":
            return [
                (
                    f"store {name}: pushed {_commits(self.commits)} to {where}, "
                    f"each screened for personal data"
                )
            ]
        if self.state == "refused":
            read = "the whole history" if self.whole else _commits(self.commits, "outgoing ")
            out = [
                (
                    f"store {name}: push refused: the privacy screen read {read} with "
                    f"the store's own privacy block and found what may not leave "
                    f"(ADR-014 D8). Nothing was pushed. Rewrite the commits named below "
                    f"in the store's main checkout, then push again:"
                )
            ]
            return out + (self.screen.lines() if self.screen else [])
        return [f"store {name}: not pushed: {self.detail}"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "store": self.store,
            "remote": self.remote,
            "branch": self.branch,
            "commits": self.commits,
            "whole": self.whole,
            "detail": self.detail,
            "screen": self.screen.to_dict() if self.screen else None,
        }


def push(root: Path, sync: SyncSettings) -> Push:
    """Publish the store's branch to its remote, once the screen passed it.

    Every commit the push would publish is scanned, content and message,
    with the store's privacy block read at that commit; the first push after
    linking a remote reads the whole unpublished history, and while
    ``privacy.store.scan: false`` is present the whole history (ruling 10).
    A finding, or a commit the screen cannot read, refuses the push. The
    store's ``pre-push`` hook screens it again.
    """
    root = Path(root)
    out = Push(state="no_remote", store=root.name)
    remote, upstream = _tracking(root)
    if remote is None:
        return out
    out.remote = remote
    branch = git.current_branch(root)
    if branch is None:
        out.state, out.detail = "blocked", "the store has no branch checked out"
        return out
    out.branch = upstream.removeprefix(f"refs/remotes/{remote}/") if upstream else branch
    pending = storescan.outgoing(root, "HEAD", remote)
    if pending is None:
        out.state, out.detail = "blocked", "git cannot list the commits a push would publish"
        return out
    if not pending:
        out.state = "current"
        return out
    out.whole = storescan.relaxation(root).declared
    commits = storescan.outgoing(root, "HEAD", remote, whole=True) if out.whole else pending
    out.commits = len(pending)
    out.screen = storescan.scan_commits(root, commits or [])
    if commits is None:
        out.screen.problem = "git cannot list the store's history"
    if not out.screen.clean:
        out.state = "refused"
        return out
    pushed, said = git.push(root, remote, f"HEAD:refs/heads/{out.branch}", sync.timeout_seconds)
    if not pushed:
        out.state, out.detail = "failed", said
        return out
    out.state = "pushed"
    return out
