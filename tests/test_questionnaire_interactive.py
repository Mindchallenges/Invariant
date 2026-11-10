"""ADR-017 increment 2: interactive `init` asks the level and the core sections.

D3: the level once, then the core sections in catalogue order, each shown with
its default and provenance, the docs step (ADR-016 D4) last, its order and
meaning unchanged; every write after the last answer. D4: an answer its level
may not hold goes to the nearest level that may, saying so. D5: only an answer
that differs from what the levels below resolve is written; the inherited value
at a level that sets the key removes it (ruling 12); init's own comment counts
as none (ruling 9); `core/home.write_user_defaults` keeps the comment rule.
D6: each answer is validated as the configuration it produces, through
`Manifest.validate`, refused with the manifest's reason and asked again; a
command's shape is checked at entry and never at load, where `doctor` warns
instead (ruling 13). `--profile` has no parser default.

The interactive path is driven through the two prompter seams, so no test fakes
a terminal. INVARIANT_HOME is the suite's temporary home (conftest).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.cli import main as cli_main
from invariant.cli.main import build_parser, main
from invariant.core import home, questionnaire, stores
from invariant.core.errors import ManifestError
from invariant.core.home import load_user_config, user_config_path
from invariant.core.manifest import MANIFEST_RELPATH, Manifest

ORIGIN = "https://host.example/team/widgets.git"
IDENTITY = "host.example/team/widgets"

#: The comment init writes at the end of its manifest (ruling 9).
INIT_COMMENT = (
    "# Everything else has a default. 'invariant config show --provenance'\n"
    "# prints the resolved configuration and where each value came from.\n"
)

#: What an interactive init that accepts every default writes: the project
#: block and init's comment, no `enforcement` block (ruling 4).
#: The repository has no docs, so init writes the docs starter and the
#: settings that enforce the project on it (ADR-018 D10, D11).
STARTER_DOCS = {"format": "sphinx", "needs": {"state": "enforced", "srcdir": "docs"}}

ACCEPTED_MANIFEST = (
    "version: 1\n"
    "\n"
    "project:\n"
    "  name: toy\n"
    "  ecosystems: [python]\n"
    "\n"
    "docs:\n"
    "  format: sphinx\n"
    "  needs:\n"
    "    state: enforced\n"
    "    srcdir: docs\n"
    "\n"
    "items:\n"
    "  backend: sphinx_needs\n"
    "  path: docs/backlog.rst\n"
    "\n"
    "guidelines:\n"
    "  ruleset: docs/guidelines/ruleset.yaml\n"
    "\n" + INIT_COMMENT
)


def git(root: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", *argv], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def run(capsys, *argv) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def make_repo(path: Path, origin: str | None = None) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.invalid")
    git(path, "config", "user.name", "T")
    (path / "pyproject.toml").write_text("[project]\nname = 'toy'\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "init")
    if origin:
        git(path, "remote", "add", "origin", origin)
    return path


def write_user(text: str) -> Path:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def toy(tmp_path, monkeypatch):
    """A python repository with no manifest and no docs."""
    root = make_repo(tmp_path / "toy")
    monkeypatch.chdir(root)
    return root


class Keyed:
    """A prompter answering by question: the first scripted answer whose key
    the question contains, else the default. Records every question."""

    def __init__(self, **answers: list[str]) -> None:
        # A key matches as written or with underscores read as spaces, so
        # `coverage_floor` finds "the coverage floor" and `commands.test_one`
        # finds itself.
        self.answers = {key: list(values) for key, values in answers.items()}
        self.asked: list[str] = []
        self.said: list[str] = []

    def _answer(self, question: str) -> str | None:
        for key, values in self.answers.items():
            if values and (key in question or key.replace("_", " ") in question):
                return values.pop(0)
        return None

    def say(self, text: str) -> None:
        self.said.append(text)

    def choose(self, question, options, default):
        keys = [key for key, _ in options]
        assert default in keys, (question, default, keys)
        self.asked.append(question)
        answer = self._answer(question)
        if answer is None:
            return default
        assert answer in keys, (question, answer, keys)
        return answer

    def ask(self, question, default=""):
        self.asked.append(question)
        answer = self._answer(question)
        return answer or default


@pytest.fixture
def interactive(monkeypatch):
    """Answer both of init's prompter seams from one keyed script."""

    def install(**answers: list[str]) -> Keyed:
        keyed = Keyed(**answers)
        monkeypatch.setattr(cli_main, "_init_prompter", lambda args: keyed)
        monkeypatch.setattr(cli_main, "_questionnaire_prompter", lambda args: keyed)
        return keyed

    return install


def init(capsys, *extra: str) -> tuple[int, str]:
    return run(capsys, "init", "--name", "toy", *extra)


def said(keyed: Keyed) -> str:
    return "\n".join(keyed.said)


