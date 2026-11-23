"""ADR-032 increment 2: only the user excepts, loosens or rewords a default
rule, and the defaults are adopted (D7, D8; DEC_ADR_032_I2).

D7's three routes past a finding are a fix, a user-ruled exception
(``guidelines except``) and the user changing the rule (``guidelines
loosen``, or a hand edit carrying a fresh ``ruled``). What an agent cannot do
is refused mechanically: an ``na`` on an ``owner: user`` rule at submission
(P5), an ``override`` of one without a terminal, an edit writing a ``ruled``
entry (``config.rulings_by_user``), and a silent change caught against the
base branch (``guidelines.user_rules_ruled``). A blocker's ``fail`` blocks
(``guidelines.blocking_verdicts``). D8: ``guidelines adopt`` writes the
defaults into a project with no ruleset, merges them into one that has a
ruleset, and tells an updatable copy from a diverged one by its origin.

Every repository here is a scratch one, and the terminal is scripted.
"""

from __future__ import annotations

import datetime
import subprocess
from pathlib import Path

import pytest
import yaml
from test_domains import git_project
from test_guidelines_domain import RULESET_REL, write

from invariant.cli.main import main
from invariant.core import guidelines, rulings, verdicts
from invariant.core.findings import NaAnswer
from invariant.core.machines import GUIDELINES_GATES
from invariant.core.manifest import Level, Manifest
from invariant.core.rules import RuleContext, all_rules, evaluate, plan_verdicts
from invariant.core.verdicts import Facts
from invariant.domains import docs as docs_domain
from invariant.domains.guidelines import (
    DefaultsRefused,
    EditRefused,
    GuidelinesDomain,
    fresh_defaults,
)

REGISTERED = frozenset(spec.id for spec in all_rules())
TODAY = datetime.datetime.now().astimezone().date().isoformat()
PYTHON = {"name": "demo", "ecosystems": ["python"]}
SRP = "solid.single-responsibility"
OCP = "solid.open-closed"
USER = {"by": "owner", "date": "2026-11-22", "reason": "the legacy module is retired next release"}


def solid_rule(rule_id: str, title: str) -> str:
    return f"""\
  - id: {rule_id}
    section: solid
    title: {title}
    statement: One reason to change.
    severity: blocker
    owner: user
    phases: [design, review, landing]
    applies_to: ["**/*.py"]
    check:
      kind: judgement
      evidence: citation
      scope: diff
      not_applicable_when:
        - id: no_executable_change
          mechanical: {{no_executable_change: true}}
"""


RULESET = (
    """\
# The project's ruleset; this comment is kept by every edit.
version: 2
sections:
  - id: solid
    title: SOLID principles
layers:
  owner: user
  map:
    - id: inner
      title: Inner
      paths: ["app/**"]
    - id: outer
      title: Outer
      paths: ["cli/**"]
rules:
"""
    + solid_rule(SRP, "Single responsibility")
    + solid_rule(OCP, "Open/closed")
)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def parse(text: str) -> guidelines.Ruleset:
    return guidelines.parse(text, registered=REGISTERED)


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, RULESET)
    write(root, "app/m.py", "x = 1\n")
    git_project(root, {"version": 1, "project": PYTHON, "guidelines": {"ruleset": RULESET_REL}})
    git(root, "checkout", "-q", "-b", "work")
    return root


@pytest.fixture
def terminal(monkeypatch):
    """A terminal on stdin, answering each question from ``answers``."""

    def answer(*answers: str) -> list[str]:
        asked: list[str] = []
        queue = list(answers)
        monkeypatch.setattr("invariant.cli.main._stdin_is_terminal", lambda: True)

        def reply(prompt: str = "") -> str:
            asked.append(prompt)
            if not queue:
                raise EOFError
            return queue.pop(0)

        monkeypatch.setattr("builtins.input", reply)
        return asked

    return answer


@pytest.fixture
def no_terminal(monkeypatch):
    monkeypatch.setattr("invariant.cli.main._stdin_is_terminal", lambda: False)

    def refuse(prompt: str = "") -> str:
        raise AssertionError("asked with no terminal")

    monkeypatch.setattr("builtins.input", refuse)


def run(capsys, *argv) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def context(root: Path, *, pipe=None, payload=None, profile=None) -> RuleContext:
    manifest = Manifest.locate(root)
    if profile is not None:
        body = yaml.safe_load((root / ".claude" / "invariant.yaml").read_text())
        body["enforcement"] = {"profile": profile}
        (root / ".claude" / "invariant.yaml").write_text(yaml.safe_dump(body))
        manifest = Manifest.locate(root)
    return RuleContext(
        manifest=manifest, cwd=root,
        session={"id": "s", "base": "master", "files": []},
        pipeline=pipe, payload=payload,
    )


def gate(ctx: RuleContext, rule_id: str):
    (result,) = evaluate([rule_id], ctx)
    return result


# -- the na refusal (D7, P5) ------------------------------------------------------------------


class TestNa:
    def facts(self, **more) -> Facts:
        ruleset = parse(RULESET)
        values = {
            "tree_digest": ruleset.digest, "pin": ruleset.digest, "state": "reviewing",
            "roles": ("review",), "touched": ("app/m.py",),
            "read": {"app/m.py": b"x = 1\n"}.get, "added": lambda: {"app/m.py": frozenset({1})},
            "test_runs": (),
            "na": lambda rule, condition, files: NaAnswer(rule.id, condition.id, True, "comments only"),
        }
        values.update(more)
        return Facts(**values)

    def test_an_agent_submitted_na_on_single_responsibility_is_refused_at_submission(self):
        ruleset = parse(RULESET)
        sub, problems = verdicts.parse_entry(
            {"rule": SRP, "status": "na", "na_condition": "no_executable_change"}
        )
        assert not problems
        checked = verdicts.check(sub, ruleset.by_id(SRP), self.facts())
        assert checked.record is None
        (p5,) = [p for p in checked.problems if p.predicate == "P5"]
        assert "owner: user" in p5.message
        assert f"invariant guidelines except {SRP}" in p5.fix

    def test_a_project_rule_still_takes_its_mechanical_na(self):
        text = RULESET.replace("    owner: user\n    phases", "    phases", 1)
        ruleset = parse(text)
        assert ruleset.by_id(SRP).owner == "project"
        sub, _ = verdicts.parse_entry(
            {"rule": SRP, "status": "na", "na_condition": "no_executable_change"}
        )
        assert verdicts.check(sub, ruleset.by_id(SRP), self.facts()).record is not None

    def test_the_engines_na_still_answers_the_user_rule_at_the_gate(self):
        ruleset = parse(RULESET)
        (standing, *_rest) = verdicts.standings(ruleset, [], self.facts())
        assert standing.standing == verdicts.ENGINE

    def test_the_command_refuses_it_through_the_plan(self, repo):
        write(repo, "app/m.py", "x = 2\n")
        pipe = {"methodology": "tdd", "state": "reviewing",
                "ruleset": {"path": RULESET_REL, "digest": parse(RULESET).digest, "error": None},
                "evidence": {}}
        plan = plan_verdicts(context(repo, pipe=pipe), [
            {"rule": SRP, "status": "na", "na_condition": "no_executable_change"}
        ])
        assert not plan.ok
        assert any("owner: user" in str(p) for p in plan.problems)


# -- the override's terminal (D7) ------------------------------------------------------------------


