"""ADR-032 increment 1a: ruleset version 2, the pattern catalogue, the starter's defaults.

D2: an optional ``layers`` block, innermost first, from which one direction
rule per layer is derived at load (``layers.<id>-depends-inward``, a boundary
on the ``imports`` engine whose ``forbid`` lists every outer layer's paths, or
a cited judgement). A file the map places in two layers is an unanswerable
finding naming both; a layer with no paths derives nothing and is noted.
D7/D8's keys: ``owner``, ``ruled``, ``origin``, with D7's two load-time
refusals (DEC_ADR_032_I1A). D5: the catalogue, a data file the ruleset names,
validated with it and rendered beside the rules page. D4, D6, D8: the
starter's default set. Version 1 loads unchanged; a version this build does
not read is refused by its version alone.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml
from test_adr_018_rules_rst import body, docs_project
from test_adr_018_starter import SRC, run
from test_domains import git_project
from test_guidelines_domain import RULESET_REL, write
from test_guidelines_findings import RULESET as V1_RULESET
from test_guidelines_findings import Ref, run_eval

from invariant.adapters import get_adapter
from invariant.core import guidelines
from invariant.core.guidelines import RulesetError, parse
from invariant.core.rules import RuleContext, all_rules, evaluate
from invariant.domains import docs as docs_domain
from invariant.domains.guidelines import GuidelinesDomain

REGISTERED = frozenset(spec.id for spec in all_rules())
SCAFFOLD = Path(guidelines.__file__).resolve().parent.parent / "data" / "scaffold"
CATALOGUE = SCAFFOLD / "guidelines" / "patterns.yaml"
STARTER_RULESET = SCAFFOLD / "guidelines" / "ruleset.yaml"
PATTERNS_REL = "docs/patterns.rst"
CATALOGUE_REL = "docs/guidelines/patterns.yaml"

FOUR_LAYERS = """\
version: 2
sections:
  - id: style
    title: Style
rules: []
layers:
  map:
    - id: entities
      title: Entities
      paths: ["domain/**"]
    - id: use-cases
      title: Use Cases
      paths: ["usecases/**"]
    - id: interface-adapters
      title: Interface Adapters
      paths: ["adapters/**"]
    - id: frameworks-drivers
      title: Frameworks & Drivers
      paths: ["cli/**", "web/**"]
