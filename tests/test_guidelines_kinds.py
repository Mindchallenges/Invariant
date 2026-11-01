"""The check-kind sub-schemas (IDEA_031 A2 increment 2, ADR-012's check-kind
amendment, rulings DN-A1 to DN-A14).

`boundary`, both forms of `cochange`, and `judgement` are validated here, and
a registry a `cochange` rule names is read whole or refused as part of the
ruleset (DN-A9). Nothing is evaluated: A3 and A4 do that. What this proves is
that a rule of these kinds loads only when its keys say something exact.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from invariant.core import guidelines
from invariant.core.guidelines import RulesetError, parse
from invariant.core.rules import RuleContext, all_rules, evaluate

REGISTERED = frozenset(spec.id for spec in all_rules())

RULESET = """\
version: 1
sections:
  - id: layers
    title: Layers
  - id: tests
    title: Testing
  - id: quality
    title: Code quality
  - id: data
    title: Data safety
rules:
  - id: layers.core-never-imports-oracle
    section: layers
    title: Nothing in core imports the oracle
    statement: No module under core reaches into oracle.
    severity: blocker
    phases: [review, landing]
    applies_to: ["src/invariant/core/**/*.py"]
    check:
      kind: boundary
      engine: imports
      forbid: ["src/invariant/oracle/**"]
  - id: layers.core-references-only-core
    section: layers
    title: core references nothing above core
    statement: A module under core imports only other modules under core.
    severity: blocker
    phases: [review, landing]
    applies_to: ["src/invariant/core/**/*.py"]
    check:
      kind: boundary
      engine: imports
      only: ["src/invariant/core/**/*.py"]
  - id: tests.core-module-has-paired-test
    section: tests
    title: A core module change touches its paired test file
    statement: A change to src/invariant/core/X.py touches tests/test_X.py.
    severity: warn
    phases: [landing]
    applies_to: ["src/invariant/core/*.py"]
    check:
      kind: cochange
      form: pair
      when: {change: executable}
      then: {each: "tests/test_{stem}.py"}
  - id: tests.mocks-move-together
    section: tests
    title: A new container member updates both mock containers
    statement: The dummy and the mock container gain the member too.
    severity: blocker
    phases: [design, review, landing]
    applies_to: ["App/Composition/PreviewDependencies.swift"]
    check:
      kind: cochange
      form: pair
      when: {change: any, in: code, match: '^\\s*(var|let|func)\\s'}
      then:
        all_of:
          - App/Composition/DummyPreviewDependencies.swift
          - App/Composition/MockPreviewDependencies.swift
  - id: quality.duplication-registry
    section: quality
    title: A touched file is not left in an open duplication entry
    statement: A touched file in an open entry is resolved in the same change.
    severity: blocker
    phases: [design, review, landing]
    applies_to: ["App/**/*.swift"]
    check:
      kind: cochange
      form: registry
      registry: docs/guidelines/duplication.yaml
      outcomes: {contained: blocker, scoped-refactor: blocker, advisory: warn}
      fixes:
        scoped-refactor: stop and scope a refactor session
  - id: data.single-open-library
    section: data
    title: Only one library is open at a time
    statement: A switch closes the current library before it opens the next.
    severity: blocker
    phases: [design, review]
    applies_to: ["App/Storage/**/*.swift"]
    check:
      kind: judgement
      evidence: both
      scope: diff
      not_applicable_when:
        - id: no-handle-code
          mechanical:
            no_added_match: {in: code, match: '\\b(openLibrary|blobRoot)\\b'}
        - id: no-executable-change
          mechanical: {no_executable_change: true}
        - id: read-only-path
          claim: The change only reads through an already-open library.
"""

REGISTRY_REL = "docs/guidelines/duplication.yaml"

REGISTRY = """\
version: 1
entries:
  - id: DUP-07
    paths: [App/Storage/MediaStore.swift, App/Storage/ThumbnailStore.swift]
    status: open
    class: contained
    note: two copies of the suffix-normalising reader
  - id: DUP-08
    paths: [App/UI/Grid.swift]
    status: resolved
    class: advisory
