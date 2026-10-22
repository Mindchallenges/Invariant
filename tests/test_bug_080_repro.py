"""Repro for BUG_080 — see the work-item entry (docs/backlog/BUG_080.md).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

The symptom: a session in the oracle fixture that is waiting on a permission
prompt stops rendering as a red ``waiting`` signal once the wall clock moves
far enough past the day the fixture was written. The dashboard tests that
assert the red row (``test_oracle_tui.py::TestLines::test_the_waiting_row_is_red``
and ``test_oracle_web.py::TestView::test_a_waiting_session_is_red_and_counted``)
have gone amber on every run since.

This test makes the clock explicit. It moves the fixture's own clock,
``test_oracle_scan.NOW``, which every other age in ``machine_world`` is
already measured from, to an instant years ahead, builds the machine, and
reads the dashboard at that same instant. A fixture whose ages are all
relative to its clock reads the same at any clock, so the waiting signal
must be red. It asserts the dashboard line's tone, never the fixture's
``since`` value (the suspected cause).

The contract (docs/architecture/oracle.md, "oracle/signals"): ``waiting`` is
"high; medium after 6 h", and ``classify`` is pure — "the row and the clock
it is handed, nothing else".
"""

from datetime import datetime, timezone

import pytest
import test_oracle_scan

from invariant.oracle import dashboard
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.scan import ScanCache
from invariant.oracle.watcher import Watcher

machine_world = test_oracle_scan.machine_world

#: Years past the day the fixture was written: any wall clock a run may read.
FAR_FUTURE = datetime(2036, 1, 1, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def world_far_in_the_future(monkeypatch, request):
    """The shared machine, built with its clock pinned to ``FAR_FUTURE``."""
    monkeypatch.setattr(test_oracle_scan, "NOW", FAR_FUTURE)
    return request.getfixturevalue("machine_world")


@pytest.mark.xfail(strict=True, reason="BUG_080 is live — delete this line with the fix")
def test_BUG_080_a_waiting_session_reads_red_whatever_the_clock_says(world_far_in_the_future):
    world = world_far_in_the_future
    watcher = Watcher(ProjectIndex(oracle_home()), None, clock=lambda: FAR_FUTURE,
                      cache=ScanCache(probe=world["probe"]))
    lines = dashboard.lines(watcher.tick().document)
    line = next(x for x in lines if not x.nested and x.row.get("session_id") == "a-live")
    waiting = [(text, tone) for text, tone in line.signals if text.startswith("waiting")]
    assert waiting, line.signals
    assert [tone for _, tone in waiting] == [dashboard.RED], waiting
