"""BUG_073 — the sphinx_needs backend never persists item history.

Repro for BUG_073 — see the work-item entry. Fails while the defect is live.
When the fix lands, delete the expected-failure marker; the assertion must then
pass unchanged.

The contract (docs/architecture/manifest.md, "Backends for work items"): "A
ruling's `ruling` and `evidence` keys travel in the history rows, so they are
kept wherever history is". The entry's expected behaviour: "History
round-trips on every backend that claims the work-item contract."

The assertion is on the symptom a user sees — the history rows an item reads
back after a transition — and not on the directive format the fix will use.
"""

import subprocess

import pytest
from test_domains import git_project

from invariant.domains.workitem import WorkItemDomain

RULING = "maintainer, 2026-11-09"

SPHINX_NEEDS = {"version": 1, "items": {"backend": "sphinx_needs", "path": "backlog.rst"}}


@pytest.mark.xfail(
    strict=True,
    reason="BUG_073 is live — delete this marker with the fix",
)
def test_BUG_073_sphinx_needs_history_reads_back_after_a_ruled_closure(tmp_path):
    m = git_project(tmp_path / "proj", SPHINX_NEEDS)
    domain = WorkItemDomain(m)
    bug = domain.new("bug", "A defect")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=m.root, capture_output=True, text=True, check=True
    ).stdout.strip()

    domain.transition(bug.id, "fixed", ruling=RULING, evidence=sha[:8], cwd=m.root)

    # Read back through a fresh domain, so nothing in memory can answer for the file.
    stored = WorkItemDomain(m).get(bug.id)
    assert stored.status == "fixed"
    rows = [
        (row.get("from"), row.get("to"), row.get("ruling"), row.get("evidence"))
        for row in stored.history
    ]
    assert rows == [
        ("filed", "reproduced", RULING, sha),
        ("reproduced", "fixed", RULING, sha),
    ]
