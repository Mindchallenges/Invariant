# Relation

Draws one relation between elements; line style, colour and arrowheads come from the relation type, never from the call site.

**Consumer provides:** `link` (relation type), `points` (polyline, start → end) or a path `d`, optional `label` + `labelAt`. Put `Archviz.defs()` once in each SVG (the `svg()` helper does it).

| Relation | Line | Head | Colour |
| --- | --- | --- | --- |
| part_of | dashed | none | `line` (drawn as nesting in most views) |
| deployed_on, runs_on | dashed | open vee | `line` |
| connects | 2.5px solid | none | `rel-connect` |
| uses | solid | open vee | `line` |
| flows_to | solid | filled triangle | `rel-flow` |
| provides / requires | solid / dashed | none / tee | `line` |
| realizes / inherits | dashed / solid | hollow triangle | `line` |
| composes / aggregates | solid | filled / hollow diamond at the whole | `line` |
| next | solid | filled triangle | `line` |
| satisfies / decides | dotted | open vee | `rel-trace` / `rel-decide` |
| msg.sync / async / return | solid / solid / dashed | filled / open / open | `ink` |

- Do: route orthogonally (elbows) in block, hardware and network views; straight or curved elsewhere.
- Do: set labels in `edge-label` with a canvas halo so they read over lines.
- Don't: add colour to show importance; use the `selected` highlight (`rel-highlight`) instead.
