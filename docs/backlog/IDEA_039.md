---
id: IDEA_039
type: idea
title: Draft an agent overlay from a hand-edited installed copy
status: proposed
residue: []
history: []
priority: medium
effort: M
tier: '5'
area: cli/agents, agents overlays
---

**Context:** Since IDEA_031 A6.1, `agents install` skips a hand-edited installed agent copy and names it, with the fix "move the edit into an overlay" (BUG_106 keeps the edit; BUG_051 stopped assets install destroying it). Moving the edit is manual today: the operator has to compare the copy with the current render, find which anchored sections changed, and hand-write a `version: 1` overlay against those anchors. That is tedious and easy to get wrong, and downstream adopters carrying edited copies (Heap B4, unblocked by A6) hit it first. By A6 ruling 13 no migration helper shipped in A6; the ruling asked for it to be filed as its own idea.

**Proposal:** a command that drafts an `agents.overlays` file from an edited installed copy, for example `invariant agents overlay --draft <name>`. It diffs the installed copy against what `agents install` would render now (source plus any existing overlay), maps each difference to the section anchors `invariant agents show <name> --sections` lists, and prints one operation per changed section in overlay schema v1: `replace` for a changed section, `append` where the edit only adds text at a section's end, `insert_after` (with a new id) for a section the copy adds, and `description_append` for a description that only grew. It writes nothing to the manifest. The operator reviews the draft, saves it under the `docs/agents/<name>.yaml` convention and names it in `agents.overlays`; the next install then renders the same text and the copy stops reading as edited.

**Acceptance:**

* On an installed copy whose one section was edited, the draft holds exactly one operation, against that section's anchor, and saving it as the agent's overlay makes `agents install --dry-run` report the agent current, not edited.
* An edit that only adds text at a section's end drafts as `append`; a new heading between two sections drafts as `insert_after` with an id that is not already a section.
* An edit the schema cannot express (a changed `tools`, `model` or other front-matter key, or text outside any anchored section) is named in the output as not carried over, and the command does not report the draft as complete.
* An agent with no local edits drafts nothing and says so; an agent that is not in `agents.library` is refused by name.
* The draft passes the same overlay validation and privacy screen the install applies before it is shown.

**Risks / open questions:**

* Whether the command only prints the draft or can also write it to the conventional path when asked; v1 printing only keeps it out of the manifest and policy files.
* How an edit to a copy that already has an overlay is drafted: a fresh full overlay, or only the delta on top of the existing one.
* An edit that straddles a section boundary may only be expressible as two `replace` operations; the draft should prefer correctness over the smallest overlay.

**Why now / why this tier:** Medium value for a narrow group: adopters with edited copies, Heap B4 the first of them, once each per agent. Effort S to M, inside the agents install plan and renderer that A6 built, so tier 5. It is the follow-up A6 ruling 13 asked for.

**Related:** IDEA_031 (A6 ruling 13; Heap B4), BUG_106, BUG_051, ADR-012 D8 and its A6 as-built notes, cli.md (agents install, agents show), manifest.md ("Agents: the library, the project's own, and overlays").
