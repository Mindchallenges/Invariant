"""The guidelines ruleset core (IDEA_031 A2 increment 1, ADR-012 D3, D4).

One YAML ruleset per project, schema version 1. Loading it is all-or-nothing:
every problem is collected and the ruleset is refused whole, so a gate never
answers from a ruleset it read only in part. `guidelines.ruleset_valid` is the
rule that says so, N/A when no ruleset is configured, and it rides the landing
state's entry only on a project that has opted in.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from invariant.core import guidelines
from invariant.core.guidelines import RulesetError, digest_of, parse
from invariant.core.machines import load_pipeline_machine
from invariant.core.rules import RuleContext, all_rules, evaluate

REGISTERED = frozenset(spec.id for spec in all_rules())

RULESET = """\
version: 1
sections:
  - id: di
    title: Clean architecture and dependency injection
  - id: tests
    title: Testing
rules:
  - id: di.views-no-concrete-services
    section: di
    title: Views never touch concrete services or singletons
    statement: >
      A view never reaches a concrete service or a shared singleton.
    severity: blocker
    phases: [design, review]
    applies_to: ["App/UI/**/*.swift"]
    check:
      kind: pattern
      engine: regex
      in: code
      match: '\\.shared\\b'
    exceptions:
      - path: App/Composition/LiveDependencies.swift
        reason: the composition root is where concrete services are built
      - path: App/UI/Legacy.swift
        reason: scheduled for removal
        fingerprint: 3f2a9c1b7e44
    rationale: >
      The view layer must be previewable without live services.
    links: [docs/architecture/overview.md]
    amended:
      - on: 2026-07-07
        note: widened to cover property wrappers
        adr: docs/adr/ADR-012.md
  - id: tests.coverage-floor
    section: tests
    title: Touched files meet the coverage floor
    statement: Every touched source file meets the floor.
    severity: warn
    phases: [landing]
    applies_to: ["**"]
    check: {kind: engine, rule: coverage.meets_floor}
