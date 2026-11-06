"""`guidelines verdicts show`: what `guidelines.verdicts_complete` sees
(IDEA_031 A4 increment 4).

A read-only command over the session's pipeline. For the change in hand it
names each judgement guideline the change owes a verdict and how it stands
(current, with a fail's severity; stale, with every predicate it now fails;
missing; or answered by the engine's mechanical N/A, naming a pass or fail
the N/A set aside), and every recorded verdict on a guideline the change no
longer owes, with why it is not owed.

It cannot disagree with the gate, because both ask one read,
`core.rules.read_standings`, and the gate's verdict is
`core.rules.standings_verdict` over that read. Why a recorded rule is not
owed is `core/verdicts.not_owed`, the one reading `owed` asks too.
"""

from __future__ import annotations

import pytest
from test_cli import payload, run
from test_domains import git_project
from test_guidelines_domain import RULESET_REL, _review, _stored, write
from test_guidelines_verdicts_complete import (
    DIGEST,
    OWED,
    PYTHON,
    RULE,
    RULESET,
    RULESET_OBJ,
    context,
    every_pass,
    facts,
    gate,
    green,
    pipeline,
    record,
    sh,
)

from invariant.core import verdicts
from invariant.core.rules import (
    RuleContext,
    StandingsRefused,
    read_standings,
    standings_verdict,
)

SELECTOR = "tests/test_m.py::test_open"


# -- why a rule is not owed: the one reading owed asks (DN-2, DN-3) ------------------------


class TestNotOwed:
    def by_id(self, rule_id):
        return RULESET_OBJ.by_id(rule_id)

    def test_an_owed_rule_has_no_reason(self):
        for rule_id in OWED:
            assert verdicts.not_owed(self.by_id(rule_id), facts()) is None

    def test_a_mechanical_rule_is_answered_by_the_engine(self):
        reason = verdicts.not_owed(self.by_id("style.no-print"), facts())
        assert "pattern guideline" in reason

    def test_a_design_only_rule_names_its_phases(self):
        reason = verdicts.not_owed(self.by_id("data.design-only"), facts())
        assert "phases (design)" in reason and "neither review, landing nor always" in reason

    def test_a_change_meeting_no_file_names_applies_to(self):
        reason = verdicts.not_owed(self.by_id("data.tested"), facts(touched=("docs/x.md",)))
        assert "applies_to (app/*.py)" in reason and "path exceptions" in reason

    def test_a_path_excepted_file_owes_nothing(self):
        reason = verdicts.not_owed(self.by_id("data.single-open"),
                                   facts(touched=("app/legacy.py",)))
        assert reason is not None and "applies_to" in reason

    def test_no_changed_set_is_named(self):
        reason = verdicts.not_owed(self.by_id("data.tested"), facts(touched=None))
        assert "could not be established" in reason

    def test_owed_is_exactly_the_rules_with_no_reason(self):
        f = facts()
        assert tuple(r.id for r in verdicts.owed(RULESET_OBJ, f)) == tuple(
            r.id for r in RULESET_OBJ.rules if verdicts.not_owed(r, f) is None
        )


# -- recorded verdicts on rules no longer owed ------------------------------------------------


class TestUnowed:
    def test_a_record_on_an_owed_rule_is_not_listed(self):
        assert verdicts.unowed(RULESET_OBJ, [record("data.single-open")], facts()) == ()

    def test_a_record_on_a_rule_the_change_no_longer_meets(self):
        (u,) = verdicts.unowed(
            RULESET_OBJ, [record("data.tested")], facts(touched=("docs/x.md",))
        )
        assert (u.rule, u.status, u.recorded) == ("data.tested", "pass", 1)
        assert "applies_to" in u.reason

    def test_a_record_on_a_rule_gone_from_the_ruleset(self):
        (u,) = verdicts.unowed(RULESET_OBJ, [record("data.gone", citations=[])], facts())
        assert u.reason == "no guideline 'data.gone' in the ruleset"

    def test_the_latest_status_and_how_many_were_recorded(self):
        records = [record("data.design-only", "fail"), record("data.design-only", "pass")]
        (u,) = verdicts.unowed(RULESET_OBJ, records, facts())
        assert (u.status, u.recorded) == ("pass", 2)
        assert "phases (design)" in u.reason

    def test_in_the_order_each_rule_was_first_recorded(self):
        records = [record("data.zeta"), record("data.design-only"), record("data.zeta")]
        assert [u.rule for u in verdicts.unowed(RULESET_OBJ, records, facts())] == [
            "data.zeta", "data.design-only",
        ]

    def test_a_record_naming_no_rule_is_not_listed(self):
        assert verdicts.unowed(RULESET_OBJ, ["junk", {"rule": 3}, {}], facts()) == ()

    def test_a_status_that_is_not_text_reads_as_none(self):
        (u,) = verdicts.unowed(RULESET_OBJ, [{"rule": "data.gone", "status": 7}], facts())
        assert u.status is None
        assert u.describe() == (
            "data.gone: an unreadable record, 1 recorded; not owed: "
            "no guideline 'data.gone' in the ruleset"
        )

    def test_its_description(self):
        (u,) = verdicts.unowed(RULESET_OBJ, [record("data.design-only")] * 2, facts())
        assert u.describe().startswith("data.design-only: pass, 2 recorded; not owed: its phases")


