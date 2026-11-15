"""Conformance scenarios: the model, the known answers, and the comparison.

The engine layer of the conformance harness (IDEA_031 A1, ADR-012 D11). A
scenario is a small fixture repository (``tree/``), the manifest it runs
under, the steps that drive it, and the known answer those steps must reach.
This module loads and validates a scenario, reduces what a replay observed to
an :class:`Outcome`, compares that with the known answer, and renders any
divergence. It reads files and nothing else.

What it never does is evaluate a rule. Every gate outcome it reasons about is
one the CLI reported, through the ``--json`` payloads of ``pipeline gates``
and ``pipeline advance``, and every verdict standing one ``guidelines
verdicts show`` reported, so the harness answers exactly as the engine does
and cannot drift from it. Running the steps is the replay's job, which lives
in ``cli/conformance`` because it drives the CLI in-process.

Prose is never compared. An outcome is a rule id and a status per
destination, the names each refusal gave (the blocking rules of a refused
advance, the predicates of a refused verdict submission), and the answer
each judgement guideline stood at wherever the verdicts were shown.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .errors import UsageError
from .rules import get_rule
from .verdicts import CURRENT, ENGINE, MISSING, PREDICATES, STALE
from .verdicts import STATUSES as VERDICT_STATUSES

#: Where the shipped scenarios live: one directory each, holding
#: ``scenario.yaml`` and ``tree/`` (ADR-012 D11, ruling DN1).
SCENARIO_DIR = Path(__file__).resolve().parent.parent / "data" / "conformance"

SCENARIO_FILE = "scenario.yaml"
TREE_DIR = "tree"

#: What a gate can come to. ``ask`` is reported as ``block``, because outside
#: ``pre-tool-use`` an unanswered ask stops the work (``Evaluated.blocks``).
STATUSES = ("pass", "block", "warn", "skip")

#: Where a step runs: the seeded repository, or the session worktree that a
#: ``session isolate --execute`` step made beside it.
PLACES = ("root", "worktree")

#: What a judgement guideline can stand at where the verdicts are shown: no
#: verdict, one that no longer holds, the engine's own N/A, or the status of
#: the current verdict (ADR-012 D5, D11).
VERDICT_ANSWERS = (MISSING, STALE, ENGINE, *VERDICT_STATUSES)

#: The CLI words that show the verdicts, which ``expect.verdicts`` reads.
SHOW_VERDICTS = ("guidelines", "verdicts", "show")

_TOP_KEYS = frozenset({"description", "manifest", "steps", "expect"})
_EXPECT_KEYS = frozenset({"gates", "refusals", "verdicts"})
_COMMON_STEP_KEYS = frozenset({"kind", "id", "in"})

#: Each step kind, the keys it requires and the keys it may carry.
_STEP_KINDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "cli": (frozenset({"argv"}), frozenset()),
    "hook": (frozenset({"event", "payload"}), frozenset()),
    "write": (frozenset({"path", "content"}), frozenset()),
    "commit": (frozenset(), frozenset({"message"})),
    "verdicts": (frozenset({"entries"}), frozenset()),
}


class ScenarioError(UsageError):
    """A scenario is malformed, or cannot be replayed as written."""


@dataclass(frozen=True)
class Step:
    """One thing a replay does.

    ``label`` is the step's ``id`` when it has one, else ``#<position>``
    counted from 1. A refusal is keyed by it.
    """

    kind: str
    label: str
    where: str = "root"
    argv: tuple[str, ...] = ()
    event: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)
    path: str = ""
    content: str = ""
    message: str = "conformance step"
    #: A ``verdicts`` step's submission: D5 objects, submitted in bulk.
    entries: tuple[Any, ...] = ()


@dataclass(frozen=True)
class Expected:
    """A known answer.

    ``gates`` names, per destination, every rule and the status it must
    reach; a destination it does not name is not compared. ``refusals`` names
    every step that must be refused and what the refusal names (the blocking
    rules of an advance, the predicates of a verdict submission), and is
    compared whole: a refusal it does not name is a divergence.
    ``verdicts`` names, per step that shows the verdicts, every owed
    judgement guideline and its answer (:data:`VERDICT_ANSWERS`); a step it
    does not name is not compared.
    """

    gates: Mapping[str, Mapping[str, str]]
    refusals: Mapping[str, tuple[str, ...]]
    verdicts: Mapping[str, Mapping[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    manifest: Mapping[str, Any]
    steps: tuple[Step, ...]
    expected: Expected
    tree: Path


@dataclass(frozen=True)
class StepResult:
    """What one replayed step left behind.

    ``argv`` is what reached the CLI, ``--json`` included; ``document`` the
    JSON it printed, or ``None`` when it printed none.
    """

    label: str
    kind: str
    argv: tuple[str, ...]
    exit_code: int
    document: Any


@dataclass(frozen=True)
class Outcome:
    """A replay reduced to what the known answer is about."""

    gates: Mapping[str, Mapping[str, str]]
    refusals: Mapping[str, tuple[str, ...]]
    verdicts: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    def as_expected(self) -> Expected:
        """This outcome as a known answer, for comparing two replays."""
        return Expected(gates=self.gates, refusals=self.refusals, verdicts=self.verdicts)


@dataclass(frozen=True)
class Divergence:
    where: str
    expected: str
    actual: str


# -- loading -----------------------------------------------------------------


def scenario_names(root: Path = SCENARIO_DIR) -> list[str]:
    """The scenarios under ``root``, by directory name, sorted."""
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / SCENARIO_FILE).is_file())


def load_named(name: str, root: Path = SCENARIO_DIR) -> Scenario:
    if name not in scenario_names(root):
        known = ", ".join(scenario_names(root)) or "none"
        raise ScenarioError(f"no scenario named {name!r}; shipped: {known}")
    return load(root / name)


def load(directory: Path) -> Scenario:
    """Read and validate one scenario directory, or refuse it whole."""
    name = directory.name
    path = directory / SCENARIO_FILE
    if not path.is_file():
        raise ScenarioError(f"{name}: no {SCENARIO_FILE} in {directory}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{name}: {SCENARIO_FILE} is not valid YAML: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ScenarioError(f"{name}: {SCENARIO_FILE} must be a mapping")
    _refuse_unknown(name, "the scenario", raw, _TOP_KEYS)
    missing = sorted(_TOP_KEYS - set(raw))
    if missing:
        raise ScenarioError(f"{name}: missing {', '.join(missing)}")
    tree = directory / TREE_DIR
    if not tree.is_dir():
        raise ScenarioError(f"{name}: no {TREE_DIR}/ directory to seed the repository from")
    if not isinstance(raw["manifest"], Mapping):
        raise ScenarioError(f"{name}: manifest must be a mapping")
    steps = _steps(name, raw["steps"])
    expected = _expected(name, raw["expect"], {s.label: s for s in steps})
    return Scenario(
        name=name,
        description=str(raw["description"]).strip(),
        manifest=raw["manifest"],
        steps=steps,
        expected=expected,
        tree=tree,
    )


def _refuse_unknown(name: str, what: str, raw: Mapping, allowed: Iterable[str]) -> None:
    unknown = sorted(str(k) for k in set(raw) - set(allowed))
    if unknown:
        raise ScenarioError(f"{name}: unknown key(s) in {what}: {', '.join(unknown)}")


def _steps(name: str, raw: object) -> tuple[Step, ...]:
    if not isinstance(raw, list):
        raise ScenarioError(f"{name}: steps must be a list")
    steps: list[Step] = []
    labels: set[str] = set()
    for position, item in enumerate(raw, start=1):
        where = f"step {position}"
        if not isinstance(item, Mapping):
            raise ScenarioError(f"{name}: {where} must be a mapping")
        kind = item.get("kind")
        if kind not in _STEP_KINDS:
            raise ScenarioError(
                f"{name}: {where}: unknown step kind {kind!r}; "
                f"known: {', '.join(sorted(_STEP_KINDS))}"
            )
        required, optional = _STEP_KINDS[kind]
        _refuse_unknown(name, where, item, _COMMON_STEP_KEYS | required | optional)
        absent = sorted(required - set(item))
        if absent:
            raise ScenarioError(f"{name}: {where}: a {kind} step needs {', '.join(absent)}")
        place = item.get("in", "root")
        if place not in PLACES:
            raise ScenarioError(f"{name}: {where}: 'in' must be root or worktree")
        label = str(item["id"]) if "id" in item else f"#{position}"
        if label in labels:
            raise ScenarioError(f"{name}: {where}: step label {label!r} is used twice")
        labels.add(label)
        steps.append(_step(name, where, kind, label, place, item))
    return tuple(steps)


def _step(name: str, where: str, kind: str, label: str, place: str, item: Mapping) -> Step:
    if kind == "cli":
        argv = item["argv"]
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            raise ScenarioError(f"{name}: {where}: argv must be a list of strings")
        return Step(kind, label, place, argv=tuple(argv))
    if kind == "hook":
        if not isinstance(item["payload"], Mapping):
            raise ScenarioError(f"{name}: {where}: a hook payload must be a mapping")
        return Step(kind, label, place, event=str(item["event"]), payload=item["payload"])
    if kind == "write":
        rel = PurePosixPath(str(item["path"]))
        if rel.is_absolute() or ".." in rel.parts:
            raise ScenarioError(f"{name}: {where}: a write path must be relative, inside the tree")
        return Step(kind, label, place, path=str(rel), content=str(item["content"]))
    if kind == "verdicts":
        # Only a list: an entry of the wrong shape is the engine's to refuse,
        # and a scenario may submit one to watch it do so.
        if not isinstance(item["entries"], list):
            raise ScenarioError(f"{name}: {where}: a verdicts step's entries must be a list")
        return Step(kind, label, place, entries=tuple(item["entries"]))
    return Step(kind, label, place, message=str(item.get("message", Step.message)))


def _expected(name: str, raw: object, labels: Mapping[str, Step]) -> Expected:
    if not isinstance(raw, Mapping):
        raise ScenarioError(f"{name}: expect must be a mapping")
    _refuse_unknown(name, "expect", raw, _EXPECT_KEYS)
    raw_gates = {} if raw.get("gates") is None else raw["gates"]
    raw_refusals = {} if raw.get("refusals") is None else raw["refusals"]
    if not isinstance(raw_gates, Mapping) or not isinstance(raw_refusals, Mapping):
        raise ScenarioError(f"{name}: expect.gates and expect.refusals must be mappings")
    gates: dict[str, dict[str, str]] = {}
    for destination, rules in raw_gates.items():
        if not isinstance(rules, Mapping):
            raise ScenarioError(f"{name}: expect.gates.{destination} must be a mapping")
        for rule_id, status in rules.items():
            _require_rule(name, f"expect.gates.{destination}", rule_id)
            if status not in STATUSES:
                raise ScenarioError(
                    f"{name}: expect.gates.{destination}.{rule_id}: status {status!r} "
                    f"is not one of {', '.join(STATUSES)}"
                )
        gates[str(destination)] = {str(k): str(v) for k, v in rules.items()}
    refusals: dict[str, tuple[str, ...]] = {}
    for label, rules in raw_refusals.items():
        if label not in labels:
            raise ScenarioError(f"{name}: expect.refusals names {label!r}, but no step has that id")
        if not isinstance(rules, list):
            raise ScenarioError(f"{name}: expect.refusals.{label} must be a list of rule ids")
        for rule_id in rules:
            if str(rule_id) not in PREDICATES:
                _require_rule(name, f"expect.refusals.{label}", rule_id, predicates=True)
        refusals[str(label)] = tuple(sorted(str(r) for r in rules))
    return Expected(gates=gates, refusals=refusals,
                    verdicts=_expected_verdicts(name, raw.get("verdicts"), labels))


def _expected_verdicts(
    name: str, raw: object, labels: Mapping[str, Step]
) -> dict[str, dict[str, str]]:
    """``expect.verdicts``: per step that shows the verdicts, each guideline's
    answer. Guideline ids are the scenario ruleset's, so they are not checked
    against the registry; the answers are."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ScenarioError(f"{name}: expect.verdicts must be a mapping")
    verdicts: dict[str, dict[str, str]] = {}
    for label, rules in raw.items():
        where = f"expect.verdicts.{label}"
        step = labels.get(label)
        if step is None:
            raise ScenarioError(f"{name}: expect.verdicts names {label!r}, but no step has that id")
        if step.kind != "cli" or tuple(step.argv[:3]) != SHOW_VERDICTS:
            raise ScenarioError(
                f"{name}: {where}: step {label!r} does not show the verdicts "
                f"({' '.join(SHOW_VERDICTS)})"
            )
        if not isinstance(rules, Mapping):
            raise ScenarioError(f"{name}: {where} must be a mapping")
        for rule_id, answer in rules.items():
            if answer not in VERDICT_ANSWERS:
                raise ScenarioError(
                    f"{name}: {where}.{rule_id}: answer {answer!r} is not one of "
                    f"{', '.join(VERDICT_ANSWERS)}"
                )
        verdicts[str(label)] = {str(k): str(v) for k, v in rules.items()}
    return verdicts


