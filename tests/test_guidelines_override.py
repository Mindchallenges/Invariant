"""Override by guideline id (IDEA_031 A3.7).

ADR-012 ruling 4 and D12, DN-15, and manifest.md's four properties of the
override token. `invariant override <guideline-id> --reason` grants one
evaluation of the family gate, `guidelines.mechanical_clean`, in which that
guideline's new blocker findings are reported as passed by override, naming
the grant, and do not block. Every other finding stands: a baselined, warn,
excepted or unanswerable finding, and every other guideline's.

- The grant is validated against the ruleset of the tree in hand, and a grant
  nothing could ever spend is refused.
- `core/rules` reads the grant and never spends it. The CLI spends it, at a
  `pipeline advance` that succeeds while the guideline would otherwise have
  blocked, and records the spend in `session.overrides` with surface `cli`.
- `pipeline gates` and `check` never spend one, and an unspent grant is named
  in the verdict and in `session status`.
- `guidelines.baseline_only_shrinks` is not overridable by guideline id.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest
from test_cli import out, run
from test_domains import git_project
from test_guidelines_domain import RULESET_REL, write
from test_guidelines_findings import PYTHON, RULESET, context
from test_guidelines_ratchet import (
    BASELINE_REL,
    RECORDED,
    commit,
    entry_line,
    repo,  # noqa: F401 - a fixture, requested by name below
    sh,
    shrinks,
    stamp,
)

from invariant.cli.conformance import replay
from invariant.cli.main import _spend_guideline_overrides
from invariant.core import findings
from invariant.core.conformance import Step, load_named, status_of
from invariant.core.errors import UsageError
from invariant.core.findings import Finding
from invariant.core.manifest import Level, Manifest
from invariant.core.rules import Evaluated, RuleContext, Verdict, evaluate
from invariant.domains.session import SessionDomain

RULE = "guidelines.mechanical_clean"
PRINT = "style.no-print"
TODO = "style.no-todo"
LAYERS = "layers.app-never-imports-tools"
REASON = "the demo prints on purpose"


def grant(rule: str = PRINT, reason: str = REASON) -> dict:
    """A grant as `session.pending_overrides` holds one."""
    return {"rule": rule, "reason": reason, "calls": 1, "granted": 0.0}


def clean_with(ctx, *grants):
    ctx.session["pending_overrides"] = list(grants)
    (result,) = evaluate([RULE], ctx)
    return result


@pytest.fixture
def tree(tmp_path):
    write(tmp_path, RULESET_REL, RULESET)
    return tmp_path


# -- the arithmetic, in core/findings ------------------------------------------------


def finding(rule=PRINT, *, status="new", severity="blocker", note="") -> Finding:
    return Finding(
        rule=rule, kind="pattern", severity=severity, path="app/m.py",
        status=status, line=1, fingerprint="abcdefabcdef", note=note,
    )


class TestOverridden:
    def test_a_new_blocker_finding_of_a_granted_guideline_is_overridden(self):
        (found,) = findings.overridden([finding()], {PRINT: REASON})
        assert found.status == "overridden"
        assert not found.counts and not found.blocks
        assert REASON in found.note

    def test_overridden_is_a_status_a_finding_can_have(self):
        assert "overridden" in findings.STATUSES

    def test_its_record_is_a_pass_named_overridden(self):
        (found,) = findings.overridden([finding()], {PRINT: REASON})
        record = found.record("abcdefabcdef")
        assert (record["status"], record["finding"]) == ("pass", "overridden")

    def test_a_note_the_finding_carried_is_kept(self):
        (found,) = findings.overridden(
            [finding(note="tests/test_m.py did not change")], {PRINT: REASON}
        )
        assert "tests/test_m.py did not change" in found.note
        assert REASON in found.note

    @pytest.mark.parametrize("status", ["unanswerable", "baselined", "excepted"])
    def test_a_finding_that_is_not_new_is_unaffected(self, status):
        before = finding(status=status)
        assert findings.overridden([before], {PRINT: REASON}) == (before,)

    def test_a_warn_finding_is_unaffected(self):
        before = finding(severity="warn")
        assert findings.overridden([before], {PRINT: REASON}) == (before,)

    def test_another_guidelines_finding_is_unaffected(self):
        before = finding(rule=LAYERS)
        assert findings.overridden([before], {PRINT: REASON}) == (before,)

    def test_no_grant_changes_nothing(self):
        before = (finding(), finding(rule=LAYERS))
        assert findings.overridden(before, {}) == before


# -- the gate reads the grant ---------------------------------------------------------


class TestTheGateReadsTheGrant:
    def test_a_granted_guidelines_new_finding_passes_by_override(self, make_manifest, tree):
        write(tree, "app/m.py", "x = 1\nprint(x)\n")
        result = clean_with(context(make_manifest, tree, ["app/m.py"]), grant())
        assert result.verdict.ok and not result.verdict.skipped
        assert "1 passed by override" in result.verdict.message
        detail = result.verdict.detail
        assert "style.no-print app/m.py:2" in detail
        assert f"invariant override {PRINT}" in detail
        assert REASON in detail
        (record,) = [r for r in result.verdict.findings if r["rule"] == PRINT]
        assert (record["status"], record["finding"]) == ("pass", "overridden")

    def test_it_would_have_failed_without_the_grant(self, make_manifest, tree):
        write(tree, "app/m.py", "x = 1\nprint(x)\n")
        result = clean_with(context(make_manifest, tree, ["app/m.py"]))
        assert not result.verdict.ok

    def test_another_guidelines_blocker_still_fails(self, make_manifest, tree):
        write(tree, "tools/__init__.py", "")
        write(tree, "tools/helper.py", "X = 1\n")
        write(tree, "app/m.py", "from tools import helper\nprint(helper)\n")
        result = clean_with(context(make_manifest, tree, ["app/m.py"]), grant())
        assert not result.verdict.ok
        assert LAYERS in result.verdict.message
        assert "style.no-print app/m.py:2" not in result.verdict.message
        assert "1 passed by override" in result.verdict.detail

    def test_an_unanswerable_finding_is_not_covered(self, make_manifest, tree):
        """The strict direction: a grant passes what the guideline found, never
        a file nothing could answer for."""
        write(tree, "app/broken.py", "def (:\n")
        result = clean_with(
            context(make_manifest, tree, ["app/broken.py"]), grant(LAYERS)
        )
        assert not result.verdict.ok
        assert "unanswerable" in result.verdict.message

    def test_a_warn_finding_of_the_granted_guideline_stays_a_warn(self, make_manifest, tree):
        write(tree, "app/m.py", "x = 1  # TODO later\n")
        result = clean_with(context(make_manifest, tree, ["app/m.py"]), grant(TODO))
        assert result.verdict.ok
        assert "1 warn finding(s): style.no-todo app/m.py:1" in result.verdict.detail
        assert [r["finding"] for r in result.verdict.findings] == ["new"]

    def test_an_unused_grant_is_named(self, make_manifest, tree):
        """Loud while unspent: the verdict names a grant it did not use."""
        write(tree, "app/m.py", "x = 1\n")
        result = clean_with(context(make_manifest, tree, ["app/m.py"]), grant())
        assert result.verdict.ok
        assert f"override held for {PRINT}" in result.verdict.detail
        assert REASON in result.verdict.detail
        assert "passed by override" not in result.verdict.message

    def test_a_grant_naming_a_registered_rule_is_not_a_guidelines(
        self, make_manifest, tree
    ):
        write(tree, "app/m.py", "x = 1\n")
        result = clean_with(
            context(make_manifest, tree, ["app/m.py"]), grant("privacy.clean")
        )
        assert "privacy.clean" not in result.verdict.detail

    def test_a_malformed_held_entry_is_not_a_grant(self, make_manifest, tree):
        write(tree, "app/m.py", "print(x)\n")
        result = clean_with(context(make_manifest, tree, ["app/m.py"]), "style.no-print")
        assert not result.verdict.ok
        assert "override held" not in result.verdict.detail

    def test_the_fix_names_the_override_by_guideline_id(self, make_manifest, tree):
        write(tree, "app/m.py", "print(x)\n")
        result = clean_with(context(make_manifest, tree, ["app/m.py"]))
        assert any(f"invariant override {PRINT} --reason" in fix for fix in result.verdict.fixes)

    def test_evaluating_it_never_spends_the_grant(self, make_manifest, tree):
        write(tree, "app/m.py", "print(x)\n")
        ctx = context(make_manifest, tree, ["app/m.py"])
        clean_with(ctx, grant())
        assert ctx.session["pending_overrides"] == [grant()]


# -- guidelines.baseline_only_shrinks is not overridable by guideline id ------------


class TestTheBaselineGateIgnoresAGuidelineGrant:
    def test_growth_still_fails_with_a_grant_for_its_guideline(self, repo):  # noqa: F811
        write(
            repo,
            BASELINE_REL,
            "\n".join(
                [RECORDED.rstrip("\n"), entry_line(PRINT, "app/n.py", stamp("print(y)"), 1)]
            )
            + "\n",
        )
        commit(repo)
        ctx = RuleContext(
            manifest=Manifest.load(repo),
            cwd=repo,
            session={"files": [], "base": "master", "pending_overrides": [grant()]},
        )
        (result,) = evaluate(["guidelines.baseline_only_shrinks"], ctx)
        assert not result.verdict.ok
        assert "passed by override" not in result.verdict.message
        assert shrinks(repo).ok is False

    def test_a_base_it_cannot_read_names_the_forced_advance(self, repo):  # noqa: F811
        """Nothing spends a rule-id grant at a transition, so the fix names the
        route that can pass this gate: a forced advance, recorded."""
        sh(repo, "checkout", "-q", "master")
        sh(repo, "rm", "-q", BASELINE_REL)
        commit(repo, "lost")
        sh(repo, "checkout", "-q", "-B", "work")
        write(repo, BASELINE_REL, RECORDED)
        commit(repo)
        verdict = shrinks(repo)
        assert not verdict.ok
        assert not any("invariant override" in fix for fix in verdict.fixes)
        assert any("pipeline advance" in fix and "--force" in fix for fix in verdict.fixes)


# -- granting one ----------------------------------------------------------------------


ENGINE = """\
  - id: tests.green
    section: style
    title: The suite is green
    statement: The suite passes.
    severity: blocker
    phases: [landing]
    applies_to: ["**"]
    check: {kind: engine, rule: test.all_pass}
