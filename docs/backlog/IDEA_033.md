---
id: IDEA_033
type: idea
title: swift_xcode records one scoped run over several selectors
status: proposed
residue: []
history: []
priority: high
effort: M
tier: '2'
---

Under a sweep cadence (IDEA_032, ADR-010) a change records its evidence
scoped — `invariant pipeline test --selector <affected tests>` and `invariant
pipeline coverage --selector <affected tests>` — and the compliance reviewer
is told never to use the bare forms. A Swift project on the shipped
`swift_xcode` defaults cannot follow that guidance once the affected set is
larger than one selector: `swift_xcode` deliberately does not override
`Adapter.join_selectors`, so `joined_selectors` refuses any second
`--selector` before anything runs (`pipeline test`, `pipeline coverage`, and
the sweep's re-run). Running one selector per invocation is not a workaround:
`test.all_pass` reads only the LAST run, so a red leg followed by a green one
reads as green — the reason BUG_031 rejected it. What is left is the full
suite, which is exactly what `sweep.no_full_run` exists to discourage. The
proposal: let `swift_xcode` record ONE scoped run over several selectors,
revisiting BUG_031's decision with a way to know the template's shape rather
than guessing it.

**Acceptance:**

* On the shipped `swift_xcode` defaults, `invariant pipeline test --selector A
  --selector B` runs one `xcodebuild` invocation that executes the tests of
  both A and B, and records a single run whose `selectors` lists both.
* The same holds for `invariant pipeline coverage --selector A --selector B`
  through `commands.coverage_one`.
* A project whose `test_one` / `coverage_one` template has been rewritten in a
  shape the adapter cannot recognise still gets today's refusal naming every
  selector, having run and recorded nothing — never a silently wrong command.
* No input yields a single `-only-testing:` flag whose value is a
  comma-joined selector set.
* A failing selector in the set makes the one recorded run red, so
  `test.all_pass` fails.

**Architecture sketch:**

* `adapters/swift_xcode` — a `join_selectors` that renders the repeated
  `-only-testing:` form, guarded so it answers only when it can tell the
  template is the shape it renders for (e.g. the template is the shipped
  default, or the manifest declares how selectors join, such as a
  per-selector fragment the placeholder expands).
* `domains/pipeline` — `joined_selectors` unchanged in contract (adapter
  answers or the caller refuses); the sweep's re-run benefits for free.
* Manifest — only if the chosen guard is a declared join form; then a
  documented key in docs/architecture/manifest.md.

**Risks / open questions:**

* How the adapter knows the template's shape. BUG_031's objection stands: a
  rendering right for the default and wrong for a rewritten template is the
  same defect one layer along. Pick between "recognise the default" and "the
  project declares the join"; the second is more general, the first needs no
  config.
* Zero-executed ambiguity: a set where one selector matches nothing still
  executes the others, so the run's total is not zero and the unmatched
  selector goes unnoticed. Decide whether a per-selector executed count is
  needed (the `.xcresult` bundle can attribute tests to targets/classes) or
  whether that is a follow-up.
* BUG_038: a swift-testing selector can scrape as zero executed; a mixed set
  inherits that until BUG_038's counting half lands.
* xcodebuild command-line length on a large affected set.

**Why now / why this tier:**

IDEA_032 shipped (ac73b18) and made scoped runs the per-change norm; Swift
projects on the defaults are the one ecosystem that cannot follow it for a
multi-selector change, so this is a medium-value item with a strong unlock.

**Related.** [BUG_031](BUG_031.md) (the decision revisited), [IDEA_032](IDEA_032.md)
(the guidance it unblocks), [BUG_038](BUG_038.md) (zero-count ambiguity under
swift-testing). Raised by a consuming Swift project wiring IDEA_032; its
report of a comma-joined `-only-testing:` value did not reproduce from
`--selector` alone (that spelling is unconfirmed).
