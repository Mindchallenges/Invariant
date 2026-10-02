# Sphinx configuration for the invariant documentation site.
#
# The sources stay markdown (docs.format: markdown in the manifest); Sphinx is
# the renderer over them, via MyST. Mermaid fences in the architecture docs are
# mapped to the mermaid directive so the diagrams render instead of printing as
# code blocks.

project = "invariant"
author = "invariant maintainers"
copyright = "2026, invariant maintainers"

extensions = [
    "myst_parser",
    "sphinxcontrib.mermaid",
]

source_suffix = {
    ".md": "markdown",
    ".rst": "restructuredtext",
}

# ```mermaid fences become mermaid directives without editing the docs.
myst_fence_as_directive = ["mermaid"]
myst_enable_extensions = ["colon_fence"]
# Headings down to ### get slug anchors, so the user guide can link into a
# section of a reference document rather than only to its top.
myst_heading_anchors = 3

# The backlog is a work-item store (items.path), not prose for the site; it is
# read through `invariant item`, so it is excluded rather than toctree'd.
exclude_patterns = ["_build", "backlog"]

html_theme = "alabaster"
html_title = "invariant"
