# DiagramFrame

Wraps a view in its chrome: toolbar (search, fit, re-layout, legend, PNG, full screen), stage with title, optional tooltip, legend and caption.

**Consumer provides:** `title`, `body` (an SVG from `Archviz.svg` or a Cytoscape container), optional `tip` (`Archviz.tooltip`), `legend` (`Archviz.legend`), `caption`, `toolbar: false` for print.

- The tooltip shows the element's name, ID in mono, type · layer · technology, a one-line summary and an "Open in docs" link to the element's definition.
- Captions name the view and its root: `component view, root APP_LOGD`.
- In PDF the toolbar, tooltip and search disappear; title, legend and caption stay.