# -- D4: the nearest level that may hold an answer -----------------------------


class TestRoute:
    def test_a_level_that_may_hold_the_key_keeps_it(self):
        assert questionnaire.route(
            "enforcement.profile", "user", store_project=False
        ) == ("user", None)

    def test_project_at_the_user_level_goes_to_repo_saying_so(self):
        level, note = questionnaire.route("project.ecosystems", "user", store_project=False)
        assert level == "repo"
        assert note == "project.ecosystems goes to repo: the user level may not hold project"

    def test_records_at_the_user_level_in_a_store_project_goes_to_the_store(self):
        level, note = questionnaire.route("records.docs", "user", store_project=True)
        assert level == "store"
        assert "the user level may not hold records" in note

    def test_docs_adopt_source_at_the_user_level_is_named(self):
        level, note = questionnaire.route("docs.adopt.source", "user", store_project=False)
        assert level == "repo"
        assert "docs.adopt.source" in note

    def test_a_policy_key_at_the_pointer_goes_to_this_repositorys_block(self):
        level, note = questionnaire.route("methodology.pipeline", "repo", store_project=True)
        assert level == "store-repo"
        assert note == (
            "methodology.pipeline goes to store-repo: the pointer holds only "
            "version, store and records"
        )


# -- D6 and ruling 13: the shape of a command ---------------------------------


def manifest_with(tmp_path: Path, raw: dict) -> Manifest:
    return Manifest.from_raw(raw, root=tmp_path, path=tmp_path / "m.yaml", env={})


class TestCommandShape:
    @pytest.fixture
    def manifest(self, tmp_path):
        return manifest_with(tmp_path, {"project": {"ecosystems": ["python"]}})

    def test_a_good_command_has_no_problem(self, manifest):
        assert questionnaire.command_problem(manifest, "test_one", "pytest -q {selector}") is None

    def test_an_unset_command_has_no_problem(self, manifest):
        assert questionnaire.command_problem(manifest, "build", None) is None

    def test_test_one_without_selector_is_named(self, manifest):
        problem = questionnaire.command_problem(manifest, "test_one", "pytest -q")
        assert problem == "lacks {selector}, which the engine substitutes for test_one"

    def test_coverage_one_needs_it_too(self, manifest):
        assert "{selector}" in questionnaire.command_problem(manifest, "coverage_one", "pytest --cov")

    @pytest.mark.parametrize("value", ["", "   "])
    def test_an_empty_command(self, manifest, value):
        assert questionnaire.command_problem(manifest, "test", value) == "is empty"

    def test_more_than_one_line(self, manifest):
        assert questionnaire.command_problem(manifest, "test", "pytest\nrm -rf x") == (
            "is more than one line"
        )

    def test_not_text(self, manifest):
        assert questionnaire.command_problem(manifest, "test", 5) == "is not text"

    def test_an_unbalanced_brace(self, manifest):
        assert "brace" in questionnaire.command_problem(manifest, "test", "pytest {selector")

    def test_a_placeholder_nothing_substitutes(self, manifest):
        problem = questionnaire.command_problem(manifest, "test", "pytest {selector}")
        assert problem == (
            "names {selector}, which nothing substitutes for test: not the "
            "engine, vars or discover"
        )

    def test_vars_discover_and_the_result_bundle_are_substituted(self, tmp_path):
        manifest = manifest_with(
            tmp_path,
            {"vars": {"scheme": "App"}, "discover": {"device": "echo d"}},
        )
        assert questionnaire.command_problem(
            manifest, "test", "run {scheme} {device} {result_bundle}"
        ) is None

    def test_command_problems_names_each_key_and_the_manifest_loads(self, tmp_path):
        manifest = manifest_with(
            tmp_path,
            {
                "project": {"ecosystems": ["python", "node"]},
                "commands": {"test_one": "pytest -q", "node": {"test": "a\nb"}},
            },
        )
        assert questionnaire.command_problems(manifest) == [
            ("commands.test_one", "lacks {selector}, which the engine substitutes for test_one"),
            ("commands.node.test", "is more than one line"),
        ]

    def test_a_shipped_adapters_commands_have_none(self, tmp_path):
        for eco in ("python", "node", "generic"):
            manifest = manifest_with(tmp_path, {"project": {"ecosystems": [eco]}})
            assert questionnaire.command_problems(manifest) == []


# -- D6: the coverage floor is judged at load ---------------------------------


