"""Dart import references, the ``imports`` engine for ``.dart`` (ADR-032 D3).

A Dart library's dependencies are its directives, and Dart allows them only
at the top of the file, before the first declaration: ``library``,
``import``, ``export`` and ``part`` (``part of`` names the library a part
belongs to, not a file it uses). So the reader walks the directives and stops
at the first statement that is not one, and never has to lex the rest of the
file, where interpolation, nested braces and every other trap live.

Resolution (DEC_ADR_032_I5): ``dart:`` is the SDK's, outside the project. A
``package:<name>/<path>`` URI is the project's when one of the project's own
``pubspec.yaml`` files names ``<name>``, and resolves to ``<its folder>/lib/
<path>``; any other name is a dependency, outside the project. A relative URI
resolves from the file's folder. A project target that is not a file, a name
two packages claim, another scheme, an interpolated URI and a directive the
reader cannot finish all make the file unanswerable (``None``): a file whose
references are known in part would let a boundary rule pass on the part it
was shown.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator
from pathlib import Path

from .base import Reference

#: The directive keywords that name files; ``library`` names none.
_FILE_DIRECTIVES = frozenset({"import", "export", "part"})
#: Folders a package search never enters: generated output and tool state.
_SKIPPED_FOLDERS = frozenset({"build", "node_modules"})
_NAME = re.compile(r"^name:[ \t]*['\"]?([A-Za-z_][\w]*)['\"]?[ \t]*(?:#.*)?$", re.MULTILINE)
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")


class _Unread(Exception):
    """The directives could not be read to their end."""


def dart_references(path: Path, relative: Path, root: Path) -> list[Reference] | None:
    """Every project file ``relative``'s directives name, one each, in order."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    try:
        directives = list(_directives(text))
    except _Unread:
        return None
    lines = text.splitlines()
    packages: dict[str, str | None] | None = None
    found: list[Reference] = []
    for line, uris in directives:
        for uri in uris:
            if uri.startswith("package:") and packages is None:
                packages = _project_packages(Path(root))
            target = _resolve(uri, relative.parent.as_posix(), Path(root), packages or {})
            if target is None:
                return None
            if target:
                found.append(Reference(target, line, lines[line - 1] if lines else ""))
    return found


# -- reading the directives ---------------------------------------------------------------


class _Scanner:
    """The text from the top, comments skipped, one token at a time."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.i = 0

    def skip_blank(self) -> None:
        text = self.text
        while self.i < len(text):
            if text[self.i].isspace():
                self.i += 1
            elif text.startswith("//", self.i):
                end = text.find("\n", self.i)
                self.i = len(text) if end < 0 else end
            elif text.startswith("/*", self.i):
                self.i = _block_end(text, self.i)
            else:
                return

    def peek(self) -> str:
        self.skip_blank()
        return self.text[self.i] if self.i < len(self.text) else ""

    def word(self) -> str | None:
        self.skip_blank()
        match = _IDENT.match(self.text, self.i)
        if match is None:
            return None
        self.i = match.end()
        return match.group()

    def string(self) -> str | None:
        """A string literal's value, adjacent literals joined; ``None`` if
        no string starts here."""
        parts: list[str] = []
        while _starts_string(self.text, self.at()):
            parts.append(self._one_string())
        return "".join(parts) if parts else None

    def at(self) -> int:
        self.skip_blank()
        return self.i

    def _one_string(self) -> str:
        text = self.text
        raw = text[self.i] == "r"
        self.i += raw
        quote = text[self.i] * 3 if text.startswith(text[self.i] * 3, self.i) else text[self.i]
        self.i += len(quote)
        start = self.i
        while self.i < len(text):
            if text.startswith(quote, self.i):
                value = text[start:self.i]
                self.i += len(quote)
                return value
            char = text[self.i]
            if char == "\n" and len(quote) == 1:
                raise _Unread
            if not raw and char == "\\":
                self.i += 2
                continue
            if not raw and char == "$":
                # An interpolated URI is not a constant the reader can resolve.
                raise _Unread
            self.i += 1
        raise _Unread

    def skip_to_semicolon(self) -> None:
        """Past the rest of a directive: its names, combinators and ``;``."""
        depth = 0
        while True:
            char = self.peek()
            if not char:
                raise _Unread
            if _starts_string(self.text, self.i):
                self._one_string()
            elif char == "(":
                depth += 1
                self.i += 1
            elif char == ")":
                depth -= 1
                self.i += 1
            elif char == ";" and depth == 0:
                self.i += 1
                return
            else:
                self.i += 1


def _directives(text: str) -> Iterator[tuple[int, list[str]]]:
    """``(line, uris)`` per file directive, up to the first declaration."""
    scan = _Scanner(text.removeprefix("\ufeff"))
    if scan.text.startswith("#!"):
        end = scan.text.find("\n")
        scan.i = len(scan.text) if end < 0 else end
    while True:
        _skip_annotations(scan)
        start = scan.at()
        word = scan.word()
        if word not in _FILE_DIRECTIVES and word != "library":
            return
        if word == "library":
            scan.skip_to_semicolon()
            continue
        if word == "part" and _IDENT.match(scan.text, scan.at()) and (
            _IDENT.match(scan.text, scan.i).group() == "of"
        ):
            scan.skip_to_semicolon()
            continue
        yield scan.text.count("\n", 0, start) + 1, _uris(scan)


def _uris(scan: _Scanner) -> list[str]:
    """The directive's URI and, for a conditional import, each other one."""
    first = scan.string()
    if first is None:
        raise _Unread
    uris = [first]
    while True:
        char = scan.peek()
        if not char:
            raise _Unread
        if char == ";":
            scan.i += 1
            return uris
        if _starts_string(scan.text, scan.i):
            value = scan.string()
            if value is not None:
                uris.append(value)
            continue
        if char == "(":
            _skip_parens(scan)
            continue
        scan.i += 1