"""


@pytest.fixture
def tree(tmp_path) -> Path:
    write(tmp_path, REGISTRY_REL, REGISTRY)
    return tmp_path


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def mutated(edit, text: str = RULESET) -> str:
    raw = yaml.safe_load(text)
    edit(raw)
    return yaml.safe_dump(raw, sort_keys=False)


def rule_of(raw, rule_id):
    return next(r for r in raw["rules"] if r["id"] == rule_id)


def check_of(raw, rule_id):
    return rule_of(raw, rule_id)["check"]


def problems_of(text: str, root: Path | None) -> list[str]:
    with pytest.raises(RulesetError) as caught:
        parse(text, registered=REGISTERED, root=root)
    return list(caught.value.problems)


def registry_problems(tree: Path, registry_text: str) -> list[str]:
    write(tree, REGISTRY_REL, registry_text)
    return problems_of(RULESET, tree)


def registry_mutated(edit) -> str:
    return mutated(edit, REGISTRY)


# -- every kind validates ----------------------------------------------------------


class TestEveryV1KindIsValidated:
    def test_all_five_kinds_are_validated(self):
        assert guidelines.VALIDATED_KINDS == frozenset(guidelines.KINDS)

    def test_the_ruleset_with_every_kind_loads_whole(self, tree):
        ruleset = parse(RULESET, registered=REGISTERED, root=tree)
        assert [r.check.kind for r in ruleset.rules] == [
            "boundary", "boundary", "cochange", "cochange", "cochange", "judgement",
        ]

    def test_load_resolves_the_registry_against_the_root(self, tree):
        path = write(tree, "docs/guidelines/ruleset.yaml", RULESET)
        ruleset = guidelines.load(path, registered=REGISTERED, root=tree)
        assert [r.path for r in ruleset.registries] == [REGISTRY_REL]


# -- boundary ------------------------------------------------------------------------


class TestBoundary:
    def test_a_deny_list_is_kept(self, tree):
        check = parse(RULESET, registered=REGISTERED, root=tree).rules[0].check
        assert check.engine == "imports"
        assert check.forbid == ("src/invariant/oracle/**",)
        assert check.only == ()

    def test_an_allow_only_list_is_kept_and_may_repeat_applies_to(self, tree):
        """DN-A3: `only` may name the rule's own files, which is how a layer
        allows references within itself."""
        check = parse(RULESET, registered=REGISTERED, root=tree).rules[1].check
        assert check.only == ("src/invariant/core/**/*.py",)
        assert check.forbid == ()

    def test_a_target_glob_is_normalised(self, tree):
        text = mutated(
            lambda r: check_of(r, "layers.core-never-imports-oracle").update(
                forbid=["./src/invariant/oracle/**"]
            )
        )
        check = parse(text, registered=REGISTERED, root=tree).rules[0].check
        assert check.forbid == ("src/invariant/oracle/**",)

    @pytest.mark.parametrize(
        ("edit", "needle"),
        [
            (lambda c: c.pop("forbid"), "exactly one of forbid and only"),
            (lambda c: c.update(only=["src/**"]), "exactly one of forbid and only"),
            (lambda c: c.pop("engine"), "check: missing engine"),
            (lambda c: c.update(engine="symbols"), "engine 'symbols' is deferred in v1"),
            (lambda c: c.update(engine="regex"), "engine 'regex' is not a boundary engine; the boundary engines are imports"),
            (lambda c: c.update(forbid="src/invariant/oracle/**"), "forbid must be a list of glob strings, not a bare string"),
            (lambda c: c.update(forbid=[]), "forbid names nothing"),
            (lambda c: c.update(forbid=["ok/**", ""]), "forbid must be a list of glob strings"),
            (lambda c: c.update(forbid=["/abs/**"]), "forbid glob '/abs/**' is not a project-relative path glob"),
            (lambda c: c.update(forbid=["../outside/**"]), "forbid glob '../outside/**' is not a project-relative path glob"),
            (lambda c: c.update(forbid=["src/invariant/core/**/*.py"]), "forbid repeats applies_to glob 'src/invariant/core/**/*.py'"),
            (lambda c: c.update(allow=["x"]), "unknown key(s) in rule 'layers.core-never-imports-oracle' check: allow"),
        ],
    )
    def test_the_loader_refuses(self, tree, edit, needle):
        text = mutated(lambda r: edit(check_of(r, "layers.core-never-imports-oracle")))
        problems = problems_of(text, tree)
        assert any(needle in p for p in problems), problems

    def test_an_only_list_is_held_to_the_same_glob_rules(self, tree):
        text = mutated(
            lambda r: check_of(r, "layers.core-references-only-core").update(only="src/**")
        )
        problems = problems_of(text, tree)
        assert any("only must be a list of glob strings, not a bare string" in p for p in problems)


# -- cochange, pair form ----------------------------------------------------------------


PAIR = "tests.core-module-has-paired-test"
MOCKS = "tests.mocks-move-together"


class TestCochangePair:
    def test_a_template_trigger_and_requirement_are_kept(self, tree):
        rule = parse(RULESET, registered=REGISTERED, root=tree).by_id(PAIR)
        assert rule.check.form == "pair"
        assert rule.check.when == guidelines.Trigger(change="executable")
        assert rule.check.then == guidelines.Requirement(
            shape="each", template="tests/test_{stem}.py"
        )

    def test_a_match_trigger_and_an_all_of_requirement_are_kept(self, tree):
        rule = parse(RULESET, registered=REGISTERED, root=tree).by_id(MOCKS)
        assert rule.check.when == guidelines.Trigger(
            change="any", scope="code", match="^\\s*(var|let|func)\\s"
        )
        assert rule.check.then.shape == "all_of"
        assert rule.check.then.globs == (
            "App/Composition/DummyPreviewDependencies.swift",
            "App/Composition/MockPreviewDependencies.swift",
        )

    @pytest.mark.parametrize("change", ["any", "added", "executable"])
    def test_every_change_value_is_accepted(self, tree, change):
        text = mutated(lambda r: check_of(r, PAIR)["when"].update(change=change))
        assert parse(text, registered=REGISTERED, root=tree).by_id(PAIR).check.when.change == change

    def test_any_of_is_accepted(self, tree):
        text = mutated(lambda r: check_of(r, PAIR).update(then={"any_of": ["tests/**"]}))
        then = parse(text, registered=REGISTERED, root=tree).by_id(PAIR).check.then
        assert then == guidelines.Requirement(shape="any_of", globs=("tests/**",))

    @pytest.mark.parametrize(
        "template",
        ["{path}.snap", "{dir}/test_{name}", "tests/{stem}{suffix}", "{dir}/{{literal}}/{stem}.py"],
    )
    def test_every_template_token_is_accepted(self, tree, template):
        text = mutated(lambda r: check_of(r, PAIR).update(then={"each": template}))
        assert parse(text, registered=REGISTERED, root=tree).by_id(PAIR).check.then.template == template

    @pytest.mark.parametrize(
        ("edit", "needle"),
        [
            (lambda c: c.pop("form"), "check: missing form"),
            (lambda c: c.update(form="triple"), "form 'triple' is not one of pair, registry"),
            (lambda c: c.update(registry="x.yaml"), "unknown key(s) in rule 'tests.core-module-has-paired-test' check: registry"),
            (lambda c: c.pop("when"), "check: missing when"),
            (lambda c: c.pop("then"), "check: missing then"),
            (lambda c: c.update(when="executable"), "when must be a mapping"),
            (lambda c: c.update(when={}), "when: missing change"),
            (lambda c: c.update(when={"change": "edited"}), "when.change 'edited' is not one of any, added, executable"),
            (lambda c: c.update(when={"change": "any", "glob": "x"}), "unknown key(s) in rule 'tests.core-module-has-paired-test' check when: glob"),
            (lambda c: c.update(when={"change": "any", "match": "x"}), "when.match and when.in are given together or not at all"),
            (lambda c: c.update(when={"change": "any", "in": "code"}), "when.match and when.in are given together or not at all"),
            (lambda c: c.update(when={"change": "any", "in": "code", "match": "(open"}), "when.match does not compile"),
            (lambda c: c.update(when={"change": "any", "in": "code", "match": ""}), "when.match must be a non-empty string"),
            (lambda c: c.update(when={"change": "any", "in": "nowhere", "match": "x"}), "when.in 'nowhere' is not one of code, comments, strings, any"),
            (lambda c: c.update(then="tests/**"), "then must be a mapping"),
            (lambda c: c.update(then={}), "then names nothing: give exactly one of all_of, any_of, each"),
            (lambda c: c.update(then={"all_of": ["a"], "any_of": ["b"]}), "then gives all_of, any_of: give exactly one of all_of, any_of, each"),
            (lambda c: c.update(then={"each": "x/{stem}", "colour": 1}), "unknown key(s) in rule 'tests.core-module-has-paired-test' check then: colour"),
            (lambda c: c.update(then={"all_of": "tests/**"}), "then.all_of must be a list of glob strings, not a bare string"),
            (lambda c: c.update(then={"any_of": []}), "then.any_of names nothing"),
            (lambda c: c.update(then={"any_of": ["/abs/**"]}), "then.any_of glob '/abs/**' is not a project-relative path glob"),
            (lambda c: c.update(then={"each": ""}), "then.each must be a non-empty path template"),
            (lambda c: c.update(then={"each": 3}), "then.each must be a non-empty path template"),
            (lambda c: c.update(then={"each": "tests/test_x.py"}), "then.each names no token"),
            (lambda c: c.update(then={"each": "tests/{base}.py"}), "then.each token {base} is not one of {path}, {dir}, {name}, {stem}, {suffix}"),
            (lambda c: c.update(then={"each": "tests/{stem!r}.py"}), "then.each token {stem!r} is not one of"),
            (lambda c: c.update(then={"each": "tests/{stem:>4}.py"}), "then.each token {stem:>4} is not one of"),
            (lambda c: c.update(then={"each": "tests/{stem.py"}), "then.each is not a well-formed template"),
            (lambda c: c.update(then={"each": "/abs/{stem}.py"}), "then.each '/abs/{stem}.py' does not derive a project-relative path"),
            (lambda c: c.update(then={"each": "../{stem}.py"}), "then.each '../{stem}.py' does not derive a project-relative path"),
        ],
    )
    def test_the_loader_refuses(self, tree, edit, needle):
        text = mutated(lambda r: edit(check_of(r, PAIR)))
        problems = problems_of(text, tree)
        assert any(needle in p for p in problems), problems


# -- cochange, registry form ------------------------------------------------------------


DUP = "quality.duplication-registry"


class TestCochangeRegistry:
    def test_the_registry_rule_and_its_file_are_kept(self, tree):
        ruleset = parse(RULESET, registered=REGISTERED, root=tree)
        check = ruleset.by_id(DUP).check
        assert check.form == "registry"
        assert check.registry == REGISTRY_REL
        assert dict(check.outcomes) == {
            "contained": "blocker", "scoped-refactor": "blocker", "advisory": "warn",
        }
        assert dict(check.fixes) == {"scoped-refactor": "stop and scope a refactor session"}
        (registry,) = ruleset.registries
        assert registry.path == REGISTRY_REL
        assert [e.id for e in registry.entries] == ["DUP-07", "DUP-08"]
        first = registry.entries[0]
        assert first.paths == ("App/Storage/MediaStore.swift", "App/Storage/ThumbnailStore.swift")
        assert first.status == "open" and first.cls == "contained"
        assert first.note == "two copies of the suffix-normalising reader"
        assert registry.entries[1].note == ""
        assert registry.digest == guidelines.digest_of(REGISTRY.encode("utf-8"))

    def test_fixes_are_optional(self, tree):
        text = mutated(lambda r: check_of(r, DUP).pop("fixes"))
        assert parse(text, registered=REGISTERED, root=tree).by_id(DUP).check.fixes == ()

    def test_two_rules_naming_one_registry_read_it_once(self, tree):
        def twin(raw):
            copy = dict(rule_of(raw, DUP), id="quality.duplication-again")
            raw["rules"].append(copy)

        ruleset = parse(mutated(twin), registered=REGISTERED, root=tree)
        assert len(ruleset.registries) == 1

    def test_an_empty_registry_is_valid(self, tree):
        write(tree, REGISTRY_REL, "version: 1\nentries: []\n")
        (registry,) = parse(RULESET, registered=REGISTERED, root=tree).registries
        assert registry.entries == ()

    def test_a_resolved_entry_needs_no_outcome(self, tree):
        """Only an open entry produces a finding, so only an open entry's
        class must be mapped (DN-A8)."""
        write(tree, REGISTRY_REL, REGISTRY.replace("class: advisory", "class: retired"))
        assert parse(RULESET, registered=REGISTERED, root=tree)

    @pytest.mark.parametrize(
        ("edit", "needle"),
        [
            (lambda c: c.pop("registry"), "check: missing registry"),
            (lambda c: c.pop("outcomes"), "check: missing outcomes"),
            (lambda c: c.update(when={"change": "any"}), "unknown key(s) in rule 'quality.duplication-registry' check: when"),
            (lambda c: c.update(registry=""), "registry must be a project-relative path"),
            (lambda c: c.update(registry=7), "registry must be a project-relative path"),
            (lambda c: c.update(registry="."), "registry must be a project-relative path"),
            (lambda c: c.update(registry="/etc/dup.yaml"), "registry must be a project-relative path"),
            (lambda c: c.update(registry="../dup.yaml"), "registry must be a project-relative path"),
            (lambda c: c.update(registry="docs/guidelines/none.yaml"), "registry docs/guidelines/none.yaml does not exist"),
            (lambda c: c.update(outcomes={}), "outcomes names no class"),
            (lambda c: c.update(outcomes=["blocker"]), "outcomes must be a mapping of class to blocker or warn"),
            (lambda c: c.update(outcomes={"contained": "fatal"}), "outcome 'fatal' for class 'contained' is not one of blocker, warn"),
            (lambda c: c.update(fixes=["x"]), "fixes must be a mapping of class to a fix"),
            (lambda c: c["fixes"].update(contained=""), "fix for class 'contained' must be a non-empty string"),
            (lambda c: c["fixes"].update(retired="x"), "fixes names class 'retired', which outcomes does not map"),
            (lambda c: c.update(outcomes={"advisory": "warn"}), "open entry DUP-07 has class 'contained', which outcomes does not map"),
        ],
    )
    def test_the_loader_refuses(self, tree, edit, needle):
        text = mutated(lambda r: edit(check_of(r, DUP)))
        problems = problems_of(text, tree)
        assert any(needle in p for p in problems), problems

    def test_an_outcome_may_not_exceed_the_rules_severity(self, tree):
        text = mutated(lambda r: rule_of(r, DUP).update(severity="warn"))
        problems = problems_of(text, tree)
        assert any(
            "outcome 'blocker' for class 'contained' exceeds the rule's severity warn" in p
            for p in problems
        ), problems

    def test_with_no_tree_a_registry_cannot_be_read(self):
        problems = problems_of(RULESET, None)
        assert any("no project tree was given" in p for p in problems), problems

    @pytest.mark.parametrize(
        ("registry_text", "needle"),
        [
            ("- a list\n", "must be a mapping with version and entries"),
            ("version: [unclosed\n", "is not valid YAML"),
            ("version: 2\nentries: []\n", "version 2; this build reads version 1"),
            ("entries: []\n", "missing version"),
            ("version: 1\n", "missing entries"),
            ("version: 1\nentries: {}\n", "entries must be a list"),
            ("version: 1\nentries: []\ncolour: red\n", "unknown key(s) in registry docs/guidelines/duplication.yaml: colour"),
            ("version: 1\nentries: [x]\n", "entries[1] must be a mapping"),
        ],
    )
    def test_the_registry_envelope_is_refused_whole(self, tree, registry_text, needle):
        problems = registry_problems(tree, registry_text)
        assert any(needle in p and REGISTRY_REL in p for p in problems), problems

    def test_an_unreadable_registry_is_refused(self, tree):
        (tree / REGISTRY_REL).write_bytes(b"\xff\xfe\x00")
        problems = problems_of(RULESET, tree)
        assert any("could not be read" in p for p in problems), problems

    @pytest.mark.parametrize(
        ("edit", "needle"),
        [
            (lambda r: r["entries"][0].pop("id"), "entries[1]: id must be a non-empty string with no whitespace"),
            (lambda r: r["entries"][0].update(id="DUP 07"), "entries[1]: id must be a non-empty string with no whitespace"),
            (lambda r: r["entries"][1].update(id="DUP-07"), "entry DUP-07 is declared twice"),
            (lambda r: r["entries"][0].update(owner="x"), "unknown key(s) in registry docs/guidelines/duplication.yaml entry DUP-07: owner"),
            (lambda r: r["entries"][0].pop("paths"), "entry DUP-07: missing paths"),
            (lambda r: r["entries"][0].pop("status"), "entry DUP-07: missing status"),
            (lambda r: r["entries"][0].pop("class"), "entry DUP-07: missing class"),
            (lambda r: r["entries"][0].update(paths="App/A.swift"), "entry DUP-07: paths must be a list of project-relative paths"),
            (lambda r: r["entries"][0].update(paths=[]), "entry DUP-07: paths names nothing"),
            (lambda r: r["entries"][0].update(paths=["App/*.swift"]), "entry DUP-07: path 'App/*.swift' is a glob; a registry names files"),
            (lambda r: r["entries"][0].update(paths=["/abs/A.swift"]), "entry DUP-07: path '/abs/A.swift' is not a project-relative path"),
            (lambda r: r["entries"][0].update(paths=["."]), "entry DUP-07: path '.' is not a project-relative path"),
            (lambda r: r["entries"][0].update(status="closed"), "entry DUP-07: status 'closed' is not one of open, resolved"),
            (lambda r: r["entries"][0].update({"class": ""}), "entry DUP-07: class must be a non-empty string"),
            (lambda r: r["entries"][0].update(note=3), "entry DUP-07: note must be a string"),
        ],
    )
    def test_a_registry_entry_is_refused(self, tree, edit, needle):
        problems = registry_problems(tree, registry_mutated(edit))
        assert any(needle in p for p in problems), problems

    def test_an_entry_path_is_normalised(self, tree):
        write(tree, REGISTRY_REL, REGISTRY.replace("[App/UI/Grid.swift]", "[./App/UI/Grid.swift]"))
        (registry,) = parse(RULESET, registered=REGISTERED, root=tree).registries
        assert registry.entries[1].paths == ("App/UI/Grid.swift",)


# -- judgement ----------------------------------------------------------------------------


JUDGED = "data.single-open-library"


class TestJudgement:
    def test_every_key_is_kept(self, tree):
        check = parse(RULESET, registered=REGISTERED, root=tree).by_id(JUDGED).check
        assert check.evidence == "both"
        assert check.verdict_scope == "diff"
        added, executable, claim = check.not_applicable_when
        assert added == guidelines.NaCondition(
            id="no-handle-code",
            mechanical="no_added_match",
            scope="code",
            match="\\b(openLibrary|blobRoot)\\b",
        )
        assert executable == guidelines.NaCondition(
            id="no-executable-change", mechanical="no_executable_change"
        )
        assert claim == guidelines.NaCondition(
            id="read-only-path",
            claim="The change only reads through an already-open library.",
        )

    @pytest.mark.parametrize("evidence", ["citation", "test", "either", "both"])
    def test_every_evidence_value_is_accepted(self, tree, evidence):
        text = mutated(lambda r: check_of(r, JUDGED).update(evidence=evidence))
        assert parse(text, registered=REGISTERED, root=tree).by_id(JUDGED).check.evidence == evidence

    @pytest.mark.parametrize("scope", ["diff", "file"])
    def test_every_scope_is_accepted(self, tree, scope):
        text = mutated(lambda r: check_of(r, JUDGED).update(scope=scope))
        assert parse(text, registered=REGISTERED, root=tree).by_id(JUDGED).check.verdict_scope == scope

    def test_no_conditions_means_na_is_not_offered(self, tree):
        text = mutated(lambda r: check_of(r, JUDGED).pop("not_applicable_when"))
        assert parse(text, registered=REGISTERED, root=tree).by_id(JUDGED).check.not_applicable_when == ()

    @pytest.mark.parametrize(
        ("edit", "needle"),
        [
            (lambda c: c.pop("evidence"), "check: missing evidence"),
            (lambda c: c.pop("scope"), "check: missing scope"),
            (lambda c: c.update(evidence="vibes"), "evidence 'vibes' is not one of citation, test, either, both"),
            (lambda c: c.update(scope="tree"), "scope 'tree' is not one of diff, file"),
            (lambda c: c.update(candidates="x"), "unknown key(s) in rule 'data.single-open-library' check: candidates"),
            (lambda c: c.update(not_applicable_when={}), "not_applicable_when must be a list"),
            (lambda c: c["not_applicable_when"].append("x"), "not_applicable_when[4] must be a mapping"),
            (lambda c: c["not_applicable_when"][0].pop("id"), "not_applicable_when[1]: id must be a non-empty string with no whitespace"),
            (lambda c: c["not_applicable_when"][0].update(id="has space"), "not_applicable_when[1]: id must be a non-empty string with no whitespace"),
            (lambda c: c["not_applicable_when"][1].update(id="no-handle-code"), "condition 'no-handle-code' is declared twice"),
            (lambda c: c["not_applicable_when"][2].update(why="x"), "unknown key(s) in rule 'data.single-open-library' check condition 'read-only-path': why"),
            (lambda c: c["not_applicable_when"][2].pop("claim"), "condition 'read-only-path' gives neither claim nor mechanical"),
            (lambda c: c["not_applicable_when"][2].update(mechanical={"no_executable_change": True}), "condition 'read-only-path' gives both claim and mechanical"),
            (lambda c: c["not_applicable_when"][2].update(claim=""), "condition 'read-only-path': claim must be a non-empty string"),
            (lambda c: c["not_applicable_when"][1].update(mechanical="no_executable_change"), "condition 'no-executable-change': mechanical must be a mapping"),
            (lambda c: c["not_applicable_when"][1].update(mechanical={}), "mechanical names no condition: give exactly one of no_added_match, no_executable_change"),
            (lambda c: c["not_applicable_when"][1].update(mechanical={"no_executable_change": True, "no_added_match": {"in": "code", "match": "x"}}), "give exactly one of no_added_match, no_executable_change"),
            (lambda c: c["not_applicable_when"][1].update(mechanical={"no_change_in": ["x"]}), "unknown key(s) in rule 'data.single-open-library' check condition 'no-executable-change' mechanical: no_change_in"),
            (lambda c: c["not_applicable_when"][1].update(mechanical={"no_executable_change": False}), "no_executable_change must be true"),
            (lambda c: c["not_applicable_when"][0].update(mechanical={"no_added_match": "x"}), "no_added_match must be a mapping with in and match"),
            (lambda c: c["not_applicable_when"][0].update(mechanical={"no_added_match": {"in": "code"}}), "no_added_match: missing match"),
            (lambda c: c["not_applicable_when"][0].update(mechanical={"no_added_match": {"in": "code", "match": "x", "glob": "y"}}), "unknown key(s) in rule 'data.single-open-library' check condition 'no-handle-code' no_added_match: glob"),
            (lambda c: c["not_applicable_when"][0].update(mechanical={"no_added_match": {"in": "air", "match": "x"}}), "no_added_match.in 'air' is not one of code, comments, strings, any"),
            (lambda c: c["not_applicable_when"][0].update(mechanical={"no_added_match": {"in": "code", "match": "(x"}}), "no_added_match.match does not compile"),
            (lambda c: c["not_applicable_when"][0].update(mechanical={"no_added_match": {"in": "code", "match": ""}}), "no_added_match.match must be a non-empty string"),
        ],
    )
    def test_the_loader_refuses(self, tree, edit, needle):
        text = mutated(lambda r: edit(check_of(r, JUDGED)))
        problems = problems_of(text, tree)
        assert any(needle in p for p in problems), problems


# -- the checklist names every kind -------------------------------------------------------


class TestTheChecklistSummaries:
    @pytest.mark.parametrize("fmt", ["markdown", "sphinx"])
    def test_every_kind_is_summarised(self, tree, fmt):
        ruleset = parse(RULESET, registered=REGISTERED, root=tree)
        text = guidelines.render_checklist(ruleset, fmt, source="docs/guidelines/ruleset.yaml")
        for needle in (
            "boundary (imports): may not reference",
            "src/invariant/oracle/**",
            "boundary (imports): may reference only",
            "cochange: when a file changes (executable), each of",
            "tests/test_{stem}.py",
            "cochange: when a file changes (any) and an added line in code matches",
            "all of",
            "App/Composition/MockPreviewDependencies.swift",
            "cochange: a file in an open entry of",
            REGISTRY_REL,
            "contained: blocker",
            "advisory: warn",
            "judgement: a cited verdict, evidence both, scope diff",
            "N/A when: no-handle-code, no-executable-change, read-only-path",
        ):
            assert needle in text, needle

    def test_any_of_is_summarised(self, tree):
        text = mutated(lambda r: check_of(r, PAIR).update(then={"any_of": ["tests/**"]}))
        out = guidelines.render_checklist(
            parse(text, registered=REGISTERED, root=tree), "markdown", source="r.yaml"
        )
        assert "any of `tests/**`" in out

    def test_a_judgement_with_no_conditions_offers_no_na(self, tree):
        text = mutated(lambda r: check_of(r, JUDGED).pop("not_applicable_when"))
        out = guidelines.render_checklist(
            parse(text, registered=REGISTERED, root=tree), "markdown", source="r.yaml"
        )
        assert "N/A when" not in out


# -- the rule reads the registry ----------------------------------------------------------


CONFIGURED = {"guidelines": {"ruleset": "docs/guidelines/ruleset.yaml"}}


def ruleset_valid(make_manifest, cwd, *, session=None):
    # The ruleset's boundary rules need an adapter offering imports.
    project = {"name": "demo", "ecosystems": ["python"]}
    manifest = make_manifest({"version": 1, "project": project, **CONFIGURED})
    (result,) = evaluate(
        ["guidelines.ruleset_valid"], RuleContext(manifest=manifest, cwd=cwd, session=session)
    )
    return result


class TestRulesetValidReadsTheRegistry:
    def test_a_ruleset_and_its_registry_pass(self, make_manifest, tree):
        write(tree, "docs/guidelines/ruleset.yaml", RULESET)
        result = ruleset_valid(make_manifest, tree)
        assert result.verdict.ok and not result.verdict.skipped
        assert "6 guideline(s)" in result.verdict.message

    def test_a_missing_registry_fails(self, make_manifest, tree):
        """DN-A9: a missing registry fails, as a missing ruleset does."""
        write(tree, "docs/guidelines/ruleset.yaml", RULESET)
        (tree / REGISTRY_REL).unlink()
        result = ruleset_valid(make_manifest, tree)
        assert result.blocks
        assert f"registry {REGISTRY_REL} does not exist" in result.verdict.detail

    def test_the_session_worktree_registry_is_the_one_judged(self, make_manifest, tree):
        """The registry is read from the ruleset's tree, so a registry broken
        in the session cannot pass on the main checkout's copy."""
        write(tree, "docs/guidelines/ruleset.yaml", RULESET)
        worktree = tree / "wt"
        write(worktree, "docs/guidelines/ruleset.yaml", RULESET)
        write(worktree, REGISTRY_REL, "version: 9\nentries: []\n")
        result = ruleset_valid(make_manifest, tree, session={"worktree": str(worktree)})
        assert result.blocks
        assert "version 9" in result.verdict.detail


