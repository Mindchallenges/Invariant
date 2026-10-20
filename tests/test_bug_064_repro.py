"""Repro for BUG_064 half (a) — see the work-item entry (docs/backlog/BUG_064.md).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom: a ``chore`` pipeline whose review agent ran and committed the
change is refused ``reviewing -> committed`` by ``agents.design_agent_ran``
("no design agent ran in this pipeline"), although ``chore`` declares no
design state — per docs/architecture/state_model.md it "is asserting there is
no design content".

Asserts the outcome of the landing edge (the gates ``pipeline advance
committed`` evaluates), not the suspected cause (``AGENT_ROLE_GATES`` injected
into every machine with a landing state). Half (b), ``docs.updated``, is
fixed and not asserted here.
"""

import pytest

from test_domains import git_project

from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain


@pytest.mark.xfail(
    strict=True,
    reason="BUG_064 (a) is live — delete this line with the fix",
)
def test_BUG_064_a_chore_landing_is_not_gated_on_a_design_agent(tmp_path):
    # This repository's configuration: a design role configured, its gate raised
    # to block — the setting under which the symptom was observed.
    m = git_project(
        tmp_path / "proj",
        {
            "version": 1,
            "project": {"name": "demo"},
            "agents": {
                "roles": {
                    "design": ["feature-architect"],
                    "review": ["compliance-reviewer"],
                }
            },
            "enforcement": {
                "profile": "standard",
                "rules": {"agents.design_agent_ran": "block"},
            },
        },
    )
    session = SessionDomain(m).start("x")
    pipelines = PipelineDomain(m)
    pipelines.start(session.id, methodology="chore")
    pipelines.advance(session.id, "reviewing", cwd=m.root)
    # The review agent ran, exactly as in the observed run. No design agent:
    # chore work has no design content to hand one.
    pipelines.record_agent_run(session.id, "compliance-reviewer")

    verdicts = pipelines.evaluate_gates(session.id, "committed", cwd=m.root)
    design = [v for v in verdicts if v.rule_id == "agents.design_agent_ran"]

    assert not any(v.blocks for v in design), (
        "chore 'reviewing -> committed' is blocked by agents.design_agent_ran: "
        + "; ".join(v.verdict.message for v in design)
    )
