"""The forbidden-model list and the window model (BUG_159, ADR-030).

``host.forbidden_models`` and ``host.window_model`` are configuration, each
null for the host adapter's default (D2). One comparison answers every check,
a case-insensitive substring of configured text (D3). The rule context derives
which audited agents the list forbids before it removes the audit record, and
that membership is the one fact of the record a rule reads (D4): the role
gates and the red proof fail on it (D5). Every window runs on ``--model`` and
a forbidden one is refused (D6); so is a dispatch whose asked model is
forbidden (D7).

The forbidden value is built from the adapter's own default, so no model name
is spelled here beyond what configuration would hold.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_domains import git_project
from test_oracle_start_session import (  # noqa: F401 - fixtures
    _launch_files_here,
    _scan_without_a_probe,
    macos_with_claude,
    project,
    successor,
)

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.errors import GateFailure, InvariantError, ManifestError
from invariant.core.manifest import Level, Manifest
from invariant.core.rules import (
    AUDIT_KEY,
    RuleContext,
    apply_acknowledgements,
    evaluate,
    forbidden_entry,
)
from invariant.domains.pipeline import PipelineDomain, unchecked_agents
from invariant.domains.session import SessionDomain
from invariant.hosts import claude_code_launch as launch
from invariant.hosts.claude_code import (
    FORBIDDEN_MODELS,
    WINDOW_MODEL,
    ClaudeCodeHost,
    forbidden_models,
    window_model,
)
from invariant.oracle import startsession

#: A value the adapter's default list forbids, and one it does not.
FORBIDDEN = f"family-{FORBIDDEN_MODELS[0].upper()}-5"
ALLOWED = "placeholder-model"
LIST = ("placeholder-entry", FORBIDDEN_MODELS[0])
DESIGN = "feature-architect"
REVIEW = "compliance-reviewer"


def _pipeline(agents: dict | object, ran=(DESIGN,), runs=()) -> dict:
    return {
        "state": "reviewing",
        "methodology": "tdd",
        "evidence": {"agents_run": list(ran), AUDIT_KEY: {"host": "h", "agents": agents},
                     "test_runs": list(runs)},
    }


def _entry(models=(), asked=None) -> dict:
    return {"tier": None, "asked": asked, "models": list(models), "runs": []}


def _ctx(m: Manifest, pipeline, forbidden=LIST) -> RuleContext:
    return RuleContext(manifest=m, cwd=m.root, pipeline=pipeline,
                       forbidden_models=tuple(forbidden))


@pytest.fixture
def m(tmp_path):
    return git_project(tmp_path / "proj", {
        "version": 1, "project": {"name": "demo"},
        "agents": {"roles": {"design": [DESIGN], "review": [REVIEW]}},
    })


# -- D3: the one comparison ------------------------------------------------------


class TestForbiddenEntry:
    def test_an_entry_inside_the_value_matches_ignoring_case(self):
        assert forbidden_entry(FORBIDDEN, LIST) == FORBIDDEN_MODELS[0]

    def test_a_value_holding_no_entry_matches_nothing(self):
        assert forbidden_entry(ALLOWED, LIST) is None

    @pytest.mark.parametrize("value", [None, "", 7, ["x"], {"a": 1}])
    def test_a_value_that_is_not_text_matches_nothing(self, value):
        assert forbidden_entry(value, LIST) is None

    @pytest.mark.parametrize("entries", [(), ("",), (None, 3)])
    def test_an_entry_that_is_not_text_forbids_nothing(self, entries):
        assert forbidden_entry(FORBIDDEN, entries) is None


# -- D4: the context's one read ---------------------------------------------------


class TestTheContext:
    def test_an_observed_model_the_list_holds_names_the_agent(self, m):
        ctx = _ctx(m, _pipeline({DESIGN: _entry([ALLOWED, FORBIDDEN])}))
        assert ctx.forbidden_agents == {DESIGN: FORBIDDEN}

    def test_the_audit_is_still_removed(self, m):
        ctx = _ctx(m, _pipeline({DESIGN: _entry([FORBIDDEN])}))
        assert AUDIT_KEY not in ctx.pipeline["evidence"]

    def test_the_asked_model_answers_when_none_was_observed(self, m):
        ctx = _ctx(m, _pipeline({DESIGN: _entry([], asked=FORBIDDEN)}))
        assert ctx.forbidden_agents == {DESIGN: FORBIDDEN}

    def test_an_observed_model_wins_over_the_asked_one(self, m):
        ctx = _ctx(m, _pipeline({DESIGN: _entry([ALLOWED], asked=FORBIDDEN)}))
        assert ctx.forbidden_agents == {}

    def test_no_model_known_forbids_nothing(self, m):
        ctx = _ctx(m, _pipeline({DESIGN: _entry()}))
        assert ctx.forbidden_agents == {}

    def test_an_empty_list_forbids_nothing(self, m):
        ctx = _ctx(m, _pipeline({DESIGN: _entry([FORBIDDEN])}), forbidden=())
        assert ctx.forbidden_agents == {}

    @pytest.mark.parametrize("agents", [
        "text", None, [1], {DESIGN: "text"}, {DESIGN: _entry([FORBIDDEN]) | {"models": "x"}},
    ])
    def test_a_shape_it_does_not_expect_is_no_fact(self, m, agents):
        ctx = _ctx(m, _pipeline(agents))
        assert ctx.forbidden_agents == {}

    def test_a_record_with_no_audit_or_no_pipeline_is_no_fact(self, m):
        assert _ctx(m, None).forbidden_agents == {}
        assert _ctx(m, {"evidence": "text"}).forbidden_agents == {}


# -- D5: the gates ----------------------------------------------------------------


def _verdict(m, rule, pipeline):
    (result,) = evaluate([rule], _ctx(m, pipeline))
    return result.verdict


class TestTheRoleGates:
    def test_a_member_observed_on_a_forbidden_model_fails_its_gate(self, m):
        verdict = _verdict(m, "agents.design_agent_ran",
                           _pipeline({DESIGN: _entry([FORBIDDEN])}))
        assert not verdict.ok
        assert verdict.message == "no design agent ran on an allowed model in this pipeline"
        assert f"{DESIGN} on {FORBIDDEN}" in verdict.detail
        assert verdict.fixes[0] == (
            "invariant pipeline advance <state> --force --reason '<why this work stands>'"
        )

    def test_the_review_gate_names_its_own_rule(self, m):
        verdict = _verdict(m, "agents.review_agent_ran",
                           _pipeline({REVIEW: _entry([FORBIDDEN])}, ran=(REVIEW,)))
        assert not verdict.ok
        assert "agents.review_agent_ran" in verdict.detail

    def test_a_member_on_an_allowed_model_passes(self, m):
        verdict = _verdict(m, "agents.design_agent_ran",
                           _pipeline({DESIGN: _entry([ALLOWED])}))
        assert verdict.ok

    def test_a_member_with_no_model_known_passes(self, m):
        verdict = _verdict(m, "agents.design_agent_ran", _pipeline({}))
        assert verdict.ok

    def test_one_allowed_member_fills_the_role(self, tmp_path):
        other = "another-architect"
        m = git_project(tmp_path / "two", {
            "version": 1, "project": {"name": "demo"},
            "agents": {"local": [other], "roles": {"design": [DESIGN, other]}},
        })
        verdict = _verdict(m, "agents.design_agent_ran", _pipeline(
            {DESIGN: _entry([FORBIDDEN]), other: _entry([ALLOWED])}, ran=(DESIGN, other)))
        assert verdict.ok and verdict.message == f"{other} ran in this pipeline"


RED = {"phase": "red", "passed": False, "executed": 1, "failed": 1, "exit_code": 1}


class TestTheRedProof:
    def test_a_red_run_in_a_pipeline_with_a_forbidden_agent_fails(self, m):
        verdict = _verdict(m, "test.new_test_fails", _pipeline(
            {"bug-reproducer": _entry([FORBIDDEN])}, ran=("bug-reproducer",), runs=[RED]))
        assert not verdict.ok
        assert verdict.message.startswith("a red run is on record, but an agent")
        assert "bug-reproducer on " in verdict.detail
        assert "test.new_test_fails" in verdict.detail
        assert "--force --reason" in verdict.fixes[0]

    def test_a_red_run_with_no_forbidden_agent_passes(self, m):
        verdict = _verdict(m, "test.new_test_fails", _pipeline(
            {"bug-reproducer": _entry([ALLOWED])}, runs=[RED]))
        assert verdict.ok


# -- D5: a forbidden model blocks in every profile (ruling 4) ---------------------


REPRO = "bug-reproducer"


def _profiled(tmp_path, profile: str, rules: dict | None = None) -> Manifest:
    """A project on ``profile``, this repository's ``standard`` among them, where
    the three gates a forbidden model blocks are configured below ``block``."""
    return git_project(tmp_path / profile, {
        "version": 1, "project": {"name": "demo"},
        "agents": {"roles": {"design": [DESIGN], "review": [REVIEW]}},
        "enforcement": {"profile": profile, "rules": rules or {}},
    })


def _evaluated(m, rule, pipeline):
    (result,) = evaluate([rule], _ctx(m, pipeline))
    return result


@pytest.mark.parametrize("profile", ["standard", "loose", "strict"])
class TestTheFloor:
    def test_a_forbidden_reproducer_blocks_the_red_proof_at_every_profile(
        self, tmp_path, profile
    ):
        m = _profiled(tmp_path, profile, {"test.new_test_fails": "warn"})
        assert m.rule_level("test.new_test_fails") is Level.WARN
        result = _evaluated(m, "test.new_test_fails", _pipeline(
            {REPRO: _entry([FORBIDDEN])}, ran=(REPRO,), runs=[RED]))
        assert result.level is Level.BLOCK and result.blocks and not result.warns

    @pytest.mark.parametrize("rule, agent", [
        ("agents.design_agent_ran", DESIGN), ("agents.review_agent_ran", REVIEW),
    ])
    def test_a_forbidden_design_or_review_agent_blocks_its_role_gate(
        self, tmp_path, profile, rule, agent
    ):
        m = _profiled(tmp_path, profile, {rule: "warn"})
        result = _evaluated(m, rule, _pipeline({agent: _entry([FORBIDDEN])}, ran=(agent,)))
        assert result.level is Level.BLOCK and result.blocks

    def test_an_ask_level_gate_is_raised_to_block(self, tmp_path, profile):
        m = _profiled(tmp_path, profile, {"test.new_test_fails": "ask"})
        result = _evaluated(m, "test.new_test_fails", _pipeline(
            {REPRO: _entry([FORBIDDEN])}, ran=(REPRO,), runs=[RED]))
        assert result.level is Level.BLOCK and not result.asks


class TestTheFloorLeavesEverythingElse:
    def test_the_same_gate_failing_for_another_reason_keeps_its_level(self, tmp_path):
        m = _profiled(tmp_path, "standard")
        result = _evaluated(m, "test.new_test_fails", _pipeline({REPRO: _entry([FORBIDDEN])},
                                                                ran=(REPRO,)))
        assert not result.verdict.ok and result.level is Level.WARN and result.warns

    def test_a_pass_keeps_its_level(self, tmp_path):
        m = _profiled(tmp_path, "standard")
        result = _evaluated(m, "test.new_test_fails", _pipeline(
            {REPRO: _entry([ALLOWED])}, ran=(REPRO,), runs=[RED]))
        assert result.verdict.ok and result.level is Level.WARN

    def test_a_gate_the_project_turned_off_stays_off(self, tmp_path):
        m = _profiled(tmp_path, "standard", {"test.new_test_fails": "off"})
        result = _evaluated(m, "test.new_test_fails", _pipeline(
            {REPRO: _entry([FORBIDDEN])}, ran=(REPRO,), runs=[RED]))
        assert result.level is Level.OFF and result.verdict.skipped

    def test_an_acknowledgement_does_not_pass_it(self, tmp_path):
        m = _profiled(tmp_path, "standard")
        pipeline = _pipeline({REPRO: _entry([FORBIDDEN])}, ran=(REPRO,), runs=[RED])
        pipeline["evidence"]["acknowledged"] = {"test.new_test_fails": "a reason"}
        (result,) = apply_acknowledgements(
            pipeline, evaluate(["test.new_test_fails"], _ctx(m, pipeline)))
        assert result.blocks and not result.verdict.skipped


class TestTheReproEdgeUnderThisRepositorysProfile:
    """The edge the bug-reproducer's red run satisfies, walked through the
    pipeline domain on ``standard``, where ``test.new_test_fails`` is warn."""

    def _bugfix_at_reproducing(self, tmp_path, model: str):
        m = _profiled(tmp_path, "standard")
        assert m.rule_level("test.new_test_fails") is Level.WARN
        session = SessionDomain(m).start("repro")
        pipelines = PipelineDomain(m, forbidden_models=LIST)
        pipelines.start(session.id, methodology="bugfix", task="t")
        record = pipelines.require(session.id)
        record.state = "reproducing"
        # The bugfix machine's red phase is `reproducing`.
        record.evidence["test_runs"] = [dict(RED, phase="reproducing")]
        pipelines._save(session.id, record)
        pipelines.record_agent_run(session.id, REPRO)
        pipelines.record_audit(session.id, host="h", agent=REPRO, model=model)
        return pipelines, session.id

    def test_a_forbidden_reproducer_blocks_the_edge_into_fixing(self, tmp_path):
        pipelines, sid = self._bugfix_at_reproducing(tmp_path, FORBIDDEN)
        with pytest.raises(GateFailure) as raised:
            pipelines.advance(sid, "fixing")
        (blocking,) = [v for v in raised.value.verdicts if v.blocks]
        assert blocking.rule_id == "test.new_test_fails"
        assert pipelines.require(sid).state == "reproducing"

    def test_an_allowed_reproducer_walks_it(self, tmp_path):
        pipelines, sid = self._bugfix_at_reproducing(tmp_path, ALLOWED)
        record, _ = pipelines.advance(sid, "fixing")
        assert record.state == "fixing"

    def test_a_forced_advance_is_the_recorded_way_past(self, tmp_path):
        pipelines, sid = self._bugfix_at_reproducing(tmp_path, FORBIDDEN)
        record, _ = pipelines.advance(sid, "fixing", force=True, reason="work stands")
        assert record.state == "fixing"
        assert record.overrides[-1]["gates"] == ["test.new_test_fails"]


# -- D3: the gap pipeline status names ----------------------------------------------


class TestUncheckedAgents:
    def test_an_agent_with_neither_model_is_named_once(self):
        evidence = {"agents_run": [DESIGN, DESIGN, REVIEW, "x"],
                    AUDIT_KEY: {"agents": {REVIEW: _entry([ALLOWED]),
                                           "x": _entry(asked=ALLOWED)}}}
        assert unchecked_agents(evidence) == [DESIGN]

    @pytest.mark.parametrize("evidence", [
        {}, {"agents_run": "text"}, {"agents_run": [7]},
    ])
    def test_nothing_ran_is_nothing_unchecked(self, evidence):
        assert unchecked_agents(evidence) == []

    def test_a_malformed_audit_leaves_every_agent_unchecked(self):
        evidence = {"agents_run": [DESIGN, REVIEW],
                    AUDIT_KEY: {"agents": {DESIGN: "text", REVIEW: _entry() | {"models": 3}}}}
        assert unchecked_agents(evidence) == [DESIGN, REVIEW]

    def test_pipeline_status_names_the_gap(self, m, monkeypatch, capsys):
        session = SessionDomain(m).start("gap")
        pipelines = PipelineDomain(m)
        pipelines.start(session.id, methodology="tdd", task="t")
        pipelines.record_agent_run(session.id, DESIGN)
        monkeypatch.chdir(m.root)
        assert main(["pipeline", "status", "--id", session.id]) == 0
        out = capsys.readouterr().out
        assert (f"  models  not checked against the forbidden list "
                f"({', '.join(FORBIDDEN_MODELS)}): {DESIGN} ran with no model known") in out

    def test_pipeline_gates_through_the_cli_reads_the_configured_list(
        self, tmp_path, monkeypatch, capsys
    ):
        m = git_project(tmp_path / "own", {
            "version": 1, "project": {"name": "demo"},
            "agents": {"roles": {"design": [DESIGN]}},
            "host": {"kind": "claude_code", "forbidden_models": [ALLOWED]},
        })
        session = SessionDomain(m).start("own")
        pipelines = PipelineDomain(m)
        pipelines.start(session.id, methodology="tdd", task="t")
        record = pipelines.require(session.id)
        record.state = "reviewing"
        pipelines._save(session.id, record)
        pipelines.record_agent_run(session.id, DESIGN)
        pipelines.record_audit(session.id, host="claude_code", agent=DESIGN, model=FORBIDDEN)
        monkeypatch.chdir(m.root)
        main(["pipeline", "gates", "--id", session.id, "--state", "committed", "--json"])
        gates = {g["rule"]: g for g in json.loads(capsys.readouterr().out)["gates"]}
        # The project's own list replaces the adapter's: FORBIDDEN is allowed here.
        assert gates["agents.design_agent_ran"]["ok"]


# -- D2: the keys and the adapter's defaults ----------------------------------------------


class TestTheKeys:
    def test_both_default_to_null_and_the_adapter_answers(self, m):
        assert m.get("host.window_model") is None
        assert m.get("host.forbidden_models") is None
        assert forbidden_models(m) == FORBIDDEN_MODELS
        assert window_model(m) == WINDOW_MODEL
        assert forbidden_models(None) == FORBIDDEN_MODELS
        assert window_model(None) == WINDOW_MODEL

    def test_a_configured_value_wins_and_an_empty_list_forbids_nothing(self, tmp_path):
        m = git_project(tmp_path / "set", {
            "version": 1, "project": {"name": "demo"},
            "host": {"kind": "claude_code", "window_model": ALLOWED, "forbidden_models": []},
        })
        assert forbidden_models(m) == ()
        assert window_model(m) == ALLOWED

    @pytest.mark.parametrize(("host", "named"), [
        ({"window_model": ""}, "host.window_model"),
        ({"window_model": "a\nb"}, "host.window_model"),
        ({"window_model": 3}, "host.window_model"),
        ({"forbidden_models": "x"}, "host.forbidden_models"),
        ({"forbidden_models": ["ok", ""]}, "host.forbidden_models"),
        ({"forbidden_models": [3]}, "host.forbidden_models"),
    ])
    def test_a_wrong_shape_is_refused_at_load(self, tmp_path, host, named):
        with pytest.raises(ManifestError, match=named):
            git_project(tmp_path / "bad", {"version": 1, "project": {"name": "demo"},
                                           "host": {"kind": "claude_code", **host}})

    def test_the_cli_reads_a_configured_list_for_a_host_with_no_adapter(self, tmp_path):
        m = git_project(tmp_path / "other", {
            "version": 1, "project": {"name": "demo"},
            "host": {"kind": "elsewhere", "forbidden_models": [ALLOWED]},
        })
        assert cli_main.host_forbidden_models(m) == (ALLOWED,)

    def test_a_host_with_no_adapter_and_no_list_forbids_nothing(self, tmp_path):
        m = git_project(tmp_path / "none", {
            "version": 1, "project": {"name": "demo"}, "host": {"kind": "elsewhere"}})
        assert cli_main.host_forbidden_models(m) == ()


# -- D6: the window model ----------------------------------------------------------------


class TestTheLaunch:
    def test_a_plan_carries_the_model_before_the_prompt(self, tmp_path):
        plan = launch.plan_launch("/opt/bin/claude", "x", "go", tmp_path, stage=False,
                                  model=ALLOWED, forbidden=LIST)
        assert plan.argv == ("/opt/bin/claude", "--name", "x", "--remote-control", "x",
                             "--model", ALLOWED, "go")

    def test_a_forbidden_model_is_refused_before_a_plan(self, tmp_path):
        with pytest.raises(InvariantError, match="forbidden-model list forbids it"):
            launch.plan_launch("/opt/bin/claude", "x", None, tmp_path, stage=False,
                               model=FORBIDDEN, forbidden=LIST)

    @pytest.mark.parametrize("model", ["", "  ", "-p", "--permission-mode", "a b", "a\nb"])
    def test_a_model_claude_would_misread_is_refused(self, model):
        with pytest.raises(InvariantError, match="one word"):
            launch.require_allowed_model(model)

    def test_no_model_is_no_flag(self):
        launch.require_allowed_model(None, LIST)

    def test_the_window_model_resolves_at_the_root(self, tmp_path):
        m = git_project(tmp_path / "set", {
            "version": 1, "project": {"name": "demo"},
            "host": {"kind": "claude_code", "window_model": ALLOWED},
        })
        assert cli_main._window_model(m.root) == (ALLOWED, FORBIDDEN_MODELS)
        assert cli_main._window_model(m.root, " chosen ") == ("chosen", FORBIDDEN_MODELS)
        assert cli_main._window_model(m.root, "  ") == (ALLOWED, FORBIDDEN_MODELS)

    def test_a_root_whose_configuration_does_not_load_takes_the_defaults(self, tmp_path):
        root = tmp_path / "broken"
        (root / ".claude").mkdir(parents=True)
        (root / ".claude" / "invariant.yaml").write_text("version: 1\nhost: 3\n")
        assert cli_main._window_model(root) == (WINDOW_MODEL, FORBIDDEN_MODELS)

    def test_start_session_takes_the_persons_model(
        self, project, capsys, _no_real_terminal_launch, macos_with_claude, successor  # noqa: F811
    ):
        assert main(["oracle", "start-session", "demo", "--model", ALLOWED, "--json"]) == 0
        (plan,) = _no_real_terminal_launch.plans
        assert plan.argv[-2:] == ("--model", ALLOWED)
        assert json.loads(capsys.readouterr().out)["model"] == ALLOWED

    def test_start_session_names_the_model_and_refuses_a_forbidden_one(
        self, project, capsys, _no_real_terminal_launch, macos_with_claude, successor  # noqa: F811
    ):
        assert main(["oracle", "start-session", "demo", "--dry-run"]) == 0
        assert f" on {WINDOW_MODEL} — dry run" in capsys.readouterr().out
        assert main(["oracle", "start-session", "demo", "--model", FORBIDDEN]) == 1
        assert "forbidden-model list forbids it" in capsys.readouterr().err
        assert _no_real_terminal_launch.plans == []

    def test_the_starter_carries_the_model_to_prepare(self):
        calls = []

        def prepare(key, label, prompt, scanned, model=None):
            calls.append(model)
            return {"name": "k-x", "label": "x", "prompt": prompt, "model": model}

        starter = startsession.Starter(prepare=prepare, launch=lambda p: "")
        starter.prepare_in({}, "k", None, None, ALLOWED)
        prepared, why = starter.again({}, "k", "x", "k-x", None, ALLOWED)
        assert why is None and prepared["model"] == ALLOWED
        assert calls == [ALLOWED, ALLOWED]


# -- D7: the dispatch ---------------------------------------------------------------------


class _Ctx:
    def __init__(self, manifest):
        self.manifest = manifest

    def rule_context(self, payload=None):  # pragma: no cover - a refusal never gets here
        raise AssertionError("the rules ran on a refused dispatch")


def _dispatch(model=None, agent=DESIGN) -> dict:
    tool_input = {"subagent_type": agent, **({"model": model} if model else {})}
    return {"tool_name": "Agent", "tool_input": tool_input}


class TestTheDispatch:
    def test_a_dispatch_asking_for_a_forbidden_model_is_denied(self, m):
        answer = ClaudeCodeHost(m.root, m)._pre_tool_use(_dispatch(FORBIDDEN), _Ctx(m))
        out = answer["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny"
        assert out["permissionDecisionReason"].startswith(
            f"invariant: {DESIGN} would run on {FORBIDDEN}")

    def test_the_installed_copys_model_line_answers_when_none_is_asked(self, m):
        agents = m.root / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / f"{DESIGN}.md").write_text(f"---\nname: {DESIGN}\nmodel: {FORBIDDEN}\n---\n")
        assert ClaudeCodeHost._forbidden_dispatch(_dispatch(), _Ctx(m)) is not None

    def test_an_allowed_or_unseen_model_is_not_refused(self, m):
        assert ClaudeCodeHost._forbidden_dispatch(_dispatch(ALLOWED), _Ctx(m)) is None
        assert ClaudeCodeHost._forbidden_dispatch(_dispatch(), _Ctx(m)) is None

    def test_a_call_that_is_no_dispatch_is_not_refused(self, m):
        payload = {"tool_name": "Read", "tool_input": {"model": FORBIDDEN}}
        assert ClaudeCodeHost._forbidden_dispatch(payload, _Ctx(m)) is None

    def test_a_context_with_no_manifest_refuses_nothing(self):
        assert ClaudeCodeHost._forbidden_dispatch(_dispatch(FORBIDDEN), object()) is None

    def test_the_project_list_replaces_the_default(self, tmp_path):
        m = git_project(tmp_path / "open", {
            "version": 1, "project": {"name": "demo"},
            "host": {"kind": "claude_code", "forbidden_models": []},
        })
        assert ClaudeCodeHost._forbidden_dispatch(_dispatch(FORBIDDEN), _Ctx(m)) is None


def test_the_engine_holds_no_model_name():
    """Ruling 5: the adapter's defaults appear in no engine module."""
    root = Path(__file__).resolve().parents[1] / "src" / "invariant"
    spelled = [WINDOW_MODEL, *FORBIDDEN_MODELS]
    for layer in ("core", "domains", "oracle"):
        for path in (root / layer).rglob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            assert not [name for name in spelled if f'"{name}"' in text], path