@pytest.fixture
def session_repo(repo, monkeypatch) -> Path:
    monkeypatch.chdir(repo)
    assert main(["session", "start", "user-rules"]) == 0
    return repo


class TestOverride:
    def test_an_agents_override_on_a_user_rule_without_a_terminal_is_refused(
        self, session_repo, capsys, no_terminal
    ):
        code, text = run(capsys, "override", SRP, "--reason", "it is fine", "--by", "agent")
        assert code == 1
        assert "terminal" in text
        _code, status = run(capsys, "session", "status")
        assert f"override   {SRP}" not in status

    def test_it_needs_by(self, session_repo, capsys, terminal):
        terminal("y")
        code, text = run(capsys, "override", SRP, "--reason", "it is fine")
        assert code != 0 and "--by" in text

    def test_json_refuses_even_at_a_terminal(self, session_repo, capsys, terminal):
        terminal("y")
        code, _text = run(capsys, "override", SRP, "--reason", "r", "--by", "owner", "--json")
        assert code == 1

    def test_confirmed_at_a_terminal_it_is_granted_and_records_by(
        self, session_repo, capsys, terminal
    ):
        asked = terminal("y")
        code, text = run(capsys, "override", SRP, "--reason", "it is fine", "--by", "owner")
        assert code == 0, text
        assert asked and "Grant this override?" in asked[0]
        _code, status = run(capsys, "session", "status")
        assert f"override   {SRP}" in status

    def test_declined_grants_nothing(self, session_repo, capsys, terminal):
        terminal("n")
        code, _text = run(capsys, "override", SRP, "--reason", "r", "--by", "owner")
        assert code == 1
        _code, status = run(capsys, "session", "status")
        assert f"override   {SRP}" not in status


# -- config.rulings_by_user over the ruleset (D7) -----------------------------------------------


def edit(path: Path, old: str, new: str) -> dict:
    return {"tool_name": "Edit", "tool_input": {
        "file_path": str(path), "old_string": old, "new_string": new}}


EXCEPTION = (
    "    exceptions:\n"
    "      - path: app/m.py\n"
    "        reason: legacy\n"
    "        ruled: {by: owner, date: '2026-11-22', reason: legacy}\n"
)


class TestTheHook:
    def test_an_agents_edit_adding_an_exception_to_the_ruleset_is_refused(self, repo):
        path = repo / RULESET_REL
        anchor = "    applies_to: [\"**/*.py\"]\n"
        judged = gate(context(repo, payload=edit(path, anchor, anchor + EXCEPTION)),
                      "config.rulings_by_user")
        assert judged.blocks
        assert "ruled" in judged.verdict.message
        assert any("invariant guidelines except" in fix for fix in judged.verdict.fixes)

    def test_an_edit_writing_no_ruled_entry_passes(self, repo):
        path = repo / RULESET_REL
        judged = gate(context(repo, payload=edit(path, "One reason", "A single reason")),
                      "config.rulings_by_user")
        assert judged.verdict.ok and not judged.verdict.skipped

    def test_an_edit_removing_a_ruled_entry_passes(self, repo):
        path = write(repo, RULESET_REL, RULESET.replace(
            "    applies_to: [\"**/*.py\"]\n", "    applies_to: [\"**/*.py\"]\n" + EXCEPTION, 1))
        judged = gate(context(repo, payload=edit(path, EXCEPTION, "")), "config.rulings_by_user")
        assert judged.verdict.ok

    def test_an_edit_to_the_catalogue_writing_a_ruled_entry_is_refused(self, repo):
        write(repo, RULESET_REL, RULESET.replace("version: 2\n", "version: 2\ncatalogue: patterns.yaml\n"))
        catalogue = write(repo, "docs/guidelines/patterns.yaml", "version: 1\npatterns: []\n")
        judged = gate(context(repo, payload=edit(
            catalogue, "patterns: []\n", "patterns: []\nruled: {by: me, date: '2026-11-22', reason: x}\n"
        )), "config.rulings_by_user")
        assert judged.blocks

    def test_another_yaml_file_is_not_its_business(self, repo):
        other = write(repo, "app/data.yaml", "a: 1\n")
        judged = gate(context(repo, payload=edit(other, "a: 1\n", "ruled: {by: x}\n")),
                      "config.rulings_by_user")
        assert judged.verdict.skipped


class TestChangedRuled:
    def test_added_and_changed_entries_are_named_by_where_they_sit(self):
        before = "rules:\n  - id: a\n    ruled: {by: x, date: '2026-01-01', reason: r}\n"
        after = (
            "rules:\n  - id: b\n    ruled: {by: y, date: '2026-01-01', reason: r}\n"
            "  - id: a\n    ruled: {by: z, date: '2026-01-01', reason: r}\n"
        )
        assert sorted(rulings.changed_ruled(before, after)) == ["rules[a].ruled", "rules[b].ruled"]

    def test_a_reordering_is_no_change(self):
        one = "rules:\n  - id: a\n    ruled: {by: x}\n  - id: b\n    ruled: {by: y}\n"
        two = "rules:\n  - id: b\n    ruled: {by: y}\n  - id: a\n    ruled: {by: x}\n"
        assert rulings.changed_ruled(one, two) == []

    def test_an_exception_is_keyed_by_its_path(self):
        after = "exceptions:\n  - path: app/m.py\n    ruled: {by: x}\n"
        assert rulings.changed_ruled("", after) == ["exceptions[app/m.py].ruled"]

    def test_text_that_is_not_yaml_cannot_be_told(self):
        assert rulings.changed_ruled("a: 1\n", "a: [\n") is None


# -- guidelines.user_rules_ruled (D7) ----------------------------------------------------------------


class TestUserRulesRuled:
    RULE = "guidelines.user_rules_ruled"

    def test_lowering_open_closed_with_no_new_ruled_fails(self, repo):
        write(repo, RULESET_REL, RULESET.replace(
            "title: Open/closed\n    statement: One reason to change.\n    severity: blocker",
            "title: Open/closed\n    statement: One reason to change.\n    severity: warn"))
        verdict = gate(context(repo), self.RULE).verdict
        assert not verdict.ok and not verdict.skipped
        assert OCP in verdict.message and "lowered" in verdict.message

    def test_the_same_lowering_with_a_fresh_ruled_passes(self, repo):
        write(repo, RULESET_REL, RULESET.replace(
            "title: Open/closed\n    statement: One reason to change.\n    severity: blocker",
            "title: Open/closed\n    statement: One reason to change.\n    severity: warn\n"
            "    ruled: {by: owner, date: '2026-11-22', reason: too strict for scripts}"))
        verdict = gate(context(repo), self.RULE).verdict
        assert verdict.ok, verdict.message

    def test_an_unchanged_ruleset_passes(self, repo):
        assert gate(context(repo), self.RULE).verdict.ok

    def test_it_blocks_in_every_profile(self, tmp_path):
        for profile in ("loose", "standard", "strict"):
            manifest = git_project(tmp_path / profile, {
                "version": 1, "project": PYTHON, "enforcement": {"profile": profile}})
            assert manifest.rule_level(self.RULE) is Level.BLOCK

    def test_it_rides_the_landing(self):
        assert self.RULE in GUIDELINES_GATES["landing"]["entry"]

    def test_no_ruleset_is_not_applicable(self, tmp_path):
        root = tmp_path / "bare"
        git_project(root, {"version": 1, "project": PYTHON})
        assert gate(context(root), self.RULE).verdict.skipped