"""

JUDGEMENT = """\
  - id: style.judged
    section: style
    title: A judged rule
    statement: Someone judges it.
    severity: blocker
    phases: [review]
    applies_to: ["app/**"]
    check:
      kind: judgement
      evidence: citation
      scope: diff
      not_applicable_when:
        - id: read-only
          claim: The change only reads.
"""

REGISTRY_REL = "docs/guidelines/duplication.yaml"


WARN_REGISTRY_RULE = f"""\
  - id: quality.duplication
    section: style
    title: A touched file is not left in an open duplication entry
    statement: Resolve the entry in the same change.
    severity: warn
    phases: [review]
    applies_to: ["app/**"]
    check: {{kind: cochange, form: registry, registry: {REGISTRY_REL},
            outcomes: {{contained: warn}}}}
"""


def sessions_in(root, ruleset=RULESET, *, configured=True):
    if ruleset is not None:
        write(root, RULESET_REL, ruleset)
    write(root, REGISTRY_REL, "version: 1\nentries: []\n")
    body = {"version": 1, "project": PYTHON}
    if configured:
        body["guidelines"] = {"ruleset": RULESET_REL}
    sessions = SessionDomain(git_project(root, body))
    return sessions, sessions.start("override")


class TestGrantingOne:
    def test_a_guideline_of_the_ruleset_is_granted(self, tmp_path):
        sessions, session = sessions_in(tmp_path / "p")
        updated = sessions.grant_override(session.id, PRINT, REASON, cwd=tmp_path / "p")
        (held,) = updated.pending_overrides
        assert set(held) == {"rule", "reason", "calls", "granted"}
        assert (held["rule"], held["reason"], held["calls"]) == (PRINT, REASON, 1)

    def test_an_id_naming_neither_a_rule_nor_a_guideline_is_refused(self, tmp_path):
        sessions, session = sessions_in(tmp_path / "p")
        with pytest.raises(UsageError, match="no rule or guideline 'style.no-prnt'"):
            sessions.grant_override(session.id, "style.no-prnt", REASON, cwd=tmp_path / "p")

    def test_the_refusal_suggests_the_guideline_meant(self, tmp_path):
        sessions, session = sessions_in(tmp_path / "p")
        with pytest.raises(UsageError, match="did you mean 'style.no-print'"):
            sessions.grant_override(session.id, "style.no-prnt", REASON, cwd=tmp_path / "p")

    def test_with_no_ruleset_an_unknown_id_names_no_guideline_either(self, tmp_path):
        sessions, session = sessions_in(tmp_path / "p", configured=False)
        with pytest.raises(UsageError, match="no guidelines ruleset is configured"):
            sessions.grant_override(session.id, PRINT, REASON, cwd=tmp_path / "p")

    def test_a_ruleset_that_does_not_load_names_no_guideline(self, tmp_path):
        sessions, session = sessions_in(
            tmp_path / "p", RULESET.replace("severity: warn", "severity: loud")
        )
        with pytest.raises(UsageError, match="does not validate"):
            sessions.grant_override(session.id, PRINT, REASON, cwd=tmp_path / "p")

    def test_an_engine_guideline_is_refused_naming_its_rule(self, tmp_path):
        sessions, session = sessions_in(tmp_path / "p", RULESET + ENGINE)
        with pytest.raises(UsageError, match="invariant override test.all_pass"):
            sessions.grant_override(session.id, "tests.green", REASON, cwd=tmp_path / "p")

    def test_a_judgement_guideline_is_granted_for_the_verdict_gate(self, tmp_path):
        """A4.5: its family gate is guidelines.verdicts_complete
        (tests/test_guidelines_verdicts_override.py)."""
        sessions, session = sessions_in(tmp_path / "p", RULESET + JUDGEMENT)
        updated = sessions.grant_override(
            session.id, "style.judged", REASON, cwd=tmp_path / "p"
        )
        assert [o["rule"] for o in updated.pending_overrides] == ["style.judged"]

    def test_a_guideline_that_can_never_block_is_refused(self, tmp_path):
        sessions, session = sessions_in(tmp_path / "p")
        with pytest.raises(UsageError, match="never block"):
            sessions.grant_override(session.id, TODO, REASON, cwd=tmp_path / "p")

    def test_a_warn_registry_rule_is_refused(self, tmp_path):
        """A class's outcome never exceeds its rule's severity, so a warn
        registry rule can never block either."""
        sessions, session = sessions_in(tmp_path / "p", RULESET + WARN_REGISTRY_RULE)
        with pytest.raises(UsageError, match="never block"):
            sessions.grant_override(
                session.id, "quality.duplication", REASON, cwd=tmp_path / "p"
            )

    def test_more_than_one_call_is_refused_for_a_guideline(self, tmp_path):
        """Ruling 4: one-shot, for one evaluation of the family gate."""
        sessions, session = sessions_in(tmp_path / "p")
        with pytest.raises(UsageError, match="one evaluation"):
            sessions.grant_override(session.id, PRINT, REASON, calls=2, cwd=tmp_path / "p")

    def test_a_reason_is_still_required(self, tmp_path):
        sessions, session = sessions_in(tmp_path / "p")
        with pytest.raises(UsageError, match="requires --reason"):
            sessions.grant_override(session.id, PRINT, "  ", cwd=tmp_path / "p")

    def test_a_rule_id_grant_is_unchanged(self, tmp_path):
        sessions, session = sessions_in(tmp_path / "p")
        updated = sessions.grant_override(
            session.id, "privacy.clean", REASON, calls=3, cwd=tmp_path / "p"
        )
        assert updated.pending_overrides[0]["calls"] == 3

    def test_the_tree_in_hand_is_the_session_worktree(self, tmp_path):
        """The ruleset is read where the gate reads it: the session's tree."""
        root = tmp_path / "p"
        sessions, session = sessions_in(root)
        worktree = tmp_path / "wt"
        write(worktree, RULESET_REL, RULESET.replace("style.no-print", "style.no-echo"))
        session.worktree = str(worktree)
        sessions._save(session)
        sessions.grant_override(session.id, "style.no-echo", REASON, cwd=root)
        with pytest.raises(UsageError, match="no rule or guideline"):
            sessions.grant_override(session.id, PRINT, REASON, cwd=root)


