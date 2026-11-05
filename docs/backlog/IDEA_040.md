---
id: IDEA_040
type: idea
title: A project's records can live in a home store, outside its repository
status: proposed
residue: []
history: []
priority: high
effort: XL
tier: '3'
area: core/manifest, hosts/claude_code, core/rules, session landing
---

**Context:** Adopting invariant commits its files to the repository it governs: the manifest and host assets under `.claude/`, and docs and backlog under the configured paths. That is wrong for a repository whose content must stay its own. The user wants invariant on four cloned git repositories that are themselves documentation projects (Sphinx, sphinx-needs), with invariant's own documents kept out of them entirely.

**Proposal:** opt in per project to keep its invariant records in a store under `~/.invariant`, the way a host keeps per-project data under its own home directory. invariant and Heap keep their in-repo layout.

* One folder per project under `~/.invariant` (which already holds `projects.json` and oracle state), keyed on repo identity, because a path key breaks when the repo moves.
* Each store is its own git repo. A remote is optional, so the user can link and share it or not; publishing stays off by default.
* An optional pointer in the repo names the docs remote, so the store can be pulled at start. It is the one small file that lands in the repo. A repo that must stay untouched links from the home side instead.
* Pull at start: an offline or failed pull starts from the local copy with a clear warning; a diverged local copy stops and asks the user, with no automatic merge.

Out of v1: one home project listing several repos (see the shared-parent option below). It stays a possible later extension.

**Options compared and rejected (tested 2026-11-05 on throwaway repos):**

* Each repo separately, with invariant's paths moved to a hidden `.invariant` folder. This works today: doctor passed, session start and isolate worked, the docs folder was untouched; the full pipeline to merge and a real Sphinx build were not run. But `.claude/` and `.invariant/` still get committed to each repo, and init always scaffolds `docs/architecture/overview.md` into the Sphinx folder. Keeping those files out of git fails: the session worktree reports no `.claude/invariant.yaml`.
* A shared parent directory with the repos nested inside. Unsupported. Init and doctor succeed in a non-git folder, but session start cannot derive a base branch, and with `--base` given, isolate fails with "not a git repository". From inside a nested clone the walk-up finds the parent's manifest and fails the same way. Making the parent a git repo lets sessions run, but the worktree holds none of the clones. It would redefine what a project is across session and gate code.

**Acceptance:**

* A project linked from the home side, with no invariant file in its repo, runs a session from start through isolate, gates and merge. Afterwards its git history and working tree hold only the project's own changes, init has scaffolded nothing into its docs folder, and moving the repo to another path finds the same store.
* The store is a git repo that works with no remote. Linking a remote makes it shareable, and nothing is pushed unless publishing was turned on.
* With the pointer set, a fresh machine pulls the store before work starts. An offline or failed pull starts from the local copy and warns; a diverged local copy stops and asks, leaving both sides unchanged.
* A session that changes the project and owes a doc update is held by the docs gate until the store carries that update, and clears it once it does. If only one of the project repo and the store lands, the operator is told which, and can finish the other without redoing the work.
* A project with the in-repo layout behaves as it does today, with each hook firing once. A repo that is not registered sees no output from any user-level hook.

**Engine work known so far (from reading code, not run, unless noted):**

* Manifest location is fixed at `.claude/invariant.yaml`: `MANIFEST_RELPATH` in `core/manifest.py` and `Manifest.find_root`, 28 references across core, CLI and oracle. Absolute store paths already resolve (`Manifest.path_for`; tested).
* Hooks and agents: `hosts/claude_code.py` wires `$CLAUDE_PROJECT_DIR/.claude/hooks` and installs agents into the repo's `.claude`. They would move to user-level host settings.
* Gates that read the branch: `_docs_updated` in `core/rules.py` looks for changes under the docs root in the session diff, which a home store never appears in. The baseline gate's base-copy reader, also in `core/rules.py`, reads the manifest from the base branch through `MANIFEST_RELPATH`.
* A session changes a project repo and a docs repo, so landing has to handle one succeeding without the other.
* Docs-only projects: doctor fails on unset `commands.build` and `commands.test`.

**Build order agreed:** (1) an ADR settling manifest lookup, the docs gate over a home store, pull rules and partial landing, before any code; (2) manifest lookup and host wiring, proven on one of the four repos; (3) move stores and adapt gates; (4) later, one home project listing several repos.

**Risks / open questions (for the ADR):**

* What counts as repo identity for the store key, given clones, forks and a changed remote. The key is also a folder name on disk, so it should not carry a URL or a username.
* Whether the coverage gate needs adjusting for a repo with no code (not tested). BUG_002 and BUG_064 handled gates inapplicable to a docs-only change, not to a docs-only project.
* Added at filing, not yet discussed with the user. How sessions isolate inside a store: one store serves parallel sessions, but it is not checked out per worktree, so the isolation gates and the own-file-set check have no tree to judge.
* Added at filing. Attribution: ADR-011 re-roots a hook only for a path matching a registered live worktree, so a read or edit in a store path names no session. `docs.read_architecture` depends on this.
* Added at filing. `privacy.clean`: a store with a remote is shareable, so its commits need the same screening, and the gate today sits on the project's commits and review boundary.
* Added at filing. Whether `items.exempt` (BUG_032) and "Declared stores are writable stores" still hold for a store outside the repo, and whether the oracle's index, keyed by state root, is affected. Runtime state already sits in the git common directory, outside the tree.

**Why now / why this tier:** The user prioritised this as the next work, to adopt invariant on the four repos. Tier 3 because it redefines where a project's records live across manifest, hosts, gates and landing: effort XL and multi-phase.

**Related:** ADR-011 and BUG_040 (cross-project recording), ADR-013 (per-ecosystem commands, one possible way to declare a Sphinx build), BUG_032, BUG_072 (exempt stores), installation.md ("Several projects, one machine"), overview.md ("Where things live at rest"), manifest.md ("Declared stores are writable stores", "Resolution order"), state_model.md ("Attribution"), oracle.md (`ProjectIndex`).