class TestUnruledChanges:
    def changes(self, after: str, before: str = RULESET) -> tuple[str, ...]:
        return guidelines.unruled_changes(parse(before), parse(after))

    def test_nothing_changed_is_nothing(self):
        assert self.changes(RULESET) == ()

    def test_no_base_is_nothing(self):
        assert guidelines.unruled_changes(None, parse(RULESET)) == ()

    def test_a_reworded_rule_needs_a_ruling(self):
        (said,) = self.changes(RULESET.replace("One reason to change.", "Do one thing.", 1))
        assert SRP in said and "reworded" in said

    def test_a_narrowed_rule_needs_a_ruling(self):
        (said,) = self.changes(RULESET.replace('applies_to: ["**/*.py"]', 'applies_to: ["app/*.py"]', 1))
        assert "narrowed" in said

    def test_widening_needs_nothing(self):
        assert self.changes(RULESET.replace(
            'applies_to: ["**/*.py"]', 'applies_to: ["**/*.py", "**/*.pyi"]', 1)) == ()

    def test_dropping_an_na_condition_needs_nothing(self):
        text = RULESET.replace(
            "      not_applicable_when:\n        - id: no_executable_change\n"
            "          mechanical: {no_executable_change: true}\n", "", 1)
        assert self.changes(text) == ()

    def test_an_exception_needs_its_own_new_ruled(self):
        anchor = '    applies_to: ["**/*.py"]\n'
        assert self.changes(RULESET.replace(anchor, anchor + EXCEPTION, 1)) == ()
        with_one = RULESET.replace(anchor, anchor + EXCEPTION, 1)
        copied = EXCEPTION.replace("    exceptions:\n", "").replace("app/m.py", "app/n.py")
        both = with_one.replace(EXCEPTION, EXCEPTION + copied, 1)
        (said,) = guidelines.unruled_changes(parse(with_one), parse(both))
        assert "app/n.py" in said

    def test_a_removed_rule_fails(self):
        text = RULESET.split(f"  - id: {OCP}")[0]
        (said,) = self.changes(text)
        assert OCP in said and "removed" in said

    def test_the_layer_map_losing_a_path_needs_a_ruling(self):
        (said,) = self.changes(RULESET.replace('paths: ["cli/**"]', "paths: []"))
        assert "layer map" in said

    def test_the_layer_map_gaining_a_path_needs_nothing(self):
        assert self.changes(RULESET.replace('paths: ["cli/**"]', 'paths: ["cli/**", "web/**"]')) == ()

    def test_a_project_rule_is_the_projects_to_change(self):
        text = RULESET.replace("    owner: user\n    phases", "    phases")
        assert guidelines.unruled_changes(
            parse(text), parse(text.replace("severity: blocker", "severity: warn"))) == ()


# -- guidelines.blocking_verdicts (D7) ---------------------------------------------------------------


class TestBlockingVerdicts:
    RULE = "guidelines.blocking_verdicts"

    def pipeline(self, records=()):
        return {"methodology": "tdd", "state": "reviewing",
                "ruleset": {"path": RULESET_REL, "digest": parse(RULESET).digest, "error": None},
                "evidence": {"test_runs": [], "verdicts": list(records)}}

    def failed(self, repo):
        write(repo, "app/m.py", "x = 1\ny = 2\n")
        plan = plan_verdicts(context(repo, pipe=self.pipeline()), [
            {"rule": SRP, "status": "fail", "citations": [{"path": "app/m.py", "lines": [2, 2]}]},
            {"rule": OCP, "status": "pass", "citations": [{"path": "app/m.py", "lines": [2, 2]}]},
        ])
        assert plan.ok, plan.problems
        return [r.record() for r in plan.records]

    def test_a_current_fail_blocks_under_standard(self, repo):
        recorded = self.failed(repo)
        judged = gate(context(repo, pipe=self.pipeline(recorded), profile="standard"), self.RULE)
        assert judged.blocks
        assert SRP in judged.verdict.message
        assert any("invariant guidelines except" in fix for fix in judged.verdict.fixes)

    def test_after_the_users_exception_the_same_finding_passes(self, repo, capsys, terminal, monkeypatch):
        recorded = self.failed(repo)
        monkeypatch.chdir(repo)
        terminal("y")
        code, text = run(capsys, "guidelines", "except", SRP, "app/m.py",
                         "--by", "owner", "--reason", "retired next release")
        assert code == 0, text
        data = yaml.safe_load((repo / RULESET_REL).read_text())
        (srp,) = [r for r in data["rules"] if r["id"] == SRP]
        (exc,) = srp["exceptions"]
        assert exc["ruled"] == {"by": "owner", "date": TODAY, "reason": "retired next release"}
        judged = gate(context(repo, pipe=self.pipeline(recorded), profile="standard"), self.RULE)
        assert judged.verdict.ok, judged.verdict.message

    def test_a_missing_blocker_verdict_blocks(self, repo):
        write(repo, "app/m.py", "x = 3\n")
        judged = gate(context(repo, pipe=self.pipeline(), profile="standard"), self.RULE)
        assert judged.blocks

    def test_it_warns_under_loose(self, repo):
        recorded = self.failed(repo)
        judged = gate(context(repo, pipe=self.pipeline(recorded), profile="loose"), self.RULE)
        assert not judged.verdict.ok and not judged.blocks

    @pytest.mark.parametrize(("profile", "level"),
                             [("loose", "warn"), ("standard", "block"), ("strict", "block")])
    def test_its_level_per_profile(self, tmp_path, profile, level):
        manifest = git_project(tmp_path / profile, {
            "version": 1, "project": PYTHON, "enforcement": {"profile": profile}})
        assert manifest.rule_level(self.RULE).value == level

    def test_it_rides_the_landing(self):
        assert self.RULE in GUIDELINES_GATES["landing"]["entry"]

    def test_a_grant_passes_it(self, repo):
        recorded = self.failed(repo)
        ctx = context(repo, pipe=self.pipeline(recorded), profile="standard")
        ctx.session["pending_overrides"] = [{"rule": SRP, "reason": "user", "calls": 1, "by": "owner"}]
        judged = gate(ctx, self.RULE)
        assert judged.verdict.ok
        assert dict(judged.verdict.overridden).get(SRP)

    def test_no_pipeline_is_not_applicable(self, repo):
        assert gate(context(repo), self.RULE).verdict.skipped


# -- guidelines except and loosen at a terminal (D7) ---------------------------------------------