"""


def problems_of(text: str) -> list[str]:
    with pytest.raises(RulesetError) as caught:
        parse(text, registered=REGISTERED)
    return list(caught.value.problems)


def mutated(edit) -> str:
    raw = yaml.safe_load(RULESET)
    edit(raw)
    return yaml.safe_dump(raw, sort_keys=False)


# -- a ruleset that validates ---------------------------------------------------


class TestAValidRuleset:
    def test_it_loads_whole(self):
        ruleset = parse(RULESET, registered=REGISTERED)
        assert ruleset.version == 1
        assert [s.id for s in ruleset.sections] == ["di", "tests"]
        assert [r.id for r in ruleset.rules] == [
            "di.views-no-concrete-services",
            "tests.coverage-floor",
        ]

    def test_every_field_of_a_pattern_rule_is_kept(self):
        rule = parse(RULESET, registered=REGISTERED).rules[0]
        assert rule.section == "di"
        assert rule.severity == "blocker"
        assert rule.phases == ("design", "review")
        assert rule.applies_to == ("App/UI/**/*.swift",)
        assert rule.check.kind == "pattern"
        assert rule.check.engine == "regex"
        assert rule.check.scope == "code"
        assert rule.check.match == "\\.shared\\b"
        assert rule.exceptions[0].path == "App/Composition/LiveDependencies.swift"
        assert rule.exceptions[0].fingerprint is None
        assert rule.exceptions[1].fingerprint == "3f2a9c1b7e44"
        assert "previewable" in rule.rationale
        assert rule.links == ("docs/architecture/overview.md",)
        assert rule.amended[0].on == "2026-07-07"
        assert rule.amended[0].adr == "docs/adr/ADR-012.md"

    def test_an_engine_rule_points_at_a_registered_rule(self):
        rule = parse(RULESET, registered=REGISTERED).rules[1]
        assert rule.check.kind == "engine"
        assert rule.check.rule == "coverage.meets_floor"
        assert rule.exceptions == () and rule.links == () and rule.amended == ()
        assert rule.rationale == ""

    def test_the_digest_is_the_first_twelve_hex_of_the_bytes(self):
        ruleset = parse(RULESET, registered=REGISTERED)
        assert ruleset.digest == digest_of(RULESET.encode("utf-8"))
        assert len(ruleset.digest) == 12
        assert int(ruleset.digest, 16) >= 0

    def test_the_digest_moves_with_any_byte(self):
        assert digest_of(b"a") != digest_of(b"b")

    def test_an_empty_rule_list_is_a_valid_ruleset(self):
        ruleset = parse("version: 1\nsections: []\nrules: []\n", registered=REGISTERED)
        assert ruleset.rules == () and ruleset.sections == ()

    def test_a_string_amendment_date_is_kept_as_written(self):
        text = mutated(lambda r: r["rules"][0]["amended"][0].update(on="2026-07-07"))
        assert parse(text, registered=REGISTERED).rules[0].amended[0].on == "2026-07-07"

    def test_load_reads_the_file(self, tmp_path):
        path = tmp_path / "ruleset.yaml"
        path.write_text(RULESET, encoding="utf-8")
        assert guidelines.load(path, registered=REGISTERED).digest == digest_of(path.read_bytes())

    def test_by_id(self):
        ruleset = parse(RULESET, registered=REGISTERED)
        assert ruleset.by_id("tests.coverage-floor").severity == "warn"
        assert ruleset.by_id("no.such") is None


# -- what is refused, and that it is refused whole ------------------------------


class TestRefusals:
    @pytest.mark.parametrize(
        ("edit", "needle"),
        [
            (lambda r: r.update(extra=1), "unknown key(s) in the ruleset: extra"),
            (lambda r: r.update(version=2), "version 2"),
            (lambda r: r.pop("rules"), "missing rules"),
            (lambda r: r.update(sections={}), "sections must be a list"),
            (lambda r: r.update(rules={}), "rules must be a list"),
            (lambda r: r["sections"].append("di"), "sections[3] must be a mapping"),
            (lambda r: r["sections"].append({"id": "di", "title": "again"}), "section 'di' is declared twice"),
            (lambda r: r["sections"][0].update(colour="red"), "unknown key(s) in section 'di': colour"),
            (lambda r: r["sections"][0].pop("title"), "section 'di': missing title"),
            (lambda r: r["sections"][0].update(id=""), "sections[1]: id must be a non-empty string"),
            (lambda r: r["rules"].append("x"), "rules[3] must be a mapping"),
            (lambda r: r["rules"][0].update(colour="red"), "unknown key(s) in rule 'di.views-no-concrete-services': colour"),
            (lambda r: r["rules"][0].pop("statement"), "missing statement"),
            (lambda r: r["rules"][0].update(id="has space"), "rules[1]: id must be a non-empty string with no whitespace"),
            (lambda r: r["rules"][1].update(id="di.views-no-concrete-services"), "rule 'di.views-no-concrete-services' is declared twice"),
            (lambda r: r["rules"][0].update(section="nope"), "section 'nope', which no section declares"),
            (lambda r: r["rules"][0].update(severity="fatal"), "severity 'fatal' is not one of blocker, warn"),
            (lambda r: r["rules"][0].update(phases="design"), "phases must be a list"),
            (lambda r: r["rules"][0].update(phases=[]), "phases names no phase"),
            (lambda r: r["rules"][0].update(phases=["design", "deploy"]), "phase 'deploy' is not one of design, review, landing, always"),
            (lambda r: r["rules"][0].update(applies_to="App/**"), "applies_to must be a list of glob strings, not a bare string"),
            (lambda r: r["rules"][0].update(applies_to=[]), "applies_to names nothing"),
            (lambda r: r["rules"][0].update(applies_to=["ok", 3]), "applies_to must be a list of glob strings"),
            (lambda r: r["rules"][0].update(title=""), "title must be a non-empty string"),
            (lambda r: r["rules"][0].update(rationale=["a"]), "rationale must be a string"),
            (lambda r: r["rules"][0].update(links="x"), "links must be a list of strings"),
            (lambda r: r["rules"][0].update(exceptions={}), "exceptions must be a list"),
            (lambda r: r["rules"][0]["exceptions"].append("x"), "exceptions[3] must be a mapping"),
            (lambda r: r["rules"][0]["exceptions"][0].pop("reason"), "exceptions[1]: missing reason"),
            (lambda r: r["rules"][0]["exceptions"][0].update(reason=""), "exceptions[1]: reason must be a non-empty string"),
            (lambda r: r["rules"][0]["exceptions"][0].update(why="x"), "unknown key(s) in rule 'di.views-no-concrete-services' exceptions[1]: why"),
            (lambda r: r["rules"][0]["exceptions"][1].update(fingerprint="XYZ"), "fingerprint must be twelve lowercase hex digits"),
            (lambda r: r["rules"][0].update(amended={}), "amended must be a list"),
            (lambda r: r["rules"][0]["amended"].append(3), "amended[2] must be a mapping"),
            (lambda r: r["rules"][0]["amended"][0].pop("note"), "amended[1]: missing note"),
            (lambda r: r["rules"][0]["amended"][0].update(on=""), "amended[1]: on must be a date"),
            (lambda r: r["rules"][0]["amended"][0].update(adr=4), "amended[1]: adr must be a non-empty string"),
            (lambda r: r["rules"][0].update(check="pattern"), "check must be a mapping"),
            (lambda r: r["rules"][0]["check"].pop("kind"), "check: missing kind"),
            (lambda r: r["rules"][0]["check"].update(kind="vibes"), "unknown check kind 'vibes'"),
            (lambda r: r["rules"][0]["check"].update(match="(unclosed"), "match does not compile"),
            (lambda r: r["rules"][0]["check"].update(match=""), "match must be a non-empty string"),
            (lambda r: r["rules"][0]["check"].pop("engine"), "check: missing engine"),
            (lambda r: r["rules"][0]["check"].pop("in"), "check: missing in"),
            (lambda r: r["rules"][0]["check"].update({"in": "everywhere"}), "in 'everywhere' is not one of code, comments, strings, any"),
            (lambda r: r["rules"][0]["check"].update(engine="syntax"), "engine 'syntax' is deferred in v1"),
            (lambda r: r["rules"][0]["check"].update(engine="imports"), "engine 'imports' is not a pattern engine"),
            (lambda r: r["rules"][0]["check"].update(rule="x"), "unknown key(s) in rule 'di.views-no-concrete-services' check: rule"),
            (lambda r: r["rules"][1]["check"].update(rule="coverage.meets_flor"), "names no registered rule; did you mean 'coverage.meets_floor'?"),
            (lambda r: r["rules"][1]["check"].update(rule="zzzz"), "names no registered rule"),
            (lambda r: r["rules"][1]["check"].pop("rule"), "check: missing rule"),
            (lambda r: r["rules"][1]["check"].update(rule=7), "rule must be a non-empty string"),
            (lambda r: r["rules"][1]["check"].update(engine="regex"), "check: engine"),
        ],
    )
    def test_the_loader_refuses(self, edit, needle):
        problems = problems_of(mutated(edit))
        assert any(needle in p for p in problems), problems

    @pytest.mark.parametrize("kind", ["boundary", "cochange", "judgement"])
    def test_a_v1_kind_this_build_cannot_validate_is_refused_not_waved_through(self, kind):
        """Accepting an unvalidated kind would let a malformed rule load as
        though it had been checked. Refused, and the refusal says why."""
        text = mutated(lambda r: r["rules"][0].update(check={"kind": kind}))
        problems = problems_of(text)
        assert any(f"kind {kind!r}" in p and "does not validate" in p for p in problems), problems

    def test_every_problem_is_reported_not_only_the_first(self):
        def two(r):
            r["rules"][0].update(severity="fatal")
            r["rules"][1]["check"].update(rule="nope.nope")

        problems = problems_of(mutated(two))
        assert len(problems) == 2
        assert str(RulesetError(problems)).startswith("the ruleset has 2 problem(s)")

    @pytest.mark.parametrize("text", ["- a list\n", "version: [unclosed\n", ""])
    def test_a_document_that_is_not_a_mapping_is_refused(self, text):
        assert problems_of(text)

    def test_load_refuses_unreadable_bytes(self, tmp_path):
        path = tmp_path / "ruleset.yaml"
        path.write_bytes(b"\xff\xfe\x00")
        with pytest.raises(RulesetError, match="could not be read"):
            guidelines.load(path, registered=REGISTERED)


# -- the rule ---------------------------------------------------------------------


def context(make_manifest, tmp_path, body=None, *, session=None):
    manifest = make_manifest({"version": 1, "project": {"name": "demo"}, **(body or {})})
    return RuleContext(manifest=manifest, cwd=tmp_path, session=session)


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def ruleset_valid(ctx):
    (result,) = evaluate(["guidelines.ruleset_valid"], ctx)
    return result


CONFIGURED = {"guidelines": {"ruleset": "docs/guidelines/ruleset.yaml"}}


class TestRulesetValid:
    def test_no_ruleset_configured_is_not_applicable(self, make_manifest, tmp_path):
        result = ruleset_valid(context(make_manifest, tmp_path))
        assert result.verdict.skipped
        assert "no guidelines ruleset configured" in result.verdict.message

    def test_it_blocks_in_the_standard_profile(self, make_manifest, tmp_path):
        assert context(make_manifest, tmp_path).manifest.rule_level(
            "guidelines.ruleset_valid"
        ).value == "block"

    def test_a_valid_ruleset_passes_naming_what_it_read(self, make_manifest, tmp_path):
        write(tmp_path, "docs/guidelines/ruleset.yaml", RULESET)
        result = ruleset_valid(context(make_manifest, tmp_path, CONFIGURED))
        assert result.verdict.ok and not result.verdict.skipped
        assert "2 guideline(s)" in result.verdict.message
        assert digest_of(RULESET.encode("utf-8")) in result.verdict.message

    def test_a_malformed_ruleset_fails_naming_every_problem(self, make_manifest, tmp_path):
        write(tmp_path, "docs/guidelines/ruleset.yaml", RULESET.replace("blocker", "fatal"))
        result = ruleset_valid(context(make_manifest, tmp_path, CONFIGURED))
        assert result.blocks
        assert "does not validate" in result.verdict.message
        assert "severity 'fatal'" in result.verdict.detail

    def test_a_configured_ruleset_that_does_not_exist_fails(self, make_manifest, tmp_path):
        """Fails rather than skips: an absent file cannot be told apart from
        a ruleset that was never wired (D3, every gate that needs it fails)."""
        result = ruleset_valid(context(make_manifest, tmp_path, CONFIGURED))
        assert result.blocks
        assert "does not exist" in result.verdict.message

    def test_a_ruleset_outside_the_project_fails(self, make_manifest, tmp_path):
        body = {"guidelines": {"ruleset": "../elsewhere/ruleset.yaml"}}
        result = ruleset_valid(context(make_manifest, tmp_path, body))
        assert result.blocks
        assert "names nothing inside the project" in result.verdict.message

    def test_an_absolute_path_inside_the_project_is_read(self, make_manifest, tmp_path):
        path = write(tmp_path, "docs/guidelines/ruleset.yaml", RULESET)
        body = {"guidelines": {"ruleset": str(path)}}
        assert ruleset_valid(context(make_manifest, tmp_path, body)).verdict.ok

    def test_the_session_worktree_copy_is_the_one_judged(self, make_manifest, tmp_path):
        """The tree in hand, with no fallback to another checkout's copy: a
        ruleset broken in the session must not pass on the main checkout's."""
        write(tmp_path, "docs/guidelines/ruleset.yaml", RULESET)
        worktree = tmp_path / "wt"
        write(worktree, "docs/guidelines/ruleset.yaml", "version: 9\n")
        ctx = context(make_manifest, tmp_path, CONFIGURED, session={"worktree": str(worktree)})
        assert ruleset_valid(ctx).blocks

    def test_the_rule_reads_the_registry_for_engine_rules(self, make_manifest, tmp_path):
        bad = RULESET.replace("coverage.meets_floor", "coverage.meets_flor")
        write(tmp_path, "docs/guidelines/ruleset.yaml", bad)
        result = ruleset_valid(context(make_manifest, tmp_path, CONFIGURED))
        assert "did you mean 'coverage.meets_floor'" in result.verdict.detail


