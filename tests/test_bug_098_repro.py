"""Repro for BUG_098 — see the work-item entry (docs/backlog/BUG_098.md).

The fix has landed (close_host asks a busy tab again for a bounded time),
and the strict expected-failure marker is gone; the assertion is unchanged.

Observed: the panel's Close on a leftover Claude window ends Claude but often
leaves its Terminal tab open. Claude's own children (``caffeinate``, a
language server) share its tty and outlive it by a moment, so a tab asked
about at the instant Claude has gone still answers ``busy <processes>``.

Asserted: the documented contract of docs/architecture/oracle.md "Close host
(IDEA_027 increment 6)" and the Close row of docs/architecture/oracle-panel.md
-- "Ends Claude in this window, then closes the Terminal tab." With a faked
process table in which the tab's last child exits a moment (half a second)
after Claude, close-host's last line, the one the result sheet shows as what
happened, must be "Claude ended, and its Terminal tab closed." and the tab
must have been closed.

Not asserted: the suspected cause (one ``osascript`` call and no retry in
``_tab_outcome``) -- the fake tab answers by the clock, as Terminal would, not
by how many times it is asked. Also not asserted: the separable second face
of the entry, that the outcome line never reaches the actions audit log's
done entry; the fix's tests in tests/test_oracle_web_actions.py cover it.

Every seam is a fake: nothing is signalled, no ``osascript`` runs.
"""

from __future__ import annotations

from test_oracle_closehost import PID, TTY, FakeHost, request, unbound

from invariant.oracle.closehost import OUTCOMES, close_host

#: Seconds after Claude has gone that its last child (``caffeinate``) leaves the tab.
CHILD_LINGERS = 0.5


class LingeringChildHost(FakeHost):
    """Claude exits after SIGTERM; its child stays on the tab's tty a moment longer."""

    claude_gone_at: float | None = None
    closed: list

    def is_alive(self, pid: int) -> bool:
        alive = super().is_alive(pid)
        if self.signalled and not alive and self.claude_gone_at is None:
            self.claude_gone_at = self.clock
        return alive

    def close_tab(self, tty: str) -> tuple[str, str]:
        self.tabs_asked.append(tty)
        child_running = (self.claude_gone_at is None
                         or self.clock < self.claude_gone_at + CHILD_LINGERS)
        if child_running:
            return ("busy", "login,-zsh,caffeinate")
        self.closed.append(tty)
        return ("closed_tab", "")


def test_BUG_098_close_closes_the_tab_once_claudes_lingering_child_has_gone():
    fake = LingeringChildHost()
    fake.closed = []

    lines = close_host(request(), fake.seams(), unbound)

    assert fake.signalled == [PID]  # one SIGTERM, to Claude alone
    assert lines[-1] == OUTCOMES["closed_tab"], lines[-1]
    assert fake.closed == [TTY]