class TestExcept:
    def test_it_refuses_without_a_terminal_and_writes_nothing(self, repo, capsys, no_terminal, monkeypatch):
        monkeypatch.chdir(repo)
        before = (repo / RULESET_REL).read_text()
        code, text = run(capsys, "guidelines", "except", SRP, "app/m.py", "--by", "o", "--reason", "r")
        assert code == 1 and "terminal" in text
        assert (repo / RULESET_REL).read_text() == before

    def test_json_refuses_even_at_a_terminal(self, repo, capsys, terminal, monkeypatch):
        monkeypatch.chdir(repo)
        terminal("y")
        code, _text = run(capsys, "guidelines", "except", SRP, "app/m.py", "--by", "o",
                          "--reason", "r", "--json")
        assert code == 1

    def test_declined_writes_nothing(self, repo, capsys, terminal, monkeypatch):
        monkeypatch.chdir(repo)
        before = (repo / RULESET_REL).read_text()
        terminal("n")
        code, _text = run(capsys, "guidelines", "except", SRP, "app/m.py", "--by", "o", "--reason", "r")
        assert code == 1 and (repo / RULESET_REL).read_text() == before

    def test_it_keeps_every_other_byte(self, repo):
        domain = GuidelinesDomain(context(repo))
        planned = domain.plan_except(SRP, "app/m.py", fingerprint="abcdefabcdef",
                                     by="owner", reason="legacy", today=datetime.date(2026, 11, 22))
        domain.write_edit(planned)
        text = (repo / RULESET_REL).read_text()
        assert text.startswith("# The project's ruleset; this comment is kept by every edit.\n")
        (srp,) = [r for r in parse(text).rules if r.id == SRP]
        (exc,) = srp.exceptions
        assert (exc.path, exc.fingerprint, exc.ruled.by) == ("app/m.py", "abcdefabcdef", "owner")
        assert parse(text).by_id(OCP) == parse(RULESET).by_id(OCP)

    def test_a_derived_rule_is_excepted_in_its_layer(self, repo):
        domain = GuidelinesDomain(context(repo))
        domain.write_edit(domain.plan_except(
            "layers.inner-depends-inward", "app/legacy.py", by="o", reason="r",
            today=datetime.date(2026, 11, 22)))
        data = yaml.safe_load((repo / RULESET_REL).read_text())
        (inner, _outer) = data["layers"]["map"]
        assert inner["exceptions"][0]["path"] == "app/legacy.py"

    def test_an_exception_the_rule_has_is_refused(self, repo):
        domain = GuidelinesDomain(context(repo))
        domain.write_edit(domain.plan_except(SRP, "app/m.py", by="o", reason="r",
                                             today=datetime.date(2026, 11, 22)))
        with pytest.raises(EditRefused, match="already"):
            GuidelinesDomain(context(repo)).plan_except(
                SRP, "app/m.py", by="o", reason="r", today=datetime.date(2026, 11, 22))

    def test_an_unknown_rule_is_refused(self, repo):
        with pytest.raises(EditRefused, match="no guideline"):
            GuidelinesDomain(context(repo)).plan_except(
                "solid.nope", "app/m.py", by="o", reason="r", today=datetime.date(2026, 11, 22))

    def test_a_file_changed_since_the_plan_is_not_written_over(self, repo):
        domain = GuidelinesDomain(context(repo))
        planned = domain.plan_except(SRP, "app/m.py", by="o", reason="r",
                                     today=datetime.date(2026, 11, 22))
        write(repo, RULESET_REL, RULESET + "\n")
        with pytest.raises(EditRefused, match="changed"):
            domain.write_edit(planned)


class TestLoosen:
    def test_confirmed_it_lowers_the_rule_and_records_the_ruling(self, repo, capsys, terminal, monkeypatch):
        monkeypatch.chdir(repo)
        asked = terminal("y")
        code, text = run(capsys, "guidelines", "loosen", OCP, "--to", "warn",
                         "--by", "owner", "--reason", "too strict for scripts")
        assert code == 0, text
        assert asked and "?" in asked[0]
        rule = parse((repo / RULESET_REL).read_text()).by_id(OCP)
        assert rule.severity == "warn"
        assert rule.ruled == guidelines.Ruling("owner", TODAY, "too strict for scripts")
        assert gate(context(repo), "guidelines.user_rules_ruled").verdict.ok

    def test_it_refuses_without_a_terminal(self, repo, capsys, no_terminal, monkeypatch):
        monkeypatch.chdir(repo)
        code, _text = run(capsys, "guidelines", "loosen", OCP, "--to", "warn", "--by", "o", "--reason", "r")
        assert code == 1

    def test_the_layer_map_is_lowered_as_a_whole(self, repo):
        domain = GuidelinesDomain(context(repo))
        domain.write_edit(domain.plan_loosen("layers", to="warn", by="o", reason="r",
                                             today=datetime.date(2026, 11, 22)))
        ruleset = parse((repo / RULESET_REL).read_text())
        assert ruleset.layers.severity == "warn" and ruleset.layers.ruled is not None
        assert ruleset.by_id("layers.inner-depends-inward").severity == "warn"

    def test_a_derived_rule_names_the_map_route(self, repo):
        with pytest.raises(EditRefused, match="loosen layers"):
            GuidelinesDomain(context(repo)).plan_loosen(
                "layers.inner-depends-inward", to="warn", by="o", reason="r",
                today=datetime.date(2026, 11, 22))

    def test_a_rule_at_warn_is_refused(self, repo):
        domain = GuidelinesDomain(context(repo))
        domain.write_edit(domain.plan_loosen(OCP, to="warn", by="o", reason="r",
                                             today=datetime.date(2026, 11, 22)))
        with pytest.raises(EditRefused, match="already"):
            GuidelinesDomain(context(repo)).plan_loosen(
                OCP, to="warn", by="o", reason="r", today=datetime.date(2026, 11, 22))


# -- guidelines adopt (D8) -------------------------------------------------------------------------


@pytest.fixture
def toy(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "toy"
    root.mkdir()
    write(root, "app/m.py", "x = 1\n")
    git_project(root, {"version": 1, "project": PYTHON})
    monkeypatch.chdir(root)
    return root


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts and ".invariant" not in p.parts
    }


class TestAdopt:
    def test_on_a_toy_project_with_no_ruleset_it_writes_the_defaults_and_a_baseline(
        self, toy, capsys, terminal
    ):
        terminal("y")
        code, text = run(capsys, "guidelines", "adopt")
        assert code == 0, text
        manifest = Manifest.locate(toy)
        assert manifest.get("guidelines.ruleset") == "docs/guidelines/ruleset.yaml"
        assert manifest.get("guidelines.baseline") == "docs/guidelines/baseline.txt"
        ruleset = parse_with_catalogue(toy)
        for rule_id in (SRP, OCP, "patterns.named-or-declined", "docs.design-before-code"):
            assert ruleset.by_id(rule_id) is not None
        assert ruleset.catalogue is not None and len(ruleset.catalogue.patterns) == 32
        assert (toy / "docs" / "guidelines" / "baseline.txt").is_file()
        assert "maps no files" in text or "empty" in text

    def test_run_again_it_changes_nothing(self, toy, capsys, terminal):
        terminal("y")
        assert run(capsys, "guidelines", "adopt")[0] == 0
        before = tree_bytes(toy)
        terminal()
        code, text = run(capsys, "guidelines", "adopt")
        assert code == 0 and "nothing to adopt" in text
        assert tree_bytes(toy) == before

    def test_dry_run_writes_nothing_and_needs_no_terminal(self, toy, capsys, no_terminal):
        before = tree_bytes(toy)
        code, text = run(capsys, "guidelines", "adopt", "--dry-run")
        assert code == 0 and SRP in text
        assert tree_bytes(toy) == before

    def test_it_refuses_without_a_terminal(self, toy, capsys, no_terminal):
        before = tree_bytes(toy)
        code, _text = run(capsys, "guidelines", "adopt")
        assert code == 1 and tree_bytes(toy) == before

    def test_layer_fills_the_map(self, toy, capsys, terminal):
        terminal("y")
        code, text = run(capsys, "guidelines", "adopt", "--layer", "entities=app/**",
                         "--layer", "frameworks-drivers=cli/**")
        assert code == 0, text
        ruleset = parse_with_catalogue(toy)
        assert ruleset.by_id("layers.entities-depends-inward") is not None


