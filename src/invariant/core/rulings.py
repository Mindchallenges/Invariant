"""Rulings: what a configuration loosens, and who allowed it (ADR-021 D6 to D8).

A configuration *loosens* a built-in when it sets a registered rule below its
level in the shipped ``standard`` profile, chooses a profile that does (the
``loose`` profile, keyed ``profile``), or lets an ``extra_machines`` entry
shadow a shipped machine (keyed ``machine.<name>``). Each loosening needs a
ruling under ``enforcement.rulings``: who gave it, when, and why (D6).

This module is the one answer to what a configuration loosens, which
``Manifest.validate`` asks at every load, and the writer ``rules loosen`` and
``rules carry-over`` use (D7, D8): it edits the project's own level in place,
line by line, so the comments a configuration carries survive, and it writes
nothing it cannot read back as exactly what it meant to write. Nothing here
asks anyone anything; the confirmation at a terminal is the CLI's.
"""

from __future__ import annotations

import contextvars
import copy
import datetime
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import ManifestError
from .home import user_config_path
from .manifest import Level, _load_profile, _refuse_unknown, profile_names
from .stores import STORE_MANIFEST

#: The ruling key the chosen profile is recorded under (ruling 6).
PROFILE_KEY = "profile"
#: The prefix of the ruling key a shadowing machine is recorded under (D6).
MACHINE_PREFIX = "machine."
#: The profile a loosening is measured against (ruling 1).
BASELINE = "standard"
#: What a ruling records, and nothing else (D6).
FIELDS = ("by", "date", "reason")
#: Who a carried-over ruling names, and why when the entry has no comment (D8).
CARRIED_OVER = "carried over"
CARRIED_REASON = "lowered before rulings were recorded, carried over at the upgrade"

_STRENGTH = {Level.OFF: 0, Level.WARN: 1, Level.ASK: 2, Level.BLOCK: 3}
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: While set, a loosening with no ruling is collected on the manifest instead
#: of refused, so the commands that record one can load the configuration
#: they are about to repair.
_COLLECTING: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "invariant_rulings_collecting", default=False
)


@contextmanager
def collecting() -> Iterator[None]:
    """Load without refusing a loosening that has no ruling (D7, D8)."""
    token = _COLLECTING.set(True)
    try:
        yield
    finally:
        _COLLECTING.reset(token)


@dataclass(frozen=True)
class Loosening:
    """One thing a configuration loosens, keyed as its ruling is."""

    key: str
    #: The rule's configured level, the profile's name, or the machine entry.
    value: str
    #: The rule's level in ``standard``; ``None`` for a profile or a machine.
    baseline: str | None
    #: The provenance label of the level that set it.
    source: str

    @property
    def kind(self) -> str:
        if self.key == PROFILE_KEY:
            return "profile"
        if self.key.startswith(MACHINE_PREFIX):
            return "machine"
        return "rule"

    def describe(self) -> str:
        if self.kind == "profile":
            return (
                f"enforcement.profile is {self.value} (from {self.source}), which "
                f"sets rules below the {BASELINE} profile"
            )
        if self.kind == "machine":
            return (
                f"methodology.extra_machines entry {self.value} (from {self.source}) "
                f"shadows the shipped machine {self.key[len(MACHINE_PREFIX):]} and "
                f"replaces its gates"
            )
        return (
            f"enforcement.rules.{self.key} is {self.value} (from {self.source}), "
            f"below {self.baseline} in the {BASELINE} profile"
        )

    def command(self) -> str:
        """The command that records this loosening's ruling (D7)."""
        to = "" if self.kind == "machine" else f" --to {self.value}"
        return f"invariant rules loosen {self.key}{to} --by <who> --reason <why>"


# -- what a configuration loosens -----------------------------------------------


def weaker(level: Any, than: Any) -> bool:
    """Is ``level`` delivered more softly than ``than``? ``ask`` is below ``block``."""
    return _STRENGTH[_level(level)] < _STRENGTH[_level(than)]


def _level(value: Any) -> Level:
    return value if isinstance(value, Level) else Level.parse(value)


def _profile_loosens(rules: Mapping[str, Any], baseline: Mapping[str, Any], floored) -> bool:
    return any(
        weaker(level, baseline[rule_id])
        for rule_id, level in rules.items()
        if rule_id in baseline and rule_id not in floored
    )