"""


def load(text: str, *, engines=None, files=None, location=RULESET_REL):
    files = files or {}
    return parse(
        text, registered=REGISTERED, engines=engines, location=location,
        read=lambda rel: files.get(rel),
    )


def problems(text: str, **kwargs) -> tuple[str, ...]:
    with pytest.raises(RulesetError) as caught:
        load(text, **kwargs)
    return caught.value.problems


def with_layers(block: dict, rules: list | None = None, **top) -> str:
    raw = {"version": 2, "sections": [], "rules": rules or [], "layers": block, **top}
    return yaml.safe_dump(raw, sort_keys=False)


def layer(lid: str, title: str, paths: list[str], **more) -> dict:
    return {"id": lid, "title": title, "paths": paths, **more}


def judgement_rule(rule_id="solid.x", **more) -> dict:
    raw = {
        "id": rule_id, "section": "s", "title": "A rule", "statement": "Do it.",
        "severity": "blocker", "phases": ["review"], "applies_to": ["**/*.py"],
        "check": {"kind": "judgement", "evidence": "citation", "scope": "diff"},
    }
    raw.update(more)
    return raw


def v2(*rules: dict, **top) -> str:
    raw = {"version": 2, "sections": [{"id": "s", "title": "S"}], "rules": list(rules), **top}
    return yaml.safe_dump(raw, sort_keys=False)


# -- versions ----------------------------------------------------------------------------


class TestVersions:
    def test_version_1_loads_unchanged(self):
        ruleset = load(V1_RULESET)
        assert ruleset.version == 1
        assert ruleset.layers is None and ruleset.catalogue is None and ruleset.notes == ()
        assert [r.id for r in ruleset.rules] == [
            "style.no-print", "style.no-todo", "layers.app-never-imports-tools",
        ]
        assert all(r.owner == "project" and r.ruled is None and r.origin is None
                   for r in ruleset.rules)

    def test_a_version_this_build_does_not_read_is_refused_by_its_version_alone(self):
        found = problems("version: 3\nsections: []\nrules: []\nlayers: {}\nshiny: true\n")
        assert len(found) == 1, found
        assert "version 3" in found[0] and "1 and 2" in found[0]

    def test_a_version_2_key_in_a_version_1_file_is_unknown_there(self):
        text = V1_RULESET.replace("version: 1", "version: 1\ncatalogue: patterns.yaml")
        found = problems(text)
        assert any("catalogue" in p and "version 2" in p for p in found), found

    def test_a_version_2_rule_key_in_a_version_1_file_is_refused(self):
        text = V1_RULESET.replace(
            "    severity: warn\n", "    severity: warn\n    owner: user\n", 1
        )
        found = problems(text)
        assert any("owner" in p and "version 2" in p for p in found), found


# -- owner, ruled, origin ---------------------------------------------------------------------


RULED = {"by": "the user", "date": "2026-11-22", "reason": "agreed"}
ORIGIN = {"set": "defaults", "version": 1, "digest": "0123456789ab"}


class TestRuleKeys:
    def test_the_three_keys_are_read(self):
        (rule,) = load(v2(judgement_rule(owner="user", ruled=RULED, origin=ORIGIN))).rules
        assert rule.owner == "user"
        assert rule.ruled == guidelines.Ruling(by="the user", date="2026-11-22", reason="agreed")
        assert rule.origin == guidelines.Origin(set="defaults", version=1, digest="0123456789ab")

    def test_owner_defaults_to_project(self):
        (rule,) = load(v2(judgement_rule())).rules
        assert rule.owner == "project"

    def test_a_yaml_date_is_read_as_its_iso_spelling(self):
        text = v2(judgement_rule(ruled={**RULED, "date": date(2026, 11, 22)}))
        assert load(text).rules[0].ruled.date == "2026-11-22"

    @pytest.mark.parametrize(
        ("key", "value", "said"),
        [
            ("owner", "agent", "owner"),
            ("ruled", {"by": "u", "date": "2026-11-22"}, "reason"),
            ("ruled", {**RULED, "date": "yesterday"}, "date"),
            ("ruled", {**RULED, "extra": 1}, "extra"),
            ("ruled", "the user", "ruled"),
            ("origin", {**ORIGIN, "digest": "XYZ"}, "digest"),
            ("origin", {**ORIGIN, "version": 0}, "version"),
            ("origin", {**ORIGIN, "set": ""}, "set"),
            ("origin", ["defaults"], "origin"),
        ],
    )
    def test_a_malformed_key_is_refused(self, key, value, said):
        found = problems(v2(judgement_rule(**{key: value})))
        assert any(said in p for p in found), found

    def test_an_exception_on_a_user_rule_needs_a_ruling(self):
        exception = {"path": "a.py", "reason": "legacy"}
        found = problems(v2(judgement_rule(owner="user", exceptions=[exception])))
        assert any("owner: user" in p and "ruled" in p for p in found), found

    def test_a_ruled_exception_on_a_user_rule_loads(self):
        exception = {"path": "a.py", "reason": "legacy", "ruled": RULED}
        (rule,) = load(v2(judgement_rule(owner="user", exceptions=[exception]))).rules
        assert rule.exceptions[0].ruled.by == "the user"

    def test_a_project_rule_takes_an_exception_without_a_ruling(self):
        (rule,) = load(v2(judgement_rule(exceptions=[{"path": "a.py", "reason": "x"}]))).rules
        assert rule.exceptions[0].ruled is None

    def test_a_claim_condition_on_a_user_rule_is_refused(self):
        check = {"kind": "judgement", "evidence": "citation", "scope": "diff",
                 "not_applicable_when": [{"id": "trivial", "claim": "the change is trivial"}]}
        found = problems(v2(judgement_rule(owner="user", check=check)))
        assert any("claim" in p and "owner: user" in p for p in found), found

    def test_a_mechanical_condition_on_a_user_rule_loads(self):
        check = {"kind": "judgement", "evidence": "citation", "scope": "diff",
                 "not_applicable_when": [
                     {"id": "no_executable_change", "mechanical": {"no_executable_change": True}}
                 ]}
        (rule,) = load(v2(judgement_rule(owner="user", check=check))).rules
        assert rule.check.not_applicable_when[0].mechanical == "no_executable_change"

    def test_origin_digest_ignores_the_origin_and_key_order(self):
        raw = judgement_rule()
        stamped = {**raw, "origin": ORIGIN}
        reordered = dict(reversed(list(raw.items())))
        assert guidelines.origin_digest(raw) == guidelines.origin_digest(stamped)
        assert guidelines.origin_digest(raw) == guidelines.origin_digest(reordered)
        assert guidelines.origin_digest(raw) != guidelines.origin_digest({**raw, "title": "B"})


# -- the layer map ------------------------------------------------------------------------------


class TestTheDerivation:
    def test_a_four_layer_map_derives_three_rules_forbidding_every_outer_layer(self):
        ruleset = load(FOUR_LAYERS)
        derived = {r.id: r for r in ruleset.rules if r.layer is not None}
        assert list(derived) == [
            "layers.entities-depends-inward",
            "layers.use-cases-depends-inward",
            "layers.interface-adapters-depends-inward",
        ]
        assert derived["layers.entities-depends-inward"].check.forbid == (
            "usecases/**", "adapters/**", "cli/**", "web/**",
        )
        assert derived["layers.use-cases-depends-inward"].check.forbid == (
            "adapters/**", "cli/**", "web/**",
        )
        assert derived["layers.interface-adapters-depends-inward"].check.forbid == (
            "cli/**", "web/**",
        )

    def test_a_derived_rule_is_a_blocker_boundary_on_imports_in_the_layers_section(self):
        ruleset = load(FOUR_LAYERS)
        rule = ruleset.by_id("layers.use-cases-depends-inward")
        assert (rule.section, rule.severity, rule.owner) == ("layers", "blocker", "project")
        assert rule.phases == ("design", "review", "landing")
        assert (rule.check.kind, rule.check.engine) == ("boundary", "imports")
        assert rule.applies_to == ("usecases/**",)
        assert rule.title == "Use Cases depends inward"
        assert rule.statement.startswith(
            "Nothing in Use Cases imports Interface Adapters or Frameworks & Drivers."
        )
        assert "Fix:" in rule.statement and "inject it" in rule.statement
        assert any(s.id == "layers" for s in ruleset.sections)

    def test_the_block_sets_severity_phases_and_owner(self):
        block = {"severity": "warn", "phases": ["review"], "owner": "user", "ruled": RULED,
                 "map": [layer("a", "A", ["a/**"]), layer("b", "B", ["b/**"])]}
        (rule,) = load(with_layers(block)).rules
        assert (rule.severity, rule.phases, rule.owner) == ("warn", ("review",), "user")

    def test_judgement_evidence_derives_cited_judgement_rules(self):
        block = {"evidence": "judgement",
                 "map": [layer("a", "A", ["a/**"]), layer("b", "B", ["b/**"])]}
        (rule,) = load(with_layers(block), engines=frozenset()).rules
        assert rule.check.kind == "judgement"
        assert (rule.check.evidence, rule.check.verdict_scope) == ("citation", "diff")
        assert rule.statement.startswith("Nothing in A imports B.")

    def test_a_layer_with_no_paths_derives_nothing_and_is_noted(self):
        block = {"map": [layer("a", "A", []), layer("b", "B", ["b/**"]),
                         layer("c", "C", ["c/**"]), layer("d", "D", [])]}
        ruleset = load(with_layers(block))
        assert [r.id for r in ruleset.rules] == ["layers.b-depends-inward"]
        assert ruleset.by_id("layers.b-depends-inward").check.forbid == ("c/**",)
        assert ruleset.notes == ("layer A maps no files", "layer D maps no files")

    def test_an_empty_map_needs_no_import_engine(self):
        block = {"map": [layer("a", "A", []), layer("b", "B", [])]}
        ruleset = load(with_layers(block), engines=frozenset({"mask"}))
        assert ruleset.rules == ()

    def test_imports_with_no_adapter_offering_it_is_refused_naming_judgement(self):
        found = problems(FOUR_LAYERS, engines=frozenset({"mask"}))
        assert any("imports" in p and "judgement" in p for p in found), found

    def test_exclude_takes_a_file_out_of_its_layer(self):
        block = {"map": [layer("a", "A", ["a/**"], exclude=["a/gen/**"]),
                         layer("b", "B", ["b/**"])]}
        (rule,) = load(with_layers(block)).rules
        assert rule.exclude == ("a/gen/**",)
        assert guidelines.covers(rule, "a/x.py")
        assert not guidelines.covers(rule, "a/gen/x.py")
        assert not guidelines.meets(rule, "a/gen/x.py")

    def test_per_layer_exceptions_land_on_the_derived_rule(self):
        exception = {"path": "a/old.py", "reason": "legacy"}
        block = {"map": [layer("a", "A", ["a/**"], exceptions=[exception]),
                         layer("b", "B", ["b/**"])]}
        (rule,) = load(with_layers(block)).rules
        assert rule.exceptions[0].path == "a/old.py"

    def test_an_exception_on_a_user_owned_map_needs_a_ruling(self):
        exception = {"path": "a/old.py", "reason": "legacy"}
        block = {"owner": "user",
                 "map": [layer("a", "A", ["a/**"], exceptions=[exception]),
                         layer("b", "B", ["b/**"])]}
        found = problems(with_layers(block))
        assert any("ruled" in p for p in found), found

    @pytest.mark.parametrize(
        ("block", "said"),
        [
            ({}, "map"),
            ({"map": []}, "names no layer"),
            ({"map": "entities"}, "map must be a list"),
            ({"map": [layer("a", "A", ["a/**"]), layer("a", "B", ["b/**"])]}, "twice"),
            ({"map": [layer("a", "A", ["a/**"]), layer("b", "A", ["b/**"])]}, "title"),
            ({"map": [layer("a", "A", "a/**")]}, "bare string"),
            ({"map": [{"id": "a", "paths": []}]}, "title"),
            ({"map": [layer("a b", "A", [])]}, "whitespace"),
            ({"map": [layer("a", "A", [], colour="red")]}, "colour"),
            ({"evidence": "symbols", "map": [layer("a", "A", [])]}, "evidence"),
            ({"severity": "loud", "map": [layer("a", "A", [])]}, "severity"),
            ({"phases": ["lunch"], "map": [layer("a", "A", [])]}, "phase"),
            ({"owner": "agent", "map": [layer("a", "A", [])]}, "owner"),
            ({"shape": 1, "map": [layer("a", "A", [])]}, "shape"),
            ({"map": [layer("a", "A", ["../x/**"])]}, "project-relative"),
        ],
    )
    def test_a_malformed_block_is_refused(self, block, said):
        found = problems(with_layers(block))
        assert any(said in p for p in found), found

    def test_a_block_that_is_not_a_mapping_is_refused(self):
        found = problems(with_layers("layers"))
        assert any("layers must be a mapping" in p for p in found), found

    def test_a_derived_id_a_declared_rule_already_has_is_refused(self):
        clash = judgement_rule("layers.a-depends-inward", section="layers")
        block = {"map": [layer("a", "A", ["a/**"]), layer("b", "B", ["b/**"])]}
        text = with_layers(block, rules=[clash])
        text = text.replace("sections: []", "sections:\n- id: layers\n  title: Layers")
        found = problems(text)
        assert any("layers.a-depends-inward" in p and "derived" in p for p in found), found

    def test_a_declared_layers_section_is_kept(self):
        block = {"map": [layer("a", "A", ["a/**"]), layer("b", "B", ["b/**"])]}
        text = with_layers(block).replace(
            "sections: []", "sections:\n- id: layers\n  title: Architecture layers"
        )
        ruleset = load(text)
        assert [(s.id, s.title) for s in ruleset.sections] == [("layers", "Architecture layers")]

    def test_the_map_is_part_of_what_a_rule_is_judged_by(self):
        one = load(with_layers({"map": [layer("a", "A", ["a/**"], exclude=["a/x/**"]),
                                        layer("b", "B", ["b/**"])]}))
        two = load(with_layers({"map": [layer("a", "A", ["a/**"]),
                                        layer("b", "B", ["b/**"])]}))
        assert guidelines.definition_of(one.rules[0]) != guidelines.definition_of(two.rules[0])

    def test_the_rendered_pages_say_a_rule_is_derived(self):
        ruleset = load(FOUR_LAYERS)
        checklist = guidelines.render_checklist(ruleset, "sphinx", source=RULESET_REL)
        needs = guidelines.render_rule_needs(ruleset, source=RULESET_REL)
        for page in (checklist, needs):
            assert "Derived from the layer map (layer Use Cases)" in page
            assert "RULE_LAYERS_USE_CASES_DEPENDS_INWARD" in needs

    def test_an_exclude_is_rendered(self):
        block = {"map": [layer("a", "A", ["a/**"], exclude=["a/gen/**"]),
                         layer("b", "B", ["b/**"])]}
        page = guidelines.render_checklist(load(with_layers(block)), "markdown", source="r")
        assert "except `a/gen/**`" in page


# -- the findings of a derived rule --------------------------------------------------------------


class TestDerivedFindings:
    def test_a_reference_into_an_outer_layer_is_a_finding(self):
        rule = load(FOUR_LAYERS).by_id("layers.use-cases-depends-inward")
        files = {"usecases/s.py": "from adapters import db\n"}
        refs = {"usecases/s.py": [Ref("adapters/db.py", 1, "from adapters import db")]}
        result, _ = run_eval([rule], files, refs=refs)
        (finding,) = result.findings
        assert (finding.status, finding.target) == ("new", "adapters/db.py")

    def test_an_inward_reference_is_clean(self):
        rule = load(FOUR_LAYERS).by_id("layers.use-cases-depends-inward")
        files = {"usecases/s.py": "from domain import x\n"}
        refs = {"usecases/s.py": [Ref("domain/x.py", 1, "from domain import x")]}
        result, _ = run_eval([rule], files, refs=refs)
        assert result.findings == ()

    def test_a_target_excluded_from_the_outer_layer_is_not_in_it(self):
        block = {"map": [layer("a", "A", ["a/**", "b/ports/**"]),
                         layer("b", "B", ["b/**"], exclude=["b/ports/**"])]}
        (rule,) = load(with_layers(block)).rules
        files = {"a/s.py": "x\n"}
        refs = {"a/s.py": [Ref("b/ports/p.py", 1, "import p"), Ref("b/q.py", 2, "import q")]}
        result, _ = run_eval([rule], files, refs=refs)
        assert [f.target for f in result.findings] == ["b/q.py"]

    def test_a_file_in_two_layers_is_unanswerable_naming_both(self):
        block = {"map": [layer("a", "A", ["a/**"]), layer("b", "B", ["b/**", "a/shared/**"]),
                         layer("c", "C", ["c/**"])]}
        ruleset = load(with_layers(block))
        files = {"a/shared/m.py": "x = 1\n"}
        result, _ = run_eval(ruleset.rules, files, refs={"a/shared/m.py": []})
        assert result.findings, result
        assert all(f.status == "unanswerable" for f in result.findings)
        assert all("A" in f.note and "B" in f.note and "exclude" in f.note
                   for f in result.findings)

    def test_a_hand_written_boundary_is_unchanged(self):
        rule = load(V1_RULESET).by_id("layers.app-never-imports-tools")
        files = {"app/m.py": "import tools\n"}
        refs = {"app/m.py": [Ref("tools/h.py", 1, "import tools")]}
        result, _ = run_eval([rule], files, refs=refs)
        assert [f.target for f in result.findings] == ["tools/h.py"]


PYTHON = {"name": "demo", "ecosystems": ["python"]}


class TestMechanicalClean:
    """The deliverable test: a Python file in Use Cases importing Interface Adapters."""

    def clean(self, root, files):
        m = git_project(root, {"version": 1, "project": PYTHON,
                               "guidelines": {"ruleset": RULESET_REL}})
        ctx = RuleContext(manifest=m, cwd=root, session={"files": files, "base": ""})
        (result,) = evaluate(["guidelines.mechanical_clean"], ctx)
        return result.verdict

    def test_a_use_case_importing_an_interface_adapter_is_a_finding(self, tmp_path):
        root = tmp_path / "proj"
        write(root, RULESET_REL, FOUR_LAYERS)
        write(root, "adapters/__init__.py", "")
        write(root, "adapters/db.py", "X = 1\n")
        write(root, "usecases/__init__.py", "")
        write(root, "usecases/service.py", "from adapters import db\n")
        verdict = self.clean(root, ["usecases/service.py"])
        assert not verdict.ok
        assert (
            "layers.use-cases-depends-inward usecases/service.py:1 -> adapters/db.py"
            in verdict.message
        )

    def test_a_file_in_two_layers_fails_unanswered(self, tmp_path):
        root = tmp_path / "proj"
        write(root, RULESET_REL, FOUR_LAYERS.replace('["adapters/**"]',
                                                     '["adapters/**", "usecases/**"]'))
        write(root, "usecases/__init__.py", "")
        write(root, "usecases/service.py", "X = 1\n")
        verdict = self.clean(root, ["usecases/service.py"])
        assert not verdict.ok
        assert "unanswerable" in verdict.message
        assert "Use Cases" in verdict.message and "Interface Adapters" in verdict.message


class TestRulesetValid:
    def test_the_notes_are_printed_without_failing(self, make_manifest, tmp_path):
        text = FOUR_LAYERS.replace('paths: ["domain/**"]', "paths: []")
        write(tmp_path, RULESET_REL, text)
        ctx = RuleContext(manifest=make_manifest(
            {"version": 1, "project": PYTHON, "guidelines": {"ruleset": RULESET_REL}}
        ), cwd=tmp_path)
        (result,) = evaluate(["guidelines.ruleset_valid"], ctx)
        assert result.verdict.ok and not result.verdict.skipped
        assert "layer Entities maps no files" in result.verdict.detail


# -- the catalogue ----------------------------------------------------------------------------------


def catalogue_text(patterns: list[dict], version=1) -> bytes:
    return yaml.safe_dump({"version": version, "patterns": patterns}).encode()


def pattern(pid="gof.x", family="creational", **more) -> dict:
    raw = {"id": pid, "name": "X", "family": family, "intent": "Do x.",
           "use_when": ["when x"], "avoid_when": ["when not x"]}
    raw.update(more)
    return raw


def with_catalogue(data: bytes | None, name="patterns.yaml"):
    files = {} if data is None else {CATALOGUE_REL: data}
    return v2(judgement_rule(), catalogue=name), files


class TestTheCatalogue:
    def test_the_shipped_catalogue_has_32_entries_and_validates(self):
        text, files = with_catalogue(CATALOGUE.read_bytes())
        catalogue = load(text, files=files).catalogue
        assert catalogue.path == CATALOGUE_REL and catalogue.version == 1
        assert len(catalogue.patterns) == 32
        families = [p.family for p in catalogue.patterns]
        assert families.count("creational") == 5
        assert families.count("structural") == 7
        assert families.count("behavioural") == 11
        assert families.count("enterprise") + families.count("domain") == 9
        names = {p.name for p in catalogue.patterns}
        assert {"Repository", "Unit of Work", "Dependency Injection", "Service Layer",
                "Data Mapper", "Specification", "Domain Event", "Value Object",
                "Null Object"} <= names
        assert all(p.intent and p.use_when and p.avoid_when for p in catalogue.patterns)

    def test_no_catalogue_key_is_no_catalogue(self):
        assert load(v2(judgement_rule())).catalogue is None

    def test_a_duplicate_id_is_refused(self):
        text, files = with_catalogue(catalogue_text([pattern(), pattern(name="Y")]))
        found = problems(text, files=files)
        assert any("gof.x" in p and "twice" in p for p in found), found

    @pytest.mark.parametrize(
        ("patterns", "said"),
        [
            ([pattern(family="ancient")], "family"),
            ([pattern(intent="")], "intent"),
            ([pattern(use_when="always")], "use_when"),
            ([pattern(avoid_when=[])], "avoid_when"),
            ([pattern(colour="red")], "colour"),
            ([{"name": "X"}], "id"),
            (["gof.x"], "mapping"),
        ],
    )
    def test_a_malformed_entry_is_refused(self, patterns, said):
        text, files = with_catalogue(catalogue_text(patterns))
        found = problems(text, files=files)
        assert any(said in p for p in found), found

    @pytest.mark.parametrize(
        ("data", "said"),
        [
            (None, "could not be read"),
            (b"version: 2\npatterns: []\n", "version 2"),
            (b"- a\n", "mapping"),
            (b"version: 1\npatterns: x\n", "patterns must be a list"),
            (b"[unclosed\n", "not valid YAML"),
            (b"\xff\xfe", "could not be read"),
            (b"version: 1\npatterns: []\nmore: 1\n", "more"),
        ],
    )
    def test_a_malformed_file_is_refused(self, data, said):
        text, files = with_catalogue(data)
        found = problems(text, files=files)
        assert any(said in p for p in found), found

    @pytest.mark.parametrize("name", ["", "../../outside.yaml", "/abs.yaml", "p*.yaml", 3])
    def test_a_catalogue_path_must_be_a_file_inside_the_project(self, name):
        found = problems(v2(judgement_rule(), catalogue=name))
        assert any("catalogue" in p for p in found), found

    def test_without_a_location_or_tree_the_catalogue_cannot_be_read(self):
        with pytest.raises(RulesetError) as caught:
            parse(v2(judgement_rule(), catalogue="patterns.yaml"), registered=REGISTERED)
        assert any("catalogue" in p for p in caught.value.problems)

    def test_load_reads_it_from_the_tree_beside_the_ruleset(self, tmp_path):
        write(tmp_path, RULESET_REL, v2(judgement_rule(), catalogue="patterns.yaml"))
        write(tmp_path, CATALOGUE_REL, CATALOGUE.read_text(encoding="utf-8"))
        ruleset = guidelines.load(tmp_path / RULESET_REL, registered=REGISTERED, root=tmp_path)
        assert len(ruleset.catalogue.patterns) == 32

    def test_load_names_a_missing_catalogue(self, tmp_path):
        write(tmp_path, RULESET_REL, v2(judgement_rule(), catalogue="patterns.yaml"))
        with pytest.raises(RulesetError) as caught:
            guidelines.load(tmp_path / RULESET_REL, registered=REGISTERED, root=tmp_path)
        assert any("does not exist" in p for p in caught.value.problems)


class TestThePatternsPage:
    def catalogue(self):
        text, files = with_catalogue(CATALOGUE.read_bytes())
        return load(text, files=files).catalogue

    def test_the_marker_holds_the_digest_of_the_body(self):
        page = guidelines.render_patterns(self.catalogue(), source=CATALOGUE_REL)
        digest, rest = guidelines.split_managed(page, "sphinx")
        assert digest == guidelines.digest_of(rest.encode("utf-8"))

    def test_one_label_per_pattern_id(self):
        catalogue = self.catalogue()
        page = guidelines.render_patterns(catalogue, source=CATALOGUE_REL)
        for entry in catalogue.patterns:
            assert page.count(f".. _{entry.id}:\n") == 1, entry.id
        assert "Use when:" in page and "Avoid when:" in page

    def test_no_catalogue_renders_a_page_saying_so(self):
        page = guidelines.render_patterns(None, source=None)
        assert "No pattern catalogue is configured" in page


class TestRenderWritesPatternsRst:
    @pytest.fixture
    def tree(self, tmp_path):
        write(tmp_path, RULESET_REL, v2(judgement_rule(), catalogue="patterns.yaml"))
        write(tmp_path, CATALOGUE_REL, CATALOGUE.read_text(encoding="utf-8"))
        docs_project(tmp_path)
        return tmp_path

    def domain(self, make_manifest, root, **kwargs):
        return GuidelinesDomain(RuleContext(manifest=make_manifest(body(**kwargs)), cwd=root))

    def current(self, make_manifest, root):
        ctx = RuleContext(manifest=make_manifest(body()), cwd=root)
        (result,) = evaluate(["guidelines.generated_current"], ctx)
        return result.verdict

    def test_render_writes_patterns_rst_beside_the_rules_page(self, make_manifest, tree):
        result = self.domain(make_manifest, tree).render()
        assert (result.patterns_relpath, result.patterns_status) == (PATTERNS_REL, "new")
        text = (tree / PATTERNS_REL).read_text(encoding="utf-8")
        assert ".. _gof.abstract-factory:" in text

    def test_the_drift_gate_reads_it(self, make_manifest, tree):
        assert f"{PATTERNS_REL} is missing" in self.current(make_manifest, tree).message
        self.domain(make_manifest, tree).render()
        verdict = self.current(make_manifest, tree)
        assert verdict.ok, verdict.message
        assert "3 generated output(s) current" in verdict.message
        path = tree / PATTERNS_REL
        path.write_text(path.read_text(encoding="utf-8") + "\nmore\n", encoding="utf-8")
        assert f"{PATTERNS_REL} was edited by hand" in self.current(make_manifest, tree).message

    def test_no_catalogue_no_page(self, make_manifest, tree):
        write(tree, RULESET_REL, v2(judgement_rule()))
        result = self.domain(make_manifest, tree).render()
        assert result.patterns_relpath is None and result.patterns_status is None
        assert not (tree / PATTERNS_REL).exists()

    def test_an_exempt_patterns_page_is_refused(self, make_manifest, tree):
        from invariant.core.rules import RenderRefused

        domain = self.domain(make_manifest, tree, session={"exempt_paths": [PATTERNS_REL]})
        with pytest.raises(RenderRefused, match="patterns page"):
            domain.render()

    def test_a_privacy_finding_in_the_catalogue_refuses_the_render(self, make_manifest, tree):
        from invariant.core.rules import RenderRefused

        raw = yaml.safe_load(CATALOGUE.read_text(encoding="utf-8"))
        raw["patterns"][0]["intent"] = "Ask TICKET-4242 first. " + raw["patterns"][0]["intent"]
        write(tree, CATALOGUE_REL, yaml.safe_dump(raw))
        allow = [{"paths": ["docs/guidelines/checklist.rst", "docs/rules.rst"], "reason": "r"}]
        domain = self.domain(
            make_manifest, tree, privacy={"extra": ["TICKET-[0-9]+"], "allow": allow}
        )
        with pytest.raises(RenderRefused) as caught:
            domain.render()
        assert f"{PATTERNS_REL}:" in str(caught.value) and "privacy" in str(caught.value)
        assert not (tree / PATTERNS_REL).exists()


class TestRenderCommand:
    @pytest.fixture(autouse=True)
    def _inside(self, project, monkeypatch):
        monkeypatch.chdir(project)

    def test_json_and_text_carry_the_patterns_page(self, project, capsys):
        from test_cli import out, payload
        from test_cli import run as cli_run

        write(project, RULESET_REL, v2(judgement_rule(), catalogue="patterns.yaml"))
        write(project, CATALOGUE_REL, CATALOGUE.read_text(encoding="utf-8"))
        docs_project(project)
        manifest = project / ".claude" / "invariant.yaml"
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        data["guidelines"] = {"ruleset": RULESET_REL}
        data["project"]["ecosystems"] = ["python"]
        data.setdefault("docs", {})["format"] = "sphinx"
        manifest.write_text(yaml.safe_dump(data), encoding="utf-8")
        assert cli_run("--json", "guidelines", "render") == 0
        rendered = payload(capsys)
        assert rendered["patterns"] == {"path": PATTERNS_REL, "status": "new", "written": True}
        assert cli_run("guidelines", "render", "--dry-run") == 0
        assert PATTERNS_REL in out(capsys)


# -- the adapters' source globs ------------------------------------------------------------------------


class TestSourceGlobs:
    @pytest.mark.parametrize(
        ("ecosystem", "globs"),
        [
            ("python", ("**/*.py",)),
            ("dart", ("**/*.dart",)),
            ("swift_xcode", ("**/*.swift",)),
            ("node", ("**/*.js", "**/*.jsx", "**/*.ts", "**/*.tsx")),
        ],
    )
    def test_each_adapter_spells_its_source_suffixes(self, ecosystem, globs):
        adapter = get_adapter(ecosystem)
        assert adapter.source_globs() == globs
        named = [Path(f"x/a{glob[4:]}") for glob in globs] + [Path("x/readme.md")]
        assert adapter.source_files(named) == named[:-1]

    def test_an_adapter_that_knows_no_suffix_has_no_globs(self):
        assert get_adapter("generic").source_globs() == ()


# -- the starter's default set ----------------------------------------------------------------------------

SOLID = (
    "solid.single-responsibility",
    "solid.open-closed",
    "solid.liskov-substitution",
    "solid.interface-segregation",
    "solid.dependency-inversion",
)


@pytest.fixture
def started(tmp_path, monkeypatch, capsys):
    from test_adr_018_starter import repo

    root = repo(tmp_path / "toy", {
        "pyproject.toml": '[project]\nname = "toy"\nversion = "0"\n',
        "pkg/__init__.py": "",
    })
    monkeypatch.chdir(root)
    code, text = run(
        capsys, "init", "--home", "repo", "--host-install", "--ecosystem", "python",
        "--name", "toy",
    )
    assert code == 0, text
    return root


def starter_raw(root: Path) -> dict:
    return yaml.safe_load((root / SRC / "guidelines" / "ruleset.yaml").read_text("utf-8"))


def starter_ruleset(root: Path):
    return guidelines.load(
        root / SRC / "guidelines" / "ruleset.yaml", registered=REGISTERED, root=root
    )


class TestTheStarter:
    def test_it_carries_the_five_solid_rules_as_user_owned_blockers(self, started):
        ruleset = starter_ruleset(started)
        rules = [ruleset.by_id(rule_id) for rule_id in SOLID]
        assert all(rules)
        for rule in rules:
            assert (rule.severity, rule.owner, rule.section) == ("blocker", "user", "solid")
            assert rule.phases == ("design", "review", "landing")
            assert (rule.check.kind, rule.check.evidence, rule.check.verdict_scope) == (
                "judgement", "citation", "diff",
            )
            (condition,) = rule.check.not_applicable_when
            assert condition.mechanical == "no_executable_change"
            assert rule.statement and rule.rationale and "Fix:" in rule.statement
            assert rule.applies_to == ("**/*.py",)
        assert len({r.statement for r in rules}) == 5
        assert len({r.rationale for r in rules}) == 5

    def test_the_pattern_and_docs_first_rules(self, started):
        ruleset = starter_ruleset(started)
        named = ruleset.by_id("patterns.named-or-declined")
        assert (named.severity, named.owner, named.phases) == ("warn", "user", ("design", "review"))
        assert named.check.kind == "judgement"
        first = ruleset.by_id("docs.design-before-code")
        assert (first.severity, first.owner, first.phases) == ("warn", "user", ("design", "review"))
        assert first.check.not_applicable_when == ()
        assert ruleset.by_id("docs.items-carry-no-design")
        assert ruleset.by_id("docs.decisions-are-declared")

    def test_the_layers_are_the_four_defaults_with_no_paths(self, started):
        ruleset = starter_ruleset(started)
        assert [layer.title for layer in ruleset.layers.layers] == [
            "Entities", "Use Cases", "Interface Adapters", "Frameworks & Drivers",
        ]
        assert all(layer.paths == () for layer in ruleset.layers.layers)
        assert ruleset.layers.owner == "user"
        assert [r for r in ruleset.rules if r.layer] == []
        assert len(ruleset.notes) == 4

    def test_every_default_carries_its_origin_digest(self, started):
        raw = starter_raw(started)
        stamped = [*raw["rules"], raw["layers"]]
        for entry in stamped:
            origin = entry["origin"]
            assert (origin["set"], origin["version"]) == ("defaults", 1)
            assert origin["digest"] == guidelines.origin_digest(entry)

    def test_the_catalogue_is_copied_and_named(self, started):
        ruleset = starter_ruleset(started)
        assert ruleset.catalogue.path == f"{SRC}/guidelines/patterns.yaml"
        copied = (started / SRC / "guidelines" / "patterns.yaml").read_bytes()
        assert copied == CATALOGUE.read_bytes()

    def test_it_renders_the_patterns_page_and_names_it_in_the_toctree(self, started):
        page = (started / SRC / "patterns.rst").read_text(encoding="utf-8")
        assert ".. _gof.observer:" in page
        assert "\n   patterns\n" in (started / SRC / "index.rst").read_text(encoding="utf-8")
        rules = (started / SRC / "rules.rst").read_text(encoding="utf-8")
        assert "RULE_SOLID_SINGLE_RESPONSIBILITY" in rules

    def test_render_finds_the_starter_current(self, started, capsys):
        code, text = run(capsys, "--json", "guidelines", "render", "--dry-run")
        assert code == 0, text
        rendered = json.loads(text)
        assert rendered["patterns"]["status"] == "current", rendered
        assert rendered["rules"]["status"] == "current", rendered

    def test_the_starter_text_names_no_host_or_model(self):
        text = STARTER_RULESET.read_text(encoding="utf-8").lower()
        catalogue = CATALOGUE.read_text(encoding="utf-8").lower()
        for word in ("claude", "anthropic", "openai", "gpt", "gemini", "opus", "sonnet"):
            assert word not in text and word not in catalogue


class TestStarterSourceGlobs:
    def test_no_ecosystem_applies_the_defaults_everywhere(self, tmp_path):
        assert docs_domain.default_source_globs([]) == ("**",)

    def test_several_ecosystems_are_joined_in_order(self):
        assert docs_domain.default_source_globs(["python", "dart", "python"]) == (
            "**/*.py", "**/*.dart",
        )

    def test_an_ecosystem_with_no_suffix_falls_back(self):
        assert docs_domain.default_source_globs(["generic"]) == ("**",)


class TestStampOrigins:
    def test_each_placeholder_is_stamped_in_document_order(self):
        text = with_layers(
            {"map": [layer("a", "A", [])],
             "origin": {"set": "defaults", "version": 1, "digest": "000000000000"}},
            rules=[judgement_rule(origin={"set": "defaults", "version": 1,
                                          "digest": "000000000000"})],
        )
        stamped = yaml.safe_load(guidelines.stamp_origins(text))
        assert stamped["rules"][0]["origin"]["digest"] == guidelines.origin_digest(
            stamped["rules"][0]
        )
        assert stamped["layers"]["origin"]["digest"] == guidelines.origin_digest(
            stamped["layers"]
        )
        assert "000000000000" not in guidelines.stamp_origins(text)