def parse_with_catalogue(root: Path) -> guidelines.Ruleset:
    rel = Manifest.locate(root).get("guidelines.ruleset")
    return guidelines.load(root / rel, registered=REGISTERED, root=root)


class TestDefaultStandings:
    def defaults(self, manifest) -> str:
        return docs_domain.default_ruleset(manifest, "docs/backlog.rst")

    def test_a_shipped_digest_bump_lists_the_rule_as_updatable_and_a_user_edit_as_diverged(self, toy):
        manifest = Manifest.locate(toy)
        shipped = self.defaults(manifest)
        project = guidelines.fill_defaults(shipped)
        assert {s.id: s.standing for s in guidelines.default_standings(project, shipped)}[SRP] == "current"
        bumped = shipped.replace("Each module, type and function", "Every module, type and function")
        edited = project.replace("Open/closed.", "Open/closed, as we read it.").replace(
            "      Fix: where a change adds a branch", "      Fix: where any change adds a branch")
        found = {s.id: s.standing for s in guidelines.default_standings(edited, bumped)}
        assert found[SRP] == "updatable"
        assert found[OCP] == "diverged"

    def test_update_replaces_an_updatable_rule(self, toy):
        manifest = Manifest.locate(toy)
        shipped = self.defaults(manifest)
        project = guidelines.fill_defaults(shipped)
        bumped = shipped.replace("Each module, type and function", "Every module, type and function")
        updated = guidelines.replace_default(project, bumped, SRP)
        assert "Every module, type and function" in updated
        assert {s.id: s.standing for s in guidelines.default_standings(updated, bumped)}[SRP] == "current"

    def test_the_map_filled_by_the_project_is_current(self, toy):
        manifest = Manifest.locate(toy)
        shipped = self.defaults(manifest)
        project = guidelines.fill_defaults(shipped, {"entities": ["app/**"]})
        assert {s.id: s.standing for s in guidelines.default_standings(project, shipped)}["layers"] == "current"

    def test_the_domain_plan_lists_and_updates(self, toy):
        manifest = Manifest.locate(toy)
        shipped = self.defaults(manifest)
        write(toy, RULESET_REL, guidelines.fill_defaults(shipped))
        write(toy, ".claude/invariant.yaml", yaml.safe_dump(
            {"version": 1, "project": PYTHON, "guidelines": {"ruleset": RULESET_REL}}))
        catalogue = docs_domain.default_catalogue()
        write(toy, "docs/guidelines/patterns.yaml", catalogue)
        bumped = shipped.replace("Each module, type and function", "Every module, type and function")
        domain = GuidelinesDomain(context(toy))
        plan = domain.plan_adopt(bumped, catalogue)
        assert {s.id: s.standing for s in plan.standings}[SRP] == "updatable"
        assert plan.status == "nothing"
        with pytest.raises(DefaultsRefused, match="current"):
            domain.plan_adopt(bumped, catalogue, update=(OCP,))
        plan = domain.plan_adopt(bumped, catalogue, update=(SRP,))
        assert plan.updated == (SRP,)
        domain.write_adoption(plan)
        assert "Every module, type and function" in (toy / RULESET_REL).read_text()


class TestOneWriter:
    def test_the_starter_writes_what_fresh_defaults_writes(self, toy):
        manifest = Manifest.locate(toy)
        text = docs_domain.default_ruleset(manifest, "invariant-docs/backlog.rst")
        fresh = fresh_defaults(text, docs_domain.default_catalogue(),
                               location="invariant-docs/guidelines/ruleset.yaml")
        starter = docs_domain.plan_starter(manifest, project="toy", base=toy,
                                           docs="invariant-docs/architecture")
        assert starter.ruleset == ("invariant-docs/guidelines/ruleset.yaml", fresh.text)
        assert starter.catalogue == (fresh.catalogue_rel, fresh.catalogue_text)


# -- the edges: refusals, the line editor's limits, the map's changes ----------------------------


DAY = datetime.date(2026, 11, 22)


class TestEditRefusals:
    @pytest.mark.parametrize(("kwargs", "said"), [
        ({"path": "/etc/passwd"}, "project-relative"),
        ({"fingerprint": "XYZ"}, "twelve lowercase hex"),
        ({"by": " "}, "--by and --reason"),
    ])
    def test_what_except_refuses(self, repo, kwargs, said):
        values = {"path": "app/m.py", "fingerprint": None, "by": "o", "reason": "r"} | kwargs
        path = values.pop("path")
        with pytest.raises(EditRefused, match=said):
            GuidelinesDomain(context(repo)).plan_except(SRP, path, today=DAY, **values)

    def test_an_engine_guideline_names_its_rule(self, repo):
        write(repo, RULESET_REL, RULESET + """\
  - id: solid.engine
    section: solid
    title: Engine
    statement: The engine answers it.
    severity: blocker
    phases: [review]
    applies_to: ["**/*.py"]
    check: {kind: engine, rule: privacy.clean}
""")
        with pytest.raises(EditRefused, match="invariant override privacy.clean"):
            GuidelinesDomain(context(repo)).plan_except(
                "solid.engine", "app/m.py", by="o", reason="r", today=DAY)

    def test_no_ruleset_is_refused_naming_adopt(self, tmp_path):
        root = tmp_path / "bare"
        git_project(root, {"version": 1, "project": PYTHON})
        with pytest.raises(EditRefused, match="guidelines adopt"):
            GuidelinesDomain(context(root)).plan_loosen(
                OCP, to="warn", by="o", reason="r", today=DAY)

    def test_a_ruleset_that_does_not_load_is_refused(self, repo):
        write(repo, RULESET_REL, RULESET.replace("severity: blocker", "severity: loud", 1))
        with pytest.raises(EditRefused):
            GuidelinesDomain(context(repo)).plan_loosen(
                OCP, to="warn", by="o", reason="r", today=DAY)

    def test_a_level_that_is_no_lowering_is_refused(self, repo):
        with pytest.raises(EditRefused, match="not a level"):
            GuidelinesDomain(context(repo)).plan_loosen(
                OCP, to="loud", by="o", reason="r", today=DAY)

    def test_an_unknown_rule_and_a_map_absent_are_refused(self, repo):
        domain = GuidelinesDomain(context(repo))
        with pytest.raises(EditRefused, match="no guideline"):
            domain.plan_loosen("solid.nope", to="warn", by="o", reason="r", today=DAY)
        write(repo, RULESET_REL, RULESET.split("layers:\n")[0] + "rules:\n"
              + RULESET.split("rules:\n", 1)[1])
        with pytest.raises(EditRefused, match="no layer map"):
            GuidelinesDomain(context(repo)).plan_loosen(
                "layers", to="warn", by="o", reason="r", today=DAY)

    def test_an_inline_list_is_added_by_hand(self, repo, capsys, terminal, monkeypatch):
        write(repo, RULESET_REL, RULESET.replace(
            '    applies_to: ["**/*.py"]\n',
            '    applies_to: ["**/*.py"]\n    exceptions: [{path: x.py, reason: r, '
            'ruled: {by: o, date: "2026-01-01", reason: r}}]\n', 1))
        planned = GuidelinesDomain(context(repo)).plan_except(
            SRP, "app/m.py", by="o", reason="r", today=DAY)
        assert planned.after is None and "path" in planned.entry
        with pytest.raises(EditRefused, match="in place"):
            GuidelinesDomain(context(repo)).write_edit(planned)
        monkeypatch.chdir(repo)
        terminal("y")
        code, text = run(capsys, "guidelines", "except", SRP, "app/m.py", "--by", "o", "--reason", "r")
        assert code == 1 and "by hand" in text

    def test_an_empty_inline_list_becomes_a_block(self, repo):
        write(repo, RULESET_REL, RULESET.replace(
            '    applies_to: ["**/*.py"]\n', '    applies_to: ["**/*.py"]\n    exceptions: []\n', 1))
        domain = GuidelinesDomain(context(repo))
        domain.write_edit(domain.plan_except(SRP, "app/m.py", by="o", reason="r", today=DAY))
        (exc,) = parse((repo / RULESET_REL).read_text()).by_id(SRP).exceptions
        assert exc.path == "app/m.py"

    def test_a_second_exception_joins_the_list(self, repo):
        domain = GuidelinesDomain(context(repo))
        domain.write_edit(domain.plan_except(SRP, "app/m.py", by="o", reason="r", today=DAY))
        domain = GuidelinesDomain(context(repo))
        domain.write_edit(domain.plan_except(SRP, "app/n.py", by="o", reason="s", today=DAY))
        paths = [e.path for e in parse((repo / RULESET_REL).read_text()).by_id(SRP).exceptions]
        assert paths == ["app/m.py", "app/n.py"]

    def test_a_held_ruling_is_replaced_by_the_lowering(self, repo):
        write(repo, RULESET_REL, RULESET.replace(
            "title: Open/closed\n", "title: Open/closed\n    ruled: {by: x, date: '2026-01-01', "
            "reason: older}\n", 1))
        domain = GuidelinesDomain(context(repo))
        domain.write_edit(domain.plan_loosen(OCP, to="warn", by="o", reason="new", today=DAY))
        assert parse((repo / RULESET_REL).read_text()).by_id(OCP).ruled.reason == "new"


