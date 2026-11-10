# Sphinx configuration for invariant's docs project (ADR-018 D3).
#
# Architecture and design live here as sphinx-needs: every decision,
# architecture element, guideline rule and work item is a need with an id, and
# the links between them are checked at build time. The build is strict, and
# `invariant docs build` runs it in the pinned environment beside this file
# (`invariant docs venv` creates it from requirements.txt):
#
#   .venv/bin/sphinx-build -E -W --keep-going -n -b html . <output>
#
# The architecture pages (paths.docs, docs/architecture), the decision records
# (decisions/), the backlog (backlog.rst) and the generated rules page are
# reStructuredText. The user guide and the audits stay Markdown, read through
# MyST: they are not design (ADR-018 D8). The keys below are those of the
# sphinx-needs version requirements.txt pins, `needs_fields` and
# `needs_links`; the keys they replaced are deprecated there, and a
# deprecation warning fails the strict build.

import re
from pathlib import Path

project = "invariant"
author = "invariant maintainers"
copyright = "2026, invariant maintainers"

extensions = [
    "myst_parser",
    "sphinx_needs",
    "sphinxcontrib.mermaid",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# ```mermaid fences in the Markdown pages become mermaid directives.
myst_fence_as_directive = ["mermaid"]
myst_enable_extensions = ["colon_fence"]
# Headings down to ### get slug anchors, so the user guide can link into a
# section of a page rather than only to its top.
myst_heading_anchors = 3

exclude_patterns = [".venv", "_build"]

html_theme = "alabaster"
html_title = "invariant"

# -- sphinx-needs -------------------------------------------------------------

needs_id_required = True
needs_id_regex = r"^[A-Z][A-Z0-9_]*_[A-Z0-9_]+$"
needs_build_json = True

# The need types, each mapped to a role by the manifest's docs.needs.types
# (left unset: the engine's defaults, which these are). The docs.needs_traceable
# gate names a directive it cannot map.
needs_types = [
    {"directive": "req", "title": "Requirement", "prefix": "REQ_", "color": "#BFD8D2", "style": "node"},
    {"directive": "dec", "title": "Decision", "prefix": "DEC_", "color": "#FEDCD2", "style": "node"},
    {"directive": "comp", "title": "Component", "prefix": "COMP_", "color": "#DF744A", "style": "node"},
    {"directive": "ui", "title": "UI", "prefix": "UI_", "color": "#DCB239", "style": "node"},
    {"directive": "rule", "title": "Rule", "prefix": "RULE_", "color": "#9DC3E6", "style": "node"},
    {"directive": "idea", "title": "Idea", "prefix": "IDEA_", "color": "#C5E0B4", "style": "node"},
    {"directive": "bug", "title": "Bug", "prefix": "BUG_", "color": "#F4B183", "style": "node"},
]

_TEXT = {"schema": {"type": "string"}, "nullable": True}

needs_fields = {
    "design_link": {**_TEXT, "description": "The design-tool canvas a ui need records"},
}

needs_links = {
    "satisfies": {"incoming": "satisfied by", "outgoing": "satisfies"},
    "implements": {"incoming": "implemented by", "outgoing": "implements"},
    "uses": {"incoming": "used by", "outgoing": "uses"},
    "decided_by": {"incoming": "decides", "outgoing": "decided by"},
    "affects": {"incoming": "affected by", "outgoing": "affects"},
    "part_of": {"incoming": "has part", "outgoing": "part of"},
    "amends": {"incoming": "amended by", "outgoing": "amends"},
    "supersedes": {"incoming": "superseded by", "outgoing": "supersedes"},
}

# The pages `invariant item` writes work items into (items.path), relative to
# this file.
_ITEM_FILES = ["backlog.rst"]

# The options a need directive owns, which are never declared as fields.
_DIRECTIVE_OPTIONS = {
    "id", "status", "tags", "collapse", "delete", "hide", "jinja_content", "layout",
    "style", "template", "pre_template", "post_template", "constraints", "title_from_content",
}


def _item_fields() -> dict:
    """Declare every option the work items carry.

    `invariant item` owns the items' fields, so they are read from the item
    pages rather than listed here, and a field it writes never breaks the
    build.
    """
    names = set()
    for name in _ITEM_FILES:
        page = Path(__file__).parent / name
        if page.is_file():
            names |= set(re.findall(r"^[ \t]+:([\w-]+):", page.read_text(encoding="utf-8"), re.MULTILINE))
    known = set(needs_fields) | set(needs_links) | _DIRECTIVE_OPTIONS | {"links"}
    return {name: dict(_TEXT) for name in sorted(names - known)}


needs_fields.update(_item_fields())
