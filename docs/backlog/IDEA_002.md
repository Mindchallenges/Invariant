---
id: IDEA_002
type: idea
title: A privacy scan reports nothing about what an exemption marker suppressed
status: proposed
residue:
- 'REAL-CORPUS DATA POINT, from a consuming project (Heap), 2026-08-24, via BUG_053''s
  reporter. The cost this entry predicts is already being paid and is measurable:
  to suppress FOUR false ip_addresses matches, that project added exemption markers
  that — because an exemption reaches to the end of its block (BUG_045) — make 58
  lines across two files exempt from a BLOCKING gate. A 14:1 ratio of silenced lines
  to suppressed findings, and every one of those lines is a region where a real violation
  would now pass silently. The entry''s argument was that a reader of the VERDICT
  cannot see what a marker set aside; this measurement adds that a reader of the DIFF
  cannot easily see it either once the markers are numerous, which weakens BUG_045''s
  structural answer — the reach is two blocks, so a file-wide exemption is a diff
  full of markers — exactly where it was strongest. Note the ratio is a property of
  markup with long blocks, reST with 20-line paragraphs, rather than of the exemption
  design, so read it as an upper bound rather than as typical. The four findings it
  was spent on are BUG_053''s.'
history: []
area: core/privacy
severity: low
priority: medium
slug: report-what-a-marker-suppressed
---

Deferred out of BUG_045 deliberately, so that the scope change and a new
reporting surface were not one diff.

`scan_text` drops an exempted line before any pattern runs, so nothing
downstream can tell "this text is clean" from "this text has three findings
behind two markers". The verdict `privacy.clean` prints, and `invariant privacy
scan`'s output, are identical in both cases.

That is the shape this repository already refuses elsewhere. `overview.md` says
it of the coverage levers: *an exclusion nobody can see is indistinguishable
from a gate that did not run*, and both `coverage_exclude` and
`coverage_baseline` name what they set aside in the verdict for exactly that
reason. The privacy exemption is the same kind of lever and does not.

BUG_045's answer to the abuse case is structural — the reach is two blocks, so
a file-wide exemption is a diff full of markers. That holds, and it only helps
a reader who is looking at the diff. A reader looking at the *verdict* still
sees nothing.

Sketch: collect exempted findings alongside the reported ones rather than
skipping the line before matching, and have the scan and the verdict end with a
count — "N match(es) suppressed by M marker(s)" — with the detail available
behind the existing `--reveal`/`--verbose` flags. Redaction rules apply
unchanged: a suppressed match is still a match and must not be quoted by
default. The cost to watch is the return type: `scan_text` returns a plain list
today and several callers unpack it, so the count wants its own accessor rather
than a widened tuple.
