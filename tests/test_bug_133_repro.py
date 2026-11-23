# ruff: noqa: F811 - the fixtures imported below are named as parameters
"""Repro for BUG_133 — see the work-item entry (`invariant item show BUG_133`).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrappers; the assertions must then pass unchanged.

Symptom: since BUG_127 a repository commit's trailer block may carry the
host's declared attribution trailer (``Claude-Session:`` and a link of the
host's declared shape). A commit made in a store is screened by two other
readers, and both still refuse that trailer as a ``links`` finding:

- route 1, ``privacy.clean``'s store route, on ``git -C <store> commit -F
  <file>``, evaluated on a rule context carrying the host's declared
  trailers as the CLI builds it;
- route 2, the store's installed ``commit-msg`` hook, fired by a real
  ``git commit -F <file>`` in the store.

Asserted: each route passes such a commit, each in its own test, and, as the
guard on any fix, the same link in the message body is still a ``links``
finding on each route. NOT asserted: why (the store routes calling the scan
without the declarations), nor where the declarations should come from on
the hook route, which has no host context; that is the fix's design.

Contract: the entry's Expected line, "A store commit's message is judged as a
repository commit's is: the host's declared trailer passes in the trailer
block, and the same link anywhere else is still a finding."
"""

from __future__ import annotations

from pathlib import Path

from test_adr_014_landing import (  # noqa: F401 - fixtures are used by name
    CLI_DOC,
    git,
    head,
    start,
    synced,
)
from test_adr_014_push_scan import sh, write

from invariant.cli.main import host_attribution_trailers
from invariant.core.manifest import Manifest
from invariant.core.rules import RuleContext, get_rule
from invariant.domains.session import SessionDomain

#: A synthetic session link of the shape the Claude Code host declares.
SESSION_LINK = "https://claude.ai/code/session_01AAAAAAAAAAAAAAAAAAAAAAAA"
CO_AUTHOR = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"

TRAILER_MESSAGE = (
    "Update the notes\n"
    "\n"
    "Explain the change in plain words.\n"
    "\n"
    f"{CO_AUTHOR}\n"
    f"Claude-Session: {SESSION_LINK}\n"
)
BODY_MESSAGE = (
    "Update the notes\n"
    "\n"
    f"Discussed at {SESSION_LINK} before the change.\n"
    "\n"
    f"{CO_AUTHOR}\n"
)


def _message_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "commit-message.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _stage_clean_change(store: Path) -> None:
    write(store, CLI_DOC, "clean\n")
    git(store, "add", "-A")


# -- route 1: privacy.clean's store route ------------------------------------------------


def _store_route_verdict(synced, capsys, tmp_path, text):
    start(capsys)
    _stage_clean_change(synced.store)
    msg = _message_file(tmp_path, text)
    manifest = Manifest.locate(synced.widgets)
    trailers = host_attribution_trailers(manifest)
    assert trailers, "the project's host.kind declares no attribution trailer"
    session = SessionDomain(manifest).require("s0000001")
    return get_rule("privacy.clean").fn(
        RuleContext(
            manifest=manifest,
            cwd=synced.widgets,
            session=session.to_dict(),
            payload={
                "tool_name": "Bash",
                "tool_input": {"command": f"git -C {synced.store} commit -F {msg}"},
            },
            host_attribution_trailers=trailers,
        )
    )


def test_BUG_133_store_route_passes_the_host_session_trailer(synced, capsys, tmp_path):
    verdict = _store_route_verdict(synced, capsys, tmp_path, TRAILER_MESSAGE)
    assert verdict.ok and not verdict.skipped, f"{verdict.message} | {verdict.detail}"


def test_BUG_133_store_route_same_link_in_the_body_is_still_a_finding(
    synced, capsys, tmp_path
):
    verdict = _store_route_verdict(synced, capsys, tmp_path, BODY_MESSAGE)
    assert not verdict.ok and not verdict.skipped, verdict.message
    assert "links" in f"{verdict.message} {verdict.detail}", verdict.detail


# -- route 2: the store's installed commit-msg hook --------------------------------------


def _hook_commit(synced, tmp_path, text):
    _stage_clean_change(synced.store)
    msg = _message_file(tmp_path, text)
    before = head(synced.store)
    made = sh(synced.store, "commit", "-q", "-F", str(msg))
    return before, made


def test_BUG_133_store_commit_msg_hook_passes_the_host_session_trailer(synced, tmp_path):
    before, made = _hook_commit(synced, tmp_path, TRAILER_MESSAGE)
    assert made.returncode == 0, made.stderr
    assert head(synced.store) != before


def test_BUG_133_store_commit_msg_hook_same_link_in_the_body_is_still_a_finding(
    synced, tmp_path
):
    before, made = _hook_commit(synced, tmp_path, BODY_MESSAGE)
    assert made.returncode != 0
    assert "commit message" in made.stderr and "links" in made.stderr, made.stderr
    assert head(synced.store) == before