class TestLandingInjection:
    """ADR-012 D3 and ruling 7: the ruleset gate rides the landing state's
    entry, and only on a project with a ruleset configured, so a project that
    has not opted in sees no change (D3, "No ruleset, no change")."""

    @pytest.mark.parametrize("methodology", ["tdd", "bugfix", "spec-first", "chore", "docs"])
    def test_without_a_ruleset_no_machine_carries_it(self, make_manifest, methodology):
        machine = load_pipeline_machine(
            make_manifest({"version": 1, "methodology": {"pipeline": methodology}})
        )
        assert "guidelines.ruleset_valid" not in machine.gate_ids()

    @pytest.mark.parametrize("methodology", ["tdd", "bugfix", "spec-first", "chore", "docs"])
    def test_with_one_the_landing_entry_carries_it(self, make_manifest, methodology):
        machine = load_pipeline_machine(
            make_manifest(
                {"version": 1, "methodology": {"pipeline": methodology}, **CONFIGURED}
            )
        )
        (landing,) = machine.states_with_role("landing")
        assert "guidelines.ruleset_valid" in machine.state(landing).entry_gates
        for state in machine.states.values():
            gates = (*state.exit_gates, *(() if state.id == landing else state.entry_gates))
            assert "guidelines.ruleset_valid" not in gates, state.id

    def test_the_machine_without_a_landing_state_gains_nothing(self, make_manifest):
        machine = load_pipeline_machine(
            make_manifest({"version": 1, "methodology": {"pipeline": "none"}, **CONFIGURED})
        )
        assert "guidelines.ruleset_valid" not in machine.gate_ids()


