"""Manifest loading, layered resolution, and provenance.

The manifest is the only project-aware artifact (``docs/architecture/manifest.md``).
Values are resolved in layers, later winning:

  1. library defaults
  2. adapter defaults, primary ecosystem first
  3. the enforcement profile (rule levels only)
  4. explicit manifest values
  5. environment overrides

Every resolved value remembers which layer produced it. "Why is this rule
blocking me" has to be answerable without reading the library's source, and a
layered config that cannot explain itself is a config people disable.
"""

from __future__ import annotations

import copy
import os
import posixpath
import sys
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from .errors import ManifestError
from .privacy import DEFAULT_PATTERNS

MANIFEST_RELPATH = Path(".claude") / "invariant.yaml"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class Level(str, Enum):
    """How a rule's verdict is delivered. Not a property of the rule's logic.

    ``ASK`` is the level that puts a human back in the loop. The agent host's
    permission contract carries an *ask* decision alongside allow and deny, and
    for a long time this library only ever emitted deny — so a rule whose
    failure the operator might legitimately want to wave through had exactly
    two settings, "stop the work" and "say nothing" (BUG_049/AUTH-01).

    It is delivered as an ask only where there is somebody to ask: the
    ``pre-tool-use`` surface. Everywhere else — the ``stop`` surface, a
    transition gate, ``invariant check`` — an ``ask`` verdict STOPS the work,
    in the same do-not-relax convention the rest of the engine uses. A
    spurious block is visible and self-correcting; a silent pass is neither.
    The one-shot override token (``invariant override``) is the appeal that
    reaches those surfaces.
    """

    BLOCK = "block"
    ASK = "ask"
    WARN = "warn"
    OFF = "off"

    @classmethod
    def parse(cls, value: Any) -> Level:
        # YAML 1.1 resolves a bare `off` to the boolean False (and `on` to
        # True). Someone writing the obvious thing in a manifest would
        # otherwise get an error about a value they never typed, so accept the
        # booleans as the words they were written as.
        if value is False:
            return cls.OFF
        if value is True:
            raise ManifestError(
                "'on' is not an enforcement level — YAML read it as a boolean. "
                "Use 'block' or 'warn', quoted if you like."
            )
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            raise ManifestError(
                f"{value!r} is not a valid enforcement level; use one of: "
                + ", ".join(level.value for level in cls)
            ) from None


def _unknown_rule(rule_id: str) -> bool:
    """Is this id absent from the rule registry?

    A deferred import, and it has to be: `core.rules` imports this module, so
    a top-level one is a cycle. That is the whole reason the check was not
    here already, and it is a solvable reason.

    The registry is the complete answer, not a partial one. A methodology may
    name any id it likes as a gate, but an id with no registered rule already
    FAILS at evaluation — there is no path by which a project legitimately
    carries a rule this build does not know about.
    """
    from .rules import get_rule

    return get_rule(rule_id) is None


def _did_you_mean(rule_id: str) -> str:
    """The registered id nearest to what was typed, where there is one.

    A typo three characters off should be answered with the id the operator
    meant. Where nothing is close the message says nothing rather than
    proposing an unrelated rule, which would be a worse kind of confident.
    """
    from difflib import get_close_matches

    from .rules import all_rules

    near = get_close_matches(rule_id, [spec.id for spec in all_rules()], n=1, cutoff=0.6)
    return f"; did you mean {near[0]!r}?" if near else ""