class TestCoverageFloorAtLoad:
    @pytest.mark.parametrize("value", [0, 85, 100, 99.5])
    def test_a_percent_loads(self, tmp_path, value):
        manifest = manifest_with(tmp_path, {"methodology": {"gates": {"coverage_min": value}}})
        assert manifest.get("methodology.gates.coverage_min") == value

    @pytest.mark.parametrize("value", [None, "85"])
    def test_what_the_gates_already_read_still_loads(self, tmp_path, value):
        """The gates read the floor as ``float(value or 0)``: null is no floor,
        and a quoted number is a number. Neither is refused now."""
        manifest_with(tmp_path, {"methodology": {"gates": {"coverage_min": value}}})

    @pytest.mark.parametrize("value", [101, -1, "ninety", "150", True])
    def test_anything_else_is_refused_naming_the_key(self, tmp_path, value):
        with pytest.raises(ManifestError, match="methodology.gates.coverage_min"):
            manifest_with(tmp_path, {"methodology": {"gates": {"coverage_min": value}}})


# -- D6: an answer is parsed and checked as the configuration it produces ------


def fresh(tmp_path: Path) -> Manifest:
    return Manifest.from_raw(
        {"version": 1, "project": {"name": "toy", "ecosystems": ["python"]}},
        root=tmp_path,
        path=tmp_path / MANIFEST_RELPATH,
        env={},
    )


def the(question_id: str, manifest: Manifest, target: str = "repo") -> questionnaire.Question:
    catalogue = questionnaire.load_catalogue()
    return next(
        q for q in questionnaire.resolve(catalogue, manifest, target) if q.id == question_id
    )


class TestParse:
    @pytest.mark.parametrize(
        ("qid", "text", "value"),
        [
            ("project.ecosystems", "python, node", ["python", "node"]),
            ("project.ecosystems", " python ,, ", ["python"]),
            ("methodology.gates.coverage_min", "85", 85),
            ("methodology.gates.coverage_min", "72.5", 72.5),
            ("session.plan_stale_days", "7", 7),
            ("session.push", "yes", True),
            ("session.push", "false", False),
            ("session.worktree_dir", "none", None),
            ("session.worktree_dir", "", None),
            ("commands:test", "", None),
            ("session.worktree_dir", "../wt", "../wt"),
            ("commands:test", "pytest -x", "pytest -x"),
        ],
    )
    def test_each_kind(self, tmp_path, qid, text, value):
        assert questionnaire.parse_answer(the(qid, fresh(tmp_path)), text) == value

    @pytest.mark.parametrize(
        ("qid", "text", "reason"),
        [
            ("methodology.gates.coverage_min", "lots", "'lots' is not a number"),
            ("session.plan_stale_days", "2.5", "'2.5' is not a whole number"),
            ("session.push", "maybe", "'maybe' is not yes or no"),
        ],
    )
    def test_a_shape_it_cannot_read_is_refused(self, tmp_path, qid, text, reason):
        with pytest.raises(questionnaire.Refused, match=reason):
            questionnaire.parse_answer(the(qid, fresh(tmp_path)), text)


class TestCheck:
    def draft(self, tmp_path) -> questionnaire.Draft:
        return questionnaire.Draft(fresh(tmp_path))

    def test_a_good_answer_passes(self, tmp_path):
        q = the("methodology.gates.coverage_min", fresh(tmp_path))
        assert questionnaire.check(q, 80, self.draft(tmp_path), "repo") is None

    def test_a_floor_of_101_is_refused_with_the_manifests_reason(self, tmp_path):
        q = the("methodology.gates.coverage_min", fresh(tmp_path))
        reason = questionnaire.check(q, 101, self.draft(tmp_path), "repo")
        with pytest.raises(ManifestError) as raised:
            manifest_with(tmp_path, {"methodology": {"gates": {"coverage_min": 101}}})
        assert reason == str(raised.value)

    def test_a_choice_outside_its_choices_names_them(self, tmp_path):
        q = the("enforcement.profile", fresh(tmp_path))
        reason = questionnaire.check(q, "lax", self.draft(tmp_path), "repo")
        assert reason.startswith("'lax' is not one of ")
        assert "strict" in reason

    def test_a_list_names_the_entry_outside_its_choices(self, tmp_path):
        q = the("project.ecosystems", fresh(tmp_path))
        assert questionnaire.check(q, ["python", "cobol"], self.draft(tmp_path), "repo") == (
            "'cobol' is not one of " + ", ".join(q.choices)
        )

    @pytest.mark.parametrize("value", ["../out", "/abs"])
    def test_a_path_that_leaves_the_project(self, tmp_path, value):
        q = the("session.worktree_dir", fresh(tmp_path))
        reason = questionnaire.check(q, value, self.draft(tmp_path), "repo")
        assert reason == f"{value!r} is not a path inside the project: no '..', not absolute"

    def test_a_command_of_the_wrong_shape_is_refused_at_entry(self, tmp_path):
        q = the("commands:test_one", fresh(tmp_path))
        assert questionnaire.check(q, "pytest -q", self.draft(tmp_path), "repo") == (
            "lacks {selector}, which the engine substitutes for test_one"
        )

    def test_the_checks_see_earlier_answers(self, tmp_path):
        draft = self.draft(tmp_path).placed("project.ecosystems", ["python", "node"], "repo")
        q = the("commands:node.test_one", draft.manifest())
        assert questionnaire.check(q, "npm test -- {selector}", draft, "repo") is None


