"""TypeScript and JavaScript import references, the ``imports`` engine (ADR-032 D3).

Two halves (DEC_ADR_032_I5). :func:`_tokens` lexes a file far enough to tell
code from comments, strings, template literals, regular expressions and, in
``.tsx`` and ``.jsx``, JSX text: a quote inside any of those is never read as
code. A file the lexer cannot finish is unanswerable, never read in part.
Then every static ``import``, ``export ... from``, ``require('x')`` and
``import('x')`` whose specifier is a string literal is a reference; a
``require`` or ``import()`` of anything else names no file the engine can see.

Resolution follows TypeScript's: a relative specifier from the file's folder;
any other through the nearest ``tsconfig.json`` (else ``jsconfig.json``) at
or above the file, its ``compilerOptions.paths`` by the longest matching
prefix, then its ``baseUrl``. A bare specifier nothing resolves is a
dependency or a ``node:`` builtin, outside the project (DN-A2). A relative
specifier or a project alias that resolves to no file makes the file
unanswerable.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .base import Reference

#: The suffixes a module may be written without, in TypeScript's order.
_EXTENSIONS = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs")
#: The TypeScript source an emitted-name specifier stands for.
_SOURCE_FOR = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
#: The files that configure resolution, nearest first, ``tsconfig`` preferred.
_CONFIGS = ("tsconfig.json", "jsconfig.json")
_JSX_SUFFIXES = frozenset({".tsx", ".jsx"})

#: After these words an expression starts, so ``/`` opens a regular expression.
_EXPRESSION_KEYWORDS = frozenset({
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw",
    "case", "do", "else", "yield", "await", "extends",
})
#: After these punctuators a value ended, so ``/`` divides.
_VALUE_END = frozenset({")", "]", "}", "++", "--"})
_IDENT = re.compile(r"[A-Za-z_$\u0080-\uffff][\w$\u0080-\uffff]*")
_NUMBER = re.compile(r"\d[\w.]*(?:[eE][+-]\d+)?|\.\d[\w]*")
_JSX_NAME = re.compile(r"[A-Za-z_$][\w$.:-]*")
_JSX_ATTRIBUTE = re.compile(r"[A-Za-z_$][\w$:-]*")


class _Unread(Exception):
    """The lexer could not finish the file."""


@dataclass(frozen=True)
class _Token:
    kind: str  # name, string, template, regex, number, punct
    value: str
    line: int


def node_references(path: Path, relative: Path, root: Path) -> list[Reference] | None:
    """Every project file ``relative`` imports, one :class:`Reference` each."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    try:
        tokens = _Lexer(text, jsx=relative.suffix in _JSX_SUFFIXES).run()
    except _Unread:
        return None
    lines = text.splitlines()
    resolver = _Resolver(Path(root), relative.parent.as_posix())
    found: list[Reference] = []
    for specifier, line in _specifiers(tokens):
        target = resolver.resolve(specifier)
        if target is None:
            return None
        if target:
            found.append(Reference(target, line, lines[line - 1] if lines else ""))
    return found


# -- the lexer --------------------------------------------------------------------------------