# -- spending it, at the CLI ----------------------------------------------------------


def mechanical(level: Level, *, ok: bool, finding: str = "overridden", rule=PRINT) -> Evaluated:
    record = {
        "rule": rule, "kind": "pattern", "severity": "blocker",
        "status": "pass" if finding == "overridden" else "fail", "finding": finding,
        "citations": [{"path": "app/m.py", "lines": [2, 2]}],
        "fingerprint": "abcdefabcdef", "target": None, "note": "", "fix": "",
        "ruleset": "abcdefabcdef",
    }
    passed = ((rule, "app/m.py:2"),) if finding == "overridden" else ()
    return Evaluated(
        RULE, level, Verdict(ok=ok, message="m", findings=(record,), overridden=passed)
    )


@pytest.fixture
def held(tmp_path):
    sessions, session = sessions_in(tmp_path / "p")
    sessions.grant_override(session.id, PRINT, REASON, cwd=tmp_path / "p")
    return SimpleNamespace(sessions=sessions), sessions.require(session.id)


class TestSpendingIt:
    def test_a_passing_gate_at_block_spends_it_and_records_it(self, held, capsys):
        ctx, session = held
        _spend_guideline_overrides(ctx, session, [mechanical(Level.BLOCK, ok=True)])
        after = ctx.sessions.require(session.id)
        assert after.pending_overrides == []
        (spent,) = after.overrides
        assert (spent["rule"], spent["reason"], spent["surface"]) == (PRINT, REASON, "cli")
        err = capsys.readouterr().err
        assert f"[override] {PRINT}: passed deliberately" in err
        assert "app/m.py:2" in err

    def test_ask_counts_as_stopping_work(self, held):
        ctx, session = held
        _spend_guideline_overrides(ctx, session, [mechanical(Level.ASK, ok=True)])
        assert ctx.sessions.require(session.id).pending_overrides == []

    def test_at_warn_nothing_was_stopped_so_it_is_held(self, held, capsys):
        ctx, session = held
        _spend_guideline_overrides(ctx, session, [mechanical(Level.WARN, ok=True)])
        assert len(ctx.sessions.require(session.id).pending_overrides) == 1
        assert "held, not spent" in capsys.readouterr().err

    def test_a_gate_still_failing_decided_nothing_so_it_is_held(self, held, capsys):
        ctx, session = held
        _spend_guideline_overrides(ctx, session, [mechanical(Level.BLOCK, ok=False)])
        assert len(ctx.sessions.require(session.id).pending_overrides) == 1
        assert "still failed" in capsys.readouterr().err

    def test_a_gate_with_nothing_overridden_spends_nothing(self, held, capsys):
        ctx, session = held
        _spend_guideline_overrides(
            ctx, session, [mechanical(Level.BLOCK, ok=False, finding="new")]
        )
        assert len(ctx.sessions.require(session.id).pending_overrides) == 1
        assert capsys.readouterr().err == ""

    def test_a_grant_withdrawn_meanwhile_spends_nothing(self, held, capsys):
        ctx, session = held
        ctx.sessions.clear_override(session.id, PRINT)
        _spend_guideline_overrides(ctx, session, [mechanical(Level.BLOCK, ok=True)])
        assert ctx.sessions.require(session.id).overrides == []
        assert capsys.readouterr().err == ""

    def test_other_gates_are_not_read(self, held):
        ctx, session = held
        other = Evaluated("privacy.clean", Level.BLOCK, Verdict.clean("fine"))
        _spend_guideline_overrides(ctx, session, [other])
        assert len(ctx.sessions.require(session.id).pending_overrides) == 1