def profile_loosens(name: str) -> bool:
    """Does the shipped profile ``name`` set any rule below ``standard``?"""
    return _profile_loosens(_load_profile(name), _load_profile(BASELINE), ())


def shadows(manifest) -> list[tuple[str, str]]:
    """Each shipped machine an ``extra_machines`` entry shadows: (id, entry).

    Read as ``find_machine_file`` reads the entries, a file by its stem and a
    directory by the machines in it.
    """
    from .machines import shipped_ids

    shipped = set(shipped_ids())
    found: list[tuple[str, str]] = []
    for entry in manifest.get("methodology.extra_machines") or []:
        path = (Path(manifest.root) / str(entry)).resolve()
        if path.is_file():
            files = [path]
        elif path.is_dir():
            files = sorted(path.glob("*.yaml"))
        else:
            files = []
        found += [(f.stem, str(entry)) for f in files if f.stem in shipped]
    return found


def loosenings(manifest) -> list[Loosening]:
    """What the configured levels loosen, in the order rulings are listed.

    Judged on the configuration as written, before the environment's escapes
    (D6): ``INVARIANT_PROFILE`` and ``INVARIANT_DISABLE`` are per-invocation
    routes, not configuration. A floored rule is never a loosening here:
    ``_validate_floor`` refuses it whatever a ruling says (ruling 1). A
    project's own step is the project's to re-level (D1).
    """
    from .steps import step_name

    profile, rules, provenance = manifest.configured()
    baseline = _load_profile(BASELINE)
    chosen = _load_profile(profile)
    floored = set(manifest._floored())
    found: list[Loosening] = []
    if _profile_loosens(chosen, baseline, floored):
        found.append(
            Loosening(PROFILE_KEY, profile, None, provenance.get("enforcement.profile", "default"))
        )
    for rule_id, level in rules.items():
        if step_name(rule_id) is not None or rule_id in floored or rule_id not in baseline:
            continue
        held = chosen.get(rule_id, baseline[rule_id])
        if weaker(level, baseline[rule_id]) and weaker(level, held):
            found.append(
                Loosening(
                    rule_id,
                    Level.parse(level).value,
                    Level.parse(baseline[rule_id]).value,
                    provenance.get(f"enforcement.rules.{rule_id}", "default"),
                )
            )
    source = manifest.source_of("methodology.extra_machines")
    for machine_id, entry in shadows(manifest):
        found.append(Loosening(f"{MACHINE_PREFIX}{machine_id}", entry, None, source))
    return found


def recorded(manifest) -> dict[str, dict[str, Any]]:
    """The rulings the configuration carries, by key; empty with none."""
    value = (manifest.data.get("enforcement") or {}).get("rulings")
    return dict(value) if isinstance(value, Mapping) else {}


def in_force(manifest) -> bool:
    """Is the refusal in force: does a level carry ``enforcement.rulings``?

    A project that has recorded a ruling, or run ``rules carry-over``, has
    one. Until then a loosening without a ruling warns rather than refuses,
    for one release (ruling 5, D8).
    """
    return "rulings" in (manifest.data.get("enforcement") or {})


# -- the load's judgement -------------------------------------------------------


