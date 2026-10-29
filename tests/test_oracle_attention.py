"""The attention sidecar: what each host session is waiting on, per project.

``<state_root>/attention/<host-id>.json`` is written by the hooks of one host
session and read, later, by the oracle. These tests cover the record and its
store, the host-side translation of hook events into attention signals, the
CLI's guarded writes from ``invariant hook`` and from a gate refusal, and the
host pid the record carries (IDEA_012 increment 2, ADR-007 D5-D9).
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from invariant.cli import main as cli_main
from invariant.cli.main import Context, main
from invariant.core.errors import StateError
from invariant.core.rules import Evaluated, Level, Verdict
from invariant.core.store import InertStore, JsonStore
from invariant.hosts import claude_code as cc
from invariant.hosts import claude_code_launch as launch
from invariant.hosts.claude_code import (
    EVENTS,
    PROBE_KEY,
    AttentionSignal,
    ClaudeCodeHost,
    notification_kind,
)
from invariant.oracle import attention as attention_module
from invariant.oracle.attention import (
    ATTENTION_DIR,
    AttentionRecord,
    AttentionStore,
)

HOST = "host-1111-2222-3333"

#: Captured at import, before conftest replaces the seam for every test.
REAL_LOOKUP = cli_main._attention_process_lookup
NOW = "2026-10-22T10:00:00+00:00"


# -- fixtures ------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> JsonStore:
    return JsonStore(tmp_path / "state")


@pytest.fixture
def attention(store) -> AttentionStore:
    return AttentionStore(store, clock=lambda: NOW)


def on_disk(store: JsonStore, host: str = HOST) -> dict:
    path = store.root / ATTENTION_DIR / f"{host}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def fixed(pid):
    """A pid source that answers ``pid`` whatever the record held."""
    return lambda current: pid


def hook(event: str, body: dict) -> tuple[int, str]:
    stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps(body))
    out = io.StringIO()
    stdout = sys.stdout
    sys.stdout = out
    try:
        code = main(["hook", event])
    finally:
        sys.stdin = stdin
        sys.stdout = stdout
    return code, out.getvalue()


@pytest.fixture
def governed(project, monkeypatch):
    """A project the hook resolves, standing in it."""
    monkeypatch.chdir(project)
    return project


def sidecar(project: Path, host: str = HOST) -> Path:
    state_root = Context(cwd=project).manifest.state_root
    return Path(state_root) / ATTENTION_DIR / f"{host}.json"


def blocking(rule_id: str) -> Evaluated:
    return Evaluated(rule_id, Level.BLOCK, Verdict(ok=False, message="no"))


# -- the record ----------------------------------------------------------


class TestRecord:
    def test_defaults_describe_a_host_nothing_has_happened_to(self):
        record = AttentionRecord(host_session_id=HOST)
        assert record.to_dict() == {
            "host_session_id": HOST,
            "pid": None,
            "waiting": None,
            "blocked": None,
            "turn": None,
            "turn_since": None,
            "ended": False,
            "degraded": None,
            "background": None,
        }

    def test_keys_this_build_does_not_know_survive_a_round_trip(self):
        record = AttentionRecord.from_dict({"host_session_id": HOST, "later": 1})
        assert record.to_dict()["later"] == 1


# -- the store -----------------------------------------------------------


class TestStore:
    def test_a_host_never_written_reads_as_none(self, attention):
        assert attention.read(HOST) is None

    def test_a_notification_sets_waiting_with_the_raw_message(self, attention, store):
        attention.wait(HOST, "permission", "Claude needs your permission")
        doc = on_disk(store)
        assert doc["waiting"] == {
            "kind": "permission",
            "message": "Claude needs your permission",
            "since": NOW,
        }
        assert doc["host_session_id"] == HOST

    def test_an_unknown_waiting_kind_is_refused(self, attention):
        with pytest.raises(ValueError):
            attention.wait(HOST, "coffee", None)

    def test_a_finished_tool_clears_waiting(self, attention, store):
        attention.wait(HOST, "question", "pick one")
        assert attention.answered(HOST) is True
        assert on_disk(store)["waiting"] is None

    def test_a_finished_tool_with_nothing_waiting_writes_nothing(self, attention, store):
        assert attention.answered(HOST) is False
        assert not (store.root / ATTENTION_DIR).exists()

        attention.prompt(HOST)
        path = store.root / ATTENTION_DIR / f"{HOST}.json"
        before = path.stat().st_mtime_ns
        assert attention.answered(HOST) is False
        assert path.stat().st_mtime_ns == before

    def test_a_prompt_starts_a_turn_and_clears_waiting_blocked_and_degraded(
        self, attention, store
    ):
        attention.wait(HOST, "idle", "waiting for your input")
        attention.refused(HOST, ["pipeline.complete"])
        attention.degraded(HOST, "stop", "RuntimeError: x")
        attention.host_ended(HOST)
        attention.prompt(HOST)
        doc = on_disk(store)
        assert doc["turn"] == "running"
        assert doc["turn_since"] == NOW
        assert doc["waiting"] is None
        assert doc["blocked"] is None
        assert doc["degraded"] is None
        assert doc["ended"] is False

    def test_a_stop_the_rules_block_records_the_rule_ids(self, attention, store):
        attention.wait(HOST, "permission", "x")
        attention.turn_ended(HOST, ["pipeline.complete", "test.all_pass"])
        doc = on_disk(store)
        assert doc["turn"] == "ended"
        assert doc["waiting"] is None
        assert doc["blocked"] == {
            "rules": ["pipeline.complete", "test.all_pass"],
            "surface": "stop",
            "since": NOW,
        }

    def test_a_clean_stop_clears_a_stop_block(self, attention, store):
        attention.turn_ended(HOST, ["pipeline.complete"])
        attention.turn_ended(HOST, [])
        assert on_disk(store)["blocked"] is None

    def test_a_clean_stop_keeps_a_cli_refusal(self, attention, store):
        """The refusal is what the oracle reports once the turn has ended, so
        the Stop that ends the turn must not be the thing that erases it."""
        attention.refused(HOST, ["docs.read_architecture"])
        attention.turn_ended(HOST, [])
        doc = on_disk(store)
        assert doc["blocked"]["surface"] == "cli"
        assert doc["blocked"]["rules"] == ["docs.read_architecture"]
        assert doc["turn"] == "ended"

    def test_a_stop_arriving_ends_the_turn_and_clears_waiting(self, attention, store):
        attention.prompt(HOST)
        attention.wait(HOST, "permission", "x")
        attention.turn_stopped(HOST, pid_source=fixed(4242))
        doc = on_disk(store)
        assert doc["turn"] == "ended"
        assert doc["turn_since"] == NOW
        assert doc["waiting"] is None
        assert doc["pid"] == 4242

    @pytest.mark.parametrize("surface", ["stop", "cli"])
    def test_a_stop_arriving_leaves_any_block_to_the_closeout(
        self, attention, store, surface
    ):
        """Whether the closeout refused the turn is not known yet, and a
        closeout cancelled at its timeout never says: the last word stands."""
        if surface == "stop":
            attention.turn_ended(HOST, ["pipeline.complete"])
        else:
            attention.refused(HOST, ["pipeline.complete"])
        before = on_disk(store)["blocked"]
        attention.turn_stopped(HOST)
        assert on_disk(store)["blocked"] == before

    def test_the_closeout_refines_the_arrival(self, attention, store):
        attention.turn_ended(HOST, ["pipeline.complete"])
        attention.turn_stopped(HOST)
        attention.turn_ended(HOST, [])
        assert on_disk(store)["blocked"] is None
        attention.turn_stopped(HOST)
        attention.turn_ended(HOST, ["test.all_pass"])
        assert on_disk(store)["blocked"]["rules"] == ["test.all_pass"]

    def test_a_blocking_stop_replaces_a_cli_refusal(self, attention, store):
        attention.refused(HOST, ["docs.read_architecture"])
        attention.turn_ended(HOST, ["pipeline.complete"])
        assert on_disk(store)["blocked"]["surface"] == "stop"

    def test_session_end_clears_everything(self, attention, store):
        attention.wait(HOST, "permission", "x", pid_source=fixed(4242))
        attention.refused(HOST, ["r"])
        attention.degraded(HOST, "stop", "boom")
        attention.host_ended(HOST)
        doc = on_disk(store)
        assert doc["ended"] is True
        assert doc["waiting"] is None
        assert doc["blocked"] is None
        assert doc["degraded"] is None
        assert doc["pid"] is None
        assert doc["turn"] == "ended"

    def test_a_cli_refusal_and_its_lifting(self, attention, store):
        attention.refused(HOST, ["session.push_barrier"])
        assert on_disk(store)["blocked"] == {
            "rules": ["session.push_barrier"],
            "surface": "cli",
            "since": NOW,
        }
        assert attention.refusal_lifted(HOST) is True
        assert on_disk(store)["blocked"] is None

    def test_lifting_leaves_a_stop_block_and_an_absent_record_alone(
        self, attention, store
    ):
        assert attention.refusal_lifted(HOST) is False
        assert not (store.root / ATTENTION_DIR).exists()
        attention.turn_ended(HOST, ["pipeline.complete"])
        assert attention.refusal_lifted(HOST) is False
        assert on_disk(store)["blocked"]["surface"] == "stop"

    def test_degraded_records_the_event_and_the_error(self, attention, store):
        attention.degraded(HOST, "pre-tool-use", "KeyError: 'x'")
        assert on_disk(store)["degraded"] == {
            "event": "pre-tool-use",
            "error": "KeyError: 'x'",
            "since": NOW,
        }

    def test_unknown_keys_on_disk_survive_every_write(self, attention, store):
        path = store.root / ATTENTION_DIR / f"{HOST}.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"host_session_id": HOST, "later": [1]}))
        attention.prompt(HOST)
        assert on_disk(store)["later"] == [1]

    def test_a_malformed_record_is_replaced_by_the_next_write(self, attention, store):
        path = store.root / ATTENTION_DIR / f"{HOST}.json"
        path.parent.mkdir(parents=True)
        path.write_text("[not a record")
        with pytest.raises(StateError):
            attention.read(HOST)
        attention.prompt(HOST)
        assert on_disk(store)["turn"] == "running"

    def test_a_list_on_disk_is_replaced_too(self, attention, store):
        path = store.root / ATTENTION_DIR / f"{HOST}.json"
        path.parent.mkdir(parents=True)
        path.write_text("[1, 2]")
        attention.prompt(HOST)
        assert on_disk(store)["turn"] == "running"

    @pytest.mark.parametrize("bad", ["", "../escape", "a/b", ".hidden", "x" * 200])
    def test_a_host_id_that_is_not_a_plain_name_is_refused(self, attention, bad):
        with pytest.raises(ValueError):
            attention.prompt(bad)

    def test_the_inert_store_writes_nothing(self, tmp_path):
        inert = AttentionStore(InertStore(tmp_path / "state"), clock=lambda: NOW)
        inert.wait(HOST, "permission", "x")
        inert.prompt(HOST)
        inert.refused(HOST, ["r"])
        assert not (tmp_path / "state").exists()

    def test_the_default_clock_is_utc_iso_seconds(self, store):
        AttentionStore(store).prompt(HOST)
        since = on_disk(store)["turn_since"]
        assert since.endswith("+00:00")
        assert "." not in since


class TestPid:
    def test_a_write_records_the_pid_the_source_answers(self, attention, store):
        attention.prompt(HOST, pid_source=fixed(4242))
        assert on_disk(store)["pid"] == 4242

    def test_the_source_is_handed_the_pid_already_recorded(self, attention, store):
        seen = []
        attention.prompt(HOST, pid_source=fixed(4242))
        attention.prompt(HOST, pid_source=lambda current: seen.append(current) or current)
        assert seen == [4242]
        assert on_disk(store)["pid"] == 4242

    def test_a_source_answering_none_keeps_what_was_recorded(self, attention, store):
        attention.prompt(HOST, pid_source=fixed(4242))
        attention.prompt(HOST, pid_source=fixed(None))
        assert on_disk(store)["pid"] == 4242

    def test_a_read_first_path_that_writes_nothing_asks_for_no_pid(self, attention):
        asked = []
        attention.answered(HOST, pid_source=lambda c: asked.append(c))
        assert asked == []


# -- the host pid --------------------------------------------------------


class TestHostPid:
    @staticmethod
    def chain(rows: dict[int, tuple[int, str]]):
        return lambda pid: rows.get(pid)

    def test_the_walk_finds_the_first_claude_ancestor(self):
        rows = {
            700: (600, "/bin/bash"),
            600: (500, "/usr/local/bin/claude"),
            500: (1, "/bin/zsh"),
        }
        assert launch.find_host_pid(700, self.chain(rows)) == 600

    def test_the_start_itself_may_be_the_host(self):
        rows = {600: (500, "claude")}
        assert launch.find_host_pid(600, self.chain(rows)) == 600

    def test_no_claude_ancestor_answers_none(self):
        rows = {700: (600, "bash"), 600: (1, "launchd")}
        assert launch.find_host_pid(700, self.chain(rows)) is None

    def test_an_unreadable_process_ends_the_walk(self):
        assert launch.find_host_pid(700, self.chain({})) is None

    def test_the_walk_is_bounded(self):
        rows = {pid: (pid + 1, "bash") for pid in range(100, 200)}
        rows[150] = (151, "claude")
        assert launch.find_host_pid(100, self.chain(rows), max_hops=10) is None
        assert launch.find_host_pid(100, self.chain(rows), max_hops=60) == 150

    def test_a_process_that_is_its_own_parent_ends_the_walk(self):
        assert launch.find_host_pid(5, self.chain({5: (5, "kernel")})) is None

    def test_a_non_positive_start_is_refused(self):
        assert launch.find_host_pid(0, self.chain({0: (0, "claude")})) is None

    def test_parent_process_reads_this_process(self):
        row = launch.parent_process(os.getpid())
        assert row is not None
        assert row[0] == os.getppid()
        assert row[1]

    def test_parent_process_answers_none_for_no_such_process(self, monkeypatch):
        class Proc:
            returncode = 1
            stdout = ""

        monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: Proc())
        assert launch.parent_process(12345) is None

    def test_parent_process_answers_none_for_unparseable_output(self, monkeypatch):
        class Proc:
            returncode = 0
            stdout = "garbage\n"

        monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: Proc())
        assert launch.parent_process(12345) is None

    def test_parent_process_answers_none_when_ps_cannot_run(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("no ps")

        monkeypatch.setattr(launch.subprocess, "run", boom)
        assert launch.parent_process(12345) is None


# -- the host's signals ----------------------------------------------------


class TestNotificationKind:
    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            ("permission_prompt", "permission"),
            ("elicitation_dialog", "question"),
            ("idle_prompt", "idle"),
        ],
    )
    def test_the_documented_types(self, kind, expected):
        assert notification_kind({"notification_type": kind, "message": ""}) == expected

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("Claude needs your permission to use Bash", "permission"),
            ("Claude is waiting for your input", "idle"),
            ("Claude has a question for you", "question"),
        ],
    )
    def test_the_message_is_the_fallback_when_the_type_drifts(self, message, expected):
        assert notification_kind({"notification_type": "renamed", "message": message}) == expected
        assert notification_kind({"message": message}) == expected

    def test_a_notification_that_says_nothing_known_is_not_waiting(self):
        assert notification_kind({"notification_type": "auth_success", "message": "ok"}) is None
        assert notification_kind({"message": 3}) is None


class TestHostSignals:
    """What ``ClaudeCodeHost.handle`` leaves in ``attention`` per event."""

    @pytest.fixture
    def host(self, governed):
        return ClaudeCodeHost(governed)

    @pytest.fixture
    def ctx(self, governed):
        return Context(cwd=governed, host_session_id=HOST)

    def signal(self, host, ctx, event, body) -> AttentionSignal | None:
        host.handle(event, json.dumps(body), ctx)
        return host.attention

    def test_notification(self, host, ctx):
        body = {"notification_type": "permission_prompt", "message": "Claude needs your permission"}
        assert host.handle("notification", json.dumps(body), ctx) == {}
        assert host.attention == AttentionSignal(
            cc.ATTENTION_WAITING, kind="permission", message="Claude needs your permission"
        )

    def test_an_unrecognised_notification_signals_nothing(self, host, ctx):
        assert self.signal(host, ctx, "notification", {"message": "hello"}) is None

    def test_ask_user_question_is_waiting_on_a_question(self, host, ctx):
        body = {
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{"question": "Which one?"}]},
        }
        assert self.signal(host, ctx, "pre-tool-use", body) == AttentionSignal(
            cc.ATTENTION_WAITING, kind="question", message="Which one?"
        )

    def test_ask_user_question_without_a_readable_question(self, host, ctx):
        body = {"tool_name": "AskUserQuestion", "tool_input": {"questions": "?"}}
        assert self.signal(host, ctx, "pre-tool-use", body) == AttentionSignal(
            cc.ATTENTION_WAITING, kind="question", message=None
        )

    def test_a_denied_question_is_not_waiting(self, host, ctx, monkeypatch):
        monkeypatch.setattr(cc, "evaluate_event", lambda e, c: [blocking("r.x")])
        body = {"tool_name": "AskUserQuestion", "tool_input": {}}
        assert self.signal(host, ctx, "pre-tool-use", body) is None

    def test_any_other_pre_tool_use_signals_nothing(self, host, ctx):
        body = {"tool_name": "Bash", "tool_input": {"command": "true"}}
        assert self.signal(host, ctx, "pre-tool-use", body) is None

    def test_post_tool_use_is_answered(self, host, ctx):
        body = {"tool_name": "Read", "tool_input": {}}
        assert self.signal(host, ctx, "post-tool-use", body) == AttentionSignal(
            cc.ATTENTION_ANSWERED
        )

    def test_user_prompt_submit_is_a_prompt(self, host, ctx):
        assert self.signal(host, ctx, "user-prompt-submit", {}) == AttentionSignal(
            cc.ATTENTION_PROMPT
        )

    def test_a_clean_stop_carries_no_rules(self, host, ctx):
        assert self.signal(host, ctx, "stop", {}) == AttentionSignal(cc.ATTENTION_TURN_ENDED)

    def test_a_blocking_stop_carries_the_rule_ids(self, host, ctx, monkeypatch):
        monkeypatch.setattr(
            cc, "evaluate_event", lambda e, c: [blocking("pipeline.complete")]
        )
        assert self.signal(host, ctx, "stop", {}) == AttentionSignal(
            cc.ATTENTION_TURN_ENDED, rules=("pipeline.complete",)
        )

    def test_a_verdict_the_signal_cannot_read_never_costs_the_response(
        self, host, ctx, monkeypatch
    ):
        class Odd:
            blocks, asks, warns = True, False, False

            def render(self):
                return "[BLOCK] odd"

        monkeypatch.setattr(cc, "evaluate_event", lambda e, c: [Odd()])
        assert host.handle("stop", "{}", ctx)["decision"] == "block"
        assert host.attention is None

    def test_only_a_stop_says_anything_on_arrival(self, host):
        assert host.arrival_attention("stop") == AttentionSignal(
            cc.ATTENTION_TURN_STOPPED
        )
        for event in (*EVENTS, "no-such-event"):
            if event != "stop":
                assert host.arrival_attention(event) is None, event

    def test_session_end(self, host, ctx):
        assert self.signal(host, ctx, "session-end", {}) == AttentionSignal(
            cc.ATTENTION_HOST_ENDED
        )

    def test_session_start_signals_nothing_and_resets_the_last_one(self, host, ctx):
        self.signal(host, ctx, "user-prompt-submit", {})
        assert self.signal(host, ctx, "session-start", {}) is None

    def test_an_unknown_event_signals_nothing(self, host, ctx):
        assert self.signal(host, ctx, "no-such-event", {}) is None


# -- the hook wiring -------------------------------------------------------


class TestSevenEvents:
    def test_notification_is_the_seventh_event(self):
        assert len(EVENTS) == 7
        assert EVENTS["notification"] == "Notification"
        assert cc._PROBE_PAYLOADS["notification"][PROBE_KEY] is True

    def test_hooks_install_wires_seven_events(self, project):
        host = ClaudeCodeHost(project)
        host.install_hooks()
        hooks = json.loads((project / ".claude" / "settings.json").read_text())["hooks"]
        assert len(hooks) == 7
        assert "Notification" in hooks
        assert "matcher" not in hooks["Notification"][0]
        assert (project / ".claude" / "hooks" / "invariant-notification.sh").is_file()
        assert "install 7 hook shims" in host.plan_install()[0]


class TestHookWrites:
    def test_each_event_writes_the_sidecar(self, governed):
        hook("user-prompt-submit", {"session_id": HOST})
        assert json.loads(sidecar(governed).read_text())["turn"] == "running"

        hook(
            "notification",
            {"session_id": HOST, "notification_type": "permission_prompt", "message": "m"},
        )
        assert json.loads(sidecar(governed).read_text())["waiting"]["kind"] == "permission"

        hook("post-tool-use", {"session_id": HOST, "tool_name": "Read", "tool_input": {}})
        assert json.loads(sidecar(governed).read_text())["waiting"] is None

        hook(
            "pre-tool-use",
            {"session_id": HOST, "tool_name": "AskUserQuestion", "tool_input": {}},
        )
        assert json.loads(sidecar(governed).read_text())["waiting"]["kind"] == "question"

        hook("stop", {"session_id": HOST})
        doc = json.loads(sidecar(governed).read_text())
        assert doc["turn"] == "ended"
        assert doc["waiting"] is None

        hook("session-end", {"session_id": HOST})
        assert json.loads(sidecar(governed).read_text())["ended"] is True

    def test_a_payload_without_a_host_id_writes_nothing(self, governed, monkeypatch):
        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", HOST)
        hook("user-prompt-submit", {})
        assert not sidecar(governed).parent.exists()

    def test_the_probe_writes_nothing(self, governed, monkeypatch):
        """Every probe payload, including the new one, run as `doctor` runs
        it: with a host id in the body, so only the probe marker stops it."""
        walked = []
        monkeypatch.setattr(
            cli_main, "_attention_process_lookup", lambda: walked.append(1) or (lambda p: None)
        )
        for event, body in cc._PROBE_PAYLOADS.items():
            code, out = hook(event, {**body, "session_id": HOST})
            assert code == 0
            assert cc.DEGRADED_KEY not in out
        assert not sidecar(governed).parent.exists()
        assert walked == []

    def test_a_failing_write_leaves_the_response_byte_identical(
        self, governed, monkeypatch
    ):
        body = {"session_id": HOST, "notification_type": "idle_prompt", "message": "m"}
        stop = {"session_id": HOST}
        healthy = [hook("notification", body), hook("stop", stop)]
        assert sidecar(governed).is_file()

        def boom(*args, **kwargs):
            raise OSError("disk full")

        for name in (
            "wait", "turn_stopped", "turn_ended", "prompt", "answered", "host_ended"
        ):
            monkeypatch.setattr(AttentionStore, name, boom)
        broken = [hook("notification", body), hook("stop", stop)]
        assert broken == healthy

    def test_an_unwritable_state_root_never_degrades_a_hook(self, governed, monkeypatch):
        def boom(self, rel, data):
            raise StateError("read-only filesystem")

        healthy = hook("user-prompt-submit", {"session_id": HOST})
        monkeypatch.setattr(JsonStore, "write", boom)
        assert hook("user-prompt-submit", {"session_id": HOST}) == healthy

    def test_a_raising_handler_records_degraded(self, governed, monkeypatch):
        def explode(self, event, raw, ctx):
            raise RuntimeError("engine broke")

        monkeypatch.setattr(ClaudeCodeHost, "handle", explode)
        code, out = hook("stop", {"session_id": HOST})
        assert code == 0
        assert json.loads(out)[cc.DEGRADED_KEY] == "RuntimeError: engine broke"
        assert json.loads(sidecar(governed).read_text())["degraded"] == {
            "event": "stop",
            "error": "RuntimeError: engine broke",
            "since": json.loads(sidecar(governed).read_text())["degraded"]["since"],
        }

    def test_a_stop_whose_handler_raises_still_ends_the_turn(
        self, governed, monkeypatch
    ):
        """The arrival is recorded before the closeout runs (BUG_095)."""
        hook("user-prompt-submit", {"session_id": HOST})

        def explode(self, event, raw, ctx):
            raise RuntimeError("engine broke")

        monkeypatch.setattr(ClaudeCodeHost, "handle", explode)
        hook("stop", {"session_id": HOST})
        doc = json.loads(sidecar(governed).read_text())
        assert doc["turn"] == "ended"
        assert doc["degraded"]["event"] == "stop"

    def test_the_arrival_is_written_before_the_closeout_runs(
        self, governed, monkeypatch
    ):
        hook("user-prompt-submit", {"session_id": HOST})
        seen = []

        def closeout(event, rule_ctx):
            seen.append(json.loads(sidecar(governed).read_text())["turn"])
            return [blocking("pipeline.complete")]

        monkeypatch.setattr(cc, "evaluate_event", closeout)
        _, out = hook("stop", {"session_id": HOST})
        assert seen == ["ended"]
        assert json.loads(out)["decision"] == "block"
        doc = json.loads(sidecar(governed).read_text())
        assert doc["turn"] == "ended"
        assert doc["blocked"]["rules"] == ["pipeline.complete"]

    def test_a_raising_handler_under_the_probe_records_nothing(
        self, governed, monkeypatch
    ):
        def explode(self, event, raw, ctx):
            raise RuntimeError("engine broke")

        monkeypatch.setattr(ClaudeCodeHost, "handle", explode)
        hook("stop", {PROBE_KEY: True, "session_id": HOST})
        assert not sidecar(governed).parent.exists()


class TestHookPid:
    def test_claude_pid_in_the_environment_is_recorded(self, governed, monkeypatch):
        monkeypatch.setenv("CLAUDE_PID", "4242")
        hook("user-prompt-submit", {"session_id": HOST})
        assert json.loads(sidecar(governed).read_text())["pid"] == 4242

    def test_without_claude_pid_the_parent_chain_is_walked(self, governed, monkeypatch):
        parent = os.getppid()
        rows = {parent: (77, "/usr/local/bin/claude")}
        monkeypatch.setattr(
            cli_main, "_attention_process_lookup", lambda: rows.get
        )
        hook("user-prompt-submit", {"session_id": HOST})
        assert json.loads(sidecar(governed).read_text())["pid"] == parent

    def test_the_walk_runs_only_while_the_pid_is_unknown(self, governed, monkeypatch):
        calls = []

        def lookup(pid):
            calls.append(pid)
            return (77, "claude")

        monkeypatch.setattr(cli_main, "_attention_process_lookup", lambda: lookup)
        hook("user-prompt-submit", {"session_id": HOST})
        hook("stop", {"session_id": HOST})
        assert len(calls) == 1

    def test_claude_pid_replaces_a_recorded_pid(self, governed, monkeypatch):
        """A resumed host keeps its session id and gets a new process."""
        monkeypatch.setenv("CLAUDE_PID", "4242")
        hook("user-prompt-submit", {"session_id": HOST})
        monkeypatch.setenv("CLAUDE_PID", "5353")
        hook("stop", {"session_id": HOST})
        assert json.loads(sidecar(governed).read_text())["pid"] == 5353

    def test_the_default_lookup_is_the_process_table(self):
        assert REAL_LOOKUP() is launch.parent_process


# -- a refusal at the CLI --------------------------------------------------


class TestCliRefusal:
    @pytest.fixture
    def bound(self, tmp_path, monkeypatch, capsys):
        """A session bound to HOST, with a pipeline whose next edge is gated."""
        import subprocess

        project = tmp_path / "repo"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", HOST)
        for argv in (
            ["init", "-q", "-b", "main"],
            ["config", "user.email", "t@example.invalid"],
            ["config", "user.name", "T"],
        ):
            subprocess.run(["git", *argv], cwd=project, check=True, capture_output=True)
        assert main(["init", "--name", "demo"]) == 0
        subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-qm", "i"], cwd=project, check=True, capture_output=True
        )
        assert main(["session", "start", "t1"]) == 0
        assert main(["pipeline", "start", "--task", "x"]) == 0
        capsys.readouterr()
        return project

    def test_a_refused_advance_records_blocked_on_the_bound_host(self, bound):
        assert main(["pipeline", "advance", "red"]) != 0
        doc = json.loads(sidecar(bound).read_text())
        assert doc["blocked"]["surface"] == "cli"
        assert "docs.read_architecture" in doc["blocked"]["rules"]
        assert doc["pid"] is None

    def test_a_successful_advance_lifts_it(self, bound):
        main(["pipeline", "advance", "red"])
        assert main(["pipeline", "advance", "red", "--force", "--reason", "t"]) == 0
        assert json.loads(sidecar(bound).read_text())["blocked"] is None

    @staticmethod
    def refuse_transitions(monkeypatch):
        from invariant.core.errors import GateFailure
        from invariant.domains.session import SessionDomain

        def refuse(self, *args, **kwargs):
            raise GateFailure("no", verdicts=[blocking("session.gate")])

        monkeypatch.setattr(SessionDomain, "transition", refuse)

    def test_a_refused_session_transition_records_it(self, bound, monkeypatch):
        self.refuse_transitions(monkeypatch)
        assert main(["session", "transition", "isolated"]) != 0
        assert json.loads(sidecar(bound).read_text())["blocked"]["rules"] == [
            "session.gate"
        ]

    def test_a_session_transition_lifts_it(self, bound, monkeypatch):
        main(["pipeline", "advance", "red"])
        assert main(["session", "isolate", "--execute"]) == 0
        assert main(["session", "transition", "working"]) == 0
        assert json.loads(sidecar(bound).read_text())["blocked"] is None

    def test_a_refused_review_records_it(self, bound, monkeypatch):
        assert main(["session", "isolate", "--execute"]) == 0
        assert main(["session", "transition", "working"]) == 0
        self.refuse_transitions(monkeypatch)
        assert main(["session", "review"]) != 0
        assert json.loads(sidecar(bound).read_text())["blocked"]["surface"] == "cli"

    def test_a_failing_record_never_changes_the_refusal(self, bound, monkeypatch, capsys):
        main(["pipeline", "advance", "red"])
        healthy = capsys.readouterr()

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(AttentionStore, "refused", boom)
        code = main(["pipeline", "advance", "red"])
        assert code != 0
        assert capsys.readouterr() == healthy

    def test_an_unbound_or_ended_session_records_nothing(self, project):
        ctx = Context(cwd=project)
        verdicts = [blocking("r")]
        cli_main._record_cli_refusal(ctx, SimpleNamespace(host_session_id=None), verdicts)
        cli_main._record_cli_refusal(
            ctx, SimpleNamespace(host_session_id=HOST, host_ended=True), verdicts
        )
        cli_main._record_cli_refusal(ctx, None, verdicts)
        cli_main._lift_cli_refusal(ctx, None)
        assert not sidecar(project).parent.exists()

    def test_a_refused_merge_records_it(self, project, monkeypatch):
        ctx = Context(cwd=project)
        session = SimpleNamespace(host_session_id=HOST, host_ended=False)
        cli_main._record_cli_refusal(ctx, session, [blocking("session.ff_merge_target")])
        assert json.loads(sidecar(project).read_text())["blocked"]["rules"] == [
            "session.ff_merge_target"
        ]
        cli_main._lift_cli_refusal(ctx, session)
        assert json.loads(sidecar(project).read_text())["blocked"] is None


# -- layering --------------------------------------------------------------


class TestLayering:
    # The ruleset rule layers.only-the-cli-imports-the-oracle now covers this
    # (docs/guidelines/ruleset.yaml). The scan is kept for now (DN-17);
    # retiring it is a later step.
    def test_hosts_do_not_import_the_oracle(self):
        """ADR-007 D6: the CLI writes the sidecar; the host only says what happened."""
        import ast

        src = Path(cc.__file__).resolve().parent
        offenders = []
        for path in src.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else []
                )
                if any("oracle" in n for n in names):
                    offenders.append(path.name)
        assert offenders == []

    def test_the_module_names_its_directory(self):
        assert attention_module.ATTENTION_DIR == "attention"


# -- background work (BUG_094) ----------------------------------------------


AGENT_ID = "a0f1e2d3c4b5a6978"
SHELL_ID = "b1x2y3z4w"


def launched_agent(**extra) -> dict:
    """The PostToolUse of an Agent dispatched in the background: it answers at
    once, with the agent launched, not finished."""
    return {
        "tool_name": "Agent",
        "tool_input": {"description": "Review it", "prompt": "Review it.",
                       "run_in_background": True},
        "tool_response": {"isAsync": True, "status": "async_launched",
                          "agentId": AGENT_ID, "description": "Review it"},
        **extra,
    }


def launched_shell(**extra) -> dict:
    return {
        "tool_name": "Bash",
        "tool_input": {"command": "make test", "run_in_background": True},
        "tool_response": {"stdout": "", "stderr": "", "interrupted": False,
                          "backgroundTaskId": SHELL_ID},
        **extra,
    }


def notification(task: str = AGENT_ID, status: str = "completed", note: str | None = None,
                 summary: str = 'Agent "Review it" finished') -> str:
    """A task-notification prompt, as Claude Code delivers one as a turn."""
    lines = ["<task-notification>", f"<task-id>{task}</task-id>",
             "<tool-use-id>toolu_01</tool-use-id>", f"<status>{status}</status>",
             f"<summary>{summary}</summary>"]
    if note is not None:
        lines.append(f"<note>{note}</note>")
    lines += ["<result>done</result>", "</task-notification>"]
    return "\n".join(lines)


STILL_RUNNING = ("This agent stopped with background work of its own still running. "
                 "It may resume on its own when that work completes or reports, and the "
                 "same task-id notifies again if it does; the result below may be interim.")
NO_CHILDREN = ("A task-notification fires each time this agent stops with no live "
               "background children of its own.")


class TestBackgroundStore:
    def test_a_launch_opens_a_ledger_entry_and_answers_what_waited(self, attention, store):
        attention.wait(HOST, "idle", "waiting for your input")
        attention.background_started(HOST, AGENT_ID, "agent", pid_source=fixed(4242))
        doc = on_disk(store)
        assert doc["background"] == {AGENT_ID: {"kind": "agent", "since": NOW}}
        assert doc["waiting"] is None
        assert doc["pid"] == 4242

    def test_a_second_launch_adds_and_a_relaunch_keeps_one_entry(self, attention, store):
        attention.background_started(HOST, AGENT_ID, "agent")
        attention.background_started(HOST, SHELL_ID, "command")
        attention.background_started(HOST, AGENT_ID, "agent")
        assert sorted(on_disk(store)["background"]) == sorted([AGENT_ID, SHELL_ID])

    def test_an_unknown_kind_is_refused(self, attention):
        with pytest.raises(ValueError):
            attention.background_started(HOST, AGENT_ID, "daemon")

    def test_the_ledger_is_bounded_oldest_out(self, attention, store):
        for n in range(attention_module.BACKGROUND_LIMIT + 3):
            attention.background_started(HOST, f"t{n}", "command")
        ledger = on_disk(store)["background"]
        assert len(ledger) == attention_module.BACKGROUND_LIMIT
        assert "t0" not in ledger and f"t{attention_module.BACKGROUND_LIMIT + 2}" in ledger

    def test_a_notification_turn_closes_its_entries_and_runs_the_turn(self, attention, store):
        attention.background_started(HOST, AGENT_ID, "agent")
        attention.background_started(HOST, SHELL_ID, "command")
        attention.turn_ended(HOST, [])
        attention.wait(HOST, "idle", "waiting for your input")
        attention.background_done(HOST, [AGENT_ID, "never-opened"])
        doc = on_disk(store)
        assert doc["background"] == {SHELL_ID: {"kind": "command", "since": NOW}}
        assert (doc["turn"], doc["turn_since"], doc["waiting"]) == ("running", NOW, None)
        attention.background_done(HOST, [SHELL_ID])
        assert on_disk(store)["background"] is None

    def test_a_notification_turn_is_not_the_person_speaking(self, attention, store):
        """A refusal and a failed hook stay until the person answers them."""
        attention.refused(HOST, ["pipeline.complete"])
        attention.degraded(HOST, "stop", "RuntimeError: x")
        attention.background_done(HOST, [])
        doc = on_disk(store)
        assert doc["blocked"]["rules"] == ["pipeline.complete"]
        assert doc["degraded"]["event"] == "stop"

    def test_a_prompt_and_a_stop_keep_the_ledger(self, attention, store):
        attention.background_started(HOST, AGENT_ID, "agent")
        attention.prompt(HOST)
        attention.turn_stopped(HOST)
        attention.turn_ended(HOST, [])
        assert list(on_disk(store)["background"]) == [AGENT_ID]

    def test_session_end_clears_the_ledger(self, attention, store):
        attention.background_started(HOST, AGENT_ID, "agent")
        attention.host_ended(HOST)
        assert on_disk(store)["background"] is None

    def test_a_ledger_on_disk_this_build_cannot_read_is_started_over(self, attention, store):
        store.write(f"{ATTENTION_DIR}/{HOST}.json", {"host_session_id": HOST,
                                                     "background": ["junk"]})
        attention.background_done(HOST, [AGENT_ID])
        assert on_disk(store)["background"] is None
        attention.background_started(HOST, AGENT_ID, "agent")
        assert list(on_disk(store)["background"]) == [AGENT_ID]


class TestBackgroundSignals:
    """What the host adapter reads from a launch and from a task-notification."""

    @pytest.fixture
    def host(self, governed):
        return ClaudeCodeHost(governed)

    @pytest.fixture
    def ctx(self, governed):
        return Context(cwd=governed, host_session_id=HOST)

    def signal(self, host, ctx, event, body) -> AttentionSignal | None:
        host.handle(event, json.dumps(body), ctx)
        return host.attention

    @pytest.mark.parametrize("tool", ["Agent", "Task"])
    def test_a_background_agent_launch_opens_work(self, host, ctx, tool):
        body = launched_agent(tool_name=tool)
        assert self.signal(host, ctx, "post-tool-use", body) == AttentionSignal(
            cc.ATTENTION_BACKGROUND_STARTED, kind="agent", tasks=(AGENT_ID,))

    def test_a_background_shell_opens_work(self, host, ctx):
        assert self.signal(host, ctx, "post-tool-use", launched_shell()) == AttentionSignal(
            cc.ATTENTION_BACKGROUND_STARTED, kind="command", tasks=(SHELL_ID,))

    def test_a_shell_moved_to_the_background_mid_run_opens_work_too(self, host, ctx):
        body = launched_shell(tool_input={"command": "make test"})
        assert self.signal(host, ctx, "post-tool-use", body).tasks == (SHELL_ID,)

    @pytest.mark.parametrize("body", [
        {"tool_name": "Agent", "tool_input": {}, "tool_response": {"agentId": AGENT_ID,
                                                                   "status": "completed"}},
        {"tool_name": "Agent", "tool_input": {}, "tool_response": "a string"},
        {"tool_name": "Agent", "tool_input": {},
         "tool_response": {"isAsync": True, "agentId": ""}},
        {"tool_name": "Bash", "tool_input": {"command": "true"},
         "tool_response": {"stdout": "", "stderr": ""}},
        {"tool_name": "Bash", "tool_input": {}, "tool_response": {"backgroundTaskId": 7}},
        {"tool_name": "Bash", "tool_input": {},
         "tool_response": {"backgroundTaskId": "x" * 200}},
        {"tool_name": "Read", "tool_input": {}, "tool_response": {"backgroundTaskId": "b1"}},
    ])
    def test_anything_else_is_a_finished_tool(self, host, ctx, body):
        assert self.signal(host, ctx, "post-tool-use", body) == AttentionSignal(
            cc.ATTENTION_ANSWERED)

    def test_a_subagents_own_launch_is_not_the_hosts(self, host, ctx):
        """Its completion notifies the subagent, never the main turn."""
        body = launched_shell(agent_id="a0sub", agent_type="general-purpose")
        assert self.signal(host, ctx, "post-tool-use", body) == AttentionSignal(
            cc.ATTENTION_ANSWERED)

    @pytest.mark.parametrize("status", ["completed", "failed", "killed", "stopped"])
    def test_a_terminal_notification_closes_its_task(self, host, ctx, status):
        body = {"prompt": notification(status=status, note=NO_CHILDREN)}
        assert self.signal(host, ctx, "user-prompt-submit", body) == AttentionSignal(
            cc.ATTENTION_BACKGROUND_DONE, tasks=(AGENT_ID,))

    def test_an_agent_with_work_still_running_stays_open(self, host, ctx):
        body = {"prompt": notification(note=STILL_RUNNING)}
        assert self.signal(host, ctx, "user-prompt-submit", body) == AttentionSignal(
            cc.ATTENTION_BACKGROUND_DONE)

    @pytest.mark.parametrize("status", ["running", "", "progress"])
    def test_a_notification_that_is_not_terminal_closes_nothing(self, host, ctx, status):
        body = {"prompt": notification(status=status)}
        assert self.signal(host, ctx, "user-prompt-submit", body).tasks == ()

    def test_several_notifications_in_one_turn_close_each(self, host, ctx):
        prompt = notification() + "\n" + notification(task=SHELL_ID, summary="Background "
                                                       'command "x" completed (exit code 0)')
        assert self.signal(host, ctx, "user-prompt-submit", {"prompt": prompt}).tasks == (
            AGENT_ID, SHELL_ID)

    @pytest.mark.parametrize("prompt", [
        "please look at <task-notification> handling",
        "  hello",
        "",
        None,
        7,
    ])
    def test_a_person_s_prompt_is_a_prompt(self, host, ctx, prompt):
        body = {} if prompt is None else {"prompt": prompt}
        assert self.signal(host, ctx, "user-prompt-submit", body) == AttentionSignal(
            cc.ATTENTION_PROMPT)

    def test_a_notification_naming_no_task_closes_nothing(self, host, ctx):
        prompt = "<task-notification>\n<status>completed</status>\n</task-notification>"
        assert self.signal(host, ctx, "user-prompt-submit", {"prompt": prompt}) == (
            AttentionSignal(cc.ATTENTION_BACKGROUND_DONE))

    def test_the_response_does_not_depend_on_the_signal(self, host, ctx):
        plain = host.handle("user-prompt-submit", json.dumps({"prompt": "hi"}), ctx)
        told = host.handle("user-prompt-submit",
                           json.dumps({"prompt": notification()}), ctx)
        assert told == plain


class TestBackgroundHooks:
    def test_the_hooks_keep_the_ledger_across_a_background_agent(self, governed):
        base = {"session_id": HOST}
        hook("user-prompt-submit", {**base, "prompt": "review this in the background"})
        hook("post-tool-use", {**base, **launched_agent()})
        hook("post-tool-use", {**base, **launched_shell()})
        hook("stop", base)
        doc = json.loads(sidecar(governed).read_text())
        assert sorted(doc["background"]) == sorted([AGENT_ID, SHELL_ID])
        assert doc["turn"] == "ended"

        hook("user-prompt-submit", {**base, "prompt": notification()})
        doc = json.loads(sidecar(governed).read_text())
        assert list(doc["background"]) == [SHELL_ID]
        assert doc["turn"] == "running"

        hook("session-end", base)
        assert json.loads(sidecar(governed).read_text())["background"] is None

    def test_a_failing_ledger_write_leaves_the_response_byte_identical(
        self, governed, monkeypatch
    ):
        base = {"session_id": HOST}
        launch_body = {**base, **launched_agent()}
        told = {**base, "prompt": notification()}
        healthy = [hook("post-tool-use", launch_body), hook("user-prompt-submit", told)]

        def boom(*args, **kwargs):
            raise OSError("disk full")

        for name in ("background_started", "background_done"):
            monkeypatch.setattr(AttentionStore, name, boom)
        assert [hook("post-tool-use", launch_body),
                hook("user-prompt-submit", told)] == healthy