# -- a cited file edited after the verdict: the fix is a fresh verdict ----------------------------


class TestAnEditedCitation:
    def sub(self, blob):
        return verdicts.Submitted(
            rule="data.single-open", status="pass",
            citations=(verdicts.Cited("app/m.py", 2, 2, blob),),
        )

    def test_at_the_gate_it_asks_for_the_rule_again(self):
        rule = RULESET_OBJ.by_id("data.single-open")
        result = verdicts.check(self.sub("0" * 12), rule, facts(), submitting=False)
        (problem,) = result.problems
        assert "was 000000000000 when the verdict was recorded" in problem.message
        assert problem.fix == (
            "answer data.single-open again on the file as it is now: "
            "invariant guidelines verdict data.single-open"
        )

    def test_at_submission_the_blob_is_the_submitters_slip(self):
        rule = RULESET_OBJ.by_id("data.single-open")
        result = verdicts.check(self.sub("0" * 12), rule, facts(roles=("review",)))
        (problem,) = result.problems
        assert problem.fix == "leave the blob out; the engine records it"


# -- the one read the gate and the command share ------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    """The gate tests' repository: the ruleset and ``app/m.py`` on a work branch."""
    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, RULESET)
    write(root, "app/m.py", "x = 1\n")
    git_project(root, {"version": 1, "project": PYTHON, "guidelines": {"ruleset": RULESET_REL}})
    sh(root, "checkout", "-q", "-b", "work")
    return root


class TestTheRead:
    def test_no_ruleset_configured(self, tmp_path):
        root = tmp_path / "bare"
        git_project(root, {"version": 1, "project": PYTHON})
        read = read_standings(context(root))
        assert read.status == "no-ruleset" and read.failure is None
        assert standings_verdict(read).skipped

    def test_no_pipeline(self, repo):
        ctx = context(repo)
        ctx = RuleContext(manifest=ctx.manifest, cwd=repo, session=ctx.session, pipeline=None)
        read = read_standings(ctx)
        assert read.status == "no-pipeline" and read.ruleset == DIGEST
        assert standings_verdict(read).skipped

    def test_a_ruleset_that_does_not_load_is_unanswerable(self, repo):
        write(repo, RULESET_REL, RULESET.replace("severity: warn", "severity: loud"))
        read = read_standings(context(repo))
        assert read.status == "unanswerable" and not read.failure.ok
        assert standings_verdict(read) is read.failure

    def test_a_changed_set_that_cannot_be_established_is_unanswerable(self, repo):
        read = read_standings(context(repo, base=""))
        assert read.status == "unanswerable"
        assert "could not be established" in read.failure.message

    def test_it_carries_the_standings_the_unowed_and_the_digest(self, repo):
        write(repo, "app/m.py", "x = 1\ny = 2\n")
        runs = [green(SELECTOR)]
        recorded = every_pass(repo, runs)[:1] + [record("data.gone", citations=[])]
        read = read_standings(context(repo, pipe=pipeline(verdicts_=recorded, runs=runs)))
        assert read.status == "standings" and read.ruleset == DIGEST
        assert read.state == "reviewing"
        assert {s.rule: s.standing for s in read.standings} == {
            "data.single-open": verdicts.CURRENT,
            "data.tested": verdicts.MISSING,
            "style.explained": verdicts.MISSING,
        }
        assert [u.rule for u in read.unowed] == ["data.gone"]

    @pytest.mark.parametrize("answered", [0, 2, 3])
    def test_the_gate_is_the_verdict_over_the_same_read(self, repo, answered):
        write(repo, "app/m.py", "x = 1\ny = 2\n")
        runs = [green(SELECTOR)]
        recorded = every_pass(repo, runs)[:answered]
        ctx = context(repo, pipe=pipeline(verdicts_=recorded, runs=runs))
        assert gate(ctx).verdict == standings_verdict(read_standings(ctx))


