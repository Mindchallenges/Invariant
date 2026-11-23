"""Repro for BUG_159 — see the work-item entry (docs/backlog.rst).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrappers; the assertions must then pass unchanged.

Symptom: development ran on a small model and nothing in invariant stopped
it. The audit recorded the small model as the observed model of the design
and review agents, and every gate still passed; and every window the oracle
opens passes no model, so it follows the host's global default.

Asserted at the seams the user's rulings (2026-11-21) name, with their
defaults only, so no test depends on the name a setting is given:

- ruling 4: the forbidden-model list defaults to ``[haiku]``, and a design or
  review agent whose observed model is forbidden blocks the gate it would
  satisfy. Driven through ``invariant pipeline gates``, the CLI, so the
  default may live in configuration or in the host adapter (ruling 5): the
  engine never has to hold a model name for this to pass.
- ruling 3: a window the oracle opens carries ``--model`` with the host-side
  default ``opus``, through ``oracle start-session`` and through the chain
  step's injected launcher alike.

Not asserted: the suspected cause (``RuleContext`` strips ``evidence.audit``;
``plan_launch`` builds no ``--model``). The ``host.tier_map`` ruling (2) is
not here: the map already resolves from the user level today, so what is
missing there is the configuration on the machine, not engine behaviour.
"""

from __future__ import annotations

import json

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
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain

#: The observed model the audit recorded on 2026-11-21.
OBSERVED_SMALL = "claude-haiku-5-5"
DESIGN = "feature-architect"
REVIEW = "compliance-reviewer"


def _reviewing_pipeline_whose_agents_ran_on(tmp_path, model: str):
    """A tdd pipeline at ``reviewing`` whose design and review agents ran,
    the host's audit recording ``model`` as each one's observed model."""
    m = git_project(
        tmp_path / "proj",
        {
            "version": 1,
            "project": {"name": "demo"},
            "host": {"kind": "claude_code"},
            "agents": {"roles": {"design": [DESIGN], "review": [REVIEW]}},
            "enforcement": {
                "profile": "standard",
                "rules": {
                    "agents.design_agent_ran": "block",
                    "agents.review_agent_ran": "block",
                },
            },
        },
    )
    session = SessionDomain(m).start("bug-159")
    pipelines = PipelineDomain(m)
    pipelines.start(session.id, methodology="tdd", task="t")
    p = pipelines.require(session.id)
    p.state = "reviewing"
    pipelines._save(session.id, p)
    for agent, tier in ((DESIGN, "deep"), (REVIEW, "standard")):
        pipelines.record_agent_run(session.id, agent)
        pipelines.record_audit(
            session.id, host="claude_code", agent=agent, tier=tier, model=model, run=f"r-{agent}"
        )
    return m, session.id


@pytest.mark.parametrize("gate", ["agents.design_agent_ran", "agents.review_agent_ran"])
def test_BUG_159_an_agent_observed_on_a_forbidden_model_does_not_satisfy_its_gate(
    tmp_path, monkeypatch, capsys, gate
):
    m, sid = _reviewing_pipeline_whose_agents_ran_on(tmp_path, OBSERVED_SMALL)
    monkeypatch.chdir(m.root)
    assert main(["pipeline", "gates", "--id", sid, "--state", "committed", "--json"]) in (0, 1)
    verdicts = {v["rule"]: v for v in json.loads(capsys.readouterr().out)["gates"]}

    verdict = verdicts[gate]
    assert not verdict["ok"], (
        f"{gate} passed although the audit observed its agent on {OBSERVED_SMALL}: "
        f"{verdict['message']}"
    )


def _model_of(argv) -> str | None:
    argv = list(argv)
    return argv[argv.index("--model") + 1] if "--model" in argv else None


def test_BUG_159_oracle_start_session_launches_on_the_default_model(
    project, capsys, _no_real_terminal_launch, macos_with_claude, successor  # noqa: F811
):
    assert main(["oracle", "start-session", "demo", "--json"]) == 0
    (plan,) = _no_real_terminal_launch.plans
    assert _model_of(plan.argv) == "opus", (
        f"oracle start-session launched {list(plan.argv)}: no --model opus, so the "
        f"window follows the host's global default"
    )


def test_BUG_159_a_chained_start_launches_on_the_default_model(
    project, _no_real_terminal_launch, macos_with_claude, successor  # noqa: F811
):
    index = cli_main._project_index()
    hands = cli_main._real_chain_hands(index)
    hands.start(index.match("demo").key, "chained", "work the next queue entry")
    (plan,) = _no_real_terminal_launch.plans
    assert _model_of(plan.argv) == "opus", (
        f"the chain step launched {list(plan.argv)}: no --model opus, so the "
        f"window follows the host's global default"
    )
