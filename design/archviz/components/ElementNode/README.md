# ElementNode

Draws one architecture element as a node: the shape comes from its type (and `kind`), the colours from its layer's role.

**Consumer provides:** `type` (one of the 22 node types), `label`, optional `tech`, `stereotype`, `kind`, `status`, and position `x`, `y` (top-left). Width and height grow with the label unless `w`/`h` are given; `Archviz.size(o)` returns them for hand layout.

| Layer | Types | Role token |
| --- | --- | --- |
| Context | actor, system, extsys (dashed, neutral), usecase | `layer-context` → `primary` |
| Hardware | hw (kinds soc, sensor, actuator), bus | `layer-hardware` → `tertiary` |
| Cloud & infrastructure | cloud, node, net | `layer-infrastructure` → `secondary` |
| Platform | platform | `layer-platform` → `neutral` |
| Applications | app (kinds ui, gateway), store, queue | `layer-application` → `primary` |
| Components | comp, iface (accent), data | `layer-component` → `secondary` |
| Detailed design | cls (see UmlClass) | `layer-design` → `tertiary` |
| Behaviour | state, step, scenario | `layer-behaviour` → `primary` |
| Trace | req, adr (accent) | `layer-trace` → `neutral` |

- Do: name elements by what they are (`Log collector daemon`), put the technology in `tech`, not the label.
- Do: keep labels under ~28 characters; the box widens, it never wraps.
- Don't: colour an element by hand. Change the layer role or the type override in tokens.
- Don't: use `critical` for a layer or type; it is reserved for status.