def judge(manifest) -> None:
    """Refuse what D6 refuses, and collect what D8 lets through with a warning.

    Sets ``manifest.unruled`` to the loosenings that carry no ruling. They are
    refused when the refusal is in force (``in_force``), and listed for
    ``doctor`` and ``rules list`` otherwise, or while ``collecting``.
    """
    enforcement = manifest.data.get("enforcement") or {}
    raw = enforcement.get("rulings")
    if "rulings" in enforcement and raw is not None and not isinstance(raw, Mapping):
        raise ManifestError(
            f"enforcement.rulings must be a mapping of what is loosened to its "
            f"ruling, got {raw!r}"
        )
    if raw is None and "rulings" in enforcement:
        enforcement["rulings"] = {}
    rulings = enforcement.get("rulings") or {}
    for dotted, label in manifest.provenance.items():
        if dotted.startswith("enforcement.rulings") and label.startswith("user:"):
            raise ManifestError(
                f"enforcement.rulings is set at the user level ({label[len('user:'):]}): "
                f"a ruling records one project's decision, and the user level is "
                f"every project's default (ADR-021 D6). Remove it there, and record "
                f"each ruling in the project with 'invariant rules loosen'."
            )
    for key, ruling in rulings.items():
        _check_ruling(str(key), ruling)
    found = loosenings(manifest)
    keys = {loosening.key for loosening in found}
    stale = [str(key) for key in rulings if str(key) not in keys]
    if stale:
        raise ManifestError(
            "; ".join(_stale_reason(key) for key in stale)
            + ". A ruling must match the configuration it allows, so the record "
            "cannot drift from it (ADR-021 D6). Remove the entry, or restore the "
            "loosening it records."
        )
    missing = tuple(loosening for loosening in found if loosening.key not in rulings)
    manifest.unruled = missing
    if missing and in_force(manifest) and not _COLLECTING.get():
        raise ManifestError(
            "a loosening needs a ruling: who allowed it, when and why (ADR-021 "
            "ruling 1, D6). "
            + "; ".join(loosening.describe() for loosening in missing)
            + ". Only the user records one, at a terminal of their own: "
            + "; ".join(f"'{loosening.command()}'" for loosening in missing)
            + ". Or remove the loosening."
        )


def _check_ruling(key: str, ruling: Any) -> None:
    where = f"enforcement.rulings.{key}"
    if not isinstance(ruling, Mapping):
        raise ManifestError(
            f"{where} must be a mapping of {', '.join(FIELDS)}, got {ruling!r}"
        )
    _refuse_unknown(where, ruling, FIELDS)
    for name in FIELDS:
        value = ruling.get(name)
        if name == "date" and isinstance(value, datetime.date):
            continue
        if value is None or not str(value).strip():
            raise ManifestError(
                f"{where} has no {name}: a ruling records who gave it, when and why "
                f"(ADR-021 D6)"
            )
    if not isinstance(ruling["by"], str) or "\n" in ruling["by"].strip():
        raise ManifestError(f"{where}.by must be one line naming who gave the ruling")
    if not isinstance(ruling["reason"], str):
        raise ManifestError(f"{where}.reason must be text saying why")
    date = ruling["date"]
    if isinstance(date, datetime.date):
        ruling["date"] = date.isoformat()
    elif not _is_date(date):
        raise ManifestError(f"{where}.date must be a date, YYYY-MM-DD, got {date!r}")


def _is_date(value: Any) -> bool:
    if not isinstance(value, str) or not _DATE.fullmatch(value.strip()):
        return False
    try:
        datetime.date.fromisoformat(value.strip())
    except ValueError:
        return False
    return True


def _stale_reason(key: str) -> str:
    from .rules import get_rule

    if key == PROFILE_KEY or key.startswith(MACHINE_PREFIX) or get_rule(key) is not None:
        return f"enforcement.rulings.{key} records a ruling, but this configuration does not loosen {key}"
    return (
        f"enforcement.rulings.{key} records a ruling for {key!r}, which is no "
        f"registered rule, '{PROFILE_KEY}' or '{MACHINE_PREFIX}<name>'"
    )


def warning(manifest) -> str | None:
    """The one-release warning (D8), or ``None`` when nothing is unruled."""
    missing = getattr(manifest, "unruled", ())
    if not missing:
        return None
    return (
        f"{len(missing)} loosening(s) carry no ruling: "
        + ", ".join(loosening.key for loosening in missing)
        + ". They load for one release (ADR-021 D8); the user records them by "
        "running 'invariant rules carry-over' at a terminal of their own"
    )


# -- where a ruling is written ---------------------------------------------------


@dataclass(frozen=True)
class Target:
    """The file holding the project's own level, and where in it (D7)."""

    path: Path
    #: The keys above the level's own, ``store.repos.<key>`` in a store.
    prefix: tuple[str, ...]
    label: str


def own_level(manifest) -> Target:
    """Where ``rules loosen`` and ``rules carry-over`` write.

    A store project's own level is its block in the store's
    ``invariant.yaml``, so a store serving several repositories is not
    changed for the others; an in-repo project's is its manifest. A project
    defined by its pointer alone keeps no policy at all.
    """
    if not getattr(manifest, "located", True):
        raise ManifestError("no project here: run it in a project's checkout")
    location = manifest.location
    if location.kind == "store":
        return Target(
            Path(location.tree) / STORE_MANIFEST,
            ("store", "repos", str(location.key)),
            manifest.store.repo_label,
        )
    if location.kind == "pointer":
        raise ManifestError(
            "this project is defined by its pointer alone, which may set no policy "
            "key, so it has no level to record a ruling in. Link a store, or keep "
            "the manifest in the repository."
        )
    return Target(Path(location.tree) / location.relpath, (), f"repo:{location.relpath}")


