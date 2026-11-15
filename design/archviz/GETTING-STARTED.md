# Archviz design system — source

`README.md` is the brand book (usage rules). This file explains the source tree.

```
tokens.json                 source of truth: colours (light + dark), type, spacing, radius, stroke, size
tokens.css                  generated from tokens.json (CSS custom properties) — do not edit by hand
components/bundle.js        diagram primitives, global `window.Archviz` (no dependencies)
components/bundle.css       styles for the primitives; reads only token variables
components/index.d.ts       prop types for every component
components/<Name>/          README.md (guidelines) + preview.html (live example) per component
components/Cover/           cover artwork
design-system.json          index used by the Claude Design System artifact
gallery.html                generated: all previews on one standalone page with a theme toggle
scripts/                    build_tokens_css.py, build_gallery.py
```

## Build

```bash
python3 scripts/build_tokens_css.py      # tokens.json -> tokens.css
python3 scripts/build_gallery.py         # previews   -> gallery.html
```

Open `gallery.html` directly in a browser (works from `file://`; fonts load from Google Fonts).

## Use in a page

```html
<link rel="stylesheet" href="tokens.css">
<link rel="stylesheet" href="components/bundle.css">
<script src="components/bundle.js"></script>
<div id="d"></div>
<script>
  const A = window.Archviz;
  let s = A.node({type: "app", label: "Log collector", tech: "C++20", x: 20, y: 20});
  s += A.node({type: "store", label: "Log spool", tech: "eMMC", x: 260, y: 16});
  s += A.relation({link: "flows_to", points: [[150, 41], [260, 41]]});
  document.getElementById("d").innerHTML = A.svg(420, 100, s, "Example");
</script>
```

Set `data-theme="dark"` on `<html>` for the dark theme; without the attribute the page follows the OS setting.

## Re-brand

Change the six role colours (`primary`, `secondary`, `tertiary`, `accent`, `neutral`, `critical`) and their
`-fill`, `-group`, `-ink` variants in `tokens.json`, then rebuild `tokens.css`. Layers, relations and status
point at roles through aliases (`layer-*`, `rel-*`) and follow automatically.

The Sphinx extension (`sphinx_archviz`, delivered separately) derives its Cytoscape and PDF styling from its own
`archviz_tokens` setting; keep its six base colours in sync with this file.
