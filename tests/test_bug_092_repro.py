"""Repro for BUG_092 — see the work-item entry (docs/backlog/BUG_092.md).

Fails while the defect is live. When the fix lands, delete the strict
expected-failure marker; the assertion must then pass unchanged.

Observed: on the mobile panel a project's line under Projects read
"2 live sessions" while only one of that project's sessions was running;
the other was on the panel too, with its own card saying "Paused" because
its Claude window had closed. "Live" reads as "running", so the count
contradicted the card beside it.

Asserted: the entry's expected behaviour -- the Projects line names what it
counts in the panel's plain language, "N open sessions", with "· N paused"
appended when one or more of the counted sessions has no live host
(``host_ended`` or ``parked``). The documented surface is the Projects
section of docs/architecture/oracle-panel.md ("7 · New session (IDEA_030)":
one row per registered project, "its name over" the session count).

The test asserts the symptom -- the line the panel renders for a project
with one working session and one paused one -- not the suspected cause (the
``_START`` strings or the row count in ``_start_projects``).
"""

from __future__ import annotations

import pytest
from test_oracle_panel import NOW, cards, host, session

from invariant.oracle import panel


@pytest.mark.xfail(strict=True, reason="BUG_092 is live — delete this marker with the fix")
def test_BUG_092_projects_line_separates_paused_from_open_sessions():
    working = session("s1", name="fix-it-s1", hosts=[host()], turn_state="busy")
    paused = session("s2", name="other-s2",
                     hosts=[host("7d3e11aa-1111-2222-3333-444455556666", alive=False)],
                     host_ended=True)
    document = {"now": NOW, "projects": [
        {"project": {"key": "K", "name": "Heap", "root": "/r"},
         "scanned_at": NOW, "elapsed": 0.0, "rows": [working, paused]},
    ]}

    shown = panel.view(document, acting=True, starting=True)

    # The scenario the user saw: one of the project's sessions is running.
    assert [c["title"] for c in cards(shown, "working")] == ["Fix it"]
    # The symptom: the Projects line for that project.
    line = shown["start"]["projects"][0]["line"]
    assert line == "2 open sessions · 1 paused"