class TestTheLineEditor:
    def test_a_missing_rule_list_is_unreadable(self):
        with pytest.raises(guidelines.RulesetError):
            guidelines.add_exception("version: 2\nsections: []\n", {
                "path": "a", "reason": "r", "ruled": USER}, rule=SRP)

    def test_a_list_that_is_not_a_list_is_added_by_hand(self):
        text = RULESET.replace('    applies_to: ["**/*.py"]\n',
                               '    applies_to: ["**/*.py"]\n    exceptions: oops\n', 1)
        with pytest.raises(guidelines.EditUnwritable):
            guidelines.add_exception(text, {"path": "a", "reason": "r", "ruled": USER}, rule=SRP)

    def test_a_map_written_inline_is_not_edited(self):
        text = RULESET.replace(
            '  map:\n    - id: inner\n      title: Inner\n      paths: ["app/**"]\n'
            '    - id: outer\n      title: Outer\n      paths: ["cli/**"]\n',
            '  map: [{id: inner, title: Inner, paths: ["app/**"]}]\n')
        with pytest.raises(guidelines.RulesetError):
            guidelines.add_exception(text, {"path": "a", "reason": "r", "ruled": USER}, layer="inner")

    def test_a_layer_map_with_no_severity_gains_one(self):
        edit = guidelines.lower_severity(RULESET, USER)
        data = yaml.safe_load(edit.text)
        assert data["layers"]["severity"] == "warn" and data["layers"]["ruled"]["by"] == "owner"

    def test_the_layer_map_severity_line_keeps_its_comment(self):
        text = RULESET.replace("  owner: user\n  map:", "  owner: user\n  severity: blocker  # ours\n  map:", 1)
        edit = guidelines.lower_severity(text, USER)
        assert "  severity: warn  # ours\n" in edit.text

    def test_replacing_a_rule_the_defaults_lack_is_refused(self):
        with pytest.raises(guidelines.RulesetError):
            guidelines.replace_default(RULESET, RULESET.replace(SRP, "solid.other"), SRP)

    def test_a_map_or_rule_without_origin_has_no_standing(self):
        assert guidelines.default_standings(RULESET, RULESET) == ()


class TestTheMapsChanges:
    def changes(self, after: str) -> tuple[str, ...]:
        return guidelines.unruled_changes(parse(RULESET), parse(after))

    def test_a_dropped_retitled_or_moved_layer_needs_a_ruling(self):
        dropped = RULESET.replace('    - id: outer\n      title: Outer\n      paths: ["cli/**"]\n', "")
        assert "dropped" in self.changes(dropped)[0]
        assert "retitled" in self.changes(RULESET.replace("title: Outer", "title: Outside"))[0]
        moved = RULESET.replace(
            '    - id: inner\n      title: Inner\n      paths: ["app/**"]\n'
            '    - id: outer\n      title: Outer\n      paths: ["cli/**"]\n',
            '    - id: outer\n      title: Outer\n      paths: ["cli/**"]\n'
            '    - id: inner\n      title: Inner\n      paths: ["app/**"]\n')
        assert "moved" in self.changes(moved)[0]

    def test_the_map_lowered_or_made_the_projects_needs_a_ruling(self):
        lowered = RULESET.replace("  owner: user\n  map:", "  owner: user\n  severity: warn\n  map:")
        assert "lowered" in self.changes(lowered)[0]
        made = RULESET.replace("  owner: user\n  map:", "  owner: project\n  map:")
        assert "made the project's" in self.changes(made)[0]
        moved = RULESET.replace("  owner: user\n  map:", "  owner: user\n  evidence: judgement\n  map:")
        assert "evidence" in self.changes(moved)[0]

    def test_the_map_removed_needs_a_ruling(self):
        text = RULESET.split("layers:\n")[0] + "rules:\n" + RULESET.split("rules:\n", 1)[1]
        assert "removed" in self.changes(text)[0]

    def test_with_a_new_ruled_the_map_may_change(self):
        text = RULESET.replace("  owner: user\n  map:", "  owner: user\n  severity: warn\n"
                               "  ruled: {by: owner, date: '2026-11-22', reason: r}\n  map:")
        assert self.changes(text) == ()

    def test_a_layer_exception_needs_its_own_ruling(self):
        text = RULESET.replace('      paths: ["app/**"]\n', '      paths: ["app/**"]\n'
                               '      exceptions:\n        - path: app/x.py\n          reason: r\n'
                               "          ruled: {by: o, date: '2026-11-22', reason: r}\n", 1)
        assert self.changes(text) == ()

    def test_a_rule_made_the_projects_or_with_a_changed_check_needs_a_ruling(self):
        made = RULESET.replace("    owner: user\n    phases", "    owner: project\n    phases", 1)
        assert "made the project's" in self.changes(made)[0]
        checked = RULESET.replace("      evidence: citation\n", "      evidence: either\n", 1)
        assert "check changed" in self.changes(checked)[0]
        phases = RULESET.replace("phases: [design, review, landing]", "phases: [review]", 1)
        assert "phases loses" in self.changes(phases)[0]


