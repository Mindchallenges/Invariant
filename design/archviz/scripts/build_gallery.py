#!/usr/bin/env python3
"""Build gallery.html: every component preview on one standalone page (works from file://).
Usage: python3 scripts/build_gallery.py   (run from the design-system root, after build_tokens_css.py)"""
import os, re, html
ORDER = ["ElementNode", "Interface", "Relation", "Container", "UmlClass", "Sequence", "StateFlow", "Status", "Legend", "DiagramFrame"]
sections, cover = [], ""
for name in ["Cover"] + ORDER:
    p = f"components/{name}/preview.html"
    if not os.path.exists(p):
        continue
    src = open(p, encoding="utf-8").read()
    marker = re.search(r'@dsCard([^>]*)-->', src).group(1)
    sub = re.search(r'subtitle="([^"]*)"', marker)
    group = re.search(r'group="([^"]*)"', marker)
    body = src.split("<body>", 1)[1].rsplit("</body>", 1)[0]
    head_style = re.findall(r"<style>(.*?)</style>", src.split("<body>")[0], re.S)
    rid = f"root-{name}"
    body = body.replace('id="root"', f'id="{rid}"').replace('getElementById("root")', f'getElementById("{rid}")')
    body = re.sub(r"<script>", "<script>(function(){", body).replace("</script>", "})();</script>")
    if name == "Cover":
        cover = "".join(f"<style>{s}</style>" for s in head_style) + body
        continue
    sections.append(f'<section class="g-card" id="{name}"><header><span class="g-group">{html.escape(group.group(1) if group else "")}</span>'
                    f'<h2>{name}</h2><p>{html.escape(sub.group(1) if sub else "")}</p></header><div class="g-body">{body}</div></section>')
nav = "".join(f'<a href="#{n}">{n}</a>' for n in ORDER)
page = f"""<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Archviz design system</title>
<link rel="stylesheet" href="tokens.css">
<link rel="stylesheet" href="components/bundle.css">
<script src="components/bundle.js"></script>
<style>
  body {{ font-family: var(--font-sans); background: var(--canvas); color: var(--ink); }}
  .g-top {{ position: sticky; top: 0; z-index: 2; display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center;
           padding: 10px 24px; background: var(--surface); border-bottom: 1px solid var(--grid); font-size: 13px; }}
  .g-top a {{ color: var(--ink-muted); text-decoration: none; }} .g-top a:hover {{ color: var(--primary-ink); }}
  .g-top b {{ margin-right: 8px; }}
  .g-top button {{ margin-left: auto; font: 500 12px var(--font-sans); color: var(--ink); background: var(--canvas);
                  border: 1px solid var(--grid); border-radius: var(--radius-control); padding: 6px 10px; cursor: pointer; }}
  .g-top button:focus-visible {{ outline: 2px solid var(--focus-ring); outline-offset: 1px; }}
  main {{ max-width: 1040px; margin: 0 auto; padding: 24px; }}
  .g-cover {{ overflow-x: auto; border: 1px solid var(--grid); border-radius: var(--radius-group); margin-bottom: 32px; }}
  .g-card {{ border: 1px solid var(--grid); border-radius: var(--radius-group); margin: 0 0 24px; overflow: hidden; }}
  .g-card header {{ padding: 14px 18px; border-bottom: 1px solid var(--grid); background: var(--surface); }}
  .g-card h2 {{ margin: 2px 0; font-size: 16px; }} .g-card p {{ margin: 0; font-size: 13px; color: var(--ink-muted); }}
  .g-group {{ font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--ink-muted); }}
  .g-body {{ overflow-x: auto; }}
</style>
</head>
<body>
<nav class="g-top"><b>Archviz</b>{nav}<button type="button" id="g-theme">Dark theme</button></nav>
<main>
<div class="g-cover">{cover}</div>
{''.join(sections)}
</main>
<script>
document.getElementById("g-theme").addEventListener("click", function () {{
  var r = document.documentElement, dark = r.getAttribute("data-theme") !== "dark";
  r.setAttribute("data-theme", dark ? "dark" : "light");
  this.textContent = dark ? "Light theme" : "Dark theme";
}});
</script>
</body>
</html>
"""
open("gallery.html", "w", encoding="utf-8").write(page)
print("wrote gallery.html")
