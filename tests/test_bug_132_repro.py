"""Repro for BUG_132 — see the work-item entry (`invariant item show BUG_132`).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom: under the default manifest (``commit_messages`` in
``privacy.scope``), ``privacy.clean`` passes a ``git commit`` whose personal
data sits in the second (or a later) ``-m`` / ``--message`` value. Git writes
every one of those values into the message as its own paragraph, so the
finding lands in history unscreened.

Asserted: the verdict of ``privacy.clean`` on such a commit is a failure that
names the ``emails`` finding, for each spelling of a later message value.
NOT asserted: the suspected cause (the commit-message reader returning the
first value alone) or how a fix should gather the values — that is the fix's
design.

Contract: docs/architecture/manifest.rst, "Privacy scope": ``commit_messages``
"scans ``git commit`` messages in every spelling git accepts — ``-m``,
``--message[=]``, combined clusters like ``-am``". The entry's Expected line:
"Every paragraph git writes into the message is scanned, whichever ``-m``
carried it."

The address is assembled at run time so this file carries no literal one.
"""

import pytest
from test_rules import (  # noqa: F401
    bash_payload,
    ctx_factory,
    only,
    repo,
)

from invariant.core.rules import evaluate

ADDRESS = "a.person" + "@" + "example.com"
LEAK = f"'Reported by {ADDRESS} last week.'"


@pytest.mark.parametrize(
    "command",
    [
        f"git commit -m 'Fix the widget' -m {LEAK}",
        f"git commit -m 'Fix the widget' --message={LEAK}",
        f"git commit -m 'Fix the widget' --message {LEAK}",
        f"git commit --message='Fix the widget' -m {LEAK}",
        f"git commit -am 'Fix the widget' -m {LEAK}",
        f"git commit -m 'Fix the widget' -m 'Plain body.' -m {LEAK}",
    ],
    ids=[
        "second-m",
        "second-message-equals",
        "second-message-spaced",
        "message-equals-then-m",
        "am-cluster-then-m",
        "third-m",
    ],
)
def test_BUG_132_a_later_message_paragraph_is_scanned(ctx_factory, command):  # noqa: F811
    ctx = ctx_factory(cwd=ctx_factory.main, payload=bash_payload(command))
    verdict = only(evaluate(["privacy.clean"], ctx)).verdict
    assert not verdict.ok and not verdict.skipped, (verdict.message, verdict.detail)
    assert "emails" in (verdict.detail or ""), verdict.detail