# -- the command, through main ------------------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch, capsys):
    from test_domains import git_project

    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, RULESET)
    write(root, "app/m.py", "x = 1\n")
    git_project(root, {"version": 1, "project": PYTHON,
                       "guidelines": {"ruleset": RULESET_REL}})
    sh(root, "checkout", "-q", "-b", "work")
    monkeypatch.chdir(root)
    assert run("session", "start", "verdicts") == 0
    assert run("pipeline", "start", "--task", "t") == 0
    write(root, "app/m.py", "x = 1\ny = 2\n")
    _review(root)
    capsys.readouterr()
    return root


def show(capsys, *extra) -> tuple[int, str]:
    code = run("guidelines", "verdicts", "show", *extra)
    return code, capsys.readouterr().out


def cite(rule, status, first=2, last=2):
    return run("guidelines", "verdict", rule, "--status", status,
               "--cite", "app/m.py", str(first), str(last))


class TestTheCommand:
    def test_nothing_recorded_shows_every_owed_rule_missing(self, project, capsys):
        code, text = show(capsys)
        assert code == 0
        assert f"3 judgement guideline(s) owed a verdict by this change (ruleset {DIGEST}" in text
        assert "state reviewing" in text
        for rule_id in OWED:
            assert f"  missing  {rule_id}: no verdict" in text
        assert f"{RULE}: 3 of 3 owed" in text
        assert "try: invariant guidelines verdict data.single-open --status pass" in text

    def test_a_current_pass_and_a_current_warn_fail(self, project, capsys):
        assert cite("data.single-open", "pass") == 0
        assert cite("style.explained", "fail") == 0
        capsys.readouterr()
        code, text = show(capsys)
        assert code == 0
        assert "  current  data.single-open: pass" in text
        assert "  current  style.explained: fail at warn" in text
        assert "  missing  data.tested: no verdict" in text

    def test_a_current_blocker_fail_is_named_with_its_severity(self, project, capsys):
        assert cite("data.single-open", "fail") == 0
        capsys.readouterr()
        _code, text = show(capsys)
        assert "  current  data.single-open: fail at blocker" in text

    def test_a_stale_verdict_names_the_predicate_it_now_fails(self, project, capsys):
        assert cite("data.single-open", "pass") == 0
        write(project, "app/m.py", "x = 1\ny = 3\n")
        capsys.readouterr()
        _code, text = show(capsys)
        assert "  stale    data.single-open: the latest verdict no longer holds (P3:" in text

    def test_the_engine_answers_a_comment_only_change_and_sets_a_fail_aside(
        self, project, capsys
    ):
        assert cite("data.single-open", "fail") == 0
        write(project, "app/m.py", "x = 1  # explained\n")
        capsys.readouterr()
        _code, text = show(capsys)
        assert "  engine   data.single-open: na, answered by the engine (no-code holds" in text
        assert "the fail recorded on it is set aside" in text

    def test_a_recorded_verdict_no_longer_owed_is_listed(self, project, capsys):
        assert cite("data.single-open", "pass") == 0
        write(project, "app/m.py", "x = 1\n")
        write(project, "docs/notes.md", "prose\n")
        capsys.readouterr()
        _code, text = show(capsys)
        assert "no judgement guideline is owed a verdict by this change" in text
        assert "recorded on guidelines the change no longer owes:" in text
        assert "  data.single-open: pass, 1 recorded; not owed: no changed file" in text

    def test_it_exits_zero_when_the_gate_would_fail(self, project, capsys):
        code, text = show(capsys)
        assert code == 0 and "[warn] guidelines.verdicts_complete" in text

    def test_json_carries_each_standing_the_unowed_and_the_gate(self, project, capsys):
        assert cite("data.single-open", "pass") == 0
        capsys.readouterr()
        assert run("guidelines", "verdicts", "show", "--json") == 0
        body = payload(capsys)
        assert body["status"] == "standings"
        assert body["ruleset"] == DIGEST and body["state"] == "reviewing"
        owed = {s["rule"]: s for s in body["owed"]}
        assert list(owed) == list(OWED)
        assert owed["data.single-open"] == {
            "rule": "data.single-open", "severity": "blocker", "standing": "current",
            "status": "pass", "reason": "", "problems": [], "set_aside": None,
            "blocks": False, "warns": False, "override": None, "overridden": False,
        }
        assert owed["data.tested"]["standing"] == "missing" and owed["data.tested"]["blocks"]
        assert body["not_owed"] == []
        assert body["gate"]["rule"] == RULE and body["gate"]["ok"] is False
        assert body["gate"]["fixes"]

    def test_json_names_a_stale_verdicts_problems(self, project, capsys):
        assert cite("data.single-open", "pass") == 0
        write(project, "app/m.py", "x = 1\ny = 3\n")
        capsys.readouterr()
        assert run("guidelines", "verdicts", "show", "--json") == 0
        (stale,) = [s for s in payload(capsys)["owed"] if s["standing"] == "stale"]
        assert {p["predicate"] for p in stale["problems"]} == {"P3"}
        assert all(p["message"] and p["fix"] for p in stale["problems"])

    def test_json_lists_the_unowed(self, project, capsys):
        assert cite("data.single-open", "pass") == 0
        write(project, "app/m.py", "x = 1\n")
        write(project, "docs/notes.md", "prose\n")
        capsys.readouterr()
        assert run("guidelines", "verdicts", "show", "--json") == 0
        body = payload(capsys)
        assert body["owed"] == []
        (u,) = body["not_owed"]
        assert (u["rule"], u["status"], u["recorded"]) == ("data.single-open", "pass", 1)
        assert "applies_to" in u["reason"]

    def test_it_writes_nothing(self, project, capsys):
        assert cite("data.single-open", "pass") == 0
        before = _stored(project)
        tree = sorted(p.relative_to(project) for p in project.rglob("*") if ".git" not in p.parts)
        show(capsys)
        show(capsys, "--json")
        assert _stored(project) == before
        assert sorted(
            p.relative_to(project) for p in project.rglob("*") if ".git" not in p.parts
        ) == tree

    def test_it_answers_in_any_state(self, project, capsys):
        from invariant.core.manifest import Manifest
        from invariant.domains.pipeline import PipelineDomain
        from invariant.domains.session import SessionDomain

        manifest = Manifest.locate(project)
        session = SessionDomain(manifest).current(project)
        pipelines = PipelineDomain(manifest)
        current = pipelines.current(session.id)
        current.state = "green"
        pipelines._save(session.id, current)
        code, text = show(capsys)
        assert code == 0 and "state green" in text