def _source_file(manifest, label: str) -> tuple[Path, tuple[str, ...]] | None:
    """The file and keys a provenance label names, when it is a config level."""
    store = getattr(manifest, "store", None)
    if label.startswith("repo:"):
        return Path(manifest.root) / label[len("repo:"):], ()
    if store is not None and label == store.label:
        return Path(store.root) / STORE_MANIFEST, ()
    if store is not None and label == store.repo_label:
        return Path(store.root) / STORE_MANIFEST, ("store", "repos", str(store.key))
    if label.startswith("user:"):
        return user_config_path(), ("defaults",)
    return None


def _keys_of(loosening: Loosening) -> tuple[str, ...]:
    if loosening.kind == "profile":
        return ("enforcement", "profile")
    if loosening.kind == "machine":
        return ("methodology", "extra_machines")
    return ("enforcement", "rules", loosening.key)


def comment_for(manifest, loosening: Loosening) -> str | None:
    """The comment above the entry that set ``loosening``, as one line (D8).

    The comment lines directly above the entry, else its own trailing
    comment; ``None`` when it has neither, or its file cannot be read.
    """
    source = _source_file(manifest, loosening.source)
    if source is None:
        return None
    path, prefix = source
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    index = _locate(lines, (*prefix, *_keys_of(loosening)))
    if index is None:
        return None
    above: list[str] = []
    cursor = index - 1
    while cursor >= 0 and lines[cursor].strip().startswith("#"):
        above.insert(0, lines[cursor].strip().lstrip("#").strip())
        cursor -= 1
    text = " ".join(part for part in above if part)
    if not text:
        trailing = _TRAILING.search(lines[index])
        text = trailing.group(1).strip() if trailing else ""
    return text or None


# -- the plans ---------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """What a ruling command would write, and whether it can (D7, D8)."""

    target: Target
    #: (keys of the mapping, key, value), each set in the target's level.
    edits: tuple[tuple[tuple[str, ...], str, Any], ...]
    before: str
    #: The file as it would be written; ``None`` when it cannot be edited in
    #: place, and then ``by_hand`` is the block to add and ``reason`` why.
    after: str | None
    by_hand: str
    reason: str = ""


class RulingRefused(ManifestError):
    """A ruling command was asked for something it does not record."""


def plan_loosen(
    manifest,
    key: str,
    *,
    to: str | None,
    by: str,
    reason: str,
    today: datetime.date,
) -> Plan:
    """The level and the ruling ``rules loosen`` writes (D7), or a refusal."""
    by, reason = (by or "").strip(), (reason or "").strip()
    if not by or "\n" in by:
        raise RulingRefused("--by must name, in one line, who gives the ruling")
    if not reason:
        raise RulingRefused("--reason must say why the rule is loosened")
    ruling = {"by": by, "date": today.isoformat(), "reason": reason}
    current = {loosening.key: loosening for loosening in loosenings(manifest)}
    edits: list[tuple[tuple[str, ...], str, Any]] = []
    if key == PROFILE_KEY:
        if to not in profile_names():
            raise RulingRefused(
                f"--to must name a shipped profile: {', '.join(profile_names())}"
            )
        if not profile_loosens(to):
            raise RulingRefused(
                f"the {to} profile sets no rule below {BASELINE}, so choosing it "
                f"loosens nothing and needs no ruling: set enforcement.profile"
            )
        edits.append((("enforcement",), "profile", to))
    elif key.startswith(MACHINE_PREFIX):
        if to is not None:
            raise RulingRefused(
                "a shadowing machine has no level: drop --to, and add its "
                "methodology.extra_machines entry before recording the ruling"
            )
        if key not in current:
            raise RulingRefused(
                f"no methodology.extra_machines entry shadows the shipped machine "
                f"{key[len(MACHINE_PREFIX):]}, so there is nothing to record a "
                f"ruling for"
            )
    else:
        edits += _rule_edit(manifest, key, to)
    edits.append((("enforcement", "rulings"), key, ruling))
    return _plan(own_level(manifest), edits)