DEFAULTS: dict[str, Any] = {
    "version": 1,
    "project": {"name": "unnamed", "ecosystems": ["generic"]},
    "paths": {
        # null means "derive it". State must be SHARED by every worktree of a
        # repository: a session created from the main checkout has to stay
        # visible from the worktree it just created, and a per-worktree state
        # directory makes it invisible at exactly that moment. The git common
        # directory is already shared by every worktree and already untracked,
        # so it is where the state belongs. Set a path here to override.
        "state": None,
        "docs": "docs/architecture",
        "memory": ".claude/memory",
        "memory_index": "MEMORY.md",
        # Where `invariant stats report --write` puts its artifact (ADR-002
        # D4). Declared here rather than appended to the CLI's write-scope
        # invariant as one more exception: that invariant now reads "the state
        # root and the stores the manifest declares", so the next feature that
        # needs a writable location adds a key instead of widening a list. The
        # second exception is always easier to justify than the first.
        "reports": "docs/reports",
    },
    "commands": {
        "build": None,
        "test": None,
        "test_one": None,
        "lint": None,
        "coverage": None,
        # Optional second leg, for ecosystems where measuring coverage and
        # READING the measurement are separate programs. Where it is unset the
        # coverage command speaks for itself and nothing changes; where it is
        # set, the suite's exit status comes from the first command and the
        # numbers from the second, so neither can be mistaken for the other.
        "coverage_report": None,
    },
    # Static substitutions for command templates.
    "vars": {},
    # Substitutions resolved by RUNNING a command, not by writing the value
    # down. An operational identifier written into a config is both a privacy
    # leak and a value that goes stale silently; a command that resolves it is
    # neither.
    "discover": {},
    "docs": {
        "format": "markdown",
        "diagrams": "mermaid",
        "index": None,
        "require_diagram_update": True,
    },
    "methodology": {
        "pipeline": "tdd",
        "extra_machines": [],
        # `coverage_exclude` is a list of glob patterns held OUT of the floor
        # — never out of the measurement. The row is still produced and the
        # number still reported; only the below-floor verdict skips it, and
        # the verdict names every path it set aside and why. Empty by
        # default: excluding anything from a floor is a policy decision, and
        # it belongs in a diff someone can review rather than in a habit each
        # author has to remember.
        # `coverage_baseline` is a path to a recorded per-file shortfall the
        # floor is ratcheted against: below the floor but at or above the
        # record does not block, below the record is a regression and does,
        # and no record at all fails exactly as it did before. Null by
        # default, because the relaxation must rest on evidence somebody
        # wrote down rather than on the absence of any.
        # `non_source` is a list of glob patterns the project declares are not
        # code — versioned design bundles, fixtures, vendored assets. A
        # DIFFERENT question from `coverage_exclude`, and the pair is easy to
        # confuse: that one means "measured, but not held to the floor" and is
        # applied after the missing-row check on purpose, so that suppressing
        # a path from the reporter cannot buy a pass. This one means "no run in
        # this project could produce a row for it at all", and is applied where
        # the built-in documentation suffixes are. Nothing in the manifest
        # could say that, so a project versioning design bundles beside the
        # code they specify had a blocking gate with no local fix and no work
        # that could clear it. Empty by default and every match named in the
        # verdict with the pattern that spent it: this is the project asserting
        # something the engine cannot check.
        "gates": {
            "coverage_min": 90,
            "coverage_scope": "touched",
            "coverage_exclude": [],
            "coverage_baseline": None,
            "non_source": [],
        },
    },
    "enforcement": {"profile": "standard", "rules": {}},
    "session": {
        # Paths exempt from worktree isolation. Configuration and knowledge
        # files are the motivating case: they are data rather than code, every
        # session edits them, and routing them through a branch produces
        # conflicts with no review value.
        "exempt_paths": [".claude/*", ".claude/**", "MEMORY.md"],
        "worktree_prefix": None,
        # Where session worktrees are created. Unset means beside the
        # repository, which keeps the worktree off every path the repository
        # itself tracks. A project sets this when its host trusts one specific
        # location — some hosts confirm before moving their permission root
        # into a directory they do not manage, and the confirmation is not
        # suppressible by any permission rule, so the only way to stop asking
        # is to put the worktree where the host already trusts.
        #
        # A relative value is resolved against the repository root. Placing
        # worktrees INSIDE the repository is therefore supported, but it makes
        # exempt_paths load-bearing in a way it is not otherwise: see
        # _project_relative_forms, which resolves a path in a linked worktree
        # against that worktree alone so a directory prefix cannot exempt the
        # sources underneath it.
        "worktree_dir": None,
        # Whether publishing to a remote is part of the session lifecycle.
        #
        # Off by default. A session's job is to get reviewed work onto the base
        # branch; the worktree and its branch are then removed and the work is
        # already visible to every other worktree, because they share one object
        # database. Publishing costs a CI run and is a decision about the
        # project rather than about the session, so it belongs to whoever is
        # making that decision — not to whichever agent happened to finish last.
        "push": False,
        # How long an OPEN plan may sit without moving before the closeout gate
        # treats the branch as stranded rather than parked.
        #
        # A session that spans several agent-host sessions rests with commits
        # on its branch by design — that is the whole point of a plan
        # outliving one host — so "there is unmerged work here" stops being
        # evidence of anything on its own. What still distinguishes the two
        # cases is whether the plan is being WORKED: a parked session's
        # deliverables move; an abandoned one's do not.
        #
        # Seven days, because the distinction being drawn is "paused over a
        # weekend" against "abandoned", and a bound that cannot tell those
        # apart is worse than none.
        "plan_stale_days": 7,
    },
    # The markdown backend stores one file per item, so its path is a
    # directory. The json and sphinx_needs backends treat it as a single file.
    "items": {
        "backend": "markdown",
        "path": "docs/backlog",
        # An ADDITIONAL project prefix; the item type already contributes its
        # own stem, so "BUG_" here mints BUG_BUG_001.
        "id_prefix": "",
        # numeric -> BUG_001. slug -> BUG_DRAIN_TREATS_EVERY_FAILURE, for a
        # backlog whose entries are referenced by name, where a number is both
        # inconsistent with its neighbours and unlinkable.
        "id_strategy": "numeric",
        # Work items are data every session touches, like configuration and
        # memory, so the store is exempt from worktree isolation by default.
        # Set false where backlog edits should be reviewed with the code.
        "exempt": True,
    },
    "memory": {"managed": True, "index_generated": True},
    "privacy": {
        "enabled": True,
        # One authoritative default set, owned by core/privacy. A second copy
        # here is how a bare PrivacyScanner() and the manifest path came to
        # enable different patterns (AUD-15).
        "patterns": list(DEFAULT_PATTERNS),
        "extra": [],
        "allow": [],
        # Keywords resolve through paths.*: docs -> paths.docs, memory ->
        # paths.memory AND paths.memory_index (the index is a concentrate of
        # exactly the text the memory scope protects), reports ->
        # paths.reports (the one rendering of the stats store that leaves the
        # machine). Any other entry is a project-relative path prefix, which
        # is how a project widens scope beyond what paths.* names (AUD-15).
        "scope": ["docs", "memory", "reports", "commit_messages"],
    },
    "agents": {
        "library": [
            "feature-architect",
            "compliance-reviewer",
            "backlog-curator",
            "bug-reproducer",
        ],
        "local": [],
        # Which agents satisfy which pipeline role. Installing an agent does
        # not make anything use it, so this mapping is what the agent-role
        # gates read. A role left empty makes its gate "not applicable" rather
        # than passing: a project with no review agent should see the gate skip,
        # not see it go green.
        "roles": {
            "design": ["feature-architect"],
            "review": ["compliance-reviewer"],
        },
    },
    "host": {"kind": "claude_code"},
}