# -- end to end, through the CLI ----------------------------------------------------


def seeded(*, extra_before=(), extra_after=(), manifest=None):
    """The seeded-pattern scenario, with steps added around its landing."""
    scenario = load_named("seeded-pattern")
    steps = list(scenario.steps)
    (land,) = [i for i, s in enumerate(steps) if s.label == "land"]
    steps[land:land] = list(extra_before)
    steps += list(extra_after)
    return dataclasses.replace(
        scenario, steps=tuple(steps), manifest=manifest or scenario.manifest
    )


def cli(label, *argv):
    return Step(kind="cli", label=label, where="worktree", argv=tuple(argv))


def by_label(replayed):
    return {r.label: r for r in replayed.steps}


GRANT = cli("grant", "override", PRINT, "--reason", REASON)
BEFORE = cli("before", "session", "status")
AFTER = cli("after", "session", "status")


class TestEndToEnd:
    def test_the_grant_lets_the_landing_through_and_is_spent(self, tmp_path):
        replayed = replay(
            seeded(
                extra_before=(
                    GRANT,
                    cli("gates", "pipeline", "gates"),
                    cli("check", "check", RULE),
                    BEFORE,
                ),
                extra_after=(AFTER,),
            ),
            workdir=tmp_path / "r",
        )
        steps = by_label(replayed)
        assert steps["grant"].exit_code == 0
        # Asking never spends it.
        before = steps["before"].document["session"]
        assert [o["rule"] for o in before["pending_overrides"]] == [PRINT]
        assert before["overrides"] == []
        # The landing succeeds, and the gate passed by override.
        land = steps["land"]
        assert land.exit_code == 0
        (gate,) = [g for g in land.document["gates"] if g["rule"] == RULE]
        assert status_of(gate) == "pass"
        assert "passed by override" in gate["message"]
        # The landing spent it, and the spend is recorded.
        after = steps["after"].document["session"]
        assert after["pending_overrides"] == []
        (spent,) = after["overrides"]
        assert (spent["rule"], spent["surface"], spent["reason"]) == (PRINT, "cli", REASON)

    def test_without_the_grant_the_landing_is_refused(self, tmp_path):
        replayed = replay(seeded(), workdir=tmp_path / "r")
        assert by_label(replayed)["land"].exit_code != 0

    def test_where_the_gate_warns_the_grant_is_not_spent(self, tmp_path):
        manifest = dict(load_named("seeded-pattern").manifest)
        manifest.pop("enforcement")
        replayed = replay(
            seeded(extra_before=(GRANT,), extra_after=(AFTER,), manifest=manifest),
            workdir=tmp_path / "r",
        )
        steps = by_label(replayed)
        assert steps["land"].exit_code == 0
        after = steps["after"].document["session"]
        assert [o["rule"] for o in after["pending_overrides"]] == [PRINT]
        assert after["overrides"] == []

    def test_an_unknown_guideline_is_refused_at_the_command(self, tmp_path):
        replayed = replay(
            seeded(extra_before=(cli("typo", "override", "style.no-prnt", "--reason", "r"),)),
            workdir=tmp_path / "r",
        )
        assert by_label(replayed)["typo"].exit_code != 0


class TestTheCommandSaysWhatItGranted:
    @pytest.fixture
    def project(self, tmp_path, monkeypatch):
        root = tmp_path / "proj"
        root.mkdir()
        write(root, RULESET_REL, RULESET)
        git_project(root, {"version": 1, "project": PYTHON,
                           "guidelines": {"ruleset": RULESET_REL}})
        monkeypatch.chdir(root)
        assert run("session", "start", "override") == 0
        return root

    def test_the_grant_says_which_gate_spends_it(self, project, capsys):
        capsys.readouterr()
        assert run("override", PRINT, "--reason", REASON) == 0
        text = out(capsys)
        assert f"override granted for guideline {PRINT}" in text
        assert "one evaluation of guidelines.mechanical_clean" in text

    def test_status_names_an_unspent_guideline_grant(self, project, capsys):
        run("override", PRINT, "--reason", REASON)
        capsys.readouterr()
        run("session", "status")
        text = out(capsys)
        assert f"override   {PRINT}" in text
        assert "guideline" in text
