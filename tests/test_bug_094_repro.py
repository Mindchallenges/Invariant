"""Repro for BUG_094 — see the work-item entry (docs/backlog/BUG_094.md).

Fails while the defect is live. When the fix lands, delete the strict
expected-failure marker; the assertion must then pass unchanged.

Observed: sessions waiting on their own background subagents were listed on
the panel under Idle, and one card read ``your_turn`` ("Claude finished its
turn and is waiting for you") although it was not the person's turn.

Asserted: the entry's expected behaviour -- "A host whose main turn has
ended but which still has background work in flight (a background subagent,
or a Bash command started with ``run_in_background``) reads as working, not
idle: no ``your_turn``, grouped under Working". The contract it extends
(docs/architecture/oracle.md, "Mid-turn", BUG_087): ``your_turn`` is raised
only when the host "is not mid-turn (``turn_state`` ``idle``)", and the
panel's Working group (docs/architecture/oracle-panel.md, "Groups and
tiles") holds a row whose current host is mid-turn and working.

The host's life is driven through the real hook command (``invariant hook``)
in the order Claude Code delivers it: a prompt, the main turn launching an
Agent with ``run_in_background`` (its PostToolUse answers at once with the
agent launched), the main turn's Stop, and the idle-prompt Notification. No
task-notification has arrived, so the background agent is still working.
The project is then read by the real scan, assessed, and grouped by the
real panel.

The test asserts the symptom -- the panel group the session's row lands in
and whether it carries ``your_turn`` -- not the suspected cause (the
``_STATUSES`` filter in ``signals.turn_state``, the missing hook events, or
a background-work ledger).
"""

from __future__ import annotations

from test_oracle_attention import hook
from test_oracle_scan import NOW, THE_HOST_PID, FakeProbe, make_project, put_session

from invariant.oracle import panel
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.recommend import assess
from invariant.oracle.scan import ScanCache, scan_all

HOST = "b9a4c0de-1111-2222-3333-444455556666"
SESSION = "s-bg"
AGENT_ID = "a1b2c3d4e5f6"


def test_BUG_094_a_host_waiting_on_its_background_agent_is_working_not_your_turn(
    tmp_path, monkeypatch
):
    alpha = make_project(tmp_path, "alpha")
    put_session(alpha, id=SESSION, name=f"bg-{SESSION}", state="working",
                origin=str(alpha.root), host_session_id=HOST, heartbeat=NOW - 60)
    monkeypatch.chdir(alpha.root)
    monkeypatch.setenv("CLAUDE_PID", str(THE_HOST_PID))
    base = {"session_id": HOST, "cwd": str(alpha.root)}

    # The person asks for something; the main turn starts.
    hook("user-prompt-submit", {**base, "hook_event_name": "UserPromptSubmit",
                                "prompt": "review this in the background"})
    # The main turn dispatches a subagent in the background. The tool returns
    # at once: the agent is launched, not finished.
    hook("post-tool-use", {
        **base, "hook_event_name": "PostToolUse", "tool_name": "Agent",
        "tool_input": {"description": "Review the change", "prompt": "Review it.",
                       "subagent_type": "compliance-reviewer",
                       "run_in_background": True},
        "tool_response": {"isAsync": True, "status": "async_launched",
                          "agentId": AGENT_ID, "description": "Review the change"},
    })
    # The main turn ends while the background agent keeps working, and the
    # host later notifies that it is idle at the prompt. No task-notification
    # (the agent's completion) has arrived.
    hook("stop", {**base, "hook_event_name": "Stop", "stop_hook_active": False})
    hook("notification", {**base, "hook_event_name": "Notification",
                          "notification_type": "idle_prompt",
                          "message": "Claude is waiting for your input"})

    probe = FakeProbe({"alpha": {HOST: NOW - 30}})
    snapshots = scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW,
                         cache=ScanCache(probe=probe))
    document = assess(snapshots, NOW)
    [row] = [r for p in document["projects"] for r in p["rows"]
             if r.get("session_id") == SESSION]

    kinds = sorted(s["kind"] for s in row["signals"])
    seen = (panel.group_of(row), "your_turn" in kinds)
    assert seen == ("working", False), (
        f"a host whose background agent is still working is grouped "
        f"{seen[0]!r} with signals {kinds} (turn_state={row.get('turn_state')!r}); "
        f"expected Working and no your_turn"
    )
