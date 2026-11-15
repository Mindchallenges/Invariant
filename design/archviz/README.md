Archviz is the visual language for architecture documentation written with sphinx-needs. Every element is written once as a need with an ID; every diagram is a view generated from those needs. Diagrams are rendered with Cytoscape.js in HTML (interactive) and with the same tokens as static SVG elsewhere. Nobody draws a box by hand.

## Principles

- **One model, many views.** An element appears in every view that selects it; never copy an element into a picture.
- **Colour means layer.** The six role colours tell the reader which layer an element belongs to. Status, highlight and decisions have their own reserved tokens.
- **Shape means type.** A barrel is always a data store, a lollipop always an interface, a cut rectangle always hardware.
- **Notation from relations.** Line style and arrowhead come from the relation type (`uses`, `flows_to`, `inherits` …), not from the author.
- **Quiet by default.** Fills are 10% tints (`*-fill`), containers 4.5% (`*-group`); only buses and fragment tags are solid.

## Content

- Label elements with the noun people use: `Log collector daemon`, `Application SoC`, `Telemetry store`. Sentence case, no articles, no trailing punctuation.
- Put technology in the `tech` line (`[C++20]`, `[Kafka]`, `[Cortex-R52]`), set in `node-meta` mono, never in the label.
- IDs are upper case with the type prefix (`CMP_RINGBUF`, `HW_HPC_APP`) and appear only in tooltips, tables and captions, set in `id`.
- Edge labels are short verbs or protocols: `deployed on`, `SPI 20 MHz`, `window opens`, `timer [retries < 5]`.
- Figure captions name the view and its root: `Component view · root APP_LOGD`.
- No emoji, no icons inside nodes except the UML component glyph.

## Layers and colour roles

Every element type belongs to one layer; the layer token (`layer-*`) points at a role; the role gives four tokens: stroke (`primary`), fill (`primary-fill`), container fill (`primary-group`) and text colour (`primary-ink`).

| Layer token | Role | Types (directive · ID prefix) |
| --- | --- | --- |
| `layer-context` | primary | actor ACT_, system SYS_, usecase UC_; extsys EXT_ is neutral and dashed |
| `layer-application` | primary | app APP_, store DB_, queue Q_ |
| `layer-component` | secondary | comp CMP_, data DAT_; iface IF_ is accent |
| `layer-design` | tertiary | cls CLS_ |
| `layer-behaviour` | primary | state ST_, step STP_, scenario SEQ_, msg MSG_, rel REL_ |
| `layer-platform` | neutral | platform PLT_ |
| `layer-infrastructure` | secondary | cloud CLD_, node NODE_, net NET_ |
| `layer-hardware` | tertiary | hw HW_, bus BUS_ |
| `layer-trace` | neutral | req REQ_; adr ADR_ is accent |

- Fill element boxes with `<role>-fill` and stroke them with `<role>` at `stroke-node`.
- Fill containers with `<role>-group`; write their titles in `<role>-ink`.
- Use `accent` only for interfaces, decisions, sequence fragments and the focus/highlight state.
- Use `critical` only for status (`risk`, `open`, `deprecated`), always with a border change too.
- Text on any fill is `ink`; text on a solid role fill (bus bar, fragment tag) is `on-strong`.
- To re-brand, change the six role tokens only. Layers, relations and status follow.

## Shapes

Shapes are fixed per type and sub-kind. `hw` cut rectangle (soc octagon, sensor ellipse, actuator hexagon) · `bus`/`net` solid bar · `cloud` dashed zone with `radius-zone` · `node`, `platform`, `req` rectangle · `system`, `app`, `comp`, `state`, `step` rounded box with `radius-node` (component adds the UML glyph; app.ui bottom-rounded; app.gateway hexagon) · `store` barrel · `queue` and `step.io` rhomboid · `data` and `adr` tag · `usecase`, `step.start`/`end` ellipse · `step.decision` and `state.choice` diamond · `iface` lollipop, `iface.port` square · `cls` UML compartments · `actor` stick figure · `state.initial` dot, `state.final` ringed dot, `step.fork`/`join` bar.

## Relations

