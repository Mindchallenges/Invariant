"""Repro for BUG_066 — see the work-item entry (docs/backlog/BUG_066.md).

Both tests fail while the defect is live. When the fix lands, delete the
expected-failure wrapper; each assertion must then pass unchanged.

Two halves, two symptoms:

1. A ``bugfix`` pipeline in ``reproducing`` with a red run on record has no
   lawful terminal that ``pipeline.complete`` (the entry gate of the session's
   ``reviewed`` and ``merged`` states — the review boundary) accepts, short of
   fixing the bug or declaring a false ``not_reproducible`` / ``abandoned``.
2. A run whose only test is a strict expected failure that fails is recorded
   in the red phase as a PASS, so ``test.new_test_fails`` reads "the run
   recorded in the reproducing phase passed".

Neither asserts the suspected cause (a missing machine state; an exit-status
only recorder) — each asserts the outcome a repro-only session meets.
"""

import sys

from test_domains import git_project

from invariant.core.machines import load_pipeline_machine
from invariant.core.rules import RuleContext, evaluate
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain

#: Terminals that would be a FALSE statement about a proof that fired.
FALSE_FOR_A_PROOF_THAT_FIRED = {"not_reproducible", "abandoned"}


def test_BUG_066_a_reproduced_bugfix_pipeline_has_a_lawful_terminal(tmp_path):
    m = git_project(tmp_path / "proj", {"version": 1, "project": {"name": "demo"}})
    machine = load_pipeline_machine(m, "bugfix")

    # Everything reachable from `reproducing` WITHOUT fixing the bug.
    seen, frontier = {"reproducing"}, ["reproducing"]
    while frontier:
        state = frontier.pop()
        for nxt in machine.next_states(state):
            if nxt == "fixing" or nxt in seen:
                continue
            seen.add(nxt)
            frontier.append(nxt)

    red_run = {
        "phase": "reproducing", "passed": False, "executed": 1, "failed": 1,
    }
    landable = []
    for terminal in sorted(t for t in seen if t in machine.terminal):
        if terminal in FALSE_FOR_A_PROOF_THAT_FIRED:
            continue
        ctx = RuleContext(
            manifest=m,
            cwd=m.root,
            pipeline={
                "methodology": "bugfix",
                "state": terminal,
                "evidence": {"test_runs": [red_run]},
            },
        )
        (result,) = evaluate(["pipeline.complete"], ctx)
        if result.verdict.ok:
            landable.append(terminal)

    assert landable, (
        "a bugfix pipeline in 'reproducing' with a red run recorded reaches no "
        "terminal pipeline.complete accepts without fixing; terminals reachable "
        f"without fixing: {sorted(t for t in seen if t in machine.terminal)}"
    )


def test_BUG_066_a_strict_expected_failure_that_fails_is_a_red_run(tmp_path):
    m = git_project(
        tmp_path / "proj",
        {
            "version": 1,
            "project": {"name": "demo", "ecosystems": ["python"]},
            "commands": {
                "test": f"{sys.executable} -m pytest -p no:cacheprovider",
                "test_one": f"{sys.executable} -m pytest -p no:cacheprovider {{selector}}",
            },
        },
    )
    # The repro test a bug-reproducer commits: wrapped, and failing on purpose.
    (m.root / "test_symptom.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.xfail(strict=True, reason='defect is live')\n"
        "def test_symptom():\n"
        "    assert 'wrong' == 'right'\n",
        encoding="utf-8",
    )
    session = SessionDomain(m).start("x")
    pipelines = PipelineDomain(m)
    pipelines.start(session.id, methodology="bugfix")
    pipelines.run_tests(
        session.id, phase="reproducing", selectors=["test_symptom.py"], cwd=m.root
    )

    ctx = RuleContext(
        manifest=m, cwd=m.root, pipeline=pipelines.current(session.id).to_dict()
    )
    (result,) = evaluate(["test.new_test_fails"], ctx)
    assert result.verdict.ok, result.verdict.message
