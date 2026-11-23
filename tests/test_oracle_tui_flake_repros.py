"""Deterministic repros for the dashboard's load-only test flakes.

Each test forces, on purpose, the interleaving that a loaded machine
(``pytest -n logical``) produces only by chance, so it fails every run while
the defect is live. They drive the app through ``test_oracle_tui.drive``, the
helper every dashboard test uses, so a fix to that helper or to
``oracle/tui`` turns them green unchanged.

* BUG_081: a refresh supersedes the in-flight one while the test's settle
  waits on it, and the wait raises ``WorkerCancelled``.
* BUG_134: ``run_test`` tears the app down without ``App.exit()``. It prunes
  the default screen's widgets while the app's timers and load workers are
  still live, and a refresh callback that lands in that window queries a
  widget that is gone: ``NoMatches`` for ``#fresh`` from the freshness
  timer, or ``WorkerFailed`` (``NoMatches`` for ``#banner``) from a load
  that finishes there.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time

import pytest

pytest.importorskip("textual")

from test_oracle_tui import _actions, drive

from invariant.oracle import dashboard, tui


def _empty(tmp_path):
    return dashboard.load(tmp_path, time.time(), lambda: {"projects": []})


def _slow_default_screen_teardown(app, after_removal):
    """Run ``after_removal()`` (a coroutine) once the default screen's widgets
    are removed at teardown, before the app stops its timers and workers:
    the window a loaded machine stretches."""
    real = app._prune

    def prune(*nodes, parent=None):
        removal = real(*nodes, parent=parent)
        if not any(getattr(node, "id", None) == "_default" for node in nodes):
            return removal

        async def slowly():
            await removal
            await after_removal()

        return slowly()

    app._prune = prune


# Repro for BUG_081 - see the work-item entry.
# Fails while the defect is live. When the fix lands, delete the
# expected-failure marker; the assertion must then pass unchanged.
def test_BUG_081_a_refresh_superseded_while_the_test_settles_does_not_error(tmp_path):
    """The dashboard's own timer (0.05 s, as the flaky test runs it) starts a
    read while the one before it is still running, during the helper's wait.
    Each read here ends only once the next has begun, until that has happened
    once with the helper waiting; every read after it is quick."""
    calls = []
    waiting = threading.Event()
    forced = threading.Event()
    give_up = time.monotonic() + 3.0

    def load():
        calls.append(1)
        mine = len(calls)
        while not forced.is_set() and time.monotonic() < give_up:
            if waiting.is_set() and len(calls) > mine:
                forced.set()
                break
            time.sleep(0.005)
        return _empty(tmp_path)

    app = tui.OracleApp(load, _actions(), interval=0.05)
    real_wait = app.workers.wait_for_complete

    async def observed_wait(workers=None):
        waiting.set()
        await real_wait(workers)

    app.workers.wait_for_complete = observed_wait

    async def script(pilot):
        assert len(calls) >= 2
        assert app.refresh_timer is not None

    try:
        drive(app, script)
    finally:
        forced.set()
    assert forced.is_set()


# Repro for BUG_134 - see the work-item entry.
# Fails while the defect is live. When the fix lands, delete the
# expected-failure marker; the assertion must then pass unchanged.
def test_BUG_134_the_freshness_tick_during_teardown_does_not_error(tmp_path):
    """The once-a-second ``updated N s ago`` tick lands after the default
    screen's widgets are gone: a teardown slower than one tick."""
    app = tui.OracleApp(lambda: _empty(tmp_path), _actions())
    _slow_default_screen_teardown(app, lambda: asyncio.sleep(1.2))

    async def script(pilot):
        assert "updated" in str(app.query_one("#fresh").content)

    drive(app, script)


# Repro for BUG_134 (its second face) - see the work-item entry.
# Fails while the defect is live. When the fix lands, delete the
# expected-failure marker; the assertion must then pass unchanged.
def test_BUG_134_a_load_that_finishes_during_teardown_does_not_error(tmp_path):
    """A refresh is still reading when the test ends, and its rows arrive
    after the default screen's widgets are gone."""
    pruned = threading.Event()
    calls = []

    def load():
        calls.append(1)
        if len(calls) > 1:
            pruned.wait(3.0)
        return _empty(tmp_path)

    app = tui.OracleApp(load, _actions())

    async def deliver():
        pruned.set()
        for worker in list(app.workers):
            with contextlib.suppress(Exception):
                await worker.wait()

    _slow_default_screen_teardown(app, deliver)

    async def script(pilot):
        await pilot.press("r")
        for _ in range(100):
            if len(calls) == 2:
                break
            await pilot.pause(0.01)
        assert len(calls) == 2, "the refresh is reading as the test ends"

    try:
        drive(app, script)
    finally:
        pruned.set()