def _require_rule(name: str, where: str, rule_id: object, *, predicates: bool = False) -> None:
    if get_rule(str(rule_id)) is None:
        also = f", nor a verdict predicate ({', '.join(PREDICATES)})" if predicates else ""
        raise ScenarioError(f"{name}: {where} names unregistered rule {rule_id!r}{also}")


# -- reduction -----------------------------------------------------------------


def status_of(gate: Mapping[str, Any]) -> str:
    """One gate payload (``verdicts_payload``'s shape) as a status."""
    if gate.get("skipped"):
        return "skip"
    if gate.get("ok"):
        return "pass"
    level = gate.get("level")
    if level in ("block", "ask"):
        return "block"
    if level == "warn":
        return "warn"
    return "skip"


def reduce(results: Sequence[StepResult]) -> Outcome:
    """What the steps showed: each destination's gates, every refusal, and
    the verdicts each step that showed them saw.

    A destination observed more than once keeps its LAST observation, which
    is the one the scenario's later steps acted on. A refusal names the
    rules that blocked it and, for a refused verdict submission, the
    predicates its problems were named by.
    """
    gates: dict[str, dict[str, str]] = {}
    refusals: dict[str, tuple[str, ...]] = {}
    verdicts: dict[str, dict[str, str]] = {}
    for result in results:
        document = result.document if isinstance(result.document, Mapping) else {}
        for destination, payload in _observed(result.argv, document).items():
            gates[destination] = {g["rule"]: status_of(g) for g in payload}
        shown = _shown(result.argv, document)
        if shown is not None:
            verdicts[result.label] = shown
        if result.exit_code != 0 or _denied(document):
            named = {
                g["rule"] for g in document.get("gates") or () if status_of(g) == "block"
            }
            named |= {
                str(r["predicate"]) for r in document.get("refused") or ()
                if isinstance(r, Mapping) and "predicate" in r
            }
            refusals[result.label] = tuple(sorted(named))
    return Outcome(gates=gates, refusals=refusals, verdicts=verdicts)