# -- D5: the write plan ----------------------------------------------------------


def answer(key, value, level="repo", default=None):
    return questionnaire.Answer(key=key, value=value, level=level, default=default)


class TestPlan:
    def test_an_accepted_default_writes_nothing(self, tmp_path):
        plan = questionnaire.plan(
            fresh(tmp_path), [answer("enforcement.profile", "standard", default="standard")]
        )
        assert plan.writes == ()
        assert plan.notes == ()

    def test_a_changed_answer_is_written_at_its_level(self, tmp_path):
        plan = questionnaire.plan(
            fresh(tmp_path),
            [
                answer("enforcement.profile", "strict", "user", "standard"),
                answer("methodology.gates.coverage_min", 80, "repo", 90),
            ],
        )
        assert plan.at("user").sets == {"enforcement": {"profile": "strict"}}
        assert plan.at("repo").sets == {"methodology": {"gates": {"coverage_min": 80}}}
        assert plan.at("user").removes == ()

    def test_the_value_below_is_what_decides(self, tmp_path):
        """A user default below the repo: answering the library default at the
        repo differs from what the levels below resolve, so it is written."""
        user = load_user_config()
        base = Manifest.from_raw(
            {"version": 1, "project": {"name": "toy", "ecosystems": ["python"]}},
            root=tmp_path,
            path=tmp_path / MANIFEST_RELPATH,
            env={},
            below=[(user.label, {"methodology": {"gates": {"coverage_min": 70}}})],
        )
        plan = questionnaire.plan(
            base, [answer("methodology.gates.coverage_min", 90, "repo", 70)]
        )
        assert plan.at("repo").sets == {"methodology": {"gates": {"coverage_min": 90}}}

    def test_the_inherited_value_at_a_level_that_sets_it_removes_it(self, tmp_path):
        base = Manifest.from_raw(
            {
                "version": 1,
                "project": {"name": "toy", "ecosystems": ["python"]},
                "methodology": {"gates": {"coverage_min": 70}},
            },
            root=tmp_path,
            path=tmp_path / MANIFEST_RELPATH,
            env={},
        )
        plan = questionnaire.plan(
            base, [answer("methodology.gates.coverage_min", 90, "repo", 70)]
        )
        assert plan.at("repo").sets == {}
        assert plan.at("repo").removes == ("methodology.gates.coverage_min",)
        assert plan.notes == (
            (
                "methodology.gates.coverage_min: 90 is what the levels below repo "
                "resolve, so it is removed from repo and flows from default again"
            ),
        )

    def test_the_inherited_value_where_the_level_does_not_set_it_writes_nothing(
        self, tmp_path
    ):
        plan = questionnaire.plan(
            fresh(tmp_path), [answer("methodology.gates.coverage_min", 90, "user", 80)]
        )
        assert plan.writes == ()


# -- the writers: the comment rule, init's comment, removal ---------------------


class TestWriteUserDefaults:
    def test_a_new_user_file_holds_the_defaults(self):
        result = home.write_user_defaults({"enforcement": {"profile": "strict"}})
        assert result.written
        assert yaml.safe_load(user_config_path().read_text(encoding="utf-8")) == {
            "version": 1,
            "defaults": {"enforcement": {"profile": "strict"}},
        }
        assert load_user_config().defaults == {"enforcement": {"profile": "strict"}}

    def test_merged_into_an_existing_file_keeping_repos(self):
        write_user(
            "version: 1\nrepos:\n  a/b: {store: /s}\n"
            "defaults:\n  methodology: {gates: {coverage_min: 70}}\n"
        )
        home.write_user_defaults({"methodology": {"pipeline": "chore"}})
        user = load_user_config()
        assert user.repos == {"a/b": {"store": "/s"}}
        assert user.defaults == {
            "methodology": {"gates": {"coverage_min": 70}, "pipeline": "chore"}
        }

    def test_a_removed_key_takes_its_emptied_parents_with_it(self):
        write_user(
            "version: 1\ndefaults:\n  methodology: {gates: {coverage_min: 70}}\n"
            "  enforcement: {profile: strict}\n"
        )
        home.write_user_defaults({}, remove=["methodology.gates.coverage_min"])
        assert load_user_config().defaults == {"enforcement": {"profile": "strict"}}

    def test_a_commented_file_is_not_rewritten_and_the_block_is_given(self):
        path = write_user("# mine\nversion: 1\n")
        before = path.read_bytes()
        result = home.write_user_defaults(
            {"enforcement": {"profile": "strict"}}, remove=["session.push"]
        )
        assert not result.written
        assert path.read_bytes() == before
        assert yaml.safe_load(result.by_hand) == {
            "defaults": {"enforcement": {"profile": "strict"}}
        }
        assert "comment" in result.reason
        assert "remove defaults.session.push" in result.reason

    def test_a_malformed_user_file_is_refused_before_it_is_written(self):
        write_user("version: 1\nnonsense: 1\n")
        with pytest.raises(ManifestError):
            home.write_user_defaults({"enforcement": {"profile": "strict"}})


