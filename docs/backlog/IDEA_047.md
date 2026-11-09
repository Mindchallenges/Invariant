---
id: IDEA_047
type: idea
title: The dart adapter reads Dart for guideline checks, comment-only edits and workspace
  source areas
status: proposed
residue: []
history: []
priority: low
effort: M
area: adapters/dart
---

**Context:** ADR-019 D7 built no lexical knowledge into the `dart` and `flutter` adapters beyond the declaration reader `carries_executable_code` uses. So on a `.dart` file a guideline `pattern` rule reading `code`, `comments` or `strings` and a `boundary` rule cannot be answered, a comment-only `.dart` edit is still held to the coverage floor (no `executable_text`), and `source_areas` is the base answer (top-level directories holding `.dart`), which names `test` and `tool` as areas in a pub workspace.

**Proposal:** a Dart region reader (line, doc and nestable block comments; single, double, triple and raw strings; `${}` interpolation as code) shared by `lexical_mask` and `executable_text`; `references` from `import`/`export`/`part` URIs, resolving relative paths and `package:<own package>/` to `lib/`; `source_areas` as the `lib/src/*` directories of the root package and of each workspace member.

**Acceptance:**

* A pattern rule with `in: comments` matches inside `//` and nested `/* */` comments in a `.dart` file and not the same text inside a string.
* A comment-only `.dart` edit is set aside by `coverage.meets_floor` as comment-only.
* A boundary rule forbidding `lib/src/ui` from importing `lib/src/data` reports a `package:` or relative import that crosses it.
* `source_areas` of a pub workspace names each member's `lib/src` subdirectories and never `test`.

**Risks / open questions:**

* `${}` nesting inside strings is the lexing trap; the reader must answer `None` rather than guess.
* Conditional imports (`if (dart.library.io)`) name two targets; whether both count as references.

**Why now / why this tier:** Low: matters once a Dart project writes guideline rules or hits a comment-only coverage block. Filed so ADR-019 D7 is on record.

**Related:** ADR-019, ADR-012 D4, IDEA_038 (the same gap for node), BUG_114.
