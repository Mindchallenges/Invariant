#!/usr/bin/env python3
"""Compile tokens.json -> tokens.css (CSS custom properties, light + dark themes, type classes).
Usage: python3 scripts/build_tokens_css.py [tokens.json] [tokens.css]"""
import json, re, sys
src = sys.argv[1] if len(sys.argv) > 1 else "tokens.json"
dst = sys.argv[2] if len(sys.argv) > 2 else "tokens.css"
t = json.load(open(src, encoding="utf-8"))
themes = [th["id"] for th in t["color"]["themes"]]
first = themes[0]

def val(v):
    m = re.fullmatch(r"\{(.+)\}", v)
    return f"var(--{m.group(1)})" if m else v

def color_for(tok, theme):
    v = tok["value"]
    if isinstance(v, str):
        return val(v) if theme == first else None
    return val(v[theme]) if theme in v else (val(v[first]) if theme == first else None)

out = [f"/* {t['name']} — generated from tokens.json */"]
for theme in themes:
    sel = f':root, [data-theme="{theme}"]' if theme == first else f'[data-theme="{theme}"]'
    lines = []
    for tok in t["color"]["tokens"]:
        c = color_for(tok, theme)
        if c is None:
            continue
        # aliases must be re-declared in every theme so they resolve against that theme
        if theme != first and isinstance(tok["value"], dict) and tok["value"].get(theme) == tok["value"].get(first):
            continue
        lines.append(f"  --{tok['name']}: {c};")
    if theme != first:
        lines += [f"  --{tok['name']}: {val(tok['value'])};" for tok in t["color"]["tokens"] if isinstance(tok["value"], str)]
    out.append(sel + " {\n" + "\n".join(lines) + "\n}")
    if theme != first:
        out.append(f"@media (prefers-color-scheme: {theme}) {{\n  :root:not([data-theme]) {{\n" +
                   "\n".join("  " + l for l in lines) + "\n  }\n}")
root = []
for fam, data in t.items():
    if isinstance(data, dict) and "tokens" in data and fam != "color":
        root += [f"  --{tok['name']}: {tok['value']};" for tok in data["tokens"]]
root += [f"  --font-{k}: {v};" for k, v in t["type"]["families"].items()]
out.append(":root {\n" + "\n".join(root) + "\n}")
for g in t["type"]["groups"]:
    for s in g["styles"]:
        fam = s.get("family", g["family"])
        out.append(f".{s['name']} {{ font-family: var(--font-{fam}); font-size: {s['fontSize']}; "
                   f"line-height: {s['lineHeight']}; font-weight: {s['fontWeight']}; }}")
open(dst, "w", encoding="utf-8").write("\n".join(out) + "\n")
print(f"wrote {dst}")