# -- a pattern rule needs a mask some configured adapter offers (A3.1) -------------


PATTERN_RULESET = """\
version: 1
sections:
  - id: quality
    title: Code quality
rules:
  - id: quality.no-inline-todo
    section: quality
    title: No inline backlog markers
    statement: A TODO in a comment is filed as an idea instead.
    severity: warn
    phases: [landing]
    applies_to: ["src/**/*.py"]
    check: {kind: pattern, engine: regex, in: comments, match: 'TODO'}
"""


def pattern_ruleset(scope: str) -> str:
    return PATTERN_RULESET.replace("in: comments", f"in: {scope}")


def ruleset_valid_under(make_manifest, cwd, ecosystems):
    project = {"name": "demo"}
    if ecosystems is not None:
        project["ecosystems"] = ecosystems
    manifest = make_manifest({"version": 1, "project": project, **CONFIGURED})
    (result,) = evaluate(
        ["guidelines.ruleset_valid"], RuleContext(manifest=manifest, cwd=cwd)
    )
    return result


class TestAPatternScopeNeedsAMask:
    """ADR-012 D4: a rule needing a mask fails as unanswerable rather than
    matching raw text. ``ruleset_valid`` refuses a pattern rule reading
    ``code``, ``comments`` or ``strings`` unless a configured adapter offers
    ``mask``. ``any`` needs no mask."""

    @pytest.mark.parametrize("scope", ["code", "comments", "strings"])
    @pytest.mark.parametrize("ecosystems", [None, ["generic"], ["node"]])
    def test_no_adapter_offering_a_mask_refuses_it(
        self, make_manifest, tmp_path, scope, ecosystems
    ):
        write(tmp_path, "docs/guidelines/ruleset.yaml", pattern_ruleset(scope))
        result = ruleset_valid_under(make_manifest, tmp_path, ecosystems)
        assert result.blocks
        assert (
            f"rule 'quality.no-inline-todo' check: in {scope!r} needs a lexical mask"
            in result.verdict.detail
        )

    @pytest.mark.parametrize(
        "ecosystems", [["python"], ["swift_xcode"], ["node", "python"]]
    )
    def test_a_configured_adapter_offering_a_mask_passes(
        self, make_manifest, tmp_path, ecosystems
    ):
        write(tmp_path, "docs/guidelines/ruleset.yaml", pattern_ruleset("code"))
        result = ruleset_valid_under(make_manifest, tmp_path, ecosystems)
        assert result.verdict.ok, result.verdict.detail

    def test_any_needs_no_mask(self, make_manifest, tmp_path):
        write(tmp_path, "docs/guidelines/ruleset.yaml", pattern_ruleset("any"))
        assert ruleset_valid_under(make_manifest, tmp_path, ["node"]).verdict.ok

    def test_parse_without_engines_checks_the_schema_only(self):
        """``engines=None`` is the schema question alone; the gate always
        passes what the configured adapters offer."""
        assert parse(PATTERN_RULESET, registered=REGISTERED).rules
        with pytest.raises(RulesetError, match="needs a lexical mask"):
            parse(PATTERN_RULESET, registered=REGISTERED, engines=frozenset())


