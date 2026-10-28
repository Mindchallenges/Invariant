"""Repro for BUG_095 — see the work-item entry (docs/backlog/BUG_095.md).

Fails while the defect is live. When the fix lands, delete the strict
expected-failure marker; the assertion must then pass unchanged.

Observed: invariant's Stop hook ran past its 15 s hook timeout and the host
cancelled it (``hook_cancelled``). ``turn: ended`` was never written, so the
attention record stayed at ``turn: running`` with the prompt's
``turn_since`` and the oracle read the host as mid-turn.

Asserted (docs/architecture/oracle.md, "What writes it"): ``Stop`` ->
``turn-ended`` -> "``turn: ended``, ``waiting`` cleared"; and the entry's
expected behaviour: every Stop the host delivers records ``turn: ended``
"however long the rest of the Stop path (the closeout checks) takes, and
whether or not the hook is cancelled at its timeout".

How the cancellation is modelled in-process: the slow part of the Stop path
(the closeout evaluation) is replaced by a step that, after running past
its deadline, stops the process the way the host's cancel does -- nothing
on the Stop path after that point runs. That is ``HookCancelled``, a
``BaseException`` that the hook command does not catch, exactly as it
cannot catch the signal that ends the real hook. Whatever the Stop path
wrote to the attention record before that point is what the oracle sees.

The test asserts the symptom -- the ``turn`` the attention record holds
after a cancelled Stop -- and not the suspected cause (the order of the
writes inside ``cmd_hook`` / ``ClaudeCodeHost._stop``, or the 15 s timeout
in ``_wire_settings``).
"""

from __future__ import annotations

import json

import pytest
from test_oracle_attention import HOST, hook, sidecar

from invariant.hosts import claude_code as cc


class HookCancelled(BaseException):
    """The host's cancellation at the hook timeout: the process stops here."""


@pytest.fixture
def governed(project, monkeypatch):
    """A project the hook resolves, standing in it."""
    monkeypatch.chdir(project)
    return project


@pytest.mark.xfail(
    strict=True,
    reason="BUG_095 is live — delete this marker with the fix",
)
def test_BUG_095_a_stop_cancelled_at_its_timeout_still_records_the_turn_ended(
    governed, monkeypatch
):
    # A turn starts: the record says the host is mid-turn.
    hook("user-prompt-submit", {"session_id": HOST})
    assert json.loads(sidecar(governed).read_text())["turn"] == "running"

    reached = []

    def slow_closeout_cancelled_at_the_timeout(event, rule_ctx):
        # The closeout checks outlast the host's hook timeout; the host
        # cancels the hook while they are still running.
        reached.append(event)
        raise HookCancelled("hook_cancelled: Stop exceeded its 15 s timeout")

    monkeypatch.setattr(cc, "evaluate_event", slow_closeout_cancelled_at_the_timeout)

    # The host delivers the Stop; the hook is cancelled mid-way.
    try:
        hook("stop", {"session_id": HOST})
    except HookCancelled:
        pass

    doc = json.loads(sidecar(governed).read_text())
    assert doc["turn"] == "ended", (
        f"the host delivered a Stop, but after the hook was cancelled at its "
        f"timeout the attention record still says turn={doc['turn']!r}; "
        f"the oracle reads this host "
        f"as mid-turn (closeout step reached: {bool(reached)})"
    )
