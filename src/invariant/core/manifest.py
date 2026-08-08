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
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Mapping

import yaml

from .errors import ManifestError

MANIFEST_RELPATH = Path(".claude") / "invariant.yaml"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class Level(str, Enum):
    """How a rule's verdict is delivered. Not a property of the rule's logic."""

    BLOCK = "block"
    WARN = "warn"
    OFF = "off"

    @classmethod
    def parse(cls, value: Any) -> "Level":
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
    },
    "commands": {
        "build": None,
        "test": None,
        "test_one": None,
        "lint": None,
        "coverage": None,
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
        "gates": {"coverage_min": 90, "coverage_scope": "touched"},
    },
    "enforcement": {"profile": "standard", "rules": {}},
    "session": {
        # Paths exempt from worktree isolation. Configuration and knowledge
        # files are the motivating case: they are data rather than code, every
        # session edits them, and routing them through a branch produces
        # conflicts with no review value.
        "exempt_paths": [".claude/*", ".claude/**", "MEMORY.md"],
        "worktree_prefix": None,
    },
    # The markdown backend stores one file per item, so its path is a
    # directory. The json and sphinx_needs backends treat it as a single file.
    "items": {"backend": "markdown", "path": "docs/backlog", "id_prefix": ""},
    "memory": {"managed": True, "index_generated": True},
    "privacy": {
        "enabled": True,
        "patterns": [
            "home_paths",
            "emails",
            "ip_addresses",
            "tokens",
            "clock_times",
            "links",
        ],
        "extra": [],
        "allow": [],
        "scope": ["docs", "memory", "commit_messages"],
    },
    "agents": {
        "library": [
            "feature-architect",
            "compliance-reviewer",
            "backlog-curator",
            "bug-reproducer",
        ],
        "local": [],
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
    def load(cls, start: Path | None = None, *, required: bool = True) -> "Manifest":
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
    ) -> "Manifest":
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
            for rule_id in (r.strip() for r in disabled.split(",") if r.strip()):
                self.data["enforcement"]["rules"][rule_id] = Level.OFF.value
                self.provenance[f"enforcement.rules.{rule_id}"] = "env:INVARIANT_DISABLE"

    def validate(self) -> None:
        if int(self.data.get("version", 1)) != 1:
            raise ManifestError(
                f"manifest version {self.data['version']} is not supported by "
                f"this release, which understands version 1"
            )
        for rule_id, level in self.data["enforcement"]["rules"].items():
            Level.parse(level)  # raises with a useful message
        scope = self.data["privacy"]["scope"]
        if not isinstance(scope, list):
            raise ManifestError("privacy.scope must be a list")
        backend = self.data["items"]["backend"]
        if backend not in {"markdown", "sphinx_needs", "json", "none"}:
            raise ManifestError(f"unknown work-item backend {backend!r}")

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