class TestWriteRepoSettings:
    def test_inits_own_comment_counts_as_none(self, tmp_path):
        path = tmp_path / "invariant.yaml"
        path.write_text(ACCEPTED_MANIFEST.replace("python", "generic"), encoding="utf-8")
        result = stores.write_repo_settings(path, {"methodology": {"pipeline": "chore"}})
        assert result.written
        text = path.read_text(encoding="utf-8")
        assert text.endswith("\n" + INIT_COMMENT)
        assert yaml.safe_load(text)["methodology"] == {"pipeline": "chore"}

    def test_inits_comment_edited_still_holds_the_file(self, tmp_path):
        path = tmp_path / "invariant.yaml"
        body = ACCEPTED_MANIFEST.replace("Everything else", "Most things")
        path.write_text(body, encoding="utf-8")
        result = stores.write_repo_settings(path, {"project": {"name": "x"}})
        assert not result.written
        assert path.read_text(encoding="utf-8") == body

    def test_an_operators_comment_beside_inits_still_holds_the_file(self, tmp_path):
        path = tmp_path / "invariant.yaml"
        body = "# mine\n" + ACCEPTED_MANIFEST
        path.write_text(body, encoding="utf-8")
        assert not stores.write_repo_settings(path, {"project": {"name": "x"}}).written

    def test_a_removal_on_a_file_without_comments(self, tmp_path):
        path = tmp_path / "invariant.yaml"
        path.write_text(
            "version: 1\nmethodology:\n  pipeline: chore\n  gates: {coverage_min: 70}\n",
            encoding="utf-8",
        )
        assert stores.write_repo_settings(
            path, {}, remove=["methodology.gates.coverage_min"]
        ).written
        assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
            "version": 1,
            "methodology": {"pipeline": "chore"},
        }

    def test_a_removal_from_a_commented_file_is_held_and_named(self, tmp_path):
        path = tmp_path / "invariant.yaml"
        path.write_text("# mine\nversion: 1\nsession: {push: true}\n", encoding="utf-8")
        result = stores.write_repo_settings(path, {}, remove=["session.push"])
        assert not result.written
        assert "remove session.push" in result.reason


# -- --profile has no parser default --------------------------------------------


class TestProfileFlag:
    def test_no_parser_default(self):
        assert build_parser().parse_args(["init"]).profile is None

    def test_given_it_is_parsed(self):
        assert build_parser().parse_args(["init", "--profile", "strict"]).profile == "strict"


# -- D3 to D5 through a scripted prompter ---------------------------------------