@dataclass
class Manifest:
    """A fully resolved configuration plus the provenance of every value."""

    root: Path
    path: Path
    data: dict[str, Any]
    provenance: dict[str, str] = field(default_factory=dict)
    _discovery_cache: dict[str, str] = field(default_factory=dict, repr=False)

    # -- discovery -----------------------------------------------------

    @staticmethod
    def find_root(start: Path | None = None) -> Path | None:
        """Walk upward for a directory containing the manifest."""
        current = Path(start or Path.cwd()).resolve()
        for candidate in [current, *current.parents]:
            if (candidate / MANIFEST_RELPATH).is_file():
                return candidate
        return None

    @classmethod
    def load(cls, start: Path | None = None, *, required: bool = True) -> Manifest:
        root = cls.find_root(start)
        if root is None:
            if required:
                raise ManifestError(
                    "no .claude/invariant.yaml found in this directory or any "
                    "parent; run 'invariant init' to create one"
                )
            root = Path(start or Path.cwd()).resolve()
            return cls.from_raw({}, root=root, path=root / MANIFEST_RELPATH)

        path = root / MANIFEST_RELPATH
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ManifestError(f"{MANIFEST_RELPATH} is not valid YAML: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise ManifestError(f"{MANIFEST_RELPATH} must contain a mapping")
        return cls.from_raw(dict(raw), root=root, path=path)

    # -- resolution ----------------------------------------------------

    @classmethod
    def from_raw(
        cls,
        raw: Mapping[str, Any],
        *,
        root: Path,
        path: Path,
        env: Mapping[str, str] | None = None,
    ) -> Manifest:
        env = os.environ if env is None else env

        data = copy.deepcopy(DEFAULTS)
        provenance: dict[str, str] = {}
        _record(provenance, data, "default")

        # Layer 2 — adapter defaults, primary ecosystem first.
        ecosystems = _ecosystems(raw)
        for eco in ecosystems:
            adapter_defaults = _adapter_defaults(eco)
            if adapter_defaults:
                _merge(data, adapter_defaults, provenance, f"adapter:{eco}")

        # Layer 3 — the enforcement profile supplies rule levels only.
        profile_name = str(
            _dig(raw, "enforcement", "profile") or data["enforcement"]["profile"]
        )
        profile_rules = _load_profile(profile_name)
        _merge(
            data,
            {"enforcement": {"rules": profile_rules}},
            provenance,
            f"profile:{profile_name}",
        )

        # Layer 4 — explicit manifest values.
        _merge(data, raw, provenance, "manifest")

        manifest = cls(root=Path(root), path=Path(path), data=data, provenance=provenance)

        # Layer 5 — environment escapes.
        manifest._apply_env(env)
        manifest.validate()
        return manifest

    def _apply_env(self, env: Mapping[str, str]) -> None:
        if profile := env.get("INVARIANT_PROFILE"):
            rules = _load_profile(profile)
            self.data["enforcement"]["profile"] = profile
            self.data["enforcement"]["rules"].update(rules)
            self.provenance["enforcement.profile"] = "env:INVARIANT_PROFILE"
            for rule_id in rules:
                self.provenance[f"enforcement.rules.{rule_id}"] = "env:INVARIANT_PROFILE"

        if state_root := env.get("INVARIANT_STATE_ROOT"):
            self.data["paths"]["state"] = state_root
            self.provenance["paths.state"] = "env:INVARIANT_STATE_ROOT"

        if disabled := env.get("INVARIANT_DISABLE"):
            # Loud, and NOT a refusal. This variable is an escape hatch reached
            # in a hurry by somebody who is already blocked; refusing to load
            # the whole manifest because one token was mistyped would wedge
            # exactly that caller. It used to do neither — a token naming no
            # rule was written straight into the map and silently did nothing,
            # so `privacy.klean,docs.read_architecture` disabled the second and
            # ignored the first without a word (BUG_050).
            for rule_id in (r.strip() for r in disabled.split(",") if r.strip()):
                if _unknown_rule(rule_id):
                    print(
                        f"invariant: INVARIANT_DISABLE names {rule_id!r}, which "
                        f"is not a rule{_did_you_mean(rule_id)}. It was ignored; "
                        f"the rule you meant is still enforced.",
                        file=sys.stderr,
                    )
                    continue
                self.data["enforcement"]["rules"][rule_id] = Level.OFF.value
                self.provenance[f"enforcement.rules.{rule_id}"] = "env:INVARIANT_DISABLE"

    def validate(self) -> None:
        if int(self.data.get("version", 1)) != 1:
            raise ManifestError(
                f"manifest version {self.data['version']} is not supported by "
                f"this release, which understands version 1"
            )
        # Both halves of every entry. The VALUE has always been parsed; the KEY
        # was never checked against anything, and `rule_level()` answers WARN
        # for an id it does not hold — so an override naming a rule that does
        # not exist loaded without error, did nothing, and showed a provenance
        # entry that read as though it had taken (BUG_050).
        #
        # An override is the one control this library hands back, so a silent
        # no-op here costs more than anywhere else. The instinct is already in
        # the codebase twice: `core.rules.evaluate` fails loudly on a gate
        # naming a rule that does not exist, because "a gate naming a rule that
        # does not exist would otherwise pass silently forever", and AUD-08
        # added exactly this check for a mistyped methodology. Neither had been
        # extended to the ids an operator uses to exercise authority.
        #
        # Refused at load rather than warned: a manifest is read on every
        # command, so a warning here is printed hundreds of times and read none
        # of them. The escape-hatch case that must NOT be refused is
        # `INVARIANT_DISABLE`, and it is handled in `_apply_env` above.
        for rule_id, level in self.data["enforcement"]["rules"].items():
            Level.parse(level)  # raises with a useful message
            if _unknown_rule(rule_id):
                raise ManifestError(
                    f"enforcement.rules names {rule_id!r}, which is not a rule "
                    f"this build carries{_did_you_mean(rule_id)}.\n"
                    f"  An override that names nothing is silently discarded, "
                    f"and the rule you meant keeps enforcing.\n"
                    f"  'invariant rules list' shows every id."
                )
        scope = self.data["privacy"]["scope"]
        if not isinstance(scope, list):
            raise ManifestError("privacy.scope must be a list")
        backend = self.data["items"]["backend"]
        if backend not in {"markdown", "sphinx_needs", "json", "none"}:
            raise ManifestError(f"unknown work-item backend {backend!r}")
        # A mistyped methodology used to surface only when a machine load
        # happened to run — and several gates skipped, silently and
        # non-blocking, exactly then (AUD-08). Existence is checked here, at
        # load, where a configuration error belongs; whether the file parses
        # as a machine of the right kind is still the loader's question.
        methodology = str(self.data["methodology"]["pipeline"] or "tdd")
        from .machines import find_machine_file

        try:
            find_machine_file(methodology, self)
        except ManifestError as exc:
            raise ManifestError(f"methodology.pipeline: {exc}") from exc

    # -- access --------------------------------------------------------

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return default
            node = node[part]
        return node

    def source_of(self, dotted: str) -> str:
        return self.provenance.get(dotted, "default")

    def rule_level(self, rule_id: str) -> Level:
        rules = self.data["enforcement"]["rules"]
        if rule_id in rules:
            return Level.parse(rules[rule_id])
        return Level.WARN

    def command_for(self, name: str, **extra: str) -> str:
        """Resolve a command template, running discovery commands as needed.

        Discovery results are cached for the life of the process: a device
        identifier should be looked up once per invocation, not once per
        placeholder.
        """
        template = self.get(f"commands.{name}")
        if not template:
            raise ManifestError(
                f"no command configured for {name!r}; set commands.{name} in "
                f"{MANIFEST_RELPATH} or select an adapter that supplies it"
            )

        values: dict[str, str] = {}
        values.update({k: str(v) for k, v in (self.get("vars") or {}).items()})
        values.update(extra)

        for placeholder in _placeholders(template):
            if placeholder in values:
                continue
            discovery = (self.get("discover") or {}).get(placeholder)
            if discovery is None:
                raise ManifestError(
                    f"command {name!r} needs {{{placeholder}}}, which is neither "
                    f"in vars nor in discover. Prefer a discover entry — a "
                    f"command that resolves the value at runtime — over writing "
                    f"the value down."
                )
            values[placeholder] = self._discover(placeholder, discovery)

        try:
            return template.format(**values)
        except KeyError as exc:
            raise ManifestError(
                f"command {name!r} references unknown placeholder {exc}"
            ) from None

    def _discover(self, key: str, command: str) -> str:
        if key in self._discovery_cache:
            return self._discovery_cache[key]
        import subprocess

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,  # the exit status is read below, with its stderr
            )
        except subprocess.TimeoutExpired as exc:
            raise ManifestError(
                f"discovery for {key!r} timed out; the command was: {command}"
            ) from exc
        value = proc.stdout.strip()
        if proc.returncode != 0 or not value:
            raise ManifestError(
                f"discovery for {key!r} produced nothing (exit {proc.returncode}). "
                f"The command was: {command}"
                + (f"\n{proc.stderr.strip()}" if proc.stderr.strip() else "")
            )
        self._discovery_cache[key] = value
        return value

    def path_for(self, key: str) -> Path:
        """Resolve a configured path against the project root."""
        value = self.get(f"paths.{key}")
        if value is None:
            raise ManifestError(f"no path configured for {key!r}")
        return (self.root / value).resolve()

    @property
    def ecosystems(self) -> list[str]:
        return list(self.get("project.ecosystems") or ["generic"])

    @property
    def state_root(self) -> Path:
        """Where runtime state lives — shared across every worktree.

        An explicit ``paths.state`` wins. Otherwise the git common directory,
        which every linked worktree of a repository resolves to the same place
        and which git already ignores. Only outside a repository does this fall
        back to a path under the project root.
        """
        configured = self.get("paths.state")
        if configured:
            return (self.root / configured).resolve()

        from . import git

        common = git.common_dir(self.root)
        if common is not None:
            return (common / "invariant" / "state").resolve()
        return (self.root / ".claude" / "invariant" / "state").resolve()

    def iter_provenance(self) -> Iterator[tuple[str, Any, str]]:
        for dotted, value in sorted(_flatten(self.data)):
            yield dotted, value, self.source_of(dotted)


