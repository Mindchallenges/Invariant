"""ADR-018 increment 3: the `docs.needs` keys (D2, D3, D10), `docs.diagrams` as
a list (D1), and `core/needs`, the reader of a built `needs.json` (D6).

The needs.json samples below are synthetic, shaped as sphinx-needs 8.1 and 9.2
write them (one entry under ``versions``, each need carrying its link options
and their ``_back`` counterparts, ``needs_schema`` marking link fields with
``field_type: links``). One test reads a real project's needs.json when
``INVARIANT_TEST_NEEDS_JSON`` names one, so the suite never needs Sphinx.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from invariant.core import needs
from invariant.core.errors import ManifestError
from invariant.core.manifest import MANIFEST_RELPATH, Level, Manifest

# -- samples ---------------------------------------------------------------


def need(id_, type_, docname="architecture/overview", lineno=10, status=None, **links):
    body = {
        "id": id_,
        "type": type_,
        "docname": docname,
        "doctype": ".rst",
        "lineno": lineno,
        "status": status,
        "is_external": False,
        "links": [],
        "links_back": [],
    }
    for option, targets in links.items():
        body[option] = list(targets)
        body[f"{option}_back"] = []
    return body


def needs_json(items, *, link_options=("links", "implements", "decided_by"), schema=True):
    """A needs.json shaped as both majors write it."""
    version = {
        "created": "2026-11-09T00:00:00",
        "creator": {"name": "sphinx-needs", "version": "8.1.1"},
        "needs": {item["id"]: item for item in items},
        "needs_amount": len(items),
    }
    if schema:
        properties = {"id": {"field_type": "core"}, "status": {"field_type": "core"}}
        for option in link_options:
            properties[option] = {"field_type": "links", "type": "array"}
            properties[f"{option}_back"] = {"field_type": "backlinks", "type": "array"}
        version["needs_schema"] = {"properties": properties, "type": "object"}
    for item in items:
        for option in link_options:
            item.setdefault(option, [])
            item.setdefault(f"{option}_back", [])
    return {
        "created": "2026-11-09T00:00:00",
        "current_version": "",
        "project": "toy",
        "versions": {"": version},
    }


def toy_needs():
    return needs_json(
        [
            need("COMP_CORE", "comp"),
            need("DEC_ADR_001", "dec", "decisions/adr-001", 4, "accepted", implements=["COMP_CORE"]),
            need("BUG_001", "bug", "backlog", 20, "filed", links=["COMP_CORE"]),
            need("REQ_FAST", "req", "requirements", 3),
        ]
    )


# -- the roles and types (D2) ----------------------------------------------


class TestTypes:
    def test_the_defaults_are_the_seven_baseline_types(self):
        types = needs.default_types()
        assert [(t.role, t.directive, t.prefix) for t in types] == [
            ("requirement", "req", "REQ_"),
            ("decision", "dec", "DEC_"),
            ("element", "comp", "COMP_"),
            ("ui", "ui", "UI_"),
            ("rule", "rule", "RULE_"),
            ("idea", "idea", "IDEA_"),
            ("bug", "bug", "BUG_"),
        ]

    def test_the_item_types_follow_the_items_id_prefix(self):
        types = {t.role: t.prefix for t in needs.default_types("HEAP_")}
        assert types["idea"] == "HEAP_IDEA_" and types["bug"] == "HEAP_BUG_"
        assert types["element"] == "COMP_"

    def test_a_set_list_replaces_the_defaults_whole(self):
        raw = [
            {"directive": "service", "prefix": "SVC_", "role": "element"},
            {"directive": "view", "prefix": "VIEW_", "role": "element"},
            {"directive": "instr", "prefix": "INSTR_"},
        ]
        types = needs.parse_types(raw)
        assert [t.directive for t in types] == ["service", "view", "instr"]
        assert types[2].role is None

    @pytest.mark.parametrize(
        ("raw", "said"),
        [
            ([{"directive": "x", "prefix": "X_", "role": "widget"}], "unknown role 'widget'"),
            (
                [{"directive": "x", "prefix": "X_", "role": "element"},
                 {"directive": "x", "prefix": "Y_", "role": "element"}],
                "directive 'x' is used twice",
            ),
            (
                [{"directive": "x", "prefix": "X_", "role": "element"},
                 {"directive": "y", "prefix": "X_", "role": "element"}],
                "prefix 'X_' is used twice",
            ),
            ([{"directive": "x", "prefix": "x_", "role": "element"}], "^[A-Z][A-Z0-9_]*_$"),
            ([{"directive": "x", "prefix": "X", "role": "element"}], "^[A-Z][A-Z0-9_]*_$"),
            ([{"directive": "x", "prefix": "X_", "colour": "red"}], "'colour'"),
            ([{"prefix": "X_", "role": "element"}], "directive"),
            (["comp"], "mapping"),
            ("comp", "a list"),
        ],
    )
    def test_a_malformed_type_list_is_refused_naming_it(self, raw, said):
        problems = needs.type_problems(raw, "")
        assert any(said in problem for problem in problems), problems

    def test_a_role_with_no_type_is_refused(self):
        raw = [t.to_dict() for t in needs.default_types() if t.role != "ui"]
        problems = needs.type_problems(raw, "")
        assert any("role 'ui' has no type" in p for p in problems), problems

    def test_an_item_type_whose_prefix_differs_from_what_items_mint_is_refused(self):
        raw = [t.to_dict() for t in needs.default_types()]
        problems = needs.type_problems(raw, "HEAP_")
        assert any("'BUG_'" in p and "'HEAP_BUG_'" in p for p in problems), problems

    def test_an_item_role_takes_exactly_its_own_directive(self):
        raw = [t.to_dict() for t in needs.default_types()]
        raw.append({"directive": "defect", "prefix": "DEFECT_", "role": "bug"})
        problems = needs.type_problems(raw, "")
        assert any("role 'bug'" in p and "exactly one type" in p for p in problems), problems

    def test_the_defaults_pass_their_own_check(self):
        raw = [t.to_dict() for t in needs.default_types("HEAP_")]
        assert needs.type_problems(raw, "HEAP_") == []


class TestLinkRules:
    def test_the_defaults_are_ruling_9s_two(self):
        rules = needs.parse_link_rules(needs.DEFAULT_REQUIRE_LINKS)
        assert [(r.source, r.target, r.via) for r in rules] == [
            ("decision", "element", ()),
            ("bug", "element", ()),
        ]

    @pytest.mark.parametrize(
        ("raw", "said"),
        [
            ([{"from": "decision"}], "'to'"),
            ([{"from": "nope", "to": "element"}], "unknown role 'nope'"),
            ([{"from": "rule", "to": "decision", "via": "decided_by"}], "via"),
            ([{"from": "rule", "to": "decision", "extra": 1}], "'extra'"),
            ("decision->element", "a list"),
        ],
    )
    def test_a_malformed_rule_is_refused(self, raw, said):
        problems = needs.link_rule_problems(raw)
        assert any(said in p for p in problems), problems


# -- reading needs.json (D6) -----------------------------------------------


class TestReadingNeedsJson:
    def test_the_8x_and_9x_shape_reads_its_needs_and_link_options(self):
        read = needs.parse_needs_json(toy_needs())
        assert [n.id for n in read.needs] == ["BUG_001", "COMP_CORE", "DEC_ADR_001", "REQ_FAST"]
        assert set(read.link_options) == {"links", "implements", "decided_by"}
        dec = read.by_id()["DEC_ADR_001"]
        assert dec.links == {"implements": ("COMP_CORE",)}
        assert dec.where() == "decisions/adr-001.rst:4"

    def test_without_a_schema_link_options_are_those_with_a_back_counterpart(self):
        data = needs_json(
            [need("DEC_A", "dec", implements=["COMP_A"]), need("COMP_A", "comp")],
            schema=False,
        )
        read = needs.parse_needs_json(data)
        assert set(read.link_options) == {"links", "implements", "decided_by"}

    def test_the_current_version_is_read_when_there_are_several(self):
        data = toy_needs()
        data["versions"]["1.0"] = {"needs": {}}
        data["current_version"] = "1.0"
        assert needs.parse_needs_json(data).needs == ()

    @pytest.mark.parametrize("data", [{}, {"versions": {}}, {"versions": {"": {}}}, []])
    def test_a_file_that_is_not_needs_json_is_refused(self, data):
        with pytest.raises(needs.NeedsJsonError):
            needs.parse_needs_json(data)

    def test_an_unreadable_file_is_refused_naming_it(self, tmp_path):
        bad = tmp_path / "needs.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(needs.NeedsJsonError, match="needs.json"):
            needs.load_needs_json(bad)
        with pytest.raises(needs.NeedsJsonError):
            needs.load_needs_json(tmp_path / "absent.json")

    def test_a_file_reads_back(self, tmp_path):
        path = tmp_path / "needs.json"
        path.write_text(json.dumps(toy_needs()), encoding="utf-8")
        assert len(needs.load_needs_json(path).needs) == 4


class TestTraceability:
    def check(self, data, types=None, rules=None, statuses=needs.DEFAULT_DECISION_STATUSES):
        return needs.check(
            needs.parse_needs_json(data),
            types or needs.default_types(),
            needs.parse_link_rules(needs.DEFAULT_REQUIRE_LINKS if rules is None else rules),
            statuses,
            prefix="docs/",
        )

    def test_a_traceable_project_has_no_problem(self):
        assert self.check(toy_needs()) == []

    def test_a_decision_with_no_element_link_is_named_with_its_file_and_line(self):
        data = toy_needs()
        data["versions"][""]["needs"]["DEC_ADR_001"]["implements"] = []
        (problem,) = self.check(data)
        assert problem.need == "DEC_ADR_001"
        text = problem.render()
        assert "docs/decisions/adr-001.rst:4" in text
        assert "decision -> element" in text

    def test_a_link_to_a_need_of_another_role_does_not_count(self):
        data = toy_needs()
        data["versions"][""]["needs"]["DEC_ADR_001"]["implements"] = ["REQ_FAST"]
        assert [p.need for p in self.check(data)] == ["DEC_ADR_001"]

    def test_via_limits_the_link_options_that_count(self):
        rules = [{"from": "decision", "to": "element", "via": ["decided_by"]}]
        assert [p.need for p in self.check(toy_needs(), rules=rules)] == ["DEC_ADR_001"]
        rules = [{"from": "decision", "to": "element", "via": ["implements"]}]
        assert self.check(toy_needs(), rules=rules) == []

    def test_an_unmapped_directive_is_named(self):
        data = toy_needs()
        data["versions"][""]["needs"]["WP_ONE"] = need("WP_ONE", "wp", "plan", 7)
        (problem,) = self.check(data)
        assert problem.need == "WP_ONE"
        assert "directive 'wp'" in problem.render() and "docs.needs.types" in problem.render()

    def test_an_id_without_its_types_prefix_is_named(self):
        data = toy_needs()
        data["versions"][""]["needs"]["CORE"] = need("CORE", "comp", "x", 1)
        (problem,) = self.check(data)
        assert problem.need == "CORE" and "'COMP_'" in problem.render()

    def test_a_decision_status_outside_the_list_is_named(self):
        data = toy_needs()
        data["versions"][""]["needs"]["DEC_ADR_001"]["status"] = "agreed"
        (problem,) = self.check(data)
        assert "'agreed'" in problem.render() and "proposed" in problem.render()
        assert self.check(data, statuses=["agreed"]) == []

    def test_a_decision_with_no_status_is_named(self):
        data = toy_needs()
        data["versions"][""]["needs"]["DEC_ADR_001"]["status"] = None
        (problem,) = self.check(data)
        assert "no status" in problem.render()

    def test_a_role_with_no_needs_is_not_a_failure(self):
        """No ui, rule or idea need here, and none is asked for (D6 item 2)."""
        data = needs_json([need("COMP_CORE", "comp")])
        assert self.check(data) == []

    def test_an_external_need_is_not_held_to_this_projects_types(self):
        data = toy_needs()
        foreign = need("EXT_1", "thing", "", None)
        foreign["is_external"] = True
        data["versions"][""]["needs"]["EXT_1"] = foreign
        assert self.check(data) == []

    def test_an_extension_type_is_held_to_its_prefix_and_no_role_rule(self):
        types = (*needs.default_types(), needs.NeedType("instr", "INSTR_", None))
        data = toy_needs()
        data["versions"][""]["needs"]["INSTR_A"] = need("INSTR_A", "instr")
        assert self.check(data, types=types) == []

    def test_classify_counts_needs_by_role_and_type(self):
        report = needs.classify(needs.parse_needs_json(toy_needs()), needs.default_types())
        assert report.by_role["element"] == 1 and report.by_role["decision"] == 1
        assert report.by_type["comp"] == 1 and report.unmapped == {}


@pytest.mark.skipif(
    not os.environ.get("INVARIANT_TEST_NEEDS_JSON"),
    reason="set INVARIANT_TEST_NEEDS_JSON to a sphinx-needs 8.x needs.json (Heap's) to run",
)
def test_heaps_needs_json_is_classified_with_its_seven_element_types_mapped():
    """Increment 3's proof: Heap's built needs.json (run), its seven element
    types mapped to `element`, the item types to their roles."""
    elements = ["component", "view", "service", "model", "util", "iface", "dataflow"]
    prefixes = ["COMP_", "VIEW_", "SVC_", "MOD_", "UTIL_", "IF_", "DF_"]
    types = [needs.NeedType(d, p, "element") for d, p in zip(elements, prefixes, strict=True)]
    types += [needs.NeedType("idea", "IDEA_", "idea"), needs.NeedType("bug", "BUG_", "bug")]
    read = needs.load_needs_json(Path(os.environ["INVARIANT_TEST_NEEDS_JSON"]))
    report = needs.classify(read, types)
    assert report.unmapped == {}
    assert set(report.by_type) == {*elements, "idea", "bug"}
    assert report.by_role["element"] == sum(report.by_type[d] for d in elements)
    assert report.by_role["element"] > 0 and report.by_role["bug"] > 0


# -- the manifest keys (D2, D3, D10) -----------------------------------------


def load(root: Path, docs: dict | None = None, **extra) -> Manifest:
    body = {"version": 1, "project": {"name": "toy"}, **extra}
    if docs is not None:
        body["docs"] = docs
    path = root / MANIFEST_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return Manifest.locate(root)


def starter(root: Path) -> None:
    """A docs project at docs/, as the starter writes it."""
    (root / "docs" / "architecture").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "conf.py").write_text("extensions = ['sphinx_needs']\n", encoding="utf-8")


ENFORCED = {
    "docs": {"format": "sphinx", "needs": {"state": "enforced"}},
    "items": {"backend": "sphinx_needs", "path": "docs/backlog.rst"},
}


class TestManifestKeys:
    def test_an_absent_state_reads_as_migrating(self, tmp_path):
        manifest = load(tmp_path)
        assert manifest.get("docs.needs.state") == "migrating"
        assert manifest.get("docs.needs.decision_statuses") == list(
            needs.DEFAULT_DECISION_STATUSES
        )
        assert manifest.get("docs.needs.require_links") == [
            dict(rule) for rule in needs.DEFAULT_REQUIRE_LINKS
        ]

    def test_an_unknown_state_is_refused(self, tmp_path):
        with pytest.raises(ManifestError, match="docs.needs.state"):
            load(tmp_path, {"needs": {"state": "done"}})

    def test_an_unknown_needs_key_is_refused(self, tmp_path):
        with pytest.raises(ManifestError, match="docs.needs"):
            load(tmp_path, {"needs": {"prefixes": []}})

    def test_a_malformed_type_list_is_refused_at_load(self, tmp_path):
        with pytest.raises(ManifestError, match="docs.needs.types.*unknown role"):
            load(tmp_path, {"needs": {"types": [{"directive": "x", "prefix": "X_", "role": "y"}]}})

    def test_an_item_prefix_differing_from_the_items_id_prefix_is_refused(self, tmp_path):
        types = [t.to_dict() for t in needs.default_types()]
        with pytest.raises(ManifestError, match="HEAP_BUG_"):
            load(tmp_path, {"needs": {"types": types}}, items={"id_prefix": "HEAP_"})

    def test_a_malformed_link_rule_is_refused_at_load(self, tmp_path):
        with pytest.raises(ManifestError, match="docs.needs.require_links"):
            load(tmp_path, {"needs": {"require_links": [{"from": "decision"}]}})

    @pytest.mark.parametrize("statuses", [[], "accepted", [""], ["a\nb"]])
    def test_malformed_decision_statuses_are_refused(self, tmp_path, statuses):
        with pytest.raises(ManifestError, match="docs.needs.decision_statuses"):
            load(tmp_path, {"needs": {"decision_statuses": statuses}})

    @pytest.mark.parametrize("srcdir", [5, "", "a\nb"])
    def test_a_malformed_srcdir_is_refused(self, tmp_path, srcdir):
        with pytest.raises(ManifestError, match="docs.needs.srcdir"):
            load(tmp_path, {"needs": {"srcdir": srcdir}})


class TestSrcdir:
    def test_the_default_is_the_increment_2_rule(self, tmp_path):
        starter(tmp_path)
        manifest = load(tmp_path)
        assert needs.srcdir_of(manifest) == (tmp_path / "docs").resolve()

    def test_no_conf_py_is_no_docs_project(self, tmp_path):
        assert needs.srcdir_of(load(tmp_path)) is None

    def test_a_set_srcdir_wins(self, tmp_path):
        starter(tmp_path)
        (tmp_path / "design").mkdir()
        manifest = load(tmp_path, {"needs": {"srcdir": "design"}})
        assert needs.srcdir_of(manifest) == (tmp_path / "design").resolve()

    def test_it_is_read_against_another_tree_when_asked(self, tmp_path):
        starter(tmp_path)
        other = tmp_path / "wt"
        starter(other)
        manifest = load(tmp_path)
        assert needs.srcdir_of(manifest, other) == (other / "docs").resolve()
        manifest = load(tmp_path, {"needs": {"srcdir": "docs"}})
        assert needs.srcdir_of(manifest, other) == (other / "docs").resolve()


class TestEnforcedRefusals:
    def test_an_enforced_starter_shaped_project_loads(self, tmp_path):
        starter(tmp_path)
        manifest = load(tmp_path, **ENFORCED)
        assert manifest.get("docs.needs.state") == "enforced"

    def test_markdown_is_refused_when_enforced(self, tmp_path):
        starter(tmp_path)
        body = {**ENFORCED, "docs": {"format": "markdown", "needs": {"state": "enforced"}}}
        with pytest.raises(ManifestError, match="docs.format is 'markdown'.*enforced"):
            load(tmp_path, **body)

    def test_markdown_still_loads_while_migrating(self, tmp_path):
        assert load(tmp_path, {"format": "markdown"}).get("docs.needs.state") == "migrating"

    def test_another_items_backend_is_refused_when_enforced(self, tmp_path):
        starter(tmp_path)
        body = {**ENFORCED, "items": {"backend": "markdown", "path": "docs/backlog"}}
        with pytest.raises(ManifestError, match="items.backend is 'markdown'.*sphinx_needs"):
            load(tmp_path, **body)

    def test_an_items_store_outside_the_source_directory_is_refused(self, tmp_path):
        starter(tmp_path)
        body = {**ENFORCED, "items": {"backend": "sphinx_needs", "path": "backlog.rst"}}
        with pytest.raises(ManifestError, match="items.path is 'backlog.rst'.*docs"):
            load(tmp_path, **body)

    def test_a_closed_store_outside_the_source_directory_is_refused(self, tmp_path):
        starter(tmp_path)
        items = {"backend": "sphinx_needs", "path": "docs/backlog.rst", "closed_path": "old.rst"}
        with pytest.raises(ManifestError, match="items.closed_path"):
            load(tmp_path, **{**ENFORCED, "items": items})

    def test_an_enforced_project_with_no_docs_project_is_refused(self, tmp_path):
        with pytest.raises(ManifestError, match="no docs project"):
            load(tmp_path, **ENFORCED)

    def test_the_backlog_and_the_docs_in_different_records_are_refused(self, tmp_path):
        starter(tmp_path)
        (tmp_path / "store").mkdir()
        raw = {"version": 1, "project": {"name": "toy"}, **ENFORCED,
               "records": {"backlog": "store"}}
        with pytest.raises(ManifestError, match="records.backlog.*records.docs"):
            Manifest.from_raw(
                raw, root=tmp_path, path=tmp_path / MANIFEST_RELPATH,
                store_root=tmp_path / "store", env={},
            )

    def test_a_docs_project_in_a_store_is_read_against_the_store(self, tmp_path):
        store = tmp_path / "store"
        starter(store)
        raw = {"version": 1, "project": {"name": "toy"}, **ENFORCED,
               "records": {"backlog": "store", "docs": "store"}}
        manifest = Manifest.from_raw(
            raw, root=tmp_path / "repo", path=tmp_path / "repo" / MANIFEST_RELPATH,
            store_root=store, env={},
        )
        assert manifest.get("docs.needs.state") == "enforced"


class TestLevelsByState:
    @pytest.mark.parametrize("profile", ["loose", "standard", "strict"])
    def test_the_two_rules_warn_while_migrating(self, tmp_path, profile):
        manifest = load(tmp_path, enforcement={"profile": profile})
        assert manifest.rule_level("docs.builds_strict") is Level.WARN
        assert manifest.rule_level("docs.needs_traceable") is Level.WARN

    @pytest.mark.parametrize("profile", ["loose", "standard", "strict"])
    def test_the_two_rules_block_when_enforced(self, tmp_path, profile):
        starter(tmp_path)
        manifest = load(tmp_path, enforcement={"profile": profile}, **ENFORCED)
        assert manifest.rule_level("docs.builds_strict") is Level.BLOCK
        assert manifest.rule_level("docs.needs_traceable") is Level.BLOCK
        assert "enforced" in manifest.source_of("enforcement.rules.docs.builds_strict")


class TestDiagrams:
    def test_a_single_name_still_loads_as_a_list_of_one(self, tmp_path):
        assert load(tmp_path, {"diagrams": "plantuml"}).diagram_formats() == ["plantuml"]

    def test_a_list_loads(self, tmp_path):
        manifest = load(tmp_path, {"diagrams": ["mermaid", "plantuml"]})
        assert manifest.diagram_formats() == ["mermaid", "plantuml"]

    @pytest.mark.parametrize("value", [None, "none", []])
    def test_none_is_no_diagram(self, tmp_path, value):
        assert load(tmp_path, {"diagrams": value}).diagram_formats() == []

    @pytest.mark.parametrize("value", [5, ["mermaid", 3], {"a": 1}])
    def test_a_malformed_value_is_refused(self, tmp_path, value):
        with pytest.raises(ManifestError, match="docs.diagrams"):
            load(tmp_path, {"diagrams": value})
