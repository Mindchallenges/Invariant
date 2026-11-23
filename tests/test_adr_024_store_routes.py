# ruff: noqa: F811 - the fixtures imported below are named as parameters
"""ADR-024 D5: a store commit's message is judged as a repository commit's is.

The host's declared attribution trailer passes in the trailer block on every
store reader of a message: ``privacy.clean``'s store route (with the rule
context's declarations), the store's ``commit-msg`` and ``pre-push`` hooks and
the push (with the declarations of the host the store's own level names). D2's
narrowness holds on each: the token spelled exactly, the trailer block only,
git's own lines, and a ``--trailer`` value never admitted.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from test_adr_014_landing import (  # noqa: F401 - fixtures are used by name
    CLI_DOC,
    git,
    head,
    run,
    start,
    synced,
)
from test_adr_014_push_scan import sh, write

from invariant.cli.main import host_attribution_trailers, store_attribution_trailers
from invariant.core import storescan
from invariant.core.home import invariant_home
from invariant.core.manifest import Manifest
from invariant.core.rules import RuleContext, get_rule
from invariant.domains.session import SessionDomain
from invariant.hosts.claude_code import ATTRIBUTION_TRAILERS

LINK = "https://claude.ai/code/session_01BBBBBBBBBBBBBBBBBBBBBBBB"
CO_AUTHOR = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def message(*trailer_lines: str, body: str = "Explain the change.") -> str:
    return "Update the notes\n\n" + body + "\n\n" + "".join(f"{t}\n" for t in trailer_lines)


TRAILERED = message(CO_AUTHOR, f"Claude-Session: {LINK}")


def _commit(store: Path, tmp_path: Path, text: str, *flags: str):
    # Distinct content per message, so each commit has a change to carry.
    write(store, CLI_DOC, hashlib.sha256(text.encode("utf-8")).hexdigest() + "\n")
    git(store, "add", "-A")
    msg = tmp_path / "message.txt"
    msg.write_bytes(text.encode("utf-8"))
    before = head(store)
    made = sh(store, "commit", "-q", *flags, "-F", str(msg))
    return before, made


def _set_store_level(store: Path, update: dict) -> None:
    body = yaml.safe_load((store / "invariant.yaml").read_text(encoding="utf-8"))
    body.update(update)
    (store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


# -- the commit-msg hook: D2's narrowness on the store route ------------------------------


def test_hook_admits_nothing_in_a_message_holding_a_comment_line(synced, tmp_path):
    # Under -F git keeps the comment line, so in the history the trailer is
    # not the last paragraph: D2 admits nothing there.
    text = TRAILERED + "# a comment line\n"
    before, made = _commit(synced.store, tmp_path, text)
    assert made.returncode != 0 and "links" in made.stderr, made.stderr
    assert head(synced.store) == before


def test_hook_admits_the_trailer_with_crlf_line_ends(synced, tmp_path):
    before, made = _commit(synced.store, tmp_path, TRAILERED.replace("\n", "\r\n"))
    assert made.returncode == 0, made.stderr
    assert head(synced.store) != before


def test_hook_refuses_the_token_in_another_case(synced, tmp_path):
    before, made = _commit(synced.store, tmp_path, message(f"claude-session: {LINK}"))
    assert made.returncode != 0 and "links" in made.stderr, made.stderr
    assert head(synced.store) == before


def test_hook_refuses_a_line_git_does_not_cut_at_a_lone_carriage_return(synced, tmp_path):
    # Python's text mode would cut this line at the carriage return into two
    # trailer lines and make the host's look whole; git keeps it one line,
    # carrying more after the session id.
    text = message(CO_AUTHOR, f"Claude-Session: {LINK}\rX-Note: more")
    before, made = _commit(synced.store, tmp_path, text, "--cleanup=verbatim")
    assert made.returncode != 0 and "links" in made.stderr, made.stderr
    assert head(synced.store) == before


def test_hook_refuses_the_trailer_when_the_store_level_names_another_host(synced, tmp_path):
    _set_store_level(synced.store, {"host": {"kind": "another_host"}})
    before, made = _commit(synced.store, tmp_path, TRAILERED)
    assert made.returncode != 0 and "links" in made.stderr, made.stderr
    assert head(synced.store) == before


# -- the push: store push and the pre-push hook ------------------------------------------


def test_store_push_publishes_a_commit_carrying_the_trailer(synced, capsys, tmp_path):
    _before, made = _commit(synced.store, tmp_path, TRAILERED)
    assert made.returncode == 0, made.stderr
    code, out = run(capsys, "store", "push")
    assert code == 0, out
    assert git(synced.remote, "rev-parse", "main") == head(synced.store)


def test_store_push_refuses_the_link_in_a_message_body(synced, capsys, tmp_path):
    text = message(CO_AUTHOR, body=f"Discussed at {LINK} first.")
    _before, made = _commit(synced.store, tmp_path, text, "--no-verify")
    assert made.returncode == 0, made.stderr
    code, out = run(capsys, "store", "push")
    assert code == 1 and "links" in out, out
    assert git(synced.remote, "rev-parse", "main") != head(synced.store)


def test_pre_push_hook_passes_the_trailer_and_refuses_it_in_the_body(synced, tmp_path):
    _before, made = _commit(synced.store, tmp_path, TRAILERED)
    assert made.returncode == 0, made.stderr
    pushed = sh(synced.store, "push", "-q", "origin", "main")
    assert pushed.returncode == 0, pushed.stderr
    text = message(CO_AUTHOR, body=f"Discussed at {LINK} first.")
    _before, made = _commit(synced.store, tmp_path, text, "--no-verify")
    refused = sh(synced.store, "push", "-q", "origin", "main")
    assert refused.returncode != 0 and "links" in refused.stderr, refused.stderr


# -- privacy.clean's store route ----------------------------------------------------------


def test_store_route_never_admits_a_trailer_option_value(synced, capsys, tmp_path):
    start(capsys)
    write(synced.store, CLI_DOC, "clean\n")
    git(synced.store, "add", "-A")
    manifest = Manifest.locate(synced.widgets)
    session = SessionDomain(manifest).require("s0000001")
    command = (
        f"git -C {synced.store} commit -m 'Update the notes' "
        f"--trailer 'Claude-Session: {LINK}'"
    )
    verdict = get_rule("privacy.clean").fn(
        RuleContext(
            manifest=manifest,
            cwd=synced.widgets,
            session=session.to_dict(),
            payload={"tool_name": "Bash", "tool_input": {"command": command}},
            host_attribution_trailers=host_attribution_trailers(manifest),
        )
    )
    assert not verdict.ok and "links" in f"{verdict.message} {verdict.detail}"


# -- where the hook's declarations come from ---------------------------------------------


def test_store_level_host_resolves_over_the_user_level_and_the_defaults(synced):
    assert store_attribution_trailers(synced.store) == ATTRIBUTION_TRAILERS
    user = invariant_home() / "config.yaml"
    user.write_text(yaml.safe_dump({"defaults": {"host": {"kind": "another_host"}}}))
    assert store_attribution_trailers(synced.store) == ()
    _set_store_level(synced.store, {"host": {"kind": "claude_code"}})
    assert store_attribution_trailers(synced.store) == ATTRIBUTION_TRAILERS


def test_a_store_level_that_does_not_load_declares_none(synced):
    (synced.store / "invariant.yaml").write_text("store: [\n", encoding="utf-8")
    assert store_attribution_trailers(synced.store) == ()


def test_scan_message_reads_no_comment_line_and_admits_only_without_one(synced):
    screen = storescan.screen_now(synced.store)
    text = f"Subject\n\nBody\n\nClaude-Session: {LINK}\n"
    assert storescan.scan_message(synced.store, text, screen, ATTRIBUTION_TRAILERS).clean
    assert not storescan.scan_message(synced.store, text, screen).clean
    commented = f"Subject\n# {LINK}\n\nBody\n"
    assert storescan.scan_message(synced.store, commented, screen).clean
    assert not storescan.scan_message(
        synced.store, text + "# note\n", screen, ATTRIBUTION_TRAILERS
    ).clean


# -- D2's narrowness, the same on every store reader of a message -------------------------

#: Messages carrying the host's link in a shape D2 admits nowhere. Each is a
#: finding on the hook, on the push screen and on ``privacy.clean``'s store
#: route, exactly as on the repository's.
NOT_ADMITTED = {
    "lowercase token": message(CO_AUTHOR, f"claude-session: {LINK}"),
    "lone carriage return": message(CO_AUTHOR, f"Claude-Session: {LINK}\rX-Note: more"),
    "text after the id": message(CO_AUTHOR, f"Claude-Session: {LINK} and more"),
    "id with more after a dash": message(CO_AUTHOR, f"Claude-Session: {LINK}-x"),
    "id too long": message(
        CO_AUTHOR, "Claude-Session: https://claude.ai/code/session_" + "A" * 65
    ),
    "forged host": message(
        CO_AUTHOR, "Claude-Session: https://claude.ai.evil.example/code/session_01AB"
    ),
    "forged path": message(
        CO_AUTHOR, "Claude-Session: https://evil.example/claude.ai/code/session_01AB"
    ),
    "plain http": message(CO_AUTHOR, f"Claude-Session: {LINK.replace('https', 'http')}"),
    "another token": message(CO_AUTHOR, f"X-Session: {LINK}"),
    "space before the colon": message(CO_AUTHOR, f"Claude-Session : {LINK}"),
    "in the body": message(CO_AUTHOR, body=f"Discussed at {LINK} first."),
    "trailer line in the body": message(CO_AUTHOR, body=f"Claude-Session: {LINK}"),
    "block followed by text": TRAILERED + "\nA closing paragraph.\n",
    "block with a non-trailer line": message(
        CO_AUTHOR, f"Claude-Session: {LINK}", "not a trailer"
    ),
    "subject only": f"Claude-Session: {LINK}\n",
}


def test_several_trailers_pass_on_every_store_reader(synced, capsys, tmp_path):
    text = message("Reviewed-by: A Person", CO_AUTHOR,
                   f"Claude-Session: {LINK}")
    assert storescan.scan_message(
        synced.store, text, storescan.screen_now(synced.store), ATTRIBUTION_TRAILERS
    ).clean
    _before, made = _commit(synced.store, tmp_path, text)
    assert made.returncode == 0, made.stderr
    shas = [head(synced.store)]
    assert storescan.scan_commits(synced.store, shas, ATTRIBUTION_TRAILERS).clean


def test_no_shape_d2_refuses_passes_the_commit_msg_hook(synced, tmp_path):
    passed = []
    for name, text in NOT_ADMITTED.items():
        before, made = _commit(synced.store, tmp_path, text)
        if made.returncode == 0 or head(synced.store) != before:
            passed.append(name)
    assert not passed, passed


def test_no_shape_d2_refuses_passes_the_push_screen(synced, tmp_path):
    passed = []
    for name, text in NOT_ADMITTED.items():
        _before, made = _commit(synced.store, tmp_path, text, "--no-verify",
                                "--cleanup=verbatim")
        assert made.returncode == 0, made.stderr
        screened = storescan.scan_commits(
            synced.store, [head(synced.store)], ATTRIBUTION_TRAILERS
        )
        if screened.clean:
            passed.append(name)
    assert not passed, passed


def test_no_shape_d2_refuses_passes_the_store_route(synced, capsys, tmp_path):
    start(capsys)
    manifest = Manifest.locate(synced.widgets)
    session = SessionDomain(manifest).require("s0000001")
    write(synced.store, CLI_DOC, "clean\n")
    git(synced.store, "add", "-A")
    passed = []
    for index, (name, text) in enumerate(NOT_ADMITTED.items()):
        msg = tmp_path / f"route-{index}.txt"
        msg.write_bytes(text.encode("utf-8"))
        verdict = get_rule("privacy.clean").fn(
            RuleContext(
                manifest=manifest,
                cwd=synced.widgets,
                session=session.to_dict(),
                payload={
                    "tool_name": "Bash",
                    "tool_input": {"command": f"git -C {synced.store} commit -F {msg}"},
                },
                host_attribution_trailers=host_attribution_trailers(manifest),
            )
        )
        if verdict.ok:
            passed.append(name)
    assert not passed, passed


def test_the_hook_reads_no_repository_block_naming_a_host(synced, tmp_path):
    # A repository block in the store naming another host does not reach the
    # screen: only the store's own level does (DEC_ADR_024_D5).
    body = yaml.safe_load((synced.store / "invariant.yaml").read_text(encoding="utf-8"))
    for settings in body["store"]["repos"].values():
        settings["host"] = {"kind": "another_host"}
    (synced.store / "invariant.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    assert store_attribution_trailers(synced.store) == ATTRIBUTION_TRAILERS