class TestInteractiveInit:
    def test_accepting_everything_writes_the_project_block_and_no_enforcement(
        self, toy, capsys, interactive
    ):
        interactive()
        code, text = init(capsys)
        assert code == 0, text
        assert (toy / MANIFEST_RELPATH).read_text(encoding="utf-8") == ACCEPTED_MANIFEST
        assert not user_config_path().exists()
        manifest = Manifest.locate(toy)
        assert manifest.get("enforcement.profile") == "standard"
        assert manifest.source_of("enforcement.profile") == "default"
        assert "configuration (core questions):" not in text

    def test_the_order_is_level_core_sections_then_the_docs_step(
        self, toy, capsys, interactive
    ):
        keyed = interactive()
        assert init(capsys)[0] == 0
        order = ["level", "ecosystems", "Commands", "profile", "lane", "coverage floor", "gaps"]
        positions = [
            next(i for i, q in enumerate(keyed.asked) if word in q) for word in order
        ]
        assert positions == sorted(positions), keyed.asked

    def test_each_question_shows_its_default_and_provenance(self, toy, capsys, interactive):
        keyed = interactive()
        assert init(capsys)[0] == 0
        asked = "\n".join(keyed.asked)
        assert "Which enforcement profile? (now standard, from default)" in asked
        assert "(now [python], from detected)" in asked
        assert "(now 90, from default)" in asked
        commands = said(keyed)
        assert "commands.test_one" in commands and "adapter:python" in commands

    def test_profile_strict_at_user_lands_in_defaults_only(self, toy, capsys, interactive):
        interactive(level=["user"], profile=["strict"])
        code, text = init(capsys)
        assert code == 0, text
        assert load_user_config().defaults == {"enforcement": {"profile": "strict"}}
        assert (toy / MANIFEST_RELPATH).read_text(encoding="utf-8") == ACCEPTED_MANIFEST
        code, shown = run(capsys, "config", "show", "--provenance")
        assert code == 0, shown
        row = next(
            line for line in shown.splitlines() if line.strip().startswith("enforcement.profile")
        )
        assert "strict" in row
        assert "user:" in row

    def test_a_commented_user_file_is_not_rewritten_and_the_block_is_printed(
        self, toy, capsys, interactive
    ):
        path = write_user("# my defaults\nversion: 1\n")
        before = path.read_bytes()
        interactive(level=["user"], profile=["strict"])
        code, text = init(capsys)
        assert code == 0, text
        assert path.read_bytes() == before
        assert "comment" in text
        block = text[text.index("defaults:\n") :].split("\n\n")[0]
        assert yaml.safe_load(block) == {"defaults": {"enforcement": {"profile": "strict"}}}
        assert Manifest.locate(toy).get("enforcement.profile") == "standard"

    def test_ecosystems_aimed_at_user_land_in_repo_saying_so(self, toy, capsys, interactive):
        keyed = interactive(level=["user"], ecosystems=["python, node"])
        code, text = init(capsys)
        assert code == 0, text
        assert "project.ecosystems goes to repo: the user level may not hold project" in said(
            keyed
        )
        assert Manifest.locate(toy).get("project.ecosystems") == ["python", "node"]
        assert not user_config_path().exists()

    def test_a_floor_of_101_is_refused_with_the_manifests_reason_and_asked_again(
        self, toy, capsys, interactive
    ):
        keyed = interactive(coverage_floor=["101", "80"])
        code, text = init(capsys)
        assert code == 0, text
        assert sum("coverage floor" in q for q in keyed.asked) == 2
        assert any(
            "methodology.gates.coverage_min must be a number from 0 to 100" in s
            for s in keyed.said
        )
        manifest = Manifest.locate(toy)
        assert manifest.get("methodology.gates.coverage_min") == 80
        assert manifest.source_of("methodology.gates.coverage_min") == f"repo:{MANIFEST_RELPATH}"

    def test_a_test_one_without_selector_is_refused_at_entry(self, toy, capsys, interactive):
        keyed = interactive(
            change_any=["yes"],
            **{"commands.test_one": ["pytest -q", "pytest -q {selector}"]},
        )
        code, text = init(capsys)
        assert code == 0, text
        assert any("lacks {selector}" in s for s in keyed.said)
        manifest = Manifest.locate(toy)
        assert manifest.get("commands.test_one") == "pytest -q {selector}"
        assert manifest.get("commands.test") == "pytest"
        assert "test:" not in (toy / MANIFEST_RELPATH).read_text(encoding="utf-8").replace(
            "test_one:", ""
        )

    def test_a_lane_and_a_floor_are_written_with_the_rest(self, toy, capsys, interactive):
        interactive(lane=["chore"], coverage_floor=["75"], gaps=["incremental"])
        code, text = init(capsys)
        assert code == 0, text
        raw = yaml.safe_load((toy / MANIFEST_RELPATH).read_text(encoding="utf-8"))
        assert raw["methodology"] == {"pipeline": "chore", "gates": {"coverage_min": 75}}
        assert raw["docs"] == {"onboarding": "incremental", **STARTER_DOCS}
        assert "enforcement" not in raw
        assert (toy / MANIFEST_RELPATH).read_text(encoding="utf-8").endswith(INIT_COMMENT)

    def test_profile_given_is_an_answer_and_not_asked(self, toy, capsys, interactive):
        keyed = interactive()
        code, text = init(capsys, "--profile", "strict")
        assert code == 0, text
        assert not any("profile" in q for q in keyed.asked)
        assert Manifest.locate(toy).get("enforcement.profile") == "strict"
        assert yaml.safe_load((toy / MANIFEST_RELPATH).read_text(encoding="utf-8"))[
            "enforcement"
        ] == {"profile": "strict"}

    def test_a_profile_given_that_does_not_exist_is_a_usage_error(
        self, toy, capsys, interactive
    ):
        interactive()
        code, text = init(capsys, "--profile", "lax")
        assert code == 2
        assert "lax" in text
        assert not (toy / MANIFEST_RELPATH).exists()

    def test_the_inherited_value_at_the_user_level_removes_it_saying_so(
        self, toy, capsys, interactive
    ):
        write_user("version: 1\ndefaults:\n  methodology: {gates: {coverage_min: 70}}\n")
        interactive(level=["user"], coverage_floor=["90"])
        code, text = init(capsys)
        assert code == 0, text
        assert load_user_config().defaults == {}
        assert "removed from user" in text
        assert Manifest.locate(toy).get("methodology.gates.coverage_min") == 90

    def test_settings_by_hand_is_a_json_field(self, toy, capsys, interactive):
        write_user("# mine\nversion: 1\n")
        interactive(level=["user"], profile=["strict"])
        code, text = run(capsys, "--json", "init", "--name", "toy")
        assert code == 0, text
        (entry,) = json.loads(text)["settings_by_hand"]
        assert entry["path"] == str(user_config_path())
        assert yaml.safe_load(entry["yaml"]) == {"defaults": {"enforcement": {"profile": "strict"}}}

    def test_without_the_questionnaire_settings_by_hand_is_empty(self, toy, capsys, monkeypatch):
        monkeypatch.setattr(cli_main, "_init_prompter", lambda args: None)
        monkeypatch.setattr(cli_main, "_questionnaire_prompter", lambda args: None)
        code, text = run(capsys, "--json", "init", "--name", "toy")
        assert code == 0, text
        assert json.loads(text)["settings_by_hand"] == []