def answer_of(standing: Mapping[str, Any]) -> str:
    """One owed rule's standing (``Standing.record``) as its answer: a current
    verdict's status, else the standing itself."""
    if standing.get("standing") == CURRENT:
        return str(standing.get("status"))
    return str(standing.get("standing"))


def _shown(argv: Sequence[str], document: Mapping[str, Any]) -> dict[str, str] | None:
    """The answers a ``guidelines verdicts show`` document gave, or ``None``."""
    words = [w for w in argv if not w.startswith("-")]
    if tuple(words[:3]) != SHOW_VERDICTS or not isinstance(document.get("owed"), list):
        return None
    return {
        str(s["rule"]): answer_of(s) for s in document["owed"]
        if isinstance(s, Mapping) and "rule" in s
    }


def _observed(argv: Sequence[str], document: Mapping[str, Any]) -> dict[str, list]:
    words = list(argv)
    if "by_destination" in document:
        return dict(document["by_destination"])
    if "pipeline" not in words or "gates" not in document:
        return {}
    if "advance" in words:
        targets = [w for w in words[words.index("advance") + 1:] if not w.startswith("-")]
        return {targets[0]: document["gates"]} if targets else {}
    if "--state" in words and words.index("--state") + 1 < len(words):
        return {words[words.index("--state") + 1]: document["gates"]}
    return {}