| Relation | Draw |
| --- | --- |
| part_of, deployed_on, runs_on | as nesting when the view nests by it; else dashed `line`, open vee (deployed_on, runs_on) |
| connects | 2.5px solid `rel-connect`, no head |
| uses | solid `line`, open vee |
| flows_to | solid `rel-flow`, filled triangle |
| provides / requires | solid, no head / dashed, tee (socket) |
| realizes / inherits | dashed / solid, hollow triangle |
| composes / aggregates | solid, filled / hollow diamond at the whole |
| next | solid, filled triangle; label `event [guard] / action` |
| satisfies / decides | dotted `rel-trace` / `rel-decide`, open vee |
| msg sync / async / return | `ink`, filled triangle / open vee / dashed open vee |

Labelled relations exist as `rel` needs (`src`, `dst`, `kind`, `protocol`); unlabelled ones are plain need links.

## Views

| View | Diagram | Nests by | Layout |
| --- | --- | --- | --- |
| context | system context (C4 L1) | — | layered, left → right |
| usecase | UML use case | system boundary | left → right |
| hardware | hardware block diagram | part_of | layered, top → down, orthogonal edges |
| network | vehicle-to-cloud topology | part_of | top → down, orthogonal |
| cloud | cloud / infrastructure | deployed_on, runs_on, part_of | left → right |
| deployment | UML deployment | deployed_on, runs_on, part_of | left → right |
| container | C4 L2 | part_of | left → right |
| component | C4 L3 / UML component | part_of | left → right |
| interface | interface map | — | left → right, linked only |
| dataflow | data flow | part_of | left → right, linked only |
| block | generic block diagram | part_of | left → right, orthogonal |
| layered | all layers stacked in bands | part_of within a layer | bands top → down |
| class | UML class | — | top → down |
| state | UML state machine | owning component | top → down, model order |
| flow | flowchart / activity | swimlanes (`lane`) | top → down, lanes as columns |
| sequence | UML sequence | fragments | fixed grid (`seq-col`, `seq-row`) |
| trace | requirements traceability | — | left → right, linked only |

Scope a view with `root` (element plus contained descendants), `depth`, `closure` (follow dependencies down the stack) and `expand` (neighbours as ghosts). Split any view that passes about 40 elements.

## Typography

- Set diagrams in `font-sans` (IBM Plex Sans); technology, stereotypes, class members and IDs in `font-mono` (IBM Plex Mono).
- `diagram-title` 15/600 above the stage · `group-label` 12/600 for container titles · `node-label` 12/500 inside nodes · `node-meta` 10 mono for `[tech]` and «stereotype» · `edge-label` 10 in `ink-muted` with a `canvas` halo · `class-body` 10.5 mono · `legend` 11.5 · `caption` 13 in `ink-muted`.
- Never wrap a label; the node widens. Keep labels under ~28 characters.

## Spacing and layout

- Pad nodes by `space-3`, containers by `space-4` with 40px for the title; keep `space-4` minimum between boxes.
- Layout spacing: `node-sep` between siblings, `rank-sep` between ranks (×1.6 when edges carry labels).
- Corner radii: `radius-node` elements, `radius-group` containers and frames, `radius-zone` cloud zones only, `radius-control` buttons.
- Line weights: `stroke-hairline` frames and lifelines, `stroke-node` boxes and relations, `stroke-strong` physical links and risk borders.
- Flow direction: structure left → right, physical and behavioural views top → down.

## States and interaction

- Selected element: border `focus-ring` at 2.5px; its neighbourhood stays opaque, everything else drops to 12%.
- Ghost (neighbour outside the scope): 50% opacity, dashed border, `surface` fill.
- Keyboard focus on toolbar controls: 2px solid `focus-ring` outline with 1px offset (≥4.6:1 on `canvas` and `surface` in both themes).
- Tooltip: name, ID, type · layer · technology, summary, "Open in docs →" in `primary-ink`. Double-click opens the element's definition.

## Themes

Light and dark share every name. Dark lightens strokes 30% toward white and raises fill strength to 22%; `ink` stays ≥10:1 on every fill, each `*-ink` ≥4.6:1 on `canvas`. Diagrams follow the page theme live; never hard-code a hex value in a view.

## Using this system with sphinx-needs

Need types, links and fields come from `sphinx_archviz.metamodel.needs_config()`; views are `.. archview::` directives with `:view:` and `:root:`. The Cytoscape stylesheet and the static renderer read these tokens; change a role colour here and both outputs follow. The bundle's `Archviz` helpers (`node`, `relation`, `container`, `umlClass`, `sequence`, `legend`, `frame`) draw the same notation as static SVG for mock-ups and slides.