class TestTheSeams:
    def test_the_questionnaire_seam_answers_none_without_a_terminal(self):
        args = build_parser().parse_args(["init"])
        assert cli_main._questionnaire_prompter(args) is None

    def test_and_a_terminal_prompter_on_one(self, monkeypatch):
        from invariant.cli import prompts

        monkeypatch.setattr(prompts, "interactive", lambda args: True)
        args = build_parser().parse_args(["init"])
        assert isinstance(cli_main._questionnaire_prompter(args), prompts.TerminalPrompter)


# -- ruling 13: a hand-written command of the wrong shape ------------------------


class TestDoctorCommandShape:
    def doctor_row(self, capsys, label):
        _code, text = run(capsys, "--json", "doctor")
        rows = {row["check"]: row for row in json.loads(text)["checks"]}
        return rows.get(label)

    def test_a_misshapen_command_loads_and_doctor_warns_naming_it(self, toy, capsys):
        (toy / MANIFEST_RELPATH).parent.mkdir(parents=True)
        (toy / MANIFEST_RELPATH).write_text(
            "version: 1\nproject:\n  name: toy\n  ecosystems: [python]\n"
            "commands:\n  test_one: pytest -q\n",
            encoding="utf-8",
        )
        assert Manifest.locate(toy).get("commands.test_one") == "pytest -q"
        row = self.doctor_row(capsys, "command shape")
        assert row["ok"] is True
        assert row["warn"] is True
        assert "commands.test_one" in row["detail"]
        assert "lacks {selector}" in row["detail"]

    def test_well_shaped_commands_pass_without_a_warning(self, toy, capsys):
        (toy / MANIFEST_RELPATH).parent.mkdir(parents=True)
        (toy / MANIFEST_RELPATH).write_text(
            "version: 1\nproject:\n  name: toy\n  ecosystems: [python]\n", encoding="utf-8"
        )
        row = self.doctor_row(capsys, "command shape")
        assert row["ok"] is True
        assert row["warn"] is False


def test_init_writes_the_one_shared_comment():
    assert stores.INIT_COMMENT == INIT_COMMENT


def test_a_choice_with_no_label_is_listed_by_its_key_alone():
    from invariant.cli.prompts import TerminalPrompter

    written: list[str] = []
    prompter = TerminalPrompter(read=lambda prompt: "", write=written.append)
    assert prompter.choose("Which?", [("tdd", ""), ("chore", "small")], "tdd") == "tdd"
    assert written[1:] == ["  1) tdd  (default)", "  2) chore: small"]


# -- the edges of the driver, the draft and the writers ---------------------------