class _Lexer:
    """Significant tokens of a file, comments dropped; :class:`_Unread` when
    a comment, string, template, regular expression or JSX element is not
    closed where the language says it must be."""

    def __init__(self, text: str, *, jsx: bool) -> None:
        self.text = text.removeprefix("\ufeff")
        self.jsx = jsx
        self.i = 0
        self.line = 1
        self.tokens: list[_Token] = []

    def run(self) -> list[_Token]:
        if self.text.startswith("#!"):
            self._skip_to_line_end()
        self._code(until_brace=False)
        return self.tokens

    # -- code ------------------------------------------------------------------------

    def _code(self, *, until_brace: bool) -> None:
        """Code until the end, or past the ``}`` that closes the caller's ``{``."""
        text = self.text
        depth = 0
        while self.i < len(text):
            char = text[self.i]
            if char == "\n":
                self.line += 1
                self.i += 1
            elif char.isspace():
                self.i += 1
            elif text.startswith("//", self.i):
                self._skip_to_line_end()
            elif text.startswith("/*", self.i):
                self._block_comment()
            elif char in "'\"":
                self._emit("string", self._quoted(char))
            elif char == "`":
                self._template()
            elif char == "/":
                if self._expression_expected():
                    self._regex()
                else:
                    self._punct(1)
            elif char == "<" and self.jsx and self._expression_expected() and self._jsx_starts():
                self._jsx_element()
                self._emit("punct", ")")  # an element is a value
            elif char == "{":
                depth += 1
                self._punct(1)
            elif char == "}":
                if until_brace and depth == 0:
                    self.i += 1
                    return
                depth -= 1
                self._punct(1)
            elif char == "#" or _IDENT.match(text, self.i):
                match = _IDENT.match(text, self.i + (char == "#"))
                if match is None:
                    raise _Unread
                self._emit("name", text[self.i:match.end()])
                self.i = match.end()
            elif char.isdigit() or (char == "." and text[self.i + 1:self.i + 2].isdigit()):
                match = _NUMBER.match(text, self.i)
                assert match is not None
                self._emit("number", match.group())
                self.i = match.end()
            elif text.startswith(("++", "--"), self.i):
                self._punct(2)
            else:
                self._punct(1)
        if until_brace:
            raise _Unread

    def _expression_expected(self) -> bool:
        """Does a value start here, so ``/`` is a regex and ``<`` may be JSX?"""
        if not self.tokens:
            return True
        last = self.tokens[-1]
        if last.kind == "name":
            return last.value in _EXPRESSION_KEYWORDS
        if last.kind == "punct":
            return last.value not in _VALUE_END
        return False

    def _emit(self, kind: str, value: str) -> None:
        self.tokens.append(_Token(kind, value, self.line))

    def _punct(self, width: int) -> None:
        self._emit("punct", self.text[self.i:self.i + width])
        self.i += width

    def _skip_to_line_end(self) -> None:
        end = self.text.find("\n", self.i)
        self.i = len(self.text) if end < 0 else end

    def _block_comment(self) -> None:
        end = self.text.find("*/", self.i + 2)
        if end < 0:
            raise _Unread
        self.line += self.text.count("\n", self.i, end)
        self.i = end + 2

    def _quoted(self, quote: str) -> str:
        """A ``'`` or ``"`` string's raw body; a line end inside it is unread."""
        text = self.text
        start = self.i + 1
        self.i = start
        while self.i < len(text):
            char = text[self.i]
            if char == quote:
                self.i += 1
                return _unescaped(text[start:self.i - 1])
            if char == "\\":
                if text[self.i + 1:self.i + 2] == "\n":
                    self.line += 1
                self.i += 2
                continue
            if char == "\n":
                raise _Unread
            self.i += 1
        raise _Unread

    def _template(self) -> None:
        """A template literal; one with no substitution is a plain string."""
        text = self.text
        start_line = self.line
        self.i += 1
        start = self.i
        plain = True
        while self.i < len(text):
            char = text[self.i]
            if char == "`":
                self.i += 1
                body = _unescaped(text[start:self.i - 1]) if plain else ""
                self.tokens.append(_Token("string" if plain else "template", body, start_line))
                return
            if char == "\\":
                if text[self.i + 1:self.i + 2] == "\n":
                    self.line += 1
                self.i += 2
                continue
            if text.startswith("${", self.i):
                plain = False
                self.i += 2
                self._emit("punct", "(")  # the substitution's code is its own
                self._code(until_brace=True)
                self._emit("punct", ")")
                continue
            if char == "\n":
                self.line += 1
            self.i += 1
        raise _Unread

    def _regex(self) -> None:
        text = self.text
        self.i += 1
        in_class = False
        while self.i < len(text):
            char = text[self.i]
            if char == "\n":
                raise _Unread
            if char == "\\":
                self.i += 2
                continue
            if char == "[":
                in_class = True
            elif char == "]":
                in_class = False
            elif char == "/" and not in_class:
                self.i += 1
                while self.i < len(text) and (text[self.i].isalnum() or text[self.i] == "$"):
                    self.i += 1
                self._emit("regex", "")
                return
            self.i += 1
        raise _Unread

    # -- JSX --------------------------------------------------------------------------

    def _jsx_starts(self) -> bool:
        """``<`` opening an element or a fragment, not a generic's ``<T,>``."""
        text = self.text
        after = self.i + 1
        if text[after:after + 1] == ">":
            return True
        match = _JSX_NAME.match(text, after)
        if match is None:
            return False
        rest = text[match.end():].lstrip()
        return not (rest.startswith(",") or re.match(r"extends\b", rest))

    def _jsx_element(self) -> None:
        """An element at ``<``: its attributes, and its children unless it
        closes itself."""
        self.i += 1
        if self.text[self.i:self.i + 1] == ">":
            self.i += 1
            self._jsx_children()
            return
        match = _JSX_NAME.match(self.text, self.i)
        if match is None:
            raise _Unread
        self.i = match.end()
        while True:
            self._jsx_blank()
            text = self.text
            if text.startswith("/>", self.i):
                self.i += 2
                return
            char = text[self.i:self.i + 1]
            if char == ">":
                self.i += 1
                self._jsx_children()
                return
            if char == "{":
                self.i += 1
                self._code(until_brace=True)
                continue
            attribute = _JSX_ATTRIBUTE.match(text, self.i)
            if attribute is None:
                raise _Unread
            self.i = attribute.end()
            self._jsx_blank()
            if text[self.i:self.i + 1] == "=":
                self.i += 1
                self._jsx_blank()
                self._jsx_value()

    def _jsx_value(self) -> None:
        text = self.text
        char = text[self.i:self.i + 1]
        if char in ("'", '"'):
            end = text.find(char, self.i + 1)
            if end < 0:
                raise _Unread
            self.line += text.count("\n", self.i, end)
            self.i = end + 1
        elif char == "{":
            self.i += 1
            self._code(until_brace=True)
        elif char == "<":
            self._jsx_element()
        else:
            raise _Unread

    def _jsx_children(self) -> None:
        """Text, expression containers and elements, to the closing tag."""
        text = self.text
        while self.i < len(text):
            char = text[self.i]
            if char == "<":
                if text.startswith("</", self.i):
                    end = text.find(">", self.i)
                    if end < 0:
                        raise _Unread
                    self.i = end + 1
                    return
                self._jsx_element()
            elif char == "{":
                self.i += 1
                self._code(until_brace=True)
            else:
                if char == "\n":
                    self.line += 1
                self.i += 1
        raise _Unread

    def _jsx_blank(self) -> None:
        text = self.text
        while self.i < len(text):
            if text[self.i] == "\n":
                self.line += 1
                self.i += 1
            elif text[self.i].isspace():
                self.i += 1
            elif text.startswith("//", self.i):
                self._skip_to_line_end()
            elif text.startswith("/*", self.i):
                self._block_comment()
            else:
                return
        raise _Unread


