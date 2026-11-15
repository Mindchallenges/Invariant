"""A repository's identity: the key a store is found by (ADR-014 D4).

As ruled (5): the normalised origin URL comes first, so a clone on another
machine finds the same store; a repository with no usable origin falls back to
``invariant.id``, a generated value in its local git config, which never
touches a tracked file. ``store link`` writes that ID even when a remote
exists, so a remote added or renamed later is still recognised (``doctor``
says so; nothing re-keys by itself).

Reading costs one file and no subprocess, because the lookup runs on every
manifest load and, from increment 3, in a hook shim (D11): the worktree's
``.git`` file is followed to its git directory, that directory's
``commondir`` to the shared one, and its ``config`` is parsed here. The parser
reads what ``git config`` writes, the last value of a key winning as ``git
config --get`` answers. It does not follow ``include`` directives and does not
apply ``url.<base>.insteadOf`` rewrites: an origin set only through either
reads as absent here, and the repository falls back to its local ID.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import git
from .errors import StateError

#: The local git config key holding the generated ID.
LOCAL_ID_KEY = "invariant.id"

#: Hosts whose repository paths are case-insensitive, so their path is
#: lowercased too (ADR-014 D4, recommended). Every other host keeps its case.
CASE_INSENSITIVE_HOSTS = frozenset({"github.com", "gitlab.com", "bitbucket.org"})

#: ``[user@]host:path``, git's scp-like form: no scheme, and a colon before
#: any slash.
_SCP = re.compile(r"^(?:[^@/:]+@)?(?P<host>[^@/:]+):(?P<path>.*)$")

_SECTION = re.compile(r'^\[\s*(?P<name>[^\s\]"]+)(?:\s+"(?P<sub>(?:[^"\\]|\\.)*)")?\s*\]')
_ESCAPES = {"n": "\n", "t": "\t", "b": "\b", '"': '"', "\\": "\\"}


@dataclass(frozen=True)
class Identity:
    """Who a repository is, as far as a store is concerned."""

    #: The lookup key: the normalised origin, else the local ID.
    key: str
    #: The normalised origin, when the repository has a usable one.
    origin: str | None
    #: ``invariant.id`` from the local git config, when written.
    local_id: str | None

    @property
    def source(self) -> str:
        """``origin`` or ``local``: which of the two the key is."""
        return "origin" if self.origin is not None and self.key == self.origin else "local"


def normalise_origin(url: str | None) -> str | None:
    """``host/path`` for an origin URL, or ``None`` when it names no host.

    The scheme, the userinfo, the port, a trailing ``.git`` and a trailing
    ``/`` are dropped and the host is lowercased; the path is lowercased too
    on the hosts in :data:`CASE_INSENSITIVE_HOSTS`. A local path, a
    ``file://`` URL, or one with no path names a place on one machine rather
    than a repository others can clone, so it is no identity.
    """
    text = (url or "").strip()
    if not text:
        return None
    if "://" in text:
        parts = urlsplit(text)
        host, path = parts.hostname, parts.path
    else:
        match = _SCP.match(text)
        if match is None:
            return None
        host, path = match["host"].lower(), match["path"]
    if not host:
        return None
    path = path.strip("/").removesuffix(".git").strip("/")
    if not path:
        return None
    if host in CASE_INSENSITIVE_HOSTS:
        path = path.lower()
    return f"{host}/{path}"


def common_dir_of(top: Path) -> Path | None:
    """The git common directory of the checkout at ``top``, read from disk.

    ``top/.git`` is the directory itself in a main checkout, and a file
    naming the git directory in a linked worktree, whose ``commondir`` names
    the shared one. ``None`` when ``top`` is no git top level.
    """
    dot_git = Path(top) / ".git"
    if dot_git.is_dir():
        return dot_git.resolve()
    if not dot_git.is_file():
        return None
    try:
        first = dot_git.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return None
    if not first.startswith("gitdir:"):
        return None
    git_dir = (dot_git.parent / first[len("gitdir:"):].strip()).resolve()
    try:
        common = (git_dir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return git_dir
    return (git_dir / common).resolve()


def read_git_config(path: Path) -> dict[tuple[str, str | None], dict[str, str]]:
    """The values of a git config file, keyed by (section, subsection).

    Section and key names are lowercased, as git compares them; a quoted
    subsection keeps its case and the older ``[section.sub]`` form is
    lowercased whole. A key written with no ``=`` is ``"true"``. A file that
    cannot be read is empty.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    found: dict[tuple[str, str | None], dict[str, str]] = {}
    section: tuple[str, str | None] | None = None
    for raw in lines:
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        header = _SECTION.match(line)
        if header:
            name, sub = header["name"].lower(), header["sub"]
            if sub is None and "." in name:
                name, sub = name.split(".", 1)
            section = (name, sub)
            found.setdefault(section, {})
            continue
        if section is None:
            continue
        key, sep, value = line.partition("=")
        key = key.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9-]*", key):
            continue
        found[section][key] = _config_value(value) if sep else "true"
    return found


def _config_value(text: str) -> str:
    """A config value with its quotes, escapes and trailing comment read."""
    out: list[str] = []
    quoted = False
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            out.append(_ESCAPES.get(text[i + 1], text[i + 1]))
            i += 2
            continue
        if char == '"':
            quoted = not quoted
        elif char in "#;" and not quoted:
            break
        else:
            out.append(char)
        i += 1
    return "".join(out).strip()


def repo_identity(top: Path) -> Identity | None:
    """The identity of the repository checked out at ``top``, or ``None``.

    ``None`` means neither a usable origin nor a local ID: such a repository
    can be linked to a store only after ``store link`` writes its ID.
    """
    common = common_dir_of(top)
    if common is None:
        return None
    config = read_git_config(common / "config")
    origin = normalise_origin(config.get(("remote", "origin"), {}).get("url"))
    section, _, key = LOCAL_ID_KEY.partition(".")
    local_id = config.get((section, None), {}).get(key) or None
    if origin is None and local_id is None:
        return None
    return Identity(key=origin or local_id or "", origin=origin, local_id=local_id)


def new_local_id(name: str) -> str:
    """``local:<slug>-<8 hex>``: a fresh ID for a repository named ``name``."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "repo"
    return f"local:{slug}-{secrets.token_hex(4)}"


def write_local_id(top: Path, value: str) -> None:
    """Write ``invariant.id`` into the local git config of ``top``.

    Through git, because a write must keep git's own file format and lock;
    only reading is held to no subprocess.
    """
    written, said = git.set_local_config(Path(top), LOCAL_ID_KEY, value)
    if not written:
        raise StateError(
            f"could not write {LOCAL_ID_KEY} into the git config of {top}: {said}"
        )