class TestTheHookEdges:
    def test_an_edit_that_does_not_read_as_yaml_and_names_ruled_is_refused(self, repo):
        path = repo / RULESET_REL
        judged = gate(context(repo, payload=edit(path, "version: 2\n", "version: [\nruled: x\n")),
                      "config.rulings_by_user")
        assert judged.blocks

    def test_an_edit_that_does_not_read_as_yaml_and_names_nothing_passes(self, repo):
        path = repo / RULESET_REL
        judged = gate(context(repo, payload=edit(path, "version: 2\n", "version: [\n")),
                      "config.rulings_by_user")
        assert judged.verdict.ok

    def test_a_ruleset_that_cannot_be_read_names_no_catalogue(self, repo):
        write(repo, RULESET_REL, "version: [\n")
        other = write(repo, "docs/guidelines/patterns.yaml", "a: 1\n")
        judged = gate(context(repo, payload=edit(other, "a: 1\n", "ruled: x\n")),
                      "config.rulings_by_user")
        assert judged.verdict.skipped


class TestTheGateEdges:
    def test_user_rules_ruled_fails_on_a_ruleset_that_does_not_load(self, repo):
        write(repo, RULESET_REL, RULESET.replace("severity: blocker", "severity: loud", 1))
        verdict = gate(context(repo), "guidelines.user_rules_ruled").verdict
        assert not verdict.ok and "cannot be compared" in verdict.message

    def test_user_rules_ruled_fails_with_no_base(self, repo):
        ctx = context(repo)
        ctx.session["base"] = ""
        assert not gate(ctx, "guidelines.user_rules_ruled").verdict.ok

    def test_a_base_with_no_ruleset_admits_every_rule(self, tmp_path):
        root = tmp_path / "fresh"
        git_project(root, {"version": 1, "project": PYTHON})
        git(root, "checkout", "-q", "-b", "work")
        write(root, RULESET_REL, RULESET)
        write(root, ".claude/invariant.yaml", yaml.safe_dump(
            {"version": 1, "project": PYTHON, "guidelines": {"ruleset": RULESET_REL}}))
        verdict = gate(context(root), "guidelines.user_rules_ruled").verdict
        assert verdict.ok and "every rule is new" in verdict.message

    def test_blocking_verdicts_fails_on_a_ruleset_that_does_not_load(self, repo):
        write(repo, RULESET_REL, RULESET.replace("severity: blocker", "severity: loud", 1))
        pipe = {"methodology": "tdd", "state": "reviewing", "ruleset": None, "evidence": {}}
        assert not gate(context(repo, pipe=pipe), "guidelines.blocking_verdicts").verdict.ok

    def test_blocking_verdicts_with_no_ruleset_is_not_applicable(self, tmp_path):
        root = tmp_path / "bare"
        git_project(root, {"version": 1, "project": PYTHON})
        pipe = {"methodology": "tdd", "state": "reviewing", "evidence": {}}
        assert gate(context(root, pipe=pipe), "guidelines.blocking_verdicts").verdict.skipped