def _unescaped(body: str) -> str:
    """A literal's value for a module specifier: the simple escapes undone."""
    return re.sub(r"\\(.)", r"\1", body, flags=re.DOTALL)


# -- the import forms --------------------------------------------------------------------------


def _specifiers(tokens: list[_Token]) -> Iterator[tuple[str, int]]:
    """``(specifier, line)`` for every form that names a module by a literal."""
    for index, token in enumerate(tokens):
        if token.kind != "name" or token.value not in ("import", "export", "require"):
            continue
        before = tokens[index - 1] if index else None
        if before is not None and before.kind == "punct" and before.value in (".", "?."):
            continue  # a member: loader.require, x.import
        after = tokens[index + 1] if index + 1 < len(tokens) else None
        if after is None:
            continue
        if token.value == "export":
            found = _reexport(tokens, index + 1)
        elif after.kind == "punct" and after.value == "(":
            found = _called(tokens, index + 2)
        elif token.value == "import":
            found = _static_import(tokens, index + 1)
        else:
            found = None
        if found is not None:
            yield found, token.line


def _called(tokens: list[_Token], index: int) -> str | None:
    """``require('x')`` and ``import('x')``: the literal argument, if it is one."""
    if index + 1 >= len(tokens):
        return None
    argument, closing = tokens[index], tokens[index + 1]
    if argument.kind == "string" and closing.kind == "punct" and closing.value in (")", ","):
        return argument.value
    return None


