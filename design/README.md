# Design work

| Folder | What | Status |
| --- | --- | --- |
| [`archviz/`](archviz/) | Archviz — visual language for architecture documentation built on sphinx-needs: tokens (light + dark), diagram primitives, brand book, standalone gallery | design draft; basis for the planned sphinx-needs theme |

Start with [`archviz/GETTING-STARTED.md`](archviz/GETTING-STARTED.md), then [`archviz/README.md`](archviz/README.md).
Open `archviz/gallery.html` in a browser to see every component.

## Next step: sphinx-needs theme

The theme will consume `archviz/tokens.json` as its single source of styling:
need types, link types and fields from the Archviz metamodel; views rendered with Cytoscape.js in HTML and
Graphviz/TikZ in PDF; colours, type and spacing taken from these tokens.