class TestTheCommandWithoutStandings:
    def test_no_session_is_refused(self, tmp_path, monkeypatch, capsys):
        root = tmp_path / "nosession"
        root.mkdir()
        write(root, RULESET_REL, RULESET)
        git_project(root, {"version": 1, "project": PYTHON,
                           "guidelines": {"ruleset": RULESET_REL}})
        monkeypatch.chdir(root)
        assert run("guidelines", "verdicts", "show") == 1
        err = capsys.readouterr().err
        assert "no session is active here" in err
        assert "try: invariant session start <task-name>" in err

    def test_no_pipeline_says_nothing_is_owed(self, project, capsys):
        sid, pipelines = _review(project)
        raw = pipelines.store.read(pipelines.relpath(sid), {})
        raw["current"] = None
        pipelines.store.write(pipelines.relpath(sid), raw)
        code, text = show(capsys)
        assert code == 0
        assert text.strip() == "no pipeline is running for this session, so no verdict is owed"
        assert run("guidelines", "verdicts", "show", "--json") == 0
        body = payload(capsys)
        assert body["status"] == "no-pipeline" and body["owed"] == []
        assert body["gate"]["skipped"] is True

    def test_no_ruleset_configured_says_nothing_is_owed(self, tmp_path, monkeypatch, capsys):
        root = tmp_path / "bare"
        root.mkdir()
        git_project(root, {"version": 1, "project": PYTHON})
        monkeypatch.chdir(root)
        assert run("session", "start", "bare") == 0
        capsys.readouterr()
        code, text = show(capsys)
        assert code == 0
        assert text.strip() == "no guidelines ruleset configured; no verdict is owed"

    def test_a_ruleset_that_does_not_load_is_refused_with_the_gates_fix(self, project, capsys):
        write(project, RULESET_REL, RULESET.replace("severity: warn", "severity: loud"))
        assert run("guidelines", "verdicts", "show") == 1
        err = capsys.readouterr().err
        assert "no verdict standing can be shown: no verdict can be judged" in err
        assert "try:" in err


def test_the_refusal_renders_detail_and_fixes():
    exc = StandingsRefused("it broke", "because", ("do this", "or that"))
    assert str(exc) == (
        "no verdict standing can be shown: it broke\n  because\n  try: do this\n  try: or that"
    )
    assert exc.exit_code == 1
