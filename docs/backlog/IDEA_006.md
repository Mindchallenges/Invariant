---
id: IDEA_006
type: idea
title: A fix line annotated in parentheses cannot be pasted into a shell
status: proposed
residue: []
history: []
area: core/rules, domains/stats
severity: low
priority: low
---



**The want.** Every command a refusal names should survive being pasted into a
shell exactly as printed.

**What is there now.** Two annotation forms are in use for the prose that
follows a command in a `fixes` line. The `#` form is a shell comment, so the
whole line runs:

```
invariant session plan done <n>     # it is finished
```

The parenthetical form is not, and the shell reports a syntax error on it:

```
invariant session teardown  (the normal end of a session)
invariant pipeline test --phase {phase}  (records the failing run)
```

A third shape is a command named in prose without its required argument —
`invariant stats archive`, which the parser refuses for a missing `session`.

**Found by.** `tests/test_cli.py::TestEveryCommandARuleNamesCanBeRun`, which
puts every command string in `core/rules` and `domains/session` through the
real argument parser. It strips BOTH annotation forms before parsing, which is
what lets it pass over these three — so the test that would catch a future one
is already normalising the existing ones away.

**Why it is an idea and not a bug.** Nobody is stopped: the command up to the
annotation is correct, and a reader who deletes the parenthesis gets what they
wanted. The cost is one paste that fails at the moment somebody is already
blocked, which is exactly the audience a `fixes` line is written for.

**The change.** Convert the two parenthetical annotations to `#`, give
`stats archive` its `<session>` placeholder, and drop the parenthesis-stripping
from the test's normaliser so the form cannot come back. Out of scope for the
increment that found it: that one is `cli/main.py` only, and these live in
`core/rules` and `domains/stats`.
