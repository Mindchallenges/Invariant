"""Repro for BUG_127 — see the work-item entry (`invariant item show BUG_127`).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom: under the default manifest (``commit_messages`` in
``privacy.scope``, the ``links`` pattern on), ``privacy.clean`` refuses a
commit whose message ends with the attribution trailers the Claude Code host
asks agents to append: ``Co-Authored-By: ...`` and ``Claude-Session:``
followed by an https link to the host's session.

Asserted: the verdict of ``privacy.clean`` on ``git commit -F <file>`` whose
message carries those two trailers is a pass; and, as the guard on any fix,
the same link written in the message body is still refused. NOT asserted:
the suspected cause, or where the allowance should live (host adapter,
default allow list, trailer-aware reader) — that is the fix's design.

Contract: the entry's Expected line, "The commit trailer the shipped Claude
Code host asks for passes ``privacy.clean`` in a project that keeps the
default scope, without the project writing its own exemption. A link
anywhere else in the message is still screened."
"""

from test_rules import (  # noqa: F401
    bash_payload,
    ctx_factory,
    host_ctx_factory,
    only,
    repo,
)

from invariant.core.rules import evaluate

SESSION_LINK = "https://claude.ai/code/session_01AAAAAAAAAAAAAAAAAAAAAAAA"
CO_AUTHOR = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def verdict_for_message(ctx_factory, text):  # noqa: F811
    msg = ctx_factory.main / "commit-message.txt"
    msg.write_text(text, encoding="utf-8")
    ctx = ctx_factory(cwd=ctx_factory.main, payload=bash_payload(f"git commit -F {msg}"))
    return only(evaluate(["privacy.clean"], ctx)).verdict


def test_BUG_127_host_session_trailer_passes_privacy_clean(host_ctx_factory):  # noqa: F811
    message = (
        "Fix the widget\n"
        "\n"
        "Explain the change in plain words.\n"
        "\n"
        f"{CO_AUTHOR}\n"
        f"Claude-Session: {SESSION_LINK}\n"
    )
    verdict = verdict_for_message(host_ctx_factory, message)
    assert verdict.ok and not verdict.skipped, verdict.detail


def test_BUG_127_same_link_in_the_body_is_still_a_finding(host_ctx_factory):  # noqa: F811
    message = (
        "Fix the widget\n"
        "\n"
        f"Discussed at {SESSION_LINK} before the change.\n"
        "\n"
        f"{CO_AUTHOR}\n"
    )
    verdict = verdict_for_message(host_ctx_factory, message)
    assert not verdict.ok and not verdict.skipped, verdict.detail
    assert "links" in (verdict.detail or ""), verdict.detail