def _rule_edit(manifest, key: str, to: str | None) -> list:
    from .rules import get_rule
    from .steps import step_name

    if step_name(key) is not None:
        raise RulingRefused(
            f"{key} is the project's own step, the project's to re-level: set it "
            f"in enforcement.rules, no ruling needed (ADR-021 D1)"
        )
    if get_rule(key) is None:
        raise RulingRefused(
            f"{key!r} is no registered rule, '{PROFILE_KEY}' or "
            f"'{MACHINE_PREFIX}<name>'; 'invariant rules list' shows every id"
        )
    if to is None:
        raise RulingRefused(f"--to must name the level {key} is loosened to")
    level = Level.parse(to)
    if key in manifest._floored():
        raise RulingRefused(
            f"{key} cannot be set below block here, and no ruling lowers it "
            f"(ADR-018 ruling 19, ADR-021 ruling 1). To pass one change, use "
            f"'invariant override', 'pipeline advance --force --reason' or "
            f"'invariant standdown'."
        )
    baseline = _load_profile(BASELINE).get(key, Level.WARN.value)
    if not weaker(level, baseline):
        raise RulingRefused(
            f"{key} at {level.value} is not below {baseline} in the {BASELINE} "
            f"profile, so it loosens nothing and needs no ruling: set it in "
            f"enforcement.rules"
        )
    profile, _rules, _provenance = manifest.configured()
    held = _load_profile(profile).get(key, baseline)
    if not weaker(level, held):
        raise RulingRefused(
            f"the {profile} profile already holds {key} at {Level.parse(held).value}, "
            f"which its own ruling covers ('{PROFILE_KEY}'), so {level.value} needs "
            f"no ruling of its own"
        )
    return [(("enforcement", "rules"), key, level.value)]


def plan_carry_over(manifest, today: datetime.date) -> tuple[Plan | None, list[tuple[Loosening, str]]]:
    """The rulings ``rules carry-over`` writes (D8), and each one's reason.

    One per loosening with no ruling, ``by: carried over``, today, and the
    comment above the entry as the reason when it has one. With none to
    carry, an empty ``enforcement.rulings``, so the refusal is in force from
    then on. ``None`` when the refusal is in force and nothing is unruled.
    """
    carried = [
        (loosening, comment_for(manifest, loosening) or CARRIED_REASON)
        for loosening in getattr(manifest, "unruled", ())
    ]
    if not carried and in_force(manifest):
        return None, []
    target = own_level(manifest)
    edits: list[tuple[tuple[str, ...], str, Any]] = [
        (
            ("enforcement", "rulings"),
            loosening.key,
            {"by": CARRIED_OVER, "date": today.isoformat(), "reason": reason},
        )
        for loosening, reason in carried
    ]
    if not edits:
        edits.append((("enforcement",), "rulings", {}))
    return _plan(target, edits), carried


def _plan(target: Target, edits: Sequence[tuple[tuple[str, ...], str, Any]]) -> Plan:
    full = tuple(((*target.prefix, *keys), key, value) for keys, key, value in edits)
    try:
        before = target.path.read_text(encoding="utf-8")
    except FileNotFoundError:
        before = ""
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError(f"{target.path} cannot be read: {exc}") from exc
    by_hand = _by_hand(full)
    try:
        after = edit_text(before, full)
    except _Unwritable as exc:
        return Plan(target, full, before, None, by_hand, str(exc))
    return Plan(target, full, before, after, by_hand)


def write(plan: Plan, reload) -> None:
    """Write ``plan`` and load the configuration again through ``reload``;
    a load that refuses puts the file back as it was and raises."""
    from .stores import _write_atomic

    if plan.after is None:
        raise ManifestError(f"{plan.target.path} cannot be edited in place: {plan.reason}")
    # The plan was read before the terminal's question, so a file changed
    # while it was asked would lose that change to ``after`` and, on a
    # refusal, to ``before``. Refuse rather than write over it.
    try:
        current = plan.target.path.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError(f"{plan.target.path} cannot be read: {exc}") from exc
    if current != plan.before:
        raise ManifestError(
            f"{plan.target.path} changed since the entries were shown, so nothing "
            f"was written; run the command again"
        )
    _write_atomic(plan.target.path, plan.after)
    try:
        with collecting():
            reload()
    except BaseException:
        # Any failure, not only a refusal, puts the file back as it was.
        _write_atomic(plan.target.path, plan.before)
        raise