# -- helpers -----------------------------------------------------------


def project_relative(value: Any, bases: Iterable[Path | str | None] = ()) -> str | None:
    """The canonical project-relative spelling of a declared path or glob.

    Every manifest value that is MATCHED against a path — an exempt pattern, a
    store a rule tests membership of, a scope prefix, a coverage glob — is
    compared against the project-relative spelling of the target, and that
    spelling never starts with ``./``, never ends with ``/`` and never carries
    an empty or ``..`` segment. A declaration spelled any of those ways used to
    be compared as written and could therefore match nothing: the exemption,
    scope or exclusion it declared was off, and nothing said so (BUG_071).

    So equivalent spellings are reduced to one before anything is compared:
    ``./docs/backlog``, ``docs/backlog/``, ``docs//backlog`` and
    ``docs/x/../backlog`` are all ``docs/backlog``. Normalised rather than
    refused at load, because refusing would turn spellings that already
    worked — a trailing ``/`` on ``items.path`` — into a manifest that no
    longer loads, and because a spelling the library can read unambiguously is
    not an error the operator should have to fix.

    An absolute value is spelled against the first of ``bases`` (the project's
    checkouts) that contains it. ``None`` means the value names nothing inside
    the project — an absolute path under none of the bases, or a relative one
    whose ``..`` climbs out — and a caller building match patterns must DROP
    it: a declaration outside the project cannot exempt, scope or exclude a
    path outside it either. The project root itself comes back as ``"."``,
    which no project-relative target spells, so it stays exactly as inert as
    it always was rather than widening to "everything".

    Lexical only: nothing is read from disk, so a glob is normalised the same
    way as a path. Storage is a different question and is not asked here —
    `path_for` and the item backends join the raw value onto the root, which
    already accepts every one of these spellings.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if posixpath.isabs(text):
        # POSIX keeps a leading `//` as implementation-defined; nothing here
        # means that, so collapse it before normalising.
        candidate = posixpath.normpath("/" + text.lstrip("/"))
        for base in bases:
            if base is None:
                continue
            for spelling in _base_spellings(Path(base)):
                if candidate == spelling:
                    return "."
                if candidate.startswith(spelling.rstrip("/") + "/"):
                    return candidate[len(spelling.rstrip("/")) + 1 :]
        return None
    normal = posixpath.normpath(text)
    if normal == ".." or normal.startswith("../"):
        return None
    return normal


def _base_spellings(base: Path) -> list[str]:
    """A checkout's absolute spelling as written and as resolved.

    Both, because the declaration may have been written through a symlinked
    prefix the resolved root no longer carries, or the other way round.
    """
    spellings = [posixpath.normpath(str(base))]
    try:
        resolved = posixpath.normpath(str(base.resolve()))
    except OSError:  # pragma: no cover - a resolve that cannot answer
        return spellings
    if resolved not in spellings:
        spellings.append(resolved)
    return spellings


def _ecosystems(raw: Mapping[str, Any]) -> list[str]:
    value = _dig(raw, "project", "ecosystems")
    if not value:
        return list(DEFAULTS["project"]["ecosystems"])
    return [str(v) for v in value]


def _dig(mapping: Mapping[str, Any], *keys: str) -> Any:
    node: Any = mapping
    for key in keys:
        if not isinstance(node, Mapping) or key not in node:
            return None
        node = node[key]
    return node


def _adapter_defaults(ecosystem: str) -> dict[str, Any]:
    """Adapter-supplied manifest defaults, imported lazily to avoid a cycle."""
    from ..adapters import defaults_for  # local import: adapters import Level

    return defaults_for(ecosystem)


def _load_profile(name: str) -> dict[str, str]:
    path = DATA_DIR / "profiles" / f"{name}.yaml"
    if not path.is_file():
        available = sorted(p.stem for p in (DATA_DIR / "profiles").glob("*.yaml"))
        raise ManifestError(
            f"unknown enforcement profile {name!r}; available: "
            + (", ".join(available) or "(none installed)")
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ManifestError(f"profile {name!r} is not valid YAML: {exc}") from exc
    return dict(raw.get("rules", {}) or {})


def _merge(
    target: dict[str, Any],
    source: Mapping[str, Any],
    provenance: dict[str, str],
    origin: str,
    prefix: str = "",
) -> None:
    """Recursive merge that records provenance for every leaf it sets."""
    for key, value in source.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, Mapping) and isinstance(target.get(key), Mapping):
            _merge(target[key], value, provenance, origin, f"{dotted}.")
        else:
            target[key] = copy.deepcopy(value)
            provenance[dotted] = origin
            if isinstance(value, Mapping):
                _record(provenance, value, origin, f"{dotted}.")


def _record(
    provenance: dict[str, str],
    node: Mapping[str, Any],
    origin: str,
    prefix: str = "",
) -> None:
    for key, value in node.items():
        dotted = f"{prefix}{key}"
        provenance[dotted] = origin
        if isinstance(value, Mapping):
            _record(provenance, value, origin, f"{dotted}.")


def _placeholders(template: str) -> list[str]:
    """The ``{name}`` placeholders in a command template, in order, deduped."""
    import string

    seen: list[str] = []
    for _literal, field_name, _spec, _conv in string.Formatter().parse(template):
        if field_name and field_name not in seen:
            seen.append(field_name)
    return seen


def _flatten(node: Mapping[str, Any], prefix: str = "") -> Iterator[tuple[str, Any]]:
    for key, value in node.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, Mapping):
            yield from _flatten(value, f"{dotted}.")
        else:
            yield dotted, value
