"""The sphinx adapter (ADR-015 D1, pulled forward by ADR-018 increment 2).

A project whose own content is a Sphinx project: a documentation repository.
It has no unit tests and no code to measure, so the adapter's test is a strict
reading of the documents and its coverage is declared not applicable, by
name, instead of being quietly skipped.

- **Detection** looks for a ``conf.py`` at the root or under ``docs/``,
  ``doc/`` or ``source/`` (each also one ``source/`` deeper), and skips the
  docs project's source directory (ADR-018 D3): the nearest directory at or
  above ``paths.docs`` that holds a ``conf.py``. A project whose only Sphinx
  tree is its docs project is not a ``sphinx`` ecosystem.
- **Commands** are strict: ``-E`` re-reads every source, ``-W`` turns a
  warning into a failure, ``-n`` is nitpicky and ``--keep-going`` reports
  every warning before failing. Output goes into the per-run result bundle
  under the state root, never into the tree. ``{srcdir}`` is a ``vars`` entry
  ``init`` fills in (:meth:`SphinxAdapter.init_settings`), with the needs
  builder added to the test when ``conf.py`` configures ``sphinx_needs``.
- **A run** is read line by line: every ``WARNING:``, ``ERROR:`` and
  ``CRITICAL:`` line and every linkcheck ``broken`` line is a failure, named
  by document, line and warning type.
- **Hazards**: a build without ``-W`` or without ``-E`` can overstate; a
  network failure in linkcheck can only understate. ``conf.py`` settings
  that narrow what passes are the project's stated choice, so they are noted
  on the run (:meth:`SphinxAdapter.annotate`), never a hazard.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar

from .base import Adapter, Hazard, TestResult

#: The default ``paths.docs``, which locates the docs project's source
#: directory (ADR-018 D3). Spelled here because an adapter imports nothing
#: from the engine; a test holds it equal to the manifest's default.
DEFAULT_DOCS = "docs/architecture"

#: Where a documentation repository's ``conf.py`` is looked for, in order.
_HOMES = ("", "docs", "docs/source", "doc", "doc/source", "source")

_STRICT = "sphinx-build -E -W --keep-going -n"
_LINKCHECK = (
    f"{_STRICT} -b linkcheck -D linkcheck_retries=2 {{srcdir}} {{result_bundle}}/linkcheck"
)
_NEEDS = f"{_STRICT} -b needs {{srcdir}} {{result_bundle}}/needs"

#: Documents Sphinx reads in a source directory that also holds other code.
_DOCUMENT_SUFFIXES = (".rst", ".md", ".txt", ".inc")
#: Directories of a source directory that are Sphinx's own.
_SPHINX_DIRS = frozenset({"_static", "_templates"})

#: Settings in ``conf.py`` whose patterns narrow what a strict build refuses;
#: ``linkcheck_anchors = False`` is the one boolean that does.
_NARROWING = ("linkcheck_ignore", "nitpick_ignore", "nitpick_ignore_regex", "suppress_warnings")

_DIAGNOSTIC = re.compile(
    r"^(?:(?P<document>.*?):(?:(?P<line>\d+):)? )?"
    r"(?P<level>WARNING|ERROR|CRITICAL|SEVERE): (?P<message>.*?)\s*$"
)
_TYPE = re.compile(r"\s*\[(?P<type>[a-z][\w.-]*)\]\s*$")
_BROKEN = re.compile(r"^\(\s*(?P<document>.+?): line\s+(?P<line>\d+)\)\s+broken\s+(?P<uri>\S+)")
_DOCUMENTS = re.compile(r"updating environment: (?:\[[^\]]*\] )?(\d+) added, (\d+) changed")

#: `init`'s notes, each naming what it could not decide and what to set.
_SET_SRCDIR = "set vars.srcdir to {which}"
_NO_TREE = (
    "sphinx: no conf.py at the root or under docs/, doc/ or source/; "
    + _SET_SRCDIR.format(which="the directory the project builds")
)
_UNREAD_EXTENSIONS = (
    "sphinx: could not read the extensions in {conf}; the test runs linkcheck "
    "only. Add the needs builder step to commands.test if it configures sphinx_needs"
)

#: How many failures a run's note names before it counts the rest.
_NAMED = 8


@dataclass(frozen=True)
class Diagnostic:
    """One warning or error a Sphinx run printed."""

    document: str | None
    line: int | None
    level: str
    type: str | None
    message: str

    def render(self) -> str:
        where = self.document or ""
        if where and self.line is not None:
            where = f"{where}:{self.line}"
        kind = f"[{self.type}] " if self.type else ""
        return f"{where} {kind}{self.message}".strip() if where else f"{kind}{self.message}"


def diagnostics(text: str) -> list[Diagnostic]:
    """Every ``WARNING:``, ``ERROR:``, ``CRITICAL:`` or ``SEVERE:`` line, in order.

    The warning type (``[ref.doc]``, ``[needs.link_outgoing]``) closes the
    message. A docutils message quotes the offending source on the lines
    after it, and its type closes the last of them, so the type is read from
    the first line that carries one before the next diagnostic.
    """
    lines = text.splitlines()
    found: list[Diagnostic] = []
    for index, raw in enumerate(lines):
        match = _DIAGNOSTIC.match(raw)
        if not match:
            continue
        message = match.group("message")
        kind = _TYPE.search(message)
        if kind:
            message = message[: kind.start()]
        else:
            for later in lines[index + 1 : index + 6]:
                if _DIAGNOSTIC.match(later):
                    break
                kind = _TYPE.search(later)
                if kind:
                    break
        line = match.group("line")
        found.append(
            Diagnostic(
                document=match.group("document") or None,
                line=int(line) if line else None,
                level=match.group("level"),
                type=kind.group("type") if kind else None,
                message=message.strip(),
            )
        )
    return found


@dataclass(frozen=True)
class ConfFacts:
    """What a ``conf.py`` says, read without running it.

    ``extensions`` is ``None`` when they cannot be told: the file is absent,
    does not parse, or builds the list at run time. ``narrowing`` names each
    setting that narrows what a strict build refuses, with its count.
    """

    extensions: tuple[str, ...] | None
    narrowing: tuple[str, ...]


def conf_facts(path: Path) -> ConfFacts:
    """Read ``extensions`` and the narrowing settings from ``path``'s literals."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        return ConfFacts(extensions=None, narrowing=())
    extensions: list[str] | None = []
    narrowing: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id == "extensions":
                    extensions = _extend(extensions, node.value)
                else:
                    said = _narrows(target.id, node.value)
                    if said is not None:
                        narrowing[target.id] = said
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "extensions"
            and node.func.attr in ("append", "extend")
            and len(node.args) == 1
        ):
            arg = node.args[0]
            value = ast.List(elts=[arg], ctx=ast.Load()) if node.func.attr == "append" else arg
            extensions = _extend(extensions, value)
    order = ("linkcheck_ignore", "linkcheck_anchors", *_NARROWING[1:])
    return ConfFacts(
        extensions=None if extensions is None else tuple(extensions),
        narrowing=tuple(narrowing[name] for name in order if name in narrowing),
    )