def _static_import(tokens: list[_Token], index: int) -> str | None:
    """``import 'x'``, or the bindings, then ``from 'x'``."""
    first = tokens[index]
    if first.kind == "string":
        return first.value
    if first.kind == "punct" and first.value not in ("{", "*"):
        return None  # import.meta, an `import` property key
    return _from_clause(tokens, index)


def _reexport(tokens: list[_Token], index: int) -> str | None:
    """``export {..} from 'x'``, ``export * [as n] from 'x'``, with ``type``."""
    if index < len(tokens) and tokens[index].kind == "name" and tokens[index].value == "type":
        index += 1
    if index >= len(tokens) or tokens[index].value not in ("{", "*"):
        return None
    return _from_clause(tokens, index)


def _from_clause(tokens: list[_Token], index: int) -> str | None:
    """The literal after ``from`` that ends this statement's bindings."""
    depth = 0
    for position in range(index, len(tokens)):
        token = tokens[position]
        if token.kind == "punct":
            if token.value == "{":
                depth += 1
            elif token.value == "}":
                depth -= 1
            elif depth == 0 and token.value in (";", "=", "("):
                return None
        elif depth == 0 and token.kind == "name" and token.value == "from":
            following = tokens[position + 1] if position + 1 < len(tokens) else None
            if following is not None and following.kind == "string":
                return following.value
            return None
    return None


# -- resolution ------------------------------------------------------------------------------


class _Resolver:
    """A specifier to a project file (``str``), outside the project (``""``),
    or unresolvable (``None``), as TypeScript resolves it from one folder."""

    def __init__(self, root: Path, folder: str) -> None:
        self.root = root
        self.folder = "" if folder == "." else folder
        self._config: _Config | None = None
        self._read = False

    def resolve(self, specifier: str) -> str | None:
        if specifier in (".", "..") or specifier.startswith(("./", "../")):
            joined = posixpath.normpath(posixpath.join(self.folder or ".", specifier))
            if joined == ".." or joined.startswith("../"):
                return ""
            return self._module(joined)
        config = self.config()
        return None if config is None else config.resolve(specifier, self._module)

    def config(self) -> _Config | None:
        """The nearest config (:data:`_NO_CONFIG` where there is none), read
        once; ``None`` for one that does not read."""
        if not self._read:
            self._config, self._read = _nearest_config(self.root, self.folder), True
        return self._config

    def _module(self, rel: str) -> str | None:
        """The file a module path names, by TypeScript's extension rules."""
        rel = posixpath.normpath(rel)
        if rel == ".." or rel.startswith("../"):
            return None
        base = "" if rel == "." else rel
        candidates = [base] if base else []
        stem, suffix = posixpath.splitext(base)
        candidates += [stem + source for source in _SOURCE_FOR.get(suffix, ())]
        candidates += [base + extension for extension in _EXTENSIONS if base]
        index = posixpath.join(base, "index") if base else "index"
        candidates += [index + extension for extension in _EXTENSIONS]
        return next((c for c in candidates if (self.root / c).is_file()), None)