def test_the_module_imports_nothing_above_core():
    """`core/rules` asks it questions, so it may not ask `core/rules` back,
    and nothing in `core/` reaches a domain, the CLI, a host or the oracle."""
    source = Path(guidelines.__file__).read_text(encoding="utf-8")
    for forbidden in ("from .rules", "from ..domains", "from ..cli", "from ..hosts", "from ..oracle"):
        assert forbidden not in source, forbidden


class TestManifestKey:
    """D13's `guidelines.ruleset`, and only it: a key this build does not yet
    read is refused rather than loaded and ignored (the BUG_050 shape)."""

    def test_the_default_is_no_ruleset(self, make_manifest):
        manifest = make_manifest({"version": 1})
        assert manifest.get("guidelines.ruleset") is None
        assert manifest.source_of("guidelines.ruleset") == "default"

    def test_a_configured_path_is_kept_as_written(self, make_manifest):
        manifest = make_manifest({"version": 1, **CONFIGURED})
        assert manifest.get("guidelines.ruleset") == "docs/guidelines/ruleset.yaml"
        assert manifest.source_of("guidelines.ruleset") == "manifest"

    @pytest.mark.parametrize(
        ("guidelines_value", "needle"),
        [
            ("docs/guidelines/ruleset.yaml", "guidelines must be a mapping"),
            ({"ruleset": 3}, "guidelines.ruleset must be null or a path"),
            ({"ruleset": "  "}, "guidelines.ruleset must be null or a path"),
            ({"instruction_files": ["CLAUDE.md"]}, "guidelines.instruction_files is not a key this build reads"),
            ({"checklist": 3}, "guidelines.checklist must be null or a path"),
            ({"checklist": ""}, "guidelines.checklist must be null or a path"),
        ],
    )
    def test_it_is_refused_at_load(self, make_manifest, guidelines_value, needle):
        from invariant.core.errors import ManifestError

        with pytest.raises(ManifestError, match=needle.replace(".", r"\.")):
            make_manifest({"version": 1, "guidelines": guidelines_value})

    def test_the_checklist_defaults_to_null(self, make_manifest):
        manifest = make_manifest({"version": 1})
        assert manifest.get("guidelines.checklist") is None
        assert manifest.source_of("guidelines.checklist") == "default"

    def test_a_configured_checklist_is_kept_as_written(self, make_manifest):
        manifest = make_manifest(
            {"version": 1, "guidelines": {"checklist": "docs/guidelines/rules.md"}}
        )
        assert manifest.get("guidelines.checklist") == "docs/guidelines/rules.md"
        assert manifest.source_of("guidelines.checklist") == "manifest"