def _denied(document: Mapping[str, Any]) -> bool:
    output = document.get("hookSpecificOutput")
    return isinstance(output, Mapping) and output.get("permissionDecision") == "deny"


# -- comparison ------------------------------------------------------------------


def compare(expected: Expected, outcome: Outcome, *, exhaustive: bool = False) -> list[Divergence]:
    """Every way ``outcome`` differs from ``expected``.

    Destinations, and steps that showed the verdicts, the known answer does
    not name are not compared, unless ``exhaustive`` — which is how two
    replays are held to each other.
    """
    found = _compare_each("gates to", expected.gates, outcome.gates, exhaustive)
    found += _compare_each("verdicts at", expected.verdicts, outcome.verdicts, exhaustive)
    for label in _ordered(expected.refusals, outcome.refusals):
        want = expected.refusals.get(label)
        got = outcome.refusals.get(label)
        if want != got:
            found.append(
                Divergence(f"refusal at {label}", _rules_text(want), _rules_text(got))
            )
    return found


def _compare_each(
    prefix: str,
    expected: Mapping[str, Mapping[str, str]],
    observed: Mapping[str, Mapping[str, str]],
    exhaustive: bool,
) -> list[Divergence]:
    """Each named observation compared exactly, rule by rule."""
    found: list[Divergence] = []
    for key, rules in expected.items():
        where = f"{prefix} {key}"
        if key not in observed:
            found.append(Divergence(where, "observed", "never observed"))
            continue
        actual = observed[key]
        for rule_id in sorted(set(rules) | set(actual)):
            want = rules.get(rule_id, "absent")
            got = actual.get(rule_id, "absent")
            if want != got:
                found.append(Divergence(f"{where}: {rule_id}", want, got))
    if exhaustive:
        for key in sorted(set(observed) - set(expected)):
            found.append(Divergence(f"{prefix} {key}", "never observed", "observed"))
    return found


def _ordered(first: Mapping, second: Mapping) -> list:
    return list(first) + [k for k in second if k not in first]


def _rules_text(rules: tuple[str, ...] | None) -> str:
    if rules is None:
        return "no refusal"
    return ", ".join(rules) or "(none named)"


def render(name: str, divergences: Sequence[Divergence]) -> str:
    if not divergences:
        return f"scenario {name} matches its known answer"
    lines = [f"scenario {name} diverges from its known answer in {len(divergences)} place(s):"]
    lines += [f"  {d.where}: expected {d.expected}, got {d.actual}" for d in divergences]
    return "\n".join(lines)