# -- a boundary rule needs an adapter offering imports (A3.2, DN-A14) -------------


BOUNDARY_RULESET = """\
version: 1
sections:
  - id: layers
    title: Layers
rules:
  - id: layers.core-never-imports-oracle
    section: layers
    title: Nothing in core imports the oracle
    statement: No module under core reaches into oracle.
    severity: blocker
    phases: [review, landing]
    applies_to: ["src/invariant/core/**/*.py"]
    check: {kind: boundary, engine: imports, forbid: ["src/invariant/oracle/**"]}
"""


class TestABoundaryNeedsImports:
    """DN-A14: a boundary rule is accepted only when a configured adapter's
    ``guideline_engines`` offers ``imports``, and the refusal names why. Only
    the python adapter offers it: Swift boundaries need symbols (ruling 3),
    and node and generic gain nothing (DN-9)."""

    @pytest.mark.parametrize(
        "ecosystems", [None, ["generic"], ["node"], ["swift_xcode"], ["node", "swift_xcode"]]
    )
    def test_no_adapter_offering_imports_refuses_it(self, make_manifest, tmp_path, ecosystems):
        write(tmp_path, "docs/guidelines/ruleset.yaml", BOUNDARY_RULESET)
        result = ruleset_valid_under(make_manifest, tmp_path, ecosystems)
        assert result.blocks
        detail = result.verdict.detail
        assert (
            "rule 'layers.core-never-imports-oracle' check: engine 'imports' needs "
            "an adapter that reads the import graph, and no configured ecosystem's "
            "adapter offers one" in detail
        )

    @pytest.mark.parametrize("ecosystems", [["python"], ["swift_xcode", "python"]])
    def test_a_configured_adapter_offering_imports_passes(
        self, make_manifest, tmp_path, ecosystems
    ):
        write(tmp_path, "docs/guidelines/ruleset.yaml", BOUNDARY_RULESET)
        result = ruleset_valid_under(make_manifest, tmp_path, ecosystems)
        assert result.verdict.ok, result.verdict.detail

    def test_an_only_list_needs_imports_too(self):
        text = BOUNDARY_RULESET.replace(
            'forbid: ["src/invariant/oracle/**"]', 'only: ["src/invariant/core/**"]'
        )
        with pytest.raises(RulesetError, match="needs an adapter that reads the import graph"):
            parse(text, registered=REGISTERED, engines=frozenset({"mask"}))

    def test_parse_without_engines_checks_the_schema_only(self):
        assert parse(BOUNDARY_RULESET, registered=REGISTERED).rules
        assert parse(
            BOUNDARY_RULESET, registered=REGISTERED, engines=frozenset({"imports"})
        ).rules

    def test_a_deferred_engine_is_refused_as_deferred_not_as_unoffered(self):
        """``symbols`` is still refused by the schema; the offered check names
        only ``imports``."""
        text = BOUNDARY_RULESET.replace("engine: imports", "engine: symbols")
        with pytest.raises(RulesetError) as caught:
            parse(text, registered=REGISTERED, engines=frozenset())
        assert "deferred" in str(caught.value.problems)
        assert "reads the import graph" not in str(caught.value.problems)


