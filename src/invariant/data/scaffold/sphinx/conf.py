# Sphinx configuration for {{title}}'s docs project.
#
# Written once by `invariant init` (ADR-018 D11), and the project's own file
# from then on. Architecture and design live here as sphinx-needs: every
# requirement, decision, architecture element, screen, guideline rule and
# work item is a need with an id, and the links between them are checked at
# build time. The build is strict, and `invariant docs build` runs it in the
# pinned environment beside this file (`invariant docs venv` creates it):
#
#   .venv/bin/sphinx-build -E -W --keep-going -n -b html . <output>
#
# The keys below are those of the sphinx-needs version requirements.txt pins:
# `needs_fields` and `needs_links`. The keys they replaced are deprecated
# there, and a deprecation warning fails the strict build.

import re
from pathlib import Path

project = {{project}}

extensions = [
    "sphinx_needs",
    "sphinxcontrib.mermaid",
]

exclude_patterns = [".venv", "_build"]

# -- sphinx-needs -------------------------------------------------------------

needs_id_required = True
needs_id_regex = r"^[A-Z][A-Z0-9_]*_[A-Z0-9_]+$"
needs_build_json = True

# The need types, each mapped to a role in the manifest's docs.needs.types
# (requirement, decision, element, ui, rule, idea, bug). Add a type here and
# map it there; the docs.needs_traceable gate names a directive it cannot map.
needs_types = {{types}}

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

# The pages `invariant item` writes work items into (items.path, and
# items.closed_path when set), relative to this file.
_ITEM_FILES = {{item_files}}

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
            names |= set(re.findall(r"^[ \t]+:([\w-]+):", page.read_text(encoding="utf-8"), re.M))
    known = set(needs_fields) | set(needs_links) | _DIRECTIVE_OPTIONS | {"links"}
    return {name: dict(_TEXT) for name in sorted(names - known)}


needs_fields.update(_item_fields())