@dataclass(frozen=True)
class _Config:
    """What a config chain says about resolution: ``baseUrl`` and ``paths``,
    each with the folder it is read from, project-relative."""

    base_url: str | None
    paths: dict[str, list[str]]
    paths_base: str

    def resolve(self, specifier: str, module) -> str | None:
        pattern = _longest_match(self.paths, specifier)
        if pattern is not None:
            star = specifier[len(pattern.partition("*")[0]):]
            if "*" in pattern:
                star = star[: len(star) - len(pattern.partition("*")[2])]
            base = self.base_url if self.base_url is not None else self.paths_base
            for substitution in self.paths[pattern]:
                if not isinstance(substitution, str):
                    continue
                target = module(posixpath.join(base or ".", substitution.replace("*", star)))
                if target is not None:
                    return target
            if pattern != "*":
                return None
        if self.base_url is not None:
            target = module(posixpath.join(self.base_url or ".", specifier))
            if target is not None:
                return target
        return ""


#: No config: no alias and no ``baseUrl``, so every bare specifier is outside.
_NO_CONFIG = _Config(base_url=None, paths={}, paths_base="")


def _longest_match(paths: dict[str, list[str]], specifier: str) -> str | None:
    best: str | None = None
    for pattern in paths:
        prefix, star, suffix = pattern.partition("*")
        if star:
            matched = (
                specifier.startswith(prefix)
                and specifier.endswith(suffix)
                and len(specifier) >= len(prefix) + len(suffix)
            )
        else:
            matched = specifier == pattern
        if matched and (best is None or len(prefix) > len(best.partition("*")[0])):
            best = pattern
    return best


def _nearest_config(root: Path, folder: str) -> _Config | None:
    parts = folder.split("/") if folder else []
    while True:
        here = "/".join(parts)
        for name in _CONFIGS:
            rel = posixpath.join(here, name) if here else name
            if (root / rel).is_file():
                return _read_chain(root, rel, set())
        if not parts:
            return _NO_CONFIG
        parts.pop()


def _read_chain(root: Path, rel: str, seen: set[str]) -> _Config | None:
    """The config at ``rel`` over the relative configs it extends."""
    if rel in seen:
        return None
    seen.add(rel)
    raw = _read_jsonc(root / rel)
    if not isinstance(raw, dict):
        return None
    folder = posixpath.dirname(rel)
    merged = _Config(base_url=None, paths={}, paths_base=folder)
    extends = raw.get("extends")
    for parent in [extends] if isinstance(extends, str) else extends or []:
        if not isinstance(parent, str) or not parent.startswith(("./", "../")):
            continue  # a package's config: it names no project folder
        target = posixpath.normpath(posixpath.join(folder or ".", parent))
        if not target.endswith(".json"):
            target += ".json"
        if target.startswith("../") or not (root / target).is_file():
            return None
        inherited = _read_chain(root, target, seen)
        if inherited is None:
            return None
        merged = inherited
    options = raw.get("compilerOptions")
    if not isinstance(options, dict):
        return merged
    base_url, paths, paths_base = merged.base_url, merged.paths, merged.paths_base
    if isinstance(options.get("baseUrl"), str):
        base_url = posixpath.normpath(posixpath.join(folder or ".", options["baseUrl"]))
        base_url = "" if base_url == "." else base_url
    if isinstance(options.get("paths"), dict):
        paths, paths_base = options["paths"], folder
    return _Config(base_url=base_url, paths=paths, paths_base=paths_base)


def _read_jsonc(path: Path) -> object:
    """JSON with comments and trailing commas, as ``tsconfig.json`` allows."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    out: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == '"':
            end = i + 1
            while end < len(text) and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            out.append(text[i:end + 1])
            i = end + 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
        else:
            out.append(char)
            i += 1
    cleaned = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    try:
        return json.loads(cleaned)
    except ValueError:
        return None