def _by_hand(edits: Sequence[tuple[tuple[str, ...], str, Any]]) -> str:
    body: dict[str, Any] = {}
    for keys, key, value in edits:
        node = body
        for part in keys:
            node = node.setdefault(part, {})
        node[key] = copy.deepcopy(value)
    return _dump(body)


# -- the hook's question ----------------------------------------------------------


def is_config_file(manifest, path: Path) -> bool:
    """Is ``path`` a file a config level is read from (D7)?

    An in-repo manifest anywhere (``.claude/invariant.yaml``, in the main
    checkout or a session worktree), the project's store ``invariant.yaml``,
    and the user level.
    """
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    if resolved.name == "invariant.yaml" and resolved.parent.name == ".claude":
        return True
    store = getattr(manifest, "store", None)
    candidates = [user_config_path()]
    if store is not None:
        candidates.append(Path(store.root) / STORE_MANIFEST)
    for candidate in candidates:
        try:
            if resolved == candidate.resolve():
                return True
        except OSError:
            continue
    return False


def changed_rulings(before: str, after: str) -> list[str] | None:
    """The ruling keys ``after`` adds or changes over ``before``.

    Every ``enforcement.rulings`` mapping in the file counts: a manifest's
    own, a store's top level and each of its repository blocks, and the user
    level's ``defaults``. A ruling removed is not one written. ``None`` when
    either text does not read as YAML, so nothing can be told.
    """
    try:
        old = yaml.safe_load(before) if before.strip() else {}
        new = yaml.safe_load(after) if after.strip() else {}
    except yaml.YAMLError:
        return None
    was = dict(_rulings_in(old))
    changed: list[str] = []
    for where, entries in _rulings_in(new):
        prior = was.get(where) or {}
        for key, value in entries.items():
            if key not in prior or prior[key] != value:
                changed.append(str(key))
    return changed


def _rulings_in(node: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], dict]]:
    if not isinstance(node, Mapping):
        return
    for key, value in node.items():
        here = (*path, str(key))
        if here[-2:] == ("enforcement", "rulings"):
            yield here, dict(value) if isinstance(value, Mapping) else {}
        else:
            yield from _rulings_in(value, here)



def changed_ruled(before: str, after: str) -> list[str] | None:
    """The ``ruled`` entries ``after`` adds or changes over ``before``
    (ADR-032 D7, increment 2), each named by where it sits.

    Every ``ruled`` mapping at any depth counts: a rule's, the layer map's,
    an exception's. A list item is keyed by its ``id``, else by its ``path``
    and ``fingerprint``, else by its position, so reordering a list is no
    change. A ruling removed is not one written. ``None`` when either text
    does not read as YAML, so nothing can be told.
    """
    try:
        old = yaml.safe_load(before) if before.strip() else {}
        new = yaml.safe_load(after) if after.strip() else {}
    except yaml.YAMLError:
        return None
    was = dict(_ruled_in(old))
    return [where for where, value in _ruled_in(new) if was.get(where) != value]


