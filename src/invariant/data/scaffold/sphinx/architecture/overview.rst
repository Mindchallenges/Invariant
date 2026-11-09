{{heading}}

This page is the authoritative map of {{title}}'s components and the
dependencies between them. It exists for a gate, not for a shelf: the
docs-first rule reads this directory before any design state can be left
(``docs.read_architecture``), and a structural change, such as a new
component, a new or removed dependency, or a changed flow or schema, must
update it before the work can land (``docs.updated``). When the code and this
page disagree, fix one of them in the same change.

Layers
------

Replace the diagram below with the real layers. Every dependency the code has
should appear as an arrow here; an arrow that only exists in the code is one a
design cannot be checked against.

.. mermaid::

   graph TD
{{diagram}}

**Dependency rule:** arrows point down and never back up. State which layer
may import which, and name the exceptions so a reviewer can tell a deliberate
one from a leak.

Component responsibilities
--------------------------

One ``{{element}}`` need per component. Its content says what it owns, what it
never does, and which other elements it ``uses``; the "never does" is what
stops two components from growing the same responsibility. A decision that
shapes an element links to it, and the build checks every link.

{{elements}}

Compliance checklist
--------------------

The checklist the project's reviewing agents read as canonical is generated
from the guidelines ruleset: :doc:`../checklist`, and each rule as a need in
:doc:`../rules`. Until the project has a ruleset, these hold:

- Every dependency the change introduces is drawn in the Layers diagram.
- A new component is a new node there and a new element need above.
- Arrows point down: no layer imports from the layer above it.
- Touched source files are tested to the configured coverage floor.
- A persisted schema changes through the documented safe-migration pattern,
  never by altering records in place.
- No personal data in docs, memory, reports or commit messages.
{{sections}}