# -- the generated checklist (A2 increment 3, DN-B) -----------------------------------


SOURCE = "docs/guidelines/ruleset.yaml"


def rendered(fmt="markdown", text=RULESET):
    return guidelines.render_checklist(
        parse(text, registered=REGISTERED), fmt, source=SOURCE
    )


class TestTheManagedMarker:
    """The first line holds the digest of everything after it, so a hand
    edit is detectable without re-rendering (ADR-012 D3, ruling DN-B)."""

    def test_markdown_marker_is_an_html_comment_holding_the_body_digest(self):
        text = rendered("markdown")
        first, body = text.split("\n", 1)
        assert first == f"<!-- invariant-managed: {digest_of(body.encode('utf-8'))} -->"

    def test_rst_marker_is_a_comment_followed_by_a_blank_line(self):
        text = rendered("sphinx")
        first, second, *_ = text.split("\n")
        _, body = text.split("\n", 1)
        assert first == f".. invariant-managed: {digest_of(body.encode('utf-8'))}"
        assert second == ""

    @pytest.mark.parametrize("fmt", ["markdown", "sphinx"])
    def test_split_managed_reads_back_the_digest_and_the_body(self, fmt):
        text = rendered(fmt)
        digest, body = guidelines.split_managed(text, fmt)
        assert digest == digest_of(body.encode("utf-8"))
        assert text.endswith(body)

    @pytest.mark.parametrize(
        ("fmt", "text"),
        [
            ("markdown", "# Hand written\n\nrules\n"),
            ("markdown", ".. invariant-managed: 3f2a9c1b7e44\n\nbody\n"),
            ("sphinx", "<!-- invariant-managed: 3f2a9c1b7e44 -->\nbody\n"),
            ("markdown", "<!-- invariant-managed: not-hex -->\nbody\n"),
            ("markdown", ""),
        ],
    )
    def test_a_file_without_this_formats_marker_is_unmanaged(self, fmt, text):
        assert guidelines.split_managed(text, fmt) == (None, text)

    def test_an_unknown_format_is_refused_naming_the_formats(self):
        with pytest.raises(ValueError, match="markdown, sphinx"):
            rendered("asciidoc")
        with pytest.raises(ValueError, match="markdown, sphinx"):
            guidelines.split_managed("x", "asciidoc")

    def test_the_suffix_follows_the_docs_format(self):
        assert guidelines.CHECKLIST_SUFFIXES == {"markdown": ".md", "sphinx": ".rst"}