# -- a cochange when.in needs a mask some configured adapter offers (A3.4) ---------


COCHANGE_RULESET = """\
version: 1
sections:
  - id: di
    title: Dependency injection
rules:
  - id: di.container-member-updates-mocks
    section: di
    title: A new container member updates both mocks
    statement: A member added to the container is added to both mocks.
    severity: blocker
    phases: [landing]
    applies_to: ["app/container.py"]
    check:
      kind: cochange
      form: pair
      when: {change: any, in: code, match: '^\\s*def\\s'}
      then: {all_of: [app/mock_a.py, app/mock_b.py]}
"""


def cochange_ruleset(scope: str) -> str:
    return COCHANGE_RULESET.replace("in: code", f"in: {scope}")


class TestACochangeScopeNeedsAMask:
    """A3 increment 1 deferred this to A3.4: a cochange ``when.in`` of code,
    comments or strings is read through the adapter's lexical mask, so the
    ruleset is refused when no configured adapter offers ``mask``. ``any``
    needs none. A4.2 refuses a ``no_added_match.in`` the same way."""

    @pytest.mark.parametrize("scope", ["code", "comments", "strings"])
    @pytest.mark.parametrize("ecosystems", [None, ["generic"], ["node"]])
    def test_no_adapter_offering_a_mask_refuses_it(
        self, make_manifest, tmp_path, scope, ecosystems
    ):
        write(tmp_path, "docs/guidelines/ruleset.yaml", cochange_ruleset(scope))
        result = ruleset_valid_under(make_manifest, tmp_path, ecosystems)
        assert result.blocks
        assert (
            f"rule 'di.container-member-updates-mocks' check: when.in {scope!r} "
            f"needs a lexical mask" in result.verdict.detail
        )

    @pytest.mark.parametrize("ecosystems", [["python"], ["swift_xcode"], ["node", "python"]])
    def test_a_configured_adapter_offering_a_mask_passes(
        self, make_manifest, tmp_path, ecosystems
    ):
        write(tmp_path, "docs/guidelines/ruleset.yaml", cochange_ruleset("code"))
        result = ruleset_valid_under(make_manifest, tmp_path, ecosystems)
        assert result.verdict.ok, result.verdict.detail

    def test_any_needs_no_mask(self, make_manifest, tmp_path):
        write(tmp_path, "docs/guidelines/ruleset.yaml", cochange_ruleset("any"))
        assert ruleset_valid_under(make_manifest, tmp_path, ["node"]).verdict.ok

    def test_a_trigger_with_no_match_needs_no_mask(self):
        text = COCHANGE_RULESET.replace(
            "when: {change: any, in: code, match: '^\\s*def\\s'}", "when: {change: any}"
        )
        assert parse(text, registered=REGISTERED, engines=frozenset()).rules

    def test_parse_without_engines_checks_the_schema_only(self):
        assert parse(COCHANGE_RULESET, registered=REGISTERED).rules
        with pytest.raises(RulesetError, match="when.in 'code' needs a lexical mask"):
            parse(COCHANGE_RULESET, registered=REGISTERED, engines=frozenset())

    def test_a_judgement_no_added_match_needs_a_mask_too(self):
        """A4.2 evaluates ``no_added_match`` through the mask its ``in`` names,
        so it is refused here as a ``when.in`` is."""
        text = """\
version: 1
sections:
  - id: data
    title: Data
rules:
  - id: data.one-library
    section: data
    title: One library at a time
    statement: Only one library is open at a time.
    severity: blocker
    phases: [review]
    applies_to: ["app/**"]
    check:
      kind: judgement
      evidence: citation
      scope: diff
      not_applicable_when:
        - id: no-handle-code
          mechanical: {no_added_match: {in: code, match: 'open'}}
"""
        assert parse(text, registered=REGISTERED).rules
        assert parse(text, registered=REGISTERED, engines=frozenset({"mask"})).rules
        with pytest.raises(RulesetError, match="no_added_match.in 'code' needs a lexical mask"):
            parse(text, registered=REGISTERED, engines=frozenset())
        anything = text.replace("in: code", "in: any")
        assert parse(anything, registered=REGISTERED, engines=frozenset()).rules