class TestAdoptEdges:
    def test_it_merges_into_an_existing_ruleset_and_baselines_the_derived_rules(
        self, repo, capsys, terminal, monkeypatch
    ):
        monkeypatch.chdir(repo)
        no_map = RULESET.split("layers:\n")[0] + "rules:\n" + RULESET.split("rules:\n", 1)[1]
        write(repo, RULESET_REL, no_map)
        write(repo, "app/m.py", "import cli.x\n")
        write(repo, "cli/x.py", "y = 1\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "map")
        terminal("y")
        code, text = run(capsys, "guidelines", "adopt", "--layer", "entities=app/**",
                         "--layer", "frameworks-drivers=cli/**")
        assert code == 0, text
        manifest = Manifest.locate(repo)
        assert manifest.get("guidelines.baseline") == "docs/guidelines/baseline.txt"
        baseline = (repo / "docs/guidelines/baseline.txt").read_text()
        assert "layers.entities-depends-inward" in baseline
        assert parse_with_catalogue(repo).by_id("patterns.named-or-declined") is not None

    def test_layer_must_name_id_and_glob(self, toy, capsys, terminal):
        terminal("y")
        code, text = run(capsys, "guidelines", "adopt", "--layer", "entities")
        assert code != 0 and "<layer id>=<glob>" in text

    def test_an_unknown_layer_id_is_refused(self, toy, capsys, terminal):
        terminal("y")
        code, _text = run(capsys, "guidelines", "adopt", "--layer", "nowhere=app/**")
        assert code != 0

    def test_update_with_no_ruleset_is_refused(self, toy):
        manifest = Manifest.locate(toy)
        with pytest.raises(DefaultsRefused, match="no ruleset"):
            GuidelinesDomain(context(toy)).plan_adopt(
                docs_domain.default_ruleset(manifest, "docs/backlog.rst"),
                docs_domain.default_catalogue(), update=(SRP,))

    def test_a_ruleset_file_already_there_is_not_written_over(self, toy):
        write(toy, RULESET_REL, "version: 2\n")
        manifest = Manifest.locate(toy)
        with pytest.raises(DefaultsRefused, match="not written over"):
            GuidelinesDomain(context(toy)).plan_adopt(
                docs_domain.default_ruleset(manifest, "docs/backlog.rst"),
                docs_domain.default_catalogue())

    def test_a_ruleset_changed_since_the_plan_is_not_written_over(self, toy):
        manifest = Manifest.locate(toy)
        domain = GuidelinesDomain(context(toy))
        plan = domain.plan_adopt(docs_domain.default_ruleset(manifest, "docs/backlog.rst"),
                                 docs_domain.default_catalogue())
        write(toy, RULESET_REL, "version: 2\n")
        with pytest.raises(EditRefused, match="changed"):
            domain.write_adoption(plan)

    def test_json_dry_run_carries_the_plan(self, toy, capsys, no_terminal):
        code, text = run(capsys, "guidelines", "adopt", "--dry-run", "--json")
        assert code == 0 and '"status": "new"' in text

    def test_fresh_defaults_refuses_a_layer_the_defaults_lack(self, toy):
        manifest = Manifest.locate(toy)
        with pytest.raises(DefaultsRefused):
            fresh_defaults(docs_domain.default_ruleset(manifest, "docs/backlog.rst"),
                           docs_domain.default_catalogue(), location=RULESET_REL,
                           layers={"nowhere": ["x/**"]})


class TestDoctor:
    def test_a_newer_shipped_default_set_is_said_updatable(self, toy, capsys, terminal, monkeypatch):
        terminal("y")
        assert run(capsys, "guidelines", "adopt")[0] == 0
        from invariant.domains import docs as docs_module

        real = docs_module.default_ruleset

        def bumped(manifest, backlog):
            return real(manifest, backlog).replace("version: 1,", "version: 2,").replace(
                "Each module, type and function", "Every module, type and function")

        monkeypatch.setattr(docs_module, "default_ruleset", bumped)
        _code, text = run(capsys, "doctor")
        row = next(line for line in text.splitlines() if "guidelines defaults" in line)
        assert "1 default rule(s) updatable" in row and SRP in row

    def test_a_current_ruleset_has_no_row(self, toy, capsys, terminal):
        terminal("y")
        assert run(capsys, "guidelines", "adopt")[0] == 0
        _code, text = run(capsys, "doctor")
        assert "guidelines defaults" not in text


# -- ruling 13: a paused rule (severity off) and a retired one -------------------------------


class TestRuling13:
    RULE = "guidelines.user_rules_ruled"

    def test_loosen_off_writes_severity_off_with_ruled_and_the_rule_is_not_owed(self, repo):
        domain = GuidelinesDomain(context(repo))
        domain.write_edit(domain.plan_loosen(OCP, to="off", by="owner", reason="paused", today=DAY))
        ruleset = parse((repo / RULESET_REL).read_text())
        assert ruleset.by_id(OCP) is None
        paused = ruleset.any_rule(OCP)
        assert paused.severity == "off" and paused.ruled == guidelines.Ruling("owner", DAY.isoformat(), "paused")
        facts = TestNa().facts(na=lambda rule, condition, files: NaAnswer(rule.id, condition.id, False, "code"))
        assert [r.id for r in verdicts.owed(ruleset, facts)] == [SRP]
        assert gate(context(repo), self.RULE).verdict.ok

    def test_off_without_a_new_ruled_fails(self, repo):
        write(repo, RULESET_REL, RULESET.replace(
            "title: Open/closed\n    statement: One reason to change.\n    severity: blocker",
            "title: Open/closed\n    statement: One reason to change.\n    severity: off"))
        verdict = gate(context(repo), self.RULE).verdict
        assert not verdict.ok and OCP in verdict.message

    def test_retire_writes_a_retired_entry_and_removes_the_rule(self, repo):
        domain = GuidelinesDomain(context(repo))
        domain.write_edit(domain.plan_retire(OCP, by="owner", reason="not ours", today=DAY))
        text = (repo / RULESET_REL).read_text()
        ruleset = parse(text)
        assert ruleset.any_rule(OCP) is None
        (retired,) = ruleset.retired
        assert (retired.id, retired.ruled.reason) == (OCP, "not ours")
        assert text.startswith("# The project's ruleset; this comment is kept by every edit.\n")
        assert gate(context(repo), self.RULE).verdict.ok

    def test_a_removal_without_a_retired_entry_fails(self, repo):
        write(repo, RULESET_REL, RULESET.split(f"  - id: {OCP}")[0])
        verdict = gate(context(repo), self.RULE).verdict
        assert not verdict.ok and "removed" in verdict.message

    def test_a_removal_is_ruled_by_its_new_retired_entry_alone(self):
        retired = "retired:\n  - id: solid.gone\n    ruled: {by: o, date: '2026-01-01', reason: r}\n"
        base = parse(RULESET + retired)
        removed = RULESET.split(f"  - id: {OCP}")[0]
        (said,) = guidelines.unruled_changes(base, parse(removed + retired))
        assert OCP in said and "retire" in said
        ruled = retired + f"  - id: {OCP}\n    ruled: {{by: o, date: '2026-11-22', reason: new}}\n"
        assert guidelines.unruled_changes(base, parse(removed + ruled)) == ()

    def test_the_commands_at_a_terminal(self, repo, capsys, terminal, monkeypatch):
        monkeypatch.chdir(repo)
        terminal("y", "y")
        code, text = run(capsys, "guidelines", "loosen", SRP, "--to", "off", "--by", "o", "--reason", "r")
        assert code == 0, text
        code, text = run(capsys, "guidelines", "retire", OCP, "--by", "o", "--reason", "r")
        assert code == 0, text
        ruleset = parse((repo / RULESET_REL).read_text())
        assert ruleset.any_rule(SRP).severity == "off" and ruleset.any_rule(OCP) is None

    def test_retire_refuses_without_a_terminal(self, repo, capsys, no_terminal, monkeypatch):
        monkeypatch.chdir(repo)
        code, _text = run(capsys, "guidelines", "retire", OCP, "--by", "o", "--reason", "r")
        assert code == 1

    def test_a_derived_rule_is_not_retired(self, repo):
        with pytest.raises(EditRefused, match="loosen layers --to off"):
            GuidelinesDomain(context(repo)).plan_retire(
                "layers.inner-depends-inward", by="o", reason="r", today=DAY)

    def test_an_unknown_rule_is_not_retired(self, repo):
        with pytest.raises(EditRefused, match="no guideline"):
            GuidelinesDomain(context(repo)).plan_retire("solid.nope", by="o", reason="r", today=DAY)

    def test_a_paused_rule_can_be_raised_and_excepted(self, repo):
        domain = GuidelinesDomain(context(repo))
        domain.write_edit(domain.plan_loosen(OCP, to="off", by="o", reason="r", today=DAY))
        planned = GuidelinesDomain(context(repo)).plan_except(OCP, "app/m.py", by="o", reason="r", today=DAY)
        assert planned.after is not None
        with pytest.raises(EditRefused, match="already"):
            GuidelinesDomain(context(repo)).plan_loosen(OCP, to="off", by="o", reason="r", today=DAY)

    def test_adopt_re_adds_neither(self, toy, capsys, terminal):
        terminal("y")
        assert run(capsys, "guidelines", "adopt")[0] == 0
        domain = GuidelinesDomain(context(toy))
        domain.write_edit(domain.plan_loosen(SRP, to="off", by="o", reason="r", today=DAY))
        domain = GuidelinesDomain(context(toy))
        domain.write_edit(domain.plan_retire(OCP, by="o", reason="r", today=DAY))
        manifest = Manifest.locate(toy)
        plan = GuidelinesDomain(context(toy)).plan_adopt(
            docs_domain.default_ruleset(manifest, "docs/backlog.rst"), docs_domain.default_catalogue())
        assert plan.status == "nothing" and OCP not in plan.rules and SRP not in plan.rules
        assert {s.id: s.standing for s in plan.standings}[SRP] == "diverged"

    def test_the_hook_refuses_an_agent_writing_a_retired_entry(self, repo):
        path = repo / RULESET_REL
        added = "retired:\n  - id: solid.gone\n    ruled: {by: o, date: '2026-11-22', reason: r}\n"
        judged = gate(context(repo, payload={"tool_name": "Write", "tool_input": {
            "file_path": str(path), "content": RULESET + added}}), "config.rulings_by_user")
        assert judged.blocks and "retired[solid.gone].ruled" in judged.verdict.message

    def test_version_1_refuses_off(self):
        text = RULESET.replace("version: 2", "version: 1").split("layers:\n")[0] + "rules:\n" + \
            RULESET.split("rules:\n", 1)[1].replace("    owner: user\n", "").replace(
                "severity: blocker", "severity: off", 1)
        with pytest.raises(guidelines.RulesetError, match="off"):
            parse(text)

    def test_a_retired_id_still_declared_is_refused(self):
        text = RULESET + f"retired:\n  - id: {OCP}\n    ruled: {{by: o, date: '2026-01-01', reason: r}}\n"
        with pytest.raises(guidelines.RulesetError, match="retired"):
            parse(text)

    def test_a_retired_entry_needs_its_ruling(self):
        with pytest.raises(guidelines.RulesetError, match="ruled"):
            parse(RULESET + "retired:\n  - id: solid.gone\n")

    def test_a_paused_rules_baseline_entries_are_known(self):
        ruleset = parse(RULESET.replace("  owner: user\n  map:", "  owner: user\n  severity: off\n"
                                        "  ruled: {by: o, date: '2026-01-01', reason: r}\n  map:"))
        entry = guidelines.BaselineEntry("layers.inner-depends-inward", "app/m.py", "abcdefabcdef", 1)
        assert guidelines.baseline_rule_problems([entry], ruleset) == []
        assert ruleset.by_id("layers.inner-depends-inward") is None