def _ruled_in(node: Any, where: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(node, Mapping):
        for key, value in node.items():
            here = f"{where}.{key}" if where else str(key)
            if key == "ruled":
                yield here, value
            else:
                yield from _ruled_in(value, here)
    elif isinstance(node, list):
        for position, item in enumerate(node):
            yield from _ruled_in(item, f"{where}[{_item_key(item, position)}]")


def _item_key(item: Any, position: int) -> str:
    if isinstance(item, Mapping):
        if item.get("id") is not None:
            return str(item["id"])
        if item.get("path") is not None:
            fingerprint = item.get("fingerprint")
            return str(item["path"]) + (f"#{fingerprint}" if fingerprint else "")
    return str(position)

# -- editing a level in place --------------------------------------------------------


class _Unwritable(Exception):
    """The file cannot be edited in place; the block is added by hand."""


_TRAILING = re.compile(r"\s#\s?(.*)$")


def _content(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _find(lines: list[str], lo: int, hi: int, indent: int, key: str) -> int | None:
    pattern = re.compile(" " * indent + r"""(["']?)""" + re.escape(key) + r"\1 *:(?:\s|$)")
    for index in range(lo, hi):
        line = lines[index]
        if _content(line) and _indent(line) == indent and pattern.match(line):
            return index
    return None


def _end(lines: list[str], index: int, indent: int) -> int:
    """The index after the last line of the block a key at ``index`` opens."""
    last = index
    for cursor in range(index + 1, len(lines)):
        if not _content(lines[cursor]):
            continue
        if _indent(lines[cursor]) <= indent:
            break
        last = cursor
    return last + 1


def _child_indent(lines: list[str], index: int, end: int, indent: int) -> int:
    for cursor in range(index + 1, end):
        if _content(lines[cursor]):
            return _indent(lines[cursor])
    return indent + 2


def _locate(lines: list[str], keys: Sequence[str]) -> int | None:
    """The line of the key at ``keys``, or ``None``; nothing is created."""
    lo, hi, indent, index = 0, len(lines), 0, None
    for key in keys:
        index = _find(lines, lo, hi, indent, key)
        if index is None:
            return None
        end = _end(lines, index, indent)
        lo, hi, indent = index + 1, end, _child_indent(lines, index, end, indent)
    return index


def _inline(line: str) -> str:
    """What follows the key's colon on its own line, its comment dropped."""
    rest = line.split(":", 1)[1] if ":" in line else ""
    return _TRAILING.sub("", " " + rest).strip()


def _open(lines: list[str], keys: Sequence[str]) -> tuple[int, int, int]:
    """The block of the mapping at ``keys``, created where it is missing:
    (first line, end, the indent of its keys)."""
    lo, hi, indent = 0, len(lines), 0
    for key in keys:
        index = _find(lines, lo, hi, indent, key)
        if index is None:
            lines.insert(hi, " " * indent + _dump_key(key) + ":")
            index = hi
        inline = _inline(lines[index])
        if inline in ("{}", "null", "~"):
            lines[index] = lines[index].split(":", 1)[0] + ":"
        elif inline:
            raise _Unwritable(f"{'.'.join(keys)} is written inline, so it is not edited in place")
        end = _end(lines, index, indent)
        lo, hi, indent = index + 1, end, _child_indent(lines, index, end, indent)
    return lo, hi, indent


def _set(lines: list[str], keys: Sequence[str], key: str, value: Any) -> None:
    lo, hi, indent = _open(lines, keys)
    rendered = [" " * indent + line for line in _dump({key: value}).splitlines()]
    index = _find(lines, lo, hi, indent, key)
    if index is None:
        lines[hi:hi] = rendered
        return
    if not isinstance(value, Mapping):
        trailing = _TRAILING.search(lines[index])
        if trailing and len(rendered) == 1:
            rendered[0] += "  # " + trailing.group(1).strip()
    lines[index:_end(lines, index, indent)] = rendered


def edit_text(text: str, edits: Sequence[tuple[tuple[str, ...], str, Any]]) -> str:
    """``text`` with each edit set, every other line as it was.

    Refused (``_Unwritable``) when the result would not read back as exactly
    the old YAML with the edits set: a flow-style mapping, a document that is
    not a mapping, or anything the line edit misjudged.
    """
    try:
        old = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as exc:
        raise _Unwritable(f"the file is not valid YAML: {exc}") from exc
    if old is None:
        old = {}
    if not isinstance(old, Mapping):
        raise _Unwritable("the file does not hold a mapping")
    expected = copy.deepcopy(dict(old))
    lines = text.splitlines()
    for keys, key, value in edits:
        node = expected
        for part in keys:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[key] = copy.deepcopy(value)
        _set(lines, keys, key, value)
    after = "\n".join(lines) + "\n"
    try:
        reread = yaml.safe_load(after)
    except yaml.YAMLError as exc:
        raise _Unwritable(f"the edit would not read as YAML: {exc}") from exc
    if reread != expected:
        raise _Unwritable("the edit would not read back as exactly the settings meant")
    return after


def _dump(value: Any) -> str:
    return yaml.safe_dump(
        value, sort_keys=False, default_flow_style=False, allow_unicode=True, width=10**6
    )


def _dump_key(key: str) -> str:
    return _dump({key: None}).split(":", 1)[0] if key else key
