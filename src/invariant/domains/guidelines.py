"""The guidelines domain: the effects the ruleset has on the tree (ADR-012 D12).

Not an FSM, like ``stats`` and ``sweep``: there is no lifecycle to advance.
It owns one effect, rendering: the human checklist (A2 increment 3) and the
managed ``guidelines/index`` block in each instruction file (increment 4).
``core/guidelines`` decides what the text is; this module decides where it
goes and whether it may be written there. It also shows the checklist text
without writing it, which is where a capped block points.

It writes one class of file only: a generated file the manifest declares, the
CLI's third write class (ruling 13). That class is never exempt from session
isolation, so a render goes through a session's review like any edit. So a
render is refused, with nothing written, when:

- the ruleset does not load (every gate that needs it fails; so does render);
- the target names nothing inside the project, is the ruleset itself, or is a
  path the project declared exempt from isolation;
- its suffix disagrees with ``docs.format``, or that format has no renderer;
- the file there carries no managed marker, because render did not write it
  and the text is the project's;
- the privacy scan of the rendered text is not clean.

A file render did write is rewritten whatever its state, and its state is
named: current, stale (rendered from an older ruleset), or edited (its body
no longer matches its marker). Hand edits are replaced, because the ruleset is
the source and the fix for drift is "move the change into the ruleset, then
render" (D3). The file is never exempt from isolation, so the replaced text
is still in the branch's diff.

An instruction file is different: only the text between a block's markers is
render's, and the rest is the project's. So a block is never inserted unasked
(``insert`` names it), and a block edited by hand is skipped and named unless
``overwrite_local`` asks for the rendered text back, the three-way rule of
``classify_installed``. The files default to the host adapter's, which the
caller passes in, because no domain imports a host. Refused as well, with
nothing written anywhere:

- an instruction file outside the project, not markdown, exempt from
  isolation, or the same file as the ruleset or the checklist;
- a malformed begin/end pair, named by file and line;
- a block name to insert that render does not know;
- a privacy finding in a rendered block.

Every output is decided, scanned and checked before the first write, so a
refusal leaves the tree as it was.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..core import guidelines
from ..core.errors import InvariantError
from ..core.privacy import PrivacyScanner
from ..core.rules import (
    RuleContext,
    guidelines_relpath,
    guidelines_tree,
    is_exempt_path,
    load_ruleset,
)


class RenderRefused(InvariantError):
    """A render that would write somewhere it may not, or text it may not."""

    exit_code = 1

    def __init__(self, message: str, fix: str) -> None:
        super().__init__(f"{message}\n  {fix}")


@dataclass(frozen=True)
class BlockRendered:
    """What one render did to one managed block in one instruction file."""

    relpath: str
    name: str
    #: ``new`` (inserted), ``current``, ``stale``, ``edited`` (written only
    #: with ``overwrite_local``), or ``absent`` (no block, and none inserted).
    status: str
    written: bool


@dataclass(frozen=True)
class Rendered:
    """What one render did, or with ``--dry-run`` would have done."""

    #: ``new``, ``current``, ``stale``, ``edited``, or ``no-ruleset``.
    status: str
    written: bool
    path: Path | None = None
    relpath: str | None = None
    #: The ruleset digest the text was rendered from.
    ruleset: str | None = None
    #: One entry per instruction file and block.
    blocks: tuple[BlockRendered, ...] = ()


@dataclass(frozen=True)
class Shown:
    """The checklist text ``guidelines show`` prints; ``None`` with no ruleset."""

    text: str | None
    ruleset: str | None = None


@dataclass(frozen=True)
class _BlockPlan:
    """A block decided and checked, not yet written."""

    result: BlockRendered
    path: Path
    #: The whole file as it will be written, or ``None`` when it is not.
    text: str | None


class GuidelinesDomain:
    def __init__(self, ctx: RuleContext, *, instruction_files: tuple[str, ...] = ()) -> None:
        self.ctx = ctx
        self.manifest = ctx.manifest
        #: The host adapter's instruction files, used when
        #: ``guidelines.instruction_files`` is null. The CLI supplies them.
        self.default_instruction_files = tuple(instruction_files)

    def render(
        self,
        *,
        dry_run: bool = False,
        insert: tuple[str, ...] = (),
        overwrite_local: bool = False,
    ) -> Rendered:
        unknown = [name for name in insert if name not in guidelines.BLOCKS]
        if unknown:
            raise RenderRefused(
                f"no managed block is named {', '.join(unknown)}",
                f"--insert takes {', '.join(guidelines.BLOCKS)}",
            )
        ruleset = self._ruleset()
        if ruleset is None:
            return Rendered(status="no-ruleset", written=False)

        source = guidelines_relpath(self.ctx, self.manifest.get("guidelines.ruleset"))
        fmt = self._format()
        rel = self._target(source, fmt)
        tree = guidelines_tree(self.ctx)
        path = tree / rel
        if is_exempt_path(self.manifest, tree, path):
            raise RenderRefused(
                f"the checklist {rel} is exempt from session isolation, and a "
                f"generated file never is (ADR-012 ruling 13)",
                "set guidelines.checklist to a path outside session.exempt_paths "
                "and the declared stores",
            )

        scanner = PrivacyScanner.from_manifest(self.manifest)
        text = guidelines.render_checklist(ruleset, fmt, source=source)
        findings = scanner.scan_text(text)
        if findings:
            named = ", ".join(f"{rel}:{f.line} {f.pattern} {f.redacted}" for f in findings)
            raise RenderRefused(
                f"the rendered checklist fails the privacy scan ({named}); "
                f"nothing was written",
                f"remove it from {source}, then render again",
            )
        status = self._status(path, rel, fmt, text)

        plans = self._plan_blocks(
            ruleset, source, rel, tree, scanner,
            insert=insert, overwrite_local=overwrite_local,
        )

        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            for plan in plans:
                if plan.text is not None:
                    plan.path.parent.mkdir(parents=True, exist_ok=True)
                    plan.path.write_text(plan.text, encoding="utf-8")
        return Rendered(
            status=status, written=not dry_run, path=path, relpath=rel,
            ruleset=ruleset.digest,
            blocks=tuple(
                BlockRendered(
                    plan.result.relpath, plan.result.name, plan.result.status,
                    plan.result.written and not dry_run,
                )
                for plan in plans
            ),
        )

    def show(self) -> Shown:
        """The checklist text, in markdown, without its marker. Writes nothing.

        Markdown whatever ``docs.format`` says: it is read by an agent or a
        terminal, not built into the project's docs.
        """
        ruleset = self._ruleset()
        if ruleset is None:
            return Shown(text=None)
        source = guidelines_relpath(self.ctx, self.manifest.get("guidelines.ruleset"))
        text = guidelines.render_checklist(ruleset, "markdown", source=source)
        _digest, body = guidelines.split_managed(text, "markdown")
        return Shown(text=body.lstrip("\n"), ruleset=ruleset.digest)

    def _ruleset(self) -> guidelines.Ruleset | None:
        ruleset, failure = load_ruleset(self.ctx)
        if failure is not None:
            fixes = "; ".join(failure.fixes) or "fix the ruleset"
            detail = f": {failure.detail}" if failure.detail else ""
            raise RenderRefused(f"{failure.message}{detail}", fixes)
        return ruleset

    # -- where it goes --------------------------------------------------------

    def _format(self) -> str:
        fmt = self.manifest.get("docs.format")
        if fmt not in guidelines.CHECKLIST_SUFFIXES:
            raise RenderRefused(
                f"docs.format {fmt!r} has no checklist renderer",
                f"set docs.format to one of {', '.join(guidelines.CHECKLIST_SUFFIXES)}",
            )
        return fmt

    def _target(self, source: str, fmt: str) -> str:
        suffix = guidelines.CHECKLIST_SUFFIXES[fmt]
        configured = self.manifest.get("guidelines.checklist")
        if configured is None:
            return posixpath.join(posixpath.dirname(source), f"checklist{suffix}")
        rel = guidelines_relpath(self.ctx, configured)
        if rel is None:
            raise RenderRefused(
                f"guidelines.checklist {configured!r} names nothing inside the project",
                "set it to a project-relative path, or null for one beside the ruleset",
            )
        if rel == source:
            raise RenderRefused(
                f"guidelines.checklist {rel} is the ruleset; rendering would "
                f"overwrite the source with its own rendering",
                "point guidelines.checklist somewhere else, or set it to null",
            )
        if PurePosixPath(rel).suffix != suffix:
            raise RenderRefused(
                f"guidelines.checklist {rel} does not end in {suffix}, and "
                f"docs.format is {fmt}",
                f"name a {suffix} file, or set guidelines.checklist to null",
            )
        return rel

    def _instruction_files(self, source: str, checklist: str, tree: Path) -> list[str]:
        """The instruction files' project-relative paths, each checked, once each."""
        configured = self.manifest.get("guidelines.instruction_files")
        named = self.default_instruction_files if configured is None else configured
        found: list[str] = []
        for entry in named:
            rel = guidelines_relpath(self.ctx, entry)
            if rel is None:
                raise RenderRefused(
                    f"the instruction file {entry!r} names nothing inside the project",
                    "set guidelines.instruction_files to project-relative paths",
                )
            if PurePosixPath(rel).suffix != ".md":
                raise RenderRefused(
                    f"the instruction file {rel} is not a markdown file, and the "
                    f"block markers are markdown comments",
                    "name .md files in guidelines.instruction_files",
                )
            if rel in (source, checklist):
                what = "ruleset" if rel == source else "checklist"
                raise RenderRefused(
                    f"the instruction file {rel} is also the guidelines {what}",
                    "name a different file in guidelines.instruction_files",
                )
            if is_exempt_path(self.manifest, tree, tree / rel):
                raise RenderRefused(
                    f"the instruction file {rel} is exempt from session isolation, "
                    f"and a generated file never is (ADR-012 ruling 13)",
                    "name a file outside session.exempt_paths and the declared stores",
                )
            if rel not in found:
                found.append(rel)
        return found

    # -- what is there now ---------------------------------------------------------

    def _plan_blocks(
        self,
        ruleset: guidelines.Ruleset,
        source: str,
        checklist: str,
        tree: Path,
        scanner: PrivacyScanner,
        *,
        insert: tuple[str, ...],
        overwrite_local: bool,
    ) -> list[_BlockPlan]:
        """Every instruction file's block, decided and scanned, none written."""
        body = guidelines.render_index_block(
            ruleset, source=source,
            max_lines=self.manifest.get("guidelines.injection.max_lines"),
        )
        plans = [
            self._plan_block(tree, target, body, insert, overwrite_local)
            for target in self._instruction_files(source, checklist, tree)
        ]
        writing = [plan for plan in plans if plan.text is not None]
        findings = scanner.scan_text(guidelines.block_text(guidelines.INDEX_BLOCK, body))
        if findings and writing:
            named = ", ".join(
                f"{plan.result.relpath} ({guidelines.INDEX_BLOCK} block, line "
                f"{f.line}) {f.pattern} {f.redacted}"
                for plan in writing for f in findings
            )
            raise RenderRefused(
                f"the rendered block fails the privacy scan ({named}); nothing "
                f"was written",
                f"remove it from {source}, then render again",
            )
        return plans

    @staticmethod
    def _plan_block(
        tree: Path, rel: str, body: str, insert: tuple[str, ...], overwrite_local: bool
    ) -> _BlockPlan:
        name = guidelines.INDEX_BLOCK
        path = tree / rel
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        try:
            block = guidelines.find_blocks(existing).get(name)
        except guidelines.BlockError as exc:
            raise RenderRefused(
                f"{rel} {exc}; nothing was written",
                "repair the pair by hand (a begin and an end line per block, "
                "never nested), then render again",
            ) from exc
        if block is None:
            if name not in insert:
                return _BlockPlan(BlockRendered(rel, name, "absent", False), path, None)
            text = guidelines.append_block(existing, name, body)
            return _BlockPlan(BlockRendered(rel, name, "new", True), path, text)
        state = guidelines.block_state(block, body)
        if state == "edited" and not overwrite_local:
            return _BlockPlan(BlockRendered(rel, name, state, False), path, None)
        text = guidelines.replace_block(existing, block, body)
        return _BlockPlan(BlockRendered(rel, name, state, True), path, text)

    @staticmethod
    def _status(path: Path, rel: str, fmt: str, text: str) -> str:
        if not path.exists():
            return "new"
        existing = path.read_text(encoding="utf-8")
        digest, body = guidelines.split_managed(existing, fmt)
        if digest is None:
            raise RenderRefused(
                f"{rel} exists and has no invariant-managed marker, so render "
                f"did not write it and will not overwrite it",
                "move that file, or point guidelines.checklist at another path",
            )
        if digest != guidelines.digest_of(body.encode("utf-8")):
            return "edited"
        return "current" if existing == text else "stale"