def _skip_annotations(scan: _Scanner) -> None:
    while scan.peek() == "@":
        scan.i += 1
        if scan.word() is None:
            raise _Unread
        while scan.peek() == ".":
            scan.i += 1
            if scan.word() is None:
                raise _Unread
        if scan.peek() == "(":
            _skip_parens(scan)


def _skip_parens(scan: _Scanner) -> None:
    depth = 0
    while True:
        char = scan.peek()
        if not char:
            raise _Unread
        if _starts_string(scan.text, scan.i):
            scan._one_string()
            continue
        scan.i += 1
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return


def _starts_string(text: str, i: int) -> bool:
    if i >= len(text):
        return False
    if text[i] in "'\"":
        return True
    return (
        text[i] == "r"
        and i + 1 < len(text)
        and text[i + 1] in "'\""
        and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_$"))
    )


def _block_end(text: str, i: int) -> int:
    """Past a block comment opening at ``i``; Dart's nest."""
    depth = 0
    while i < len(text):
        if text.startswith("/*", i):
            depth += 1
            i += 2
        elif text.startswith("*/", i):
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    raise _Unread


# -- resolving a URI -------------------------------------------------------------------------


def _resolve(
    uri: str, folder: str, root: Path, packages: dict[str, str | None]
) -> str | None:
    """The project file ``uri`` names, ``""`` for one outside the project,
    or ``None`` when it cannot be resolved."""
    if uri.startswith("dart:"):
        return ""
    if uri.startswith("package:"):
        name, _, rest = uri[len("package:"):].partition("/")
        if name not in packages:
            return ""
        home = packages[name]
        if home is None or not rest:
            return None
        return _existing(root, posixpath.join(home, "lib", rest) if home else f"lib/{rest}")
    if re.match(r"^[A-Za-z][\w+.-]*:", uri):
        return None
    joined = posixpath.normpath(posixpath.join(folder, uri))
    if joined == ".." or joined.startswith("../") or posixpath.isabs(joined):
        return ""
    return _existing(root, joined)


def _existing(root: Path, rel: str) -> str | None:
    """``rel`` when it is a file inside the project, else ``None``."""
    rel = posixpath.normpath(rel)
    if rel == ".." or rel.startswith("../"):
        return None
    return rel if (root / rel).is_file() else None


def _project_packages(root: Path) -> dict[str, str | None]:
    """Each package name the project's ``pubspec.yaml`` files declare, to its
    folder (``""`` at the root); ``None`` for a name two of them claim."""
    found: dict[str, str | None] = {}
    for pubspec in _pubspecs(root):
        try:
            match = _NAME.search(pubspec.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if match is None:
            continue
        folder = pubspec.parent.relative_to(root).as_posix()
        folder = "" if folder == "." else folder
        name = match.group(1)
        found[name] = folder if name not in found else None
    return found


def _pubspecs(root: Path) -> Iterator[Path]:
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name == "pubspec.yaml" and entry.is_file():
                yield entry
            elif (
                entry.is_dir()
                and not entry.is_symlink()
                and not entry.name.startswith(".")
                and entry.name not in _SKIPPED_FOLDERS
            ):
                stack.append(entry)
