# invariant documentation

Deterministic, scripted state for agent workflows. The user guide is for
anyone adopting the tool in a project of their own; the architecture documents
are the authoritative dependency map (the docs-first gate reads them); the
ADRs record why each design decision was taken; audits record what was checked
against the mission and when.

```{toctree}
:maxdepth: 1
:caption: User guide

guide/index
```

```{toctree}
:maxdepth: 1
:caption: Architecture

architecture/overview
architecture/state_model
architecture/manifest
architecture/cli
```

```{toctree}
:maxdepth: 1
:caption: Decisions

adr/ADR-001-scripted-state-over-prose-rules
adr/ADR-002-session-telemetry
adr/ADR-003-agent-role-gate-placement
adr/ADR-004-a-session-is-a-deliverable-set
adr/ADR-005-session-handoff-launches-a-host
adr/ADR-006-proved-not-fixed-is-a-pipeline-outcome
```

```{toctree}
:maxdepth: 1
:caption: Audits

audits/mission-audit-2026-08
audits/authority-audit-2026-08
```
