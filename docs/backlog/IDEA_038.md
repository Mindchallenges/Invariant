---
id: IDEA_038
type: idea
title: The node adapter reads JS/TS for guideline pattern and boundary checks
status: proposed
residue: []
history: []
priority: medium
effort: M
tier: '5'
area: adapters/node, domains/guidelines
---

**Context:** IDEA_031 A3 adds the guideline checkers per ecosystem adapter: the pattern kind, which matches within a chosen region of a file (`in: code`, `comments` or `strings`), and the boundary kind (import layering). Both need the adapter to know the language: the pattern kind needs a lexical mask that separates code, comments and string literals, and the boundary kind needs the file's import references. By user ruling (IDEA_031 A3, DN-9), A3 does not build these for the node adapter, so on a node file neither check kind can give a verdict.

**Proposal:** the node adapter gains a JS/TS lexical mask (line and block comments; single-, double- and template-literal strings, including template substitutions; regular-expression literals where they can be told from division) and relative-import references (`import ... from './x'`, `export ... from`, `require('./x')`, dynamic `import('./x')`), resolved to project paths. With them, a guideline pattern scoped to `code`, `comments` or `strings` and a boundary rule both work on `.js`/`.ts` files.

**Acceptance:**

* A pattern rule with `in: comments` matches text inside a `//` or `/* */` comment in a `.ts` file and not the same text inside a string or code.
* A pattern rule with `in: strings` matches inside single-quoted, double-quoted and template-literal strings, and not inside a `${...}` substitution's code.
* A boundary rule forbidding one directory from importing another reports a relative `import`, `export ... from`, `require` or dynamic `import()` that crosses it, naming the file and line.
* A file the lexer cannot read confidently gets no verdict that reads as a pass; the check reports it as unchecked.

**Risks / open questions:**

* Regular-expression literals versus division is the classic JS lexing trap; a regex-first lexer may need a conservative fallback.
* Whether bare package specifiers or path aliases (tsconfig `paths`) count as references in v1, or only relative paths.
* Node coverage is still unreadable until BUG_018; this idea does not depend on it, but a node-heavy adopter will want both.

**Why now / why this tier:** Low-to-medium value and not scheduled: it matters only to projects that declare node and write guideline rules for it (Heap's web side, per ADR-013). A follow-up to IDEA_031 A3, filed so the gap DN-9 leaves is on record.

**Related:** IDEA_031 (A3), ADR-013, ADR-012, BUG_018, BUG_103 (the stacked-ecosystem lexer mismatch on the coverage floor).