class Accepting:
    """Answers every question with its default, recording the questions."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.said: list[str] = []

    def say(self, text):
        self.said.append(text)

    def choose(self, question, options, default):
        self.asked.append(question)
        return default

    def ask(self, question, default=""):
        self.asked.append(question)
        return default


class TestTheDriver:
    def run(self, tmp_path, sections, prompter=None):
        from invariant.cli import configure

        prompter = prompter or Accepting()
        asked = configure.Asked(level="repo", draft=questionnaire.Draft(fresh(tmp_path)))
        catalogue = questionnaire.load_catalogue()
        configure.ask_sections(prompter, catalogue, asked, sections)
        return prompter, asked

    def test_an_advanced_section_asks_each_kind_and_skips_the_store_step(self, tmp_path):
        prompter, asked = self.run(tmp_path, ["session", "records"])
        keys = [a.key for a in asked.answers]
        assert "session.push" in keys and "session.plan_stale_days" in keys
        assert "records.docs" in keys
        assert not any(q.startswith("Where is the store") for q in prompter.asked)
        assert asked.value("session.push") is False
        assert asked.value("nothing.here", "fallback") == "fallback"

    def test_an_unreadable_answer_is_refused_and_asked_again(self, tmp_path):
        keyed = Keyed(coverage_floor=["lots", "[", "80"])
        _prompter, asked = self.run(tmp_path, ["coverage"], keyed)
        assert asked.value("methodology.gates.coverage_min") == 80
        assert "refused: 'lots' is not a number" in keyed.said
        assert "refused: '[' is not a number" in keyed.said

    def test_typed_spells_a_value_as_it_is_typed_back(self):
        from invariant.cli.configure import typed

        assert [typed(v) for v in (None, True, False, ["a", "b"], 85)] == [
            "",
            "yes",
            "no",
            "a, b",
            "85",
        ]


class TestTheDraft:
    def test_a_level_the_project_does_not_have_is_refused_naming_its_levels(self, tmp_path):
        draft = questionnaire.Draft(fresh(tmp_path))
        with pytest.raises(ManifestError, match="'store' is not one this project has; it has user, repo"):
            draft.placed("session.push", True, "store")

    def test_an_empty_block_for_this_repository_is_still_a_level(self, tmp_path):
        repo = make_repo(tmp_path / "widgets", origin=ORIGIN)
        store = tmp_path / "stores" / "widgets"
        store.mkdir(parents=True)
        (store / "invariant.yaml").write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "project": {"name": "widgets", "ecosystems": ["python"]},
                    "store": {"repos": {IDENTITY: {}}},
                }
            ),
            encoding="utf-8",
        )
        write_user(yaml.safe_dump({"version": 1, "repos": {IDENTITY: {"store": str(store)}}}))
        base = Manifest.locate(repo)
        draft = questionnaire.Draft(base).placed("methodology.pipeline", "chore", "store-repo")
        manifest = draft.manifest()
        assert manifest.get("methodology.pipeline") == "chore"
        assert manifest.source_of("methodology.pipeline") == base.store.repo_label


class TestTheWritersEdges:
    def test_a_commented_repo_file_already_setting_the_block_is_held(self, tmp_path):
        path = tmp_path / "invariant.yaml"
        path.write_text("# mine\nversion: 1\nsession: {push: true}\n", encoding="utf-8")
        result = stores.write_repo_settings(path, {"session": {"push": False}})
        assert not result.written
        assert "already sets session" in result.reason

    def test_a_store_block_loses_a_removed_key(self, tmp_path):
        root = tmp_path / "store"
        root.mkdir()
        (root / "invariant.yaml").write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "store": {"repos": {"k": {"methodology": {"pipeline": "chore"}, "x": 1}}},
                }
            ),
            encoding="utf-8",
        )
        assert stores.write_store_settings(
            root, "k", repo={"y": 2}, remove_repo=["methodology.pipeline"]
        ).written
        body = yaml.safe_load((root / "invariant.yaml").read_text(encoding="utf-8"))
        assert body["store"]["repos"]["k"] == {"x": 1, "y": 2}

    def test_a_held_store_names_the_removal_to_make_by_hand(self, tmp_path):
        from invariant.cli.onboarding import StorePlacement

        root = tmp_path / "store"
        root.mkdir()
        (root / "invariant.yaml").write_text(
            "# reviewed\nversion: 1\nstore:\n  repos:\n    k: {}\n", encoding="utf-8"
        )
        placement = StorePlacement(root=root, key="k", lines=())
        line = placement.write({"y": 2}, ["methodology.pipeline"])
        assert line.startswith("not written, to add by hand: y: 2, remove methodology.pipeline")
        held = placement.by_hand()
        assert "remove store.repos.k.methodology.pipeline by hand" in held["reason"]
        assert yaml.safe_load(held["yaml"])["store"]["repos"]["k"] == {"y": 2}

    def test_a_route_from_a_level_that_holds_nothing_project_specific(self):
        assert questionnaire.route("project.name", "user", store_project=True) == (
            "store",
            "project.name goes to store: the user level may not hold project",
        )


def test_a_misshapen_default_is_accepted_as_it_is_and_never_loops(tmp_path):
    """A hand-written command of the wrong shape loads (ruling 13) and is the
    default shown; Enter accepts it, writing nothing, instead of refusing it
    for ever."""
    from invariant.cli import configure

    base = Manifest.from_raw(
        {"version": 1, "project": {"ecosystems": ["python"]}, "commands": {"test_one": "pytest"}},
        root=tmp_path,
        path=tmp_path / MANIFEST_RELPATH,
        env={},
    )
    keyed = Keyed(change_any=["yes"])
    asked = configure.Asked(level="repo", draft=questionnaire.Draft(base))
    catalogue = questionnaire.load_catalogue()
    configure.ask_sections(keyed, catalogue, asked, ["ecosystems"])
    assert asked.value("commands.test_one") == "pytest"
    assert questionnaire.plan(base, asked.answers).writes == ()