def _extend(known: list[str] | None, value: ast.expr) -> list[str] | None:
    if known is None or not isinstance(value, (ast.List, ast.Tuple)):
        return None
    names = [elt.value for elt in value.elts if isinstance(elt, ast.Constant)]
    if len(names) != len(value.elts) or not all(isinstance(n, str) for n in names):
        return None
    return known + [str(name) for name in names]


def _narrows(name: str, value: ast.expr) -> str | None:
    """How the setting ``name = value`` narrows a strict build, or ``None``."""
    if name == "linkcheck_anchors":
        if isinstance(value, ast.Constant) and value.value is False:
            return "linkcheck_anchors = False"
        return None
    if name not in _NARROWING:
        return None
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        count = len(value.elts)
        if not count:
            return None
        return f"{name} ({count} pattern{'' if count == 1 else 's'})"
    return f"{name} (not a literal; count unknown)"


def docs_project_srcdir(root: Path, docs: str = DEFAULT_DOCS) -> Path | None:
    """The docs project's source directory (ADR-018 D3), or ``None``.

    The nearest directory at or above ``root / docs`` that holds a
    ``conf.py``, strictly below ``root``. Until ``docs.needs.srcdir`` is a key
    (ADR-018 increment 3) this default rule is the only one.

    The root itself is never the docs project's: the starter writes it at
    ``docs/`` (D3), and a ``conf.py`` at the root is a documentation
    repository's own content (ADR-015 D1), which ruling 18 keeps apart from
    the docs project. Reading the rule up to the root would take that
    content for the docs project and detect nothing.
    """
    root = Path(root)
    current = root / docs
    while current != root and root in current.parents:
        if (current / "conf.py").is_file():
            return current
        current = current.parent
    return None


