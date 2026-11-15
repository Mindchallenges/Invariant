# UmlClass

Draws a UML class, struct or interface type with name, attribute and operation compartments.

**Consumer provides:** `label`, optional `stereotype` (`interface`, `struct`, `enum`), `attributes[]`, `operations[]` with UML visibility marks (`+ - # ~`), `abstract` (italic name), position.

- Members are set in `class-body` (mono); the name in `node-label` weight 600.
- Empty compartments stay visible: an interface shows an empty attribute compartment.
- Relations: `inherits` and `realizes` point to the general type; `composes` and `aggregates` put the diamond at the whole; multiplicities go in the edge label.
