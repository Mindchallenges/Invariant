---
id: IDEA_004
type: idea
title: invariant uninstall — a first-class way to remove what init installed
status: proposed
residue: []
history: []
area: hosts/claude_code
severity: low
priority: medium
slug: uninstall-command
---

**The want.** `invariant uninstall` — remove the hook shims, unwire the entries
this library added to the host settings, and say what is being left behind.

**Why.** Adoption is one command. Withdrawal is hand surgery: delete each
`.claude/hooks/invariant-*.sh`, then hand-edit the JSON that `_wire_settings`
wrote — merged into the operator's own settings file, so it cannot simply be
deleted — and know which entries were the library's. Miss one and the shim
still fires; the shim then finds no `invariant` on PATH and exits silently by
design, so the leftover is invisible until it is not. `invariant doctor` will
report a half-removed install as broken and offer `hooks install`, pushing back
toward the state the operator was trying to leave.

The right to stop using a tool is part of the operator's authority over their
own project, and it is the part this library currently makes hardest. A tool
that is easy to adopt and awkward to remove is asking for a trust it has not
earned yet.

**Shape.**

```
invariant uninstall [--keep-manifest] [--keep-assets] [--dry-run]
  removes N hook shim(s) from .claude/hooks/
  removes N hook entr(ies) from .claude/settings.json  (yours are untouched)
  leaves  .claude/invariant.yaml           (--keep-manifest is the default)
  leaves  the state directory, with its N session records
  leaves  .claude/agents/*, docs/backlog/*, .claude/memory/*
```

Two properties matter more than the command existing:

* **It removes only what it wrote.** `_wire_settings` recognises its own entries
  by the shim path already; removal is the same match, and anything else in the
  settings file is left exactly as found. The same care `_wire_settings` takes
  with malformed JSON — refuse rather than overwrite — applies here.
* **It never deletes the operator's data.** Sessions, work items, memories,
  architecture docs and the manifest survive by default, so uninstalling is not
  a decision about the backlog. `--dry-run` enumerates before anything moves.

**Effort.** Small. The install side is one method and one settings writer; the
inverse is symmetric and the identity test already exists.

**Related.** [IDEA_003](IDEA_003.md) is the reversible version of this and
should be the recommended step before it — a stand-down that works is what makes
uninstalling rare. [BUG_049](BUG_049.md) is the same authority question at the
level of a single call.

**Provenance.** Raised by a review of the implementation against the project's
founding position: the operator holds the authority, which includes the
authority to leave. Reported 2026-08-20.
