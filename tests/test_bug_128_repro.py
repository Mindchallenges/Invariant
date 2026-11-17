"""Repro for BUG_128 — see the work-item entry (`invariant item show BUG_128`).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom: a project that brought its own Sphinx-needs vocabulary is held to
invariant's baseline ``docs.needs``, and the failure's fix line names only
``docs.needs.types``. It never names ``docs.needs.decision_statuses`` or
``require_links``, the settings that settle most of the problems: a decision
whose status is the project's own (``agreed``) and a decision that links only
to a requirement.

Asserted: what ``docs.needs_traceable`` tells the project to do, its fix line,
for a needs.json carrying one of each kind of problem, against the baseline
``docs.needs``. NOT asserted: the suspected cause, or any design for the fix.
Nothing here requires adoption to read ``conf.py``.

Contract: the entry's Expected line, "Each kind of problem names the setting
that resolves it", read against state_model.rst ("``docs.needs_traceable``
... every decision's status is one of ``docs.needs.decision_statuses``, and
every ``require_links`` rule holds").
"""

from __future__ import annotations

from test_adr_018_docs_build import (
    OVERVIEW,
    TRACE,
    build,
    commit,
    ctx,
    toy,  # noqa: F401 - the fixture
    verdicts,
    worktree,
    write,
)
from test_adr_018_needs import need, needs_json


def adopted_needs():
    """A hand-built project's needs: its own status, link option and directive."""
    return needs_json(
        [
            need("COMP_CORE", "comp", "architecture/overview", 4),
            need("REQ_ONE", "req", "requirements", 3),
            need("DEC_ONE", "dec", "decisions/one", 6, "agreed", satisfies=["REQ_ONE"]),
            need("WP_ONE", "wp", "plan", 2),
        ],
        link_options=("links", "satisfies"),
    )


def test_BUG_128_fix_line_names_the_setting_for_each_kind_of_problem(toy):  # noqa: F811
    wt, fake = worktree(toy)
    write(wt, "docs/architecture/overview.rst", OVERVIEW + "\nMore.\n")
    commit(wt)
    fake.green(adopted_needs())
    build(wt)
    verdict = verdicts(ctx(toy, wt))[TRACE].verdict

    # The harness reaches the surface: all three kinds are reported today.
    assert not verdict.ok and not verdict.skipped
    detail = verdict.detail or ""
    assert "status 'agreed'" in detail
    assert "links to no element need" in detail
    assert "directive 'wp' is not declared in docs.needs.types" in detail

    # The symptom: what the project is told to do names each setting.
    fixes = " ".join(verdict.fixes)
    assert "docs.needs.types" in fixes
    assert "docs.needs.decision_statuses" in fixes, fixes
    assert "require_links" in fixes, fixes
