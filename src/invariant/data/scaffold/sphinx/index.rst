{{heading}}

The architecture and design of {{title}}, as a sphinx-needs project: every
requirement, decision, architecture element, screen, guideline rule and work
item is a need with an id, linked to what it shapes. ``invariant docs build``
builds it strictly, and a change that touches it lands only on a green build.

.. toctree::
   :maxdepth: 2

   {{architecture}}
   requirements
   decisions/index
   ui
   rules
   checklist
   backlog