class TestACochangeExceptionHasNoFingerprint:
    """DN-4: a cochange finding describes the change, not the tree, so it
    carries no fingerprint. An exception naming one could never apply, and is
    refused rather than kept as a silent no-op. A path exception stands."""

    def test_a_fingerprint_exception_on_a_cochange_rule_is_refused(self):
        text = COCHANGE_RULESET + (
            "    exceptions:\n"
            "      - {path: app/container.py, reason: generated, fingerprint: abcdefabcdef}\n"
        )
        with pytest.raises(RulesetError, match="a cochange finding has no fingerprint"):
            parse(text, registered=REGISTERED)

    def test_a_path_exception_on_a_cochange_rule_stands(self):
        text = COCHANGE_RULESET + (
            "    exceptions:\n"
            "      - {path: app/container.py, reason: generated}\n"
        )
        (rule,) = parse(text, registered=REGISTERED).rules
        assert rule.exceptions[0].path == "app/container.py"


# -- a guideline id is never a registered rule id (A3.7, DN-15) --------------------


def colliding(rule_id: str) -> str:
    return PATTERN_RULESET.replace("quality.no-inline-todo", rule_id)


class TestAGuidelineIdIsNeverARuleId:
    """DN-15: `invariant override <id>` names a rule or a guideline, never
    both, so a guideline id equal to a registered rule id is refused."""

    def test_a_guideline_named_after_a_registered_rule_is_refused(self):
        with pytest.raises(RulesetError) as caught:
            parse(colliding("privacy.clean"), registered=REGISTERED)
        (problem,) = caught.value.problems
        assert "rule 'privacy.clean'" in problem
        assert "registered rule" in problem
        assert "invariant override" in problem

    def test_a_guideline_id_no_rule_holds_loads(self):
        (rule,) = parse(colliding("quality.no-privacy-leaks"), registered=REGISTERED).rules
        assert rule.id == "quality.no-privacy-leaks"

    def test_ruleset_valid_refuses_it(self, make_manifest, tree):
        write(tree, "docs/guidelines/ruleset.yaml", colliding("test.all_pass"))
        result = ruleset_valid(make_manifest, tree)
        assert result.blocks
        assert "rule 'test.all_pass'" in result.verdict.detail
