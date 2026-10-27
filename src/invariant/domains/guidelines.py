"""The guidelines domain: the effects the ruleset has on the tree (ADR-012 D12).

Not an FSM, like ``stats`` and ``sweep``: there is no lifecycle to advance.
This increment (IDEA_031 A2 increment 3) owns one effect, rendering the human
checklist. ``core/guidelines`` decides what the text is; this module decides
where it goes and whether it may be written there.

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
class Rendered:
    """What one render did, or with ``--dry-run`` would have done."""

    #: ``new``, ``current``, ``stale``, ``edited``, or ``no-ruleset``.
    status: str
    written: bool
    path: Path | None = None
    relpath: str | None = None
    #: The ruleset digest the text was rendered from.
    ruleset: str | None = None


class GuidelinesDomain:
    def __init__(self, ctx: RuleContext) -> None:
        self.ctx = ctx
        self.manifest = ctx.manifest

    def render(self, *, dry_run: bool = False) -> Rendered:
        ruleset, failure = load_ruleset(self.ctx)
        if failure is not None:
            fixes = "; ".join(failure.fixes) or "fix the ruleset"
            detail = f": {failure.detail}" if failure.detail else ""
            raise RenderRefused(f"{failure.message}{detail}", fixes)
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

        text = guidelines.render_checklist(ruleset, fmt, source=source)
        findings = PrivacyScanner.from_manifest(self.manifest).scan_text(text)
        if findings:
            named = ", ".join(f"{rel}:{f.line} {f.pattern} {f.redacted}" for f in findings)
            raise RenderRefused(
                f"the rendered checklist fails the privacy scan ({named}); "
                f"nothing was written",
                f"remove it from {source}, then render again",
            )

        status = self._status(path, rel, fmt, text)
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return Rendered(
            status=status, written=not dry_run, path=path, relpath=rel,
            ruleset=ruleset.digest,
        )

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

    # -- what is there now ---------------------------------------------------------

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