class SphinxAdapter(Adapter):
    """A Sphinx project's own content: strict builds, no coverage."""

    id = "sphinx"

    coverage_applicable: ClassVar[bool] = False
    coverage_reason: ClassVar[str] = "a documentation build executes no measurable lines"

    manifest_defaults: ClassVar[dict[str, Any]] = {
        "commands": {
            "build": f"{_STRICT} -b html {{srcdir}} {{result_bundle}}/html",
            "test": _LINKCHECK,
            "test_one": f"{_LINKCHECK} {{selector}}",
            "lint": None,
            "coverage": None,
            "coverage_one": None,
            "coverage_report": None,
        },
    }

    # -- where the tree is ------------------------------------------------

    def source_dirs(self, root: Path, *, skip_docs_project: bool = False) -> list[Path]:
        """Every directory at a known home holding a ``conf.py``, in order."""
        root = Path(root)
        skipped = docs_project_srcdir(root) if skip_docs_project else None
        found: list[Path] = []
        for home in _HOMES:
            candidate = root / home if home else root
            if candidate != skipped and (candidate / "conf.py").is_file():
                found.append(candidate)
        return found

    def detect(self, root: Path) -> bool:
        return bool(self.source_dirs(root, skip_docs_project=True))

    def source_dir(self, root: Path) -> Path | None:
        """The one source directory this project builds, or ``None``.

        The docs project's is skipped, unless it is the only tree there, which
        is a project declared ``sphinx`` by hand. Two candidates are asked
        about, not guessed between.
        """
        content = self.source_dirs(root, skip_docs_project=True)
        if not content:
            content = self.source_dirs(root)
        return content[0] if len(content) == 1 else None

    def claims(self, relative: Path, root: Path) -> bool:
        """A file under a Sphinx source directory: a document, ``conf.py``,
        a template, a static file, an extension.

        With ``conf.py`` at the root, only what Sphinx itself owns there is
        claimed, so a stacked ecosystem's code keeps its floor (ADR-013): a
        claim sets a file aside from coverage, the relaxing direction.
        """
        root = Path(root)
        for srcdir in self.source_dirs(root):
            try:
                inner = (root / relative).relative_to(srcdir)
            except ValueError:
                continue
            if srcdir != root:
                return True
            if (
                inner == Path("conf.py")
                or inner.suffix in _DOCUMENT_SUFFIXES
                or inner.parts[0] in _SPHINX_DIRS
            ):
                return True
        return False

    # -- running -----------------------------------------------------------

    def owns_selector(self, selector: str) -> bool:
        """A document is this ecosystem's to run: ``.rst``, or MyST ``.md``."""
        return Path(selector).suffix in (".rst", ".md")

    def join_selectors(self, selectors: Sequence[str]) -> str | None:
        """`sphinx-build` takes the documents to write as trailing arguments."""
        return " ".join(selectors)

    def test_scope_of(self, extra_tokens: Sequence[str]) -> str | None:
        """No filenames after the command is the whole tree; filenames alone,
        a subset; an option, doubt."""
        tokens = list(extra_tokens)
        if not tokens:
            return "full"
        return None if any(token.startswith("-") for token in tokens) else "scoped"

    def init_settings(self, root: Path) -> tuple[dict[str, Any], list[str]]:
        """``vars.srcdir``, and the needs builder when ``conf.py`` names
        ``sphinx_needs`` (ADR-015 D1: "``init`` fills in")."""
        root = Path(root)
        srcdir = self.source_dir(root)
        if srcdir is None:
            found = self.source_dirs(root, skip_docs_project=True)
            if not found:
                return {}, [_NO_TREE]
            names = ", ".join(_spelled(root, path) for path in found)
            return {}, [
                f"sphinx: several source directories ({names}); "
                + _SET_SRCDIR.format(which="the one this project builds")
            ]
        settings: dict[str, Any] = {"vars": {"srcdir": _spelled(root, srcdir)}}
        conf = srcdir / "conf.py"
        extensions = conf_facts(conf).extensions
        if extensions is None:
            return settings, [_UNREAD_EXTENSIONS.format(conf=_spelled(root, conf))]
        if "sphinx_needs" in extensions:
            settings["commands"] = {
                "test": f"{_LINKCHECK} && {_NEEDS}",
                "test_one": f"{_LINKCHECK} {{selector}} && {_NEEDS} {{selector}}",
            }
        return settings, []

    # -- reading a run --------------------------------------------------------

    def parse_test_output(self, stdout: str, stderr: str, code: int) -> TestResult:
        combined = f"{stdout}\n{stderr}"
        found = [d.render() for d in diagnostics(stderr) + diagnostics(stdout)]
        broken = [
            f"{m.group('document')}:{m.group('line')} broken link {m.group('uri')}"
            for m in (_BROKEN.match(line) for line in combined.splitlines())
            if m
        ]
        failures = found + broken
        counts = [int(a) + int(c) for a, c in _DOCUMENTS.findall(combined)]
        note = ""
        if failures:
            named = "; ".join(failures[:_NAMED])
            more = len(failures) - _NAMED
            note = f"{len(failures)} failure(s): {named}" + (
                f"; and {more} more" if more > 0 else ""
            )
        live = [h for h in self.hazards() if h.matches(combined)]
        return TestResult(
            passed=code == 0 and not failures,
            exit_code=code,
            total=sum(counts) if counts else None,
            failed=len(failures),
            failed_tests=len(failures),
            hazards=live,
            note=note,
        )

    def annotate(self, result: TestResult, root: Path) -> TestResult:
        """Spell the tree's paths from the project, and note what each
        ``conf.py`` narrows: the project's stated choice, annotated, never a
        hazard (ADR-015 D1)."""
        root = Path(root)
        note = result.note
        for base in {str(root.resolve()), str(root)}:
            note = note.replace(f"{base}/", "")
        trees = self.source_dirs(root, skip_docs_project=True) or self.source_dirs(root)
        said = []
        for srcdir in trees:
            narrowing = conf_facts(srcdir / "conf.py").narrowing
            if narrowing:
                where = f"{_spelled(root, srcdir / 'conf.py')}: " if len(trees) > 1 else ""
                said.append(where + ", ".join(narrowing))
        if said:
            line = "conf.py narrows what passes (the project's stated choice): " + "; ".join(said)
            note = f"{note}; {line}" if note else line
        return result if note == result.note else replace(result, note=note)

    def hazards(self) -> list[Hazard]:
        return [
            Hazard(
                id="sphinx.warnings_not_errors",
                summary="a build without -W passes on warnings",
                detail=(
                    "sphinx-build exits 0 on warnings unless -W turns them into "
                    "errors, so 'build succeeded' can hide a dangling reference."
                ),
                detect=r"(?m)^build succeeded, \d+ warnings?\.",
                can_overstate=True,
            ),
            Hazard(
                id="sphinx.incremental",
                summary="a build without -E reuses its saved environment",
                detail=(
                    "An incremental build re-reads only the sources it thinks "
                    "changed, and validates nothing it skipped."
                ),
                detect=r"(?m)^loading pickled environment\.\.\. done",
                can_overstate=True,
            ),
            Hazard(
                id="sphinx.linkcheck_network",
                summary="a network failure in linkcheck reads as a broken link",
                detail=(
                    "A link that could not be reached (no connection, a name "
                    "that did not resolve, a timeout) is reported broken; the "
                    "failure is the network's, not necessarily the link's."
                ),
                detect=(
                    r"(?m)^\(.*\)\s+broken\s+\S+ - .*(?:Max retries exceeded|"
                    r"NewConnectionError|NameResolutionError|Failed to resolve|"
                    r"ConnectTimeout|ReadTimeout|timed out|Connection refused|"
                    r"ConnectionError)"
                ),
                can_overstate=False,
            ),
        ]


def _spelled(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