class TestTheChecklistBody:
    def test_rendering_is_deterministic(self):
        assert rendered() == rendered()

    def test_it_names_its_source_and_the_ruleset_digest(self):
        text = rendered()
        assert SOURCE in text
        assert digest_of(RULESET.encode("utf-8")) in text
        assert "invariant guidelines render" in text

    @pytest.mark.parametrize("fmt", ["markdown", "sphinx"])
    def test_every_rule_field_a_reader_relied_on_is_kept(self, fmt):
        text = rendered(fmt)
        for needle in (
            "Clean architecture and dependency injection",
            "di.views-no-concrete-services",
            "Views never touch concrete services or singletons",
            "A view never reaches a concrete service or a shared singleton.",
            "blocker",
            "design, review",
            "App/UI/**/*.swift",
            "regex",
            "\\.shared\\b",
            "App/Composition/LiveDependencies.swift",
            "the composition root is where concrete services are built",
            "3f2a9c1b7e44",
            "The view layer must be previewable without live services.",
            "docs/architecture/overview.md",
            "2026-07-07",
            "widened to cover property wrappers",
            "docs/adr/ADR-012.md",
            "tests.coverage-floor",
            "coverage.meets_floor",
        ):
            assert needle in text, needle

    def test_rules_are_listed_under_their_section_in_declared_order(self):
        text = rendered()
        assert text.index("Clean architecture") < text.index("di.views-no-concrete")
        assert text.index("di.views-no-concrete") < text.index("## Testing")
        assert text.index("## Testing") < text.index("tests.coverage-floor")

    def test_a_section_with_no_rules_says_so(self):
        text = rendered(text=RULESET.replace("section: tests", "section: di"))
        testing = text[text.index("## Testing"):]
        assert "No rules in this section." in testing

    def test_rst_headings_are_underlined_at_least_their_length(self):
        lines = rendered("sphinx").split("\n")
        for index, line in enumerate(lines[1:-1], start=1):
            under = lines[index + 1]
            if under and set(under) <= set("=-~") and line.strip():
                assert len(under) >= len(line), line

    def test_markdown_code_holding_a_backtick_stays_one_span(self):
        text = RULESET.replace("'\\.shared\\b'", "'a`b'")
        assert "`` a`b ``" in rendered(text=text)

    def test_a_multi_paragraph_statement_keeps_its_paragraphs(self):
        text = mutated(
            lambda r: r["rules"][0].update(statement="First line\nwraps.\n\nSecond paragraph.")
        )
        out = rendered(text=text)
        assert "First line wraps.\n\nSecond paragraph." in out

    @pytest.mark.parametrize("fmt", ["markdown", "sphinx"])
    def test_a_rule_ending_in_a_nested_list_leaves_no_doubled_blank(self, fmt):
        def edit(raw):
            for key in ("rationale", "links", "amended"):
                raw["rules"][0].pop(key)
        out = rendered(fmt, text=mutated(edit))
        assert "\n\n\n" not in out
        assert out.endswith("\n") and not out.endswith("\n\n")

    def test_a_kind_with_no_summary_yet_is_named_by_its_kind(self):
        from invariant.core.guidelines import Check, _check_summary

        assert _check_summary(Check(kind="judgement"), str) == "judgement"

